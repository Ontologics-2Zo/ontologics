"""A-only, explicitly synthetic historical consumption lookup and estimation."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

PATH=Path(__file__).with_name('data')/'process_usage_history_v1.json'
ROOT=Path(__file__).resolve().parents[2]

def number(value):
    if not isinstance(value,str) or len(value)>32:raise ValueError('이력 수량 형식을 확인하세요.')
    v=Decimal(value)
    if not v.is_finite() or not 0<=v<=1000000000 or v.normalize().as_tuple().exponent < -6:
        raise ValueError('이력 수량 범위를 확인하세요.')
    return v

def at(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.utcoffset() is None:raise ValueError('이력 기준 시각에 시간대가 필요합니다.')
    return d

def load_history():
    h=json.loads(PATH.read_text())
    if h.get('format')!='synthetic-process-usage-history/1.0' or h.get('origin')!='synthetic':raise ValueError('가상 사용 이력 형식을 확인하세요.')
    ref=ROOT/'data/production_planning/process_materials_v1.json'
    if hashlib.sha256(ref.read_bytes()).hexdigest()!=h['reference_sha256']:raise ValueError('사용 이력의 원문 기준이 변경되었습니다.')
    ids=set();at(h['as_of'])
    for r in h['records']:
        if r['id'] in ids or r['origin']!='synthetic':raise ValueError('중복 또는 미구분 이력입니다.')
        ids.add(r['id']);at(r['completed_at'])
        if type(r['produced_quantity']) is not int or not 0<r['produced_quantity']<=10000000:raise ValueError('생산 이력 수량을 확인하세요.')
        used=number(r['issued'])-number(r['returned'])-number(r['setup_used'])
        if used<0 or used!=number(r['simulated_variable_used']):raise ValueError('출고·반납·준비량의 합계가 일치하지 않습니다.')
    return h

def estimate(history,cid,mid,route,unit):
    matches=[r for r in history['records'] if r['configuration_id']==cid and r['material_id']==mid and r['route_id']==route
             and r['unit']==unit and r['origin']=='synthetic' and r['status']=='completed'
             and at(r['completed_at'])<=at(history['as_of'])]
    selected=sorted(matches,key=lambda r:(r['completed_at'],r['id']),reverse=True)[:6]
    if len({r['batch_id'] for r in selected})<3:return None
    if len({r['batch_id'] for r in selected})!=len(selected):raise ValueError('같은 배치의 자재 이력이 중복되었습니다.')
    boards=sum(r['produced_quantity'] for r in selected)
    used=sum((number(r['issued'])-number(r['returned'])-number(r['setup_used']) for r in selected),Decimal(0))
    setup=sum((number(r['setup_used']) for r in selected),Decimal(0))/len(selected)
    fmt=lambda v:format(v.quantize(Decimal('.000001'),rounding=ROUND_CEILING).normalize(),'f')
    rates=[(number(r['issued'])-number(r['returned'])-number(r['setup_used']))/r['produced_quantity'] for r in selected]
    return dict(origin='synthetic_history_estimate',base_per_board=fmt(used/boards),setup_quantity=fmt(setup),
        sample_count=len(selected),total_boards=boards,variable_used=fmt(used),unit=unit,route_id=route,as_of=history['as_of'],
        rate_range=[fmt(min(rates)),fmt(max(rates))],records=selected,
        formula='Σ(출고량−반납량−작업 준비량) / Σ생산대수; 준비량은 배치 평균으로 작업당 한 번 추가',
        selection='동일 제품·자재·공정·단위 / 완료 / 기준일 이전 / 최근 최대6배치 / 최소3배치',
        confidence='가상 물리모델 이력의 집계값. 실제 측정 표본이나 통계적 신뢰도 검증 아님.')

def supplement_from_history(reference):
    h=load_history();reference['usage_history']=h
    sid='source:synthetic-process-usage:v1'
    reference['sources'].append(dict(id=sid,type='SourceDocument',origin='synthetic',path=str(PATH.relative_to(ROOT)),url='',
        sha256=hashlib.sha256(PATH.read_bytes()).hexdigest(),license='Project-generated synthetic operational fixture',version='1'))
    for c in reference['per_board_supplement']['configurations']:
        anchor=next(a for a in h['geometry_anchors'] if a['configuration_id']==c['configuration_id'])
        for m in c['materials']:
            if m['base_per_board'] is not None:continue
            found=estimate(h,c['configuration_id'],m['material_id'],h['route_id'],m['unit'])
            if not found:continue
            m.update(base_per_board=found['base_per_board'],setup_quantity=found['setup_quantity'],origin=found['origin'],
                status='가상 생산 이력 기반 추정',history_estimate=found,formula=found['formula'],
                basis=f"가상 생산 이력 {found['sample_count']}배치·{found['total_boards']}대의 수량 가중 평균. {found['selection']}. 실제 관측 이력이 아님. {anchor['assumptions']['scope']}",
                setup_basis='가상 배치의 준비 사용량 평균. 초기 납조 충전 제외; 사용량×여유율에 더해 한 작업에 한 번 반영.')
            m['history_assumptions']=anchor
            for r in found['records']:
                ev=dict(id=r['evidence_id'],type='Evidence',origin='synthetic',source_id=sid,
                    locator='records[id='+r['id']+']',description=r['basis'])
                reference['evidence'].append(ev);m['evidence'].append(ev['id'])
    return reference
