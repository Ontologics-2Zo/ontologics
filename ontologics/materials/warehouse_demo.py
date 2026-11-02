"""Explicitly seed synthetic warehouse receipts through A's validated registration service."""
import argparse
from hashlib import sha256
from ..database import DatabaseSettings
from ..identity import DevelopmentContext,DevelopmentIdentityProvider
from ..web import USERS
from .host import MaterialsHost
from .contract_library_web import LibraryApplication


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract-id',required=True)
    parser.add_argument('--item-index',type=int,default=0)
    parser.add_argument('--schema',default='a_contract_library')
    args=parser.parse_args()
    context=DevelopmentContext('dev-alice')
    host=MaterialsHost(DevelopmentIdentityProvider(USERS,enabled=True),DatabaseSettings.from_environment(schema=args.schema))
    app=LibraryApplication(host,context)
    inspected=app.fulfillment.inspect(context,args.contract_id,args.item_index)
    if not inspected['ok']:raise RuntimeError('현재 계획 조회 실패')
    data=inspected['data'];purchase=data['purchase']
    if not purchase:raise RuntimeError('현재 구매 요청서가 필요합니다.')
    existing=app.fulfillment.warehouse.inspect(context,purchase['id'])['data']['receipts']
    waiting={r['component_id']:r['waiting'] for r in data['basis']['lines']}
    created=0
    for line in purchase['payload']['lines']:
        cid=line['component_id'];reference='DEMO-WH-'+purchase['id']+'-'+sha256(cid.encode()).hexdigest()[:12]
        if any(r['reference']==reference for r in existing):continue
        prior=sum(r['payload']['received'] for r in existing if r['payload']['component_id']==cid)
        qty=min(waiting.get(cid) or 0,line['S']-prior)
        if qty<=0:continue
        # Deliberately partial LAN receipt; all other requested shortages have a full test receipt.
        if 'LAN8710' in line['name']:qty=max(1,qty//2)
        with host.request(context) as session:at=session._service.now()
        details=dict(component_id=cid,received=qty,accepted=qty,rejected=0,pending=0,occurred_at=at,location='테스트 자재창고 · 검수 완료 구역',evidence='사용자 요청으로 등록한 가상 입고·검수 시나리오. 실제 납품/OLIMEX 운영 기록이 아님.',origin='synthetic')
        result=app.fulfillment.warehouse.register(context,purchase['id'],reference,details,reference)
        if not result['ok']:raise RuntimeError('테스트 입고 등록 실패: '+str(result.get('errors')))
        created+=1
    print('테스트 입고 등록:',created,'건. 계획 할당은 실행하지 않았습니다.')


if __name__=='__main__':main()
