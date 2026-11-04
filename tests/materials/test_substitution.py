"""Task05 boundaries: real pinned source, exact queries, no invented approval."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.materials.test_materials import fixture, CONFIG
from ontologics.service import configuration_changes
from ontologics.materials.materials import available_stock, calculate_material_requirements

ROOT=Path(__file__).resolve().parents[2]
RIGHT='config:esp32-poe:L1:WROOM'
U2='component:olimex-L-wroom:77be1300069a66c9'
META=ROOT/'data/production_planning/substitution_evidence_v1.json'

class CatalogSession:
    """Public read interface double; uses real catalog and common comparison."""
    def __init__(self, version=1, registered=False):
        self.version=version;self.calls=[]
        self.nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        if registered:self.nodes+=json.loads(META.read_text())['nodes']
    def call(self, tool, args):
        self.calls.append((tool,deepcopy(args)))
        assert args['version']==self.version
        idx={n['id']:n for n in self.nodes}
        if tool=='lookup':data=self.nodes
        elif tool=='configuration':
            c=idx[args['configuration_id']];rev=idx[c['revision_id']]
            rows=[n for n in self.nodes if n.get('configuration_id')==c['id'] and n['type']=='BOMItem']
            data=dict(configuration=c,revision=rev,product=idx[rev['model_id']],items=rows,
                      components=[idx[cid] for cid in sorted({n['component_id'] for n in rows})])
        elif tool=='component_usage':
            cid=args['component_id'];data=dict(component_id=cid,items=[n for n in self.nodes if n.get('component_id')==cid and n['type']=='BOMItem'],scope='exact component identity; unverified cross-revision equivalence is not merged')
        elif tool=='compare_configurations':
            data=dict(left=args['left_id'],right=args['right_id'],changes=configuration_changes(self.nodes,args['left_id'],args['right_id']),is_preview=False,draft_id=None,draft_version=None,comparison_basis='source descriptions by reference designator; not electrical equivalence')
        else:raise AssertionError(tool)
        return deepcopy(dict(ok=True,data=data,data_version=self.version,rule_version='temporal-0.1',run_id='test-double',warnings=[],errors=[],conflicts=[],evidence=[]))


def u2_shortage(version=1):
    doc,scope,_=fixture();row=next(n for n in CatalogSession().nodes if n['id']=='bom:esp32-poe:L:WROOM:row-57')
    doc['stocks']=[dict(id='test:u2',lot_id='test:u2:lot',component_id=U2,unit='piece',quantity_state='known',availability_basis='reported_available',available_quantity=0)]
    doc['source_ref']='test-only:u2-shortage';scope['component_lots']={U2:['test:u2:lot']}
    return calculate_material_requirements([row],available_stock([doc],scope),100,configuration_id=CONFIG,data_version=version,demand_source={'state':'unconfirmed','basis':'separate synthetic U2 shortage; not stock.json'},bom_complete=False)

class SubstitutionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.materials.substitution'),'Task05 module missing')
        from ontologics.materials import substitution
        self.s=substitution;self.e=self.s.load_revision_evidence();self.m=u2_shortage()
        self.query=self.s.query_substitution(CatalogSession(),configuration_id=CONFIG,component_id=U2,candidate_configuration_id=RIGHT,data_version=1)
    def review(self,**kw):
        return self.s.build_substitution_review(kw.get('shortage',self.m),kw.get('comparisons',[self.query]),kw.get('evidence',[self.e]),kw.get('constraints',[]))
    def test_real_source_bytes_and_related_positions(self):
        r=self.review();self.assertEqual(1,len(r['candidates']));c=r['candidates'][0]
        self.assertEqual('existing_configuration_transition',c['kind']);self.assertEqual(RIGHT,c['configuration_id'])
        self.assertEqual({'R6','R50','R51','U2'},{x['position'] for x in c['related_changes']})
        self.assertEqual('component:olimex-L1-wroom:35335677b228a6d2',c['component_id'])
        self.assertFalse(c['direct_exchange_allowed']);self.assertEqual('blocked',r['states']['material_calculation'])
        self.assertEqual('c8ee91c74caa752e5ffdd8f5a18faef3dacc2c8339b1e6a7e49f4aa1939952db',self.e['metadata']['source']['sha256'])
        self.assertEqual([25,34],self.e['metadata']['sections']['L1'])
        for p in ('R50','R51'):
            self.assertEqual('unknown',next(c for c in r['candidates'][0]['related_changes'] if c['position']==p)['after']['population'])
    def test_original_shortages_are_not_u2(self):
        doc,scope,items=fixture()
        m=calculate_material_requirements(items,available_stock([doc],scope),100,configuration_id=CONFIG,data_version=1,demand_source={'state':'unconfirmed'})
        r=self.review(shortage=m);self.assertEqual([],r['candidates']);self.assertEqual(2,len(r['purchase_review']))
        self.assertEqual([30,240],sorted(x['shortage_quantity'] for x in r['purchase_review']))
        self.assertEqual(70,r['original_materials']['conditional_upper_bound'])
    def test_no_evidence_and_acquisition_failure_diagnostics(self):
        for evidence in ([],[{'state':'unavailable','errors':[{'code':'DOWNLOAD_FAILED'}]}]):
            r=self.review(evidence=evidence);self.assertFalse(r['candidates']);self.assertTrue(r['diagnostics']);self.assertFalse(r['production_authorized'])
    def test_tampered_bytes_commit_scope_license_and_locator_rejected(self):
        for mutation in ('bytes','commit','scope','license','locator'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);meta=json.loads(META.read_text())
                for p in [meta['source']['path'],*[x['path'] for x in meta['license_basis']]]:
                    dest=root/p;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/p).read_bytes())
                if mutation=='bytes':(root/meta['source']['path']).write_bytes(b'<html>404</html>')
                if mutation=='commit':meta['source']['version']='main'
                if mutation=='scope':meta['scope']['right']=CONFIG
                if mutation=='license':(root/meta['license_basis'][0]['path']).write_bytes(b'unknown')
                if mutation=='locator':meta['sections']['L1']=[38,47]
                e=self.s.load_revision_evidence(root=root,metadata=meta)
                self.assertEqual('unavailable',e['state']);self.assertFalse(self.review(evidence=[e])['candidates'])
    def test_preview_wrong_version_and_query_failure_retained(self):
        for mode in ('preview','version','failure','exception'):
            base=CatalogSession()
            class Bad:
                def call(_,tool,args):
                    r=base.call(tool,args)
                    if tool=='compare_configurations':
                        if mode=='preview':r['data']['is_preview']=True
                        if mode=='version':r['data_version']=9
                        if mode=='failure':r.update(ok=False,data=None,errors=[{'code':'FORBIDDEN'}])
                        if mode=='exception':raise RuntimeError('transport')
                    return r
            q=self.s.query_substitution(Bad(),configuration_id=CONFIG,component_id=U2,candidate_configuration_id=RIGHT,data_version=1)
            r=self.review(comparisons=[q]);self.assertFalse(r['candidates']);self.assertTrue(r['diagnostics'])
            if mode=='failure':self.assertEqual('FORBIDDEN',r['comparisons'][0]['comparison_response']['errors'][0]['code'])
    def test_forged_flags_and_graph_registration_do_not_grant_authority(self):
        q=self.s.query_substitution(CatalogSession(registered=True),configuration_id=CONFIG,component_id=U2,candidate_configuration_id=RIGHT,data_version=1)
        r=self.review(comparisons=[q],constraints=[dict(approved=True,confirmed=True,valid=True,revision_change=True,substitution=True)])
        self.assertEqual('registered',r['states']['graph_evidence']);self.assertEqual('unverified',r['states']['technical_suitability'])
        self.assertEqual('unverified',r['states']['contract_permission']);self.assertFalse(r['production_authorized'])
    def test_registration_state_is_independent_of_shortage_candidate(self):
        q=self.s.query_substitution(CatalogSession(registered=True),configuration_id=CONFIG,component_id=U2,candidate_configuration_id=RIGHT,data_version=1)
        m=deepcopy(self.m);m['components'][0]['S']=0
        r=self.review(shortage=m,comparisons=[q])
        self.assertFalse(r['candidates']);self.assertEqual('registered',r['states']['graph_evidence'])

    def test_name_package_and_wrong_component_do_not_join(self):
        bad=deepcopy(self.m);bad['components'][0]['component_id']='same-name-different-id'
        self.assertFalse(self.review(shortage=bad)['candidates'])
        q=deepcopy(self.query);q['comparison_response']['data']['changes'][0]['before']['item_id']='fabricated'
        self.assertFalse(self.review(comparisons=[q])['candidates'])
    def test_independent_repeatable_copy_and_virtual_review_label(self):
        before=deepcopy((self.m,self.query,self.e));a=self.review();b=self.review()
        self.assertEqual(a,b);a['original_materials']['components'][0]['A']=999
        self.assertEqual(before,(self.m,self.query,self.e))
        virtual=deepcopy(self.m);virtual['purpose']='virtual_material_review_only';virtual['stock']['state']='virtual'
        self.assertEqual('virtual_remaining_scenario',self.review(shortage=virtual)['shortage_scope'])
    def test_invalid_materials_cannot_discover_a_usable_candidate(self):
        m=deepcopy(self.m);m['errors']=[{'code':'INVALID_BOM_ROW'}]
        self.assertFalse(self.review(shortage=m)['candidates'])

    def test_same_component_lots_reuse_coverage_and_future_exclusions(self):
        doc,scope,_=fixture();cid=doc['stocks'][0]['component_id']
        r=self.s.review_same_component_lots([doc],scope,component_id=cid)
        self.assertEqual(70,r['available_quantity']);self.assertEqual('same_component_other_lot',r['kind'])
        scope['component_lots']={};self.assertIsNone(self.s.review_same_component_lots([doc],scope,component_id=cid)['available_quantity'])
        doc['stock_scope']['as_of']='2027-01-01T00:00:00Z'
        self.assertIsNone(self.s.review_same_component_lots([doc],scope,component_id=cid)['available_quantity'])

from tests.materials.test_materials import MaterialsPostgresTests as _PG
@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Explicit disposable PG required')
class SubstitutionPostgresTests(unittest.TestCase):
    setUp=_PG.setUp
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.materials.substitution'),'Task05 module missing')
        from ontologics.materials import substitution
        return substitution
    def query(self,version=1,component_id=U2):
        with self.host.request(self.reader) as session:
            return self.module().query_substitution(session,configuration_id=CONFIG,component_id=component_id,candidate_configuration_id=RIGHT,data_version=version)
    def test_real_queries_preserve_envelopes_and_exact_versions(self):
        q=self.query();self.assertFalse(q['errors'])
        for r in [q['usage_response'],q['comparison_response'],q['catalog']['lookup_response'],*q['catalog']['configuration_responses']]:
            self.assertTrue(r['ok']);self.assertEqual(1,r['data_version']);self.assertTrue(r['run_id']);self.assertEqual('temporal-0.1',r['rule_version'])
        self.assertFalse(self.query(version=999)['catalog']['lookup_response']['ok'])
        self.assertFalse(self.query(component_id='unknown')['usage_response']['ok'])
    def test_source_draft_distinct_reviewer_exact_apply_and_immutable_bom(self):
        from ontologics.identity import DevelopmentContext
        s=self.module();e=s.load_revision_evidence()
        before=self.query()['catalog']['lookup_response']['data']
        with self.host.request(self.author) as session:
            draft=session.call('prepare_draft',{'operations':[{'op':'put','node':n} for n in e['metadata']['nodes']],'request_key':'task05-source'})['data']
            did=draft['draft_id'];dv=draft['draft_version']
            self.assertFalse(session.review(did,dv,decision='approve',note='self must fail',request_key='self')['ok'])
        with self.host.request(DevelopmentContext('reviewer')) as session:
            self.assertTrue(session.review(did,dv,decision='approve',note='Development source registration only, no suitability or contract approval',request_key='review')['ok'])
        self.assertFalse(self.host.apply_approved(did,dv+1,request_key='wrong-version')['ok'])
        applied=self.host.apply_approved(did,dv,request_key='apply');self.assertTrue(applied['ok'],applied)
        q=self.query(version=2);after=q['catalog']['lookup_response']['data']
        ids={n['id'] for n in e['metadata']['nodes']};self.assertEqual(before,[n for n in after if n['id'] not in ids])
        r=s.build_substitution_review(u2_shortage(2),[q],[e],[])
        self.assertEqual('registered',r['states']['graph_evidence']);self.assertFalse(r['production_authorized'])
        self.assertFalse(s.build_substitution_review(u2_shortage(1),[q],[e],[])['candidates'])
    def recalc(self,**kw):
        s=self.module();review=s.build_substitution_review(u2_shortage(),[self.query()],[s.load_revision_evidence()],[])
        args=dict(candidate_configuration_id=RIGHT,demand_id=self.saved['demand_id'],item_id='contract-item:planning:001',data_version=1,snapshots=[self.doc],scope=self.scope,initial_quantity=0,expected_revision=self.saved['revision'],expected_target_sha256=self.saved['target_sha256'])
        args.update(kw)
        return s.recalculate_configuration_review(self.workflow,self.host,self.reader,review,**args)
    def test_actual_unapproved_candidate_blocks_and_preserves_demand(self):
        before=self.workflow.get(self.reader,self.saved['demand_id'])['data'];doc=deepcopy(self.doc)
        r=self.recalc();self.assertIsNone(r['recalculation']);self.assertIn('APPLICABILITY_READER_UNAVAILABLE',{e['code'] for e in r['blockers']})
        self.assertEqual(before,self.workflow.get(self.reader,self.saved['demand_id'])['data']);self.assertEqual(doc,self.doc);self.assertEqual([],before['events'])
    def test_target_revision_permission_and_virtual_inputs_block(self):
        for kwargs in ({'expected_revision':99},{'expected_target_sha256':'0'*64},{'data_version':2},{'snapshots':[{'state':'virtual'}]}):
            self.assertIsNone(self.recalc(**kwargs)['recalculation'])
        from ontologics.identity import UserIdentity
        self.identities.users=[u if u.subject!='reader' else UserIdentity('reader',frozenset()) for u in self.identities.users]
        r=self.recalc();self.assertIsNone(r['recalculation']);self.assertTrue(r['blockers'])

    def register_source(self):
        from ontologics.identity import DevelopmentContext
        e=self.module().load_revision_evidence()
        with self.host.request(self.author) as session:
            response=session.call('prepare_draft',{'operations':[{'op':'put','node':n} for n in e['metadata']['nodes']],'request_key':'seam-source'})
            self.assertTrue(response['ok'],response);draft=response['data']
        with self.host.request(DevelopmentContext('reviewer')) as session:
            response=session.review(draft['draft_id'],draft['draft_version'],decision='approve',note='Development source registration',request_key='seam-review')
            self.assertTrue(response['ok'],response)
        response=self.host.apply_approved(draft['draft_id'],draft['draft_version'],request_key='seam-apply')
        self.assertTrue(response['ok'],response)
        return response['data_version']

    def seam(self, *, record_state='accepted', mutate=None, stale_after_first=False, disguise_virtual=False):
        # Explicit test-only demand/applicability host doubles. Real Task02 does
        # NOT support these permissions/technical decisions. Real PG BOM reads
        # and unchanged Task03/04 arithmetic still execute below.
        s=self.module();version=self.register_source()
        actual=self.workflow.get(self.reader,self.saved['demand_id'])
        candidate=deepcopy(actual);candidate['data_version']=version
        d=candidate['data'];d['confirmation']['verified']=True;d['status']='confirmed'
        item=d['items'][0];item['contract_item_candidate']['configuration_id']=RIGHT
        item['permissions']={'revision_change':True,'substitution':True}
        class WorkflowDouble:
            def __init__(_):_.count=0
            def get(_,context,demand_id):
                _.count+=1;r=deepcopy(candidate)
                if stale_after_first and _.count>1:r['data']['confirmation']['verified']=False
                return r
        class ApplicabilityDouble:
            def read_current(_,context,binding):
                r=dict(binding=deepcopy(binding),technical={'state':record_state,'evidence_refs':['test-only:technical']},contract={'state':record_state,'evidence_refs':['test-only:contract']},human={'state':record_state,'evidence_refs':['test-only:review']})
                if mutate:mutate(r)
                return r
        review=s.build_substitution_review(u2_shortage(version),[self.query(version)],[s.load_revision_evidence()],[])
        if disguise_virtual:
            review['original_materials']['purpose']='virtual_material_review_only'
            review['original_materials']['stock']['state']='virtual'
            review['shortage_scope']='original_material_review'
        before=deepcopy(self.doc)
        r=s.recalculate_configuration_review(WorkflowDouble(),self.host,self.reader,review,candidate_configuration_id=RIGHT,
            demand_id=self.saved['demand_id'],item_id='contract-item:planning:001',data_version=version,snapshots=[self.doc],scope=self.scope,
            initial_quantity=0,expected_revision=d['revision'],expected_target_sha256=d['target_sha256'],applicability_reader=ApplicabilityDouble())
        self.assertEqual(before,self.doc)
        return r

    def test_test_only_host_seam_uses_entire_l1_and_never_transfers_l_stock(self):
        r=self.seam();self.assertFalse(r['blockers'],r['blockers']);p=r['recalculation']['plan']
        self.assertEqual(RIGHT,p['configuration_id']);self.assertEqual(100,p['planned_input_quantity']);self.assertEqual(0,p['initial_quantity'])
        self.assertEqual(74,len(p['input_materials']['components']));self.assertEqual(74,sum(c['A'] is None for c in p['components']))
        self.assertEqual(12,sum(c['q'] is None for c in p['components']));self.assertIsNone(p['whole_bom_material_limit'])
        self.assertFalse(p['production_authorized']);self.assertEqual('independent_original_inputs',p['comparison_basis'])
        self.assertEqual([],self.workflow.get(self.reader,self.saved['demand_id'])['data']['events'])

    def test_test_only_host_rejected_or_unverified_applicability_blocks(self):
        r=self.seam(record_state='rejected');self.assertIsNone(r['recalculation']);self.assertTrue(r['blockers'])

    def test_test_only_host_binding_and_current_confirmation_rechecked(self):
        r=self.seam(mutate=lambda r:r['binding'].update(target_sha256='0'*64))
        self.assertIsNone(r['recalculation']);self.assertTrue(r['blockers'])

    def test_test_only_host_confirmation_lost_between_reads_blocks(self):
        r=self.seam(stale_after_first=True);self.assertIsNone(r['recalculation']);self.assertTrue(r['blockers'])

    def test_virtual_provenance_cannot_be_hidden_by_outer_review_label(self):
        r=self.seam(disguise_virtual=True)
        self.assertIsNone(r['recalculation'])
        self.assertIn('ORIGINAL_SCENARIO_REQUIRED',{e['code'] for e in r['blockers']})

    def test_forged_registered_review_cannot_replace_current_graph(self):
        s=self.module();q=s.query_substitution(CatalogSession(registered=True),configuration_id=CONFIG,component_id=U2,candidate_configuration_id=RIGHT,data_version=1)
        review=s.build_substitution_review(u2_shortage(),[q],[s.load_revision_evidence()],[])
        r=s.recalculate_configuration_review(self.workflow,self.host,self.reader,review,candidate_configuration_id=RIGHT,
            demand_id=self.saved['demand_id'],item_id='contract-item:planning:001',data_version=1,snapshots=[self.doc],scope=self.scope,
            initial_quantity=0,expected_revision=1,expected_target_sha256=self.saved['target_sha256'])
        self.assertIn('EVIDENCE_NOT_REGISTERED',{e['code'] for e in r['blockers']})

    def test_example(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.materials_substitution'),'Task05 example missing')
        from examples.materials_substitution import run_example
        r=run_example();self.assertEqual(0,r['model_api_calls']);self.assertEqual(0,r['demand_event_count'])
        self.assertEqual([1,2],r['source_registration']['versions']);self.assertFalse(r['confirmation_verified'])
        self.assertEqual(70,r['original_plan']['conditional_upper_bound']);self.assertEqual(71,r['unknown_stock_components'])
        self.assertIsNone(r['transition']['recalculation'])

del _PG
