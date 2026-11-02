"""Explicit, persistent company-stock scenarios. Never loaded implicitly."""
from copy import deepcopy
from datetime import datetime
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


def shared_stock(rows):
    """Resolve one company/location pool without copying or adding duplicate lots.

    A configuration snapshot is an observation source. For each component use
    its newest complete observation; retain original per-lot provenance.
    The current reservation ledger has no warehouse dimension, so mixed scopes
    must be reconciled rather than silently consuming stock from another site.
    """
    if not rows:return None
    if len(rows)==1:return rows[0]
    scopes={(r['payload']['scope']['company'],r['payload']['scope']['location'],
             r['payload']['scope']['unit']) for r in rows}
    if len(scopes)!=1:
        raise DomainError('STOCK_SCOPE_CONFLICT','회사·창고가 다른 재고 자료입니다. 공용 재고 범위를 먼저 대사하세요.')
    sources=[];chosen={}
    for row in rows:
        p=row['payload'];calculated=available_stock(p['snapshots'],p['scope'])
        if not calculated['valid']:
            raise DomainError('STOCK_SOURCE_INVALID','공용 재고 원본에 오류 또는 상충이 있습니다.')
        at=datetime.fromisoformat(p['scope']['as_of'].replace('Z','+00:00'))
        sources.append((row,calculated))
        for cid,component in calculated['components'].items():
            # Include lot quantities, not just the summed availability.
            lots=[{k:v for k,v in x['observation'].items() if k!='id'} for x in calculated['selected'] if x['observation'].get('component_id')==cid]
            signature=digest(dict(component=component,lots=lots))
            old=chosen.get(cid)
            if old and old[0]==at and old[3]!=signature:
                raise DomainError('STOCK_OBSERVATION_CONFLICT','동일 시점의 부품 재고가 서로 다릅니다. 재고 담당자의 대사가 필요합니다.')
            if old is None or at>old[0]:chosen[cid]=(at,row,calculated,signature)
    newest=max(rows,key=lambda r:datetime.fromisoformat(r['payload']['scope']['as_of'].replace('Z','+00:00')))
    result=deepcopy(next(c for r,c in sources if r['id']==newest['id']))
    result.update(components={},selected=[],excluded=[],errors=[],conflicts=[],unresolved=[])
    coverage={}
    for cid,(_,row,calculated,_) in chosen.items():
        result['components'][cid]=deepcopy(calculated['components'][cid])
        result['components'][cid]['source_snapshot_id']=row['id']
        result['components'][cid]['source_as_of']=row['payload']['scope']['as_of']
        coverage[cid]=row['payload']['scope'].get('component_lots',{}).get(cid,[])
    # Keep original source/locator evidence intact; do not rewrite source rows.
    for row,calculated in sources:
        selected_ids={cid for cid,value in chosen.items() if value[1]['id']==row['id']}
        result['selected'].extend(deepcopy(x) for x in calculated['selected'] if x['observation'].get('component_id') in selected_ids)
        result['unresolved'].extend(deepcopy(calculated['unresolved']))
    result['scope']['component_lots']=coverage
    result['scope']['coverage_basis']='부품별 최신 관측 원본 참조; 중복 로트 합산 없음'
    payload=deepcopy(newest['payload']);payload['scope']=deepcopy(result['scope'])
    payload['label']='회사 공용 부품 재고'
    merged=dict(newest,id=digest(sorted(r['id'] for r in rows)),payload=payload,
                shared_calculated=result,source_snapshot_ids=sorted(r['id'] for r in rows))
    return merged


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
        # configuration_id identifies the consumer, never ownership of stock.
        rows=svc.db.execute('''SELECT DISTINCT ON (configuration_id) *
            FROM pp_company_stock_snapshots
            ORDER BY configuration_id,created_at DESC,id DESC''').fetchall()
        from .library_inventory_identity import links
        mapping=links(svc)
        if not mapping:
            own=next((r for r in rows if r['configuration_id']==configuration_id),None)
            return own or (rows[0] if len(rows)==1 else None)
        normalized=[]
        for raw in rows:
            row=deepcopy(raw);p=row['payload'];coverage={}
            for cid,lots in p['scope'].get('component_lots',{}).items():
                key=mapping.get(cid,cid)
                if key in coverage:raise DomainError('STOCK_IDENTITY_CONFLICT','동일 재고 품목의 관측이 중복됐습니다.')
                coverage[key]=lots
            p['scope']['component_lots']=coverage
            for doc in p['snapshots']:
                for item in doc['stocks']:
                    item['component_id']=mapping.get(item['component_id'],item['component_id'])
            normalized.append(row)
        pooled=shared_stock(normalized)
        if pooled:
            pooled=dict(pooled,id=digest(dict(sources=sorted(r['id'] for r in rows),links=mapping)),inventory_links=mapping)
        return pooled

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
        payload=row['payload']; result=deepcopy(row['shared_calculated']) if 'shared_calculated' in row else available_stock(payload['snapshots'],payload['scope'])
        result.update(snapshot_id=row['id'],origin=payload['origin'],label=payload['label'],state='available')
        if svc is not None:
            from .library_reservations import reserved_totals,reservation_token
            totals=reserved_totals(svc,owner)
            from .library_fulfillment import received_totals
            incoming=received_totals(svc)
            from .library_inventory_identity import shared_totals
            incoming=shared_totals(svc,incoming)
            for cid,component in result['components'].items():
                value=component['available_quantity']
                if value is not None:
                    value+=incoming.get(cid,0)
                    component['available_before_a_reservations']=value
                    component['a_reserved_quantity']=totals.get(cid,0)
                    component['available_quantity']=max(0,value-totals.get(cid,0))
            result['reservation_token']=reservation_token(svc,owner)
        result['inventory_links']=row.get('inventory_links',{})
        for alias,key in row.get('inventory_links',{}).items():
            if key in result['components']:
                result['components'][alias]=dict(result['components'][key],stock_component_id=key)
        return result


def main():
    import argparse
    from ..database import DatabaseSettings
    from .host import MaterialsHost as CommonHost
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
