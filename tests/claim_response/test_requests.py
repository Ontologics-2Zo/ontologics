"""5-1 production stop, 5-2 shipment hold and 5-3 review state of containment requests."""
import json
from pathlib import Path
import tempfile
import unittest

from ontologics.claim_response.fixtures import claim_response_nodes, load_operations
from ontologics.claim_response.intake import convert_claim
from ontologics.claim_response.requests import (
    PRODUCTION_STOP, REVIEW_LABELS, SHIPMENT_HOLD, line_scope, prepare_containment, prepare_request,
    render_request, serial_ranges, write_request)
from ontologics.claim_response.requests import prepare_test_request, test_request_filename
from ontologics.claim_response.trace import (
    by_type, c_operations_shipments, c_operations_stock, candidate_scope, link_serials, prepare_quality_case,
    stored_scope)
from ontologics.demo import fixture_nodes
from ontologics.model import ROOT, validate_graph
from ontologics.service import Service
from tests.db import database_for_test

LINE_C01 = 'production-line:demo:C-01'
CONFIG = 'config:esp32-poe:L:WROOM'
PREPARED = '2026-10-26T10:15:00+09:00'
CLAIM_ID = 'CLM-DEMO-C-20261026-001'


def existing_nodes():
    catalog = json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']
    fictional, _ = fixture_nodes()
    return catalog + fictional


def scenario():
    claim = convert_claim(ROOT / 'data/claim_response/claims/claim_table.csv',
                          collected_at='2026-10-26T10:30:00+09:00')['nodes']
    claim = [dict(n, intake_state='confirmed') if n['type'] == 'ClaimIntake' else n for n in claim]
    intake = by_type(claim)['ClaimIntake'][0]
    operations = by_type(claim_response_nodes())
    case = prepare_quality_case(intake, link_serials(intake, operations['ProductUnit']))['node']
    raw = load_operations()
    records = operations['UnitProductionRecord']
    hold = stored_scope(case, LINE_C01, records, c_operations_stock(raw), c_operations_shipments(raw))
    return claim, case, records, hold


def build(hold=None, **kwargs):
    claim, case, records, ready_hold = scenario()
    result = prepare_containment(case, LINE_C01, records, ready_hold if hold is None else hold,
                                 prepared_at=PREPARED, external_claim_id=CLAIM_ID, configuration_id=CONFIG, **kwargs)
    return claim, case, records, result


def of_kind(nodes, kind):
    return next(n for n in nodes if n['type'] == 'ContainmentRequest' and n['request_kind'] == kind)


