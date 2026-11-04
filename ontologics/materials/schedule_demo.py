"""Explicit, synthetic operations master data for the schedule workflow.
Separate business records, effective standard revisions, staffed shifts and inbound promises.
"""
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from .library_schedule import TABLES


def build_dataset(basis,configuration_id,purchase,receipts,now):
    local=now.astimezone(ZoneInfo('Asia/Seoul'));day=local.date()+timedelta(days=1)
    while day.weekday()>4:day+=timedelta(days=1)
    def at(d,h,m=0):return datetime.combine(d,time(h,m),tzinfo=ZoneInfo('Asia/Seoul')).isoformat()
    data={k:[] for k in TABLES};prefix='DEMO-OPS-20261031'
    def source(label):return dict(origin='synthetic',evidence=label+' — 사용자 요청에 따른 테스트 운영 기준. 실제 OLIMEX 가동 실적/표준이 아님.')
    for code,name,staff,unit in [('A','조립·검사 라인 A',2,180),('B','조립·검사 라인 B',3,240)]:
        rid=prefix+'-LINE-'+code
        data['resources'].append(dict(id=rid,name=name,timezone='Asia/Seoul',active=True,**source('설비 대장 '+code)))
        data['routes'].append(dict(id=prefix+'-ROUTE-'+code,configuration_id=configuration_id,resource_id=rid,revision='STD-ESP32-POE-L-WROOM-R1',valid_from=at(local.date(),0),valid_until=at(day+timedelta(days=30),0),setup_minutes=45,unit_seconds=unit,inspection_minutes=30,required_staff=staff,pausable=True,**source('작업표준 R1: 배치 준비+대당 조립/검사+배치 최종검사. 휴게/야간 중단 후 재개 가능 가정')))
        for delta in range(14):
            d=day+timedelta(days=delta)
            if d.weekday()>4:continue
            for tag,start,end in [('AM',9,12),('PM',13,18)]:
                data['shifts'].append(dict(id=f'{prefix}-{code}-{d}-{tag}',resource_id=rid,start_at=at(d,start),end_at=at(d,end),staff_available=staff if code=='A' or tag=='PM' else 1,crew_id=prefix+'-CREW-'+code,shift_name='주간조 '+tag,**source('생산관리 근무계획: 점심12~13시·주말 제외, 라인 전담 근무조')))
        data['blocks'].append(dict(id=prefix+'-PRIOR-'+code,resource_id=rid,start_at=at(day,9),end_at=at(day,11),reason='기존 작업지시 WO-DEMO-001',**source('기존 생산 작업의 라인 점유')))
        if code=='A':data['blocks'].append(dict(id=prefix+'-MAINT-A',resource_id=rid,start_at=at(day+timedelta(days=1),13),end_at=at(day+timedelta(days=1),15),reason='정기 점검 PM-DEMO-001',**source('설비 보전 점검 계획')))
    if purchase:
        for line in basis['lines']:
            if not line['waiting']:continue
            cid=line['component_id'];received=sum(r['payload']['received'] for r in receipts if r['payload']['component_id']==cid)
            available=sum(r['available'] for r in receipts if r['payload']['component_id']==cid)
            requested=next((r['S'] for r in purchase['payload']['lines'] if r['component_id']==cid),0)
            quantity=min(max(0,line['waiting']-available),max(0,requested-received))
            if not quantity:continue
            from hashlib import sha256
            data['arrivals'].append(dict(id=prefix+'-INBOUND-'+sha256((purchase['id']+cid).encode()).hexdigest()[:16],purchase_id=purchase['id'],component_id=cid,quantity=quantity,baseline_received=received,recorded_at=now.isoformat(),expected_at=at(day+timedelta(days=1),10),status='planned',**source('구매 담당 입고 예정표: 도착 예정이며 검수 합격·이번 계획 할당은 별도')))
    return data


def main():
    import argparse
    from ..database import DatabaseSettings
    from ..identity import DevelopmentContext,DevelopmentIdentityProvider
    from ..web import USERS
    from .host import MaterialsHost
    from .contract_library_web import LibraryApplication
    from .library_schedule import inputs
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--contract-id',required=True);parser.add_argument('--schema',default='a_contract_library');args=parser.parse_args()
    context=DevelopmentContext('dev-alice');host=MaterialsHost(DevelopmentIdentityProvider(USERS,enabled=True),DatabaseSettings.from_environment(schema=args.schema));app=LibraryApplication(host,context)
    response=app.fulfillment.inspect(context,args.contract_id,0)
    if not response['ok']:raise RuntimeError('현재 계획 조회 실패')
    data=response['data']
    with host.request(context) as session:
        svc=session._service
        if any(r['id'].startswith('DEMO-OPS-20261031') for r in inputs(svc)['resources']):
            print('기존 테스트 운영 기준을 보존했습니다. 자동 갱신하지 않습니다.');return
        choice=app.rfq.replacements.latest(svc,args.contract_id,0);configuration_id=choice['payload']['result']['configuration_id'];now=datetime.fromisoformat(svc.now())
    bundle=build_dataset(data['basis'],configuration_id,data['purchase'],data['receipts'],now)
    result=app.schedule.store(context,bundle,'demo-schedule-operations-20261031')
    if not result['ok']:raise RuntimeError('운영 기준 등록 실패: '+str(result.get('errors')))
    print('테스트 운영 기준 DB 등록 완료:',{k:len(v) for k,v in bundle.items()})
    print('일정 저장·설비 예약·생산 승인은 실행하지 않았습니다.')

if __name__=='__main__':main()
