'use strict';
// Display only: all identity, approval, quantities and graph semantics remain on the server.
let dashboardSnapshot = null;
let businessSelection = null;
let businessRows = [];
const businessViews = {
  planning: {title: '생산·자재 계획', note: '승인된 생산 작업·자재 기준을 조회합니다. 계약 원문 확인과 제품 구성 연결 후 기존 A 자재 소요 계산을 실행합니다. 계산은 생산 승인이나 실제 입출고가 아닙니다.'},
  fulfillment: {title: '계약·납품', note: '승인된 계약·납품회차·배정·출하 기록입니다. 부분 생산 관측과 분할 출하 기록을 근거가 있는 초안으로 작성하고 별도 사람 승인 후 반영합니다.'},
  claims: {title: '클레임·대응', note: '승인된 고객 신고·사건·영향 범위를 확인합니다. 신고 접수와 보드 연결은 원문 근거가 있는 초안으로 저장합니다. 불량 확정·시험 실행·출고 중단은 수행하지 않습니다.'}
};
const recordNames = {
  Configuration:'제품 구성', ProductModel:'제품 모델', ProductRevision:'제품 개정', ComponentModel:'부품', BOMItem:'BOM 항목',
  ProductUnit:'개별 보드', ComponentLot:'부품 로트', Installation:'장착·제거 기록', HistoryCoverage:'이력 확인 범위',
  SourceDocument:'원본 자료', Evidence:'근거', Contract:'계약', ContractItem:'계약 품목', DeliveryRound:'납품 회차',
  FinishedGoodsStock:'완제품 재고', ProductionWork:'생산 작업', ProductionObservation:'생산 실적', SupplyAllocation:'공급 배정',
  Shipment:'출하', FulfillmentSnapshot:'계약·납품 기준 시점', Allocation:'공급 배정', DeliveryCondition:'납품 조건', TransportPlan:'운송 계획', ComponentStockSnapshot:'부품 재고 기준', ProductionRateAssumption:'생산 조건',
  ProductionLine:'생산라인', UnitProductionRecord:'보드 생산 기록', ClaimIntake:'클레임 접수', QualityCase:'품질 사건',
  QualityScope:'영향 범위', ContainmentRequest:'중단·보류 요청', TestRequest:'시험 의뢰', QualityTestResult:'시험팀 결과', ReproductionRequirement:'재생산 요건'
};
const fieldNames = {
  destination:'납품 목적지', due_basis:'납기 기준', prepared_at:'작성 시각', production_state:'생산 상태', produced_quantity:'생산 수량', available_quantity:'가용 수량', on_hand_quantity:'보유 수량', held_quantity:'보류 수량', reserved_quantity:'예약 수량', planned_start_at:'예정 시작', started_at:'실제 시작', planned_departure_at:'예정 출발', preparation_seconds:'준비 시간(초)', transport_seconds:'운송 시간(초)', dispatch_mode:'출발 방식', confirmations:'확인 기록', line_code:'라인 코드', id:'식별자', type:'기록 종류', origin:'자료 구분', evidence:'원본·기록 근거', title:'제목', name:'이름', company:'회사',
  configuration_id:'제품 구성', contract_id:'계약', contract_item_id:'계약 품목', delivery_round_id:'납품 회차', round_id:'납품 회차',
  production_work_id:'생산 작업', production_line_id:'생산라인', unit_id:'개별 보드', unit_ids:'대상 보드', lot_id:'부품 로트',
  snapshot_id:'기준 시점', allocation_id:'공급 배정', stock_id:'재고', round_ids:'납품 회차', production_state:'생산 상태', departed_at:'출발 시각', expected_arrival_at:'예상 도착', location:'보관 위치', quantity_state:'수량 확인 상태', availability_basis:'가용 수량 근거', case_id:'사건', intake_id:'클레임 접수', scope_id:'영향 범위', test_request_id:'시험 의뢰', test_result_ids:'시험 결과',
  reported_symptom:'고객 신고 증상', reported_at:'신고 시각', intake_state:'접수 상태', scope_state:'범위 상태',
  observed_at:'관측 시각', prepared_at:'작성 시각', as_of:'기준 시각', quantity:'수량', target_quantity:'목표 수량',
  completed_quantity:'완료 수량', good_quantity:'정상 수량', defective_quantity:'제공된 불량 수량', unit:'단위',
  due_at:'납기', promised_at:'약속 시각', shipped_at:'출하 시각', arrived_at:'도착 시각', produced_at:'생산 시각',
  customer_name:'고객', external_claim_id:'신고 번호', claimed_quantity:'신고 수량', reported_serials:'신고 일련번호',
  disposition:'시험팀 조치 판정', outcome:'시험 결과', assessment_state:'수량 확정 여부', required_good_quantity:'재생산 필요량',
  customer_replacement_quantity:'고객 교체 수요', stock_replacement_quantity:'보관 대체 수요', available_replacement_quantity:'사용할 대체 재고',
  unresolved_quantity:'미결 수량', replacement_unit_ids:'대체 보드 식별자', document_ref:'문서 참조', source_id:'원본 자료',
  serial:'일련번호', start:'시작 시각', end:'종료 시각', note:'기록 내용', request_kind:'요청 종류'
};
const recordStates = {needs_review:'확인 필요', confirmed:'확정', rejected:'반려', hold:'보류', candidate:'조사 후보',
  lower_bound:'최소 수량 · 미결 있음', unresolved:'미결', release:'출고 허용 판정', customer_replacement:'고객 교체',
  stock_replacement:'보관 대체', not_reproduced:'증상 미재현', fail:'가상 시험 불합격', pass:'가상 시험 합격',
  piece:'개', arrival:'도착 기준', departure:'출발 기준', production_stop:'생산 중단 요청', shipment_hold:'출고 보류 요청'};
