"""B-only development web composition over the unchanged common web/runtime.

Run with ``python -m ontologics.order_fulfillment.web serve --help``.
The two Markdown inputs remain unregistered. Only common runtime/audit records
are written by model execution; no business seed, approval or apply is invoked.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import re

from examples.order_fulfillment import b_model_connection as connection
from examples.order_fulfillment import b_mail_intake_task as mail_intake
from examples.order_fulfillment.b_mail_input import (
    INPUTS, MAX_TRANSPORT_MINUTES, case_task, load_input, operations_source, transport_comparison, transport_response)
from examples.order_fulfillment.b_model_preflight import load_policy, policy_from_snapshot
from examples.order_fulfillment.b_response_task import ResponseAgent
from ..database import DatabaseError, DatabaseSettings, environment_values
from ..embeddings import OpenAIEmbeddings
from ..graph_views import GraphSettings
from ..harness import canonical
from ..host import AgentRegistration, CommonHost
from ..identity import DevelopmentContext, DevelopmentIdentityProvider, IdentityError
from ..model import ROOT
from ..openai_provider import OpenAIProvider, OpenAISettings, ProviderError
from ..paid_budget import CostLedger, cost_policy_from_environment
from ..responses import exact_keys, failure
from ..service import DomainError
from ..web import USERS, Handler, WebApplication, WebServer


MODEL_CASES = {'demo_intake': 'cq01_intake', 'demo_response': 'cq05_mixed'}
DEMO_ACTIONS = frozenset((*MODEL_CASES, 'demo_contract', 'demo_forecast', 'demo_compare'))
SECTION_NAMES = ('업무 요약', '출고 요청 초안', '고객 회신 초안', '담당자 확인')
COMPACT_CSS = """
#view-business:has(#b-compact-fulfillment) .page-heading .eyebrow,
#view-business:has(#b-compact-fulfillment) #business-note{display:none}
#view-business:has(#b-compact-fulfillment[data-records-open="false"])>.business-layout{display:none}
#b-compact-fulfillment{display:grid;gap:16px;min-width:0}
#b-compact-fulfillment .card{margin:0;padding:20px;min-width:0}
#b-compact-fulfillment .quick-actions{margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--line)}
#b-compact-fulfillment .record-detail-panel{display:grid;gap:16px;min-width:0}
#b-compact-fulfillment h3,#b-compact-fulfillment h4,#b-compact-fulfillment p{margin:0}
#b-compact-fulfillment h4{font-size:13px;line-height:1.6}
#b-compact-fulfillment button{justify-self:start;max-width:100%;white-space:normal;overflow-wrap:anywhere}
#b-compact-fulfillment .work-links{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:16px}
#b-compact-fulfillment .work-links button{width:100%;line-height:1.7;min-width:0}
#b-compact-fulfillment .work-links button[aria-pressed="true"]{background:var(--accent-light);color:var(--ink);border-color:var(--accent)}
#b-compact-fulfillment .b-metrics{display:grid;grid-template-columns:1fr 1fr 1.5fr;gap:14px;min-width:0}
#b-compact-fulfillment .metric{display:block;min-width:0;min-height:112px;padding:16px}
#b-compact-fulfillment .metric strong{font-size:28px;line-height:1.4;letter-spacing:-.5px;overflow-wrap:anywhere}
#b-compact-fulfillment .metric.b-arrival strong{font-size:19px}
#b-compact-fulfillment .b-arrival span{display:block;font-size:23px;margin-top:3px}
#b-compact-fulfillment .b-two-column{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;align-items:start;min-width:0}
#b-compact-fulfillment .b-region{display:grid;gap:14px;padding:18px;border:1px solid var(--line);border-radius:8px;min-width:0;background:#fff}
#b-compact-fulfillment .b-comparison{border-top:3px solid var(--accent);background:#f8fbfc}
#b-compact-fulfillment .b-transport-inputs{display:flex;gap:12px;align-items:end;flex-wrap:wrap}
#b-compact-fulfillment .b-transport-inputs label{width:110px}
#b-compact-fulfillment .b-transport-inputs input{margin-top:6px;width:100%}
#b-compact-fulfillment .b-comparison-output{display:grid;gap:14px;min-width:0}
#b-compact-fulfillment details{margin:0;padding:0;border:1px solid var(--line);border-radius:7px;min-width:0}
#b-compact-fulfillment summary{padding:13px 16px;line-height:1.6;cursor:pointer;font-weight:650}
#b-compact-fulfillment details[open]{display:block;padding:0 16px 16px}
#b-compact-fulfillment details[open]>summary{margin:0 -16px 14px;border-bottom:1px solid var(--line)}
#b-compact-fulfillment details[open]>:not(summary)+*{margin-top:14px}
#b-compact-fulfillment .table-wrap{min-width:0;width:100%;max-height:none;overflow:auto}
#b-compact-fulfillment table{width:100%;table-layout:fixed;border-collapse:collapse}
#b-compact-fulfillment th,#b-compact-fulfillment td{padding:12px 10px;vertical-align:top;line-height:1.7;white-space:normal;overflow-wrap:anywhere;word-break:normal}
#b-compact-fulfillment td:first-child,#b-compact-fulfillment th:first-child{width:40%}
#b-compact-fulfillment thead th{position:static}
#b-compact-fulfillment .b-comparison-output table{min-width:620px}
#b-compact-fulfillment .b-comparison-output td:first-child,#b-compact-fulfillment .b-comparison-output th:first-child{width:22%}
#b-compact-fulfillment .source-box{margin:0;padding:16px;max-height:none;overflow-wrap:anywhere;white-space:pre-wrap;line-height:1.9;min-width:0}
#b-compact-fulfillment pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;max-width:100%}
#b-compact-fulfillment .b-mail{background:#f8fafb;border:1px solid var(--line);border-radius:6px}
#b-compact-fulfillment .check{display:flex;align-items:flex-start;gap:8px;margin:0}
#b-compact-fulfillment .check input{width:auto;margin-top:4px;flex-shrink:0}
@media(max-width:960px){
 #b-compact-fulfillment .b-two-column{grid-template-columns:minmax(0,1fr)}
}
@media(max-width:600px){
 #b-compact-fulfillment .card{padding:14px}
 #b-compact-fulfillment .work-links,#b-compact-fulfillment .b-metrics{grid-template-columns:minmax(0,1fr)}
 #b-compact-fulfillment .b-region{padding:14px}
}
"""


class FulfillmentHandler(Handler):
    """One B stylesheet, with the unchanged shared origin guards and headers."""

    def handle_method(self, method):
        if method == 'GET' and self.path == '/static/b-fulfillment.css':
            try:
                self.guard()
                return self.send_content(200, COMPACT_CSS.encode(), 'text/css; charset=utf-8')
            except DomainError as error:
                return self.respond(failure(error.code, str(error)))
        return super().handle_method(method)


class FulfillmentServer(WebServer):
    def __init__(self, app, port):
        super().__init__(app, port)
        self.RequestHandlerClass = FulfillmentHandler


class DraftAgent(ResponseAgent):
    """Presentation headings only; the existing Task owns facts and validation."""

    def messages(self, task):
        facts = task.data()['assessment']
        quantities = facts['dispatch_request']
        clarification = (
            f"현재 확보분은 {quantities['currently_available_quantity']}개, 미래 생산분은 "
            f"{quantities['future_quantity']}개, 기한 내 새 출고 검토량은 "
            f"{quantities['on_time_new_dispatch_quantity']}개입니다. "
            '기한 내 검토량에는 미래 생산분이 포함될 수 있습니다. 현재 확보와 출고 가능 확정도 다릅니다. '
            '전체 기한 내 검토량을 현재 확보량으로 바꾸거나 즉시 출고하라고 작성하지 마세요. '
            '운송 시간은 초 단위 코드 대신 시·분으로 표현하세요. ')
        if facts['forecast']['shortfall_quantity'] == 0:
            clarification += ('이 입력은 기한 내 전량 도착 예상입니다. 변경 일정이 제안되거나 합의된 사실이 없으므로 '
                              '변경 일정 동의가 미완료라고 단정하거나 필수 확인 항목으로 만들지 마세요. ')
        return super().messages(task) + [dict(role='user', content=clarification + (
            '화면에서 두 초안을 구분할 수 있도록 answer_ko에 다음 제목을 각각 독립된 줄에 '
            '정확히 사용하세요: ## 업무 요약, ## 출고 요청 초안, ## 고객 회신 초안, ## 담당자 확인. '
            '각 초안에는 실제 수신자에게 전달할 본문을 작성하세요.'))]


def draft_sections(answer):
    """Split model-authored sections without inventing replacement prose."""
    headings = [match for match in re.finditer(r'^\s*#{1,6}\s+(.+?)\s*$', answer, re.M)
                if match.group(1).strip().strip('*').strip() in SECTION_NAMES]
    values = {}
    for index, match in enumerate(headings):
        title = match.group(1).strip().strip('*').strip()
        if title in SECTION_NAMES:
            if title in values:
                raise DomainError('DRAFT_FORMAT_INCOMPLETE', '초안 제목이 중복되었습니다. 초안 작성을 다시 실행하세요.')
            end = headings[index + 1].start() if index + 1 < len(headings) else len(answer)
            values[title] = answer[match.end():end].strip()
    if any(not values.get(name) for name in SECTION_NAMES):
        raise DomainError('DRAFT_FORMAT_INCOMPLETE', '초안 구분이 불완전합니다. 초안 작성을 다시 실행하세요.')
    return dict(zip(('summary', 'logistics', 'customer', 'checks'),
                    (values[name] for name in SECTION_NAMES)))


class FulfillmentApplication(WebApplication):
    """Handle B actions only; all other routes retain the common application."""

    def __init__(self, host, context, *, model_settings=None, provider_factory=None,
                 task_provider_factory=None):
        super().__init__(host, context, provider_factory=provider_factory)
        self.model_settings = model_settings
        # Test injection is trusted constructor state, never HTTP input.
        self.task_provider_factory = task_provider_factory
        self.inputs = {value['sha256']: value for value in (
            load_input(ROOT / 'data/order_fulfillment/examples' / name) for name in INPUTS)}

    def dispatch(self, path, body=None):
        if (path == '/api/fulfillment/query' and isinstance(body, dict)
                and 'action' in body and not isinstance(body['action'], str)):
            raise DomainError('INVALID_INPUT', '실행할 B 기능을 문자열로 지정하세요.')
        if (path == '/api/fulfillment/query' and isinstance(body, dict)
                and body.get('action') in DEMO_ACTIONS):
            exact_keys(body, ('action', 'arguments'))
            action, args = body['action'], body['arguments']
            exact_keys(args, ('version',) if action == 'demo_contract' else ('version', 'input_sha256'),
                       ('transport_minutes',) if action in ('demo_compare', 'demo_response') else ())
            if 'transport_minutes' in args and (type(args['transport_minutes']) is not int
                    or not 0 <= args['transport_minutes'] <= MAX_TRANSPORT_MINUTES):
                raise DomainError('INVALID_INPUT', '운송 시간은 0분부터 168시간까지 정수 분으로 입력하세요.')
            if type(args['version']) is not int or args['version'] < 1:
                raise DomainError('INVALID_INPUT', '조회 가능한 승인 데이터 버전이 필요합니다.')
            if action in MODEL_CASES:
                if not self.model_lock.acquire(blocking=False):
                    return failure('AI_BUSY', '다른 AI 요청이 처리 중입니다. 완료 후 다시 시도하세요.')
                try:
                    return self.demo(action, args)
                finally:
                    self.model_lock.release()
            return self.demo(action, args)
        return super().dispatch(path, body)

    def input_source(self, input_hash):
        if not isinstance(input_hash, str) or input_hash not in self.inputs:
            raise DomainError('INVALID_INPUT', '표시된 계약 중 하나를 선택하세요.')
        value = self.inputs[input_hash]
        if load_input(ROOT / value['path']) != value:
            raise DomainError('STALE_REVISION', '계약 입력이 변경되었습니다. 서버와 화면을 새로 불러오세요.')
        return value

    def readiness(self):
        model_ready = self.model_settings is not None or self.task_provider_factory is not None
        policy = self.host._cost_policy
        cost_ready = False
        if policy is not None:
            try:
                CostLedger(policy).reserve(connection.MODEL,
                    connection.INPUT_TOKEN_RESERVATION_FLOOR, 4096)
                cost_ready = True
            except DomainError:
                pass
        return dict(model_ready=model_ready, cost_ready=cost_ready)

    def checked_catalog(self, request, value, version):
        """Require real version-pinned catalog reads even for pure calculations."""
        _, task = case_task('cq01_intake', value, version=version)
        response = None
        for node in task.data()['catalog_nodes']:
            response = request.call('lookup', dict(node_id=node['id'], version=version))
            if not response['ok']:
                raise DomainError(response['errors'][0]['code'], '계약의 제품 근거를 조회하지 못했습니다.')
            if response['data_version'] != version or response['data'] != [node]:
                raise DomainError('CATALOG_INPUT_MISMATCH', '계약 입력과 승인 제품 정보가 다릅니다. 제품 근거를 확인하세요.')
        return response

    def demo(self, action, args):
        version = args['version']
        values = (list(self.inputs.values()) if action == 'demo_contract'
                  else [self.inputs.get(args['input_sha256'])] if isinstance(args['input_sha256'], str) else [None])
        with self.host.request(self.context) as request:
            selected = []
            for cached in values:
                value = self.input_source(cached['sha256'] if cached else None)
                envelope = self.checked_catalog(request, value, version)
                selected.append(value)
            if action == 'demo_contract':
                contracts = []
                for value in selected:
                    _, promise = case_task('cq01_promise', value, version=version)
                    _, forecast = case_task('cq04_mixed', value, version=version)
                    contract = promise.data()['promise']
                    contract['contract_number'] = contract['contract_id'].removeprefix('contract:demo:')
                    contracts.append(dict(contract=contract, source_text=value['text'],
                        input_sha256=value['sha256'], input_path=value['path'],
                        operations_source=operations_source(value),
                        as_of=forecast.data()['assessment']['as_of'],
                        queried_at=forecast.data()['assessment']['queried_at'], **self.readiness()))
                data = dict(contracts=contracts, **self.readiness())
            elif action == 'demo_forecast':
                tasks = {case: case_task(case, selected[0], version=version)[1].data() for case in (
                    'cq02_normal', 'cq03_normal', 'cq04_mixed', 'cq05_mixed')}
                data = dict(fulfillment=tasks['cq02_normal']['assessment'],
                    production=tasks['cq03_normal']['assessment'],
                    forecast=tasks['cq04_mixed']['assessment'],
                    details=tasks['cq04_mixed']['calculation_details'],
                    confirmations=tasks['cq05_mixed']['review_material']['confirmations'])
            elif action == 'demo_compare':
                if 'transport_minutes' in args:
                    task, assumption = transport_comparison(selected[0], args['transport_minutes'], version=version)
                else:
                    _, task = case_task('cq06_transport2h', selected[0], version=version)
                    assumption = None
                data = {key: task.data()[key] for key in ('assessment', 'review_material')}
                if assumption is not None:
                    data['comparison_input'] = assumption
            else:
                if self.model_settings is None and self.task_provider_factory is None:
                    return failure('AI_NOT_CONFIGURED', '서버의 모델 설정이 필요합니다.')
                if not self.readiness()['cost_ready']:
                    return failure('COST_POLICY_REQUIRED', '서버에 실행 가능한 비용 한도·모델 가격을 설정하세요.')
                assumption = None
                if action == 'demo_response' and 'transport_minutes' in args:
                    role, task, assumption = transport_response(selected[0], args['transport_minutes'], version=version)
                else:
                    role, task = case_task(MODEL_CASES[action], selected[0], version=version)
                module, agent, _ = role
                implementation = DraftAgent() if action == 'demo_response' else agent()
                provider_type = (mail_intake.MailProvider if module is mail_intake
                                 else connection._CatalogEvidenceProvider)
                provider = (self.task_provider_factory(role, task) if self.task_provider_factory else
                    provider_type(self.model_settings, module.SPEC.requested_tools,
                                  task=task, name=module.SPEC.name))
                result = request.run_task(module.SPEC.name, implementation, task, provider,
                    config=connection.CONFIG, capture_tool_trace=False)
                if not result['runtime']['ok']:
                    error = result.get('error') or result['runtime'].get('error') or {}
                    return failure(error.get('code', 'AGENT_RESULT_REJECTED'),
                                   'AI 실행을 완료하지 못했습니다. 입력을 유지한 채 다시 실행하세요.')
                data = dict(result['payload'], run_id=result['runtime']['run_id'])
                if action == 'demo_response':
                    data['sections'] = draft_sections(data['answer_ko'])
                    data['response_basis'] = dict(
                        transport_minutes=task.data()['assessment']['forecast']['condition']['transport_seconds'] // 60,
                        condition_origin='compared' if assumption is not None else 'original')
            if action != 'demo_contract':
                data['operations_source'] = operations_source(selected[0])
            return dict(envelope, data=data, evidence=[], input_state='unregistered')


def make_application(database, context, *, identities=None, model_settings=None,
                     cost_policy=None, graph_settings=None, task_provider_factory=None):
    """Compose existing shared identity, registration, model and approval owners."""
    from ..materials.library_review import REGISTRATION as PLANNING_REGISTRATION
    registrations = [PLANNING_REGISTRATION]
    for case in MODEL_CASES.values():
        role, _ = case_task(case, load_input(ROOT / 'data/order_fulfillment/examples' / INPUTS[0]))
        spec = (replace(role[0].SPEC, limits=connection.ROLE_LIMITS) if role[0] is mail_intake
                else connection.model_spec(role[0]))
        registrations.append(AgentRegistration.from_spec(spec))
    factory = (lambda names: OpenAIProvider(model_settings, names)) if model_settings else None
    host = CommonHost(identities or DevelopmentIdentityProvider(USERS, enabled=True), database,
        agent_registrations=tuple(registrations), cost_policy=cost_policy, graph_settings=graph_settings,
        embedding_provider=OpenAIEmbeddings(model_settings) if model_settings else None)
    return FulfillmentApplication(host, context, model_settings=model_settings, provider_factory=factory,
                                  task_provider_factory=task_provider_factory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('serve',))
    parser.add_argument('--development', required=True, action='store_true')
    parser.add_argument('--schema', required=True)
    parser.add_argument('--env-file')
    parser.add_argument('--model-env-file')
    parser.add_argument('--graph-backend', choices=('postgres', 'rdf', 'neo4j'), default='postgres')
    parser.add_argument('--graph-env-file')
    parser.add_argument('--cost-policy', help='Existing public cost-policy JSON; no secrets')
    parser.add_argument('--user', choices=('alice', 'bob', 'reader'), default='alice')
    parser.add_argument('--port', type=int, default=8064)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('Use a port in 1024..65535')
    try:
        database = DatabaseSettings.from_environment(schema=args.schema, env_file=args.env_file)
        values = environment_values(args.model_env_file)
        settings = OpenAISettings.from_environment(environ=values) if values.get('OPENAI_API_KEY') else None
        if settings:
            settings = replace(settings, timeout_seconds=30, max_output_tokens=4096)
        policy = (policy_from_snapshot(load_policy(args.cost_policy)) if args.cost_policy
                  else cost_policy_from_environment(values))
        graph = GraphSettings.from_environment(env_file=args.graph_env_file or args.env_file,
                                              backend=args.graph_backend)
        app = make_application(database, DevelopmentContext('dev-' + args.user),
                               model_settings=settings, cost_policy=policy, graph_settings=graph)
        context = app.dispatch('/api/context')
        if not context['ok']:
            print(canonical(dict(ok=False, errors=context['errors'])))
            return 1
        server = FulfillmentServer(app, args.port)
        print(canonical(dict(url=server.origin, mode='development', schema=database.schema,
                             **app.readiness())), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (DatabaseError, DomainError, IdentityError, ProviderError, OSError, ValueError):
        print(canonical(dict(ok=False, message='B 서버를 시작하지 못했습니다. DB·모델·비용 설정과 포트를 확인하세요.')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
