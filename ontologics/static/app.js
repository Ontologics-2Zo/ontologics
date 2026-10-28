'use strict';

// Business tools never supply identity or permissions. The explicit development
// selector only chooses a preset owned and resolved by the server.
const $ = (id) => document.getElementById(id);
const clone = (value) => JSON.parse(JSON.stringify(value));
const labels = {included: '장착 이력 확인', excluded: '조건상 제외', needs_confirmation: '추가 이력 확인 필요'};
const draftLabels = {awaiting_review: '등록·변경안 승인 대기', needs_changes: '이력·근거 보완 필요', approved: '승인 완료 · 반영 결과 확인 필요', applied: '승인된 버전 반영 완료', superseded: '이전 초안 버전', rejected: '반려'};
const reasons = {
  installation_condition_confirmed: '요청한 조건에 맞는 장착 이력이 확인되었습니다.',
  removal_or_later_installation_state_unknown: '제거 시점 또는 이후 장착 상태가 미확인입니다.',
  conflicting_installation_evidence: '서로 충돌하는 장착 근거가 있습니다.',
  no_matching_installation_in_complete_history: '전체 이력 확인 범위에서 조건에 맞는 장착이 없습니다.',
  history_scope_incomplete: '조사 조건 전체의 이력 완전성이 확인되지 않았습니다.'
};
const errorGuides = {
  SELF_APPROVAL_FORBIDDEN: '작성자와 다른 검토자가 승인해야 합니다. 상단에서 Bob 등 다른 개발 사용자를 선택한 뒤 업무 현황에서 같은 초안을 여세요.',
  FORBIDDEN: '현재 서버에 연결된 사용자에게 이 작업 권한이 없습니다. 이 사용자의 권한은 서버에서 정합니다.',
  SUPERSEDED_DRAFT: '새 초안 버전이 있습니다. 검토할 정확한 버전을 확인해 다시 불러오세요.',
  STALE_RECEIPT: '원본 접수 후 확정 데이터가 변경되었습니다. 같은 원본 파일을 다시 접수하고 차이를 확인한 뒤 새 초안 버전으로 저장하세요.',
  STALE_BASE: '기준 데이터가 변경되었습니다. 최신 데이터를 확인하고 새 초안 버전으로 저장한 뒤 다시 검토하세요.',
  APPROVAL_REQUIRED: '이 정확한 초안 버전의 별도 검토자 승인이 필요합니다.',
  INVALID_STATE: '이 초안은 현재 새 검토 결정을 받을 수 없습니다. 저장된 상태와 이전 처리 결과를 확인하세요.',
  DRAFT_CLOSED: '이미 반영되었거나 반려된 초안입니다. 다른 변경은 새 초안으로 작성하세요.',
  NO_CHANGES: '같은 내용이 이미 반영되어 있습니다. 승인된 버전을 조회해 재사용하세요.',
  IDEMPOTENCY_CONFLICT: '이 요청 키는 다른 내용에 사용되었습니다. 저장된 요청을 확인하세요.',
  STORAGE_ERROR: '저장소 작업을 완료하지 못했습니다. 상태를 확인한 뒤 같은 요청으로 재시도하세요.',
  CSRF_REJECTED: '개발 사용자 세션 또는 요청 보호 정보가 변경되었습니다. 화면을 새로고침해 서버의 현재 신원을 확인하세요.',
  DEVELOPMENT_SESSION_CHANGED: '다른 탭에서 개발 사용자가 변경되었거나 세션이 갱신되었습니다. 화면을 새로고침한 뒤 서버가 확인한 사용자로 작업하세요.',
  DEVELOPMENT_SESSION_EXPIRED: '개발 사용자 세션을 더 이상 사용할 수 없습니다. 화면을 새로고침해 서버가 제공한 개발 신원과 권한을 다시 확인하세요.',
  AI_NOT_CONFIGURED: '이 서버에 실제 모델이 설정되어 있지 않습니다. 공통 도구의 조회 결과는 계속 사용할 수 있습니다.',
  COST_POLICY_REQUIRED: '서버의 AI 요청 비용 한도가 설정되지 않아 외부 호출을 중단했습니다. 담당자가 한도와 모델 단가를 설정해야 합니다. 직접 조회·등록·사람 승인은 계속 사용할 수 있습니다.',
  COST_BUDGET_EXCEEDED: '이 요청의 남은 비용 한도로 다음 AI 호출을 진행할 수 없습니다. 확인된 도구 결과와 저장된 초안을 확인하세요.',
  SHARED_BUDGET_EXCEEDED: '이 요청의 공통 호출 한도에 도달했습니다. 완료된 결과와 남은 작업을 확인하세요.',
  DEADLINE_EXCEEDED: '이 요청의 실행 시간이 끝났습니다. 저장된 초안은 별도로 확인하고 같은 요청 키로 재시도하세요.',
  EMBEDDINGS_NOT_CONFIGURED: '의미 검색용 임베딩 API가 서버에 설정되지 않았습니다. 문자열 검색을 선택하거나 서버 설정을 확인하세요.',
  EVIDENCE_INDEX_NOT_READY: '선택한 승인 버전·자료 범위의 의미 검색 색인이 준비되지 않았습니다. 문자열 검색을 사용하거나 해당 버전의 색인 준비 후 다시 요청하세요.',
  INDEX_INTEGRITY: '색인 내용과 승인 원본의 일치성을 확인하지 못했습니다. 이 검색 결과를 사용하지 말고 서버의 색인 상태를 확인하세요.',
  OPENAI_KEY_MISSING: '이 서버에 실제 모델 키가 설정되지 않았습니다. 서버 환경 설정을 확인하세요.',
  AI_CONDITION_MISMATCH: '모델이 화면과 다른 조건을 사용했습니다. 정상 설명으로 처리하지 않았습니다.',
  AI_REQUIRED_TOOLS_MISSING: '모델이 필수 A/B 조회를 완료하지 않았습니다. 정상 설명으로 처리하지 않았습니다.'
};
let context = null;
let state = {baseline: null, comparison: null, draftRef: null, lastReview: null, draftCycle: null, productDraft: null, activeProductTarget: null, productCycle: null, sourceReceipt: null, knowledgeContext: null, requestKeys: {}};
let storageKey = '';
let template = null;
let loadedDraft = null;
let loadedValidation = null;
let loadedDraftReady = false;
let isBusy = false;
let currentView = 'home';
let workspaceVersion = null;
let aiScopeFingerprint = null;
let developmentSwitchPending = false;
let developmentSessionNeedsReload = false;

