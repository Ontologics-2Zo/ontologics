"""Free Responses-protocol rehearsal, never a real API or an execution approval.

The common provider, host, runtime and cost ledger are real. Only the HTTP opener
is a deterministic local double. No environment loader or real key is used.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import b_model_connection as connection
from .b_input_task import json_sha256
from .b_run_isolation import checked_path, save
from .b_task_workflow import CASES, case_task, _review_material
from ontologics.database import DatabaseSettings
from ontologics.harness import canonical
from ontologics.model import ROOT
from ontologics.openai_provider import ENDPOINT, MODEL, OpenAISettings
from ontologics.paid_budget import COST_ENV_KEYS, cost_policy_from_environment
from ontologics.service import Service


_execute_case = connection.execute_case


def policy_from_snapshot(snapshot):
    """Only explicit public operator settings, never approval-provided prices."""
    if (not isinstance(snapshot, dict) or set(snapshot) != {'values', 'sha256'}
            or not isinstance(snapshot['values'], dict)
            or set(snapshot['values']) != COST_ENV_KEYS
            or any(type(v) is not str for v in snapshot['values'].values())
            or len(canonical(snapshot['values']).encode()) > 16384
            or snapshot['sha256'] != json_sha256(snapshot['values'])):
        raise ValueError('Use an intact public common cost configuration snapshot')
    policy = cost_policy_from_environment(snapshot['values'])
    if policy is None:
        raise ValueError('Explicit request USD cap and complete model prices required')
    return policy


def load_policy(path):
    target = checked_path(Path(path))
    with target.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('Public cost configuration must fit in 16384 bytes')
    values = json.loads(raw)
    snapshot = dict(values=values, sha256=json_sha256(values))
    policy_from_snapshot(snapshot)
    return snapshot


class _Reply:
    status = 200
    headers = {'x-request-id': 'req_b_preflight_mock'}

    def __init__(self, value):
        self.body = canonical(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, count):
        return self.body[:count]


class ResponseDouble:
    """Sequential tool responses respecting parallel_tool_calls=false.

    Usage numbers are synthetic fixtures, not token estimates or billed usage.
    The original scripted role supplies expected payloads after actual DB reads.
    """
    def __init__(self, role, task):
        self.scripted = role[2](task)
        self.initial = self.scripted(model=MODEL, messages=[])['tool_calls']
        self.requests = []

    def __call__(self, request, timeout):
        body = json.loads(request.data)
        if (request.full_url != ENDPOINT or body['model'] != MODEL
                or body['max_output_tokens'] != 4096
                or body['parallel_tool_calls'] is not False or not 0 < timeout <= 30):
            raise ValueError('Unexpected common provider profile')
        self.requests.append(body)
        results = [item for item in body['input'] if item.get('type') == 'function_call_output']
        index = len(results)
        if index < len(self.initial):
            tool = self.initial[index]
            output = [dict(type='function_call', call_id='call_b_preflight_' + str(index),
                           name=tool['name'], arguments=canonical(tool['arguments']))]
        else:
            value = self.scripted(model=MODEL, messages=[
                dict(role='tool', content=item['output']) for item in results])
            if value['tool_calls']:
                raise ValueError('Expected the scripted final payload')
            output = [dict(type='message', role='assistant', content=[
                dict(type='output_text', text=value['text'])])]
        return _Reply(dict(id='resp_b_preflight_' + str(len(self.requests)), model=MODEL,
            status='completed', output=output,
            usage=dict(input_tokens=100, output_tokens=100, total_tokens=200)))


def run_preflight(*, database, output, cases, policy_snapshot):
    """Rehearse fresh input-only cases; every dispatch uses the local double."""
    if not isinstance(database, DatabaseSettings):
        raise ValueError('Use an explicit fresh development database')
    if (not isinstance(cases, list) or not cases
            or cases != [case for case in CASES if case in cases]):
        raise ValueError('Use unique representatives in the fixed order')
    policy = policy_from_snapshot(policy_snapshot)
    profile = connection.policy_summary(policy, cases)
    output = checked_path(Path(output))
    output.mkdir(mode=0o700)
    save(output / 'run.json', dict(execution_mode='mock_http', cases=cases,
        policy_sha256=policy_snapshot['sha256'], model_connection=profile,
        actual_api_attempts=0, human_review='not_performed', live_authorization_eligible=False))
    audit = None
    reports = []
    active = None
    total = 0
    budget = connection.new_budget(policy, case_count=len(cases))
    try:
        audit = Service(database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        if audit._version() != 0:
            raise ValueError('Use a fresh empty catalog-only database')
        seeded = audit.bootstrap('seed', catalog, request_key='b-model-preflight-catalog')
        if not seeded['ok']:
            raise ValueError('Catalog bootstrap failed')
        version = seeded['data_version']
        host = connection.make_host(database, policy)
        for case in cases:
            active = case
            role, task = case_task(case, version=version)
            directory = output / case
            directory.mkdir()
            data = task.data()
            save(directory / 'input.json', data)
            material = _review_material(case, data)
            if material is not None:
                save(directory / 'review_material.json', material)
            transport = ResponseDouble(role, task)
            # No real key is read; transport cannot perform network operations.
            result, calls = _execute_case(host, case, task, role,
                settings=OpenAISettings('preflight-only-not-a-real-api-key'),
                budget=budget, opener=transport)
            total += len(transport.requests)
            save(directory / 'result.json', dict(execution_mode='mock_http',
                actual_api_attempts=0, mock_http_attempts=len(transport.requests),
                answer_producer='mock_response',
                human_review='not_performed', synthetic_usage=True,
                result=result, provider_calls=calls))
            if (not result['runtime']['ok'] or result['error'] is not None
                    or result['payload'] is None):
                raise ValueError('Common model connection or role validation failed')
            if case == 'cq01_intake' and result['payload']['extraction']['status'] != 'ready':
                raise ValueError('Intake needs correction; later representatives stopped')
            reports.append(dict(case=case, input_sha256=json_sha256(data),
                result_path=case + '/result.json', mock_http_attempts=len(transport.requests)))
        if (audit._version() != version or audit._nodes() != catalog
                or audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n']
                or audit.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n']):
            raise ValueError('Model rehearsal must not change business storage')
        summary = dict(passed=True, status='awaiting_execution_approval',
            execution_mode='mock_http', workflow='input_tasks', cases=cases,
            completed_representatives=len(reports), representatives=reports,
            model_connection=profile, policy_sha256=policy_snapshot['sha256'],
            request_execution_budget=budget.snapshot(), mock_http_attempts=total,
            actual_api_attempts=0, synthetic_usage=True, human_review='not_performed',
            b_business_stored=False, live_quality_verified=False, live_authorization_eligible=False)
        save(output / 'summary.json', summary)
        return summary
    except BaseException as error:
        save(output / 'stopped.json', dict(stopped=True, case=active,
            error_type=type(error).__name__, completed_cases=[r['case'] for r in reports],
            request_execution_budget=budget.snapshot(), mock_http_attempts=total,
            actual_api_attempts=0, automatic_paid_rerun=False))
        raise
    finally:
        if audit is not None:
            audit.close()


def pending_authorization(settings, summary):
    """Preparation only, bound to preserved evidence after owned shutdown."""
    output = Path(settings['output'])
    from .b_run_isolation import sha
    return dict(schema_version='b-model-preparation-1', approved=False,
        status='awaiting_execution_approval', run_id=settings['run_id'],
        source_sha256=settings['source_sha256'], safe_inputs=settings['safe_inputs'],
        policy_sha256=settings['model_preflight']['sha256'],
        profile_sha256=json_sha256(summary['model_connection']),
        input_sha256=json_sha256([r['input_sha256'] for r in summary['representatives']]),
        summary_sha256=sha(output / 'campaign/summary.json'),
        shutdown_sha256=sha(output / 'shutdown.json'),
        actual_api_attempts=0, human_review='not_performed', live_authorization_eligible=False,
        note='Preparation only; a separate explicitly approved live run is required')
