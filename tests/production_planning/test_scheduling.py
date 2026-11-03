"""All positive supplier timing is test-only, not an OLIMEX commitment."""
from copy import deepcopy
from decimal import Decimal, Inexact, localcontext
import importlib.util
import json
import os
import unittest
from tests.production_planning.test_materials import fixture, ROOT, CONFIG
from tests.production_planning.test_planning import demand_for
from ontologics.production_planning.materials import available_stock, calculate_material_requirements
from ontologics.production_planning.planning import build_production_plan

AT='2026-10-26T10:00:00+09:00'
SOURCE=dict(source_ref='test-only:timing',sha256='b'*64,locator='test-only timing fixture')

def plan(i=70,n=100,full=False):
    doc,scope,rows=fixture()
    if full:
        rows=[x for x in json.loads((ROOT/'data/catalog.json').read_text())['nodes'] if x.get('type')=='BOMItem' and x['configuration_id']==CONFIG]
    m=calculate_material_requirements(rows,available_stock([doc],scope),n,
        configuration_id=CONFIG,data_version=1,demand_source={'state':'unconfirmed'},bom_complete=True)
    return build_production_plan(demand_for(m),m,i)

def resource():
    return json.loads((ROOT/'data/production_planning/production.json').read_text())['production_conditions']

def context(scenario='virtual_remaining'):
    return dict(configuration_id=CONFIG,data_version=1,scenario=scenario,evaluated_at=AT,
        selected_schedules={},milestones={})

def initial(p):
    return dict(state='assumed',scope='all_required_materials_for_initial_quantity',quantity=p['initial_quantity'],
        ready_at=AT,source=deepcopy(SOURCE),prerequisites=[])

def schedules(p):
    m=p['remaining_materials']
    return [dict(schedule_id='test-only:schedule:'+str(j),component_id=c['component_id'],
        configuration_id=CONFIG,data_version=1,scenario='virtual_remaining',board_quantity=m['planned_input_quantity'],
        demand_source=deepcopy(m['demand_source']),source=deepcopy(SOURCE),unit='piece',quantity=c['D'],
        coverage='full_requirement',kind='usable_at',at=AT,state='assumed',
        valid_from='2026-10-25T00:00:00Z',valid_until='2026-11-01T00:00:00Z',required_milestones=[])
        for j,c in enumerate(m['components'])]