class ProductionStopTests(unittest.TestCase):
    def test_line_scope_is_the_claimed_boards_on_line_c01(self):
        _, case, records, _ = scenario()
        result = line_scope(case, LINE_C01, records)
        self.assertEqual('ready', result['state'])
        scope = result['node']
        self.assertEqual(['unit:C-D00%d' % n for n in range(1, 6)], scope['unit_ids'])
        self.assertEqual(('candidate', LINE_C01), (scope['scope_state'], scope['production_line_id']))
        self.assertTrue(scope['id'].endswith(':line'))
        self.assertEqual(['evidence:claim_response:batch:delivered'], scope['evidence'])

    def test_line_scope_needs_every_claimed_board_on_that_line(self):
        _, case, records, _ = scenario()
        moved = [dict(r, production_line_id='production-line:demo:C-02') if r['unit_id'] == 'unit:C-D003' else r
                 for r in records]
        result = line_scope(case, LINE_C01, moved)
        self.assertEqual('line_not_confirmed', result['state'])
        self.assertEqual(['unit:C-D003'], result['unit_ids'])
        missing = line_scope(case, LINE_C01, [r for r in records if r['unit_id'] != 'unit:C-D001'])
        self.assertEqual('line_not_confirmed', missing['state'])

    def test_production_stop_request_names_its_line(self):
        _, case, _, result = build()
        stop = of_kind(result['nodes'], PRODUCTION_STOP)
        self.assertEqual(LINE_C01, stop['production_line_id'])
        self.assertTrue(stop['scope_id'].endswith(':line'))
        self.assertEqual(PREPARED, stop['prepared_at'])
        self.assertTrue(stop['document_ref'].startswith('artifacts/local/claim_response/requests/'))
        line = line_scope(case, LINE_C01, scenario()[2])['node']
        with self.assertRaisesRegex(ValueError, 'PRODUCTION_LINE_REQUIRED'):
            prepare_request(case, line, PRODUCTION_STOP, prepared_at=PREPARED, document_ref='x.md')
        with self.assertRaisesRegex(ValueError, 'CASE_MISMATCH'):
            prepare_request(case, dict(line, case_id='quality-case:other'), PRODUCTION_STOP,
                            prepared_at=PREPARED, document_ref='x.md', production_line_id=LINE_C01)
        with self.assertRaisesRegex(ValueError, 'INVALID_REQUEST_KIND'):
            prepare_request(case, line, 'recall', prepared_at=PREPARED, document_ref='x.md')

    def test_production_stop_document_keeps_cause_open_and_nothing_sent(self):
        _, _, _, result = build()
        doc = next(text for name, text in result['documents'].items() if name.endswith('production_stop.md'))
        for phrase in ('# 생산 중단 요청서 검토 초안', LINE_C01, 'C-D001 ~ C-D005 5대', CLAIM_ID, CONFIG,
                       '| 검토 상태 | 미검토 |', '| 송부 상태 | 미송부 |', '| 현장 반영 상태 | 미확인 |',
                       '불량 원인은 미확정', '재개를 승인하지 않음', '2026년 10월 26일 10시 15분'):
            self.assertIn(phrase, doc)


class ShipmentHoldTests(unittest.TestCase):
    def test_shipment_hold_targets_the_120_stored_boards(self):
        _, case, _, result = build()
        hold = of_kind(result['nodes'], SHIPMENT_HOLD)
        scope = next(n for n in result['nodes'] if n['id'] == hold['scope_id'])
        self.assertEqual(120, len(scope['unit_ids']))
        self.assertEqual('hold', scope['scope_state'])
        self.assertNotIn('production_line_id', hold)
        self.assertEqual(case['id'], hold['case_id'])

    def test_shipment_hold_document_states_basis_and_limits(self):
        _, _, _, result = build()
        doc = next(text for name, text in result['documents'].items() if name.endswith('shipment_hold.md'))
        for phrase in ('# 출고 배송 중단 요청서 검토 초안', 'C-S001 ~ C-S120 (120대)', '120대는 초기 보류 요청 수량',
                       '불량 확정·폐기·재생산 수량이 아님', 'C 운영 기록 기준', '자동 회수', '| 송부 상태 | 미송부 |',
                       '서로 다른 집합'):
            self.assertIn(phrase, doc)

    def test_no_ready_hold_scope_means_no_shipment_hold_request(self):
        _, _, _, result = build(hold={'state': 'events_unconfirmed', 'node': None,
                                      'reason': 'departures after the observation are not confirmed up to as_of'})
        kinds = [n['request_kind'] for n in result['nodes'] if n['type'] == 'ContainmentRequest']
        self.assertEqual([PRODUCTION_STOP], kinds)
        self.assertEqual([{'request_kind': SHIPMENT_HOLD, 'state': 'events_unconfirmed',
                           'reason': 'departures after the observation are not confirmed up to as_of'}],
                         result['skipped'])
        self.assertEqual(1, len(result['documents']))

    def test_serial_ranges_compress_runs_and_keep_gaps(self):
        self.assertEqual('C-S001 ~ C-S003, C-S005', serial_ranges(['unit:C-S003', 'unit:C-S001', 'unit:C-S002',
                                                                   'unit:C-S005']))
        self.assertEqual('C-D001', serial_ranges(['unit:C-D001']))


