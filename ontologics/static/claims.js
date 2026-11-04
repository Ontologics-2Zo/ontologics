'use strict';
// C claim response on the common screen: seven step tabs for one claim at a time. Each tab shows
// what the step works on (the claim file as written, the serial match, the request documents, the
// test team's file) and, after the step, what it put in the graph. Every step saves a draft that a
// different user approves. Nothing here confirms a defect, invents a test outcome or sends anything:
// the "send" buttons only mark on this screen that a person sent the document, as the documents say.
// Reads go through /api/claims/query (claim_response/workspace.py query), wired like B's query.

// Waiting claim files: the synthetic scenario inputs under examples/claim_response/scenario_inputs.
// Only the claims that run to a reproduction requirement are listed (scenarios 3·4·5 stop for a person
// and stay in the C scenario app and the tests). `serial` is the first reported board, used to tell
// which file a registered claim came from; `result` is that claim's test team file.
const CLAIM_INBOX = [
  {file: '1_메일_전수시험_클레임.eml', kind: '메일', note: 'PoE 연결 후 이더넷 링크 LED 미점등 · 3대', serial: 'C-D021', result: '1_테스트팀_결과.csv'},
  {file: '2_엑셀_표본시험_클레임.xlsx', kind: '엑셀', note: '장시간 가동 후 재부팅 반복 · 4대', serial: 'C-D040', result: '2_테스트팀_결과.csv'},
];
const CLAIM_STEPS = ['접수', '사건 연결', '중단 요청', '시험 의뢰', '결과', '재생산', 'A 인계'];
const CLAIM_HINTS = [
  '대기 중인 클레임 파일입니다. 파일 이름을 누르면 원문과 변환 결과를 볼 수 있습니다. 필수 항목이 없으면 접수되지 않습니다.',
  '신고 일련번호를 등록된 보드와 하나씩 대조합니다. 맞지 않는 번호가 있으면 사건을 만들지 않고 사람이 확인하라고 멈춥니다.',
  '같은 생산라인의 보관·납품 범위를 추려 중단 요청서를 만듭니다. 요청서를 만드는 것은 불량 확정이 아닙니다.',
  '보류한 보드를 시험 대상으로 하는 의뢰서를 만듭니다. 시험 범위와 방식만 적고 결과는 테스트팀이 정합니다.',
  '테스트팀이 보낸 결과 파일을 고치지 않고 읽습니다. 한 번에 100건까지라 결과가 많으면 나눠 올립니다.',
  '테스트팀 판정을 세어 재생산 수량을 계산합니다. 결과가 엇갈리거나 미정이면 0으로 저장하지 않고 거부합니다.',
  '생산 담당(A)은 요청서를 받지 않고 공통 그래프에서 재생산 요건 기록을 직접 조회합니다.',
];
// Display names for stored codes. Outcome and disposition use the test team's own words
// (claim_response/reproduction.py OUTCOMES, DISPOSITIONS); an unknown code is shown as stored.
const CLAIM_LABELS = {
  needs_review: '확인 필요', confirmed: '확정', rejected: '반려', blocked: '막힘',
  hold: '보류', candidate: '후보', production_stop: '생산 중단', shipment_hold: '출고 보류',
  fail: '불합격', pass: '통과', not_reproduced: '증상 미재현',
  customer_replacement: '고객 교체', stock_replacement: '보관 대체', release: '출고 허용', unresolved: '미결',
  lower_bound: '하한', piece: '대', matched: '일치', not_found: '등록 안 됨', ambiguous: '여러 대 일치',
};
const claimLabel = code => CLAIM_LABELS[code] || code;
const claimNow = () => new Date(Date.now() + 32400000).toISOString().replace(/\.\d+Z$/, '+09:00');

let claimAutoApprove = false;  // demonstration shortcut, off until a person turns it on
let claimAttested = false;     // the person's own "synthetic record" confirmation, once per page
let claimTab = null;           // chosen tab; null follows the next step of the focused claim
let claimFocus = null;         // ClaimIntake id of the claim being worked on
let claimPendingSerial = null; // a file just submitted; focus its intake once it is in the graph
let claimRender = null;        // redraw of the mounted screen without reloading data
// Screen-only marks. The graph has no field for "sent" or "received", so these live in this page
// and are gone after a reload; the documents themselves say sending is a person's act.
const claimUi = {open: {}, sent: {}, arrival: {}, received: {}, docShown: {}, handedOff: {}};
const claimCache = new Map();

