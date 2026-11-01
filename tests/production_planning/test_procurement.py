"""Prices below are fictional test-only inputs, never OLIMEX offers."""
from copy import deepcopy
from decimal import Decimal, localcontext
import importlib.util
import json
import os
import unittest


def context():
    return dict(evaluated_at='2026-10-28T09:00:00+09:00', data_version=1,
        configuration_id='test-only:configuration', scenario='original', coverage_complete=True,
        rounding={'USD':'0.01','KRW':'1'}, selected_offers={}, costs={})


def requirement(cid='test-only:part', shortage=31):
    return dict(component_id=cid,shortage_quantity=shortage,unit='piece')


def offer(cid='test-only:part', oid='test-only:quote'):
    return dict(component_id=cid,offer_id=oid,supplier_id='test-only:supplier',
        source={'sha256':'a'*64,'locator':'test fixture only / price table','source_ref':'test-only:quote'},
        data_version=1,currency='USD',unit='piece',minimum_order=50,order_multiple=20,
        price_basis_quantity=100,pricing_method='all_units',
        tiers=[{'minimum':0,'maximum':60,'amount':'20.00'},
               {'minimum':60,'maximum':None,'amount':'12.50'}],
        valid_from='2026-10-28T00:00:00Z',valid_until='2026-10-29T00:00:00Z',
        available_quantity=1000,cost_inclusion={'shipping':'excluded','tax':'excluded'})


def zero_costs(c, currency='USD'):
    c['costs'][currency]={k:{'state':'separate','amount':'0'} for k in ('shipping','tax','other')}
    return c


class ProcurementTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.procurement'),'Task06 missing')
        from ontologics.production_planning import procurement
        self.p=procurement

    def estimate(self, req=None, offers=None, ctx=None):
        return self.p.estimate_purchase_cost([requirement()] if req is None else req,
            [offer()] if offers is None else offers,context() if ctx is None else ctx)

    def test_quantity(self):
        for args,expected in [((31,50,20),60),((0,50,20),0),((60,0,20),60),((61,0,20),80)]:
            self.assertEqual(expected,self.p.purchase_quantity(*args))
        for bad in (True,1.0,-1,None,float('nan'),float('inf')):
            for pos in range(3):
                args=[31,50,20];args[pos]=bad
                with self.assertRaises(ValueError):self.p.purchase_quantity(*args)
        with self.assertRaises(ValueError):self.p.purchase_quantity(0,0,0)

    def test_quantity_tier_and_price_basis(self):
        r=self.estimate();i=r['items'][0]
        self.assertEqual((31,60,29,'7.50'),tuple(i[k] for k in ('shortage_quantity','purchase_quantity','excess_quantity','subtotal')))
        self.assertEqual('12.50',i['price']['amount']);self.assertEqual(100,i['price']['basis_quantity'])
        self.assertEqual({'USD':'7.50'},r['known_item_totals']);self.assertIsNone(r['total_by_currency'])
        self.assertEqual('procurement-0.1',r['calculation']['rule_version'])

    def test_missing_offers_conditions_unknown_zero(self):
        r=self.estimate([requirement(shortage=v) for v in []],[])
        self.assertIsNone(r['total_by_currency'])
        for shortage,status,q in [(31,'quote_required',None),(None,'shortage_unknown',None),(0,'not_required',0)]:
            i=self.estimate([requirement(shortage=shortage)],[])['items'][0]
            self.assertEqual((status,q),(i['status'],i['purchase_quantity']))
        for field in ('minimum_order','order_multiple'):
            o=offer();o.pop(field)
            i=self.estimate(offers=[o])['items'][0]
            self.assertIsNone(i['purchase_quantity']);self.assertIn('quantity_conditions_required',i['issues'])

    def test_zero_does_not_hide_invalid_quote(self):
        o=offer();o['order_multiple']=0
        r=self.estimate([requirement(shortage=0)],[o])
        self.assertEqual('not_required',r['items'][0]['status']);self.assertTrue(r['diagnostics'])
        self.assertIsNone(r['total_by_currency'])

    def test_bad_requirements_and_duplicate_ids(self):
        for v in (True,1.0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):self.estimate([requirement(shortage=v)])
        with self.assertRaises(ValueError):self.estimate([requirement(),requirement()])
        r=requirement();r['unit']='box'
        with self.assertRaises(ValueError):self.estimate([r])

    def test_strict_decimal_inputs_and_explicit_zero(self):
        for v in (0.1,True,'NaN','Infinity','-1',Decimal('NaN')):
            o=offer();o['tiers'][1]['amount']=v
            r=self.estimate(offers=[o]);self.assertTrue(r['diagnostics']);self.assertIsNone(r['items'][0]['subtotal'])
        for v in ('0',0,Decimal('0')):
            o=offer();o['tiers'][1]['amount']=v
            self.assertEqual('0.00',self.estimate(offers=[o])['items'][0]['subtotal'])

    def test_decimal_rounding_and_json(self):
        o=offer();o.update(minimum_order=0,order_multiple=1,price_basis_quantity=1)
        o['tiers']=[{'minimum':0,'maximum':None,'amount':Decimal('0.105')}]
        with localcontext() as ctx:
            ctx.prec=6
            r=self.estimate([requirement(shortage=3)],[o],zero_costs(context()))
        self.assertEqual('0.32',r['items'][0]['subtotal']);self.assertEqual({'USD':'0.32'},r['total_by_currency'])
        json.dumps(r,allow_nan=False)
        o['currency']='KRW';self.assertEqual('0',self.estimate([requirement(shortage=3)],[o])['items'][0]['subtotal'])

    def test_tier_gap_overlap_unsupported(self):
        o=offer();o['tiers'][1]['minimum']=61
        r=self.estimate(offers=[o]);self.assertIn('price_tier_gap',r['items'][0]['issues'])
        for mutate in ('overlap','duplicate','progressive','bad_basis','unit'):
            o=offer()
            if mutate=='overlap':o['tiers'][0]['maximum']=61
            elif mutate=='duplicate':o['tiers'].append(deepcopy(o['tiers'][1]))
            elif mutate=='progressive':o['pricing_method']='graduated'
            elif mutate=='bad_basis':o['price_basis_quantity']=0
            else:o['unit']='pack'
            r=self.estimate(offers=[o]);self.assertTrue(r['diagnostics']);self.assertIsNone(r['items'][0]['subtotal'])

    def test_quote_identity_and_selection(self):
        self.assertEqual('quote_required',self.estimate(offers=[offer('test-only:other')])['items'][0]['status'])
        a,b=offer(),offer(oid='test-only:quote2')
        self.assertIn('offer_selection_required',self.estimate(offers=[a,b])['items'][0]['issues'])
        c=context();c['selected_offers']={'test-only:part':b['offer_id']}
        self.assertEqual(b['offer_id'],self.estimate(offers=[a,b],ctx=c)['items'][0]['offer']['offer_id'])
        c['selected_offers']['test-only:part']='missing'
        self.assertIsNone(self.estimate(offers=[a,b],ctx=c)['items'][0]['subtotal'])
        self.assertTrue(self.estimate(offers=[a,a])['diagnostics'])

    def test_quote_provenance_and_version(self):
        for field in ('supplier_id','offer_id','source','data_version'):
            o=offer();o.pop(field)
            self.assertTrue(self.estimate(offers=[o])['diagnostics'])
        for change in ({'sha256':'z'*64},{'locator':''},{'source_ref':''}):
            o=offer();o['source'].update(change);self.assertTrue(self.estimate(offers=[o])['diagnostics'])
        o=offer();o['data_version']=2;self.assertTrue(self.estimate(offers=[o])['diagnostics'])

    def test_time_boundaries(self):
        for at,known in [('2026-10-28T00:00:00Z',True),('2026-10-29T00:00:00Z',False),('2026-10-27T23:59:59Z',False)]:
            c=context();c['evaluated_at']=at;r=self.estimate(ctx=c)
            self.assertEqual(known,r['items'][0]['subtotal'] is not None)
        for field in ('valid_from','valid_until'):
            for val in (None,'2026-10-28','2026-10-28T00:00:00'):
                o=offer();o[field]=val;self.assertTrue(self.estimate(offers=[o])['diagnostics'])
        c=context();c.pop('evaluated_at')
        with self.assertRaises(ValueError):self.estimate(ctx=c)

    def test_supply_separate_from_arithmetic(self):
        for available,state in [(59,'insufficient'),(None,'unknown'),(60,'reported_sufficient')]:
            o=offer();o['available_quantity']=available;r=self.estimate(offers=[o])
            self.assertEqual('7.50',r['items'][0]['subtotal']);self.assertEqual(state,r['items'][0]['supply_state'])
            self.assertFalse(r['purchase_authorized'])

    def test_costs_unknown_explicit_zero_and_included(self):
        self.assertEqual({'USD':'7.50'},self.estimate(ctx=zero_costs(context()))['total_by_currency'])
        c=zero_costs(context());c['costs']['USD']['shipping']['amount']='2.125'
        self.assertEqual({'USD':'9.63'},self.estimate(ctx=c)['total_by_currency'])
        o=offer();o['cost_inclusion']={'shipping':'included','tax':'included'}
        c=zero_costs(context())
        self.assertIsNone(self.estimate(offers=[o],ctx=c)['total_by_currency'])
        for k in ('shipping','tax'):c['costs']['USD'][k]={'state':'included'}
        self.assertEqual({'USD':'7.50'},self.estimate(offers=[o],ctx=c)['total_by_currency'])
        c['costs']['USD']['other']={'state':'unknown'}
        self.assertIsNone(self.estimate(offers=[o],ctx=c)['total_by_currency'])

    def test_mixed_currency_partial_and_coverage(self):
        req=[requirement(),requirement('test-only:second')];o=offer('test-only:second','test-only:quote2');o['currency']='KRW'
        c=zero_costs(zero_costs(context()),'KRW')
        r=self.estimate(req,[offer(),o],c);self.assertEqual({'USD':'7.50','KRW':'8'},r['total_by_currency']);self.assertIsNone(r['grand_total'])
        r=self.estimate(req,[offer()],c);self.assertEqual({'USD':'7.50'},r['known_item_totals']);self.assertIsNone(r['total_by_currency'])
        c['coverage_complete']=False;self.assertIsNone(self.estimate(ctx=c)['total_by_currency'])
        c['exchange_rates']={'USD/KRW':'1400'}
        with self.assertRaises(ValueError):self.estimate(ctx=c)

    def test_maximum_price_magnitude_and_quantity(self):
        o=offer();o.update(minimum_order=0,order_multiple=1,price_basis_quantity=1)
        o['tiers']=[{'minimum':0,'maximum':None,'amount':'999999999999999999999999E+12'}]
        r=self.estimate([requirement(shortage=10**18)],[o])
        self.assertEqual('999999999999999999999999'+'0'*30+'.00',r['items'][0]['subtotal'])

    def test_decimal_context_does_not_inherit_traps_or_exponents(self):
        from decimal import Inexact
        o=offer();o.update(minimum_order=0,order_multiple=1,price_basis_quantity=1)
        o['tiers']=[{'minimum':0,'maximum':None,'amount':'0.105'}]
        with localcontext() as dc:
            dc.traps[Inexact]=True;dc.Emax=1;dc.Emin=-1
            r=self.estimate([requirement(shortage=3)],[o])
        self.assertEqual('0.32',r['items'][0]['subtotal'])

    def test_foreign_extra_cost_is_not_omitted(self):
        c=zero_costs(context());c['costs']['KRW']={'other':{'state':'separate','amount':'15'}}
        r=self.estimate(ctx=c)
        self.assertIsNone(r['total_by_currency'])
        self.assertEqual('15',r['additional_costs']['KRW']['other']['added_amount'])

    def test_no_alias_or_caller_authority(self):
        req=[requirement()];o=[offer()];c=context();c.update(approved=True,confirmed=True)
        before=deepcopy((req,o,c));r=self.estimate(req,o,c);again=self.estimate(req,o,c)
        r['items'][0]['offer']['source']['locator']='modified'
        self.assertEqual(before,(req,o,c));self.assertEqual(again,self.estimate(req,o,c));self.assertFalse(again['purchase_authorized'])


