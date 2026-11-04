"""Disposable PG + manual Task02 claims + real BOM + synthetic stock.

No model calls, operational authentication, demand confirmation or stock writes.
The graph's development registration review is distinct from demand review.
"""
from copy import deepcopy
import hashlib
import json
import os

from examples.production_planning_inputs import ROOT
from examples.production_planning_contract_intake import reference_interpretation
from ontologics.production_planning.contract_intake import extract_contract_text
from ontologics.production_planning.reference_adapter import validate_registered_context
from ontologics.production_planning.materials import available_stock, calculate_material_requirements, calculate_saved_demand


def run_example():
    if not os.environ.get('TEST_DATABASE_URL'):
        raise ValueError('Explicit disposable TEST_DATABASE_URL required')
    from tests.db import isolated_database
    from ontologics.production_planning.host import PlanningHost as CommonHost
    from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
    from ontologics.production_planning.persistence import PlanningService as Service
    from ontologics.production_planning.demand_confirmation import DemandConfirmationWorkflow
    from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow
    folder=ROOT/'data/production_planning'
    with isolated_database() as settings:
        identities=DevelopmentIdentityProvider([
            UserIdentity('materials-author',frozenset({'read','draft'})),
            UserIdentity('materials-reviewer',frozenset({'read','review'})),
            UserIdentity('materials-reader',frozenset({'read'}))],enabled=True)
        host=CommonHost(identities,settings)
        author,reviewer,reader=[DevelopmentContext('materials-'+s) for s in ('author','reviewer','reader')]
        def ok(response):
            if not response['ok']: raise RuntimeError('Development example failed: '+str([e['code'] for e in response['errors']]))
            return response['data']
        seed=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        try: ok(seed.bootstrap('fixture',json.loads((ROOT/'data/catalog.json').read_text())['nodes'],request_key='materials-catalog'))
        finally: seed.close()
        nodes=json.loads((folder/'graph_nodes_v1.json').read_text())['nodes']
        with host.request(author) as session:
            draft=ok(session.call('prepare_draft',dict(operations=[dict(op='put',node=n) for n in nodes],request_key='materials-graph')))
        with host.request(reviewer) as session:
            ok(session.review(draft['draft_id'],draft['draft_version'],decision='approve',
                              note='Synthetic graph registration test only; not demand confirmation',request_key='materials-graph-review'))
        applied=ok(host.apply_approved(draft['draft_id'],draft['draft_version'],request_key='materials-apply'))
        version=applied['applied_version']
        context=json.loads((folder/'request_graph_v1.json').read_text());context['data_version']=version
        names={'input:planning:contract':'contract.txt','input:planning:stock':'stock.json',
               'input:planning:lots:v1':'lot_reference_v1.json','input:planning:production':'production.json'}
        contents={ref:(folder/name).read_bytes() for ref,name in names.items()}
        with host.request(reader) as session:
            registered=validate_registered_context(session,context,external_contents=contents,
                registration_ref={k:draft[k] for k in ('draft_id','draft_version')})
        assert registered['report']['valid'] and not registered['report']['graph_integration_verified']
        interpreted=reference_interpretation(extract_contract_text(folder/'contract.txt'),data_version=version)
        prepared=DemandConfirmationWorkflow(host,identities).prepare(author,folder/'contract.txt',interpreted,data_version=version)
        workflow=PersistentDemandConfirmationWorkflow(host,identities,settings)
        saved=ok(workflow.save(author,prepared['target'],request_key='materials-demand'))
        # Fresh instance ensures consumption is through persisted get, not target dict.
        workflow=PersistentDemandConfirmationWorkflow(CommonHost(identities,settings),identities,settings)
        doc=json.loads(contents['input:planning:stock'])
        doc.update(source_ref='input:planning:stock',sha256=hashlib.sha256(contents['input:planning:stock']).hexdigest())
        scope=dict(doc['stock_scope'],unit='piece',component_lots={r['component_id']:[r['lot_id']] for r in doc['stocks']},
            coverage_basis='Development assumption: listed lots exhaust only these three components at this location; not real inventory completeness')
        result=calculate_saved_demand(workflow,host,reader,demand_id=saved['demand_id'],
            item_id='contract-item:planning:001',data_version=version,snapshots=[doc],scope=scope,
            expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'])
        assert not result['errors'] and result['calculation'] is not None
        full=result['calculation']
        ids={r['bom_item_id'] for r in doc['stocks']}
        detail=next(c for c in result['query']['configurations']['candidates'] if c['configuration']['id']==full['configuration_id'])
        representative=calculate_material_requirements([r for r in detail['items'] if r['id'] in ids],
            available_stock([doc],scope),full['planned_input_quantity'],configuration_id=full['configuration_id'],
            data_version=version,demand_source=full['demand_source'],bom_complete=False)
        expected={'55':(1,100,70,30),'9':(8,800,560,240),'14':(11,1100,1100,0)}
        assert {r['bom_rows'][0]['id'].split('-')[-1]:tuple(r[k] for k in ('q','D','A','S')) for r in representative['components']}==expected
        demand=result['demand_response']['data']
        assert representative['conditional_upper_bound']==70 and full['whole_bom_material_limit'] is None
        assert demand['events']==[] and not demand['confirmation']['verified']
        return dict(mode='actual_disposable_pg_manual_claims_synthetic_stock_no_model_call',
            representative_scope='Explicit three-row test evaluation only, not complete product BOM',
            representative=representative,whole_bom=full,
            bom_summary=dict(rows=len(detail['items']),population_counts={p:sum(r['population']==p for r in detail['items'])
                for p in ('populated','not_populated','unknown')},
                missing_or_unknown_stock_components=sum(c['A'] is None for c in full['components'])),
            registered_validation=registered,
            demand={k:deepcopy(demand[k]) for k in ('demand_id','revision','target_sha256','status','confirmation','events')},
            query=result['query'])


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL');return 2
    report=run_example()
    # Retain source IDs/row evidence and all diagnostics in the calculation;
    # public envelopes remain available in run_example, without printing full catalog.
    print(json.dumps({k:v for k,v in report.items() if k not in ('query','registered_validation')},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
