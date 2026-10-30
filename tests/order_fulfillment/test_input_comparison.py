"""Unregistered CQ06 input revisions; pure tests without DB, server or LLM."""
import copy
import json
import unittest
from unittest.mock import patch

from ontologics.model import ROOT, digest
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.order_fulfillment.recalculation_demo import (
    AFTER_AS_OF, CASES, load_demo_recalculation, load_demo_recalculation_input)
from ontologics.order_fulfillment.response_comparison import (
    ComparisonError, compare_input_response_plan)

QUERIED = '2026-10-27T12:00:00+09:00'


class InputComparisonTests(unittest.TestCase):
    def setUp(self):
        self.before = (json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
                       + load_demo_contract() + load_demo_delivery())
        self.after = copy.deepcopy(self.before)
        self.after_as_of = AS_OF

    def query(self, **changes):
        args = dict(before_nodes=self.before, after_nodes=self.after,
                    delivery_round_id=ROUND, before_as_of=AS_OF,
                    after_as_of=self.after_as_of, before_revision='cq04-baseline',
                    after_revision='cq06-input', queried_at=QUERIED)
        args.update(changes)
        return compare_input_response_plan(**args)

    def assert_error(self, code, **changes):
        with self.assertRaises(ComparisonError) as found:
            self.query(**changes)
        self.assertEqual(code, found.exception.code)

    def test_all_independent_cases_match_existing_hand_arithmetic_goldens(self):
        golden = json.loads((ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())
        for case in CASES:
            with self.subTest(case=case):
                self.after = load_demo_recalculation_input(case)
                self.after_as_of = AFTER_AS_OF[case]
                result = self.query(after_revision=case)
                expected = golden['cases'][case]
                self.assertEqual(expected['quantity_deltas'], result['quantity_deltas'])
                self.assertEqual(expected['time_deltas'], result['time_deltas'])
                for field, value in expected['production'].items():
                    self.assertEqual(value, result['after']['production'][field])
                for section in ('forecast', 'dispatch_request'):
                    for field, value in expected[section].items():
                        self.assertEqual(value, result['after']['response_plan'][section][field])

    def test_metadata_binds_actual_inputs_without_claiming_database_versions(self):
        before = copy.deepcopy((self.before, self.after))
        result = self.query()
        self.assertEqual('unregistered_delivery_input_comparison', result['assessment_scope'])
        self.assertEqual('unregistered', result['input_state'])
        self.assertTrue(result['draft_only'])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['execution_performed'])
        for side, revision in (('before', 'cq04-baseline'), ('after', 'cq06-input')):
            self.assertEqual(revision, result[side]['input_revision'])
            self.assertEqual(digest(sorted(self.before, key=lambda node: node['id'])),
                             result[side]['input_sha256'])
            self.assertEqual('unregistered', result[side]['input_state'])
        def check(value):
            if isinstance(value, dict):
                self.assertNotIn('data_version', value)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(result)
        self.assertNotIn('schema_version', result)
        self.assertIn('not approved database version comparison', ' '.join(result['limitations']))
        result['before']['response_plan']['forecast']['promise']['contract']['customer_name'] = 'mutated'
        self.assertEqual(before, (self.before, self.after))

    def test_same_revision_accepts_node_order_and_equivalent_instant(self):
        self.after.reverse()
        result = self.query(after_revision='cq04-baseline', after_as_of='2026-10-27T01:00:00Z')
        self.assertEqual(result['before']['input_sha256'], result['after']['input_sha256'])
        self.assertTrue(all(value == 0 for value in result['quantity_deltas'].values()))
        self.assertEqual([], result['changed_inputs'])

    def test_same_revision_cannot_bind_different_graph_or_instant(self):
        next(n for n in self.after if n['type'] == 'DeliveryCondition')['transport_seconds'] = 7200
        self.assert_error('INCOMPARABLE_INPUTS', after_revision='cq04-baseline')
        self.assert_error('INVALID_TIME', after_revision='cq04-baseline',
                          after_as_of='2026-10-27T11:00:00+09:00')

    def test_revisions_are_opaque_and_times_must_not_reverse(self):
        result = self.query(before_revision='z-last-alphabetically', after_revision='a-first')
        self.assertEqual('compared', result['comparison_status'])
        for field in ('before_revision', 'after_revision'):
            for value in (None, True, 0, '', '  ', [], {}):
                with self.subTest(field=field, value=value):
                    self.assert_error('INVALID_INPUT_REVISION', **{field: value})
        for field in ('before_as_of', 'after_as_of', 'queried_at'):
            for value in (None, [], {}, '2026-10-27', '2026-10-27T10:00:00'):
                with self.subTest(field=field, value=value):
                    self.assert_error('INVALID_TIME', **{field: value})
        self.assert_error('INVALID_TIME', after_as_of='2026-10-27T09:00:00+09:00')

    def test_scope_and_missing_target_are_not_silently_adopted(self):
        for kind, field, value in (('Contract', 'customer_name', 'Different customer'),
                                    ('DeliveryRound', 'destination', 'Different destination')):
            with self.subTest(kind=kind):
                self.after = copy.deepcopy(self.before)
                next(n for n in self.after if n['type'] == kind)[field] = value
                self.assert_error('INCOMPARABLE_INPUTS')
        for value in (None, '', [], {}):
            self.assert_error('INVALID_INPUT', delivery_round_id=value)
        with self.assertRaises(KeyError):
            self.query(delivery_round_id='delivery-round:missing')

    def test_incomplete_facts_stay_unknown_and_malformed_facts_fail(self):
        self.after = load_demo_recalculation_input('zero_rate11')
        self.after_as_of = AFTER_AS_OF['zero_rate11']
        result = self.query()
        self.assertEqual('partially_unknown', result['comparison_status'])
        self.assertIsNone(result['quantity_deltas']['on_time_quantity'])
        self.assertIsNone(result['time_deltas']['estimated_completion_seconds'])
        self.after = copy.deepcopy(self.before)
        self.after_as_of = AS_OF
        next(n for n in self.after if n['type'] == 'FulfillmentSnapshot')['confirmations'] = {}
        self.assertEqual('partially_unknown', self.query()['comparison_status'])
        next(n for n in self.after if n['type'] == 'ProductionObservation')['good_quantity'] = -1
        with self.assertRaises(ValueError):
            self.query()
        for nodes in (None, {}, self.before + [copy.deepcopy(self.before[-1])]):
            with self.subTest(shape=type(nodes).__name__), self.assertRaises(ValueError):
                self.query(after_nodes=nodes)

    def test_source_hash_tampering_is_not_accepted_as_new_revision(self):
        next(n for n in self.after if n['type'] == 'SourceDocument')['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.query(after_revision='source-changed')

    def test_loader_reloads_baseline_for_each_alternative_and_returns_fresh_nodes(self):
        first = load_demo_recalculation_input('continued11')
        other = load_demo_recalculation_input('defects11')
        self.assertEqual(1, sum(n['type'] == 'ProductionObservation'
                               and n['observed_at'] == AFTER_AS_OF['defects11'] for n in other))
        self.assertFalse(any('continued11' in n['id'] for n in other))
        next(n for n in first if n['type'] == 'Contract')['customer_name'] = 'mutated'
        self.assertNotEqual('mutated', next(n for n in load_demo_recalculation_input('continued11')
                                          if n['type'] == 'Contract')['customer_name'])
        delta = load_demo_recalculation('transport2h')
        with patch('ontologics.order_fulfillment.recalculation_demo.load_demo_recalculation',
                   return_value=delta + [copy.deepcopy(delta[-1])]):
            with self.assertRaises(ValueError):
                load_demo_recalculation_input('transport2h')
