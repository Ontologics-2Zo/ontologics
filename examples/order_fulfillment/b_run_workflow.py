"""Bounded B Host campaign; the caller supplies a fresh isolated database.

Offline responses are explicit HTTP doubles. Live responses always pass through
the existing providers and require exact, separately supplied review records.
"""
import copy
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

from examples.order_fulfillment import (
    b_agent, b_contract, b_cq01_bounded, b_fulfillment_agent,
    b_production_agent, b_delivery_agent, b_response_agent, b_comparison_agent,
)
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from examples.order_fulfillment.b_run_transport import (
    Ledger, Transport, Response, Blocked, MODEL, accept_after_review,
)
from ontologics import model as model_module
from ontologics.database import DatabaseSettings
from ontologics.demo import must
from ontologics.identity import UserIdentity, DevelopmentIdentityProvider, DevelopmentContext
from ontologics.model import canonical, digest
from ontologics.order_fulfillment.host import CommonHost, AgentRegistration
from ontologics.order_fulfillment.openai_provider import OpenAIProvider, OpenAISettings
from ontologics.order_fulfillment.runtime import ModelConfig
from ontologics.order_fulfillment.service import Service


SPECS = [
    ('cq01_intake', 'intake', 'normal'), ('cq01_promise', 'promise', 'normal'),
    ('cq02_normal', 'fulfillment', 'normal'), ('cq03_normal', 'production', 'normal'),
    ('cq04_normal', 'delivery', 'normal'), ('cq04_mixed', 'delivery', 'mixed'),
    ('cq05_normal', 'response', 'normal'), ('cq05_mixed', 'response', 'mixed'),
    ('cq06_defects11', 'comparison', 'defects11'),
    ('cq06_transport2h', 'comparison', 'transport2h'),
]
CASES = [s[0] for s in SPECS]
REVIEWER_SUBJECT = 'task11-reviewer'
MODULES = dict(fulfillment=b_fulfillment_agent, production=b_production_agent,
               delivery=b_delivery_agent, response=b_response_agent,
               comparison=b_comparison_agent)
TABLES = [('snapshots', 'version'), ('drafts', 'id,version'),
          ('review_events', 'sequence'), ('requests', 'actor,request_key'),
          ('evidence_vectors', 'data_version,evidence_id,model')]


def _persist(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.write('\n')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _freeze(service):
    return {name: service.db.execute('SELECT * FROM ' + name + ' ORDER BY ' + order).fetchall()
            for name, order in TABLES}


@contextmanager
def _input_roots(root):
    modules = [model_module, b_agent, b_contract, *MODULES.values()]
    originals = [(module, module.ROOT) for module in modules if hasattr(module, 'ROOT')]
    try:
        for module, _ in originals:
            module.ROOT = root
        yield
    finally:
        for module, original in originals:
            module.ROOT = original


def _fixture_manifest(root):
    """Read only exact input paths declared by the caller's safe manifest."""
    declared = json.loads((root / 'safe_input_manifest.json').read_text())
    entries = {}
    for relative, expected in declared.items():
        path = root / relative
        if (not isinstance(relative, str) or Path(relative).is_absolute()
                or '..' in Path(relative).parts or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())):
            raise ValueError('fixture inputs must be owned regular paths')
        actual = _sha(path)
        if actual != expected:
            raise ValueError('fixture input hash differs from declared manifest')
        entries[relative] = actual
    if not entries:
        raise ValueError('fixture input data is empty')
    return entries


