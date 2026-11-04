"""4-1 serial -> board links and 4-2 board -> production line traces."""
import json
import unittest

from ontologics.claim_response.fixtures import claim_response_nodes, load_operations
from ontologics.claim_response.intake import convert_claim
from ontologics.claim_response.trace import (
    AMBIGUOUS, C_OPERATIONS_BASIS, CONFLICT, MATCHED, NEEDS_HISTORY, NOT_FOUND, TRACED,
    by_type, c_operations_shipments, c_operations_stock, candidate_scope, line_summary, link_serials,
    prepare_quality_case, stored_scope, trace_claim, trace_lines)
from ontologics.demo import fixture_nodes
from ontologics.model import ROOT, validate_graph
from ontologics.service import Service
from tests.db import database_for_test

CONFIG = 'config:esp32-poe:L:WROOM'
LINE_C01 = 'production-line:demo:C-01'
CLAIMED = ['unit:C-D001', 'unit:C-D002', 'unit:C-D003', 'unit:C-D004', 'unit:C-D005']


def existing_nodes():
    catalog = json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']
    fictional, _ = fixture_nodes()
    return catalog + fictional


def claim_nodes(*, confirmed):
    nodes = convert_claim(ROOT / 'data/claim_response/claims/claim_table.csv',
                          collected_at='2026-10-26T10:30:00+09:00')['nodes']
    if confirmed:
        nodes = [dict(n, intake_state='confirmed') if n['type'] == 'ClaimIntake' else n for n in nodes]
    return nodes


class LinkSerialTests(unittest.TestCase):
    def setUp(self):
        self.operations = by_type(claim_response_nodes())
        self.units = self.operations['ProductUnit']
        self.intake = by_type(claim_nodes(confirmed=False))['ClaimIntake'][0]

    def test_reported_serials_match_one_board_each(self):
        links = link_serials(self.intake, self.units, configuration_id=CONFIG)
        self.assertEqual([MATCHED] * 5, [link['match_state'] for link in links])
        self.assertEqual(CLAIMED, [link['unit_id'] for link in links])
        self.assertTrue(all(link['warnings'] == [] for link in links))

    def test_unknown_duplicate_and_mismatched_boards_stay_open(self):
        intake = dict(self.intake, reported_serials=['C-D001', 'C-X999', 'C-D002'])
        twin = dict(self.units[1], id='unit:other:C-D002', company='Other Company')
        other_config = dict(self.units[0], configuration_id='config:esp32-poe:L1:WROOM')
        links = link_serials(intake, [other_config, twin, *self.units[1:]], configuration_id=CONFIG)
        self.assertEqual([MATCHED, NOT_FOUND, AMBIGUOUS], [link['match_state'] for link in links])
        self.assertEqual(['CONFIGURATION_MISMATCH'], [w['code'] for w in links[0]['warnings']])
        self.assertIsNone(links[1]['unit_id'])
        self.assertIsNone(links[2]['unit_id'])
        self.assertEqual(['unit:C-D002', 'unit:other:C-D002'], links[2]['candidates'])

    def test_quality_case_needs_a_person_and_resolved_serials(self):
        links = link_serials(self.intake, self.units)
        waiting = prepare_quality_case(self.intake, links)
        self.assertEqual('waiting_for_confirmation', waiting['state'])
        self.assertIsNone(waiting['node'])
        confirmed = dict(self.intake, intake_state='confirmed')
        blocked = prepare_quality_case(confirmed, link_serials(dict(confirmed, reported_serials=['C-D001', 'C-X999']),
                                                               self.units))
        self.assertEqual('blocked', blocked['state'])
        self.assertIn('C-X999', blocked['reason'])
        ready = prepare_quality_case(confirmed, links)
        self.assertEqual('ready', ready['state'])
        self.assertEqual(CLAIMED, ready['node']['unit_ids'])
        self.assertEqual(confirmed['id'], ready['node']['intake_id'])

    def test_quality_case_candidate_is_a_valid_graph_addition(self):
        claim = claim_nodes(confirmed=True)
        intake = by_type(claim)['ClaimIntake'][0]
        case = prepare_quality_case(intake, link_serials(intake, self.units))['node']
        outcome = validate_graph(existing_nodes() + claim_response_nodes() + claim + [case])
        self.assertEqual([], outcome['errors'])
        unconfirmed = validate_graph(existing_nodes() + claim_response_nodes() + claim_nodes(confirmed=False) + [case])
        self.assertIn('INTAKE_NOT_CONFIRMED', {e['code'] for e in unconfirmed['errors']})


