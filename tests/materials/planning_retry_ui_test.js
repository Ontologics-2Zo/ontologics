'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {randomUUID} = require('node:crypto');

const app = fs.readFileSync('ontologics/static/app.js', 'utf8').replace(/run\(initialize\);\s*$/, '');
const planning = fs.readFileSync('ontologics/static/planning.js', 'utf8');

class Element {
  constructor(tag, text = '') {
    this.tagName = tag;
    this.textContent = text;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.classList = {add() {}, toggle() {}};
    this.style = {};
    this.value = '';
    this.hidden = false;
    this.disabled = false;
  }
  append(...children) {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }
  prepend(child) { child.parent = this; this.children.unshift(child); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, listener) { (this.listeners[name] ??= []).push(listener); }
  reportValidity() { return true; }
  fire(name) {
    for (const listener of this.listeners[name] ?? []) listener({preventDefault() {}});
  }
  *visible() {
    if (this.hidden) return;
    yield this;
    for (const child of this.children) yield* child.visible();
  }
}

const review = {
  id: 'interpretation', status: 'needs_review', result_sha256: 'digest',
  result: {items: [{fields: {
    manufacturer: {state: 'supported', value: 'Example', claims: [{evidence: [{quote: 'Example'}]}]},
  }}]},
  checks: [{id: 'review', actor: 'user:alice', decisions: {'0:manufacturer': {state: 'unresolved', value: null, reason: ''}}}],
};
const link = {id: 'link', interpretation_id: 'interpretation', review_id: 'review', configuration_id: 'config'};
const workflow = {
  search: {items: [{id: 'contract', title: '계약'}], total: 1},
  get: {id: 'contract', title: '계약', text: 'Example', mime: 'text/plain', review},
  planning: {items: [{
    fields: {manufacturer: {state: 'unresolved'}},
    missing: ['manufacturer'], link_current: true, link,
    candidates: [{exact: true, name: '제품', configuration_id: 'config', comparison: {}}],
  }], interpretation_id: 'interpretation', review_id: 'review'},
  materials: {title: '계약', item_index: 0, link, fields: {
    ordered_quantity: {state: 'accepted', value: 7}, unit: {state: 'accepted', value: 'piece'},
  }},
  'product-inventory': {products: [{name: '제품', configuration_id: 'config', result: {components: []}}]},
  'process-requirement-preview': {lines: []},
  'request-inbox': {data_version: 1, requests: [{
    evidence_id: 'evidence', status: 'new', request: {request_id: 'request', revision: 1, quantity: 7, configuration_id: 'config'},
  }]},
  'request-seen': {},
};
const saved = {id: 'saved', payload: {quantity: 7, allowance_percent: '10', rfq: {lines: [], excluded: []}, bom: {components: []}, process: []}};