def _approved_comparison(database, case, input_root, registrations, persist):
    """Development fixtures have separately reviewed, applied versions 2 and 3."""
    from ontologics.demo import PRINCIPALS
    from ontologics.order_fulfillment.contracts import load_demo_contract
    from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
    from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, load_demo_recalculation

    author = UserIdentity('task11-fixture-author', frozenset({'read', 'draft', 'review'}))
    reviewer = UserIdentity('task11-fixture-reviewer', frozenset({'read', 'review'}))
    host = CommonHost(DevelopmentIdentityProvider([author, reviewer], enabled=True), database,
                      agent_registrations=registrations)
    service = Service(database, principals={**PRINCIPALS, author.actor: author.principal(),
                                           reviewer.actor: reviewer.principal()})
    approvals = []
    try:
        seeded = must(service.bootstrap('seed-admin',
            json.loads((input_root / 'data/catalog.json').read_text())['nodes'] + load_demo_contract(),
            request_key='task11-comparison-seed'))
        assert seeded['data_version'] == 1
        for label, nodes, expected in [('before', load_demo_delivery(), 2),
                                       ('after', load_demo_recalculation(case), 3)]:
            with host.request(DevelopmentContext(author.subject)) as session:
                prepared = must(session.call('prepare_draft', dict(
                    operations=[dict(op='put', node=n) for n in nodes], request_key=label + '-draft')))
            draft = prepared['data']
            assert draft['validation']['valid'] is True
            did, version = draft['draft_id'], draft['draft_version']
            premature = host.apply_approved(did, version, request_key=label + '-premature')
            assert premature['errors'][0]['code'] == 'APPROVAL_REQUIRED'
            with host.request(DevelopmentContext(author.subject)) as session:
                self_review = session.review(did, version, decision='approve',
                    note='자기 승인 거부 검사', request_key=label + '-self')
            assert self_review['errors'][0]['code'] == 'SELF_APPROVAL_FORBIDDEN'
            with host.request(DevelopmentContext(reviewer.subject)) as session:
                reviewed = must(session.review(did, version, decision='approve',
                    note='별도 개발 검토자의 가상 비교 입력 검토. 운영 승인이나 발송 승인 아님.',
                    request_key=label + '-review'))
            assert reviewed['data']['reviewer'] == reviewer.actor != author.actor
            assert reviewed['data']['content_hash'] == draft['content_hash']
            applied = must(host.apply_approved(did, version, request_key=label + '-apply'))
            assert (applied['data']['draft_version'], applied['data_version']) == (version, expected)
            stored = must(service.get_draft(author.actor, did, version))
            assert (stored['data']['status'], stored['data']['applied_version'],
                    stored['data']['content_hash']) == ('applied', expected, draft['content_hash'])
            approvals.append(dict(label=label, prepared=prepared, premature_apply=premature,
                self_review=self_review, review=reviewed, apply=applied, persisted=stored))
    finally:
        service.close()
    persist('cq06_' + case + '_approved_pair.json', dict(case=case, approvals=approvals,
        development_fixture=True, authoring_connection_closed=True, before_version=2,
        after_version=3, agent_approval_tools=False))
    return dict(mode='comparison', case=case, delivery_round_id=ROUND, before_version=2,
        before_as_of=AS_OF, after_version=3, after_as_of=AFTER_AS_OF[case],
        question='두 승인 버전의 수량·시각·입력 변경과 검토 초안 비교')


class OfflineStub:
    """Explicit fake HTTP only: scripted/oracle answers never enter live mode."""
    def __init__(self, mode, audit, task, actor):
        self.mode, self.audit, self.task, self.actor = mode, audit, task, actor
        self.kwargs = None
        self.normalized = []
        if mode in MODULES:
            cls = MODULES[mode].ScriptedProvider
            self.scripted = cls(audit, task) if mode == 'fulfillment' else cls(audit, task, actor=actor)

    def answer(self):
        task = self.task
        history = [m for m in self.kwargs['messages'] if m['role'] == 'tool']

        def calls(name, arguments):
            return dict(text='', tool_calls=[dict(name=name, arguments=a) for a in arguments])

        if self.mode == 'intake':
            if not history:
                ids = [task['source_id'], *task['evidence_ids'].values()]
                arguments = [dict(node_id=i, version=task['data_version']) for i in ids]
                arguments += [dict(kind=k, version=task['data_version'])
                              for k in ('ProductModel', 'ProductRevision', 'Configuration')]
                return calls('lookup', arguments)
            if len(history) == 7:
                baseline = b_agent.intake_baseline(self.audit, task, actor=self.actor)
                return calls('prepare_draft', [dict(operations=[dict(op='put', node=n)
                    for n in baseline['nodes']], request_key=task['request_key'])])
            prepared = json.loads(next(m['content'] for m in history
                                       if m['name'] == 'prepare_draft'))['data']
            if len(history) == 8:
                return calls('validate_draft', [dict(draft_id=prepared['draft_id'],
                                                      version=prepared['draft_version'])])
            answer = dict(status='awaiting_review', draft_id=prepared['draft_id'],
                draft_version=prepared['draft_version'], data_version=task['data_version'],
                evidence=list(task['evidence_ids'].values()), unknowns=[],
                needs_human_review=True, synthetic_contract=True,
                summary='가상 계약의 미승인 등록 초안은 검증을 통과했으며 사람 검토 대기입니다.',
                next_action='작성자와 다른 개발 검토자가 원문·구성·수량·도착 기한을 확인해야 합니다. 고객 발송·출고를 하지 않았습니다.')
        elif self.mode == 'promise':
            if not history:
                return calls('delivery_round', [dict(delivery_round_id=task['delivery_round_id'],
                                                     version=task['data_version'])])
            result = json.loads(history[0]['content'])
            if len(history) == 1:
                return calls('lookup', [dict(node_id=i, version=task['data_version'])
                                        for i in result['evidence']])
            if len(history) == 1 + len(result['evidence']):
                sources = dict.fromkeys(json.loads(m['content'])['data'][0]['source_id']
                                        for m in history[1:])
                return calls('lookup', [dict(node_id=i, version=task['data_version']) for i in sources])
            answer = dict(status='promise_only', data_version=task['data_version'],
                promise=b_agent.promise_values(result['data']), evidence=result['evidence'],
                unknowns=b_agent.NOT_ASSESSED, synthetic_contract=True,
                summary='가상고객사 A에 ESP32-POE Rev L WROOM 1000대를 2026-10-27 15:00 +09:00까지 도착시키는 가상 약속입니다. 실제 제품 구성 근거와 가상 계약 근거를 구분합니다.',
                next_action='재고·배정·생산·운송·실제 도착은 미평가이며 납품 가능성을 보장하지 않습니다.')
        else:
            return self.scripted(**self.kwargs)
        return dict(text=canonical(answer), tool_calls=[])

    def __call__(self, request, *, timeout):
        normalized = self.answer()
        self.normalized.append(normalized)
        index = len(self.normalized)
        items = [dict(type='function_call', name=c['name'], arguments=canonical(c['arguments']),
                      call_id='stub_' + str(index) + '_' + str(i))
                 for i, c in enumerate(normalized['tool_calls'])]
        if normalized['text']:
            items.append(dict(type='message', content=[dict(type='output_text', text=normalized['text'])]))
        return Response(canonical(dict(id='offline_' + str(index), status='completed',
            model=MODEL, output=items, offline_http_double=True)).encode())


