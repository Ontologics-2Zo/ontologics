"""Shared BOM stock through the real planning/reservation/release services."""
import base64
import json
import os
import unittest
from pathlib import Path
from copy import deepcopy
from ontologics.materials.library_stock import LibraryStock, shared_stock, company_stock_scenario
from ontologics.service import DomainError

class SharedResolverTests(unittest.TestCase):
    @staticmethod
    def row(key='a',available=100,when='2026-11-02T00:00:00+00:00',*, observed=None, component='part'):
        scope=dict(company='C',location='W',as_of=when,unit='piece',component_lots={component:['lot']},coverage_basis='full count')
        doc=dict(source_ref=key,sha256='a'*64,stock_scope=scope,stocks=[dict(id=key,component_id=component,lot_id='lot',unit='piece',quantity_state='known',availability_basis='reported_available',available_quantity=available)])
        if observed is not None:doc['stocks'][0]['as_of']=observed
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

    def test_observation_time_wins_over_later_evaluation_cutoff(self):
        a=self.row('new-count',100,'2026-11-03T00:00:00Z')
        b=self.row('old-count',200,'2026-11-04T00:00:00Z',observed='2026-11-02T00:00:00Z')
        for rows in ([a,b],[b,a]):
            with self.subTest(order=[row['id'] for row in rows]):
                result=shared_stock(rows)['shared_calculated']
                self.assertEqual(100,result['components']['part']['available_quantity'])
                self.assertEqual('2026-11-03T00:00:00Z',result['components']['part']['source_as_of'])
                self.assertEqual('new-count',result['selected'][0]['source']['source_ref'])
                self.assertEqual('/stocks/0',result['selected'][0]['source']['locator'])
                self.assertEqual('2026-11-04T00:00:00Z',result['scope']['as_of'])
                self.assertEqual(86400,result['selected'][0]['age_seconds'])

    def test_pooled_lot_ages_use_returned_scope_cutoff(self):
        a=self.row('a',100,'2026-11-03T00:00:00Z')
        payload=a['payload']
        payload['scope']['component_lots']['part'].append('lot2')
        doc=payload['snapshots'][0]
        doc['stocks'].append(dict(doc['stocks'][0],id='a2',lot_id='lot2',as_of='2026-11-02T09:00:00+09:00'))
        b=self.row('b',70,'2026-11-04T00:00:00Z',component='other')
        b['payload']['scope']['component_lots']['other']=['other-lot']
        b['payload']['snapshots'][0]['stocks'][0]['lot_id']='other-lot'
        result=shared_stock([a,b])['shared_calculated']
        self.assertEqual('2026-11-04T00:00:00Z',result['scope']['as_of'])
        self.assertEqual(
            {'lot':86400,'lot2':172800,'other-lot':0},
            {s['observation']['lot_id']:s['age_seconds'] for s in result['selected']},
        )


    def test_equal_instant_different_counts_conflict_across_cutoffs(self):
        a=self.row('a',100,'2026-11-03T00:00:00Z')
        b=self.row('b',200,'2026-11-04T00:00:00Z',observed='2026-11-03T09:00:00+09:00')
        with self.assertRaises(DomainError) as error:
            shared_stock([a,b])
        self.assertEqual('STOCK_OBSERVATION_CONFLICT',error.exception.code)

    def test_equal_instant_equivalent_counts_corroborate(self):
        a=self.row('a',100,'2026-11-03T00:00:00Z')
        b=self.row('b',100,'2026-11-03T09:00:00+09:00')
        result=shared_stock([a,b])['shared_calculated']
        self.assertEqual(100,result['components']['part']['available_quantity'])
        self.assertEqual(1,len(result['selected']))

    def test_crossing_lot_times_need_reconciliation(self):
        a=self.row('a',100,'2026-11-04T00:00:00Z',observed='2026-11-03T00:00:00Z')
        b=self.row('b',200,'2026-11-05T00:00:00Z',observed='2026-11-02T00:00:00Z')
        for row,when in ((a,'2026-11-02T00:00:00Z'),(b,'2026-11-04T00:00:00Z')):
            payload=row['payload']
            payload['scope']['component_lots']['part'].append('lot2')
            doc=payload['snapshots'][0]
            doc['stocks'].append(dict(doc['stocks'][0],id=row['id']+'2',lot_id='lot2',as_of=when))
        with self.assertRaises(DomainError) as error:
            shared_stock([a,b])
        self.assertEqual('STOCK_OBSERVATION_CONFLICT',error.exception.code)

    def test_new_complete_lot_set_replaces_old_without_union(self):
        a=self.row('a',100,'2026-11-03T00:00:00Z')
        b=self.row('b',70,'2026-11-04T00:00:00Z')
        b['payload']['scope']['component_lots']['part']=['replacement-lot']
        b['payload']['snapshots'][0]['stocks'][0]['lot_id']='replacement-lot'
        result=shared_stock([a,b])['shared_calculated']
        self.assertEqual(70,result['components']['part']['available_quantity'])
        self.assertEqual(['replacement-lot'],result['components']['part']['lot_ids'])

    def test_future_observation_cannot_replace_eligible_count(self):
        a=self.row('a',100,'2026-11-03T00:00:00Z')
        b=self.row('b',200,'2026-11-04T00:00:00Z',observed='2026-11-05T00:00:00Z')
        result=shared_stock([a,b])['shared_calculated']
        self.assertEqual(100,result['components']['part']['available_quantity'])
        self.assertEqual([],result['unresolved'])

    def test_new_unknown_quantity_remains_unknown(self):
        a=self.row('a',100,'2026-11-03T00:00:00Z')
        b=self.row('b',None,'2026-11-04T00:00:00Z')
        result=shared_stock([a,b])['shared_calculated']
        self.assertIsNone(result['components']['part']['available_quantity'])
        self.assertTrue(result['unresolved'])

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL required')
class SharedInventoryTests(unittest.TestCase):
    def stock_fixture(self):
        from tests.db import database_for_test
        from ontologics.host import CommonHost
        from ontologics.identity import DevelopmentContext,DevelopmentIdentityProvider
        from ontologics.web import USERS
        settings=database_for_test(self)
        context=DevelopmentContext('dev-alice')
        host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),settings)
        return host,context,LibraryStock(host,context)

    def test_late_registration_preserves_newest_observation(self):
        host,context,stock=self.stock_fixture()
        for row in (
            SharedResolverTests.row('new-count',100,'2026-11-03T00:00:00Z'),
            SharedResolverTests.row('old-count',200,'2026-11-04T00:00:00Z',observed='2026-11-02T00:00:00Z'),
        ):
            stored=stock.store(context,'config',row['payload'],row['id'])
            self.assertTrue(stored['ok'],stored)
        with host.request(context) as session:
            svc=session._service
            result=stock.calculate(stock.latest(svc,'config'),svc)
        self.assertEqual(100,result['components']['part']['available_quantity'])
        self.assertEqual('new-count',result['selected'][0]['source']['source_ref'])

    def test_omitted_human_report_count_stays_unknown(self):
        self.assertIsNone(self.available_after_omission('2026-11-04T00:00:00Z'))

    def test_late_older_omission_does_not_hide_newer_human_count(self):
        self.assertEqual(100,self.available_after_omission('2026-11-02T00:00:00Z'))

    def available_after_omission(self, omitted_as_of):
        from ontologics.model import canonical
        host,context,stock=self.stock_fixture()
        first=SharedResolverTests.row('first',100,'2026-11-03T00:00:00Z')['payload']
        latest=SharedResolverTests.row('omitted',when=omitted_as_of)['payload']
        latest['scope']['component_lots']={}
        latest['snapshots'][0]['stocks']=[]
        with host.request(context) as session:
            svc=session._service
            for key,payload in (('first',first),('omitted',latest)):
                payload['origin']='human_reported'
                svc.db.execute('INSERT INTO pp_company_stock_snapshots VALUES(%s,%s,%s,%s,%s)',
                               (key,'config',canonical(payload),'fixture',svc.now()))
            result=stock.calculate(stock.latest(svc,'config'),svc)
        return result['components'].get('part',{}).get('available_quantity')

    def test_receipt_reconciliation_uses_canonical_stock_identity(self):
        from ontologics.model import canonical,digest
        for receipt_part,observed_part,ledger,available in (
            ('alias','part','warehouse',100),('part','alias','supply',105),('part','part','warehouse',100),
        ):
            with self.subTest(receipt_part=receipt_part,observed_part=observed_part,ledger=ledger):
                host,context,stock=self.stock_fixture()
                with host.request(context) as session:
                    svc=session._service
                    for member,key in [('part','part'),('alias','part'),('other-alias','other-part')]:
                        svc.db.execute('INSERT INTO pp_inventory_component_links VALUES(%s,%s,%s,%s,%s)',
                                       (member,key,'isolated fixture','fixture',svc.now()))
                baseline=SharedResolverTests.row('base',component='part')
                stored=stock.store(context,'config',baseline['payload'],'base')
                self.assertTrue(stored['ok'],stored)
                with host.request(context) as session:
                    svc=session._service
                    payload=dict(component_id=receipt_part,accepted=5,held=3)
                    now=svc.now()
                    if ledger=='warehouse':
                        svc.db.execute('INSERT INTO pp_warehouse_receipts VALUES(%s,%s,%s,%s,%s,%s,%s)',
                                       ('receipt','purchase','reference',canonical(payload),digest(payload),'fixture',now))
                    else:
                        svc.db.execute('INSERT INTO pp_supply_events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                       ('receipt','receipt','reference','contract',0,'reservation',canonical(payload),digest(payload),'fixture',now))
                update=SharedResolverTests.row('update',105,'2026-11-03T00:00:00Z',component=observed_part)
                saved=stock.store(context,'config',update['payload'],f'update-{receipt_part}-{observed_part}')
                self.assertFalse(saved['ok'],saved)
                self.assertEqual('RECONCILIATION_REQUIRED',saved['errors'][0]['code'])
                with host.request(context) as session:
                    svc=session._service
                    result=stock.calculate(stock.latest(svc,'config'),svc)
                for component in ('part','alias'):
                    count=result['components'][component]
                    self.assertEqual(105,count['available_before_a_reservations'])
                    self.assertEqual(available,count['available_quantity'])
                self.assertEqual('base',result['selected'][0]['source']['source_ref'])
                unrelated=SharedResolverTests.row('other',70,component='other-alias')['payload']
                unrelated['scope']['component_lots']['other-alias']=['other-lot']
                unrelated['snapshots'][0]['stocks'][0]['lot_id']='other-lot'
                saved=stock.store(context,'other-config',unrelated,'other-stock')
                self.assertTrue(saved['ok'],saved)

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
