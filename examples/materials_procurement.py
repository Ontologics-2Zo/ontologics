"""Actual disposable PG, manual interpretation fixture, virtual comparisons only."""
import hashlib
import json
import os
from pathlib import Path


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
        identities=DevelopmentIdentityProvider([UserIdentity('procurement-author',frozenset({'read','draft'})),
            UserIdentity('procurement-reader',frozenset({'read'}))],enabled=True)
        host=CommonHost(identities,settings);author=DevelopmentContext('procurement-author');reader=DevelopmentContext('procurement-reader')
        seed=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        try:
            nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
            nodes+=json.loads((folder/'graph_nodes_v1.json').read_text())['nodes']
            assert seed.bootstrap('fixture',nodes,request_key='procurement-fixture')['ok']
        finally:seed.close()
        interpreted=reference_interpretation(extract_contract_text(folder/'contract.txt'),data_version=1)
        target=DemandConfirmationWorkflow(host,identities).prepare(author,folder/'contract.txt',interpreted,data_version=1)['target']
        workflow=PersistentDemandConfirmationWorkflow(host,identities,settings)
        saved=workflow.save(author,target,request_key='procurement-demand')['data']
        workflow=PersistentDemandConfirmationWorkflow(CommonHost(identities,settings),identities,settings)
        stock_bytes=(folder/'stock.json').read_bytes();doc=json.loads(stock_bytes)
        doc.update(source_ref='input:planning:stock',sha256=hashlib.sha256(stock_bytes).hexdigest())
        scope=dict(doc['stock_scope'],unit='piece',component_lots={r['component_id']:[r['lot_id']] for r in doc['stocks']},
                   coverage_basis='Explicit complete three-component test evaluation only; not real inventory completeness')
        kwargs=dict(demand_id=saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
                    snapshots=[doc],scope=scope,expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'])
        before=workflow.get(reader,saved['demand_id'])['data']
        with host.request(reader) as session:graph_before=session.call('lookup',{'version':1})['data']
        from ontologics.materials.procurement import build_saved_procurement_review
        from ontologics.materials.substitution import build_substitution_review
        reports={}
        for scenario in ('original','virtual_remaining'):
            cost_context=dict(evaluated_at='2026-10-28T09:00:00+09:00',data_version=1,
                configuration_id='config:esp32-poe:L:WROOM',scenario=scenario,coverage_complete=False,
                rounding={},costs={},selected_offers={})
            extra={} if scenario=='original' else {'initial_quantity':70}
            result=build_saved_procurement_review(workflow,host,reader,offers=[],cost_context=cost_context,
                scenario=scenario,**extra,**kwargs)
            report=result['procurement'];assert report is not None
            m=report['materials']
            # No new source registration/download and no separate U2 test fixture.
            sub=build_substitution_review(m,[],[],[before['items'][0]['permissions']])
            assert sub['purchase_review']==report['purchase_review']
            assert sorted(i['shortage_quantity'] for i in report['purchase_review'])==[30,240]
            assert report['total_by_currency'] is None and report['known_item_totals']=={}
            assert len(m['components'])==74 and sum(c['A'] is None for c in m['components'])==71
            assert m['whole_bom_material_limit'] is None
            assert not report['purchase_authorized']
            reports[scenario]=report
        assert workflow.get(reader,saved['demand_id'])['data']==before
        with host.request(reader) as session:assert session.call('lookup',{'version':1})['data']==graph_before
        assert before['events']==[] and not before['confirmation']['verified']
        return dict(mode='actual_disposable_pg_manual_claims_synthetic_stock_no_quotes',
            assumptions='Order100/fulfilled0/allocated0/new100 and loss/defect/rework0 are development assumptions',
            **reports,unknown_stock_components=71,bom_rows=74,population_unknown=11,
            demand_event_count=0,confirmation_verified=False,model_api_calls=0,
            demand_response=result['demand_response'],query=result['query'])


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL');return 2
    r=run_example()
    summary={k:v for k,v in r.items() if k not in ('original','virtual_remaining','demand_response','query')}
    for scenario in ('original','virtual_remaining'):
        p=r[scenario]
        summary[scenario]={k:p[k] for k in ('scenario','purchase_authorized','calculation','purchase_review','known_item_totals','total_by_currency')}
        summary[scenario]['known_shortage_items']=[i for i in p['items'] if i['shortage_quantity'] is not None]
        summary[scenario]['demand_source']=p['materials']['demand_source']
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
