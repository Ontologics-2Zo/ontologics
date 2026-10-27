"""Independent expected answers and boundary scenarios for B-CQ03, without LLM/DB."""
import copy
import hashlib
import json
import unittest

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.model import ROOT, validate_graph
from ontologics.order_fulfillment.production import (AS_OF, ROUND, SNAPSHOT, WORK, SOURCE_PATH, SOURCE_SHA256,
    calculate_completion, load_demo_production, production_completion)


class ProductionCompletionTests(unittest.TestCase):
    def setUp(self):
        self.nodes = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract() + load_demo_production()
        self.index = {n['id']: n for n in self.nodes}
        self.work = self.index[WORK]
        self.snapshot = self.index[SNAPSHOT]
        self.previous = self.index['observation:demo:CQ03:09']
        self.latest = self.index['observation:demo:CQ03:10']
        self.allocation = self.index['allocation:demo:CQ03:main']

    def query(self, **kwargs):
        args = dict(nodes=self.nodes, production_work_id=WORK, delivery_round_id=ROUND,
                    as_of=AS_OF, queried_at='2026-10-27T10:10:00+09:00')
        args.update(kwargs)
        return production_completion(**args)

    def remove(self, nid):
        self.nodes = [n for n in self.nodes if n['id'] != nid]

    def assert_unknown(self, response, code):
        self.assertEqual('needs_confirmation', response['status'])
        self.assertIn(code, [u['code'] for u in response['unknowns']])
        self.assertIsNone(response['estimated_completion_at'])

    def test_01_independent_golden_and_read_without_mutation(self):
        before = copy.deepcopy(self.nodes)
        expected = json.loads((ROOT / 'data/order_fulfillment/b_production_expected.json').read_text())
        response = self.query()
        for key in ('status', 'production_work_id', 'delivery_round_id', 'snapshot_id', 'configuration_id',
                    'unit', 'target_quantity', 'latest_good_quantity', 'selected_observation_ids',
                    'good_rate_per_hour', 'produced_rate_per_hour', 'recent_defect_ratio',
                    'remaining_quantity', 'remaining_seconds', 'estimated_completion_at'):
            self.assertEqual(expected[key], response[key], key)
        self.assertFalse(response['fulfillment_assessed'])
        self.assertTrue(response['synthetic'])
        self.assertEqual([], response['unknowns'])
        self.assertEqual(before, self.nodes)

    def test_02_old_and_unrelated_work_observations_do_not_enter_interval_or_citations(self):
        older = dict(self.previous, id='observation:older', observed_at='2026-10-27T08:00:00+09:00',
                     produced_quantity=400, good_quantity=360, defective_quantity=40)
        # A distinct source locator is present but must not be cited for the selected interval.
        older_evidence = dict(self.index[self.previous['evidence'][0]], id='evidence:older')
        older['evidence'] = [older_evidence['id']]
        other_work = dict(self.work, id='work:other')
        other = dict(self.latest, id='observation:other', production_work_id='work:other')
        self.nodes += [older_evidence, older, other_work, other]
        r = self.query()
        self.assertEqual(['observation:demo:CQ03:09', 'observation:demo:CQ03:10'], r['selected_observation_ids'])
        self.assertEqual(90, r['good_rate_per_hour'])
        self.assertNotIn('evidence:older', r['evidence'])

    def test_03_storage_order_is_irrelevant_but_reversed_function_pair_is_invalid(self):
        r = self.query(nodes=list(reversed(self.nodes)))
        self.assertEqual('2026-10-27T14:26:40+09:00', r['estimated_completion_at'])
        with self.assertRaises(ValueError):
            calculate_completion(self.latest, self.previous, 1000)

    def test_04_query_execution_time_does_not_move_observation_or_forecast(self):
        r = self.query(queried_at='2026-10-27T11:00:00+09:00')
        self.assertEqual(AS_OF, r['observation_as_of'])
        self.assertEqual(600, r['latest_good_quantity'])
        self.assertEqual('2026-10-27T14:26:40+09:00', r['estimated_completion_at'])

    def test_05_other_as_of_is_unknown_without_reconstruction(self):
        r = self.query(as_of='2026-10-27T11:00:00+09:00')
        self.assert_unknown(r, 'SNAPSHOT_NOT_CONFIRMED')
        self.assertEqual([AS_OF], r['available_as_of'])
        self.assertIsNone(r['latest_good_quantity'])
        self.assertIsNone(r['remaining_quantity'])

    def test_06_only_latest_observation_preserves_known_remainder_but_no_rate(self):
        self.remove(self.previous['id'])
        r = self.query()
        self.assert_unknown(r, 'PREVIOUS_OBSERVATION_REQUIRED')
        self.assertEqual(400, r['remaining_quantity'])
        for key in ('good_rate_per_hour', 'recent_defect_ratio', 'remaining_seconds'):
            self.assertIsNone(r[key], key)

    def test_07_missing_latest_does_not_substitute_work_count_or_compare_different_instants(self):
        self.remove(self.latest['id'])
        self.assertTrue(validate_graph(self.nodes)['valid'])
        r = self.query()
        self.assert_unknown(r, 'LATEST_OBSERVATION_REQUIRED')
        self.assertEqual(600, r['work_completed_quantity'])
        self.assertEqual(AS_OF, r['work_observed_at'])
        self.assertIsNone(r['latest_good_quantity'])
        self.assertIsNone(r['remaining_quantity'])
        self.assertEqual([], r['selected_observation_ids'])

    def test_08_no_production_means_zero_rate_and_undefined_defect_ratio(self):
        self.previous.update(produced_quantity=650, good_quantity=600, defective_quantity=50)
        r = self.query()
        self.assert_unknown(r, 'ZERO_GOOD_PRODUCTION_RATE')
        self.assertEqual(0, r['good_rate_per_hour'])
        self.assertIsNone(r['recent_defect_ratio'])
        self.assertIsNone(r['remaining_seconds'])

    def test_09_all_defective_increment_never_yields_completion(self):
        self.latest.update(produced_quantity=650, good_quantity=510, defective_quantity=140)
        self.work['completed_quantity'] = 510
        r = self.query()
        self.assert_unknown(r, 'ZERO_GOOD_PRODUCTION_RATE')
        self.assertEqual(1, r['recent_defect_ratio'])
        self.assertEqual(0, r['good_rate_per_hour'])
        self.assertEqual(490, r['remaining_quantity'])

    def test_10_goal_observed_with_one_observation_never_creates_actual_completion(self):
        self.remove(self.previous['id'])
        self.latest.update(produced_quantity=1050, good_quantity=1000, defective_quantity=50)
        self.work['completed_quantity'] = 1000
        before = copy.deepcopy(self.nodes)
        r = self.query()
        self.assertEqual('target_observed', r['status'])
        self.assertEqual((0, 0), (r['remaining_quantity'], r['remaining_seconds']))
        self.assertEqual(AS_OF, r['target_observed_at'])
        self.assertIsNone(r['actual_completion_at'])
        self.assertIsNone(r['estimated_completion_at'])
        self.assertIsNone(r['good_rate_per_hour'])
        self.assertEqual(before, self.nodes)
        self.assertEqual('in_progress', self.work['production_state'])

    def test_11_over_target_is_refused_without_clipping(self):
        self.latest.update(produced_quantity=1051, good_quantity=1001, defective_quantity=50)
        self.work['completed_quantity'] = 1001
        with self.assertRaises(ValueError):
            self.query()
        self.assertEqual(1001, self.latest['good_quantity'])

    def test_12_bad_total_is_refused(self):
        self.latest['produced_quantity'] = 649
        with self.assertRaises(ValueError):
            self.query()

    def test_13_invalid_older_observation_cannot_be_hidden_by_latest_two_valid_rows(self):
        self.nodes.append(dict(self.previous, id='observation:bad-older', observed_at='2026-10-27T08:00:00+09:00',
                               produced_quantity=560, good_quantity=520, defective_quantity=40))
        with self.assertRaises(ValueError) as error:
            self.query()
        self.assertIn('PRODUCTION_COUNTER_DECREASED', str(error.exception))

    def test_14_same_utc_instant_duplicate_is_refused(self):
        self.nodes.append(dict(self.latest, id='observation:duplicate', observed_at='2026-10-27T01:00:00Z'))
        with self.assertRaises(ValueError) as error:
            self.query()
        self.assertIn('DUPLICATE_PRODUCTION_OBSERVATION', str(error.exception))

    def test_15_pure_calculation_rejects_noninteger_counters_and_zero_target(self):
        for bad in (-1, True, False, 600.0, '600', None, [], {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                calculate_completion(self.previous, dict(self.latest, good_quantity=bad), 1000)
        with self.assertRaises(ValueError):
            calculate_completion(self.previous, self.latest, 0)
        # Produced650 > future good-target610 is legal: defective units are not good units.
        self.assertEqual(10, calculate_completion(self.previous, self.latest, 610)['remaining_quantity'])

    def test_16_offset_required_and_equivalent_utc_request_same_result(self):
        r = self.query(as_of='2026-10-27T01:00:00Z')
        self.assertEqual(AS_OF, r['as_of'])
        self.assertEqual('2026-10-27T14:26:40+09:00', r['estimated_completion_at'])
        for bad in ('2026-10-27', '2026-10-27T10:00:00', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.query(as_of=bad)

    def test_17_missing_or_wrong_type_work_and_round_do_not_fall_back(self):
        for key, bad in (('production_work_id', 'work:missing'), ('production_work_id', ROUND),
                         ('delivery_round_id', 'round:missing'), ('delivery_round_id', WORK)):
            with self.subTest(key=key, bad=bad), self.assertRaises(KeyError):
                self.query(**{key:bad})

    def test_18_same_instant_work_and_good_counts_conflict(self):
        self.latest.update(good_quantity=599, defective_quantity=51)
        with self.assertRaises(ValueError) as error:
            self.query()
        self.assertIn('PRODUCTION_COMPLETION_MISMATCH', str(error.exception))

    def test_19_start_and_snapshot_limits_not_silently_filtered(self):
        for bad in ('2026-10-27T07:59:59+09:00', '2026-10-27T11:00:00+09:00'):
            with self.subTest(bad=bad):
                self.previous['observed_at'] = bad
                with self.assertRaises(ValueError):
                    self.query()

    def test_20_real_design_evidence_cannot_attest_to_fictional_production(self):
        self.latest['evidence'] = list(self.index['config:esp32-poe:L:WROOM']['evidence'])
        with self.assertRaises(ValueError) as error:
            self.query()
        self.assertIn('EVIDENCE_ORIGIN_MISMATCH', str(error.exception))

    def test_21_missing_partial_split_multiple_work_or_target_mismatch_allocation_is_unsupported(self):
        original = copy.deepcopy(self.nodes)
        for scenario in ('missing', 'partial', 'split', 'multiple_work', 'target_mismatch'):
            with self.subTest(scenario=scenario):
                self.nodes = copy.deepcopy(original)
                idx = {n['id']:n for n in self.nodes}
                allocation = idx['allocation:demo:CQ03:main']
                if scenario == 'missing':
                    self.remove(allocation['id'])
                elif scenario == 'partial':
                    allocation['quantity'] = 500
                elif scenario == 'split':
                    allocation['quantity'] = 700
                    item = dict(idx['contract-item:demo:B-01:1'], id='item:other', quantity=300)
                    other_round = dict(idx[ROUND], id='round:other', contract_item_id='item:other', quantity=300)
                    idx[SNAPSHOT]['round_ids'].append('round:other')
                    other_allocation = dict(allocation, id='allocation:other', delivery_round_id='round:other', quantity=300)
                    self.nodes += [item, other_round, other_allocation]
                elif scenario == 'multiple_work':
                    allocation['quantity'] = 700
                    other_work = dict(idx[WORK], id='work:other', completed_quantity=0)
                    self.nodes += [other_work, dict(allocation, id='allocation:other', production_work_id='work:other', quantity=300)]
                else:
                    idx[ROUND]['quantity'] = 900
                    allocation['quantity'] = 900
                report = validate_graph(self.nodes)
                self.assertTrue(report['valid'], report)
                self.assert_unknown(self.query(), 'SINGLE_WORK_FULL_ALLOCATION_REQUIRED')

    def test_22_exact_ratio_final_second_ceiling(self):
        self.previous.update(produced_quantity=993, good_quantity=992, defective_quantity=1)
        self.latest.update(produced_quantity=1000, good_quantity=999, defective_quantity=1)
        self.work['completed_quantity'] = 999
        r = self.query()
        self.assertEqual(7, r['good_rate_per_hour'])
        self.assertEqual(1, r['remaining_quantity'])
        self.assertEqual(515, r['remaining_seconds'])
        self.assertEqual('2026-10-27T10:08:35+09:00', r['estimated_completion_at'])

    def test_23_independent_eleven_hour_scenarios_recompute_recent_interval(self):
        for good, bad, speed, finish in ((690,60,90,'2026-10-27T14:26:40+09:00'),
                                        (680,70,80,'2026-10-27T15:00:00+09:00')):
            with self.subTest(good=good):
                nodes = copy.deepcopy(self.nodes)
                idx = {n['id']: n for n in nodes}
                now = '2026-10-27T11:00:00+09:00'
                idx[WORK].update(observed_at=now, completed_quantity=good)
                idx[SNAPSHOT]['as_of'] = now
                nodes.append(dict(self.latest, id='observation:11', observed_at=now,
                                  produced_quantity=750, good_quantity=good, defective_quantity=bad))
                r = self.query(nodes=nodes, as_of=now)
                self.assertEqual(speed, r['good_rate_per_hour'])
                self.assertEqual(finish, r['estimated_completion_at'])
                self.assertEqual(['observation:demo:CQ03:10','observation:11'], r['selected_observation_ids'])

    def test_24_unstarted_work_withholds_prediction_without_fake_start(self):
        self.remove(self.previous['id']); self.remove(self.latest['id'])
        self.work.pop('started_at')
        self.work['completed_quantity'] = 0
        for state in ('ready','waiting'):
            self.work['production_state'] = state
            self.assertTrue(validate_graph(self.nodes)['valid'])
            self.assert_unknown(self.query(), 'WORK_NOT_STARTED')
        self.work['started_at'] = '2026-10-27T08:00:00+09:00'
        with self.assertRaises(ValueError):
            self.query()

    def test_stale_work_and_corresponding_old_observation_do_not_answer_new_snapshot(self):
        self.remove(self.latest['id'])
        self.work.update(observed_at=self.previous['observed_at'], completed_quantity=510)
        self.assertTrue(validate_graph(self.nodes)['valid'])
        r = self.query()
        self.assert_unknown(r, 'WORK_STATE_NOT_CURRENT')
        self.assertIsNone(r['remaining_quantity'])

    def test_all_five_relevant_coverage_flags_are_required(self):
        from ontologics.order_fulfillment.production import REQUIRED_CONFIRMATIONS
        for key in REQUIRED_CONFIRMATIONS:
            with self.subTest(key=key):
                saved=self.snapshot['confirmations'].pop(key)
                self.assertTrue(validate_graph(self.nodes)['valid'])
                self.assert_unknown(self.query(),'COVERAGE_NOT_CONFIRMED')
                self.snapshot['confirmations'][key]=saved
        # Unclaimed stock/shipment event coverage is not invented or required.
        self.assertNotIn('shipment_events_complete', self.snapshot['confirmations'])
        self.assertEqual('forecast', self.query()['status'])

    def test_subsecond_interval_is_not_truncated_before_final_ceiling(self):
        previous=dict(self.previous, observed_at='2026-10-27T09:59:58.999999+09:00',
                      produced_quantity=0, good_quantity=0, defective_quantity=0)
        latest=dict(self.latest, produced_quantity=1, good_quantity=1, defective_quantity=0)
        r=calculate_completion(previous,latest,2)
        self.assertEqual(1.000001, r['interval_seconds'])
        self.assertEqual({'numerator':3600000000,'denominator':1000001}, r['good_rate_fraction'])
        self.assertEqual(2,r['remaining_seconds'])
        self.assertEqual('2026-10-27T10:00:02+09:00',r['estimated_completion_at'])

    def test_pure_calculation_requires_explicit_work_identity_and_time(self):
        for bad in (None, '', '  ', [], {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                calculate_completion(dict(self.previous, production_work_id=bad),
                                     dict(self.latest, production_work_id=bad), 1000)
        for field in ('production_work_id', 'observed_at'):
            previous, latest = dict(self.previous), dict(self.latest)
            previous.pop(field); latest.pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                calculate_completion(previous, latest, 1000)

    def test_shared_source_and_assumptions_are_pinned_and_graph_has_no_forecast_facts(self):
        self.assertEqual(SOURCE_SHA256, hashlib.sha256((ROOT/SOURCE_PATH).read_bytes()).hexdigest())
        r=self.query()
        expected=json.loads((ROOT/'data/order_fulfillment/b_production_expected.json').read_text())
        self.assertTrue(set(expected['assumptions_evidence']) <= set(r['evidence']))
        self.assertTrue(all(a['kind']=='calculation_condition' for a in r['assumptions']))
        self.assertFalse(any(n['type']=='Forecast' for n in self.nodes))


if __name__ == '__main__':
    unittest.main()
