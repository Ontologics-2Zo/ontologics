"""Material arithmetic and observation boundaries; original inputs stay untouched."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = 'config:esp32-poe:L:WROOM'

def fixture():
    doc = json.loads((ROOT/'data/production_planning/stock.json').read_text())
    doc['source_ref'] = 'input:planning:stock'
    import hashlib
    doc['sha256'] = hashlib.sha256((ROOT/'data/production_planning/stock.json').read_bytes()).hexdigest()
    scope = dict(doc['stock_scope'], unit='piece', component_lots={r['component_id']: [r['lot_id']] for r in doc['stocks']},
                 coverage_basis='Explicit three-row development test assumption, not actual stock coverage')
    nodes = json.loads((ROOT/'data/catalog.json').read_text())['nodes']
    items = [n for n in nodes if n['id'] in {r['bom_item_id'] for r in doc['stocks']}]
    return doc, scope, items

class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.materials'), 'Task03 implementation is missing')
        from ontologics.production_planning import materials
        self.m = materials
        self.doc, self.scope, self.items = fixture()

    def stock(self, doc=None, scope=None):
        return self.m.available_stock([doc or self.doc], scope or self.scope)

    def calc(self, items=None, stock=None, n=100, **kw):
        return self.m.calculate_material_requirements(self.items if items is None else items,
            stock or self.stock(), n, configuration_id=CONFIG, data_version=1,
            bom_complete=kw.get('bom_complete', True), demand_source={'state':'unconfirmed','basis':'explicit test input'})

    def test_real_three_rows_and_immutability(self):
        before = deepcopy((self.doc,self.scope,self.items))
        r = self.calc()
        expected = {'55':(1,100,70,30),'9':(8,800,560,240),'14':(11,1100,1100,0)}
        for row in r['components']:
            key=row['bom_rows'][0]['id'].split('-')[-1]
            self.assertEqual(expected[key], tuple(row[k] for k in ('q','D','A','S')))
        self.assertEqual(70,r['conditional_upper_bound'])
        self.assertEqual('materials-0.1',r['calculation']['rule_version'])
        self.assertEqual(before,(self.doc,self.scope,self.items))

    def test_full_zero_and_no_required_components(self):
        self.assertEqual(10,self.calc(n=10)['conditional_upper_bound'])
        self.assertEqual(0,self.calc(n=0)['conditional_upper_bound'])
        self.doc['stocks'][0].update(on_hand_quantity=30)
        self.assertEqual(0,self.calc()['conditional_upper_bound'])
        self.assertIsNone(self.calc(items=[])['conditional_upper_bound'])

    def test_missing_stock_and_partial_coverage_are_not_zero(self):
        self.doc['stocks'].pop()
        r=self.calc()
        self.assertIsNone(r['whole_bom_material_limit'])
        self.assertTrue(any(x['A'] is None and x['S'] is None for x in r['components']))
        self.scope['component_lots']={}
        self.assertIsNone(self.calc()['conditional_upper_bound'])

    def test_reported_available_is_not_deducted_twice(self):
        self.doc['stocks'][0].update(availability_basis='reported_available', available_quantity=70)
        self.assertEqual(70,self.stock()['components'][self.doc['stocks'][0]['component_id']]['available_quantity'])

    def test_bad_quantities_and_deductions(self):
        for value in (True,1.0,-1,float('nan'),float('inf'),'2'):
            with self.subTest(value=value):
                doc=deepcopy(self.doc); doc['stocks'][0]['on_hand_quantity']=value
                self.assertTrue(self.stock(doc)['errors'])
                with self.assertRaises(ValueError): self.calc(n=value)
        for updates in ({'deductions_disjoint':False},{'reserved_quantity':101}):
            doc=deepcopy(self.doc); doc['stocks'][0].update(updates)
            self.assertTrue(self.stock(doc)['errors'])

    def test_unknown_and_missing_values_are_distinct_from_errors(self):
        for updates in ({'quantity_state':'unknown'},{'on_hand_quantity':None}):
            doc=deepcopy(self.doc); doc['stocks'][0].update(updates)
            s=self.stock(doc)
            self.assertFalse(s['errors']); self.assertTrue(s['unresolved'])
            self.assertIsNone(s['components'][doc['stocks'][0]['component_id']]['available_quantity'])

    def test_latest_past_future_offsets_and_duplicate_observations(self):
        older=deepcopy(self.doc); older['stock_scope']['as_of']='2026-10-25T00:00:00Z'
        for row in older['stocks']: row['id']+=':older'
        future=deepcopy(self.doc); future['stock_scope']['as_of']='2026-10-27T00:00:00Z'
        for row in future['stocks']: row['id']+=':future'
        same=deepcopy(self.doc); same['stock_scope']['as_of']='2026-10-26T00:00:00Z'
        s=self.m.available_stock([older,self.doc,future,same],self.scope)
        self.assertFalse(s['conflicts']); self.assertEqual(3,len(s['selected']))
        self.assertEqual({'older_observation','future_observation','duplicate_observation'}, {x['reason'] for x in s['excluded']})
        self.scope['as_of']='2026-10-26T01:00:00Z'
        self.assertEqual(3600,self.stock()['selected'][0]['age_seconds'])

    def test_id_time_and_lot_conflicts_are_order_independent(self):
        for mutation in ('id','time','lot'):
            other=deepcopy(self.doc)
            other['stocks']=other['stocks'][:1]
            row=other['stocks'][0]
            if mutation=='id': row['on_hand_quantity']=101
            if mutation=='time': row.update(id='different',on_hand_quantity=101)
            if mutation=='lot': row.update(id='different',component_id=self.doc['stocks'][1]['component_id'])
            for docs in ([self.doc,other],[other,self.doc]):
                s=self.m.available_stock(docs,self.scope)
                self.assertTrue(s['conflicts'])
                self.assertIsNone(s['components'][self.doc['stocks'][0]['component_id']]['available_quantity'])

    def test_scope_unit_and_naive_time(self):
        for key,value in [('company','elsewhere'),('location','elsewhere'),('as_of','2027-01-01T00:00:00Z')]:
            doc=deepcopy(self.doc); doc['stock_scope'][key]=value
            self.assertFalse(self.stock(doc)['selected'])
        doc=deepcopy(self.doc); doc['stocks'][0]['unit']='kg'
        self.assertTrue(self.stock(doc)['errors'])
        with self.assertRaises(ValueError): self.stock(scope=dict(self.scope,as_of='2026-10-26'))

    def test_multiple_lots_need_explicit_complete_scope(self):
        row=deepcopy(self.doc['stocks'][0]); row.update(id='new',lot_id='newlot')
        self.doc['stocks'].append(row)
        self.assertIsNone(self.stock()['components'][row['component_id']]['available_quantity'])
        self.scope['component_lots'][row['component_id']].append('newlot')
        self.assertEqual(140,self.stock()['components'][row['component_id']]['available_quantity'])

    def test_bom_aggregation_duplicate_conflict_and_unknown(self):
        row=deepcopy(self.items[0]); row['id']+=':second'
        r=self.calc(items=[self.items[0],row])
        self.assertEqual(self.items[0]['quantity']*2,r['components'][0]['q'])
        self.assertEqual(self.items[0]['quantity'], self.calc(items=[row,row])['components'][0]['q'])
        other=deepcopy(row);other['quantity']+=1
        self.assertTrue(self.calc(items=[row,other])['conflicts'])
        row['population']='unknown'
        r=self.calc(items=[self.items[0],row])
        self.assertIsNone(r['components'][0]['q']);self.assertIsNone(r['components'][0]['S'])
        self.assertIsNone(r['conditional_upper_bound'])
        row['population']='not_populated'
        self.assertTrue(self.calc(items=[row])['excluded_bom'])

    def test_different_ids_not_merged_and_bad_bom_rejected(self):
        row=deepcopy(self.items[0]); row.update(id='other',component_id='similar-name-different-id')
        self.assertEqual(2,len(self.calc(items=[self.items[0],row])['components']))
        for updates in ({'quantity':True},{'quantity':1.5},{'quantity':-1},{'unit':'kg'},{'configuration_id':'other'}):
            row=deepcopy(self.items[0]);row.update(updates)
            self.assertTrue(self.calc(items=[row])['errors'])

    def test_whole_bom_never_claims_seventy(self):
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        items=[n for n in nodes if n.get('type')=='BOMItem' and n['configuration_id']==CONFIG]
        r=self.calc(items=items)
        self.assertEqual(70,r['conditional_upper_bound'])
        self.assertIsNone(r['whole_bom_material_limit'])
        self.assertTrue(r['unresolved'])

    def test_missing_observation_subtotal_is_unknown_not_zero(self):
        self.doc['stocks']=[]
        s=self.stock()
        for c in s['components'].values():
            self.assertIsNone(c['observed_available_quantity'])

    def test_bad_quantity_even_in_old_or_future_observation(self):
        for when in ('2026-10-25T00:00:00Z','2026-10-27T00:00:00Z'):
            doc=deepcopy(self.doc);doc['stock_scope']['as_of']=when
            for row in doc['stocks']: row['id']+=':other'
            doc['stocks'][0]['on_hand_quantity']=True
            self.assertTrue(self.m.available_stock([doc,self.doc],self.scope)['errors'])

    def test_duplicate_id_in_other_scope_is_still_a_conflict(self):
        doc=deepcopy(self.doc);doc['stock_scope']['location']='other-place'
        s=self.m.available_stock([self.doc,doc],self.scope)
        self.assertTrue(s['conflicts'])

    def test_conflicting_selection_does_not_publish_known_amount(self):
        doc=deepcopy(self.doc);doc['stocks'][0]['on_hand_quantity']=101
        s=self.m.available_stock([self.doc,doc],self.scope)
        cid=doc['stocks'][0]['component_id']
        for row in s['selected']:
            if row['observation']['component_id']==cid:
                self.assertIsNone(row['available_quantity'])

    def test_invalid_unit_or_time_does_not_hide_identity_collision(self):
        for updates in ({'unit':'kg'},{'as_of':'not-a-timestamp'}):
            other=deepcopy(self.doc);other['stocks']=other['stocks'][:1]
            other['stocks'][0].update(component_id='contradicting-component',**updates)
            for docs in ([self.doc,other],[other,self.doc]):
                s=self.m.available_stock(docs,self.scope)
                self.assertTrue(s['conflicts'])
                self.assertIsNone(s['components'][self.doc['stocks'][0]['component_id']]['available_quantity'])

    def test_conflicting_observation_reason_is_not_corroboration(self):
        other=deepcopy(self.doc);other['stocks']=other['stocks'][:1]
        other['stocks'][0].update(id='different',on_hand_quantity=101)
        s=self.m.available_stock([self.doc,other],self.scope)
        self.assertIn('conflicting_observation',{x['reason'] for x in s['excluded']})
        self.assertNotIn('corroborating_observation',{x['reason'] for x in s['excluded']})

import os

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit disposable PG required')
class MaterialsPostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.db import database_for_test
        from tests.production_planning.test_demand_confirmation import MutableIdentities
        from ontologics.production_planning.host import PlanningHost as CommonHost
        from ontologics.identity import DevelopmentContext
        from ontologics.production_planning.persistence import PlanningService as Service
        from ontologics.production_planning.demand_confirmation import DemandConfirmationWorkflow
        from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow
        from ontologics.production_planning.contract_intake import extract_contract_text
        from examples.production_planning_contract_intake import reference_interpretation
        from ontologics.production_planning import materials
        self.assertTrue(hasattr(materials,'calculate_saved_demand'),'Host handoff is missing')
        self.m=materials;self.settings=database_for_test(self);self.identities=MutableIdentities()
        self.host=CommonHost(self.identities,self.settings)
        self.reader=DevelopmentContext('reader');self.author=DevelopmentContext('author')
        seed=Service(self.settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        try:
            nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
            nodes+=json.loads((ROOT/'data/production_planning/graph_nodes_v1.json').read_text())['nodes']
            response=seed.bootstrap('fixture',nodes,request_key='materials-seed')
            self.assertTrue(response['ok'],response)
        finally: seed.close()
        self.workflow=PersistentDemandConfirmationWorkflow(self.host,self.identities,self.settings)
        self.preflight=DemandConfirmationWorkflow(self.host,self.identities)
        self.path=ROOT/'data/production_planning/contract.txt'
        self.interpreted=reference_interpretation(extract_contract_text(self.path),data_version=1)
        target=self.preflight.prepare(self.author,self.path,self.interpreted,data_version=1)['target']
        self.saved=self.workflow.save(self.author,target,request_key='materials-save')['data']
        self.doc,self.scope,self.items=fixture()

    def call(self,**kwargs):
        args=dict(demand_id=self.saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
                  snapshots=[self.doc],scope=self.scope)
        args.update(kwargs)
        return self.m.calculate_saved_demand(self.workflow,self.host,self.reader,**args)

    def test_public_exact_bom_lots_and_unconfirmed_persisted_demand(self):
        r=self.call();self.assertFalse(r['errors'],r)
        calc=r['calculation']; self.assertEqual(70,calc['conditional_upper_bound'])
        self.assertIsNone(calc['whole_bom_material_limit'])
        self.assertFalse(calc['demand_source']['confirmation']['verified'])
        self.assertEqual([],r['demand_response']['data']['events'])
        self.assertEqual(1,calc['data_version'])
        self.assertEqual('temporal-0.1',r['query']['lookup_response']['rule_version'])
        self.assertEqual(64,len(calc['demand_source']['source']['sha256']))
        self.assertEqual('supported',calc['demand_source']['quantity_basis']['state'])
        self.assertEqual('2026-11-10',r['demand_response']['data']['items'][0]['contract_item_candidate']['delivery_due'])
        self.assertEqual(11,sum(x['code']=='UNKNOWN_POPULATION' for x in calc['unresolved']))

    def test_version_and_lot_mismatch_do_not_become_empty_success(self):
        self.assertIsNone(self.call(data_version=2)['calculation'])
        self.doc['stocks'][0]['lot_id']='missing:lot'
        self.assertIsNone(self.call()['calculation'])

    def test_current_revision_reloaded_and_old_revision_rejected(self):
        self.interpreted['items'][0]['proposed_configuration_id']=CONFIG
        target=self.preflight.prepare(self.author,self.path,self.interpreted,data_version=1)['target']
        self.workflow.save(self.author,target,demand_id=self.saved['demand_id'],expected_revision=1,request_key='revise')
        self.assertIsNone(self.call(expected_revision=1)['calculation'])
        self.assertEqual(2,self.call()['calculation']['demand_source']['revision'])

    def test_current_read_permission_is_enforced(self):
        from ontologics.identity import UserIdentity
        from ontologics.service import DomainError
        self.identities.users=[u if u.subject != 'reader' else UserIdentity('reader',frozenset()) for u in self.identities.users]
        try:
            r=self.call()
        except DomainError as e:
            self.assertEqual('FORBIDDEN',e.code)
        else:
            self.assertIsNone(r['calculation'])
            self.assertFalse(r['demand_response']['ok'])

    def test_failed_public_query_keeps_failure_envelope(self):
        from unittest.mock import patch
        from ontologics.host import RequestSession
        original=RequestSession.call
        def fail(session,tool,args):
            result=original(session,tool,args)
            if tool=='lookup': result.update(ok=False,data=None)
            return result
        with patch.object(RequestSession,'call',fail):
            r=self.call()
        self.assertIsNone(r['calculation'])
        self.assertFalse(r['demand_response']['ok'])

    def test_registered_partial_context_does_not_block_material_arithmetic(self):
        from examples.production_planning_materials import run_example
        report=run_example()
        registered=report['registered_validation']['report']
        self.assertTrue(registered['valid'])
        self.assertFalse(registered['graph_integration_verified'])
        self.assertTrue(registered['unresolved'])
        self.assertEqual(70,report['whole_bom']['conditional_upper_bound'])

    def test_example_runs_against_disposable_pg(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.production_planning_materials'),'Task03 example missing')
        from examples.production_planning_materials import run_example
        # A separate tests.db schema is owned and cleaned by the example.
        report=run_example()
        self.assertEqual(70,report['representative']['conditional_upper_bound'])
        self.assertIsNone(report['whole_bom']['whole_bom_material_limit'])
        self.assertFalse(report['demand']['confirmation']['verified'])
