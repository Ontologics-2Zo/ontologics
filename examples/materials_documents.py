"""Actual disposable PG, manual interpretation fixture, virtual comparisons only."""
import hashlib
import json
import os
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def run_example(output_directory):
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
        identities=DevelopmentIdentityProvider([UserIdentity('documents-author',frozenset({'read','draft'})),
            UserIdentity('documents-reader',frozenset({'read'}))],enabled=True)
        host=CommonHost(identities,settings);author=DevelopmentContext('documents-author');reader=DevelopmentContext('documents-reader')
        seed=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        try:
            nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
            nodes+=json.loads((folder/'graph_nodes_v1.json').read_text())['nodes']
            assert seed.bootstrap('fixture',nodes,request_key='documents-fixture')['ok']
        finally:seed.close()
        interpreted=reference_interpretation(extract_contract_text(folder/'contract.txt'),data_version=1)
        target=DemandConfirmationWorkflow(host,identities).prepare(author,folder/'contract.txt',interpreted,data_version=1)['target']
        workflow=PersistentDemandConfirmationWorkflow(host,identities,settings)
        saved=workflow.save(author,target,request_key='documents-demand')['data']
        workflow=PersistentDemandConfirmationWorkflow(CommonHost(identities,settings),identities,settings)
        stock_bytes=(folder/'stock.json').read_bytes();doc=json.loads(stock_bytes)
        doc.update(source_ref='input:planning:stock',sha256=hashlib.sha256(stock_bytes).hexdigest())
        scope=dict(doc['stock_scope'],unit='piece',component_lots={r['component_id']:[r['lot_id']] for r in doc['stocks']},
                   coverage_basis='Explicit complete three-component test evaluation only; not real inventory completeness')
        kwargs=dict(demand_id=saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
                    snapshots=[doc],scope=scope,expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'])
        before=workflow.get(reader,saved['demand_id'])['data']
        with host.request(reader) as session:graph_before=session.call('lookup',{'version':1})['data']
        from ontologics.materials.scheduling import estimate_material_ready
        from ontologics.materials.documents import build_saved_business_documents, edit_document_text, export_document_markdown
        production_bytes=(folder/'production.json').read_bytes()
        request=json.loads((folder/'request.json').read_text())
        production_hash=hashlib.sha256(production_bytes).hexdigest()
        assert production_hash==next(x['sha256'] for x in request['external_inputs'] if x['source_ref']=='input:planning:production')=='5973421783a94e122fa5f3b84fb544802cd304e07872506ea875268eee442498'
        resource=json.loads(production_bytes)['production_conditions']
        resource['source']=dict(source_ref='input:planning:production',sha256=production_hash,
            locator='data/production_planning/production.json / production_conditions',origin='synthetic')
        schedule_context=dict(configuration_id='config:esp32-poe:L:WROOM',data_version=1,scenario='virtual_remaining',
            evaluated_at='2026-10-28T09:00:00+09:00',selected_schedules={},milestones={})
        # Separate declared hypothesis; production.json itself supplies NO readiness evidence.
        initial_conditions=dict(state='assumed',scope='all_required_materials_for_initial_quantity',quantity=70,
            ready_at=resource['available_at'],prerequisites=[],source=dict(source_ref='example:initial-readiness-hypothesis',
                sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),locator='run_example / explicit all-material initial hypothesis'))
        result=build_saved_business_documents(workflow,host,reader,initial_quantity=70,resource=resource,
            schedules=[],schedule_context=schedule_context,initial_conditions=initial_conditions,**kwargs)
        assert result['schedule'] is not None
        original_context=dict(schedule_context,scenario='original')
        original=estimate_material_ready(result['plan']['input_materials'],[],original_context,scenario='original')
        assert workflow.get(reader,saved['demand_id'])['data']==before
        with host.request(reader) as session:assert session.call('lookup',{'version':1})['data']==graph_before
        assert before['events']==[] and not before['confirmation']['verified']
        assert result['schedule']['initial']['completion']=='2026-10-26T14:30:00+09:00'
        assert result['schedule']['remaining']['completion'] is None
        assert result['material_ready']['ready_at'] is None and original['ready_at'] is None
        for ready in (original,result['material_ready']):
            assert sorted(i['shortage_quantity'] for i in ready['procurement']['purchase_review'])==[30,240]
            assert len(ready['items'])==74
            assert sum(c['A'] is None for c in ready['procurement']['materials']['components'])==71
        documents=result['documents']
        assert documents[2]['title']=='견적·납기 확인 요청'
        assert all(d['document_ref'] is None and d['document_version'] is None for d in documents)
        documents[2]=edit_document_text(documents[2], {'request_message':
            '정확 원 부품의 견적·MOQ·주문 배수·공급 가능량·납기 및 포함 비용을 확인해 주세요. 발주서가 아닙니다.'})
        exports=[export_document_markdown(d,output_directory,name,expected_sha256=d['content_sha256'])
            for d,name in zip(documents,('생산_계획.md','대체_검토안.md','견적_납기_확인_요청.md'))]
        for d,path in zip(documents,exports):assert Path(path).read_text(encoding='utf-8')==d['markdown']
        result['exports']=exports
        # Identical content is not a new candidate revision or confirmation event.
        repeated=workflow.save(author,target,demand_id=saved['demand_id'],expected_revision=1,request_key='documents-no-change')
        assert not repeated['ok'] and repeated['errors'][0]['code']=='NO_CHANGES'
        return dict(mode='actual_disposable_pg_manual_interpretation_synthetic_stock_separate_initial_hypothesis',
            **result,original_material_ready=original,unknown_stock_components=71,bom_rows=74,population_unknown=11,
            demand_event_count=0,confirmation_verified=False,model_api_calls=0,
            delivery_due=request['contract_item']['delivery_due'],production_source_sha256=production_hash,
            no_changes_code=repeated['errors'][0]['code'])


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL');return 2
    import tempfile
    output=tempfile.mkdtemp(prefix='task08-documents-')
    r=run_example(output)
    print(json.dumps({k:r[k] for k in ('mode','exports','unknown_stock_components','bom_rows',
        'population_unknown','demand_event_count','confirmation_verified','model_api_calls','delivery_due',
        'production_source_sha256','no_changes_code')},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
