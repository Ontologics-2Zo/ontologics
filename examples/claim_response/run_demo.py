"""C 1st phase end to end: claim file -> confirmed claim -> trace -> scopes -> request drafts.

Same pattern as ontologics.demo: a fresh PostgreSQL schema, scripted development
identities (not real operational approval) and a JSON summary in artifacts/local.
No model or paid API call is made. Request drafts stop at awaiting_review because
reviewing and sending them is a person's job.

The claim trace also runs once through the common host as the registered C Agent
(AgentSpec -> AgentRegistration.from_spec -> request.run_task) with a scripted
provider, so the typed result and its host-resolved evidence references are shown.
With --live-agent (7-1) the same task runs once on the project's fixed OpenAI model with
the request cost policy from .env.local; the answer is accepted only when it equals the
trace recomputed from the model's own reads. Nothing else in the demo uses a model.

C 2nd phase (with --results): after the scripted review of the request drafts, the test
request is drafted and approved (scripted "sent to the test team"), the test team's CSV
is read, the reproduction quantity is computed and the result + reproduction draft stops
at awaiting_review with an unsent request document for A. C decides no disposition.
"""
import argparse
import json
from pathlib import Path
import uuid

from ontologics import __version__
from ontologics.claim_response.registration import CLAIM_RESPONSE, SPEC, ClaimTraceAgent, expected_trace
from ontologics.contracts import AgentTask
from ontologics.harness import PinnedContext
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.runtime import ModelConfig
from ontologics.database import environment_values
from ontologics.openai_provider import OpenAIProvider, OpenAISettings
from ontologics.paid_budget import cost_policy_from_environment
from ontologics.service import DomainError
from ontologics.claim_response.fixtures import claim_response_nodes, load_operations
from ontologics.claim_response.intake import convert_claim
from ontologics.claim_response.reproduction import (
    c_operations_replacement, read_test_results, reproduction_requirement)
from ontologics.claim_response.requests import (
    REQUEST_DIR, REVIEW_LABELS, prepare_containment, prepare_reproduction_request, prepare_test_request,
    serial_ranges, write_request)
from ontologics.claim_response.trace import (
    MATCHED, c_operations_shipments, c_operations_stock, candidate_scope, stored_scope, trace_claim)
from ontologics.demo import fixture_nodes
from ontologics.model import ROOT
from ontologics.service import Service

PRINCIPALS = {
    'seed-admin': {'kind': 'human', 'roles': ['read', 'seed', 'audit']},
    'claim-agent': {'kind': 'agent', 'roles': ['read', 'draft']},
    'reviewer': {'kind': 'human', 'roles': ['read', 'review', 'audit']},
    'workflow': {'kind': 'system', 'roles': ['read', 'apply']},
}
CLAIMS = ROOT / 'data/claim_response/claims'
DEFAULT_CLAIM = CLAIMS / 'claim_table.csv'
CONFIGURATION = 'config:esp32-poe:L:WROOM'
COLLECTED_AT = '2026-10-26T10:30:00+09:00'
PREPARED_AT = '2026-10-26T10:45:00+09:00'
TEST_RESULTS = ROOT / 'data/claim_response/test_results'
TEST_REQUESTED_AT = '2026-10-26T11:00:00+09:00'
RESULTS_COLLECTED_AT = '2026-10-28T09:00:00+09:00'
REPRODUCTION_AT = '2026-10-28T09:30:00+09:00'


def must(response):
    if not response['ok']:
        raise RuntimeError(json.dumps(response['errors'], ensure_ascii=False))
    return response


def approve_and_apply(svc, operations, key, note):
    """claim-agent drafts, a different human identity approves, the workflow applies."""
    draft = must(svc.prepare_draft('claim-agent', operations, request_key=key + '-draft'))['data']
    must(svc.review('reviewer', draft['draft_id'], draft['draft_version'], decision='approve', note=note,
                    request_key=key + '-review'))
    return must(svc.apply('workflow', draft['draft_id'], draft['draft_version'], request_key=key + '-apply'))