async function browser({session = new Map(), actor = 'alice', schema = 'materials', storageAvailable = true, simulateConfirmation = false} = {}) {
  const ids = new Map(), calls = [];
  const byId = id => {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  };
  let loseResponse = false;
  let confirmedReview = null;
  const publishCheck = (body, checkedBy) => {
    const check = {id: 'committed-check', actor: checkedBy, decisions: body.decisions, note: body.note};
    confirmedReview = {...review, checks: [...review.checks, check]};
  };
  const sandbox = vm.createContext({
    document: {
      getElementById: byId, createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text),
      querySelectorAll: () => [], adoptedStyleSheets: [],
    },
    window: {addEventListener() {}},
    initializeDashboardControls() {},
    Node: Element, CSSStyleSheet: class {replaceSync() {}},
    MutationObserver: class {observe() {} disconnect() {}},
    requestAnimationFrame() {}, structuredClone, crypto: {randomUUID},
    sessionStorage: {
      setItem(key, value) { if (!storageAvailable) throw Error('blocked'); session.set(key, value); },
      getItem(key) { if (!storageAvailable) throw Error('blocked'); return session.get(key) ?? null; },
    },
    async fetch(path, options) {
      let data;
      if (path === '/api/context') {
        data = {database_schema: schema, identity: {actor: 'user:' + actor, permissions: ['read', 'review', 'draft']}};
      } else {
        const action = path.replace('/api/planning/', '');
        const body = JSON.parse(options.body);
        calls.push({action, body});
        if (simulateConfirmation && action === 'review') {
          publishCheck(body, 'user:' + actor);
        }
        if (confirmedReview && action === 'get') {
          data = {...workflow.get, review: confirmedReview};
        } else if (confirmedReview && action === 'planning') {
          data = {...workflow.planning, review_id: 'committed-check'};
        } else if (Object.hasOwn(workflow, action)) {
          data = workflow[action];
        } else {
          assert.match(action, /^(review|combined-review|contract-combined-review|request-review)$/);
          if (loseResponse) {
            loseResponse = false;
            throw Error('network lost after dispatch');
          }
          data = saved;
        }
      }
      return {headers: {get: () => null}, json: async () => ({ok: true, data, data_version: 1})};
    },
  });
  vm.runInContext(app, sandbox, {filename: 'app.js'});
  vm.runInContext(planning, sandbox, {filename: 'planning.js'});
  // Keep the common initialize, storage, action dispatch and fetch handling.
  // Omit unrelated app rendering and routing.
  vm.runInContext(`
    renderDevelopmentIdentity = updateSearchMode = fillQueries = updateControls = route = () => {};
  `, sandbox);
  const root = byId('test-planning');
  const mount = () => vm.runInContext("mountPlanning($('test-planning'))", sandbox);
  await vm.runInContext('initialize()', sandbox);
  await mount();

  function find(tag, text) {
    const matches = [...root.visible()].filter(element => element.tagName === tag && element.textContent === text);
    assert.equal(matches.length, 1, 'visible ' + tag + ': ' + text);
    return matches[0];
  }
  async function click(label) {
    const button = find('button', label);
    assert.equal(button.disabled, false);
    if (button.type === 'submit') {
      let form = button.parent;
      while (form.tagName !== 'form') form = form.parent;
      form.fire('submit');
    } else {
      button.fire('click');
    }
    // All test fetches settle without timers. The click listeners leave their
    // promises to the shared host, so wait one event-loop turn for rendering.
    await new Promise(resolve => setImmediate(resolve));
  }
  function setField(label, value) {
    const field = find('label', label).children[0];
    field.value = value;
    field.fire('input');
  }
  return {
    click, setField, mount, publishCheck,
    loseNextResponse() { loseResponse = true; },
    writes: action => calls.filter(call => call.action === action).map(call => call.body),
    feedback: () => [...root.visible()].find(element => element.attributes.role === 'status')?.textContent,
  };
}

const contractButton = '계산하고 결과 확인 →';
const productButton = '이 제품 검토하고 결과 보기 →';
const sourceButton = '원문 대조 저장하고 제품 연결 →';
const confirmationButton = '위 제품 정보 확인·저장 →';

async function openProduct(ui, quantity = '7') {
  await ui.click('제품별 자재');
  ui.setField('비교할 생산 수량 (대)', quantity);
  await ui.click('제품별 필요량 비교 →');
}
async function openContract(ui) { await ui.click('계약 열기'); }
async function openContractCalculation(ui) {
  await openContract(ui);
  await ui.click('이 제품의 자재 계산 →');
}
async function openRequest(ui) {
  await ui.click('추가 생산 요청');
  await ui.click('요청 내용 확인 →');
}
async function openSourceReview(ui) {
  await openContract(ui);
  await ui.click('← 원문 대조');
}

async function retryThenStartNew({open, button, action, back, change}) {
  const ui = await browser();
  await open(ui);
  ui.loseNextResponse();
  await ui.click(button);
  assert.match(ui.feedback(), /완료 여부가 불확실.*같은 내용으로 재시도/);
  const [pending] = ui.writes(action);
  assert.ok(pending.request_key);
  if (change) {
    await change(ui);
    ui.loseNextResponse();
    await ui.click(button);
    assert.notEqual(ui.writes(action)[1].request_key, pending.request_key, action + ' changed input');
    await ui.mount();
    await open(ui);
  }
  await ui.click(button);
  assert.deepEqual(ui.writes(action).at(-1), pending, action + ' retry after lost response');
  assert.match(ui.feedback(), /저장했습니다/);
  await ui.click(back);
  await ui.click(button);
  assert.notEqual(ui.writes(action).at(-1).request_key, pending.request_key, action + ' new explicit calculation');
  if (change) {
    await ui.click(back);
    await change(ui);
    await ui.click(button);
    assert.deepEqual(ui.writes(action).at(-1), ui.writes(action)[1], action + ' other pending attempt survives');
  }
}

