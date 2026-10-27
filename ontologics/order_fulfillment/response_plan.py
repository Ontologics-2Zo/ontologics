"""Reviewer drafts derived from CQ04, without changing facts or executing work.

The delivery forecaster owns rates, rounding, deadlines and arrival estimates.
This module only classifies its quantities and renders a deterministic draft.
"""
from __future__ import annotations

from .delivery_forecast import delivery_forecast
from ..model import instant

RESPONSE_RULE_VERSION = 'response-0.1'

_QUANTITY_FIELDS = (
    'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity',
    'on_time_new_dispatch_quantity', 'later_new_dispatch_quantity',
    'currently_available_quantity', 'future_quantity', 'unallocated_quantity',
)
_LOGISTICS_FIELDS = ('carrier', 'vehicle', 'detailed_address', 'contact', 'request_number')


def _selected_evidence(forecast):
    """Match CQ04's answer citations while retaining its full integrity evidence."""
    entries = [forecast['promise']['delivery_round'], forecast.get('snapshot'), forecast['condition']]
    entries += forecast['allocations'] + forecast['breakdown'] + forecast['assumptions']
    return sorted({eid for entry in entries if entry for eid in entry['evidence']})


def _dispatch_request(forecast, state):
    contract = forecast['promise']['contract']
    condition = forecast['condition']
    request = {key: forecast[key] for key in (
        'company', 'delivery_round_id', 'configuration_id', 'destination',
        'unit', 'as_of', 'due_at', 'due_basis', 'snapshot_id')}
    request.update(customer_name=contract['customer_name'], contract_id=contract['id'])
    request.update({key: condition[key] if condition else None for key in (
        'dispatch_mode', 'preparation_seconds', 'transport_seconds')})
    request.update({key: None for key in _QUANTITY_FIELDS + _LOGISTICS_FIELDS})
    request['items'] = []
    if state in ('needs_confirmation', 'historical_review'):
        request['planning_status'] = 'historical_only' if state == 'historical_review' else 'not_assessed'
        request['detail'] = (
            '과거 기한의 이행 검토입니다. 새 출고 수량은 미평가이며 빈 항목은 출고 대상이 없다는 확인이 아닙니다.'
            if state == 'historical_review' else
            '전체 계산에 필요한 입력이 미확인입니다. 새 출고 수량은 미평가이며 빈 항목은 배정이 없다는 확인이 아닙니다.')
        return request

    rows = forecast['breakdown']
    unsent = [row for row in rows if row['source_state'] not in ('delivered', 'in_transit')]
    for row in unsent:
        item = {key: row[key] for key in (
            'allocation_id', 'source_id', 'source_state', 'quantity',
            'known_available_quantity', 'on_time_quantity', 'estimated_arrival_at')}
        item.update(later_quantity=row['quantity'] - row['on_time_quantity'],
                    evidence=list(row['evidence']))
        request['items'].append(item)
    new = sum(row['quantity'] for row in unsent)
    on_time = sum(row['on_time_quantity'] for row in unsent)
    available = (sum(row['known_available_quantity'] for row in unsent)
                 if all(row['known_available_quantity'] is not None for row in unsent) else None)
    request.update(
        planning_status='draft',
        delivered_quantity=sum(row['quantity'] for row in rows if row['source_state'] == 'delivered'),
        in_transit_quantity=sum(row['quantity'] for row in rows if row['source_state'] == 'in_transit'),
        new_dispatch_quantity=new, on_time_new_dispatch_quantity=on_time,
        later_new_dispatch_quantity=new - on_time,
        currently_available_quantity=available,
        future_quantity=new - available if available is not None else None,
        unallocated_quantity=forecast['unallocated_quantity'],
        detail='이미 도착한 물량과 실제 출발한 운송 중 물량은 사실 설명이며 새 신청에서 제외합니다. '
               '미출고 배정의 현재 확보량과 미래 생산 예상량을 구분한 검토 초안으로, 출고·도착을 실행하거나 기록하지 않았습니다.')
    return request


