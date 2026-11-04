"""File-backed C workflow over actual HTTP and a disposable PostgreSQL schema."""
import hashlib
from urllib.parse import urlencode

from examples.claim_response.seed_common import seed_nodes
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentIdentityProvider
from ontologics.model import ROOT
from ontologics.service import Service
from ontologics.web import USERS
from tests.db import database_for_test
from tests.test_web import WebCase


class ClaimFileHttpTests(WebCase):
    def setUp(self):
        self.database = database_for_test(self)
        service = Service(self.database, principals={'fixture': {'kind': 'human', 'roles': ['seed']}})
        try:
            result = service.bootstrap('fixture', seed_nodes(), request_key='c-http-seed')
            self.assertTrue(result['ok'], result)
        finally:
            service.close()
        self.host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), self.database)
        self.clients = {}
        self.alice, self.bob, self.reader = [self.start(user) for user in ('alice', 'bob', 'reader')]
        self.version = 1
        self.paths_before = set((ROOT / 'data/receipts').glob('business-*'))
        self.addCleanup(self.clean_sources)

    def clean_sources(self):
        for path in set((ROOT / 'data/receipts').glob('business-*')) - self.paths_before:
            path.unlink(missing_ok=True)

    def query(self, action, **arguments):
        return self.ok(self.http(self.alice, '/api/claims/query',
            {'action': action, 'arguments': dict(arguments, version=self.version)}))

    def submit(self, action, payload, key):
        body = dict(domain='claims', action=action, payload=payload, base_version=self.version,
            source_text='시연용 원본 파일·승인 범위 확인', authored_at='2026-11-04T18:00:00+09:00',
            notice='실제 회사 기록이 아닌 회귀 시험', synthetic_confirmed=True, request_key=key)
        result = self.ok(self.http(self.alice, '/api/business/draft', body))
        draft = result['data']
        self.assertTrue(draft['validation']['valid'], draft['validation'])
        saved = self.ok(self.tool('get_draft', {'draft_id': draft['draft_id'], 'version': draft['draft_version']}))['data']
        self.assertEqual('user:dev-alice', saved['author'])
        for source in draft['registration']['original_sources']:
            query = urlencode(dict(source_id=source['id'], draft_id=draft['draft_id'], version=draft['draft_version']))
            status, raw, _ = self.http(self.bob, '/api/draft-source?' + query)
            self.assertEqual(200, status)
            self.assertEqual(source['sha256'], hashlib.sha256(raw).hexdigest())
        self.failed(self.http(self.alice, '/api/review', self.review_body(draft, key='self-' + key)),
                    403, 'SELF_APPROVAL_FORBIDDEN')
        approved = self.ok(self.http(self.bob, '/api/review', self.review_body(draft, key='review-' + key)))
        self.version = approved['data_version']
        replay = self.ok(self.http(self.alice, '/api/business/draft', body))['data']
        self.assertEqual((draft['draft_id'], draft['draft_version']), (replay['draft_id'], replay['draft_version']))
        return draft['registration']['record_ids']

    def test_file_sources_partial_results_and_exact_approval_survive_full_c_flow(self):
        at = '2026-11-04T18:00:00+09:00'
        claim = self.submit('intake_file', {'file': '1_메일_전수시험_클레임.eml', 'collected_at': at}, 'intake')[0]
        approved = self.ok(self.tool('lookup', {'node_id': claim, 'version': self.version}))['data'][0]
        self.assertTrue(any(e.startswith('evidence:claim_response:') for e in approved['evidence']))
        case = self.submit('trace_case', {'intake_id': claim}, 'case')[1]
        self.submit('containment', {'case_id': case, 'prepared_at': at}, 'containment')
        self.submit('test_request', {'case_id': case, 'prepared_at': at}, 'request')
        file = '1_테스트팀_결과.csv'
        self.assertEqual(123, self.query('result_file', case_id=case, file=file)['data']['pending_count'])
        first = self.submit('test_results', {'case_id': case, 'file': file, 'collected_at': at}, 'results-1')
        self.assertEqual(100, len(first))
        partial = self.query('result_file', case_id=case, file=file)
        self.assertEqual(23, partial['data']['pending_count'])
        self.assertFalse(partial['data']['registration_complete'])
        self.assertTrue(partial['evidence'])
        second = self.submit('test_results', {'case_id': case, 'file': file, 'collected_at': at}, 'results-2')
        self.assertEqual(23, len(second))
        self.assertTrue(self.query('result_file', case_id=case, file=file)['data']['registration_complete'])
        self.submit('reproduction', {'case_id': case, 'collected_at': at}, 'reproduction')
        result = self.query('reproduction_document', case_id=case)
        self.assertEqual(6, result['data']['requirement']['required_good_quantity'])
        self.assertEqual('2026-11-13', result['data']['requested_due_date'])
        self.assertEqual(self.version, result['data_version'])
        denied = self.http(self.reader, '/api/business/draft', {'domain': 'claims', 'action': 'reproduction',
            'payload': {'case_id': case, 'collected_at': at}, 'base_version': self.version,
            'source_text': '시연용', 'authored_at': at, 'notice': '시험', 'synthetic_confirmed': True,
            'request_key': 'denied'})
        self.failed(denied, 403, 'FORBIDDEN')
