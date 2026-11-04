"""Actual disposable PG, manual interpretation fixture, virtual comparisons only."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from ontologics.materials.planning import build_production_plan, build_saved_production_plan
from ontologics.materials.materials import calculate_material_requirements

ROOT=Path(__file__).resolve().parents[1]


def run_example():
    if not os.environ.get('TEST_DATABASE_URL'): raise ValueError('Explicit disposable TEST_DATABASE_URL required')
    from tests.db import isolated_database
    from ontologics.materials.host import MaterialsHost as CommonHost
    from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
    from ontologics.materials.persistence import MaterialsService as Service
    from ontologics.materials.contract_intake import extract_contract_text
    from ontologics.materials.demand_confirmation import DemandConfirmationWorkflow
    from ontologics.materials.demand_confirmation_store import PersistentDemandConfirmationWorkflow
    from examples.materials_contract_intake import reference_interpretation
    folder=ROOT/'data/materials'
    raw=(folder/'contract.txt').read_bytes()
    expected=json.loads((folder/'request.json').read_text())['external_inputs'][0]['sha256']
    assert hashlib.sha256(raw).hexdigest()==expected=='6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962'
    with isolated_database() as settings:
        identities=DevelopmentIdentityProvider([UserIdentity('plan-author',frozenset({'read','draft'})),
            UserIdentity('plan-reader',frozenset({'read'}))],enabled=True)
        host=CommonHost(identities,settings);author=DevelopmentContext('plan-author');reader=DevelopmentContext('plan-reader')
        seed=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        try:
            nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
            nodes+=json.loads((folder/'graph_nodes_v1.json').read_text())['nodes']
            assert seed.bootstrap('fixture',nodes,request_key='plan-fixture')['ok']
        finally:seed.close()
        interpreted=reference_interpretation(extract_contract_text(folder/'contract.txt'),data_version=1)
        target=DemandConfirmationWorkflow(host,identities).prepare(author,folder/'contract.txt',interpreted,data_version=1)['target']
        workflow=PersistentDemandConfirmationWorkflow(host,identities,settings)
        saved=workflow.save(author,target,request_key='plan-demand')['data']
        workflow=PersistentDemandConfirmationWorkflow(CommonHost(identities,settings),identities,settings)
        stock_bytes=(folder/'stock.json').read_bytes();doc=json.loads(stock_bytes)
        doc.update(source_ref='input:planning:stock',sha256=hashlib.sha256(stock_bytes).hexdigest())
        scope=dict(doc['stock_scope'],unit='piece',component_lots={r['component_id']:[r['lot_id']] for r in doc['stocks']},
                   coverage_basis='Explicit complete three-component test evaluation only; not real inventory completeness')
        kwargs=dict(demand_id=saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
                    snapshots=[doc],scope=scope,expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'])
        before=workflow.get(reader,saved['demand_id'])['data']
        with host.request(reader) as session:graph_before=session.call('lookup',{'version':1})['data']
        comparisons=[build_saved_production_plan(workflow,host,reader,initial_quantity=i,**kwargs)['plan'] for i in (0,30,70)]
        result=build_saved_production_plan(workflow,host,reader,initial_quantity=70,review_state='selected',**kwargs)
        selected=result['plan'];assert selected is not None
        m=result['calculation'];ids={r['bom_item_id'] for r in doc['stocks']}
        rows=[r for c in m['components'] for r in c['bom_rows'] if r['id'] in ids]
        representative=calculate_material_requirements(rows,m['stock'],100,configuration_id=m['configuration_id'],
            data_version=1,demand_source=m['demand_source'],bom_complete=True)
        # True here asserts only the three-row *test* evaluation, not whole product coverage.
        representative_plan=build_production_plan(selected['demand'],representative,70)
        representative_plan['evaluation_notice']='Complete three-row test evaluation; not the whole product BOM'
        assert workflow.get(reader,saved['demand_id'])['data']==before
        with host.request(reader) as session:assert session.call('lookup',{'version':1})['data']==graph_before
        assert selected['remaining_quantity']==30 and selected['whole_bom_material_limit'] is None
        assert len(m['components'])==74 and sum(c['A'] is None for c in m['components'])==71
        assert before['events']==[] and not before['confirmation']['verified']
        return dict(mode='actual_disposable_pg_manual_claims_synthetic_stock',assumptions='Development loss/defect/rework zero only',
            representative=representative_plan,selected=selected,comparisons=comparisons,
            unknown_stock_components=71,bom_rows=74,population_unknown=11,demand_event_count=len(before['events']),
            confirmation_verified=before['confirmation']['verified'],model_api_calls=0,
            query=result['query'],demand_response=result['demand_response'])


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL');return 2
    report=run_example()
    summary={k:v for k,v in report.items() if k not in ('query','demand_response','comparisons','selected','representative')}
    summary['comparisons']=[{k:p[k] for k in ('initial_quantity','remaining_quantity','quantity_class','review_status')} for p in report['comparisons']]
    summary['representative']=[{k:c[k] for k in ('component_id','q','A','D','S','virtual_used_quantity','remaining_available_quantity','remaining_required_quantity','remaining_shortage')} for c in report['representative']['components']]
    summary['selected']={k:report['selected'][k] for k in ('purpose','review_state','confirmation_status','conditional_upper_bound','whole_bom_material_limit','calculation','demand')}
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