def _approve_intake(host, audit, answer, reviewer, *, offline, review_wait, persist, input_root):
    did, version = answer['draft_id'], answer['draft_version']
    draft = must(audit.get_draft('user:task11-author', did, version))['data']
    request = dict(kind='cq01_draft_review', case='cq01_intake', draft_id=did, version=version,
        content_hash=draft['content_hash'], author=draft['author'], reviewer=reviewer.actor,
        development_fixture=True)
    persist('reviews/cq01_draft.requested.json', request)
    review = dict(request, decision='approve', notes='무료 HTTP 모형의 명시적 개발 검토 fixture',
                  offline_simulated_review=True) if offline else review_wait(copy.deepcopy(request))
    persist('reviews/cq01_draft.json', review)
    if (not isinstance(review, dict) or review.get('kind') != request['kind']
            or review.get('decision') != 'approve' or review.get('reviewer') != reviewer.actor
            or reviewer.actor == draft['author'] or review.get('author') != draft['author']
            or review.get('draft_id') != did or type(review.get('version')) is not int
            or review['version'] != version or review.get('content_hash') != draft['content_hash']
            or not isinstance(review.get('notes'), str) or not review['notes'].strip()):
        raise Blocked('exact trusted developer draft approval required')
    premature = host.apply_approved(did, version, request_key='task11-before-review')
    assert not premature['ok'] and premature['errors'][0]['code'] == 'APPROVAL_REQUIRED'
    with host.request(DevelopmentContext(reviewer.subject)) as session:
        reviewed = must(session.review(did, version, decision='approve', note=review['notes'],
                                       request_key='task11-review'))
    assert reviewed['data']['reviewer'] == reviewer.actor != draft['author']
    assert reviewed['data']['content_hash'] == draft['content_hash']
    applied = must(host.apply_approved(did, version, request_key='task11-apply'))
    assert applied['data']['draft_version'] == version
    golden = json.loads((input_root / 'data/order_fulfillment/b_contract_expected.json').read_text())
    task = dict(mode='promise', delivery_round_id=golden['delivery_round_id'],
        data_version=applied['data_version'], not_assessed=b_agent.NOT_ASSESSED,
        task='승인된 정확 버전의 가상 계약 약속만 조회하세요.')
    persist('cq01_approval.json', dict(request=request, supplied_review=review, review=reviewed,
        apply=applied, premature_rejected=premature, offline_simulated_review=offline))
    return task