function node(tag, text, className) {
  const value = document.createElement(tag);
  if (text !== undefined && text !== null) value.textContent = text;
  if (className) value.className = className;
  return value;
}
function clear(id) { $(id).replaceChildren(); return $(id); }
function invalidateComparison() {
  state.comparison = null;
  clear('comparison-results');
  clear('ai-output');
}
function comparisonMatchesDraft() {
  return state.comparison && state.draftRef &&
    state.comparison.draft.id === state.draftRef.id && state.comparison.draft.version === state.draftRef.version;
}
function jsonDetails(title, value) {
  const detail = node('details');
  detail.append(node('summary', title), node('pre', JSON.stringify(value, null, 2)));
  return detail;
}
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function persist() {
  if (!storageKey) return;
  try { sessionStorage.setItem(storageKey, JSON.stringify(state)); }
  catch { /* In-memory keys remain stable for this page if storage is unavailable. */ }
}
function requestKey(action, body) {
  const signature = action + ':' + canonical(body);
  if (!state.requestKeys[signature]) {
    state.requestKeys[signature] = 'web-' + crypto.randomUUID();
    persist();
  }
  return state.requestKeys[signature];
}
function message(text, type = '') {
  $('global-message').className = 'message ' + type;
  $('global-message').textContent = text;
  $('global-message').hidden = false;
  if (developmentSessionNeedsReload) {
    const reload = node('button', '화면 새로고침');
    reload.type = 'button'; reload.id = 'reload-development-session';
    reload.addEventListener('click', () => location.reload());
    $('global-message').append(document.createTextNode(' '), reload);
  }
}
function responseError(result) {
  const issue = result?.errors?.[0] || result?.error || {code: 'REQUEST_FAILED', message: '요청을 처리하지 못했습니다.'};
  const error = new Error((errorGuides[issue.code] || issue.message || '요청을 처리하지 못했습니다.') + ' [' + issue.code + ']');
  error.response = result;
  return error;
}
function must(result) { if (!result?.ok) throw responseError(result); return result; }
async function api(path, body) {
  const options = {method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}};
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-CSRF-Token'] = context?.csrf_token || '';
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('서버 응답을 받지 못했습니다. 변경 요청은 완료 여부가 불확실할 수 있습니다. 같은 내용으로 재시도하면 기존 요청 키를 재사용합니다.'); }
  const responseSubject = response.headers.get('X-Ontologics-Development-Subject');
  if (context && !['/api/context', '/api/development-user'].includes(path) && responseSubject && responseSubject !== context.identity.subject) {
    developmentSessionNeedsReload = true;
    throw responseError({ok: false, data: null, errors: [{code: 'DEVELOPMENT_SESSION_CHANGED', message: errorGuides.DEVELOPMENT_SESSION_CHANGED}]});
  }
  let result;
  try { result = await response.json(); }
  catch { throw new Error('서버가 공통 JSON 응답을 반환하지 않았습니다. 처리 결과를 확인한 뒤 같은 요청으로 재시도하세요.'); }
  if (!result || typeof result.ok !== 'boolean') throw new Error('알 수 없는 응답 형식입니다. 완료로 처리하지 않았습니다.');
  if (!result.ok && result.errors?.some((error) => ['DEVELOPMENT_SESSION_CHANGED', 'DEVELOPMENT_SESSION_EXPIRED', 'CSRF_REJECTED'].includes(error.code))) developmentSessionNeedsReload = true;
  return result;
}
const tool = async (name, args) => must(await api('/api/tools/' + encodeURIComponent(name), args));
async function run(action, button) {
  if (isBusy || developmentSwitchPending) return;
  if (developmentSessionNeedsReload) { message(errorGuides.DEVELOPMENT_SESSION_CHANGED, 'error'); return; }
  isBusy = true;
  $('dev-user-select').disabled = true;
  const original = button?.textContent;
  const buttons = [...document.querySelectorAll('button:not(#close-evidence)')].map((item) => [item, item.disabled]);
  // Keep the displayed target and conditions fixed while the server processes
  // the request. In particular, never hide a saved draft through a stale read guard.
  const fields = [...document.querySelectorAll('input, textarea, select')].map((item) => [item, item.disabled]);
  buttons.forEach(([item]) => { item.disabled = true; });
  fields.forEach(([item]) => { item.disabled = true; });
  if (button) button.textContent = '처리 중…';
  $('global-message').hidden = true;
  try { await action(); }
  catch (error) { message(error.message || '작업에 실패했습니다. 완료 여부를 확인하세요.', 'error'); }
  finally {
    buttons.forEach(([item, disabled]) => { item.disabled = disabled; });
    fields.forEach(([item, disabled]) => { item.disabled = disabled; });
    if (button) button.textContent = original;
    isBusy = false;
    updateControls();
  }
}
function pill(status) { return node('span', labels[status] || status, 'pill ' + (labels[status] ? status : 'neutral')); }
function updateControls() {
  if (isBusy) return;
  if (developmentSwitchPending || developmentSessionNeedsReload) {
    document.querySelectorAll('button:not(#reload-development-session):not(#close-evidence)').forEach((button) => { button.disabled = true; });
    $('dev-user-select').disabled = true;
    return;
  }
  const ready = Boolean(context);
  $('dev-user-select').disabled = !ready || !currentDevelopmentPreset() || !context.development_users?.length;
  $('query-submit').disabled = !ready;
  $('load-supplement').disabled = !ready;
  $('load-draft').disabled = !ready;
  $('save-supplement').disabled = !template;
  $('new-supplement').disabled = !template;
  $('ask-ai').disabled = !ready || !assistantScope() || !context.model?.configured;
  const draftTarget = assistantDraftTarget();
  $('ai-draft-controls').hidden = !draftTarget;
  $('prepare-ai-draft').disabled = !ready || !draftTarget || !context.model?.configured || !context.identity.permissions?.includes('draft');
  $('prepare-ai-draft').textContent = draftTarget?.kind === 'history' ? 'AI 보완 초안 저장 요청' : 'AI 등록 초안 저장 요청';
  $('explain-results').disabled = !ready || !state.baseline || !context.model?.configured;
  $('compare-results').disabled = !state.baseline || !state.draftRef;
  ['refresh-workspace', 'source-intake', 'configuration-load', 'history-load', 'history-intake', 'evidence-search'].forEach((id) => { $(id).disabled = !ready; });
  $('history-save-draft').disabled = !state.historyReceipt;
  $('history-request-review').disabled = !state.historyDraft;
  $('history-source-revise').disabled = !state.historyDraft;
  $('history-revise-target').textContent = state.historyDraft ? state.historyDraft.id + ' · 버전 ' + state.historyDraft.version : '작성된 보완 초안 없음';
  $('source-compare').disabled = !state.sourceReceipt;
  $('source-save-draft').disabled = !state.sourceReceipt;
  $('source-new-draft').disabled = !state.sourceReceipt;
  $('source-request-review').disabled = !state.productDraft;
  $('source-revise').disabled = !state.productDraft;
  $('source-revise-target').textContent = state.productDraft ? state.productDraft.id + ' · 버전 ' + state.productDraft.version : '작성된 등록 초안 없음';
  if (!state.productDraft) $('source-revise').checked = false;
  const sameTarget = loadedDraftReady && loadedDraft && $('draft-id').value.trim() === loadedDraft.id && Number($('draft-version').value) === loadedDraft.version;
  const reviewable = sameTarget && ['awaiting_review', 'needs_changes'].includes(loadedDraft.status);
  $('approve-draft').disabled = !reviewable || !loadedValidation?.ok || !loadedValidation.data.validation.valid;
  $('request-changes').disabled = !reviewable;
  $('reject-draft').disabled = !reviewable;
  const retryMatches = sameTarget && state.lastReview?.draft_id === loadedDraft.id && state.lastReview?.version === loadedDraft.version;
  $('retry-review').hidden = !retryMatches;
  $('retry-review').disabled = !retryMatches;
  $('revise-draft').disabled = !state.draftRef;
  $('revision-target').textContent = state.draftRef ? state.draftRef.id + ' · 버전 ' + state.draftRef.version : '선택된 초안 없음';
  if (!state.draftRef) $('revise-draft').checked = false;
}
function selectView(view, setHash = true) {
  const titles = {home: '업무 현황', product: '제품 구성·BOM 등록', history: '개별 보드·부품 로트 이력', investigation: '로트 장착 이력 조회', review: '등록·변경안 검토 및 승인', evidence: '기술 문서·근거 조회'};
  if (!Object.hasOwn(titles, view)) view = 'home';
  if (currentView !== view) clear('ai-output');
  currentView = view;
  document.title = 'Ontologics · ' + (titles[view] || titles.home);
  document.querySelectorAll('.view').forEach((item) => { item.hidden = true; });
  $('view-' + view).hidden = false;
  document.querySelectorAll('#navigation button').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === view);
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (setHash) history.replaceState(null, '', '#' + view);
  updateAIContext();
}
function queriesFromForm() {
  const unit_ids = $('unit-ids').value.split(',').map((value) => value.trim()).filter(Boolean);
  if (!unit_ids.length) throw new Error('대상 보드 ID를 한 개 이상 입력하세요.');
  const lot_id = $('lot-id').value.trim();
  const at = $('query-at').value.trim(), start = $('query-start').value.trim(), end = $('query-end').value.trim();
  for (const value of [at, start, end]) if (!/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) throw new Error('A/B 시각에 날짜·시각과 시간대(+09:00 등)를 모두 입력하세요.');
  return {A: {mode: 'A', lot_id, unit_ids, at}, B: {mode: 'B', lot_id, unit_ids: [...unit_ids], start, end, period_meaning: 'installation_overlap'}};
}
function fillQueries(queries) {
  $('unit-ids').value = queries.A.unit_ids.join(', ');
  $('lot-id').value = queries.A.lot_id;
  $('query-at').value = queries.A.at;
  $('query-start').value = queries.B.start;
  $('query-end').value = queries.B.end;
}
function conditionLines(queries, version) {
  return ['대상: ' + queries.A.unit_ids.join(', '), '로트: ' + queries.A.lot_id, 'A: ' + queries.A.at, 'B: [' + queries.B.start + ', ' + queries.B.end + ')', '데이터 버전: ' + version];
}
function updateContext() {
  const baseline = state.baseline;
  if (!baseline) { updateAIContext(); return; }
  $('result-version').textContent = '조회 데이터 v' + baseline.version;
  const summary = clear('condition-summary');
  conditionLines(baseline.queries, baseline.version).forEach((line) => summary.append(node('p', line)));
  summary.append(node('p', '화면 입력을 수정해도 이 조회·AI·전후 비교의 조건은 바뀌지 않습니다. 새 조건은 다시 조회해야 합니다.'));
  updateAIContext();
  $('comparison-context').textContent = '고정된 최초 조회: v' + baseline.version + ' · 선택 초안: ' + (state.draftRef ? state.draftRef.id + ' / 버전 ' + state.draftRef.version : '없음') + ' · 비교 중에는 시간 조건을 바꾸지 않습니다.';
}
function simpleTable(headers, rows) {
  const wrapper = node('div', null, 'table-wrap'), table = node('table'), head = node('thead'), tr = node('tr');
  headers.forEach((header) => tr.append(node('th', header))); head.append(tr); table.append(head);
  const body = node('tbody');
  rows.forEach((cells) => { const row = node('tr'); cells.forEach((content) => { const cell = node('td'); cell.append(content instanceof Node ? content : document.createTextNode(String(content ?? '미확인'))); row.append(cell); }); body.append(row); });
  table.append(body); wrapper.append(table); return wrapper;
}
function actionButton(label, action) {
  const button = node('button', label, 'evidence-button'); button.type = 'button';
  button.addEventListener('click', () => run(action, button)); return button;
}
function optionalVersion(id) {
  const text = $(id).value.trim();
  if (!text) return undefined;
  const version = Number(text);
  if (!Number.isInteger(version) || version < 0) throw new Error('데이터 버전은 0 이상의 정수로 입력하세요.');
  return version;
}
function displaySource(source, version, download = true) {
  const box = node('div', null, 'source-box');
  box.append(node('strong', source.origin === 'synthetic' ? '시연용 가상 운영 기록' : '실제 공개 기술 자료'));
  for (const [label, value] of [['원본 ID', source.id], ['원본 파일', source.path], ['원본 버전', source.version], ['SHA-256', source.sha256], ['라이선스', source.license]]) box.append(node('p', label + ': ' + (value ?? '미확인')));
  if (source.metadata_warning) box.append(node('p', source.metadata_warning, 'warning-line'));
  if (source.url && /^https?:\/\//.test(source.url)) { const link = node('a', '공개 원본 위치 열기'); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; box.append(link, node('br')); }
  if (download && source.id && Number.isInteger(version)) { const link = node('a', '보존 원본 내려받기 · 서버에서 해시 확인'); link.href = '/api/source?' + new URLSearchParams({source_id: source.id, version: String(version)}); link.target = '_blank'; link.rel = 'noopener'; box.append(link); }
  return box;
}
async function enterView(view) {
  selectView(view);
  if (view === 'home') await refreshWorkspace();
  if (view === 'product' && state.sourceReceipt) renderReceipt();
  if (view === 'history' && state.historyReceipt) renderHistoryReceipt();
  updateControls();
}
async function refreshWorkspace() {
  const response = must(await api('/api/workspace'));
  const data = response.data, drafts = data.drafts || [];
  workspaceVersion = response.data_version;
  $('workspace-version').textContent = '데이터 v' + workspaceVersion;
  $('current-version').textContent = '확인한 현재 데이터 v' + workspaceVersion;
  const metrics = clear('workspace-metrics');
  for (const [label, value, detail] of [['승인된 데이터 버전', 'v' + workspaceVersion, '조회에 사용하는 스냅샷'], ['불러온 변경안', drafts.length, '서버가 반환한 최근 초안 목록'], ['목록 중 검토 대기', drafts.filter((item) => item.status === 'awaiting_review').length, '현재 유효성은 검토 시 재확인']]) {
    const card = node('div', null, 'metric'); card.append(node('small', label), node('strong', value), node('p', detail)); metrics.append(card);
  }
  const output = clear('workspace-drafts');
  if (!drafts.length) output.append(node('p', '저장된 등록·변경안이 없습니다. 제품 구성 등록 또는 이력 보완을 시작하세요.', 'hint'));
  else output.append(simpleTable(['초안 · 버전', '상태', '작성자 / 검토자', '기준 → 반영', ''], drafts.map((draft) => {
    const id = draft.draft_id || draft.id, version = draft.version;
    const identity = node('div'); identity.append(node('p', draft.author), node('small', draft.reviewer || '검토자 없음'));
    return [id + ' · v' + version, node('span', draftLabels[draft.status] || draft.status, 'pill neutral'), identity, 'v' + draft.base_version + ' → ' + ((draft.applied_data_version ?? draft.applied_version) === null || (draft.applied_data_version ?? draft.applied_version) === undefined ? '미반영' : 'v' + (draft.applied_data_version ?? draft.applied_version)), actionButton('이 버전 검토', async () => { selectView('review'); await loadDraft({id, version}); })];
  })));
  output.append(node('p', '현재 페이지의 조사 조건은 브라우저 탭에 보관됩니다. 서버의 전체 조사 이력 재개·업무함 기능을 제공하는 것은 아닙니다.', 'hint'));
  output.append(jsonDetails('현황 조회 실행 근거', {data_version: response.data_version, run_id: response.run_id, returned_drafts: drafts.length}));
  updateAIContext();
}
async function intakeSource() {
  const file = $('source-file').files[0];
  if (!file) throw new Error('접수할 OLIMEX WROOM ODS 원본 파일을 선택하세요.');
  if (!file.name.toLowerCase().endsWith('.ods') || file.size > 700000) throw new Error('700 KB 이하의 확인된 ODS 원본만 접수할 수 있습니다.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 16384) binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
  const response = must(await api('/api/sources/intake', {filename: file.name, content_base64: btoa(binary)}));
  const priorConfiguration = state.sourceReceipt?.configuration_id;
  state.sourceReceipt = {...response.data, data_version: response.data_version};
  state.activeProductTarget = 'receipt';
  state.productSaveRequest = null;
  state.sourceComparison = null;
  state.productCycle = crypto.randomUUID();
  if (priorConfiguration !== state.sourceReceipt.configuration_id) state.productDraft = null;
  $('source-reviewed').checked = false; $('source-revise').checked = false;
  clear('source-comparison'); clear('source-draft-result'); clear('ai-output');
  $('configuration-id').value = state.sourceReceipt.configuration_id;
  $('configuration-version').value = '';
  persist(); renderReceipt(); updateAIContext();
  message('원본 바이트·해시를 확인하고 BOM을 구조화했습니다. 아직 등록·승인·반영하지 않았습니다.', 'success');
}
function renderReceipt() {
  const receipt = state.sourceReceipt;
  if (!receipt) return;
  ['source-extraction-card', 'source-compare-card', 'source-draft-card'].forEach((id) => { $(id).hidden = false; });
  const revision = typeof receipt.revision === 'object' ? receipt.revision.revision : receipt.revision;
  $('source-revision').textContent = 'ESP32-POE · 개정 ' + revision + ' · WROOM';
  const intake = clear('source-intake-result');
  intake.append(node('p', receipt.notice || '원본 접수와 추출을 완료했습니다. 확정 데이터는 변경하지 않았습니다.', 'message success'));
  if (receipt.source) intake.append(displaySource(receipt.source, receipt.base_version ?? receipt.data_version));
  const meta = clear('source-metadata');
  metadata(meta, '접수 ID', receipt.receipt_id, true); metadata(meta, '연결할 구성', receipt.configuration_id, true); metadata(meta, '구조화한 BOM 행', receipt.rows.length); metadata(meta, '참조 위치 수', receipt.rows.reduce((sum, row) => sum + row.positions.length, 0)); metadata(meta, '실장 여부 미확인 행', receipt.unknown_count); metadata(meta, '접수 당시 등록 여부', receipt.existing_configuration ? '기존 승인 구성 있음 · 차이/중복 확인 필요' : '미등록 구성 · 초안 검토 필요'); metadata(meta, '접수 기준 데이터 버전', receipt.base_version ?? receipt.data_version);
  $('source-limitations').textContent = 'BOM의 실장 여부와 개별 보드의 부품 로트 장착 이력은 서로 다릅니다. NA·NS·Opened는 실장 미확인으로 유지합니다. 원본 범위의 부품 ID를 유지하며 개정 간 동일성·호환성을 추론하지 않습니다. ODS 구조·수량·위치는 확인했으나 PDF 전체 행의 시각 대조를 완료한 것은 아닙니다.';
  $('source-receipt-details').textContent = JSON.stringify({source: receipt.source, verification: receipt.verification, receipt_id: receipt.receipt_id, base_version: receipt.base_version, operations_count: receipt.operations_count}, null, 2);
  renderSourceRows();
}
function renderSourceRows() {
  if (!state.sourceReceipt) return;
  const receipt = state.sourceReceipt, rows = receipt.rows.filter((row) => !$('unknown-only').checked || row.population === 'unknown');
  $('source-row-count').textContent = rows.length + '행 표시 / 원본 ' + receipt.rows.length + '행';
  clear('source-rows').append(simpleTable(['원본 행·위치', '원문 품목', '패키지', '수량·참조 위치', 'BOM 실장 여부'], rows.map((row) => {
    const location = node('div'); location.append(node('strong', '행 ' + row.row), node('p', row.locator, 'raw-note'));
    return [location, row.raw_value, row.package, row.quantity + ' · ' + row.positions.join(', '), node('span', row.population === 'unknown' ? '미확인' : '원본 기준 실장', 'pill ' + (row.population === 'unknown' ? 'needs_confirmation' : 'neutral'))];
  })));
}
async function compareSource() {
  if (!state.sourceReceipt) throw new Error('원본 자료를 먼저 접수하세요.');
  const response = must(await api('/api/sources/compare', {receipt_id: state.sourceReceipt.receipt_id, left_id: $('source-left-id').value.trim()}));
  state.sourceComparison = {receipt_id: state.sourceReceipt.receipt_id, left_id: $('source-left-id').value.trim(), data_version: response.data_version}; persist();
  const output = clear('source-comparison'), data = response.data;
  output.append(node('p', '접수 후보와 기존 구성의 변경 ' + data.changes.length + '건 · 미반영 비교', 'message'));
  const value = (entry) => entry ? [entry.raw_value || entry.component, entry.package, entry.population === 'unknown' ? '실장 미확인' : '원본 기준 실장', ...(entry.evidence || [])].filter(Boolean).join('\n') : '해당 위치 없음';
  output.append(simpleTable(['참조 위치', '기존 구성', '접수 후보'], data.changes.map((change) => [change.position, node('span', value(change.before), 'diff-value before'), node('span', value(change.after), 'diff-value after')])));
  output.append(jsonDetails('비교 기준·근거', {data: data, data_version: response.data_version, evidence: response.evidence, run_id: response.run_id}));
}
function sourceDraftArguments(kind) {
  const isHistory = kind === 'history';
  const receipt = isHistory ? state.historyReceipt : state.sourceReceipt;
  if (!receipt) throw new Error('원본 자료를 먼저 접수하세요.');
  if (!isHistory && (!state.sourceComparison || state.sourceComparison.receipt_id !== receipt.receipt_id)) throw new Error('접수 원본과 기존 구성의 개정별 BOM 비교를 먼저 확인하세요.');
  if (!$(isHistory ? 'history-source-reviewed' : 'source-reviewed').checked) throw new Error(isHistory ? '접수한 시연 기록과 정정 범위를 먼저 확인하세요.' : '추출 정보·원본·차이·미확인 사항을 확인한 뒤 저장하세요.');
  const saveKey = isHistory ? 'historySaveRequest' : 'productSaveRequest';
  const cycleKey = isHistory ? 'historyCycle' : 'productCycle';
  let body = state[saveKey];
  if (!body) {
    body = {receipt_id: receipt.receipt_id};
    if ($(isHistory ? 'history-source-revise' : 'source-revise').checked) {
      const draft = isHistory ? state.historyDraft : state.productDraft;
      if (!draft) throw new Error('새 버전으로 저장할 정확한 초안을 먼저 선택하세요.');
      body.draft_id = draft.id; body.expected_version = draft.version;
    }
    if (!state[cycleKey]) state[cycleKey] = crypto.randomUUID();
    body.request_key = requestKey((isHistory ? 'history_draft:' : 'source_draft:') + state[cycleKey], body);
    state[saveKey] = clone(body); persist();
  }
  return {receipt, body: clone(body)};
}
async function saveSourceDraft() {
  const {receipt, body} = sourceDraftArguments('product');
  const response = must(await api('/api/sources/draft', body)), draft = response.data;
  state.productDraft = {id: draft.draft_id, version: draft.draft_version, configuration_id: receipt.configuration_id, base_version: draft.base_version};
  state.activeProductTarget = 'draft';
  persist();
  const output = clear('source-draft-result');
  output.append(node('p', draft.validation.valid ? '등록 초안을 저장했습니다. 등록 조건 검증은 통과했으며 별도 검토자의 승인이 필요합니다.' : '초안은 저장했지만 등록 조건 검증을 통과하지 못했습니다. 근거와 오류를 보완하세요.', 'message ' + (draft.validation.valid ? 'success' : 'warning')));
  output.append(node('p', '초안 ' + draft.draft_id + ' · 버전 ' + draft.draft_version + ' · 기준 데이터 v' + draft.base_version));
  output.append(jsonDetails('등록 조건 검증 결과', draft.validation));
  if (response.warnings?.length) output.append(jsonDetails('재시도·미확인 안내', response.warnings));
  await loadDraft({id: draft.draft_id, version: draft.draft_version}, {announce: false});
  updateAIContext();
  if (loadedDraft.status === 'applied') message('이 등록안은 이미 데이터 v' + loadedDraft.applied_version + '로 반영되었습니다. 같은 요청의 저장 결과를 다시 확인했습니다.', 'success');
  else if (loadedDraft.status === 'approved') message('이 등록안은 승인되었으나 반영 완료를 확인해야 합니다. 검토 화면에서 같은 요청의 처리 결과를 확인하세요.', 'warning');
  else message(draft.validation.valid ? '등록 초안 버전 ' + draft.draft_version + ' 저장·검증을 확인했습니다. 별도 검토자의 검토·승인이 필요하며 아직 확정 반영되지 않았습니다.' : '등록 초안은 저장했지만 검증 오류를 보완해야 합니다. 아직 승인·반영되지 않았습니다.', draft.validation.valid ? 'success' : 'warning');
}
async function requestSourceReview() {
  if (!state.productDraft) throw new Error('먼저 등록 초안을 저장하세요.');
  selectView('review'); await loadDraft(state.productDraft);
  message('저장한 등록안의 같은 ID·버전을 검토 화면으로 연결했습니다. 별도 검토자가 의견을 남기고 결정해야 합니다.');
}
function configurationFromDraft(draft) {
  if (!draft?.operations) return null;
  return draft.operations.find((operation) => operation.node.type === 'Configuration')?.node.id || draft.operations.find((operation) => operation.node.type === 'BOMItem')?.node.configuration_id || null;
}
async function loadConfiguration(pinned) {
  const args = pinned || {configuration_id: $('configuration-id').value.trim()};
  if (!pinned) { const version = optionalVersion('configuration-version'); if (version !== undefined) args.version = version; }
  const response = await tool('configuration', args), data = response.data;
  state.confirmedConfiguration = {configuration_id: data.configuration.id, version: response.data_version};
  state.activeProductTarget = 'configuration'; persist();
  $('configuration-id').value = data.configuration.id; $('configuration-version').value = response.data_version;
  const output = clear('configuration-result');
  output.append(node('p', data.product.manufacturer + ' ' + data.product.name + ' · 개정 ' + data.revision.revision + ' · ' + data.configuration.variant + ' · 승인 데이터 v' + response.data_version, 'message success'));
  output.append(node('p', data.items.length + '개 BOM 행 · ' + data.items.reduce((sum, item) => sum + item.positions.length, 0) + '개 참조 위치 · 실장 미확인 ' + data.items.filter((item) => item.population === 'unknown').length + '행', 'hint'));
  output.append(simpleTable(['참조 위치', '원본 품목', '수량', 'BOM 실장 여부', '원본 근거'], data.items.map((item) => [item.positions.join(', '), item.raw_value, item.quantity, item.population === 'unknown' ? '미확인' : '원본 기준 실장', evidenceButton(item.evidence, response.data_version)])));
  output.append(jsonDetails('구성 연결·조회 버전', {product: data.product, revision: data.revision, configuration: data.configuration, data_version: response.data_version, run_id: response.run_id}));
  updateAIContext();
}
async function intakeHistory() {
  const file = $('history-source-file').files[0];
  if (!file || !file.name.toLowerCase().endsWith('.json') || file.size > 200000) throw new Error('200 KB 이하의 시연용 가상 운영 기록 JSON 파일을 선택하세요.');
  const bytes = new Uint8Array(await file.arrayBuffer()); let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 16384) binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
  const response = must(await api('/api/history/intake', {filename: file.name, content_base64: btoa(binary)}));
  state.historyReceipt = {...response.data, data_version: response.data_version}; state.activeHistoryTarget = 'receipt'; state.historySaveRequest = null; state.historyCycle = crypto.randomUUID();
  $('history-source-reviewed').checked = false; $('history-source-revise').checked = false;
  clear('history-draft-result'); clear('ai-output'); persist(); renderHistoryReceipt(); updateAIContext();
}
function renderHistoryReceipt() {
  const receipt = state.historyReceipt;
  if (!receipt) return;
  const output = clear('history-intake-result');
  output.append(node('p', receipt.notice || '가상 운영 기록을 접수했습니다. 아직 사실 확정·승인·반영되지 않았습니다.', 'message'));
  output.append(displaySource(receipt.source, receipt.data_version, false));
  output.append(node('p', '접수 ID: ' + receipt.receipt_id + ' · 기준 데이터 v' + receipt.base_version + ' · 기록 ' + receipt.records.length + '건 · 변경 묶음 ' + receipt.operations_count + '건', 'hint'));
  output.append(jsonDetails('제출된 시연 기록 · 저장 전에 확인', receipt.records));
  if (receipt.validation) { output.append(node('p', receipt.validation.valid ? '등록 조건 검증 통과 · 사실 확인·승인 아님' : '등록 조건 오류 있음 · 초안을 저장해도 승인 전에 보완해야 합니다.', 'message ' + (receipt.validation.valid ? 'success' : 'warning'))); output.append(jsonDetails('접수 기록의 등록 조건 검증', receipt.validation)); }
  output.append(node('p', '새 원본은 초안에 연결됩니다. 초안 저장 후 검토 화면에서 해당 버전의 원본을 내려받을 수 있습니다.', 'hint'));
  $('history-draft-controls').hidden = false;
}

async function saveHistoryDraft() {
  const {body} = sourceDraftArguments('history');
  const response = must(await api('/api/sources/draft', body)), draft = response.data;
  state.historyDraft = {id: draft.draft_id, version: draft.draft_version, base_version: draft.base_version}; state.activeHistoryTarget = 'draft'; persist();
  const output = clear('history-draft-result');
  output.append(node('p', draft.validation.valid ? '보완 초안을 저장하고 등록 조건 검증을 통과했습니다. 별도 검토자의 승인이 필요합니다.' : '보완 초안은 저장했지만 검증 오류가 있습니다. 원본과 오류를 확인하고 새 초안 버전으로 보완하세요.', 'message ' + (draft.validation.valid ? 'success' : 'warning')));
  output.append(node('p', '초안 ' + draft.draft_id + ' · 버전 ' + draft.draft_version + ' · 확정 데이터 미변경'));
  output.append(jsonDetails('등록 조건 검증 결과', draft.validation));
  await loadDraft(state.historyDraft, {announce: false});
  message('제출한 시연 자료를 정확한 보완 초안 버전에 연결했습니다. 검증은 원본의 사실성 또는 사람 승인을 대신하지 않습니다.', 'success');
}

async function loadHistory() {
  const kind = $('history-kind').value, id = $('history-node-id').value.trim(), version = optionalVersion('history-version');
  const args = {kind}; if (id) args.node_id = id; if (version !== undefined) args.version = version;
  const selected = await tool('lookup', args), pinned = selected.data_version, ids = new Set(selected.data.map((record) => record.id));
  const records = {};
  for (const type of ['Installation', 'HistoryCoverage', 'InventoryMovement']) records[type] = (await tool('lookup', {kind: type, version: pinned})).data;
  const installations = records.Installation.filter((record) => ids.has(kind === 'ProductUnit' ? record.unit_id : record.lot_id));
  const units = kind === 'ProductUnit' ? ids : new Set(installations.map((record) => record.unit_id));
  const lots = kind === 'ComponentLot' ? ids : new Set(installations.map((record) => record.lot_id));
  const coverage = records.HistoryCoverage.filter((record) => units.has(record.unit_id)), inventory = records.InventoryMovement.filter((record) => lots.has(record.lot_id));
  $('history-result-card').hidden = false; $('history-result-version').textContent = '데이터 v' + pinned;
  const output = clear('history-results');
  output.append(node('h3', kind === 'ProductUnit' ? '개별 보드' : '부품 로트'));
  output.append(simpleTable(['ID', '등록된 연결', '근거', '조사 조건으로 이동'], selected.data.map((record) => [record.id, kind === 'ProductUnit' ? record.configuration_id : record.component_id, evidenceButton(record.evidence, pinned), actionButton('A/B 조사에 사용', async () => {
    if (kind === 'ProductUnit') $('unit-ids').value = record.id; else $('lot-id').value = record.id;
    selectView('investigation'); $('query-note').textContent = '이력 화면에서 입력값을 가져왔습니다. 새 조건으로 조회해야 결과와 AI에 반영됩니다.';
    message('선택한 ID를 조사 입력에 채웠습니다. 대상 집합·로트·A/B 조건을 확인하고 장착 이력을 조회하세요.');
  })])));
  output.append(node('h3', '장착·제거 기록'));
  if (!installations.length) output.append(node('p', '현재 선택 범위에 연결된 장착 기록이 없습니다. 미장착·안전을 뜻하지 않습니다.', 'message warning'));
  else output.append(simpleTable(['보드 / 로트 / 위치', '장착 시각', '제거 / 장착 확인 기한', '근거'], installations.map((record) => [record.unit_id + '\n' + record.lot_id + '\n' + record.position, record.installed_at, '제거: ' + (record.removed_at || '미확인') + '\n확인 기한: ' + (record.confirmed_until || '없음 / 미확인'), evidenceButton(record.evidence, pinned)])));
  output.append(node('h3', '이력 확인 범위'), node('p', '전체 이력 확인은 시작 상태와 범위 내 모든 장착·제거 기록을 확인했다는 근거가 있어야 합니다.', 'hint'));
  output.append(simpleTable(['보드', '범위 [시작, 끝)', '전체 이력 근거', '근거'], coverage.map((record) => [record.unit_id, '[' + record.start + ', ' + record.end + ')', record.complete ? '있음' : '미확인', evidenceButton(record.evidence, pinned)])));
  output.append(node('h3', '관련 로트 입출고 기록'), node('p', '출고 기록만으로 특정 보드에 장착되었다고 추론하지 않습니다.', 'hint'));
  output.append(simpleTable(['로트', '입출고', '수량·시각', '근거'], inventory.map((record) => [record.lot_id, record.movement === 'receipt' ? '입고' : record.movement === 'issue' ? '출고' : record.movement, record.quantity + ' · ' + record.occurred_at, evidenceButton(record.evidence, pinned)])));
  state.knowledgeContext = {version: pinned, context: id ? {node_id: id} : {kind}, label: id || (kind === 'ProductUnit' ? '개별 보드 목록' : '부품 로트 목록')}; state.activeHistoryTarget = 'records'; persist(); updateAIContext();
}
function updateSearchMode() {
  const semantic = $('evidence-search-mode').value === 'semantic';
  $('evidence-search').textContent = semantic ? '관련 근거 후보 찾기' : '원본·기록 근거 찾기';
  $('evidence-search-mode-note').textContent = semantic
    ? (context?.embeddings?.configured
      ? '의미 검색 설정 있음 · ' + context.embeddings.model + ' · ' + context.embeddings.dimensions + '차원. 실행 시 선택한 승인 버전의 색인 범위를 확인합니다.'
      : '의미 검색 미설정 · 서버의 임베딩 API 설정이 필요합니다. 문자열 검색은 계속 사용할 수 있습니다.')
    : '현재 승인된 그래프에서 ID·원문 문자열이 일치하는 정보를 찾습니다. 검색어를 비우면 등록된 정보를 표시합니다.';
}
function searchCoverage(data) {
  const coverage = data.index_coverage, complete = coverage?.complete === true;
  const box = node('div', null, 'search-coverage');
  box.append(node('p', complete ? '선택한 범위의 근거 색인 완료 · 검색 결과는 관련 후보입니다.' : '일부 근거만 검색될 수 있습니다. 색인되지 않은 근거가 있으므로 전체 자료의 조사 결과로 해석하지 마세요.', 'message ' + (complete ? '' : 'warning')));
  if (Number.isInteger(coverage?.indexed) && Number.isInteger(coverage?.total)) box.append(node('p', '선택한 버전·자료 범위의 색인: ' + coverage.indexed + ' / ' + coverage.total + '건', 'hint'));
  else box.append(node('p', '색인 범위의 수량을 확인하지 못했습니다.', 'hint'));
  box.append(jsonDetails('검색 색인 범위', {index_coverage: coverage, index_scope: data.index_scope, model: data.model, dimensions: data.dimensions}));
  return box;
}
async function searchEvidence() {
  const query = $('evidence-search-query').value.trim(), origin = $('evidence-origin').value, version = optionalVersion('evidence-version');
  const mode = $('evidence-search-mode').value, semantic = mode === 'semantic';
  $('evidence-results-card').hidden = true;
  state.knowledgeContext = null; state.evidenceSearchVersion = null; persist(); updateAIContext();
  if (semantic && !query) throw new Error('의미 검색으로 찾을 제품·이력·원본 정보를 입력하세요.');
  if (semantic && !context?.embeddings?.configured) throw new Error('의미 검색용 임베딩 API가 서버에 설정되지 않았습니다. 문자열 검색을 선택하거나 서버 설정을 확인하세요.');
  const args = {query, origin}; if (version !== undefined) args.version = version;
  const response = await tool(semantic ? 'semantic_search' : (query ? 'search_evidence' : 'lookup'), semantic || query ? args : (version === undefined ? {} : {version}));
  const output = clear('evidence-search-results');
  state.evidenceSearchVersion = response.data_version;
  $('evidence-results-card').hidden = false; $('evidence-result-version').textContent = '승인 데이터 v' + response.data_version;
  if (semantic) {
    const candidates = response.data.candidates || [];
    $('evidence-result-count').textContent = '의미 검색 후보 ' + candidates.length + '건. 같은 의미 검색 조건을 AI에게 설명 요청하거나 후보의 원본 근거를 확인할 수 있습니다.';
    output.append(searchCoverage(response.data));
    for (const candidate of candidates) {
      const article = node('article', null, 'search-result');
      article.append(node('h3', (candidate.evidence || []).join(', ')), node('span', '관련 근거 후보', 'pill neutral'), node('span', candidate.origin === 'synthetic' ? '시연용 가상 기록' : '실제 공개 기술 자료', 'pill ' + (candidate.origin === 'synthetic' ? 'synthetic' : 'neutral')));
      article.append(node('p', '원본 위치: ' + (candidate.locator || '미확인'), 'hint'));
      if (Number.isFinite(candidate.similarity)) article.append(node('p', '유사도 점수: ' + candidate.similarity.toFixed(4) + ' · 사실 확률이나 장착 판정이 아닙니다.', 'hint'));
      if (candidate.evidence?.length) article.append(evidenceButton(candidate.evidence, response.data_version));
      if (candidate.source) article.append(displaySource(candidate.source, response.data_version));
      if (candidate.evidence?.[0]) article.append(actionButton('이 근거 후보를 AI로 설명', async () => { state.knowledgeContext = {version: response.data_version, context: {node_id: candidate.evidence[0]}, label: candidate.evidence[0] + ' · 의미 검색 후보'}; persist(); updateAIContext(); $('ai-question').focus(); document.querySelector('.ai-panel').scrollIntoView({behavior: 'smooth', block: 'start'}); }));
      article.append(jsonDetails('후보에 연결된 승인 정보·근거 해시', {facts: candidate.facts, content_hash: candidate.content_hash})); output.append(article);
    }
    if (!candidates.length) output.append(node('p', '이 색인 범위에서 반환된 후보가 없습니다. 관련 근거나 사실이 존재하지 않는다는 뜻은 아닙니다.', 'hint'));
    output.append(jsonDetails('의미 검색 요청·승인 버전·실행 근거', {query, origin, data_version: response.data_version, run_id: response.run_id, warnings: response.warnings}));
  } else {
    const matches = query ? response.data.matches : response.data.filter((record) => origin === 'all' || record.origin === origin);
    const total = query ? response.data.total : matches.length;
    const results = matches.slice(0, 60);
    $('evidence-result-count').textContent = (query ? '문자 일치 ' : '등록 정보 ') + total + '건 중 ' + results.length + '건 표시. 같은 검색 조건 또는 선택한 대상을 AI에게 설명 요청할 수 있습니다.';
    for (const record of results) {
      const article = node('article', null, 'search-result'); article.append(node('h3', record.id), node('span', record.origin === 'synthetic' ? '시연용 가상 기록' : '실제 공개 기술 자료', 'pill ' + (record.origin === 'synthetic' ? 'synthetic' : 'neutral')), node('p', record.type + ' · ' + (record.description || record.raw_value || record.name || record.locator || record.path || record.serial || record.lot_code || ''), 'hint'));
      const references = record.type === 'Evidence' ? [record.id] : record.evidence || [];
      if (references.length) article.append(evidenceButton(references, response.data_version));
      if (record.type === 'SourceDocument') article.append(displaySource(record, response.data_version));
      article.append(actionButton('이 대상을 AI로 설명', async () => { state.knowledgeContext = {version: response.data_version, context: {node_id: record.id}, label: record.id}; persist(); updateAIContext(); $('ai-question').focus(); document.querySelector('.ai-panel').scrollIntoView({behavior: 'smooth', block: 'start'}); }));
      article.append(jsonDetails('등록된 값·관계', record)); output.append(article);
    }
    if (!results.length) output.append(node('p', '이 버전과 검색 조건에 맞는 등록 정보가 없습니다. 존재하지 않음이나 안전 판정을 뜻하지 않습니다.', 'hint'));
  }
  persist(); updateAIContext();
}

async function query() {
  const queries = queriesFromForm();
  const A = await tool('investigate', {query: queries.A});
  const B = await tool('investigate', {query: queries.B, version: A.data_version});
  state.baseline = {queries: clone(queries), version: A.data_version, results: {A, B}};
  invalidateComparison();
  persist();
  renderBaseline();
  message('A/B 조회를 동일한 데이터 버전 ' + A.data_version + '로 완료했습니다. 이 조건을 보완 전후 비교에 유지합니다.', 'success');
}
function evidenceButton(ids, version, draftContext = null) {
  const button = node('button', '원본·이력 근거 ' + ids.length + '건', 'evidence-button');
  button.type = 'button';
  button.addEventListener('click', () => run(() => showEvidence(ids, version, draftContext), button));
  return button;
}
function resultTable(result, version) {
  const wrapper = node('div', null, 'table-wrap');
  const table = node('table');
  const head = node('thead'), header = node('tr');
  ['개별 보드', '조회 판정', '판정 근거·남은 미확인'].forEach((title) => header.append(node('th', title)));
  head.append(header); table.append(head);
  const body = node('tbody');
  (result.results || []).forEach((row) => {
    const tr = node('tr');
    tr.append(node('td', row.unit_id));
    const status = node('td'); status.append(pill(row.status));
    status.append(node('p', '이력 범위: ' + (row.coverage_complete ? '전체 확인 근거 있음' : '추가 확인 필요'), 'record-detail'));
    tr.append(status);
    const description = node('td'), list = node('ul', null, 'reason-list');
    (row.reasons || []).forEach((reason) => list.append(node('li', reasons[reason] || reason)));
    description.append(list);
    if (row.matched_intervals?.length) {
      row.matched_intervals.forEach((interval) => description.append(node('p', interval.installation_id + ' · [' + interval.start + ', ' + (interval.end || '끝 미확인') + ') · ' + ({removed: '제거 시각', confirmed_until: '장착 확인 기한', unknown: '장착 사건만 확인'}[interval.end_status] || interval.end_status), 'record-detail')));
    }
    if (row.unresolved_intervals?.length) description.append(node('p', '미확인 기록: ' + row.unresolved_intervals.join(', '), 'warning-line'));
    if (row.evidence?.length) description.append(evidenceButton(row.evidence, version));
    else description.append(node('p', '판정에 연결된 근거가 없습니다.', 'record-detail'));
    tr.append(description); body.append(tr);
  });
  table.append(body); wrapper.append(table);
  return wrapper;
}
function renderBaseline() {
  if (!state.baseline) return;
  $('results-card').hidden = false;
  const output = clear('results');
  for (const mode of ['A', 'B']) {
    const response = state.baseline.results[mode];
    const group = node('section', null, 'result-group');
    group.append(node('h3', mode === 'A' ? 'A · 기준 시점 장착 상태 확인' : 'B · 조사 기간 중 장착 이력 확인'));
    group.append(resultTable(response.data, response.data_version));
    if (response.conflicts?.length) group.append(node('p', '충돌 근거가 있습니다. 아래 실행 상세에서 대상과 근거를 확인하세요.', 'warning-line'));
    group.append(jsonDetails('조회 조건·규칙·실행 근거', {query: response.data.query, data_version: response.data_version, rule_version: response.rule_version, run_id: response.run_id, warnings: response.warnings, conflicts: response.conflicts}));
    output.append(group);
  }
  updateContext();
}
async function showEvidence(ids, version, draftContext = null) {
  const content = clear('evidence-content');
  $('evidence-dialog').showModal();
  content.append(node('p', '근거를 조회하고 있습니다…', 'hint'));
  try {
    const items = [];
    const draftNodes = new Map((draftContext?.operations || []).map((operation) => [operation.node.id, operation.node]));
    const resolve = async (id) => {
      const result = await api('/api/tools/lookup', {node_id: id, version});
      if (result.ok) return {value: result.data[0], fromDraft: false};
      if (result.errors?.[0]?.code === 'NOT_FOUND' && draftNodes.has(id)) return {value: draftNodes.get(id), fromDraft: true};
      throw responseError(result);
    };
    for (const id of [...new Set(ids)]) {
      const resolvedEvidence = await resolve(id), evidence = resolvedEvidence.value;
      if (!evidence || evidence.type !== 'Evidence') throw new Error('Evidence 형식의 근거를 찾지 못했습니다. [' + id + ']');
      const resolvedSource = await resolve(evidence.source_id), source = resolvedSource.value;
      if (!source || source.type !== 'SourceDocument') throw new Error('원본 문서의 연결을 확인할 수 없습니다.');
      const section = node('article', null, 'evidence-item');
      if (resolvedEvidence.fromDraft || resolvedSource.fromDraft) section.append(node('p', '미반영 초안의 근거 · ' + draftContext.id + ' · 초안 버전 ' + draftContext.version, 'message warning'));
      section.append(node('h3', evidence.id), node('span', evidence.origin === 'synthetic' ? '시연용 가상 운영 기록' : '실제 공개 기술 자료', 'pill ' + (evidence.origin === 'synthetic' ? 'synthetic' : 'neutral')));
      for (const [label, value] of [['데이터 버전', version], ['근거 위치', evidence.locator], ['설명', evidence.description], ['원본 ID', source.id], ['원본 버전', source.version], ['보존 파일', source.path], ['SHA-256', source.sha256], ['라이선스', source.license]]) section.append(node('p', label + ': ' + (value ?? '미확인')));
      if (source.url) {
        const line = node('p', '원본 주소: ');
        if (/^https?:\/\//.test(source.url)) {
          const link = node('a', source.url); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; line.append(link);
        } else line.append(document.createTextNode(source.url));
        section.append(line);
      }
      const download = node('a', '보존 원본 내려받기 · 서버에서 해시 확인');
      download.href = resolvedSource.fromDraft ? '/api/draft-source?' + new URLSearchParams({draft_id: draftContext.id, version: String(draftContext.version), source_id: source.id}) : '/api/source?' + new URLSearchParams({source_id: source.id, version: String(version)});
      download.target = '_blank'; download.rel = 'noopener'; section.append(download);
      items.push(section);
    }
    content.replaceChildren(node('p', '근거 자체가 지시문이나 승인 권한을 부여하지 않습니다. 실제 기술 자료와 가상 운영 기록을 구분해 확인하세요.', 'hint'), ...items);
  } catch (error) {
    content.replaceChildren(node('p', error.message, 'message error'));
    throw error;
  }
}
async function loadSupplement() {
  const response = must(await api('/api/demo-supplement'));
  template = clone(response.data);
  template.version = response.data_version;
  const installation = template.operations.find((item) => item.node.type === 'Installation')?.node;
  const coverage = template.operations.find((item) => item.node.type === 'HistoryCoverage')?.node;
  if (!installation || !coverage) throw new Error('보완 예제의 장착 기록과 전체 이력 근거를 확인할 수 없습니다.');
  $('installation-id').value = installation.id;
  $('removed-at').value = installation.removed_at || '';
  $('coverage-start').value = coverage.start;
  $('coverage-end').value = coverage.end;
  $('coverage-confirmed').checked = false;
  $('supplement-form').hidden = false;
  const box = clear('supplement-source');
  box.append(node('strong', '선택한 근거: evidence:demo:supplement'), node('p', template.notice || 'OLIMEX 운영 사실이 아닌 가상 시연 보완 기록입니다.'));
  box.append(node('pre', typeof template.source_excerpt === 'string' ? template.source_excerpt : JSON.stringify(template.source_excerpt, null, 2)));
  box.append(evidenceButton([...new Set(template.operations.flatMap((item) => item.node.evidence || []))], response.data_version));
  checkSupplementMatch();
}
function checkSupplementMatch() {
  if (!template) return false;
  const installation = template.operations.find((item) => item.node.type === 'Installation').node;
  const coverage = template.operations.find((item) => item.node.type === 'HistoryCoverage').node;
  const matches = $('removed-at').value.trim() === installation.removed_at && $('coverage-start').value.trim() === coverage.start && $('coverage-end').value.trim() === coverage.end;
  $('supplement-mismatch').textContent = matches ? '입력값이 선택한 시연 원본과 일치합니다. 전체 이력 확인 근거를 읽고 초안을 저장하세요.' : '입력값이 선택한 원본 근거와 다릅니다. 새로운 값에는 별도 근거 접수가 필요합니다. 이번 시연 경로에서는 이 값을 확인된 사실로 저장할 수 없습니다.';
  $('supplement-mismatch').className = matches ? 'hint' : 'warning-line';
  return matches;
}
async function saveSupplement() {
  if (!template) throw new Error('시연 보완 근거를 먼저 불러오세요.');
  if (!checkSupplementMatch()) throw new Error('보완 입력이 선택한 원본 근거와 다릅니다. 원본의 값을 사용하거나 별도 근거 접수를 준비하세요.');
  if (!$('coverage-confirmed').checked) throw new Error('시작 상태와 전체 장착·제거 이력의 확인 근거를 검토하세요.');
  const body = {operations: clone(template.operations)};
  if ($('revise-draft').checked) {
    if (!state.draftRef) throw new Error('새 버전으로 저장할 초안을 선택하세요.');
    body.draft_id = state.draftRef.id;
    body.expected_version = state.draftRef.version;
  }
  if (!state.draftCycle) state.draftCycle = crypto.randomUUID();
  body.request_key = requestKey('prepare_draft:' + state.draftCycle, body);
  const response = await tool('prepare_draft', body);
  const data = response.data;
  state.draftRef = {id: data.draft_id, version: data.draft_version};
  state.lastReview = null;
  invalidateComparison();
  persist();
  $('draft-id').value = data.draft_id;
  $('draft-version').value = data.draft_version;
  const text = data.validation.valid ? '보완 초안을 저장했습니다. 등록 조건 검증은 통과했으며 별도 검토자의 승인이 필요합니다.' : '보완 초안은 저장했지만 등록 조건 검증을 통과하지 못했습니다. 오류와 근거를 보완해야 합니다.';
  const box = clear('draft-created'); box.hidden = false; box.className = 'message ' + (data.validation.valid ? 'success' : 'warning');
  box.append(node('p', text), node('p', '초안 ID: ' + data.draft_id + ' · 초안 버전 ' + data.draft_version + ' · 기준 데이터 v' + data.base_version));
  const button = node('button', '등록·변경안 검토 화면으로 이동'); button.type = 'button';
  button.addEventListener('click', () => run(async () => { selectView('review'); await loadDraft(state.draftRef); }, button)); box.append(button);
  await loadDraft(state.draftRef, {announce: false});
  message(text, data.validation.valid ? 'success' : 'warning');
  updateContext();
}
function metadata(parent, key, value, full = false) {
  const group = node('dl', null, full ? 'full' : '');
  group.append(node('dt', key), node('dd', String(value ?? '미확인'))); parent.append(group);
}
function validationBlock(response, draft) {
  const box = clear('draft-validation');
  if (draft.status === 'applied') {
    box.append(node('p', '이 정확한 초안 버전은 데이터 v' + draft.applied_version + '로 반영되었습니다. 같은 초안에 새 승인을 하지 않습니다.', 'message success'));
    return;
  }
  if (!response?.ok) {
    box.append(node('p', responseError(response).message, 'message error'));
    return;
  }
  const validation = response.data.validation;
  box.append(node('p', validation.valid ? '현재 등록 조건 검증 통과. 이 결과는 사람 승인이나 확정 반영이 아닙니다.' : '현재 등록 조건 검증을 통과하지 못했습니다. 저장된 상태와 현재 검증 결과를 함께 확인하세요.', 'message ' + (validation.valid ? 'success' : 'warning')));
  if (draft.status === 'awaiting_review') box.append(node('p', '승인 대기는 작성 시 검증을 통과했다는 상태입니다. 기준 데이터가 달라졌다면 새 버전과 재검토가 필요합니다.', 'hint'));
  if (draft.status === 'needs_changes') box.append(node('p', '보완 필요는 검증 오류 또는 사람의 보완 요청일 수 있습니다. 아래 오류와 검토 이력을 함께 확인하세요.', 'hint'));
  if (validation.errors?.length) box.append(jsonDetails('등록 조건 오류', validation.errors));
  if (validation.warnings?.length) box.append(jsonDetails('검증 경고·미확인', validation.warnings));
}
function renderChanges(changes, version) {
  const target = clear('draft-changes');
  target.append(node('p', changes.length + '개 노드의 변경입니다. 각 항목을 열어 변경 전후 값과 원본 근거를 확인하세요.', 'hint'));
  for (const change of changes) {
    const group = node(changes.length > 8 ? 'details' : 'section'); group.append(node(changes.length > 8 ? 'summary' : 'h3', change.id));
    const wrapper = node('div', null, 'table-wrap'), table = node('table');
    const head = node('thead'), row = node('tr'); ['변경 항목', '변경 전', '등록·변경안'].forEach((title) => row.append(node('th', title))); head.append(row); table.append(head);
    const body = node('tbody');
    const fields = [...new Set([...Object.keys(change.before || {}), ...Object.keys(change.after || {})])];
    for (const key of fields) {
      if (canonical(change.before?.[key]) === canonical(change.after?.[key])) continue;
      const tr = node('tr', null, 'field-changed');
      const show = (value) => value === undefined ? '없음 / 미확인' : typeof value === 'string' ? value : JSON.stringify(value);
      tr.append(node('td', ({removed_at: '제거 시각', evidence: '연결 근거', start: '확인 범위 시작', end: '확인 범위 종료', complete: '전체 이력 확인', unit_id: '개별 보드', origin: '자료 구분'}[key] || key)), node('td', show(change.before?.[key]), 'diff-value before'), node('td', show(change.after?.[key]), 'diff-value after')); body.append(tr);
    }
    table.append(body); wrapper.append(table); group.append(wrapper);
    const ids = [...new Set([...(change.before?.evidence || []), ...(change.after?.evidence || [])])];
    if (ids.length) group.append(evidenceButton(ids, version, loadedDraft));
    target.append(group);
  }
}
async function loadDraft(ref, {announce = true, preserveOutcome = false} = {}) {
  loadedDraftReady = false;
  loadedValidation = null;
  if (!preserveOutcome) clear('review-outcome');
  clear('draft-validation');
  clear('draft-changes');
  const response = await tool('get_draft', {draft_id: ref.id, version: ref.version});
  const draft = response.data;
  // Review decisions change without changing the immutable draft version.
  if (loadedDraft?.id === draft.id && loadedDraft?.version === draft.version &&
      (loadedDraft.status !== draft.status || loadedDraft.reviewer !== draft.reviewer ||
       canonical(loadedDraft.review_history) !== canonical(draft.review_history))) {
    clear('ai-output');
  }
  loadedDraft = draft;
  if (state.productDraft?.id === draft.id && state.productDraft?.version === draft.version) { state.productDraft.base_version = draft.base_version; state.productDraft.applied_version = draft.applied_version; }
  if (state.historyDraft?.id === draft.id && state.historyDraft?.version === draft.version) { state.historyDraft.base_version = draft.base_version; state.historyDraft.applied_version = draft.applied_version; }
  if (state.draftRef?.id !== draft.id || state.draftRef?.version !== draft.version) invalidateComparison();
  state.draftRef = {id: draft.id, version: draft.version};
  if (state.lastReview && (state.lastReview.draft_id !== draft.id || state.lastReview.version !== draft.version)) state.lastReview = null;
  $('draft-id').value = draft.id; $('draft-version').value = draft.version;
  $('review-card').hidden = false;
  $('review-confirmed').checked = false;
  $('draft-status').textContent = draftLabels[draft.status] || draft.status;
  $('draft-status').className = 'pill ' + (draft.status === 'applied' ? 'included' : 'neutral');
  const meta = clear('draft-metadata');
  metadata(meta, '초안 ID', draft.id, true); metadata(meta, '초안 버전', draft.version); metadata(meta, '작성자 · 서버 신원', draft.author); metadata(meta, '기준 데이터 버전', draft.base_version); metadata(meta, ['approved', 'applied'].includes(draft.status) ? '승인자' : '검토자', draft.reviewer || '아직 없음'); metadata(meta, '반영된 데이터 버전', draft.applied_version ?? '아직 반영되지 않음'); metadata(meta, '내용 해시', draft.content_hash, true);
  $('draft-operations').textContent = JSON.stringify(draft.operations, null, 2);
  const events = clear('review-history');
  if (!draft.review_history?.length) events.append(node('p', '이 버전의 검토 이력이 없습니다.', 'hint'));
  else for (const event of draft.review_history) events.append(node('p', event.created_at + ' · ' + event.actor + ' · ' + ({approve: '승인', request_changes: '보완 요청', reject: '반려'}[event.decision] || event.decision) + '\n' + event.note));
  loadedValidation = draft.status === 'applied' ? null : await api('/api/tools/validate_draft', {draft_id: draft.id, version: draft.version});
  validationBlock(loadedValidation, draft);
  let changes = loadedValidation?.ok ? loadedValidation.data.changes : null;
  if (!changes) {
    changes = [];
    const baseNodes = new Map();
    for (const operation of draft.operations) {
      const kind = operation.node.type;
      if (!baseNodes.has(kind)) baseNodes.set(kind, (await tool('lookup', {kind, version: draft.base_version})).data);
      changes.push({id: operation.node.id, before: baseNodes.get(kind).find((item) => item.id === operation.node.id) || null, after: operation.node});
    }
  }
  renderChanges(changes, draft.status === 'applied' ? draft.applied_version : draft.base_version);
  const params = new URLSearchParams({draft: draft.id, version: String(draft.version)});
  $('review-path').value = '/#review?' + params;
  if (location.hash.startsWith('#review')) history.replaceState(null, '', '#review?' + params);
  loadedDraftReady = true;
  persist(); updateContext();
  if (announce) message('초안 ' + draft.id + '의 정확한 버전 ' + draft.version + '을 불러왔습니다.', 'success');
}
async function review(decision, retry = false) {
  let body;
  if (retry) {
    if (!state.lastReview) throw new Error('저장된 검토 요청이 없습니다.');
    body = clone(state.lastReview);
  } else {
    if (!loadedDraftReady || !loadedDraft || $('draft-id').value.trim() !== loadedDraft.id || Number($('draft-version').value) !== loadedDraft.version) throw new Error('검토할 초안을 먼저 불러오세요.');
    if (!$('review-confirmed').checked) throw new Error('초안 ID·버전, 근거와 변경 전후 값을 확인했는지 체크하세요.');
    const note = $('review-note').value.trim();
    if (!note) throw new Error('검토 의견을 입력하세요.');
    body = {draft_id: loadedDraft.id, version: loadedDraft.version, decision, note};
    body.request_key = requestKey('review', body);
    state.lastReview = clone(body); persist();
  }
  const result = await api('/api/review', body);
  renderReviewOutcome(result);
  if (result.data?.review?.ok) {
    await loadDraft({id: body.draft_id, version: body.version}, {announce: false, preserveOutcome: true});
    if (result.data.application?.ok) {
      $('current-version').textContent = '확인한 반영 데이터 v' + result.data.application.data_version;
      message('별도 검토자 승인과 데이터 v' + result.data.application.data_version + ' 반영을 확인했습니다. ' + (configurationFromDraft(loadedDraft) ? '승인된 구성 확인에서 이 버전을 재조회하세요.' : '최초 조회와 같은 조건으로 전후 결과를 비교하세요.'), 'success');
    } else if (result.data.application) message('승인은 기록되었지만 반영이 완료되지 않았습니다. 아래 반영 오류를 확인하고 동일 요청으로 재시도하세요.', 'warning');
    else message('검토 결정을 기록했습니다. 확정 데이터는 변경하지 않았습니다.', 'success');
  } else if (!result.ok) throw responseError(result);
}
function renderReviewOutcome(result) {
  const target = clear('review-outcome');
  const reviewResult = result.data?.review;
  const application = result.data?.application;
  if (!reviewResult) { target.append(node('p', responseError(result).message, 'message error')); return; }
  const steps = node('div', null, 'workflow-steps');
  const reviewed = node('div'); reviewed.append(node('strong', '사람 검토 결과'), node('span', reviewResult.ok ? (draftLabels[reviewResult.data.status] || reviewResult.data.status) : responseError(reviewResult).message));
  if (reviewResult.ok) reviewed.append(node('p', '초안 버전 ' + reviewResult.data.draft_version + ' · ' + reviewResult.data.reviewer));
  const applied = node('div'); applied.append(node('strong', '서버 반영 결과'));
  if (application?.ok) applied.append(node('span', '데이터 v' + application.data_version + ' 반영 완료', 'pill included'));
  else if (application) applied.append(node('span', responseError(application).message, 'warning-line'));
  else applied.append(node('span', '이번 결정에서 반영하지 않음'));
  steps.append(reviewed, applied); target.append(steps);
  const warnings = [...(reviewResult.warnings || []), ...(application?.warnings || [])];
  if (warnings.length) target.append(node('p', '응답 재사용/처리 안내: ' + warnings.map((item) => item.code).join(', '), 'hint'));
  target.append(jsonDetails('검토·반영 실행 근거', result));
}
async function compareResults() {
  if (!state.baseline || !state.draftRef) throw new Error('최초 조사 조건과 보완 초안을 먼저 선택하세요.');
  const baseline = clone(state.baseline), ref = clone(state.draftRef);
  const saved = (await tool('get_draft', {draft_id: ref.id, version: ref.version})).data;
  if (saved.status !== 'applied' || !Number.isInteger(saved.applied_version)) throw new Error('이 정확한 초안 버전이 아직 반영되지 않았습니다. 검토·승인 화면에서 반영 결과를 확인하세요.');
  const comparisons = {};
  for (const mode of ['A', 'B']) comparisons[mode] = await tool('compare_investigation', {query: baseline.queries[mode], before_version: baseline.version, after_version: saved.applied_version});
  state.comparison = {after_version: saved.applied_version, results: comparisons, draft: ref};
  clear('ai-output');
  persist(); renderComparison(); updateContext();
  message('대상·로트·A/B 조건을 유지하고 데이터 버전 ' + baseline.version + ' → ' + saved.applied_version + '을 비교했습니다.', 'success');
}
function renderComparison() {
  if (state.comparison && !comparisonMatchesDraft()) { invalidateComparison(); persist(); }
  const comparison = state.comparison;
  if (!comparison || !state.baseline) return;
  const target = clear('comparison-results');
  for (const mode of ['A', 'B']) {
    const response = comparison.results[mode], data = response.data;
    target.append(node('h3', mode + ' · 동일 조건 · v' + data.before_version + ' → v' + data.after_version));
    const wrapper = node('div', null, 'table-wrap'), table = node('table'), head = node('thead'), header = node('tr');
    ['보드', '보완 전 · v' + data.before_version, '보완 후 · v' + data.after_version].forEach((label) => header.append(node('th', label))); head.append(header); table.append(head);
    const body = node('tbody');
    data.before.results.forEach((before) => {
      const after = data.after.results.find((row) => row.unit_id === before.unit_id);
      const tr = node('tr'), oldCell = node('td'), newCell = node('td');
      oldCell.append(pill(before.status)); if (before.evidence?.length) oldCell.append(node('br'), evidenceButton(before.evidence, data.before_version));
      if (after) { newCell.append(pill(after.status)); if (after.evidence?.length) newCell.append(node('br'), evidenceButton(after.evidence, data.after_version)); if (after.unresolved_intervals?.length) newCell.append(node('p', '미확인 기록 유지: ' + after.unresolved_intervals.join(', '), 'record-detail')); }
      else newCell.append(node('span', '대상 결과 누락 · 확인 필요'));
      tr.append(node('td', before.unit_id), oldCell, newCell); body.append(tr);
    });
    table.append(body); wrapper.append(table); target.append(wrapper);
    const details = node('details'); details.append(node('summary', '보완 후 판정 이유·남은 미확인'), resultTable(data.after, data.after_version)); target.append(details);
    target.append(jsonDetails('동일 조건·버전·규칙 확인', {query: data.query, condition_hash: data.condition_hash, before_version: data.before_version, after_version: data.after_version, rule_version: response.rule_version, run_id: response.run_id}));
  }
}
function assistantDraftTarget() {
  if (currentView === 'product' && state.sourceReceipt && state.activeProductTarget !== 'configuration') return {kind: 'product', receipt: state.sourceReceipt};
  if (currentView === 'history' && state.historyReceipt && state.activeHistoryTarget !== 'records') return {kind: 'history', receipt: state.historyReceipt};
  return null;
}
function assistantScope() {
  if (!context) return null;
  const currentVersion = Number.isInteger(workspaceVersion) ? workspaceVersion : context.initial_data_version;
  let version = currentVersion, shared = {}, description = '업무에 필요한 대상과 조건을 먼저 확인합니다.', subtitle = '업무 요청 분석·처리 안내';
  if (currentView === 'investigation') {
    subtitle = '로트 사용처·장착 이력 조사 지원';
    if (state.baseline) {
      const body = {queries: clone(state.baseline.queries), version: comparisonMatchesDraft() ? state.comparison.after_version : state.baseline.version};
      if (comparisonMatchesDraft()) body.before_version = state.baseline.version;
      return {path: '/api/quality', body, subtitle, description: conditionLines(body.queries, body.version).join('\n') + (body.before_version !== undefined ? '\n비교 v' + body.before_version + ' → v' + body.version : '')};
    }
    description = '아직 실행한 조사 조건이 없습니다. 보드·로트·A/B 시각을 자연어로 요청하면 필요한 입력 또는 조건 제안을 안내합니다.';
  } else if (currentView === 'product') {
    subtitle = '제품정보 등록 지원';
    if (state.activeProductTarget === 'draft' && state.productDraft) {
      version = state.productDraft.applied_version ?? state.productDraft.base_version ?? state.sourceReceipt?.base_version ?? state.sourceReceipt?.data_version;
      shared = {draft_id: state.productDraft.id, draft_version: state.productDraft.version};
      description = '등록 초안: ' + state.productDraft.id + ' · v' + state.productDraft.version;
    } else if (state.activeProductTarget === 'configuration' && state.confirmedConfiguration) {
      version = state.confirmedConfiguration.version; shared = {configuration_id: state.confirmedConfiguration.configuration_id}; description = '승인 구성: ' + state.confirmedConfiguration.configuration_id;
    } else if (state.sourceReceipt) {
      version = state.sourceReceipt.base_version ?? state.sourceReceipt.data_version;
      shared = {receipt_id: state.sourceReceipt.receipt_id}; description = '미승인 원본 접수: ' + state.sourceReceipt.receipt_id;
    } else description = '등록할 실제 원본 또는 조회할 구성을 먼저 확인합니다.';
  } else if (currentView === 'review') {
    subtitle = '등록·변경안의 근거·검증 설명';
    if (loadedDraftReady && loadedDraft) {
      version = loadedDraft.applied_version ?? loadedDraft.base_version;
      shared = {draft_id: loadedDraft.id, draft_version: loadedDraft.version}; description = '검토 초안: ' + loadedDraft.id + ' · v' + loadedDraft.version;
    } else description = '설명할 정확한 등록·변경안 ID와 버전이 필요합니다.';
  } else if (currentView === 'history' || currentView === 'evidence') {
    subtitle = '제품·이력·근거 조회';
    if (currentView === 'history' && state.activeHistoryTarget === 'draft' && state.historyDraft) {
      version = state.historyDraft.applied_version ?? state.historyDraft.base_version;
      shared = {draft_id: state.historyDraft.id, draft_version: state.historyDraft.version}; description = '보완 초안: ' + state.historyDraft.id + ' · v' + state.historyDraft.version;
    } else if (currentView === 'history' && state.activeHistoryTarget === 'receipt' && state.historyReceipt) {
      version = state.historyReceipt.base_version ?? state.historyReceipt.data_version;
      shared = {receipt_id: state.historyReceipt.receipt_id}; description = '미승인 가상 기록 접수: ' + state.historyReceipt.receipt_id;
    } else if (state.knowledgeContext) {
      version = state.knowledgeContext.version; shared = clone(state.knowledgeContext.context); description = '선택한 대상: ' + state.knowledgeContext.label;
    } else if (currentView === 'evidence' && $('evidence-search-query').value.trim()) {
      const text = $('evidence-version').value.trim();
      version = text ? Number(text) : (state.evidenceSearchVersion ?? currentVersion);
      shared = {search_query: $('evidence-search-query').value.trim(), origin: $('evidence-origin').value, search_mode: $('evidence-search-mode').value}; description = (shared.search_mode === 'semantic' ? '의미 검색 후보: ' : '문자열 검색: ') + shared.search_query + ' · 자료: ' + shared.origin;
    } else description = '대상 ID 또는 찾을 제품·이력·원본 정보를 요청하세요.';
  } else if (state.baseline) {
    version = comparisonMatchesDraft() ? state.comparison.after_version : state.baseline.version;
    shared = {queries: clone(state.baseline.queries)};
    if (comparisonMatchesDraft()) shared.before_version = state.baseline.version;
    description = '최근 실제 조사 조건\n' + conditionLines(state.baseline.queries, version).join('\n');
  } else {
    shared = {kind: 'Configuration'}; description = '현재 등록된 구성 목록 · 추가 업무는 필요한 조건을 확인합니다.';
  }
  if (!Number.isInteger(version) || version < 0) return null;
  return {path: '/api/assistant', body: {version, screen: currentView, context: shared}, subtitle, description: description + '\n데이터 버전: ' + version};
}
function updateAIContext() {
  const scope = assistantScope();
  // A previous explanation belongs to its exact target, conditions and version.
  // Typing another question alone does not change those pinned facts.
  const fingerprint = scope ? canonical({path: scope.path, body: scope.body}) : null;
  if (fingerprint !== aiScopeFingerprint) {
    clear('ai-output');
    aiScopeFingerprint = fingerprint;
  }
  const descriptions = {product: '등록 초안을 저장하거나 승인된 구성을 조회하면 같은 대상을 AI가 설명할 수 있습니다.', review: '정확한 초안 버전을 불러오면 검토 근거를 설명할 수 있습니다.', history: '보드·로트 이력을 조회해 설명할 대상을 고정하세요.', evidence: '검색 결과에서 「이 대상을 AI로 설명」을 선택하세요.', home: '업무 현황을 불러오면 요청의 처리 경로를 안내합니다.', investigation: '아직 고정된 조회 조건이 없습니다.'};
  $('ai-context').textContent = scope?.description || descriptions[currentView];
  $('ai-subtitle').textContent = scope?.subtitle || '현재 업무의 대상·조건·근거 연결';
  if (!isBusy) $('ask-ai').disabled = !scope || !context?.model?.configured;
  const draftTarget = assistantDraftTarget();
  $('ai-draft-controls').hidden = !draftTarget;
  $('ai-draft-target').textContent = draftTarget ? '초안에 사용할 접수: ' + draftTarget.receipt.receipt_id + ' · 기준 데이터 v' + (draftTarget.receipt.base_version ?? draftTarget.receipt.data_version) : '';
  if (!isBusy) $('prepare-ai-draft').disabled = !draftTarget || !context?.model?.configured || !context?.identity?.permissions?.includes('draft');
}
async function askContextAI() {
  if (currentView === 'product' && state.activeProductTarget === 'draft' && state.productDraft) await loadDraft(state.productDraft, {announce: false});
  else if (currentView === 'history' && state.activeHistoryTarget === 'draft' && state.historyDraft) await loadDraft(state.historyDraft, {announce: false});
  else if (currentView === 'review' && loadedDraftReady) await loadDraft({id: loadedDraft.id, version: loadedDraft.version}, {announce: false, preserveOutcome: true});
  const scope = assistantScope();
  if (!scope) throw new Error('현재 화면에서 설명할 대상과 데이터 버전을 먼저 고정하세요.');
  if (scope.path === '/api/quality') return askAI();
  if (!context.model?.configured) throw new Error('서버에 실제 모델이 설정되지 않았습니다. 공통 도구 조회는 계속 사용할 수 있습니다.');
  const defaults = {home: '현재 대상에 맞는 업무 처리 경로와 다음에 확인할 내용을 알려줘.', investigation: '로트 장착 이력을 조사하려고 해. 보드·로트와 A/B 시각 중 필요한 입력을 알려줘.', product: '선택한 제품 구성 또는 등록 초안의 원본 근거와 미확인 사항, 현재 처리 상태를 설명해줘.', review: '이 정확한 초안 버전의 변경 내용과 검증·근거·검토할 사항을 설명해줘. 승인하지 마.', history: '선택한 보드·로트의 기록과 확인할 수 있는 범위, 미확인 사항을 원본 근거와 함께 설명해줘.', evidence: '선택한 정보와 원본 근거의 적용 범위, 확인할 수 없는 내용을 설명해줘.'};
  const body = {...scope.body, question: $('ai-question').value.trim() || defaults[currentView]};
  const output = clear('ai-output'); output.append(node('p', '실제 모델과 공통 도구를 실행하고 있습니다…', 'hint'));
  let response;
  try { response = must(await api(scope.path, body)); }
  catch (error) { output.replaceChildren(node('p', error.message, 'message error')); throw error; }
  const data = response.data, runtime = data.runtime;
  if (renderAssistantGuidance(data, scope, output)) return;
  if (data.consistency !== 'matched' || !runtime?.ok || (data.delegation && !data.delegation.runtime?.ok)) { output.replaceChildren(node('p', '실제 모델의 도구 결과·대상 일치성을 확인하지 못해 정상 설명으로 표시하지 않았습니다.', 'message warning')); return; }
  const displayText = (data.display_text ?? runtime.text).replace(/\b(included|excluded|needs_confirmation)\b/g, (word) => labels[word]);
  output.replaceChildren(node('span', '실모델 조회·설명 완료', 'pill included'), node('p', displayText, 'ai-answer'));
  if (data.delegation) output.append(node('p', '업무 연결: ' + ({product: '제품정보 등록 지원', knowledge: '제품·이력·근거 조회', quality: '로트 장착 이력 조사 지원'}[data.role] || data.role) + ' · ' + data.delegation.reason, 'hint'));
  output.append(node('p', runtime.model + ' · 실행 ' + runtime.run_id, 'ai-answer-meta'));
  output.append(node('p', '이 요청의 모델 연결과 공통 도구 조회 결과를 확인했습니다. 초안 저장은 별도 요청이며 검토·승인·반영과 다릅니다. 범용 해석 품질 전체의 완료 판정은 아닙니다.', 'hint'));
  const references = new Map();
  const receiptResult = scope.body.context?.receipt_id ? data.results?.find?.((result) => result.data?.source) : null;
  if (receiptResult) output.append(displaySource(receiptResult.data.source, receiptResult.data.base_version, false), node('p', '접수 후보의 원본입니다. 아직 승인된 지식이나 장착 사실이 아닙니다.', 'hint'));
  for (const result of (Array.isArray(data.results) ? data.results : Object.values(data.results || {}))) if (result.ok && result.data?.mode === 'semantic') output.append(searchCoverage(result.data));
  for (const result of (Array.isArray(data.results) ? data.results : Object.values(data.results || {}))) if (result.ok && result.evidence?.length) {
    const key = result.data_version; if (!references.has(key)) references.set(key, new Set()); result.evidence.forEach((id) => references.get(key).add(id));
  }
  for (const [version, ids] of references) {
    if (scope.body.context?.receipt_id) continue; // Candidate-only evidence is opened after an exact draft exists.
    const list = [...ids]; output.append(evidenceButton(list.slice(0, 20), version, ['review', 'product', 'history'].includes(currentView) ? loadedDraft : null));
    if (list.length > 20) output.append(node('p', '화면에서 우선 20건의 근거를 엽니다. 전체 ID는 실행 상세에서 확인하세요.', 'hint'));
  }
  output.append(jsonDetails('대상·버전·역할 연결·실행 근거', {request_context: scope.body, role: data.role, delegation_role: data.delegation?.role, tool_calls: data.tool_calls, results: data.results, runtime_id: data.runtime.run_id, delegated_runtime_id: data.delegation?.runtime?.run_id}));
}

function renderAssistantGuidance(data, scope, output) {
  if (!['needs_input', 'confirm_conditions'].includes(data?.action)) return false;
  output.replaceChildren(node('span', data.action === 'needs_input' ? '추가 입력 필요' : '조사 조건 확인 필요', 'pill needs_confirmation'));
  output.append(node('p', '아직 이 요청으로 장착 이력을 조회하거나 초안을 저장하지 않았습니다.', 'hint'));
  const questions = Array.isArray(data.questions) ? data.questions.filter((question) => typeof question === 'string') : [];
  if (questions.length) { const list = node('ul', null, 'ai-questions'); questions.forEach((question) => list.append(node('li', question))); output.append(list); }
  if (data.action === 'confirm_conditions') {
    const queries = data.proposed_queries;
    if (!queries?.A || !queries?.B || !Array.isArray(queries.A.unit_ids) || !Array.isArray(queries.B.unit_ids) ||
        !queries.A.unit_ids.length || queries.A.unit_ids.some((id) => typeof id !== 'string') ||
        canonical(queries.A.unit_ids) !== canonical(queries.B.unit_ids) || queries.A.lot_id !== queries.B.lot_id ||
        typeof queries.A.lot_id !== 'string' || [queries.A.at, queries.B.start, queries.B.end].some((value) => typeof value !== 'string') ||
        queries.A.exclude_unit_id || queries.B.exclude_unit_id) throw new Error('제안된 조건을 현재 화면에 정확히 연결할 수 없습니다. 대상·로트·A/B 조건을 직접 입력하세요.');
    output.append(node('p', conditionLines(queries, scope.body.version).join('\n'), 'ai-proposed-conditions'));
    output.append(actionButton('조사 조건에 적용', async () => {
      fillQueries(queries);
      state.baseline = null; invalidateComparison(); persist();
      $('results-card').hidden = true;
      selectView('investigation');
      $('query-note').textContent = 'AI가 제안한 조건을 입력에만 적용했습니다. 보드·로트·시간대를 확인하고 장착 이력 조회를 누르세요.';
      message('조건 입력만 변경했습니다. 아직 조회하지 않았습니다. 장착 이력 조회를 눌러 공통 도구로 확인하세요.');
      $('query-submit').focus();
    }));
  } else {
    const targets = {product: '원본 접수 화면으로', investigation: '조사 조건 입력으로', history: '이력 자료 접수 화면으로', evidence: '기술 문서·근거 조회로', review: '등록·변경안 선택으로', home: '업무 현황으로'};
    if (targets[data.target_screen]) output.append(actionButton(targets[data.target_screen], async () => { await enterView(data.target_screen); }));
    output.append(node('p', '질문에 필요한 정보를 아래 업무 요청에 추가해 다시 요청할 수도 있습니다.', 'hint'));
  }
  output.append(jsonDetails('확인할 조건·역할 실행', {role: data.role, action: data.action, required_fields: data.required_fields, conditions: data.conditions, runtime_id: data.runtime?.run_id, delegated_runtime_id: data.delegation?.runtime?.run_id}));
  return true;
}
async function prepareAIDraft() {
  const target = assistantDraftTarget();
  if (!target) throw new Error('등록·보완할 원본 자료를 먼저 접수하세요.');
  if (!context?.model?.configured) throw new Error('실제 모델이 설정되지 않았습니다. 직접 초안 저장은 계속 사용할 수 있습니다.');
  const {receipt, body: sourceRequest} = sourceDraftArguments(target.kind);
  const shared = {receipt_id: sourceRequest.receipt_id};
  if (sourceRequest.draft_id) { shared.draft_id = sourceRequest.draft_id; shared.draft_version = sourceRequest.expected_version; }
  const body = {action: 'prepare_draft', screen: currentView, version: receipt.base_version ?? receipt.data_version,
    context: shared, request_key: sourceRequest.request_key,
    question: $('ai-question').value.trim() || '접수한 원본의 확인된 내용과 미확인 사항을 보존해 등록·보완 초안을 저장하고 검증해줘. 승인하거나 확정 반영하지 마.'};
  const output = clear('ai-output'); output.append(node('p', '실제 모델이 접수 자료와 공통 도구로 초안을 작성하고 있습니다…', 'hint'));
  let response;
  try { response = await api('/api/assistant', body); }
  catch (error) { output.replaceChildren(node('p', error.message, 'message error'), node('p', '저장 여부가 불확실하면 업무 현황을 확인하세요. 다시 요청해도 동일한 저장 요청 키를 사용합니다.', 'hint')); throw error; }
  const data = response.data;
  if (response.ok && renderAssistantGuidance(data, {body}, output)) return;
  const draft = data?.draft, record = data?.draft_record;
  const id = draft?.draft_id || record?.id, version = draft?.draft_version || record?.version;
  const saved = typeof id === 'string' && Number.isInteger(version) && version > 0;
  if (saved) {
    const ref = {id, version, base_version: draft?.base_version ?? record?.base_version ?? receipt.base_version};
    if (Number.isInteger(record?.applied_version)) ref.applied_version = record.applied_version;
    if (target.kind === 'product') { state.productDraft = {...ref, configuration_id: receipt.configuration_id}; state.activeProductTarget = 'draft'; }
    else { state.historyDraft = ref; state.activeHistoryTarget = 'draft'; }
    state.draftRef = {id, version}; invalidateComparison(); persist();
    // Persist the exact saved reference even when the model's later explanation failed.
    updateAIContext();
    output.replaceChildren(node('span', response.ok && data.consistency === 'matched' && data.runtime?.ok ? 'AI 초안 저장 결과 확인' : '초안 저장됨 · AI 후속 처리 미완료', 'pill ' + (response.ok ? 'neutral' : 'needs_confirmation')));
    output.append(node('p', '초안 ' + id + ' · 버전 ' + version + ' · 기준 데이터 v' + ref.base_version, 'ai-draft-reference'));
    output.append(node('p', '초안 저장은 검토 승인이나 확정 반영이 아닙니다. 정확한 버전의 변경 내용과 원본 근거를 검토하세요.', 'hint'));
    if (record?.status) output.append(node('p', '서버가 반환한 초안 상태: ' + (draftLabels[record.status] || record.status)));
    if (draft?.validation) output.append(jsonDetails('등록 조건 검증 결과', draft.validation));
    output.append(actionButton('저장된 정확한 초안 검토', async () => { selectView('review'); await loadDraft({id, version}); }));
    const link = node('a', '이 초안 버전의 검토 경로'); link.href = '/#review?' + new URLSearchParams({draft: id, version: String(version)}); const linkParagraph = node('p'); linkParagraph.append(link); output.append(linkParagraph);
    const screenOutput = clear(target.kind === 'product' ? 'source-draft-result' : 'history-draft-result');
    screenOutput.append(node('p', 'AI 요청으로 초안 ' + id + ' · 버전 ' + version + '을 저장했습니다. 검토·승인은 별도입니다.', 'message'), actionButton('저장된 초안 검토', async () => { selectView('review'); await loadDraft({id, version}); }));
  } else if (!response.ok) output.replaceChildren(node('p', responseError(response).message, 'message error'));
  else output.replaceChildren(node('p', '저장된 정확한 초안 ID·버전을 확인하지 못했습니다. 완료로 표시하지 않았습니다.', 'message warning'));
  if (!response.ok && saved) output.append(node('p', responseError(response).message, 'message warning'), node('p', 'AI 후속 설명이 실패했어도 저장된 초안은 남아 있습니다. 동일 요청 재시도 또는 정확한 초안 검토를 사용할 수 있습니다.', 'hint'));
  if (response.ok && data?.consistency === 'matched' && data.runtime?.ok && saved) output.append(node('p', data.display_text ?? data.runtime.text, 'ai-answer'));
  if (data) output.append(jsonDetails('초안·버전·모델 실행 근거', {action: data.action, role: data.role, draft, draft_record: record, tool_calls: data.tool_calls, runtime_id: data.runtime?.run_id, request_key: body.request_key, conditions: data.conditions}));
  updateControls();
}

async function askAI() {
  if (state.comparison && !comparisonMatchesDraft()) { invalidateComparison(); persist(); updateContext(); }
  if (!state.baseline) throw new Error('조회 조건을 먼저 실행해 고정하세요.');
  if (!context.model?.configured) throw new Error('서버에 실제 모델이 연결되지 않았습니다. 실모델 설명 완료로 표시하지 않습니다.');
  const body = {queries: clone(state.baseline.queries), version: state.comparison ? state.comparison.after_version : state.baseline.version, question: $('ai-question').value.trim() || '확인된 장착 이력과 추가 근거가 필요한 부분을 A/B 조건, 원본 근거, 데이터 버전과 함께 설명해줘.'};
  if (state.comparison) body.before_version = state.baseline.version;
  const output = clear('ai-output');
  output.append(node('p', '실제 모델과 공통 조회 도구를 실행하고 있습니다…', 'hint'));
  let response;
  try { response = must(await api('/api/quality', body)); }
  catch (error) { output.replaceChildren(node('p', error.message, 'message error')); throw error; }
  const data = response.data;
  if (data.consistency !== 'matched' || !data.runtime?.ok) {
    output.replaceChildren(node('p', '조회 조건·결과의 일치성을 확인하지 못해 정상 설명으로 표시하지 않았습니다.', 'message warning'));
    return;
  }
  const displayText = (data.display_text ?? data.runtime.text).replace(/\b(included|excluded|needs_confirmation)\b/g, (word) => labels[word]);
  output.replaceChildren(node('span', '실모델 설명 완료 · 도구 결과 일치', 'pill included'), node('p', displayText, 'ai-answer'));
  output.append(node('p', '이 장착·제거 이력은 시연용 가상 운영 기록입니다. 장착 이력 확인은 결함·고장 확인이 아니며 조건상 제외도 안전 판정이 아닙니다.', 'hint'));
  output.append(node('p', data.runtime.model + ' · 실행 ' + data.runtime.run_id, 'ai-answer-meta'));
  output.append(node('p', '이 요청의 실제 모델 연결과 도구 결과 일치를 확인했습니다. 범용 대화·해석 품질 전체의 완료 판정은 아닙니다.', 'hint'));
  output.append(jsonDetails('사용한 조건·버전·실행 기록', {conditions: data.conditions, consistency: data.consistency, tool_run_ids: data.runtime.tool_run_ids, provider_calls: data.provider_calls}));
  for (const mode of ['A', 'B']) {
    const result = data.results?.[mode];
    if (result?.evidence?.length) output.append(evidenceButton(result.evidence, result.data_version));
  }
}
async function route() {
  const [view, raw] = location.hash.slice(1).split('?');
  selectView(view || 'home', false);
  if (view === 'home' || (!view && currentView === 'home')) await refreshWorkspace();
  if (view === 'product' && state.sourceReceipt) renderReceipt();
  if (view === 'history' && state.historyReceipt) renderHistoryReceipt();
  if (view === 'review' && raw) {
    const params = new URLSearchParams(raw), id = params.get('draft'), version = Number(params.get('version'));
    if (id && Number.isInteger(version) && version > 0) { $('draft-id').value = id; $('draft-version').value = version; await loadDraft({id, version}); }
  }
}
function currentDevelopmentPreset() {
  return context?.development_users?.find((user) => user.subject === context.identity.subject);
}
function renderDevelopmentIdentity() {
  $('identity').textContent = '서버 확인 개발 신원: ' + context.identity.subject + ' · ' + context.identity.actor;
  const permissionLabels = {read: '조회', draft: '초안 작성', review: '검토·승인', audit: '실행 기록 조회'};
  const permissions = Array.isArray(context.identity.permissions) ? context.identity.permissions : [];
  $('identity-permissions').textContent = '서버 권한: ' + (permissions.length ? permissions.map((permission) => permissionLabels[permission] || permission).join(' · ') : '반환된 권한 정보 없음') + ' · 실제 인증 미완료';
  const selector = clear('dev-user-select');
  for (const user of context.development_users || []) {
    const option = node('option', user.label); option.value = user.id; selector.append(option);
  }
  const current = currentDevelopmentPreset();
  if (current) selector.value = current.id;
  else { const unknown = node('option', '서버 사용자 선택 정보 없음'); unknown.value = ''; unknown.selected = true; selector.prepend(unknown); }
}
async function switchDevelopmentUser(id) {
  const current = currentDevelopmentPreset();
  const target = context?.development_users?.find((user) => user.id === id);
  if (!current || !target) throw new Error('서버가 제공한 개발 사용자만 선택할 수 있습니다. 화면을 새로고침하세요.');
  if (target.id === current.id) return;
  // Save the old person's workflow under the old actor key before switching.
  persist();
  try {
    const response = must(await api('/api/development-user', {user: target.id}));
    if (response.data?.identity?.subject !== target.subject || !response.data?.csrf_token) throw new Error('사용자 변경 응답을 확인할 수 없습니다. 새로고침해 서버의 현재 신원을 확인하세요.');
    developmentSwitchPending = true;
    clear('ai-output');
    document.querySelectorAll('.view').forEach((view) => { view.hidden = true; });
    message(target.label + '로 개발 사용자를 변경했습니다. 서버의 신원·권한과 해당 사용자의 화면을 다시 불러옵니다.');
    history.replaceState(null, '', '/#home');
    location.reload();
  } catch (error) {
    $('dev-user-select').value = current.id;
    // A lost response can still have changed the server cookie. No old-person
    // action is safe until the browser has read the current server session.
    developmentSessionNeedsReload = true;
    throw error;
  }
}
async function initialize() {
  const result = must(await api('/api/context'));
  context = result.data;
  context.initial_data_version = result.data_version;
  if (context.session_reset) history.replaceState(null, '', '/#home');
  renderDevelopmentIdentity();
  updateSearchMode();
  $('current-version').textContent = '연결 시 데이터 v' + result.data_version;
  $('development-notice').textContent = context.development_notice || '로그인 없는 개발 신원입니다. 실제 사용자 인증 완료를 의미하지 않습니다. 보드·로트·장착/제거 기록은 시연용 가상 데이터입니다.';
  $('model-status').textContent = context.model?.configured ? '고정 모델 설정 있음 · 실제 연결은 요청 실행 후 확인합니다. ' + (context.model.model || '') : '실제 모델 미설정 · 공통 도구 조회는 사용할 수 있습니다.';
  if (context.model?.configured && !context.model?.cost_policy_configured) $('model-status').textContent += ' · 비용 정책 미설정: 유료 호출은 차단됩니다.';
  storageKey = 'ontologics-s1:' + (context.database_schema || context.dataset || 'default') + ':' + context.identity.actor;
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey));
    if (stored && typeof stored === 'object') {
      state = {...state, ...stored, requestKeys: stored.requestKeys || {}};
      if (state.baseline?.version > result.data_version) { state.baseline = null; state.comparison = null; }
    }
  } catch { /* A missing or malformed local UI cache is not server data. */ }
  fillQueries(state.baseline?.queries || context.default_queries);
  if (state.draftRef) { $('draft-id').value = state.draftRef.id; $('draft-version').value = state.draftRef.version; }
  if (state.baseline) renderBaseline();
  if (state.comparison) renderComparison();
  updateControls();
  await route();
  if (context.session_reset) message('개발 세션을 새로 확인했습니다. 현재 서버가 제공한 사용자 ' + context.identity.subject + '의 업무 현황에서 시작합니다. 실제 로그인·인증 완료를 뜻하지 않습니다.');
}