class ScriptedClaimTrace:
    """Deterministic provider for this example, not an LLM quality claim.

    Step 1 asks for the three lookups; step 2 answers from the returned tool results
    with the same trace functions the role validator uses.
    """
    def __init__(self, task):
        self.intake_id, self.version = task.data()['intake_id'], task.context.data_version

    def public_config(self):
        return {'provider': 'scripted-claim-trace-1'}

    def __call__(self, *, model, messages):
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        if not results:
            return {'text': '', 'tool_calls': [
                {'name': 'lookup', 'arguments': {'node_id': self.intake_id, 'version': self.version}},
                {'name': 'lookup', 'arguments': {'kind': 'ProductUnit', 'version': self.version}},
                {'name': 'lookup', 'arguments': {'kind': 'UnitProductionRecord', 'version': self.version}}]}
        calls = [{'name': 'lookup', 'arguments': args, 'result': result} for args, result in zip(
            ({'node_id': self.intake_id, 'version': self.version}, {'kind': 'ProductUnit', 'version': self.version},
             {'kind': 'UnitProductionRecord', 'version': self.version}), results[-3:])]
        return {'text': json.dumps(expected_trace(self.intake_id, self.version, calls), ensure_ascii=False),
                'tool_calls': []}


LIVE_CONFIG = {'max_steps': 8, 'max_tool_calls': 6}


class RecordingProvider:
    """Keeps the model's last text: the common runtime drops a final answer that the role validator rejects."""
    def __init__(self, inner):
        self.inner, self.last_text = inner, None

    @property
    def calls(self):
        return getattr(self.inner, 'calls', [])

    def public_config(self):
        return self.inner.public_config()

    def __call__(self, *, model, messages):
        response = self.inner(model=model, messages=messages)
        self.last_text = response.get('text')
        return response


def live_agent_settings(env_file=None):
    """7-1: model settings and the request cost policy, read as data from .env.local / .env.

    Fails before any network call when the key or the cost policy is missing (no default cap).
    """
    values = environment_values(env_file)
    settings = OpenAISettings.from_environment(environ=values)
    policy = cost_policy_from_environment(values)
    if policy is None:
        raise DomainError('COST_POLICY_REQUIRED', 'Set ONTOLOGICS_REQUEST_MAX_COST_USD and ONTOLOGICS_MODEL_PRICES_JSON')
    return {'settings': settings, 'cost_policy': policy, 'provider_factory': OpenAIProvider}


def run_agent_trace(database, schema, intake_id, version, live=None):
    """The registered C Agent runs the claim trace through CommonHost.request -> run_task.

    live=None uses the scripted provider (no cost). live={'settings', 'cost_policy', 'provider_factory'}
    uses the fixed model; the provider sees only the tools the spec requests.
    """
    user = UserIdentity('dev-claim-agent', frozenset({'read', 'draft'}))
    host = CommonHost(DevelopmentIdentityProvider([user], enabled=True), database, schema=schema,
                      agent_registrations=(CLAIM_RESPONSE,), cost_policy=live and live['cost_policy'])
    task = AgentTask({'intake_id': intake_id, 'question': 'Trace the confirmed claim to boards and a production line.'},
                     PinnedContext('Claim trace; no defect, cause or approval conclusion', version))
    if live is None:
        provider, config = ScriptedClaimTrace(task), ModelConfig(model='scripted-claim-trace')
    else:
        provider = RecordingProvider(live['provider_factory'](live['settings'], sorted(SPEC.requested_tools)))
        config = ModelConfig(model=live['settings'].model, **LIVE_CONFIG)
    with host.request(DevelopmentContext(user.subject)) as request:
        result = request.run_task(SPEC.name, ClaimTraceAgent(), task, provider, config=config, capture_tool_trace=True)
    if live is not None:
        result['task_intake_id'], result['task_version'] = intake_id, version
        result['provider_calls'] = list(getattr(provider, 'calls', []))
        result['provider_config'] = provider.public_config()
        result['last_model_text'] = provider.last_text
    return result


