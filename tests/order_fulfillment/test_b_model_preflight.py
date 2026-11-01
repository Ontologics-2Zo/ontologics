"""Real common provider with local Responses doubles; no paid API or credentials."""
import contextlib
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.order_fulfillment import (
    b_model_connection as connection, b_model_preflight as preflight,
    b_run, b_task_workflow as workflow)
from ontologics.database import DatabaseSettings
from ontologics.harness import canonical
from ontologics.paid_budget import CostPolicy
from ontologics.service import DomainError, Service
from tests.db import database_for_test


def public_values(cap='50'):
    # Invented rates test enforcement only; this is not an operator rate card.
    return dict(ONTOLOGICS_REQUEST_MAX_COST_USD=cap,
        ONTOLOGICS_MODEL_PRICES_JSON=json.dumps({connection.MODEL: dict(
            input_usd_per_million='1', output_usd_per_million='2',
            max_input_tokens=400000)}))


class PolicySnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='b-model-policy-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'public-policy.json'
        self.values = public_values()
        self.path.write_text(json.dumps(self.values))
        self.guards = []
        for target in ('ontologics.database.environment_values', 'psycopg.connect',
                       'urllib.request.build_opener', 'urllib.request.urlopen'):
            guard = patch(target, side_effect=AssertionError('No environment, DB or network'))
            self.guards.append(guard.start())
            self.addCleanup(guard.stop)

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()

    def test_load_preserves_exact_two_public_values_and_common_policy(self):
        snapshot = preflight.load_policy(self.path)
        self.assertEqual({'values', 'sha256'}, set(snapshot))
        self.assertEqual(self.values, snapshot['values'])
        self.assertEqual(hashlib.sha256(canonical(self.values).encode()).hexdigest(),
                         snapshot['sha256'])
        policy = preflight.policy_from_snapshot(snapshot)
        self.assertIsInstance(policy, CostPolicy)
        self.assertEqual('50', policy.max_cost_usd)
        self.assertEqual(connection.MODEL, policy.prices[0].model)
        # The trusted content is independent of the file's whitespace.
        self.path.write_text(json.dumps(self.values, indent=2))
        self.assertEqual(snapshot, preflight.load_policy(self.path))

    def test_mutated_snapshot_keys_values_or_hash_cannot_supply_policy(self):
        original = preflight.load_policy(self.path)
        values = deepcopy(original)
        values['values']['ONTOLOGICS_REQUEST_MAX_COST_USD'] = '500'
        for candidate in (values, dict(original, sha256='0' * 64),
                          dict(original, approved=True), {'values': original['values']},
                          dict(original, values=dict(original['values'], OPENAI_API_KEY='fake'))):
            with self.subTest(keys=list(candidate)), self.assertRaises((ValueError, DomainError)):
                preflight.policy_from_snapshot(candidate)
        self.assertEqual(original, preflight.load_policy(self.path))

    def test_wrong_keys_types_and_invalid_common_cost_settings_are_rejected(self):
        for values in ({}, dict(self.values, OPENAI_API_KEY='fake-never-a-real-key'),
                       dict(self.values, approved=True),
                       {'ONTOLOGICS_REQUEST_MAX_COST_USD': '50'},
                       dict(self.values, ONTOLOGICS_REQUEST_MAX_COST_USD=50),
                       dict(self.values, ONTOLOGICS_REQUEST_MAX_COST_USD='NaN'),
                       dict(self.values, ONTOLOGICS_MODEL_PRICES_JSON='{broken'),
                       dict(self.values, ONTOLOGICS_MODEL_PRICES_JSON='{}')):
            with self.subTest(values=list(values)):
                self.path.write_text(json.dumps(values))
                with self.assertRaises((ValueError, DomainError)):
                    preflight.load_policy(self.path)

    def test_policy_file_is_bounded_and_rejects_file_and_parent_aliases(self):
        self.path.write_text(' ' * 16385)
        with self.assertRaises((ValueError, DomainError)):
            preflight.load_policy(self.path)
        self.path.write_text(json.dumps(self.values))
        alias = self.root / 'alias.json'
        alias.symlink_to(self.path)
        parent = self.root / 'alias-parent'
        parent.symlink_to(self.root, target_is_directory=True)
        for path in (alias, parent / self.path.name, Path('relative.json')):
            with self.subTest(path=str(path)), self.assertRaises((ValueError, DomainError)):
                preflight.load_policy(path)


class PreflightEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='b-model-entry-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.policy_path = self.root / 'public-policy.json'
        self.policy_path.write_text(json.dumps(public_values()))
        self.snapshot = preflight.load_policy(self.policy_path)

    def test_preflight_defaults_to_one_case_and_selects_free_owned_worker(self):
        with patch.object(b_run, 'prepare', return_value={'prepared': True}) as prepare, \
                patch.object(b_run, 'launch', return_value=0) as launch, \
                patch.object(b_run, 'postgres_bin', return_value=Path('/private/tmp/pg')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, b_run.main(['preflight', '--output', str(self.root / 'new'),
                                           '--cost-policy', str(self.policy_path)]))
        self.assertEqual('free', prepare.call_args.kwargs['mode'])
        self.assertEqual('input_tasks', prepare.call_args.kwargs['workflow'])
        self.assertEqual(['cq01_intake'], prepare.call_args.kwargs['cases'])
        self.assertEqual(self.snapshot, prepare.call_args.kwargs['policy_snapshot'])
        launch.assert_called_once_with({'prepared': True})

    def test_missing_or_invalid_policy_rejects_before_database_or_launch(self):
        for path in (None, self.root / 'missing.json', self.policy_path):
            if path == self.policy_path:
                path.write_text(json.dumps(dict(public_values(), OPENAI_API_KEY='fake')))
            args = ['preflight', '--output', str(self.root / 'new')]
            if path is not None:
                args += ['--cost-policy', str(path)]
            with self.subTest(path=str(path)), patch.object(b_run, 'prepare') as prepare, \
                    patch.object(b_run, 'launch') as launch, \
                    patch.object(b_run, 'postgres_bin') as pg, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, b_run.main(args))
                prepare.assert_not_called(); launch.assert_not_called(); pg.assert_not_called()
            self.assertFalse((self.root / 'new').exists())

    def test_preflight_cannot_accept_old_paid_authorization_or_free_gate(self):
        for option in ('--authorization', '--free-gate'):
            with self.subTest(option=option), patch.object(b_run, 'prepare') as prepare, \
                    patch.object(b_run, 'read_json') as read, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, b_run.main(['preflight', '--output', str(self.root / 'new'),
                    '--cost-policy', str(self.policy_path), option, '/never-read.json']))
                prepare.assert_not_called(); read.assert_not_called()

    def test_default_tasks_cannot_silently_adopt_model_policy(self):
        with patch.object(b_run, 'prepare') as prepare, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, b_run.main(['tasks', '--output', str(self.root / 'new'),
                                           '--cost-policy', str(self.policy_path)]))
            prepare.assert_not_called()

    def test_invalid_database_or_cases_create_no_output_or_provider(self):
        database = DatabaseSettings('postgresql://do-not-connect/unused', 'b_model_preflight_test')
        for supplied_database, cases in ((None, ['cq01_intake']), (database, []),
                (database, ['cq01_intake'] * 2), (database, ['cq02_normal', 'cq01_intake']),
                (database, ['unknown'])):
            output = self.root / 'new'
            with self.subTest(cases=cases), patch.object(preflight, '_execute_case') as execute, \
                    patch('psycopg.connect') as connect, self.assertRaises(ValueError):
                preflight.run_preflight(database=supplied_database, output=output,
                    cases=cases, policy_snapshot=self.snapshot)
            execute.assert_not_called(); connect.assert_not_called()
            self.assertFalse(output.exists())


class PreflightCommonHostTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        temporary = tempfile.TemporaryDirectory(prefix='b-model-preflight-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / 'campaign'
        policy_path = self.root / 'public-policy.json'
        policy_path.write_text(json.dumps(public_values()))
        self.snapshot = preflight.load_policy(policy_path)
        # Actual common OpenAIProvider must use the explicit local opener.
        self.network_guards = []
        for target in ('urllib.request.build_opener', 'urllib.request.urlopen',
                       'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('External network is forbidden'))
            self.network_guards.append(guard.start())
            self.addCleanup(guard.stop)

    def tearDown(self):
        for guard in self.network_guards:
            guard.assert_not_called()

    def run_preflight(self, cases=None):
        return preflight.run_preflight(database=self.database, output=self.output,
            cases=list(workflow.CASES if cases is None else cases), policy_snapshot=self.snapshot)

    def read(self, name):
        return json.loads((self.output / name).read_text())

    def test_all_ten_run_actual_common_provider_with_shared_mock_cost_and_reads(self):
        summary = self.run_preflight()
        self.assertTrue(summary['passed'])
        self.assertEqual('mock_http', summary['execution_mode'])
        self.assertEqual('awaiting_execution_approval', summary['status'])
        self.assertEqual(0, summary['actual_api_attempts'])
        self.assertEqual(40, summary['mock_http_attempts'])
        self.assertEqual('not_performed', summary['human_review'])
        self.assertFalse(summary['live_authorization_eligible'])
        expected = connection.policy_summary(preflight.policy_from_snapshot(self.snapshot), workflow.CASES)
        self.assertEqual(expected, summary['model_connection'])
        budget = summary['request_execution_budget']
        self.assertEqual(40, budget['attempted_calls']['model'])
        self.assertEqual(0, budget['attempted_calls']['repair'])
        self.assertEqual(40, budget['cost']['paid_calls'])
        self.assertEqual(40, budget['cost']['reported_calls'])
        self.assertGreater(Decimal(budget['cost']['reported_cost_usd']), 0)
        previous_cost = Decimal(0)
        for case in workflow.CASES:
            saved = self.read(case + '/result.json')
            result = saved['result']
            self.assertTrue(result['runtime']['ok'], result['runtime'])
            self.assertIsNone(result['error'])
            self.assertEqual(4, len(saved['provider_calls']))
            self.assertTrue(all(call['status'] == 'completed' for call in saved['provider_calls']))
            self.assertEqual('mock_response', saved['answer_producer'])
            self.assertTrue(result['evidence_refs'])
            cumulative = Decimal(result['request_execution_budget']['cost']['reported_cost_usd'])
            self.assertGreater(cumulative, previous_cost)
            previous_cost = cumulative
        self.assertEqual(previous_cost, Decimal(budget['cost']['reported_cost_usd']))
        for case in ('cq04_normal', 'cq04_mixed', 'cq05_normal', 'cq05_mixed',
                     'cq06_defects11', 'cq06_transport2h'):
            material = self.read(case + '/review_material.json')
            self.assertEqual(workflow.json_sha256(material['data']), material['sha256'])
        audit = Service(self.database, principals={})
        try:
            self.assertEqual(1, audit._version())
            self.assertEqual(json.loads((workflow.ROOT / 'data/catalog.json').read_text())['nodes'],
                             audit._nodes())
            for table, count in (('drafts', 0), ('review_events', 0), ('runtime_runs', 10)):
                self.assertEqual(count, audit.db.execute('SELECT COUNT(*) AS n FROM ' + table).fetchone()['n'])
        finally:
            audit.close()
        for name in ('free_gate.json', 'completion.json', 'authorization.json'):
            self.assertFalse((self.output / name).exists())

    def test_duplicate_campaign_preserves_result_and_starts_no_provider_or_database(self):
        self.run_preflight(['cq01_intake'])
        before = {str(path.relative_to(self.output)): path.read_bytes()
                  for path in self.output.rglob('*') if path.is_file()}
        with patch.object(preflight, '_execute_case') as execute, \
                patch('psycopg.connect') as connect, self.assertRaises(FileExistsError):
            self.run_preflight(['cq01_intake'])
        execute.assert_not_called(); connect.assert_not_called()
        self.assertEqual(before, {str(path.relative_to(self.output)): path.read_bytes()
                                 for path in self.output.rglob('*') if path.is_file()})

    def test_first_failed_case_preserves_failure_and_blocks_later_cases(self):
        original = preflight._execute_case
        called = []
        def fail_second(host, case, task, role, **kwargs):
            called.append(case)
            result, calls = original(host, case, task, role, **kwargs)
            if len(called) == 2:
                result['error'] = {'code': 'INJECTED_FAILURE'}
                result['payload'] = None
            return result, calls
        with patch.object(preflight, '_execute_case', side_effect=fail_second), \
                self.assertRaises(ValueError):
            self.run_preflight()
        self.assertEqual(workflow.CASES[:2], called)
        self.assertEqual('INJECTED_FAILURE',
                         self.read('cq01_promise/result.json')['result']['error']['code'])
        self.assertEqual(workflow.CASES[:1], self.read('stopped.json')['completed_cases'])
        self.assertFalse((self.output / 'cq02_normal').exists())
        self.assertFalse((self.output / 'summary.json').exists())

    def test_existing_catalog_database_is_rejected_before_model_dispatch(self):
        audit = Service(self.database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        catalog = json.loads((workflow.ROOT / 'data/catalog.json').read_text())['nodes']
        try:
            self.assertTrue(audit.bootstrap('seed', catalog, request_key='preexisting')['ok'])
            with patch.object(preflight, '_execute_case') as execute, self.assertRaises(ValueError):
                self.run_preflight(['cq01_intake'])
            execute.assert_not_called()
            self.assertEqual(1, audit._version())
            self.assertEqual(catalog, audit._nodes())
            self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM runtime_runs').fetchone()['n'])
        finally:
            audit.close()


if __name__ == '__main__':
    unittest.main()
