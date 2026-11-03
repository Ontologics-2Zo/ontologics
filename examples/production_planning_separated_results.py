"""Three scripted scenarios using the separate host/model result entry.

Requires explicit disposable PG. No live API option, real confirmation or dispatch.
Fixture setup follows the existing workflow example; business functions are reused.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from examples.production_planning_agent import FixtureProvider as ScriptedProvider
from examples.production_planning_contract_intake import reference_interpretation
from ontologics.production_planning.host import PlanningHost as CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.production_planning.persistence import PlanningService as Service
from ontologics.runtime import ModelConfig
from ontologics.production_planning.agent_workflow import REGISTRATION, interpret_source
from ontologics.production_planning.contract_intake import extract_contract_text
from ontologics.production_planning.demand_confirmation import DemandConfirmationWorkflow
from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow
from ontologics.production_planning.document_store import BusinessDocumentWorkflow

from ontologics.production_planning.agent_result_separation import run_review_preserving_host

ROOT=Path(__file__).resolve().parents[1]


def run_example(output_directory, *, scenario='standard'):
    if scenario not in ('standard','missing_field','stored_conflict'):
        raise ValueError('Unknown scripted scenario')
    if not os.environ.get('TEST_DATABASE_URL') or os.environ['TEST_DATABASE_URL']!=os.environ.get('DATABASE_URL'):
        raise ValueError('Explicit identical disposable test DB environment required')
    from tests.db import isolated_database
    folder=ROOT/'data/production_planning'
    raw=(folder/'contract.txt').read_bytes();request=json.loads((folder/'request.json').read_text())
    expected=next(x['sha256'] for x in request['external_inputs'] if x['source_ref']=='input:planning:contract')
    assert hashlib.sha256(raw).hexdigest()==expected=='6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962'
    with isolated_database() as settings, tempfile.TemporaryDirectory(prefix='task09-source-') as temp:
        identities=DevelopmentIdentityProvider([UserIdentity('documents-author',frozenset({'read','draft'})),
            UserIdentity('documents-reader',frozenset({'read'})),UserIdentity('documents-reviewer',frozenset({'read','review'}))],enabled=True)
        host=CommonHost(identities,settings,agent_registrations=(REGISTRATION,))
        author=DevelopmentContext('documents-author');reader=DevelopmentContext('documents-reader')
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']+json.loads((folder/'graph_nodes_v1.json').read_text())['nodes']
        if scenario=='stored_conflict':
            next(n for n in nodes if n['id']=='contract-item:planning:001')['quantity']=120
        path=Path(temp)/'contract.txt';path.write_bytes(raw)
        seed=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        try:assert seed.bootstrap('fixture',nodes,request_key='followup-seed')['ok']
        finally:seed.close()
        with host.request(reader) as session:initial_graph=session.call('lookup',{'version':1})['data']
        manual=reference_interpretation(extract_contract_text(path),data_version=1)
        interpreted_provider=lambda **kw:dict(text=json.dumps(manual),tool_calls=[])
        model='scripted-separation-demo'
        interpretation=interpret_source(host,author,path,data_version=1,provider=interpreted_provider,
            config=ModelConfig(model=model,max_steps=1,max_attempts=1))
        result=dict(mode='scripted_fixture',scenario=scenario,
            interpretation={k:v for k,v in interpretation.items() if k not in ('interpretation','report','runtime')},
            interpretation_runtime={k:interpretation['runtime'].get(k) for k in ('run_id','ok','status','steps','error','output_hash')} if interpretation['runtime'] else None,
            actual_transmissions=0,exports=[])
        def finish():
            result['model_api_attempts']=0
            with host.request(reader) as session:
                assert session.call('lookup',{'version':1})['data']==initial_graph
            result['business_state_unchanged']=True
            out=Path(output_directory)/'result.json'
            with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
            return result
        if not interpretation['accepted']:
            result.update(status='blocked',host_available=False,model_accepted=False,data_check_only=True,
                message='원문과 저장 수량이 다릅니다. 원문·개정 이력·저장 데이터를 점검해 주세요. 계산과 문서 생성은 중단했습니다.')
            assert scenario=='stored_conflict' and interpretation['data_checks']
            return finish()
        target=DemandConfirmationWorkflow(host,identities).prepare(author,path,interpretation['interpretation'],data_version=1)['target']
        assert target is not None
        workflow=PersistentDemandConfirmationWorkflow(host,identities,settings)
        saved=workflow.save(author,target,request_key='followup-model-candidate')['data']
        before=workflow.get(author,saved['demand_id'])['data']
        with host.request(reader) as session:graph_before=session.call('lookup',{'version':1})['data']
        stock_bytes=(folder/'stock.json').read_bytes();doc=json.loads(stock_bytes)
        doc.update(source_ref='input:planning:stock',sha256=hashlib.sha256(stock_bytes).hexdigest())
        scope=dict(doc['stock_scope'],unit='piece',component_lots={r['component_id']:[r['lot_id']] for r in doc['stocks']},
                   coverage_basis='Explicit complete three-component test evaluation only; not real inventory completeness')
        kwargs=dict(demand_id=saved['demand_id'],item_id='contract-item:planning:001',data_version=1,
                    snapshots=[doc],scope=scope,expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'])
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
        store=BusinessDocumentWorkflow(host,workflow)
        def provider_factory(task,tools):
            provider=ScriptedProvider(task,tools)
            def respond(**kwargs):
                response=provider(**kwargs)
                if scenario=='missing_field' and not response['tool_calls']:
                    answer=json.loads(response['text'])
                    del answer['facts']['schedule']['remaining']['processing_start']
                    response['text']=json.dumps(answer,ensure_ascii=False)
                return response
            return respond
        result['review']=run_review_preserving_host(host,workflow,store,author,
            calculation_arguments=calculation_arguments,request_key='separated-demo-documents',
            provider_factory=provider_factory,config=ModelConfig(model=model,max_steps=4,max_attempts=1))
        review=result['review'];available=review['host_result']['available']
        result.update(status=review['status'],host_available=available,
            model_accepted=review['model_review']['accepted'])
        assert available, review
        answer=review['host_result']['answer'];facts=answer['facts']
        assert tuple(facts['quantities'][k] for k in ('planned_input_quantity','initial_quantity','remaining_quantity'))==(100,70,30)
        assert facts['schedule']['remaining']['processing_start'] is None
        assert facts['scope']['unknown_stock_components']==71
        assert facts['scope']['whole_bom_material_limit'] is None
        assert sorted(i['shortage_quantity'] for i in facts['procurement']['items'])==[30,240]
        assert facts['schedule']['initial']['completion']=='2026-10-26T14:30:00+09:00'
        assert facts['schedule']['remaining']['completion'] is None
        assert all(facts['authority'][k] is False for k in ('production_authorized','purchase_authorized','resource_reserved'))
        result['message']='계산 결과: 초기 70대, 잔여 30대. 잔여 가공 시작 시각은 미정입니다. '
        if scenario=='missing_field':
            assert not result['model_accepted']
            assert review['model_review']['errors']==['PLANNING_FACT_MISMATCH']
            result['message']+='모델 답변에는 항목 누락이 있어 채택하지 않았습니다. 호스트 계산 결과와 문서는 재검증 후 제공합니다.'
        else:
            assert result['model_accepted']
            result['message']+='모델 답변 검사도 통과했습니다. 실제 생산·구매 승인은 아닙니다.'
        for n,d in enumerate(answer['documents']):
            view=store.get_document(author,d['document_ref'],d['document_version'])['data']
            export=store.export_document(author,d['document_ref'],d['document_version'],
                expected_sha256=d['content_sha256'],output_directory=output_directory,filename=str(n)+'-v1.md')
            assert export['ok']
            assert Path(export['data']['path']).read_text()==view['markdown']
            result['exports'].append(export['data']['path'])
        assert workflow.get(author,saved['demand_id'])['data']==before
        with host.request(reader) as session:assert session.call('lookup',{'version':1})['data']==graph_before
        result['business_state_unchanged']=True
        return finish()



def run_suite(output_directory):
    output=Path(output_directory)
    rows=[]
    for scenario in ('standard','missing_field','stored_conflict'):
        folder=output/scenario;folder.mkdir(parents=True,exist_ok=False)
        r=run_example(folder,scenario=scenario)
        rows.append({k:r[k] for k in ('scenario','status','host_available','model_accepted','message','exports','business_state_unchanged')})
    summary=dict(mode='scripted_fixture',model_api_attempts=0,scenarios=rows)
    with (output/'summary.json').open('x',encoding='utf-8') as f:json.dump(summary,f,ensure_ascii=False,indent=2)
    lines=['# 생산계획 결과 분리 실행 예제','',
        '시험용 대역 모델과 폐기형 DB 결과입니다. 실제 모델 품질 검증이나 운영 승인·송부가 아닙니다.',
        '문서 식별자는 정리된 시험 DB의 기록이며 운영 식별자로 재사용하지 않습니다.','']
    for r in rows:
        lines.extend(['## '+r['scenario'],'',r['message'],''])
        lines.extend('- ['+Path(f).name+']('+r['scenario']+'/'+Path(f).name+')' for f in r['exports'])
        lines.append('')
    (output/'summary.md').write_text('\n'.join(lines),encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory',help='New output directory; existing paths are rejected')
    args=parser.parse_args()
    if not os.environ.get('TEST_DATABASE_URL') or os.environ.get('DATABASE_URL')!=os.environ['TEST_DATABASE_URL']:
        parser.error('Explicit identical disposable test DB environment required')
    if args.output_directory:
        output=Path(args.output_directory);output.mkdir(parents=True,exist_ok=False)
    else:output=Path(tempfile.mkdtemp(prefix='task09-separated-demo-'))
    summary=run_suite(output)
    print(json.dumps(dict(output_directory=str(output),**summary),ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
