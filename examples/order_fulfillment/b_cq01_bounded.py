"""Opt-in four-response CQ01 transport; interpretation stays with the model.

Only read batches and workflow ordering are constrained here. Existing CQ01
evaluators independently judge the model's draft and answer after execution.
"""
from __future__ import annotations

from copy import deepcopy
import json

from examples.order_fulfillment import b_agent
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import OpenAIProvider, ProviderError
from ontologics.order_fulfillment.tool_specs import function_tools


INTAKE_PROMPT = b_agent.INTAKE_PROMPT.replace(
    '다음 조회를 생략하지 않고 한 번에 하나씩 완료한다. 모든 version은 입력 data_version이다.',
    '첫 응답에서 다음 조회 7개를 모두 함께 호출한다. 모든 version은 입력 data_version이다.',
) + '\n최대4응답: 첫 응답은 위7조회, 둘째는 prepare_draft 하나, 셋째는 반환된 초안의 validate_draft 하나, 넷째는 최종 JSON이다.'
PROMISE_PROMPT = b_agent.PROMISE_PROMPT + (
    '\n최대4응답: 첫째 delivery_round 하나, 둘째 최상위 evidence 전체의 lookup을 함께, '
    '셋째 중복제거한 source_id 전체의 lookup을 함께, 넷째 최종 JSON이다.'
)


def _failure(message):
    return ProviderError('CQ01_BOUNDED_PHASE', message)


