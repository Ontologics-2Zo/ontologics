"""Task09: scripted development fixture, actual Runtime/PG, no live model.

Caller must supply a NEW disposable TEST_DATABASE_URL. This example creates no
human confirmation, review, dispatch or operational execution events.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from ontologics.materials.agent import MATERIALS_REGISTRATION, run_saved_planning
from ontologics.runtime import ModelConfig

ROOT=Path(__file__).resolve().parents[1]


class FixtureProvider:
    """Development fixture selecting a real tool; NOT a model quality test."""
    def __init__(self, task, tools):
        self.task=deepcopy(task);self.tools=tools;self.calls=0
        if {t['name'] for t in tools} != set(MATERIALS_REGISTRATION.allowed_tools):
            raise ValueError('Provider tool scope mismatch')

    def public_config(self):
        return {'provider':'task09-development-fixture-not-LLM'}

    def __call__(self, *, model, messages):
        self.calls+=1
        target=self.task['target']
        if self.calls==1 and target.get('configuration_id'):
            return dict(text='',tool_calls=[dict(name='configuration',arguments=dict(
                configuration_id=target['configuration_id'],version=target['data_version']))])
        evidence=[]
        # Read the ACTUAL tool response. Do not synthesize a tool result/trace.
        for message in messages:
            if message['role']=='tool':
                r=json.loads(message['content'])
                def walk(v):
                    if isinstance(v,dict):
                        for k,x in v.items():
                            if k=='evidence' and isinstance(x,list):
                                for e in x:
                                    if isinstance(e,str):evidence.append(dict(tool_run_id=r['run_id'],evidence_id=e))
                            walk(x)
                    elif isinstance(v,list):
                        for x in v:walk(x)
                walk(r['data'])
        answer=dict(deepcopy(self.task),evidence=evidence[:1],error=None)
        return dict(text=json.dumps(answer,ensure_ascii=False),tool_calls=[])


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
            UserIdentity('documents-reader',frozenset({'read'})),
            UserIdentity('documents-reviewer',frozenset({'read','review'}))],enabled=True)
        host=CommonHost(identities,settings,agent_registrations=(MATERIALS_REGISTRATION,));author=DevelopmentContext('documents-author');reader=DevelopmentContext('documents-reader')
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
        from ontologics.materials.document_store import BusinessDocumentWorkflow
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
        result=run_saved_planning(host,workflow,documents,author,demand_id=saved['demand_id'],
            data_version=1,expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'],
            document_refs=saved_documents,provider_factory=FixtureProvider,config=ModelConfig(model='task09-fixture'))
        assert result['accepted'],result['errors']
        exports=[]
        for d in saved_documents:
            view=ok(documents.get_document(author,d['document_ref'],d['document_version']))
            output=ok(documents.export_document(author,d['document_ref'],d['document_version'],
                expected_sha256=d['content_sha256'],output_directory=output_directory,filename=d['kind']+'_v1.md'))
            assert Path(output['path']).read_text(encoding='utf-8')==view['markdown']
            exports.append(output['path'])
        assert workflow.get(reader,saved['demand_id'])['data']==before
        with host.request(reader) as session:assert session.call('lookup',{'version':1})['data']==graph_before
        return dict(mode='DEVELOPMENT FIXTURE: actual Runtime + disposable PG; NOT actual LLM interpretation/tool quality',
            result=result,exports=exports,demand_event_count=len(before['events']),model_api_calls=0,actual_transmissions=0,
            host_workflow=['prepare/save demand (manual interpretation fixture)','create_documents: existing Task02–08 calculation/store','get_document: fresh exact version','export_document: exact saved bytes'])


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared NEW disposable TEST_DATABASE_URL');return 2
    import tempfile
    folder=tempfile.mkdtemp(prefix='task09-agent-documents-')
    result=run_example(folder)
    # The output is a review artifact, not a live operational identifier registry.
    output=Path(folder)/'agent_result.json'
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    r=result['result'];a=r['answer']
    print(json.dumps(dict(mode=result['mode'],status=a['status'],conditional=a['conditional'],
        quantities=a['facts']['quantities'],procurement=a['facts']['procurement'],
        model_selected_tools=[x['name'] for x in r['runtime']['tool_trace']],
        host_workflow=result['host_workflow'],documents=a['documents'],questions=a['questions'],
        exports=result['exports'],result_path=str(output),demand_event_count=0,model_api_calls=0),ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
