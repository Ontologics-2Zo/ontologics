"""Python-owned CQ06 demo prose, derived only from approved comparison results.

This application projection adds no graph facts, approval, or new tool contract.
The model must copy basic_text; it does not author a second customer reply.
"""
from __future__ import annotations

from copy import deepcopy

from ontologics.model import instant

TEXT_LIMITS = dict(change_summary=60, customer_reply=80, dispatch_summary=45, confirmations=35)
CUSTOMER_PREFIX = '가상 자료의 담당자 검토용·미발송·미등록 초안입니다.'


def _view(side):
    if 'response_plan' not in side:
        return side, {**side['production_metrics'], 'estimated_completion_at':side['estimated_completion_at']}
    plan=side['response_plan']
    return {**plan['forecast'], **plan['dispatch_request'], 'as_of':side['as_of']}, side['production'] or {}


def build_explanation(data):
    """Return metric-specific reasons and complete, bounded review-only prose.

    Full and compact inputs are supported, but a supplied python_explanation is
    never trusted or reused. A missing metric stays unknown, including 0 speed.
    """
    if any(data.get(k) is not v for k,v in (
        ('synthetic',True),('draft_only',True),('review_required',True),('execution_performed',False))):
        raise ValueError('CQ06 demo prose requires synthetic, unexecuted review-only data')
    views={side:_view(data[side]) for side in ('before','after')}
    fields=(('currently_available_quantity','현재 확보 수량','대','forecast'),
            ('latest_good_quantity','관측 누계 양품 수량','대','production'),
            ('good_rate_per_hour','최근 정상완료 속도','대/시간','production'),
            ('recent_defect_ratio','최근 불량 비율','비율','production'),
            ('transport_seconds','운송시간','초','condition'),
            ('preparation_seconds','준비시간','초','condition'),
            ('dispatch_mode','출고방식','방식','condition'),
            ('on_time_quantity','기한 내 예상 수량','대','forecast'),
            ('shortfall_quantity','기한 내 부족 수량','대','forecast'),
            ('estimated_completion_at','생산 완료 예상 시각','시각','production'),
            ('estimated_full_arrival_at','전량 도착 예상 시각','시각','forecast'),
            ('as_of','조회 기준 시각','시각','forecast'))
    reasons=[]
    for field,label,unit,section in fields:
        values=[]
        for forecast,production in views.values():
            source=production if section=='production' else (forecast.get('condition') or {}) if section=='condition' else forecast
            values.append(deepcopy(source.get(field)))
        before,after=values
        if before is None or after is None:
            direction='미확인'
        elif before==after:
            direction='동일'
        elif unit=='방식':
            direction='변경'
        else:
            old,new=(instant(before),instant(after)) if unit=='시각' else (before,after)
            direction='증가' if new>old else '감소' if new<old else '동일'
        reasons.append(dict(field=field,label=label,unit=unit,before=before,after=after,direction=direction))
    facts={r['field']:r for r in reasons}
    forecast,production=views['after']
    rate=production.get('good_rate_per_hour')
    uncertain=(data['comparison_status']=='partially_unknown' or rate is None or rate<=0
               or forecast.get('condition') is None)
    # Reasons describe observed inputs; they do not diagnose equipment or blame.
    changed=[]
    for field in ('good_rate_per_hour','transport_seconds','preparation_seconds','dispatch_mode'):
        fact=facts[field]
        if fact['direction'] not in ('동일','미확인'):
            changed.append(fact)
    if rate is None or rate<=0:
        summary='최근 정상완료 속도가 0 또는 미확인이므로 생산 완료 시각 확인이 필요합니다.'
    elif uncertain:
        summary='비교 자료에 미확인 지표가 있어 전후 변화 확인이 필요합니다.'
    elif len(changed)==1:
        fact=changed[0]
        particle='을' if fact['direction']=='변경' else '를'
        summary=f"{fact['label']} {fact['direction']}{particle} 반영해 납품 예상을 재계산했습니다."
    elif changed:
        summary='·'.join(r['label'] for r in changed)+' 변경을 반영해 납품 예상을 재계산했습니다.'
    elif any(r['direction']!='동일' for r in reasons):
        summary='추가 실적·조회 시각을 반영해 납품 예상을 재계산했습니다.'
    else:
        summary='동일한 입력·조회 시각으로 비교해 납품 예상에 변화가 없습니다.'
    on_time,shortfall=forecast.get('on_time_quantity'),forecast.get('shortfall_quantity')
    if on_time is None or shortfall is None:
        reply=CUSTOMER_PREFIX+' 조건부 예상에 미확인 수량이 있어 추가 확인이 필요합니다.'
    else:
        reply=CUSTOMER_PREFIX+f' 조건부 예상은 기한 내 {on_time}대·부족 {shortfall}대입니다.'
    condition=forecast.get('condition')
    if condition is None:
        dispatch='출고조건이 미확인이므로 출고 물량을 확인해야 합니다.'
    elif condition['dispatch_mode']=='all_at_once':
        dispatch='일괄출고 조건의 현재확보·미래생산 물량을 검토합니다.'
    else:
        dispatch='분할출고 조건의 현재확보·미래생산과 기한내·후속 물량을 검토합니다.'
    text=dict(change_summary=summary,customer_reply=reply,dispatch_summary=dispatch,
              confirmations='출고능력·운송조건·고객동의를 담당자가 확인해야 합니다.')
    if any(not value.strip() or '\n' in value or '\r' in value or len(value)>TEXT_LIMITS[key]
           for key,value in text.items()) or sum(map(len,text.values()))>220:
        # No truncation or silently shortened fallback: reject before a paid call.
        raise ValueError('Python CQ06 basic text exceeds its fixed review contract')
    return dict(producer='python',narrative_version='cq06-narrative-0.3',
                reasons=reasons,basic_text=text,
                limitation='관측 지표와 재계산 결과의 비교이며 불량·설비고장 원인이나 납품을 보증하지 않습니다.')