class BoundedOpenAIProvider(OpenAIProvider):
    """One opt-in run, four HTTP attempts including failures, no answer repair."""

    def __init__(self, settings, task, *, opener=None):
        if settings.max_output_tokens != 1024 or settings.timeout_seconds != 30:
            raise ProviderError('CQ01_BOUNDED_CONFIG', 'CQ01 requires output1024 and timeout30 explicitly')
        if task.get('mode') not in {'intake', 'promise'} or type(task.get('data_version')) is not int:
            raise _failure('Provide one CQ01 mode and explicit data version')
        self.task = deepcopy(task)
        tools = b_agent.B_INTAKE_TOOLS if task['mode'] == 'intake' else b_agent.B_PROMISE_TOOLS
        super().__init__(settings, tools, opener=opener, parallel_tool_calls=True)
        self._phase_tool = None
        self._phase_batch = False
        self._phase_instructions = ''
        self.final_submission = None

    def _tool_choice(self):
        if self._phase_batch:
            return 'required'
        return {'type': 'function', 'name': self._phase_tool} if self._phase_tool else 'none'

    def _text_format(self):
        return None if self._phase_tool else {'type': 'json_object'}

    def _input(self, messages):
        items = super()._input(messages)
        items.append({'role': 'system', 'content': self._phase_instructions})
        return items

    def _reads(self, ids):
        return [{'node_id': nid, 'version': self.task['data_version']} for nid in ids]

    def _phase(self, messages):
        history = [m for m in messages if m.get('role') == 'tool']
        results = []
        for message in history:
            try:
                result = json.loads(message['content'])
            except (KeyError, TypeError, ValueError):
                raise _failure('Invalid tool history') from None
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise _failure('A failed tool result cannot advance the workflow')
            if message.get('name') in {'lookup', 'delivery_round'} and (
                type(result.get('data_version')) is not int
                or result['data_version'] != self.task['data_version']
            ):
                raise _failure('Read history changed the confirmed data version')
            results.append(result)
        names = [m.get('name') for m in history]
        if self.task['mode'] == 'intake':
            ids = [self.task['source_id'], *self.task['evidence_ids'].values()]
            if len(ids) != 4 or len(set(ids)) != 4:
                raise _failure('Intake requires one source and three distinct Evidence IDs')
            if not history:
                return 'lookup', self._reads(ids) + [
                    {'kind': kind, 'version': self.task['data_version']}
                    for kind in ('ProductModel', 'ProductRevision', 'Configuration')]
            if names == ['lookup'] * 7:
                return 'prepare_draft', None
            if names == ['lookup'] * 7 + ['prepare_draft']:
                prepared = results[-1].get('data', {})
                did, version = prepared.get('draft_id'), prepared.get('draft_version')
                if not isinstance(did, str) or not did or type(version) is not int or version < 1:
                    raise _failure('Prepared draft has no exact ID/version')
                return 'validate_draft', [{'draft_id': did, 'version': version}]
            if names == ['lookup'] * 7 + ['prepare_draft', 'validate_draft']:
                data = results[-1].get('data')
                validation = data.get('validation') if isinstance(data, dict) else None
                if not isinstance(validation, dict) or validation.get('valid') is not True:
                    raise _failure('Invalid draft validation cannot advance to a final response')
                return None, []
            raise _failure('Unexpected intake phase history')
        if not history:
            return 'delivery_round', [{'delivery_round_id': self.task['delivery_round_id'],
                                       'version': self.task['data_version']}]
        if not names or names[0] != 'delivery_round':
            raise _failure('Promise must start with delivery_round')
        ids = results[0].get('evidence')
        if not isinstance(ids, list) or not ids or not all(isinstance(n, str) and n for n in ids) or len(set(ids)) != len(ids):
            raise _failure('Promise evidence must be a nonempty unique ID array')
        if len(history) == 1:
            return 'lookup', self._reads(ids)
        if names[1:] != ['lookup'] * (len(history) - 1) or len(history) < 1 + len(ids):
            raise _failure('Incomplete promise Evidence batch')
        sources, read_evidence = [], set()
        for result in results[1:1 + len(ids)]:
            nodes = result.get('data')
            if (not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict)
                    or nodes[0].get('type') != 'Evidence' or nodes[0].get('id') not in ids
                    or nodes[0]['id'] in read_evidence or not isinstance(nodes[0].get('source_id'), str)
                    or not nodes[0]['source_id']):
                raise _failure('Promise Evidence lookup returned the wrong node')
            read_evidence.add(nodes[0]['id'])
            sources.append(nodes[0]['source_id'])
        sources = list(dict.fromkeys(sources))
        if len(history) == 1 + len(ids):
            return 'lookup', self._reads(sources)
        if len(history) == 1 + len(ids) + len(sources):
            read_sources = set()
            for result in results[1 + len(ids):]:
                nodes = result.get('data')
                if (not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict)
                        or nodes[0].get('type') != 'SourceDocument' or nodes[0].get('id') not in sources
                        or nodes[0]['id'] in read_sources):
                    raise _failure('Promise source lookup returned the wrong node')
                read_sources.add(nodes[0]['id'])
            return None, []
        raise _failure('Unexpected promise phase history')

    def __call__(self, *, model, messages):
        if len(self.calls) >= 4:
            raise ProviderError('LIVE_BUDGET_EXHAUSTED', 'Four-request authorization exhausted')
        name, arguments = self._phase(messages)
        self._phase_tool = name
        self._phase_batch = arguments is not None and len(arguments) > 1
        self.tools = function_tools((name,)) if name else []
        self._phase_instructions = (
            '현재 단계: 아래 도구 호출 전체를 정확히 한 번씩 함께 수행한다. '
            + canonical([{'name': name, 'arguments': a} for a in arguments]) if arguments else
            '현재 단계: 원문과 조회 결과를 해석하여 prepare_draft 하나를 호출한다. '
            '새 초안의 operations는 직접 작성하고 입력 request_key를 사용한다.' if name else
            '현재 단계: 도구 없이 원래 CQ01 계약의 최종 JSON 하나를 출력한다.'
        )
        response = super().__call__(model=model, messages=messages)
        calls = response['tool_calls']
        if arguments is None:
            valid = (len(calls) == 1 and calls[0]['name'] == 'prepare_draft'
                     and set(calls[0]['arguments']) == {'operations', 'request_key'}
                     and isinstance(calls[0]['arguments']['operations'], list)
                     and calls[0]['arguments']['request_key'] == self.task['request_key'])
        else:
            expected = [{'name': name, 'arguments': a} for a in arguments]
            valid = sorted(map(canonical, calls)) == sorted(map(canonical, expected))
        if name is None:
            self.final_submission = {'text': response['text'], 'accepted': valid}
        if not valid:
            raise _failure('Model did not return the exact required phase calls')
        return response