class ProcurementScenarioTests(unittest.TestCase):
    def setUp(self):
        from ontologics.production_planning import procurement
        self.assertTrue(hasattr(procurement,'build_procurement_review'),'Scenario adapter missing')
        self.p=procurement
        from tests.production_planning.test_materials import fixture,ROOT,CONFIG
        from ontologics.production_planning.materials import available_stock,calculate_material_requirements
        self.doc,scope,_=fixture()
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        rows=[n for n in nodes if n.get('type')=='BOMItem' and n['configuration_id']==CONFIG]
        self.m=calculate_material_requirements(rows,available_stock([self.doc],scope),100,
            configuration_id=CONFIG,data_version=1,demand_source={'state':'unconfirmed'},bom_complete=True)
        self.c=context();self.c['configuration_id']=CONFIG

    def test_original_unknowns_and_task05_binding(self):
        from ontologics.production_planning.substitution import build_substitution_review,U2
        sub=build_substitution_review(self.m,[],[],[])
        r=self.p.build_procurement_review(self.m,[],self.c,scenario='original',substitution_review=sub)
        self.assertEqual([30,240],sorted(i['shortage_quantity'] for i in r['items'] if i['status']=='quote_required'))
        self.assertEqual(71,sum(i['shortage_quantity'] is None for i in r['items']))
        self.assertEqual(0,next(i['purchase_quantity'] for i in r['items'] if i['shortage_quantity']==0))
        self.assertEqual(sub['purchase_review'],r['purchase_review']);self.assertNotIn(U2,[x['component_id'] for x in r['purchase_review']])
        self.assertIsNone(r['total_by_currency']);self.assertIsNone(r['materials']['whole_bom_material_limit'])
        bad=deepcopy(sub);bad['purchase_review'][0]['shortage_quantity']+=1
        with self.assertRaises(ValueError):self.p.build_procurement_review(self.m,[],self.c,scenario='original',substitution_review=bad)

    def test_virtual_is_selected_not_added_and_not_new_stock(self):
        from tests.production_planning.test_planning import demand_for
        from ontologics.production_planning.planning import build_production_plan
        plan=build_production_plan(demand_for(self.m),self.m,70)
        before=deepcopy(plan);c=deepcopy(self.c);c['scenario']='virtual_remaining'
        r=self.p.build_procurement_review(plan,[],c,scenario='virtual_remaining')
        self.assertEqual([30,240],sorted(x['shortage_quantity'] for x in r['purchase_review']))
        self.assertEqual('virtual',r['materials']['stock']['state']);self.assertEqual(before,plan)
        self.assertEqual(71,sum(i['shortage_quantity'] is None for i in r['items']))
        r['materials']['stock']['original_stock']['scope']['company']='mutated'
        self.assertEqual(before,plan)
        for m in (plan['remaining_materials'],dict(plan['remaining_materials'],purpose='material_review_only')):
            with self.assertRaises(ValueError):self.p.build_procurement_review(m,[],self.c,scenario='original')
        bad=deepcopy(plan);bad['remaining_materials']['components'][0]['S']=0
        with self.assertRaises(ValueError):self.p.build_procurement_review(bad,[],c,scenario='virtual_remaining')

    def test_errors_and_conflicts_preserved_not_free_total(self):
        self.m['errors']=[{'code':'test-error'}];self.m['conflicts']=[{'code':'test-conflict'}]
        r=self.p.build_procurement_review(self.m,[],self.c,scenario='original')
        self.assertEqual(self.m,r['materials']);self.assertTrue(r['upstream_diagnostics']['errors'])
        self.assertIsNone(r['total_by_currency']);self.assertFalse(r['purchase_authorized'])

    def test_binding_and_mutated_shortage_rejected(self):
        for key,value in [('data_version',2),('configuration_id','different'),('scenario','virtual_remaining')]:
            c=deepcopy(self.c);c[key]=value
            with self.assertRaises(ValueError):self.p.build_procurement_review(self.m,[],c,scenario='original')
        self.m['components'][0]['S']=123
        with self.assertRaises(ValueError):self.p.build_procurement_review(self.m,[],self.c,scenario='original')


