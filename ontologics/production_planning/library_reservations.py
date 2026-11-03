"""A-only reservation ledger. All writes run through the host transaction lock.

Exact component IDs share one conservative A inventory pool. This is not a
warehouse integration, receipt, issue, physical count or production approval.
"""
from uuid import uuid4
from ..model import canonical, digest
from ..service import DomainError


def initialize(svc):
    from .library_fulfillment import initialize as initialize_supply
    initialize_supply(svc)
    svc.db.execute('''CREATE TABLE IF NOT EXISTS pp_material_reservations(
        id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,item_index INTEGER NOT NULL,
        payload JSONB NOT NULL,content_hash TEXT NOT NULL,state TEXT NOT NULL,
        actor TEXT NOT NULL,created_at TEXT NOT NULL,released_by TEXT,released_at TEXT)''')
    svc.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS pp_one_active_material_reservation ON pp_material_reservations(contract_id,item_index) WHERE state='active'")


def active_rows(svc,exclude=None):
    rows=svc.db.execute("SELECT * FROM pp_material_reservations WHERE state='active' ORDER BY id").fetchall()
    for r in rows:
        if digest(r['payload'])!=r['content_hash']:raise DomainError('RESERVATION_INTEGRITY','예약 기록의 무결성을 확인하지 못했습니다.')
    from .library_fulfillment import apply_receipts
    return [apply_receipts(svc,r) for r in rows if exclude is None or (r['contract_id'],r['item_index'])!=exclude]


def reservation_token(svc,owner):
    rows=active_rows(svc,owner)
    return digest([(r['id'],r['content_hash']) for r in rows]) if rows else None


def reserved_totals(svc,exclude=None):
    from .library_warehouse import held_totals
    totals=held_totals(svc)
    for r in active_rows(svc,exclude):
        for line in r['payload']['lines']:
            cid=line['component_id'];totals[cid]=totals.get(cid,0)+(line['reserve'] or 0)
    return totals


def current_reservation(svc,owner):
    return next((r for r in active_rows(svc) if (r['contract_id'],r['item_index'])==owner),None)


