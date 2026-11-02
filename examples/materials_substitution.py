"""Disposable PG demo: real OLIMEX evidence, manual claims, synthetic stock.

The only approvals below register source/evidence in a disposable development
schema. They do NOT confirm demand, electrical suitability or customer consent.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from ontologics.materials.substitution import (
    load_revision_evidence,query_substitution,build_substitution_review,recalculate_configuration_review,
    LEFT,RIGHT,U2)
from ontologics.materials.materials import available_stock,calculate_material_requirements
from ontologics.materials.planning import build_saved_production_plan

ROOT=Path(__file__).resolve().parents[1]


def run_example():
    if not os.environ.get('TEST_DATABASE_URL'):raise ValueError('Explicit disposable TEST_DATABASE_URL required')
    from tests.db import isolated_database
    from ontologics.materials.host import MaterialsHost as CommonHost
    from ontologics.identity import DevelopmentContext,DevelopmentIdentityProvider,UserIdentity
    from ontologics.materials.persistence import MaterialsService as Service
    from ontologics.materials.contract_intake import extract_contract_text
    from ontologics.materials.demand_confirmation import DemandConfirmationWorkflow
    from ontologics.materials.demand_confirmation_store import PersistentDemandConfirmationWorkflow
    from examples.materials_contract_intake import reference_interpretation
    folder=ROOT/'data/materials';raw=(folder/'contract.txt').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==json.loads((folder/'request.json').read_text())['external_inputs'][0]['sha256']=='6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962'
    evidence=load_revision_evidence()
    if evidence['state']!='acquired':raise ValueError('Pinned evidence unavailable; inspect load_revision_evidence diagnostics')
    with isolated_database() as settings:
        identities=DevelopmentIdentityProvider([UserIdentity('sub-author',frozenset({'read','draft'})),
            UserIdentity('sub-reviewer',frozenset({'read','review'})),UserIdentity('sub-reader',frozenset({'read'}))],enabled=True)
        author,reviewer,reader=map(DevelopmentContext,('sub-author','sub-reviewer','sub-reader'))
        host=CommonHost(identities,settings)
        seed=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']+json.loads((folder/'graph_nodes_v1.json').read_text())['nodes']
        try:assert seed.bootstrap('fixture',nodes,request_key='sub-fixture')['ok']
        finally:seed.close()
        preflight=DemandConfirmationWorkflow(host,identities);workflow=PersistentDemandConfirmationWorkflow(host,identities,settings)
        def prepare(version):
            interpretation=reference_interpretation(extract_contract_text(folder/'contract.txt'),data_version=version)
            return preflight.prepare(author,folder/'contract.txt',interpretation,data_version=version)['target']
        saved=workflow.save(author,prepare(1),request_key='sub-demand-1')['data']
        with host.request(author) as session:
            draft=session.call('prepare_draft',{'operations':[dict(op='put',node=n) for n in evidence['metadata']['nodes']],'request_key':'sub-source'})
        assert draft['ok'];did=draft['data']['draft_id'];dv=draft['data']['draft_version']
        with host.request(reviewer) as session:
            registration_review=session.review(did,dv,decision='approve',note='Development source bytes/locator registration only; no electrical, contract or demand approval',request_key='sub-source-review')
        assert registration_review['ok']
        applied=host.apply_approved(did,dv,request_key='sub-source-apply');assert applied['ok'];version=applied['data_version']
        with host.request(reader) as session:graph=session.call('lookup',{'version':version})['data']
        added_ids={n['id'] for n in evidence['metadata']['nodes']}
        assert [n for n in graph if n['id'] not in added_ids]==nodes
        # Re-prepare from unchanged source and exact version; never relabel L as L1.
        saved=workflow.save(author,prepare(version),demand_id=saved['demand_id'],expected_revision=1,request_key='sub-demand-2')['data']
        workflow=PersistentDemandConfirmationWorkflow(CommonHost(identities,settings),identities,settings)
        before=workflow.get(reader,saved['demand_id'])['data']
        stock_raw=(folder/'stock.json').read_bytes();doc=json.loads(stock_raw)
        doc.update(source_ref='input:planning:stock',sha256=hashlib.sha256(stock_raw).hexdigest())
        original_doc=deepcopy(doc)
        scope=dict(doc['stock_scope'],unit='piece',component_lots={r['component_id']:[r['lot_id']] for r in doc['stocks']},coverage_basis='Explicit three-component synthetic development evaluation only')
        args=dict(demand_id=saved['demand_id'],item_id='contract-item:planning:001',data_version=version,snapshots=[doc],scope=scope,expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'])
        plan=build_saved_production_plan(workflow,host,reader,initial_quantity=70,**args)['plan'];assert plan is not None
        materials=plan['input_materials']
        with host.request(reader) as session:
            queries=[query_substitution(session,configuration_id=LEFT,component_id=c['component_id'],candidate_configuration_id=RIGHT,data_version=version) for c in materials['components'] if c['S'] is not None and c['S']>0]
            u2_query=query_substitution(session,configuration_id=LEFT,component_id=U2,candidate_configuration_id=RIGHT,data_version=version)
        constraints=[before['items'][0]['fields']['constraints'],before['items'][0]['permissions']]
        original_review=build_substitution_review(materials,queries,[evidence],constraints)
        # Separate fixture: zero U2 stock for N=100. No supplier or registered lot
        # is asserted, and this fixture never enters the original stock document.
        u2_doc=dict(source_ref='test-only:u2-zero-stock',sha256=hashlib.sha256(b'task05 synthetic U2 shortage fixture').hexdigest(),stock_scope=deepcopy(doc['stock_scope']),stocks=[dict(id='test:u2:observation',lot_id='test:u2:lot',component_id=U2,unit='piece',quantity_state='known',availability_basis='reported_available',available_quantity=0)])
        u2_scope=dict(scope,component_lots={U2:['test:u2:lot']},coverage_basis='Separate U2 zero-stock development fixture; not real stock')
        u2_row=next(n for n in nodes if n['id']=='bom:esp32-poe:L:WROOM:row-57')
        u2_materials=calculate_material_requirements([u2_row],available_stock([u2_doc],u2_scope),100,configuration_id=LEFT,data_version=version,demand_source={'state':'unconfirmed','basis':'Separate synthetic U2 shortage, not actual shortage'},bom_complete=False)
        u2_review=build_substitution_review(u2_materials,[u2_query],[evidence],constraints)
        transition=recalculate_configuration_review(workflow,host,reader,u2_review,candidate_configuration_id=RIGHT,initial_quantity=0,**args)
        assert transition['recalculation'] is None
        assert original_review['candidates']==[] and len(u2_review['candidates'])==1
        assert plan['conditional_upper_bound']==70 and plan['whole_bom_material_limit'] is None
        assert sum(c['A'] is None for c in materials['components'])==71
        assert workflow.get(reader,saved['demand_id'])['data']==before and doc==original_doc
        with host.request(reader) as session:assert session.call('lookup',{'version':version})['data']==graph
        # Supersede is a revision event, not a confirmation event; current rev has none.
        assert before['events']==[] and not before['confirmation']['verified']
        right=next(d for d in u2_query['catalog']['configurations']['candidates'] if d['configuration']['id']==RIGHT)
        return dict(mode='actual_disposable_pg_manual_interpretation_synthetic_stock',model_api_calls=0,
            source_registration=dict(versions=[1,version],draft=draft,review=registration_review,apply=applied,added_node_ids=sorted(added_ids),meaning='Development evidence registration only'),
            candidate_reprepared=dict(reason='Evidence registration changed public version; re-read/re-prepare unchanged L demand',revision=saved['revision'],data_version=version),
            original_plan=plan,original_review=original_review,u2_review=u2_review,transition=transition,
            unknown_stock_components=71,demand_event_count=len(before['events']),confirmation_verified=False,
            l1_bom=dict(rows=len(right['items']),population_unknown=sum(n['population']=='unknown' for n in right['items'])),
            u2_fixture_notice='N=100,A=0,S=100 is a separate development fixture, not actual U2 inventory/shortage')


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL');return 2
    r=run_example()
    summary={k:r[k] for k in ('mode','model_api_calls','candidate_reprepared','unknown_stock_components','demand_event_count','confirmation_verified','l1_bom','u2_fixture_notice')}
    summary['source_registration']={k:r['source_registration'][k] for k in ('versions','added_node_ids','meaning')}
    summary['original']={k:r['original_plan'][k] for k in ('planned_input_quantity','initial_quantity','remaining_quantity','conditional_upper_bound','whole_bom_material_limit')}
    summary['original_purchase_review']=r['original_review']['purchase_review']
    summary['u2_candidates']=r['u2_review']['candidates'];summary['states']=r['u2_review']['states'];summary['blockers']=r['transition']['blockers']
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
