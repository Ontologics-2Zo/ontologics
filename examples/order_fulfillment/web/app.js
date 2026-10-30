'use strict';

const $ = (id) => document.getElementById(id);
const state = {catalog: null, selected: null, run: null, busy: false, timer: null, failures: 0};
const stages = [['cq01', '계약 접수·약속'], ['cq02', '물량 현황'], ['cq03', '생산 완료 예상'], ['cq04', '납기 영향'], ['cq05', '대응 초안'], ['cq06', '변경 전후 비교']];
const number = new Intl.NumberFormat('ko-KR', {maximumFractionDigits: 4});
const json = (value) => JSON.stringify(value, null, 2);
const storageKey = 'ontologics-b-last-run';

function rememberRun(id) {
  try { if (id) localStorage.setItem(storageKey, id); else localStorage.removeItem(storageKey); } catch { /* Storage may be disabled; execution still works. */ }
}

function rememberedRun() {
  try { return localStorage.getItem(storageKey); } catch { return null; }
}

function linkRun(id) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('run', id); else url.searchParams.delete('run');
  window.history.replaceState(null, '', url);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

async function request(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  let value;
  try { value = await response.json(); } catch { throw new Error('서버 응답을 읽을 수 없습니다. 서버 상태를 확인하세요.'); }
  if (!response.ok) {
    const error = value.error;
    const failure = new Error(typeof error === 'string' ? error : (error && (error.message || error.code)) || `요청 실패 (${response.status})`);
    failure.status = response.status;
    throw failure;
  }
  return value;
}

function showError(message) {
  $('error').textContent = message;
  $('error').hidden = !message;
}

function resultFor(id) { return state.run && state.run.results && state.run.results[id]; }
function caseStatus(id) {
  const saved = resultFor(id);
  if (saved) return saved.result && saved.result.runtime && saved.result.runtime.ok && !saved.result.error ? 'passed' : 'failed';
  if (state.run && state.run.status === 'running') {
    const next = state.run.cases.find((caseId) => !resultFor(caseId));
    if (id === next) return 'running';
  }
  return '';
}

function renderNavigation() {
  const cases = state.catalog.cases;
  $('case-list').replaceChildren();
  cases.forEach((item) => {
    const status = caseStatus(item.id);
    const button = element('button', `case-link ${state.selected === item.id ? 'selected' : ''} ${status}`);
    button.type = 'button';
    button.setAttribute('aria-current', state.selected === item.id ? 'true' : 'false');
    button.append(element('span', 'case-node'), element('span', '', item.label));
    if (status) button.append(element('span', 'case-state', {passed: '✓', failed: '!', running: '·'}[status]));
    button.addEventListener('click', () => selectCase(item.id));
    $('case-list').append(button);
  });
  $('flow').replaceChildren();
  stages.forEach(([code, title]) => {
    const related = cases.filter((item) => item.id.startsWith(code));
    const relevantRun = related.filter((item) => state.run && state.run.cases.includes(item.id));
    const done = relevantRun.length > 0 && relevantRun.every((item) => caseStatus(item.id) === 'passed');
    const active = state.selected && state.selected.startsWith(code);
    const button = element('button', `flow-step ${active ? 'active' : ''} ${done ? 'done' : ''}`);
    button.type = 'button';
    button.setAttribute('aria-label', `${code.toUpperCase()} ${title}`);
    button.setAttribute('aria-pressed', String(Boolean(active)));
    const top = element('div', 'flow-top');
    top.append(element('span', 'flow-code', code.toUpperCase()), element('span', 'flow-check', done ? '✓' : ''));
    button.append(top, element('div', 'flow-title', title));
    button.addEventListener('click', () => { if (related.length) selectCase(related[0].id); });
    $('flow').append(button);
  });
}

function selectCase(id) {
  const item = state.catalog.cases.find((row) => row.id === id);
  if (!item) return;
  state.selected = id;
  $('case-select').value = id;
  $('question').textContent = (item.input && item.input.question) || item.question || '입력의 결과와 근거를 확인합니다.';
  $('source-text').textContent = item.source_text || '이 사례는 구조화된 가상 입력을 사용합니다. 구조화 입력 탭에서 확인하세요.';
  $('input-json').textContent = json(item.input);
  $('input-role').textContent = typeof item.role === 'string' ? item.role : '고정 가상 사례';
  renderNavigation();
  renderResult();
}