$('dev-user-select').addEventListener('change', (event) => {
  if (isBusy || developmentSwitchPending || developmentSessionNeedsReload) { event.target.value = currentDevelopmentPreset()?.id || ''; return; }
  const id = event.target.value;
  run(() => switchDevelopmentUser(id));
});
$('navigation').addEventListener('click', (event) => { const button = event.target.closest('button[data-view]'); if (button) run(() => enterView(button.dataset.view)); });
document.querySelectorAll('[data-open-view]').forEach((button) => button.addEventListener('click', () => run(() => enterView(button.dataset.openView))));
$('query-form').addEventListener('submit', (event) => { event.preventDefault(); run(query, $('query-submit')); });
$('query-form').addEventListener('input', () => { $('query-note').textContent = state.baseline ? '입력 변경은 아직 조회·AI·비교 조건에 반영되지 않았습니다. 다시 조회하세요.' : 'A와 B는 동일 데이터 버전으로 조회합니다.'; });
$('open-supplement').addEventListener('click', () => $('supplement-card').scrollIntoView({behavior: 'smooth', block: 'start'}));
$('load-supplement').addEventListener('click', () => run(loadSupplement, $('load-supplement')));
$('supplement-form').addEventListener('submit', (event) => { event.preventDefault(); run(saveSupplement, $('save-supplement')); });
$('new-supplement').addEventListener('click', () => {
  state.draftCycle = crypto.randomUUID();
  state.draftRef = null;
  state.lastReview = null;
  loadedDraft = loadedValidation = null;
  loadedDraftReady = false;
  invalidateComparison();
  $('draft-id').value = '';
  $('draft-version').value = '1';
  $('review-card').hidden = true;
  $('draft-created').hidden = true;
  clear('review-outcome');
  $('revise-draft').checked = false;
  persist(); updateControls(); updateContext();
  message('새 보완안 작성으로 전환했습니다. 기존 초안은 유지됩니다. 저장 전 입력과 근거를 다시 확인하세요.');
});
['removed-at', 'coverage-start', 'coverage-end'].forEach((id) => $(id).addEventListener('input', checkSupplementMatch));
$('draft-load-form').addEventListener('submit', (event) => { event.preventDefault(); run(() => loadDraft({id: $('draft-id').value.trim(), version: Number($('draft-version').value)}), $('load-draft')); });
['draft-id', 'draft-version'].forEach((id) => $(id).addEventListener('input', updateControls));
$('approve-draft').addEventListener('click', () => run(() => review('approve'), $('approve-draft')));
$('request-changes').addEventListener('click', () => run(() => review('request_changes'), $('request-changes')));
$('reject-draft').addEventListener('click', () => run(() => review('reject'), $('reject-draft')));
$('retry-review').addEventListener('click', () => run(() => review(null, true), $('retry-review')));
$('compare-results').addEventListener('click', () => run(compareResults, $('compare-results')));
$('go-compare').addEventListener('click', () => run(async () => { const configuration = configurationFromDraft(loadedDraft); if (configuration) { selectView('product'); $('configuration-id').value = configuration; if (loadedDraft.status !== 'applied') throw new Error('이 등록안의 정확한 버전이 아직 반영되지 않았습니다.'); await loadConfiguration({configuration_id: configuration, version: loadedDraft.applied_version}); $('configuration-result').scrollIntoView({behavior: 'smooth', block: 'start'}); } else { selectView('investigation'); $('comparison-card').scrollIntoView({behavior: 'smooth', block: 'start'}); } }, $('go-compare')));
$('ai-form').addEventListener('submit', (event) => { event.preventDefault(); run(askContextAI, $('ask-ai')); });
$('prepare-ai-draft').addEventListener('click', () => run(prepareAIDraft, $('prepare-ai-draft')));
$('explain-results').addEventListener('click', () => { $('ai-question').focus(); document.querySelector('.ai-panel').scrollIntoView({behavior: 'smooth', block: 'start'}); });
$('close-evidence').addEventListener('click', () => $('evidence-dialog').close());
$('copy-review-path').addEventListener('click', () => run(async () => { try { await navigator.clipboard.writeText($('review-path').value); message('검토 경로를 복사했습니다. Bob 등 다른 개발 사용자를 선택한 뒤 같은 서비스 주소에서 열거나 업무 현황의 같은 초안을 선택하세요.', 'success'); } catch { $('review-path').select(); message('자동 복사를 사용할 수 없습니다. 선택된 검토 경로를 직접 복사하세요.'); } }, $('copy-review-path')));
$('refresh-workspace').addEventListener('click', () => run(refreshWorkspace, $('refresh-workspace')));
$('source-intake-form').addEventListener('submit', (event) => { event.preventDefault(); run(intakeSource, $('source-intake')); });
$('unknown-only').addEventListener('change', renderSourceRows);
$('source-compare-form').addEventListener('submit', (event) => { event.preventDefault(); run(compareSource, $('source-compare')); });
$('source-save-draft').addEventListener('click', () => run(saveSourceDraft, $('source-save-draft')));
$('source-request-review').addEventListener('click', () => run(requestSourceReview, $('source-request-review')));
$('source-revise').addEventListener('change', () => { state.productSaveRequest = null; persist(); });
$('source-new-draft').addEventListener('click', () => { state.productCycle = crypto.randomUUID(); state.productSaveRequest = null; state.productDraft = null; state.activeProductTarget = null; $('source-revise').checked = false; $('source-reviewed').checked = false; clear('source-draft-result'); persist(); updateControls(); updateAIContext(); message('이 접수 원본으로 별도의 새 등록안을 시작합니다. 기존 초안·검토 기록은 유지됩니다.'); });
$('configuration-form').addEventListener('submit', (event) => { event.preventDefault(); run(() => loadConfiguration(), $('configuration-load')); });
$('history-form').addEventListener('submit', (event) => { event.preventDefault(); run(loadHistory, $('history-load')); });
$('history-intake-form').addEventListener('submit', (event) => { event.preventDefault(); run(intakeHistory, $('history-intake')); });
$('history-save-draft').addEventListener('click', () => run(saveHistoryDraft, $('history-save-draft')));
$('history-request-review').addEventListener('click', () => run(async () => { if (!state.historyDraft) throw new Error('보완 초안을 먼저 저장하세요.'); selectView('review'); await loadDraft(state.historyDraft); }, $('history-request-review')));
$('history-source-revise').addEventListener('change', () => { state.historySaveRequest = null; persist(); });
for (const id of ['evidence-search-query', 'evidence-search-mode', 'evidence-origin', 'evidence-version']) $(id).addEventListener('input', () => { state.knowledgeContext = null; state.evidenceSearchVersion = null; persist(); updateSearchMode(); updateAIContext(); });
$('evidence-search-form').addEventListener('submit', (event) => { event.preventDefault(); run(searchEvidence, $('evidence-search')); });
window.addEventListener('hashchange', () => run(route));
run(initialize);
