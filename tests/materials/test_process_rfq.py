import copy
import unittest
from ontologics.materials.library_process_materials import load_reference,scenario,inspect_materials
from ontologics.materials.library_process_rfq import calculate_review
NOW='2026-11-02T09:00:00+00:00'
CID='config:esp32-poe:L:WROOM'
MID='process-material:kester:951'
class ReviewRulesTests(unittest.TestCase):
    def setUp(self):
        ref=load_reference();self.data=inspect_materials(ref,scenario(ref,NOW),CID,100,NOW)
    def test_confirmed_total_shortage_decimal_and_unknown(self):
        r=calculate_review(self.data,[dict(material_id=MID,required='20.5',unit='g',basis='작업표준 WP-1: 총 투입량 20.5g')])
        flux=next(x for x in r if x['material_id']==MID)
        self.assertEqual('7.5',flux['shortage']);self.assertEqual('13',flux['available'])
        self.assertTrue(all(x['required'] is None for x in r if x['material_id']!=MID))
    def test_empty_input_does_not_use_conditional_estimate(self):
        self.assertTrue(all(x['shortage'] is None for x in calculate_review(self.data,[])))
    def test_missing_stock_does_not_become_zero(self):
        self.data['materials'][2]['stock']['available']=None
        self.assertIsNone(calculate_review(self.data,[dict(material_id=MID,required='20',unit='g',basis='작업표준')])[2]['shortage'])
    def test_invalid_input(self):
        row=dict(material_id=MID,required='20',unit='g',basis='작업표준')
        for patch in [dict(required='-1'),dict(required='NaN'),dict(required=True),dict(required='1e-1000000'),dict(unit='mL'),dict(basis=''),dict(material_id='invented')]:
            with self.assertRaises(ValueError):calculate_review(self.data,[dict(row,**patch)])
        with self.assertRaises(ValueError):calculate_review(self.data,[row,row])