def live_report(agent, expected):
    """Safe report: status, tokens, tool names/arguments, answer and checks. No key, no HTTP body."""
    runtime = agent['runtime']
    trace = runtime.get('tool_trace') or []
    return {
        'execution_mode': 'live_api', 'provider_config': agent.get('provider_config'),
        'provider_calls': agent.get('provider_calls', []),
        'runtime': {k: runtime.get(k) for k in ('ok', 'status', 'steps', 'error', 'model', 'run_id')},
        'tool_calls': [{'name': t['name'], 'arguments': t['arguments'], 'ok': t['result'].get('ok')} for t in trace],
        'answer': agent['payload'], 'evidence_refs': len(agent['evidence_refs']),
        # For a rejected run: the model's final text (fictional data only) and the trace its reads give.
        'final_text': agent.get('last_model_text') if agent['payload'] is None else None,
        'expected_from_reads': expected_trace(agent['task_intake_id'], agent['task_version'], [
            {'name': t['name'], 'arguments': t['arguments'], 'result': t['result']} for t in trace])
        if agent['payload'] is None else None,
        'automatic_checks': {
            'runtime_completed': bool(runtime.get('ok')),
            'model_selected_tools_executed': bool(trace),
            'answer_equals_recomputed_trace': agent['payload'] is not None,
            'same_boards_and_line_as_scripted_trace': agent['payload'] is not None and all(
                agent['payload'].get(k) == expected.get(k) for k in ('matched_unit_ids', 'production_line_id')),
        },
        'note': 'One narrow task on fictional data; automatic checks are not a general quality benchmark.',
    }


def run_phase2(svc, case, line_id, hold, candidate, containment_draft, *, external_claim_id, results_path,
               request_dir):
    """6-1 ~ 6-5 after the 1st phase. Scripted reviews stand in for people; nothing is sent."""
    # The request drafts are reviewed (scripted) so the case is in the graph for the test request.
    must(svc.review('reviewer', containment_draft['draft_id'], containment_draft['draft_version'], decision='approve',
                    note='Development review: request drafts checked; sending is recorded by people.',
                    request_key='containment-review'))
    after_requests = must(svc.apply('workflow', containment_draft['draft_id'], containment_draft['draft_version'],
                                    request_key='containment-apply'))['data_version']
    # 6-1: test request to the test team (scripted approval = "a person reviewed and sent it").
    test = prepare_test_request(case, line_id, hold, prepared_at=TEST_REQUESTED_AT, external_claim_id=external_claim_id,
                                configuration_id=CONFIGURATION, candidate_result=candidate)
    approved = approve_and_apply(svc, [{'op': 'put', 'node': test['node']}], 'test-request',
                                 'Development review: test request checked and sent to the test team by a person.')
    # 6-2: the test team's results and dispositions, read as they are.
    results = read_test_results(results_path, test['node'], case, collected_at=RESULTS_COLLECTED_AT)
    if results['state'] != 'needs_review':
        raise RuntimeError('Test results are blocked: ' + json.dumps(results['issues'], ensure_ascii=False))
    # 6-3 / 6-5: reproduction quantity and the request document for A.
    replacement = c_operations_replacement(load_operations())
    reproduction = reproduction_requirement(case, test['node'], results['nodes'], replacement,
                                            configuration_id=CONFIGURATION)
    request = prepare_reproduction_request(case, reproduction, prepared_at=REPRODUCTION_AT,
                                           external_claim_id=external_claim_id)
    nodes = results['nodes'] + ([request['node']] if request['node'] else [])
    draft = must(svc.prepare_draft('claim-agent', [{'op': 'put', 'node': n} for n in nodes],
                                   request_key='reproduction-draft'))['data']
    status = must(svc.get_draft('claim-agent', draft['draft_id'], draft['draft_version']))['data']['status']
    if request['node']:
        request = prepare_reproduction_request(case, reproduction, prepared_at=REPRODUCTION_AT,
                                               external_claim_id=external_claim_id, review_status=status)
    documents = {**test['documents'], **request['documents']}
    return {
        'versions': {'after_request_review': after_requests, 'test_request_sent': approved['data_version'],
                     'after_reproduction_draft': svc._version()},
        'test_request': {'id': test['node']['id'], 'count': len(test['node']['unit_ids'])},
        'test_results': {'file': results_path.name, 'counts': results['counts']},
        'reproduction': {k: reproduction[k] for k in (
            'state', 'customer_replacement_quantity', 'stock_replacement_quantity', 'available_replacement_quantity',
            'required_good_quantity', 'unresolved_quantity', 'reason')},
        'reproduction_draft': {'draft_id': draft['draft_id'], 'draft_version': draft['draft_version'],
                               'status': status, 'review_label': REVIEW_LABELS.get(status, status), 'sending': '미송부'},
        'documents': [str(write_request(text, name, base_dir=request_dir)) for name, text in documents.items()],
    }


def existing_nodes():
    catalog = json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']
    fictional, _ = fixture_nodes()
    return catalog + fictional


