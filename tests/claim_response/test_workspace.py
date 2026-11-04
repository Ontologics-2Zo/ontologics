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