// --- Demonstration shortcut -------------------------------------------------
// When a person turns it on, saving a step also performs the reviewer's approval from this screen:
// it switches the development identity to the reviewer, approves, and switches back, which is what
// that person would otherwise do by hand. It is NOT two people approving: one operator drives both
// identities, and every approval says so on screen and in the stored review note.
async function claimSwitchUser(id) {
  const response = must(await api('/api/development-user', {user: id}));
  if (!response.data?.identity?.subject || !response.data?.csrf_token)
    throw new Error('사용자 변경 응답을 확인할 수 없습니다. 화면을 새로고침하세요.');
  context.identity = response.data.identity;
  context.csrf_token = response.data.csrf_token;
}

async function claimApproveDraft(draft) {
  const author = currentDevelopmentPreset();
  const reviewer = (context.development_users || []).find(v => v.id !== author?.id && v.id !== 'reader');
  if (!author || !reviewer) throw new Error('개발 신원이 아니어서 시연 승인을 쓸 수 없습니다.');
  const body = {draft_id: draft.draft_id, version: draft.draft_version, decision: 'approve',
    note: '시연용 자동 승인. 한 사람이 작성자와 검토자 신원을 모두 조작했습니다. 두 사람의 검토가 아닙니다.'};
  body.request_key = requestKey('review', body);
  let outcome;
  await claimSwitchUser(reviewer.id);
  try {
    outcome = must(await api('/api/review', body));
  } finally {
    try { await claimSwitchUser(author.id); }
    // The session is still the reviewer. No further action by this screen is safe.
    catch (error) { developmentSessionNeedsReload = true; throw error; }
  }
  const version = outcome.data?.application?.data_version;
  return {version, told: reviewer.label + '로 전환해 승인하고 ' + author.label + '로 돌아왔습니다. 반영 데이터 v'
    + version + '. 시연용 단축이며 두 사람의 승인이 아닙니다.'};
}

// The evidence the common draft builder requires. The text is this screen's description of the step;
// the synthetic confirmation is the person's tick at the top of the page.
function claimEvidenceForm(text, notice) {
  const form = node('form'); businessSourceFields(form);
  form.elements.source_text.value = text;
  form.elements.authored_at.value = claimNow();
  form.elements.notice.value = notice;
  form.elements.synthetic_confirmed.checked = claimAttested;
  return form;
}

const claimCode = error => error?.response?.errors?.[0]?.code;

function claimRequireAttested() {
  if (!claimAttested) throw new Error('맨 위 "시연용 가상 기록 확인"을 먼저 체크하세요.');
}

async function claimReload(tab) {
  claimTab = tab;
  await loadDashboard(); await openBusiness('claims');
}

// One step: save the draft; in demonstration mode also approve, then show the same tab with its result.
async function claimAct(output, action, payload, text, notice, tab) {
  claimRequireAttested();
  const response = await submitBusiness(claimEvidenceForm(text, notice), 'claims', action, payload, output);
  if (!claimAutoApprove) return;
  const {told} = await claimApproveDraft(response.data);
  await claimReload(tab);
  message(told, 'success');
}

// Test results arrive in parts of at most 100 records. In demonstration mode one press sends every
// part, approving each, until the server says the file is fully registered.
async function claimResults(output, payload, tab) {
  claimRequireAttested();
  const text = '테스트팀이 보낸 결과 파일을 그대로 읽었습니다.', notice = '시연용 가상 시험 결과. 실제 시험 성적이 아님.';
  if (!claimAutoApprove) {
    await submitBusiness(claimEvidenceForm(text, notice), 'claims', 'test_results', payload, output); return;
  }
  const parts = [];
  for (;;) {
    let response;
    try { response = await submitBusiness(claimEvidenceForm(text, notice), 'claims', 'test_results', payload, output); }
    catch (error) { if (claimCode(error) === 'TEST_RESULTS_COMPLETE' && parts.length) break; throw error; }
    const {version} = await claimApproveDraft(response.data);
    businessSelection.version = version;  // the next part builds on the records just applied
    parts.push(version);
  }
  await claimReload(tab);
  message('결과를 ' + parts.length + '번에 나눠 올리고 각각 검토자 신원으로 승인했습니다. 반영 데이터 v'
    + parts[parts.length - 1] + '. 시연용 단축이며 두 사람의 승인이 아닙니다.', 'success');
}

// --- Reads ------------------------------------------------------------------
function claimQuery(action, args) {
  const version = businessSelection.version, key = action + ':' + JSON.stringify(args) + '@' + version;
  if (!claimCache.has(key)) {
    const pending = api('/api/claims/query', {action, arguments: {...args, version}}).then(must).then(r => r.data);
    pending.catch(() => claimCache.delete(key));
    claimCache.set(key, pending);
  }
  return claimCache.get(key);
}

