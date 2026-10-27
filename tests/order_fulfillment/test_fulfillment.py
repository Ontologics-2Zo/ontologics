"""B-CQ02 independent status answers and graph boundaries; no model or DB calls."""
from __future__ import annotations

import copy
import json
import unittest

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment, fulfillment_status
from ontologics.model import ROOT, graph_jsonld, instant, tbox_turtle, validate_graph


ROUND = 'delivery-round:demo:B-01:1'
OTHER_ROUND = 'delivery-round:demo:B-CQ02-other:1'
SNAPSHOT = 'snapshot:demo:CQ02:10'
AS_OF = '2026-10-27T10:00:00+09:00'
QUERIED_AT = '2026-10-27T10:10:00+09:00'
CONFIGURATION = 'config:esp32-poe:L:WROOM'
OPERATIONS = {'FulfillmentSnapshot', 'FinishedGoodsStock', 'ProductionWork', 'Allocation', 'Shipment'}
EXPECTED_STATES = {'delivered': 100, 'in_transit': 150, 'warehouse': 250,
                   'in_progress': 300, 'ready': 100, 'waiting': 100}
CONFIRMATIONS = ('allocations_complete', 'source_allocations_complete', 'allocations_disjoint',
                 'stock_events_complete', 'stock_observations_before_departures',
                 'shipment_events_complete', 'production_state_complete', 'material_disjoint',
                 'no_reallocation')


def stock(label):
    return 'stock:demo:CQ02:' + label


def work(label):
    return 'work:demo:CQ02:' + label


def allocation(label):
    return 'allocation:demo:CQ02:' + label


def shipment(label):
    return 'shipment:demo:CQ02:' + label