function switchTab(name) {
  ['source', 'input'].forEach((key) => {
    const active = key === name;
    $(key + '-tab').classList.toggle('active', active);
    $(key + '-tab').setAttribute('aria-selected', String(active));
    $(key + '-tab').tabIndex = active ? 0 : -1;
    $(key + '-panel').hidden = !active;
  });
}

function detail(label, content, opened = false) {
  const node = element('details');
  node.open = opened;
  node.append(element('summary', '', label));
  if (content instanceof Node) node.append(content);
  else node.append(element('pre', 'json-view', json(content)));
  return node;
}

function metric(label, value, unit = '', time = false) {
  if (value === undefined) return;
  const node = element('div', 'metric' + (time ? ' time' : ''));
  const display = value === null ? '미확인' : typeof value === 'number' ? number.format(value) : String(value);
  const valueNode = element('div', 'metric-value', display);
  if (value !== null && unit) valueNode.append(element('span', 'metric-unit', unit));
  node.append(element('span', 'metric-label', label), valueNode);
  $('metrics').append(node);
}

function renderMetrics(payload) {
  $('metrics').replaceChildren();
  const assessment = payload.assessment || {};
  const forecast = assessment.forecast || assessment;
  const promise = payload.promise || {};
  if (payload.extraction) {
    const extraction = payload.extraction;
    const names = {ready: '검토 후보 준비', needs_confirmation: '추가 확인 필요', invalid: '입력 보완 필요'};
    metric('접수 상태', names[extraction.status] || extraction.status, '', true);
    metric('등록 후보', Array.isArray(extraction.nodes) ? extraction.nodes.length : undefined, '개');
  } else if (assessment.before && assessment.after) {
    const before = assessment.before.forecast || {};
    const after = assessment.after.forecast || {};
    metric('변경 전 · 기한 내 도착', before.on_time_quantity, '개');
    metric('변경 후 · 기한 내 도착', after.on_time_quantity, '개');
    metric('변경 전 · 부족 수량', before.shortfall_quantity, '개');
    metric('변경 후 · 부족 수량', after.shortfall_quantity, '개');
  } else if (forecast.on_time_quantity !== undefined) {
    metric('기한 내 도착 예상', forecast.on_time_quantity, '개');
    metric('부족 수량', forecast.shortfall_quantity, '개');
    metric('전량 도착 예상', forecast.estimated_full_arrival_at, '', true);
    if (assessment.dispatch_request) metric('새 출고 검토', assessment.dispatch_request.new_dispatch_quantity, '개');
    else metric('약속 수량', forecast.quantity, '개');
  } else if (assessment.estimated_completion_at !== undefined) {
    metric('생산 완료 예상', assessment.estimated_completion_at, '', true);
    metric('남은 정상 수량', assessment.remaining_quantity, '개');
    metric('정상 생산 속도', assessment.good_rate_per_hour, '개/시간');
    metric('최근 구간 불량률', typeof assessment.recent_defect_ratio === 'number' ? assessment.recent_defect_ratio * 100 : assessment.recent_defect_ratio, '%');
  } else if (promise.quantity !== undefined) {
    metric('약속 수량', promise.quantity, '개');
    metric('고객 도착 기한', promise.due_at, '', true);
    metric('고객', promise.customer_name, '', true);
    metric('제품 · 구성', [promise.product_name, promise.revision, promise.variant].filter(Boolean).join(' / '), '', true);
  } else {
    metric('회차 배정', assessment.allocated_quantity, '개');
    metric('남은 약속', assessment.remaining_quantity, '개');
    metric('미배정', assessment.unallocated_quantity, '개');
    const labels = {complete: '현황 확인 완료', needs_confirmation: '추가 확인 필요', forecast: '조건부 예상'};
    metric('확인 상태', labels[assessment.status] || assessment.status, '', true);
  }
  $('metrics').hidden = $('metrics').children.length === 0;
}