// Fill `target` once the read arrives; `draw` returns the node to show.
function claimLoad(target, action, args, draw) {
  const wait = node('p', '불러오는 중…', 'hint'); target.append(wait);
  claimQuery(action, args).then(data => wait.replaceWith(draw(data)))
    .catch(error => wait.replaceWith(node('p', error.message, 'message error')));
}

// --- Display helpers --------------------------------------------------------
function claimTable(headers, rows) {
  if (!rows.length) return node('p', '아직 없습니다.', 'hint');
  const wrap = node('div', null, 'table-wrap'), table = node('table'), head = node('tr'), body = node('tbody');
  for (const name of headers) head.append(node('th', name));
  for (const cells of rows) {
    const row = node('tr');
    for (const cell of cells) {
      const td = node('td');
      if (cell instanceof Node) td.append(cell); else td.textContent = cell === undefined || cell === null ? '' : String(cell);
      row.append(td);
    }
    body.append(row);
  }
  table.append(head, body); wrap.append(table); return wrap;
}

function claimFacts(pairs) {
  const list = node('dl', null, 'metadata');
  for (const [name, value] of pairs) {
    const item = node('div'), dd = node('dd');
    if (value instanceof Node) dd.append(value); else dd.textContent = value === undefined || value === null || value === '' ? '—' : String(value);
    item.append(node('dt', name), dd); list.append(item);
  }
  return list;
}

function claimSection(panel, title, ...content) {
  panel.append(node('h3', title), ...content);
}

function claimButton(label, action, primary = true) {
  const button = actionButton(label, action);
  if (primary) button.className = 'primary';
  return button;
}

function claimActions(...buttons) {
  const bar = node('div', null, 'actions'); bar.append(...buttons); return bar;
}

function claimGo(tab) { claimTab = tab; claimRender?.(); }

// "Done; next step" bar shown once a step's result is in the graph.
function claimNext(text, tab) {
  const bar = node('div', null, 'message success');
  bar.append(document.createTextNode(text + ' '));
  if (tab !== null) bar.append(claimButton('다음: ' + (tab + 1) + ' ' + CLAIM_STEPS[tab] + ' →', async () => claimGo(tab)));
  return bar;
}

// Bold and code inside a line, built as nodes; the text is never parsed as HTML.
function claimInline(parent, text) {
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (!part) continue;
    if (part.startsWith('**') && part.endsWith('**')) parent.append(node('strong', part.slice(2, -2)));
    else if (part.startsWith('`') && part.endsWith('`')) parent.append(node('code', part.slice(1, -1)));
    else parent.append(document.createTextNode(part));
  }
  return parent;
}

// The request documents are Markdown: headings, tables, lists and paragraphs. This draws just those.
function claimMarkdown(text) {
  const box = node('div', null, 'source-box'), lines = text.split('\n');
  const cells = row => row.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) { box.append(claimInline(node(['h2', 'h3', 'h4', 'h4'][heading[1].length - 1]), heading[2])); i++; continue; }
    if (line.trim().startsWith('|')) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(lines[i].trim()); i++; }
      const body = rows.filter(row => !/^\|[\s:|-]+\|$/.test(row));
      const table = node('table'), head = node('tr'), tbody = node('tbody');
      for (const cell of cells(body[0])) head.append(claimInline(node('th'), cell));
      for (const row of body.slice(1)) {
        const tr = node('tr'); for (const cell of cells(row)) tr.append(claimInline(node('td'), cell)); tbody.append(tr);
      }
      table.append(head, tbody); box.append(table); continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const list = node('ul');
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { list.append(claimInline(node('li'), lines[i].replace(/^\s*[-*]\s+/, ''))); i++; }
      box.append(list); continue;
    }
    const paragraph = node('p');
    let first = true;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*[-*]\s)/.test(lines[i])) {
      if (!first) paragraph.append(node('br'));
      claimInline(paragraph, lines[i].trim()); first = false; i++;
    }
    box.append(paragraph);
  }
  return box;
}

