"""Bounded one-specialist handoff; no authority delegation."""
import json
from .instructions import ROUTE_PROMPT
import re
from ...service import DomainError
from ..execution import run_reads

def route(app, request, question, version):
    role = 'supervisor'
    # Explicit workflow words constrain a handoff; they never trigger a
    # write. Missing time/identity conditions belong to the specialist.
    route_hint = None
    if re.search(r'(?:등록|접수|구조화)\s*(?:해|하자|하고|하려|요청|진행)|초안.{0,12}(?:작성|저장|만들)', question):
        route_hint = 'product'
    elif any(word in question for word in ('장착', '제거', '로트 사용처', '보완 전후', '보완 전·후')):
        route_hint = 'quality'
    kinds = ('ProductUnit', 'ComponentLot') if route_hint == 'quality' else ('ProductModel',)
    calls = [{'name': 'lookup', 'arguments': {'kind': kind, 'version': version}} for kind in kinds]
    route_constraint = (' 호스트가 명시적 업무 표현으로 제한한 역할은 ' + route_hint +
                        '다. 반드시 이 역할로 인계하고 빠진 조건은 전문 역할이 질문한다.') if route_hint else ''
    routed = run_reads(app, request, role, question, calls,
        ROUTE_PROMPT + route_constraint,
        flow='route', options={'route_hint': route_hint})
    if not routed['ok']:
        return None, routed
    try:
        choice = json.loads(routed['data']['runtime']['text'])
        if (set(choice) != {'role', 'reason'} or choice['role'] not in {'product', 'knowledge', 'quality'} or
                not isinstance(choice['reason'], str) or (route_hint and choice['role'] != route_hint)):
            raise ValueError()
    except (ValueError, TypeError):
        raise DomainError('AI_ROUTING_INVALID', '업무 역할 분류를 확인하지 못했습니다. 해당 업무 화면에서 다시 요청하세요.') from None
    role = choice['role']
    delegation = {'runtime': routed['data']['runtime'], 'agent_result': routed['data']['agent_result'], 'role': role,
        'reason': {'product': '원본 접수·제품/이력 초안 지원으로 연결합니다. 저장은 별도 명시 요청이 필요합니다.',
                   'quality': '로트 장착 이력 조사로 연결합니다. 누락된 대상·A/B 시각은 먼저 확인합니다.',
                   'knowledge': '제품·문서·근거 조회로 연결합니다. 조회 범위와 버전을 유지합니다.'}[role]}
    return delegation, None