class GraphAndFileTests(unittest.TestCase):
    def test_requests_scopes_and_case_are_valid_graph_additions(self):
        claim, case, _, result = build()
        outcome = validate_graph(existing_nodes() + claim_response_nodes() + claim + [case] + result['nodes'])
        self.assertEqual([], outcome['errors'])
        self.assertEqual(2, sum(n['type'] == 'ContainmentRequest' for n in result['nodes']))
        self.assertEqual(len(result['nodes']), len({n['id'] for n in result['nodes']}))

    def test_documents_are_written_only_inside_the_request_folder(self):
        _, _, _, result = build()
        with tempfile.TemporaryDirectory() as folder:
            for name, text in result['documents'].items():
                path = write_request(text, name, base_dir=folder)
                self.assertEqual(Path(folder).resolve(), path.parent)
                self.assertEqual(text, path.read_text(encoding='utf-8'))
            for bad in ('../escape.md', 'nested/x.md', 'note.txt'):
                with self.assertRaisesRegex(ValueError, 'INVALID_DOCUMENT_PATH'):
                    write_request('x', bad, base_dir=folder)


class ReviewStateTests(unittest.TestCase):
    def test_review_labels_never_claim_sending(self):
        self.assertEqual('미검토', REVIEW_LABELS['awaiting_review'])
        self.assertEqual('검토 완료 (그래프 반영 전)', REVIEW_LABELS['approved'])
        _, case, _, result = build()
        stop = of_kind(result['nodes'], PRODUCTION_STOP)
        line = next(n for n in result['nodes'] if n['id'] == stop['scope_id'])
        approved = render_request(stop, case, line, external_claim_id=CLAIM_ID, configuration_id=CONFIG,
                                  review_status='approved', document_version=2)
        self.assertIn('| 검토 상태 | 검토 완료 (그래프 반영 전) |', approved)
        self.assertIn('| 문서 버전 | 2 |', approved)
        self.assertIn('| 송부 상태 | 미송부 |', approved)
        self.assertIn('| 현장 반영 상태 | 미확인 |', approved)


