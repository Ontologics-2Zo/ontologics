"""Real PostgreSQL tests: cross-connection contention and versioned pgvector."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import unittest

from ontologics.database import DatabaseError, DatabaseSettings, in_transaction
from ontologics.demo import fixture_nodes
from ontologics.retrieval import EvidenceIndex
from ontologics.service import Service, AgentGateway
from tests.db import database_for_test
from tests.test_service import PRINCIPALS, LEFT


class PostgreSQLTests(unittest.TestCase):
    def setUp(self):
        self.settings = database_for_test(self)
        self.principals = {**copy.deepcopy(PRINCIPALS), 'indexer': {'kind':'system','roles':['read','index']}}
        self.service = Service(self.settings, principals=self.principals)
        self.addCleanup(self.service.close)
        self.nodes = json.loads(Path('data/catalog.json').read_text())['nodes'] + fixture_nodes()[0]
        self.assertTrue(self.service.bootstrap('developer', self.nodes, request_key='seed')['ok'])
        self.node = copy.deepcopy(next(n for n in self.nodes if n['id']==LEFT))
        self.node['name'] += ' (reviewed update)'

    def parallel(self, call):
        barrier = threading.Barrier(2)
        def worker(i):
            svc = Service(self.settings, principals=self.principals)
            try:
                barrier.wait(timeout=10)
                return call(svc,i)
            finally: svc.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker,range(2)))

    def test_concurrent_same_request_key_creates_one_draft_and_one_apply(self):
        results = self.parallel(lambda s,i:s.prepare_draft('author',[{'op':'put','node':self.node}],request_key='same'))
        self.assertTrue(all(r['ok'] for r in results),results)
        self.assertEqual(results[0]['data']['draft_id'],results[1]['data']['draft_id'])
        self.assertEqual(1,sum(any(w['code']=='IDEMPOTENT_REPLAY' for w in r['warnings']) for r in results))
        did=results[0]['data']['draft_id']
        self.assertTrue(self.service.review('reviewer',did,1,decision='approve',note='test',request_key='review')['ok'])
        applied=self.parallel(lambda s,i:s.apply('reviewer',did,1,request_key='same-apply'))
        self.assertTrue(all(r['ok'] for r in applied),applied)
        self.assertEqual([2,2],[r['data_version'] for r in applied])
        self.assertEqual(3,self.service.db.execute('SELECT count(*) FROM snapshots').fetchone()['count'])
        self.assertEqual(1,self.service.db.execute('SELECT count(*) FROM drafts').fetchone()['count'])

    def test_two_approved_drafts_from_one_base_cannot_both_apply(self):
        ids=[]
        for i in range(2):
            node=copy.deepcopy(self.node);node['name']+=str(i)
            d=self.service.prepare_draft('author',[{'op':'put','node':node}],request_key='draft'+str(i))['data']
            ids.append(d['draft_id'])
            self.assertTrue(self.service.review('reviewer',ids[-1],1,decision='approve',note='test',request_key='r'+str(i))['ok'])
        results=self.parallel(lambda s,i:s.apply('reviewer',ids[i],1,request_key='apply'+str(i)))
        self.assertEqual(1,sum(r['ok'] for r in results))
        self.assertEqual(['STALE_BASE'],[r['errors'][0]['code'] for r in results if not r['ok']])
        self.assertEqual(2,self.service._version())

    def test_closed_connection_returns_safe_unrecorded_failure(self):
        self.service.close()
        result=self.service.lookup('reader')
        self.assertFalse(result['ok'])
        self.assertEqual('STORAGE_ERROR',result['errors'][0]['code'])
        self.assertEqual('AUDIT_NOT_RECORDED',result['warnings'][0]['code'])
        self.assertNotIn(self.settings.url,json.dumps(result))

    def test_caller_transaction_is_rejected_without_commit_or_rollback(self):
        self.service.db.execute('CREATE TEMP TABLE caller_work(value INTEGER)')
        self.service.db.execute('BEGIN')
        self.service.db.execute('INSERT INTO caller_work VALUES (1)')
        response=self.service.lookup('reader')
        self.assertEqual('TRANSACTION_BOUNDARY',response['errors'][0]['code'])
        self.assertTrue(in_transaction(self.service.db))
        self.assertEqual(1,self.service.db.execute('SELECT count(*) FROM caller_work').fetchone()['count'])
        self.service.db.execute('ROLLBACK')
        self.assertEqual(0,self.service.db.execute('SELECT count(*) FROM caller_work').fetchone()['count'])
        self.assertTrue(self.service.lookup('reader')['ok'])

    def test_rejects_file_memory_and_unsafe_schema(self):
        for uri in (':memory:','test.sqlite','sqlite:///tmp/test.db'):
            with self.assertRaises(DatabaseError): DatabaseSettings(uri)
        with self.assertRaises(DatabaseError): DatabaseSettings(self.settings.url,'public')
        with self.assertRaises(DatabaseError): DatabaseSettings(self.settings.url,'x;DROP SCHEMA public')

    def test_vector_search_keeps_evidence_origin_version_model_and_graph_unchanged(self):
        idx=EvidenceIndex(self.service,'indexer',model='test-vectors-not-semantic',dimensions=3)
        ids=['evidence:olimex-L-wroom:row-55','evidence:demo:initial']
        docs=idx.documents(ids,version=1)['data']['documents']
        entries=[{'evidence_id':d['evidence_id'],'content_hash':d['content_hash'],'embedding':v}
                 for d,v in zip(docs,([1,0,0],[0,1,0]))]
        indexed=idx.index(entries,version=1,request_key='vectors');self.assertTrue(indexed['ok'],indexed)
        self.assertEqual(1,indexed['data_version'])
        result=idx.search([1,0,0],version=1)
        self.assertTrue(result['ok'],result)
        self.assertEqual(ids[0],result['data']['candidates'][0]['evidence'][0])
        self.assertEqual('real',result['data']['candidates'][0]['origin'])
        self.assertTrue(result['data']['candidates'][0]['source']['sha256'])
        self.assertTrue(result['data']['candidate_only'])
        filtered=idx.search([1,0,0],origin='synthetic')['data']['candidates']
        self.assertEqual([ids[1]],[c['evidence'][0] for c in filtered])
        other=EvidenceIndex(self.service,'reader',model='other-model',dimensions=3)
        self.assertEqual([],other.search([1,0,0])['data']['candidates'])
        self.assertEqual([],idx.search([1,0,0],version=0)['data']['candidates'])
        agent=EvidenceIndex(self.service,'agent',model='test-vectors-not-semantic',dimensions=3)
        self.assertEqual('FORBIDDEN',agent.index(entries,version=1,request_key='forbidden')['errors'][0]['code'])
        self.assertEqual('TOOL_NOT_ALLOWED',AgentGateway(self.service,'agent').call('index_evidence',{})['errors'][0]['code'])
        self.assertEqual(1,self.service._version())
        # Exact source content/finite vector guards and immutable cache.
        bad=copy.deepcopy(entries);bad[0]['content_hash']='invented'
        self.assertEqual('EVIDENCE_CONTENT_MISMATCH',idx.index(bad,version=1,request_key='bad')['errors'][0]['code'])
        for vector in ([0,0,0],[1,0],[float('nan'),0,1],[True,0,1],[10**400,0,1]):
            self.assertFalse(idx.search(vector)['ok'])
        bad=copy.deepcopy(entries);bad[0]['embedding']=[0,0,1]
        self.assertEqual('INDEX_CONFLICT',idx.index(bad,version=1,request_key='change')['errors'][0]['code'])
        reopened=Service(self.settings,principals=self.principals)
        try:
            persisted=EvidenceIndex(reopened,'reader',model=idx.model,dimensions=3).search([1,0,0])
            self.assertEqual(ids[0],persisted['data']['candidates'][0]['evidence'][0])
        finally:reopened.close()
