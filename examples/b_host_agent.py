"""Existing B roles through CommonHost, with development identity and NO paid API.

Registration is trusted startup code, never model output or HTTP input. Fixtures
use separate fresh development schemas; no draft produced by an Agent is approved.
The original standalone examples and their default actors remain available.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

from examples import b_agent, b_fulfillment_agent
from ontologics.database import DatabaseSettings
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import canonical
from ontologics.openai_provider import MODEL
from ontologics.runtime import ModelConfig
from ontologics.service import Service
from ontologics.tool_specs import B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS


REGISTRATIONS = (
    AgentRegistration('b_intake', b_agent.INTAKE_PROMPT, B_INTAKE_TOOLS),
    AgentRegistration('b_promise', b_agent.PROMISE_PROMPT, B_PROMISE_TOOLS),
    AgentRegistration('b_fulfillment', b_fulfillment_agent.PROMPT, B_FULFILLMENT_TOOLS),
)


def run_scripted_example(mode, *, database=None, identity=None, case='normal', source_path=None):
    """Revalidate existing factual gates under a host-bound development user.

    The audit connection has only this same user's permissions; it cannot fall
    back to the old fixed Agent actor. Scripted oracle reads are test scaffolding,
    not a claim that a real model read or understood a contract.
    """
    if mode not in {'intake', 'promise', 'fulfillment'}:
        raise ValueError('Unknown B mode')
    if case != 'normal' and mode != 'fulfillment':
        raise ValueError('Snapshot cases apply only to fulfillment')
    if source_path is not None and mode != 'intake':
        raise ValueError('Contract source applies only to intake')
    user = identity if identity is not None else UserIdentity('dev-b-author', frozenset({'read', 'draft'}))
    identities = DevelopmentIdentityProvider([user], enabled=True)
    # Only the development fixture owns seed/reviewer/workflow capabilities.
    # Close it before entering the user request; none reach CommonHost.
    if mode == 'intake':
        fixture, task = b_agent.setup_intake(database, source_path=source_path)
    elif mode == 'promise':
        fixture, task = b_agent.setup_promise(database)
    else:
        fixture, task = b_fulfillment_agent.setup(database, case=case)
    schema = fixture.schema
    fixture.close()
    database = database if database is not None else DatabaseSettings.from_environment(schema=schema)
    host = CommonHost(identities, database, agent_registrations=REGISTRATIONS)
    audit = Service(database, principals={user.actor: user.principal()})
    try:
        provider = (b_fulfillment_agent.ScriptedProvider(audit, task) if mode == 'fulfillment'
                    else b_agent.ScriptedProvider(audit, task, actor=user.actor))
        guarded = b_fulfillment_agent.ProvenanceProvider(provider, task) if mode == 'fulfillment' else None
        config = (b_fulfillment_agent.CONFIG if mode == 'fulfillment'
                  else ModelConfig(model=MODEL, max_steps=16))
        role = 'b_' + mode
        with host.request(DevelopmentContext(user.subject)) as request:
            result = request.run_agent(role, guarded or provider,
                [{'role': 'user', 'content': canonical(task)}], config=config, capture_tool_trace=True)
            context = request.context
        evaluate = b_fulfillment_agent.evaluate if mode == 'fulfillment' else b_agent.evaluate
        checks, answer = evaluate(audit, task, result, task['data_version'], actor=user.actor)
        row = audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                               (result['run_id'],)).fetchone()
        registration = next(r for r in REGISTRATIONS if r.name == role)
        checks.update(
            runtime_bound_to_request_user=row['actor'] == context['actor'] == user.actor,
            persisted_role_scope=set(json.loads(row['config'])['allowed_tools']) == registration.allowed_tools,
            output_hash_recorded=bool(result.get('output_hash')) and row['output_hash'] == result['output_hash'])
        return dict(mode=mode, registered_role=role, execution_mode='test_double', case=case,
            database_schema=schema, task=task, request_context=context, runtime=result, answer=answer,
            final_submission=guarded.final_submission if guarded else None,
            evaluation=dict(checks=checks, passed=all(checks.values()), narrative_review='not_yet_reviewed',
                            scope='CommonHost + development identity + fixed synthetic fixtures; not live model or web'),
            confirmed_version_after=audit.lookup(user.actor)['data_version'],
            api_requests=0,
            fixture='Trusted scripted fixture before user request; never approval of an Agent-produced draft')
    finally:
        audit.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['intake', 'promise', 'fulfillment', 'all'], default='all')
    parser.add_argument('--env-file')
    parser.add_argument('--output', required=True, help='New local JSON report; existing files are preserved')
    args = parser.parse_args()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve before seeding anything. A repeated command cannot replace evidence.
    with path.open('x', encoding='utf-8') as output:
        report = dict(execution_mode='test_double', api_requests=0, roles=[])
        try:
            for mode in (('intake', 'promise', 'fulfillment') if args.mode == 'all' else (args.mode,)):
                database = DatabaseSettings.from_environment(
                    schema='b_host_' + mode + '_' + uuid.uuid4().hex, env_file=args.env_file)
                report['roles'].append(run_scripted_example(mode, database=database))
            passed = all(r['evaluation']['passed'] for r in report['roles'])
            report['status'] = 'passed' if passed else 'failed_checks_or_runtime'
        except Exception as exc:
            passed = False
            report.update(status='example_failed', error={'error_type': type(exc).__name__})
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(output=str(path), status=report['status'], api_requests=0,
        roles=[dict(mode=r['mode'], passed=r['evaluation']['passed'],
                    checks=r['evaluation']['checks'], runtime_error=r['runtime']['error'])
               for r in report['roles']]), ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
