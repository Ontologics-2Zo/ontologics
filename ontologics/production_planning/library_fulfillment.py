"""A workflow records for human-reported transmission, inspected receipts and schedule review.
No external sending, warehouse integration, resource booking or production approval.
"""
from copy import deepcopy
from datetime import datetime,timedelta
from uuid import uuid4
from ..model import canonical,digest
from ..service import DomainError


def initialize(svc):
    from .library_schedule import initialize as initialize_schedule
    initialize_schedule(svc)
    svc.db.execute('''CREATE TABLE IF NOT EXISTS pp_warehouse_receipts(id TEXT PRIMARY KEY,purchase_id TEXT NOT NULL,reference TEXT NOT NULL UNIQUE,payload JSONB NOT NULL,content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')
    svc.db.execute('''CREATE TABLE IF NOT EXISTS pp_supply_events(
        id TEXT PRIMARY KEY,kind TEXT NOT NULL,reference TEXT NOT NULL UNIQUE,
        contract_id TEXT NOT NULL,item_index INTEGER NOT NULL,reservation_id TEXT NOT NULL,
        payload JSONB NOT NULL,content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')


def events(svc):
    rows=svc.db.execute('SELECT * FROM pp_supply_events ORDER BY created_at,id').fetchall()
    for row in rows:
        if digest(row['payload'])!=row['content_hash']:raise DomainError('SUPPLY_INTEGRITY','입고·전달 기록의 무결성을 확인하지 못했습니다.')
    return rows


def received_totals(svc):
    totals={}
    for r in events(svc):
        if r['kind']=='receipt':
            p=r['payload'];cid=p['component_id'];totals[cid]=totals.get(cid,0)+p['accepted']
    from .library_warehouse import rows
    for row in rows(svc):
        p=row['payload'];cid=p['component_id'];totals[cid]=totals.get(cid,0)+p['accepted']
    return totals


def apply_receipts(svc,row):
    row=deepcopy(row)
    for event in events(svc):
        if event['kind'] not in ('receipt','allocation') or event['reservation_id']!=row['id']:continue
        p=event['payload']
        for line in row['payload']['lines']:
            if line['component_id']==p['component_id']:
                line['reserve']+=p['accepted'];line['waiting']-=p['accepted']
    row['content_hash']=digest(row['payload'])
    return row


class LibraryFulfillment:
    def __init__(self,documents):
        self.documents=documents;self.host=documents.host
        from .library_warehouse import LibraryWarehouse
        self.warehouse=LibraryWarehouse(self)

    def _view(self,svc,contract_id,item_index):
        basis=self.documents._basis(svc,contract_id,item_index)
        rows=[r for r in events(svc) if r['contract_id']==contract_id and r['item_index']==item_index]
        current=[r for r in rows if r['reservation_id']==basis['reservation_id']]
        purchase=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE id=%s',(basis['purchase_id'],)).fetchone() if basis['purchase_id'] else None
        transmitted=any(r['kind']=='transmission' and r['payload']['purchase_id']==basis['purchase_id'] for r in current)
        schedule=next((r for r in reversed(current) if r['kind']=='schedule'),None)
        from .library_warehouse import remaining
        receipts=[r for r in remaining(svc) if r['purchase_id']==basis['purchase_id']]
        return dict(receipts=receipts,basis=basis,basis_hash=digest(basis),events=rows,purchase=purchase,transmitted=transmitted,schedule=schedule,schedule_current=bool(schedule and basis.get('schedule') and basis['schedule']['id']==schedule['id']))

    def inspect(self,context,contract_id,item_index):
        self.documents.reservations._validate(contract_id,item_index)
        with self.host.request(context) as session:
            svc=session._service
            return svc._run(session.context['actor'],'supply_inspect',dict(contract_id=contract_id,item_index=item_index),lambda:svc._envelope(ok=True,data=self._view(svc,contract_id,item_index)))

    def record(self,context,contract_id,item_index,basis_hash,kind,reference,details,reviewed,request_key):
        self.documents.reservations._validate(contract_id,item_index)
        if reviewed is not True or kind not in ('transmission','receipt','schedule') or not isinstance(reference,str) or not reference.strip() or len(reference)>200 or not isinstance(details,dict):raise DomainError('INVALID_INPUT','기록 종류·업무 기록 번호와 전체 확인을 확인하세요.')
        reference=reference.strip()
        with self.host.request(context) as session:
            svc=session._service
            def save():
                existing=next((r for r in events(svc) if r['reference']==reference),None)
                if existing:
                    if existing['kind']!=kind or existing['contract_id']!=contract_id or existing['item_index']!=item_index or existing['payload']['input']!=details:raise DomainError('REFERENCE_CONFLICT','같은 업무 기록 번호의 내용이 다릅니다. 기존 기록을 보존했습니다.')
                    return svc._envelope(ok=True,data=existing)
                view=self._view(svc,contract_id,item_index);b=view['basis']
                if view['basis_hash']!=basis_hash:raise DomainError('STALE_REVISION','확보 상태가 변경됐습니다. 다시 확인하세요.')
                def text(key):
                    value=details.get(key)
                    if not isinstance(value,str) or not value.strip() or len(value)>2000:raise DomainError('INVALID_INPUT',key+' 내용을 확인하세요.')
                    return value.strip()
                def instant(key):
                    try:
                        value=datetime.fromisoformat(text(key).replace('Z','+00:00'))
                        if value.utcoffset() is None:raise ValueError()
                        return value
                    except ValueError:raise DomainError('INVALID_INPUT','시간대가 포함된 일시를 입력하세요.') from None
                payload=dict(input=details,basis_hash=basis_hash,purchase_id=b['purchase_id'],origin='human_reported')
                if kind=='transmission':
                    if set(details)!={'recipient','channel','occurred_at','evidence'}:raise DomainError('INVALID_INPUT','전달 기록 항목을 확인하세요.')
                    if not b['purchase_id']:raise DomainError('PURCHASE_REQUIRED','현재 구매 요청 초안이 필요합니다.')
                    text('recipient');text('channel');text('evidence')
                    if instant('occurred_at')>datetime.fromisoformat(svc.now()):raise DomainError('INVALID_INPUT','미래의 전달을 완료로 기록할 수 없습니다.')
                elif kind=='receipt':
                    if set(details)!={'component_id','received','accepted','rejected','pending','occurred_at','location','evidence'}:raise DomainError('INVALID_INPUT','입고 기록 항목을 확인하세요.')
                    if not view['transmitted']:raise DomainError('TRANSMISSION_REQUIRED','구매 요청 전달 사실을 먼저 기록하세요.')
                    cid=text('component_id');text('location');text('evidence')
                    at=instant('occurred_at')
                    if at>datetime.fromisoformat(svc.now()):raise DomainError('INVALID_INPUT','미래 입고를 완료로 기록할 수 없습니다.')
                    for key in ('received','accepted','rejected','pending'):
                        if type(details.get(key)) is not int or not 0<=details[key]<=1000000000:raise DomainError('INVALID_INPUT','입고·검수 수량은 0 이상 정수여야 합니다.')
                    if details['received']<=0 or details['received']!=details['accepted']+details['rejected']+details['pending']:raise DomainError('INVALID_INPUT','도착 수량은 합격+불합격+검수 대기 수량과 같아야 합니다.')
                    line=next((r for r in b['lines'] if r['component_id']==cid),None)
                    requested=next((r['S'] for r in view['purchase']['payload']['lines'] if r['component_id']==cid),0)
                    prior=sum(r['payload']['input']['received'] for r in view['events'] if r['kind']=='receipt' and r['payload']['purchase_id']==b['purchase_id'] and r['payload']['component_id']==cid)
                    if not line or line['waiting'] is None or details['accepted']>line['waiting'] or prior+details['received']>requested:raise DomainError('RECEIPT_EXCEEDS_PLAN','요청량 또는 현재 미확보 수량을 초과합니다. 요청·계획을 먼저 검토하세요.')
                    payload.update(component_id=cid,accepted=details['accepted'])
                else:
                    if set(details)!={'resource','staff','start_at','setup_minutes','process_minutes','inspection_minutes','evidence'}:raise DomainError('INVALID_INPUT','일정 검토 항목을 확인하세요.')
                    text('resource');text('staff');text('evidence');start=instant('start_at')
                    for key in ('setup_minutes','process_minutes','inspection_minutes'):
                        if type(details.get(key)) is not int or not 0<=details[key]<=525600:raise DomainError('INVALID_INPUT','소요 시간은 0~525600분의 정수로 입력하세요.')
                    if details['process_minutes']==0:raise DomainError('INVALID_INPUT','전체 생산 수량의 공정 시간이 필요합니다.')
                    if b['waiting_count'] or b['unknown_count'] or b['unresolved_contract_fields']:raise DomainError('UNRESOLVED_PLAN','자재 부족과 미확인 조건을 먼저 해결해야 완료 예정일을 계산할 수 있습니다.')
                    latest=max([datetime.fromisoformat(r['created_at']) for r in view['events'] if r['kind']=='receipt']+[datetime.fromisoformat(svc.now())])
                    if start<latest:raise DomainError('INVALID_INPUT','계획 시작은 현재 및 입고 반영 시각 이후여야 합니다.')
                    payload['basis_hash']=digest(dict(b,completion_date=None,schedule=None))
                    payload.update(start_at=start.isoformat(),completion_at=(start+timedelta(minutes=sum(details[k] for k in ('setup_minutes','process_minutes','inspection_minutes')))).isoformat(),calendar='continuous_elapsed',resource_reserved=False,production_authorized=False)
                row=dict(id=str(uuid4()),kind=kind,reference=reference,contract_id=contract_id,item_index=item_index,reservation_id=b['reservation_id'],payload=payload,content_hash=digest(payload),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_supply_events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],kind,reference,contract_id,item_index,b['reservation_id'],canonical(payload),row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'supply_record',dict(contract_id=contract_id,item_index=item_index,basis_hash=basis_hash,kind=kind,reference=reference,details=details,reviewed=reviewed),save,permission='review',request_key=request_key)
