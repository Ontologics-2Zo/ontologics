'use strict';
async function mountClaims(container) {
  container.append(node('h2','고객 신고 접수·사건 연결'),node('p','신고 내용은 불량 확정이 아닙니다. 시연용 가상 원문을 접수하고 별도 검토자가 승인한 뒤 개별 보드를 연결합니다.','hint'));
  const intake=node('form'),grid=node('div',null,'form-grid');intake.append(grid);
  businessField(grid,'external_claim_id','가상 클레임 번호',{placeholder:'CLM-DEMO-001'});
  businessField(grid,'customer_name','가상 고객명',{placeholder:'DEMO 고객사'});
  businessField(grid,'reported_at','신고 시각 · 시간대 포함',{placeholder:'2026-11-04T10:00:00+09:00'});
  businessField(grid,'claimed_quantity','신고 수량',{type:'number'});
  businessField(grid,'reported_symptom','고객이 신고한 증상',{type:'textarea'});
  businessField(grid,'reported_serials','신고 일련번호 · 한 줄에 하나',{type:'textarea',placeholder:'등록된 보드의 실제 serial 값을 확인하세요.'});
  businessSourceFields(intake);const output=node('div');output.id='business-feedback';
  businessSubmit(intake,'신고 접수 초안 저장',async()=>{
    const payload={};for(const key of ['external_claim_id','customer_name','reported_at','reported_symptom'])payload[key]=intake.elements[key].value.trim();
    payload.claimed_quantity=Number(intake.elements.claimed_quantity.value);
    payload.reported_serials=intake.elements.reported_serials.value.split(/\n/).map(v=>v.trim()).filter(Boolean);
    await submitBusiness(intake,'claims','intake',payload,output);
  });
  const details=node('details');details.append(node('summary','1. 새 신고 접수'));details.open=true;details.append(intake);container.append(details);
  const caseSection=node('details');caseSection.append(node('summary','2. 승인된 신고에 보드 연결'));
  const form=node('form'),status=node('p','승인된 신고·보드를 불러오는 중…','hint');caseSection.append(status,form);container.append(caseSection,output);
  const [intakes,units]=await Promise.all([tool('lookup',{kind:'ClaimIntake',version:businessSelection.version}),tool('lookup',{kind:'ProductUnit',version:businessSelection.version})]);
  const rows=intakes.data.filter(v=>v.intake_state!=='rejected');
  if(!rows.length){status.textContent='먼저 신고 접수 초안을 별도 사용자로 승인한 뒤 최신 승인 버전을 조회하세요.';return;}
  status.textContent='사람이 신고 일련번호와 보드를 대조합니다. 이 연결도 별도 승인 후 반영됩니다.';
  const select=businessField(form,'intake_id','승인된 신고',{options:rows.map(v=>({value:v.id,label:v.external_claim_id+' · '+v.customer_name})),value:rows[0].id});
  const boards=node('fieldset');boards.append(node('legend','신고 일련번호에 해당하는 보드'));
  const render=()=>{boards.replaceChildren(node('legend','신고 일련번호에 해당하는 보드'));const item=rows.find(v=>v.id===select.value);
    for(const unit of units.data.filter(v=>item.reported_serials.includes(v.serial)))businessField(boards,'unit_ids',unit.serial+' · '+unit.id,{type:'checkbox',required:false}).value=unit.id;
    if(!boards.querySelector('input'))boards.append(node('p','해당 일련번호의 보드가 없습니다. 보드·원본 기록을 확인하세요.'));
  };select.addEventListener('change',render);render();form.append(boards);
  businessField(form,'serials_confirmed','신고 일련번호와 선택한 보드가 일치함을 직접 확인했습니다.',{type:'checkbox'});
  businessSourceFields(form);businessSubmit(form,'접수 확인·사건 연결 초안 저장',async()=>{
    const payload={intake_id:select.value,unit_ids:Array.from(boards.querySelectorAll('input:checked')).map(v=>v.value),serials_confirmed:form.elements.serials_confirmed.checked};
    await submitBusiness(form,'claims','confirm_case',payload,output);
  });
}
