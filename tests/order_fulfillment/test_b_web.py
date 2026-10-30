"""Local web routing and result boundaries; never launch a model or database."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from examples.order_fulfillment import b_web


class LabTests(unittest.TestCase):
    def test_catalog_uses_current_task_question_without_promoting_input_to_approved(self):
        lab = b_web.Lab()
        with patch.object(lab, 'live_readiness', return_value={'ready': False}):
            catalog = lab.catalog()
        self.assertEqual(10, len(catalog['cases']))
        comparison = next(row for row in catalog['cases'] if row['id'] == 'cq06_defects11')
        self.assertEqual(comparison['input']['question'], comparison['question'])
        self.assertNotIn('승인 버전', comparison['question'])

    def test_invalid_requests_are_rejected_before_constructing_runner(self):
        bodies = [None, [], {}, {'cases': []}, {'cases': 'cq01_intake'},
                  {'cases': ['unknown']}, {'cases': ['cq01_intake', 'cq01_intake']},
                  {'cases': [None]}, {'cases': [['cq01_intake']]},
                  {'cases': ['cq01_intake'], 'mode': 'paid'},
                  {'cases': ['cq01_intake'], 'cost_policy': '/private/settings'}]
        lab = b_web.Lab()
        with patch.object(b_web, 'Demo') as runner:
            for body in bodies:
                with self.subTest(body=body), self.assertRaises(ValueError):
                    lab.start(body)
        runner.assert_not_called()
        self.assertEqual({}, lab.runs)

    def test_scripted_mode_uses_existing_tasks_and_canonical_case_order(self):
        lab = b_web.Lab()
        demo = Mock()
        with patch.object(b_web, 'Demo', return_value=demo) as constructor, \
                patch.object(lab, '_start_live') as live, \
                patch.object(lab, 'snapshot', return_value={'started': True}):
            self.assertEqual({'started': True}, lab.start(
                {'cases': ['cq04_mixed', 'cq01_intake']}))
        constructor.assert_called_once_with(['cq01_intake', 'cq04_mixed'])
        demo.start_tasks.assert_called_once_with()
        live.assert_not_called()
        self.assertEqual('scripted', next(iter(lab.runs.values()))['mode'])

    def test_actual_mode_delegates_only_after_live_readiness(self):
        lab = b_web.Lab('/private/tmp/public.json')
        demo = Mock()
        with patch.object(b_web, 'Demo', return_value=demo), \
                patch.object(lab, 'live_readiness', return_value={'ready': True}), \
                patch.object(lab, '_start_live') as live, \
                patch.object(lab, 'snapshot', return_value={}):
            lab.start({'cases': ['cq01_intake'], 'mode': 'openai'})
        live.assert_called_once_with(demo)
        demo.start_tasks.assert_not_called()
        self.assertEqual('openai', next(iter(lab.runs.values()))['mode'])

    def test_unready_actual_mode_never_constructs_or_launches_runner(self):
        lab = b_web.Lab()
        with patch.object(lab, 'live_readiness', return_value={'ready': False}), \
                patch.object(b_web, 'Demo') as runner, self.assertRaises(ValueError):
            lab.start({'cases': ['cq01_intake'], 'mode': 'openai'})
        runner.assert_not_called()

    def test_running_request_blocks_a_second_launch(self):
        lab = b_web.Lab()
        lab.runs['existing'] = {'demo': Mock(running=Mock(return_value=True))}
        with patch.object(b_web, 'Demo') as runner, self.assertRaises(RuntimeError):
            lab.start({'cases': ['cq02_normal']})
        runner.assert_not_called()

    def test_actual_command_uses_existing_cli_and_clean_environment(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw).resolve()
            demo = SimpleNamespace(_controls=Mock(), control=directory,
                outputs={'tasks': directory / 'new-output'}, cases=['cq01_intake'],
                processes={}, started=set(), _stop_at_exit=Mock())
            process = Mock()
            with patch('subprocess.Popen', return_value=process) as launch, \
                    patch('atexit.register'), \
                    patch.dict('os.environ', {'OPENAI_API_KEY': 'sentinel-key',
                                              'DATABASE_URL': 'sentinel-db'}):
                b_web.Lab('/private/tmp/public.json')._start_live(demo)
            command = launch.call_args.args[0]
            self.assertEqual('openai', command[command.index('--transport') + 1])
            self.assertIn('examples.order_fulfillment.b_run', command)
            self.assertIn('run', command)
            self.assertEqual('1.00', command[command.index('--representative-max-cost-usd') + 1])
            self.assertNotIn('OPENAI_API_KEY', launch.call_args.kwargs['env'])
            self.assertNotIn('DATABASE_URL', launch.call_args.kwargs['env'])
            self.assertNotIn('shell', launch.call_args.kwargs)
            self.assertIs(process, demo.processes['tasks'])

    def snapshot(self, *, mode='scripted', running=False, exit_code=0, files=None):
        output = Path('/private/tmp/b-web-unit-owned')
        demo = SimpleNamespace(cases=['cq01_intake'], outputs={'tasks': output},
            status=Mock(return_value={'running': running, 'exit_code': exit_code}))
        lab = b_web.Lab()
        lab.runs['owned'] = {'demo': demo, 'mode': mode}
        with patch.object(lab, '_read_pending', side_effect=lambda p: (files or {}).get(str(p.relative_to(output)))):
            return lab.snapshot('owned')

    def test_pass_requires_summary_validation_exit_and_owned_shutdown(self):
        files = {'campaign/summary.json': {'passed': True},
                 'shutdown.json': {'server_stopped': True},
                 'task_validation.json': {'passed': True}}
        self.assertEqual('passed', self.snapshot(files=files)['status'])
        for missing in files:
            with self.subTest(missing=missing):
                incomplete = {k: v for k, v in files.items() if k != missing}
                self.assertEqual('failed', self.snapshot(files=incomplete)['status'])
        self.assertEqual('failed', self.snapshot(exit_code=1, files=files)['status'])
        self.assertEqual('running', self.snapshot(running=True, files=files)['status'])

    def test_actual_requires_its_model_validation_instead_of_task_validation(self):
        files = {'campaign/summary.json': {'passed': True},
                 'shutdown.json': {'server_stopped': True},
                 'task_validation.json': {'passed': True}}
        self.assertEqual('failed', self.snapshot(mode='openai', files=files)['status'])
        files['model_validation.json'] = {'passed': True}
        self.assertEqual('passed', self.snapshot(mode='openai', files=files)['status'])

    def test_partial_failure_preserves_raw_model_result_and_safe_failure_type(self):
        report = {'execution_mode': 'openai', 'actual_api_attempts': 4,
                  'result': {'runtime': {'ok': False}, 'payload': None,
                             'error': {'code': 'AGENT_EVIDENCE_MISMATCH'}}}
        files = {'campaign/cq01_intake/result.json': report,
                 'campaign/stopped.json': {'case': 'cq01_intake'},
                 'execution_failure.json': {'error_type': 'ValueError'}}
        result = self.snapshot(mode='openai', exit_code=1, files=files)
        self.assertEqual('failed', result['status'])
        self.assertEqual(report, result['results']['cq01_intake'])
        self.assertEqual(4, result['actual_api_attempts'])
        self.assertEqual('ValueError', result['error']['error_type'])
        self.assertEqual('cq01_intake', result['error']['case'])

    def test_unknown_run_never_discovers_historical_results(self):
        with self.assertRaises(KeyError):
            b_web.Lab().snapshot('historical')

    def test_failed_call_count_is_preserved_without_a_final_result(self):
        result = self.snapshot(mode='openai', exit_code=1, files={
            'campaign/stopped.json': {'case': 'cq01_intake', 'actual_api_attempts': 2}})
        self.assertEqual({}, result['results'])
        self.assertEqual(2, result['actual_api_attempts'])
        self.assertEqual('failed', result['status'])

    def test_partial_json_is_pending_and_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / 'result.json'
            self.assertIsNone(b_web.Lab._read_pending(path))
            path.write_text('{"incomplete":', encoding='utf-8')
            self.assertIsNone(b_web.Lab._read_pending(path))
            path.write_text('{"complete":true}', encoding='utf-8')
            self.assertEqual({'complete': True}, b_web.Lab._read_pending(path))

    def test_close_stops_only_its_active_run(self):
        lab = b_web.Lab()
        active = Mock(running=Mock(return_value=True))
        finished = Mock(running=Mock(return_value=False))
        lab.runs = {'active': {'demo': active}, 'finished': {'demo': finished}}
        lab.close()
        active.stop.assert_called_once_with('tasks')
        finished.stop.assert_not_called()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.lab = b_web.Lab()
        self.lab.catalog = Mock(return_value={'cases': [], 'csrf_token': self.lab.csrf_token})
        self.lab.start = Mock(return_value={'id': 'new', 'status': 'running'})
        self.lab.snapshot = Mock(side_effect=KeyError('unknown'))
        self.server = b_web.Server(0, self.lab)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post_headers(self):
        return {'Content-Type': 'application/json', 'Origin': self.server.origin,
                'X-Ontol-Token': self.lab.csrf_token}

    def test_catalog_same_origin_is_readable_and_cache_and_frame_protected(self):
        status, headers, raw = self.request('GET', '/api/catalog')
        self.assertEqual(200, status)
        self.assertEqual(self.lab.csrf_token, json.loads(raw)['csrf_token'])
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_foreign_host_origin_and_fetch_site_are_rejected(self):
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(403, self.request('GET', '/api/catalog', headers=headers)[0])
        self.lab.catalog.assert_not_called()

    def test_post_requires_current_token_and_same_origin_before_launch(self):
        for changes in ({'X-Ontol-Token': ''}, {'X-Ontol-Token': 'stale'},
                        {'Origin': 'https://evil.example'}, {'Host': 'evil.example'}):
            headers = self.post_headers()
            headers.update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(403, self.request('POST', '/api/runs', '{}', headers)[0])
        self.lab.start.assert_not_called()

    def test_valid_post_passes_json_to_lab_once(self):
        body = {'cases': ['cq01_intake'], 'mode': 'openai'}
        status, _, raw = self.request('POST', '/api/runs', json.dumps(body), self.post_headers())
        self.assertEqual(202, status)
        self.assertEqual('new', json.loads(raw)['id'])
        self.lab.start.assert_called_once_with(body)

    def test_non_ascii_csrf_token_is_rejected_without_dropping_connection(self):
        headers = self.post_headers()
        headers['X-Ontol-Token'] = 'caf\u00e9'
        status, _, raw = self.request('POST', '/api/runs', '{}', headers)
        self.assertEqual(403, status)
        self.assertIn('error', json.loads(raw))
        self.lab.start.assert_not_called()

    def test_bounded_json_and_content_type_are_required(self):
        requests = [('{}', {'Content-Type': 'text/plain'}), ('x' * 4097, {}), ('not-json', {})]
        for body, changes in requests:
            headers = self.post_headers()
            headers.update(changes)
            with self.subTest(body=body[:12]):
                self.assertEqual(400, self.request('POST', '/api/runs', body, headers)[0])
        self.lab.start.assert_not_called()

    def test_busy_and_invalid_requests_have_actionable_http_status(self):
        for error, expected in ((RuntimeError(), 409), (ValueError(), 400)):
            self.lab.start.side_effect = error
            self.assertEqual(expected, self.request('POST', '/api/runs', '{}', self.post_headers())[0])

    def test_static_paths_are_an_allowlist_and_unknown_runs_do_not_leak_paths(self):
        for path in ('/../.env.local', '/%2e%2e/.env.local', '/api/runs/historical', '/private.json'):
            with self.subTest(path=path):
                status, _, raw = self.request('GET', path)
                self.assertEqual(404, status)
                self.assertNotIn(b'private', raw)
        self.assertEqual(404, self.request('POST', '/arbitrary', '{}', self.post_headers())[0])


if __name__ == '__main__':
    unittest.main()
