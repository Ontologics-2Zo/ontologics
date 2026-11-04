"""A warehouse intake ledger; inspected stock is held until explicitly allocated.
Warehouse and planning use the existing authenticated host transaction boundary.
"""
from datetime import datetime
from uuid import uuid4
from ..model import canonical, digest
from ..service import DomainError


def rows(svc):
    result=svc.db.execute('SELECT * FROM pp_warehouse_receipts ORDER BY created_at,id').fetchall()
    for row in result:
        if digest(row['payload'])!=row['content_hash']:
            raise DomainError('WAREHOUSE_INTEGRITY','입고·검수 원본의 무결성을 확인하지 못했습니다.')
    return result


def remaining(svc):
    from .library_fulfillment import events
    used={}
    for event in events(svc):
        if event['kind']=='allocation':
            p=event['payload'];used[p['receipt_id']]=used.get(p['receipt_id'],0)+p['accepted']
    result=[]
    for row in rows(svc):
        allocated=used.get(row['id'],0);available=row['payload']['accepted']-allocated
        if available<0:raise DomainError('WAREHOUSE_INTEGRITY','입고량보다 할당량이 큽니다.')
        result.append(dict(row,allocated=allocated,available=available))
    return result


def held_totals(svc):
    totals={}
    for row in remaining(svc):
        cid=row['payload']['component_id'];totals[cid]=totals.get(cid,0)+row['available']
    return totals


