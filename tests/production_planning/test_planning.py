"""Virtual planning boundaries: literal expectations, real Task03 arithmetic."""
from copy import deepcopy
import importlib.util
import json
import unittest
from tests.production_planning.test_materials import fixture, ROOT, CONFIG
from ontologics.production_planning.materials import available_stock, calculate_material_requirements


def demand_for(m):
    return dict(configuration_id=m['configuration_id'], data_version=m['data_version'],
                planned_input_quantity=m['planned_input_quantity'], unit='piece',
                target_quantity=100, target_quantity_basis='ordered_quantity',
                demand_source=deepcopy(m['demand_source']))


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.planning'), 'Task04 missing')
        from ontologics.production_planning.planning import build_production_plan
        self.build=build_production_plan
        self.doc,self.scope,self.items=fixture()

    def materials(self,n=100,items=None):
        return calculate_material_requirements(self.items if items is None else items,
            available_stock([self.doc],self.scope),n,configuration_id=CONFIG,data_version=1,
            demand_source={'state':'unconfirmed','quantity_basis':{'state':'supported','value':n}},bom_complete=True)

    def plan(self,i=70,n=100,items=None):
        m=self.materials(n,items);return self.build(demand_for(m),m,i)

    def test_seventy_thirty(self):
        r=self.plan(); self.assertEqual((100,70,30,'partial'),tuple(r[k] for k in ('planned_input_quantity','initial_quantity','remaining_quantity','quantity_class')))
        expected={'55':(70,0,30,30),'9':(560,0,240,240),'14':(770,330,330,0)}
        for c in r['components']:
            key=c['bom_rows'][0]['id'].split('-')[-1]
            self.assertEqual(expected[key],tuple(c[k] for k in ('virtual_used_quantity','remaining_available_quantity','remaining_required_quantity','remaining_shortage')))
            self.assertEqual(c['virtual_used_quantity'],sum(l['used_quantity'] for l in c['lots']))
        self.assertEqual('production-0.1',r['calculation']['rule_version'])
        self.assertEqual('materials-0.1',r['input_materials']['calculation']['rule_version'])
        self.assertFalse(r['production_authorized']);self.assertEqual('unverified',r['confirmation_status'])

    def test_full_zero_and_zero_demand(self):
        self.assertEqual('full',self.plan(60,60)['quantity_class'])
        for n in (0,100):
            r=self.plan(0,n);self.assertEqual('zero',r['quantity_class'])
            for c in r['components']:
                self.assertEqual(0,c['virtual_used_quantity']);self.assertEqual(c['A'],c['remaining_available_quantity']);self.assertEqual(c['S'],c['remaining_shortage'])

    def test_explicit_quantities_not_clamped(self):
        for i in (71,100,101,-1,True,1.0,float('nan'),float('inf'),None):
            with self.subTest(i=i),self.assertRaises(ValueError):self.plan(i)
        m=self.materials()
        for n in (True,1.0,-1,None,float('inf')):
            d=demand_for(m);d['planned_input_quantity']=n
            with self.assertRaises(ValueError):self.build(d,m,0)

    def test_missing_unknown_and_empty_bom(self):
        self.doc['stocks']=[]
        r=self.plan();self.assertIsNone(r['conditional_upper_bound']);self.assertEqual('conditional',r['review_status'])
        for c in r['components']:self.assertIsNone(c['virtual_used_quantity']);self.assertIsNone(c['remaining_shortage'])
        self.assertIsNone(self.plan(items=[])['conditional_upper_bound'])
        self.doc,self.scope,self.items=fixture();self.items[0]['population']='unknown'
        c=next(c for c in self.plan(0)['components'] if c['component_id']==self.items[0]['component_id'])
        self.assertIsNone(c['virtual_used_quantity']);self.assertIsNone(c['remaining_required_quantity'])

    def test_zero_bound_not_unknown(self):
        self.doc['stocks'][0]['on_hand_quantity']=30
        self.assertEqual(0,self.plan(0)['conditional_upper_bound'])
        with self.assertRaises(ValueError):self.plan(1)

    def test_conflicts_and_invalid_inputs_are_blocked(self):
        for kind in ('stock','bom'):
            self.doc,self.scope,self.items=fixture()
            if kind=='stock':
                row=deepcopy(self.doc['stocks'][0]);row['on_hand_quantity']+=1;self.doc['stocks'].append(row)
            else:
                row=deepcopy(self.items[0]);row['quantity']+=1;self.items.append(row)
            with self.assertRaises(ValueError):self.plan(0)

    def test_multiple_rows_exact_ids(self):
        row=deepcopy(self.items[0]);row['id']+=':second';self.items.append(row)
        r=self.plan(10);c=next(c for c in r['components'] if c['component_id']==row['component_id'])
        self.assertEqual(2*row['quantity']*10,c['virtual_used_quantity'])
        row['component_id']='different:exact-id'
        r=self.plan(10);self.assertEqual(4,len(r['components']))
        self.assertIsNone(next(c for c in r['components'] if c['component_id']==row['component_id'])['virtual_used_quantity'])

    def test_lot_order_partial_use_duplicates_and_exclusions(self):
        row=deepcopy(self.doc['stocks'][0]);row.update(id='extra',lot_id='aaa',availability_basis='reported_available',available_quantity=20)
        self.doc['stocks'] += [row,deepcopy(row)]
        self.scope['component_lots'][row['component_id']].append('aaa')
        future=deepcopy(row);future.update(id='future',lot_id='future',as_of='2027-01-01T00:00:00Z',available_quantity=9999)
        self.doc['stocks'].append(future)
        first=self.plan(30);self.doc['stocks'].reverse();second=self.plan(30)
        cid=row['component_id'];a=next(c for c in first['components'] if c['component_id']==cid);b=next(c for c in second['components'] if c['component_id']==cid)
        self.assertEqual([('aaa',20),('lot:planning:lan8710:001',10)],[(l['lot_id'],l['used_quantity']) for l in a['lots']])
        self.assertEqual([(l['lot_id'],l['used_quantity']) for l in a['lots']],[(l['lot_id'],l['used_quantity']) for l in b['lots']])
        for l in a['lots']:self.assertLessEqual(l['used_quantity'],l['available_quantity'])

    def test_reported_available_not_rededucted(self):
        self.doc['stocks'][0].update(availability_basis='reported_available',available_quantity=70)
        c=next(c for c in self.plan()['components'] if c['q']==1)
        self.assertEqual(0,c['remaining_available_quantity'])

    def test_binding_and_modified_calculation_rejected(self):
        m=self.materials()
        for key,value in [('configuration_id','other'),('data_version',2),('unit','kg'),('planned_input_quantity',99),('demand_source',{'state':'confirmed'})]:
            d=demand_for(m);d[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.build(d,m,0)
        for key in ('A','q','D','S'):
            bad=deepcopy(m);bad['components'][0][key]+=1
            with self.subTest(key=key),self.assertRaises(ValueError):self.build(demand_for(bad),bad,0)
        bad=deepcopy(m);bad['stock']['selected'][0]['available_quantity']+=1
        with self.assertRaises(ValueError):self.build(demand_for(bad),bad,0)

    def test_no_input_output_aliases_or_double_consumption(self):
        m=self.materials();d=demand_for(m);before=deepcopy((d,m))
        a=self.build(d,m,70);b=self.build(d,m,20);again=self.build(d,m,70)
        self.assertEqual(a,again);self.assertEqual(before,(d,m))
        a['input_materials']['stock']['selected'][0]['observation']['reserved_quantity']=999
        a['demand']['demand_source']['state']='changed'
        self.assertEqual(before,(d,m));self.assertEqual(again,self.build(d,m,70))
        self.assertEqual(80,b['remaining_quantity'])
        with self.assertRaises(ValueError):self.build(demand_for(again['remaining_materials']),again['remaining_materials'],0)

    def test_full_bom_retains_unknowns(self):
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        items=[n for n in nodes if n.get('type')=='BOMItem' and n['configuration_id']==CONFIG]
        r=self.plan(items=items)
        self.assertEqual(74,len(r['components']));self.assertEqual(71,sum(c['A'] is None for c in r['components']))
        self.assertIsNone(r['whole_bom_material_limit']);self.assertEqual(70,r['conditional_upper_bound'])
        self.assertEqual(11,sum(c['q'] is None for c in r['components']))
        self.assertTrue(r['remaining_materials']['unresolved'])

    def test_cleared_diagnostics_cannot_hide_invalid_bom(self):
        self.items[0]['unit']='kg'
        m=self.materials();m['errors']=[];m['valid']=True
        with self.assertRaises(ValueError):self.build(demand_for(m),m,10)

    def test_removed_unknown_diagnostics_rejected(self):
        self.items[0]['population']='unknown'
        m=self.materials();m['unresolved']=[];m['questions']=[]
        with self.assertRaises(ValueError):self.build(demand_for(m),m,0)

    def test_forged_confirmation_does_not_issue_authority(self):
        m=self.materials();m['demand_source'].update(state='confirmed',confirmation={'verified':True})
        r=self.build(demand_for(m),m,60)
        self.assertEqual('unverified',r['confirmation_status'])
        self.assertEqual('conditional',r['review_status'])
        self.assertFalse(r['production_authorized'])

    def test_internally_inconsistent_provenance_rejected(self):
        for update in ({'data_version':2},{'revision':1,'current_revision':2},
                       {'status':'superseded'},{'quantity_basis':{'state':'supported','value':99}}):
            m=self.materials();m['demand_source'].update(update)
            with self.subTest(update=update),self.assertRaises(ValueError):self.build(demand_for(m),m,0)


import os
from tests.production_planning.test_materials import MaterialsPostgresTests as _MaterialsPG

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit disposable PG required')
class PlanningPostgresTests(unittest.TestCase):
    setUp = _MaterialsPG.setUp

    def call(self,**kw):
        from ontologics.production_planning import planning
        self.assertTrue(hasattr(planning,'build_saved_production_plan'),'Fresh host planning missing')
        args=dict(demand_id=self.saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
                  snapshots=[self.doc],scope=self.scope,initial_quantity=70)
        args.update(kw)
        return planning.build_saved_production_plan(self.workflow,self.host,self.reader,**args)

    def test_fresh_saved_plan_preserves_graph_demand_and_envelopes(self):
        from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow
        self.workflow=PersistentDemandConfirmationWorkflow(self.host,self.identities,self.settings)
        before=self.workflow.get(self.reader,self.saved['demand_id'])['data']
        with self.host.request(self.reader) as session:graph=session.call('lookup',{'version':1})['data']
        original=deepcopy(self.doc)
        r=self.call(review_state='selected');p=r['plan']
        self.assertEqual(30,p['remaining_quantity']);self.assertEqual('partial',p['quantity_class'])
        self.assertEqual('unverified',p['confirmation_status']);self.assertFalse(p['production_authorized'])
        self.assertEqual('temporal-0.1',r['query']['lookup_response']['rule_version'])
        self.assertTrue(r['query']['lookup_response']['run_id']);self.assertEqual(1,r['query']['lookup_response']['data_version'])
        self.assertEqual('materials-0.1',r['calculation']['calculation']['rule_version'])
        self.assertEqual('production-0.1',p['calculation']['rule_version'])
        self.assertEqual(before,self.workflow.get(self.reader,self.saved['demand_id'])['data'])
        with self.host.request(self.reader) as session:self.assertEqual(graph,session.call('lookup',{'version':1})['data'])
        self.assertEqual(original,self.doc);self.assertEqual([],before['events'])
        self.assertEqual(before['target_sha256'],p['demand']['demand_source']['target_sha256'])
        self.assertEqual(71,sum(c['A'] is None for c in p['components']))

    def test_revisions_hash_and_versions_rechecked(self):
        old=self.call()['plan']
        self.assertIsNone(self.call(expected_target_sha256='0'*64)['plan'])
        self.assertIsNone(self.call(data_version=2)['plan'])
        self.interpreted['items'][0]['proposed_configuration_id']=CONFIG
        target=self.preflight.prepare(self.author,self.path,self.interpreted,data_version=1)['target']
        self.workflow.save(self.author,target,demand_id=self.saved['demand_id'],expected_revision=1,request_key='planning-revise')
        self.assertIsNone(self.call(expected_revision=1)['plan'])
        self.assertEqual(2,self.call()['plan']['demand']['demand_source']['revision'])
        self.assertEqual(1,old['demand']['demand_source']['revision'])

    def test_current_permission_rechecked(self):
        from ontologics.identity import UserIdentity
        from ontologics.service import DomainError
        self.call()
        self.identities.users=[u if u.subject!='reader' else UserIdentity('reader',frozenset()) for u in self.identities.users]
        try:r=self.call()
        except DomainError as e:self.assertEqual('FORBIDDEN',e.code)
        else:self.assertIsNone(r['plan']);self.assertFalse(r['demand_response']['ok'])

    def test_public_failure_and_lot_relation_block_plan(self):
        from unittest.mock import patch
        from ontologics.host import RequestSession
        original=RequestSession.call
        def fail(session,tool,args):
            r=original(session,tool,args)
            if tool=='lookup':r.update(ok=False,data=None)
            return r
        with patch.object(RequestSession,'call',fail):
            r=self.call();self.assertIsNone(r['plan']);self.assertFalse(r['demand_response']['ok'])
        self.doc['stocks'][0]['lot_id']='unregistered'
        self.assertIsNone(self.call()['plan'])

    def test_example(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.production_planning_plan'),'Task04 example missing')
        from examples.production_planning_plan import run_example
        r=run_example();self.assertEqual(30,r['selected']['remaining_quantity'])
        self.assertEqual(0,r['demand_event_count']);self.assertFalse(r['confirmation_verified'])
        self.assertEqual(71,r['unknown_stock_components'])

# Avoid unittest collecting an imported TestCase twice.
del _MaterialsPG
