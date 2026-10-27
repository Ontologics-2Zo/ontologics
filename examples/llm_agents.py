"""Minimal real-model Product/Quality handoff examples, not full service Agents."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from ontologics.demo import must, run_demo
from ontologics.model import canonical
from ontologics.openai_provider import OpenAIProvider, OpenAISettings, ProviderError
from ontologics.runtime import ModelConfig, ModelRuntime
from ontologics.harness import ExecutionBudget
from ontologics.paid_budget import cost_policy_from_environment
from ontologics.service import Service
from ontologics.database import DatabaseSettings, environment_values
from ontologics.tool_specs import PRODUCT_TOOLS, QUALITY_TOOLS

# Compatibility exports: role-owned definitions live alongside the service Agent.
from ontologics.agents.sample_support import COMMON, example_principals, _successful_calls
from ontologics.agents.product.sample_cli import (
    PRODUCT_PROMPT, setup_product, product_task, evaluate_product, parse_answer as _product_answer,
)
from ontologics.agents.quality.sample_cli import (
    QUALITY_PROMPT, quality_task, evaluate_quality, parse_answer as _quality_answer,
)


def _json_answer(text, role):
    """Compatibility adapter for existing example evaluator imports."""
    return (_product_answer if role == 'product' else _quality_answer)(text)


def evidence_details(service, ids, actor):
    result = []
    for eid in sorted(set(ids)):
        response = service.lookup(actor,node_id=eid)
        if not response['ok'] or response['data'][0]['type'] != 'Evidence': continue
        evidence = response['data'][0]
        source = must(service.lookup(actor,node_id=evidence['source_id']))['data'][0]
        result.append({'evidence_id':eid,'origin':evidence['origin'],'locator':evidence['locator'],
            'source_id':source['id'],'url':source['url'],'path':source['path'],'sha256':source['sha256'],'version':source['version']})
    return result

def run_role(role, settings, directory, *, provider_factory=OpenAIProvider, execution_mode='live_api', database=None, budget=None):
    database = database or DatabaseSettings.from_environment(schema='llm_' + role + '_' + uuid.uuid4().hex)
    if role == 'product':
        service, selected = setup_product(database)
        task = product_task(service, selected, 'llm-product-' + directory.name)
        prompt, tools, actor = PRODUCT_PROMPT, PRODUCT_TOOLS, 'product-agent'
        setup = {'mode':'development_seed','base_version':1,'scope':'two representative already-parsed source rows; not full BOM extraction'}
    else:
        # Trusted fixture setup is fixed, before any model output. It uses the
        # existing approval path with test humans, never approves an LLM draft.
        summary, _ = run_demo(database)
        service = Service(database, principals=example_principals())
        task = quality_task(summary['registered_version'],summary['supplemented_version'])
        prompt, tools, actor = QUALITY_PROMPT, QUALITY_TOOLS, 'quality-agent'
        setup = {'mode':'scripted_host_review_fixture_not_real_human_approval',
            'before_version':task['before_version'],'after_version':task['after_version']}
    try:
        before = service.lookup(actor)['data_version']
        provider = provider_factory(settings,tools)
        runtime = ModelRuntime(service,actor,provider,config=ModelConfig(model=settings.model,max_steps=12),capture_tool_trace=True,budget=budget)
        result = runtime.run([{'role':'system','content':prompt},{'role':'user','content':canonical(task)}])
        if role == 'product': checks, answer = evaluate_product(service,selected,result,before)
        else: checks, answer = evaluate_quality(service,task,result,before)
        ids = service._evidence(answer)
        if isinstance(answer,dict): ids += answer.get('real_design_evidence',[])
        api_calls = getattr(provider,'calls',[])
        return {'role':role,'database_schema':service.schema,'storage':'postgresql+pgvector','execution_mode':execution_mode,'fixture':setup,'provider_config':settings.public_config(),
            'provider_calls':api_calls,'task':task,'runtime':result,'answer':answer,
            'integration':{'api_completed':execution_mode=='live_api' and any(c['status']=='completed' for c in api_calls),
                'model_selected_tools_executed':bool(result.get('tool_trace')),'runtime_completed':result['ok']},
            'evaluation':{'scope':'one narrow example; automatic checks are not a general quality benchmark',
                'automatic_checks':checks,'automatic_pass':all(checks.values()),'narrative_review':'not_yet_reviewed'},
            'evidence_details':evidence_details(service,ids,actor),
            'recorded_runtime':dict(service.db.execute('SELECT id,model,status,config FROM runtime_runs WHERE id=%s',(result['run_id'],)).fetchone())}
    finally: service.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role',choices=['product','quality','both'],default='both')
    parser.add_argument('--env-file',default=None,help='Default .env.local if present, otherwise .env; process env overrides file')
    parser.add_argument('--output-dir',default='artifacts/llm_runs')
    args=parser.parse_args()
    directory=Path(args.output_dir)/('run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    directory.mkdir(parents=True,exist_ok=False)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'execution_mode':'live_api','roles':[],
            'warning':'Public OLIMEX and fictional demo data only. Local role output is untrusted; review before sharing.'}
    try:
        env_file=args.env_file
        values=environment_values(env_file)
        settings=OpenAISettings.from_environment(environ=values)
        # Both example roles share one explicitly configured CLI allowance.
        # This is a service execution limit, not a personal evaluation budget.
        budget=ExecutionBudget(cost_policy=cost_policy_from_environment(values))
        for role in (['product','quality'] if args.role=='both' else [args.role]):
            report['roles'].append(run_role(role,settings,directory,database=DatabaseSettings.from_environment(
                schema='llm_' + role + '_' + uuid.uuid4().hex,env_file=env_file),budget=budget))
        passed=all(r['runtime']['ok'] and r['evaluation']['automatic_pass'] for r in report['roles'])
        report['status']='completed' if passed else 'failed_checks_or_runtime'
    except ProviderError as exc:
        report.update(status='configuration_blocked',error={'code':exc.code,'message':exc.message})
        passed=False
    except Exception as exc:
        report.update(status='example_failed',error={'code':'EXAMPLE_ERROR','error_type':type(exc).__name__})
        passed=False
    output=directory/'report.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'report':str(output),'status':report['status'],'error':report.get('error'),
        'roles':[{'role':r['role'],'integration':r['integration'],'automatic_checks':r['evaluation']['automatic_checks'],
            'runtime_error':r['runtime']['error']} for r in report['roles']]},ensure_ascii=False,indent=2))
    return 0 if passed else 1

if __name__=='__main__': sys.exit(main())