import os
from ontologics.service import DomainError
@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL required')
class ProcessRFQDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.materials.test_process_material_stock import ProcessMaterialDatabaseTests
        ProcessMaterialDatabaseTests.setUp(self)
        self.ok(self.app.process_materials.seed_scenario(self.context,'seed'))
    def ok(self,r):self.assertTrue(r['ok'],r);return r['data']
    def review(self,key='review',required='20.5'):
        return self.ok(self.app.dispatch('/api/tools/contracts/process-requirement-review',dict(configuration_id=CID,quantity=100,job_name='시험작업100대',inputs=[dict(material_id=MID,required=required,unit='g',basis='확인된 작업 총량')],reviewed=True,request_key=key)))
    def create(self,review,**changes):
        args=dict(review_id=review['id'],material_ids=[MID],supplier='공급사 검토 대상',desired_date='2026-12-01',date_basis='작업 준비일',destination='자재 창고',note='납기 회신 요청',reviewed=True,request_key='rfq')
        args.update(changes)
        return self.app.dispatch('/api/tools/contracts/process-rfq-create',args)
    def test_persist_reopen_rfq_exact_units_and_idempotency(self):
        r=self.review();draft=self.ok(self.create(r));self.assertIn('7.5 g',draft['payload']['text'])
        self.assertEqual('draft_not_sent',draft['payload']['status']);self.assertEqual(4,len(draft['payload']['unknown_materials']))
        self.assertEqual(draft['id'],self.ok(self.create(r))['id'])
        reopened=self.ok(self.app.dispatch('/api/tools/contracts/process-requirement-get',dict(configuration_id=CID,review_id=r['id'])))
        self.assertTrue(reopened['current']);self.assertEqual(draft['id'],reopened['drafts'][0]['id'])
        with self.host.request(self.context) as s:
            self.assertEqual(0,s._service.db.execute('SELECT count(*) AS n FROM pp_material_reservations').fetchone()['n'])
    def test_multiple_jobs_and_unknown_or_covered_rejected(self):
        a=self.review();b=self.review('review2','10');self.assertNotEqual(a['id'],b['id'])
        self.assertFalse(self.create(b)['ok'])
        self.assertFalse(self.create(a,material_ids=['process-material:kester:np560:7048230910'])['ok'])
        with self.assertRaises(DomainError):self.create(a,material_ids=[MID,MID])
    def test_stale_stock_and_expiry_block_rfq(self):
        r=self.review()
        from ontologics.model import canonical,digest
        with self.host.request(self.context) as s:
            old=self.app.process_materials.latest(s._service);p=copy.deepcopy(old['payload'])
            p['lots'][2]['expires_at']='2020-01-01T00:00:00+00:00'
            s._service.db.execute('UPDATE pp_process_material_stock SET payload=%s,content_hash=%s WHERE id=%s',(canonical(p),digest(p),old['id']))
        self.assertFalse(self.create(r)['ok'])
        d=self.ok(self.app.process_rfq.inspect(self.context,CID,r['id']));self.assertFalse(d['current'])
        fresh=self.review('new');self.assertEqual('20.5',self.ok(self.create(fresh,request_key='new-rfq'))['payload']['lines'][0]['shortage'])
    def test_scope_integrity_and_reader_permissions(self):
        r=self.review()
        self.assertFalse(self.app.process_rfq.inspect(self.context,'config:esp32-poe:L1:WROOM',r['id'])['ok'])
        from ontologics.identity import DevelopmentContext,DevelopmentIdentityProvider,UserIdentity
        from ontologics.host import CommonHost
        from ontologics.materials.library_process_materials import LibraryProcessMaterials
        from ontologics.materials.library_process_rfq import LibraryProcessRFQ
        ctx=DevelopmentContext('reader');host=CommonHost(DevelopmentIdentityProvider([UserIdentity('reader',frozenset({'read'}))],enabled=True),self.settings)
        service=LibraryProcessRFQ(LibraryProcessMaterials(host,ctx),ctx)
        self.assertFalse(service.review(ctx,CID,100,'test',[],True,'denied')['ok'])
        with self.host.request(self.context) as s:
            s._service.db.execute("UPDATE pp_process_requirement_reviews SET content_hash='bad' WHERE id=%s",(r['id'],))
        self.assertFalse(self.create(r)['ok'])
    def test_unconfirmed_and_date_basis_blocked(self):
        with self.assertRaises(DomainError):self.app.process_rfq.review(self.context,CID,100,'test',[],False,'no-confirm')
        with self.assertRaises(DomainError):self.create(self.review(),date_basis='')

    def test_http_csrf_review_to_rfq_and_script_delivery(self):
        import json,threading
        from urllib.request import build_opener,HTTPCookieProcessor,Request
        from urllib.error import HTTPError
        from http.cookiejar import CookieJar
        from ontologics.materials.contract_library_web import LibraryServer
        server=LibraryServer(self.app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        client=build_opener(HTTPCookieProcessor(CookieJar()))
        token=json.load(client.open(server.origin+'/api/context'))['data']['csrf_token']
        def post(route,body,csrf=token):
            req=Request(server.origin+'/api/tools/contracts/'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':server.origin,'X-CSRF-Token':csrf})
            return json.load(client.open(req))
        body=dict(configuration_id=CID,quantity=100,job_name='HTTP workflow',inputs=[dict(material_id=MID,required='20.5',unit='g',basis='work standard'),dict(material_id='process-material:mgchemicals:824',required='800',unit='mL',basis='cleaning work standard')],reviewed=True,request_key='http-review')
        with self.assertRaises(HTTPError):post('process-requirement-review',body,'bad')
        review=self.ok(post('process-requirement-review',body))
        draft=self.ok(post('process-rfq-create',dict(review_id=review['id'],material_ids=[MID,'process-material:mgchemicals:824'],supplier='test supplier',desired_date='',date_basis='',destination='test warehouse',note='',reviewed=True,request_key='http-rfq')))
        self.assertIn('7.5 g',draft['payload']['text']);self.assertIn('100 mL',draft['payload']['text'])
        saved=self.ok(post('process-requirement-get',dict(configuration_id=CID,review_id=review['id'])))
        self.assertEqual(draft['id'],saved['drafts'][0]['id'])
        self.assertIn('renderProcessProcurement',client.open(server.origin+'/process-rfq.js').read().decode())
