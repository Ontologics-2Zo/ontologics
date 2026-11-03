"""Explicit, persistent company-stock scenarios. Never loaded implicitly."""
from ..model import canonical, digest
from ..service import DomainError
from .materials import available_stock


def company_stock_scenario(detail, as_of):
    """User-authorized example counts; this does not assert physical inventory."""
    rows=[]; coverage={}
    for item in detail['items']:
        cid=item['component_id']; lot='scenario-lot:'+cid
        q=item['quantity']; positions=set(item['positions'])
        available=80 if 'U6' in positions else 100 if 'U4' in positions else 0 if 'U2' in positions else 135*q
        reserved=10*q; held=5*q
        rows.append(dict(id='scenario-stock:'+cid,component_id=cid,lot_id=lot,
            unit='piece',quantity_state='known',availability_basis='on_hand_less_reserved_and_held',
            deductions_disjoint=True,on_hand_quantity=available+reserved+held,
            reserved_quantity=reserved,held_quantity=held))
        coverage[cid]=[lot]
    scope=dict(company='Planning Reference Company',location='자재 창고',as_of=as_of,unit='piece',
        component_lots=coverage,coverage_basis='사용자 요청 회사 재고 시나리오: 부품별 단일 로트, 창고 내 전량 포함 가정')
    doc=dict(source_ref='company-stock-scenario-v1',stock_scope={k:scope[k] for k in ('company','location','as_of')},stocks=rows)
    doc['sha256']=digest(doc)
    return dict(origin='synthetic',label='회사 재고 시나리오',scope=scope,snapshots=[doc])


class LibraryStock:
    def __init__(self,host,context):
        self.host=host
        with host.request(context) as session:
            from .library_reservations import initialize
            initialize(session._service)
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_company_stock_snapshots(
                id TEXT PRIMARY KEY,configuration_id TEXT NOT NULL,payload JSONB NOT NULL,
                actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

    def latest(self,svc,configuration_id):
        return svc.db.execute('SELECT * FROM pp_company_stock_snapshots WHERE configuration_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(configuration_id,)).fetchone()

    def store(self,context,configuration_id,payload,request_key):
        if payload.get('origin')!='synthetic':raise DomainError('INVALID_INPUT','이 등록 경로는 명시적인 회사 재고 시나리오 전용입니다.')
        calculated=available_stock(payload['snapshots'],payload['scope'])
        if not calculated['valid'] or calculated['unresolved']:raise DomainError('INVALID_INPUT','재고 수량·범위·근거를 확인하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def save():
                from .library_fulfillment import received_totals
                if set(calculated['components']) & set(received_totals(svc)):
                    raise DomainError('RECONCILIATION_REQUIRED','입고 원장이 있는 부품의 기준 재고는 입고 반영분 대사 없이 교체할 수 없습니다.')
                key=digest(dict(configuration_id=configuration_id,payload=payload))
                svc.db.execute('INSERT INTO pp_company_stock_snapshots VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(key,configuration_id,canonical(payload),session.context['actor'],svc.now()))
                return svc._envelope(ok=True,data={'id':key,'components':len(calculated['components'])})
            return svc._run(session.context['actor'],'company_stock_scenario_store',dict(configuration_id=configuration_id,payload=payload),save,permission='review',request_key=request_key)

    def calculate(self,row,svc=None,owner=None):
        if row is None:
            return dict(components={},selected=[],excluded=[],errors=[],conflicts=[],unresolved=[dict(code='STOCK_SOURCE_NOT_CONNECTED',refs=[],message='재고 자료 미연결')],scope=None,valid=True,state='unavailable')
        payload=row['payload']; result=available_stock(payload['snapshots'],payload['scope'])
        result.update(snapshot_id=row['id'],origin=payload['origin'],label=payload['label'],state='available')
        if svc is not None:
            from .library_reservations import reserved_totals,reservation_token
            totals=reserved_totals(svc,owner)
            from .library_fulfillment import received_totals
            incoming=received_totals(svc)
            for cid,component in result['components'].items():
                value=component['available_quantity']
                if value is not None:
                    value+=incoming.get(cid,0)
                    component['available_before_a_reservations']=value
                    component['a_reserved_quantity']=totals.get(cid,0)
                    component['available_quantity']=max(0,value-totals.get(cid,0))
            result['reservation_token']=reservation_token(svc,owner)
        return result


def main():
    import argparse
    from ..database import DatabaseSettings
    from .host import PlanningHost as CommonHost
    from ..identity import DevelopmentContext, DevelopmentIdentityProvider
    from ..web import USERS
    from .configuration_lookup import lookup_contract_configurations
    parser=argparse.ArgumentParser(description='회사 보유 재고 시나리오를 A 전용 DB에 명시적으로 등록')
    parser.add_argument('--schema',default='a_contract_library')
    parser.add_argument('--configuration',default='config:esp32-poe:L:WROOM')
    args=parser.parse_args()
    context=DevelopmentContext('dev-alice')
    host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),DatabaseSettings.from_environment(schema=args.schema))
    stock=LibraryStock(host,context)
    with host.request(context) as session:
        svc=session._service
        existing=stock.latest(svc,args.configuration)
        if existing:
            print('기존 재고가 있어 보존했습니다. 자동 덮어쓰기를 하지 않습니다.');return
        snapshot=session.call('lookup',{})
        if not snapshot['ok']:raise ValueError('제품 DB 조회 실패')
        query=lookup_contract_configurations(session,data_version=snapshot['data_version'])
        if query['configurations'] is None:raise ValueError('제품 카탈로그 조회 실패')
        detail=next(d for d in query['configurations']['candidates'] if d['configuration']['id']==args.configuration)
        payload=company_stock_scenario(detail,svc.now())
    result=stock.store(context,args.configuration,payload,'company-stock-scenario-v1:'+args.configuration)
    if not result['ok']:raise ValueError('재고 시나리오 등록 실패')
    print('회사 재고 시나리오 DB 저장 완료:',result['data']['components'],'종')


if __name__=='__main__':main()
