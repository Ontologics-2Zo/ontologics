"""A-only, version-bound schedule proposals from persisted operational records.
A proposal never books a resource or authorizes production.
"""
from datetime import datetime, timedelta
from math import ceil
from uuid import uuid4
from zoneinfo import ZoneInfo
from ..model import canonical, digest
from ..service import DomainError

TABLES={
 'resources':('pp_schedule_resources','name TEXT NOT NULL',('name',)),
 'routes':('pp_schedule_routes','configuration_id TEXT NOT NULL,resource_id TEXT NOT NULL',('configuration_id','resource_id')),
 'shifts':('pp_schedule_shifts','resource_id TEXT NOT NULL,start_at TEXT NOT NULL,end_at TEXT NOT NULL,staff_available INTEGER NOT NULL CHECK(staff_available>=0)',('resource_id','start_at','end_at','staff_available')),
 'blocks':('pp_schedule_blocks','resource_id TEXT NOT NULL,start_at TEXT NOT NULL,end_at TEXT NOT NULL',('resource_id','start_at','end_at')),
 'arrivals':('pp_schedule_arrivals','purchase_id TEXT NOT NULL,component_id TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>0),expected_at TEXT NOT NULL',('purchase_id','component_id','quantity','expected_at')),
}

def instant(value):
    try:
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        if d.utcoffset() is None:raise ValueError()
        return d
    except (ValueError,TypeError,AttributeError):raise DomainError('INVALID_INPUT','시간대를 포함한 일시가 필요합니다.') from None