from tests.production_planning.test_materials import MaterialsPostgresTests as _MaterialsPG

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Explicit disposable PG required')
class ProcurementPostgresTests(unittest.TestCase):
    setUp=_MaterialsPG.setUp

    def call(self,**kw):
        from ontologics.production_planning import procurement
        self.assertTrue(hasattr(procurement,'build_saved_procurement_review'),'Saved adapter missing')
        c=context();c['configuration_id']='config:esp32-poe:L:WROOM'
        args=dict(demand_id=self.saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
            snapshots=[self.doc],scope=self.scope,scenario='original',offers=[],cost_context=c)
        args.update(kw)
        return procurement.build_saved_procurement_review(self.workflow,self.host,self.reader,**args)

    def test_fresh_unconfirmed_reconnection_and_no_writes(self):
        from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow
        self.workflow=PersistentDemandConfirmationWorkflow(self.host,self.identities,self.settings)
        before=self.workflow.get(self.reader,self.saved['demand_id'])['data'];doc=deepcopy(self.doc)
        with self.host.request(self.reader) as s:graph=s.call('lookup',{'version':1})['data']
        r=self.call();review=r['procurement']
        self.assertEqual([30,240],sorted(x['shortage_quantity'] for x in review['purchase_review']))
        self.assertFalse(review['purchase_authorized']);self.assertEqual(71,sum(i['shortage_quantity'] is None for i in review['items']))
        self.assertEqual(before['target_sha256'],review['materials']['demand_source']['target_sha256'])
        self.assertEqual('temporal-0.1',r['query']['lookup_response']['rule_version']);self.assertTrue(r['query']['lookup_response']['run_id'])
        self.assertEqual(before,self.workflow.get(self.reader,self.saved['demand_id'])['data']);self.assertEqual([],before['events'])
        self.assertFalse(before['confirmation']['verified']);self.assertEqual(doc,self.doc)
        with self.host.request(self.reader) as s:self.assertEqual(graph,s.call('lookup',{'version':1})['data'])

    def test_current_revision_hash_public_version(self):
        self.assertIsNone(self.call(expected_target_sha256='0'*64)['procurement'])
        self.assertIsNone(self.call(data_version=2)['procurement'])
        self.interpreted['items'][0]['proposed_configuration_id']='config:esp32-poe:L:WROOM'
        target=self.preflight.prepare(self.author,self.path,self.interpreted,data_version=1)['target']
        response=self.workflow.save(self.author,target,demand_id=self.saved['demand_id'],expected_revision=1,request_key='procurement-revise')
        self.assertTrue(response['ok']);self.assertEqual(2,response['data']['revision'])
        self.assertIsNone(self.call(expected_revision=1)['procurement'])
        self.assertEqual(2,self.call()['procurement']['materials']['demand_source']['revision'])

    def test_permission_and_public_failure_preview(self):
        from unittest.mock import patch
        from ontologics.host import RequestSession
        original=RequestSession.call
        for mode in ('failed','preview','version'):
            def changed(session,tool,args):
                r=original(session,tool,args)
                if tool=='lookup':
                    if mode=='failed':r.update(ok=False,data=None)
                    elif mode=='preview':r['is_preview']=True
                    else:r['data_version']=999
                return r
            with patch.object(RequestSession,'call',changed):self.assertIsNone(self.call()['procurement'],mode)
        from ontologics.identity import UserIdentity
        from ontologics.service import DomainError
        self.identities.users=[u if u.subject!='reader' else UserIdentity('reader',frozenset()) for u in self.identities.users]
        try:r=self.call()
        except DomainError as exc:self.assertEqual('FORBIDDEN',exc.code)
        else:self.assertIsNone(r['procurement'])

    def test_virtual_host_path_and_internal_stock_guard(self):
        c=context();c.update(configuration_id='config:esp32-poe:L:WROOM',scenario='virtual_remaining')
        r=self.call(scenario='virtual_remaining',initial_quantity=70,cost_context=c)
        self.assertEqual('virtual_material_review_only',r['procurement']['materials']['purpose'])
        for key,value in [('state','virtual'),('purpose','virtual_material_review_only'),('original_stock',{}),('virtual_deductions',[])]:
            doc=deepcopy(self.doc);doc[key]=value
            with self.assertRaises(ValueError):self.call(snapshots=[doc])

    def test_example(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.production_planning_procurement'),'Task06 example missing')
        from examples.production_planning_procurement import run_example
        r=run_example();self.assertEqual(0,r['model_api_calls']);self.assertEqual(0,r['demand_event_count'])
        self.assertFalse(r['confirmation_verified']);self.assertEqual(71,r['unknown_stock_components'])
        for scenario in ('original','virtual_remaining'):
            self.assertEqual([30,240],sorted(i['shortage_quantity'] for i in r[scenario]['purchase_review']))
            self.assertIsNone(r[scenario]['total_by_currency'])


del _MaterialsPG