function renderResult() {
  const saved = resultFor(state.selected);
  const result = saved && saved.result || {};
  const hasPayload = Boolean(result.payload);
  const payload = result.payload || {};
  const badge = $('result-badge');
  const status = caseStatus(state.selected);
  badge.className = 'small-badge' + (status ? ' ' + {passed: 'success', failed: 'failure', running: 'running'}[status] : '');
  badge.textContent = {passed: '자동 검사 통과', failed: '실행 실패', running: '실행 중'}[status] || '실행 대기';
  $('result-empty').hidden = Boolean(saved);
  $('result-content').hidden = !saved;
  $('metrics').replaceChildren();
  $('answer').textContent = '';
  $('result-details').replaceChildren();
  if (!saved) {
    return;
  }
  if (result.error) showError('선택한 사례 실행이 거절되었습니다. ' + (typeof result.error === 'string' ? result.error : json(result.error)));
  const actual = saved.execution_mode === 'openai' || saved.execution_mode === 'actual_paid';
  const attempts = saved.actual_api_attempts !== undefined ? saved.actual_api_attempts : (saved.provider_calls || []).length;
  $('result-kind').textContent = (actual ? '실제 LLM 응답' : '무료 모의 응답 · 실제 LLM 품질 검증 아님') + ` · 실제 API 요청 ${attempts}회 · 제품 데이터 v${payload.data_version ?? result.context?.data_version ?? '미확인'}`;
  $('answer').textContent = payload.answer_ko || (hasPayload ? '한국어 답변이 없습니다. 반환 데이터와 도구 기록을 확인하세요.' : '자동 검증이 거절되어 업무 답변을 확정하지 않았습니다. 아래 모델 원문과 도구 기록에서 실패 원인을 확인하세요.');
  renderMetrics(payload);
  const details = $('result-details');
  details.replaceChildren();
  const evidence = payload.evidence_ids || result.evidence_refs || [];
  const list = element('ul', 'evidence-list');
  evidence.forEach((value) => list.append(element('li', '', typeof value === 'string' ? value : json(value))));
  if (!evidence.length) list.append(element('li', '', '반환된 제품 근거가 없습니다.'));
  details.append(detail(`제품 조회 근거 · ${evidence.length}개`, list));
  const sources = payload.input_sources || payload.input_sources_by_side;
  if (sources) details.append(detail('가상 운영 입력 · 원본과 해시', sources));
  const trace = (result.runtime && result.runtime.tool_trace) || result.tool_trace || [];
  const traceList = element('ol', 'trace-list');
  trace.forEach((entry, index) => {
    const item = element('li');
    const body = element('div');
    body.append(element('strong', '', `${String(index + 1).padStart(2, '0')} · ${entry.name || entry.tool || '도구 실행'}`));
    const returned = entry.result || {};
    const args = entry.arguments || entry.args || {};
    body.append(element('small', '', `${args.node_id || ''}${args.node_id ? ' · ' : ''}${returned.ok === false ? '실패' : returned.ok === true ? '정상 반환' : '기록 확인'}${returned.data_version !== undefined ? ` · v${returned.data_version}` : ''}`));
    item.append(body); traceList.append(item);
  });
  if (!trace.length) traceList.append(element('li', '', '도구 기록이 없습니다.'));
  details.append(detail(`도구 실행 기록 · ${trace.length}회`, traceList));
  const assessment = payload.assessment || payload.promise || payload.extraction;
  if (assessment) details.append(detail('계산·판단 상세', assessment));
  if (actual) {
    const calls = saved.provider_calls || [];
    const raw = element('div');
    calls.forEach((call, index) => {
      const response = element('div');
      response.append(element('p', 'question-label', `요청 ${index + 1}${call.http_status !== undefined ? ' · HTTP ' + call.http_status : ''}`));
      response.append(element('pre', 'json-view', call.model_text || '이 요청에는 모델의 최종 텍스트가 없습니다. 전체 요청 기록을 확인하세요.'));
      raw.append(response);
    });
    if (!calls.length) raw.append(element('pre', 'json-view', '보존된 모델 응답 기록이 없습니다.'));
    details.append(detail('실제 LLM 모델 원문 · 검증 전 응답', raw, !hasPayload));
  }
  const budget = result.request_execution_budget || saved.request_execution_budget;
  if (budget && budget.cost) details.append(detail('이 결과 시점의 비용·예약 기록', budget.cost));
  if (result.error) details.append(detail('검증 오류 상세', result.error, true));
  details.append(detail('전체 반환 JSON', saved));
}

