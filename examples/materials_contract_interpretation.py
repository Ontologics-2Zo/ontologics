"""Disposable PG example: scripted by default; --live explicitly invokes OpenAI.

Only the fixed development contract is sent externally. Output is a bounded
summary, never raw source, model response, identity, DB URL or API credentials.
The host saves a candidate, never confirms or applies it.
"""
from __future__ import annotations

import argparse
import json
import os

from examples.materials_contract_intake import ROOT, reference_interpretation, _CONTRACT_SHA
from ontologics.database import environment_values
from ontologics.materials.host import MaterialsHost as CommonHost
from ontologics.paid_budget import cost_policy_from_environment
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.openai_provider import OpenAIProvider, OpenAISettings, ProviderError
from ontologics.materials.contract_intake import extract_contract_text
from ontologics.materials.contract_interpretation import REGISTRATION, interpret_contract
from ontologics.materials.demand_confirmation import DemandConfirmationWorkflow
from ontologics.materials.demand_confirmation_store import PersistentDemandConfirmationWorkflow
from ontologics.runtime import ModelConfig
from ontologics.service import DomainError
from ontologics.materials.persistence import MaterialsService as Service


def run_example(settings, *, live=False, env_file=None):
    path = ROOT/'data/production_planning/contract.txt'
    extracted = extract_contract_text(path)
    if not extracted['supported'] or extracted['source']['sha256'] != _CONTRACT_SHA:
        return dict(status='source_changed', mode='live' if live else 'scripted'), 1
    service = Service(settings, principals={'fixture':{'kind':'human','roles':['seed','read']}})
    try:
        seeded = service.bootstrap('fixture',json.loads((ROOT/'data/catalog.json').read_text())['nodes'],request_key='interpretation-seed')
        if not seeded['ok']: return dict(status='seed_failed'), 1
        version = seeded['data_version']
    finally: service.close()
    identities = DevelopmentIdentityProvider([
        UserIdentity('interpretation-author',frozenset({'read','draft'}))],enabled=True)
    context = DevelopmentContext('interpretation-author')
    values = environment_values(env_file) if live else {}
    host = CommonHost(identities,settings,agent_registrations=(REGISTRATION,),
                      cost_policy=cost_policy_from_environment(values))
    if live:
        provider_settings = OpenAISettings.from_environment(environ=values)
        provider = OpenAIProvider(provider_settings, REGISTRATION.allowed_tools)
        model = provider_settings.model
    else:
        # A hand-authored test response ONLY in explicit default scripted mode.
        # No branch ever falls back here after a live failure.
        manual = reference_interpretation(extracted,data_version=version)
        def provider(**kwargs): return {'text':json.dumps(manual,ensure_ascii=False),'tool_calls':[]}
        model = 'scripted-contract-example'
    with host.request(context) as session:
        result = interpret_contract(session,extracted,data_version=version,provider=provider,
                                    config=ModelConfig(model=model,max_steps=1,max_attempts=1))
    runtime = result['runtime']
    if runtime is None:
        query = result['reference_query']
        envelopes = [query['lookup_response'], *query['configuration_responses']]
        return dict(mode='live_model_not_called' if live else 'scripted_not_called',
            status='query_failed', runtime_started=False, source_sha256=extracted['source']['sha256'],
            data_version=version, model=model, provider_calls=getattr(provider,'calls',[]),
            errors=[{'code':e['code']} for e in query['errors']] +
                [{'code':e['code']} for response in envelopes if response is not None
                 for e in response.get('errors',[])], human_confirmation=False), 1
    summary = dict(mode='live_model' if live else 'scripted_manual_fixture_not_model_interpretation',
        status=result['status'], source_sha256=extracted['source']['sha256'],data_version=version,
        model=model,run_id=runtime['run_id'],runtime_ok=runtime['ok'],runtime_error=runtime['error'],
        output_hash=runtime['output_hash'],provider_calls=getattr(provider,'calls',[]),
        errors=result['errors'],human_confirmation=False,
        reference_query_run_ids=[r['run_id'] for r in [result['reference_query']['lookup_response'],
            *result['reference_query']['configuration_responses']] if r is not None])
    if result['review'] is not None:
        summary['review_status'] = result['review']['status']
        summary['diagnostic_codes'] = sorted({e['code'] for e in result['review']['errors']+result['review']['unresolved']})
    if result['status'] != 'needs_confirmation': return summary, 1
    # Host phase, outside model tools: independently reread source and snapshot.
    try:
        prepared = DemandConfirmationWorkflow(host,identities).prepare(context,path,result['interpretation'],data_version=version)
        if prepared['target'] is None:
            summary['status']='target_failed'; return summary,1
        store = PersistentDemandConfirmationWorkflow(host,identities,settings)
        saved = store.save(context,prepared['target'],request_key='model-candidate-'+runtime['run_id'])
        if not saved['ok']:
            summary.update(status='storage_failed',storage_error_codes=[e['code'] for e in saved['errors']])
            return summary,1
        # Reconnect through the same existing persistent workflow.
        store = PersistentDemandConfirmationWorkflow(CommonHost(identities,settings),identities,settings)
        got = store.get(context,saved['data']['demand_id'],revision=1)
        if not got['ok']:
            summary.update(status='storage_read_failed',storage_error_codes=[e['code'] for e in got['errors']])
            return summary,1
        summary.update(storage=saved['data'],storage_run_id=saved['run_id'],
            persisted_status=got['data']['status'],event_count=len(got['data']['events']),
            confirmation_verified=got['data']['confirmation']['verified'],target_sha256=prepared['target'].sha256)
    except DomainError as exc:
        summary.update(status='target_or_storage_failed',errors=[{'code':exc.code}]); return summary,1
    # Narrow reference-case evaluation, not an extractor or general accuracy score.
    fields=result['demand']['report']['items'][0]['fields'] if len(result['demand']['report']['items'])==1 else {}
    expected={'contract_item_id':'contract-item:planning:001','product_model':'ESP32-POE',
              'configuration_id':'config:esp32-poe:L:WROOM','unit':'piece','revision':'L','variant':'WROOM','ordered_quantity':100,'fulfilled_quantity':0,
              'allocated_quantity':0,'planned_input_quantity':100,'delivery_due':'2026-11-10',
              'constraints':'명시 없음, 사람 확인 필요'}
    summary['reference_checks']={name:fields.get(name,{}).get('state')=='supported' and
        fields[name]['value']==value for name,value in expected.items()}
    summary['reference_checks']['constraints_unconfirmed']=(result['demand']['report']['items'][0]['contract_item_candidate']['constraints_state']=='requires_review' and
        result['demand']['report']['items'][0]['permissions']=={'revision_change':None,'substitution':None})
    summary['reference_checks']['selected_configuration']=(result['demand']['report']['items'][0]['contract_item_candidate']['configuration_id']=='config:esp32-poe:L:WROOM')
    summary['evidence_locations']={name:[{k:ev[k] for k in ('segment_id','start','end')} for claim in
        fields.get(name,{}).get('claims',[]) for ev in claim['evidence']] for name in expected}
    summary['review_scopes']=[{'scope':r['scope'],'availability':r['availability']}
        for item in prepared['review']['items'] for r in item['review_scopes']]
    passed=all(summary['reference_checks'].values()) and summary['event_count']==0 and not summary['confirmation_verified']
    summary['status']='candidate_saved_needs_human_review' if passed else 'candidate_saved_extraction_incomplete'
    return summary,0 if passed else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--env-file',help='Local model settings only; never used to select the database')
    args=parser.parse_args()
    # Explicit identical process settings only; no .env fallback for test DB.
    if not os.environ.get('TEST_DATABASE_URL') or os.environ.get('TEST_DATABASE_URL') != os.environ.get('DATABASE_URL'):
        print(json.dumps({'status':'requires_explicit_disposable_database'})); return 2
    from tests.db import isolated_database
    try:
        with isolated_database() as settings:
            summary,code=run_example(settings,live=args.live,env_file=args.env_file)
    except ProviderError as exc:
        summary,code={'status':'configuration_blocked','error_code':exc.code},2
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return code


if __name__=='__main__': raise SystemExit(main())