class TraceLineTests(unittest.TestCase):
    def setUp(self):
        self.operations = by_type(claim_response_nodes())
        self.records = self.operations['UnitProductionRecord']
        self.lines = self.operations['ProductionLine']

    def test_claimed_boards_trace_to_line_c01_with_evidence(self):
        traces = trace_lines(CLAIMED, self.records, self.lines)
        self.assertEqual([TRACED] * 5, [t['state'] for t in traces])
        self.assertEqual({LINE_C01}, {t['production_line_id'] for t in traces})
        self.assertEqual('C-01', traces[0]['line_code'])
        self.assertEqual('production:C-D001', traces[0]['record_id'])
        self.assertEqual(['evidence:claim_response:batch:delivered'], traces[0]['evidence'])
        summary = line_summary(traces)
        self.assertEqual(LINE_C01, summary['single_line'])
        self.assertEqual([], summary['unresolved_unit_ids'])
        replacement = trace_lines(['unit:C-R001'], self.records, self.lines)[0]
        self.assertEqual('production-line:demo:C-02', replacement['production_line_id'])

    def test_missing_or_double_history_is_never_guessed(self):
        records = [r for r in self.records if r['unit_id'] != 'unit:C-D003']
        records.append(dict(self.records[0], id='production:C-D001:again'))
        traces = trace_lines(CLAIMED, records, self.lines)
        states = {t['unit_id']: t['state'] for t in traces}
        self.assertEqual(NEEDS_HISTORY, states['unit:C-D003'])
        self.assertEqual(CONFLICT, states['unit:C-D001'])
        missing = next(t for t in traces if t['unit_id'] == 'unit:C-D003')
        self.assertIsNone(missing['production_line_id'])
        summary = line_summary(traces)
        self.assertIsNone(summary['single_line'])
        self.assertEqual(['unit:C-D001', 'unit:C-D003'], summary['unresolved_unit_ids'])


def confirmed_case():
    claim = claim_nodes(confirmed=True)
    intake = by_type(claim)['ClaimIntake'][0]
    units = by_type(claim_response_nodes())['ProductUnit']
    return claim, prepare_quality_case(intake, link_serials(intake, units))['node']


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.operations = load_operations()
        self.records = by_type(claim_response_nodes())['UnitProductionRecord']
        self.stock = c_operations_stock(self.operations)
        self.shipments = c_operations_shipments(self.operations)
        self.claim, self.case = confirmed_case()

    def test_operations_values_are_read_as_temporary_c_basis(self):
        self.assertEqual(1, len(self.stock))
        self.assertEqual(120, len(self.stock[0]['unit_ids']))
        self.assertTrue(self.stock[0]['unit_ids_complete'])
        self.assertEqual('2026-10-26T10:00:00+09:00', self.stock[0]['events_complete_until'])
        self.assertEqual('2026-10-26T10:00:00+09:00', self.stock[0]['observed_at'])
        self.assertEqual(1, len(self.shipments))
        self.assertEqual('DO-DEMO-C-001', self.shipments[0]['delivery_reference'])
        self.assertEqual(80, len(self.shipments[0]['unit_ids']))
        self.assertEqual({C_OPERATIONS_BASIS}, {o['basis'] for o in self.stock + self.shipments})

    def test_hold_scope_is_the_120_stored_boards_from_line_c01(self):
        result = stored_scope(self.case, LINE_C01, self.records, self.stock, self.shipments)
        self.assertEqual('ready', result['state'])
        self.assertEqual([C_OPERATIONS_BASIS], result['basis'])
        node = result['node']
        self.assertEqual(120, len(node['unit_ids']))
        self.assertTrue(all(u.startswith('unit:C-S') for u in node['unit_ids']))
        self.assertEqual(('hold', '2026-10-26T10:00:00+09:00', LINE_C01),
                         (node['scope_state'], node['as_of'], node['production_line_id']))
        self.assertEqual(['evidence:claim_response:batch:stored'], node['evidence'])

    def test_hold_scope_keeps_other_lines_and_unknown_lists_out(self):
        mixed = [dict(self.stock[0], unit_ids=self.stock[0]['unit_ids'] + ['unit:C-R001'])]
        units = stored_scope(self.case, LINE_C01, self.records, mixed)['node']['unit_ids']
        self.assertNotIn('unit:C-R001', units)
        partial = [dict(self.stock[0], unit_ids_complete=False)]
        self.assertEqual('stock_list_incomplete', stored_scope(self.case, LINE_C01, self.records, partial)['state'])
        early = stored_scope(self.case, LINE_C01, self.records, self.stock, as_of='2026-10-26T09:00:00+09:00')
        self.assertEqual('no_stock_observation', early['state'])
        self.assertIsNone(early['node'])

    def test_boards_that_left_after_the_observation_are_not_held(self):
        later = [{'unit_ids': ['unit:C-S001', 'unit:C-S002'], 'departed_at': '2026-10-26T11:00:00+09:00',
                  'unit_ids_complete': True, 'evidence': [], 'basis': C_OPERATIONS_BASIS}]
        checked = [dict(self.stock[0], events_complete_until='2026-10-26T12:00:00+09:00')]
        result = stored_scope(self.case, LINE_C01, self.records, checked, self.shipments + later,
                              as_of='2026-10-26T12:00:00+09:00')
        self.assertEqual(118, len(result['node']['unit_ids']))
        self.assertEqual(['unit:C-S001', 'unit:C-S002'], result['departed_after_observation'])

    def test_events_after_the_observation_must_be_confirmed_up_to_as_of(self):
        unconfirmed = stored_scope(self.case, LINE_C01, self.records, self.stock, self.shipments,
                                   as_of='2026-10-26T12:00:00+09:00')
        self.assertEqual('events_unconfirmed', unconfirmed['state'])
        self.assertIsNone(unconfirmed['node'])
        unknown = stored_scope(self.case, LINE_C01, self.records,
                               [dict(self.stock[0], events_complete_until=None)], self.shipments)
        self.assertEqual('events_unconfirmed', unknown['state'])

    def test_departure_at_the_observation_time_is_not_ordered(self):
        same = [{'unit_ids': ['unit:C-S007'], 'departed_at': '2026-10-26T10:00:00+09:00',
                 'unit_ids_complete': True, 'evidence': [], 'basis': C_OPERATIONS_BASIS}]
        result = stored_scope(self.case, LINE_C01, self.records, self.stock, self.shipments + same)
        self.assertEqual('same_time_departure', result['state'])
        self.assertEqual(['unit:C-S007'], result['unit_ids'])
        self.assertIsNone(result['node'])

    def test_candidate_scope_is_the_75_other_delivered_boards(self):
        result = candidate_scope(self.case, LINE_C01, self.records, self.shipments)
        node = result['node']
        self.assertEqual('candidate', node['scope_state'])
        self.assertEqual(['unit:C-D%03d' % n for n in range(6, 81)], node['unit_ids'])
        self.assertFalse(set(CLAIMED) & set(node['unit_ids']))
        self.assertEqual([C_OPERATIONS_BASIS], result['basis'])
        before = candidate_scope(self.case, LINE_C01, self.records, self.shipments, as_of='2026-09-25T13:00:00+09:00')
        self.assertEqual('no_shipment', before['state'])
        partial = candidate_scope(self.case, LINE_C01, self.records, [dict(self.shipments[0], unit_ids_complete=False)])
        self.assertEqual('shipment_list_incomplete', partial['state'])

    def test_case_and_scopes_are_valid_graph_additions(self):
        hold = stored_scope(self.case, LINE_C01, self.records, self.stock, self.shipments)['node']
        candidate = candidate_scope(self.case, LINE_C01, self.records, self.shipments)['node']
        self.assertNotEqual(hold['id'], candidate['id'])
        outcome = validate_graph(existing_nodes() + claim_response_nodes() + self.claim + [self.case, hold, candidate])
        self.assertEqual([], outcome['errors'])


class TraceClaimDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals={
            'fixture': {'kind': 'human', 'roles': ['seed', 'read', 'audit']},
            'claim-agent': {'kind': 'agent', 'roles': ['read', 'draft']},
        })
        self.addCleanup(self.service.close)
        self.claim = claim_nodes(confirmed=True)
        seeded = self.service.bootstrap('fixture', existing_nodes() + claim_response_nodes() + self.claim,
                                        request_key='trace-seed')
        self.assertTrue(seeded['ok'], seeded)

    def test_trace_reads_one_version_and_prepares_a_reviewable_case(self):
        intake_id = by_type(self.claim)['ClaimIntake'][0]['id']
        result = trace_claim(self.service, 'claim-agent', intake_id, version=1, configuration_id=CONFIG)
        self.assertEqual(CLAIMED, [link['unit_id'] for link in result['links']])
        self.assertEqual(LINE_C01, result['lines']['single_line'])
        case = result['quality_case']['node']
        draft = self.service.prepare_draft('claim-agent', [{'op': 'put', 'node': case}], request_key='case-draft')
        self.assertTrue(draft['ok'], draft)
        saved = self.service.get_draft('claim-agent', draft['data']['draft_id'], draft['data']['draft_version'])['data']
        self.assertEqual('awaiting_review', saved['status'])
        self.assertEqual(1, self.service._version())
        with self.assertRaisesRegex(ValueError, 'NOT_A_CLAIM_INTAKE'):
            trace_claim(self.service, 'claim-agent', 'unit:C-D001', version=1)

    def test_scopes_from_one_version_become_reviewable_drafts(self):
        _, case = confirmed_case()
        records = self.service.lookup('claim-agent', kind='UnitProductionRecord', version=1)['data']
        operations = load_operations()
        hold = stored_scope(case, LINE_C01, records, c_operations_stock(operations),
                            c_operations_shipments(operations))['node']
        candidate = candidate_scope(case, LINE_C01, records, c_operations_shipments(operations))['node']
        draft = self.service.prepare_draft('claim-agent', [{'op': 'put', 'node': n} for n in (case, hold, candidate)],
                                           request_key='scope-draft')
        self.assertTrue(draft['ok'], draft)
        saved = self.service.get_draft('claim-agent', draft['data']['draft_id'], draft['data']['draft_version'])['data']
        self.assertEqual('awaiting_review', saved['status'])
        self.assertEqual(1, self.service._version())


if __name__ == '__main__':
    unittest.main()
