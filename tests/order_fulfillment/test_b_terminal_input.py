"""New terminal input calculations and common-host wiring; never paid APIs."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_model_run as runner, b_run as cli
from examples.order_fulfillment import b_run_isolation as isolation, b_terminal_input as terminal
from examples.order_fulfillment.b_task_workflow import case_task
from tests.db import database_for_test
from tests.order_fulfillment.test_b_model_run import snapshot


SOURCE = isolation.ROOT / 'data/order_fulfillment/examples/terminal_800.md'


class TerminalInputTests(unittest.TestCase):
    def setUp(self):
        self.value = terminal.load_input(SOURCE)

    def task(self, case):
        return terminal.case_task(case, self.value)[1].data()

    def test_new_contract_differs_from_fixed_demo_and_uses_source_bytes(self):
        old = case_task('cq01_intake')[1].data()
        intake = self.task('cq01_intake')
        self.assertEqual(SOURCE.read_text(), intake['source_text'])
        self.assertNotEqual(old['input_sha256'], intake['input_sha256'])
        promise = self.task('cq01_promise')['promise']
        self.assertEqual(800, promise['quantity'])
        self.assertEqual('가상고객사 B', promise['customer_name'])
        self.assertEqual('가상고객사 B 제2수령창고', promise['destination'])
        self.assertEqual(1000, case_task('cq01_promise')[1].data()['promise']['quantity'])

    def test_mixed_round_separates_completed_production_and_forecasts_only_its_work(self):
        value = self.task('cq02_normal')['assessment']
        self.assertEqual('complete', value['status'])
        self.assertEqual([], value['unknowns'])
        self.assertEqual(dict(delivered=100, in_transit=100, warehouse=200,
            in_progress=300, ready=0, waiting=0, production_completed=100), value['states'])
        self.assertEqual(800, sum(value['states'].values()))
        self.assertEqual(700, value['remaining_quantity'])
        production = self.task('cq03_normal')['assessment']
        self.assertEqual('forecast', production['status'])
        self.assertEqual([], production['unknowns'])
        self.assertEqual((400, 100, 300, 100.0, '2026-10-30T13:00:00+09:00'), tuple(
            production[k] for k in ('target_quantity', 'latest_good_quantity',
                'remaining_quantity', 'good_rate_per_hour', 'estimated_completion_at')))

    def test_new_arrival_and_draft_quantities_follow_existing_arithmetic(self):
        delivery = self.task('cq04_mixed')['assessment']
        self.assertEqual((800, 750, 50), tuple(delivery[key] for key in
            ('quantity', 'on_time_quantity', 'shortfall_quantity')))
        self.assertEqual('2026-10-30T15:30:00+09:00', delivery['estimated_full_arrival_at'])
        response = self.task('cq05_mixed')['assessment']
        request = response['dispatch_request']
        self.assertEqual((100, 100, 600, 300, 300, 550, 50), tuple(request[key] for key in (
            'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity',
            'currently_available_quantity', 'future_quantity', 'on_time_new_dispatch_quantity',
            'later_new_dispatch_quantity')))
        self.assertFalse(response['execution_performed'])
        for key in ('carrier', 'vehicle', 'detailed_address', 'contact', 'request_number'):
            self.assertIsNone(request[key])

    def test_transport_comparison_changes_only_the_explicit_condition(self):
        value = self.task('cq06_transport2h')['assessment']
        before, after = value['before']['forecast'], value['after']['forecast']
        self.assertEqual((7200, 10800), (before['condition']['transport_seconds'],
                                       after['condition']['transport_seconds']))
        self.assertEqual((750, 650), (before['on_time_quantity'], after['on_time_quantity']))
        self.assertEqual((50, 150), (before['shortfall_quantity'], after['shortfall_quantity']))
        self.assertEqual('2026-10-30T16:30:00+09:00', after['estimated_full_arrival_at'])

    def test_invalid_source_path_or_mutated_plan_stops_before_resources(self):
        with self.assertRaises(ValueError):
            terminal.load_input(isolation.ROOT / '.env.local')
        for key, value in (('text', self.value['text'] + '\n'), ('sha256', '0' * 64),
                           ('path', '../outside.md')):
            candidate = dict(self.value, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                terminal.case_task('cq01_intake', candidate)
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw).resolve() / 'new'
            plan = runner.build_plan(output=output, cases=['cq05_mixed'], policy_snapshot=snapshot(),
                review_mode='none', terminal_input=self.value)
            runner.validate_plan(plan)
            changed = deepcopy(plan)
            changed['terminal_input']['text'] += '\n'
            with self.assertRaises(ValueError):
                runner.validate_plan(changed)
            self.assertFalse(output.exists())

    def test_source_is_the_only_extra_file_in_worker_read_allowlist(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw).resolve() / 'new'
            plan = runner.build_plan(output=output, cases=['cq01_intake'], policy_snapshot=snapshot(),
                review_mode='none', terminal_input=self.value)
            settings = isolation.prepare(output, mode='free', cases=plan['cases'],
                pg_bin=Path(raw).resolve(), workflow='input_model', model_run=plan)
            self.assertEqual([str(SOURCE)], settings['model_evidence_files'])
            policy = isolation.sandbox_policy(settings)
            self.assertIn('(literal "' + str(SOURCE) + '")', policy)
            self.assertNotIn('(subpath "' + str(SOURCE.parent) + '")', policy)

    def test_cli_accepts_explicit_input_and_saves_original_for_normal_run(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw).resolve(); output = directory / 'new'
            policy = directory / 'cost.json'; policy.write_text(json.dumps(snapshot()['values']))
            def prepare(path, **kwargs):
                path.mkdir()
                return dict(output=str(path), model_run=kwargs['model_run'])
            with patch.object(cli, 'prepare', side_effect=prepare), \
                    patch.object(cli, 'postgres_bin', return_value=directory), \
                    patch.object(cli, '_launch_model', return_value=0) as launch, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, cli.main(['run', '--output', str(output), '--case', 'cq05_mixed',
                    '--input', str(SOURCE), '--cost-policy', str(policy)]))
            settings, approval = launch.call_args.args
            self.assertEqual('mock_http', settings['model_run']['transport'])
            self.assertEqual(self.value, settings['model_run']['terminal_input'])
            self.assertEqual(SOURCE.read_bytes(), (output / 'input_source.md').read_bytes())
            runner.validate_authorization(settings['model_run'], approval)


class BasicTerminalExampleTests(unittest.TestCase):
    def source(self, hour):
        return terminal.load_input(SOURCE.parent / ('basic_800_' + hour + '.md'))

    def task(self, case, hour):
        return terminal.case_task(case, self.source(hour))[1].data()

    def test_start_snapshot_classifies_the_entire_round_without_partial_production(self):
        intake = self.task('cq01_intake', '0600')
        self.assertIn('B-BASIC-800', intake['source_text'])
        promise = self.task('cq01_promise', '0600')['promise']
        self.assertEqual((800, '가상고객사 B', '2026-10-30T15:00:00+09:00'),
                         tuple(promise[k] for k in ('quantity', 'customer_name', 'due_at')))
        value = self.task('cq02_normal', '0600')['assessment']
        self.assertEqual('complete', value['status'])
        self.assertEqual([], value['unknowns'])
        self.assertEqual(dict(delivered=0, in_transit=0, warehouse=0,
                              in_progress=800, ready=0, waiting=0), value['states'])
        self.assertEqual(800, value['remaining_quantity'])

    def test_later_snapshot_supports_production_arrival_drafts_and_comparison(self):
        fulfillment = self.task('cq02_normal', '1000')['assessment']
        self.assertEqual('complete', fulfillment['status'])
        self.assertEqual((400, 400, 0, 800), (fulfillment['states']['production_completed'],
            fulfillment['states']['in_progress'], fulfillment['states']['warehouse'],
            fulfillment['remaining_quantity']))
        production = self.task('cq03_normal', '1000')['assessment']
        self.assertEqual('forecast', production['status'])
        self.assertEqual([], production['unknowns'])
        self.assertEqual((400, 400, 100.0, '2026-10-30T14:00:00+09:00'), tuple(
            production[k] for k in ('latest_good_quantity', 'remaining_quantity',
                                    'good_rate_per_hour', 'estimated_completion_at')))
        delivery = self.task('cq04_mixed', '1000')['assessment']
        self.assertEqual([], delivery['unknowns'])
        self.assertEqual((650, 150, '2026-10-30T16:30:00+09:00'), tuple(delivery[k]
            for k in ('on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at')))
        response = self.task('cq05_mixed', '1000')['assessment']
        request = response['dispatch_request']
        self.assertEqual((0, 0, 800, 400, 400, 650, 150), tuple(request[k] for k in (
            'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity',
            'currently_available_quantity', 'future_quantity', 'on_time_new_dispatch_quantity',
            'later_new_dispatch_quantity')))
        self.assertFalse(response['execution_performed'])
        comparison = self.task('cq06_transport2h', '1000')['assessment']
        before, after = (comparison[side]['forecast'] for side in ('before', 'after'))
        self.assertEqual((650, 550, 150, 250), (before['on_time_quantity'], after['on_time_quantity'],
                                             before['shortfall_quantity'], after['shortfall_quantity']))
        self.assertEqual('2026-10-30T17:30:00+09:00', after['estimated_full_arrival_at'])
        self.assertEqual(7200, before['condition']['transport_seconds'])
        self.assertEqual(10800, after['condition']['transport_seconds'])

    def test_zero_quantity_does_not_create_stock_allocations_or_shipment_events(self):
        value = self.source('0600')
        data = terminal.validate_input(value)
        nodes = terminal._nodes(value, data)[3]
        self.assertEqual(1, sum(n['type'] == 'Allocation' for n in nodes))
        self.assertFalse(any(n['type'] in ('FinishedGoodsStock', 'Shipment') for n in nodes))
        for field in ('departed_at', 'arrived_at', 'expected_arrival_at'):
            invalid = deepcopy(data)
            invalid['delivered'][field] = '2026-10-30T05:00:00+09:00'
            with self.subTest(field=field), self.assertRaises(ValueError):
                terminal._nodes(value, invalid)


class TerminalInputCampaignTests(unittest.TestCase):
    def test_basic_examples_reach_all_seven_roles_with_mock_responses(self):
        groups = (('0600', terminal.CASES[:3]), ('1000', terminal.CASES[3:]))
        for hour, cases in groups:
            with self.subTest(hour=hour), tempfile.TemporaryDirectory() as raw:
                source = SOURCE.parent / ('basic_800_' + hour + '.md')
                output = Path(raw).resolve() / 'campaign'
                plan = runner.build_plan(output=Path(raw).resolve() / 'new-run', cases=list(cases),
                    policy_snapshot=snapshot(), review_mode='none',
                    terminal_input=terminal.load_input(source))
                approval = runner.authorization(plan, approver='test-operator', confirmed=True)
                summary = runner.run_campaign(database=database_for_test(self), output=output,
                    plan=plan, approval=approval, emit=lambda _: None)
                self.assertTrue(summary['passed'])
                self.assertEqual(len(cases), summary['completed_representatives'])
                self.assertEqual(0, summary['actual_api_attempts'])
                for case in cases:
                    result = json.loads((output / case / 'result.json').read_text())
                    self.assertTrue(result['result']['runtime']['ok'])
                    self.assertEqual('mock_response', result['answer_producer'])
                    self.assertEqual(0, result['actual_api_attempts'])

    def test_seven_new_input_roles_run_through_common_host_without_paid_calls(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw).resolve() / 'campaign'
            plan = runner.build_plan(output=Path(raw).resolve() / 'new-run', cases=list(terminal.CASES),
                policy_snapshot=snapshot(), review_mode='none', terminal_input=terminal.load_input(SOURCE))
            approval = runner.authorization(plan, approver='test-operator', confirmed=True)
            database = database_for_test(self)
            summary = runner.run_campaign(database=database, output=output,
                plan=plan, approval=approval, emit=lambda _: None)
            self.assertTrue(summary['passed'])
            self.assertEqual(0, summary['actual_api_attempts'])
            self.assertEqual(7, summary['completed_representatives'])
            self.assertFalse(summary['b_business_stored'])
            for case in terminal.CASES:
                result = json.loads((output / case / 'result.json').read_text())
                self.assertTrue(result['result']['runtime']['ok'])
                self.assertEqual('mock_response', result['answer_producer'])
                self.assertEqual(0, result['actual_api_attempts'])
            before = (output / 'summary.json').read_bytes()
            with self.assertRaises(FileExistsError):
                runner.run_campaign(database=database, output=output,
                    plan=plan, approval=approval, emit=lambda _: None)
            self.assertEqual(before, (output / 'summary.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
