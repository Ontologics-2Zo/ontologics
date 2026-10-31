"""Version-bound production planning drafts; never production authorization."""
from uuid import uuid4
from ..model import canonical,digest
from ..service import DomainError


class LibraryPlanDocument:
    def __init__(self,reservations,context):
        self.reservations=reservations;self.host=reservations.host
        with self.host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_production_plan_drafts(
                id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,item_index INTEGER NOT NULL,
                payload JSONB NOT NULL,markdown TEXT NOT NULL,content_hash TEXT NOT NULL,
                actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

    def _basis(self,svc,contract_id,item_index,include_auto=True):
        plan=self.reservations._plan(svc,contract_id,item_index)
        reservation=plan['reservation']
        if not reservation:raise DomainError('RESERVATION_REQUIRED','자재 예약을 먼저 확인·저장하세요.')
        if plan['stale']:raise DomainError('STALE_REVISION',plan['message'])
        owner=(contract_id,item_index)
        original=svc.db.execute('SELECT title,sha256 FROM pp_contract_originals WHERE id=%s',(contract_id,)).fetchone()
        choice=self.reservations.rfq.replacements.latest(svc,*owner)
        material=svc.db.execute('SELECT * FROM pp_contract_material_reviews WHERE id=%s',(reservation['payload']['material_review_id'],)).fetchone()
        link=svc.db.execute('SELECT * FROM pp_contract_configuration_links WHERE id=%s',(material['link_id'],)).fetchone()
        check=svc.db.execute('SELECT * FROM pp_contract_reading_checks WHERE id=%s',(link['review_id'],)).fetchone()
        interpretation=svc.db.execute('SELECT * FROM pp_contract_interpretations WHERE id=%s',(link['interpretation_id'],)).fetchone()
        if digest(interpretation['result'])!=interpretation['result_sha256'] or check['result_sha256']!=interpretation['result_sha256'] or interpretation['source_sha256']!=original['sha256']:
            raise DomainError('RESULT_INTEGRITY','계약 해석과 확인 기록의 무결성을 확인하지 못했습니다.')
        purchase=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',owner).fetchone()
        if purchase and digest(dict(payload=purchase['payload'],markdown=purchase['markdown']))!=purchase['content_hash']:raise DomainError('RFQ_INTEGRITY','구매 초안의 무결성을 확인하지 못했습니다.')
        if purchase and (purchase['payload'].get('document_type')!='purchase_request' or purchase['payload'].get('choice_id')!=choice['id']):purchase=None
        fields=interpretation['result']['items'][item_index]['fields']
        decisions=check.get('decisions') or {}
        def value(name):
            decision=decisions.get(f'{item_index}:{name}',{})
            if decision.get('state')=='unresolved':return '미확인'
            if decision.get('state')=='corrected':return decision.get('value')
            if decision.get('state')!='accepted' and f'{item_index}:{name}' not in (check.get('scopes') or []):return '미확인'
            parsed=fields.get(name,{}).get('value')
            return parsed if parsed is not None else '미확인'
        unresolved=[k.split(':',1)[1] for k,v in decisions.items() if k.startswith(f'{item_index}:') and v.get('state')=='unresolved']
        lines=plan['lines'];waiting=sum(r['waiting'] is not None and r['waiting']>0 for r in lines);unknown=sum(r['unknown'] for r in lines)
        status='일부 확보 · 자재 확보 조건부' if waiting else '확인 대기' if unknown or unresolved else '자재 확보 완료'
        basis=dict(contract_id=contract_id,item_index=item_index,title=original['title'],ordered_quantity=value('ordered_quantity'),delivery_due=value('delivery_due'),constraints=value('constraints'),quantity=plan['quantity'],configuration=plan['configuration'],status=status,reservation_id=reservation['id'],reservation_hash=reservation['content_hash'],choice_id=choice['id'],lines=lines,replacements=plan['replacements'],unknown_count=unknown,waiting_count=waiting,unresolved_contract_fields=unresolved,material_unresolved=choice['payload']['result'].get('unresolved',[]),purchase_id=purchase['id'] if purchase else None,purchase_markdown=purchase['markdown'] if purchase else None,production_authorized=False,completion_date=None,producible_quantity=None,schedule=None)
        from .library_fulfillment import events
        records=[r for r in events(svc) if r['reservation_id']==reservation['id']]
        basis['purchase_transmitted']=any(r['kind']=='transmission' and r['payload']['purchase_id']==basis['purchase_id'] for r in records)
        schedule=next((r for r in reversed(records) if r['kind']=='schedule'),None)
        if schedule and schedule['payload']['basis_hash']==digest(basis):
            basis['schedule']=dict(id=schedule['id'],**schedule['payload'])
            basis['completion_date']=schedule['payload']['completion_at']
        if include_auto:
            from .library_schedule import attach
            basis=attach(svc,basis)
        return basis

    @staticmethod
    def render(basis,note=''):
        b=basis
        labels={'contract_item_id':'계약 품목 번호','configuration_id':'제품 구성 식별','ordered_quantity':'주문 수량','fulfilled_quantity':'기존 이행 수량','allocated_quantity':'기존 배정 수량','planned_input_quantity':'신규 생산 검토 수량','delivery_due':'고객 납기','constraints':'변경·대체 조건'}
        text=['생산계획서 — 검토 초안',f"관련 계약: {b['title']}",f"계약 주문 수량: {b['ordered_quantity']}",f"생산 검토 수량: {b['quantity']}대",f"제품 구성: {b['configuration']}",f"계획 상태: {b['status']}",f"고객 납기: {b['delivery_due']}",'생산 가능 수량: 미확정',f"생산 완료일: {b['completion_date'] or '미확정'}",'생산 승인·출고 지시 아님. 설비·인력·공정·일정은 별도 검토 필요.',f"계약의 변경·대체 조건: {b['constraints']}",'','선택한 대체 부품:']
        text += [f"- {r['source_name']} → {r['target_name']}: {r['quantity']}개" for r in b['replacements']] or ['- 대체 없음']
        text+=['','자재 확보 내역 (입고 대기는 미확보 수량):']
        for r in b['lines']:
            text.append(f"- {r['name']}: 필요 {r['demand'] if r['demand'] is not None else '미확인'}, 예약 {r['reserve'] if r['reserve'] is not None else '미확인'}, 입고 대기 {r['waiting'] if r['waiting'] is not None else '미확인'}")
        text += ['',f"부족 {b['waiting_count']}종 · 자재 미확인 {b['unknown_count']}종",'계약 미확인 항목: '+(', '.join(labels.get(x,x) for x in b['unresolved_contract_fields']) or '별도 표시 없음'),'미확인 자재의 실장 여부·제품당 수량·재고 근거를 확인해야 합니다.' if b['unknown_count'] else '자재 수량 미확인 항목 없음',f"검토 의견: {note or '추가 의견 없음'}",'','구매 요청서 원문 (작성 당시 기준; 현재 전달 기록 있음):' if b.get('purchase_transmitted') else '구매 요청서 (미전달 초안):',b['purchase_markdown'] or '현재 적용안에 연결된 구매 요청 초안 없음','',f"예약 기록: {b['reservation_id']}"]
        if b.get('auto_schedule'):
            a=b['auto_schedule']
            text+=['','DB 기준 일정 검토안 (생산 승인·설비 예약 아님):',f"라인: {a['resource']['name']}",f"조건부 시작 예정: {a['start_at']}",f"조건부 완료 예정: {a['completion_at']}",f"표준 작업시간: {a['work_minutes']}분 / 필요 인원: {a['route']['required_staff']}명",f"작업표준 개정: {a['route']['revision']}"]
            text+=['- '+c for c in a['conditions']]
            text+=['- 작업 구간: '+r['start_at']+' ~ '+r['end_at'] for r in a['segments']]
        return '\n'.join(text)

    def _latest(self,svc,owner):
        row=svc.db.execute('SELECT * FROM pp_production_plan_drafts WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',owner).fetchone()
        if row and digest(dict(payload=row['payload'],markdown=row['markdown']))!=row['content_hash']:raise DomainError('PLAN_INTEGRITY','생산계획 문서의 무결성을 확인하지 못했습니다.')
        return row

    def inspect(self,context,contract_id,item_index):
        self.reservations._validate(contract_id,item_index)
        with self.host.request(context) as session:
            svc=session._service
            def read():
                previous=self._latest(svc,(contract_id,item_index))
                try:basis=self._basis(svc,contract_id,item_index)
                except DomainError as exc:
                    if previous and exc.code in ('RESERVATION_REQUIRED','STALE_REVISION','CHOICE_REQUIRED','MATERIAL_REVIEW_REQUIRED'):
                        return svc._envelope(ok=True,data=dict(basis=None,basis_hash=None,draft=previous,draft_current=False,message=str(exc),markdown=previous['markdown']))
                    raise
                key=digest(basis)
                return svc._envelope(ok=True,data=dict(basis=basis,basis_hash=key,markdown=self.render(basis),draft=previous,draft_current=bool(previous and previous['payload']['basis_hash']==key),message=''))
            return svc._run(session.context['actor'],'production_plan_inspect',dict(contract_id=contract_id,item_index=item_index),read)

    def save(self,context,contract_id,item_index,basis_hash,note,reviewed,request_key):
        self.reservations._validate(contract_id,item_index)
        if reviewed is not True or not isinstance(note,str) or len(note)>2000 or not isinstance(basis_hash,str):raise DomainError('INVALID_INPUT','조건을 확인하고 검토 의견은 2,000자 이내로 입력하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def write():
                basis=self._basis(svc,contract_id,item_index)
                if digest(basis)!=basis_hash:raise DomainError('STALE_REVISION','예약·계획·구매 초안이 바뀌었습니다. 다시 확인하세요.')
                payload=dict(basis=basis,basis_hash=basis_hash,note=note.strip(),reviewed=True)
                previous=self._latest(svc,(contract_id,item_index))
                if previous and previous['payload']==payload:return svc._envelope(ok=True,data=dict(draft=previous))
                markdown=self.render(basis,note.strip())
                row=dict(id=str(uuid4()),contract_id=contract_id,item_index=item_index,payload=payload,markdown=markdown,content_hash=digest(dict(payload=payload,markdown=markdown)),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_production_plan_drafts VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],contract_id,item_index,canonical(payload),markdown,row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=dict(draft=row))
            return svc._run(session.context['actor'],'production_plan_save',dict(contract_id=contract_id,item_index=item_index,basis_hash=basis_hash,note=note,reviewed=reviewed),write,permission='review',request_key=request_key)