def run_demo(database=None, *, schema=None, claim_path=DEFAULT_CLAIM, request_dir=REQUEST_DIR, results_path=None,
             live=None):
    svc = Service(database, schema=schema or ('claim_demo_' + uuid.uuid4().hex if database is None else None),
                  principals=PRINCIPALS)
    try:
        if svc._version() != 0:
            raise ValueError('The C demo requires a fresh schema; choose a new --schema')
        # Version 1: existing real/synthetic data + C synthetic operations (boards, lines, production records).
        seeded = must(svc.bootstrap('seed-admin', existing_nodes() + claim_response_nodes(), request_key='claim-seed'))

        # Version 2: the converted claim is registered as needs_review after a human review of the draft.
        conversion = convert_claim(claim_path, collected_at=COLLECTED_AT)
        if conversion['report']['state'] != 'needs_review':
            raise RuntimeError('Claim conversion is blocked: ' + json.dumps(conversion['report']['issues'], ensure_ascii=False))
        registered = approve_and_apply(svc, [{'op': 'put', 'node': n} for n in conversion['nodes']], 'claim-intake',
                                       'Development review: converted claim fields match the source file.')
        intake = next(n for n in conversion['nodes'] if n['type'] == 'ClaimIntake')

        # Version 3: a person confirms the claim (scripted reviewer identity in this demo).
        confirmed = approve_and_apply(svc, [{'op': 'put', 'node': dict(intake, intake_state='confirmed')}],
                                      'claim-confirm', 'Development review: customer claim confirmed for investigation.')
        version = confirmed['data_version']

        # 4-1 / 4-2 on the confirmed version through the existing read API.
        trace = trace_claim(svc, 'claim-agent', intake['id'], version=version,
                            configuration_id=conversion['report']['configuration_candidate_id'])
        case = trace['quality_case']['node']
        if case is None or trace['lines']['single_line'] is None:
            raise RuntimeError('Trace needs a person: ' + json.dumps(trace['quality_case'], ensure_ascii=False))
        line_id = trace['lines']['single_line']
        # 5A-4: the same trace through the registered C Agent (typed contract, scripted provider).
        agent = run_agent_trace(database, svc.schema, intake['id'], version)
        live_trace = None
        if live is not None:
            # 7-1: the same task once on the real model; the scripted result above is the reference.
            live_trace = live_report(run_agent_trace(database, svc.schema, intake['id'], version, live=live),
                                     agent['payload'] or {})

        # 4-3 / 4-4 with the temporary C operations basis (until the shared B stock/shipment types).
        records = must(svc.lookup('claim-agent', kind='UnitProductionRecord', version=version))['data']
        operations = load_operations()
        stock, shipments = c_operations_stock(operations), c_operations_shipments(operations)
        hold = stored_scope(case, line_id, records, stock, shipments)
        candidate = candidate_scope(case, line_id, records, shipments)

        # 5-1 / 5-2 / 5-3: request drafts wait for a human review; nothing is sent.
        containment = prepare_containment(case, line_id, records, hold, prepared_at=PREPARED_AT,
                                          external_claim_id=intake['external_claim_id'],
                                          configuration_id=CONFIGURATION)
        extra = [candidate['node']] if candidate['node'] else []
        draft = must(svc.prepare_draft('claim-agent', [{'op': 'put', 'node': n} for n in [case, *containment['nodes'], *extra]],
                                       request_key='containment-draft'))['data']
        status = must(svc.get_draft('claim-agent', draft['draft_id'], draft['draft_version']))['data']['status']
        again = prepare_containment(case, line_id, records, hold, prepared_at=PREPARED_AT,
                                    external_claim_id=intake['external_claim_id'], configuration_id=CONFIGURATION,
                                    review_status=status)
        documents = [str(write_request(text, name, base_dir=request_dir)) for name, text in again['documents'].items()]
        after_drafts, phase2 = svc._version(), None
        if results_path is not None:
            phase2 = run_phase2(svc, case, line_id, hold, candidate, draft, external_claim_id=intake['external_claim_id'],
                                results_path=Path(results_path), request_dir=request_dir)

        return {
            'contract_version': __version__, 'database_schema': svc.schema,
            'mode': 'development demonstration; scripted human identities, not real operational approval; no model calls',
            'versions': {'seed': seeded['data_version'], 'claim_registered': registered['data_version'],
                         'claim_confirmed': version, 'after_request_drafts': after_drafts},
            'claim': {'file': conversion['report']['file'], 'external_claim_id': intake['external_claim_id'],
                      'claimed_quantity': intake['claimed_quantity'], 'reported_serials': intake['reported_serials']},
            'links': {'matched': [l['unit_id'] for l in trace['links'] if l['match_state'] == MATCHED],
                      'open': [l for l in trace['links'] if l['match_state'] != MATCHED]},
            'production_line_id': line_id,
            'agent_trace': {'ok': agent['runtime']['ok'], 'agent_name': agent['agent_name'],
                            'agent_version': agent['agent_version'], 'schema_version': agent['schema_version'],
                            'payload': agent['payload'], 'evidence_refs': len(agent['evidence_refs']),
                            'error': agent['error']},
            'quality_case_id': case['id'],
            'hold_scope': {'state': hold['state'], 'basis': hold.get('basis'),
                           'units': serial_ranges(hold['node']['unit_ids']) if hold['node'] else None,
                           'count': len(hold['node']['unit_ids']) if hold['node'] else 0},
            'candidate_scope': {'state': candidate['state'], 'basis': candidate.get('basis'),
                                'units': serial_ranges(candidate['node']['unit_ids']) if candidate['node'] else None,
                                'count': len(candidate['node']['unit_ids']) if candidate['node'] else 0},
            'requests': [{'id': n['id'], 'kind': n['request_kind'], 'scope_id': n['scope_id']}
                         for n in containment['nodes'] if n['type'] == 'ContainmentRequest'],
            'skipped_requests': containment['skipped'],
            'request_draft': {'draft_id': draft['draft_id'], 'draft_version': draft['draft_version'],
                              'status': status, 'review_label': REVIEW_LABELS.get(status, status),
                              'sending': '미송부', 'on_site': '미확인'},
            'documents': documents,
            'phase2': phase2,
            'live_agent_trace': live_trace,
        }
    finally:
        svc.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', default=None, help='Fresh PostgreSQL schema; default unique claim_demo_<id>')
    parser.add_argument('--claim', default=str(DEFAULT_CLAIM), help='Claim file (.csv, .xlsx or .eml)')
    parser.add_argument('--results', default=None,
                        help='Test-team result CSV for the 2nd phase, e.g. scenario_1 or a path; omitted = 1st phase only')
    parser.add_argument('--live-agent', action='store_true',
                        help='7-1: also run the claim trace once on the fixed OpenAI model (paid; needs key and cost policy)')
    parser.add_argument('--env-file', default=None, help='Default .env then .env.local; process environment wins')
    parser.add_argument('--output', default='artifacts/local/claim_response/run_demo.json')
    args = parser.parse_args()
    results = args.results
    if results and not results.endswith('.csv'):
        results = TEST_RESULTS / (results + '.csv')
    live = None
    if args.live_agent:
        try:
            live = live_agent_settings(args.env_file)
        except DomainError as exc:  # ProviderError is a DomainError: safe code/message only, no secret.
            print(json.dumps({'status': 'configuration_blocked', 'error': {'code': exc.code, 'message': exc.message}},
                             ensure_ascii=False, indent=2))
            return 1
    summary = run_demo(schema=args.schema, claim_path=Path(args.claim), results_path=results, live=live)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'schema': summary['database_schema'], 'output': str(output), 'versions': summary['versions'],
                      'claim': summary['claim']['external_claim_id'], 'matched_boards': len(summary['links']['matched']),
                      'production_line': summary['production_line_id'],
                      'agent_trace': {k: summary['agent_trace'][k] for k in ('ok', 'agent_name', 'agent_version', 'evidence_refs')},
                      'hold': summary['hold_scope'], 'candidate': summary['candidate_scope'],
                      'request_draft': summary['request_draft'], 'documents': summary['documents'],
                      'phase2': summary['phase2'] and {k: summary['phase2'][k] for k in (
                          'versions', 'test_results', 'reproduction', 'reproduction_draft', 'documents')},
                      'live_agent_trace': summary['live_agent_trace'] and {k: summary['live_agent_trace'][k] for k in (
                          'runtime', 'tool_calls', 'answer', 'final_text', 'expected_from_reads', 'automatic_checks')}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
