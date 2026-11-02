"""Process-material requirement reviews and unissued RFQs, A domain only."""
from decimal import Decimal, ROUND_CEILING
from uuid import uuid4
from datetime import date
from ..model import canonical, digest
from ..service import DomainError
from .library_process_materials import load_reference, inspect_materials, amount, decimal_text


def calculate_review(data, inputs):
    if not isinstance(inputs,list):raise ValueError('자재별 필요량 목록이 필요합니다.')
    materials={m['id']:m for m in data['materials']};provided={}
    for row in inputs:
        if not isinstance(row,dict) or set(row)!={'material_id','required','unit','basis'}:
            raise ValueError('자재·총필요량·단위·산정 근거를 입력하세요.')
        mid=row['material_id']
        if not isinstance(mid,str) or mid not in materials or mid in provided:
            raise ValueError('연결된 자재를 중복 없이 입력하세요.')
        if row['unit']!=materials[mid]['stock']['unit']:raise ValueError('자재의 기준 단위가 다릅니다.')
        if not isinstance(row['basis'],str) or not row['basis'].strip() or len(row['basis'])>2000:
            raise ValueError('필요량의 산정 근거를 2,000자 이내로 입력하세요.')
        if not isinstance(row['required'],str) or len(row['required'])>64:raise ValueError('필요량은 소수점 6자리 이내 숫자로 입력하세요.')
        value=amount(row['required'])
        if value.normalize().as_tuple().exponent < -6:raise ValueError('필요량은 소수점 6자리 이내로 입력하세요.')
        provided[mid]=dict(row,required=decimal_text(value),basis=row['basis'].strip())
    result=[]
    for mid,m in materials.items():
        row=provided.get(mid);required=row['required'] if row else None
        available=m['stock']['available']
        shortage=decimal_text(max(Decimal(0),amount(required)-amount(available))) if required is not None and available is not None else None
        result.append(dict(material_id=mid,name=m['name'],unit=m['stock']['unit'],required=required,
            available=available,shortage=shortage,basis=row['basis'] if row else None,
            requirement_source='human_review_input' if row else 'unknown',evidence=m['evidence']))
    return result


def text_input(value,label,required=False):
    if not isinstance(value,str) or len(value)>2000 or (required and not value.strip()):
        raise DomainError('INVALID_INPUT',label+'을(를) 2,000자 이내로 입력하세요.')
    return value.strip()


def calculate_automatic(data, allowance_percent):
    if not isinstance(allowance_percent,str) or len(allowance_percent)>16:
        raise ValueError('여유율은 0~100% 숫자로 입력하세요.')
    allowance=amount(allowance_percent)
    if allowance>100 or allowance.normalize().as_tuple().exponent < -2:
        raise ValueError('여유율은 0~100%, 소수점 두 자리 이내로 입력하세요.')
    inputs=[]
    for m in data['materials']:
        b=m.get('usage_basis')
        if not b or b['base_per_board'] is None:continue
        setup=amount(b['setup_quantity']) if b.get('setup_quantity') is not None and data['quantity']>0 else Decimal(0)
        required=(amount(b['base_per_board'])*data['quantity']*(1+allowance/100)+setup).quantize(Decimal('.000001'),rounding=ROUND_CEILING)
        setup_label=f"{decimal_text(setup)} {b['unit']}" if b.get('setup_quantity') is not None else '미설정(합산 제외)'
        inputs.append(dict(material_id=m['id'],required=decimal_text(required),unit=m['stock']['unit'],
            basis=f"대당 기준 {b['base_per_board']} {b['unit']} × {data['quantity']}대 × (1 + {allowance_percent}%) + 준비량 {setup_label}; {b['basis']} 초기 장비 충전량 미포함."))
    rows=calculate_review(data,inputs)
    for r in rows:
        r['requirement_source']='automatic_per_board' if r['required'] is not None else 'unregistered'
        material=next(m for m in data['materials'] if m['id']==r['material_id'])
        b=material.get('usage_basis') or {}
        r['basis_origin']=b.get('origin')
        r['setup_quantity']=b.get('setup_quantity')
        r['evidence']=list(dict.fromkeys(r['evidence']+b.get('evidence',[])))
        r['status']='unregistered' if r['required'] is None else ('stock_unknown' if r['shortage'] is None else ('shortage' if amount(r['shortage'])>0 else 'covered'))
    return rows


