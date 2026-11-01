"""Actual disposable PG, manual interpretation fixture, virtual comparisons only."""
import hashlib
import json
import os
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def run_example(output_directory):
    if not os.environ.get('TEST_DATABASE_URL'): raise ValueError('Explicit disposable TEST_DATABASE_URL required')
    from tests.db import isolated_database
    from ontologics.production_planning.host import PlanningHost as CommonHost
    from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
    from ontologics.production_planning.persistence import PlanningService as Service
    from ontologics.production_planning.contract_intake import extract_contract_text
    from ontologics.production_planning.demand_confirmation import DemandConfirmationWorkflow
    from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow
    from examples.production_planning_contract_intake import reference_interpretation
    folder=ROOT/'data/production_planning'
    raw=(folder/'contract.txt').read_bytes()
    expected=json.loads((folder/'request.json').read_text())['external_inputs'][0]['sha256']
    assert hashlib.sha256(raw).hexdigest()==expected=='6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962'
    with isolated_database() as settings:
        identities=DevelopmentIdentityProvider([UserIdentity('documents-author',frozenset({'read','draft'})),
            UserIdentity('documents-reader',frozenset({'read'})),
            UserIdentity('documents-reviewer',frozenset({'read','review'}))],enabled=True)
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
        from ontologics.production_planning.document_store import BusinessDocumentWorkflow
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
        calculation_arguments=dict(kwargs,initial_quantity=70,resource=resource,schedules=[],
            schedule_context=schedule_context,initial_conditions=initial_conditions)
        documents=BusinessDocumentWorkflow(host,workflow)
        def ok(response):
            assert response['ok'],response.get('errors')
            return response['data']
        saved_documents=ok(documents.create_documents(author,calculation_arguments=calculation_arguments,
            request_key='persistent-documents-create'))['documents']
        # Reconstruct both host and workflow to prove the bodies came from PG.
        reconnected=CommonHost(identities,settings)
        workflow=PersistentDemandConfirmationWorkflow(reconnected,identities,settings)
        documents=BusinessDocumentWorkflow(reconnected,workflow)
        reviewer=DevelopmentContext('documents-reviewer')
        purchase=saved_documents[2]
        original=ok(documents.get_document(author,purchase['document_ref'],1))
        ok(documents.review_document(reviewer,purchase['document_ref'],1,expected_sha256=purchase['content_sha256'],
            decision='approve',note='개발 신원 시험: 정확 문서 내용만 검토. 구매/기술/고객 합의 승인이 아님.',request_key='document-review-v1'))
        purchase=ok(documents.revise_document(author,purchase['document_ref'],expected_version=1,
            edits={'text':{'request_message':'정확 원 부품 견적·MOQ·배수·공급량·납기·포함 비용 확인 요청. 발주서가 아닙니다.'}},
            request_key='document-text-v2'))
        current=ok(documents.get_document(author,purchase['document_ref'],2))
        assert current['state']['review']=='unreviewed' and current['events']==[]
        assert ok(documents.get_document(author,purchase['document_ref'],1))['markdown']==original['markdown']
        saved_documents[2]=purchase
        for n,d in enumerate(saved_documents):
            ok(documents.review_document(reviewer,d['document_ref'],d['document_version'],expected_sha256=d['content_sha256'],
                decision='approve',note='개발용 문서 내용 검토 시험이며 운영 승인 아님',request_key='document-review-final-'+str(n)))
        # Synthetic event fixture only, in this disposable DB. No real message is sent.
        dispatch=dict(recipient='test-only purchasing recipient',sent_at=Service.now(),
            method='development fixture: external email reported, no actual transmission',
            source=dict(source_ref='example:synthetic-manual-dispatch',sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                locator='run_example / synthetic dispatch fixture only'))
        recorded=ok(documents.record_manual_dispatch(author,purchase['document_ref'],2,expected_sha256=purchase['content_sha256'],
            details=dispatch,request_key='document-dispatch-fixture'))
        assert recorded['transmission_performed'] is False
        exports=[];views=[]
        for d,name in zip(saved_documents,('생산_계획_v1.md','대체_검토안_v1.md','견적_납기_확인_요청_v2.md')):
            view=ok(documents.get_document(author,d['document_ref'],d['document_version']));views.append(view)
            output=ok(documents.export_document(author,d['document_ref'],d['document_version'],expected_sha256=d['content_sha256'],
                output_directory=output_directory,filename=name))
            assert Path(output['path']).read_text(encoding='utf-8')==view['markdown'];exports.append(output['path'])
            assert view['payload']['scope']['unknown_stock_components']==71
            assert view['payload']['schedule']['initial']['completion']=='2026-10-26T14:30:00+09:00'
            assert view['payload']['schedule']['remaining']['completion'] is None
            assert view['payload']['procurement']['total_by_currency'] is None
        assert views[2]['state']['dispatch']=='recorded' and views[2]['state']['ordered']=='unknown'
        assert workflow.get(reader,saved['demand_id'])['data']==before
        with host.request(reader) as session:assert session.call('lookup',{'version':1})['data']==graph_before
        assert before['events']==[] and not before['confirmation']['verified']
        return dict(mode='actual_disposable_pg_development_identities_synthetic_dispatch_fixture',
            documents=views,exports=exports,old_version_unchanged=True,demand_event_count=0,
            confirmation_verified=False,model_api_calls=0,actual_transmissions=0,
            unknown_stock_components=71,bom_rows=74,population_unknown=11,delivery_due='2026-11-10')


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL');return 2
    import tempfile
    folder=tempfile.mkdtemp(prefix='task08-persisted-documents-')
    r=run_example(folder)
    summary={k:v for k,v in r.items() if k!='documents'}
    summary['documents']=[{k:d[k] for k in ('document_ref','document_version','content_sha256','state','binding_current')} for d in r['documents']]
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