function refreshControls() {
  const mode = $('execution-mode').value;
  const live = state.catalog && state.catalog.live;
  const ready = Boolean(state.catalog) && (mode !== 'openai' || live && live.ready);
  $('run-selected').disabled = state.busy || !ready;
  $('run-all').disabled = state.busy || !ready;
  $('execution-mode').disabled = state.busy;
  $('download').disabled = !state.run;
  if (!state.catalog) return;
  const actual = mode === 'openai';
  $('notice-title').textContent = actual ? '실제 LLM 실행 모드' : '무료 모의 실행 모드';
  $('notice-text').textContent = actual ? '실행 버튼을 누르면 기존 모델과 공통 비용 정책으로 실제 API를 호출합니다. 가상 자료와 미승인 초안을 사용합니다.' : (state.catalog.notice || '기존 공통 호스트와 B 역할의 연결을 확인합니다. 실제 LLM 답변 품질과 업무 승인은 별도 검증 대상입니다.');
  $('notice-tag').textContent = actual ? '유료 API' : 'API 비용 0';
  $('sidebar-mode').textContent = actual ? '가상 자료 · 실제 LLM 실행' : '가상 자료 · 무료 모의 실행';
  $('notice').classList.toggle('live-mode', actual);
  const cap = live && live.total_max_cost_usd;
  const representativeCap = live && live.representative_max_cost_usd;
  $('mode-detail').textContent = actual ? (live && live.ready ? `${live.model || '설정 모델'} · 실행 전체 최대 $${cap || '미설정'} · 대표당 최대 $${representativeCap || '미설정'}. 실행 시 비용이 발생합니다.` : '실제 LLM 실행 준비 필요: ' + ((live && live.issues || ['서버의 모델·비용 정책 설정을 확인하세요.']).join(' · '))) : '기존 실행·계산·근거 연결을 무료로 확인합니다.';
}

function renderProgress() {
  if (!state.run) return;
  const run = state.run;
  const completed = run.cases.filter((id) => Boolean(resultFor(id))).length;
  $('run-progress').hidden = false;
  $('progress-count').textContent = `${completed} / ${run.cases.length}`;
  $('progress-bar').style.width = `${run.cases.length ? completed / run.cases.length * 100 : 0}%`;
  const labels = {running: '실행 중 · 결과를 확인하고 있습니다', passed: '선택한 사례 실행 완료', failed: '실행 중단 · 오류를 확인하세요'};
  $('run-status').textContent = labels[run.status] || '실행 상태 확인 중';
  const stopped = run.shutdown && run.shutdown.server_stopped;
  const summary = run.summary || {};
  const attempts = run.actual_api_attempts ?? summary.actual_api_attempts;
  const budget = summary.request_execution_budget || (run.stopped && run.stopped.request_execution_budget);
  const cost = budget && budget.cost;
  const reportedCost = cost && cost.reported_cost_usd;
  $('run-detail').textContent = `실행 ${run.id}${attempts !== undefined ? ' · 실제 API ' + attempts + '회' : ''}${run.mode === 'openai' && reportedCost !== undefined ? ' · 실행 누적 추정 비용 $' + reportedCost : ''}${stopped === true ? ' · 격리 DB 서버 종료 확인' : run.status === 'running' ? ' · 개별 사례는 독립 입력입니다' : ' · 서버 종료 기록 확인 필요'}`;
  $('footer-status').textContent = run.status === 'running' ? `실행 진행 중 · ${completed}/${run.cases.length}` : run.status === 'passed' ? `자동 검사 완료 · ${completed}개 사례` : '실행 중단 · 오류 기록 확인';
  const error = run.error;
  if (error) showError(typeof error === 'string' ? error : [error.message, error.case, error.error_type].filter(Boolean).join(' · '));
  renderNavigation();
  renderResult();
  refreshControls();
}

