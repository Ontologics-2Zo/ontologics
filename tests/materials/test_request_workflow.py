import unittest
from ontologics.materials.request_workflow import rfq_document, select_latest

class RequestRulesTests(unittest.TestCase):
    def test_revision_replaces_not_sums(self):
        rows=[dict(evidence_id='e1',request=dict(request_id='R',revision=1,quantity=30)),dict(evidence_id='e2',request=dict(request_id='R',revision=2,quantity=40))]
        self.assertEqual([40],[r['request']['quantity'] for r in select_latest(rows)])
    def test_duplicate_revision_is_blocked(self):
        rows=[dict(evidence_id=e,request=dict(request_id='R',revision=1,quantity=30)) for e in ['a','b']]
        self.assertTrue(all(r['issues'] for r in select_latest(rows)))
    def test_rfq_only_known_shortages_preserves_units(self):
        req=dict(request_id='R',revision=1,quantity=30,configuration_id='C',need_date='2026-11-20',need_basis='production_complete',origin='synthetic',conditions_note='기존 동일')
        bom=dict(components=[dict(component_id='x',name='부품',D=30,A=20,S=10),dict(component_id='y',D=30,A=None,S=None)],excluded_bom=[])
        process=[dict(material_id='flux',name='플럭스',required='15.5',available='13',shortage='2.5',unit='g',basis='대당 기준'),dict(material_id='ipa',name='IPA',required=None,available='10',shortage=None,unit='mL')]
        doc=rfq_document(req,bom,process)
        self.assertEqual(2,len(doc['lines']));self.assertEqual(2,len(doc['excluded']))
        self.assertIsNone(doc['desired_arrival_date']);self.assertFalse(doc['sent'])
        self.assertIn('2.5 g',doc['text']);self.assertIn('생산 완료',doc['text'])

