"""Independent CQ04 deadlines, mixed source accounting, and incomplete knowledge; no LLM/DB."""
import copy
import json
import unittest

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.order_fulfillment.delivery_forecast import delivery_forecast
from ontologics.model import ROOT, instant
from ontologics.order_fulfillment.schema_validation import validate_input_graph

AS_OF = '2026-10-27T10:00:00+09:00'
ROUND = 'delivery-round:demo:B-01:1'


class DeliveryForecastTests(unittest.TestCase):
    def setUp(self):
        self.load()

    def load(self, mixed=False):
        self.nodes = (json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
                      + load_demo_contract() + load_demo_delivery(mixed=mixed))
        self.index = {n['id']: n for n in self.nodes}
        self.snapshot = next(n for n in self.nodes if n['type'] == 'FulfillmentSnapshot')
        self.condition = next(n for n in self.nodes if n['type'] == 'DeliveryCondition')
        self.work = next(n for n in self.nodes if n['type'] == 'ProductionWork')
        self.observations = sorted((n for n in self.nodes if n['type'] == 'ProductionObservation'),
                                   key=lambda n: n['observed_at'])
        self.previous, self.latest = self.observations
        self.delivery = self.index[ROUND]

    def query(self, **changes):
        args = dict(nodes=self.nodes, delivery_round_id=ROUND, as_of=AS_OF,
                    queried_at='2026-10-27T10:10:00+09:00')
        args.update(changes)
        return delivery_forecast(**args)

    def remove(self, *ids):
        self.nodes[:] = [n for n in self.nodes if n['id'] not in ids]

    def expect(self, result, ontime, full, promised=1000):
        self.assertEqual(ontime, result['on_time_quantity'], result)
        self.assertEqual(promised-ontime, result['shortfall_quantity'], result)
        self.assertEqual('met' if ontime == promised else 'shortfall', result['deadline_status'])
        if full:
            self.assertEqual(instant('2026-10-27T' + full + '+09:00'),
                             instant(result['estimated_full_arrival_at']))
        else:
            self.assertIsNone(result['estimated_full_arrival_at'])

    def unknown(self, result):
        self.assertEqual('needs_confirmation', result['deadline_status'], result)
        self.assertIsNone(result['on_time_quantity'])
        self.assertIsNone(result['shortfall_quantity'])
        self.assertIsNone(result['estimated_full_arrival_at'])
        self.assertTrue(result['unknowns'])

    def test_D01_base_independent_golden_and_read_only_result(self):
        before = copy.deepcopy(self.nodes)
        result = self.query()
        golden = json.loads((ROOT / 'data/order_fulfillment/b_delivery_expected.json').read_text())
        for key in ('deadline_status', 'on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at'):
            self.assertEqual(golden[key], result[key], key)
        self.expect(result, 960, '15:26:40')
        self.assertEqual('delivery-0.1', result['forecast_rule_version'])
        self.assertEqual('conditional_arrival_forecast', result['assessment_scope'])
        self.assertTrue(result['synthetic'])
        self.assertEqual(before, self.nodes)

    def test_D02_later_deadline_can_cover_full_target(self):
        self.delivery['due_at'] = '2026-10-27T16:00:00+09:00'
        self.expect(self.query(), 1000, '15:26:40')

    def test_D03_longer_transport_reduces_ontime_count(self):
        self.condition['transport_seconds'] = 7200
        self.expect(self.query(), 870, '16:26:40')

    def test_D04_preparation_applies_before_transport(self):
        self.condition['preparation_seconds'] = 1800
        self.expect(self.query(), 915, '15:56:40')

    def test_D05_D06_all_at_once_requires_entire_round_ready(self):
        self.condition['dispatch_mode'] = 'all_at_once'
        self.expect(self.query(), 0, '15:26:40')
        self.delivery['due_at'] = '2026-10-27T16:00:00+09:00'
        self.expect(self.query(), 1000, '15:26:40')

    def test_D07_customer_arrival_deadline_includes_exact_boundary(self):
        for due, expected in [('15:26:39',999), ('15:26:40',1000), ('15:26:41',1000)]:
            with self.subTest(due=due):
                self.delivery['due_at'] = '2026-10-27T' + due + '+09:00'
                self.expect(self.query(), expected, '15:26:40')

    def test_D08_D09_no_backdated_dispatch_of_current_completed_units(self):
        self.delivery['due_at'] = '2026-10-27T10:59:59+09:00'
        self.expect(self.query(), 0, '15:26:40')
        self.delivery['due_at'] = '2026-10-27T11:00:00+09:00'
        self.expect(self.query(), 600, '15:26:40')

    def test_D10_fractional_deadline_matches_rounded_ready_seconds(self):
        self.work['completed_quantity'] = 999
        self.previous.update(good_quantity=992, defective_quantity=40, produced_quantity=1032)
        self.latest.update(good_quantity=999, defective_quantity=50, produced_quantity=1049)
        self.delivery['due_at'] = '2026-10-27T11:08:34.900000+09:00'
        self.expect(self.query(), 999, '11:08:35')
        self.delivery['due_at'] = '2026-10-27T11:08:35+09:00'
        self.expect(self.query(), 1000, '11:08:35')

    def test_D11_long_horizon_never_invents_more_than_target(self):
        self.delivery['due_at'] = '2026-10-28T23:00:00+09:00'
        self.expect(self.query(), 1000, '15:26:40')

    def test_D12_zero_durations_are_explicit_valid_conditions(self):
        self.condition.update(preparation_seconds=0, transport_seconds=0)
        self.delivery['due_at'] = '2026-10-27T14:00:00+09:00'
        self.expect(self.query(), 960, '14:26:40')

    def test_D13_observed_target_needs_no_previous_rate_or_actual_completion_event(self):
        self.work['completed_quantity'] = 1000
        self.latest.update(good_quantity=1000, produced_quantity=1050)
        self.remove(self.previous['id'])
        before = copy.deepcopy(self.nodes)
        self.expect(self.query(), 1000, '11:00:00')
        self.assertEqual(before, self.nodes)
        self.assertNotIn('actual_completion_at', self.work)

    def test_D14_zero_good_rate_does_not_mean_permanent_zero_future(self):
        self.previous.update(good_quantity=600, produced_quantity=640)
        result = self.query()
        self.unknown(result)
        self.assertTrue(any(row.get('known_available_quantity') == 600 for row in result['breakdown']))

    def test_D15_missing_previous_latest_and_all_observations_remain_unknown(self):
        for which in ('previous', 'latest', 'all'):
            with self.subTest(missing=which):
                self.load()
                ids = [self.previous['id']] if which == 'previous' else [self.latest['id']]
                if which == 'all': ids = [self.previous['id'], self.latest['id']]
                self.remove(*ids)
                self.assertTrue(validate_input_graph(self.nodes)['valid'])
                self.unknown(self.query())

    def test_D15_old_conflicting_row_cannot_be_hidden_by_latest_selection(self):
        older = dict(self.previous, id='observation:test:CQ04:older',
                     observed_at='2026-10-27T08:30:00+09:00', produced_quantity=1050,
                     good_quantity=1000, defective_quantity=50)
        self.nodes.append(older)
        with self.assertRaises(ValueError): self.query()

    def test_D16_missing_entire_condition_is_unknown_not_zero_transport(self):
        self.remove(self.condition['id'])
        self.assertTrue(validate_input_graph(self.nodes)['valid'])
        self.unknown(self.query())

    def test_D19_equivalent_utc_and_storage_order_do_not_change_forecast(self):
        self.expect(self.query(as_of='2026-10-27T01:00:00Z', nodes=list(reversed(self.nodes))),
                    960, '15:26:40')

    def test_D20_different_requested_snapshot_is_not_reconstructed(self):
        self.unknown(self.query(as_of='2026-10-27T11:00:00+09:00'))
        for value in ('2026-10-27', '2026-10-27T10:00:00', None, [], {}):
            with self.subTest(as_of=value), self.assertRaises((TypeError, ValueError)):
                self.query(as_of=value)

    def test_D20_stale_work_state_does_not_become_current(self):
        self.remove(self.latest['id'])
        self.work.update(observed_at='2026-10-27T09:00:00+09:00', completed_quantity=510)
        self.assertTrue(validate_input_graph(self.nodes)['valid'])
        self.unknown(self.query())

    def test_D23_round_and_all_source_allocation_caps_checked_before_calculating(self):
        allocation = next(n for n in self.nodes if n['type'] == 'Allocation')
        allocation['quantity'] = 1001
        with self.assertRaises(ValueError): self.query()
        allocation['quantity'] = 1000
        other_round = dict(self.delivery, id=ROUND + ':other')
        self.snapshot['round_ids'].append(other_round['id'])
        other_allocation = dict(allocation, id=allocation['id'] + ':other',
                                delivery_round_id=other_round['id'], quantity=1)
        self.nodes += [other_round, other_allocation]
        with self.assertRaises(ValueError): self.query()

    def test_D24_actual_arrival_before_equal_or_after_deadline(self):
        for arrived, expected in [('13:59:59',100), ('14:00:00',100), ('14:00:01',0)]:
            with self.subTest(arrived=arrived):
                self.load(mixed=True)
                shipment = next(n for n in self.nodes if n['type'] == 'Shipment' and 'arrived_at' in n)
                aid = shipment['allocation_id']
                allocation = self.index[aid]
                source_id = allocation['stock_id']
                self.nodes[:] = [n for n in self.nodes if n['type'] not in
                    ('ProductionWork','ProductionObservation','Allocation','Shipment','FinishedGoodsStock')
                    or n['id'] in (shipment['id'],aid,source_id)]
                self.snapshot['as_of'] = '2026-10-27T15:00:00+09:00'
                self.delivery['due_at'] = '2026-10-27T14:00:00+09:00'
                shipment['arrived_at'] = '2026-10-27T' + arrived + '+09:00'
                result = self.query(as_of=self.snapshot['as_of'])
                self.assertEqual(expected, result['on_time_quantity'], result)
                self.assertEqual(1000-expected, result['shortfall_quantity'])

    def test_D25_D26_mixed_independent_golden_and_no_double_count(self):
        self.load(mixed=True)
        golden = json.loads((ROOT / 'data/order_fulfillment/b_delivery_mixed_expected.json').read_text())
        result = self.query()
        for key in ('deadline_status','on_time_quantity','shortfall_quantity','estimated_full_arrival_at'):
            self.assertEqual(golden[key], result[key], key)
        self.expect(result,1000,'14:20:00')
        self.assertEqual(4,len(result['breakdown']))
        self.assertEqual(1000,sum(row['quantity'] for row in result['breakdown']))
        self.delivery['due_at'] = '2026-10-27T14:00:00+09:00'
        self.expect(self.query(),970,'14:20:00')

    def test_D27_elapsed_expected_arrival_without_actual_confirmation_is_unknown(self):
        self.load(mixed=True)
        shipment=next(n for n in self.nodes if n['type']=='Shipment' and 'arrived_at' not in n)
        shipment['expected_arrival_at']='2026-10-27T09:59:59+09:00'
        self.unknown(self.query())

    def test_D28_planned_departure_is_not_already_in_transit(self):
        self.load(mixed=True)
        shipment=next(n for n in self.nodes if n['type']=='Shipment' and 'arrived_at' not in n)
        shipment['planned_departure_at']=shipment.pop('departed_at')
        shipment['expected_arrival_at']='2026-10-27T16:00:00+09:00'
        # No actual departure: source remains available at as_of and new conditions apply.
        self.expect(self.query(),1000,'14:20:00')

    def test_D29_coverage_missing_is_unknown_but_confirmed_empty_is_zero(self):
        for key in list(self.snapshot['confirmations']):
            value=self.snapshot['confirmations'].pop(key)
            with self.subTest(missing=key): self.unknown(self.query())
            self.snapshot['confirmations'][key]=value
        self.nodes[:]=[n for n in self.nodes if n['type'] not in ('Allocation','ProductionObservation','ProductionWork')]
        self.expect(self.query(),0,None)

    def test_D30_known_unallocated_gap_is_not_unknown_or_automatic_supply(self):
        allocation=next(n for n in self.nodes if n['type']=='Allocation')
        self.nodes[:]=[n for n in self.nodes if n['type'] not in ('ProductionObservation','ProductionWork')]
        stock=dict(id='stock:test:CQ04:900',type='FinishedGoodsStock',origin='synthetic',
            evidence=allocation['evidence'][:],snapshot_id=self.snapshot['id'],company='DEMO-B',
            configuration_id='config:esp32-poe:L:WROOM',observed_at=AS_OF,
            location='test warehouse',quantity=900,unit='piece')
        self.nodes.append(stock)
        allocation.pop('production_work_id');allocation.update(stock_id=stock['id'],quantity=900)
        self.expect(self.query(),900,None)

    def test_D31_ready_and_waiting_do_not_invent_future_speed(self):
        self.nodes[:]=[n for n in self.nodes if n['type']!='ProductionObservation']
        self.work.pop('started_at')
        self.work['completed_quantity']=0
        for state in ('ready','waiting'):
            self.work['production_state']=state
            with self.subTest(state=state): self.unknown(self.query())

    def test_D32_same_work_split_allocations_partition_current_and_future_units_once(self):
        first = next(n for n in self.nodes if n['type'] == 'Allocation')
        first['quantity'] = 500
        second = dict(first, id=first['id'] + ':second')
        self.nodes.append(second)
        before = copy.deepcopy(self.nodes)

        result = self.query()

        self.expect(result, 960, '15:26:40')
        rows = {row['allocation_id']: row for row in result['breakdown']}
        self.assertEqual({first['id'], second['id']}, set(rows))
        self.assertEqual(500, rows[first['id']]['on_time_quantity'])
        self.assertEqual(460, rows[second['id']]['on_time_quantity'])
        self.assertEqual(600, sum(row['known_available_quantity'] for row in rows.values()))
        self.assertEqual(1000, sum(row['quantity'] for row in rows.values()))
        self.assertEqual(before, self.nodes)

    def test_D33_global_all_at_once_with_already_shipped_mixed_is_unsupported(self):
        self.load(mixed=True)
        self.condition['dispatch_mode']='all_at_once'
        self.unknown(self.query())

    def test_D34_repeated_read_never_adds_shipments_arrivals_or_versions(self):
        self.load(mixed=True)
        before=copy.deepcopy(self.nodes)
        first=self.query();second=self.query()
        self.assertEqual(first,second)
        self.assertEqual(before,self.nodes)

    def test_D35_used_provenance_resolves_and_assumptions_are_visible(self):
        result=self.query()
        required=set()
        for node in (self.snapshot,self.condition,self.work,self.previous,self.latest):
            required.update(node['evidence'])
        self.assertTrue(required <= set(result['evidence']))
        self.assertTrue(result['assumptions'])
        for eid in result['evidence']:
            evidence=self.index[eid]
            self.assertEqual('Evidence',evidence['type'])
            self.assertEqual('SourceDocument',self.index[evidence['source_id']]['type'])

    def test_D36_new_eleven_observation_changes_recent_interval_only(self):
        for good, expected, full in [(690,960,'15:26:40'),(680,920,'16:00:00')]:
            with self.subTest(good=good):
                self.load()
                stamp='2026-10-27T11:00:00+09:00'
                new=dict(self.latest,id='observation:test:CQ04:11',observed_at=stamp,
                         produced_quantity=750,good_quantity=good,defective_quantity=750-good)
                self.nodes.append(new)
                self.snapshot['as_of']=stamp
                self.work.update(observed_at=stamp,completed_quantity=good)
                self.expect(self.query(as_of=stamp),expected,full)

    def test_partial_work_target_and_multiple_works_remain_explicit_unsupported_scope(self):
        allocation=next(n for n in self.nodes if n['type']=='Allocation')
        allocation['quantity']=900
        self.assertTrue(validate_input_graph(self.nodes)['valid'])
        self.unknown(self.query())
        allocation['quantity']=500
        other=dict(self.work,id='work:test:CQ04:other',target_quantity=500,completed_quantity=0,
                   production_state='ready')
        other.pop('started_at')
        other_alloc=dict(allocation,id='allocation:test:CQ04:other',production_work_id=other['id'])
        self.nodes.extend([other,other_alloc])
        self.unknown(self.query())


if __name__=='__main__':
    unittest.main()