function claimDocuments(documents) {
  const wrap = node('div');
  for (const doc of documents) {
    const box = node('details'); box.open = true;
    box.append(node('summary', doc.markdown.split('\n')[0].replace(/^#+\s*/, '') + ' · ' + doc.filename), claimMarkdown(doc.markdown));
    wrap.append(box);
  }
  return wrap;
}

function claimModal(title, lines, actionLabel, onAction) {
  const dialog = node('dialog'), heading = node('div', null, 'dialog-heading');
  heading.append(node('h2', title)); dialog.append(heading);
  for (const line of lines) dialog.append(node('p', line));
  const close = () => { dialog.close(); dialog.remove(); };
  dialog.append(claimActions(claimButton(actionLabel, async () => { close(); onAction(); }),
    claimButton('나중에', async () => close(), false)));
  document.body.append(dialog); dialog.showModal();
}

function claimToggle(container, label, value, onChange) {
  const wrapper = node('label', label, 'check'), input = node('input');
  input.type = 'checkbox'; input.checked = value;
  input.addEventListener('change', () => onChange(input.checked));
  wrapper.prepend(input); container.append(wrapper); return input;
}

// The claim file as the customer sent it, beside what C read from it.
function claimFileView(data) {
  const wrap = node('div', null, 'form-grid'), original = node('div'), converted = node('div');
  original.append(node('h4', '원문 (' + data.file + ')'));
  if (data.original.kind === 'mail') original.append(claimFacts(data.original.headers), node('pre', data.original.text));
  else original.append(claimTable(data.original.columns, data.original.rows));
  const claim = data.claim;
  converted.append(node('h4', '변환 결과 · ' + claimLabel(data.state)), claimFacts([
    ['고객 클레임 번호', claim.external_claim_id], ['고객', claim.customer_name], ['신고 시각', claim.reported_at],
    ['증상', claim.reported_symptom], ['발생 조건', claim.reported_conditions], ['신고 수량', data.claimed_quantity],
    ['신고 보드', data.serials.join(', ')], ['납품 문서', claim.delivery_reference],
    ['요청 사항', claim.requested_action], ['희망 기한', claim.requested_due_date]]));
  if (data.issues.length) converted.append(claimTable(['문제', '내용'], data.issues.map(v => [v.code, v.message])));
  wrap.append(original, converted); return wrap;
}

async function mountClaims(container) {
  container.append(node('h2', '클레임 대응'),
    node('p', '고객 신고 접수부터 재생산 요건까지 한 흐름입니다. 각 단계는 초안으로 저장되고 별도 검토자가 승인해야 반영됩니다. 신고는 불량 확정이 아니고, 시험 결과는 테스트팀 파일을 그대로 읽습니다.', 'hint'));
  const controls = node('div');
  claimToggle(controls, ' 시연 모드 · 저장하면 검토자 신원으로 전환해 자동 승인합니다. 두 사람의 승인이 아닙니다.',
    claimAutoApprove, value => run(async () => {
      claimAutoApprove = value; await openBusiness('claims');
      message(value ? '시연 모드를 켰습니다. 각 단계가 저장 후 검토자 신원으로 자동 승인됩니다.'
        : '시연 모드를 껐습니다. 각 단계는 초안만 저장하고 별도 검토자가 승인해야 반영됩니다.');
    }));
  claimToggle(controls, ' 시연용 가상 기록 확인 · 이 화면에서 저장하는 고객·시험·출하 기록이 실제 사실이 아닌 시연용 가상 기록임을 확인합니다.',
    claimAttested, value => { claimAttested = value; });
  container.append(controls);

  const version = businessSelection.version;
  const kinds = ['ClaimIntake', 'QualityCase', 'QualityScope', 'ContainmentRequest', 'TestRequest',
    'QualityTestResult', 'ReproductionRequirement', 'ProductUnit'];
  const loaded = await Promise.all(kinds.map(kind => tool('lookup', {kind, version})));
  const [intakes, cases, scopes, containments, tests, results, repros, units] = loaded.map(v => v.data);
  const caseFor = intake => cases.find(c => c.intake_id === intake?.id) || null;
  const mine = (rows, c) => c ? rows.filter(v => v.case_id === c.id) : [];

  // The claim being worked on: the one just submitted, else the one chosen, else the latest unfinished.
  if (claimPendingSerial) {
    const hit = intakes.find(v => v.reported_serials?.includes(claimPendingSerial));
    if (hit) claimFocus = hit.id;
    claimPendingSerial = null;
  }
  if (!intakes.some(v => v.id === claimFocus))
    claimFocus = ([...intakes].reverse().find(v => !mine(repros, caseFor(v)).length) || intakes[intakes.length - 1])?.id ?? null;
  const I = intakes.find(v => v.id === claimFocus) || null, C = caseFor(I);
  const R = mine(repros, C)[0] || null;
  const inbox = CLAIM_INBOX.find(v => I?.reported_serials?.includes(v.serial));
  // Recomputed on every redraw: the hand-off mark is screen state that changes without a reload.
  const progressNow = () => [!!I, !!C, mine(containments, C).length > 0, mine(tests, C).length > 0,
    mine(results, C).length > 0, !!R, !!(C && claimUi.handedOff[C.id])];
  const nextOf = progress => progress.indexOf(false) === -1 ? 6 : progress.indexOf(false);
  const output = node('div'); output.id = 'business-feedback';
  const claimTitle = I ? I.external_claim_id + ' · ' + I.customer_name : '';

  if (intakes.length > 1) {
    const choose = node('div', null, 'actions');
    const select = businessField(choose, 'claim_focus', '진행 중인 클레임',
      {options: intakes.map(v => ({value: v.id, label: v.external_claim_id + ' · ' + v.customer_name})), value: claimFocus});
    select.addEventListener('change', () => { claimFocus = select.value; claimTab = null; claimRender(); });
    container.append(choose);
  }

  // Step tabs stay at the top of the card while the panel scrolls; styled here, not in the common CSS.
  const tabs = node('div'), panel = node('div');
  Object.assign(tabs.style, {display: 'flex', flexWrap: 'wrap', gap: '6px', margin: '16px 0 4px', padding: '8px 0',
    borderBottom: '1px solid var(--line)', position: 'sticky', top: '0', background: 'var(--paper)', zIndex: '2'});
  const needClaim = () => panel.append(node('p', '먼저 1단계에서 클레임을 접수하세요.', 'message'));
  const needCase = () => panel.append(node('p', '먼저 2단계에서 품질 사건을 연결하세요.', 'message'));

  // 1. Intake: the waiting files, each opening to its original and its conversion.
  const intakeTab = () => {
    const wrap = node('div', null, 'table-wrap'), table = node('table'), head = node('tr'), body = node('tbody');
    for (const name of ['파일', '형식', '내용', '상태', '']) head.append(node('th', name));
    for (const item of CLAIM_INBOX) {
      const registered = intakes.find(v => v.reported_serials?.includes(item.serial));
      const open = !!claimUi.open[item.file], row = node('tr');
      const toggle = claimButton((open ? '▾ ' : '▸ ') + item.file, async () => { claimUi.open[item.file] = !open; claimRender(); }, false);
      const act = registered
        ? claimButton('이 클레임으로 진행', async () => { claimFocus = registered.id; claimGo(null); }, false)
        : claimButton('접수', () => { claimPendingSerial = item.serial; return claimAct(output, 'intake_file',
          {file: item.file, collected_at: claimNow()}, '시연용 가상 고객 클레임 원문 파일을 그대로 변환했습니다.',
          '시연용 가상 클레임. 실제 고객 신고가 아님.', 0); });
      for (const cell of [toggle, item.kind, item.note, registered ? '접수됨' : '대기', act]) {
        const td = node('td'); if (cell instanceof Node) td.append(cell); else td.textContent = cell; row.append(td);
      }
      body.append(row);
      if (open) {
        const detail = node('tr'), td = node('td'); td.colSpan = 5;
        claimLoad(td, 'claim_file', {file: item.file}, claimFileView);
        detail.append(td); body.append(detail);
      }
    }
    table.append(head, body); wrap.append(table);
    claimSection(panel, '대기 중인 클레임', wrap);
    claimSection(panel, '접수된 클레임', claimTable(['고객 클레임 번호', '고객', '신고 시각', '증상', '수량', '상태'],
      intakes.map(v => [v.external_claim_id, v.customer_name, v.reported_at, v.reported_symptom, v.claimed_quantity, claimLabel(v.intake_state)])));
    if (I) panel.append(claimNext(claimTitle + ' 접수가 그래프에 반영됐습니다.', 1));
    panel.append(claimManualIntake(output));
  };

  // 2. Serial match, then the quality case.
  const traceTab = () => {
    if (!I) return needClaim();
    claimSection(panel, '신고 내용', claimFacts([['고객 클레임 번호', I.external_claim_id], ['고객', I.customer_name],
      ['신고 시각', I.reported_at], ['증상', I.reported_symptom], ['신고 수량', I.claimed_quantity],
      ['신고 일련번호', (I.reported_serials || []).join(', ')]]));
    const match = node('div');
    claimSection(panel, '일련번호 대조', match);
    claimLoad(match, 'serial_match', {intake_id: I.id}, data => {
      const wrap = node('div');
      wrap.append(claimTable(['신고 번호', '결과', '보드', '생산라인', '생산 시각'], data.links.map(v =>
        [v.serial, claimLabel(v.match_state), v.unit_id, v.production_line, v.produced_at])));
      if (!data.ready) wrap.append(node('p', '맞지 않는 일련번호가 있어 사건을 만들 수 없습니다. 사람이 고객에게 확인해야 합니다.', 'message warning'));
      else if (!C) wrap.append(claimActions(claimButton('사건 연결', () => claimAct(output, 'trace_case', {intake_id: I.id},
        '신고 일련번호와 등록된 보드 일련번호를 대조했습니다.', '시연용 가상 대조 기록.', 1))));
      return wrap;
    });
    if (C) {
      claimSection(panel, '만든 품질 사건', claimFacts([['사건', C.id], ['신고 보드', C.unit_ids.length + '대'],
        ['고객', C.customer_name], ['신고 시각', C.reported_at]]));
      panel.append(claimNext('신고 보드 ' + C.unit_ids.length + '대로 품질 사건을 만들었습니다.', 2));
    }
    panel.append(claimManualLink(output, intakes.filter(v => v.intake_state !== 'rejected'), units));
  };

  // 3. Containment: the two request documents, saved, then marked sent.
  const scopeTab = () => {
    if (!C) return needCase();
    const saved = mine(containments, C).length > 0, sent = claimUi.sent[C.id + ':containment'];
    panel.append(node('p', saved ? '저장됨 · 승인된 기록으로 다시 만든 문서입니다.' : '초안 · 아직 저장하지 않았습니다. 내용을 확인하고 저장하세요.',
      saved ? 'message success' : 'message'));
    const bar = node('div'); panel.append(bar);  // the step's button sits above the long documents
    if (saved) claimSection(panel, '조사 범위', claimTable(['범위 상태', '보드', '기준 시각'],
      mine(scopes, C).map(s => [claimLabel(s.scope_state), s.unit_ids.length + '대', s.as_of])));
    const docs = node('div');
    claimSection(panel, '중단 요청서', docs);
    claimLoad(docs, 'containment_documents', {case_id: C.id}, data => claimDocuments(data.documents));
    if (!saved) bar.append(claimActions(claimButton('중단 요청 저장', () => claimAct(output, 'containment',
      {case_id: C.id, prepared_at: claimNow()}, '생산 기록과 C 운영 기록으로 조사 범위를 추렸습니다.', '시연용 가상 범위·중단 요청.', 2))));
    else if (!sent) bar.append(claimActions(claimButton('보내기', async () => {
      claimUi.sent[C.id + ':containment'] = true;
      message('중단 요청서를 보냄으로 표시했습니다. 시스템은 실제로 송부하지 않습니다.', 'success'); claimGo(3);
    })));
    else bar.append(claimNext('중단 요청서를 보냄으로 표시했습니다.', 3));
  };

  // 4. Test request: create, show the document, mark sent to the test team.
  const testTab = () => {
    if (!C) return needCase();
    const saved = mine(tests, C)[0], sent = claimUi.sent[C.id + ':test'];
    if (!saved) {
      const hold = mine(scopes, C).find(s => s.scope_state === 'hold');
      panel.append(claimFacts([['클레임', claimTitle], ['신고 보드', C.unit_ids.length + '대'],
        ['보류한 보관 보드', hold ? hold.unit_ids.length + '대' : '—']]));
      panel.append(claimActions(claimButton('테스트 의뢰서 생성', () => claimAct(output, 'test_request',
        {case_id: C.id, prepared_at: claimNow()}, '보류 범위를 시험 대상으로 하는 의뢰서를 작성했습니다.', '시연용 가상 시험 의뢰.', 3))));
      return;
    }
    panel.append(node('p', '테스트 의뢰서를 만들었습니다 · 의뢰 보드 ' + saved.unit_ids.length + '대', 'message success'));
    const bar = node('div'), docs = node('div'); panel.append(bar);
    claimSection(panel, '테스트 의뢰서', docs);
    claimLoad(docs, 'test_request_document', {case_id: C.id}, data => claimDocuments(data.documents));
    if (!sent) bar.append(claimActions(claimButton('테스트팀에 보내기', async () => {
      claimUi.sent[C.id + ':test'] = true; claimUi.arrival[C.id] = true;
      message('테스트 의뢰서를 보냄으로 표시했습니다. 시스템은 실제로 송부하지 않습니다.', 'success'); claimGo(4);
    })));
    else bar.append(claimNext('테스트 의뢰서를 보냄으로 표시했습니다.', 4));
  };

  // 5. Results: the file arrives, is read as written, and is registered in parts.
  const resultTab = () => {
    if (!C) return needCase();
    if (!mine(tests, C).length) return panel.append(node('p', '먼저 4단계에서 테스트 의뢰서를 만드세요.', 'message'));
    const file = inbox?.result || CLAIM_INBOX[0].result, stored = mine(results, C).length;
    const arrive = () => claimModal('테스트팀 결과가 도착했습니다', ['파일: ' + file,
      '시연용 가상 시험 결과 파일입니다. 실제 시험 성적이 아닙니다.'], '받기', () => { claimUi.received[C.id] = true; claimRender(); });
    if (!stored && !claimUi.received[C.id]) {
      panel.append(node('p', '테스트팀 결과를 기다리는 중입니다.', 'message'), claimActions(claimButton('결과 파일 받기', async () => arrive())));
      if (claimUi.arrival[C.id]) { claimUi.arrival[C.id] = false; setTimeout(arrive, 0); }
      return;
    }
    const view = node('div'); panel.append(view);
    claimLoad(view, 'result_file', {case_id: C.id, file}, data => {
      const wrap = node('div');
      wrap.append(claimFacts([['파일', data.file], ['결과 행', data.rows.length + '건'], ['읽은 상태', claimLabel(data.state)],
        ['그래프에 등록', data.stored + ' / ' + data.rows.length + '건']]));
      claimSection(wrap, '판정별 대수', claimTable(['시험 결과', '조치 판정', '대수'],
        data.counts.map(v => [claimLabel(v.outcome), claimLabel(v.disposition), v.count + '대'])));
      if (data.issues.length) wrap.append(claimTable(['위치', '문제'], data.issues.map(v => [v.locator || '파일', v.code])));
      const raw = node('details'); raw.append(node('summary', '결과 파일 원문 (' + data.rows.length + '행)'), claimTable(data.header, data.rows));
      wrap.append(raw);
      if (data.stored < data.rows.length) wrap.append(claimActions(claimButton('결과 등록', () => claimResults(output,
        {case_id: C.id, file, collected_at: claimNow()}, 4))));
      else wrap.append(claimNext('테스트팀 결과 ' + data.stored + '건을 등록했습니다.', 5));
      return wrap;
    });
  };

  // 6. Reproduction quantity, then A's request document.
  const reproTab = () => {
    if (!C) return needCase();
    const got = mine(results, C);
    if (!got.length) return panel.append(node('p', '먼저 5단계에서 테스트팀 결과를 등록하세요.', 'message'));
    if (!R) {
      const count = {};
      for (const r of got) { const key = claimLabel(r.disposition); count[key] = (count[key] || 0) + 1; }
      panel.append(claimFacts([['등록된 결과', got.length + '건'], ...Object.entries(count).map(([k, n]) => [k, n + '대'])]));
      panel.append(claimActions(claimButton('재생산 계산', () => claimAct(output, 'reproduction',
        {case_id: C.id, collected_at: claimNow()}, '승인된 시험 결과의 처리 구분을 세어 재생산 수량을 계산했습니다.', '시연용 가상 재생산 요건.', 5))));
      return;
    }
    const need = R.customer_replacement_quantity + R.stock_replacement_quantity;
    claimSection(panel, '재생산 수량', claimFacts([['고객 교체', R.customer_replacement_quantity + '대'],
      ['보관 대체', R.stock_replacement_quantity + '대'], ['교체 필요 합계', need + '대'],
      ['사용 가능한 대체 재고', R.available_replacement_quantity + '대'], ['재생산 필요', R.required_good_quantity + '대'],
      ['미해결', R.unresolved_quantity + '대'], ['판정', claimLabel(R.assessment_state)]]),
      node('p', '계산: max(0, ' + R.customer_replacement_quantity + ' + ' + R.stock_replacement_quantity + ' − '
        + R.available_replacement_quantity + ') = ' + R.required_good_quantity + '대'
        + (R.assessment_state === 'lower_bound' ? ' · 미검사 보드가 남아 있어 최소 수량입니다.' : ''), 'hint'));
    if (!claimUi.docShown[C.id]) {
      panel.append(claimActions(claimButton('요청서 만들기', async () => { claimUi.docShown[C.id] = true; claimRender(); })));
      return;
    }
    const bar = node('div'), doc = node('div'); panel.append(bar);
    claimSection(panel, 'A 재생산 요청서', node('p', '승인된 재생산 요건으로 만든 사람용 사본입니다. 그래프에 저장되는 것은 재생산 요건 기록입니다.', 'hint'), doc);
    claimLoad(doc, 'reproduction_document', {case_id: C.id}, data => claimDocuments([{filename: '희망 기한 ' + data.requested_due_date, markdown: data.markdown}]));
    if (!claimUi.handedOff[C.id]) bar.append(claimActions(claimButton('A에게 인계', async () => {
      claimUi.handedOff[C.id] = true; claimGo(6);
    })));
    else bar.append(claimNext('A에게 인계했습니다.', 6));
  };

  // 7. Hand-off: A reads the record from the common graph. C sends no document.
  const handoffTab = () => {
    if (!R) return panel.append(node('p', '먼저 6단계에서 재생산 수량을 계산하세요.', 'message'));
    claimSection(panel, 'A가 조회하는 방법', node('pre', "lookup(kind='ReproductionRequirement', version=" + version + ')'));
    claimSection(panel, 'A가 가져갈 기록', claimFacts([['기록', R.id], ['클레임', claimTitle], ['구성', R.configuration_id],
      ['재생산 필요', R.required_good_quantity + claimLabel(R.unit)], ['판정', claimLabel(R.assessment_state)],
      ['미해결', R.unresolved_quantity + '대'], ['근거 결과', (R.test_result_ids || []).length + '건']]));
    claimSection(panel, '모든 재생산 요건', claimTable(['클레임', '재생산 필요', '판정'],
      repros.map(r => [(intakes.find(v => caseFor(v)?.id === r.case_id) || {}).external_claim_id || r.case_id,
        r.required_good_quantity + '대', claimLabel(r.assessment_state)])));
  };

  const views = [intakeTab, traceTab, scopeTab, testTab, resultTab, reproTab, handoffTab];
  claimRender = () => {
    const progress = progressNow(), next = nextOf(progress), active = claimTab ?? next;
    tabs.replaceChildren();
    CLAIM_STEPS.forEach((title, index) => {
      const mark = progress[index] ? ' ✓' : index === next ? ' ●' : '';
      const button = node('button', (index + 1) + ' ' + title + mark, index === active ? 'primary' : '');
      button.type = 'button';
      if (index === active) button.setAttribute('aria-current', 'step');
      button.addEventListener('click', () => claimGo(index));
      tabs.append(button);
    });
    panel.replaceChildren(node('p', '그래프 v' + version + ' 기준' + (claimTitle ? ' · ' + claimTitle : '') + ' · ' + CLAIM_HINTS[active], 'hint'));
    views[active]();
  };
  container.append(tabs, panel, output);
  claimRender();
}

// --- Manual entry for a claim that arrives without a file (kept from the first C screen) ---------
function claimManualIntake(output) {
  const box = node('details'); box.append(node('summary', '파일 없이 직접 입력'));
  const form = node('form'), grid = node('div', null, 'form-grid'); form.append(grid);
  businessField(grid, 'external_claim_id', '가상 클레임 번호', {placeholder: 'CLM-DEMO-001'});
  businessField(grid, 'customer_name', '가상 고객명', {placeholder: 'DEMO 고객사'});
  businessField(grid, 'reported_at', '신고 시각 · 시간대 포함', {placeholder: '2026-11-04T10:00:00+09:00'});
  businessField(grid, 'claimed_quantity', '신고 수량', {type: 'number'});
  businessField(grid, 'reported_symptom', '고객이 신고한 증상', {type: 'textarea'});
  businessField(grid, 'reported_serials', '신고 일련번호 · 한 줄에 하나', {type: 'textarea', placeholder: '등록된 보드의 실제 serial 값을 확인하세요.'});
  businessSourceFields(form);
  businessSubmit(form, '신고 접수 초안 저장', async () => {
    const payload = {};
    for (const key of ['external_claim_id', 'customer_name', 'reported_at', 'reported_symptom']) payload[key] = form.elements[key].value.trim();
    payload.claimed_quantity = Number(form.elements.claimed_quantity.value);
    payload.reported_serials = form.elements.reported_serials.value.split(/\n/).map(v => v.trim()).filter(Boolean);
    await submitBusiness(form, 'claims', 'intake', payload, output);
  });
  box.append(form); return box;
}

function claimManualLink(output, live, units) {
  const box = node('details'); box.append(node('summary', '파일 없이 직접 보드 연결'));
  if (!live.length) { box.append(node('p', '승인된 클레임 접수가 없습니다.', 'hint')); return box; }
  const form = node('form'); box.append(form);
  const select = businessField(form, 'intake_id', '승인된 신고',
    {options: live.map(v => ({value: v.id, label: v.external_claim_id + ' · ' + v.customer_name})), value: live[0].id});
  const boards = node('fieldset');
  const render = () => {
    boards.replaceChildren(node('legend', '신고 일련번호에 해당하는 보드'));
    const item = live.find(v => v.id === select.value);
    for (const unit of units.filter(v => item.reported_serials.includes(v.serial)))
      businessField(boards, 'unit_ids', unit.serial + ' · ' + unit.id, {type: 'checkbox', required: false}).value = unit.id;
    if (!boards.querySelector('input')) boards.append(node('p', '해당 일련번호의 보드가 없습니다. 보드·원본 기록을 확인하세요.'));
  };
  select.addEventListener('change', render); render(); form.append(boards);
  businessField(form, 'serials_confirmed', '신고 일련번호와 선택한 보드가 일치함을 직접 확인했습니다.', {type: 'checkbox'});
  businessSourceFields(form);
  businessSubmit(form, '접수 확인·사건 연결 초안 저장', async () => {
    const payload = {intake_id: select.value, unit_ids: Array.from(boards.querySelectorAll('input:checked')).map(v => v.value),
      serials_confirmed: form.elements.serials_confirmed.checked};
    await submitBusiness(form, 'claims', 'confirm_case', payload, output);
  });
  return box;
}
