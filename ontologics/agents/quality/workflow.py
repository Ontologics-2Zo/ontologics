"""Quality conditions and investigation workflows through the common host."""
from ...model import canonical, digest
from ...runtime import ModelConfig
from ...service import DomainError
from ...contracts import withhold_agent_result
from ...responses import failure, exact_keys
from ..execution import run_reads, needs_input, finish_screen
from .instructions import INVESTIGATION_PROMPT, CONDITIONS_PROMPT
from .validators import verified_quality_text


class ScopedQualityProvider:
    """Reject model-selected condition/version drift BEFORE any tool execution."""

    def __init__(self, provider, calls):
        self.provider = provider
        self.allowed = {canonical(call) for call in calls}
        self.seen = set()

    def public_config(self):
        return {**self.provider.public_config(), 'scope': 'fixed_screen_conditions_and_versions'}

    def __call__(self, **kwargs):
        response = self.provider(**kwargs)
        calls = response.get('tool_calls', []) if isinstance(response, dict) else []
        # Runtime validates complete shape; this guard adds the screen scope.
        for call in calls:
            if canonical(call) not in self.allowed:
                raise DomainError('AI_CONDITION_MISMATCH', 'AI가 선택한 도구·조건·버전이 화면의 조회와 다릅니다.')
        self.seen.update(canonical(call) for call in calls)
        if isinstance(response, dict) and not calls and self.seen != self.allowed:
            raise DomainError('AI_REQUIRED_TOOLS_MISSING', 'AI가 A/B 공통 도구 조회를 모두 완료하지 않았습니다.')
        return response


def investigate(app, body):
    from ..registry import builtin_task
    exact_keys(body, ('queries', 'version', 'question'), ('before_version',))
    queries = body['queries']
    if (not isinstance(queries, dict) or set(queries) != {'A', 'B'} or
            any(not isinstance(queries[m], dict) or queries[m].get('mode') != m for m in ('A', 'B')) or
            not isinstance(body['question'], str) or not 1 <= len(body['question'].strip()) <= 2000 or
            type(body['version']) is not int or body['version'] < 0 or
            ('before_version' in body and (type(body['before_version']) is not int or body['before_version'] < 0))):
        raise DomainError('INVALID_INPUT', 'A/B 조건·명시적 데이터 버전·질문이 필요합니다.')
    if any(queries['A'].get(k) != queries['B'].get(k) for k in ('unit_ids', 'lot_id', 'exclude_unit_id')):
        raise DomainError('INVALID_INPUT', 'A와 B의 보드 집합·로트·제외 대상이 같아야 합니다.')
    with app.host.request(app.context) as request:
        calls, results = [], {}
        for mode in ('A', 'B'):
            if 'before_version' in body:
                name = 'compare_investigation'
                arguments = {'query': queries[mode], 'before_version': body['before_version'],
                             'after_version': body['version']}
            else:
                name, arguments = 'investigate', {'query': queries[mode], 'version': body['version']}
            result = request.call(name, arguments)
            if not result['ok']:
                return result
            results[mode] = result
            calls.append({'name': name, 'arguments': arguments})
        if app.provider_factory is None:
            return failure('AI_NOT_CONFIGURED', '실제 모델이 설정되지 않았습니다. 공통 도구의 조회 결과를 확인하세요.')
        provider = app.provider_factory((name,))
        scoped = ScopedQualityProvider(provider, calls)
        prompt = INVESTIGATION_PROMPT
        implementation, task = builtin_task('quality', 'investigation', body['question'], body['version'], calls,
            [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': canonical({
                'question': body['question'], 'supplied_calls': calls})}],
            conditions={k: body[k] for k in ('queries', 'before_version') if k in body})
        agent_result = request.run_task('quality', implementation, task, scoped,
            config=ModelConfig(model=app.model, max_steps=6), capture_tool_trace=True)
        runtime = agent_result['runtime']
        trace = runtime.pop('tool_trace', [])
        data = {'runtime': runtime, 'agent_result': agent_result, 'results': results, 'conditions': {
            k: body[k] for k in ('queries', 'version', 'before_version') if k in body},
            'consistency': 'not_verified', 'provider_calls': getattr(provider, 'calls', []),
            'tool_calls': [{'name': t['name'], 'arguments': t['arguments'],
                            'run_id': t['result'].get('run_id')} for t in trace]}
        if not runtime['ok']:
            err = runtime.get('error') or {'code': 'AI_FAILED', 'message': 'AI 설명을 완료하지 못했습니다.'}
            return failure(err['code'], err['message'], data=data)
        # Compare actual model tool outputs to direct reads, not model prose.
        for mode, call in zip(('A', 'B'), calls):
            matching = [t['result'] for t in trace if t['name'] == call['name'] and t['arguments'] == call['arguments']]
            fields = ('ok', 'data', 'data_version', 'evidence', 'conflicts', 'rule_version', 'schema_version')
            if not matching or any(any(r[k] != results[mode][k] for k in fields) for r in matching):
                runtime['text'] = ''
                withhold_agent_result(agent_result, 'AI_RESULT_MISMATCH', 'Direct and Agent reads differ')
                return failure('AI_RESULT_MISMATCH', '직접 조회와 AI 도구 결과의 일치를 확인하지 못했습니다.', data=data)
        try:
            data['display_text'], data['verified_observations'] = verified_quality_text(runtime['text'], results)
            data['display_hash'] = digest(data['display_text'])
            runtime.update(text='', output_withheld=True)
            data['explanation_mode'] = 'verified_template'
        except DomainError as exc:
            runtime['text'] = ''
            withhold_agent_result(agent_result, exc.code, exc.message)
            # A completed model transport is not a verified explanation.
            checked = request.record_agent_check(runtime['run_id'], accepted=False, code=exc.code)
            checked['data'] = data
            return checked
        checked = request.record_agent_check(runtime['run_id'], accepted=True, display_hash=data['display_hash'])
        if not checked['ok']:
            data['display_text'] = ''
            runtime['text'] = ''
            error = checked['errors'][0]
            withhold_agent_result(agent_result, error['code'], error['message'])
            checked['data'] = data
            return checked
        data['consistency'] = 'matched'
        result = dict(results['B'])
        result['data'] = data
        result['run_id'] = runtime['run_id']
        result['evidence'] = sorted(set(results['A']['evidence'] + results['B']['evidence']))
        for field in ('conflicts', 'warnings'):
            result[field] = list({canonical(item): item for mode in ('A', 'B')
                                  for item in results[mode][field]}.values())
        return result