function businessTitle(record) { return record.title || record.name || record.external_claim_id || record.contract_number || record.id; }
function businessStatus(record) { const value = record.assessment_state || record.intake_state || record.scope_state || record.disposition || record.status; return value ? (recordStates[value] || value) : '승인된 기록'; }
function originBadge(origin) { return node('span', origin === 'real' ? '실제 기술 자료' : origin === 'synthetic' ? '가상 운영 기록' : '자료 구분 미확인', 'pill ' + (origin === 'synthetic' ? 'synthetic' : 'neutral')); }
function setAIExpanded(expanded) {
  $('ai-panel').hidden = !expanded;
  document.querySelector('.content-grid').classList.toggle('ai-expanded', expanded);
  $('toggle-ai').setAttribute('aria-expanded', String(expanded));
  $('toggle-ai').textContent = expanded ? 'AI 업무 지원 닫기' : '✦ AI 업무 지원';
}
function draftTable(drafts) {
  return simpleTable(['등록·변경안 / 버전','상태','작성자','기준 → 반영','처리'], drafts.map(draft => {
    const id = draft.draft_id || draft.id, version = draft.version;
    return [id + ' · v' + version, node('span',draftLabels[draft.status] || draft.status,'pill neutral'), draft.author,
      'v' + draft.base_version + ' → ' + (draft.applied_data_version == null ? '미반영' : 'v' + draft.applied_data_version),
      actionButton('검토', async () => { selectView('review'); await loadDraft({id,version}); })];
  }));
}
function filterDashboardDrafts() {
  const output = clear('workspace-drafts');
  if (!dashboardSnapshot) { output.append(node('p','현황을 불러오세요.','hint')); return; }
  const filter = $('draft-status-filter').value, drafts = dashboardSnapshot.data.drafts.filter(d => !filter || d.status === filter);
  if (drafts.length) output.append(draftTable(drafts));
  else output.append(node('p', '표시 범위에 해당하는 등록·변경안이 없습니다.','empty-row'));
  const data = dashboardSnapshot.data;
  $('draft-coverage').textContent = '최신 버전 기준 전체 ' + data.draft_total + '건 · 최근 ' + data.drafts.length + '건에서 필터 적용 · 최대 ' + data.draft_limit + '건';
}
async function loadDashboard() {
  $('dashboard-state').textContent = '승인 데이터·등록안 조회 중…';
  $('workspace-metrics').setAttribute('aria-busy','true');
  try {
    const response = must(await api('/api/dashboard'));
    dashboardSnapshot = response;
    workspaceVersion = response.data_version;
    const data = response.data;
    $('workspace-version').textContent = '승인 데이터 v' + workspaceVersion;
    $('current-version').textContent = '조회 기준 v' + workspaceVersion;
    $('dashboard-state').textContent = '조회 완료 ' + new Date().toLocaleTimeString('ko-KR') + ' · 자동 갱신 없음';
    const counts = data.draft_counts, metrics = clear('workspace-metrics');
    for (const [label,value,detail,target] of [
      ['검토 대기',counts.awaiting_review || 0,'최신 초안 전체','awaiting_review'],
      ['보완 필요',counts.needs_changes || 0,'별도 검토 의견 확인','needs_changes'],
      ['승인 후 미반영',counts.approved || 0,'반영 결과 확인 필요','approved'],
      ['생산·자재 기록',data.groups.planning.total,'승인 기록 건수 · 생산량 아님','planning'],
      ['계약·납품 기록',data.groups.fulfillment.total,'승인 기록 건수 · 납품량 아님','fulfillment'],
      ['클레임·대응 기록',data.groups.claims.total,'승인 기록 건수 · 불량률 아님','claims']]) {
      const metric = node('button',null,'metric'); metric.type='button';
      metric.append(node('small',label),node('strong',value),node('p',detail));
      metric.addEventListener('click',()=>run(async()=>{ if (businessViews[target]) await openBusiness(target); else { $('draft-status-filter').value=target; filterDashboardDrafts(); $('workspace-drafts').scrollIntoView({block:'center'}); }}));
      metrics.append(metric);
    }
    filterDashboardDrafts();
    const panels = clear('business-panels');
    for (const key of ['planning','fulfillment','claims']) {
      const group = data.groups[key];
      const panel=node('section',null,'card domain-panel'), heading=node('div',null,'section-heading');
      heading.append(node('h2',businessViews[key].title),actionButton('전체 보기',()=>openBusiness(key)));
      panel.append(heading,node('p',group.total+'건 · 승인 데이터 v'+workspaceVersion,'hint'));
      const preview=group.records.slice(0,4);
      if (preview.length) panel.append(simpleTable(['종류 / 대상','상태'], preview.map(record=>[
        actionButton((recordNames[record.type] || record.type)+' · '+businessTitle(record),()=>openBusinessRecord(record.id,workspaceVersion,key)),businessStatus(record)])));
      else panel.append(node('p','이 버전에 등록된 기록이 없습니다.','empty-row'));
      panel.append(node('p','확인된 기록만 표시 · 근거와 상세 연결','hint'));
      panels.append(panel);
    }
    const foundation=clear('foundation-counts');
    for (const kind of ['Configuration','ProductUnit','ComponentLot','SourceDocument']) foundation.append(node('span',(recordNames[kind] || kind)+' '+(data.graph_counts[kind] || 0)+'건','pill neutral'));
    updateAIContext();
  } catch(error) {
    dashboardSnapshot=null;
    for(const id of ['workspace-metrics','workspace-drafts','business-panels','foundation-counts']) clear(id);
    $('draft-coverage').textContent='';
    $('workspace-version').textContent='조회 실패';
    $('dashboard-state').textContent='조회 실패 · 건수 미확인. 현황 새로고침으로 재시도하세요.';
    throw error;
  } finally { $('workspace-metrics').removeAttribute('aria-busy'); }
}
async function openBusiness(view, kind) {
  if (!dashboardSnapshot) await loadDashboard();
  selectView(view);
  const group=dashboardSnapshot.data.groups[view];
  $('business-title').textContent=businessViews[view].title;
  $('business-note').textContent=businessViews[view].note;
  const select=clear('business-kind');
  for(const name of group.kinds) { const option=node('option',recordNames[name] || name); option.value=name;select.append(option); }
  if (kind && group.kinds.includes(kind)) select.value=kind;
  businessSelection={view,version:dashboardSnapshot.data_version,node:null};
  $('business-query').value='';
  const actions = clear('business-actions');
  await ({planning:mountPlanning, fulfillment:mountFulfillment, claims:mountClaims}[view])(actions);
  await loadBusinessKind();
}
async function loadBusinessKind() {
  if(!businessSelection) return;
  const version=businessSelection.version, kind=$('business-kind').value;
  businessSelection.node=null; businessRows=[]; clear('business-detail'); clear('business-records');
  $('business-coverage').textContent='조회 중…'; updateAIContext();
  try {
    const response=await tool('lookup',{kind,version});
    if(response.data_version!==version) throw new Error('조회 버전이 일치하지 않습니다. 현황을 다시 확인하세요.');
    businessRows=response.data; renderBusinessRows();
  } catch(error) { $('business-coverage').textContent='조회 실패 · 기록과 수량 미확인'; throw error; }
}
function renderBusinessRows() {
  const output=clear('business-records'), term=$('business-query').value.trim().toLocaleLowerCase();
  const rows=businessRows.filter(row=>JSON.stringify(row).toLocaleLowerCase().includes(term));
  $('business-coverage').textContent='승인 데이터 v'+businessSelection.version+' · '+rows.length+' / '+businessRows.length+'건 · 최신 반영분은 현황 새로고침 후 확인';
  if(!rows.length) { output.append(node('p','조건에 맞는 등록 기록이 없습니다. 실제 재고·생산량 0을 뜻하지 않습니다.','empty-row'));return; }
  output.append(simpleTable(['대상 / 식별자','상태','자료','확인'],rows.map(row=>[
    businessTitle(row),businessStatus(row),originBadge(row.origin),actionButton('상세·근거',()=>openBusinessRecord(row.id,businessSelection.version,businessSelection.view))])));
}
async function openBusinessRecord(id,version,view) {
  if (view && currentView!==view) await openBusiness(view);
  const destination = view || businessSelection?.view || 'claims';
  businessSelection={view:destination,version,node:null};
  clear('business-detail').append(node('p',id+' · 데이터 v'+version+' 조회 중…','hint'));
  updateAIContext();
  let response;
  try {
    response=await tool('lookup',{node_id:id,version});
    if(response.data_version!==version || response.data.length!==1) throw new Error('선택한 버전의 기록을 찾을 수 없습니다.');
  } catch(error) {
    clear('business-detail').append(node('p',id+' · 데이터 v'+version+' 조회 실패. 목록에서 대상을 다시 선택하세요.','message error'));
    throw error;
  }
  const record=response.data[0];
  businessSelection={view:view || businessSelection?.view || 'claims',version,node:record};
  const output=clear('business-detail');
  const heading=node('div',null,'section-heading');heading.append(node('h2',recordNames[record.type] || record.type),originBadge(record.origin));output.append(heading);
  output.append(node('h3',businessTitle(record)),node('p','승인 데이터 v'+version+' · '+record.id,'hint'));
  const rows=[];
  for (const [key,value] of Object.entries(record)) {
    if (['id','type','origin','evidence'].includes(key)) continue;
    let display;
    if (['configuration_id','contract_id','contract_item_id','delivery_round_id','snapshot_id','allocation_id','stock_id','production_work_id','production_line_id','unit_id','lot_id','case_id','intake_id','scope_id','test_request_id','source_id','model_id','revision_id','component_id'].includes(key) && typeof value==='string') display=actionButton(value,()=>openBusinessRecord(value,version,businessSelection.view));
    else if (Array.isArray(value) && key.endsWith('_ids') && key!=='replacement_unit_ids') {
      display=node('div');for(const ref of value) display.append(actionButton(ref,()=>openBusinessRecord(ref,version,businessSelection.view)));
    } else display=typeof value==='object' ? JSON.stringify(value) : (recordStates[value] || String(value ?? '미확인'));
    rows.push([fieldNames[key] || key,display]);
  }
  output.append(simpleTable(['항목','등록된 값'],rows));
  if(record.evidence?.length) output.append(evidenceButton(record.evidence,version));
  output.append(actionButton('AI에 이 기록 설명 요청',async()=>{setAIExpanded(true);$('ai-question').value='선택한 기록의 의미와 근거, 확인하지 못한 사항을 설명해줘.';$('ai-question').focus();}));
  output.append(jsonDetails('등록 원문',record));
  updateAIContext();
}
function initializeDashboardControls() {
  $('toggle-ai').addEventListener('click',()=>setAIExpanded($('ai-panel').hidden));
  $('draft-status-filter').addEventListener('change',filterDashboardDrafts);
  $('business-kind').addEventListener('change',()=>run(loadBusinessKind));
  $('business-query').addEventListener('input',renderBusinessRows);
  $('business-refresh').addEventListener('click',()=>run(async()=>{const view=businessSelection.view,kind=$('business-kind').value;await loadDashboard();await openBusiness(view,kind);}));
  $('global-search').addEventListener('submit',event=>{event.preventDefault();const query=$('global-query').value.trim();if(!query)return;run(async()=>{selectView('evidence');$('evidence-search-query').value=query;$('evidence-version').value=workspaceVersion ?? '';await searchEvidence();});});
  setAIExpanded(false);
}
