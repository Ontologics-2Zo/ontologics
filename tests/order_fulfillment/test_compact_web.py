"""B demo HTTP + disposable PostgreSQL checks; providers are explicit test doubles."""
import hashlib
import json
import threading
import unittest
from unittest.mock import Mock, patch

from examples.order_fulfillment.b_model_connection import MODEL
from ontologics.database import connect
from ontologics.identity import DevelopmentContext
from ontologics.model import ROOT
from ontologics.openai_provider import ProviderError
from ontologics.order_fulfillment import web
from ontologics.paid_budget import CostPolicy, ModelPrice
from ontologics.service import DomainError
from ontologics.web import WebApplication
from tests.test_web import WebCase


class ScriptedSections:
    """Use the existing CQ provider and add test prose only after real tool results."""
    def __init__(self, role, task, *, sections=True):
        self.task = task
        self.delegate = role[2](task)
        self.sections = sections and role[0].SPEC.name.endswith('response')
        self.tool_observations = []

    def public_config(self):
        return {'provider': 'scripted-http-test-not-live-llm'}

    def __call__(self, *, model, messages):
        observations = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        self.tool_observations.extend(observations)
        result = self.delegate(model=model, messages=messages)
        if self.sections and observations and result.get('text'):
            value = json.loads(result['text'])
            value['answer_ko'] = ('## 업무 요약\n' + value['answer_ko']
                + '\n## 출고 요청 초안\n물류 담당자께: 미출고 배정 검토용이며 실제 출고 신청은 아닙니다.'
                + '\n## 고객 회신 초안\n고객사 담당자께: 조건부 납품 전망을 검토 중이며 새 일정은 미합의입니다.'
                + '\n## 담당자 확인\n물류 정보·고객 동의·출고 능력을 확인해 주세요.')
            result = dict(result, text=json.dumps(value, ensure_ascii=False))
        return result


class DraftSectionTests(unittest.TestCase):
    def test_logistics_keeps_nested_headings_and_their_model_authored_content(self):
        logistics = '물류 담당자께\n### 검토 물량\n미출고 600개를 검토해 주세요.\n### 운송 확인\n차량은 미확인입니다.'
        answer = ('## 업무 요약\n조건부 검토\n## 출고 요청 초안\n' + logistics
                  + '\n## 고객 회신 초안\n고객 협의 전입니다.\n## 담당자 확인\n확인이 필요합니다.')
        sections = web.draft_sections(answer)
        self.assertEqual(logistics, sections['logistics'])
        self.assertEqual('고객 협의 전입니다.', sections['customer'])

    def test_duplicate_required_section_is_rejected_instead_of_overwriting_prose(self):
        answer = ('## 업무 요약\n요약\n## 출고 요청 초안\n첫 요청\n'
                  '## 고객 회신 초안\n회신\n## 담당자 확인\n확인\n'
                  '## 출고 요청 초안\n서로 다른 두 번째 요청')
        with self.assertRaises(DomainError) as raised:
            web.draft_sections(answer)
        self.assertEqual('DRAFT_FORMAT_INCOMPLETE', raised.exception.code)


