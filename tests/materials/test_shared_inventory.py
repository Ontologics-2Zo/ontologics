"""Shared BOM stock through the real planning/reservation/release services."""
import base64
import json
import os
import unittest
from pathlib import Path
from copy import deepcopy
from ontologics.materials.library_stock import shared_stock, company_stock_scenario
from ontologics.service import DomainError

class SharedResolverTests(unittest.TestCase):
    def row(self,key='a',available=100,when='2026-11-02T00:00:00+00:00'):
        scope=dict(company='C',location='W',as_of=when,unit='piece',component_lots={'part':['lot']},coverage_basis='full count')
        doc=dict(source_ref=key,sha256='a'*64,stock_scope=scope,stocks=[dict(id=key,component_id='part',lot_id='lot',unit='piece',quantity_state='known',availability_basis='reported_available',available_quantity=available)])
        return dict(id=key,payload=dict(origin='synthetic',label=key,scope=scope,snapshots=[doc]))
    def test_same_lot_is_not_added_twice(self):
        r=shared_stock([self.row(),self.row('b')])
        self.assertEqual(100,r['shared_calculated']['components']['part']['available_quantity'])
        self.assertEqual(1,len(r['shared_calculated']['selected']))
    def test_newer_observation_updates_shared_component(self):
        r=shared_stock([self.row(),self.row('b',70,'2026-11-03T00:00:00+00:00')])
        self.assertEqual(70,r['shared_calculated']['components']['part']['available_quantity'])
    def test_conflicts_and_distinct_warehouses_are_blocked(self):
        with self.assertRaises(DomainError):shared_stock([self.row(),self.row('b',70)])
        b=self.row('b');b['payload']['scope']['location']='other'
        with self.assertRaises(DomainError):shared_stock([self.row(),b])

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL required')
class SharedInventoryTests(unittest.TestCase):
    def test_reserve_30_and_release_visible_in_both_products(self):
        from tests.db import database_for_test
        from ontologics.host import CommonHost
        from ontologics.identity import DevelopmentContext,DevelopmentIdentityProvider
        from ontologics.web import USERS
        from ontologics.service import Service
        from ontologics.materials.library_review import REGISTRATION
        from ontologics.materials.contract_library_web import LibraryApplication
        from ontologics.materials.configuration_lookup import lookup_contract_configurations
        settings=database_for_test(self);ctx=DevelopmentContext('dev-alice')
        host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),settings,agent_registrations=(REGISTRATION,))
        def provider(**kw):
            return dict(text=json.dumps({'items':[{'fields':{'ordered_quantity':[{'value':30,'evidence':[{'segment_id':'text:line:1','quote':'30','occurrence':0}]}]}}]}),tool_calls=[])
        app=LibraryApplication(host,ctx,lambda:provider,'test')
        svc=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}});self.addCleanup(svc.close)
        nodes=json.loads((Path(__file__).resolve().parents[2]/'data/catalog.json').read_text())['nodes']
        self.assertTrue(svc.bootstrap('fixture',nodes,request_key='catalog')['ok'])
        def ok(r):self.assertTrue(r['ok'],r);return r['data']
        cid=ok(app.library.register(ctx,title='30 board inventory test',counterparty='test',filename='contract.txt',content_base64=base64.b64encode(b'30 boards').decode(),request_key='original'))['id']
        row=ok(app.review.interpret(ctx,cid,'interpret'))
        decisions={f'0:{name}':dict(state='unresolved',value=None,reason='') for name in row['result']['items'][0]['fields']}
        decisions['0:ordered_quantity']=dict(state='accepted',value=30,reason='')
        ok(app.review.confirm(ctx,cid,row['id'],row['result_sha256'],[], 'fixture', 'initial-check',decisions=decisions))
        config='config:esp32-poe:L:WROOM';other='config:esp32-poe:L1:WROOM'
        candidate=next(c for c in ok(app.planning.inspect(ctx,cid))['items'][0]['candidates'] if c['configuration_id']==config)
        for name,comparison in candidate['comparison'].items():decisions['0:'+name]=dict(state='corrected',value=comparison['catalog'],reason='isolated fixture product identity')
        decisions['0:ordered_quantity']=dict(state='accepted',value=30,reason='')
        ok(app.review.confirm(ctx,cid,row['id'],row['result_sha256'],[], 'fixture', 'check',decisions=decisions))
        ready=ok(app.planning.inspect(ctx,cid))
        ok(app.planning.connect(ctx,cid,ready['interpretation_id'],ready['review_id'],ready['data_version'],0,config,'connect'))
        with host.request(ctx) as session:
            detail=next(d for d in lookup_contract_configurations(session,data_version=ready['data_version'])['configurations']['candidates'] if d['configuration']['id']==config)
            stock=company_stock_scenario(detail,session._service.now())
        from ontologics.materials.library_inventory_identity import register
        parts=[n for n in nodes if n['type']=='ComponentModel' and n['name']=='100k/R0402']
        left=next(n['id'] for n in parts if 'olimex-L-wroom:' in n['id'])
        right=next(n['id'] for n in parts if 'olimex-L1-wroom:' in n['id'])
        ok(register(host,ctx,[left,right],'Isolated company SKU test: same stocked resistor in both configurations','link-inventory'))
        # Explicit 100 usable pieces per component, no synthetic prior holds.
        for doc in stock['snapshots']:
            for r in doc['stocks']:r.update(on_hand_quantity=100,reserved_quantity=0,held_quantity=0)
            from ontologics.model import digest
            doc['sha256']=digest({k:v for k,v in doc.items() if k!='sha256'})
        ok(app.materials.stock.store(ctx,config,stock,'stock'))
        saved=ok(app.materials.get(ctx,cid,0))
        ok(app.materials.calculate(ctx,cid,0,saved['link']['id'],30,'30 board shared-stock test','calculate'))
        preview=ok(app.rfq.replacements.preview(ctx,cid,0,[]))
        ok(app.rfq.replacements.select(ctx,cid,0,[],preview['comparison_hash'],'no substitution',True,'select'))
        def availability():
            data=ok(app.materials.inventory(ctx,30))
            return {p['configuration_id']:{r['component_id']:r for r in p['result']['components']} for p in data['products']}
        before=availability()
        part=left
        self.assertEqual([100,100],[before[config][left]['A'],before[other][right]['A']])
        plan=ok(app.reservations.inspect(ctx,cid,0))
        reservation=ok(app.reservations.reserve(ctx,cid,0,plan['preview_hash'],True,'reserve'))['reservation']
        self.assertEqual(30,next(r['reserve'] for r in reservation['payload']['lines'] if r['component_id']==part))
        during=availability();self.assertEqual([70,70],[during[config][left]['A'],during[other][right]['A']])
        ok(app.reservations.release(ctx,cid,0,reservation['id'],'release'))
        after=availability();self.assertEqual([100,100],[after[config][left]['A'],after[other][right]['A']])
        # Repeat through the other revision: alias reservations must debit L too.
        candidate=next(c for c in ok(app.planning.inspect(ctx,cid))['items'][0]['candidates'] if c['configuration_id']==other)
        for name,comparison in candidate['comparison'].items():decisions['0:'+name]=dict(state='corrected',value=comparison['catalog'],reason='switch isolated plan to L1')
        ok(app.review.confirm(ctx,cid,row['id'],row['result_sha256'],[], 'fixture L1', 'check-L1',decisions=decisions))
        ready=ok(app.planning.inspect(ctx,cid))
        ok(app.planning.connect(ctx,cid,ready['interpretation_id'],ready['review_id'],ready['data_version'],0,other,'connect-L1'))
        saved=ok(app.materials.get(ctx,cid,0))
        ok(app.materials.calculate(ctx,cid,0,saved['link']['id'],30,'L1 shared-stock test','calculate-L1'))
        preview=ok(app.rfq.replacements.preview(ctx,cid,0,[]))
        ok(app.rfq.replacements.select(ctx,cid,0,[],preview['comparison_hash'],'no substitution',True,'select-L1'))
        plan=ok(app.reservations.inspect(ctx,cid,0))
        reservation=ok(app.reservations.reserve(ctx,cid,0,plan['preview_hash'],True,'reserve-L1'))['reservation']
        during=availability();self.assertEqual([70,70],[during[config][left]['A'],during[other][right]['A']])
        ok(app.reservations.release(ctx,cid,0,reservation['id'],'release-L1'))
        after=availability();self.assertEqual([100,100],[after[config][left]['A'],after[other][right]['A']])
        print('Shared stock verified in BOTH directions: L/L1 100/100 -> reserve 30 -> 70/70 -> release -> 100/100')
