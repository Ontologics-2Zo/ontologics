'use strict';
// No polling: the user explicitly starts discovery and review.
document.getElementById('request-inbox').onclick=()=>action(showRequestInbox);
async function showRequestInbox(){
 const panel=$('request-panel');panel.hidden=false;panel.replaceChildren(el('p','공유 그래프의 요청을 확인하는 중…'));
 const data=await api('request-inbox',{});panel.replaceChildren(el('h2','추가 생산 자재 검토 요청'));
 panel.append(el('p',`최신 요청 ${data.requests.length}건 · 그래프 버전 ${data.data_version}. 요청에 연결된 기존 계약과 제품을 확인한 뒤 검토하세요.`));
 panel.append(el('p','조회만으로 계산하지 않습니다. RFQ 초안까지 검토하면 BOM·공정 자재 필요량과 현재 공용 재고 부족량을 계산합니다.'));
 const close=el('button','닫기');close.onclick=()=>panel.hidden=true;panel.append(close);
 const label=el('label','공정 자재 여유율 (%) '),allowance=el('input');allowance.type='number';allowance.min='0';allowance.max='100';allowance.step='.01';allowance.value='10';label.append(allowance);panel.append(label);
 for(const bad of data.invalid)panel.append(el('p',`${bad.evidence_id}: ${bad.message}`,'error'));
 if(!data.requests.length)panel.append(el('p','등록된 추가 생산 요청이 없습니다. 기존 계약은 계약 보관함에서 검토할 수 있습니다.'));
 for(const row of data.requests){
  const p=row.request,card=el('details'),summary=el('summary',`${p.request_id} · 개정 ${p.revision} · ${p.quantity}대 · ${{needs_info:'보완 필요',reviewed:'검토 이력 있음',seen:'확인함',new:'새 요청'}[row.status]}`);card.append(summary);
  card.append(el('p',`제품: ${p.configuration_id}`),el('p',`기존 계약: ${row.contract?.id||'연결 필요'} / 품목: ${p.contract_item_id||'미연결'} / 납품 차수: ${p.delivery_round_id||'미지정'}`),el('p',`기존 계획 근거: ${p.baseline_evidence_id}`),el('p',`필요일: ${p.need_date} · ${{production_complete:'생산 완료',dispatch:'출고',customer_arrival:'고객 도착'}[p.need_basis]}`),el('p',`요청 사유: ${p.reason}`),el('p',`조건: ${p.conditions_note}`),el('p',`자료: ${p.origin==='synthetic'?'가상 운영 기록':'실제 운영 기록'}`));
  if(row.contract_context?.contract){
   const c=row.contract_context,original=el('details');original.append(el('summary','최초 계약 · 원문과 연결 근거 확인'));
   original.append(el('p',`고객: ${c.contract.customer_name} / 생산 회사: ${c.contract.company}`),el('p',`계약: ${c.contract.id} / 품목: ${c.item.id}`),el('p',`기존 계약 수량: ${c.item.quantity}대 · 이번 추가 요청: ${p.quantity}대 (이번 계산에는 추가 요청량만 적용)`),el('p',`계약 제품 구성: ${c.item.configuration_id}`));
   for(const ev of c.evidence)original.append(el('p',`계약 근거: ${ev.id} · 원문 위치: ${ev.locator}`));
   for(const doc of c.documents){
    original.append(el('h4','보관된 최초 계약 원문'),el('p',`원문 ID: ${doc.source.id} · 자료 구분: ${doc.source.origin==='synthetic'?'가상 계약':'실제 계약'}`));
    if(doc.text!==null)original.append(el('p','원문 해시 일치 확인. 아래 내용은 작성 당시 원문이며 현재 그래프 연결 상태와 구분됩니다.'),el('pre',doc.text,'preview'));
    else original.append(el('p',doc.status==='hash_mismatch'?'원문 해시가 달라 표시할 수 없습니다.':'원문 내용은 현재 서버에서 표시할 수 없습니다. 등록된 근거 정보를 확인하세요.'));
    original.append(el('p',`SHA-256: ${doc.source.sha256}`));
   }
   if(!c.documents.length)original.append(el('p','연결된 계약의 원문 근거를 보완해야 합니다.'));
   card.append(original);
  }
  for(const issue of row.issues)card.append(el('p',issue,'error'));
  const result=el('div');
  const render=saved=>{result.replaceChildren();const d=saved.payload;result.append(el('h3','자재 검토 결과'),el('p',`검토 시각: ${date(saved.created_at)} · 그래프 v${d.data_version} · 공정 여유율 ${d.allowance_percent}%`),el('p','저장 당시 결과입니다. 재고나 요청이 바뀌면 다시 검토하세요.'));
   for(const [kind,scope] of Object.entries(d.stock_scopes||{}))if(scope)result.append(el('p',`${kind==='bom'?'BOM':'공정'} 재고: ${scope.company} / ${scope.location} · ${scope.as_of}`));
   for(const [title,rows] of [['BOM 부품',d.bom.components.map(r=>({name:r.name,required:r.D,available:r.A,shortage:r.S,unit:'개'}))],['공정 자재',d.process]]){
    const section=el('details');section.open=title==='공정 자재';section.append(el('summary',title));const table=el('table'),head=el('tr');for(const t of ['자재','필요량','공용 가용량','부족량'])head.append(el('th',t));table.append(head);
    for(const r of rows){const tr=el('tr');for(const v of [r.name,...['required','available','shortage'].map(k=>r[k]===null?'미확인':`${r[k]} ${r.unit}`)])tr.append(el('td',v));table.append(tr);}section.append(table);result.append(section);
   }
   result.append(el('p',`확인된 부족 ${d.rfq.lines.length}종 · 미확인으로 RFQ 제외 ${d.rfq.excluded.length}종. 재고 예약·발주·발송 없음.`));
   for(const x of d.rfq.excluded)result.append(el('p',`${x.name}: ${x.reason}`));
   result.append(el('pre',d.rfq.text,'preview'));
   if(d.rfq.lines.length){const download=el('button','RFQ 초안 다운로드');download.onclick=()=>{const url=URL.createObjectURL(new Blob([d.rfq.text],{type:'text/plain;charset=utf-8'}));const a=el('a');a.href=url;a.download=`RFQ-${p.request_id}-r${p.revision}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};result.append(download);}
  };
  if(row.last_review)render(row.last_review);
  const review=el('button',row.last_review?'현재 재고로 다시 검토':'자재 검토 → RFQ 초안','primary');review.dataset.locked=String(!!row.issues.length);review.disabled=!!row.issues.length;
  review.onclick=()=>action(async()=>{const saved=await api('request-review',{evidence_id:row.evidence_id,data_version:data.data_version,allowance_percent:allowance.value,request_key:crypto.randomUUID()});render(saved);review.textContent='현재 재고로 다시 검토';row.status='reviewed';summary.textContent=`${p.request_id} · 개정 ${p.revision} · ${p.quantity}대 · 검토 이력 있음`;notice('자재 검토 결과와 미발송 RFQ 초안을 저장했습니다.');});
  card.append(review,result);let marked=false;card.ontoggle=async()=>{if(!card.open||marked)return;try{await api('request-seen',{evidence_id:row.evidence_id});marked=true;if(row.status==='new'){row.status='seen';summary.textContent=`${p.request_id} · 개정 ${p.revision} · ${p.quantity}대 · 확인함`;}}catch(e){notice(e.message,true);}};panel.append(card);
 }
 panel.scrollIntoView({behavior:'smooth'});notice('요청 목록을 확인했습니다. 검토할 요청을 열어 주세요.');
}