class CompactWebTests(WebCase):
    """Reuse the common seed, identity, HTTP client and isolated-schema cleanup."""
    def setUp(self):
        self.providers = []
        self.policy = CostPolicy('100', (ModelPrice(MODEL, '1', '2', 400000),))
        super().setUp()

    def scripted_provider(self, role, task):
        provider = ScriptedSections(role, task)
        self.providers.append(provider)
        return provider

    def start(self, user, factory=None):
        app = web.make_application(self.database, DevelopmentContext('dev-' + user),
            cost_policy=self.policy, task_provider_factory=self.scripted_provider)
        server = web.FulfillmentServer(app, 0)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(stop)
        self.clients[user] = server
        context = self.ok(self.http(server, '/api/context'))['data']
        self.assertEqual('dev-' + user, context['identity']['subject'])
        server.test_token = context['csrf_token']
        return server

    def query(self, action, checksum=None, *, server=None, version=1):
        arguments = {'version': version}
        if action != 'demo_contract':
            arguments['input_sha256'] = checksum
        return self.http(server or self.alice, '/api/fulfillment/query',
                         {'action': action, 'arguments': arguments})

    def catalog(self):
        return self.ok(self.query('demo_contract'))['data']['contracts']

    def rows(self, table):
        # Names are test-owned constants. Values never expose connection settings.
        from psycopg import sql
        with connect(self.database) as db:
            return db.execute(sql.SQL('SELECT * FROM {} ORDER BY 1, 2').format(
                sql.Identifier(table))).fetchall()

    def business_state(self):
        return {name: self.rows(name) for name in ('snapshots', 'drafts', 'review_events')}

    def test_catalog_exact_source_hashes_and_existing_forecast_comparison_arithmetic(self):
        before = self.business_state()
        contracts = self.catalog()
        self.assertEqual(2, len(contracts))
        self.assertEqual(2, len({row['input_sha256'] for row in contracts}))
        expected = {
            'B-DEMO-NORMAL-800': (800, 0, '15:30:00', 750, 50, '16:30:00'),
            'B-DEMO-RISK-800': (450, 350, '22:00:00', 400, 400, '23:00:00'),
        }
        self.assertEqual(set(expected), {r['contract']['contract_number'] for r in contracts})
        for row in contracts:
            with self.subTest(contract=row['contract']['contract_number']):
                raw = (ROOT / row['input_path']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row['input_sha256'])
                self.assertEqual(raw.decode(), row['source_text'])
                self.assertEqual('unregistered', row['contract']['input_state'])
                self.assertEqual(800, row['contract']['quantity'])
                self.assertEqual('2026-10-30T10:00:00+09:00', row['as_of'])
                self.assertTrue(row['model_ready'])
                self.assertTrue(row['cost_ready'])
                forecast = self.ok(self.query('demo_forecast', row['input_sha256']))
                self.assertEqual('unregistered', forecast['input_state'])
                self.assertEqual(1, forecast['data_version'])
                comparison = self.ok(self.query('demo_compare', row['input_sha256']))['data']['assessment']
                a, b, at, c, d, ct = expected[row['contract']['contract_number']]
                state = forecast['data']['forecast']
                self.assertEqual((a, b, '2026-10-30T' + at + '+09:00'),
                    (state['on_time_quantity'], state['shortfall_quantity'], state['estimated_full_arrival_at']))
                self.assertEqual((c, d, '2026-10-30T' + ct + '+09:00'),
                    tuple(comparison['after']['forecast'][key] for key in (
                        'on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at')))
                self.assertFalse(comparison['execution_performed'])
                self.assertEqual(3600, comparison['time_deltas']['estimated_full_arrival_seconds'])
        self.assertEqual([], self.providers)
        self.assertEqual(before, self.business_state())

    def test_user_transport_minutes_recalculate_only_the_comparison_without_business_writes(self):
        before = self.business_state()
        for row in self.catalog():
            checksum = row['input_sha256']
            original = self.ok(self.query('demo_forecast', checksum))['data']
            current = original['forecast']['condition']['transport_seconds'] // 60
            source = ROOT / row['operations_source']['path']
            original_bytes = source.read_bytes()
            for minutes in (0, current - 30, current, current + 90, 10080):
                with self.subTest(contract=row['contract']['contract_number'], minutes=minutes):
                    response = self.ok(self.http(self.alice, '/api/fulfillment/query', dict(
                        action='demo_compare', arguments=dict(version=1, input_sha256=checksum,
                                                              transport_minutes=minutes))))['data']
                    assessment = response['assessment']
                    old, new = assessment['before'], assessment['after']
                    self.assertEqual(minutes * 60, new['forecast']['condition']['transport_seconds'])
                    self.assertEqual(original['production']['estimated_completion_at'],
                                     new['production']['estimated_completion_at'])
                    self.assertEqual(old['dispatch_request']['delivered_quantity'],
                                     new['dispatch_request']['delivered_quantity'])
                    self.assertEqual(old['dispatch_request']['in_transit_quantity'],
                                     new['dispatch_request']['in_transit_quantity'])
                    self.assertEqual((minutes-current)*60,
                                     assessment['time_deltas']['estimated_full_arrival_seconds'])
                    self.assertEqual(minutes, response['comparison_input']['transport_minutes'])
                    self.assertFalse(assessment['execution_performed'])
                    if minutes == current:
                        self.assertEqual(0, assessment['quantity_deltas']['on_time_quantity'])
                    for ref in response['review_material']['comparison']['after']['response_plan']['forecast']['condition']['evidence']:
                        self.assertTrue(ref.startswith('evidence:transport:'))
            self.assertEqual(original_bytes, source.read_bytes())
            self.assertEqual(original, self.ok(self.query('demo_forecast', checksum))['data'])
        self.assertEqual([], self.providers)
        self.assertEqual(before, self.business_state())

    def test_transport_input_is_bounded_and_cannot_be_injected_into_other_actions(self):
        checksum = self.catalog()[0]['input_sha256']
        for value in (None, True, -1, 10081, 1.5, '180', {}, []):
            with self.subTest(value=value):
                self.failed(self.http(self.alice, '/api/fulfillment/query', dict(
                    action='demo_compare', arguments=dict(version=1, input_sha256=checksum,
                                                          transport_minutes=value))), 400, 'INVALID_INPUT')
                self.failed(self.http(self.alice, '/api/fulfillment/query', dict(
                    action='demo_response', arguments=dict(version=1, input_sha256=checksum,
                                                           transport_minutes=value))), 400, 'INVALID_INPUT')
        self.failed(self.http(self.alice, '/api/fulfillment/query', dict(
            action='demo_intake', arguments=dict(version=1, input_sha256=checksum,
                                                 transport_minutes=180))), 400, 'INVALID_INPUT')
        self.assertEqual([], self.providers)

    def test_response_uses_the_exact_compared_condition_and_original_stays_available(self):
        before = self.business_state()
        for row in self.catalog():
            checksum = row['input_sha256']
            source = ROOT / row['operations_source']['path']
            source_bytes = source.read_bytes()
            original = self.ok(self.query('demo_response', checksum))['data']
            current = original['response_basis']['transport_minutes']
            self.assertEqual('original', original['response_basis']['condition_origin'])
            for minutes in (0, current + 90):
                with self.subTest(contract=row['contract']['contract_number'], minutes=minutes):
                    args = dict(version=1, input_sha256=checksum, transport_minutes=minutes)
                    comparison = self.ok(self.http(self.alice, '/api/fulfillment/query', dict(
                        action='demo_compare', arguments=args)))['data']
                    response = self.ok(self.http(self.alice, '/api/fulfillment/query', dict(
                        action='demo_response', arguments=args)))['data']
                    forecast = response['assessment']['forecast']
                    compared = comparison['assessment']['after']
                    self.assertEqual(dict(transport_minutes=minutes, condition_origin='compared'),
                                     response['response_basis'])
                    for key in ('condition', 'on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at'):
                        self.assertEqual(compared['forecast'][key], forecast[key])
                    for key, value in compared['dispatch_request'].items():
                        self.assertEqual(value, response['assessment']['dispatch_request'][key])
                    task_data = self.providers[-1].task.data()
                    self.assertEqual(response['assessment'], task_data['assessment'])
                    self.assertTrue(task_data['input_revision'].startswith('transport-'))
                    self.assertEqual(minutes * 60, task_data['review_material']['forecast_details']['condition']['transport_seconds'])
                    self.assertTrue(any(ref['source_id'].startswith('source:transport:') for ref in task_data['input_sources']))
                    self.assertEqual({'summary', 'logistics', 'customer', 'checks'}, set(response['sections']))
                    self.assertFalse(response['assessment']['execution_performed'])
            restored = self.ok(self.query('demo_response', checksum))['data']
            self.assertEqual(original['assessment'], restored['assessment'])
            self.assertEqual(source_bytes, source.read_bytes())
        self.assertEqual(before, self.business_state())

    def test_real_runtime_lookup_and_current_browser_identity_leave_business_records_unchanged(self):
        existing = self.draft('preserved-shared-draft')
        self.ok(self.http(self.bob, '/api/review', self.review_body(
            existing, decision='request_changes', key='preserved-review')))
        before = self.business_state()
        self.assertTrue(before['drafts'])
        self.assertTrue(before['review_events'])
        checksum = self.catalog()[0]['input_sha256']
        intake = self.ok(self.query('demo_intake', checksum))['data']
        self.assertEqual('unregistered', intake['input_state'])
        self.assertTrue(intake['needs_human_review'])
        self.assertTrue(intake['evidence_ids'])
        self.assertEqual('ready', intake['extraction']['status'])
        self.ok(self.http(self.alice, '/api/development-user', {'user': 'reader'}))
        context = self.ok(self.http(self.alice, '/api/context'))['data']
        self.alice.test_token = context['csrf_token']
        self.assertEqual(['read'], context['identity']['permissions'])
        response = self.ok(self.query('demo_response', checksum))['data']
        self.assertEqual({'summary', 'logistics', 'customer', 'checks'}, set(response['sections']))
        for section in response['sections'].values():
            self.assertTrue(section)
            self.assertIn(section, response['answer_ko'])
        self.assertFalse(response['assessment']['execution_performed'])
        self.assertEqual(2, len(self.providers))
        self.assertTrue(all(p.tool_observations for p in self.providers))
        self.assertTrue(all(r['ok'] for p in self.providers for r in p.tool_observations))
        runs = {r['id']: r for r in self.rows('runtime_runs')}
        self.assertEqual('user:dev-alice', runs[intake['run_id']]['actor'])
        self.assertEqual('user:dev-reader', runs[response['run_id']]['actor'])
        self.assertTrue(any(r['tool_name'] == 'lookup' for r in self.rows('runtime_steps')))
        self.assertEqual(before, self.business_state())

    def test_exact_input_contract_rejects_hash_version_and_caller_configuration_before_model(self):
        checksum = self.catalog()[0]['input_sha256']
        body = {'action': 'demo_intake', 'arguments': {'version': 1, 'input_sha256': checksum}}
        invalid = [
            {**body, 'action': []},
            {**body, 'action': {}},
            {**body, 'actor': 'user:dev-bob'},
            {**body, 'model_settings': {'api_key': 'caller-key'}},
            {**body, 'cost_policy': '/private/config'},
            {**body, 'arguments': {**body['arguments'], 'source_text': 'caller contract'}},
            {**body, 'arguments': {**body['arguments'], 'input_sha256': '0' * 64}},
            {**body, 'arguments': {**body['arguments'], 'input_sha256': []}},
            *[{**body, 'arguments': {**body['arguments'], 'version': version}}
              for version in (0, -1, True, '1', 1.5)],
            {'action': 'demo_contract', 'arguments': {'version': 1, 'input_sha256': checksum}},
        ]
        for value in invalid:
            with self.subTest(body=value):
                self.failed(self.http(self.alice, '/api/fulfillment/query', value), 400, 'INVALID_INPUT')
        self.failed(self.query('demo_intake', checksum, version=999), 404, 'VERSION_NOT_FOUND')
        self.assertEqual([], self.providers)

    def test_changed_source_hash_is_stale_and_cannot_run_model(self):
        checksum = self.catalog()[0]['input_sha256']
        original = web.load_input
        def changed(path):
            value = original(path)
            return dict(value, sha256='f' * 64) if value['sha256'] == checksum else value
        with patch.object(web, 'load_input', side_effect=changed):
            self.failed(self.query('demo_intake', checksum), 409, 'STALE_REVISION')
        self.assertEqual([], self.providers)

    def test_missing_model_or_cost_configuration_fails_without_running_a_provider(self):
        checksum = self.catalog()[0]['input_sha256']
        factory = Mock(side_effect=AssertionError('Provider must not start'))
        self.alice.app.task_provider_factory = None
        self.failed(self.query('demo_intake', checksum), 503, 'AI_NOT_CONFIGURED')
        catalog = self.ok(self.query('demo_contract'))['data']
        self.assertFalse(catalog['model_ready'])
        self.alice.app.task_provider_factory = factory
        self.alice.app.host._cost_policy = None
        self.failed(self.query('demo_response', checksum), 503, 'COST_POLICY_REQUIRED')
        self.assertFalse(self.ok(self.query('demo_contract'))['data']['cost_ready'])
        factory.assert_not_called()

    def test_provider_failure_and_incomplete_draft_do_not_fabricate_sections(self):
        checksum = self.catalog()[0]['input_sha256']
        before = self.business_state()
        class FailedProvider:
            def public_config(self):
                return {'provider': 'scripted-failure-not-live-llm'}
            def __call__(self, **kwargs):
                raise ProviderError('OPENAI_AUTHENTICATION_FAILED', 'Injected safe model failure')
        self.alice.app.task_provider_factory = lambda role, task: FailedProvider()
        failed = self.failed(self.query('demo_response', checksum), 502, 'OPENAI_AUTHENTICATION_FAILED')
        self.assertIsNone(failed['data'])
        self.alice.app.task_provider_factory = lambda role, task: ScriptedSections(role, task, sections=False)
        incomplete = self.failed(self.query('demo_response', checksum), 400, 'DRAFT_FORMAT_INCOMPLETE')
        self.assertIsNone(incomplete['data'])
        self.assertEqual(before, self.business_state())

    def test_common_csrf_and_origin_rejection_precede_b_model_execution(self):
        checksum = self.catalog()[0]['input_sha256']
        body = {'action': 'demo_response', 'arguments': {'version': 1, 'input_sha256': checksum}}
        for headers, code in (({'X-CSRF-Token': 'wrong'}, 'CSRF_REJECTED'),
                              ({'Origin': 'http://attacker.invalid'}, 'ORIGIN_REJECTED')):
            with self.subTest(code=code):
                self.failed(self.http(self.alice, '/api/fulfillment/query', body, headers=headers), 403, code)
        self.assertEqual([], self.providers)

    def test_b_stylesheet_uses_shared_csp_and_same_origin_guard(self):
        status, content, headers = self.http(self.alice, '/static/b-fulfillment.css')
        self.assertEqual(200, status)
        self.assertEqual('text/css', headers.get_content_type())
        self.assertIn(b'#b-compact-fulfillment', content)
        _, _, common_headers = self.http(self.alice, '/api/context')
        self.assertEqual(common_headers['Content-Security-Policy'], headers['Content-Security-Policy'])
        self.assertIn("style-src 'self'", headers['Content-Security-Policy'])
        self.failed(self.http(self.alice, '/static/b-fulfillment.css',
            headers={'Origin': 'http://attacker.invalid'}), 403, 'ORIGIN_REJECTED')

    def test_a_c_and_shared_routes_keep_common_dispatch(self):
        original = WebApplication.dispatch
        with patch.object(WebApplication, 'dispatch', autospec=True, side_effect=original) as delegated:
            self.ok(self.http(self.alice, '/api/dashboard'))
            self.failed(self.http(self.alice, '/api/planning/get', {}), 400, 'INVALID_INPUT')
            self.failed(self.http(self.alice, '/api/business/draft', {'domain': 'claims'}), 400, 'INVALID_INPUT')
            self.failed(self.query('delivery_round', 'irrelevant'), 400, 'INVALID_INPUT')
        self.assertEqual(['/api/dashboard', '/api/planning/get', '/api/business/draft',
                          '/api/fulfillment/query'], [call.args[1] for call in delegated.call_args_list])
        self.assertEqual([], self.providers)