def _customer_reply(forecast, request, state):
    prefix = '담당자 검토용·미발송·미등록 초안입니다. '
    ending = ' 고객과 변경 일정은 미합의이며 출고 신청이나 실제 출고·도착을 새로 수행하지 않았습니다.'
    if state == 'needs_confirmation':
        return prefix + (
            '전체 계산에 필요한 자료가 미확인되어 기한 내 도착 수량·부족 수량·전량 도착 예상 시각과 '
            '새 출고 수량을 확정할 수 없습니다. 입력과 확인 범위를 먼저 확인해 주십시오. '
            '미평가는 배정이나 출고 필요가 없다는 뜻이 아닙니다.') + ending
    if state == 'historical_review':
        shortage = forecast['shortfall_quantity']
        assessment = (f"당시 기한 내 도착 {forecast['on_time_quantity']}{forecast['unit']}, "
                      f"당시 부족 {shortage}{forecast['unit']}로 계산되었습니다. "
                      if shortage is not None else '당시 기한의 전체 이행 수량도 미확인입니다. ')
        return prefix + f"기한 {forecast['due_at']}이 기준시점 {forecast['as_of']}보다 이전입니다. " + assessment + (
            '당시 부족은 현재 미이행 잔량을 뜻하지 않습니다. 이후 실제 도착 기록을 포함해 과거 이행을 검토하며 '
            '새 출고·재출고 계획은 만들지 않습니다.') + ending

    unit = forecast['unit']
    mode = '전량 일괄 출고' if request['dispatch_mode'] == 'all_at_once' else '완성 물량부터 분할 출고'
    codes = {assumption['code'] for assumption in forecast['assumptions']}
    conditions = []
    if 'RECENT_TREND_CONTINUES' in codes:
        conditions.append('현재 생산 추세 지속')
    if 'GOOD_UNITS_USABLE' in codes:
        conditions.append('양품 사용 가능')
    conditions += [mode, f"준비 {request['preparation_seconds']}초", f"운송 {request['transport_seconds']}초"]
    if 'UNLIMITED_DISPATCH_CAPACITY' in codes:
        conditions.append('출고 능력 제약 미반영')
    text = (f"{forecast['as_of']} 기준, 이미 도착한 {request['delivered_quantity']}{unit}는 실제 기록이고 "
            f"실제 출발한 {request['in_transit_quantity']}{unit}는 운송 중입니다. "
            f"{'·'.join(conditions)} 조건과 가정이 유지될 때, "
            f"기한 {forecast['due_at']}까지 {forecast['on_time_quantity']}{unit} 도착, "
            f"약속 {forecast['quantity']}{unit} 대비 {forecast['shortfall_quantity']}{unit} 부족으로 예상됩니다. ")
    full = forecast['estimated_full_arrival_at']
    text += f'전량 도착 예상은 {full}입니다. ' if full else '전량 도착 예상 시각은 미확인입니다. '
    text += (f"새 출고 검토는 기존 미출고 배정 {request['new_dispatch_quantity']}{unit}이며 "
             f"현재 확보 {request['currently_available_quantity']}{unit}와 미래 생산 예상 "
             f"{request['future_quantity']}{unit}를 구분합니다. "
             f"이 중 기한 내 도착 검토는 {request['on_time_new_dispatch_quantity']}{unit}, "
             f"기한 후 도착 검토는 {request['later_new_dispatch_quantity']}{unit}입니다. ")
    if request['unallocated_quantity']:
        text += f"미배정 {request['unallocated_quantity']}{unit}는 확보·배정 계획 확인이 필요합니다. "
    if request['new_dispatch_quantity']:
        text += f'{mode}와 운송 계획을 검토해 주십시오. '
    if forecast['shortfall_quantity']:
        text += '부족 물량의 후속 일정은 고객과 협의할 검토 사항입니다. '
    text += '조건부 예상으로 확정 납품이나 실제 도착을 보증하지 않습니다.'
    return prefix + text + ending


