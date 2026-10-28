"""Real HTTP/PostgreSQL retrieval checks; embeddings and chat are test doubles."""
import json

from ontologics.demo import fixture_nodes
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import digest
from ontologics.openai_provider import ProviderError
from ontologics.retrieval import EvidenceIndex
from ontologics.service import Service
from ontologics.web import USERS, seed_development
from tests.db import database_for_test
from tests.test_assistant import ScriptedScreen
from tests.test_web import WebCase


class FakeEmbeddings:
    model = 'test-fixed-embedding-not-semantic-quality'
    dimensions = 3

    def __init__(self):
        self.calls = []
        self.failures_remaining = 0

    def public_config(self):
        return {'provider': 'test-double', 'model': self.model, 'dimensions': self.dimensions}

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise ProviderError('OPENAI_EMBEDDING_UNAVAILABLE', 'private upstream response detail')
        return [[1, 0, 0] for _ in texts]


class SemanticSearchTests(WebCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.assertTrue(seed_development(self.database)['ok'])
        self.embeddings = FakeEmbeddings()
        self.identities = DevelopmentIdentityProvider(USERS, enabled=True)
        self.host = CommonHost(self.identities, self.database, embedding_provider=self.embeddings)
        self.clients = {}
        self.alice = self.start('alice')
        self.bob = self.start('bob')
        self.reader = self.start('reader')
        _, self.operations = fixture_nodes()

    def index_fixture(self):
        service = Service(self.database, principals={
            'test-indexer': {'kind': 'system', 'roles': ['read', 'index']}})
        try:
            index = EvidenceIndex(service, 'test-indexer', model=self.embeddings.model,
                                  dimensions=self.embeddings.dimensions)
            docs = index.documents(['evidence:olimex-L-wroom:row-55', 'evidence:demo:initial'], version=1)
            self.assertTrue(docs['ok'], docs)
            entries = [{'evidence_id': item['evidence_id'], 'content_hash': item['content_hash'],
                        'embedding': vector}
                       for item, vector in zip(docs['data']['documents'], ([1, 0, 0], [0, 1, 0]))]
            indexed = index.index(entries, version=1, request_key='test-evidence-v1')
            self.assertTrue(indexed['ok'], indexed)
            return {item['evidence_id']: json.loads(item['text']) for item in docs['data']['documents']}
        finally:
            service.close()

    def confirmed_state(self):
        with self.host.request(DevelopmentContext('dev-alice')) as session:
            db = session._service.db
            return digest({table: db.execute('SELECT * FROM ' + table).fetchall()
                           for table in ('snapshots', 'drafts', 'review_events')})

    def assert_failed_run(self, response, code):
        with self.host.request(DevelopmentContext('dev-alice')) as session:
            row = session._service.db.execute(
                'SELECT actor,status,error_code FROM runs WHERE id=%s', (response['run_id'],)).fetchone()
        self.assertEqual({'actor': 'user:dev-alice', 'status': 'failed', 'error_code': code}, row)

    def test_candidates_preserve_sources_origin_and_exact_version_without_graph_changes(self):
        before = self.confirmed_state()
        documents = self.index_fixture()
        args = {'query': 'LAN8710 원본과 시연용 장착 기록', 'version': 1, 'origin': 'all'}
        result = self.ok(self.tool('semantic_search', args))
        data = result['data']
        self.assertEqual(1, result['data_version'])
        self.assertEqual('semantic', data['mode'])
        self.assertTrue(data['candidate_only'])
        self.assertEqual(2, data['index_coverage']['indexed'])
        self.assertGreater(data['index_coverage']['total'], 2)
        self.assertFalse(data['index_coverage']['complete'])
        self.assertEqual({'SIMILARITY_NOT_A_FACT', 'PARTIAL_EVIDENCE_INDEX'},
                         {warning['code'] for warning in result['warnings']})
        self.assertEqual(set(documents), {item['evidence'][0] for item in data['candidates']})
        for candidate in data['candidates']:
            source = documents[candidate['evidence'][0]]
            self.assertEqual(source['source'], candidate['source'])
            self.assertEqual(source['evidence']['locator'], candidate['locator'])
            self.assertEqual(source['evidence']['origin'], candidate['origin'])
            self.assertTrue(candidate['source']['sha256'])
            self.assertEqual(source['facts'], candidate['facts'])
        for origin, evidence_id in (('real', 'evidence:olimex-L-wroom:row-55'),
                                    ('synthetic', 'evidence:demo:initial')):
            filtered = self.ok(self.tool('semantic_search', {**args, 'origin': origin}, self.reader))
            self.assertEqual([evidence_id], [c['evidence'][0] for c in filtered['data']['candidates']])
            self.assertTrue(all(c['origin'] == origin for c in filtered['data']['candidates']))
        self.assertEqual(before, self.confirmed_state())

        # An actual approved version change must not borrow the old version's index.
        draft = self.draft('semantic-version-fixture')
        self.ok(self.http(self.bob, '/api/review', self.review_body(draft)))
        approved = self.confirmed_state()
        calls = len(self.embeddings.calls)
        for selected in ({**args, 'version': 2}, {'query': args['query'], 'origin': 'all'}):
            missing = self.failed(self.tool('semantic_search', selected), 503, 'EVIDENCE_INDEX_NOT_READY')
            self.assert_failed_run(missing, 'EVIDENCE_INDEX_NOT_READY')
        self.assertEqual(calls, len(self.embeddings.calls))
        historical = self.ok(self.tool('semantic_search', args))
        self.assertEqual(1, historical['data_version'])
        self.assertEqual(data['candidates'], historical['data']['candidates'])
        self.assertEqual(approved, self.confirmed_state())

    def test_knowledge_matches_direct_retrieval_and_embeds_once_per_request(self):
        self.index_fixture()
        before = self.confirmed_state()
        args = {'query': 'LAN8710 원본 근거', 'origin': 'real', 'version': 1}
        direct = self.ok(self.tool('semantic_search', args))
        self.assertEqual([[args['query']]], self.embeddings.calls)
        self.alice.app.provider_factory = lambda names: ScriptedScreen()
        body = {'screen': 'evidence', 'version': 1, 'question': '이 검색 후보의 근거와 한계를 설명해줘',
                'context': {'search_query': args['query'], 'origin': 'real', 'search_mode': 'semantic'}}
        for expected_calls in (2, 3):
            explained = self.ok(self.http(self.alice, '/api/assistant', body))
            data = explained['data']
            self.assertEqual('knowledge', data['role'])
            self.assertEqual('matched', data['consistency'])
            self.assertTrue(data['runtime']['ok'])
            typed = data['agent_result']
            self.assertEqual('knowledge', typed['agent_name'])
            self.assertEqual(['evidence:olimex-L-wroom:row-55'], typed['payload']['evidence_ids'])
            self.assertEqual('real', typed['evidence_refs'][0]['origin'])
            self.assertEqual(['semantic_search'], [call['name'] for call in data['tool_calls']])
            self.assertEqual(args, data['tool_calls'][0]['arguments'])
            self.assertEqual(direct['data'], data['results'][0]['data'])
            self.assertEqual(direct['evidence'], explained['evidence'])
            self.assertEqual('', data['runtime']['text'])
            self.assertTrue(data['runtime']['output_withheld'])
            self.assertEqual('verified_evidence_template', data['explanation_mode'])
            self.assertEqual({'data_version': 1, 'answerable': True,
                              'selected_evidence_ids': ['evidence:olimex-L-wroom:row-55']},
                             data['verified_selection'])
            self.assertEqual(digest(data['display_text']), data['display_hash'])
            self.assertIn('evidence:olimex-L-wroom:row-55', data['display_text'])
            self.assertIn('승인된 데이터 버전: v1', data['display_text'])
            self.assertIn('원본 버전: ' + direct['data']['candidates'][0]['source']['version'], data['display_text'])
            self.assertIn('원본 BOM 표기상 실장', data['display_text'])
            self.assertNotIn('원본 BOM 실장 여부 미확인', data['display_text'])
            with self.host.request(DevelopmentContext('dev-alice')) as session:
                logged = session._service.db.execute('SELECT output_hash FROM runtime_runs WHERE id=%s',
                    (data['runtime']['run_id'],)).fetchone()
            self.assertEqual(logged['output_hash'], data['runtime']['output_hash'])
            self.assertNotEqual(logged['output_hash'], data['display_hash'])
            # The direct parity check and the Agent's call share only this request's cache.
            self.assertEqual(expected_calls, len(self.embeddings.calls))
        for attack in ('semantic_version', 'semantic_source_version', 'semantic_foreign_evidence'):
            self.alice.app.provider_factory = lambda names, attack=attack: ScriptedScreen(attack=attack)
            refused = self.failed(self.http(self.alice, '/api/assistant', body), 502, 'AI_EVIDENCE_MISMATCH')
            self.assertFalse(refused['data'].get('display_text'))
            self.assertEqual('', refused['data']['runtime']['text'])
            self.assertEqual('not_verified', refused['data']['consistency'])
        self.alice.app.provider_factory = lambda names: ScriptedScreen(attack='semantic_unanswerable')
        unanswered = self.ok(self.http(self.alice, '/api/assistant', body))['data']
        self.assertEqual('', unanswered['runtime']['text'])
        self.assertEqual({'data_version': 1, 'answerable': False, 'selected_evidence_ids': []},
                         unanswered['verified_selection'])
        self.assertEqual(digest(unanswered['display_text']), unanswered['display_hash'])
        self.assertTrue(unanswered['results'][0]['data']['candidate_only'])
        self.assertNotIn('조건상 제외', unanswered['display_text'])
        self.assertIn('판정은 아닙니다', unanswered['display_text'])
        self.assertEqual('matched', unanswered['consistency'])
        self.assertTrue(all(call == [args['query']] for call in self.embeddings.calls))
        self.assertEqual(before, self.confirmed_state())

    def test_preflight_denial_and_embedding_failure_are_recorded_and_retryable(self):
        args = {'query': '장착 근거를 찾아줘', 'origin': 'all', 'version': 1}
        before = self.confirmed_state()
        self.alice.app.host = CommonHost(self.identities, self.database)
        missing = self.failed(self.tool('semantic_search', args), 503, 'EMBEDDINGS_NOT_CONFIGURED')
        self.assert_failed_run(missing, 'EMBEDDINGS_NOT_CONFIGURED')
        self.alice.app.host = self.host
        unindexed = self.failed(self.tool('semantic_search', args), 503, 'EVIDENCE_INDEX_NOT_READY')
        self.assert_failed_run(unindexed, 'EVIDENCE_INDEX_NOT_READY')
        self.index_fixture()
        blocked_identity = DevelopmentIdentityProvider(
            [UserIdentity('dev-alice', frozenset())], enabled=True)
        self.alice.app.host = CommonHost(blocked_identity, self.database, embedding_provider=self.embeddings)
        blocked = self.failed(self.tool('semantic_search', args), 403, 'FORBIDDEN')
        self.assert_failed_run(blocked, 'FORBIDDEN')
        self.assertEqual([], self.embeddings.calls)
        self.alice.app.host = self.host

        self.embeddings.failures_remaining = 1
        failed = self.failed(self.tool('semantic_search', args), 502, 'OPENAI_EMBEDDING_UNAVAILABLE')
        self.assert_failed_run(failed, 'OPENAI_EMBEDDING_UNAVAILABLE')
        self.assertNotIn('private upstream response detail', json.dumps(failed))
        self.assertIsNone(failed['data'])
        recovered = self.ok(self.tool('semantic_search', args))
        self.assertTrue(recovered['data']['candidate_only'])
        self.assertEqual(1, recovered['data_version'])
        self.assertEqual([[args['query']], [args['query']]], self.embeddings.calls)
        self.assertEqual(before, self.confirmed_state())