class LibraryProcessRFQ:
    def __init__(self,stock,context):
        self.stock=stock;self.host=stock.host
        with self.host.request(context) as session:
            for table in ('pp_process_requirement_reviews','pp_process_rfq_drafts'):
                session._service.db.execute(f'''CREATE TABLE IF NOT EXISTS {table}(
                    id TEXT PRIMARY KEY,configuration_id TEXT NOT NULL,payload JSONB NOT NULL,
                    content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

    def current(self,svc,cid,quantity):
        try:
            reference=load_reference();stock=self.stock.latest(svc)
            data=inspect_materials(reference,stock['payload'] if stock else None,cid,quantity,svc.now())
        except (ValueError,KeyError,TypeError,OSError):
            raise DomainError('PROCESS_MATERIAL_INPUT','공정 자재·수량·근거를 다시 확인하세요.') from None
        fingerprint=digest(dict(reference=digest(reference),stock=stock['id'] if stock else None,
            availability=[(m['id'],m['stock']) for m in data['materials']]))
        return data,fingerprint

    def preview(self,context,configuration_id,quantity,allowance_percent):
        with self.host.request(context) as session:
            svc=session._service
            def read():
                data,fingerprint=self.current(svc,configuration_id,quantity)
                try:lines=calculate_automatic(data,allowance_percent)
                except ValueError as e:raise DomainError('INVALID_INPUT',str(e)) from None
                return svc._envelope(ok=True,data=dict(quantity=quantity,allowance_percent=allowance_percent,lines=lines,basis_hash=fingerprint))
            return svc._run(session.context['actor'],'process_requirement_preview',dict(configuration_id=configuration_id,quantity=quantity,allowance_percent=allowance_percent),read)

    def automatic_review(self,context,configuration_id,quantity,allowance_percent,job_name,basis_hash,reviewed,request_key):
        job_name=text_input(job_name,'작업명',True)
        if reviewed is not True:raise DomainError('INVALID_INPUT','조건부 기준과 요청 조건을 확인하세요.')
        binding=dict(configuration_id=configuration_id,quantity=quantity,allowance_percent=allowance_percent,job_name=job_name,basis_hash=basis_hash,reviewed=reviewed)
        with self.host.request(context) as session:
            svc=session._service
            def save():
                data,fingerprint=self.current(svc,configuration_id,quantity)
                if quantity<=0:raise DomainError('INVALID_INPUT','생산 수량은 1대 이상이어야 합니다.')
                if fingerprint!=basis_hash:raise DomainError('STALE_REVISION','재고 또는 기준이 변경되었습니다. 다시 계산하세요.')
                try:lines=calculate_automatic(data,allowance_percent)
                except ValueError as e:raise DomainError('INVALID_INPUT',str(e)) from None
                payload=dict(job_name=job_name,configuration_id=configuration_id,quantity=quantity,
                    allowance_percent=allowance_percent,lines=lines,basis_hash=fingerprint,stock_scope=data['stock_scope'],
                    reviewed=True,requirement_source='automatic_per_board',production_authorized=False,
                    stock_reserved=False,evaluated_at=data['evaluated_at'])
                return svc._envelope(ok=True,data=self.store(svc,'pp_process_requirement_reviews',configuration_id,payload,session.context['actor']))
            return svc._run(session.context['actor'],'process_requirement_auto',binding,save,permission='review',request_key=request_key)

    @staticmethod
    def fetch(svc,table,id):
        if not isinstance(id,str) or not id:raise DomainError('INVALID_INPUT','저장된 검토 식별자가 필요합니다.')
        row=svc.db.execute(f'SELECT * FROM {table} WHERE id=%s',(id,)).fetchone()
        if not row:raise DomainError('NOT_FOUND','저장된 공정 자재 검토를 찾을 수 없습니다.')
        if digest(row['payload'])!=row['content_hash']:raise DomainError('PROCESS_RFQ_INTEGRITY','저장된 검토·문서의 무결성을 확인하지 못했습니다.')
        return row

    @staticmethod
    def store(svc,table,cid,payload,actor):
        row=dict(id=str(uuid4()),configuration_id=cid,payload=payload,content_hash=digest(payload),actor=actor,created_at=svc.now())
        svc.db.execute(f'INSERT INTO {table} VALUES(%s,%s,%s,%s,%s,%s)',
                       (row['id'],cid,canonical(payload),row['content_hash'],actor,row['created_at']))
        return row

    def review(self,context,configuration_id,quantity,job_name,inputs,reviewed,request_key):
        job_name=text_input(job_name,'작업명/계약 참조',True)
        if reviewed is not True:raise DomainError('INVALID_INPUT','총필요량과 산정 근거를 확인하세요.')
        binding=dict(configuration_id=configuration_id,quantity=quantity,job_name=job_name,inputs=inputs,reviewed=reviewed)
        with self.host.request(context) as session:
            svc=session._service
            def save():
                data,fingerprint=self.current(svc,configuration_id,quantity)
                if quantity<=0:raise DomainError('INVALID_INPUT','생산 수량은 1대 이상이어야 합니다.')
                try:lines=calculate_review(data,inputs)
                except ValueError as e:raise DomainError('INVALID_INPUT',str(e)) from None
                if not any(r['required'] is not None for r in lines):raise DomainError('INVALID_INPUT','하나 이상의 자재 필요량을 입력하세요.')
                payload=dict(job_name=job_name,configuration_id=configuration_id,quantity=quantity,inputs=inputs,
                    lines=lines,basis_hash=fingerprint,stock_scope=data['stock_scope'],
                    reviewed=True,requirement_source='human_review_input',production_authorized=False,
                    stock_reserved=False,evaluated_at=data['evaluated_at'])
                row=self.store(svc,'pp_process_requirement_reviews',configuration_id,payload,session.context['actor'])
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'process_requirement_review',binding,save,permission='review',request_key=request_key)

    def inspect(self,context,configuration_id,review_id):
        with self.host.request(context) as session:
            svc=session._service
            def read():
                # Validate scope even when there is no saved review.
                self.current(svc,configuration_id,0)
                recent=svc.db.execute('SELECT id,payload,actor,created_at FROM pp_process_requirement_reviews WHERE configuration_id=%s ORDER BY created_at DESC,id DESC LIMIT 30',(configuration_id,)).fetchall()
                summaries=[dict(id=r['id'],job_name=r['payload']['job_name'],quantity=r['payload']['quantity'],created_at=r['created_at'],actor=r['actor']) for r in recent]
                if not review_id:return svc._envelope(ok=True,data=dict(reviews=summaries,review=None,drafts=[]))
                row=self.fetch(svc,'pp_process_requirement_reviews',review_id);p=row['payload']
                if row['configuration_id']!=configuration_id:raise DomainError('INVALID_INPUT','다른 제품의 검토입니다.')
                data,fingerprint=self.current(svc,configuration_id,p['quantity'])
                drafts=[]
                for d in svc.db.execute("SELECT id FROM pp_process_rfq_drafts WHERE payload->>'review_id'=%s ORDER BY created_at DESC,id DESC",(review_id,)).fetchall():
                    drafts.append(self.fetch(svc,'pp_process_rfq_drafts',d['id']))
                return svc._envelope(ok=True,data=dict(reviews=summaries,review=row,current=fingerprint==p['basis_hash'],drafts=drafts))
            return svc._run(session.context['actor'],'process_requirement_get',dict(configuration_id=configuration_id,review_id=review_id),read)

    def create(self,context,review_id,material_ids,supplier,desired_date,date_basis,destination,note,reviewed,request_key):
        supplier=text_input(supplier,'공급사');destination=text_input(destination,'납품처');note=text_input(note,'요청 의견')
        desired_date=text_input(desired_date,'희망 입고일');date_basis=text_input(date_basis,'입고일 근거',bool(desired_date))
        if desired_date:
            try:date.fromisoformat(desired_date)
            except ValueError:raise DomainError('INVALID_INPUT','희망 입고일을 날짜로 입력하세요.') from None
        if reviewed is not True:raise DomainError('INVALID_INPUT','선택 품목과 요청 조건을 확인하세요.')
        if not isinstance(material_ids,list) or not material_ids or any(not isinstance(x,str) for x in material_ids) or len(set(material_ids))!=len(material_ids):
            raise DomainError('INVALID_INPUT','부족 자재를 중복 없이 선택하세요.')
        binding=dict(review_id=review_id,material_ids=material_ids,supplier=supplier,desired_date=desired_date,date_basis=date_basis,destination=destination,note=note,reviewed=reviewed)
        with self.host.request(context) as session:
            svc=session._service
            def save():
                row=self.fetch(svc,'pp_process_requirement_reviews',review_id);p=row['payload'];cid=row['configuration_id']
                data,fingerprint=self.current(svc,cid,p['quantity'])
                if fingerprint!=p['basis_hash']:raise DomainError('STALE_REVISION','재고·사용기한 또는 근거 자료가 변경됐습니다. 필요량 검토를 다시 저장하세요.')
                fresh=calculate_automatic(data,p['allowance_percent']) if p.get('requirement_source')=='automatic_per_board' else calculate_review(data,p['inputs'])
                eligible={r['material_id']:r for r in fresh if r['shortage'] is not None and amount(r['shortage'])>0}
                if not set(material_ids)<=set(eligible):raise DomainError('INVALID_INPUT','필요량과 재고가 확인된 부족 자재만 RFQ에 넣을 수 있습니다.')
                lines=[eligible[mid] for mid in sorted(material_ids)]
                unknown=[r['name'] for r in fresh if r['shortage'] is None]
                words=['공정 자재 견적요청서 (RFQ) — 미발송 초안','',f"작업/계약 참조: {p['job_name']}",
                    f"제품 구성: {cid}",f"생산 검토 수량: {p['quantity']}대",f'공급사: {supplier or "지정 필요"}',
                    f'희망 입고일: {desired_date or "협의 필요"}',f'입고일 근거: {date_basis or "미확정"}',f'납품처: {destination or "협의 필요"}','',
                    '요청 품목 (확인된 부족량 기준, 발주 수량 아님):']
                for r in lines:
                    words.extend([f"- {r['name']}: {r['shortage']} {r['unit']}",
                        f"  총필요량 {r['required']} − 가용량 {r['available']} {r['unit']}",f"  산정 근거: {r['basis']}"])
                words.extend(['',f'요청 의견: {note or "없음"}',f'미확인으로 제외된 자재: {", ".join(unknown) or "없음"}',
                    ('수량은 제품 생산 수량·대당 기준·여유율로 자동 계산했습니다. 기준 미등록 자재는 제외되며 초기 충전량은 미포함입니다.' if p.get('requirement_source')=='automatic_per_board' else '수량은 담당자가 입력·확인한 작업 총필요량 기준입니다.') + ' 조건부 이론량을 확정 소비량으로 간주하지 않습니다.',
                    '구매팀 확인: 공급사·가격·납기·MOQ·포장 단위 및 대체품 조건. 납기와 발주 수량은 구매 검토 후 확정.',
                    '재고 예약·사용 차감·발주·외부 발송은 수행하지 않았습니다.',f"검토 ID: {review_id}",f"재고·근거 기준: {fingerprint}"])
                payload=dict(document_type='process_material_rfq',review_id=review_id,job_name=p['job_name'],quantity=p['quantity'],
                    lines=lines,unknown_materials=unknown,status='draft_not_sent',basis_hash=fingerprint,
                    supplier=supplier,desired_date=desired_date,date_basis=date_basis,destination=destination,note=note,text='\n'.join(words)+'\n')
                draft=self.store(svc,'pp_process_rfq_drafts',cid,payload,session.context['actor'])
                return svc._envelope(ok=True,data=draft)
            return svc._run(session.context['actor'],'process_rfq_create',binding,save,permission='review',request_key=request_key)