class LibraryWarehouse:
    def __init__(self,fulfillment):
        self.fulfillment=fulfillment;self.host=fulfillment.host

    def inspect(self,context,purchase_id):
        with self.host.request(context) as session:
            svc=session._service
            def read():
                purchase=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE id=%s',(purchase_id,)).fetchone()
                if not purchase:raise DomainError('NOT_FOUND','구매 요청서를 찾을 수 없습니다.')
                if digest(dict(payload=purchase['payload'],markdown=purchase['markdown']))!=purchase['content_hash']:raise DomainError('RFQ_INTEGRITY','구매 요청서의 무결성을 확인하지 못했습니다.')
                return svc._envelope(ok=True,data=dict(purchase=purchase,receipts=[r for r in remaining(svc) if r['purchase_id']==purchase_id]))
            return svc._run(session.context['actor'],'warehouse_inspect',dict(purchase_id=purchase_id),read)

    def register(self,context,purchase_id,reference,details,request_key):
        if not isinstance(reference,str) or not reference.strip() or len(reference)>200 or not isinstance(details,dict):raise DomainError('INVALID_INPUT','입고 기록 번호와 검수 내용을 확인하세요.')
        reference=reference.strip()
        if set(details)!={'component_id','received','accepted','rejected','pending','occurred_at','location','evidence','origin'}:raise DomainError('INVALID_INPUT','입고·검수 항목을 확인하세요.')
        for key in ('component_id','location','evidence','occurred_at'):
            if not isinstance(details[key],str) or not details[key].strip() or len(details[key])>2000:raise DomainError('INVALID_INPUT','부품·창고·검수 근거·일시가 필요합니다.')
        if details['origin'] not in ('synthetic','human_reported'):raise DomainError('INVALID_INPUT','자료 출처를 확인하세요.')
        for key in ('received','accepted','rejected','pending'):
            if type(details[key]) is not int or not 0<=details[key]<=1000000000:raise DomainError('INVALID_INPUT','수량은 0 이상 정수여야 합니다.')
        if details['received']<=0 or details['received']!=sum(details[k] for k in ('accepted','rejected','pending')):raise DomainError('INVALID_INPUT','도착 수량은 합격+불합격+대기 수량과 같아야 합니다.')
        try:
            at=datetime.fromisoformat(details['occurred_at'].replace('Z','+00:00'))
            if at.utcoffset() is None:raise ValueError()
        except ValueError:raise DomainError('INVALID_INPUT','시간대를 포함한 입고 일시가 필요합니다.') from None
        with self.host.request(context) as session:
            svc=session._service
            def save():
                records=rows(svc)
                old=next((r for r in records if r['reference']==reference),None)
                if old:
                    if old['purchase_id']!=purchase_id or old['payload']!=details:raise DomainError('REFERENCE_CONFLICT','같은 입고 기록 번호의 내용이 다릅니다.')
                    return svc._envelope(ok=True,data=old)
                if at>datetime.fromisoformat(svc.now()):raise DomainError('INVALID_INPUT','미래 입고를 등록할 수 없습니다.')
                purchase=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE id=%s',(purchase_id,)).fetchone()
                if not purchase or purchase['payload'].get('document_type')!='purchase_request':raise DomainError('PURCHASE_REQUIRED','연결할 구매 요청서가 필요합니다.')
                if digest(dict(payload=purchase['payload'],markdown=purchase['markdown']))!=purchase['content_hash']:raise DomainError('RFQ_INTEGRITY','구매 요청서 무결성 오류입니다.')
                cid=details['component_id'];requested=next((r['S'] for r in purchase['payload']['lines'] if r['component_id']==cid),0)
                prior=sum(r['payload']['received'] for r in records if r['purchase_id']==purchase_id and r['payload']['component_id']==cid)
                from .library_fulfillment import events
                prior+=sum(r['payload']['input']['received'] for r in events(svc) if r['kind']=='receipt' and r['payload']['purchase_id']==purchase_id and r['payload']['component_id']==cid)
                if prior+details['received']>requested:raise DomainError('RECEIPT_EXCEEDS_REQUEST','구매 요청 품목·누적 도착 수량을 확인하세요.')
                row=dict(id=str(uuid4()),purchase_id=purchase_id,reference=reference,payload=details,content_hash=digest(details),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_warehouse_receipts VALUES(%s,%s,%s,%s,%s,%s,%s)',(row['id'],purchase_id,reference,canonical(details),row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'warehouse_register',dict(purchase_id=purchase_id,reference=reference,details=details),save,permission='review',request_key=request_key)

    def allocate(self,context,contract_id,item_index,receipt_id,quantity,basis_hash,reviewed,request_key):
        self.fulfillment.documents.reservations._validate(contract_id,item_index)
        if reviewed is not True or type(quantity) is not int or not 0<quantity<=1000000000:raise DomainError('INVALID_INPUT','할당할 수량과 확인을 입력하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def save():
                view=self.fulfillment._view(svc,contract_id,item_index);b=view['basis']
                if view['basis_hash']!=basis_hash:raise DomainError('STALE_REVISION','계획의 확보 상태가 변경됐습니다. 다시 확인하세요.')
                receipt=next((r for r in remaining(svc) if r['id']==receipt_id),None)
                if not receipt or receipt['purchase_id']!=b['purchase_id']:raise DomainError('RECEIPT_NOT_LINKED','이번 구매 요청에 연결된 입고 결과가 아닙니다.')
                cid=receipt['payload']['component_id'];line=next((r for r in b['lines'] if r['component_id']==cid),None)
                if not line or line['waiting'] is None or quantity>min(receipt['available'],line['waiting']):raise DomainError('ALLOCATION_EXCEEDS_AVAILABLE','검수 합격 잔량 또는 이번 계획의 부족량을 초과합니다.')
                p=dict(receipt_id=receipt_id,receipt_hash=receipt['content_hash'],component_id=cid,accepted=quantity,purchase_id=b['purchase_id'],origin=receipt['payload']['origin'],basis_hash=basis_hash)
                row=dict(id=str(uuid4()),kind='allocation',reference='allocation:'+str(uuid4()),contract_id=contract_id,item_index=item_index,reservation_id=b['reservation_id'],payload=p,content_hash=digest(p),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_supply_events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],row['kind'],row['reference'],contract_id,item_index,b['reservation_id'],canonical(p),row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'warehouse_allocate',dict(contract_id=contract_id,item_index=item_index,receipt_id=receipt_id,quantity=quantity,basis_hash=basis_hash,reviewed=reviewed),save,permission='review',request_key=request_key)
