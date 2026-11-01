"""Independent B model execution, with optional run-bound reviewed demos.

The default transport is a local Responses double. Live execution is selected
only by an operator and its credentials/network remain owned by the isolated
runner. Optional reviews cover unregistered B input candidates, not common stored
draft approval. Development reviewer identifiers are not production identity.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys

from . import b_model_connection as connection
from .b_input_task import json_sha256
from .b_model_preflight import ResponseDouble, policy_from_snapshot
from .b_run_isolation import ROOT, checked_path, save, sha, source_digest, TASK_INPUTS
from .b_task_workflow import CASES, case_task, _review_material
from ontologics.database import DatabaseSettings
from ontologics.openai_provider import OpenAISettings
from ontologics.service import DomainError, Service


_execute_case = connection.execute_case


def _read(path):
    path = checked_path(Path(path))
    with path.open('rb') as stream:
        data = stream.read(8_000_001)
    if len(data) > 8_000_000:
        raise ValueError('Bounded model evidence required')
    return json.loads(data)


def _proof(directory, names):
    directory = checked_path(Path(directory))
    return dict(path=str(directory), files={name: sha(directory / name) for name in names})


def _check_proof(proof):
    if _proof(proof['path'], proof['files']) != proof:
        raise ValueError('Preserved execution evidence changed')


def _preflight(directory, cases, snapshot):
    directory = checked_path(Path(directory))
    settings = _read(directory / 'settings.json')
    marker = _read(directory / 'preflight_validation.json')
    summary = _read(directory / 'campaign/summary.json')
    shutdown = _read(directory / 'shutdown.json')
    if (marker.get('passed') is not True or shutdown.get('server_stopped') is not True
            or summary.get('passed') is not True or summary.get('execution_mode') != 'mock_http'
            or summary.get('actual_api_attempts') != 0
            or settings.get('workflow') != 'input_tasks' or settings.get('mode') != 'free'
            or settings.get('model_preflight') != snapshot
            or marker.get('source_sha256') != source_digest()
            or settings.get('source_sha256') != marker['source_sha256']
            or marker.get('safe_inputs') != TASK_INPUTS or settings.get('safe_inputs') != TASK_INPUTS
            or marker.get('summary_sha256') != sha(directory / 'campaign/summary.json')
            or marker.get('shutdown_sha256') != sha(directory / 'shutdown.json')
            or not set(cases) <= set(summary.get('cases', []))
            or json_sha256(summary.get('model_connection')) != json_sha256(connection.policy_summary(
                policy_from_snapshot(snapshot), summary['cases']))):
        raise ValueError('Matching successful free preflight and owned shutdown required')
    names = ['settings.json', 'preflight_validation.json', 'campaign/summary.json', 'shutdown.json']
    for case in cases:
        task = case_task(case)[1]
        saved = _read(directory / 'campaign' / case / 'input.json')
        result = _read(directory / 'campaign' / case / 'result.json')
        if (json_sha256(saved) != json_sha256(task.data())
                or result.get('execution_mode') != 'mock_http'
                or result.get('actual_api_attempts') != 0
                or result['result']['runtime']['ok'] is not True
                or result['result']['error'] is not None):
            raise ValueError('Representative preflight input/result mismatch')
        names.extend(['campaign/' + case + '/' + name for name in ('input.json', 'result.json')])
    return _proof(directory, names)


def _after_cq01(directory, transport):
    directory = checked_path(Path(directory))
    settings = _read(directory / 'settings.json')
    summary = _read(directory / 'campaign/summary.json')
    completion = _read(directory / 'model_validation.json')
    shutdown = _read(directory / 'shutdown.json')
    prior = settings['model_run']
    _validate_plan_fields(prior)
    validate_authorization(prior, _read(directory / 'authorization.json'))
    if (prior['transport'] != transport or prior['cases'] != ['cq01_intake']
            or prior['source_sha256'] != source_digest()
            or summary.get('passed') is not True or summary.get('execution_mode') != transport
            or summary.get('human_review') != ('completed' if transport == 'openai' else 'simulated')
            or summary.get('plan_sha256') != prior['plan_sha256']
            or completion.get('passed') is not True or shutdown.get('server_stopped') is not True
            or completion.get('summary_sha256') != sha(directory / 'campaign/summary.json')
            or completion.get('shutdown_sha256') != sha(directory / 'shutdown.json')):
        raise ValueError('A matching reviewed CQ01 run must finish and stop first')
    result = _read(directory / 'campaign/cq01_intake/result.json')
    review = _read(directory / 'campaign/cq01_intake/semantic_review.json')
    business = _read(directory / 'campaign/cq01_intake/business_review.json')
    packet = _read(directory / 'campaign/cq01_intake/review_packet.json')
    _validate_review('semantic', packet, review)
    _validate_review('business', packet, business)
    if (result['result']['runtime']['ok'] is not True
            or result.get('execution_mode') != transport
            or packet.get('execution_mode') != transport
            or any(record.get('execution_mode') != transport
                or record.get('synthetic_review') is not (transport != 'openai')
                or record.get('result_sha256') != packet['result_sha256']
                for record in (review, business))
            or (transport == 'openai' and (type(result.get('actual_api_attempts')) is not int
                or result['actual_api_attempts'] < 1 or result.get('synthetic_usage') is not False))
            or (transport == 'mock_http' and result.get('actual_api_attempts') != 0)
            or result['result']['payload']['extraction']['status'] != 'ready'
            or packet['result_sha256'] != sha(directory / 'campaign/cq01_intake/result.json')
            or packet['draft_sha256'] != json_sha256(result['result']['payload']['extraction']['nodes'])):
        raise ValueError('CQ01 evidence or candidate review changed')
    return _proof(directory, ['settings.json', 'model_validation.json', 'shutdown.json',
        'campaign/summary.json', 'authorization.json', 'campaign/cq01_intake/result.json',
        'campaign/cq01_intake/review_packet.json', 'campaign/cq01_intake/semantic_review.json',
        'campaign/cq01_intake/business_review.json'])


def _after_cq01_technical(directory, transport):
    """Bind an actual automatic success without inventing its missing human reviews."""
    directory = checked_path(Path(directory))
    prior = _read(directory / 'model_plan.json')
    body = dict(prior)
    fingerprint = body.pop('plan_sha256')
    settings = _read(directory / 'settings.json')
    saved = _read(directory / 'campaign/cq01_intake/result.json')
    packet = _read(directory / 'campaign/cq01_intake/review_packet.json')
    shutdown = _read(directory / 'shutdown.json')
    role, task = case_task('cq01_intake')
    result = saved['result']
    payload = result['payload']
    # The old code digest is historical. Current role/input validation still
    # applies; neither an old manifest nor the old result is rewritten.
    if (transport != 'openai' or prior['transport'] != transport
            or prior['cases'] != ['cq01_intake'] or prior['business_storage'] is not False
            or prior['plan_sha256'] != json_sha256(body) or fingerprint != prior['plan_sha256']
            or prior['safe_inputs'] != TASK_INPUTS or settings['model_run'] != prior
            or prior['input_sha256']['cq01_intake'] != json_sha256(task.data())
            or _read(directory / 'campaign/cq01_intake/input.json') != task.data()
            or saved.get('execution_mode') != transport or saved.get('synthetic_usage') is not False
            or type(saved.get('actual_api_attempts')) is not int or saved['actual_api_attempts'] < 1
            or len(saved.get('provider_calls', [])) != saved['actual_api_attempts']
            or not any(call.get('http_status') == 200 for call in saved['provider_calls'])
            or not saved['provider_calls'][-1].get('model_text')
            or result['runtime']['ok'] is not True or result['error'] is not None
            or result['context'] != task.context.to_dict()
            or packet['execution_mode'] != transport
            or packet['result_sha256'] != sha(directory / 'campaign/cq01_intake/result.json')
            or packet['draft_version'] != 1
            or packet['draft_sha256'] != json_sha256(payload['extraction']['nodes'])
            or shutdown.get('server_stopped') is not True
            or payload['extraction']['status'] != 'ready'):
        raise ValueError('Preserved real CQ01 automatic success and owned shutdown required')
    validate_authorization(prior, _read(directory / 'authorization.json'))
    role[0].SPEC.validate_payload(payload)
    if role[1]().validate_result(payload, task, result['runtime']['tool_trace']) is not True:
        raise ValueError('Current CQ01 business/read validation rejected the saved result')
    return _proof(directory, ['model_plan.json', 'settings.json', 'authorization.json', 'shutdown.json',
        'campaign/cq01_intake/input.json', 'campaign/cq01_intake/result.json',
        'campaign/cq01_intake/review_packet.json'])


def _remaining_cases(cases):
    return (isinstance(cases, list) and
            ((len(cases) == 1 and cases[0] in CASES[1:]) or
             any(cases == list(CASES[index:]) for index in range(1, len(CASES)))))


def _representative_cap(value):
    if not isinstance(value, str) or not 0 < len(value) <= 100:
        raise ValueError('Use an explicit representative USD cap string')
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid representative USD cap') from None
    if not amount.is_finite() or not Decimal('0') < amount <= Decimal('1.00'):
        raise ValueError('The representative USD cap must be positive and at most 1.00')
    return value


def build_plan(*, preflight=None, output, cases, policy_snapshot, transport='mock_http', after=None,
               review_mode='human', representative_max_cost_usd='1.00', skip_preflight=False,
               terminal_input=None):
    """Bind one run; review_mode=none is normal execution, others are demos."""
    output = checked_path(Path(output))
    direct = review_mode == 'none'
    if terminal_input is not None and not direct:
        raise ValueError('Terminal inputs use normal execution, separate from fixed demo evidence')
    if (type(skip_preflight) is not bool
            or (direct and (preflight is not None or after is not None or skip_preflight))
            or (skip_preflight and (preflight is not None or review_mode != 'ai_semantic'))
            or (not direct and not skip_preflight and preflight is None)):
        raise ValueError('Explicit AI technical preflight omission or matching free evidence required')
    if (review_mode not in ('none', 'human', 'ai_semantic')
            or (review_mode == 'ai_semantic' and not _remaining_cases(cases))
            or transport not in ('mock_http', 'openai') or not isinstance(cases, list) or not cases
            or cases != [case for case in CASES if case in cases] or output.exists()):
        raise ValueError('A new output and canonical representative sequence are required')
    policy = policy_from_snapshot(policy_snapshot)
    profile = connection.policy_summary(policy, cases, demo=not direct)
    if not profile['first_call_budget_sufficient']:
        raise ValueError('The explicit USD cap cannot reserve the first request')
    if direct or review_mode == 'ai_semantic':
        _representative_cap(representative_max_cost_usd)
        if Decimal(profile['first_call_reservation_usd']) > Decimal(representative_max_cost_usd):
            raise ValueError('The representative USD cap cannot reserve the first request')
    elif representative_max_cost_usd != '1.00':
        raise ValueError('Representative cap overrides require explicit AI technical review mode')
    if (not direct and transport == 'openai' and cases not in (['cq01_intake'], list(CASES[1:]))
            and not (review_mode == 'ai_semantic' and _remaining_cases(cases))):
        raise ValueError('Live sequence: CQ01 intake first, then the remaining nine')
    predecessor = _after_cq01_technical if review_mode == 'ai_semantic' else _after_cq01
    previous = predecessor(after, transport) if after is not None else None
    if not direct and transport == 'openai' and cases != ['cq01_intake'] and previous is None:
        raise ValueError('The remaining nine require a completed live CQ01 run')
    plan = dict(schema_version='b-model-run-1', run_id=str(output), transport=transport,
        cases=list(cases), source_sha256=source_digest(), safe_inputs=TASK_INPUTS,
        policy_snapshot=policy_snapshot, profile=profile,
        input_sha256={case: json_sha256(_plan_task({'terminal_input': terminal_input}, case)[1].data())
                      for case in cases},
        preflight=None if direct or skip_preflight else _preflight(preflight, cases, policy_snapshot), after=previous,
        approved=False, business_storage=False, reviewer_identity='local_development_only')
    if terminal_input is not None:
        plan['terminal_input'] = json.loads(json.dumps(terminal_input))
    if skip_preflight:
        plan['preflight_skipped'] = True
    if direct or review_mode == 'ai_semantic':
        plan.update(review_mode=review_mode, representative_max_cost_usd=representative_max_cost_usd)
    plan['plan_sha256'] = json_sha256(plan)
    return json.loads(json.dumps(plan))


def _plan_task(plan, case, *, version=1):
    if plan.get('terminal_input') is not None:
        from .b_terminal_input import case_task as terminal_task
        return terminal_task(case, plan['terminal_input'], version=version)
    return case_task(case, version=version)


def _validate_plan_fields(plan):
    if not isinstance(plan, dict):
        raise ValueError('An explicit model execution plan is required')
    body = dict(plan)
    fingerprint = body.pop('plan_sha256')
    direct = plan.get('review_mode') == 'none'
    if 'terminal_input' in plan and (not direct or plan['terminal_input'] is None):
        raise ValueError('Terminal input must be explicit and use normal execution')
    if direct:
        if (plan.get('preflight') is not None or plan.get('after') is not None
                or 'preflight_skipped' in plan):
            raise ValueError('Normal execution does not consume demo evidence')
    elif 'preflight_skipped' in plan:
        if (plan['preflight_skipped'] is not True or plan.get('preflight') is not None
                or plan.get('review_mode') != 'ai_semantic'):
            raise ValueError('Preflight omission must be explicit and limited to AI technical execution')
    elif not isinstance(plan.get('preflight'), dict):
        raise ValueError('Matching free preflight evidence required')
    if direct or plan.get('review_mode') == 'ai_semantic':
        _representative_cap(plan.get('representative_max_cost_usd'))
    if (plan.get('review_mode', 'human') not in ('none', 'human', 'ai_semantic')
            or (plan.get('review_mode') == 'ai_semantic' and (
                not _remaining_cases(plan['cases'])))
            or fingerprint != json_sha256(body) or plan['schema_version'] != 'b-model-run-1'
            or plan['transport'] not in ('mock_http', 'openai') or plan['approved'] is not False
            or plan['business_storage'] is not False
            or plan['cases'] != [case for case in CASES if case in plan['cases']]
            or not plan['cases'] or plan['safe_inputs'] != TASK_INPUTS
            or source_digest() != plan['source_sha256']
            or {name: sha(ROOT / name) for name in TASK_INPUTS} != TASK_INPUTS
            or plan['input_sha256'] != {case: json_sha256(_plan_task(plan, case)[1].data()) for case in plan['cases']}
            or json_sha256(plan['profile']) != json_sha256(connection.policy_summary(
                policy_from_snapshot(plan['policy_snapshot']), plan['cases'], demo=not direct))):
        raise ValueError('Run plan/code/input/model/cost profile changed')
    checked_path(Path(plan['run_id']))
    if not direct and plan['transport'] == 'openai' and (
            (plan['cases'] not in (['cq01_intake'], list(CASES[1:]))
             and not (plan.get('review_mode') == 'ai_semantic' and _remaining_cases(plan['cases'])))
            or (plan['cases'] != ['cq01_intake'] and plan['after'] is None)):
        raise ValueError('Live CQ01 must finish before the remaining nine')


def validate_plan(plan):
    _validate_plan_fields(plan)
    if plan.get('review_mode') != 'none' and plan.get('preflight_skipped') is not True:
        _check_proof(plan['preflight'])
        if _preflight(plan['preflight']['path'], plan['cases'], plan['policy_snapshot']) != plan['preflight']:
            raise ValueError('The free preflight does not match the selected run')
    if plan['after'] is not None:
        _check_proof(plan['after'])
        predecessor = _after_cq01_technical if plan.get('review_mode') == 'ai_semantic' else _after_cq01
        if predecessor(plan['after']['path'], plan['transport']) != plan['after']:
            raise ValueError('The earlier CQ01 review does not match')


def _person(value):
    return (isinstance(value, str) and 0 < len(value.strip()) <= 100
            and not any(ord(c) < 32 for c in value))


def authorization(plan, *, approver, confirmed):
    """UI-only record construction; never infers consent from earlier runs."""
    if not _person(approver) or type(confirmed) is not bool:
        raise ValueError('Explicit local operator and boolean decision required')
    record = dict(schema_version='b-model-authorization-1', run_id=plan['run_id'],
        plan_sha256=plan['plan_sha256'], transport=plan['transport'],
        kind='paid_execution' if plan['transport'] == 'openai' else 'mock_confirmation',
        approver=approver.strip(), approved=confirmed)
    if plan.get('review_mode') == 'none':
        record['method'] = 'explicit_cli_command'
    return record


def validate_authorization(plan, record):
    if (not isinstance(record, dict) or record.get('approved') is not True
            or record != authorization(plan, approver=record.get('approver'), confirmed=True)):
        raise ValueError('Fresh explicit approval must match this exact run plan')


def console_line(prompt):
    """Do not prefetch review lines before handing stdin to the child worker."""
    print(prompt, end='', flush=True)
    data = bytearray()
    while True:
        part = os.read(sys.stdin.fileno(), 1)
        if not part:
            raise EOFError('Explicit console input ended')
        if part == b'\n':
            return data.decode('utf-8').rstrip('\r')
        data.extend(part)
        if len(data) > 16384:
            raise ValueError('Bounded console input required')


def confirm(plan, *, ask=console_line, emit=print):
    emit('이번 실행 계획 (모의 기록을 실제 호출 승인으로 재사용하지 않습니다)')
    emit(json.dumps(dict(run=plan['run_id'], mode=plan['transport'], cases=plan['cases'],
        model=plan['profile']['provider_config'], limits=plan['profile']['root_limits'],
        cost_policy=plan['profile']['cost_policy'],
        review_mode=plan.get('review_mode', 'human'),
        preflight_skipped=plan.get('preflight_skipped', False),
        representative_max_cost_usd=plan.get('representative_max_cost_usd'),
        first_reservation_usd=plan['profile']['first_call_reservation_usd']), ensure_ascii=False, indent=2))
    person = ask('실행 승인자 ID (로컬 개발용): ').strip()
    decision = ask('위 설정으로 이번 실행을 승인하면 승인, 취소하면 그 외 입력: ').strip()
    record = authorization(plan, approver=person, confirmed=decision == '승인')
    validate_authorization(plan, record)
    return record


def terminal_review(kind, packet, *, ask=input, emit=print):
    """Display the actual answer and exact material immediately before input."""
    emit('\n대표: ' + packet['case'] + ' / ' + packet['execution_mode'])
    emit('답변 작성: ' + packet['answer_producer'] + '\n한국어 답변:\n' + packet['answer_ko'])
    emit('핵심 근거: ' + json.dumps([
        {k: item[k] for k in ('evidence_id', 'origin', 'data_version') if k in item}
        if isinstance(item, dict) else item for item in packet['evidence']], ensure_ascii=False))
    material = packet['material']
    if isinstance(material, dict) and material.get('kind') == 'deterministic_input_comparison':
        comparison = material['data']['comparison']
        material = dict(python_explanation=material['data']['python_explanation'],
            comparison={k: comparison[k] for k in ('changed_inputs', 'quantity_deltas', 'time_deltas',
                'production_deltas', 'limitations') if k in comparison})
    elif isinstance(material, dict) and material.get('kind') == 'deterministic_input_draft':
        material = {k: material['data'][k] for k in ('customer_reply', 'dispatch_request', 'actions', 'confirmations')}
    emit('입력 원문·계산·후보 자료: ' + json.dumps(material, ensure_ascii=False, indent=2))
    emit('사용량: ' + json.dumps({key: packet['budget'].get(key) for key in ('attempted_calls', 'cost')}, ensure_ascii=False))
    emit('상세 결과·도구 기록: ' + packet['result_path'])
    emit('전체 입력·검토 자료: ' + str(Path(packet['result_path']).parent))
    if kind == 'business':
        emit('별도 업무 검토: 미등록 후보 v' + str(packet['draft_version']) + ' / ' + packet['draft_sha256'])
        emit('공통 DB 초안 승인·확정 저장은 수행하지 않습니다.')
    person = ask('검토자 ID (작성자와 분리): ').strip()
    comment = ask('검토 의견 (직접 작성): ').strip()
    accepted = ask(('정확한 후보 버전 업무 승인' if kind == 'business' else '한국어 의미 검토 통과')
                   + '는 통과, 중단은 그 외 입력: ').strip() == '통과'
    record = dict(reviewer=person, comment=comment, accepted=accepted)
    if kind == 'business':
        record.update(draft_version=packet['draft_version'], draft_sha256=packet['draft_sha256'])
    return record


def _validate_review(kind, packet, record):
    if (not isinstance(record, dict) or record.get('accepted') is not True
            or record.get('reviewer_kind') == 'ai'
            or not _person(record.get('reviewer'))
            or not isinstance(record.get('comment'), str) or not 0 < len(record['comment'].strip()) <= 4000
            or record['reviewer'].strip() in (packet['author'], connection.SUBJECT)
            or (kind == 'business' and (type(record.get('draft_version')) is not int
                or record['draft_version'] != packet['draft_version']
                or record.get('draft_sha256') != packet['draft_sha256']))):
        raise ValueError('Explicit separate reviewer and matching candidate version/hash required')


def terminal_ai_review(kind, packet, *, ask=input, emit=print):
    """AI reviewer supplies an actual opinion; never calls it a human approval."""
    if kind != 'semantic':
        raise ValueError('AI technical review cannot grant candidate business approval')
    emit('\nAI 의미 검토 / 대표: ' + packet['case'])
    emit('실제 답변 (' + packet['answer_producer'] + '):\n' + packet['answer_ko'])
    emit('자료·실제 근거·사용량: ' + packet['result_path'])
    record = dict(reviewer=ask('AI 검토자 ID: ').strip(),
        comment=ask('실제 답변·자료를 대조한 AI 검토 의견: ').strip(),
        accepted=ask('AI 의미 검토 통과는 통과, 중단은 그 외 입력: ').strip() == '통과',
        reviewer_kind='ai')
    return record


def _validate_ai_review(kind, packet, record):
    if kind != 'semantic' or not isinstance(record, dict) or record.get('reviewer_kind') != 'ai':
        raise ValueError('Explicit separate AI semantic review required')
    _validate_review(kind, packet, {key: value for key, value in record.items() if key != 'reviewer_kind'})


def _check_case_cost(budget, starting_cost, cap):
    # Reuse main's exact reported/unknown/in-flight reservations, without
    # recalculating prices or weakening its conservative input reservation.
    cost = budget.snapshot()['cost']
    exposure = sum(Decimal(cost[key]) for key in (
        'reported_cost_usd', 'unreported_reserved_usd', 'in_flight_reserved_usd'))
    if exposure - starting_cost > Decimal(cap):
        raise DomainError('COST_BUDGET_EXCEEDED', 'The representative USD cap cannot reserve this send')


def run_campaign(*, database, output, plan, approval, review=None, emit=print,
                 live_settings=None, live_opener=None):
    validate_plan(plan)
    validate_authorization(plan, approval)
    if not isinstance(database, DatabaseSettings):
        raise ValueError('Fresh owned development database required')
    live = plan['transport'] == 'openai'
    direct = plan.get('review_mode') == 'none'
    ai_review = plan.get('review_mode') == 'ai_semantic'
    if live and (not isinstance(live_settings, OpenAISettings) or not callable(live_opener)):
        raise ValueError('Only the isolated owner can supply a live credential/transport window')
    if not live and (live_settings is not None or live_opener is not None):
        raise ValueError('Mock runs never accept live credentials or transports')
    output = checked_path(Path(output))
    output.mkdir(mode=0o700)
    policy = policy_from_snapshot(plan['policy_snapshot'])
    budget = connection.new_budget(policy, case_count=len(plan['cases']), demo=not direct)
    reports = []
    audit = None
    active = None
    attempts = 0
    transport = None
    save(output / 'run.json', dict(plan_sha256=plan['plan_sha256'], execution_mode=plan['transport'],
        cases=plan['cases'], synthetic_reviews=not live and not direct, automatic_paid_rerun=False))
    try:
        audit = Service(database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        if audit._version() != 0:
            raise ValueError('Use a fresh empty catalog-only database')
        seeded = audit.bootstrap('seed', catalog, request_key='b-model-reviewed-catalog')
        if not seeded['ok']:
            raise ValueError('Catalog bootstrap failed')
        version = seeded['data_version']
        host = connection.make_host(database, policy)
        for index, case in enumerate(plan['cases']):
            active = case
            validate_plan(plan)
            role, task = _plan_task(plan, case, version=version)
            if json_sha256(task.data()) != plan['input_sha256'][case]:
                raise ValueError('The approved input changed')
            directory = output / case
            directory.mkdir()
            data = task.data()
            save(directory / 'input.json', data)
            material = _review_material(case, data)
            if material is not None:
                save(directory / 'review_material.json', material)
            emit('\n[' + str(index + 1) + '/' + str(len(plan['cases'])) + '] ' + case + ' 호출 시작')
            transport = live_opener if live else ResponseDouble(role, task)
            cost = budget.snapshot()['cost']
            starting_cost = Decimal(cost['reported_cost_usd']) + Decimal(cost['unreported_reserved_usd'])
            count = 0
            def counted(request, timeout):
                nonlocal count, attempts
                validate_authorization(plan, approval)
                if direct or ai_review:
                    _check_case_cost(budget, starting_cost, plan['representative_max_cost_usd'])
                count += 1
                attempts += 1
                # Persist before sending; a lost response is never zero usage.
                save(directory / ('request-' + str(count) + '.json'),
                    dict(attempt=count, execution_mode=plan['transport'], actual_api_attempt=live))
                return transport(request, timeout)
            result, calls = _execute_case(host, case, task, role,
                settings=live_settings if live else OpenAISettings('mock-only-no-real-api-key'),
                budget=budget, opener=counted)
            saved = dict(execution_mode=plan['transport'], actual_api_attempts=count if live else 0,
                mock_http_attempts=0 if live else count, synthetic_usage=not live,
                human_review='pending', answer_producer='llm' if live else 'mock_response',
                result=result, provider_calls=calls)
            if direct or ai_review:
                saved.update(human_review='not_performed', business_approval='not_performed',
                    ai_semantic_review='not_performed' if direct else 'pending', validation_scope='technical')
            if case == 'cq01_intake' and result['payload'] is not None:
                status = result['payload']['extraction']['status']
                saved.update(intake_status=status, candidate_ready=status == 'ready')
            save(directory / 'result.json', saved)
            if (not result['runtime']['ok'] or result['error'] is not None or result['payload'] is None
                    or (not direct and case == 'cq01_intake' and result['payload']['extraction']['status'] != 'ready')):
                code = (result.get('error') or {}).get('code', 'B_RESULT_VALIDATION_FAILED')
                emit('실행 실패: ' + case + ' / 오류 코드: ' + code)
                emit('기록: ' + str(directory / 'result.json'))
                emit('후속 호출 중단. 이 실행은 자동 재시도하지 않습니다.')
                raise ValueError('Automatic representative validation failed; no next call')
            payload = result['payload']
            packet = dict(case=case, execution_mode=plan['transport'],
                author='agent:' + role[0].SPEC.name, answer_ko=payload['answer_ko'],
                answer_producer=saved['answer_producer'], evidence=result.get('evidence_refs', []),
                material=material if material is not None else dict(source_text=data.get('source_text'),
                    extraction=payload.get('extraction'), input_sources=data.get('input_sources')),
                budget=budget.snapshot(), result_path=str(directory / 'result.json'),
                result_sha256=sha(directory / 'result.json'))
            if case == 'cq01_intake':
                packet.update(draft_version=1, draft_sha256=json_sha256(payload['extraction']['nodes']))
            save(directory / 'review_packet.json', packet)
            if direct:
                emit(('실제 LLM 답변:\n' if live else '모의 답변:\n') + payload['answer_ko'])
                if case == 'cq01_intake':
                    emit('접수 업무 상태: ' + saved['intake_status'] + ' / 후보 준비: '
                         + ('완료' if saved['candidate_ready'] else '미완료') + ' / 저장·승인: 미수행')
                emit('근거: ' + json.dumps(payload['evidence_ids'], ensure_ascii=False))
                emit('기록: ' + str(directory / 'result.json'))
            for kind in (() if direct else ('semantic', 'business') if case == 'cq01_intake' else ('semantic',)):
                emit('상태: ' + kind + ' 검토 대기 / 다음 행동: 답변·자료 확인 후 직접 검토')
                record = review(kind, packet)
                name = 'ai_semantic_review.json' if ai_review else kind + '_review.json'
                save(directory / name, dict(record,
                    execution_mode=plan['transport'], synthetic_review=not live,
                    result_sha256=packet['result_sha256']))
                (_validate_ai_review if ai_review else _validate_review)(kind, packet, record)
            reports.append(dict(case=case, result_path=case + '/result.json',
                result_sha256=packet['result_sha256'], human_review='not_performed' if direct or ai_review else
                ('completed' if live else 'simulated')))
            if case == 'cq01_intake':
                reports[-1].update(intake_status=saved['intake_status'], candidate_ready=saved['candidate_ready'])
        if (audit._version() != version or audit._nodes() != catalog
                or audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n']
                or audit.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n']):
            raise ValueError('Input-only execution must not change business storage')
        summary = dict(passed=True, execution_mode=plan['transport'], cases=plan['cases'],
            plan_sha256=plan['plan_sha256'], completed_representatives=len(reports), representatives=reports,
            actual_api_attempts=attempts if live else 0, mock_http_attempts=0 if live else attempts,
            human_review='completed' if live else 'simulated', synthetic_usage=not live,
            request_execution_budget=budget.snapshot(), b_business_stored=False,
            live_quality_verified=live, automatic_paid_rerun=False)
        if direct or ai_review:
            summary.update(human_review='not_performed', business_approval='not_performed',
                ai_semantic_review='not_performed' if direct else 'completed',
                validation_scope='technical', live_quality_verified=False)
        intake = next((item for item in reports if item['case'] == 'cq01_intake'), None)
        if intake is not None:
            summary.update(intake_status=intake['intake_status'], intake_candidate_ready=intake['candidate_ready'])
        save(output / 'summary.json', summary)
        emit('선택한 대표 ' + ('실행' if direct else '검토') + ' 완료. 소유 서버 종료 확인 중입니다.')
        return summary
    except BaseException as error:
        save(output / 'stopped.json', dict(stopped=True, case=active,
            error_type=type(error).__name__, completed_cases=[r['case'] for r in reports],
            actual_api_attempts=attempts if live else 0, mock_http_attempts=0 if live else attempts,
            request_execution_budget=budget.snapshot(), automatic_paid_rerun=False))
        raise
    finally:
        if audit is not None:
            audit.close()