def _actions(forecast, request, state, evidence):
    actions = []

    def add(code, detail, quantity=None):
        actions.append(dict(code=code, detail=detail, quantity=quantity, evidence=list(evidence)))

    if state == 'historical_review':
        add('REVIEW_HISTORY', '당시 기한의 실제 이행과 이후 도착 기록을 검토합니다. 당시 부족을 현재 재출고량으로 사용하지 않습니다.')
    elif state == 'needs_confirmation':
        add('CONFIRM_INPUTS', 'forecast.unknowns의 입력·확인 범위를 확인한 뒤 다시 계산합니다. 수치형 출고 계획은 미평가입니다.')
    else:
        if request['new_dispatch_quantity']:
            mode = '전량 일괄' if request['dispatch_mode'] == 'all_at_once' else '완성 물량부터 분할'
            add('REVIEW_DISPATCH', f'기존 미출고 배정의 현재 확보량과 미래 생산량을 구분하여 {mode} 출고·운송을 검토합니다.',
                request['new_dispatch_quantity'])
        if request['in_transit_quantity']:
            add('MONITOR_IN_TRANSIT', '실제 출발한 물량의 예상 도착을 추적하며 새 출고 신청에서 제외합니다.', request['in_transit_quantity'])
        if forecast['shortfall_quantity']:
            add('AGREE_FOLLOWUP', '기한 부족 물량의 후속 일정을 고객과 협의할지 검토합니다. 새 일정 합의나 추가 출고 확정이 아닙니다.',
                forecast['shortfall_quantity'])
        if request['unallocated_quantity']:
            add('CONFIRM_SUPPLY', '미배정 물량의 확보·배정 계획을 확인합니다. 해당 물량이나 도착 시각을 새로 만들지 않습니다.',
                request['unallocated_quantity'])
    return actions


def _confirmations(forecast):
    confirmations = [dict(item, detail='상위 납품 예상에서 미확인된 입력 또는 확인 범위를 확인해야 합니다.')
                     for item in forecast['unknowns']]
    confirmations += [dict(code='CONFIRM_' + field.upper(), field=field,
                           detail='실제 신청 전에 확인할 물류 정보이며 현재 값은 미확인입니다.')
                      for field in _LOGISTICS_FIELDS]
    confirmations.append(dict(code='CUSTOMER_AGREEMENT_REQUIRED',
                              detail='변경 일정에 대한 고객 동의를 확인해야 합니다. 이 초안은 합의 기록이 아닙니다.'))
    confirmations.append(dict(code='DISPATCH_CAPACITY_REQUIRED',
                              detail='차량·작업시간·출고 능력과 계산 가정의 적용 가능 여부를 담당자가 확인해야 합니다.'))
    return confirmations


def response_plan(nodes, delivery_round_id, as_of, *, queried_at):
    """Build one unexecuted review draft from one authoritative CQ04 forecast."""
    forecast = delivery_forecast(nodes, delivery_round_id, as_of, queried_at=queried_at)
    if instant(forecast['due_at']) < instant(forecast['as_of']):
        state = 'historical_review'
    elif forecast['on_time_quantity'] is None or forecast['shortfall_quantity'] is None:
        state = 'needs_confirmation'
    else:
        state = forecast['deadline_status']
    evidence = _selected_evidence(forecast)
    request = _dispatch_request(forecast, state)
    return dict(
        response_rule_version=RESPONSE_RULE_VERSION, assessment_scope='delivery_response_draft',
        synthetic=True, draft_only=True, review_required=True, execution_performed=False,
        response_state=state, forecast=forecast, dispatch_request=request,
        customer_reply=_customer_reply(forecast, request, state),
        actions=_actions(forecast, request, state, evidence),
        confirmations=_confirmations(forecast), evidence=evidence)
