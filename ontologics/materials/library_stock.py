"""Explicit, persistent company-stock scenarios. Never loaded implicitly."""
from copy import deepcopy
from ..model import canonical, digest
from ..service import DomainError
from .materials import _time, available_stock


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


def _normalized_payload(payload, mapping):
    if not mapping:
        return payload
    normalized=deepcopy(payload)
    coverage={}
    for cid,lots in normalized['scope'].get('component_lots',{}).items():
        key=mapping.get(cid,cid)
        if key in coverage:
            raise DomainError('STOCK_IDENTITY_CONFLICT','동일 재고 품목의 관측이 중복됐습니다.')
        coverage[key]=lots
    normalized['scope']['component_lots']=coverage
    for doc in normalized['snapshots']:
        for item in doc['stocks']:
            item['component_id']=mapping.get(item['component_id'],item['component_id'])
    return normalized


def _at_least_as_recent(current, other):
    if not other:
        return True
    if not current:
        return False
    if current.keys()==other.keys():
        return all(current[lot]>=when for lot,when in other.items())
    # Different lot sets can supersede each other only when their entire
    # observation windows are ordered. Never assemble a count across sources.
    return min(current.values())>max(other.values())


def shared_stock(rows, mapping=None, report_components=None):
    """Resolve a company/location pool using whole component observations."""
    if not rows:return None
    if len(rows)==1 and not mapping:return rows[0]
    scopes={(r['payload']['scope']['company'],r['payload']['scope']['location'],
             r['payload']['scope']['unit']) for r in rows}
    if len(scopes)!=1:
        raise DomainError('STOCK_SCOPE_CONFLICT','회사·창고가 다른 재고 자료입니다. 공용 재고 범위를 먼저 대사하세요.')
    conflict='동일 시점 또는 대사되지 않은 로트의 재고가 서로 다릅니다. 재고 담당자의 대사가 필요합니다.'
    candidates={}; observations={}; sources=[]
    for row in rows:
        p=_normalized_payload(row['payload'],mapping)
        calculated=available_stock(p['snapshots'],p['scope'])
        if not calculated['valid']:
            raise DomainError('STOCK_SOURCE_INVALID','공용 재고 원본에 오류 또는 상충이 있습니다.')
        sources.append((row,calculated))
        allowed=(report_components or {}).get(row.get('configuration_id'))
        by_component={}
        for selection in calculated['selected']:
            obs=selection['observation']
            cid=obs['component_id']
            if allowed is not None and cid not in allowed:
                continue
            lot,when=obs['lot_id'],_time(obs['as_of'])
            by_component.setdefault(cid,{})[lot]=(when,selection)
            # Use the common calculator's comparison semantics for corroborating
            # observations while keeping each source's original timestamp string.
            values={k:v for k,v in obs.items() if k not in ('id','as_of','evidence')}
            if observations.setdefault((cid,lot,when),values)!=values:
                raise DomainError('STOCK_OBSERVATION_CONFLICT',conflict)
        for cid,component in calculated['components'].items():
            if allowed is not None and cid not in allowed:
                continue
            lots=by_component.get(cid,{})
            times={lot:when for lot,(when,_) in lots.items()}
            selections=[selection for _,selection in lots.values()]
            coverage=p['scope'].get('component_lots',{}).get(cid,[])
            candidates.setdefault(cid,[]).append((times,row,component,selections,coverage,calculated))

    def source_order(row,calculated):
        return _time(calculated['scope']['as_of']),row.get('created_at',''),row['id']

    newest,latest=max(sources,key=lambda pair:source_order(*pair))
    result=deepcopy(latest)
    cutoff=_time(result['scope']['as_of'])
    result.update(components={},selected=[],excluded=[],errors=[],conflicts=[],unresolved=[])
    coverage={}
    for cid,options in sorted(candidates.items()):
        # A candidate that dominates all lot times also has the greatest
        # descending time vector. The source order breaks corroborating ties.
        times,row,component,selections,lots,calculated=max(
            options,key=lambda option:(sorted(option[0].values(),reverse=True),source_order(option[1],option[5])))
        for other_times,_,other_component,_,other_lots,_ in options:
            if (not _at_least_as_recent(times,other_times) or
                    times and times==other_times and
                    (component!=other_component or set(lots)!=set(other_lots))):
                raise DomainError('STOCK_OBSERVATION_CONFLICT',conflict)

        result['components'][cid]=deepcopy(component)
        result['components'][cid]['source_snapshot_id']=row['id']
        result['components'][cid]['source_as_of']=min(
            selections,key=lambda selection:_time(selection['observation']['as_of'])
        )['observation']['as_of'] if selections else None
        if lots:
            coverage[cid]=deepcopy(lots)
        for selection in deepcopy(selections):
            observed_at=times[selection['observation']['lot_id']]
            selection['age_seconds']=(cutoff-observed_at).total_seconds()
            result['selected'].append(selection)
        refs=[selection['source'] for selection in selections]
        result['unresolved'].extend(deepcopy(issue) for issue in calculated['unresolved']
                                    if issue['refs']==[cid] or any(ref in refs for ref in issue['refs']))
    result['scope']['component_lots']=coverage
    result['scope']['coverage_basis']='부품별 최신 관측 원본 참조; 중복 로트 합산 없음'
    source_ids=sorted(r['id'] for r in rows)
    return dict(newest,id=digest(source_ids),shared_calculated=result,source_snapshot_ids=source_ids)


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
        rows=svc.db.execute('SELECT * FROM pp_company_stock_snapshots ORDER BY created_at DESC,id DESC').fetchall()
        from .library_inventory_identity import links
        mapping=links(svc)
        if not mapping:
            own=[r for r in rows if r['configuration_id']==configuration_id]
            if not own and len({r['configuration_id'] for r in rows})!=1:
                return None
            rows=own or rows
        current={}
        for row in sorted(rows,key=lambda r:_time(r['payload']['scope']['as_of']),reverse=True):
            current.setdefault(row['configuration_id'],row['payload'])
        # An omitted human count has no observation time. The greatest report
        # cutoff determines which components the configuration still declares.
        report_components={
            cid:{mapping.get(part,part) for part in payload['scope'].get('component_lots',{})}
            for cid,payload in current.items() if payload['origin']=='human_reported'
        }
        pooled=shared_stock(rows,mapping,report_components)
        if pooled and mapping:
            pooled=dict(pooled,id=digest(dict(sources=pooled['source_snapshot_ids'],links=mapping)),inventory_links=mapping)
        return pooled

    def store(self,context,configuration_id,payload,request_key):
        if payload.get('origin')!='synthetic':raise DomainError('INVALID_INPUT','이 등록 경로는 명시적인 회사 재고 시나리오 전용입니다.')
        calculated=available_stock(payload['snapshots'],payload['scope'])
        if not calculated['valid'] or calculated['unresolved']:raise DomainError('INVALID_INPUT','재고 수량·범위·근거를 확인하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def save():
                from .library_fulfillment import received_totals
                from .library_inventory_identity import links
                mapping=links(svc)
                observed={mapping.get(cid,cid) for cid in calculated['components']}
                received={mapping.get(cid,cid) for cid in received_totals(svc)}
                if observed & received:
                    raise DomainError('RECONCILIATION_REQUIRED','입고 원장이 있는 부품의 기준 재고는 입고 반영분 대사 없이 교체할 수 없습니다.')
                key=digest(dict(configuration_id=configuration_id,payload=payload))
                svc.db.execute('INSERT INTO pp_company_stock_snapshots VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(key,configuration_id,canonical(payload),session.context['actor'],svc.now()))
                return svc._envelope(ok=True,data={'id':key,'components':len(calculated['components'])})
            return svc._run(session.context['actor'],'company_stock_scenario_store',dict(configuration_id=configuration_id,payload=payload),save,permission='review',request_key=request_key)

    def calculate(self,row,svc=None,owner=None):
        if row is None:
            return dict(components={},selected=[],excluded=[],errors=[],conflicts=[],unresolved=[dict(code='STOCK_SOURCE_NOT_CONNECTED',refs=[],message='재고 자료 미연결')],scope=None,valid=True,state='unavailable')
        payload=row['payload']; result=deepcopy(row['shared_calculated']) if 'shared_calculated' in row else available_stock(payload['snapshots'],payload['scope'])
        label=payload['label']
        if row.get('inventory_links') and len(row['source_snapshot_ids'])>1:
            label='회사 공용 부품 재고'
        result.update(snapshot_id=row['id'],origin=payload['origin'],label=label,state='available')
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