class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.scheduling'),'Task07 missing')
        from ontologics.production_planning import scheduling
        self.s=scheduling;self.p=plan()

    def ready(self,p=None,ss=None,c=None):
        p=self.p if p is None else p
        return self.s.estimate_material_ready(p,schedules(p) if ss is None else ss,context() if c is None else c,scenario='virtual_remaining')

    def estimate(self,p=None,r=None,ready=None,ic=None,**kw):
        p=self.p if p is None else p
        return self.s.estimate_production_schedule(p,resource() if r is None else r,
            self.ready(p) if ready is None else ready,initial_conditions=initial(p) if ic is None else ic,**kw)

    def test_seventy_uses_board_quantity_and_separate_setup(self):
        r=self.estimate();a=r['initial']
        self.assertEqual((AT,'2026-10-26T11:00:00+09:00','2026-10-26T14:30:00+09:00'),tuple(a[k] for k in ('work_start','processing_start','completion')))
        self.assertEqual(70,a['quantity']);self.assertFalse(r['production_authorized']);self.assertEqual('scheduling-0.1',r['calculation']['rule_version'])
        self.assertIsNone(r['remaining']['completion'])  # no implicit re-setup policy

    def test_parallel_procurement_and_same_resource(self):
        for at,want in [('2026-10-26T12:00:00+09:00','2026-10-26T14:30:00+09:00'),('2026-10-26T18:00:00+09:00','2026-10-26T18:00:00+09:00')]:
            ss=schedules(self.p)
            for s in ss:s['at']=at
            r=self.estimate(ready=self.ready(ss=ss),remaining_setup_minutes=0)
            self.assertEqual(want,r['remaining']['work_start']);self.assertEqual(resource()['resource_ref'],r['remaining']['resource_ref'])
        self.assertEqual('2026-10-26T16:00:00+09:00',self.estimate(remaining_setup_minutes=0)['remaining']['completion'])
        self.assertEqual('2026-10-26T17:00:00+09:00',self.estimate(remaining_setup_minutes=60)['remaining']['completion'])

    def test_initial_max_timezone_and_date_boundary(self):
        for at,want in [('2026-10-26T00:00:00Z',AT),('2026-10-26T01:00:00Z',AT),('2026-10-26T23:30:00+09:00','2026-10-26T23:30:00+09:00')]:
            ic=initial(self.p);ic['ready_at']=at;r=self.estimate(ic=ic)
            self.assertEqual(want,r['initial']['work_start'])
        self.assertEqual('2026-10-27T04:00:00+09:00',r['initial']['completion'])

    def test_zero_jobs_never_invent_completion(self):
        for i,n in [(0,0),(0,100),(60,60)]:
            p=plan(i,n);r=self.estimate(p=p,remaining_setup_minutes=0)
            job=r['initial'] if i==0 else r['remaining']
            self.assertEqual('no_work',job['status']);self.assertIsNone(job['completion']);self.assertIsNone(job['work_start'])
        self.assertEqual(100,self.estimate(p=plan(0),remaining_setup_minutes=0)['remaining']['quantity'])

    def test_invalid_quantities_and_resource_numbers(self):
        for v in (True,1.0,-1,None,float('nan'),float('inf')):
            p=deepcopy(self.p);p['initial_quantity']=v
            with self.subTest(v=v),self.assertRaises(ValueError):self.estimate(p=p)
        for field in ('boards_per_hour','setup_minutes'):
            for v in (True,1.0,-1,None,'NaN','Infinity'):
                r=resource();r[field]=v
                with self.subTest(field=field,v=v),self.assertRaises(ValueError):self.estimate(r=r)
        r=resource();r['boards_per_hour']=0
        with self.assertRaises(ValueError):self.estimate(r=r)

    def test_time_validation_overflow_and_round_up(self):
        for at in ('2026-10-26','2026-10-26T10:00:00','bad','2026-02-30T00:00:00Z'):
            r=resource();r['available_at']=at
            with self.assertRaises(ValueError):self.estimate(r=r)
        r=resource();r.update(boards_per_hour='7',setup_minutes='0.001')
        with localcontext() as dc:
            dc.prec=2;dc.traps[Inexact]=True;dc.Emax=1
            result=self.estimate(r=r)
        self.assertEqual('2026-10-26T20:00:01+09:00',result['initial']['completion'])
        r=resource();r['available_at']='9999-12-31T23:59:59+09:00'
        self.assertIsNone(self.estimate(r=r)['initial']['completion'])
        r=resource();r['boards_per_hour']='0.000000000001'
        self.assertIn('time_range_exceeded',self.estimate(r=r)['initial']['issues'])
        r=resource();r['boards_per_hour']='1e99'
        with self.assertRaises(ValueError):self.estimate(r=r)

    def test_unsupported_production_policy_not_ignored(self):
        for patch in ({'interruption_policy':'shifts'},{'loss_quantity':1},{'defect_quantity':1},{'rework_quantity':1},{'resources':['a','b']}):
            r=resource();r.update(patch)
            self.assertIsNone(self.estimate(r=r)['initial']['completion'])

    def test_material_max_unknown_empty_and_stock_not_usable(self):
        ss=schedules(self.p);ss[1]['at']='2026-10-26T13:00:00+09:00'
        self.assertEqual(ss[1]['at'],self.ready(ss=ss)['ready_at'])
        r=self.ready(ss=ss[:2]);self.assertIsNone(r['ready_at']);self.assertEqual(ss[1]['at'],r['known_latest_at'])
        self.assertIsNone(self.ready(ss=[])['ready_at'])
        for s in ss:s['kind']='stock_observed'
        self.assertIsNone(self.ready(ss=ss)['ready_at'])

    def test_lead_time_start_units_transport_and_inspection(self):
        ss=schedules(self.p);s=ss[0];s.pop('at');s.update(kind='lead_time',start_milestone='test-only:commitment',
            lead_time={'value':2,'unit':'hours'},transport_included=True,inspection={'value':30,'unit':'minutes'})
        c=context();c['milestones']['test-only:commitment']=dict(state='assumed',at=AT,source=deepcopy(SOURCE))
        r=self.ready(ss=ss,c=c);self.assertEqual('2026-10-26T12:30:00+09:00',r['ready_at'])
        s['transport']={'value':99,'unit':'hours'}
        self.assertEqual(r['ready_at'],self.ready(ss=ss,c=c)['ready_at'])
        s['transport_included']=False
        self.assertEqual('2026-10-30T15:30:00+09:00',self.ready(ss=ss,c=c)['ready_at'])
        for key in ('transport','inspection','start_milestone','transport_included'):
            bad=deepcopy(ss);bad[0].pop(key)
            self.assertIsNone(self.ready(ss=bad,c=c)['ready_at'],key)
        s['lead_time']['unit']='business_days'
        self.assertIsNone(self.ready(ss=ss,c=c)['ready_at'])

    def test_arrival_requires_inspection_and_explicit_zero_allowed(self):
        ss=schedules(self.p);ss[0]['kind']='arrival_at'
        self.assertIsNone(self.ready(ss=ss)['ready_at'])
        ss[0]['inspection']={'value':0,'unit':'hours'}
        self.assertEqual(AT,self.ready(ss=ss)['ready_at'])

    def test_exact_identity_scope_source_and_validity(self):
        for key,v in [('component_id','wrong'),('configuration_id','wrong'),('data_version',2),('scenario','original'),('board_quantity',31),('unit','box'),('source',{}),('demand_source',{}),('valid_until',AT)]:
            ss=schedules(self.p);ss[0][key]=v
            self.assertIsNone(self.ready(ss=ss)['ready_at'],key)
        ss=schedules(self.p);ss[0]['state']='actual';ss[0]['at']='2026-10-27T10:00:00+09:00'
        self.assertIsNone(self.ready(ss=ss)['ready_at'])
        ss[0]['state']='promised'
        self.assertEqual('estimated',self.ready(ss=ss)['status'])

    def test_selection_conflicts_no_summing_or_fastest(self):
        ss=schedules(self.p);other=deepcopy(ss[0]);other['schedule_id']+=':other';other['at']='2026-10-26T11:00:00+09:00';ss.append(other)
        self.assertIsNone(self.ready(ss=ss)['ready_at'])
        c=context();c['selected_schedules'][other['component_id']]=other['schedule_id']
        self.assertEqual(other['at'],self.ready(ss=ss,c=c)['ready_at'])
        ss.append(deepcopy(other));self.assertIsNone(self.ready(ss=ss,c=c)['ready_at'])

    def test_supply_shortage_and_existing_stock_distinguished(self):
        for qty in (None,29):
            ss=schedules(self.p);ss[0]['quantity']=qty
            self.assertIsNone(self.ready(ss=ss)['ready_at'])
        ss=schedules(self.p);s=ss[1];s.update(coverage='shortage',quantity=0)
        self.assertIsNone(self.ready(ss=ss)['ready_at'])  # 330 existing pieces need explicit usable event
        s['existing_usable']=dict(state='assumed',at=AT,source=deepcopy(SOURCE))
        self.assertEqual(AT,self.ready(ss=ss)['ready_at'])

    def test_missing_prerequisite_and_alternative_review_not_ranked(self):
        ss=schedules(self.p);ss[0]['required_milestones']=['technical_review','customer_agreement','configuration_check']
        r=self.ready(ss=ss);self.assertIsNone(r['ready_at']);self.assertIsNone(r['alternative_ranking'])
        ic=initial(self.p);ic['prerequisites']=[{'state':'unknown'}]
        self.assertIsNone(self.estimate(ic=ic)['initial']['completion'])

    def test_price_absence_does_not_erase_independent_schedule(self):
        r=self.ready();self.assertEqual(AT,r['ready_at']);self.assertIsNone(r['procurement']['total_by_currency'])
        self.assertFalse(r['purchase_authorized'])

    def test_full_bom_unknowns_and_hypothetical_initial_are_separate(self):
        p=plan(full=True);ready=self.ready(p,ss=[]);r=self.estimate(p=p,ready=ready)
        self.assertIsNone(ready['ready_at']);self.assertEqual(74,len(ready['items']))
        self.assertEqual(71,sum(c['A'] is None for c in ready['procurement']['materials']['components']))
        self.assertEqual('2026-10-26T14:30:00+09:00',r['initial']['completion']);self.assertIsNone(r['remaining']['completion'])
        self.assertIsNone(r['whole_bom_material_limit']);self.assertIsNone(r['customer_arrival_compliance'])

    def test_wrong_or_tampered_remaining_and_virtual_stock_rejected(self):
        ready=self.ready();ready['ready_at']='2026-10-20T00:00:00Z'
        with self.assertRaises(ValueError):self.estimate(ready=ready)
        for key in ('purpose','state'):
            p=deepcopy(self.p);p[key]='actual'
            with self.assertRaises(ValueError):self.estimate(p=p)
        with self.assertRaises(ValueError):self.s.estimate_material_ready(self.p['remaining_materials'],[],context('original'),scenario='original')

    def test_unsupported_calendar_and_second_resource_are_explicit(self):
        for patch in ({'calendar':'business_days'}, {'remaining_resource_ref':'test-only:second-line'}):
            r=resource();r.update(patch)
            self.assertIsNone(self.estimate(r=r)['initial']['completion'])
        ss=schedules(self.p);ss[0]['calendar']='business_days'
        self.assertIsNone(self.ready(ss=ss)['ready_at'])

    def test_purchase_quantity_floor_without_reusing_price_as_time(self):
        from tests.production_planning.test_procurement import offer, context as price_context
        ss=schedules(self.p);o=offer(ss[0]['component_id']);o.update(valid_from='2026-10-25T00:00:00Z',valid_until='2026-11-01T00:00:00Z')
        c=price_context();c.update(configuration_id=CONFIG,scenario='virtual_remaining',evaluated_at=AT)
        r=self.s.estimate_material_ready(self.p,ss,context(),scenario='virtual_remaining',offers=[o],cost_context=c)
        self.assertEqual(60,r['procurement']['items'][0]['purchase_quantity'])
        self.assertIsNone(r['ready_at'])  # only 30 schedule pieces; purchase review requires 60
        ss[0]['quantity']=60
        r=self.s.estimate_material_ready(self.p,ss,context(),scenario='virtual_remaining',offers=[o],cost_context=c)
        self.assertEqual(AT,r['ready_at']);self.assertEqual(30,r['procurement']['materials']['planned_input_quantity'])

    def test_original_errors_empty_and_context_binding(self):
        m=self.p['input_materials'];c=context('original')
        for key in ('errors','conflicts'):
            bad=deepcopy(m);bad[key]=[{'code':'test-only:upstream'}]
            r=self.s.estimate_material_ready(bad,[],c,scenario='original')
            self.assertIsNone(r['ready_at']);self.assertEqual(bad,r['procurement']['materials'])
        empty=calculate_material_requirements([],available_stock(*([fixture()[0]],fixture()[1])),100,
            configuration_id=CONFIG,data_version=1,demand_source={'state':'unconfirmed'},bom_complete=True)
        self.assertIsNone(self.s.estimate_material_ready(empty,[],c,scenario='original')['ready_at'])
        for key,value in [('data_version',2),('configuration_id','wrong'),('scenario','original')]:
            bad=context();bad[key]=value
            with self.assertRaises(ValueError):self.ready(c=bad)

    def test_invalid_duration_and_offset_forms(self):
        for v in (True,1.0,-1,'NaN','Infinity'):
            ss=schedules(self.p);ss[0].update(kind='arrival_at',inspection={'value':v,'unit':'hours'})
            self.assertIsNone(self.ready(ss=ss)['ready_at'])
        for at in ('2026-10-26T10:00:00+09:60','2026-10-26T10:00:00+24:00','2026-10-26T10:00:00.1234567Z'):
            r=resource();r['available_at']=at
            with self.assertRaises(ValueError):self.estimate(r=r)

    def test_nested_calendar_shift_and_context_policies_not_ignored(self):
        for patch in ({'calendar_policy':'business_days'},{'shifts':['09:00-12:00']}):
            r=resource();r.update(patch)
            with self.subTest(patch=patch):self.assertIsNone(self.estimate(r=r)['initial']['completion'])
        ss=schedules(self.p);ss[0].update(kind='arrival_at',inspection={'value':2,'unit':'days','calendar':'business_days'})
        self.assertIsNone(self.ready(ss=ss)['ready_at'])
        c=context();c['calendar_policy']='business_days'
        with self.assertRaises(ValueError):self.ready(c=c)

    def test_linked_offer_identity_eligibility_not_just_price_diagnostics(self):
        from tests.production_planning.test_procurement import offer, context as price_context
        ss=schedules(self.p);o=offer(ss[0]['component_id']);o.update(valid_from='2026-10-25T00:00:00Z',valid_until='2026-11-01T00:00:00Z')
        ss[0].update(offer_id=o['offer_id'],supplier_id=o['supplier_id'],quantity=60)
        c=price_context();c.update(configuration_id=CONFIG,scenario='virtual_remaining',evaluated_at=AT)
        for patch in ({'data_version':2},{'source':{}},{'unit':'box'}):
            bad=dict(deepcopy(o),**patch)
            r=self.s.estimate_material_ready(self.p,ss,context(),scenario='virtual_remaining',offers=[bad],cost_context=c)
            with self.subTest(patch=patch):self.assertIsNone(r['ready_at'])
        o.pop('tiers')  # no price, but independently stated timing identity remains eligible
        r=self.s.estimate_material_ready(self.p,ss,context(),scenario='virtual_remaining',offers=[o],cost_context=c)
        self.assertEqual(AT,r['ready_at']);self.assertIsNone(r['procurement']['total_by_currency'])

    def test_repeat_no_alias_and_caller_flags_no_authority(self):
        p=deepcopy(self.p);r=resource();r.update(approved=True,confirmed=True)
        before=deepcopy((p,r));a=self.estimate(p=p,r=r);b=self.estimate(p=p,r=r)
        a['plan']['components'].clear();self.assertEqual(before,(p,r));self.assertEqual(b,self.estimate(p=p,r=r))
        self.assertFalse(b['resource_reserved']);self.assertFalse(b['production_authorized'])