async function reloadKeepsOnlyTheSameSessionKey() {
  const session = new Map();
  const original = await browser({session});
  await openProduct(original);
  original.loseNextResponse();
  await original.click(productButton);
  const [pending] = original.writes('combined-review');
  for (const partition of [{actor: 'bob'}, {schema: 'other'}]) {
    const other = await browser({session, ...partition});
    await openProduct(other);
    await other.click(productButton);
    assert.notEqual(other.writes('combined-review')[0].request_key, pending.request_key);
  }
  const reloaded = await browser({session});
  await openProduct(reloaded);
  await reloaded.click(productButton);
  assert.deepEqual(reloaded.writes('combined-review')[0], pending);
  const afterSuccess = await browser({session});
  await openProduct(afterSuccess);
  await afterSuccess.click(productButton);
  assert.notEqual(afterSuccess.writes('combined-review')[0].request_key, pending.request_key);
}

async function unavailableStorageKeepsTheKeyInMemory() {
  const original = await browser({storageAvailable: false});
  await openProduct(original);
  original.loseNextResponse();
  await original.click(productButton);
  const [pending] = original.writes('combined-review');
  await original.mount();
  await openProduct(original);
  original.loseNextResponse();
  await original.click(productButton);
  assert.deepEqual(original.writes('combined-review')[1], pending);
  const reloaded = await browser({storageAvailable: false});
  await openProduct(reloaded);
  await reloaded.click(productButton);
  assert.notEqual(reloaded.writes('combined-review')[0].request_key, pending.request_key);
}

async function main() {
  await retryThenStartNew({
    open: openContractCalculation, button: contractButton, action: 'contract-combined-review', back: '← 계산 조건 수정',
    change: ui => ui.setField('공정 자재 여유율 (%)', '12'),
  });
  await retryThenStartNew({
    open: openRequest, button: contractButton, action: 'request-review', back: '← 요청·계산 조건',
    change: ui => ui.setField('공정 자재 여유율 (%)', '12'),
  });
  await retryThenStartNew({
    open: openProduct, button: productButton, action: 'combined-review', back: '← 제품 비교로',
  });
  await retryThenStartNew({open: openSourceReview, button: sourceButton, action: 'review', back: '← 원문 대조'});
  const confirmation = await browser({simulateConfirmation: true});
  await openContract(confirmation);
  confirmation.loseNextResponse();
  await confirmation.click(confirmationButton);
  const [pending] = confirmation.writes('review');
  await confirmation.click(confirmationButton);
  assert.deepEqual(confirmation.writes('review')[1], pending, 'confirmation retries even after its own review is committed');
  await confirmation.click(confirmationButton);
  assert.notEqual(confirmation.writes('review')[2].request_key, pending.request_key);
  for (const concurrent of [
    {actor: 'user:bob', changes: {}},
    {actor: 'user:alice', changes: {note: 'different review'}},
  ]) {
    const ui = await browser();
    await openContract(ui);
    ui.loseNextResponse();
    await ui.click(confirmationButton);
    const [first] = ui.writes('review');
    ui.publishCheck({...first, ...concurrent.changes}, concurrent.actor);
    await ui.click(confirmationButton);
    assert.equal(ui.writes('review').length, 1, 'stale confirmation must not submit again');
    assert.match(ui.feedback(), /저장된 원문 대조가 바뀌었습니다/);
  }
  await reloadKeepsOnlyTheSameSessionKey();
  await unavailableStorageKeepsTheKeyInMemory();
}
main().catch(error => { console.error(error); process.exitCode = 1; });
