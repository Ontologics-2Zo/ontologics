'use strict';

// B supplies interpretation and arithmetic; the common host owns identity,
// exact-version proposals, source preservation and separate human approval.
function fulfillmentField(parent, label, name, options = {}) {
  const wrap = node('label', label), input = node(options.rows ? 'textarea' : 'input');
  input.name = name;
  input.required = options.required !== false;
  if (options.rows) input.rows = options.rows;
  else input.type = options.type || 'text';
  if (input.type === 'number') { input.min = String(options.min ?? 0); input.step = '1'; }
  input.placeholder = options.placeholder || '';
  wrap.append(input); parent.append(wrap); return input;
}
function fulfillmentSelect(parent, label, name, rows) {
  const wrap=node('label',label), select=node('select'); select.name=name; select.required=true;
  if (!rows.length) { const empty=node('option','승인된 기록 없음');empty.value='';select.append(empty); }
  for(const [value,text] of rows) { const option=node('option',text); option.value=value; select.append(option); }
  wrap.append(select);parent.append(wrap);return select;
}
function fulfillmentResult(container, response) {
  container.replaceChildren();const data=response.data, state=data.forecast || data;
  container.append(node('h3','조회 결과 · 승인 데이터 v'+response.data_version));
  container.append(node('p','명시한 기준 시각의 가상 운영 기록입니다. 조건부 예상과 실제 도착·생산 완료 사실은 구분합니다.','hint'));
  const meanings={complete:'입력 범위 확인',needs_confirmation:'추가 확인 필요',estimated:'조건부 예상',on_time:'조건상 납기 이내',late:'조건상 납기 초과'};
  const names={status:'확인 상태',as_of:'기준 시각',quantity:'약속 수량',allocated_quantity:'배정 수량',
    remaining_quantity:'미도착 잔량',unallocated_quantity:'미배정 수량',on_time_quantity:'조건상 납기 내 수량',
    shortfall_quantity:'조건상 부족 수량',estimated_full_arrival_at:'조건부 전체 도착 예상',
    estimated_completion_at:'조건부 완료 예상',deadline_status:'납기 조건 판정',target_quantity:'목표 생산 수량',
    work_completed_quantity:'기록된 정상 완료 누계',latest_good_quantity:'최근 정상 누계',good_rate_per_hour:'관측 구간의 시간당 정상 수량'};
  if(state.assessment_scope==='requested_production_work') names.remaining_quantity='남은 정상 생산 수량';
  const rows=Object.entries(names).filter(([key])=>key in state).map(([key,label])=>[label,meanings[state[key]] || (state[key] ?? '미확인')]);
  if(data.delivery_round) rows.unshift(['고객',data.contract.customer_name],['제품 구성',data.configuration.id],
    ['약속 수량',data.delivery_round.quantity],['도착 기준 납기',data.delivery_round.due_at],['도착지',data.delivery_round.destination]);
  if(rows.length)container.append(simpleTable(['항목','값'],rows));
  if(state.states) {
    const labels={delivered:'도착 확인',in_transit:'운송 중',warehouse:'창고 배정',in_progress:'미완료 생산',ready:'생산 준비',waiting:'생산 대기',production_completed:'생산 완료 기록 · 재고 입고 미확정'};
    container.append(simpleTable(['수량 구분','개'],Object.entries(state.states).map(([key,value])=>[labels[key]||key,value])));
  }
  if(state.unknowns?.length)container.append(simpleTable(['추가 확인 대상','항목','사유'],state.unknowns.map(row=>[row.node_id,row.field,row.code])));
  if(data.customer_reply)container.append(jsonDetails('검토용 고객 답변 초안 · 발송하지 않음',data.customer_reply));
  if(response.evidence?.length)container.append(evidenceButton(response.evidence,response.data_version));
  container.append(jsonDetails('조회 조건·계산·경고 전체',response));
}

