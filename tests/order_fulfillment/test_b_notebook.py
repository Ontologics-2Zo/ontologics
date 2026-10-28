"""Notebook control tests; no model requests, credentials, or database servers."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from examples.order_fulfillment import b_notebook as notebook


class NotebookDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / 'artifacts/local/order_fulfillment').mkdir(parents=True)
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').write_text('fake interpreter; never executed')
        self.root_patch = patch.object(notebook, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.root_patch.stop)

    def gate(self, demo):
        out = demo.outputs['free']; out.mkdir()
        (out / 'campaign').mkdir()
        (out / 'campaign/summary.json').write_text('{"passed":true}')
        notebook._write_new(out / 'shutdown.json', {'server_stopped': True})
        notebook._write_new(out / 'free_gate.json', dict(passed=True, mode='free',
            cases=list(demo.cases), source_sha256='a' * 64, actual_api_attempts=0,
            summary_sha256=notebook.sha(out / 'campaign/summary.json'),
            shutdown_sha256=notebook.sha(out / 'shutdown.json')))
        demo.processes['free'] = Mock(poll=Mock(return_value=0))

    def test_construction_and_default_views_are_inert(self):
        with patch.object(notebook.subprocess, 'Popen') as launch:
            demo = notebook.Demo()
            self.assertFalse(demo.control.exists())
            self.assertFalse(demo.status('live')['started'])
            self.assertFalse(demo.result('live')['available'])
            self.assertFalse(demo.result('free')['available'])
        launch.assert_not_called()

    def test_live_rejects_empty_or_old_confirmation_before_any_write(self):
        demo = notebook.Demo()
        for phrase in ('', 'yes', notebook.Demo().approval_phrase()):
            with self.subTest(phrase=phrase), self.assertRaises(PermissionError):
                demo.start_live(phrase)
        self.assertFalse(demo.control.exists())

    def test_exact_confirmation_without_completed_free_gate_is_still_blocked(self):
        demo = notebook.Demo()
        with self.assertRaises(RuntimeError), patch.object(notebook.subprocess, 'Popen') as launch:
            demo.start_live(demo.approval_phrase())
        launch.assert_not_called()
        self.assertFalse(demo.control.exists())

    def test_free_starts_only_existing_cli_with_clean_environment(self):
        demo = notebook.Demo()
        process = Mock(pid=12345, poll=Mock(return_value=None))
        with patch.object(notebook.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(notebook.atexit, 'register'), \
                patch.dict('os.environ', {'OPENAI_API_KEY': 'never-forward', 'DATABASE_URL': 'never-forward'}):
            demo.start_free()
        args, kwargs = launch.call_args
        self.assertIn('free', args[0])
        self.assertNotIn('live', args[0])
        self.assertIn('examples.order_fulfillment.b_run', args[0])
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
        self.assertNotIn('DATABASE_URL', kwargs['env'])
        self.assertNotIn('shell', kwargs)
        self.assertTrue(kwargs['start_new_session'])
        with self.assertRaises(RuntimeError):
            demo.start_free()

    def test_approval_draft_is_unapproved_and_stale_source_is_rejected(self):
        demo = notebook.Demo(); self.gate(demo)
        with patch.object(notebook, 'source_digest', return_value='a' * 64):
            draft = demo.approval_draft()
        self.assertIs(draft['approved'], False)
        self.assertIs(draft['input_and_money_caps_unfixed_acknowledged'], False)
        self.assertEqual(draft['cases'], ['cq04_normal'])
        self.assertEqual(draft['total_request_cap'], 4)
        self.assertEqual(draft['cq01_request_cap'], 0)
        with patch.object(notebook, 'source_digest', return_value='b' * 64), self.assertRaises(ValueError):
            demo.approval_draft()

    def test_matching_confirmation_prepares_exact_live_cli_once_using_fake_process(self):
        demo = notebook.Demo(); self.gate(demo)
        process = Mock(pid=12345, poll=Mock(return_value=None))
        with patch.object(notebook, 'source_digest', return_value='a' * 64), \
                patch.object(notebook.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(notebook.atexit, 'register'):
            demo.start_live(demo.approval_phrase())
        args = launch.call_args.args[0]
        self.assertIn('live', args)
        self.assertIn('--authorization', args)
        self.assertIn('--free-gate', args)
        auth = notebook._read(demo.control / 'authorization.json')
        self.assertIs(auth['approved'], True)
        self.assertEqual(auth['run_id'], str(demo.outputs['live']))
        with self.assertRaises(RuntimeError):
            demo.start_live(demo.approval_phrase())

    def test_modified_summary_and_changed_case_gate_are_rejected(self):
        demo = notebook.Demo(); self.gate(demo)
        (demo.outputs['free'] / 'campaign/summary.json').write_text('{}')
        with patch.object(notebook, 'source_digest', return_value='a' * 64), self.assertRaises(ValueError):
            demo.approval_draft()

    def test_write_new_never_overwrites_and_rejects_symlinks(self):
        path = self.root / 'review.json'
        notebook._write_new(path, {'original': True})
        with self.assertRaises(FileExistsError):
            notebook._write_new(path, {'original': False})
        self.assertEqual(notebook._read(path), {'original': True})
        link = self.root / 'linked.json'; link.symlink_to(path)
        with self.assertRaises(ValueError):
            notebook._write_new(link, {})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def pending(self, demo, case='cq04_normal'):
        out = demo.outputs['live']; (out / 'campaign/reports').mkdir(parents=True, exist_ok=True)
        (out / 'submissions').mkdir(exist_ok=True)
        report = out / 'campaign/reports' / (case + '.json')
        notebook._write_new(report, dict(case=case, execution_mode='actual_paid',
            passed=True, checks={'one': True}, runtime={'output_hash': 'b' * 64}))
        request = dict(case=case, kind='separate_korean_semantic_review',
            report=str(report), report_sha256=notebook.sha(report), bound_output_hash='b' * 64)
        notebook._write_new(out / 'submissions' / (case + '-separate_korean_semantic_review.requested.json'), request)
        demo.processes['live'] = Mock(poll=Mock(return_value=None))
        return report

    def test_review_has_no_default_pass_and_requires_human_korean_notes(self):
        demo = notebook.Demo(); self.pending(demo)
        with self.assertRaises(ValueError):
            demo.submit_review('', passed=True)
        result = demo.submit_review('근거와 답변의 일치를 확인하지 못해 보류합니다.')
        self.assertIs(result['passed'], False)
        self.assertIs(notebook._read(demo.outputs['live'] /
            'submissions/cq04_normal-separate_korean_semantic_review.json')['passed'], False)

    def test_modified_report_cannot_receive_old_review(self):
        demo = notebook.Demo(); report = self.pending(demo)
        report.write_text(report.read_text() + ' ')
        with self.assertRaises(ValueError):
            demo.submit_review('자료·조건·근거를 확인했습니다.', passed=True)

    def test_failed_owner_publication_still_terminates_spawned_worker(self):
        demo = notebook.Demo()
        process = Mock(pid=12345, poll=Mock(return_value=None))
        with patch.object(notebook.subprocess, 'Popen', return_value=process), \
                patch.object(notebook.atexit, 'register'), \
                patch.object(notebook, '_write_new', side_effect=OSError('fake disk failure')):
            with self.assertRaises(OSError):
                demo.start_free()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=45)

    def test_failed_stop_marker_does_not_prevent_owner_termination(self):
        demo = notebook.Demo()
        (demo.outputs['live'] / 'submissions').mkdir(parents=True)
        process = Mock(poll=Mock(return_value=None))
        demo.processes['live'] = process
        with patch.object(notebook, '_write_new', side_effect=OSError('fake disk failure')):
            result = demo.stop('live')
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=45)
        self.assertEqual(result['stop_marker_error'], 'OSError')

    def test_partial_live_report_is_pending_only_while_worker_is_running(self):
        demo = notebook.Demo()
        path = demo.outputs['live'] / 'campaign/reports/cq04_normal.json'
        path.parent.mkdir(parents=True)
        path.write_text('{')
        process = Mock(poll=Mock(return_value=None))
        demo.processes['live'] = process
        self.assertFalse(demo.result('live')['available'])
        process.poll.return_value = 0
        with self.assertRaises(json.JSONDecodeError):
            demo.result('live')

    def test_partial_review_request_is_not_accepted_as_complete(self):
        demo = notebook.Demo(); self.pending(demo)
        path = demo.outputs['live'] / 'submissions/cq04_normal-separate_korean_semantic_review.requested.json'
        path.write_text('{')
        self.assertIsNone(demo.review_request())

    def test_stop_terminates_owner_and_checks_shutdown_without_killing_database(self):
        demo = notebook.Demo()
        (demo.outputs['live'] / 'submissions').mkdir(parents=True)
        process = Mock(poll=Mock(side_effect=[None, 0, 0, 0]))
        demo.processes['live'] = process
        demo.stop('live')
        self.assertTrue((demo.outputs['live'] / 'submissions/stop.json').exists())
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=45)
        process.kill.assert_not_called()

    def test_existing_control_directory_is_not_reused(self):
        demo = notebook.Demo(); demo.control.mkdir()
        with self.assertRaises(FileExistsError):
            demo._controls()

    def test_wait_does_not_start_process_and_rejects_long_wait(self):
        demo = notebook.Demo()
        with patch.object(notebook.subprocess, 'Popen') as launch:
            self.assertFalse(demo.wait('free', 0)['started'])
            with self.assertRaises(ValueError):
                demo.wait('free', 61)
        launch.assert_not_called()


    def test_full_case_plan_is_canonical_bounded_and_inert(self):
        demo = notebook.Demo(cases=notebook.CASES)
        self.assertEqual(demo.cases, tuple(notebook.CASES))
        summary = demo.plan_summary()
        self.assertEqual(summary['total_request_cap'], 40)
        self.assertEqual(summary['cq01_request_cap'], 8)
        self.assertEqual(len(summary['representatives']), 10)
        self.assertTrue(all(row['max_requests'] == 4 for row in summary['representatives']))
        self.assertFalse(demo.control.exists())
        self.assertEqual(set(demo.results('live')), set(notebook.CASES))
        self.assertTrue(all(not value['available'] for value in demo.results('live').values()))
        with self.assertRaises(ValueError):
            demo.result('live')
        with self.assertRaises(ValueError):
            demo.result('live', case='../cq04_normal')
        with self.assertRaises(AttributeError):
            demo.cases = ('cq04_normal',)

    def test_invalid_selection_refused_without_any_controls(self):
        for cases in ([], ['cq04_normal', 'cq04_normal'], ['cq02_normal', 'cq01_intake'],
                      ['old_case'], 'cq04_normal'):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                notebook.Demo(cases=cases)
        self.assertEqual(list((self.root / 'artifacts/local/order_fulfillment').iterdir()), [])

    def test_full_plan_requires_matching_free_gate_and_binds_all_40_requests(self):
        demo = notebook.Demo(cases=notebook.CASES); self.gate(demo)
        with patch.object(notebook, 'source_digest', return_value='a' * 64):
            draft = demo.approval_draft()
        self.assertEqual(draft['cases'], notebook.CASES)
        self.assertEqual(draft['total_request_cap'], 40)
        self.assertEqual(draft['cq01_request_cap'], 8)
        self.assertFalse(draft['approved'])
        self.assertIn('10대표 최대40요청', demo.approval_phrase())
        gate_path = demo.outputs['free'] / 'free_gate.json'
        gate = notebook._read(gate_path); gate['cases'] = ['cq04_normal']
        gate_path.write_text(json.dumps(gate))
        with patch.object(notebook, 'source_digest', return_value='a' * 64), self.assertRaises(ValueError):
            demo.approval_draft()

    def test_full_cli_receives_exact_cases_once_and_fresh_full_authorization(self):
        demo = notebook.Demo(cases=notebook.CASES); self.gate(demo)
        process = Mock(pid=12345, poll=Mock(return_value=None))
        with patch.object(notebook, 'source_digest', return_value='a' * 64), \
                patch.object(notebook.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(notebook.atexit, 'register'):
            demo.start_live(demo.approval_phrase())
        args = launch.call_args.args[0]
        self.assertEqual([args[i + 1] for i, part in enumerate(args) if part == '--case'], notebook.CASES)
        auth = notebook._read(demo.control / 'authorization.json')
        self.assertEqual(auth['total_request_cap'], 40)
        self.assertEqual(auth['cq01_request_cap'], 8)
        self.assertTrue(auth['approved'])

    def test_pending_review_advances_only_after_explicit_case_submission(self):
        demo = notebook.Demo(cases=['cq02_normal', 'cq03_normal'])
        self.pending(demo, 'cq02_normal'); self.pending(demo, 'cq03_normal')
        self.assertEqual(demo.review_request()['case'], 'cq02_normal')
        self.assertEqual(demo.review_request(case='cq03_normal')['case'], 'cq03_normal')
        demo.submit_review('수량과 근거의 일치를 확인하지 못해 보류합니다.', case='cq02_normal')
        self.assertEqual(demo.review_request()['case'], 'cq03_normal')
        self.assertIsNone(demo.review_request(case='cq02_normal'))
        with self.assertRaises(RuntimeError):
            demo.submit_review('중복 검토를 제출하지 않습니다.', passed=True, case='cq02_normal')

    def test_fixed_input_previews_use_only_hash_bound_sources_for_each_case(self):
        demo = notebook.Demo(cases=notebook.CASES)
        manifest = {}
        for case in notebook.CASES:
            source = notebook.CASE_DETAILS[case][2]
            relative = 'data/order_fulfillment/sources/b_demo_' + source + '_01.md'
            path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('고정된 가상 원문: ' + source)
            manifest[relative] = notebook.sha(path)
        with patch.object(notebook, 'SAFE_INPUTS', manifest):
            for case in notebook.CASES:
                preview = demo.input_preview(case=case)
                self.assertEqual(preview['case'], case)
                self.assertEqual(preview['source_sha256'], manifest[preview['source_path']])
                self.assertIn('고정된 가상 원문', preview['source_text'])
            (self.root / 'data/order_fulfillment/sources/b_demo_contract_01.md').write_text('변경된 원문')
            with self.assertRaises(ValueError):
                demo.input_preview(case='cq01_intake')

    def test_full_result_reads_only_selected_current_case_and_never_old_answers(self):
        demo = notebook.Demo(cases=['cq02_normal', 'cq03_normal'])
        out = demo.outputs['live']; (out / 'campaign/reports').mkdir(parents=True)
        report = dict(case='cq03_normal', execution_mode='actual_paid', task={'question': '생산 예상'},
                      actual_api_attempts=4, http_requests=4, usage=[], passed=True, checks={'all': True},
                      runtime={'text': '이번 응답', 'tool_trace': []}, answer={'current': True}, service_query_raw={})
        notebook._write_new(out / 'campaign/reports/cq03_normal.json', report)
        self.assertFalse(demo.result('live', case='cq02_normal')['available'])
        result = demo.result('live', case='cq03_normal')
        self.assertEqual(result['case'], 'cq03_normal')
        self.assertEqual(result['model_text'], '이번 응답')
        self.assertEqual(result['actual_api_attempts'], 4)
        with self.assertRaises(ValueError):
            demo.result('live', case='cq04_normal')

    def draft_pending(self, demo):
        report = self.pending(demo, 'cq01_intake')
        value = notebook._read(report)
        draft = dict(id='new-draft', version=1, author='user:task11-author',
                     status='awaiting_review', content_hash='c' * 64,
                     operations=[{'op': 'put', 'node': {'id': 'contract:new'}}])
        value.update(answer=dict(draft_id='new-draft', draft_version=1),
                     after_business_rows=dict(drafts=[draft]))
        report.write_text(json.dumps(value))
        semantic_path = demo.outputs['live'] / 'submissions/cq01_intake-separate_korean_semantic_review.requested.json'
        semantic = notebook._read(semantic_path); semantic['report_sha256'] = notebook.sha(report)
        semantic_path.write_text(json.dumps(semantic))
        demo.submit_review('원문과 초안 및 근거가 일치합니다.', passed=True, case='cq01_intake')
        request = dict(kind='cq01_draft_review', case='cq01_intake', draft_id='new-draft',
                       version=1, content_hash='c' * 64, author='user:task11-author',
                       reviewer='user:task11-reviewer', development_fixture=True)
        path = demo.outputs['live'] / 'submissions/cq01_intake-cq01_draft_review.requested.json'
        notebook._write_new(path, request)
        return path, report

    def test_cq01_draft_exposes_exact_material_and_default_rejects(self):
        demo = notebook.Demo(cases=notebook.CASES); self.draft_pending(demo)
        request = demo.draft_review_request()
        self.assertEqual(request['draft_material']['operations'][0]['node']['id'], 'contract:new')
        self.assertEqual(request['report_sha256'], notebook.sha(demo.outputs['live'] / 'campaign/reports/cq01_intake.json'))
        result = demo.submit_draft_review('검토가 끝나지 않아 반려합니다.')
        self.assertEqual(result['decision'], 'reject')
        self.assertIsNone(demo.draft_review_request())
        saved = notebook._read(demo.outputs['live'] / 'submissions/cq01_intake-cq01_draft_review.json')
        self.assertNotIn('draft_material', saved)
        self.assertNotIn('report_sha256', saved)
        self.assertEqual(saved['decision'], 'reject')

    def test_cq01_draft_requires_human_notes_and_explicit_approve(self):
        demo = notebook.Demo(cases=notebook.CASES); self.draft_pending(demo)
        with self.assertRaises(ValueError):
            demo.submit_draft_review('', decision='approve')
        with self.assertRaises(ValueError):
            demo.submit_draft_review('확인했습니다.', decision=True)
        result = demo.submit_draft_review('원문과 구성·수량·도착 기한을 확인했습니다.', decision='approve')
        self.assertEqual(result['decision'], 'approve')
        self.assertEqual(result['content_hash'], 'c' * 64)

    def test_cq01_draft_rejects_self_review_changed_version_and_content(self):
        for field, replacement in [('reviewer', 'user:task11-author'), ('version', 2),
                                   ('content_hash', 'd' * 64), ('development_fixture', False)]:
            with self.subTest(field=field):
                demo = notebook.Demo(cases=notebook.CASES); path, _ = self.draft_pending(demo)
                request = notebook._read(path); request[field] = replacement
                path.write_text(json.dumps(request))
                with self.assertRaises(ValueError):
                    demo.submit_draft_review('원문 검토 완료', decision='approve')

    def test_cq01_draft_rejects_report_mutation_after_semantic_review(self):
        demo = notebook.Demo(cases=notebook.CASES); _, report = self.draft_pending(demo)
        report.write_text(report.read_text() + ' ')
        with self.assertRaises(ValueError):
            demo.draft_review_request()

    def test_cq04_session_has_no_cq01_draft_review(self):
        demo = notebook.Demo()
        self.assertIsNone(demo.draft_review_request())


if __name__ == '__main__':
    unittest.main()