async function pollRun() {
  if (!state.run || !state.busy) return;
  try {
    state.run = await request('/api/runs/' + encodeURIComponent(state.run.id));
    state.failures = 0;
    showError('');
    state.busy = state.run.status === 'running';
    renderProgress();
  } catch (error) {
    state.failures += 1;
    showError(`실행 상태를 가져오지 못했습니다. ${error.message} (자동 재확인 중)`);
    $('footer-status').textContent = '서버 응답 확인 중';
  }
  if (state.busy) state.timer = window.setTimeout(pollRun, state.failures ? 3000 : 1000);
}

async function startRun(all) {
  if (state.busy || !state.catalog) return;
  showError('');
  state.busy = true;
  refreshControls();
  const cases = all ? state.catalog.cases.map((item) => item.id) : [state.selected];
  try {
    const started = await request('/api/runs', {method: 'POST', headers: {'X-Ontol-Token': state.catalog.csrf_token}, body: JSON.stringify({cases, mode: $('execution-mode').value})});
    state.run = {status: 'running', cases, results: {}, ...started};
    rememberRun(state.run.id);
    linkRun(state.run.id);
    $('download').disabled = false;
    renderProgress();
    await pollRun();
  } catch (error) {
    state.busy = false;
    showError(error.message);
    $('footer-status').textContent = '실행을 시작하지 못했습니다';
    refreshControls();
  }
}

$('source-tab').addEventListener('click', () => switchTab('source'));
$('input-tab').addEventListener('click', () => switchTab('input'));
['source-tab', 'input-tab'].forEach((id, index) => $(id).addEventListener('keydown', (event) => {
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault(); const next = index === 0 ? 'input' : 'source'; switchTab(next); $(next + '-tab').focus();
  }
}));
$('case-select').addEventListener('change', (event) => selectCase(event.target.value));
$('execution-mode').addEventListener('change', refreshControls);
$('run-selected').addEventListener('click', () => startRun(false));
$('run-all').addEventListener('click', () => startRun(true));
$('download').addEventListener('click', () => {
  if (!state.run) return;
  const url = URL.createObjectURL(new Blob([json(state.run)], {type: 'application/json;charset=utf-8'}));
  const anchor = element('a'); anchor.href = url; anchor.download = `ontologics-b-${state.run.id}.json`;
  document.body.append(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function init() {
  try {
    const catalog = await request('/api/catalog');
    if (!Array.isArray(catalog.cases) || !catalog.cases.length) throw new Error('실행할 사례가 없습니다.');
    state.catalog = catalog;
    $('case-count').textContent = String(catalog.cases.length);
    document.querySelector('.button-count').textContent = String(catalog.cases.length);
    $('case-select').replaceChildren(...catalog.cases.map((item) => { const option = element('option', '', item.label); option.value = item.id; return option; }));
    $('case-select').disabled = false;
    selectCase(catalog.cases[0].id);
    switchTab('source');
    refreshControls();
    $('footer-status').textContent = `준비 완료 · ${catalog.cases.length}개 대표 사례`;
    const prior = new URLSearchParams(window.location.search).get('run') || rememberedRun();
    if (prior) {
      state.busy = true;
      refreshControls();
      try {
        state.run = await request('/api/runs/' + encodeURIComponent(prior));
        state.busy = state.run.status === 'running';
        rememberRun(state.run.id);
        linkRun(state.run.id);
        $('execution-mode').value = state.run.mode === 'openai' ? 'openai' : 'scripted';
        if (state.run.cases && state.run.cases.length) selectCase(state.run.cases[0]);
        renderProgress();
        if (state.busy) state.timer = window.setTimeout(pollRun, 1000);
      } catch (error) {
        state.busy = false;
        if (error.status === 404) { rememberRun(null); linkRun(null); }
        else showError('이전 실행 상태를 불러오지 못했습니다. ' + error.message);
        refreshControls();
      }
    }
  } catch (error) {
    showError(error.message);
    $('question').textContent = '사례를 불러오지 못했습니다.';
    $('source-text').textContent = '서버를 확인한 후 페이지를 새로고침하세요.';
    $('footer-status').textContent = '서버 연결 확인 필요';
  }
}
init();
