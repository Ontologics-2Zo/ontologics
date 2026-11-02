"""Archive shortage -> evidence-based discovery -> immutable, unissued RFQ draft."""
from uuid import uuid4
from ..model import canonical, digest
from ..service import DomainError
from .substitution import query_substitution, build_substitution_review, load_revision_evidence, RIGHT


class LibraryRFQ:
    def __init__(self,materials,context):
        self.materials=materials;self.host=materials.host
        with self.host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_contract_rfq_drafts(
                id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,item_index INTEGER NOT NULL,
                material_review_id TEXT NOT NULL,payload JSONB NOT NULL,markdown TEXT NOT NULL,
                content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

        from .library_alternatives import LibraryAlternatives
        self.alternatives=LibraryAlternatives(self,context)
        from .library_replacements import LibraryReplacements
        self.replacements=LibraryReplacements(self,context)

    def choice_current(self,svc,choice,material):
        checker=self.replacements if choice and choice["payload"].get("mode")=="component_replacement" else self.alternatives
        return checker.current(svc,choice,material)

    def inspect(self,context,contract_id,item_index,include_choice=True,require_component=False):
        current=self.materials.get(context,contract_id,item_index)
        if not current['ok']:return current
        data=current['data'];saved=data['saved']
        if not saved or not data['saved_current'] or data['stock_changed']:
            raise DomainError('MATERIAL_REVIEW_REQUIRED','최신 제품과 재고로 소요량을 다시 계산하세요.')
        result=saved['result']
        if result['errors'] or result['conflicts']:raise DomainError('INVALID_MATERIAL_REVIEW','부품 자료의 오류·충돌을 먼저 해결하세요.')
        shortages=[r for r in result['components'] if type(r['S']) is int and r['S']>0]
        evidence=load_revision_evidence()
        queries=[]
        with self.host.request(context) as session:
            for row in shortages:
                queries.append(query_substitution(session,configuration_id=result['configuration_id'],component_id=row['component_id'],candidate_configuration_id=RIGHT,data_version=result['data_version']))
            review=build_substitution_review(result,queries,[evidence],[data['fields'].get('constraints',{})])
            names={n['id']:n['name'] for q in queries for n in (q.get('catalog',{}).get('lookup_response') or {}).get('data',[]) if n.get('type')=='ComponentModel'}
            for candidate in review['candidates']:candidate['name']=names.get(candidate['component_id'],candidate['component_id'])
            review={k:review[k] for k in ('candidates','diagnostics','states','reviewed_scope','required_review')}
            svc=session._service
            def read():
                draft=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
                if draft and digest(dict(payload=draft['payload'],markdown=draft['markdown']))!=draft['content_hash']:raise DomainError('RFQ_INTEGRITY','저장된 RFQ의 무결성을 확인하지 못했습니다.')
                choice=self.alternatives.latest(svc,contract_id,item_index) if include_choice else None
                choice_current=self.choice_current(svc,choice,saved)
                if require_component and choice and choice["payload"].get("mode")!="component_replacement":choice_current=False
                selected=choice['payload']['result'] if choice_current else result
                from .library_reservations import current_reservation
                from copy import deepcopy
                active=current_reservation(svc,(contract_id,item_index))
                if active and choice and active['payload']['choice_id']==choice['id']:
                    selected=deepcopy(selected)
                    held={r['component_id']:r for r in active['payload']['lines']}
                    for component in selected['components']:
                        if component['component_id'] in held:
                            line=held[component['component_id']];component['A']=line['reserve'];component['S']=line['waiting']
                selected_shortages=[r for r in selected['components'] if type(r['S']) is int and r['S']>0]
                return svc._envelope(ok=True,data=dict(choice=choice,choice_current=choice_current,selected_configuration=selected['configuration_name'],original_configuration=result['configuration_id'],contract_id=contract_id,item_index=item_index,title=data['title'],material_review_id=saved['id'],original_shortages=shortages,shortages=selected_shortages,unknown_count=sum(r['S'] is None for r in selected['components']),review=review,source=evidence.get('metadata'),stock_label=selected['stock'].get('label','재고'),fields=data['fields'],draft=draft,draft_current=bool(draft and all(any(c['component_id']==r['component_id'] and c['S']==r['S'] for c in selected_shortages) for r in draft['payload']['lines']) and draft['material_review_id']==saved['id'] and (draft['payload'].get('document_type')!='purchase_request' or choice_current and draft['payload'].get('choice_id')==choice['id']))))
            return svc._run(session.context['actor'],'rfq_inspect',dict(contract_id=contract_id,item_index=item_index),read)

    def create(self,context,contract_id,item_index,material_review_id,component_ids,supplier,desired_date,destination,note,request_key,request_type="rfq",department="",date_basis="",technical_note="",reviewed=False,choice_id=None,require_component=False):
        for value in (supplier,desired_date,destination,note,department,date_basis,technical_note):
            if not isinstance(value,str) or len(value)>2000:raise DomainError('INVALID_INPUT','견적 요청 조건은 2,000자 이내로 입력하세요.')
        if request_type not in ('rfq','purchase_request'):raise DomainError('INVALID_INPUT','지원하지 않는 문서 종류입니다.')
        if request_type=='purchase_request' and (reviewed is not True or (desired_date and not date_basis.strip())):raise DomainError('INVALID_INPUT','요청 내용을 전체 확인하고, 필요일을 지정했다면 산정 근거를 입력하세요.')
        if not isinstance(component_ids,list) or not component_ids or any(not isinstance(x,str) for x in component_ids) or len(set(component_ids))!=len(component_ids):raise DomainError('INVALID_INPUT','견적 요청할 원부품을 선택하세요.')
        if desired_date:
            from datetime import date
            try:date.fromisoformat(desired_date)
            except ValueError:raise DomainError('INVALID_INPUT','희망 입고일을 날짜로 입력하세요.') from None
        current=self.inspect(context,contract_id,item_index,require_component=require_component)
        if not current['ok']:return current
        data=current['data']
        if data['material_review_id']!=material_review_id:raise DomainError('STALE_REVISION','소요량 검토가 변경됐습니다. 다시 확인하세요.')
        if request_type=='purchase_request' and not data['choice_current']:raise DomainError('CHOICE_REQUIRED','원안 또는 대체안의 비교 결과를 먼저 확인하고 선택하세요.')
        if request_type=='purchase_request' and choice_id!=data['choice']['id']:raise DomainError('STALE_REVISION','적용안이 변경됐습니다. 구매 요청 화면을 다시 확인하세요.')
        indexed={r['component_id']:r for r in data['shortages']}
        if not set(component_ids)<=set(indexed):raise DomainError('INVALID_INPUT','현재 부족한 원부품만 견적 요청할 수 있습니다.')
        lines=[indexed[c] for c in sorted(component_ids)]
        payload=dict(choice_id=data['choice']['id'] if data['choice_current'] else None,selected_configuration=data['selected_configuration'],document_type=request_type,department=department.strip(),date_basis=date_basis.strip(),technical_note=technical_note.strip(),reviewed=reviewed if request_type=='purchase_request' else False,contract_constraints=data['fields'].get('constraints',{}).get('value'),material_review_id=material_review_id,lines=lines,supplier=supplier.strip(),desired_date=desired_date,destination=destination.strip(),note=note.strip(),status='draft_not_sent',stock_label=data['stock_label'],unknown_count=data['unknown_count'],substitution=data['review'])
        # Plain text document: never interpreted as HTML, no supplier addresses inferred.
        text=['견적요청서 (RFQ) — 검토용 초안','',f"관련 계약: {data['title']}",f"수신: {supplier.strip() or '공급사 지정 필요'}",f"희망 입고일: {desired_date or '협의 필요 (생산·검사·배송 일정 미확정)'}",f"납품 장소: {destination.strip() or '확인 필요'}",'', '아래 원부품에 대한 견적을 요청드립니다.','']
        for row in lines:text.append(f"- {row['name']}: {row['S']}개 (부족량 기준 견적 요청 수량)")
        text+=['','요청 회신 사항: 단가·통화·견적 유효기간, 재고 보유량, 납기, 최소주문수량(MOQ), 포장/주문 단위, 배송비·세금 및 결제 조건.','대체품 제안 시 사양서·핀맵·패키지·호환 근거를 함께 제출해 주세요. 대체 적용은 별도 기술 검토와 계약 조건 확인 후 결정합니다.','',f"검토 기준: {data['stock_label']}. 공급사 MOQ/포장 단위는 아직 확인되지 않았습니다.",f"별도 확인 대상: 부족량 미확인 {data['unknown_count']}종. 이 문서는 확인된 부족 원부품 중 선택 항목만 포함합니다.",'이 문서는 미발송 초안이며 구매 발주 또는 대체 승인 문서가 아닙니다.']
        if note.strip():text+=['', '추가 요청 사항:',note.strip()]
        if request_type=='purchase_request':
            text=['구매 요청서 — 제조·생산계획 검토 초안','',f"관련 계약: {data['title']}",f"요청 부서: {department.strip() or '확인 필요'}",'수신: 구매 담당',f"자재 필요일: {desired_date or '미정 — 생산 일정 확인 후 협의'}",f"필요일 산정 근거: {date_basis.strip() or '미확인'}",f"사용·입고 장소: {destination.strip() or '구매·자재 담당과 협의'}",'', f"구매 검토 요청 품목 ({data['selected_configuration']} 기준):"]
            for row in lines:text.append(f"- {row['name']}: {row['S']}개 (부족량 기준 요청 수량, 발주 수량 아님)")
            text+=['',f"계약의 변경·대체 조건: {payload['contract_constraints'] or '미확인'}",'적용안 선택 근거: '+data['choice']['payload']['note'], '기술 검토 기록: '+(data['choice']['payload']['technical_basis'] or '원안 유지'), '계약 허용 기록: '+(data['choice']['payload']['contract_basis'] or '원안 유지'), '이는 담당자의 계획 기준 선택 기록이며 생산 실행 또는 재고 차감이 아닙니다.']
            for candidate in data['review']['candidates']:text.append(f"검토 후보: {candidate['name']} — 다른 설계 개정의 사용 사례, 직접 교환 미승인")
            text+=['',f"추가 기술 요구사항: {technical_note.strip() or '추가 입력 없음 — 원부품 사양 및 계약 조건 유지'}",f"요청 사유·업무 영향: {note.strip() or '생산 검토에서 확인된 자재 부족'}",f"재고 기준: {data['stock_label']}",f"부족량 미확인 {data['unknown_count']}종은 본 요청에서 제외. 별도 확인 필요.",'','구매 담당 검토 사항: 공급사 탐색·선정, RFQ 발송, 가격·납기·MOQ·포장 단위·운송비·세금·거래 조건 비교. 발주 수량과 공급처는 구매 검토 후 결정.','제조 담당 확인: 선택 품목·부족 수량·미확정 조건을 검토함. 구매 승인·대체 승인·발주가 아님.','미전달 초안 — 다운로드 후 기존 업무 채널로 전달.']
        if request_type=='purchase_request' and data['choice']['payload'].get('mode')=='component_replacement':
            text+=['', '적용한 특정 부품 대체 (제품 구성 유지):']
            for a in data['choice']['payload']['result']['replacements']:text.append(f"- {a['source_name']} → {a['target_name']}: {a['quantity']}개 (1:1 계획)")
            text.append('대체품 재고는 담당자 검토 기록 기준이며 실제 예약·차감하지 않았습니다.')
        markdown='\n'.join(text)+'\n'
        with self.host.request(context) as session:
            svc=session._service
            def save():
                # Serialize with all archive review/material writes and reject stale concurrent changes.
                svc.db.execute('SELECT id FROM pp_contract_originals WHERE id=%s FOR UPDATE',(contract_id,)).fetchone()
                latest=svc.db.execute('SELECT * FROM pp_contract_material_reviews WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
                if not latest or latest['id']!=material_review_id:raise DomainError('STALE_REVISION','소요량 검토가 변경됐습니다.')
                material=latest['result']
                stock=self.materials.stock.latest(svc,material['configuration_id'])
                stock_id=stock['id'] if stock else None
                link=svc.db.execute('SELECT * FROM pp_contract_configuration_links WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
                interpretation=svc.db.execute('SELECT id FROM pp_contract_interpretations WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,)).fetchone()
                check=svc.db.execute('SELECT id FROM pp_contract_reading_checks WHERE interpretation_id=%s ORDER BY checked_at DESC,id DESC LIMIT 1',(interpretation['id'],)).fetchone() if interpretation else None
                if (stock_id!=material['stock'].get('snapshot_id') or svc._version()!=material['data_version'] or not link or link['id']!=latest['link_id'] or not interpretation or link['interpretation_id']!=interpretation['id'] or not check or link['review_id']!=check['id']):
                    raise DomainError('STALE_REVISION','계약·제품 연결·재고가 변경됐습니다. 소요량부터 다시 확인하세요.')
                if request_type=='purchase_request':
                    choice=self.alternatives.latest(svc,contract_id,item_index)
                    if not self.choice_current(svc,choice,latest) or choice['id']!=payload['choice_id']:raise DomainError('STALE_REVISION','적용안 또는 대체 재고가 변경됐습니다. 다시 확인하세요.')
                from .library_reservations import current_reservation
                active=current_reservation(svc,(contract_id,item_index))
                if active and active['payload']['choice_id']==payload['choice_id']:
                    waiting={r['component_id']:r['waiting'] for r in active['payload']['lines']}
                    if any(waiting.get(r['component_id'])!=r['S'] for r in lines):raise DomainError('STALE_REVISION','입고 반영으로 부족량이 변경됐습니다. 구매 요청을 다시 확인하세요.')
                previous=svc.db.execute('SELECT * FROM pp_contract_rfq_drafts WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
                if previous and previous['payload']==payload:
                    return svc._envelope(ok=True,data=dict(id=previous['id'],markdown=previous['markdown'],status='draft_not_sent'))
                key=str(uuid4())
                svc.db.execute('INSERT INTO pp_contract_rfq_drafts VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(key,contract_id,item_index,material_review_id,canonical(payload),markdown,digest(dict(payload=payload,markdown=markdown)),session.context['actor'],svc.now()))
                return svc._envelope(ok=True,data=dict(id=key,markdown=markdown,status='draft_not_sent'))
            return svc._run(session.context['actor'],'rfq_create',dict(contract_id=contract_id,item_index=item_index,payload=payload),save,permission='draft',request_key=request_key)