def propose_quality_conditions(app, request, question, version):
    calls = [{'name': 'lookup', 'arguments': {'kind': kind, 'version': version}}
             for kind in ('ProductUnit', 'ComponentLot')]
    result = run_reads(app, request, 'quality', question, calls,
        CONDITIONS_PROMPT, flow='conditions')
    if not result['ok']:
        return result
    proposal = result['data']['agent_result']['payload']['proposal']
    missing = [k for k,v in proposal.items() if v is None or v == []]
    if missing:
        prompts = {'unit_ids': '어떤 개별 보드를 조사하나요? 보드 ID 집합을 알려주세요.',
            'lot_id': '어느 부품 로트를 조사하나요? 정확한 로트 ID 또는 식별 정보를 알려주세요.',
            'at': 'A 기준 시점을 시간대가 있는 ISO 시각으로 알려주세요.',
            'start': 'B 조사 시작 시각과 시간대를 알려주세요.', 'end': 'B 조사 끝 시각과 시간대를 알려주세요. 끝 시각은 제외합니다.'}
        response = needs_input(request, version, 'quality', missing, [prompts[k] for k in missing],
                               'investigation', runtime=result['data']['runtime'], agent_result=result['data']['agent_result'])
        if response['ok']:
            response['data']['partial_conditions'] = proposal
        return response
    base = {'unit_ids': proposal['unit_ids'], 'lot_id': proposal['lot_id']}
    result['data'].update(action='confirm_conditions', proposed_queries={
        'A': {**base, 'mode': 'A', 'at': proposal['at']},
        'B': {**base, 'mode': 'B', 'start': proposal['start'], 'end': proposal['end']}},
        questions=[], target_screen='investigation', consistency='not_executed')
    # JSON proposal is displayed as conditions, never as a completed answer.
    result['data']['runtime'].update(text='', output_withheld=True)
    return result


def run_screen(app, request, body):
    """Use selected conditions or ask for the missing explicit investigation inputs."""
    question, version, context = body['question'], body['version'], body['context']
    if 'queries' not in context:
        return propose_quality_conditions(app, request, question, version)
    query_body = {'question': question, 'version': version, 'queries': context['queries']}
    if 'before_version' in context:
        query_body['before_version'] = context['before_version']
    result = investigate(app, query_body)
    if isinstance(result.get('data'), dict):
        result['data']['role'] = 'quality'
    return finish_screen(result, body)
