"""Ten-case input-only integration over real common reads; no paid backend."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_run, b_run_isolation as isolation, b_task_workflow as workflow
from ontologics.service import Service
from tests.db import database_for_test
from tests.order_fulfillment.test_b_intake_task import make_task, source_text


class WorkflowBoundaryTests(unittest.TestCase):
    def test_ten_cases_reuse_seven_existing_specs_and_pinned_tasks(self):
        names = []
        for case in workflow.CASES:
            role, task = workflow.case_task(case, version=1)
            module, agent, provider = role
            agent().messages(task)  # Existing pin/schema validation, no host calls.
            self.assertTrue(callable(provider(task)))
            self.assertEqual(4, module.SPEC.limits.max_model_calls)
            self.assertEqual(0, module.SPEC.limits.max_repairs)
            names.append(module.SPEC.name)
        self.assertEqual([r[0].SPEC.name for r in workflow.ROLES], list(dict.fromkeys(names)))
        self.assertEqual(names[4], names[5])
        self.assertEqual(names[6], names[7])
        self.assertEqual(names[8], names[9])
        with self.assertRaises(ValueError):
            workflow.case_task('not-a-representative')

    def test_paid_arguments_rejected_before_reading_or_preparing(self):
        for argument in ('--authorization', '--free-gate'):
            with self.subTest(argument=argument), patch.object(b_run, 'read_json') as read, \
                    patch.object(b_run, 'prepare') as prepare, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, b_run.main(['tasks', '--output', '/private/tmp/new-b-tasks',
                                              argument, '/never/read.json']))
                read.assert_not_called()
                prepare.assert_not_called()

    def test_tasks_and_default_select_only_free_input_workflow(self):
        for command in (['tasks'], []):
            with patch.object(b_run, 'prepare', return_value={'safe': True}) as prepare, \
                    patch.object(b_run, 'launch', return_value=0), \
                    patch.object(b_run, 'postgres_bin', return_value=Path('/private/tmp/pg')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, b_run.main(command + ['--output', '/private/tmp/new-b-tasks']))
                self.assertEqual('free', prepare.call_args.kwargs['mode'])
                self.assertEqual('input_tasks', prepare.call_args.kwargs.get('workflow'))

    def test_manifest_excludes_only_unused_inputs_without_changing_legacy_hashes(self):
        self.assertIs(isolation.SAFE_INPUTS, isolation.workflow_inputs('legacy'))
        self.assertNotIn('ontology/tbox.ttl', isolation.workflow_inputs('input_tasks'))
        self.assertIn('ontology/tbox.ttl', isolation.SAFE_INPUTS)
        for name, digest in isolation.workflow_inputs('input_tasks').items():
            self.assertEqual(isolation.SAFE_INPUTS[name], digest)
            self.assertEqual(digest, isolation.sha(isolation.ROOT / name), name)
        with self.assertRaises(ValueError):
            isolation.workflow_inputs('arbitrary')

    def test_direct_preparation_cannot_convert_tasks_to_live_or_paid_gate(self):
        for options in (dict(mode='live'), dict(authorization={}), dict(gate={}), dict(gate_hash='hash')):
            values = dict(mode='free', workflow='input_tasks', cases=['cq01_intake'],
                          pg_bin=Path('/private/tmp/unused'))
            values.update(options)
            with self.subTest(options=options), self.assertRaises(ValueError):
                isolation.prepare(Path('/private/tmp/new-b-tasks'), **values)

    def test_modified_review_material_is_rejected_before_execution(self):
        _, task = workflow.case_task('cq06_defects11')
        data = task.data()
        data['review_material']['python_explanation'] = {'tampered': True}
        with self.assertRaises(ValueError):
            workflow._review_material('cq06_defects11', data)

    def test_missing_database_and_invalid_case_sequences_create_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / 'new'
            with self.assertRaises(ValueError):
                workflow.run_campaign(database=None, output=output, cases=workflow.CASES)
            self.assertFalse(output.exists())


class WorkflowCommonHostTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'campaign'

    def run_campaign(self, cases=None):
        return workflow.run_campaign(database=self.database, output=self.output,
                                     cases=list(workflow.CASES if cases is None else cases))

    def read(self, name):
        return json.loads((self.output / name).read_text())

    def test_all_ten_representatives_run_common_tools_and_keep_business_storage_unchanged(self):
        summary = self.run_campaign()
        self.assertTrue(summary['passed'])
        self.assertEqual((10, 20, 0), (summary['completed_representatives'],
            summary['model_calls'], summary['actual_api_attempts']))
        self.assertFalse(summary['live_authorization_eligible'])
        self.assertFalse(summary['live_quality_verified'])
        self.assertEqual('not_performed', summary['human_review'])
        for case in workflow.CASES:
            saved = self.read(case + '/result.json')
            result = saved['result']
            self.assertTrue(result['runtime']['ok'])
            self.assertIsNone(result['error'])
            self.assertTrue(result['payload']['answer_ko'])
            self.assertEqual({'model': 2, 'tool': 4, 'repair': 0},
                             result['request_execution_budget']['attempted_calls'])
            self.assertEqual('scripted', saved['execution_mode'])
            self.assertEqual('scripted_provider', saved['answer_producer'])
            self.assertTrue(all(ref['origin'] == 'real' for ref in result['evidence_refs']))
        self.assertIn('source_text', self.read('cq01_intake/input.json'))
        for case, quantity in (('cq04_normal', 960), ('cq04_mixed', 1000)):
            self.assertEqual(quantity, self.read(case + '/result.json')['result']['payload']['assessment']['on_time_quantity'])
            self.assertEqual('deterministic_calculation_details', self.read(case + '/review_material.json')['kind'])
        for case in ('cq05_normal', 'cq05_mixed', 'cq06_defects11', 'cq06_transport2h'):
            material = self.read(case + '/review_material.json')
            state = self.read(case + '/result.json')['result']['payload']['assessment']
            self.assertEqual(state['review_material_sha256'], material['sha256'])
            self.assertEqual(material['sha256'], workflow.json_sha256(material['data']))
        for case, quantities in (('cq06_defects11', (920, 80)), ('cq06_transport2h', (870, 130))):
            forecast = self.read(case + '/result.json')['result']['payload']['assessment']['after']['forecast']
            self.assertEqual(quantities, (forecast['on_time_quantity'], forecast['shortfall_quantity']))
        audit = Service(self.database, principals={})
        try:
            self.assertEqual(1, audit._version())
            self.assertEqual(json.loads((workflow.ROOT / 'data/catalog.json').read_text())['nodes'], audit._nodes())
            for table, count in (('drafts', 0), ('review_events', 0), ('runtime_runs', 10)):
                self.assertEqual(count, audit.db.execute('SELECT COUNT(*) AS n FROM ' + table).fetchone()['n'])
        finally:
            audit.close()
        self.assertFalse((self.output / 'free_gate.json').exists())

    def test_duplicate_output_never_starts_another_case_and_preserves_prior_result(self):
        self.run_campaign(['cq01_intake'])
        saved = (self.output / 'cq01_intake/result.json').read_bytes()
        with patch.object(workflow, '_execute_case') as execute, self.assertRaises(FileExistsError):
            self.run_campaign(['cq01_intake'])
        execute.assert_not_called()
        self.assertEqual(saved, (self.output / 'cq01_intake/result.json').read_bytes())

    def test_first_failed_result_is_preserved_and_later_representatives_never_start(self):
        execute = workflow._execute_case
        calls = []
        def fail_second(host, case, task, role):
            calls.append(case)
            result = execute(host, case, task, role)
            if len(calls) == 2:
                result['error'] = {'code': 'INJECTED_FAILURE'}
                result['payload'] = None
            return result
        with patch.object(workflow, '_execute_case', side_effect=fail_second), self.assertRaises(ValueError):
            self.run_campaign()
        self.assertEqual(workflow.CASES[:2], calls)
        self.assertEqual(workflow.CASES[:1], self.read('stopped.json')['completed_cases'])
        self.assertEqual('INJECTED_FAILURE', self.read('cq01_promise/result.json')['result']['error']['code'])
        self.assertFalse((self.output / 'summary.json').exists())
        self.assertFalse((self.output / 'cq02_normal').exists())

    def test_intake_needs_confirmation_stops_even_when_runtime_is_valid(self):
        task = make_task(self, source_text().replace('| 납품 장소 | 가상고객사 A 수령창고 |\n', ''))
        with patch.object(workflow, 'case_task', return_value=(workflow.ROLES[0], task)) as build, \
                self.assertRaises(ValueError):
            self.run_campaign()
        self.assertEqual(1, build.call_count)
        result = self.read('cq01_intake/result.json')['result']
        self.assertTrue(result['runtime']['ok'])
        self.assertEqual('needs_confirmation', result['payload']['extraction']['status'])
        self.assertEqual([], self.read('stopped.json')['completed_cases'])
        self.assertFalse((self.output / 'cq01_promise').exists())

    def test_nonempty_database_is_refused_without_running_roles(self):
        self.run_campaign(['cq01_intake'])
        self.output = Path(self.temp.name) / 'another'
        with patch.object(workflow, '_execute_case') as execute, self.assertRaises(ValueError):
            self.run_campaign(['cq01_intake'])
        execute.assert_not_called()
        self.assertEqual('ValueError', self.read('stopped.json')['error_type'])

    def test_order_duplicates_and_unknown_case_are_refused_before_output(self):
        for cases in ([], ['cq01_promise', 'cq01_intake'], ['cq01_intake'] * 2, ['unknown']):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                self.run_campaign(cases)
            self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