class TestRequestTests(unittest.TestCase):
    """6-1: C asks the test team for per-board results and dispositions; it decides neither."""
    def build(self, hold=None):
        _, case, records, ready_hold = scenario()
        candidate = candidate_scope(case, LINE_C01, records, c_operations_shipments(load_operations()))
        result = prepare_test_request(case, LINE_C01, ready_hold if hold is None else hold, prepared_at=PREPARED,
                                      external_claim_id=CLAIM_ID, configuration_id=CONFIG,
                                      candidate_result=candidate)
        return case, result

    def test_targets_are_the_claimed_and_held_boards_once_each(self):
        case, result = self.build()
        groups = result['targets']['groups']
        self.assertEqual([5, 120], [len(g['unit_ids']) for g in groups])
        all_units = [u for g in groups for u in g['unit_ids']]
        self.assertEqual(len(all_units), len(set(all_units)))
        self.assertIsNone(result['targets']['skipped'])
        self.assertEqual([test_request_filename(case)], list(result['documents']))

    def test_document_asks_for_results_and_dispositions_without_deciding(self):
        _, result = self.build()
        doc = next(iter(result['documents'].values()))
        for phrase in ('# 테스트 의뢰서 검토 초안', '수신 대상 역할: 테스트팀', 'C-D001 ~ C-D005 | 5대',
                       'C-S001 ~ C-S120 | 120대', '합계 125대', 'C는 시험을 수행하거나 조치를 판정하지 않는다',
                       '| 송부 상태 | 미송부 |', '| 테스트팀 접수 | 미확인 |', '미검사', '고객 교체 / 보관 대체 / 출고 허용 / 보류 / 미결',
                       '표본 결과를 나머지 보드에 적용하지 않는다', 'C-D006 ~ C-D080 (75대)', '이번 의뢰에 포함하지 않는다',
                       '확인된 불량이나 원인이 아님', CLAIM_ID, CONFIG):
            self.assertIn(phrase, doc)

    def test_held_boards_are_left_out_when_the_hold_scope_is_not_ready(self):
        _, result = self.build(hold={'state': 'stock_list_incomplete', 'node': None, 'reason': 'list incomplete'})
        self.assertEqual([5], [len(g['unit_ids']) for g in result['targets']['groups']])
        self.assertEqual('stock_list_incomplete', result['targets']['skipped']['state'])
        doc = next(iter(result['documents'].values()))
        self.assertIn('보관 보류 보드는 포함하지 않음', doc)
        self.assertIn('합계 5대', doc)

    def test_test_request_node_is_a_valid_graph_addition(self):
        case, result = self.build()
        node = result['node']
        self.assertEqual(('TestRequest', case['id'], 125), (node['type'], node['case_id'], len(node['unit_ids'])))
        self.assertTrue(node['document_ref'].endswith(test_request_filename(case)))
        claim, _, _, hold = scenario()
        outcome = validate_graph(existing_nodes() + claim_response_nodes() + claim + [case, hold['node'], node])
        self.assertEqual([], outcome['errors'])

    def test_document_is_written_only_inside_the_request_folder(self):
        _, result = self.build()
        name, text = next(iter(result['documents'].items()))
        with tempfile.TemporaryDirectory() as folder:
            path = write_request(text, name, base_dir=folder)
            self.assertEqual(text, path.read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            prepare_test_request(scenario()[1], LINE_C01, {'state': 'empty', 'node': None}, prepared_at='2026-10-26',
                                 external_claim_id=CLAIM_ID, configuration_id=CONFIG)


class ReviewDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals={
            'fixture': {'kind': 'human', 'roles': ['seed', 'read', 'audit']},
            'claim-agent': {'kind': 'agent', 'roles': ['read', 'draft']},
            'reviewer': {'kind': 'human', 'roles': ['read', 'review']},
        })
        self.addCleanup(self.service.close)
        self.claim, self.case, _, self.result = build()
        seeded = self.service.bootstrap('fixture', existing_nodes() + claim_response_nodes() + self.claim,
                                        request_key='request-seed')
        self.assertTrue(seeded['ok'], seeded)

    def test_requests_wait_for_a_human_review_and_stay_unsent(self):
        operations = [{'op': 'put', 'node': n} for n in [self.case] + self.result['nodes']]
        draft = self.service.prepare_draft('claim-agent', operations, request_key='containment-draft')
        self.assertTrue(draft['ok'], draft)
        draft_id, version = draft['data']['draft_id'], draft['data']['draft_version']
        saved = self.service.get_draft('claim-agent', draft_id, version)['data']
        self.assertEqual('미검토', REVIEW_LABELS[saved['status']])
        agent_review = self.service.review('claim-agent', draft_id, version, decision='approve',
                                           note='agent must not approve', request_key='agent-review')
        self.assertFalse(agent_review['ok'])
        self.assertIn(agent_review['errors'][0]['code'], ('FORBIDDEN', 'SELF_APPROVAL_FORBIDDEN'))
        decision = self.service.review('reviewer', draft_id, version, decision='approve',
                                       note='scope and wording checked', request_key='human-review')
        self.assertTrue(decision['ok'], decision)
        status = self.service.get_draft('reviewer', draft_id, version)['data']['status']
        self.assertEqual('검토 완료 (그래프 반영 전)', REVIEW_LABELS[status])
        self.assertEqual(1, self.service._version())
        stop = of_kind(self.result['nodes'], PRODUCTION_STOP)
        line = next(n for n in self.result['nodes'] if n['id'] == stop['scope_id'])
        doc = render_request(stop, self.case, line, external_claim_id=CLAIM_ID, configuration_id=CONFIG,
                             review_status=status, document_version=version)
        self.assertIn('| 송부 상태 | 미송부 |', doc)


if __name__ == '__main__':
    unittest.main()
