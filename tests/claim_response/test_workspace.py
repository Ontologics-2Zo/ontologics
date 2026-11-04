"""C form-to-common-draft workflows against disposable PostgreSQL schemas."""
import copy
import hashlib
import json
import unittest
import uuid

from ontologics.business_drafts import prepare_business_draft
from ontologics.claim_response.workspace import build_records
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.model import ROOT, canonical, digest
from ontologics.service import Service
from ontologics.web import USERS, seed_development
from tests.db import database_for_test


class ClaimWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.assertTrue(seed_development(self.database)['ok'])
        self.host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), self.database)
        self.audit = Service(self.database, principals={'audit': {'kind': 'human', 'roles': ['read']}})
        self.addCleanup(self.audit.close)
        self.token = uuid.uuid4().hex
        self.serial = next(n['serial'] for n in self.audit._nodes() if n['id'] == 'unit:U104')
        self.args = dict(action='intake', payload={
            'external_claim_id': 'CLM-DEMO-' + self.token, 'customer_name': '가정 고객사 DEMO',
            'reported_at': '2026-11-04T10:00:00+09:00',
            'reported_symptom': '전원 표시가 없다는 고객 신고. 원인은 확인하지 않음.',
            'claimed_quantity': 1, 'reported_serials': [self.serial]},
            base_version=1, source_text='시연용 신고 원문 ' + self.token,
            authored_at='2026-11-04T10:00:00+09:00', notice='실제 고객 정보가 아닌 가상 C 접수 시험',
            synthetic_confirmed=True, request_key='intake')

    def prepare(self, user='alice', **changes):
        args = {**copy.deepcopy(self.args), **changes}
        document = {'origin': 'synthetic', 'domain': 'claims', 'action': args['action'],
            **{key: args[key] for key in ('authored_at', 'notice', 'source_text')},
            'submitted_fields': args['payload']}
        checksum = hashlib.sha256(canonical(document).encode()).hexdigest()
        path = ROOT / 'data/receipts' / ('business-' + checksum + '.json')
        self.addCleanup(path.unlink, missing_ok=True)
        with self.host.request(DevelopmentContext('dev-' + user)) as session:
            return prepare_business_draft(session._service, session.context['actor'],
                                          domain='claims', builder=build_records, **args)

    def ok(self, result):
        self.assertTrue(result['ok'], result)
        return result

    def error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def review(self, draft, user='bob', key='review'):
        with self.host.request(DevelopmentContext('dev-' + user)) as session:
            return session.review(draft['draft_id'], draft['draft_version'], decision='approve',
                note='가상 원문·신고 일련번호와 명시한 보드 대응을 확인', request_key=key)

    def apply(self, draft, key='apply'):
        return self.host.apply_approved(draft['draft_id'], draft['draft_version'], request_key=key)

    def test_intake_and_confirmed_case_keep_source_author_approval_and_prior_version(self):
        original = digest(self.audit._nodes(1))
        intake = self.ok(self.prepare())['data']
        self.assertTrue(intake['validation']['valid'], intake)
        self.assertEqual('awaiting_review', intake['status'])
        claim_id = intake['registration']['record_ids'][0]
        self.assertFalse(any(n['id'] == claim_id for n in self.audit._nodes()))
        stored = self.ok(self.audit.get_draft('audit', intake['draft_id'], 1))['data']
        self.assertEqual('user:dev-alice', stored['author'])
        source = intake['registration']['source']
        raw = (ROOT / source['path']).read_bytes()
        self.assertEqual(source['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.args['source_text'], json.loads(raw)['source_text'])
        self.error(self.review(intake, 'alice', 'self'), 'SELF_APPROVAL_FORBIDDEN')
        self.error(self.apply(intake, 'before-approval'), 'APPROVAL_REQUIRED')
        self.ok(self.review(intake))
        self.assertEqual(2, self.ok(self.apply(intake))['data_version'])
        approved = {n['id']: n for n in self.audit._nodes()}
        self.assertEqual('needs_review', approved[claim_id]['intake_state'])
        confirmed = self.ok(self.prepare(action='confirm_case', base_version=2, request_key='case',
            payload={'intake_id': claim_id, 'unit_ids': ['unit:U104'], 'serials_confirmed': True}))['data']
        self.assertTrue(confirmed['validation']['valid'], confirmed)
        self.assertEqual(2, self.audit._version())
        self.assertEqual('needs_review', approved[claim_id]['intake_state'])
        self.ok(self.review(confirmed, key='case-review'))
        self.assertEqual(3, self.ok(self.apply(confirmed, 'case-apply'))['data_version'])
        approved = {n['id']: n for n in self.audit._nodes()}
        case = approved[confirmed['registration']['record_ids'][1]]
        self.assertEqual('confirmed', approved[claim_id]['intake_state'])
        self.assertEqual(['unit:U104'], case['unit_ids'])
        self.assertEqual(self.args['payload']['reported_symptom'], case['reported_symptom'])
        self.assertEqual(2, len(approved[claim_id]['evidence']))
        self.assertEqual('synthetic', case['origin'])
        self.assertEqual(original, digest(self.audit._nodes(1)))
        self.assertEqual(3, self.ok(self.apply(confirmed, 'case-apply'))['data_version'])

    def test_exact_draft_revision_retry_and_stale_base_are_common(self):
        first = self.ok(self.prepare())['data']
        self.assertEqual(first['draft_id'], self.ok(self.prepare())['data']['draft_id'])
        self.error(self.prepare(source_text='다른 원문'), 'IDEMPOTENCY_CONFLICT')
        revised = self.ok(self.prepare(request_key='revised', draft_id=first['draft_id'],
            expected_version=1, source_text='보완한 가상 신고 ' + self.token))['data']
        self.assertEqual(2, revised['draft_version'])
        self.error(self.review(first), 'SUPERSEDED_DRAFT')
        self.ok(self.review(revised, key='review-revised'))
        self.ok(self.apply(revised))
        self.error(self.prepare(request_key='stale-new'), 'STALE_BASE')
        replay = self.ok(self.prepare(request_key='revised', draft_id=first['draft_id'],
            expected_version=1, source_text='보완한 가상 신고 ' + self.token))['data']
        self.assertEqual(revised['draft_id'], replay['draft_id'])
        self.assertEqual(revised['draft_version'], replay['draft_version'])
        self.error(self.prepare(base_version=2, request_key='duplicate'), 'CLAIM_ALREADY_REGISTERED')

    def test_missing_demo_confirmation_and_unrelated_boards_are_rejected(self):
        self.error(self.prepare(user='reader'), 'FORBIDDEN')
        self.error(self.prepare(synthetic_confirmed=False), 'INVALID_BUSINESS_INPUT')
        self.error(self.prepare(payload={**self.args['payload'], 'intake_state': 'confirmed'}), 'INVALID_CLAIM_INPUT')
        self.error(self.prepare(payload={**self.args['payload'], 'customer_name': 'Actual Customer'}), 'DEMO_CLAIM_REQUIRED')
        first = self.ok(self.prepare())['data']
        self.ok(self.review(first))
        self.ok(self.apply(first))
        claim_id = first['registration']['record_ids'][0]
        self.error(self.prepare(action='confirm_case', base_version=2, request_key='wrong-board',
            payload={'intake_id': claim_id, 'unit_ids': ['unit:U106'], 'serials_confirmed': True}), 'CLAIM_SERIAL_MISMATCH')
        self.error(self.prepare(action='confirm_case', base_version=2, request_key='not-confirmed',
            payload={'intake_id': claim_id, 'unit_ids': ['unit:U104'], 'serials_confirmed': False}), 'CLAIM_UNITS_REQUIRED')
        self.assertEqual(2, self.audit._version())
        self.assertFalse(any(n['type'] == 'QualityCase' for n in self.audit._nodes()))


# ---------------------------------------------------------------- common-screen claim flow
# The file-driven steps and read-only views behind the common screen's claim tabs. These run on the
# seed the demonstration uses (examples/claim_response/seed_common.py) without PostgreSQL: the common
# host attaches origin/evidence and approves; here each step's records are applied as approved.
from ontologics.claim_response import workspace as claim_workspace
from ontologics.domain_schema import C_TYPES
from ontologics.model import validate_graph
from ontologics.service import DomainError
from examples.claim_response.seed_common import seed_nodes

FLOW_AT = '2026-11-04T18:00:00+09:00'
FLOW_EVIDENCE = 'evidence:claim_response:batch:delivered'


class _Graph:
    """The shared lookup as the views see it: one approved version of every node."""
    def __init__(self, index, ok=True):
        self.index, self.ok, self.calls = index, ok, []

    def lookup(self, actor, *, version=None):
        self.calls.append((actor, version))
        if not self.ok:
            return {'ok': False, 'data': None, 'errors': [{'code': 'FORBIDDEN', 'message': 'no read'}]}
        return {'ok': True, 'data': list(self.index.values()), 'evidence': ['every'], 'data_version': version,
                'conflicts': ['any'], 'errors': []}


class ClaimFileFlowTests(unittest.TestCase):
    def setUp(self):
        self.index = {node['id']: node for node in seed_nodes()}

    def step(self, action, payload):
        records = claim_workspace.build_records(list(self.index.values()), action, payload)
        self.assertTrue(1 <= len(records) <= claim_workspace.RECORD_LIMIT)
        for record in records:
            self.assertIn(record['type'], C_TYPES)
            self.assertFalse({'origin', 'evidence'} & set(record))
            self.index[record['id']] = {**record, 'origin': 'synthetic', 'evidence': [FLOW_EVIDENCE]}
        return records

    def view(self, action, **arguments):
        return claim_workspace.query(_Graph(self.index), 'alice', action, {**arguments, 'version': 1})['data']

    def kinds(self, records):
        return sorted(record['type'] for record in records)

    def one(self, kind):
        found = [node for node in self.index.values() if node['type'] == kind]
        self.assertEqual(len(found), 1, kind)
        return found[0]

    def run_to_test_request(self, claim_file):
        self.step('intake_file', {'file': claim_file, 'collected_at': FLOW_AT})
        self.step('trace_case', {'intake_id': self.one('ClaimIntake')['id']})
        case = self.one('QualityCase')
        self.step('containment', {'case_id': case['id'], 'prepared_at': FLOW_AT})
        self.step('test_request', {'case_id': case['id'], 'prepared_at': FLOW_AT})
        return case

    def register_results(self, case, result_file):
        parts = []
        while True:
            try:
                parts.append(len(self.step('test_results', {'case_id': case['id'], 'file': result_file, 'collected_at': FLOW_AT})))
            except DomainError as error:
                self.assertEqual(error.code, 'TEST_RESULTS_COMPLETE')
                return parts

    def test_mail_claim_runs_to_six_confirmed_in_a_valid_graph(self):
        intake = self.step('intake_file', {'file': '1_메일_전수시험_클레임.eml', 'collected_at': FLOW_AT})
        self.assertEqual(self.kinds(intake), ['ClaimIntake'])
        self.assertEqual(intake[0]['reported_serials'], ['C-D021', 'C-D022', 'C-D023'])
        with self.assertRaises(DomainError) as again:
            self.step('intake_file', {'file': '1_메일_전수시험_클레임.eml', 'collected_at': FLOW_AT})
        self.assertEqual(again.exception.code, 'CLAIM_ALREADY_REGISTERED')
        traced = self.step('trace_case', {'intake_id': intake[0]['id']})
        self.assertEqual(self.kinds(traced), ['ClaimIntake', 'QualityCase'])
        self.assertEqual(self.one('ClaimIntake')['intake_state'], 'confirmed')
        case = self.one('QualityCase')
        held = self.step('containment', {'case_id': case['id'], 'prepared_at': FLOW_AT})
        self.assertEqual(self.kinds(held), ['ContainmentRequest'] * 2 + ['QualityScope'] * 3)
        request = self.step('test_request', {'case_id': case['id'], 'prepared_at': FLOW_AT})
        self.assertEqual(len(request[0]['unit_ids']), 123)
        with self.assertRaises(DomainError) as twice:
            self.step('test_request', {'case_id': case['id'], 'prepared_at': FLOW_AT})
        self.assertEqual(twice.exception.code, 'TEST_REQUEST_EXISTS')
        self.assertEqual(self.register_results(case, '1_테스트팀_결과.csv'), [100, 23])
        reproduction = self.step('reproduction', {'case_id': case['id'], 'collected_at': FLOW_AT})[0]
        self.assertEqual((reproduction['customer_replacement_quantity'], reproduction['stock_replacement_quantity'],
                          reproduction['available_replacement_quantity'], reproduction['required_good_quantity'],
                          reproduction['unresolved_quantity'], reproduction['assessment_state']),
                         (3, 10, 7, 6, 0, 'confirmed'))
        report = validate_graph(list(self.index.values()))
        self.assertTrue(report['valid'], report['errors'][:3])

    def test_sampled_sheet_claim_is_a_lower_bound_not_a_total(self):
        case = self.run_to_test_request('2_엑셀_표본시험_클레임.xlsx')
        self.register_results(case, '2_테스트팀_결과.csv')
        reproduction = self.step('reproduction', {'case_id': case['id'], 'collected_at': FLOW_AT})[0]
        self.assertEqual((reproduction['required_good_quantity'], reproduction['unresolved_quantity'],
                          reproduction['assessment_state']), (3, 90, 'lower_bound'))

    def test_miswritten_serial_stops_before_a_case_and_is_never_corrected(self):
        self.step('intake_file', {'file': '4_메일_일련번호오기재_클레임.eml', 'collected_at': FLOW_AT})
        intake = self.one('ClaimIntake')
        match = self.view('serial_match', intake_id=intake['id'])
        self.assertFalse(match['ready'])
        self.assertEqual([(row['serial'], row['match_state']) for row in match['links']],
                         [('C-D071', 'matched'), ('C-D072', 'matched'), ('C-D730', 'not_found')])
        with self.assertRaises(DomainError) as stopped:
            self.step('trace_case', {'intake_id': intake['id']})
        self.assertEqual(stopped.exception.code, 'CLAIM_SERIAL_UNRESOLVED')
        self.assertIn('C-D730', str(stopped.exception))
        self.assertFalse(any(node['type'] == 'QualityCase' for node in self.index.values()))

    def test_faulty_result_file_saves_nothing_until_the_corrected_file(self):
        case = self.run_to_test_request('5_CSV_결과파일오류_클레임.csv')
        faulty = self.view('result_file', case_id=case['id'], file='5_테스트팀_결과_오류.csv')
        self.assertEqual(faulty['state'], 'blocked')
        self.assertEqual(sorted({issue['code'] for issue in faulty['issues']}),
                         ['DUPLICATE_RESULT', 'TIMEZONE_REQUIRED', 'UNIT_NOT_REQUESTED'])
        with self.assertRaises(DomainError) as blocked:
            self.step('test_results', {'case_id': case['id'], 'file': '5_테스트팀_결과_오류.csv', 'collected_at': FLOW_AT})
        self.assertEqual(blocked.exception.code, 'TEST_RESULT_BLOCKED')
        self.assertFalse(any(node['type'] == 'QualityTestResult' for node in self.index.values()))
        self.register_results(case, '5_테스트팀_결과_수정본.csv')
        reproduction = self.step('reproduction', {'case_id': case['id'], 'collected_at': FLOW_AT})[0]
        self.assertEqual((reproduction['required_good_quantity'], reproduction['assessment_state']), (3, 'confirmed'))

    def test_views_show_the_file_as_written_and_documents_from_stored_records(self):
        mail = self.view('claim_file', file='1_메일_전수시험_클레임.eml')
        self.assertEqual(mail['original']['kind'], 'mail')
        self.assertIn('가정 고객사 C-01', mail['original']['text'])
        self.assertEqual((mail['claimed_quantity'], mail['claim']['requested_due_date'], mail['registered']), (3, '2026-11-13', False))
        sheet = self.view('claim_file', file='2_엑셀_표본시험_클레임.xlsx')
        self.assertEqual(sheet['original']['kind'], 'table')
        self.assertIn('고객 클레임 번호', sheet['original']['columns'])
        case = self.run_to_test_request('1_메일_전수시험_클레임.eml')
        self.assertTrue(self.view('claim_file', file='1_메일_전수시험_클레임.eml')['registered'])
        containment = self.view('containment_documents', case_id=case['id'])
        self.assertTrue(containment['saved'])
        self.assertEqual(sorted(doc['markdown'].splitlines()[0] for doc in containment['documents']),
                         ['# 생산 중단 요청서 검토 초안', '# 출고 배송 중단 요청서 검토 초안'])
        stored = next(node for node in self.index.values() if node['type'] == 'ContainmentRequest')
        self.assertEqual(containment['prepared_at'], stored['prepared_at'])
        test = self.view('test_request_document', case_id=case['id'])
        self.assertEqual((test['saved'], test['unit_count']), (True, 123))
        with self.assertRaises(DomainError) as early:
            self.view('reproduction_document', case_id=case['id'])
        self.assertEqual(early.exception.code, 'NOT_FOUND')
        self.register_results(case, '1_테스트팀_결과.csv')
        self.step('reproduction', {'case_id': case['id'], 'collected_at': FLOW_AT})
        document = self.view('reproduction_document', case_id=case['id'])
        self.assertEqual(document['requested_due_date'], '2026-11-13')
        self.assertIn('정상 완제품 **6대** (확정 총량)', document['markdown'])
        self.assertIn('날짜: 2026-11-13', document['markdown'])

    def test_views_are_reads_on_an_explicit_version_with_exact_input(self):
        graph = _Graph(self.index)
        before = canonical(list(self.index.values()))
        result = claim_workspace.query(graph, 'reader', 'claim_file', {'file': '1_메일_전수시험_클레임.eml', 'version': 4})
        self.assertEqual((graph.calls, result['data_version'], result['evidence'], result['conflicts']), ([('reader', 4)], 4, [], []))
        self.assertEqual(canonical(list(self.index.values())), before)
        for action, arguments, code in [
                ('claim_file', {'file': '1_메일_전수시험_클레임.eml'}, 'INVALID_INPUT'),
                ('claim_file', {'file': '1_메일_전수시험_클레임.eml', 'version': 1, 'extra': 1}, 'INVALID_INPUT'),
                ('claim_file', {'file': '1_메일_전수시험_클레임.eml', 'version': True}, 'INVALID_VERSION'),
                ('claim_file', {'file': '1_메일_전수시험_클레임.eml', 'version': -1}, 'INVALID_VERSION'),
                ('unknown', {'version': 1}, 'INVALID_INPUT'),
                ('claim_file', {'file': '../scenario_inputs/README.md', 'version': 1}, 'NOT_FOUND'),
                ('result_file', {'case_id': 'quality-case:none', 'file': '1_테스트팀_결과.csv', 'version': 1}, 'NOT_FOUND')]:
            with self.subTest(action=action, arguments=arguments), self.assertRaises(DomainError) as refused:
                claim_workspace.query(_Graph(self.index), 'alice', action, arguments)
            self.assertEqual(refused.exception.code, code)
        denied = claim_workspace.query(_Graph(self.index, ok=False), 'alice', 'claim_file', {'file': '1_메일_전수시험_클레임.eml', 'version': 1})
        self.assertEqual(denied['errors'][0]['code'], 'FORBIDDEN')