def initialize(svc):
    for table,columns,_ in TABLES.values():
        svc.db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY,{columns},payload JSONB NOT NULL,content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)')
    svc.db.execute('''CREATE TABLE IF NOT EXISTS pp_schedule_proposals(id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,item_index INTEGER NOT NULL,payload JSONB NOT NULL,content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')


def inputs(svc):
    data={}
    for kind,(table,_,columns) in TABLES.items():
        data[kind]=[]
        for row in svc.db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall():
            p=row['payload']
            if digest(p)!=row['content_hash'] or row['id']!=p['id'] or any(row[c]!=p[c] for c in columns):raise DomainError('SCHEDULE_INTEGRITY','생산 운영 기준의 무결성 오류입니다.')
            data[kind].append(p)
    return data


def schedule_work(route,shifts,blocks,quantity,ready_at):
    """Work uses only staffed windows, with occupied intervals subtracted once."""
    ready=instant(ready_at);windows=[]
    for shift in shifts:
        if shift['staff_available']>=route['required_staff']:
            start=max(ready,instant(shift['start_at']));end=instant(shift['end_at'])
            if start<end:windows.append((start,end))
    merged=[]
    for start,end in sorted(windows):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    for block in blocks:
        a,b=instant(block['start_at']),instant(block['end_at']);next_windows=[]
        for start,end in merged:
            if b<=start or a>=end:next_windows.append((start,end));continue
            if start<a:next_windows.append((start,a))
            if b<end:next_windows.append((b,end))
        merged=next_windows
    seconds=route['setup_minutes']*60+quantity*route['unit_seconds']+route['inspection_minutes']*60
    remaining=seconds;segments=[]
    for start,end in merged:
        capacity=int((end-start).total_seconds())
        if not route['pausable'] and capacity<seconds:continue
        used=min(capacity,remaining)
        if used<=0:continue
        segments.append(dict(start_at=start.isoformat(),end_at=(start+timedelta(seconds=used)).isoformat(),minutes=used/60))
        remaining-=used
        if remaining==0:return dict(start_at=segments[0]['start_at'],completion_at=segments[-1]['end_at'],work_minutes=seconds/60,segments=segments)
    return None


def latest(svc,contract_id,item_index):
    row=svc.db.execute('SELECT * FROM pp_schedule_proposals WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
    if row and digest(row['payload'])!=row['content_hash']:raise DomainError('SCHEDULE_INTEGRITY','저장된 일정안의 무결성 오류입니다.')
    return row


def attach(svc,basis):
    row=latest(svc,basis['contract_id'],basis['item_index'])
    if row and row['payload']['basis_hash']==digest(basis) and row['payload']['inputs_hash']==digest(inputs(svc)):
        result=row['payload']['candidate']
        if instant(result['start_at'])>=instant(svc.now()):basis['auto_schedule']=dict(result,proposal_id=row['id'])
    return basis


class LibrarySchedule:
    def __init__(self,documents):self.documents=documents;self.host=documents.host

    def store(self,context,bundle,request_key):
        if not isinstance(bundle,dict) or set(bundle)!=set(TABLES) or any(not isinstance(v,list) for v in bundle.values()):raise DomainError('INVALID_INPUT','생산 운영 자료 묶음을 확인하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def write():
                current=inputs(svc);combined={k:current[k]+bundle[k] for k in TABLES}
                resources={r['id']:r for r in combined['resources']}
                for kind,records in bundle.items():
                    for r in records:
                        for key in ('id','origin','evidence'):
                            if not isinstance(r.get(key),str) or not r[key].strip() or len(r[key])>2000:raise DomainError('INVALID_INPUT','기준 식별자·자료 구분·근거가 필요합니다.')
                        if r['origin'] not in ('synthetic','human_reported'):raise DomainError('INVALID_INPUT','자료 구분 오류입니다.')
                        if kind=='resources':
                            if type(r.get('active')) is not bool or not r.get('name'):raise DomainError('INVALID_INPUT','라인 이름·사용 여부가 필요합니다.')
                            try:ZoneInfo(r['timezone'])
                            except (KeyError,ValueError):raise DomainError('INVALID_INPUT','라인 시간대를 확인하세요.') from None
                        if kind in ('routes','shifts','blocks') and r.get('resource_id') not in resources:raise DomainError('INVALID_INPUT','등록된 라인이 필요합니다.')
                        if kind=='routes':
                            if not r.get('configuration_id') or not r.get('revision') or type(r.get('pausable')) is not bool:raise DomainError('INVALID_INPUT','제품 구성·표준 개정·중단 재개 조건이 필요합니다.')
                            for k in ('setup_minutes','unit_seconds','inspection_minutes','required_staff'):
                                if type(r.get(k)) is not int or not 0<=r[k]<=100000:raise DomainError('INVALID_INPUT','표준 시간·인원을 확인하세요.')
                            if not r['unit_seconds'] or not r['required_staff'] or instant(r['valid_from'])>=instant(r['valid_until']):raise DomainError('INVALID_INPUT','표준 유효기간과 단위 시간을 확인하세요.')
                        if kind in ('shifts','blocks'):
                            if instant(r['start_at'])>=instant(r['end_at']):raise DomainError('INVALID_INPUT','시간 구간이 올바르지 않습니다.')
                        if kind=='shifts':
                            if type(r.get('staff_available')) is not int or not 0<=r['staff_available']<=1000 or not r.get('crew_id'):raise DomainError('INVALID_INPUT','확보한 근무조와 인원 수가 필요합니다.')
                            for other in combined['shifts']:
                                if other['id']==r['id']:continue
                                if (other['resource_id']==r['resource_id'] or other['crew_id']==r['crew_id']) and instant(r['start_at'])<instant(other['end_at']) and instant(other['start_at'])<instant(r['end_at']):raise DomainError('CALENDAR_CONFLICT','라인 또는 근무조의 근무 구간이 중복됩니다.')
                        if kind=='blocks' and not r.get('reason'):raise DomainError('INVALID_INPUT','점유 사유가 필요합니다.')
                        if kind=='arrivals':
                            if type(r.get('quantity')) is not int or r['quantity']<=0 or type(r.get('baseline_received')) is not int or r['baseline_received']<0:raise DomainError('INVALID_INPUT','잔여 입고 예정 수량을 확인하세요.')
                            if r.get('status') not in ('planned','supplier_confirmed') or instant(r['recorded_at'])>instant(svc.now()) or instant(r['expected_at'])<instant(r['recorded_at']):raise DomainError('INVALID_INPUT','입고 예정 상태·일시를 확인하세요.')
                            purchase=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE id=%s',(r['purchase_id'],)).fetchone()
                            if not purchase or digest(dict(payload=purchase['payload'],markdown=purchase['markdown']))!=purchase['content_hash']:raise DomainError('PURCHASE_REQUIRED','유효한 구매 요청서가 필요합니다.')
                            limit=next((p['S'] for p in purchase['payload']['lines'] if p['component_id']==r['component_id']),0)
                            if r['baseline_received']+r['quantity']>limit:raise DomainError('INVALID_INPUT','구매 요청 잔량보다 입고 예정량이 큽니다.')
                        old=next((p for p in current[kind] if p['id']==r['id']),None)
                        if old and old!=r:raise DomainError('IMMUTABLE_STANDARD','기존 운영 자료는 덮어쓰지 않습니다. 새 기록으로 등록하세요.')
                        table,_,columns=TABLES[kind]
                        keys=('id',)+columns+('payload','content_hash','actor','created_at')
                        values=[r['id']]+[r[c] for c in columns]+[canonical(r),digest(r),session.context['actor'],svc.now()]
                        svc.db.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join(["%s"]*len(keys))}) ON CONFLICT(id) DO NOTHING',tuple(values))
                return svc._envelope(ok=True,data=dict(inputs_hash=digest(inputs(svc))))
            return svc._run(session.context['actor'],'schedule_master_store',dict(bundle=bundle),write,permission='review',request_key=request_key)

    def _compute(self,svc,contract_id,item_index,not_before):
        basis=self.documents._basis(svc,contract_id,item_index,include_auto=False)
        data=inputs(svc);now=instant(svc.now())
        if not_before is None:not_before=(now.replace(second=0,microsecond=0)+timedelta(minutes=15-now.minute%15)).isoformat()
        ready=instant(not_before);conditions=[];blockers=[];arrival_basis=[]
        from .library_warehouse import remaining
        receipts=[r for r in remaining(svc) if r['purchase_id']==basis['purchase_id']]
        from .library_fulfillment import events
        legacy=[e for e in events(svc) if e['kind']=='receipt' and e['payload']['purchase_id']==basis['purchase_id']]
        for line in basis['lines']:
            need=line['waiting']
            if need is None or need<=0:continue
            cid=line['component_id'];available=sum(r['available'] for r in receipts if r['payload']['component_id']==cid)
            covered=min(need,available)
            if covered:conditions.append(f"{line['name']} 합격 입고 {covered}개를 이번 계획에 할당해야 합니다.")
            need-=covered
            if need<=0:continue
            candidates=[r for r in data['arrivals'] if r['purchase_id']==basis['purchase_id'] and r['component_id']==cid]
            arrival=max(candidates,key=lambda r:(instant(r['recorded_at']),r['id'])) if candidates else None
            delivered=sum(r['payload']['received'] for r in receipts if r['payload']['component_id']==cid)+sum(r['payload']['input']['received'] for r in legacy if r['payload']['component_id']==cid)
            remaining_expected=max(0,arrival['quantity']-max(0,delivered-arrival['baseline_received'])) if arrival else 0
            if not arrival or remaining_expected<need or instant(arrival['expected_at'])<now:
                blockers.append(f"{line['name']} 부족 {need}개의 유효한 입고 예정일·수량이 필요합니다.");continue
            ready=max(ready,instant(arrival['expected_at']));arrival_basis.append(arrival)
            conditions.append(f"{line['name']} {need}개가 예정일에 도착하고 검수 합격·할당된다는 전제입니다.")
        if basis['unknown_count']:conditions.append(f"미확인 자재 {basis['unknown_count']}종의 실장·소요량·재고 조건을 생산 전에 확인해야 합니다.")
        if basis['unresolved_contract_fields']:conditions.append(f"계약 검토의 미확인 항목 {len(basis['unresolved_contract_fields'])}개를 해결해야 합니다.")
        if basis['material_unresolved']:conditions.append('소요량 검토의 미해결 조건을 확인해야 합니다.')
        choice=self.documents.reservations.rfq.replacements.latest(svc,contract_id,item_index)
        configuration_id=choice['payload']['result']['configuration_id']
        resources={r['id']:r for r in data['resources'] if r['active']};options=[];reasons=[]
        routes=[r for r in data['routes'] if r['configuration_id']==configuration_id and r['resource_id'] in resources and instant(r['valid_from'])<=ready<instant(r['valid_until'])]
        if not routes:blockers.append('이 제품 구성에 유효한 라인·표준 작업시간이 없습니다.')
        if not blockers:
            for route in routes:
                if sum(r['resource_id']==route['resource_id'] for r in routes)>1:
                    reasons.append('동일 라인에 표준 개정 유효기간이 중복됩니다.');continue
                shifts=[s for s in data['shifts'] if s['resource_id']==route['resource_id'] and instant(s['end_at'])<=instant(route['valid_until'])]
                blocks=[b for b in data['blocks'] if b['resource_id']==route['resource_id']]
                work=schedule_work(route,shifts,blocks,basis['quantity'],ready.isoformat())
                if work is None:reasons.append(resources[route['resource_id']]['name']+': 확보 인원·작업 가능 시간 부족');continue
                option=dict(**work,resource=resources[route['resource_id']],route=route,quantity=basis['quantity'],conditions=conditions,status='conditional' if conditions else 'draft',origin='synthetic' if any(r['origin']=='synthetic' for r in [route,resources[route['resource_id']]]+shifts+blocks+arrival_basis) else 'human_reported',production_authorized=False,resource_reserved=False,staff_source='라인별 확보된 전담 근무조',ready_at=ready.isoformat(),arrival_basis=arrival_basis,calendar=shifts,occupied=blocks)
                option['id']=digest(option);options.append(option)
        options.sort(key=lambda r:(instant(r['completion_at']),r['resource']['id']))
        return dict(basis_hash=digest(basis),inputs_hash=digest(data),not_before=not_before,candidates=options,blockers=blockers or ([] if options else reasons),conditions=conditions,basis=basis,inputs=data)

    def propose(self,context,contract_id,item_index,not_before=None):
        self.documents.reservations._validate(contract_id,item_index)
        with self.host.request(context) as session:
            svc=session._service
            def read():
                data=self._compute(svc,contract_id,item_index,not_before)
                saved=latest(svc,contract_id,item_index)
                data['saved']=saved
                data['saved_current']=bool(saved and saved['payload']['basis_hash']==data['basis_hash'] and saved['payload']['inputs_hash']==data['inputs_hash'] and instant(saved['payload']['candidate']['start_at'])>=instant(svc.now()))
                return svc._envelope(ok=True,data=data)
            return svc._run(session.context['actor'],'schedule_propose',dict(contract_id=contract_id,item_index=item_index,not_before=not_before),read)

    def save(self,context,contract_id,item_index,not_before,basis_hash,inputs_hash,candidate_id,note,reviewed,request_key):
        self.documents.reservations._validate(contract_id,item_index)
        if reviewed is not True or not isinstance(note,str) or len(note)>2000:raise DomainError('INVALID_INPUT','일정과 조건을 확인하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def write():
                data=self._compute(svc,contract_id,item_index,not_before)
                candidate=next((r for r in data['candidates'] if r['id']==candidate_id),None)
                if data['basis_hash']!=basis_hash or data['inputs_hash']!=inputs_hash or not candidate or instant(candidate['start_at'])<instant(svc.now()):raise DomainError('STALE_SCHEDULE','자재·근무·점유 또는 시작 시간이 변경됐습니다. 일정안을 다시 계산하세요.')
                p=dict(basis_hash=basis_hash,inputs_hash=inputs_hash,not_before=not_before,candidate=candidate,note=note.strip())
                old=latest(svc,contract_id,item_index)
                if old and old['payload']==p:return svc._envelope(ok=True,data=old)
                row=dict(id=str(uuid4()),contract_id=contract_id,item_index=item_index,payload=p,content_hash=digest(p),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_schedule_proposals VALUES(%s,%s,%s,%s,%s,%s,%s)',(row['id'],contract_id,item_index,canonical(p),row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'schedule_proposal_save',dict(contract_id=contract_id,item_index=item_index,not_before=not_before,basis_hash=basis_hash,inputs_hash=inputs_hash,candidate_id=candidate_id,note=note,reviewed=reviewed),write,permission='review',request_key=request_key)
