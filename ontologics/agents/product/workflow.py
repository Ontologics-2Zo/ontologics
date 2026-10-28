"""Product receipt, draft and configuration flows over common host tools."""
import json

from ...contracts import withhold_agent_result
from ...model import canonical, digest
from ...responses import failure
from ...runtime import ModelConfig
from ...service import DomainError
from .instructions import DRAFT_PROMPT


class ReceiptDraftProvider:
    """Model must inspect the receipt, save that candidate, then validate its exact version."""
    def __init__(self, provider, receipt_call, draft_call):
        self.provider, self.receipt_call, self.draft_call = provider, receipt_call, draft_call

    def public_config(self):
        return {**self.provider.public_config(), 'scope': 'user_requested_receipt_draft'}

    def __call__(self, **kwargs):
        outputs = [m for m in kwargs['messages'] if m['role'] == 'tool']
        if not outputs:
            expected = self.receipt_call
        elif len(outputs) == 1:
            expected = self.draft_call
        elif len(outputs) == 2:
            draft = json.loads(outputs[-1]['content'])['data']
            expected = {'name': 'validate_draft', 'arguments': {'draft_id': draft['draft_id'], 'version': draft['draft_version']}}
        else:
            expected = None
        response = self.provider(**kwargs)
        calls = response.get('tool_calls', []) if isinstance(response, dict) else []
        if expected is None:
            if calls:
                raise DomainError('AI_CONDITION_MISMATCH', '초안 작성·검증 이후 추가 도구 실행을 허용하지 않습니다.')
        elif len(calls) != 1 or canonical(calls[0]) != canonical(expected):
            raise DomainError('AI_CONDITION_MISMATCH', '접수 원본·요청 키·초안 버전 또는 도구 순서가 요청과 다릅니다.')
        return response


