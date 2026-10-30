"""Run approvals and human-review boundaries using only synthetic local responses.

The fixture review decisions are explicit test inputs, never human approval or
actual LLM success. Every database test owns a new schema in the test cluster.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.order_fulfillment import (
    b_model_connection as connection, b_model_preflight as preflight,
    b_model_run as runner, b_run as cli, b_run_isolation as isolation)
from examples.order_fulfillment.b_input_task import json_sha256
from examples.order_fulfillment.b_task_workflow import CASES, case_task
from tests.db import database_for_test
from tests.order_fulfillment.test_b_model_preflight import public_values


def snapshot():
    values = public_values()
    return dict(values=values, sha256=json_sha256(values))


def synthetic_preflight(directory, policy):
    """Construct a bounded unit fixture; it is not an executed preflight claim."""
    directory.mkdir()
    digest = isolation.source_digest()
    isolation.save(directory / 'settings.json', dict(workflow='input_tasks', mode='free',
        source_sha256=digest, safe_inputs=isolation.TASK_INPUTS, model_preflight=policy))
    isolation.save(directory / 'shutdown.json', dict(server_stopped=True))
    campaign = directory / 'campaign'
    campaign.mkdir()
    profile = connection.policy_summary(preflight.policy_from_snapshot(policy), CASES)
    isolation.save(campaign / 'summary.json', dict(passed=True, execution_mode='mock_http',
        actual_api_attempts=0, cases=CASES, model_connection=profile))
    for case in CASES:
        path = campaign / case
        path.mkdir()
        isolation.save(path / 'input.json', case_task(case)[1].data())
        isolation.save(path / 'result.json', dict(execution_mode='mock_http', actual_api_attempts=0,
            result=dict(runtime={'ok': True}, error=None)))
    isolation.save(directory / 'preflight_validation.json', dict(passed=True,
        source_sha256=digest, safe_inputs=isolation.TASK_INPUTS,
        summary_sha256=isolation.sha(campaign / 'summary.json'),
        shutdown_sha256=isolation.sha(directory / 'shutdown.json')))
    return directory


def accepted_review(kind, packet):
    record = dict(accepted=True, reviewer='test-human-reviewer',
                  comment='테스트 입력: 원문과 답변 및 미등록 상태를 확인했습니다.')
    if kind == 'business':
        record.update(draft_version=packet['draft_version'], draft_sha256=packet['draft_sha256'])
    return record


class ModelRunPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='b-model-plan-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.snapshot = snapshot()
        self.preflight = synthetic_preflight(self.root / 'preflight', self.snapshot)
        self.output = self.root / 'new-run'
        self.plan = self.build()
        self.network = []
        for target in ('urllib.request.build_opener', 'urllib.request.urlopen',
                       'socket.create_connection', 'ontologics.openai_provider.OpenAISettings.from_environment'):
            guard = patch(target, side_effect=AssertionError('No network or real credentials'))
            self.network.append(guard.start())
            self.addCleanup(guard.stop)

    def tearDown(self):
        for guard in self.network:
            guard.assert_not_called()

    def build(self, **options):
        values = dict(preflight=self.preflight, output=self.output,
            cases=['cq01_intake'], policy_snapshot=self.snapshot)
        values.update(options)
        return runner.build_plan(**values)

    def test_plan_is_detached_bound_and_creates_no_run_resources(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.plan['approved'])
        self.assertFalse(self.plan['business_storage'])
        self.assertEqual('mock_http', self.plan['transport'])
        self.assertEqual(str(self.output), self.plan['run_id'])
        self.assertEqual(json_sha256(case_task('cq01_intake')[1].data()),
                         self.plan['input_sha256']['cq01_intake'])
        self.assertEqual(4096, self.plan['profile']['provider_config']['max_output_tokens'])
        self.assertIsNone(runner.validate_plan(self.plan))
        self.plan['policy_snapshot']['values']['ONTOLOGICS_REQUEST_MAX_COST_USD'] = '500'
        self.assertEqual('50', self.snapshot['values']['ONTOLOGICS_REQUEST_MAX_COST_USD'])
        with self.assertRaises(ValueError):
            runner.validate_plan(self.plan)

    def test_explicit_preflight_omission_is_bound_to_ai_technical_plan_and_approval(self):
        with patch.object(runner, '_preflight') as free, patch.object(runner, '_check_proof') as proof:
            plan = self.build(preflight=None, cases=list(CASES[-2:]),
                              review_mode='ai_semantic', skip_preflight=True)
            self.assertIsNone(runner.validate_plan(plan))
            free.assert_not_called()
            proof.assert_not_called()
        self.assertTrue(plan['preflight_skipped'])
        self.assertIsNone(plan['preflight'])
        approval = runner.authorization(plan, approver='test-operator', confirmed=True)
        self.assertIsNone(runner.validate_authorization(plan, approval))
        changed = deepcopy(plan)
        changed.pop('preflight_skipped')
        with self.assertRaises(ValueError):
            runner.validate_plan(changed)
        changed['plan_sha256'] = 'different'
        with self.assertRaises(ValueError):
            runner.validate_authorization(changed, approval)
        for options in (dict(preflight=None), dict(skip_preflight=True),
                        dict(preflight=None, skip_preflight=True),
                        dict(preflight=None, skip_preflight=1, review_mode='ai_semantic')):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.build(**options)

    def test_representative_cap_defaults_to_one_dollar_and_preserves_lower_caps(self):
        for mode, cases in (('none', ['cq01_intake']), ('ai_semantic', ['cq06_defects11'])):
            options = dict(preflight=None, review_mode=mode, cases=cases)
            if mode == 'ai_semantic':
                options['skip_preflight'] = True
            with self.subTest(mode=mode):
                plan = self.build(**options)
                self.assertEqual('1.00', plan['representative_max_cost_usd'])
                self.assertIsNone(runner.validate_plan(plan))
                for cap in ('1.00', '0.50', '0.85766650'):
                    plan = self.build(**options, representative_max_cost_usd=cap)
                    self.assertEqual(cap, plan['representative_max_cost_usd'])
                    self.assertIsNone(runner.validate_plan(plan))
                for cap in ('1.00000001', '2', '0', 'NaN', 'Infinity'):
                    with self.subTest(cap=cap), self.assertRaises(ValueError):
                        self.build(**options, representative_max_cost_usd=cap)
                plan['representative_max_cost_usd'] = '0.99'
                with self.assertRaises(ValueError):
                    runner.validate_plan(plan)
                plan['representative_max_cost_usd'] = '1.01'
                plan.pop('plan_sha256')
                plan['plan_sha256'] = json_sha256(plan)
                with self.assertRaises(ValueError):
                    runner.validate_plan(plan)

    def test_normal_live_plan_allows_each_cq_and_subsets_without_demo_evidence(self):
        with patch.object(runner, '_preflight', side_effect=AssertionError('No demo preflight')), \
                patch.object(runner, '_check_proof', side_effect=AssertionError('No demo proof')):
            for cases in ([case] for case in CASES):
                plan = self.build(preflight=None, review_mode='none', transport='openai', cases=cases)
                self.assertIsNone(runner.validate_plan(plan))
                self.assertIsNone(plan['preflight']); self.assertIsNone(plan['after'])
            plan = self.build(preflight=None, review_mode='none', transport='openai',
                              cases=['cq02_normal', 'cq05_mixed', 'cq06_transport2h'])
            self.assertEqual(64, plan['profile']['root_limits']['max_model_calls'])
            self.assertIsNone(runner.validate_plan(plan))
        for options in (dict(preflight=self.preflight), dict(after=self.preflight),
                        dict(skip_preflight=True)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.build(**dict(dict(preflight=None, review_mode='none'), **options))

    def test_normal_cli_records_explicit_execution_without_prompt_and_rejects_duplicate(self):
        public = self.root / 'public-policy.json'
        isolation.save(public, self.snapshot['values'])
        def prepare(output, **options):
            output.mkdir()
            return dict(output=str(output), mode=options['mode'], model_run=options['model_run'])
        for transport in (None, 'openai'):
            output = self.root / ('normal-' + str(transport))
            args = ['run', '--output', str(output), '--cost-policy', str(public), '--case', 'cq05_mixed']
            if transport:
                args += ['--transport', transport]
            with patch.object(cli, 'prepare', side_effect=prepare), \
                    patch.object(cli, '_launch_model', return_value=0) as launch, \
                    patch.object(runner, 'confirm', side_effect=AssertionError('No demo prompt')), \
                    patch('builtins.input', side_effect=AssertionError('No review wait')), \
                    patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
                self.assertEqual(0, cli.main(args))
                settings, approval = launch.call_args.args
                self.assertEqual('none', settings['model_run']['review_mode'])
                self.assertEqual('1.00', settings['model_run']['representative_max_cost_usd'])
                self.assertEqual(transport or 'mock_http', settings['model_run']['transport'])
                self.assertEqual('explicit_cli_command', approval['method'])
                self.assertIsNone(runner.validate_authorization(settings['model_run'], approval))
                self.assertEqual(1, cli.main(args))
                self.assertEqual(1, launch.call_count)

    def test_normal_cli_does_not_accept_demo_options_or_implicit_all_cases(self):
        for options in ([], ['--case', 'cq02_normal', '--after', str(self.preflight)],
                        ['--case', 'cq02_normal', '--skip-preflight'],
                        ['--case', 'cq02_normal', '--review-mode', 'human']):
            with self.subTest(options=options), patch.object(cli, 'prepare') as prepare, \
                    patch('sys.stderr', new=io.StringIO()):
                self.assertEqual(1, cli.main(['run', '--output', str(self.output),
                    '--cost-policy', '/private/tmp/not-opened-public-policy.json', *options]))
                prepare.assert_not_called()

    def test_authorization_requires_positive_exact_run_transport_and_plan(self):
        denied = runner.authorization(self.plan, approver='test-operator', confirmed=False)
        with self.assertRaises(ValueError):
            runner.validate_authorization(self.plan, denied)
        approved = runner.authorization(self.plan, approver='test-operator', confirmed=True)
        self.assertIsNone(runner.validate_authorization(self.plan, approved))
        self.assertEqual('mock_confirmation', approved['kind'])
        for changes in (dict(run_id='/private/tmp/another-run'), dict(plan_sha256='0' * 64),
                        dict(transport='openai'), dict(kind='paid_execution'), dict(approved=1),
                        dict(approver=''), dict(approved=False)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runner.validate_authorization(self.plan, dict(approved, **changes))
        live = self.build(transport='openai')
        with self.assertRaises(ValueError):
            runner.validate_authorization(live, approved)
        live_approval = runner.authorization(live, approver='test-operator', confirmed=True)
        self.assertEqual('paid_execution', live_approval['kind'])
        for name, value in (('run_id', '/private/tmp/new-run-id'), ('source_sha256', '0' * 64),
                            ('input_sha256', {}), ('cases', ['cq01_promise']),
                            ('transport', 'openai')):
            mutated = deepcopy(self.plan)
            mutated[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner.validate_plan(mutated)

    def test_missing_failed_modified_free_evidence_and_insufficient_cost_block_plan(self):
        summary = self.preflight / 'campaign/summary.json'
        original = summary.read_bytes()
        broken = json.loads(original)
        for changes in (dict(passed=False), dict(actual_api_attempts=1),
                        dict(execution_mode='openai'), dict(model_connection={})):
            summary.write_text(json.dumps(dict(broken, **changes)))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.build()
        summary.write_bytes(original)
        self.assertIsNone(runner.validate_plan(self.plan))
        summary.write_bytes(original + b'\n')
        with self.assertRaises(ValueError):
            runner.validate_plan(self.plan)
        summary.write_bytes(original)
        low = snapshot()
        low['values']['ONTOLOGICS_REQUEST_MAX_COST_USD'] = '0.001'
        low['sha256'] = json_sha256(low['values'])
        with self.assertRaises(ValueError):
            self.build(policy_snapshot=low)
        summary.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_live_remainder_cannot_skip_reviewed_cq01_or_use_all_ten_at_once(self):
        for cases in (list(CASES), list(CASES[1:]), ['cq02_normal']):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                self.build(transport='openai', cases=cases)
        with self.assertRaises(ValueError):
            self.build(cases=['cq02_normal', 'cq01_intake'])
        with self.assertRaises(ValueError):
            self.build(cases=['cq01_intake'] * 2)
        self.assertFalse(self.output.exists())

    def test_rehashed_live_plan_cannot_bypass_cq01_first_sequence(self):
        for cases in (list(CASES), list(CASES[1:]), ['cq02_normal']):
            forged = deepcopy(self.plan)
            forged.update(transport='openai', cases=cases,
                profile=connection.policy_summary(preflight.policy_from_snapshot(self.snapshot), cases),
                input_sha256={case: json_sha256(case_task(case)[1].data()) for case in cases})
            forged.pop('plan_sha256')
            forged['plan_sha256'] = json_sha256(forged)
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                runner.validate_plan(forged)

    def test_prior_cq01_review_is_bound_to_result_and_mock_cannot_be_relabelled_live(self):
        # Synthetic completed-run fixture, never a live execution success claim.
        self.output.mkdir()
        case_dir = self.output / 'campaign/cq01_intake'
        case_dir.mkdir(parents=True)
        saved = dict(execution_mode='mock_http', actual_api_attempts=0, synthetic_usage=True,
            result=dict(runtime={'ok': True}, error=None,
                        payload={'extraction': {'status': 'ready', 'nodes': []}}))
        isolation.save(case_dir / 'result.json', saved)
        packet = dict(author='agent:b_intake_candidate', draft_version=1,
            draft_sha256=json_sha256([]), result_sha256=isolation.sha(case_dir / 'result.json'),
            execution_mode='mock_http')
        isolation.save(case_dir / 'review_packet.json', packet)
        for kind in ('semantic', 'business'):
            isolation.save(case_dir / (kind + '_review.json'), dict(accepted_review(kind, packet),
                execution_mode='mock_http', synthetic_review=True,
                result_sha256=packet['result_sha256']))
        isolation.save(self.output / 'settings.json', dict(model_run=self.plan))
        isolation.save(self.output / 'authorization.json', runner.authorization(
            self.plan, approver='test-operator', confirmed=True))
        summary = dict(passed=True, execution_mode='mock_http', human_review='simulated',
                       plan_sha256=self.plan['plan_sha256'])
        isolation.save(self.output / 'campaign/summary.json', summary)
        isolation.save(self.output / 'shutdown.json', dict(server_stopped=True))
        marker = dict(passed=True, summary_sha256=isolation.sha(self.output / 'campaign/summary.json'),
                      shutdown_sha256=isolation.sha(self.output / 'shutdown.json'))
        isolation.save(self.output / 'model_validation.json', marker)
        self.assertTrue(runner._after_cq01(self.output, 'mock_http')['files'])
        with self.assertRaises(ValueError):
            runner._after_cq01(self.output, 'openai')
        review_path = case_dir / 'semantic_review.json'
        original = review_path.read_bytes()
        stale = json.loads(original)
        stale['result_sha256'] = '0' * 64
        review_path.write_text(json.dumps(stale))
        with self.assertRaises(ValueError):
            runner._after_cq01(self.output, 'mock_http')
        review_path.write_bytes(original)
        # Even coordinated display-label edits cannot promote synthetic evidence.
        prior = deepcopy(self.plan)
        prior['transport'] = 'openai'
        prior.pop('plan_sha256')
        prior['plan_sha256'] = json_sha256(prior)
        (self.output / 'settings.json').write_text(json.dumps({'model_run': prior}))
        (self.output / 'authorization.json').write_text(json.dumps(runner.authorization(
            prior, approver='test-operator', confirmed=True)))
        summary.update(execution_mode='openai', human_review='completed', plan_sha256=prior['plan_sha256'])
        (self.output / 'campaign/summary.json').write_text(json.dumps(summary))
        marker['summary_sha256'] = isolation.sha(self.output / 'campaign/summary.json')
        (self.output / 'model_validation.json').write_text(json.dumps(marker))
        saved['execution_mode'] = 'openai'
        (case_dir / 'result.json').write_text(json.dumps(saved))
        packet.update(execution_mode='openai', result_sha256=isolation.sha(case_dir / 'result.json'))
        (case_dir / 'review_packet.json').write_text(json.dumps(packet))
        for kind in ('semantic', 'business'):
            path = case_dir / (kind + '_review.json')
            record = json.loads(path.read_text())
            record.update(execution_mode='openai', result_sha256=packet['result_sha256'])
            path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            runner._after_cq01(self.output, 'openai')

    def test_terminal_confirmation_is_explicit_and_displays_cost_before_input(self):
        events = []
        answers = iter(['test-operator', '승인'])
        def ask(prompt):
            events.append(('ask', prompt))
            return next(answers)
        approved = runner.confirm(self.plan, ask=ask, emit=lambda text: events.append(('emit', text)))
        self.assertTrue(approved['approved'])
        self.assertEqual('emit', events[0][0])
        self.assertIn('cost_policy', '\n'.join(text for kind, text in events if kind == 'emit'))
        for answer in ('', 'yes', '통과', '다음'):
            replies = iter(['test-operator', answer])
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                runner.confirm(self.plan, ask=lambda _: next(replies), emit=lambda _: None)

    def test_terminal_review_shows_answer_and_material_before_any_input(self):
        packet = dict(case='cq01_intake', execution_mode='mock_http',
            answer_producer='mock_response', answer_ko='접수 후보를 검토해야 합니다.',
            evidence=[{'evidence_id': 'fixture-evidence'}], material={'source_text': '가상 계약 원문'},
            budget={'attempted_calls': {'model': 4}}, result_path='/private/tmp/new/result.json',
            author='agent:b_intake_candidate', draft_version=1, draft_sha256='a' * 64)
        for kind in ('semantic', 'business'):
            events = []
            answers = iter(['test-reviewer', '직접 입력한 테스트 검토 의견', '통과'])
            def ask(prompt):
                events.append(('ask', prompt))
                return next(answers)
            record = runner.terminal_review(kind, packet, ask=ask,
                emit=lambda text: events.append(('emit', text)))
            first_ask = next(i for i, (event, _) in enumerate(events) if event == 'ask')
            display = '\n'.join(text for event, text in events[:first_ask] if event == 'emit')
            self.assertIn(packet['answer_ko'], display)
            self.assertIn('가상 계약 원문', display)
            self.assertIn('fixture-evidence', display)
            self.assertIn(packet['result_path'], display)
            self.assertTrue(record['accepted'])
            self.assertEqual('직접 입력한 테스트 검토 의견', record['comment'])
            self.assertIsNone(runner._validate_review(kind, packet, record))
            if kind == 'business':
                self.assertIn(packet['draft_sha256'], display)
                self.assertIn('공통 DB', display)

    def test_cli_duplicate_confirmation_or_launch_rejects_before_prompt_and_process(self):
        self.output.mkdir()
        settings = dict(workflow='input_model', mode='free', output=str(self.output),
            run_id=str(self.output), model_run=self.plan)
        isolation.save(self.output / 'settings.json', settings)
        for marker in ('authorization.json', 'launch_claim.json'):
            marker_path = self.output / marker
            isolation.save(marker_path, {'existing': True})
            before = marker_path.read_bytes()
            with self.subTest(marker=marker), patch.object(runner, 'confirm') as confirm, \
                    patch.object(cli, 'launch') as launch, \
                    patch('psycopg.connect') as connect, patch.object(cli, 'postgres_bin') as pg, \
                    patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(1, cli.main(['run-model', '--output', str(self.output)]))
                confirm.assert_not_called(); launch.assert_not_called()
                connect.assert_not_called(); pg.assert_not_called()
            self.assertEqual(before, marker_path.read_bytes())
            marker_path.unlink()

    def test_run_cli_rejects_configuration_override_before_reading_or_starting(self):
        for option in (['--transport', 'openai'], ['--case', 'cq01_intake'],
                       ['--cost-policy', '/never-read.json'], ['--pg-bin', '/never-start']):
            with self.subTest(option=option), patch.object(cli, 'read_json') as read, \
                    patch.object(cli, 'launch') as launch, patch.object(runner, 'confirm') as confirm, \
                    patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(1, cli.main(['run-model', '--output', str(self.output), *option]))
                read.assert_not_called(); launch.assert_not_called(); confirm.assert_not_called()

    def test_review_rejects_self_empty_wrong_version_or_candidate_hash(self):
        packet = dict(author='agent:b_intake_candidate', draft_version=1, draft_sha256='a' * 64)
        valid = accepted_review('business', packet)
        self.assertIsNone(runner._validate_review('business', packet, valid))
        for changes in (dict(accepted=False), dict(accepted=1), dict(reviewer=''),
                        dict(reviewer=packet['author']), dict(reviewer=connection.SUBJECT),
                        dict(comment=''), dict(draft_version=2), dict(draft_version=True),
                        dict(draft_sha256='b' * 64)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runner._validate_review('business', packet, dict(valid, **changes))
        semantic = accepted_review('semantic', packet)
        self.assertIsNone(runner._validate_review('semantic', packet, semantic))
        with self.assertRaises(ValueError):
            runner._validate_review('business', packet, semantic)


class ModelRunCampaignTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        temporary = tempfile.TemporaryDirectory(prefix='b-model-reviewed-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.snapshot = snapshot()
        self.preflight = synthetic_preflight(self.root / 'preflight', self.snapshot)
        self.output = self.root / 'new-run'
        self.network = []
        for target in ('urllib.request.build_opener', 'urllib.request.urlopen',
                       'socket.create_connection', 'ontologics.openai_provider.OpenAISettings.from_environment'):
            guard = patch(target, side_effect=AssertionError('No network or real credentials'))
            self.network.append(guard.start())
            self.addCleanup(guard.stop)

    def tearDown(self):
        for guard in self.network:
            guard.assert_not_called()

    def make_plan(self, cases):
        return runner.build_plan(preflight=self.preflight, output=self.output,
            cases=list(cases), policy_snapshot=self.snapshot)

    def execute(self, plan, *, review=accepted_review, approval=None, **kwargs):
        if approval is None:
            approval = runner.authorization(plan, approver='test-operator', confirmed=True)
        return runner.run_campaign(database=self.database, output=self.output,
            plan=plan, approval=approval, review=review, emit=kwargs.pop('emit', lambda _: None), **kwargs)

    def read(self, name):
        return json.loads((self.output / name).read_text())

    def test_normal_all_ten_finish_without_review_or_business_approval(self):
        plan = runner.build_plan(output=self.output, cases=list(CASES),
            policy_snapshot=self.snapshot, review_mode='none')
        summary = self.execute(plan, review=lambda *_: self.fail('Normal execution must not await review'))
        self.assertTrue(summary['passed'])
        self.assertEqual(10, summary['completed_representatives'])
        self.assertEqual(40, summary['mock_http_attempts'])
        self.assertFalse(self.read('run.json')['synthetic_reviews'])
        for key in ('human_review', 'business_approval', 'ai_semantic_review'):
            self.assertEqual('not_performed', summary[key])
        self.assertFalse(summary['live_quality_verified'])
        self.assertFalse(summary['b_business_stored'])
        for case in CASES:
            self.assertTrue(self.read(case + '/result.json')['result']['runtime']['ok'])
            self.assertFalse(list((self.output / case).glob('*_review.json')))

    def test_normal_arbitrary_subset_keeps_results_and_stops_after_automatic_failure(self):
        cases = ['cq02_normal', 'cq05_mixed']
        plan = runner.build_plan(output=self.output, cases=cases,
            policy_snapshot=self.snapshot, review_mode='none')
        original = runner._execute_case
        def rejected(*args, **kwargs):
            result, calls = original(*args, **kwargs)
            result['runtime']['ok'] = False
            result['error'] = dict(code='AGENT_EVIDENCE_MISMATCH', message='not displayed')
            return result, calls
        messages = []
        with patch.object(runner, '_execute_case', side_effect=rejected), self.assertRaises(ValueError):
            self.execute(plan, review=lambda *_: self.fail('No review in normal mode'), emit=messages.append)
        self.assertFalse(self.read('cq02_normal/result.json')['result']['runtime']['ok'])
        self.assertFalse((self.output / 'cq05_mixed').exists())
        self.assertFalse((self.output / 'summary.json').exists())
        self.assertEqual('cq02_normal', self.read('stopped.json')['case'])
        self.assertTrue(any('cq02_normal' in line and 'AGENT_EVIDENCE_MISMATCH' in line for line in messages))
        self.assertTrue(any(str(self.output / 'cq02_normal/result.json') in line for line in messages))
        self.assertFalse(any('not displayed' in line for line in messages))

    def test_normal_confirmation_is_business_status_and_continues_independent_cq(self):
        plan = runner.build_plan(output=self.output, cases=['cq01_intake', 'cq02_normal'],
            policy_snapshot=self.snapshot, review_mode='none')
        original = runner._execute_case
        def needs_confirmation(*args, **kwargs):
            result, calls = original(*args, **kwargs)
            if args[1] == 'cq01_intake':
                result['payload']['extraction'] = dict(status='needs_confirmation', nodes=[],
                    issues=['모델이 원문 보완을 요청했습니다.'])
            return result, calls
        with patch.object(runner, '_execute_case', side_effect=needs_confirmation):
            summary = self.execute(plan, review=lambda *_: self.fail('No review in normal mode'))
        self.assertTrue(summary['passed'])
        self.assertEqual(2, summary['completed_representatives'])
        saved = self.read('cq01_intake/result.json')
        self.assertEqual('needs_confirmation', saved['intake_status'])
        self.assertFalse(saved['candidate_ready'])
        self.assertEqual('needs_confirmation', summary['intake_status'])
        self.assertFalse(summary['intake_candidate_ready'])
        self.assertTrue(self.read('cq02_normal/result.json')['result']['runtime']['ok'])
        self.assertEqual('not_performed', summary['business_approval'])

    def test_all_ten_use_common_provider_shared_budget_and_separate_cq01_reviews(self):
        plan = self.make_plan(CASES)
        events = []
        def review(kind, packet):
            events.append((packet['case'], kind))
            self.assertTrue(Path(packet['result_path']).is_file())
            self.assertEqual(isolation.sha(Path(packet['result_path'])), packet['result_sha256'])
            self.assertTrue(packet['answer_ko'])
            self.assertTrue(packet['evidence'])
            next_index = CASES.index(packet['case']) + 1
            if next_index < len(CASES):
                self.assertFalse((self.output / CASES[next_index]).exists())
            return accepted_review(kind, packet)
        summary = self.execute(plan, review=review)
        self.assertTrue(summary['passed'])
        self.assertEqual('mock_http', summary['execution_mode'])
        self.assertEqual('simulated', summary['human_review'])
        self.assertEqual(0, summary['actual_api_attempts'])
        self.assertEqual(40, summary['mock_http_attempts'])
        self.assertEqual(10, summary['completed_representatives'])
        self.assertFalse(summary['live_quality_verified'])
        self.assertFalse(summary['b_business_stored'])
        self.assertEqual([('cq01_intake', 'semantic'), ('cq01_intake', 'business')]
                         + [(case, 'semantic') for case in CASES[1:]], events)
        budget = summary['request_execution_budget']
        self.assertEqual(40, budget['attempted_calls']['model'])
        self.assertEqual(40, budget['cost']['paid_calls'])
        self.assertEqual(0, budget['attempted_calls']['repair'])
        previous = 0
        for case in CASES:
            saved = self.read(case + '/result.json')
            cumulative = saved['result']['request_execution_budget']['attempted_calls']['model']
            self.assertGreater(cumulative, previous)
            previous = cumulative
            semantic = self.read(case + '/semantic_review.json')
            self.assertTrue(semantic['synthetic_review'])
            self.assertEqual('mock_http', semantic['execution_mode'])
            self.assertEqual('mock_response', saved['answer_producer'])
        packet = self.read('cq01_intake/review_packet.json')
        self.assertEqual(json_sha256(self.read('cq01_intake/result.json')['result']['payload']['extraction']['nodes']),
                         packet['draft_sha256'])
        business = self.read('cq01_intake/business_review.json')
        self.assertEqual(packet['draft_version'], business['draft_version'])
        self.assertEqual(packet['draft_sha256'], business['draft_sha256'])

    def test_unapproved_plan_blocks_before_resources_database_or_dispatch(self):
        plan = self.make_plan(['cq01_intake'])
        denied = runner.authorization(plan, approver='test-operator', confirmed=False)
        with patch.object(runner, '_execute_case') as execute, patch('psycopg.connect') as connect, \
                self.assertRaises(ValueError):
            self.execute(plan, approval=denied)
        execute.assert_not_called(); connect.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_semantic_rejection_preserves_result_and_stops_before_business_or_next_case(self):
        plan = self.make_plan(CASES[:2])
        events = []
        def reject(kind, packet):
            events.append((packet['case'], kind))
            return dict(accepted_review(kind, packet), accepted=False)
        with self.assertRaises(ValueError):
            self.execute(plan, review=reject)
        self.assertEqual([('cq01_intake', 'semantic')], events)
        self.assertTrue((self.output / 'cq01_intake/result.json').is_file())
        self.assertFalse((self.output / 'cq01_intake/business_review.json').exists())
        self.assertFalse((self.output / 'cq01_promise').exists())
        self.assertFalse((self.output / 'summary.json').exists())
        self.assertEqual([], self.read('stopped.json')['completed_cases'])
        self.assertEqual(4, self.read('stopped.json')['mock_http_attempts'])
        self.assertFalse(self.read('stopped.json')['automatic_paid_rerun'])

    def test_stale_business_approval_does_not_reuse_successful_semantic_review(self):
        plan = self.make_plan(CASES[:2])
        def stale(kind, packet):
            record = accepted_review(kind, packet)
            if kind == 'business':
                record['draft_sha256'] = '0' * 64
            return record
        with self.assertRaises(ValueError):
            self.execute(plan, review=stale)
        self.assertTrue(self.read('cq01_intake/semantic_review.json')['accepted'])
        self.assertEqual('0' * 64, self.read('cq01_intake/business_review.json')['draft_sha256'])
        self.assertFalse((self.output / 'cq01_promise').exists())
        self.assertFalse((self.output / 'summary.json').exists())

    def test_eof_while_waiting_for_review_stops_without_next_call(self):
        plan = self.make_plan(CASES[:2])
        with self.assertRaises(EOFError):
            self.execute(plan, review=lambda *_: (_ for _ in ()).throw(EOFError()))
        self.assertEqual('EOFError', self.read('stopped.json')['error_type'])
        self.assertTrue((self.output / 'cq01_intake/result.json').is_file())
        self.assertFalse((self.output / 'cq01_promise').exists())
        self.assertFalse((self.output / 'summary.json').exists())

    def test_failed_second_result_is_saved_and_later_cases_never_start(self):
        plan = self.make_plan(CASES[:3])
        original = runner._execute_case
        called = []
        def fail(host, case, task, role, **kwargs):
            called.append(case)
            result, calls = original(host, case, task, role, **kwargs)
            if case == 'cq01_promise':
                result.update(payload=None, error={'code': 'INJECTED_REJECTION'})
            return result, calls
        with patch.object(runner, '_execute_case', side_effect=fail), self.assertRaises(ValueError):
            self.execute(plan)
        self.assertEqual(CASES[:2], called)
        self.assertEqual('INJECTED_REJECTION', self.read('cq01_promise/result.json')['result']['error']['code'])
        self.assertEqual(['cq01_intake'], self.read('stopped.json')['completed_cases'])
        self.assertFalse((self.output / 'cq02_normal').exists())
        self.assertFalse((self.output / 'summary.json').exists())

    def test_duplicate_campaign_preserves_all_results_and_dispatches_nothing(self):
        plan = self.make_plan(['cq01_intake'])
        self.execute(plan)
        before = {str(p.relative_to(self.output)): p.read_bytes()
                  for p in self.output.rglob('*') if p.is_file()}
        with patch.object(runner, '_execute_case') as execute, patch('psycopg.connect') as connect, \
                self.assertRaises(FileExistsError):
            self.execute(plan)
        execute.assert_not_called(); connect.assert_not_called()
        self.assertEqual(before, {str(p.relative_to(self.output)): p.read_bytes()
                                 for p in self.output.rglob('*') if p.is_file()})

    def test_mock_rejects_supplied_live_settings_and_never_constructs_real_opener(self):
        plan = self.make_plan(['cq01_intake'])
        with patch.object(runner, '_execute_case') as execute, patch('psycopg.connect') as connect, \
                self.assertRaises(ValueError):
            self.execute(plan, live_settings=connection.OpenAISettings('synthetic-unused-key'))
        execute.assert_not_called(); connect.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