async function mountFulfillment(container) {
  const version=businessSelection.version;
  container.append(node('p','승인 데이터 v'+version+'의 부분 생산·분할 출하를 기록합니다. 실제 생산·배송을 실행하거나 생산 완료를 재고로 자동 전환하지 않습니다.','hint'));
  const kinds=['DeliveryRound','ProductionWork','FinishedGoodsStock','Allocation','Shipment','FulfillmentSnapshot'];
  const values=await Promise.all(kinds.map(async kind=>{
    const response=await tool('lookup',{kind,version});
    if(response.data_version!==version)throw new Error('B 업무 기준 버전이 다릅니다. 현황을 다시 조회하세요.');
    return [kind,response.data];
  }));
  const records=Object.fromEntries(values), choices=kind=>records[kind].map(row=>[row.id,row.id]);
  const queryCard=node('section',null,'card'), queryForm=node('form'), queryFields=node('div',null,'form-grid'), result=node('div');
  queryCard.append(node('h2','계약·납품 현황 계산'),queryForm,result);queryForm.append(queryFields);container.append(queryCard);
  const action=fulfillmentSelect(queryFields,'조회할 업무','query_action',[
    ['fulfillment_status','회차별 배정·생산·출하 현황'],['delivery_round','계약의 납품 약속'],
    ['delivery_forecast','조건부 납기 예상'],['production_completion','조건부 생산 완료 예상'],['response_plan','검토용 대응 초안']]);
  const round=fulfillmentSelect(queryFields,'납품 회차','query_round',choices('DeliveryRound'));
  const asOf=fulfillmentField(queryFields,'기준 시각 · 시간대 포함','query_as_of',{placeholder:'2026-10-27T10:00:00+09:00'});
  const work=fulfillmentSelect(queryFields,'생산 작업 · 생산 완료 예상에만 사용','query_work',choices('ProductionWork'));
  function queryScope() {
    asOf.required=action.value!=='delivery_round';asOf.closest('label').hidden=!asOf.required;
    work.required=action.value==='production_completion';work.closest('label').hidden=!work.required;
  }
  function roundTime(){asOf.value=records.FulfillmentSnapshot.find(row=>row.round_ids.includes(round.value))?.as_of || '';}
  action.addEventListener('change',queryScope);round.addEventListener('change',roundTime);queryScope();roundTime();
  const queryButton=node('button','선택 조건으로 계산','primary');queryButton.type='submit';queryForm.append(queryButton);
  queryForm.addEventListener('submit',event=>{event.preventDefault();run(async()=>{
    result.replaceChildren(node('p','동일 승인 버전으로 계산 중…'));
    const arguments_={delivery_round_id:round.value,version};
    if(action.value!=='delivery_round')arguments_.as_of=asOf.value.trim();
    if(action.value==='production_completion')arguments_.production_work_id=work.value;
    try { fulfillmentResult(result,must(await api('/api/fulfillment/query',{action:action.value,arguments:arguments_}))); }
    catch(error){result.replaceChildren(node('p','조회 실패 · 이전 계산 결과를 사용하지 마세요.','message error'));throw error;}
  },queryButton);});

  const editCard=node('details',null,'card'), form=node('form'), fields=node('div'), source=node('div'), feedback=node('div');
  editCard.append(node('summary','부분 생산·분할 출하 변경안 작성'),form,feedback);container.append(editCard);
  const operation=fulfillmentSelect(form,'등록할 업무','fulfillment_action',[
    ['production_progress','부분 생산 누계 기록'],['split_allocation','기존 미출하 배정을 분할'],['split_shipments','미배정 재고에서 출하 회차 생성']]);
  form.append(fields,source); businessSourceFields(source);
  const save=node('button','등록 초안 저장 · 별도 검토 필요','primary');save.type='submit';
  save.disabled=!context.identity.permissions.includes('draft');form.append(save);
  let collect;
  function buildForm() {
    fields.replaceChildren();const grid=node('div',null,'form-grid');fields.append(grid);
    if(operation.value==='production_progress') {
      const target=fulfillmentSelect(grid,'진행 중인 생산 작업','progress_work',choices('ProductionWork'));
      const detail=node('p',null,'hint');fields.append(detail);
      const observed=fulfillmentField(grid,'새 누계 관측 시각','progress_observed',{placeholder:'2026-10-27T11:00:00+09:00'});
      const produced=fulfillmentField(grid,'생산 누계 · 정상+불량','progress_produced',{type:'number'});
      const good=fulfillmentField(grid,'정상 완료 누계','progress_good',{type:'number'});
      const defective=fulfillmentField(grid,'제공된 불량 누계 · 원인 판정 아님','progress_defective',{type:'number'});
      const observationId='observation:web:'+crypto.randomUUID();
      const extendLabel=node('label',null,'check'),extend=node('input');extend.type='checkbox';extend.name='extend_snapshot';
      extendLabel.append(extend,node('span','선택 기준 시각도 갱신하고 완전성 조건을 새로 확인'));fields.append(extendLabel);
      const cutoff=node('div');cutoff.hidden=true;fields.append(cutoff);
      const snapshotAt=fulfillmentField(cutoff,'새 기준 시각 · 시간대 포함','progress_snapshot',{required:false,placeholder:'2026-10-27T11:00:00+09:00'});
      cutoff.append(node('p','아래 항목은 근거로 확인한 경우만 선택하세요. 선택하지 않으면 미확인으로 보존되어 전체 수량을 확정하지 않습니다.','hint'));
      const labels={allocations_complete:'회차별 배정 목록 완전',source_allocations_complete:'재고·작업의 전체 배정 목록 완전',allocations_disjoint:'배정 수량 간 중복 없음',stock_events_complete:'재고 입출고 기록 완전',stock_observations_before_departures:'재고 관측이 출발 이전',shipment_events_complete:'출하 기록 완전',production_state_complete:'생산 상태 기록 완전',material_disjoint:'재고와 생산 물량 중복 없음',no_reallocation:'미기록 재배정 없음'};
      const checks={};for(const [key,label] of Object.entries(labels)){const wrap=node('label',null,'check'),input=node('input');input.type='checkbox';input.name=key;checks[key]=input;wrap.append(input,node('span',label));cutoff.append(wrap);}
      extend.addEventListener('change',()=>{cutoff.hidden=!extend.checked;snapshotAt.required=extend.checked;});
      function describe(){const selected=records.ProductionWork.find(row=>row.id===target.value),snap=records.FulfillmentSnapshot.find(row=>row.id===selected?.snapshot_id);detail.textContent=selected?'기존 정상 누계 '+selected.completed_quantity+' / 목표 '+selected.target_quantity+' · 관측 '+selected.observed_at+' · 기준 '+snap?.as_of:'승인된 생산 작업이 없습니다.';}
      target.addEventListener('change',describe);describe();
      collect=()=>{const value={production_work_id:target.value,observation_id:observationId,observed_at:observed.value.trim(),produced_quantity:Number(produced.value),good_quantity:Number(good.value),defective_quantity:Number(defective.value)};if(extend.checked){value.snapshot_as_of=snapshotAt.value.trim();value.confirmations=Object.fromEntries(Object.entries(checks).map(([key,input])=>[key,input.checked]));}return value;};
      return;
    }
    const splitting=operation.value==='split_allocation';
    const eligible=records.Allocation.filter(row=>row.stock_id&&!records.Shipment.some(ship=>ship.allocation_id===row.id));
    const target=fulfillmentSelect(grid,splitting?'출하 기록이 없는 기존 배정':'완제품 재고',splitting?'split_allocation':'split_stock',splitting?eligible.map(row=>[row.id,row.id+' · '+row.quantity+'개']):choices('FinishedGoodsStock'));
    const targetRound=splitting?null:fulfillmentSelect(grid,'납품 회차','split_round',choices('DeliveryRound'));
    fields.append(node('p',splitting?'회차 수량 합계는 기존 배정량과 같아야 합니다. 첫 회차는 기존 배정 ID를 유지합니다.':'이미 다른 배정에 사용된 수량은 재사용할 수 없습니다. 서버가 전체 회차 합계를 검증합니다.','hint'));
    fields.append(node('p','출발·도착 시각은 확인된 경우만 입력하세요. 빈 값은 미확인이며 실제 출발·도착으로 처리하지 않습니다. 각 회차 도착은 회차 전체 수량 기준입니다.','hint'));
    const parts=node('div');fields.append(parts);const partForms=[];
    function addPart(){
      if(partForms.length>=20)return;
      const row=node('fieldset'),legend=node('legend','출하 회차 '+(partForms.length+1)),group=node('div',null,'form-grid');row.append(legend,group);parts.append(row);
      const index=partForms.length, aid=splitting&&index===0?target.value:'allocation:web:'+crypto.randomUUID(),sid='shipment:web:'+crypto.randomUUID();
      const qty=fulfillmentField(group,'회차 수량','part_quantity_'+index,{type:'number',min:1});
      const times={};for(const [key,label] of [['planned_departure_at','예정 출발'],['departed_at','확인된 실제 출발'],['expected_arrival_at','예상 도착'],['arrived_at','확인된 실제 도착']])times[key]=fulfillmentField(group,label+' · 선택',key+'_'+index,{required:false,placeholder:'2026-10-27T10:00:00+09:00'});
      const part={aid,sid,qty,times};partForms.push(part);
      row.append(actionButton('이 회차 제거',async()=>{if(splitting&&partForms[0]===part)throw new Error('첫 회차는 기존 배정 ID를 보존하므로 제거할 수 없습니다.');partForms.splice(partForms.indexOf(part),1);row.remove();}));
    }
    fields.append(actionButton('출하 회차 추가',async()=>addPart()));addPart();addPart();
    target.addEventListener('change',()=>{if(splitting&&partForms[0])partForms[0].aid=target.value;});
    collect=()=>({...(splitting?{allocation_id:target.value}:{stock_id:target.value,delivery_round_id:targetRound.value}),parts:partForms.map(part=>({allocation_id:part.aid,shipment_id:part.sid,quantity:Number(part.qty.value),...Object.fromEntries(Object.entries(part.times).filter(([,input])=>input.value.trim()).map(([key,input])=>[key,input.value.trim()]))}))});
  }
  operation.addEventListener('change',buildForm);buildForm();
  form.addEventListener('submit',event=>{event.preventDefault();run(async()=>{
    const body={domain:'fulfillment',action:operation.value,payload:collect(),base_version:version,...businessSourceValue(form)};
    body.request_key=requestKey('fulfillment-draft',body);
    showBusinessDraft(must(await api('/api/business/draft',body)),feedback);
  },save);});
}