class FulfillmentTests(unittest.TestCase):
    def setUp(self):
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        self.nodes = catalog + load_demo_contract() + load_demo_fulfillment()
        self.index = {node['id']: node for node in self.nodes}

    def result(self, *, round_id=ROUND, as_of=AS_OF, queried_at=QUERIED_AT):
        return fulfillment_status(self.nodes, round_id, as_of, queried_at=queried_at)

    def valid(self):
        report = validate_graph(self.nodes)
        self.assertTrue(report['valid'], report)

    def invalid(self):
        report = validate_graph(self.nodes)
        self.assertFalse(report['valid'], report)
        self.assertTrue(report['errors'])
        return report

    def incomplete(self, result):
        self.assertEqual('needs_confirmation', result['status'])
        self.assertIsNone(result['states'])
        for key in ('allocated_quantity', 'remaining_quantity', 'unallocated_quantity'):
            self.assertIsNone(result[key], (key, result))
        self.assertTrue(result['unknowns'])
        self.assertIs(result['fulfillment_assessed'], False)

    def test_source_and_all_synthetic_facts_form_a_valid_graph(self):
        self.valid()
        for node in self.nodes:
            if node['type'] in OPERATIONS:
                self.assertEqual('synthetic', node['origin'])
                self.assertTrue(node['evidence'])
        sources = {n['id']: n for n in self.nodes if n['type'] == 'SourceDocument'}
        for eid in self.index[SNAPSHOT]['evidence']:
            evidence = self.index[eid]
            self.assertEqual('synthetic', sources[evidence['source_id']]['origin'])
            self.assertTrue(evidence['locator'])

    def test_basic_answer_matches_independent_six_state_counts(self):
        self.valid()
        before = copy.deepcopy(self.nodes)
        result = self.result()
        self.assertEqual('complete', result['status'])
        self.assertEqual(EXPECTED_STATES, result['states'])
        self.assertEqual(1000, result['allocated_quantity'])
        self.assertEqual(900, result['remaining_quantity'])
        self.assertEqual(0, result['unallocated_quantity'])
        self.assertEqual(1000, sum(result['states'].values()))
        self.assertIs(result['fulfillment_assessed'], False)
        self.assertEqual([], result['unknowns'])
        self.assertEqual(before, self.nodes, 'Reading must not rewrite observed stock or facts')

    def test_other_round_has_its_own_stock_and_ready_allocations_only(self):
        result = self.result(round_id=OTHER_ROUND)
        self.assertEqual('complete', result['status'])
        self.assertEqual({'delivered': 0, 'in_transit': 0, 'warehouse': 100,
                          'in_progress': 0, 'ready': 200, 'waiting': 0}, result['states'])
        self.assertEqual(300, result['allocated_quantity'])
        self.assertEqual(300, result['remaining_quantity'])
        self.assertEqual(0, result['unallocated_quantity'])

    def test_other_round_overallocates_shared_stock_even_when_target_round_is_unchanged(self):
        self.index[OTHER_ROUND]['quantity'] = 301
        self.index['contract-item:demo:B-CQ02-other:1']['quantity'] = 301
        self.index[allocation('OTHER-W')]['quantity'] = 101
        report = self.invalid()
        self.assertTrue(any('351' in str(e) or 'ALLOCATION' in e['code'] or 'EXCEEDED' in e['code']
                            for e in report['errors']), report)
        with self.assertRaises(ValueError):
            self.result()

    def test_old_stock_and_departed_shipments_are_not_counted_twice(self):
        result = self.result()
        # D and T were 250 before departure; only W contributes to warehouse now.
        self.assertEqual(250, result['states']['warehouse'])
        self.assertEqual(100, result['states']['delivered'])
        self.assertEqual(150, result['states']['in_transit'])
        self.assertEqual(1000, sum(result['states'].values()))
        self.assertEqual(100, self.index[stock('D')]['quantity'])
        self.assertEqual(150, self.index[stock('T')]['quantity'])

    def test_duplicate_node_or_second_shipment_for_one_allocation_is_rejected(self):
        self.nodes.append(copy.deepcopy(self.index[allocation('W')]))
        self.invalid()
        self.nodes.pop()
        self.nodes.append(dict(self.index[shipment('T')], id=shipment('T-duplicate')))
        self.invalid()

    def test_allocation_requires_exactly_one_source_reference(self):
        node = self.index[allocation('W')]
        node['production_work_id'] = work('R')
        self.invalid()
        del node['production_work_id']
        del node['stock_id']
        self.invalid()

    def test_optional_source_refs_and_list_refs_export_as_entity_uris(self):
        self.valid()
        exported = graph_jsonld(self.nodes)
        urn = 'urn:ontologics:entity:'
        by_id = {n['id']: n for n in exported['@graph']}
        self.assertEqual(urn + stock('W'), by_id[urn + allocation('W')]['stock_id'])
        self.assertNotIn('production_work_id', by_id[urn + allocation('W')])
        self.assertEqual(urn + work('R'), by_id[urn + allocation('R')]['production_work_id'])
        self.assertNotIn('stock_id', by_id[urn + allocation('R')])
        self.assertEqual({urn + ROUND, urn + OTHER_ROUND}, set(by_id[urn + SNAPSHOT]['round_ids']))
        for key in ('snapshot_id', 'stock_id', 'production_work_id', 'allocation_id', 'round_ids'):
            self.assertEqual('@id', exported['@context'][key]['@type'])
        turtle = tbox_turtle()
        for kind in OPERATIONS:
            self.assertIn('o:' + kind + ' a owl:Class', turtle)
        self.assertIn('o:stock_id a owl:ObjectProperty', turtle)
        self.assertIn('o:round_ids a owl:ObjectProperty', turtle)

    def test_bad_refs_scope_company_configuration_and_units_are_rejected(self):
        mutations = ((allocation('W'), 'stock_id', 'stock:missing'),
                     (allocation('W'), 'stock_id', work('R')),
                     (allocation('W'), 'delivery_round_id', 'delivery-round:missing'),
                     (allocation('W'), 'delivery_round_id', stock('W')),
                     (stock('W'), 'snapshot_id', 'snapshot:missing'),
                     (stock('W'), 'company', 'OTHER-COMPANY'),
                     (stock('W'), 'configuration_id', 'config:esp32-poe:L1:WROOM'),
                     (allocation('W'), 'unit', 'box'), (stock('W'), 'unit', 'box'),
                     (work('R'), 'unit', 'box'), (shipment('T'), 'unit', 'box'))
        for nid, field, value in mutations:
            with self.subTest(node=nid, field=field, value=value):
                node = self.index[nid]
                old = node[field]
                node[field] = value
                self.invalid()
                node[field] = old
        self.valid()

    def test_operational_quantities_reject_bool_float_text_and_nonpositive_values(self):
        for nid, field in ((allocation('W'), 'quantity'), (shipment('T'), 'quantity'),
                           (work('R'), 'target_quantity')):
            node = self.index[nid]
            old = node[field]
            for value in (0, -1, True, False, 1.5, 100.0, '100', None, [], {}, float('nan'), float('inf')):
                with self.subTest(node=nid, field=field, quantity=value):
                    node[field] = value
                    self.invalid()
            node[field] = old
        self.valid()

    def test_zero_stock_is_valid_but_positive_allocation_from_it_is_not(self):
        node = self.index[stock('W')]
        node['quantity'] = 0
        self.invalid()
        self.nodes[:] = [n for n in self.nodes if n['id'] not in {allocation('W'), allocation('OTHER-W')}]
        self.valid()
        result = self.result()
        self.assertEqual(0, result['states']['warehouse'])
        self.assertEqual(750, result['allocated_quantity'])
        self.assertEqual(250, result['unallocated_quantity'])
        for value in (-1, True, False, 0.0, '0', None, [], {}):
            with self.subTest(quantity=value):
                node['quantity'] = value
                self.invalid()

    def test_round_and_production_source_caps_are_checked(self):
        self.index[work('Q')]['target_quantity'] = 101
        self.index[allocation('Q')]['quantity'] = 101
        self.invalid()  # 1001 exceeds the target round's 1000, without source overflow.
        self.index[allocation('Q')]['quantity'] = 100
        self.index[work('Q')]['target_quantity'] = 99
        self.invalid()  # The round is within 1000 but its work source is overallocated.

    def test_nine_hundred_allocated_is_valid_and_leaves_one_hundred_unallocated(self):
        self.nodes[:] = [n for n in self.nodes if n['id'] != allocation('Q')]
        self.valid()
        result = self.result()
        self.assertEqual(0, result['states']['waiting'])
        self.assertEqual(900, result['allocated_quantity'])
        self.assertEqual(100, result['unallocated_quantity'])
        self.assertEqual(900, result['remaining_quantity'])

    def test_explicitly_confirmed_empty_allocations_differ_from_missing_snapshot(self):
        self.nodes[:] = [n for n in self.nodes if n['type'] not in {'Allocation', 'Shipment'}]
        self.valid()
        result = self.result()
        self.assertEqual('complete', result['status'])
        self.assertEqual({key: 0 for key in EXPECTED_STATES}, result['states'])
        self.assertEqual(0, result['allocated_quantity'])
        self.assertEqual(1000, result['remaining_quantity'])
        self.assertEqual(1000, result['unallocated_quantity'])
        self.nodes[:] = [n for n in self.nodes if n['type'] not in OPERATIONS]
        self.valid()
        self.incomplete(self.result())

    def test_missing_or_false_coverage_never_turns_unknown_counts_into_zero(self):
        confirmations = self.index[SNAPSHOT]['confirmations']
        for key in CONFIRMATIONS:
            self.assertIs(confirmations[key], True)
            with self.subTest(confirmation=key, value='missing'):
                del confirmations[key]
                self.valid()
                self.incomplete(self.result())
            with self.subTest(confirmation=key, value=False):
                confirmations[key] = False
                self.valid()
                self.incomplete(self.result())
            confirmations[key] = True

    def test_overdue_expected_arrival_is_still_in_transit_without_actual_arrival(self):
        self.index[shipment('T')]['expected_arrival_at'] = '2026-10-27T09:50:00+09:00'
        self.valid()
        result = self.result()
        self.assertEqual(EXPECTED_STATES, result['states'])
        self.assertTrue(result['warnings'])
        self.assertEqual(900, result['remaining_quantity'])

    def test_actual_events_at_as_of_are_included_but_future_actuals_are_rejected(self):
        self.index[shipment('D')]['arrived_at'] = AS_OF
        self.index[shipment('T')]['departed_at'] = AS_OF
        self.valid()
        self.assertEqual(EXPECTED_STATES, self.result()['states'])
        self.index[shipment('D')]['arrived_at'] = '2026-10-27T10:00:01+09:00'
        self.invalid()
        self.index[shipment('D')]['arrived_at'] = AS_OF
        self.index[shipment('T')]['departed_at'] = '2026-10-27T10:00:01+09:00'
        self.invalid()

    def test_actual_arrival_requires_departure_and_nonreversed_event_order(self):
        node = self.index[shipment('D')]
        old = node.pop('departed_at')
        self.invalid()
        node['departed_at'] = old
        node['arrived_at'] = '2026-10-27T09:09:59+09:00'
        self.invalid()
        node['arrived_at'] = '2026-10-27T09:40:00+09:00'
        node['departed_at'] = '2026-10-27T08:59:59+09:00'
        self.invalid()  # Departure cannot precede this first format's pre-shipment stock observation.

    def test_all_operational_times_need_clock_and_timezone(self):
        cases = ((SNAPSHOT, 'as_of'), (stock('D'), 'observed_at'),
                 (work('R'), 'observed_at'), (work('R'), 'started_at'),
                 (work('P'), 'planned_start_at'), (shipment('D'), 'departed_at'),
                 (shipment('D'), 'arrived_at'), (shipment('T'), 'expected_arrival_at'))
        for nid, field in cases:
            node = self.index[nid]
            old = node[field]
            for value in ('2026-10-27', '2026-10-27T10:00:00', 'not-a-time', [], {}):
                with self.subTest(node=nid, field=field, time=value):
                    node[field] = value
                    self.invalid()
            node[field] = old
        self.valid()

    def test_same_utc_moment_matches_and_later_read_preserves_observed_time(self):
        result = self.result(as_of='2026-10-27T01:00:00Z', queried_at='2026-10-27T11:00:00+09:00')
        self.assertEqual('complete', result['status'])
        self.assertEqual(EXPECTED_STATES, result['states'])
        self.assertEqual(instant(AS_OF), instant(result['as_of']))
        self.assertEqual(instant('2026-10-27T11:00:00+09:00'), instant(result['queried_at']))
        self.assertEqual('2026-10-27T09:00:00+09:00', self.index[stock('D')]['observed_at'])
        self.incomplete(self.result(as_of='2026-10-27T11:00:00+09:00', queried_at='2026-10-27T11:10:00+09:00'))

    def test_partial_completion_requires_supplementation_not_counting_target_as_finished(self):
        self.index[work('R')]['completed_quantity'] = 1
        self.valid()
        self.incomplete(self.result())
        self.index[work('R')]['completed_quantity'] = 301
        self.invalid()

    def test_work_states_actual_start_and_count_shapes_are_consistent(self):
        node = self.index[work('P')]
        node['started_at'] = '2026-10-27T09:00:00+09:00'
        self.invalid()
        del node['started_at']
        node['production_state'] = 'finished'
        self.invalid()
        node['production_state'] = 'ready'
        for value in (-1, True, 0.0, '0', [], {}):
            with self.subTest(completed_quantity=value):
                node['completed_quantity'] = value
                self.invalid()
        node['completed_quantity'] = 0
        self.index[work('R')].pop('started_at')
        self.invalid()

    def test_partial_or_production_direct_shipments_are_rejected(self):
        node = self.index[shipment('T')]
        node['quantity'] = 149
        self.invalid()
        node['quantity'] = 300
        node['allocation_id'] = allocation('R')
        self.invalid()

    def test_destination_and_operational_provenance_cannot_be_substituted(self):
        node = self.index[shipment('D')]
        old = node['destination']
        node['destination'] = 'Different customer warehouse'
        self.invalid()
        node['destination'] = old
        old_evidence = node['evidence']
        node['evidence'] = self.index[CONFIGURATION]['evidence'][:]
        self.invalid()
        node['evidence'] = []
        self.invalid()
        node['evidence'] = old_evidence
        node['origin'] = 'real'
        self.invalid()

    def test_snapshot_scope_and_confirmation_shapes_are_explicit(self):
        snapshot = self.index[SNAPSHOT]
        original = snapshot['round_ids']
        for invalid in ([], [ROUND, ROUND], ['missing:round'], [stock('W')], {}, True, [None]):
            with self.subTest(round_ids=invalid):
                snapshot['round_ids'] = invalid
                self.invalid()
        snapshot['round_ids'] = original
        snapshot['confirmations']['invented_confirmation'] = True
        self.invalid()
        del snapshot['confirmations']['invented_confirmation']
        for invalid in ('yes', 1, None, [], {}):
            with self.subTest(confirmation=invalid):
                snapshot['confirmations']['allocations_complete'] = invalid
                self.invalid()
        snapshot['confirmations']['allocations_complete'] = True
        self.valid()
        snapshot['round_ids'] = [OTHER_ROUND]
        self.invalid()  # Current round allocations cannot hide outside declared coverage.

    def test_snapshot_identity_uses_instants_and_cross_snapshot_source_is_invalid(self):
        duplicate = copy.deepcopy(self.index[SNAPSHOT])
        duplicate.update(id=SNAPSHOT + ':duplicate', as_of='2026-10-27T01:00:00Z')
        self.nodes.append(duplicate)
        self.invalid()
        duplicate['as_of'] = '2026-10-27T11:00:00+09:00'
        self.valid()
        self.index[allocation('W')]['snapshot_id'] = duplicate['id']
        self.invalid()

    def test_malformed_new_node_shapes_return_reports_instead_of_raising(self):
        cases = ((SNAPSHOT, 'company'), (SNAPSHOT, 'round_ids'), (SNAPSHOT, 'confirmations'),
                 (stock('W'), 'snapshot_id'), (stock('W'), 'configuration_id'),
                 (work('R'), 'production_state'), (allocation('W'), 'stock_id'),
                 (allocation('W'), 'delivery_round_id'), (shipment('D'), 'allocation_id'),
                 (shipment('D'), 'destination'))
        for nid, field in cases:
            node = self.index[nid]
            old = node[field]
            for value in (None, [], [[]], {'nested': []}):
                with self.subTest(node=nid, field=field, value=value):
                    node[field] = value
                    report = validate_graph(self.nodes)
                    self.assertIsInstance(report['valid'], bool)
                    self.assertFalse(report['valid'], report)
            node[field] = old
        self.valid()


if __name__ == '__main__':
    unittest.main()
