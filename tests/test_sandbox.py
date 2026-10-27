"""Contract tests run everywhere; real container checks are explicitly opt-in."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import tempfile
import threading
import unittest

from ontologics.model import ROOT
from ontologics.demo import example_queries
from ontologics.sandbox import Controller, DockerRunner, JobSpec, SandboxClient, SandboxError, validate_result
from tests.test_web import WebCase

IMAGE = os.environ.get('ONTOLOGICS_TEST_SANDBOX_IMAGE')
DOCKER = os.environ.get('ONTOLOGICS_TEST_DOCKER', '/usr/local/bin/docker')


class SandboxContractTests(unittest.TestCase):
    def test_jobs_reject_caller_commands_identity_and_bad_hash(self):
        digest = hashlib.sha256(b'{}').hexdigest()
        with self.assertRaises(SandboxError):
            JobSpec('shell', digest, 'sha256:' + digest)
        with self.assertRaises(SandboxError):
            JobSpec('history_json', digest, '/etc/passwd')
        with self.assertRaises(SandboxError):
            SandboxClient('/does-not-exist').parse('history_json', b'{}', '0' * 64)
        with self.assertRaises(ValueError):
            DockerRunner('python:latest')
        with self.assertRaises(SandboxError) as caught:
            SandboxClient('/does-not-exist').parse('history_json', b'{}', digest)
        self.assertEqual('SANDBOX_UNAVAILABLE', caught.exception.code)

    def test_output_rejects_extra_fields_wrong_digest_and_invalid_rows(self):
        digest = hashlib.sha256(b'{}').hexdigest()
        valid = {'schema_version': 'sandbox-1', 'kind': 'history_json', 'sha256': digest, 'result': {}}
        self.assertEqual(valid, validate_result('history_json', digest, json.dumps(valid).encode()))
        for altered in [dict(valid, path='/etc/passwd'), dict(valid, sha256='0' * 64),
                        dict(valid, result={'bad': float('nan')}), dict(valid, kind='shell')]:
            with self.assertRaises(SandboxError):
                validate_result('history_json', digest, json.dumps(altered).encode())
        malformed = dict(valid, kind='olimex_ods', result=[{'positions': ['../../escape']}])
        with self.assertRaises(SandboxError):
            validate_result('olimex_ods', digest, json.dumps(malformed).encode())

    def test_controller_rejects_unknown_fields_before_running(self):
        class NoRun:
            calls = 0
            def parse(self, *args):
                self.calls += 1
                raise AssertionError('must not execute')
        runner = NoRun()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'parser.sock'
            with Controller(path, runner) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    self.assertEqual(0o600, path.stat().st_mode & 0o777)
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                        conn.connect(str(path))
                        conn.sendall(b'{"kind":"history_json","image":"attacker-image","mount":"/"}\n')
                        answer = json.loads(conn.makefile('rb').readline())
                    self.assertFalse(answer['ok'])
                    self.assertEqual(0, runner.calls)
                finally:
                    server.shutdown()
                    thread.join()
            self.assertFalse(path.exists())

    def test_client_normalizes_nonobject_controller_responses(self):
        class Malformed(socketserver.StreamRequestHandler):
            def handle(self):
                self.rfile.readline()
                self.wfile.write(b'[]\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'malformed.sock'
            with socketserver.UnixStreamServer(str(path), Malformed) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with self.assertRaises(SandboxError) as caught:
                        SandboxClient(path).parse('history_json', b'{}', hashlib.sha256(b'{}').hexdigest())
                    self.assertEqual('SANDBOX_OUTPUT_INVALID', caught.exception.code)
                finally:
                    server.shutdown()
                    thread.join()


@unittest.skipUnless(IMAGE, 'Set ONTOLOGICS_TEST_SANDBOX_IMAGE to a locally reviewed immutable image ID')
class DockerSandboxTests(unittest.TestCase):
    def setUp(self):
        self.runner = DockerRunner(IMAGE, docker=DOCKER)

    def test_actual_ods_parsing_and_digest(self):
        content = (ROOT / 'data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.ods').read_bytes()
        answer = self.runner.parse('olimex_ods', content, hashlib.sha256(content).hexdigest())
        self.assertEqual(74, len(answer['result']))
        self.assertEqual(128, sum(r['quantity'] for r in answer['result']))
        self.assertEqual(12, sum(r['population'] == 'unknown' for r in answer['result']))
        r50 = next(r for r in answer['result'] if 'R50' in r['positions'])
        self.assertEqual('unknown', r50['population'])
        self.assertEqual('none', answer['sandbox']['network'])

    def test_forbidden_access_and_seccomp_are_active(self):
        probe = self.runner.probe('isolation_probe')
        self.assertEqual('none', probe['sandbox']['observed_network_mode'])
        result = probe['result']
        self.assertEqual(65532, result.pop('uid'))
        self.assertEqual(64, result.pop('nofile_limit'))
        interfaces = result.pop('interfaces')
        flags = result.pop('interface_flags')
        self.assertIn('lo', interfaces)
        self.assertEqual(set(interfaces), set(flags))
        # Docker Desktop may create DOWN gre/sit tunnel devices in a new netns.
        self.assertTrue(all(not flag & 1 for name, flag in flags.items() if name != 'lo'), flags)
        self.assertEqual(128 * 1024 * 1024, result.pop('memory_limit_bytes'))
        self.assertEqual(32, result.pop('pids_limit'))
        quota, period = map(int, result.pop('cpu_quota').split())
        self.assertEqual(.5, quota / period)
        self.assertTrue(all(result.values()), result)

    def test_timeout_children_and_output_limit_are_cleaned(self):
        for kind, code in [('timeout_probe', 'SANDBOX_TIMEOUT'), ('output_probe', 'SANDBOX_OUTPUT_LIMIT'),
                           ('memory_probe', 'SANDBOX_JOB_FAILED')]:
            runner = DockerRunner(IMAGE, docker=DOCKER, timeout_seconds=2 if kind == 'timeout_probe' else 15)
            with self.assertRaises(SandboxError) as caught:
                runner.probe(kind)
            self.assertEqual(code, caught.exception.code)
            if kind == 'memory_probe':
                self.assertEqual(137, caught.exception.details['exit_code'])
            remaining = subprocess.check_output([DOCKER, 'ps', '-aq', '--filter', 'name=ontologics-parser-']).strip()
            self.assertEqual(b'', remaining)
        pids = self.runner.probe('pids_probe')['result']
        self.assertTrue(pids['pids_denied'])
        self.assertLess(pids['created_children'], 32)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(SandboxError) as caught:
            self.runner.probe('timeout_probe', cancel_event=cancelled)
        self.assertEqual('SANDBOX_CANCELLED', caught.exception.code)
        self.assertEqual(b'', subprocess.check_output([DOCKER, 'ps', '-aq', '--filter', 'name=ontologics-parser-']).strip())

    def test_archive_traversal_is_rejected_in_container(self):
        import io
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('../escape', 'synthetic')
            archive.writestr('content.xml', '<invalid/>')
        content = stream.getvalue()
        with self.assertRaises(SandboxError) as caught:
            self.runner.parse('olimex_ods', content, hashlib.sha256(content).hexdigest())
        self.assertEqual('SANDBOX_JOB_FAILED', caught.exception.code)


@unittest.skipUnless(IMAGE, 'Opt-in real Docker + PostgreSQL + HTTP integration')
class SandboxIntakeTests(WebCase):
    reserve_l1 = True

    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.directory.name) / 'parser.sock', DockerRunner(IMAGE, docker=DOCKER))
        self.thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        self.thread.start()
        self.host._sandbox_client = SandboxClient(self.controller.server_address)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.controller.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.controller.shutdown)

    def test_receipt_draft_approval_and_unavailable_fail_closed(self):
        path = ROOT / 'data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.ods'
        upload = {'filename': path.name, 'content_base64': base64.b64encode(path.read_bytes()).decode()}
        received = self.ok(self.http(self.alice, '/api/sources/intake', upload))
        self.assertTrue(received['data']['parsing']['isolated'])
        draft = self.ok(self.http(self.alice, '/api/sources/draft',
            {'receipt_id': received['data']['receipt_id'], 'request_key': 'sandbox-register'}))['data']
        _, denied, _ = self.http(self.alice, '/api/review', self.review_body(draft))
        self.assertFalse(denied['ok'])
        self.assertEqual('SELF_APPROVAL_FORBIDDEN', denied['errors'][0]['code'])
        approved = self.ok(self.http(self.bob, '/api/review', self.review_body(draft)))
        self.assertEqual(2, approved['data_version'])

        # The same isolated runner also handles untrusted synthetic S1 JSON.
        history = {'origin': 'synthetic', 'authored_at': '2026-10-26T10:00:00+09:00',
                   'notice': 'Sandbox test fictional evidence ' + self.database.schema,
                   'records': [{k: v for k, v in op['node'].items() if k not in {'origin', 'evidence'}}
                               for op in self.operations if op['node']['type'] in {'Installation', 'HistoryCoverage'}]}
        raw = json.dumps(history).encode()
        receipt_file = ROOT / 'data/receipts' / (hashlib.sha256(raw).hexdigest() + '.json')
        if not receipt_file.exists():
            self.addCleanup(lambda: receipt_file.unlink(missing_ok=True))
        received_history = self.ok(self.http(self.alice, '/api/history/intake',
            {'filename': 'synthetic.json', 'content_base64': base64.b64encode(raw).decode()}))
        self.assertTrue(received_history['data']['parsing']['isolated'])
        self.assertEqual('synthetic', received_history['data']['source']['origin'])
        supplement = self.ok(self.http(self.alice, '/api/sources/draft',
            {'receipt_id': received_history['data']['receipt_id'], 'request_key': 'sandbox-history'}))['data']
        self.assertEqual(3, self.ok(self.http(self.bob, '/api/review', self.review_body(supplement, key='sandbox-history-review')))['data_version'])
        for mode, query in example_queries().items():
            before = self.ok(self.tool('investigate', {'query': query, 'version': 2}))['data']['results']
            after = self.ok(self.tool('investigate', {'query': query, 'version': 3}))['data']['results']
            self.assertEqual('needs_confirmation', next(r for r in before if r['unit_id'] == 'unit:U104')['status'])
            self.assertEqual('excluded' if mode == 'A' else 'included', next(r for r in after if r['unit_id'] == 'unit:U104')['status'])
            self.assertEqual('needs_confirmation', next(r for r in after if r['unit_id'] == 'unit:U106')['status'])

        self.host._sandbox_client = SandboxClient('/missing/parser.sock')
        _, failed, _ = self.http(self.alice, '/api/sources/intake', upload)
        self.assertFalse(failed['ok'])
        self.assertEqual('SANDBOX_UNAVAILABLE', failed['errors'][0]['code'])
        self.assertEqual(3, self.ok(self.http(self.alice, '/api/context'))['data_version'])

    def test_legacy_receipt_requires_isolated_reintake_with_same_bytes_and_version(self):
        path = ROOT / 'data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.ods'
        upload = {'filename': path.name, 'content_base64': base64.b64encode(path.read_bytes()).decode()}
        client = self.host._sandbox_client
        self.host._sandbox_client = None
        legacy = self.ok(self.http(self.alice, '/api/sources/intake', upload))
        self.assertFalse(legacy['data']['parsing']['isolated'])
        saved = self.ok(self.http(self.alice, '/api/sources/draft',
            {'receipt_id': legacy['data']['receipt_id'], 'request_key': 'legacy-saved-draft'}))
        self.host._sandbox_client = SandboxClient('/missing/controller.sock')
        _, blocked, _ = self.http(self.alice, '/api/sources/intake', upload)
        self.assertFalse(blocked['ok'])
        self.assertEqual('SANDBOX_UNAVAILABLE', blocked['errors'][0]['code'])
        _, stale_policy, _ = self.http(self.alice, '/api/sources/draft',
            {'receipt_id': legacy['data']['receipt_id'], 'request_key': 'new-write-from-legacy'})
        self.assertEqual('SANDBOX_REINTAKE_REQUIRED', stale_policy['errors'][0]['code'])
        replay = self.ok(self.http(self.alice, '/api/sources/draft',
            {'receipt_id': legacy['data']['receipt_id'], 'request_key': 'legacy-saved-draft'}))
        self.assertEqual(saved['data']['draft_id'], replay['data']['draft_id'])
        self.host._sandbox_client = client
        isolated = self.ok(self.http(self.alice, '/api/sources/intake', upload))
        self.assertTrue(isolated['data']['parsing']['isolated'])
        self.assertNotEqual(legacy['data']['receipt_id'], isolated['data']['receipt_id'])
        self.assertEqual(legacy['data']['source']['sha256'], isolated['data']['source']['sha256'])
        self.assertEqual(legacy['data_version'], isolated['data_version'])
