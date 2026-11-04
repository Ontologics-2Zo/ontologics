"""Real common PostgreSQL approval path; synthetic requests, development people."""
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from examples.order_fulfillment.b_material_request_registration import ACTORS, fixture_nodes, request_cases
from ontologics.graph_views import GraphSettings
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.model import ROOT
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.material_request_registration import prepare_request, read_request
from ontologics.service import Service, DomainError
from tests.db import database_for_test


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.database=database_for_test(self)
        self.host=CommonHost(DevelopmentIdentityProvider(ACTORS,enabled=True),self.database,graph_settings=GraphSettings())
        self.service=Service(self.database,principals={'seed':{'kind':'human','roles':['read','seed']}})
        self.addCleanup(self.service.close)
        self.assertTrue(self.service.bootstrap('seed',fixture_nodes(),request_key='seed')['ok'])
        root=ROOT/'data/receipts/order_fulfillment';root.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(dir=root);self.addCleanup(temporary.cleanup)
        self.storage=Path(temporary.name)
        override=patch('ontologics.order_fulfillment.material_request_registration.STORAGE_ROOT',self.storage)
        override.start();self.addCleanup(override.stop)
        self.payload=request_cases()[0]

    def session(self,index=0): return self.host.request(DevelopmentContext(ACTORS[index].subject))

    def prepare(self,payload=None,key='request'):
        with self.session() as s: return prepare_request(s,payload or self.payload,key)

    def approve(self,created):
        draft=created['draft']['data']
        with self.session(1) as s:
            result=s.review(draft['draft_id'],1,decision='approve',note='개발 검토',request_key='review-'+draft['draft_id'])
            self.assertTrue(result['ok'],result)
        result=self.host.apply_approved(draft['draft_id'],1,request_key='apply-'+draft['draft_id'])
        self.assertTrue(result['ok'],result)
        return result['data_version']

    def test_real_registration_approval_readback_and_replay(self):
        created=self.prepare();self.assertEqual('awaiting_review',created['draft']['data']['status'])
        with self.session(2) as s:
            with self.assertRaises(DomainError): read_request(s,created['evidence_id'],version=1)
        repeated=self.prepare()
        self.assertEqual(created['draft']['data']['draft_id'],repeated['draft']['data']['draft_id'])
        version=self.approve(created)
        with self.session(2) as s:
            result=read_request(s,created['evidence_id'],version=version)
            self.assertEqual(self.payload,result['record']['request'])
            self.assertEqual(ACTORS[0].actor,result['record']['provenance']['actor'])
            self.assertFalse(result['a_received'])
        self.assertEqual(created['draft']['data']['draft_id'],self.prepare()['draft']['data']['draft_id'])
        with self.assertRaises(ValueError): self.prepare(key='other-key')

    def test_revision_and_independent_requests_do_not_sum_revisions(self):
        registered=[]
        for index,payload in enumerate(request_cases()):
            created=self.prepare(payload,'r'+str(index));version=self.approve(created)
            with self.session(2) as s: registered.append(read_request(s,created['evidence_id'],version=version))
        self.assertEqual([30,40,10],[r['record']['request']['quantity'] for r in registered])
        self.assertEqual(registered[0]['evidence']['id'],registered[1]['record']['request']['previous_evidence_id'])
        self.assertEqual(registered[1]['evidence']['id'],registered[2]['record']['request']['related_evidence_id'])
        stale=dict(request_cases()[1],quantity=50)
        with self.assertRaises(ValueError): self.prepare(stale,'stale')

    def test_same_key_changed_payload_and_reader_write_rejected(self):
        self.prepare()
        with self.assertRaises(DomainError) as e: self.prepare(dict(self.payload,quantity=40))
        self.assertEqual('REQUEST_KEY_REUSED',e.exception.code)
        with self.session(2) as s:
            with self.assertRaises(DomainError) as e: prepare_request(s,self.payload,'reader')
        self.assertEqual('FORBIDDEN',e.exception.code)
        self.assertEqual(1,len(list(self.storage.glob('*.md'))))

    def test_self_approval_and_apply_without_approval_rejected(self):
        created=self.prepare();draft=created['draft']['data']
        with self.session() as s:
            denied=s.review(draft['draft_id'],1,decision='approve',note='검토',request_key='self')
        self.assertEqual('SELF_APPROVAL_FORBIDDEN',denied['errors'][0]['code'])
        denied=self.host.apply_approved(draft['draft_id'],1,request_key='unapproved')
        self.assertEqual('APPROVAL_REQUIRED',denied['errors'][0]['code'])

    def test_source_tamper_blocks_common_approval(self):
        created=self.prepare();draft=created['draft']['data']
        path=ROOT/created['source_path'];path.write_text(path.read_text()+'변조')
        with self.session(1) as s:
            denied=s.review(draft['draft_id'],1,decision='approve',note='검토',request_key='tampered')
        self.assertFalse(denied['ok'])
        with self.assertRaises(ValueError): self.prepare()

    def test_stale_draft_is_not_approved(self):
        first=self.prepare()
        second=self.prepare(dict(self.payload,request_id='MREQ-OTHER'),'second')
        self.approve(second)
        with self.session(1) as s:
            denied=s.review(first['draft']['data']['draft_id'],1,decision='approve',note='검토',request_key='stale')
        self.assertEqual('STALE_BASE',denied['errors'][0]['code'])
