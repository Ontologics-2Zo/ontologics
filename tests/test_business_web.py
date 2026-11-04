"""Real HTTP identity, evidence and approval for the common C business form."""
import hashlib
import uuid

from ontologics.model import ROOT, canonical
from tests.test_web import WebCase


class BusinessWebTests(WebCase):
    def business(self, body, server=None):
        document = {'origin': 'synthetic', 'domain': body['domain'], 'action': body['action'],
            **{key: body[key] for key in ('authored_at', 'notice', 'source_text')},
            'submitted_fields': body['payload']}
        checksum = hashlib.sha256(canonical(document).encode()).hexdigest()
        self.addCleanup((ROOT / 'data/receipts' / ('business-' + checksum + '.json')).unlink, missing_ok=True)
        return self.http(server or self.alice, '/api/business/draft', body)

    def test_c_http_intake_review_case_and_exact_version_replay(self):
        unit = self.ok(self.tool('lookup', {'node_id': 'unit:U104', 'version': 1}))['data'][0]
        token = uuid.uuid4().hex
        body = {'domain': 'claims', 'action': 'intake', 'payload': {
            'external_claim_id': 'CLM-DEMO-HTTP-' + token, 'customer_name': 'DEMO 고객사',
            'reported_at': '2026-11-04T10:00:00+09:00', 'reported_symptom': '전원 표시 없음이라는 가상 고객 신고',
            'claimed_quantity': 1, 'reported_serials': [unit['serial']]},
            'base_version': 1, 'source_text': '가상 신고 원문 ' + token,
            'authored_at': '2026-11-04T10:00:00+09:00', 'notice': '시연용 클레임. 실제 불량 확인 아님.',
            'synthetic_confirmed': True, 'request_key': 'c-http-intake'}
        self.failed(self.business(body, self.reader), 403, 'FORBIDDEN')
        self.failed(self.business({**body, 'actor': 'user:dev-bob'}), 400, 'INVALID_INPUT')
        self.failed(self.business({**body, 'domain': []}), 400, 'INVALID_INPUT')
        intake = self.ok(self.business(body))['data']
        self.assertTrue(intake['validation']['valid'])
        saved = self.ok(self.tool('get_draft', {'draft_id': intake['draft_id'], 'version': 1}))['data']
        self.assertEqual('user:dev-alice', saved['author'])
        self.assertEqual('awaiting_review', saved['status'])
        self.assertEqual(intake['draft_id'], self.ok(self.business(body))['data']['draft_id'])
        self.failed(self.http(self.alice, '/api/review', self.review_body(intake, key='self')), 403, 'SELF_APPROVAL_FORBIDDEN')
        self.ok(self.http(self.bob, '/api/review', self.review_body(intake)))
        claim_id = intake['registration']['record_ids'][0]
        claim = self.ok(self.tool('lookup', {'node_id': claim_id, 'version': 2}))['data'][0]
        self.assertEqual('needs_review', claim['intake_state'])
        self.assertEqual('synthetic', claim['origin'])
        self.assertEqual([intake['registration']['evidence_id']], claim['evidence'])
        case_body = {**body, 'action': 'confirm_case', 'base_version': 2, 'request_key': 'c-http-case',
            'payload': {'intake_id': claim_id, 'unit_ids': ['unit:U104'], 'serials_confirmed': True},
            'source_text': '가상 신고 일련번호와 U104 직접 대조 ' + token}
        case = self.ok(self.business(case_body))['data']
        self.assertTrue(case['validation']['valid'])
        self.failed(self.http(self.alice, '/api/review', self.review_body(case, key='self-case')), 403, 'SELF_APPROVAL_FORBIDDEN')
        self.ok(self.http(self.bob, '/api/review', self.review_body(case, key='case-review')))
        response = self.ok(self.tool('lookup', {'node_id': case['registration']['record_ids'][1], 'version': 3}))
        self.assertEqual(3, response['data_version'])
        self.assertEqual(['unit:U104'], response['data'][0]['unit_ids'])
        self.assertEqual(body['payload']['reported_symptom'], response['data'][0]['reported_symptom'])
        self.assertTrue(response['evidence'])
        old = self.ok(self.tool('lookup', {'node_id': claim_id, 'version': 2}))['data'][0]
        new = self.ok(self.tool('lookup', {'node_id': claim_id, 'version': 3}))['data'][0]
        self.assertEqual('needs_review', old['intake_state'])
        self.assertEqual('confirmed', new['intake_state'])
        self.assertEqual(2, len(new['evidence']))
        # Completed writes replay with their exact ID/version even after application.
        replay = self.ok(self.business(case_body))['data']
        self.assertEqual((case['draft_id'], 1), (replay['draft_id'], replay['draft_version']))
        self.failed(self.business({**body, 'request_key': 'new-stale'}), 409, 'STALE_BASE')
        self.assertEqual(3, self.ok(self.http(self.alice, '/api/context'))['data_version'])
