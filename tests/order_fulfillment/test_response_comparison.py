"""CQ06 pure contracts and independently specified arithmetic, without DB/API.

Input variants below are unregistered synthetic test copies. Integration tests
exercise newly authored source IDs and the actual draft/review/apply boundary.
"""
import copy
import json
import unittest
from unittest.mock import patch

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.model import ROOT, instant
from ontologics.order_fulfillment.schema_validation import validate_input_graph
from ontologics.order_fulfillment.response_comparison import ComparisonError, compare_response_plan
from ontologics.order_fulfillment.response_plan import response_plan

T11 = '2026-10-27T11:00:00+09:00'
QUERIED = '2026-10-27T12:00:00+09:00'


class ResponseComparisonTests(unittest.TestCase):
    def setUp(self):
        self.before = self.load()
        self.after = copy.deepcopy(self.before)
        self.after_at = AS_OF

    @staticmethod
    def load(mixed=False):
        return (json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
                + load_demo_contract() + load_demo_delivery(mixed=mixed))

    @staticmethod
    def node(nodes, kind):
        return next(n for n in nodes if n['type'] == kind)

    def advance(self, good=690, transport=3600):
        self.after_at = T11
        snapshot = self.node(self.after, 'FulfillmentSnapshot')
        work = self.node(self.after, 'ProductionWork')
        observation = copy.deepcopy(max((n for n in self.after if n['type'] == 'ProductionObservation'),
                                        key=lambda n: instant(n['observed_at'])))
        observation.update(id='observation:test:CQ06:11', observed_at=T11,
                           produced_quantity=750, good_quantity=good, defective_quantity=750-good)
        self.after.append(observation)
        snapshot['as_of'] = T11
        work.update(observed_at=T11, completed_quantity=good)
        self.node(self.after, 'DeliveryCondition')['transport_seconds'] = transport

    def query(self, **changes):
        args = dict(before_nodes=self.before, after_nodes=self.after, delivery_round_id=ROUND,
                    before_as_of=AS_OF, after_as_of=self.after_at, before_version=2, after_version=3,
                    queried_at=QUERIED)
        args.update(changes)
        return compare_response_plan(**args)

    def error_is(self, code, **changes):
        with self.assertRaises(ComparisonError) as found:
            self.query(**changes)
        self.assertEqual(code, found.exception.code)

    def test_D01_D20_full_original_response_retained_not_reconstructed(self):
        result = self.query()
        self.assertEqual(response_plan(self.before, ROUND, AS_OF, queried_at=QUERIED),
                         result['before']['response_plan'])
        self.assertEqual(response_plan(self.after, ROUND, AS_OF, queried_at=QUERIED),
                         result['after']['response_plan'])
        self.assertEqual((960, 40), tuple(result['before']['response_plan']['forecast'][k]
                                        for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual('2026-10-27T14:26:40+09:00', result['before']['production']['estimated_completion_at'])
        self.assertEqual('comparison-0.1', result['comparison_rule_version'])
        self.assertTrue(all(result[k] for k in ('synthetic', 'draft_only', 'review_required')))
        self.assertFalse(result['execution_performed'])

    def test_D02_D19_continued11_has_same_arrival_but_shorter_remaining_duration(self):
        self.advance()
        result = self.query()
        self.assertEqual('compared', result['comparison_status'])
        self.assertEqual({'remaining_seconds': -3600, 'estimated_completion_seconds': 0,
                          'estimated_full_arrival_seconds': 0}, result['time_deltas'])
        self.assertEqual((0, 0, 90, -90), tuple(result['quantity_deltas'][k] for k in
                         ('on_time_quantity', 'shortfall_quantity', 'currently_available_quantity', 'future_quantity')))
        self.assertEqual(12400, result['after']['production']['remaining_seconds'])
        self.assertEqual(90, result['after']['production']['good_rate_per_hour'])
        self.assertEqual(['observation:demo:CQ04:base:10', 'observation:test:CQ06:11'],
                         result['after']['production']['selected_observation_ids'])

    def test_D03_D18_D19_defects11_uses_latest_interval_not_cumulative_average(self):
        self.advance(680)
        result = self.query()
        after = result['after']
        self.assertEqual((920, 80), tuple(after['response_plan']['forecast'][k]
                                        for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual(80, after['production']['good_rate_per_hour'])
        self.assertEqual(.2, after['production']['recent_defect_ratio'])
        self.assertEqual(320, after['production']['remaining_quantity'])
        self.assertEqual((-40, 40), tuple(result['quantity_deltas'][k]
                                         for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual({'remaining_seconds': -1600, 'estimated_completion_seconds': 2000,
                          'estimated_full_arrival_seconds': 2000}, result['time_deltas'])

    def test_D04_transport_only_preserves_production(self):
        self.node(self.after, 'DeliveryCondition')['transport_seconds'] = 7200
        result = self.query()
        self.assertEqual((-90, 90), tuple(result['quantity_deltas'][k]
                                         for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual(3600, result['time_deltas']['estimated_full_arrival_seconds'])
        self.assertEqual(result['before']['production'], result['after']['production'])

    def test_D04_preparation_and_dispatch_mode_changes_reuse_existing_rules(self):
        self.node(self.after, 'DeliveryCondition')['preparation_seconds'] = 1800
        result = self.query()
        self.assertEqual((915, 85), tuple(result['after']['response_plan']['forecast'][k]
                                         for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual(1800, result['time_deltas']['estimated_full_arrival_seconds'])
        self.after = copy.deepcopy(self.before)
        self.node(self.after, 'DeliveryCondition')['dispatch_mode'] = 'all_at_once'
        result = self.query()
        self.assertEqual((0, 1000), tuple(result['after']['response_plan']['forecast'][k]
                                         for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual((-960, 960), tuple(result['quantity_deltas'][k]
                                           for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual('all_at_once', result['after']['response_plan']['dispatch_request']['dispatch_mode'])

    def test_D19_exact_deadline_inclusive_and_integer_piece_floor_preserved(self):
        for nodes in (self.before, self.after):
            self.node(nodes, 'DeliveryRound')['due_at'] = '2026-10-27T15:26:40+09:00'
        result = self.query()
        self.assertEqual(1000, result['after']['response_plan']['forecast']['on_time_quantity'])
        self.node(self.after, 'DeliveryCondition')['transport_seconds'] = 3601
        result = self.query()
        self.assertEqual(999, result['after']['response_plan']['forecast']['on_time_quantity'])
        self.assertEqual(-1, result['quantity_deltas']['on_time_quantity'])
        self.assertEqual(1, result['time_deltas']['estimated_full_arrival_seconds'])

    def test_D05_combined_change_is_not_attributed_to_one_fault(self):
        self.advance(680, 7200)
        result = self.query()
        self.assertEqual((840, 160), tuple(result['after']['response_plan']['forecast'][k]
                                         for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual((-120, 120, 5600), (result['quantity_deltas']['on_time_quantity'],
                         result['quantity_deltas']['shortfall_quantity'],
                         result['time_deltas']['estimated_full_arrival_seconds']))
        self.assertIn('no causal attribution', ' '.join(result['limitations']))
        self.assertNotIn('cause', result)

    def test_D06_same_version_requery_has_no_business_changes(self):
        result = self.query(after_version=2)
        self.assertTrue(all(value == 0 for value in result['quantity_deltas'].values()))
        self.assertEqual([], result['changed_inputs'])
        self.assertEqual([], result['response_changes'])
        later = self.query(after_version=2, queried_at='2026-10-28T00:00:00+09:00')
        self.assertEqual(result['quantity_deltas'], later['quantity_deltas'])
        self.assertEqual([], later['response_changes'])

    def test_D07_met_result_still_updates_current_future_and_draft(self):
        for nodes in (self.before, self.after):
            self.node(nodes, 'DeliveryRound')['due_at'] = '2026-10-27T16:00:00+09:00'
        self.advance()
        result = self.query()
        self.assertEqual('met', result['after']['response_plan']['response_state'])
        self.assertEqual((1000, 0), tuple(result['after']['response_plan']['forecast'][k]
                                        for k in ('on_time_quantity', 'shortfall_quantity')))
        self.assertEqual(90, result['quantity_deltas']['currently_available_quantity'])
        self.assertIn('customer_reply', {entry['field'] for entry in result['response_changes']})

    def test_D08_zero_rate_preserves_unknown_instead_of_computing_minus960(self):
        self.advance(600)
        result = self.query()
        self.assertEqual('partially_unknown', result['comparison_status'])
        self.assertIsNone(result['quantity_deltas']['on_time_quantity'])
        self.assertIsNone(result['quantity_deltas']['shortfall_quantity'])
        self.assertIsNone(result['time_deltas']['estimated_full_arrival_seconds'])
        self.assertIsNone(result['time_deltas']['estimated_completion_seconds'])
        self.assertEqual(0, result['after']['production']['good_rate_per_hour'])

    def test_D09_missing_condition_to_known_does_not_use_null_as_zero(self):
        self.before.remove(self.node(self.before, 'DeliveryCondition'))
        result = self.query()
        self.assertEqual('partially_unknown', result['comparison_status'])
        self.assertIsNone(result['quantity_deltas']['on_time_quantity'])
        self.assertIsNone(result['time_deltas']['estimated_full_arrival_seconds'])
        self.assertEqual(960, result['after']['response_plan']['forecast']['on_time_quantity'])
        self.assertEqual(0, result['time_deltas']['estimated_completion_seconds'])
        change = next(c for c in result['changed_inputs'] if c['field'] == 'transport_seconds')
        self.assertFalse(change['before_present'])
        self.assertTrue(change['after_present'])

    def test_D09_historical_review_does_not_mask_unknown_forecast(self):
        for nodes in (self.before, self.after):
            self.node(nodes, 'DeliveryRound')['due_at'] = '2026-10-27T09:00:00+09:00'
            nodes.remove(self.node(nodes, 'DeliveryCondition'))
        result = self.query()
        self.assertEqual('partially_unknown', result['comparison_status'])
        for label in ('before', 'after'):
            response = result[label]['response_plan']
            self.assertEqual('historical_review', response['response_state'])
            self.assertEqual('needs_confirmation', response['forecast']['status'])
            self.assertTrue(response['forecast']['unknowns'])
            self.assertIsNone(response['forecast']['on_time_quantity'])
            self.assertIsNone(response['forecast']['shortfall_quantity'])
        self.assertIsNone(result['quantity_deltas']['on_time_quantity'])
        self.assertIsNone(result['quantity_deltas']['shortfall_quantity'])

    def test_D10_invalid_graph_and_counter_are_not_partial_success(self):
        self.advance(680)
        last = next(n for n in self.after if n['id'] == 'observation:test:CQ06:11')
        last['defective_quantity'] = 71
        with self.assertRaises(ValueError) as found:
            self.query()
        self.assertNotIsInstance(found.exception, ComparisonError)
        last['defective_quantity'] = 70
        last['evidence'] = []
        with self.assertRaises(ValueError):
            self.query()

    def test_D11_invalid_version_values_and_reverse_versions(self):
        for key in ('before_version', 'after_version'):
            for value in (None, True, False, -1, 2.0, '2', [], {}):
                with self.subTest(key=key, value=value):
                    self.error_is('INVALID_VERSION', **{key: value})
        self.error_is('INVALID_VERSION', before_version=4, after_version=3)

    def test_D12_reverse_times_and_same_version_different_instant(self):
        self.error_is('INVALID_TIME', after_as_of='2026-10-27T09:00:00+09:00')
        self.advance()
        self.error_is('INVALID_TIME', after_version=2)
        self.node(self.after, 'DeliveryCondition')['transport_seconds'] = 7200
        self.error_is('INCOMPARABLE_INPUTS', after_version=2, after_as_of=AS_OF)

    def test_D13_equivalent_utc_offset_does_not_change_selection(self):
        result = self.query(before_as_of='2026-10-27T01:00:00Z', after_as_of='2026-10-27T01:00:00+00:00')
        self.assertEqual('compared', result['comparison_status'])
        self.assertTrue(all(v == 0 for v in result['quantity_deltas'].values()))
        for field in ('before_as_of', 'after_as_of', 'queried_at'):
            for stamp in (None, [], {}, '2026-10-27', '2026-10-27T10:00:00'):
                with self.subTest(field=field, stamp=stamp):
                    self.error_is('INVALID_TIME', **{field: stamp})

    def test_D14_stale_requested_snapshot_is_unknown_not_moved_forward(self):
        result = self.query(after_as_of=T11)
        self.assertEqual('partially_unknown', result['comparison_status'])
        self.assertIsNone(result['after']['response_plan']['forecast']['snapshot_id'])
        self.assertIsNone(result['quantity_deltas']['on_time_quantity'])
        self.assertEqual(AS_OF, self.node(self.after, 'FulfillmentSnapshot')['as_of'])

    def test_D14_missing_latest_observation_keeps_partial_state(self):
        self.after.remove(next(n for n in self.after if n['type'] == 'ProductionObservation'
                               and n['observed_at'] == AS_OF))
        result = self.query()
        self.assertEqual('partially_unknown', result['comparison_status'])
        self.assertIsNone(result['time_deltas']['remaining_seconds'])

    def test_D15_identity_changes_rejected_even_when_each_graph_valid(self):
        self.node(self.after, 'Contract')['customer_name'] = 'Another synthetic customer'
        self.assertTrue(validate_input_graph(self.after)['valid'])
        self.error_is('INCOMPARABLE_INPUTS')
        self.after = copy.deepcopy(self.before)
        for n in self.after:
            if n.get('company') == 'DEMO-B':
                n['company'] = 'DEMO-OTHER'
        self.assertTrue(validate_input_graph(self.after)['valid'])
        self.error_is('INCOMPARABLE_INPUTS')

    def test_D16_changed_promise_is_not_a_transport_effect(self):
        for key, value in (('due_at', '2026-10-27T16:00:00+09:00'),
                           ('destination', 'Another destination')):
            with self.subTest(field=key):
                self.after = copy.deepcopy(self.before)
                self.node(self.after, 'DeliveryRound')[key] = value
                self.assertTrue(validate_input_graph(self.after)['valid'])
                self.error_is('INCOMPARABLE_INPUTS')

    def test_D17_other_work_or_allocation_plan_rejected(self):
        work = self.node(self.after, 'ProductionWork')
        old_id = work['id']; work['id'] = 'work:test:unrelated'
        for n in self.after:
            if n.get('production_work_id') == old_id:
                n['production_work_id'] = work['id']
        self.assertTrue(validate_input_graph(self.after)['valid'])
        self.error_is('INCOMPARABLE_INPUTS')
        self.after = copy.deepcopy(self.before)
        self.node(self.after, 'Allocation')['quantity'] = 900
        self.assertTrue(validate_input_graph(self.after)['valid'])
        self.error_is('INCOMPARABLE_INPUTS')

    def test_D17_mixed_actual_shipments_cannot_change_under_condition_comparison(self):
        self.before = self.load(mixed=True); self.after = copy.deepcopy(self.before)
        shipment = next(n for n in self.after if n['type'] == 'Shipment' and 'expected_arrival_at' in n)
        shipment['expected_arrival_at'] = '2026-10-27T13:00:00+09:00'
        self.assertTrue(validate_input_graph(self.after)['valid'])
        self.error_is('INCOMPARABLE_INPUTS')

    def test_D19_whole_target_observed_is_not_a_new_actual_completion(self):
        self.advance(1000)
        latest = next(n for n in self.after if n['id'] == 'observation:test:CQ06:11')
        latest.update(produced_quantity=1100, defective_quantity=100)
        result = self.query()
        production = result['after']['production']
        self.assertEqual('target_observed', production['status'])
        self.assertEqual(0, production['remaining_seconds'])
        self.assertIsNone(production['estimated_completion_at'])
        self.assertIsNone(production['actual_completion_at'])
        self.assertIsNone(result['time_deltas']['estimated_completion_seconds'])

    def test_D20_D21_input_diff_and_provenance_have_both_sides(self):
        self.advance(680, 7200)
        original_before, original_after = copy.deepcopy(self.before), copy.deepcopy(self.after)
        result = self.query()
        self.assertEqual(original_before, self.before)
        self.assertEqual(original_after, self.after)
        self.assertIn('observed_at', {r['field'] for r in result['changed_inputs']})
        self.assertIn('transport_seconds', {r['field'] for r in result['changed_inputs']})
        self.assertIn('good_quantity', {r['field'] for r in result['changed_inputs']})
        for label in ('before', 'after'):
            self.assertTrue(set(result['evidence_by_side'][label]).issubset(
                result['integrity_evidence_by_side'][label]))
        self.assertEqual(sorted(set(result['evidence_by_side']['before']) | set(result['evidence_by_side']['after'])),
                         result['evidence'])
        result['before']['response_plan']['forecast']['promise']['delivery_round']['quantity'] = 1
        self.assertEqual(original_before, self.before)

    def test_D21_irrelevant_catalog_change_not_claimed_as_forecast_change(self):
        self.node(self.after, 'ComponentModel')['name'] += ' test-only text'
        self.assertEqual([], self.query()['changed_inputs'])

    def test_D27_existing_mixed_result_preserves_no_redispatch(self):
        self.before = self.load(mixed=True); self.after = copy.deepcopy(self.before)
        result = self.query()
        dispatch = result['after']['response_plan']['dispatch_request']
        self.assertEqual((100, 150, 750), tuple(dispatch[k] for k in (
            'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity')))
        self.assertTrue(all(r['source_state'] not in ('delivered', 'in_transit') for r in dispatch['items']))
        self.assertEqual(0, result['quantity_deltas']['new_dispatch_quantity'])

    def test_empty_ids_missing_round_and_version_graph_mismatch_rejected(self):
        for value in (None, [], {}, '', ' '):
            self.error_is('INVALID_INPUT', delivery_round_id=value)
        with self.assertRaises(KeyError):
            self.query(delivery_round_id='delivery-round:missing')
        self.node(self.after, 'DeliveryCondition')['transport_seconds'] = 7200
        self.error_is('INCOMPARABLE_INPUTS', after_version=2)

    def test_reuses_existing_response_and_production_without_input_mutation(self):
        from ontologics.order_fulfillment.production import production_completion
        self.advance()
        with (patch('ontologics.order_fulfillment.response_comparison.response_plan', wraps=response_plan) as response,
              patch('ontologics.order_fulfillment.response_comparison.production_completion', wraps=production_completion) as production):
            self.query()
        self.assertEqual(2, response.call_count)
        self.assertEqual(2, production.call_count)

    def test_D01_D05_D08_D21_new_source_backed_cases_match_static_golden(self):
        from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, CASES, load_demo_recalculation
        golden = json.loads((ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())
        for case in CASES:
            with self.subTest(case=case):
                index = {n['id']: copy.deepcopy(n) for n in self.before}
                index.update({n['id']: n for n in load_demo_recalculation(case)})
                self.after = list(index.values())
                self.after_at = AFTER_AS_OF[case]
                result = self.query()
                expected = golden['cases'][case]
                self.assertEqual(expected['quantity_deltas'], result['quantity_deltas'])
                self.assertEqual(expected['time_deltas'], result['time_deltas'])
                for label, expected_side in (('before', golden['before']), ('after', expected)):
                    side = result[label]
                    for field, value in expected_side['production'].items():
                        self.assertEqual(value, side['production'][field], (case, label, 'production', field))
                    for section in ('forecast', 'dispatch_request'):
                        for field, value in expected_side[section].items():
                            self.assertEqual(value, side['response_plan'][section][field],
                                             (case, label, section, field))
                self.assertTrue(any('CQ06' in eid for eid in result['evidence_by_side']['after']))

    def test_T09_production_deltas_match_independent_five_metric_expectations(self):
        from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, load_demo_recalculation
        fields = ('good_rate_per_hour', 'produced_rate_per_hour', 'recent_defect_ratio',
                  'remaining_quantity', 'latest_good_quantity')
        # Hand arithmetic against 10:00: 90 good/h, 100 total/h, 10% defective,
        # 400 remaining and 600 completed. Do not derive expected deltas by
        # subtracting the function's own before/after output.
        expected = {
            'continued11': (0, 0, 0, -90, 90),
            'defects11': (-10, 0, 0.1, -80, 80),
            'transport2h': (0, 0, 0, 0, 0),
            'zero_rate11': (-90, 0, 0.9, 0, 0),
        }
        for case, values in expected.items():
            with self.subTest(case=case):
                index = {n['id']: copy.deepcopy(n) for n in self.before}
                index.update({n['id']: n for n in load_demo_recalculation(case)})
                self.after = list(index.values())
                self.after_at = AFTER_AS_OF[case]
                deltas = self.query()['production_deltas']
                self.assertEqual(set(fields), set(deltas))
                self.assertEqual(dict(zip(fields, values)), deltas)

    def test_T09_missing_latest_production_deltas_are_null_not_zero(self):
        self.after.remove(next(n for n in self.after if n['type'] == 'ProductionObservation'
                               and n['observed_at'] == AS_OF))
        result = self.query()
        self.assertEqual('partially_unknown', result['comparison_status'])
        self.assertEqual({
            'good_rate_per_hour': None, 'produced_rate_per_hour': None,
            'recent_defect_ratio': None, 'remaining_quantity': None,
            'latest_good_quantity': None,
        }, result['production_deltas'])

    def test_T09_changed_inputs_keep_exact_values_units_presence_and_side_evidence(self):
        from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, load_demo_recalculation
        for case in ('defects11', 'transport2h'):
            with self.subTest(case=case):
                index = {n['id']: copy.deepcopy(n) for n in self.before}
                index.update({n['id']: n for n in load_demo_recalculation(case)})
                self.after = list(index.values())
                self.after_at = AFTER_AS_OF[case]
                rows = {(row['node_id'], row['field']): row for row in self.query()['changed_inputs']}
                if case == 'defects11':
                    self.assertEqual(dict(
                        node_id='work:demo:CQ04:base', field='completed_quantity',
                        before=600, after=680, before_present=True, after_present=True, unit='piece',
                        evidence_by_side=dict(
                            before=['evidence:demo:CQ04:base:work', 'evidence:demo:CQ04:base:assumptions'],
                            after=['evidence:demo:CQ06:defects11:work', 'evidence:demo:CQ06:update-assumptions'])),
                        rows[('work:demo:CQ04:base', 'completed_quantity')])
                    for field, value in (('good_quantity', 680), ('unit', 'piece')):
                        self.assertEqual(dict(
                            node_id='observation:demo:CQ06:defects11:11', field=field,
                            before=None, after=value, before_present=False, after_present=True, unit='piece',
                            evidence_by_side=dict(before=[], after=['evidence:demo:CQ06:defects11:observation-11'])),
                            rows[('observation:demo:CQ06:defects11:11', field)])
                else:
                    self.assertEqual(dict(
                        node_id='condition:demo:CQ04:base', field='transport_seconds',
                        before=3600, after=7200, before_present=True, after_present=True, unit=None,
                        evidence_by_side=dict(
                            before=['evidence:demo:CQ04:base:condition', 'evidence:demo:CQ04:base:assumptions'],
                            after=['evidence:demo:CQ06:transport2h:condition', 'evidence:demo:CQ06:update-assumptions'])),
                        rows[('condition:demo:CQ04:base', 'transport_seconds')])

    def test_T09_added_condition_diff_distinguishes_missing_from_zero(self):
        self.before.remove(self.node(self.before, 'DeliveryCondition'))
        rows = self.query()['changed_inputs']
        self.assertEqual([dict(
            node_id='condition:demo:CQ04:base', field='preparation_seconds',
            before=None, after=0, before_present=False, after_present=True, unit=None,
            evidence_by_side=dict(before=[], after=[
                'evidence:demo:CQ04:base:condition', 'evidence:demo:CQ04:base:assumptions']))],
            [row for row in rows if row['node_id'] == 'condition:demo:CQ04:base'
             and row['field'] == 'preparation_seconds'])

    def test_T09_response_changes_preserve_full_sides_and_updated_draft_facts(self):
        self.advance(680)
        result = self.query()
        before, after = (result[label]['response_plan'] for label in ('before', 'after'))
        changes = result['response_changes']
        self.assertEqual(['customer_reply', 'dispatch_request', 'actions'], [row['field'] for row in changes])
        for row in changes:
            self.assertEqual({'field', 'before', 'after'}, set(row))
            self.assertEqual(before[row['field']], row['before'])
            self.assertEqual(after[row['field']], row['after'])
        reply = next(row['after'] for row in changes if row['field'] == 'customer_reply')
        for expected in ('920piece', '80piece', '2026-10-27T16:00:00+09:00'):
            self.assertIn(expected, reply)
        request = next(row['after'] for row in changes if row['field'] == 'dispatch_request')
        self.assertEqual((680, 320, 920, 80), tuple(request[key] for key in (
            'currently_available_quantity', 'future_quantity',
            'on_time_new_dispatch_quantity', 'later_new_dispatch_quantity')))
        actions = next(row['after'] for row in changes if row['field'] == 'actions')
        self.assertEqual([80], [action['quantity'] for action in actions if action['code'] == 'AGREE_FOLLOWUP'])
        self.assertEqual(before['confirmations'], after['confirmations'])

    def test_T09_valid_promise_quantity_configuration_and_contract_changes_are_incomparable(self):
        for change in ('quantity', 'configuration', 'contract'):
            with self.subTest(change=change):
                self.after = copy.deepcopy(self.before)
                if change == 'quantity':
                    self.node(self.after, 'ContractItem')['quantity'] = 1100
                    self.node(self.after, 'DeliveryRound')['quantity'] = 1100
                elif change == 'configuration':
                    for node in self.after:
                        if (node['type'] in {'ContractItem', 'ProductionWork', 'FinishedGoodsStock'}
                                and node.get('configuration_id') == 'config:esp32-poe:L:WROOM'):
                            node['configuration_id'] = 'config:esp32-poe:L1:WROOM'
                else:
                    contract = copy.deepcopy(self.node(self.after, 'Contract'))
                    contract['id'] = 'contract:test:CQ06:replacement'
                    self.after.append(contract)
                    self.node(self.after, 'ContractItem')['contract_id'] = contract['id']
                report = validate_input_graph(self.after)
                self.assertTrue(report['valid'], report)
                self.error_is('INCOMPARABLE_INPUTS')

    def test_T09_unsupported_unit_fails_validation_before_comparison(self):
        # piece is the only supported unit, so a valid second graph with a
        # different unit cannot be manufactured to test the fingerprint path.
        for node in self.after:
            if node.get('unit') == 'piece' and node['type'] in {
                    'ContractItem', 'DeliveryRound', 'ProductionWork', 'ProductionObservation', 'Allocation'}:
                node['unit'] = 'box'
        report = validate_input_graph(self.after)
        self.assertFalse(report['valid'])
        self.assertIn('UNSUPPORTED_UNIT', {error['code'] for error in report['errors']})
        with self.assertRaises(ValueError) as found:
            self.query()
        self.assertNotIsInstance(found.exception, ComparisonError)

    def test_T09_mixed_comparison_forecasts_its_fully_allocated_work_without_counting_stock_as_production(self):
        self.before = self.load(mixed=True)
        self.after = copy.deepcopy(self.before)
        result = self.query()
        self.assertEqual('compared', result['comparison_status'])
        for label in ('before', 'after'):
            production = result[label]['production']
            self.assertEqual('forecast', production['status'])
            self.assertEqual([], production['unknowns'])
            self.assertEqual((500, 200, 300, '2026-10-27T13:20:00+09:00'), tuple(
                production[key] for key in ('target_quantity', 'latest_good_quantity',
                                           'remaining_quantity', 'estimated_completion_at')))
            request = result[label]['response_plan']['dispatch_request']
            self.assertEqual((100, 150, 750), tuple(request[key] for key in (
                'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity')))
        self.assertEqual(0, result['quantity_deltas']['new_dispatch_quantity'])
        self.assertTrue(all(value == 0 for value in result['production_deltas'].values()))
