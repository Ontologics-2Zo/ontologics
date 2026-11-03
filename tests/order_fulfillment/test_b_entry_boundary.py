"""Supported input-task entry points; no paid calls, legacy resources or DB."""
import contextlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from examples.order_fulfillment import b_notebook, b_run, b_run_isolation as isolation
from examples.order_fulfillment.b_task_workflow import case_task


class EntryBoundaryTests(unittest.TestCase):
    def test_legacy_cli_rejects_even_without_output_before_resource_or_file_read(self):
        for mode in ('free', 'live'):
            with self.subTest(mode=mode), patch.object(b_run, 'prepare') as prepare, \
                    patch.object(b_run, 'postgres_bin') as pg, patch.object(b_run, 'read_json') as read, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(1, b_run.main([mode]))
                self.assertIn('B_LEGACY_EXECUTION_RETIRED', error.getvalue())
                prepare.assert_not_called(); pg.assert_not_called(); read.assert_not_called()

    def test_direct_prepare_rejects_legacy_before_hashing_or_creating_output(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(isolation, 'sha') as digest:
            output = Path(raw) / 'new'
            for kwargs in ({}, {'workflow': 'legacy'}, {'workflow': 'input_tasks', 'mode': 'live'}):
                options = dict(mode='free', cases=['cq01_intake'], pg_bin=Path(raw))
                options.update(kwargs)
                with self.subTest(options=kwargs), self.assertRaises(ValueError):
                    isolation.prepare(output, **options)
            digest.assert_not_called()
            self.assertFalse(output.exists())

    def test_direct_launch_owner_and_worker_reject_legacy_before_process_or_hooks(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'settings.json'
            for config in ({}, dict(workflow='legacy', mode='free'),
                           dict(workflow='input_tasks', mode='live'),
                           dict(workflow='input_tasks', mode='free', authorization={}),
                           dict(workflow='input_tasks', mode='free', free_gate_sha256='old')):
                path.write_text(json.dumps(config))
                for entry, argument in ((isolation.launch, config), (isolation.own_run, str(path)),
                                        (isolation.guarded_campaign, str(path))):
                    with self.subTest(config=config, entry=entry.__name__), \
                            patch.object(isolation.subprocess, 'Popen') as process, \
                            patch.object(isolation.sys, 'addaudithook') as hook, \
                            patch.object(isolation, 'clean_environment') as environment:
                        with self.assertRaises(ValueError):
                            entry(argument)
                        process.assert_not_called(); hook.assert_not_called(); environment.assert_not_called()

    def test_nine_storage_cli_mains_retire_without_environment_or_output(self):
        names = ('b_contract', 'b_fulfillment', 'b_production', 'b_delivery', 'b_response',
                 'b_recalculation', 'b_agent', 'b_fulfillment_agent', 'b_host_agent')
        for name in names:
            module = importlib.import_module('examples.order_fulfillment.' + name)
            with self.subTest(module=name), patch('ontologics.database.environment_values') as environment, \
                    patch.object(isolation.subprocess, 'Popen') as process, \
                    patch.object(Path, 'mkdir') as mkdir, patch.object(Path, 'open') as opened:
                with self.assertRaisesRegex(SystemExit, 'B_LEGACY_EXECUTION_RETIRED'):
                    module.main()
                environment.assert_not_called(); process.assert_not_called()
                mkdir.assert_not_called(); opened.assert_not_called()

    def test_five_internal_stored_examples_reject_before_inputs_or_resources(self):
        for name in ('b_fulfillment', 'b_production', 'b_delivery', 'b_response', 'b_recalculation'):
            module = importlib.import_module('examples.order_fulfillment.' + name)
            options = {'database': object(), 'schema': 'do-not-open'}
            if name in ('b_delivery', 'b_response'):
                options['mixed'] = True
            if name == 'b_recalculation':
                options['case'] = 'transport2h'
            with self.subTest(module=name), patch('psycopg.connect') as connect, \
                    patch('ontologics.database.environment_values') as environment, \
                    patch.object(isolation.subprocess, 'Popen') as process, \
                    patch.object(Path, 'mkdir') as mkdir, patch.object(Path, 'open') as opened:
                with self.assertRaisesRegex(RuntimeError, 'B_LEGACY_EXECUTION_RETIRED'):
                    module.run_example(**options)
                connect.assert_not_called(); environment.assert_not_called()
                process.assert_not_called(); mkdir.assert_not_called(); opened.assert_not_called()

    def test_all_remaining_stored_python_entries_reject_before_resources(self):
        entries = [
            ('b_contract', 'run_example', (), {}),
            ('b_agent', 'setup_intake', (), {'source_path': '/never-read'}),
            ('b_agent', 'setup_promise', (), {}),
            ('b_agent', 'run_mode', ('intake',), {}),
            ('b_fulfillment_agent', 'setup', (), {}),
            ('b_fulfillment_agent', 'run_example', (), {}),
            ('b_production_agent', 'setup', (), {}),
            ('b_delivery_agent', 'setup', (), {}),
            ('b_response_agent', 'setup', (), {}),
            ('b_comparison_agent', 'setup', (), {}),
            ('b_host_agent', 'run_scripted_example', ('intake',), {}),
            ('b_run_workflow', 'run_campaign', (), {
                'database_url': 'do-not-open', 'input_root': Path('/never-read'),
                'output': Path('/never-create'), 'cases': ['cq01_intake'], 'offline': False}),
        ]
        for module_name, function, args, kwargs in entries:
            module = importlib.import_module('examples.order_fulfillment.' + module_name)
            with self.subTest(module=module_name, function=function), \
                    patch('psycopg.connect') as connect, \
                    patch('ontologics.database.environment_values') as environment, \
                    patch.object(isolation.subprocess, 'Popen') as process, \
                    patch.object(Path, 'mkdir') as mkdir, patch.object(Path, 'open') as opened:
                with self.assertRaisesRegex(RuntimeError, 'B_LEGACY_EXECUTION_RETIRED'):
                    getattr(module, function)(*args, **kwargs)
                connect.assert_not_called(); environment.assert_not_called()
                process.assert_not_called(); mkdir.assert_not_called(); opened.assert_not_called()


class NotebookTasksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root_patch = patch.object(b_notebook, 'ROOT', self.root)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)
        self.demo = b_notebook.Demo(cases=['cq04_normal'])

    def saved(self, case='cq04_normal'):
        role, task = case_task(case)
        path = self.demo.outputs['tasks'] / 'campaign' / 'cq04_normal' / 'result.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(execution_mode='scripted', actual_api_attempts=0,
                    human_review='not_performed', answer_producer='scripted_provider',
                    result=dict(agent_name=role[0].SPEC.name, context=task.context.to_dict(),
                                payload={'answer_ko': '무료 대역 답변'}, runtime={'ok': True}))
        path.write_text(json.dumps(data))
        return path, data

    def test_old_free_and_direct_start_refuse_before_controls_or_process(self):
        with patch.object(b_notebook.subprocess, 'Popen') as process:
            for start in (self.demo.start_free, lambda: self.demo._start('free'),
                          lambda: self.demo._start('live'),
                          lambda: self.demo._start('tasks', ('--authorization', 'old'))):
                with self.assertRaisesRegex(RuntimeError, 'B_LEGACY_EXECUTION_RETIRED'):
                    start()
            process.assert_not_called()
        self.assertFalse(self.demo.control.exists())

    def test_task_completion_requires_both_new_marker_and_stopped_server(self):
        output = self.demo.outputs['tasks']; output.mkdir(parents=True)
        self.demo.processes['tasks'] = Mock(poll=Mock(return_value=0))
        (output / 'free_gate.json').write_text('{"passed":true}')
        (output / 'shutdown.json').write_text('{"server_stopped":true}')
        self.assertFalse(self.demo.status('tasks')['overall_passed'])
        (output / 'task_validation.json').write_text('{"passed":true}')
        self.assertTrue(self.demo.status('tasks')['overall_passed'])
        self.assertFalse(self.demo.status('tasks')['live_authorization_eligible'])
        (output / 'shutdown.json').write_text('{"server_stopped":false}')
        self.assertFalse(self.demo.status('tasks')['overall_passed'])

    def test_tasks_success_cannot_become_paid_gate_or_create_authorization(self):
        output = self.demo.outputs['tasks']; output.mkdir(parents=True)
        (output / 'task_validation.json').write_text('{"passed":true}')
        (output / 'shutdown.json').write_text('{"server_stopped":true}')
        self.demo.processes['tasks'] = Mock(poll=Mock(return_value=0))
        self.assertTrue(self.demo.status('tasks')['overall_passed'])
        with self.assertRaises(RuntimeError):
            self.demo.approval_draft()
        with self.assertRaises(RuntimeError), patch.object(b_notebook.subprocess, 'Popen') as process:
            self.demo.start_live_with_token(self.demo.approval_token())
        process.assert_not_called()
        self.assertFalse(self.demo.control.exists())

    def test_task_result_reads_only_new_path_and_keeps_execution_labels(self):
        path, data = self.saved()
        old = self.demo.outputs['free'] / 'campaign/reports/cq04_normal.json'
        old.parent.mkdir(parents=True); old.write_text('{"private":"old-answer"}')
        result = self.demo.result('tasks')
        self.assertEqual(data['result'], result['result'])
        self.assertEqual('scripted', result['execution_mode'])
        self.assertEqual('not_performed', result['human_review'])
        self.assertEqual(0, result['actual_api_attempts'])
        self.assertFalse(result['live_authorization_eligible'])
        self.assertNotIn('old-answer', json.dumps(result))
        self.assertEqual(b_notebook.sha(path), result['report_sha256'])

    def test_wrong_case_or_paid_report_is_rejected_instead_of_relabelled(self):
        path, data = self.saved(case='cq04_mixed')
        with self.assertRaises(ValueError):
            self.demo.result('tasks')
        path, data = self.saved()
        for field, value in (('execution_mode', 'actual_paid'), ('actual_api_attempts', 1),
                             ('human_review', 'passed')):
            modified = dict(data, **{field: value}); path.write_text(json.dumps(modified))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.demo.result('tasks')

    def test_missing_partial_and_linked_task_reports_never_replay_an_old_result(self):
        self.assertFalse(self.demo.result('tasks')['available'])
        path, _ = self.saved(); path.write_text('{')
        self.demo.processes['tasks'] = Mock(poll=Mock(return_value=None))
        self.assertTrue(self.demo.result('tasks')['being_written'])
        self.demo.processes['tasks'].poll.return_value = 0
        with self.assertRaises(json.JSONDecodeError):
            self.demo.result('tasks')
        path.unlink(); target = self.root / 'other.json'; target.write_text('{}'); path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.demo.result('tasks')