def run_campaign(*, database_url: str, input_root: Path, output: Path, cases: list[str],
                 offline: bool = True, settings=None, backend=None, review_wait=None):
    """Execute a canonical subset once; never load credentials or create a DB server."""
    if type(offline) is not bool or not isinstance(cases, list) or not cases:
        raise ValueError('offline must be bool and cases a nonempty canonical list')
    if cases != [c for c in CASES if c in cases] or len(set(cases)) != len(cases):
        raise ValueError('cases must be a unique canonical ordered subset')
    if not isinstance(database_url, str) or not database_url:
        raise ValueError('caller-owned isolated database_url required')
    if not offline and (not callable(backend) or settings is None or not callable(review_wait)):
        raise Blocked('live backend, explicit settings and separate review callback required')
    if offline and backend is not None:
        raise ValueError('offline mode uses only the explicit OfflineStub')
    settings = settings or OpenAISettings(api_key='offline-http-double-only', model=MODEL,
                                          max_output_tokens=1024, timeout_seconds=30)
    if settings.model != MODEL or settings.max_output_tokens != 1024 or settings.timeout_seconds != 30:
        raise ValueError('fixed model/output1024/timeout30 settings required')
    input_root, output = Path(input_root), Path(output)
    manifest = _fixture_manifest(input_root)
    fixture_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    output.mkdir(mode=0o700, exist_ok=False)
    for name in ('reports', 'reviews', 'wire', 'layers'):
        (output / name).mkdir(mode=0o700)
    persist = lambda name, value: _persist(output / name, value)
    ledger = Ledger(output / 'http_budget.jsonl', cases=cases, total_cap=4 * len(cases), offline=offline)
    persist('input_manifest.json', dict(files=manifest, fixture_inputs_sha256=fixture_hash))
    limits = dict(model=MODEL, max_output_tokens=1024, timeout_seconds=30,
                  max_steps=4, max_attempts=1, max_tool_calls=8, total_request_cap=4 * len(cases))
    persist('start.json', dict(offline=offline, cases=cases, settings=limits,
                               fixture_inputs_sha256=fixture_hash, automatic_paid_rerun=False))
    author = UserIdentity('task11-author', frozenset({'read', 'draft'}))
    reviewer = UserIdentity(REVIEWER_SUBJECT, frozenset({'read', 'review'}))
    identities = DevelopmentIdentityProvider([author, reviewer], enabled=True)
    registrations = tuple(AgentRegistration(r.name,
        b_cq01_bounded.INTAKE_PROMPT if r.name == 'b_intake' else
        b_cq01_bounded.PROMISE_PROMPT if r.name == 'b_promise' else r.instructions,
        r.allowed_tools) for r in REGISTRATIONS)
    config = ModelConfig(model=MODEL, max_steps=4, max_attempts=1, max_tool_calls=8)
    reports, schemas = [], []
    active, cq01_database, promise_task = None, None, None
    try:
        with _input_roots(input_root):
            for case, mode, variant in SPECS:
                if case not in cases:
                    continue
                active = case
                ledger.begin(case)
                if mode == 'promise' and cq01_database is not None:
                    database, task = cq01_database, promise_task
                    assert task is not None
                else:
                    database = DatabaseSettings(database_url, 'test_' + uuid.uuid4().hex)
                    schemas.append(database.schema)
                    if mode == 'comparison':
                        task = _approved_comparison(database, variant, input_root, registrations, persist)
                    else:
                        fixture, task = (b_agent.setup_intake(database) if mode == 'intake' else
                            b_agent.setup_promise(database) if mode == 'promise' else
                            MODULES[mode].setup(database, case=variant))
                        fixture.close()
                    if mode == 'intake':
                        cq01_database = database
                host = CommonHost(identities, database, agent_registrations=registrations)
                audit = Service(database, principals={u.actor: u.principal() for u in (author, reviewer)})
                try:
                    before = _freeze(audit)
                    before_version = audit.lookup(author.actor)['data_version']
                    stable_task = {k: v for k, v in task.items() if k != 'request_key'}
                    baseline = dict(case=case, task=stable_task,
                        snapshots=[dict(version=r['version'], nodes=r['nodes']) for r in before['snapshots']],
                        fixture_inputs_sha256=fixture_hash)
                    baseline_hash = digest(baseline)
                    persist('baseline_' + case + '.json', dict(baseline, fixture_task_sha256=baseline_hash))
                    views, submitted = [], []
                    stub = OfflineStub(mode, audit, task, author.actor) if offline else None
                    transport = Transport(ledger, case, output / 'wire', stub if offline else backend,
                                          offline=offline, paid_authorized=not offline)
                    cls = (b_cq01_bounded.BoundedOpenAIProvider if mode in ('intake', 'promise') else
                           OpenAIProvider if mode == 'fulfillment' else MODULES[mode].BoundedOpenAIProvider)

                    class Recorded(cls):
                        def __call__(self, *, model, messages):
                            if offline:
                                stub.kwargs = dict(model=model, messages=messages)
                            views.append(copy.deepcopy(dict(model=model, messages=messages)))
                            response = super().__call__(model=model, messages=messages)
                            submitted.append(copy.deepcopy(response))
                            return response

                    provider = (Recorded(settings, task, opener=transport) if mode in ('intake', 'promise') else
                        Recorded(settings, MODULES[mode].TOOLS, opener=transport) if mode == 'fulfillment' else
                        Recorded(settings, opener=transport))
                    guarded = provider if mode in ('intake', 'promise') else MODULES[mode].ProvenanceProvider(provider, task)
                    with host.request(DevelopmentContext(author.subject)) as session:
                        result = session.run_agent('b_' + mode, guarded,
                            [dict(role='user', content=canonical(task))], config=config, capture_tool_trace=True)
                        context = session.context
                    module = b_agent if mode in ('intake', 'promise') else MODULES[mode]
                    evaluation_error = None
                    try:
                        checks, answer = module.evaluate(audit, task, result,
                            task.get('after_version', task.get('data_version')), actor=author.actor)
                    except Exception as error:
                        checks, answer, evaluation_error = dict(evaluator_completed=False), None, type(error).__name__
                    row = audit.db.execute('SELECT actor,config,output_hash,status FROM runtime_runs WHERE id=%s',
                                           (result['run_id'],)).fetchone()
                    cfg = json.loads(row['config'])
                    registration = next(r for r in registrations if r.name == 'b_' + mode)
                    after = _freeze(audit)
                    checks.update(runtime_success=result['ok'] and result['status'] == 'completed' and bool(result['text']),
                        http_within4=1 <= transport.sent <= 4, zero_retries=cfg['max_attempts'] == 1,
                        role_scope_exact=set(cfg['allowed_tools']) == registration.allowed_tools,
                        request_context_bound=row['actor'] == context['actor'] == author.actor,
                        output_hash_bound=bool(result['text']) and result['output_hash'] == row['output_hash'] == digest(result['text']),
                        final_provider_equals_runtime=bool(submitted) and submitted[-1]['text'] == result['text'],
                        agent_no_approval_tools=not set(cfg['allowed_tools']) & {'review', 'apply'},
                        data_version_unchanged=audit.lookup(author.actor)['data_version'] == before_version,
                        wire_output1024=all(json.loads((output / 'wire' / (case + '_http' + str(i) + '_request.json')).read_text())['max_output_tokens'] == 1024 for i in range(1, transport.sent + 1)))
                    if mode == 'intake':
                        checks['intake_only_unapproved_draft'] = (after['snapshots'] == before['snapshots']
                            and after['review_events'] == before['review_events'] and len(after['drafts']) == 1
                            and after['drafts'][0]['status'] not in ('approved', 'applied'))
                    else:
                        checks['business_rows_unchanged'] = before == after
                    if offline:
                        checks['raw_offline_final_equals_runtime'] = bool(stub.normalized) and stub.normalized[-1]['text'] == result['text']
                    source = next((t['result'] for t in result.get('tool_trace', [])
                        if mode in MODULES and t['name'] == MODULES[mode].TOOLS[0]), None)
                    usage = [dict(request=e['request'], usage=e['usage'], missing=e['usage_missing'])
                        for e in ledger.rows() if e.get('case') == case and e['event'] == 'http_result']
                    report = dict(case=case, mode=mode, variant=variant, cases=cases,
                        execution_mode='offline_http_double' if offline else 'actual_paid', settings=limits,
                        fixture_inputs_sha256=fixture_hash, fixture_task_sha256=baseline_hash,
                        task=task, request_context=context, database_schema=database.schema,
                        runtime=result, answer=answer, provider_inputs=views, provider_submissions=submitted,
                        final_submission=getattr(guarded, 'final_submission', None), provider_calls=provider.calls,
                        provider_config=provider.public_config(), persisted_runtime=row, checks=checks,
                        passed=all(v is True for v in checks.values()), evaluation_error=evaluation_error,
                        allowed_http_requests=4, http_requests=transport.sent,
                        actual_api_attempts=0 if offline else transport.sent, usage=usage,
                        service_query_raw=source, before_business_rows=before, after_business_rows=after)
                    path = output / 'reports' / (case + '.json')
                    _persist(path, report)
                    layer = output / 'layers' / case
                    layer.mkdir()
                    for field in ('provider_inputs', 'provider_submissions', 'final_submission', 'runtime', 'answer', 'service_query_raw'):
                        _persist(layer / (field + '.json'), report[field])
                    (layer / 'runtime_text.txt').write_text(result.get('text') or '', encoding='utf-8')
                    _persist(layer / 'python_explanation.json', dict(
                        scope='Python-owned selected/copy fields; no free-form prose quality claim',
                        fields={k: answer[k] for k in ('change_summary', 'dispatch_summary', 'customer_reply', 'confirmations')
                                if isinstance(answer, dict) and k in answer}))
                    reports.append(dict(case=case, passed=report['passed'], report=str(path),
                        report_sha256=_sha(path), output_hash=result['output_hash'], http_requests=transport.sent,
                        actual_api_attempts=report['actual_api_attempts'], fixture_task_sha256=baseline_hash))
                    if not report['passed'] or any(e['event'] == 'stopped' for e in ledger.rows()):
                        raise Blocked('automatic/runtime/transport failure at ' + case)
                    if offline:
                        ledger.accept(case, True, dict(case=case, passed=True, bound_output_hash=result['output_hash'],
                            kind='offline_wiring_scaffold_only', offline_simulated_review=True))
                    else:
                        request = dict(kind='separate_korean_semantic_review', case=case, report=str(path),
                                       report_sha256=_sha(path), bound_output_hash=result['output_hash'])
                        persist('reviews/' + case + '.requested.json', request)
                        review_path = output / 'reviews' / (case + '.json')
                        _persist(review_path, review_wait(copy.deepcopy(request)))
                        accept_after_review(ledger, case, path, review_path)
                    if mode == 'intake' and 'cq01_promise' in cases:
                        promise_task = _approve_intake(host, audit, answer, reviewer, offline=offline,
                            review_wait=review_wait, persist=persist, input_root=input_root)
                        reopened = Service(database, principals={author.actor: author.principal()})
                        try:
                            promise = must(reopened.delivery_round(author.actor, promise_task['delivery_round_id'],
                                                                  version=promise_task['data_version']))
                            values = b_agent.promise_values(promise['data'])
                            golden = json.loads((input_root / 'data/order_fulfillment/b_contract_expected.json').read_text())
                            assert values == {k: golden[k] for k in values}
                            persist('cq01_reopened_promise.json', dict(raw=promise, independent_golden_equal=True))
                        finally:
                            reopened.close()
                finally:
                    audit.close()
            summary = dict(passed=True, offline=offline, cases=cases, settings=limits,
                completed_representatives=len(reports), representatives=reports,
                http_requests=sum(r['http_requests'] for r in reports),
                actual_api_attempts=sum(r['actual_api_attempts'] for r in reports),
                fixture_inputs_sha256=fixture_hash, live_quality_verified=not offline,
                cq01_intake_alone_unapproved='cq01_intake' in cases and 'cq01_promise' not in cases)
    except BaseException as error:
        ledger.stop(active or cases[0], type(error).__name__)
        persist('stopped.json', dict(stopped=True, case=active, error_type=type(error).__name__,
            reports=reports, actual_api_attempts=0 if offline else sum(e['event'] == 'reserved' for e in ledger.rows()),
            automatic_paid_rerun=False))
        raise
    finally:
        import psycopg
        from psycopg import sql
        dropped = False
        try:
            if schemas:
                with psycopg.connect(database_url, autocommit=True) as db:
                    for schema in schemas:
                        assert re.fullmatch(r'test_[a-f0-9]{32}', schema)
                        db.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
            dropped = True
        except BaseException as error:
            ledger.stop(active or cases[0], 'cleanup:' + type(error).__name__)
            if not (output / 'stopped.json').exists():
                persist('stopped.json', dict(stopped=True, case=active,
                    error_type='cleanup:' + type(error).__name__, automatic_paid_rerun=False))
            raise
        finally:
            persist('cleanup.json', dict(owned_schemas=schemas, dropped=dropped,
                                        input_roots_restored=True))
    persist('summary.json', summary)
    return summary
