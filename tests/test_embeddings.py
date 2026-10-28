"""Embedding transport and resumable pgvector indexing, without model API calls."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib import error

from examples.index_evidence import index_confirmed_evidence
from ontologics.database import in_transaction
from ontologics.demo import fixture_nodes
from ontologics.embeddings import (EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, ENDPOINT,
                                  MAX_TEXT_BYTES, OpenAIEmbeddings)
from ontologics.model import canonical
from ontologics.openai_provider import MODEL, OpenAISettings, ProviderError
from ontologics.retrieval import EvidenceIndex
from ontologics.service import DomainError, Service
from tests.db import database_for_test
from tests.test_service import PRINCIPALS
from ontologics.harness import BudgetLimits, ExecutionBudget, request_budget
from ontologics.paid_budget import CostPolicy, ModelPrice


KEY = 'fake-key-do-not-log'
TEXT = 'source-document-do-not-log'


def vector(index=0):
    result = [0.0] * EMBEDDING_DIMENSIONS
    result[index] = 1.0
    return result


def response_body(count=1):
    return {'model': EMBEDDING_MODEL, 'data': [
        {'index': index, 'embedding': vector(index)} for index in range(count)],
        'usage': {'prompt_tokens': 12, 'total_tokens': 12}}


class Response:
    def __init__(self, body):
        self.status = 200
        self.headers = {'x-request-id': 'req_test'}
        self.raw = json.dumps(body).encode() if not isinstance(body, bytes) else body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.raw[:limit]


class Transport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, req, **kwargs):
        self.requests.append((req, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(outcome)


def http_error(status):
    return error.HTTPError(ENDPOINT, status, 'remote echo ' + KEY + TEXT,
                           {'x-request-id': 'req_fail'}, io.BytesIO((KEY + TEXT).encode()))


class EmbeddingTransportTests(unittest.TestCase):
    def setUp(self):
        self.budget=ExecutionBudget(BudgetLimits(max_model_calls=64),
            cost_policy=CostPolicy('100',(ModelPrice(EMBEDDING_MODEL,'1','0',300000),)))
        scope=request_budget('embedding-test',self.budget)
        scope.__enter__()
        self.addCleanup(scope.__exit__,None,None,None)

    def adapter(self, transport):
        return OpenAIEmbeddings(OpenAISettings(KEY), opener=transport)

    def test_fixed_contract_preserves_order_and_logs_only_safe_metadata(self):
        body = response_body(2)
        body['data'].reverse()
        body['usage']['remote_extra'] = TEXT
        transport = Transport(body)
        adapter = self.adapter(transport)
        self.assertEqual([vector(0), vector(1)], adapter.embed([TEXT, 'second document']))
        req, kwargs = transport.requests[0]
        self.assertEqual(ENDPOINT, req.full_url)
        self.assertEqual('POST', req.method)
        self.assertEqual({'input': [TEXT, 'second document'], 'model': EMBEDDING_MODEL,
                          'dimensions': 1536, 'encoding_format': 'float'}, json.loads(req.data))
        self.assertEqual('Bearer ' + KEY, req.get_header('Authorization'))
        self.assertEqual(30, kwargs['timeout'])
        self.assertEqual(MODEL, adapter.settings.model)
        self.assertEqual({'prompt_tokens': 12, 'total_tokens': 12}, adapter.calls[0]['usage'])
        metadata = json.dumps({'config': adapter.public_config(), 'calls': adapter.calls})
        self.assertNotIn(KEY, metadata)
        self.assertNotIn(TEXT, metadata)
        self.assertEqual('completed', adapter.calls[0]['status'])

    def test_input_bounds_reject_before_transport_without_truncation(self):
        for texts in ([], ['a'] * 33, ('a',), [''], [' '], [None], ['\ud800'],
                      ['a' * (MAX_TEXT_BYTES + 1)], ['가' * 2731]):
            with self.subTest(shape=type(texts).__name__):
                transport = Transport()
                adapter = self.adapter(transport)
                with self.assertRaises(ProviderError):
                    adapter.embed(texts)
                self.assertEqual([], transport.requests)
                self.assertEqual([], adapter.calls)
        transport = Transport(response_body())
        self.adapter(transport).embed(['a' * MAX_TEXT_BYTES])
        self.assertEqual(MAX_TEXT_BYTES, len(json.loads(transport.requests[0][0].data)['input'][0]))

    def test_rejects_incomplete_or_unsafe_response_without_retry(self):
        bodies = []
        for mutate in (
            lambda b: b.update(model='different-model'),
            lambda b: b.update(data=[]),
            lambda b: b['data'][0].update(index=True),
            lambda b: b['data'][0].update(index=1),
            lambda b: b['data'][0].update(embedding=[1.0]),
            lambda b: b['data'][0].update(embedding=[0.0] * 1536),
            lambda b: b['data'][0]['embedding'].__setitem__(0, True),
            lambda b: b['data'][0]['embedding'].__setitem__(0, float('nan')),
            lambda b: b['data'][0]['embedding'].__setitem__(0, float('inf')),
            lambda b: b['data'][0]['embedding'].__setitem__(0, 10 ** 400),
            lambda b: b.update(usage={'total_tokens': -1}),
        ):
            body = response_body()
            mutate(body)
            bodies.append(body)
        bodies.extend([b'{broken-json', b'x' * 2_000_001])
        for body in bodies:
            with self.subTest(shape=type(body).__name__):
                transport = Transport(body)
                adapter = self.adapter(transport)
                with self.assertRaises(ProviderError):
                    adapter.embed([TEXT])
                self.assertEqual(1, len(transport.requests))
                self.assertEqual('failed', adapter.calls[-1]['status'])
                self.assertNotIn(TEXT, json.dumps(adapter.calls))
        duplicate = response_body(2)
        duplicate['data'][1]['index'] = 0
        with self.assertRaises(ProviderError):
            self.adapter(Transport(duplicate)).embed(['first', 'second'])

    def test_only_transient_failures_retry_once_and_errors_never_echo_bodies(self):
        for failure in (TimeoutError(TEXT + KEY), error.URLError(TimeoutError(TEXT)),
                        http_error(429), http_error(500), http_error(503)):
            transport = Transport(failure, response_body())
            adapter = self.adapter(transport)
            self.assertEqual([vector()], adapter.embed([TEXT]))
            self.assertEqual(2, len(transport.requests))
            self.assertEqual(['failed', 'completed'], [c['status'] for c in adapter.calls])
        for failure in (http_error(400), http_error(401), http_error(403), http_error(302),
                        error.URLError(TEXT + KEY), RuntimeError(TEXT + KEY)):
            transport = Transport(failure)
            adapter = self.adapter(transport)
            with self.assertRaises(ProviderError) as caught:
                adapter.embed([TEXT])
            self.assertEqual(1, len(transport.requests))
            output = str(caught.exception) + json.dumps(adapter.calls)
            self.assertNotIn(KEY, output)
            self.assertNotIn(TEXT, output)
        transport = Transport(http_error(429), http_error(503), response_body())
        adapter = self.adapter(transport)
        with self.assertRaises(ProviderError):
            adapter.embed([TEXT])
        self.assertEqual(2, len(transport.requests))


class FakeEmbeddings:
    model = EMBEDDING_MODEL
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, fail_call=None, before_call=None):
        self.batches = []
        self.calls = []
        self.fail_call = fail_call
        self.before_call = before_call

    def embed(self, texts):
        if self.before_call:
            self.before_call()
        self.batches.append(list(texts))
        self.calls.append({'status': 'started', 'input_count': len(texts)})
        if len(self.batches) == self.fail_call:
            self.calls[-1]['status'] = 'failed'
            raise ProviderError('OPENAI_TIMEOUT', 'Simulated timeout')
        self.calls[-1]['status'] = 'completed'
        return [vector() for _ in texts]


class ConfirmedEvidenceIndexTests(unittest.TestCase):
    def setUp(self):
        self.settings = database_for_test(self)
        principals = {**copy.deepcopy(PRINCIPALS),
                      'indexer': {'kind': 'system', 'roles': ['read', 'index']}}
        self.service = Service(self.settings, principals=principals)
        self.addCleanup(self.service.close)
        self.nodes = json.loads(Path('data/catalog.json').read_text())['nodes'] + fixture_nodes()[0]
        self.assertTrue(self.service.bootstrap('developer', self.nodes, request_key='seed')['ok'])
        self.index = EvidenceIndex(self.service, 'indexer', model=EMBEDDING_MODEL,
                                   dimensions=EMBEDDING_DIMENSIONS)
        ids = sorted(n['id'] for n in self.nodes if n['type'] == 'Evidence')
        self.documents = self.index.documents(ids, version=1)['data']['documents']
        self.small = [d for d in self.documents if len(d['text'].encode()) <= MAX_TEXT_BYTES]

    def test_approved_evidence_cache_replays_without_api_and_preserves_graph_and_sources(self):
        ids = ['evidence:olimex-L-wroom:row-55']
        baseline = canonical(self.service.lookup('reader', version=1)['data'])
        services = []

        def factory(*args, **kwargs):
            service = Service(*args, **kwargs)
            services.append(service)
            return service

        def outside_transaction():
            self.assertTrue(services)
            self.assertFalse(in_transaction(services[-1].db))

        embedder = FakeEmbeddings(before_call=outside_transaction)
        with patch('examples.index_evidence.Service', factory):
            result = index_confirmed_evidence(self.settings, embedder, version=1, evidence_ids=ids)
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(ids, result['indexed_ids'])
        self.assertFalse(result['snapshot_complete'])
        stored = self.service.db.execute('SELECT * FROM evidence_vectors').fetchone()
        retry = FakeEmbeddings()
        repeated = index_confirmed_evidence(self.settings, retry, version=1, evidence_ids=ids)
        self.assertEqual([], retry.batches)
        self.assertEqual(ids, repeated['already_indexed_ids'])
        self.assertEqual(stored, self.service.db.execute('SELECT * FROM evidence_vectors').fetchone())
        candidate = self.index.search(vector(), version=1, origin='real')['data']['candidates'][0]
        self.assertEqual(ids, candidate['evidence'])
        self.assertEqual('real', candidate['origin'])
        self.assertTrue(candidate['source']['sha256'])
        self.assertTrue(candidate['locator'])
        self.assertEqual(1, self.service._version())
        self.assertEqual(baseline, canonical(self.service.lookup('reader', version=1)['data']))
        self.assertEqual([], self.index.search(vector(), version=0)['data']['candidates'])
        with self.assertRaises(DomainError) as caught:
            index_confirmed_evidence(self.settings, retry, version=1, evidence_ids=['evidence:unapproved'])
        self.assertEqual('EVIDENCE_NOT_FOUND', caught.exception.code)
        self.assertNotIn('DATABASE_URL', json.dumps(result))
        self.assertNotIn('ESP32-POE', json.dumps(result))

        # Completing the full snapshot must remain visible during a later subset
        # run, without re-embedding. Selection completion is a separate claim.
        complete = index_confirmed_evidence(self.settings, FakeEmbeddings(), version=1)
        self.assertTrue(complete['selection_complete'], complete)
        self.assertTrue(complete['snapshot_complete'], complete)
        subset_retry = FakeEmbeddings()
        subset = index_confirmed_evidence(self.settings, subset_retry, version=1, evidence_ids=ids)
        self.assertTrue(subset['selection_complete'], subset)
        self.assertTrue(subset['snapshot_complete'], subset)
        self.assertEqual([], subset_retry.batches)
        other_id = next(d['evidence_id'] for d in self.documents if d['evidence_id'] not in ids)
        self.service.db.execute('UPDATE evidence_vectors SET content_hash=%s WHERE evidence_id=%s',
                                ('invalid-unselected-hash', other_id))
        invalid_snapshot = index_confirmed_evidence(self.settings, subset_retry, version=1, evidence_ids=ids)
        self.assertTrue(invalid_snapshot['selection_complete'], invalid_snapshot)
        self.assertFalse(invalid_snapshot['snapshot_complete'], invalid_snapshot)
        self.assertEqual([], subset_retry.batches)

    def test_partial_transport_failure_resumes_only_missing_documents(self):
        self.assertGreaterEqual(len(self.small), 33)
        ids = [d['evidence_id'] for d in self.small[:33]]
        first = FakeEmbeddings(fail_call=2)
        result = index_confirmed_evidence(self.settings, first, version=1, evidence_ids=ids)
        self.assertEqual('partial', result['status'], result)
        self.assertEqual((32, 1), (result['indexed_count'], result['unindexed_count']))
        self.assertEqual('OPENAI_TIMEOUT', result['unindexed'][0]['code'])
        first_rows = self.service.db.execute('SELECT * FROM evidence_vectors ORDER BY evidence_id').fetchall()
        second = FakeEmbeddings()
        resumed = index_confirmed_evidence(self.settings, second, version=1, evidence_ids=ids)
        self.assertEqual('completed', resumed['status'], resumed)
        self.assertEqual((1, 32), (resumed['indexed_count'], resumed['already_indexed_count']))
        self.assertEqual([1], [len(batch) for batch in second.batches])
        all_rows = {r['evidence_id']: r for r in self.service.db.execute('SELECT * FROM evidence_vectors')}
        self.assertEqual(first_rows, [all_rows[r['evidence_id']] for r in first_rows])
        self.assertEqual(1, self.service._version())

    def test_oversized_document_and_cache_conflict_stay_explicit_without_overwrite(self):
        # Keep the normal snapshot fully indexable. This test alone adds a large
        # test fixture through approval, so its incomplete coverage is explicit.
        oversized = 'evidence:test:oversized'
        node = {'id': oversized, 'type': 'Evidence', 'origin': 'synthetic', 'evidence': [],
                'source_id': 'source:demo:operations', 'locator': '/test-only-boundary-fixture',
                'description': 'Test-only oversized input boundary: ' + 'x' * MAX_TEXT_BYTES}
        prepared = self.service.prepare_draft('author', [{'op': 'put', 'node': node}], request_key='oversized')
        self.assertTrue(prepared['ok'], prepared)
        draft_id = prepared['data']['draft_id']
        self.assertTrue(self.service.review('reviewer', draft_id, 1, decision='approve',
                                           note='Test input-size boundary fixture', request_key='approve-oversized')['ok'])
        self.assertTrue(self.service.apply('reviewer', draft_id, 1, request_key='apply-oversized')['ok'])
        oversized_document = self.index.documents([oversized], version=2)['data']['documents'][0]
        self.assertGreater(len(oversized_document['text'].encode()), MAX_TEXT_BYTES)
        small = self.small[0]
        original = {'evidence_id': small['evidence_id'], 'content_hash': small['content_hash'], 'embedding': vector()}
        self.assertTrue(self.index.index([original], version=2, request_key='seed-vector')['ok'])
        # A corrupt/stale cache must be reported, never silently repaired by an LLM call.
        self.service.db.execute('UPDATE evidence_vectors SET content_hash=%s WHERE evidence_id=%s',
                                ('wrong-content-hash', small['evidence_id']))
        before = self.service.db.execute('SELECT * FROM evidence_vectors').fetchone()
        embedder = FakeEmbeddings()
        result = index_confirmed_evidence(self.settings, embedder, version=2,
                                         evidence_ids=[oversized, small['evidence_id']])
        self.assertEqual('partial', result['status'], result)
        self.assertEqual({'EMBEDDING_INPUT_TOO_LARGE', 'INDEX_CONFLICT'}, {i['code'] for i in result['unindexed']})
        self.assertEqual([], embedder.batches)
        self.assertEqual(before, self.service.db.execute('SELECT * FROM evidence_vectors').fetchone())
        self.assertFalse(result['snapshot_complete'])
        self.assertFalse(result['selection_complete'])
        self.assertEqual(2, self.service._version())
