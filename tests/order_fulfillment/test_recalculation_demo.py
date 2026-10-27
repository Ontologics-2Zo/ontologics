"""CQ06 source-backed updates and independent goldens; no DB or LLM."""
import copy
import hashlib
import json
import re
import unittest
from unittest.mock import patch

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, CONDITION, SNAPSHOT, WORK, load_demo_delivery
from ontologics.model import ROOT, validate_graph
from ontologics.order_fulfillment.production import production_completion
from ontologics.order_fulfillment.recalculation_demo import (
    AFTER_AS_OF, CASES, SOURCE_ID, SOURCE_PATH, SOURCE_SHA256, load_demo_recalculation)
from ontologics.order_fulfillment.response_plan import response_plan

ROUND = 'delivery-round:demo:B-01:1'


class RecalculationDemoTests(unittest.TestCase):
    def setUp(self):
        self.base = (json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
                     + load_demo_contract() + load_demo_delivery())
        self.golden = json.loads((ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())

    def merged(self, updates):
        index = {node['id']: copy.deepcopy(node) for node in self.base}
        index.update({node['id']: copy.deepcopy(node) for node in updates})
        return list(index.values())

    def test_D22_new_source_hash_and_every_locator_resolve(self):
        raw = (ROOT / SOURCE_PATH).read_bytes()
        self.assertEqual(SOURCE_SHA256, hashlib.sha256(raw).hexdigest())
        lines = raw.decode().splitlines()
        for case in CASES:
            for node in load_demo_recalculation(case):
                if node['type'] != 'Evidence':
                    continue
                self.assertEqual(SOURCE_ID, node['source_id'])
                ranges = re.findall(r'Markdown lines (\d+)-(\d+); section ([A-H])', node['locator'])
                self.assertTrue(ranges)
                for start, end, section in ranges:
                    start, end = int(start), int(end)
                    self.assertLessEqual(1, start)
                    self.assertLessEqual(start, end)
                    self.assertLessEqual(end, len(lines))
                    self.assertEqual('## ' + section + '.', lines[start-1][:5])

    def test_D02_D03_D04_D05_D08_independent_candidates_validate_and_match_golden(self):
        before = copy.deepcopy(self.base)
        for case in CASES:
            with self.subTest(case=case):
                updates = load_demo_recalculation(case)
                saved = copy.deepcopy(updates)
                nodes = self.merged(updates)
                report = validate_graph(nodes)
                self.assertTrue(report['valid'], report)
                expected = self.golden['cases'][case]
                at = AFTER_AS_OF[case]
                response = response_plan(nodes, ROUND, at, queried_at=at)
                production = production_completion(nodes, WORK, ROUND, at, queried_at=at)
                for section in ('forecast', 'dispatch_request'):
                    for field, value in expected[section].items():
                        self.assertEqual(value, response[section][field], (case, section, field))
                for field, value in expected['production'].items():
                    self.assertEqual(value, production[field], (case, field))
                self.assertEqual(saved, updates)
        self.assertEqual(before, self.base)

    def test_D22_09_and10_observations_and_previous_immutable_provenance_are_retained(self):
        before_index = {node['id']: node for node in self.base}
        originals = {key: node for key, node in before_index.items()
                     if node['type'] in ('SourceDocument', 'Evidence', 'ProductionObservation')}
        for case in CASES:
            updates = load_demo_recalculation(case)
            ids = [node['id'] for node in updates]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(set(ids) & set(originals))
            after = {node['id']: node for node in self.merged(updates)}
            for key, node in originals.items():
                self.assertEqual(node, after[key])

    def test_D02_D04_only_necessary_logical_nodes_are_put_and_case_is_independent(self):
        for case in CASES:
            with self.subTest(case=case):
                updates = load_demo_recalculation(case)
                puts = {n['id'] for n in updates if n['type'] not in ('SourceDocument', 'Evidence')}
                expected = {CONDITION} if case == 'transport2h' else {
                    SNAPSHOT, WORK, 'observation:demo:CQ06:' + case + ':11'}
                if case == 'combined11':
                    expected.add(CONDITION)
                self.assertEqual(expected, puts)
                if case == 'transport2h':
                    self.assertEqual(AS_OF, AFTER_AS_OF[case])
                else:
                    self.assertEqual('2026-10-27T11:00:00+09:00', AFTER_AS_OF[case])
                self.assertFalse(any(n['type'] == 'Allocation' for n in updates))

    def test_D22_loader_rejects_source_tampering_and_returns_fresh_values(self):
        with patch('ontologics.order_fulfillment.recalculation_demo.hashlib.sha256') as hasher:
            hasher.return_value.hexdigest.return_value = '0' * 64
            with self.assertRaises(ValueError):
                load_demo_recalculation()
        for case in (None, True, 1, [], {}, '', 'unknown'):
            with self.subTest(case=case), self.assertRaises(ValueError):
                load_demo_recalculation(case)
        original = load_demo_recalculation()
        changed = load_demo_recalculation()
        changed[-1]['good_quantity'] = -1
        self.assertEqual(original, load_demo_recalculation())

    def test_D10_alternative_same_instant_updates_must_not_be_stacked(self):
        nodes = self.merged(load_demo_recalculation('continued11'))
        index = {n['id']: n for n in nodes}
        index.update({n['id']: n for n in load_demo_recalculation('defects11')})
        report = validate_graph(list(index.values()))
        self.assertFalse(report['valid'])
        self.assertIn('DUPLICATE_PRODUCTION_OBSERVATION', {e['code'] for e in report['errors']})


if __name__ == '__main__':
    unittest.main()
