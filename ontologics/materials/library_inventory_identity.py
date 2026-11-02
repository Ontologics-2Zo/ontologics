"""A-only company inventory SKU links; no global component identity assertion."""
from ..service import DomainError

def initialize(svc):
    svc.db.execute('''CREATE TABLE IF NOT EXISTS pp_inventory_component_links(
        component_id TEXT PRIMARY KEY,stock_component_id TEXT NOT NULL,
        basis TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

def links(svc):
    return {r['component_id']:r['stock_component_id'] for r in svc.db.execute(
        'SELECT component_id,stock_component_id FROM pp_inventory_component_links').fetchall()}

def shared_totals(svc,totals):
    mapping=links(svc);pooled={}
    for cid,n in totals.items():
        key=mapping.get(cid,cid);pooled[key]=pooled.get(key,0)+n
    return {**pooled,**{cid:pooled.get(key,0) for cid,key in mapping.items()}}

def register(host,context,members,basis,request_key):
    if not isinstance(members,list) or len(members)<2 or len(set(members))!=len(members) or not basis.strip():
        raise DomainError('INVALID_INPUT','서로 다른 부품 ID와 동일 회사 재고 품목 확인 근거가 필요합니다.')
    with host.request(context) as session:
        snapshot=session.call('lookup',{})
        if not snapshot['ok']:return snapshot
        nodes=snapshot['data'];known={n['id'] for n in nodes if n['type']=='ComponentModel'}
        if not set(members)<=known:raise DomainError('INVALID_INPUT','카탈로그의 정확한 부품 ID가 필요합니다.')
        # The existing plan computes one line per BOM component. Do not allow
        # two aliases within one configuration to independently claim the pool.
        configs={}
        for n in nodes:
            if n['type']=='BOMItem' and n['component_id'] in members:
                configs.setdefault(n['configuration_id'],set()).add(n['component_id'])
        if any(len(v)>1 for v in configs.values()):raise DomainError('INVALID_INPUT','동일 구성 내 복수 부품 통합은 먼저 소요량 대사가 필요합니다.')
        svc=session._service
        def save():
            existing=links(svc);key=members[0]
            if any(existing.get(cid,key)!=key for cid in members) or existing.get(key,key)!=key:
                raise DomainError('INVENTORY_LINK_CONFLICT','기존 재고 품목 연결과 다릅니다. 자동 변경하지 않습니다.')
            for cid in members:
                svc.db.execute('INSERT INTO pp_inventory_component_links VALUES(%s,%s,%s,%s,%s) ON CONFLICT(component_id) DO NOTHING',
                               (cid,key,basis,session.context['actor'],svc.now()))
            return svc._envelope(ok=True,data=dict(stock_component_id=key,members=members))
        return svc._run(session.context['actor'],'link_company_inventory_components',dict(members=members,basis=basis),save,permission='review',request_key=request_key)

def seed_company_test_links(host,context):
    """Explicit fixture install, never invoked by a read.

    Exact complete designation + package are the declared company test SKU.
    This does not certify global manufacturer identity or electrical substitution.
    NA/NS and unnamed assembly options remain unlinked.
    """
    from ..model import digest
    with host.request(context) as session:
        r=session.call('lookup',{})
        if not r['ok']:raise DomainError('CATALOG_QUERY_FAILED','카탈로그를 조회하지 못했습니다.')
        nodes=r['data']
    configurations={'config:esp32-poe:L:WROOM','config:esp32-poe:L1:WROOM'}
    ids={n['component_id'] for n in nodes if n['type']=='BOMItem' and n['configuration_id'] in configurations}
    groups={}
    for n in nodes:
        if n['type']=='ComponentModel' and n['id'] in ids and n.get('package') and not n['name'].startswith(('NA','NS','Opened')):
            groups.setdefault((n['name'],n['package']),[]).append(n['id'])
    count=0
    for (name,package),members in sorted(groups.items()):
        if len(members)!=2:continue
        members=sorted(members)
        basis=f'회사 업무 검증용 동일 재고 품목 지정: {name} / {package}. 표기·패키지 일치에 따른 테스트 매핑이며 제조사 품번 실사 또는 전기적 대체 승인 아님.'
        result=register(host,context,members,basis,'company-test-stock-link:'+digest(members))
        if not result['ok']:raise DomainError('INVENTORY_LINK_FAILED','재고 품목 매핑을 저장하지 못했습니다.')
        count+=1
    return count
