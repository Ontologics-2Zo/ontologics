"""Real PostgreSQL contract archive tests, no provider or seeded contract."""
import base64
import hashlib
import os
import unittest
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentIdentityProvider, DevelopmentContext
from ontologics.web import USERS
from tests.db import database_for_test

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Disposable PostgreSQL required')
class ContractLibraryTests(unittest.TestCase):
    def setUp(self):
        from ontologics.production_planning.contract_library import ContractLibrary
        self.settings=database_for_test(self)
        self.host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings)
        self.alice=DevelopmentContext('dev-alice')
        self.library=ContractLibrary(self.host,self.alice)
        self.raw='실제 저장 검증 계약\n주문 137대\n'.encode()
        self.args=dict(title='검색용 계약',counterparty='고객 하나',filename='order.txt',
                       content_base64=base64.b64encode(self.raw).decode(),request_key='save-one')
    def test_db_schedule_proposals_preserve_conditions_and_reject_stale_capacity(self):
        self.schedule_scenario=True
        self.test_interpretation_persists_and_confirmation_binds_exact_result()

    def test_warehouse_registration_and_allocation_are_separate(self):
        self.warehouse_scenario=True
        self.supply_scenario=True
        self.test_interpretation_persists_and_confirmation_binds_exact_result()

    def test_received_materials_preserve_reservation_and_prevent_replay(self):
        self.supply_scenario=True
        self.test_interpretation_persists_and_confirmation_binds_exact_result()

    def test_competing_production_plans_cannot_double_reserve(self):
        self.reservation_competition=True
        self.test_interpretation_persists_and_confirmation_binds_exact_result()

    def check_competing_reservations(self,app,cid):
        """Two separately persisted plans, same stock, concurrent host transactions."""
        from copy import deepcopy
        from uuid import uuid4
        from concurrent.futures import ThreadPoolExecutor
        from ontologics.model import canonical,digest
        second=self.library.register(self.alice,**dict(self.args,content_base64=base64.b64encode(b'second isolated production plan').decode(),request_key='second-plan-original'))['data']['id']
        with self.host.request(self.alice) as session:
            svc=session._service
            def insert(table,row):
                columns=list(row)
                values=[canonical(v) if isinstance(v,(dict,list)) else v for v in row.values()]
                svc.db.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join(['%s']*len(columns))+')',values)
            interpretation=dict(svc.db.execute('SELECT * FROM pp_contract_interpretations WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(cid,)).fetchone())
            old_interpretation=interpretation['id'];interpretation.update(id=str(uuid4()),contract_id=second)
            insert('pp_contract_interpretations',interpretation)
            check=dict(svc.db.execute('SELECT * FROM pp_contract_reading_checks WHERE interpretation_id=%s ORDER BY checked_at DESC,id DESC LIMIT 1',(old_interpretation,)).fetchone())
            check.update(id=str(uuid4()),interpretation_id=interpretation['id']);insert('pp_contract_reading_checks',check)
            link=dict(svc.db.execute('SELECT * FROM pp_contract_configuration_links WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(cid,)).fetchone())
            link.update(id=str(uuid4()),contract_id=second,interpretation_id=interpretation['id'],review_id=check['id']);insert('pp_contract_configuration_links',link)
            material=dict(svc.db.execute('SELECT * FROM pp_contract_material_reviews WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(cid,)).fetchone())
            material.update(id=str(uuid4()),contract_id=second,link_id=link['id']);insert('pp_contract_material_reviews',material)
            choice=dict(svc.db.execute('SELECT * FROM pp_contract_material_choices WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(cid,)).fetchone())
            payload=deepcopy(choice['payload']);payload.update(contract_id=second,material_review_id=material['id'],base_hash=digest(material['result']),result=deepcopy(material['result']),allocations=[],reports=[])
            choice.update(id=str(uuid4()),contract_id=second,payload=payload,content_hash=digest(payload));insert('pp_contract_material_choices',choice)
        first_plan=app.reservations.inspect(self.alice,cid,0)['data']
        second_plan=app.reservations.inspect(self.alice,second,0)['data']
        inputs=[(cid,first_plan),(second,second_plan)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda x:app.reservations.reserve(self.alice,x[0],0,x[1]['preview_hash'],True,'race:'+x[0]),inputs))
        self.assertEqual(1,sum(r['ok'] for r in responses),responses)
        failed=next(r for r in responses if not r['ok'])
        self.assertEqual('STALE_REVISION',failed['errors'][0]['code'])
        winner=next(r['data']['reservation'] for r in responses if r['ok'])
        self.assertTrue(app.reservations.release(self.alice,winner['contract_id'],0,winner['id'],'race-release')['ok'])

    def test_save_reconnect_search_and_original_bytes(self):
        from ontologics.production_planning.contract_library import ContractLibrary
        saved=self.library.register(self.alice,**self.args)
        self.assertTrue(saved['ok'],saved)
        cid=saved['data']['id']
        fresh=ContractLibrary(CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings),self.alice)
        found=fresh.search(self.alice,'고객 하나')['data']['items']
        self.assertEqual([cid],[x['id'] for x in found])
        detail=fresh.get(self.alice,cid)['data']
        self.assertEqual(self.raw,base64.b64decode(detail['content_base64']))
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),detail['sha256'])
        self.assertEqual('received',detail['status'])
        self.assertEqual([],fresh.search(self.alice,'없는 고객')['data']['items'])
    def test_duplicate_retry_permissions_and_corruption(self):
        a=self.library.register(self.alice,**self.args)
        self.assertTrue(a['ok'],a)
        b=self.library.register(self.alice,**dict(self.args,request_key='other-key'))
        self.assertEqual(a['data']['id'],b['data']['id'])
        self.assertEqual(1,self.library.search(self.alice,'')['data']['total'])
        conflict=self.library.register(self.alice,**dict(self.args,title='변경'))
        self.assertEqual('IDEMPOTENCY_CONFLICT',conflict['errors'][0]['code'])
        denied=self.library.register(DevelopmentContext('dev-reader'),**self.args)
        self.assertEqual('FORBIDDEN',denied['errors'][0]['code'])
        with self.host.request(self.alice) as session:
            session._service.db.execute('UPDATE pp_contract_originals SET content=%s WHERE id=%s',(b'changed',a['data']['id']))
        self.assertEqual('SOURCE_HASH_MISMATCH',self.library.get(self.alice,a['data']['id'])['errors'][0]['code'])
    def test_http_requires_csrf_and_returns_persisted_original(self):
        import json,threading
        from urllib.request import build_opener,HTTPCookieProcessor,Request
        from urllib.error import HTTPError
        from http.cookiejar import CookieJar
        from ontologics.production_planning.contract_library_web import LibraryApplication,LibraryServer
        server=LibraryServer(LibraryApplication(self.host,self.alice),0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():server.shutdown();server.server_close();thread.join()
        self.addCleanup(close)
        client=build_opener(HTTPCookieProcessor(CookieJar()))
        with client.open(server.origin+'/api/context') as response:
            token=json.load(response)['data']['csrf_token']
        def post(op,body,csrf):
            req=Request(server.origin+'/api/tools/contracts/'+op,data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json','Origin':server.origin,'X-CSRF-Token':csrf})
            try:response=client.open(req)
            except HTTPError as exc:response=exc
            with response:return response.status,json.load(response)
        status,denied=post('register',self.args,'bad')
        self.assertEqual(403,status);self.assertEqual('CSRF_REJECTED',denied['errors'][0]['code'])
        status,saved=post('register',self.args,token)
        self.assertEqual(200,status,saved)
        _,found=post('search',dict(query='검색용',offset=0),token)
        self.assertEqual(saved['data']['id'],found['data']['items'][0]['id'])
        _,original=post('get',dict(contract_id=saved['data']['id']),token)
        self.assertEqual(self.raw,base64.b64decode(original['data']['content_base64']))
    def test_interpretation_persists_and_confirmation_binds_exact_result(self):
        import json
        from ontologics.production_planning.library_review import LibraryReview
        from ontologics.production_planning.library_review import REGISTRATION
        from ontologics.production_planning.contract_library import ContractLibrary
        host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings,agent_registrations=(REGISTRATION,))
        lib=ContractLibrary(host,self.alice)
        cid=lib.register(self.alice,**self.args)['data']['id']
        def provider(**kw):
            return dict(text=json.dumps({'items':[{'fields':{'ordered_quantity':[{'value':137,'evidence':[{'segment_id':'text:line:2','quote':'137','occurrence':0}]}]}}]}),tool_calls=[])
        workflow=LibraryReview(lib,self.alice,lambda:provider,'test')
        result=workflow.interpret(self.alice,cid,'interpret-one')
        self.assertTrue(result['ok'],result)
        row=result['data'];self.assertEqual('needs_review',row['status'],row)
        self.assertEqual(137,row['result']['items'][0]['fields']['ordered_quantity']['value'])
        args=dict(contract_id=cid,interpretation_id=row['id'],result_sha256=row['result_sha256'],scopes=['0:ordered_quantity'],note='원문 대조',request_key='check-one')
        self.assertTrue(workflow.confirm(self.alice,**args)['ok'])
        self.assertEqual(1,len(LibraryReview(lib,self.alice).latest(self.alice,cid)['data']['checks']))
        bad=workflow.confirm(self.alice,**dict(args,scopes=['0:delivery_due'],request_key='bad-scope'))
        self.assertEqual('INVALID_INPUT',bad['errors'][0]['code'])
        decisions={f'0:{name}':dict(state='unresolved',value=None,reason='') for name in row['result']['items'][0]['fields']}
        decisions['0:ordered_quantity']=dict(state='corrected',value='138',reason='원문 수량 재검토')
        reviewed=workflow.confirm(self.alice,**dict(args,scopes=[],decisions=decisions,request_key='whole-review'))
        self.assertTrue(reviewed['ok'],reviewed)
        persisted=LibraryReview(lib,self.alice).latest(self.alice,cid)['data']
        self.assertEqual(decisions,persisted['checks'][-1]['decisions'])
        repeated=workflow.confirm(self.alice,**dict(args,scopes=[],decisions=decisions,request_key='another-click'))
        self.assertTrue(repeated['data']['already_saved'])
        self.assertEqual(len(persisted['checks']),len(workflow.latest(self.alice,cid)['data']['checks']))
        storage=workflow.storage(self.alice,cid)['data']
        self.assertEqual(1,len(storage['pp_contract_originals']))
        self.assertEqual(1,len(storage['pp_contract_interpretations']))
        self.assertEqual(len(persisted['checks']),len(storage['pp_contract_reading_checks']))
        self.assertEqual(137,persisted['result']['items'][0]['fields']['ordered_quantity']['value'])
        from ontologics.production_planning.library_planning import LibraryPlanning
        planning=LibraryPlanning(workflow,self.alice)
        inspected=planning.inspect(self.alice,cid)
        self.assertTrue(inspected['ok'],inspected)
        self.assertEqual('catalog_empty',inspected['data']['status'])
        self.assertEqual('138',inspected['data']['items'][0]['fields']['ordered_quantity']['value'])
        self.assertEqual('human_correction',inspected['data']['items'][0]['fields']['ordered_quantity']['source'])
        self.assertIsNone(inspected['data']['items'][0]['fields']['delivery_due']['value'])
        from ontologics.service import Service
        from pathlib import Path
        catalog=json.loads((Path(__file__).resolve().parents[2]/'data/catalog.json').read_text())['nodes']
        if getattr(self,'complete_alternative_fixture',False):
            # Explicit isolated fixture: this is not a claim about OLIMEX assembly.
            for n in catalog:
                if n.get('type')=='BOMItem' and n.get('configuration_id')=='config:esp32-poe:L1:WROOM' and n.get('population')=='unknown':
                    n['population']='not_populated'
        service=Service(self.settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        self.addCleanup(service.close)
        self.assertTrue(service.bootstrap('fixture',catalog,request_key='catalog-seed')['ok'])
        catalog_view=planning.inspect(self.alice,cid)['data']
        candidate=catalog_view['items'][0]['candidates'][0]
        self.assertFalse(candidate['exact'])
        changes={k:dict(v) for k,v in decisions.items()}
        for name,comparison in candidate['comparison'].items():
            changes['0:'+name]=dict(state='corrected',value=comparison['catalog'],reason='제품 식별 원문 확인')
        self.assertTrue(workflow.confirm(self.alice,**dict(args,scopes=[],decisions=changes,request_key='identity-review'))['ok'])
        ready=planning.inspect(self.alice,cid)['data']
        matching=next(c for c in ready['items'][0]['candidates'] if c['exact'])
        link=dict(contract_id=cid,interpretation_id=ready['interpretation_id'],review_id=ready['review_id'],data_version=ready['data_version'],item_index=0,configuration_id=matching['configuration_id'],request_key='link-one')
        self.assertTrue(planning.connect(self.alice,**link)['ok'])
        self.assertTrue(planning.inspect(self.alice,cid)['data']['items'][0]['link_current'])
        from ontologics.production_planning.library_materials import LibraryMaterials
        materials=LibraryMaterials(planning,self.alice)
        before=materials.get(self.alice,cid,0)['data']
        self.assertIsNone(before['saved'])
        calculate=dict(contract_id=cid,item_index=0,link_id=before['link']['id'],quantity=7,reason='잔여 주문 7대 검토',request_key='materials-one')
        computed=materials.calculate(self.alice,**calculate)
        self.assertTrue(computed['ok'],computed)
        rows=computed['data']['result']['components']
        self.assertTrue(rows)
        self.assertTrue(all(r['A'] is None and r['S'] is None for r in rows))
        known=[r for r in rows if r['q'] is not None]
        self.assertTrue(known)
        self.assertTrue(all(r['D']==7*r['q'] for r in known))
        first=materials.get(self.alice,cid,0)['data']
        self.assertTrue(first['saved_current'])
        self.assertEqual(7,first['saved']['quantity'])
        materials.calculate(self.alice,**dict(calculate,request_key='materials-repeat'))
        self.assertEqual(first['saved']['id'],materials.get(self.alice,cid,0)['data']['saved']['id'])
        materials.calculate(self.alice,**dict(calculate,quantity=8,request_key='materials-eight'))
        materials.calculate(self.alice,**dict(calculate,request_key='materials-back-seven'))
        restored=materials.get(self.alice,cid,0)['data']['saved']
        self.assertEqual(7,restored['quantity'])
        self.assertNotEqual(first['saved']['id'],restored['id'])
        from ontologics.production_planning.library_stock import company_stock_scenario,LibraryStock
        from ontologics.production_planning.configuration_lookup import lookup_contract_configurations
        with self.host.request(self.alice) as session:
            catalog=lookup_contract_configurations(session,data_version=ready['data_version'])
            detail=next(d for d in catalog['configurations']['candidates'] if d['configuration']['id']==link['configuration_id'])
            payload=company_stock_scenario(detail,session._service.now())
        self.assertTrue(materials.stock.store(self.alice,link['configuration_id'],payload,'stock-one')['ok'])
        self.assertTrue(materials.get(self.alice,cid,0)['data']['stock_changed'])
        computed=materials.calculate(self.alice,**dict(calculate,quantity=120,request_key='with-stock'))
        self.assertTrue(computed['ok'],computed)
        rows=computed['data']['result']['components']
        self.assertTrue(all(r['A'] is not None for r in rows))
        self.assertEqual([20,40,120],sorted(r['S'] for r in rows if r['S']))
        self.assertTrue(all(r['S'] is None for r in rows if r['q'] is None))
        self.assertFalse(materials.get(self.alice,cid,0)['data']['stock_changed'])
        self.assertEqual('synthetic',computed['data']['result']['stock']['origin'])
        from ontologics.production_planning.library_rfq import LibraryRFQ
        rfq=LibraryRFQ(materials,self.alice)
        discovered=rfq.inspect(self.alice,cid,0)['data']
        self.assertEqual(3,len(discovered['shortages']))
        self.assertIsNone(discovered['draft'])
        self.assertTrue(all(c['direct_exchange_allowed'] is False for c in discovered['review']['candidates']))
        draft_args=dict(contract_id=cid,item_index=0,material_review_id=discovered['material_review_id'],component_ids=[r['component_id'] for r in discovered['shortages']],supplier='',desired_date='',destination='',note='원부품 견적 문의',request_key='rfq-one')
        created=rfq.create(self.alice,**draft_args)
        self.assertTrue(created['ok'],created)
        self.assertIn('협의 필요',created['data']['markdown'])
        self.assertIn('40개',created['data']['markdown'])
        stored=rfq.inspect(self.alice,cid,0)['data']
        self.assertTrue(stored['draft_current'])
        self.assertEqual(created['data']['markdown'],stored['draft']['markdown'])
        repeated=rfq.create(self.alice,**dict(draft_args,request_key='rfq-repeat'))
        self.assertEqual(created['data']['id'],repeated['data']['id'])
        denied=rfq.create(DevelopmentContext('dev-reader'),**dict(draft_args,request_key='rfq-reader'))
        self.assertFalse(denied['ok'])
        self.assertEqual('FORBIDDEN',denied['errors'][0]['code'])
        from ontologics.service import DomainError
        with self.assertRaises(DomainError):rfq.create(self.alice,**dict(draft_args,component_ids=['invented'],request_key='rfq-invented'))
        alternatives=rfq.alternatives
        from ontologics.production_planning.substitution import RIGHT
        with self.assertRaises(DomainError):rfq.create(self.alice,**dict(draft_args,request_type='purchase_request',reviewed=True,request_key='no-choice'))
        original=alternatives.compare(self.alice,cid,0,link['configuration_id'])['data']
        alternative=alternatives.compare(self.alice,cid,0,RIGHT)['data']
        self.assertTrue(alternative['blockers'])
        self.assertTrue(all(c['A'] is None for c in alternative['result']['components']))
        choose=dict(contract_id=cid,item_index=0,configuration_id=RIGHT,comparison_hash=alternative['comparison_hash'],technical_basis='격리 기술 검토 TEST-001',contract_basis='격리 고객 합의 TEST-002',note='격리 테스트 구성 변경 검증',reviewed=True,request_key='choose-blocked')
        with self.assertRaises(DomainError):alternatives.select(self.alice,**choose)
        stock_args=dict(contract_id=cid,item_index=0,configuration_id=RIGHT,comparison_hash=alternative['comparison_hash'],quantities={c['component_id']:max(0,(c['D'] or 0)-5) for c in alternative['result']['components']},company='isolated test company',location='test warehouse',as_of='2026-10-31T00:00:00+00:00',source_ref='test inventory report',reviewed=True,request_key='report-stock')
        stored_stock=alternatives.record_stock(self.alice,**stock_args)
        self.assertTrue(stored_stock['ok'],stored_stock)
        alternative=alternatives.compare(self.alice,cid,0,RIGHT)['data']
        self.assertTrue(all(c['A'] is not None for c in alternative['result']['components']))
        self.assertTrue(all(c['S']==5 for c in alternative['result']['components'] if c['D'] is not None))
        with self.assertRaises(DomainError):alternatives.select(self.alice,**choose)
        choose.update(comparison_hash=alternative['comparison_hash'],request_key='choose-target')
        if getattr(self,'complete_alternative_fixture',False):
            self.assertFalse(alternative['blockers'])
            with self.assertRaises(DomainError):alternatives.select(self.alice,**dict(choose,technical_basis=''))
            denied=alternatives.select(DevelopmentContext('dev-reader'),**choose)
            self.assertFalse(denied['ok'])
            chosen=alternatives.select(self.alice,**choose)
            self.assertTrue(chosen['ok'],chosen)
            again=alternatives.select(self.alice,**dict(choose,request_key='choose-repeat'))
            self.assertEqual(chosen['data']['id'],again['data']['id'])
            selected=rfq.inspect(self.alice,cid,0)['data']
            self.assertTrue(selected['choice_current'])
            self.assertEqual(RIGHT,selected['choice']['payload']['result']['configuration_id'])
            self.assertTrue(all(c['S']==5 for c in selected['shortages']))
            purchase=rfq.create(self.alice,**dict(draft_args,request_type='purchase_request',choice_id=selected['choice']['id'],component_ids=[c['component_id'] for c in selected['shortages']],reviewed=True,request_key='target-purchase'))
            self.assertTrue(purchase['ok'],purchase)
            self.assertIn('5개',purchase['data']['markdown'])
            self.assertEqual(link['configuration_id'],materials.get(self.alice,cid,0)['data']['saved']['result']['configuration_id'])
            new_stock=dict(stock_args,comparison_hash=alternative['comparison_hash'],source_ref='new test report',request_key='new-target-stock')
            self.assertTrue(alternatives.record_stock(self.alice,**new_stock)['ok'])
            self.assertFalse(rfq.inspect(self.alice,cid,0)['data']['choice_current'])
            self.assertFalse(rfq.inspect(self.alice,cid,0)['data']['draft_current'])
        else:
            self.assertTrue(alternative['blockers'])
            with self.assertRaises(DomainError):alternatives.select(self.alice,**choose)
        chosen=alternatives.select(self.alice,**dict(choose,configuration_id=link['configuration_id'],comparison_hash=original['comparison_hash'],technical_basis='',contract_basis='',note='원안 유지',request_key='choose-original'))
        self.assertTrue(chosen['ok'],chosen)
        purchase_args=dict(draft_args,request_type='purchase_request',choice_id=chosen['data']['id'],department='생산관리',technical_note='원부품 사양 유지',reviewed=True,request_key='purchase-one')
        with self.assertRaises(DomainError):rfq.create(self.alice,**dict(purchase_args,reviewed=False))
        with self.assertRaises(DomainError):rfq.create(self.alice,**dict(purchase_args,desired_date='2026-11-10'))
        purchase=rfq.create(self.alice,**purchase_args)
        self.assertTrue(purchase['ok'],purchase)
        self.assertIn('수신: 구매 담당',purchase['data']['markdown'])
        self.assertIn('발주 수량 아님',purchase['data']['markdown'])
        self.assertIn('미전달 초안',purchase['data']['markdown'])
        self.assertNotEqual(created['data']['id'],purchase['data']['id'])
        saved_purchase=rfq.inspect(self.alice,cid,0)['data']['draft']
        self.assertEqual('purchase_request',saved_purchase['payload']['document_type'])
        self.assertTrue(saved_purchase['payload']['reviewed'])
        self.assertEqual(purchase['data']['id'],rfq.create(self.alice,**dict(purchase_args,request_key='purchase-repeat'))['data']['id'])
        dated=rfq.create(self.alice,**dict(purchase_args,desired_date='2026-11-10',date_basis='생산 착수 전 입고',request_key='purchase-dated'))
        self.assertTrue(dated['ok'],dated)
        self.assertIn('생산 착수 전 입고',dated['data']['markdown'])
        # New web flow: specific components, original configuration retained.
        from ontologics.production_planning.contract_library_web import LibraryApplication
        app=LibraryApplication(self.host,self.alice)
        replacement=app.dispatch('/api/tools/contracts/replacements',dict(contract_id=cid,item_index=0))['data']
        source=next(c for c in replacement['material']['result']['components'] if c['S']==40)
        target=next(c for c in replacement['components'] if c['id']!=source['component_id'] and c['name']==source['name'])
        review_args=dict(contract_id=cid,item_index=0,material_review_id=replacement['material']['id'],source_id=source['component_id'],target_id=target['id'],status='compatible',technical_basis='isolated test technical record: 1:1 and mixed use',contract_basis='isolated test agreement',available=30,stock_basis='isolated stock report',stock_as_of='2026-10-31T00:00:00Z',location='isolated warehouse',reviewed=True,request_key='part-review')
        part=app.dispatch('/api/tools/contracts/replacement-review',review_args)
        self.assertTrue(part['ok'],part)
        self.assertEqual(part['data']['id'],app.dispatch('/api/tools/contracts/replacement-review',dict(review_args,request_key='part-review-again'))['data']['id'])
        allocations=[dict(review_id=part['data']['id'],quantity=40)]
        body=dict(contract_id=cid,item_index=0,allocations=allocations)
        preview=app.dispatch('/api/tools/contracts/replacement-preview',body)['data']
        result=preview['result']
        self.assertEqual(link['configuration_id'],result['configuration_id'])
        self.assertEqual(0,next(c['S'] for c in result['components'] if c['component_id']==source['component_id']))
        self.assertEqual(10,next(c['S'] for c in result['components'] if c['component_id']==target['id']))
        selection=dict(body,comparison_hash=preview['comparison_hash'],note='',reviewed=True,request_key='part-select')
        selected=app.dispatch('/api/tools/contracts/replacement-select',selection)
        self.assertTrue(selected['ok'],selected)
        self.assertEqual(selected['data']['id'],app.dispatch('/api/tools/contracts/replacement-select',dict(selection,request_key='part-repeat'))['data']['id'])
        current=app.dispatch('/api/tools/contracts/rfq',dict(contract_id=cid,item_index=0))['data']
        self.assertTrue(current['choice_current'])
        web_purchase=dict(contract_id=cid,item_index=0,material_review_id=current['material_review_id'],component_ids=[target['id']],department='test',desired_date='',date_basis='',destination='',technical_note='',note='test',reviewed=True,request_key='part-purchase',choice_id=selected['data']['id'])
        purchase=app.dispatch('/api/tools/contracts/purchase-request-create',web_purchase)
        self.assertTrue(purchase['ok'],purchase)
        self.assertIn('10개',purchase['data']['markdown'])
        self.assertIn('40개 (1:1 계획)',purchase['data']['markdown'])
        if getattr(self,'reservation_competition',False):self.check_competing_reservations(app,cid)
        plan=app.dispatch('/api/tools/contracts/reservation-plan',dict(contract_id=cid,item_index=0))
        self.assertTrue(plan['ok'],plan)
        self.assertTrue(any(r['waiting']>0 for r in plan['data']['lines'] if r['waiting'] is not None))
        reserve_args=dict(contract_id=cid,item_index=0,preview_hash=plan['data']['preview_hash'],reviewed=True,request_key='reserve-one')
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda key:app.dispatch('/api/tools/contracts/reserve-materials',dict(reserve_args,request_key=key)),['reserve-one','reserve-racing']))
        reserved=results[0]
        self.assertTrue(results[1]['ok'],results[1])
        self.assertEqual(reserved['data']['reservation']['id'],results[1]['data']['reservation']['id'])
        self.assertTrue(reserved['ok'],reserved)
        again=app.dispatch('/api/tools/contracts/reserve-materials',dict(reserve_args,request_key='reserve-again'))
        self.assertEqual(reserved['data']['reservation']['id'],again['data']['reservation']['id'])
        with self.host.request(self.alice) as session:
            from ontologics.production_planning.library_reservations import reserved_totals
            own=reserved_totals(session._service,(cid,0))
            other=reserved_totals(session._service,('another-plan',0))
            self.assertEqual({},own)
            self.assertGreater(other[target['id']],0)
            snapshot=materials.stock.latest(session._service,link['configuration_id'])
            own_stock=materials.stock.calculate(snapshot,session._service,(cid,0))
            other_stock=materials.stock.calculate(snapshot,session._service,('another-plan',0))
            for line in plan['data']['lines']:
                component=line['component_id']
                if component in own_stock['components'] and line['reserve']:
                    self.assertEqual(max(0,own_stock['components'][component]['available_quantity']-line['reserve']),other_stock['components'][component]['available_quantity'])
        denied=app.reservations.release(DevelopmentContext('dev-reader'),cid,0,reserved['data']['reservation']['id'],'reader-release')
        self.assertFalse(denied['ok'])
        summary=app.dispatch('/api/tools/contracts/production-plan',dict(contract_id=cid,item_index=0))
        self.assertTrue(summary['ok'],summary)
        self.assertIn('생산 완료일: 미확정',summary['data']['markdown'])
        document_args=dict(contract_id=cid,item_index=0,basis_hash=summary['data']['basis_hash'],note='조건 확인',reviewed=True,request_key='production-document')
        document=app.dispatch('/api/tools/contracts/production-plan-save',document_args)
        self.assertTrue(document['ok'],document)
        repeated_document=app.dispatch('/api/tools/contracts/production-plan-save',dict(document_args,request_key='production-document-repeat'))
        self.assertEqual(document['data']['draft']['id'],repeated_document['data']['draft']['id'])
        self.assertEqual(document['data']['draft']['markdown'],app.dispatch('/api/tools/contracts/production-plan',dict(contract_id=cid,item_index=0))['data']['draft']['markdown'])
        if getattr(self,'schedule_scenario',False):
            shortages=app.rfq.inspect(self.alice,cid,0)['data']['shortages']
            full_purchase=app.dispatch('/api/tools/contracts/purchase-request-create',dict(web_purchase,component_ids=[r['component_id'] for r in shortages],request_key='full-schedule-purchase'))
            self.assertTrue(full_purchase['ok'],full_purchase)
            from datetime import datetime
            from ontologics.production_planning.schedule_demo import build_dataset
            data=app.fulfillment.inspect(self.alice,cid,0)['data']
            with self.host.request(self.alice) as session:
                choice=app.rfq.replacements.latest(session._service,cid,0)
                now=datetime.fromisoformat(session._service.now())
            bundle=build_dataset(data['basis'],choice['payload']['result']['configuration_id'],data['purchase'],data['receipts'],now)
            stored=app.schedule.store(self.alice,bundle,'schedule-master')
            self.assertTrue(stored['ok'],stored)
            proposed=app.schedule.propose(self.alice,cid,0)['data']
            self.assertGreater(len(proposed['candidates']),0,proposed['blockers'])
            candidate=proposed['candidates'][0]
            self.assertEqual('conditional',candidate['status'])
            self.assertFalse(candidate['production_authorized'])
            args=dict(contract_id=cid,item_index=0,not_before=proposed['not_before'],basis_hash=proposed['basis_hash'],inputs_hash=proposed['inputs_hash'],candidate_id=candidate['id'],note='',reviewed=True,request_key='schedule-save')
            saved=app.schedule.save(self.alice,**args);self.assertTrue(saved['ok'],saved)
            repeated=app.schedule.save(self.alice,**dict(args,request_key='schedule-save-again'));self.assertEqual(saved['data']['id'],repeated['data']['id'])
            document_view=app.plan_document.inspect(self.alice,cid,0)
            self.assertTrue(document_view['ok'],document_view.get('errors'))
            self.assertIsNotNone(document_view['data']['basis'],document_view['data'].get('message'))
            self.assertEqual(candidate['completion_at'],document_view['data']['basis']['auto_schedule']['completion_at'])
            self.assertFalse(app.schedule.save(DevelopmentContext('dev-reader'),**dict(args,request_key='reader-schedule'))['ok'])
            changed={k:[] for k in bundle};changed['blocks']=[dict(bundle['blocks'][0],id='new-block',resource_id=candidate['resource']['id'],start_at=candidate['start_at'],end_at=candidate['completion_at'])]
            self.assertTrue(app.schedule.store(self.alice,changed,'schedule-master-change')['ok'])
            self.assertFalse(app.schedule.save(self.alice,**dict(args,request_key='stale-schedule'))['ok'])
            self.assertNotIn('auto_schedule',app.plan_document.inspect(self.alice,cid,0)['data']['basis'])
            return
        if getattr(self,'supply_scenario',False):
            supply=app.fulfillment.inspect(self.alice,cid,0)['data']
            base=dict(contract_id=cid,item_index=0,basis_hash=supply['basis_hash'],reviewed=True)
            sent=app.fulfillment.record(self.alice,**base,kind='transmission',reference='SEND-TEST',details=dict(recipient='구매 담당',channel='업무 채널',occurred_at='2026-10-30T00:00:00+00:00',evidence='격리 테스트 전달 기록'),request_key='send-test')
            self.assertTrue(sent['ok'],sent)
            supply=app.fulfillment.inspect(self.alice,cid,0)['data']
            if getattr(self,'warehouse_scenario',False):
                args=dict(purchase_id=supply['basis']['purchase_id'],reference='WH-TEST-1',details=dict(component_id=target['id'],received=10,accepted=8,rejected=1,pending=1,occurred_at='2026-10-30T01:00:00+00:00',location='검증 창고',evidence='격리 검수 근거',origin='synthetic'),request_key='warehouse-test')
                stored=app.fulfillment.warehouse.register(self.alice,**args)
                self.assertTrue(stored['ok'],stored)
                again=app.fulfillment.warehouse.register(self.alice,**dict(args,request_key='warehouse-repeat'))
                self.assertEqual(stored['data']['id'],again['data']['id'])
                before=app.fulfillment.inspect(self.alice,cid,0)['data']
                line=next(r for r in before['basis']['lines'] if r['component_id']==target['id'])
                self.assertEqual(30,line['reserve']);self.assertEqual(10,line['waiting'])
                lot=before['receipts'][0];self.assertEqual(8,lot['available'])
                allocation=dict(contract_id=cid,item_index=0,receipt_id=lot['id'],quantity=6,basis_hash=before['basis_hash'],reviewed=True,request_key='allocate-once')
                result=app.fulfillment.warehouse.allocate(self.alice,**allocation)
                self.assertTrue(result['ok'],result)
                replay=app.fulfillment.warehouse.allocate(self.alice,**allocation)
                self.assertTrue(replay['ok'],replay)
                after=app.fulfillment.inspect(self.alice,cid,0)['data']
                line=next(r for r in after['basis']['lines'] if r['component_id']==target['id'])
                self.assertEqual(36,line['reserve']);self.assertEqual(4,line['waiting'])
                self.assertEqual(2,after['receipts'][0]['available'])
                over=app.fulfillment.warehouse.allocate(self.alice,**dict(allocation,basis_hash=after['basis_hash'],quantity=3,request_key='allocate-over'))
                self.assertFalse(over['ok'])
                with self.host.request(self.alice) as session:
                    from ontologics.production_planning.library_reservations import reserved_totals
                    from ontologics.production_planning.library_fulfillment import received_totals
                    self.assertEqual(8,received_totals(session._service)[target['id']])
                    self.assertEqual(38,reserved_totals(session._service,('another',0))[target['id']])
                denied=app.fulfillment.warehouse.allocate(DevelopmentContext('dev-reader'),**dict(allocation,basis_hash=after['basis_hash'],quantity=1,request_key='reader-allocation'))
                self.assertFalse(denied['ok'])
                from concurrent.futures import ThreadPoolExecutor
                def allocate_last(key):
                    return app.fulfillment.warehouse.allocate(self.alice,**dict(allocation,basis_hash=after['basis_hash'],quantity=2,request_key=key))
                with ThreadPoolExecutor(max_workers=2) as pool:
                    concurrent=list(pool.map(allocate_last,['allocate-last-a','allocate-last-b']))
                self.assertEqual(1,sum(r['ok'] for r in concurrent))
                released=app.reservations.release(self.alice,cid,0,before['basis']['reservation_id'],'warehouse-release')
                self.assertTrue(released['ok'],released)
                with self.host.request(self.alice) as session:
                    from ontologics.production_planning.library_warehouse import remaining
                    self.assertEqual(0,remaining(session._service)[0]['available'])
                    self.assertEqual(8,received_totals(session._service)[target['id']])
                    self.assertEqual(0,reserved_totals(session._service).get(target['id'],0))
                return
            receipt=dict(base,basis_hash=supply['basis_hash'],kind='receipt',reference='RECEIPT-TEST-line-1',details=dict(component_id=target['id'],received=10,accepted=6,rejected=2,pending=2,occurred_at='2026-10-30T01:00:00+00:00',location='테스트 창고',evidence='격리 테스트 검수 기록'),request_key='receipt-test')
            received=app.fulfillment.record(self.alice,**receipt)
            self.assertTrue(received['ok'],received)
            duplicate=app.fulfillment.record(self.alice,**dict(receipt,request_key='receipt-repeat'))
            self.assertEqual(received['data']['id'],duplicate['data']['id'])
            updated=app.fulfillment.inspect(self.alice,cid,0)['data']
            target_line=next(r for r in updated['basis']['lines'] if r['component_id']==target['id'])
            self.assertEqual(36,target_line['reserve']);self.assertEqual(4,target_line['waiting'])
            self.assertEqual(reserved['data']['reservation']['id'],updated['basis']['reservation_id'])
            self.assertFalse(app.plan_document.inspect(self.alice,cid,0)['data']['draft_current'])
            bad=app.fulfillment.record(self.alice,**dict(receipt,basis_hash=updated['basis_hash'],reference='RECEIPT-over',request_key='receipt-over'))
            self.assertFalse(bad['ok'])
            with self.host.request(self.alice) as session:
                from ontologics.production_planning.library_reservations import reserved_totals
                self.assertEqual(36,reserved_totals(session._service,('other',0))[target['id']])
            self.assertFalse(app.rfq.inspect(self.alice,cid,0)['data']['draft_current'])
            from datetime import datetime,timedelta,timezone
            schedule=dict(base,basis_hash=updated['basis_hash'],kind='schedule',reference='SCHEDULE-1',request_key='schedule-1',details=dict(resource='시험 라인',staff='검증 인력',start_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),setup_minutes=10,process_minutes=120,inspection_minutes=20,evidence='격리 테스트 시간 근거'))
            self.assertFalse(app.fulfillment.record(self.alice,**schedule)['ok'])
            # Unit boundary for the schedule calculation: fully ready input, real DB persistence.
            from unittest.mock import patch
            from ontologics.model import digest
            ready=dict(updated['basis'],waiting_count=0,unknown_count=0,unresolved_contract_fields=[])
            with patch.object(app.plan_document,'_basis',return_value=ready):
                saved_schedule=app.fulfillment.record(self.alice,**dict(schedule,basis_hash=digest(ready),request_key='schedule-ready'))
            self.assertTrue(saved_schedule['ok'],saved_schedule)
            payload=saved_schedule['data']['payload']
            self.assertEqual(timedelta(minutes=150),datetime.fromisoformat(payload['completion_at'])-datetime.fromisoformat(payload['start_at']))
            self.assertFalse(payload['production_authorized']);self.assertFalse(payload['resource_reserved'])
            released=app.dispatch('/api/tools/contracts/release-materials',dict(contract_id=cid,item_index=0,reservation_id=updated['basis']['reservation_id'],request_key='receipt-release'))
            self.assertTrue(released['ok'],released)
            with self.host.request(self.alice) as session:
                from ontologics.production_planning.library_fulfillment import received_totals
                self.assertEqual(6,received_totals(session._service)[target['id']])
                self.assertEqual(0,reserved_totals(session._service).get(target['id'],0))
            return
        release=app.dispatch('/api/tools/contracts/release-materials',dict(contract_id=cid,item_index=0,reservation_id=reserved['data']['reservation']['id'],request_key='release-one'))
        self.assertTrue(release['ok'],release)
        released=app.dispatch('/api/tools/contracts/reservation-plan',dict(contract_id=cid,item_index=0))
        self.assertIsNone(released['data']['reservation'])
        expired=app.dispatch('/api/tools/contracts/production-plan-save',dict(document_args,request_key='production-after-release'))
        self.assertFalse(expired['ok'])
        changed=app.dispatch('/api/tools/contracts/replacement-review',dict(review_args,available=20,request_key='changed-part-stock'))
        self.assertTrue(changed['ok'],changed)
        self.assertFalse(app.dispatch('/api/tools/contracts/rfq',dict(contract_id=cid,item_index=0))['data']['draft_current'])
        with self.assertRaises(DomainError):app.dispatch('/api/tools/contracts/replacement-select',dict(selection,request_key='stale-part-select'))
        from ontologics.production_planning.substitution import U2,U2_L1
        with self.assertRaises(DomainError):app.dispatch('/api/tools/contracts/replacement-review',dict(review_args,source_id=U2,target_id=U2_L1,request_key='known-design-change'))
        unchanged_body=dict(contract_id=cid,item_index=0,allocations=[])
        unchanged=app.dispatch('/api/tools/contracts/replacement-preview',unchanged_body)['data']
        self.assertEqual(40,next(c['S'] for c in unchanged['result']['components'] if c['component_id']==source['component_id']))
        kept=app.dispatch('/api/tools/contracts/replacement-select',dict(unchanged_body,comparison_hash=unchanged['comparison_hash'],note='',reviewed=True,request_key='no-replacement'))
        self.assertTrue(kept['ok'],kept)
        original_purchase=app.dispatch('/api/tools/contracts/purchase-request-create',dict(web_purchase,component_ids=[source['component_id']],choice_id=kept['data']['id'],request_key='original-purchase'))
        self.assertTrue(original_purchase['ok'],original_purchase)
        self.assertIn('40개',original_purchase['data']['markdown'])
        materials.calculate(self.alice,**dict(calculate,quantity=121,request_key='material-newer'))
        with self.assertRaises(DomainError):rfq.create(self.alice,**dict(draft_args,request_key='rfq-stale'))
        self.assertFalse(rfq.inspect(self.alice,cid,0)['data']['draft_current'])





        invalid={**decisions,'0:delivery_due':dict(state='accepted',value='tomorrow',reason='')}
        rejected=workflow.confirm(self.alice,**dict(args,scopes=[],decisions=invalid,request_key='invalid-whole'))
        self.assertFalse(rejected['ok'])
        invalid={**decisions,'0:ordered_quantity':dict(state='corrected',value='138',reason='')}
        self.assertFalse(workflow.confirm(self.alice,**dict(args,scopes=[],decisions=invalid,request_key='no-reason'))['ok'])
        newer=workflow.interpret(self.alice,cid,'interpret-two')['data']
        self.assertNotEqual(row['id'],newer['id']);self.assertEqual([],newer['checks'])
        stale=workflow.confirm(self.alice,**dict(args,request_key='stale'))
        self.assertEqual('STALE_REVISION',stale['errors'][0]['code'])

    def test_complete_alternative_fixture_persists_selection_and_purchase(self):
        self.complete_alternative_fixture=True
        self.test_interpretation_persists_and_confirmation_binds_exact_result()

    def test_source_only_preserves_literal_date_and_unit_and_rejects_bad_evidence(self):
        import tempfile
        from pathlib import Path
        from ontologics.production_planning.contract_intake import extract_contract_text,prepare_contract_demand
        from ontologics.production_planning.contract_interpretation import parse_interpretation
        import json
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.txt';path.write_text('납기: 2026년 11월 10일\n단위: 대',encoding='utf-8')
            source=extract_contract_text(path)
            payload={'items':[{'fields':{'delivery_due':[{'value':'2026년 11월 10일','evidence':[{'segment_id':'text:line:1','quote':'2026년 11월 10일','occurrence':0}]}],'unit':[{'value':'대','evidence':[{'segment_id':'text:line:2','quote':'대','occurrence':0}]}]}}]}
            interpreted=parse_interpretation(json.dumps(payload),source,data_version=0)
            report=prepare_contract_demand(source,interpreted,{'data_version':0,'candidates':[]},source_only=True)
            self.assertTrue(report['valid'],report['errors'])
            self.assertFalse(prepare_contract_demand(source,interpreted,{'data_version':0,'candidates':[]})['valid'])
            interpreted['items'][0]['fields']['unit'][0]['value']='piece'
            self.assertFalse(prepare_contract_demand(source,interpreted,{'data_version':0,'candidates':[]},source_only=True)['valid'])
    def test_registration_runs_interpretation_and_failure_preserves_original(self):
        from ontologics.production_planning.contract_library_web import LibraryApplication
        from ontologics.production_planning.library_review import REGISTRATION
        host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings,agent_registrations=(REGISTRATION,))
        calls=[]
        def invalid(**kw):
            calls.append(1)
            return dict(text='{}',tool_calls=[])
        app=LibraryApplication(host,self.alice,lambda:invalid,'test')
        saved=app.dispatch('/api/tools/contracts/register',self.args)
        self.assertTrue(saved['ok'],saved);self.assertEqual(1,len(calls))
        cid=saved['data']['id']
        detail=app.dispatch('/api/tools/contracts/get',dict(contract_id=cid))['data']
        self.assertEqual(self.raw,base64.b64decode(detail['content_base64']))
        self.assertEqual('failed',detail['review']['status'])
        app.dispatch('/api/tools/contracts/get',dict(contract_id=cid))
        app.dispatch('/api/tools/contracts/register',self.args)
        self.assertEqual(1,len(calls),'Read and request replay must not call the model again')
        self.assertEqual([],detail['review']['checks'])
