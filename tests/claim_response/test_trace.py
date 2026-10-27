"""4-1 serial -> board links and 4-2 board -> production line traces."""
import json
import unittest

from ontologics.claim_response.fixtures import claim_response_nodes
from ontologics.claim_response.intake import convert_claim
from ontologics.claim_response.trace import (
    AMBIGUOUS, CONFLICT, MATCHED, NEEDS_HISTORY, NOT_FOUND, TRACED,
    by_type, line_summary, link_serials, prepare_quality_case, trace_claim, trace_lines)
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


if __name__ == '__main__':
    unittest.main()
