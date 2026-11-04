// Exercise the shipped browser code with a small DOM and HTTP boundary stand-in.
// This tests target selection and request intent; it does not claim browser rendering coverage.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag, text = '') { this.tag = tag; this.text = text || ''; this.children = []; this.style = {}; this.events = {}; }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, action) { this.events[event] = action; }
  get textContent() { return this.text + this.children.map(v => v.textContent || '').join(' '); }
  set textContent(value) { this.text = value; }
}
const tree = root => [root, ...root.children.flatMap(tree)];
let screen = new Element('main'), reads = [], writes = [], records = {}, pending = 0;
const node = (tag, text, klass) => Object.assign(new Element(tag, text), {className: klass});
const ctx = vm.createContext({Node: Element, console, setTimeout,
  node, document: {createTextNode: value => node('#text', value)},
  location: {hostname: '127.0.0.1'}, businessSelection: {version: 1},
  context: {identity: {subject: 'alice', identity_mode: 'development'}, development_users: [
    {id: 'alice', subject: 'alice', label: 'Alice'}, {id: 'bob', subject: 'bob', label: 'Bob'}]},
  developmentSessionNeedsReload: false,
  actionButton: (label, action) => Object.assign(node('button', label), {action}),
  run: action => action(), message: () => {}, must: r => { assert.equal(r.ok, true); return r; },
  currentDevelopmentPreset: () => ctx.context.development_users.find(u => u.subject === ctx.context.identity.subject),
  requestKey: (_, body) => JSON.stringify(body),
  businessField: (parent, name, label, options) => {
    const el = node('select', label); el.name = name; el.value = options.value; parent.append(el); return el;
  },
  businessSourceFields: form => { form.elements = Object.fromEntries(['source_text', 'authored_at', 'notice', 'synthetic_confirmed'].map(k => [k, {}])); },
  tool: async (_, {kind, version}) => { assert.equal(version, ctx.businessSelection.version); return {data: records[kind] || []}; },
  submitBusiness: async (form, domain, action, payload) => { writes.push({domain, action, payload}); return {data: {draft_id: 'draft:1', draft_version: 3}}; },
  api: async (path, body) => {
    reads.push({path, body});
    if (path === '/api/claims/query') return {ok: true, data: {pending_count: pending, registration_complete: pending === 0}};
    if (path === '/api/development-user') return {ok: true, data: {identity: {subject: body.user, identity_mode: 'development'}, csrf_token: 'test'}};
    if (path === '/api/review') return {ok: true, data: {application: {data_version: 7}}};
    throw new Error('Unexpected path ' + path);
  },
  openBusiness: async () => { screen = node('main'); await ctx.mountClaims(screen); },
});
vm.runInContext(fs.readFileSync('ontologics/static/claims.js', 'utf8'), ctx);
// Manual entry forms are outside this test. Keep the real stage rendering and action closures.
vm.runInContext("claimManualIntake = () => node('details'); claimManualLink = () => node('details'); claimLoad = (target, action, args) => { target.append(node('p', action)); uiReads.push({action, args}); }; claimAttested = true;", Object.assign(ctx, {uiReads: []}));
const find = predicate => tree(screen).find(predicate);
const click = label => { const button = find(v => v.tag === 'button' && v.text === label); assert.ok(button, label); return button.action ? button.action() : button.events.click(); };
const setup = () => {
  records = {
    ClaimIntake: ['one', 'two'].map((v, i) => ({id: 'intake:' + v, external_claim_id: 'claim-' + v, customer_name: 'DEMO', reported_serials: [i ? 'C-D040' : 'C-D021']})),
    QualityCase: ['one', 'two'].map(v => ({id: 'case:' + v, intake_id: 'intake:' + v, unit_ids: ['unit:1']})),
  };
};
(async () => {
  setup(); await ctx.mountClaims(screen);
  assert.equal(ctx.uiReads.at(-1).args.case_id, 'case:two');
  const select = find(v => v.name === 'claim_focus'); select.value = 'intake:one'; await select.events.change();
  assert.equal(ctx.uiReads.at(-1).args.case_id, 'case:one');
  await click('중단 요청 저장'); assert.equal(writes.at(-1).payload.case_id, 'case:one');
  // The separate inbox button must also remount before creating an action for the selected claim.
  vm.runInContext('claimGo(0)', ctx);
  const inboxButtons = tree(screen).filter(v => v.text === '이 클레임으로 진행');
  await inboxButtons[1].action();
  assert.equal(ctx.uiReads.at(-1).args.case_id, 'case:two');
  await click('중단 요청 저장'); assert.equal(writes.at(-1).payload.case_id, 'case:two');

  // Automatic approval remains an explicit local-development shortcut and uses exact draft version.
  assert.equal(vm.runInContext('claimAutoApprove', ctx), false);
  ctx.location.hostname = 'demo.example';
  await assert.rejects(() => ctx.claimApproveDraft({draft_id: 'draft:1', draft_version: 3}), /로컬 개발/);
  assert.equal(reads.filter(v => v.path === '/api/review').length, 0);
  ctx.location.hostname = '127.0.0.1';
  const result = await ctx.claimApproveDraft({draft_id: 'draft:1', draft_version: 3});
  assert.equal(result.version, 7); assert.equal(ctx.context.identity.subject, 'alice');
  assert.equal(reads.find(v => v.path === '/api/review').body.version, 3);
  assert.match(reads.find(v => v.path === '/api/review').body.note, /두 사람의 검토가 아닙니다/);

  // A first 100-row batch is not a completed result-file registration or a reason to advance.
  records.ContainmentRequest = [{case_id: 'case:two'}]; records.TestRequest = [{case_id: 'case:two', unit_ids: []}];
  records.QualityTestResult = [{case_id: 'case:two', disposition: 'release'}]; pending = 23;
  await ctx.openBusiness();
  assert.equal(find(v => v['aria-current'] === 'step').text, '5 결과 ●');
  vm.runInContext('claimGo(5)', ctx);
  assert.match(screen.textContent, /결과 파일의 등록이 완료되지/);
  assert.equal(find(v => v.text === '재생산 계산'), undefined);
  // A fully registered sampled file may still produce a lower bound: don't count target boards here.
  pending = 0; ctx.businessSelection.version = 2; await ctx.openBusiness();
  assert.ok(find(v => v.text === '재생산 계산'));
  records.ReproductionRequirement = ['L', 'L1'].map((v, i) => ({id: 'repro:' + v, case_id: 'case:two', configuration_id: 'config:' + v,
    customer_replacement_quantity: 3, stock_replacement_quantity: 0, available_replacement_quantity: 0,
    required_good_quantity: 3, unresolved_quantity: i, assessment_state: i ? 'lower_bound' : 'confirmed'}));
  await ctx.openBusiness();
  assert.match(screen.textContent, /재생산 수량 · config:L1/); assert.match(screen.textContent, /재생산 수량 · config:L/);
  assert.ok(find(v => v.text === '현재 조회 버전으로 재계산 초안 작성'));
  vm.runInContext('claimGo(6)', ctx);
  assert.match(screen.textContent, /repro:L1/); assert.match(screen.textContent, /repro:L/);
  console.log('claims UI target, local approval, partial-file and configuration checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