def prepare_receipt_draft(app, request, body):
    from ..registry import builtin_task
    from ..execution import needs_input
    context, version = body['context'], body['version']
    receipt_id = context.get('receipt_id')
    if not receipt_id:
        return needs_input(request, version, 'product', ['receipt_id'],
                           ['등록할 원본 또는 이력 보완 자료를 먼저 접수해 주세요.'], 'product')
    key = body.get('request_key')
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise DomainError('INVALID_INPUT', '초안 작성 요청의 재시도 키가 필요합니다.')
    read_call = {'name': 'get_source_receipt', 'arguments': {'receipt_id': receipt_id}}
    receipt = request.call(**{'tool': read_call['name'], 'arguments': read_call['arguments']})
    if not receipt['ok']:
        return receipt
    if receipt['data_version'] != version:
        raise DomainError('AI_CONDITION_MISMATCH', '선택한 접수 후보의 기준 버전과 요청 버전이 다릅니다.')
    if app.provider_factory is None:
        return failure('AI_NOT_CONFIGURED', '모델이 연결되지 않았습니다. 직접 초안 저장은 계속 사용할 수 있습니다.')
    args = {'receipt_id': receipt_id, 'request_key': key}
    if 'draft_id' in context or 'draft_version' in context:
        if not isinstance(context.get('draft_id'), str) or type(context.get('draft_version')) is not int or context['draft_version'] < 1:
            raise DomainError('INVALID_INPUT', '수정할 정확한 초안 ID와 버전이 필요합니다.')
        args.update(draft_id=context['draft_id'], expected_version=context['draft_version'])
    draft_call = {'name': 'prepare_source_draft', 'arguments': args}
    provider = app.provider_factory(('get_source_receipt', 'prepare_source_draft', 'validate_draft'))
    scoped = ReceiptDraftProvider(provider, read_call, draft_call)
    implementation, task = builtin_task('product', 'draft', body['question'], version, [read_call, draft_call], [
        {'role': 'system', 'content': DRAFT_PROMPT},
        {'role': 'user', 'content': canonical({'question': body['question'], 'receipt_call': read_call, 'draft_call': draft_call})},
    ])
    agent_result = request.run_task('product', implementation, task, scoped,
        config=ModelConfig(model=app.model, max_steps=5), capture_tool_trace=True)
    runtime = agent_result['runtime']
    trace = runtime.pop('tool_trace', [])
    writes = [t['result'] for t in trace if t['name'] == 'prepare_source_draft' and t['result']['ok']]
    data = {'role': 'product', 'action': 'draft_not_saved', 'runtime': runtime, 'agent_result': agent_result,
            'consistency': 'not_verified', 'results': [receipt],
            'tool_calls': [{'name': t['name'], 'arguments': t['arguments'], 'run_id': t['result'].get('run_id')} for t in trace],
            'conditions': {'screen': body['screen'], 'version': version, 'context': context}}
    if writes:
        saved = writes[-1]
        ref = {'draft_id': saved['data']['draft_id'], 'version': saved['data']['draft_version']}
        record = request.call('get_draft', ref)
        data.update(draft=saved['data'], draft_record=record['data'] if record['ok'] else None,
                    action='draft_saved_model_failed')
        data['results'] += [saved, record]
        validations = [t['result'] for t in trace if t['name'] == 'validate_draft']
        data['results'] += validations
        if runtime['ok'] and record['ok'] and validations and validations[-1]['ok']:
            data.update(action='draft_prepared', consistency='matched')
            status = record['data']['status']
            valid = validations[-1]['data']['validation']['valid']
            data['display_text'] = ('접수 원본에 연결된 정확한 초안 v' + str(ref['version']) + '을 확인했습니다. '
                '현재 초안 상태: ' + status + '. 등록 조건 검증: ' + ('통과' if valid else '보완 필요') + '. '
                '이 AI 요청은 승인·확정 반영을 수행하지 않습니다. 근거·변경 전후를 확인하고 별도 검토자에게 검토를 요청하세요.')
            runtime.update(text='', output_withheld=True)
            data['display_hash'] = digest(data['display_text'])
            check = request.record_agent_check(runtime['run_id'], accepted=True, display_hash=data['display_hash'])
            data['verification_run_id'] = check.get('run_id')
            if not check['ok']:
                data.update(action='draft_saved_model_failed', consistency='not_verified', display_text='')
                runtime['text'] = ''
                error = check['errors'][0]
                withhold_agent_result(agent_result, error['code'], error['message'])
                return failure(error['code'], '초안은 저장됐지만 검증 기록을 저장하지 못했습니다. 정확한 초안을 확인하고 재시도하세요.', data=data)
            result = dict(saved)
            result.update(data=data, run_id=runtime['run_id'])
            return result
    runtime['text'] = ''
    error = runtime.get('error') or {'code': 'AI_DRAFT_INCOMPLETE', 'message': '초안 저장·검증 전체 절차를 확인하지 못했습니다.'}
    if writes:
        message = '초안은 저장됐지만 모델 설명·검증을 완료하지 못했습니다. 표시된 정확한 초안을 확인하거나 같은 요청으로 재시도하세요.'
    else:
        message = 'AI 초안 작성을 완료하지 못했습니다. 같은 원본·요청 키로 재시도하세요.'
    if runtime['ok']:
        withhold_agent_result(agent_result, error['code'], message)
    return failure(error['code'], message, data=data)


def run_screen(app, request, body):
    from ..execution import run_reads, needs_input, finish_screen
    question, version, context = body['question'], body['version'], body['context']
    role = 'product'
    if context.get('receipt_id') and not context.get('draft_id'):
        receipt = request.call('get_source_receipt', {'receipt_id': context['receipt_id']})
        if not receipt['ok']:
            return receipt
        if receipt['data_version'] != version:
            raise DomainError('AI_CONDITION_MISMATCH', '접수 후보 기준 버전이 다릅니다.')
        calls = [{'name': 'get_source_receipt', 'arguments': {'receipt_id': context['receipt_id']}}]
    elif context.get('draft_id'):
        ref = {'draft_id': context['draft_id'], 'version': context.get('draft_version')}
        draft = request.call('get_draft', ref)
        if not draft['ok']:
            return draft
        record = draft['data']
        selected_version = record['applied_version'] if record['status'] == 'applied' else record['base_version']
        if selected_version != version:
            raise DomainError('AI_CONDITION_MISMATCH', '화면 버전과 선택한 등록안 버전이 다릅니다.')
        calls = [{'name': 'get_draft', 'arguments': ref}]
        if record['status'] != 'applied':
            calls.append({'name': 'validate_draft', 'arguments': ref})
    elif context.get('configuration_id'):
        calls = [{'name': 'configuration', 'arguments': {'configuration_id': context['configuration_id'], 'version': version}}]
    else:
        return needs_input(request, version, role, ['receipt_id_or_configuration_id'],
            ['원본을 접수하거나 설명할 제품 구성·정확한 초안 버전을 선택해 주세요.'], 'product')
    return finish_screen(run_reads(app, request, role, question, calls), body)