class LibraryReservations:
    def __init__(self,rfq):
        self.rfq=rfq;self.materials=rfq.materials;self.host=rfq.host

    def _plan(self,svc,contract_id,item_index):
        owner=(contract_id,item_index);active=current_reservation(svc,owner)
        material=svc.db.execute('SELECT * FROM pp_contract_material_reviews WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',owner).fetchone()
        if not material:raise DomainError('MATERIAL_REVIEW_REQUIRED','소요량을 먼저 계산하세요.')
        try:
            self.rfq.replacements.assert_base(svc,contract_id,item_index,material)
            choice=self.rfq.replacements.latest(svc,*owner)
            if not self.rfq.replacements.current(svc,choice,material):raise DomainError('CHOICE_REQUIRED','최신 대체 적용 또는 미적용 결과를 저장하세요.')
        except DomainError as exc:
            if not active:raise
            return dict(reservation=active,lines=active['payload']['lines'],quantity=active['payload']['quantity'],configuration=active['payload']['configuration'],replacements=active['payload']['replacements'],stale=True,message=str(exc),preview_hash=None)
        if active:
            stale=active['payload']['choice_id']!=choice['id']
            return dict(reservation=active,lines=active['payload']['lines'],quantity=active['payload']['quantity'],configuration=active['payload']['configuration'],replacements=active['payload']['replacements'],stale=stale,message='계획이 변경됐습니다. 기존 예약을 해제한 뒤 새 계획으로 예약하세요.' if stale else '',preview_hash=active['payload']['preview_hash'])
        result=choice['payload']['result']
        stock=self.materials.stock.calculate(self.materials.stock.latest(svc,result['configuration_id']),svc,owner)
        availability={cid:r['available_quantity'] for cid,r in stock['components'].items()}
        totals=reserved_totals(svc,owner)
        from .library_fulfillment import received_totals
        incoming=received_totals(svc)
        for report in choice['payload'].get('reports',[]):
            p=report['payload'];cid=p['target_id']
            if cid not in availability:availability[cid]=max(0,p.get('original_report_available',p.get('gross_available',p['available']))+incoming.get(cid,0)-totals.get(cid,0))
        draft=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',owner).fetchone()
        if draft and digest(dict(payload=draft['payload'],markdown=draft['markdown']))!=draft['content_hash']:raise DomainError('RFQ_INTEGRITY','구매 초안 무결성을 확인하지 못했습니다.')
        purchase=draft if draft and draft['payload'].get('document_type')=='purchase_request' and draft['payload'].get('choice_id')==choice['id'] else None
        requested={r['component_id']:r['S'] for r in purchase['payload']['lines']} if purchase else {}
        lines=[]
        for row in result['components']:
            d=row['D'];a=availability.get(row['component_id']);known=type(d) is int and type(a) is int
            n=min(d,a) if known else None;waiting=max(0,d-a) if known else None
            lines.append(dict(component_id=row['component_id'],name=row['name'],demand=d,available=a,reserve=n,waiting=waiting,requested=requested.get(row['component_id'],0),unknown=not known))
        payload=dict(choice_id=choice['id'],material_review_id=material['id'],quantity=material['quantity'],configuration=result['configuration_name'],replacements=result.get('replacements',[]),lines=lines,purchase_id=purchase['id'] if purchase else None,reservation_token=reservation_token(svc,owner),production_authorized=False)
        payload['preview_hash']=digest(payload)
        return dict(payload,reservation=None,stale=False,message='')

    def list_active(self,context,contract_id):
        self._validate(contract_id,0)
        with self.host.request(context) as session:
            svc=session._service
            return svc._run(session.context['actor'],'active_material_reservations',dict(contract_id=contract_id),lambda:svc._envelope(ok=True,data=[dict(id=r['id'],item_index=r['item_index']) for r in active_rows(svc) if r['contract_id']==contract_id]))

    def inspect(self,context,contract_id,item_index):
        self._validate(contract_id,item_index)
        with self.host.request(context) as session:
            svc=session._service
            return svc._run(session.context['actor'],'reservation_plan',dict(contract_id=contract_id,item_index=item_index),lambda:svc._envelope(ok=True,data=self._plan(svc,contract_id,item_index)))

    @staticmethod
    def _validate(contract_id,item_index):
        if not isinstance(contract_id,str) or type(item_index) is not int or item_index<0:raise DomainError('INVALID_INPUT','계약과 주문 품목을 확인하세요.')

    def reserve(self,context,contract_id,item_index,preview_hash,reviewed,request_key):
        self._validate(contract_id,item_index)
        if reviewed is not True or not isinstance(preview_hash,str):raise DomainError('INVALID_INPUT','계획과 미확인 조건을 확인하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def save():
                plan=self._plan(svc,contract_id,item_index)
                if plan['stale'] or plan['preview_hash']!=preview_hash:raise DomainError('STALE_REVISION','계획 또는 가용재고가 바뀌었습니다. 다시 확인하세요.')
                if plan['reservation']:return svc._envelope(ok=True,data=plan)
                payload={k:v for k,v in plan.items() if k not in ('reservation','stale','message')}
                row=dict(id=str(uuid4()),contract_id=contract_id,item_index=item_index,payload=payload,content_hash=digest(payload),state='active',actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_material_reservations(id,contract_id,item_index,payload,content_hash,state,actor,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],contract_id,item_index,canonical(payload),row['content_hash'],row['state'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=dict(plan,reservation=row))
            return svc._run(session.context['actor'],'reserve_materials',dict(contract_id=contract_id,item_index=item_index,preview_hash=preview_hash,reviewed=reviewed),save,permission='review',request_key=request_key)

    def release(self,context,contract_id,item_index,reservation_id,request_key):
        self._validate(contract_id,item_index)
        with self.host.request(context) as session:
            svc=session._service
            def save():
                row=svc.db.execute('SELECT * FROM pp_material_reservations WHERE id=%s AND contract_id=%s AND item_index=%s',(reservation_id,contract_id,item_index)).fetchone()
                if not row:raise DomainError('NOT_FOUND','이 계획의 예약 기록을 찾지 못했습니다.')
                if digest(row['payload'])!=row['content_hash']:raise DomainError('RESERVATION_INTEGRITY','예약 기록의 무결성을 확인하지 못했습니다.')
                svc.db.execute("UPDATE pp_material_reservations SET state='released',released_by=%s,released_at=%s WHERE id=%s AND state='active'",(session.context['actor'],svc.now(),reservation_id))
                return svc._envelope(ok=True,data=dict(id=reservation_id,state='released'))
            return svc._run(session.context['actor'],'release_materials',dict(contract_id=contract_id,item_index=item_index,reservation_id=reservation_id),save,permission='review',request_key=request_key)
