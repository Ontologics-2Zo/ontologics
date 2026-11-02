"""B AI-review boundaries using synthetic responses, never human approval."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from examples.order_fulfillment import (
    b_model_connection as connection, b_model_run as runner)
from examples.order_fulfillment.b_task_workflow import CASES
from ontologics.service import DomainError
from tests.db import database_for_test
from tests.order_fulfillment.test_b_model_run import snapshot, synthetic_preflight


def accepted_ai_review(kind, packet):
    return dict(reviewer='codex:test-semantic-reviewer', reviewer_kind='ai',
        comment='합성 검증 의견: 고정 업무 값·미확인 상태와 한국어 설명을 대조했습니다.',
        accepted=True)


class AIReviewInputTests(unittest.TestCase):
    def setUp(self):
        self.packet = dict(author='agent:b_delivery', case='cq04_normal',
            execution_mode='openai', answer_producer='llm',
            answer_ko='미등록 가상 입력의 납기 검토 설명입니다.',
            evidence=[dict(evidence_id='fixture-evidence', origin='real', data_version=1)],
            material=dict(source_text='고정 입력 원문'), budget=dict(attempted_calls={}, cost={}),
            result_path='/private/tmp/b-ai-review-fixture/result.json')

    def test_ai_console_requires_direct_decision_and_displays_result_before_input(self):
        events = []
        answers = iter(['codex:semantic-reviewer', '직접 입력한 AI 검토 의견', '통과'])

        def ask(prompt):
            events.append(('ask', prompt))
            return next(answers)

        record = runner.terminal_ai_review('semantic', self.packet, ask=ask,
            emit=lambda value: events.append(('emit', value)))
        first_input = next(index for index, event in enumerate(events) if event[0] == 'ask')
        display = '\n'.join(str(value) for kind, value in events[:first_input] if kind == 'emit')
        for expected in (self.packet['answer_ko'], 'AI', self.packet['result_path']):
            self.assertIn(expected, display)
        self.assertEqual('ai', record['reviewer_kind'])
        self.assertEqual('직접 입력한 AI 검토 의견', record['comment'])
        self.assertTrue(record['accepted'])
        self.assertIsNone(runner._validate_ai_review('semantic', self.packet, record))

    def test_ai_review_cannot_impersonate_human_or_approve_business_candidate(self):
        good = accepted_ai_review('semantic', self.packet)
        self.assertIsNone(runner._validate_ai_review('semantic', self.packet, good))
        for changes in (dict(reviewer_kind='human'), dict(reviewer_kind=None),
                        dict(reviewer=''), dict(reviewer=self.packet['author']),
                        dict(reviewer=connection.SUBJECT), dict(comment=''),
                        dict(comment=' '), dict(accepted=False), dict(accepted=1)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runner._validate_ai_review('semantic', self.packet, dict(good, **changes))
        unmarked = dict(good)
        del unmarked['reviewer_kind']
        with self.assertRaises(ValueError):
            runner._validate_ai_review('semantic', self.packet, unmarked)
        with self.assertRaises(ValueError):
            runner._validate_ai_review('business', self.packet, good)
        with self.assertRaises(ValueError):
            runner._validate_review('semantic', self.packet, good)

    def test_representative_cost_cap_counts_unknown_and_inflight_reservations(self):
        budget = Mock()
        cost = dict(reported_cost_usd='7.20', unreported_reserved_usd='0.10',
                    in_flight_reserved_usd='0.20')
        budget.snapshot.return_value = dict(cost=cost)
        self.assertIsNone(runner._check_case_cost(budget, Decimal('7.00'), '0.50'))
        for key in ('reported_cost_usd', 'unreported_reserved_usd', 'in_flight_reserved_usd'):
            excessive = dict(cost)
            excessive[key] = str(Decimal(excessive[key]) + Decimal('0.00000001'))
            budget.snapshot.return_value = dict(cost=excessive)
            with self.subTest(field=key), self.assertRaises(DomainError) as caught:
                runner._check_case_cost(budget, Decimal('7.00'), '0.50')
            self.assertEqual('COST_BUDGET_EXCEEDED', caught.exception.code)

    def test_console_eof_never_becomes_accepted_ai_review(self):
        def ended(prompt):
            raise EOFError('synthetic input ended')
        with self.assertRaises(EOFError):
            runner.terminal_ai_review('semantic', self.packet, ask=ended, emit=lambda _: None)


class TechnicalCQ01ProofTests(unittest.TestCase):
    """Synthetic receipts test proof selection, not an actual execution claim."""
    def fixture(self):
        directory = Path('/synthetic/cq01-proof')
        data = dict(synthetic_input='fixture-only')
        context = dict(data_version=1, schema_version='graph-0.1')
        task = Mock()
        task.data.return_value = data
        task.context.to_dict.return_value = context
        module = Mock()
        implementation = Mock()
        implementation.return_value.validate_result.return_value = True
        prior = dict(run_id=str(directory), transport='openai', cases=['cq01_intake'],
            business_storage=False, safe_inputs=runner.TASK_INPUTS,
            source_sha256='historical-code-digest',
            input_sha256={'cq01_intake': runner.json_sha256(data)})
        prior['plan_sha256'] = runner.json_sha256(prior)
        payload = dict(extraction=dict(status='ready', nodes=[dict(id='synthetic-fixture-node')]))
        docs = {
            'model_plan.json': prior,
            'settings.json': dict(model_run=prior),
            'authorization.json': runner.authorization(prior, approver='test-operator', confirmed=True),
            'shutdown.json': dict(server_stopped=True),
            'campaign/cq01_intake/input.json': data,
            'campaign/cq01_intake/result.json': dict(execution_mode='openai', synthetic_usage=False,
                actual_api_attempts=1, provider_calls=[dict(http_status=200, model_text='{}')],
                result=dict(payload=payload, context=context, error=None,
                            runtime=dict(ok=True, tool_trace=[]))),
            'campaign/cq01_intake/review_packet.json': dict(execution_mode='openai',
                result_sha256='synthetic-result-sha', draft_version=1,
                draft_sha256=runner.json_sha256(payload['extraction']['nodes']))}
        return directory, docs, ((module, implementation), task)

    def proof(self, directory, docs, role_task):
        def read(path):
            return deepcopy(docs[str(path.relative_to(directory))])
        with patch.object(runner, 'checked_path', side_effect=lambda path: path), \
                patch.object(runner, '_read', side_effect=read), \
                patch.object(runner, 'sha', return_value='synthetic-result-sha'), \
                patch.object(runner, 'case_task', return_value=role_task), \
                patch.object(runner, '_proof', return_value={'synthetic_proof': True}) as proof:
            value = runner._after_cq01_technical(directory, 'openai')
        return value, proof.call_args

    def test_old_automatic_success_uses_current_role_check_without_inventing_human_files(self):
        directory, docs, role_task = self.fixture()
        before = deepcopy(docs)
        value, call = self.proof(directory, docs, role_task)
        self.assertEqual({'synthetic_proof': True}, value)
        self.assertEqual(before, docs)
        names = call.args[1]
        self.assertIn('campaign/cq01_intake/result.json', names)
        self.assertNotIn('campaign/cq01_intake/semantic_review.json', names)
        self.assertNotIn('campaign/cq01_intake/business_review.json', names)
        self.assertNotIn('model_validation.json', names)
        role_task[0][0].SPEC.validate_payload.assert_called_once()
        role_task[0][1].return_value.validate_result.assert_called_once()

    def test_mock_failure_tamper_and_running_server_cannot_be_technical_success(self):
        result_name = 'campaign/cq01_intake/result.json'
        packet_name = 'campaign/cq01_intake/review_packet.json'
        changes = (
            lambda d: d[result_name].update(execution_mode='mock_http'),
            lambda d: d[result_name].update(synthetic_usage=True),
            lambda d: d[result_name]['result']['runtime'].update(ok=False),
            lambda d: d['model_plan.json'].update(plan_sha256='changed-plan-hash'),
            lambda d: d[result_name]['result']['payload']['extraction']['nodes'].append(dict(id='changed-node')),
            lambda d: d[packet_name].update(result_sha256='changed-result-hash'),
            lambda d: d['shutdown.json'].update(server_stopped=False))
        for index, change in enumerate(changes):
            directory, docs, role_task = self.fixture()
            change(docs)
            with self.subTest(change=index), self.assertRaises(ValueError):
                self.proof(directory, docs, role_task)


class AIReviewCampaignTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        temporary = tempfile.TemporaryDirectory(prefix='b-ai-review-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.snapshot = snapshot()
        self.preflight = synthetic_preflight(self.root / 'preflight', self.snapshot)
        self.output = self.root / 'new-run'
        self.network = []
        for target in ('urllib.request.build_opener', 'urllib.request.urlopen',
                       'socket.create_connection',
                       'ontologics.openai_provider.OpenAISettings.from_environment'):
            guard = patch(target, side_effect=AssertionError('No real network or credentials'))
            self.network.append(guard.start())
            self.addCleanup(guard.stop)

    def tearDown(self):
        for guard in self.network:
            guard.assert_not_called()

    def plan(self, **options):
        values = dict(preflight=self.preflight, output=self.output,
            cases=list(CASES[1:]), policy_snapshot=self.snapshot, review_mode='ai_semantic')
        values.update(options)
        return runner.build_plan(**values)

    def execute(self, plan, *, review=accepted_ai_review):
        approval = runner.authorization(plan, approver='test-operator', confirmed=True)
        return runner.run_campaign(database=self.database, output=self.output,
            plan=plan, approval=approval, review=review, emit=lambda _: None)

    def test_nine_mock_cases_record_ai_review_without_human_or_business_approval(self):
        plan = self.plan()
        self.assertEqual('ai_semantic', plan['review_mode'])
        self.assertEqual('1.00', plan['representative_max_cost_usd'])
        events = []

        def review(kind, packet):
            events.append((packet['case'], kind))
            self.assertTrue(Path(packet['result_path']).is_file())
            return accepted_ai_review(kind, packet)

        summary = self.execute(plan, review=review)
        self.assertTrue(summary['passed'])
        self.assertEqual('mock_http', summary['execution_mode'])
        self.assertEqual(0, summary['actual_api_attempts'])
        self.assertEqual(36, summary['mock_http_attempts'])
        self.assertEqual(9, summary['completed_representatives'])
        self.assertEqual('not_performed', summary['human_review'])
        self.assertEqual('not_performed', summary['business_approval'])
        self.assertEqual('completed', summary['ai_semantic_review'])
        self.assertEqual('technical', summary['validation_scope'])
        self.assertFalse(summary['live_quality_verified'])
        self.assertFalse(summary['b_business_stored'])
        self.assertEqual([(case, 'semantic') for case in CASES[1:]], events)
        for case in CASES[1:]:
            with self.subTest(case=case):
                directory = self.output / case
                record = json.loads((directory / 'ai_semantic_review.json').read_text())
                self.assertEqual('ai', record['reviewer_kind'])
                self.assertTrue(record['synthetic_review'])
                self.assertTrue(record['accepted'])
                self.assertFalse((directory / 'semantic_review.json').exists())
                self.assertFalse((directory / 'business_review.json').exists())

    def test_ai_rejection_preserves_actual_result_and_stops_later_mock_cases(self):
        plan = self.plan()
        events = []

        def reject(kind, packet):
            events.append((packet['case'], kind))
            return dict(accepted_ai_review(kind, packet), accepted=False)

        with self.assertRaises(ValueError):
            self.execute(plan, review=reject)
        first = self.output / CASES[1]
        self.assertTrue((first / 'result.json').is_file())
        self.assertFalse(json.loads((first / 'ai_semantic_review.json').read_text())['accepted'])
        self.assertEqual([(CASES[1], 'semantic')], events)
        self.assertFalse((self.output / CASES[2]).exists())
        stopped = json.loads((self.output / 'stopped.json').read_text())
        self.assertEqual(4, stopped['mock_http_attempts'])
        self.assertEqual(0, stopped['actual_api_attempts'])
        self.assertFalse((self.output / 'summary.json').exists())

    def test_ai_mode_is_explicit_and_cannot_be_changed_under_existing_plan_hash(self):
        default = runner.build_plan(preflight=self.preflight, output=self.output,
            cases=list(CASES[1:]), policy_snapshot=self.snapshot)
        self.assertEqual('human', default.get('review_mode', 'human'))
        with self.assertRaises(ValueError):
            self.plan(review_mode='automatic_human')
        plan = self.plan()
        self.assertIsNone(runner.validate_plan(plan))
        mutated = deepcopy(plan)
        mutated['review_mode'] = 'human'
        with self.assertRaises(ValueError):
            runner.validate_plan(mutated)

    def test_remaining_suffix_runs_only_seven_cases_under_reduced_bound_cap(self):
        cases = list(CASES[3:])
        plan = self.plan(cases=cases, representative_max_cost_usd='0.48741350')
        self.assertEqual(cases, plan['cases'])
        self.assertEqual('0.48741350', plan['representative_max_cost_usd'])
        self.assertIsNone(runner.validate_plan(plan))
        events = []

        def review(kind, packet):
            events.append(packet['case'])
            return accepted_ai_review(kind, packet)

        summary = self.execute(plan, review=review)
        self.assertTrue(summary['passed'])
        self.assertEqual(cases, events)
        self.assertEqual(cases, summary['cases'])
        self.assertEqual(7, summary['completed_representatives'])
        self.assertEqual(28, summary['mock_http_attempts'])
        self.assertEqual(0, summary['actual_api_attempts'])
        self.assertEqual('not_performed', summary['human_review'])
        self.assertEqual('not_performed', summary['business_approval'])
        self.assertFalse(summary['live_quality_verified'])
        for case in CASES[:3]:
            self.assertFalse((self.output / case).exists())
        for case in cases:
            directory = self.output / case
            self.assertTrue((directory / 'ai_semantic_review.json').is_file())
            self.assertFalse((directory / 'semantic_review.json').exists())
            self.assertFalse((directory / 'business_review.json').exists())

    def test_single_cq05_retry_does_not_call_other_cases_or_reset_cap(self):
        plan = self.plan(cases=['cq05_normal'], representative_max_cost_usd='0.48318725')
        self.assertEqual(['cq05_normal'], plan['cases'])
        self.assertEqual('0.48318725', plan['representative_max_cost_usd'])
        self.assertIsNone(runner.validate_plan(plan))
        with self.assertRaises(ValueError):
            self.plan(cases=['cq05_normal'], transport='openai')
        summary = self.execute(plan)
        self.assertTrue(summary['passed'])
        self.assertEqual(1, summary['completed_representatives'])
        self.assertEqual(4, summary['mock_http_attempts'])
        self.assertEqual(0, summary['actual_api_attempts'])
        self.assertEqual('not_performed', summary['human_review'])
        self.assertEqual('not_performed', summary['business_approval'])
        for case in CASES:
            self.assertEqual(case == 'cq05_normal', (self.output / case).exists())

    def test_ai_suffix_selection_preserves_order_and_live_predecessor_requirement(self):
        for cases in (list(CASES), list(CASES[3:4] + CASES[5:]),
                      list(reversed(CASES[3:])), list(CASES[3:]) + [CASES[-1]], []):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                self.plan(cases=cases)
        with self.assertRaises(ValueError):
            self.plan(cases=list(CASES[3:]), transport='openai')
        default = runner.build_plan(preflight=self.preflight, output=self.output,
            cases=list(CASES[1:]), policy_snapshot=self.snapshot)
        self.assertEqual('human', default.get('review_mode', 'human'))
        self.assertFalse(self.output.exists())

    def test_representative_cap_is_bounded_and_bound_to_the_plan_hash(self):
        for cap in (None, True, 0.49, Decimal('0.49'), '', 'NaN', 'Infinity',
                    '-Infinity', '0', '-0.01', '1.00000001'):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                self.plan(representative_max_cost_usd=cap)
        plan = self.plan(representative_max_cost_usd='0.48741350')
        mutated = deepcopy(plan)
        mutated['representative_max_cost_usd'] = '0.49'
        with self.assertRaises(ValueError):
            runner.validate_plan(mutated)
        mutated = deepcopy(plan)
        mutated['representative_max_cost_usd'] = '1.01'
        mutated.pop('plan_sha256')
        mutated['plan_sha256'] = runner.json_sha256(mutated)
        with self.assertRaises(ValueError):
            runner.validate_plan(mutated)
