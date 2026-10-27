"""Synthetic C boards, lines and production records join the existing graph as additions."""
from collections import Counter
import json
import unittest

from ontologics.claim_response.fixtures import OPERATIONS, batch_serials, claim_response_nodes, produced_times
from ontologics.claim_response.intake import convert_claim
from ontologics.demo import fixture_nodes
from ontologics.model import ROOT, instant, validate_graph
from ontologics.service import Service
from tests.db import database_for_test

CLAIM_REPORTED_AT = '2026-10-26T10:00:00+09:00'


def existing_nodes():
    catalog = json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']
    fictional, _ = fixture_nodes()
    return catalog + fictional


class ClaimResponseFixtureTests(unittest.TestCase):
    def setUp(self):
        self.nodes = claim_response_nodes()
        self.by_type = {}
        for node in self.nodes:
            self.by_type.setdefault(node['type'], []).append(node)

    def test_counts_lines_and_one_production_record_per_board(self):
        self.assertEqual({'SourceDocument': 1, 'Evidence': 4, 'ProductionLine': 2,
                          'ProductUnit': 207, 'UnitProductionRecord': 207},
                         dict(Counter(n['type'] for n in self.nodes)))
        records = self.by_type['UnitProductionRecord']
        self.assertEqual(sorted(u['id'] for u in self.by_type['ProductUnit']), sorted(r['unit_id'] for r in records))
        per_line = Counter(r['production_line_id'] for r in records)
        self.assertEqual({'production-line:demo:C-01': 200, 'production-line:demo:C-02': 7}, dict(per_line))
        serials = [u['serial'] for u in self.by_type['ProductUnit']]
        for prefix, count in (('C-D', 80), ('C-S', 120), ('C-R', 7)):
            self.assertEqual(count, sum(s.startswith(prefix) for s in serials), prefix)
        self.assertIn('C-D005', serials)

    def test_additions_validate_with_existing_graph(self):
        before = validate_graph(existing_nodes())
        after = validate_graph(existing_nodes() + self.nodes)
        self.assertEqual([], after['errors'])
        self.assertEqual(before['warnings'], after['warnings'])  # no new warnings from C data
        existing_ids = {n['id'] for n in existing_nodes()}
        self.assertFalse(existing_ids & {n['id'] for n in self.nodes})
        self.assertTrue(all(n['origin'] == 'synthetic' for n in self.nodes))

    def test_production_happens_before_the_claim_and_follows_the_schedule(self):
        reported = instant(CLAIM_REPORTED_AT)
        times = {r['unit_id']: r['produced_at'] for r in self.by_type['UnitProductionRecord']}
        self.assertTrue(all(instant(t) < reported for t in times.values()))
        self.assertEqual('2026-09-14T09:00:00+09:00', times['unit:C-D001'])
        self.assertEqual('2026-09-14T16:30:00+09:00', times['unit:C-D016'])
        self.assertEqual('2026-09-15T09:00:00+09:00', times['unit:C-D017'])
        self.assertEqual('2026-09-18T16:30:00+09:00', times['unit:C-D080'])
        self.assertEqual('2026-09-22T10:00:00+09:00', times['unit:C-R007'])
        self.assertEqual('2026-10-09T20:30:00+09:00', times['unit:C-S120'])

    def test_every_node_cites_the_operations_source(self):
        evidence = {e['id']: e for e in self.by_type['Evidence']}
        source = self.by_type['SourceDocument'][0]
        self.assertEqual('data/claim_response/operations.json', source['path'])
        self.assertTrue(all(e['source_id'] == source['id'] for e in evidence.values()))
        for node in self.nodes:
            if node['type'] not in ('SourceDocument', 'Evidence'):
                self.assertTrue(set(node['evidence']) <= set(evidence), node['id'])
        stored = evidence['evidence:claim_response:batch:stored']
        self.assertEqual('/batches/2', stored['locator'])
        self.assertIn('statement only', stored['description'])

    def test_batch_rules_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_BATCH'):
            batch_serials({'prefix': 'C-X', 'count': 1000})
        with self.assertRaisesRegex(ValueError, 'INVALID_BATCH'):
            produced_times({'first_produced_at': '2026-09-14T09:00:00+09:00', 'count': 2,
                            'per_day': 49, 'interval_minutes': 30})
        with self.assertRaises(ValueError):
            produced_times({'first_produced_at': '2026-09-14T09:00:00', 'count': 1,
                            'per_day': 1, 'interval_minutes': 30})
        self.assertTrue(OPERATIONS.is_file())


class ClaimResponseDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals={
            'fixture': {'kind': 'human', 'roles': ['seed', 'read', 'audit']},
            'claim-agent': {'kind': 'agent', 'roles': ['read', 'draft']},
        })
        self.addCleanup(self.service.close)
        seeded = self.service.bootstrap('fixture', existing_nodes() + claim_response_nodes(),
                                        request_key='claim-response-seed')
        self.assertTrue(seeded['ok'], seeded)

    def test_seeded_operations_are_readable_and_claim_becomes_a_reviewable_draft(self):
        units = self.service.lookup('claim-agent', kind='ProductUnit', version=1)['data']
        self.assertEqual(211, len(units))  # 4 existing demo boards + 207 C boards
        lines = self.service.lookup('claim-agent', kind='ProductionLine', version=1)['data']
        self.assertEqual(['C-01', 'C-02'], sorted(line['line_code'] for line in lines))
        record = self.service.lookup('claim-agent', node_id='production:C-D001', version=1)['data'][0]
        self.assertEqual('production-line:demo:C-01', record['production_line_id'])

        claim = convert_claim(ROOT / 'data/claim_response/claims/claim_table.csv',
                              collected_at='2026-10-26T10:30:00+09:00')
        operations = [{'op': 'put', 'node': node} for node in claim['nodes']]
        draft = self.service.prepare_draft('claim-agent', operations, request_key='claim-intake-draft')
        self.assertTrue(draft['ok'], draft)
        saved = self.service.get_draft('claim-agent', draft['data']['draft_id'], draft['data']['draft_version'])['data']
        self.assertEqual('awaiting_review', saved['status'])
        self.assertEqual(1, self.service._version())  # nothing is applied without human review
        self.assertEqual([], self.service.lookup('claim-agent', kind='ClaimIntake', version=1)['data'])


if __name__ == '__main__':
    unittest.main()