import os,json,tempfile
from pathlib import Path
from unittest.mock import patch
from ontologics.model import ROOT
from ontologics.service import Service,DomainError
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext,DevelopmentIdentityProvider
from ontologics.web import USERS
from ontologics.order_fulfillment.material_request_registration import prepare_request
from ontologics.materials.contract_library_web import LibraryApplication

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL required')
class RequestWorkflowDatabaseTests(unittest.TestCase):
    def ok(self,r):self.assertTrue(r['ok'],r);return r['data']
    def setUp(self):
        from tests.db import database_for_test
        self.settings=database_for_test(self);self.context=DevelopmentContext('dev-alice')
        self.host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings)
        self.app=LibraryApplication(self.host,self.context)
        self.svc=Service(self.settings,principals={'seed':{'kind':'human','roles':['seed','read']}});self.addCleanup(self.svc.close)
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']+json.loads((ROOT/'data/materials/graph_nodes_v1.json').read_text())['nodes']
        self.ok(self.svc.bootstrap('seed',nodes,request_key='seed'))
        from examples.order_fulfillment.b_material_request_registration import request_cases
        self.payload=dict(request_cases()[0],request_id='MAT-TEST-001',quantity=200,
            baseline_evidence_id='evidence:planning:contract:v1:item-001',contract_item_id='contract-item:planning:001',
            reason='Reference Customer 기존 계약의 추가 설치 물량 200대. 기존 요청과 중복 없음.',
            conditions_note='기존 계약 contract:planning:001의 제품 구성과 대체품 조건을 유지합니다.')
        root=ROOT/'data/receipts/materials';root.mkdir(parents=True,exist_ok=True)
        tmp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(tmp.cleanup)
        override=patch('ontologics.order_fulfillment.material_request_registration.STORAGE_ROOT',Path(tmp.name));override.start();self.addCleanup(override.stop)
    def register(self,p=None,key='request'):
        with self.host.request(self.context) as s:r=prepare_request(s,p or self.payload,key)
        draft=r['draft']['data']
        with self.host.request(DevelopmentContext('dev-bob')) as s:
            self.ok(s.review(draft['draft_id'],1,decision='approve',note='격리 개발 요청 검토',request_key='approve-'+key))
        self.ok(self.host.apply_approved(draft['draft_id'],1,request_key='apply-'+key));return r['evidence_id']
    def inbox(self):return self.ok(self.app.requests.inbox(self.context))
    def review(self,e,key='review'):
        return self.app.requests.review(self.context,e,self.inbox()['data_version'],'10',key)
    def seed_stock(self):
        from ontologics.materials.library_stock import company_stock_scenario
        with self.host.request(self.context) as s:
            detail=self.ok(s.call('configuration',{'configuration_id':self.payload['configuration_id']}))
            stock=company_stock_scenario(detail,s._service.now())
        self.ok(self.app.materials.stock.store(self.context,self.payload['configuration_id'],stock,'stock'))
        self.ok(self.app.process_materials.seed_scenario(self.context,'process-stock'))
    def test_registration_discovery_seen_review_rfq_without_reservation(self):
        self.assertEqual([],self.inbox()['requests']);e=self.register();self.seed_stock()
        row=self.inbox()['requests'][0];self.assertEqual('new',row['status']);self.assertEqual([],row['issues'])
        self.ok(self.app.requests.seen(self.context,e));self.assertEqual('seen',self.inbox()['requests'][0]['status'])
        saved=self.ok(self.review(e));p=saved['payload']
        self.assertEqual(200,p['bom']['planned_input_quantity']);self.assertTrue(p['rfq']['lines'])
        self.assertTrue(any(r['kind']=='process' for r in p['rfq']['lines']))
        self.assertTrue(any(r['kind']=='bom' for r in p['rfq']['lines']))
        self.assertEqual(saved['id'],self.ok(self.review(e))['id'])
        self.assertEqual('reviewed',self.inbox()['requests'][0]['status'])
        with self.host.request(self.context) as s:
            self.assertEqual(0,s._service.db.execute('SELECT count(*) AS n FROM pp_material_reservations').fetchone()['n'])
        self.assertEqual(saved['id'],self.inbox()['requests'][0]['last_review']['id'])
    def test_revision_and_stale_screen(self):
        e=self.register();v=self.inbox()['data_version']
        second=self.register(dict(self.payload,revision=2,quantity=40,relation='revision',previous_evidence_id=e),'rev2')
        rows=self.inbox()['requests'];self.assertEqual([40],[r['request']['quantity'] for r in rows])
        with self.assertRaises(DomainError):self.app.requests.review(self.context,e,v,'10','old')
        saved=self.ok(self.review(second));self.assertEqual(40,saved['payload']['request']['quantity'])
        self.assertTrue(saved['payload']['rfq']['excluded'])
    def test_missing_contract_does_not_guess(self):
        p=dict(self.payload);p.pop('contract_item_id');e=self.register(p)
        self.assertEqual('needs_info',self.inbox()['requests'][0]['status'])
        with self.assertRaises(DomainError):self.review(e)
    def test_read_only_cannot_create_draft(self):
        from ontologics.identity import UserIdentity
        e=self.register()
        reader=CommonHost(DevelopmentIdentityProvider([UserIdentity('reader',frozenset({'read'}))],enabled=True),self.settings)
        app=LibraryApplication(reader,DevelopmentContext('reader'))
        result=app.requests.review(app.context,e,self.inbox()['data_version'],'10','denied')
        self.assertFalse(result['ok']);self.assertEqual('FORBIDDEN',result['errors'][0]['code'])
    def test_http_csrf_and_draft_download_content(self):
        import threading
        from urllib.request import build_opener,HTTPCookieProcessor,Request
        from urllib.error import HTTPError
        from http.cookiejar import CookieJar
        from ontologics.materials.contract_library_web import LibraryServer
        e=self.register();self.seed_stock()
        server=LibraryServer(self.app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():server.shutdown();server.server_close();thread.join()
        self.addCleanup(close)
        client=build_opener(HTTPCookieProcessor(CookieJar()))
        token=json.load(client.open(server.origin+'/api/context'))['data']['csrf_token']
        def post(route,body,csrf=token):
            return json.load(client.open(Request(server.origin+'/api/tools/contracts/'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':server.origin,'X-CSRF-Token':csrf})))
        with self.assertRaises(HTTPError):post('request-inbox',{},'bad')
        inbox=self.ok(post('request-inbox',{}));self.assertEqual(1,len(inbox['requests']))
        saved=self.ok(post('request-review',dict(evidence_id=e,data_version=inbox['data_version'],allowance_percent='10',request_key='http')))
        self.assertIn('미발송 초안',saved['payload']['rfq']['text'])
        self.assertIn('showRequestInbox',client.open(server.origin+'/request-inbox.js').read().decode())

    @unittest.skipUnless(os.environ.get('TEST_NEO4J_URL'),'Local Neo4j required')
    def test_neo4j_discovery_and_rfq_uses_existing_graph_tools(self):
        from dataclasses import replace
        from uuid import uuid4
        from ontologics.graph_views import GraphSettings
        from ontologics.materials.request_workflow import RequestWorkflow
        e=self.register();self.seed_stock()
        settings=replace(GraphSettings.from_environment(backend='neo4j'),namespace='test_request_a_'+uuid4().hex)
        actor='system:projector';projector=Service(self.settings,principals={actor:{'kind':'system','roles':['read','index']}},graph_settings=settings)
        self.addCleanup(projector.close)
        namespace=projector.graph_views.namespace
        def clean():
            with projector.graph_views.projection()._driver.session(database=settings.database) as s:
                s.run('MATCH (n) WHERE n.namespace=$namespace DETACH DELETE n',namespace=namespace).consume()
        self.addCleanup(clean)
        # Both the request's reference snapshot and current snapshot are required.
        for v in (1,2):self.ok(projector.graph_views.synchronize(actor,v,request_key='sync-'+str(v)))
        graph=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings,graph_settings=settings)
        workflow=RequestWorkflow(self.app.materials,self.app.process_rfq,self.context,graph)
        listing=self.ok(workflow.inbox(self.context));self.assertEqual(e,listing['requests'][0]['evidence_id'])
        saved=self.ok(workflow.review(self.context,e,listing['data_version'],'10','graph-review'))
        self.assertTrue(saved['payload']['rfq']['lines'])
    def test_other_company_stock_cannot_cover_request(self):
        e=self.register();self.seed_stock()
        from ontologics.model import canonical,digest
        with self.host.request(self.context) as s:
            row=self.app.process_materials.latest(s._service);p=row['payload'];p['company']='Another Company'
            s._service.db.execute('UPDATE pp_process_material_stock SET payload=%s,content_hash=%s WHERE id=%s',(canonical(p),digest(p),row['id']))
        result=self.review(e);self.assertFalse(result['ok']);self.assertEqual('STOCK_COMPANY_MISMATCH',result['errors'][0]['code'])
    def test_invalid_latest_revision_does_not_resurrect_old_request(self):
        e=self.register();second=self.register(dict(self.payload,revision=2,quantity=40,relation='revision',previous_evidence_id=e),'rev2')
        from ontologics.order_fulfillment.material_request_document import validate_request
        def validate(p,nodes):
            if p['revision']==2:raise ValueError('검증 실패 개정')
            return validate_request(p,nodes)
        with patch('ontologics.materials.request_workflow.validate_request',validate):
            rows=self.inbox()['requests'];self.assertEqual([second],[r['evidence_id'] for r in rows])
            self.assertEqual('needs_info',rows[0]['status'])
            with self.assertRaises(DomainError):self.review(second)
    def test_fresh_stock_changes_draft_keeps_saved_review(self):
        e=self.register();self.seed_stock();first=self.ok(self.review(e))
        from ontologics.model import digest
        with self.host.request(self.context) as s:
            payload=self.app.materials.stock.latest(s._service,self.payload['configuration_id'])['payload']
        import copy
        payload=copy.deepcopy(payload)
        for doc in payload['snapshots']:
            for row in doc['stocks']:row['on_hand_quantity']+=1000
            doc['sha256']=digest({k:v for k,v in doc.items() if k!='sha256'})
        self.ok(self.app.materials.stock.store(self.context,self.payload['configuration_id'],payload,'stock-changed'))
        second=self.ok(self.review(e,'review-after-change'))
        self.assertNotEqual(first['id'],second['id'])
        self.assertGreater(len([r for r in first['payload']['rfq']['lines'] if r['kind']=='bom']),len([r for r in second['payload']['rfq']['lines'] if r['kind']=='bom']))
    def test_separate_source_host_with_a_cost_policy(self):
        from ontologics.paid_budget import cost_policy_from_environment
        policy=cost_policy_from_environment(json.loads((ROOT/'ontologics/order_fulfillment/request_entry_cost.example.json').read_text()))
        paid=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings,cost_policy=policy)
        app=LibraryApplication(paid,self.context,request_host=self.host)
        e=self.register();self.seed_stock()
        result=app.requests.review(self.context,e,self.inbox()['data_version'],'10','paid-host-review')
        self.assertTrue(result['ok'],result)
    def test_original_contract_chain_and_verified_text(self):
        self.register();c=self.inbox()['requests'][0]['contract_context']
        self.assertEqual('contract:planning:001',c['contract']['id'])
        self.assertEqual(100,c['item']['quantity'])
        self.assertEqual(1,len(c['documents']))
        self.assertEqual('verified',c['documents'][0]['status'])
        self.assertIn('주문 수량: 보드 100대',c['documents'][0]['text'])
        from ontologics.materials.request_workflow import contract_context
        nodes={x['id']:x for x in c['evidence']+[d['source'] for d in c['documents']]}
        nodes[c['documents'][0]['source']['id']]['sha256']='0'*64
        bad=contract_context(c['contract'],c['item'],nodes)
        self.assertIsNone(bad['documents'][0]['text'])
        self.assertEqual('hash_mismatch',bad['documents'][0]['status'])