from tests.production_planning.test_materials import MaterialsPostgresTests as _PG

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Explicit disposable PG required')
class SchedulingPostgresTests(unittest.TestCase):
    setUp=_PG.setUp

    def call(self,**kw):
        from ontologics.production_planning.scheduling import build_saved_schedule_review
        args=dict(demand_id=self.saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
            snapshots=[self.doc],scope=self.scope,initial_quantity=70,resource=resource(),schedules=[],schedule_context=context())
        args.update(kw)
        return build_saved_schedule_review(self.workflow,self.host,self.reader,**args)

    def test_fresh_read_preserves_state_and_unknowns(self):
        from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow
        self.workflow=PersistentDemandConfirmationWorkflow(self.host,self.identities,self.settings)
        before=self.workflow.get(self.reader,self.saved['demand_id'])['data'];stock=deepcopy(self.doc)
        with self.host.request(self.reader) as session:graph=session.call('lookup',{'version':1})['data']
        r=self.call();self.assertIsNone(r['schedule']['remaining']['completion'])
        self.assertEqual([30,240],sorted(x['shortage_quantity'] for x in r['material_ready']['procurement']['purchase_review']))
        self.assertEqual(before,self.workflow.get(self.reader,self.saved['demand_id'])['data']);self.assertEqual(stock,self.doc)
        self.assertEqual([],before['events']);self.assertFalse(before['confirmation']['verified'])
        self.assertEqual('temporal-0.1',r['query']['lookup_response']['rule_version']);self.assertTrue(r['query']['lookup_response']['run_id'])
        with self.host.request(self.reader) as session:self.assertEqual(graph,session.call('lookup',{'version':1})['data'])

    def test_revision_hash_and_version_change(self):
        for kw in ({'expected_revision':99},{'expected_target_sha256':'0'*64},{'data_version':2}):self.assertIsNone(self.call(**kw)['schedule'])
        self.interpreted['items'][0]['proposed_configuration_id']=CONFIG
        t=self.preflight.prepare(self.author,self.path,self.interpreted,data_version=1)['target']
        self.workflow.save(self.author,t,demand_id=self.saved['demand_id'],expected_revision=1,request_key='schedule-revise')
        self.assertIsNone(self.call(expected_revision=1)['schedule'])
        self.assertEqual(2,self.call()['plan']['demand']['demand_source']['revision'])

    def test_public_failure_preview_and_permission(self):
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
            with patch.object(RequestSession,'call',changed):self.assertIsNone(self.call()['schedule'])
        from ontologics.identity import UserIdentity
        from ontologics.service import DomainError
        self.identities.users=[u if u.subject!='reader' else UserIdentity('reader',frozenset()) for u in self.identities.users]
        try:r=self.call()
        except DomainError as exc:self.assertEqual('FORBIDDEN',exc.code)
        else:self.assertIsNone(r['schedule'])

    def test_virtual_stock_markers_rejected(self):
        for key,value in [('state','virtual'),('purpose','virtual_material_review_only'),('original_stock',{}),('virtual_deductions',[])]:
            doc=deepcopy(self.doc);doc[key]=value
            with self.assertRaises(ValueError):self.call(snapshots=[doc])

    def test_example(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.production_planning_schedule'))
        from examples.production_planning_schedule import run_example
        r=run_example();self.assertEqual(0,r['model_api_calls']);self.assertEqual(0,r['demand_event_count'])
        self.assertEqual('2026-10-26T14:30:00+09:00',r['schedule']['initial']['completion'])
        self.assertIsNone(r['schedule']['remaining']['completion']);self.assertEqual('2026-11-10',r['delivery_due'])

del _PG
