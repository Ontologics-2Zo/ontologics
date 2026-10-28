"""Free-only adversarial tests for the example transport boundary."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib import error, request

from examples.order_fulfillment import b_run_transport as transport


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.case = transport.CASES[0]
        self.ledger = transport.Ledger(self.out / 'ledger.jsonl')
        self.ledger.begin(self.case)
        self.req = request.Request(transport.ENDPOINT, method='POST', data=json.dumps(dict(
            model=transport.MODEL, max_output_tokens=1024, reasoning={'effort': 'none'}, store=False)).encode())
        self.body = dict(status='completed', output=[{'type': 'message'}], usage={'total_tokens': 3})
        self.hash = 'a' * 64

    def backend(self, req, *, timeout):
        self.assertEqual(timeout, 30)
        self.assertEqual(self.ledger.rows()[-1]['event'], 'reserved')
        self.assertTrue((self.out / (self.case + '_http1_request.json')).is_file())
        return transport.Response(json.dumps(self.body).encode())

    def run_request(self, backend=None, **kwargs):
        wrapper = transport.Transport(self.ledger, self.case, self.out, backend or self.backend, **kwargs)
        wrapper(self.req)
        return wrapper

    def review(self):
        return dict(case=self.case, passed=True, kind='separate_korean_semantic_review',
                    report_sha256=self.hash, bound_output_hash=self.hash, notes='의미와 근거를 별도로 확인했다.')

    def test_reservation_and_request_exist_before_single_send(self):
        wrapper = self.run_request()
        self.assertEqual(wrapper.sent, 1)
        rows = self.ledger.rows()
        self.assertTrue(rows[1]['durable_before_http'])
        self.assertEqual(rows[2]['usage']['total_tokens'], 3)
        self.assertEqual(json.loads((self.out / (self.case + '_http1_response.raw.json')).read_text()), self.body)

    def test_budget_caps_and_pending_request_deny_sends(self):
        for number in range(1, 5):
            self.assertEqual(self.ledger.reserve(self.case, self.hash), number)
            with self.assertRaises(transport.Blocked):
                self.ledger.reserve(self.case, self.hash)
            self.ledger.finish(self.case, number, self.body)
        with self.assertRaises(transport.Blocked):
            self.ledger.reserve(self.case, self.hash)
        with self.assertRaises(transport.Blocked):
            transport.Ledger(self.out / 'invalid', total_cap=41)

    def test_total_cap_and_cq01_combined_cap(self):
        ledger = transport.Ledger(self.out / 'small', cases=transport.CASES[:2], total_cap=1)
        ledger.begin(self.case)
        ledger.reserve(self.case, self.hash)
        ledger.finish(self.case, 1, self.body)
        ledger.accept(self.case, True, self.review())
        ledger.begin(transport.CASES[1])
        with self.assertRaises(transport.Blocked):
            ledger.reserve(transport.CASES[1], self.hash)
        ledger = transport.Ledger(self.out / 'cq01', cases=transport.CASES[:2])
        for case in transport.CASES[:2]:
            ledger.begin(case)
            for number in range(1, 5):
                ledger.reserve(case, self.hash)
                ledger.finish(case, number, self.body)
            review = dict(self.review(), case=case)
            ledger.accept(case, True, review)
        self.assertEqual(sum(row['event'] == 'reserved' for row in ledger.rows()), 8)

    def test_restart_and_out_of_order_fail_closed(self):
        with self.assertRaises(transport.Blocked):
            self.ledger.begin(transport.CASES[1])
        restarted = transport.Ledger(self.ledger.path)
        for action in (lambda: restarted.begin(self.case), lambda: restarted.reserve(self.case, self.hash)):
            with self.assertRaises(transport.Blocked):
                action()

    def test_accept_requires_completed_http_and_separate_review(self):
        with self.assertRaises(transport.Blocked):
            self.ledger.accept(self.case, True, self.review())
        self.assertEqual(self.ledger.rows()[-1]['event'], 'stopped')

    def test_strict_ledger_rejects_offline_scaffold_acceptance(self):
        self.run_request()
        review = dict(case=self.case, passed=True, kind='offline_wiring_scaffold_only',
                      offline_simulated_review=True, bound_output_hash=self.hash)
        with self.assertRaises(transport.Blocked):
            self.ledger.accept(self.case, True, review)
        self.assertEqual(self.ledger.rows()[-1]['event'], 'stopped')

    def test_offline_ledger_accepts_explicit_simulated_record(self):
        ledger = transport.Ledger(self.out / 'offline.jsonl', offline=True)
        ledger.begin(self.case)
        number = ledger.reserve(self.case, self.hash)
        ledger.finish(self.case, number, self.body)
        review = dict(case=self.case, passed=True, kind='offline_wiring_scaffold_only',
                      offline_simulated_review=True, bound_output_hash=self.hash)
        ledger.accept(self.case, True, review)
        accepted = ledger.rows()[-1]
        self.assertEqual(accepted['event'], 'accepted')
        self.assertEqual(accepted['semantic_review'], review)
        self.assertTrue(accepted['semantic_review']['offline_simulated_review'])

    def test_completed_acceptance_advances_exactly_once(self):
        self.run_request()
        self.ledger.accept(self.case, True, self.review())
        self.ledger.begin(transport.CASES[1])
        with self.assertRaises(transport.Blocked):
            self.ledger.begin(transport.CASES[1])

    def test_wire_rejected_before_send(self):
        for mutation in ('model', 'endpoint', 'timeout'):
            with self.subTest(mutation=mutation):
                ledger = transport.Ledger(self.out / mutation)
                ledger.begin(self.case)
                backend = mock.Mock()
                wrapper = transport.Transport(ledger, self.case, self.out, backend)
                req = self.req
                timeout = 30
                if mutation == 'model':
                    req = request.Request(transport.ENDPOINT, data=b'{"model":"other"}', method='POST')
                elif mutation == 'endpoint':
                    req = request.Request('https://example.invalid', data=self.req.data, method='POST')
                else:
                    timeout = 31
                with self.assertRaises(transport.Blocked):
                    wrapper(req, timeout=timeout)
                backend.assert_not_called()
                self.assertEqual(ledger.rows()[-1]['event'], 'stopped')

    def test_timeout_is_one_attempt_and_durable_stop(self):
        backend = mock.Mock(side_effect=TimeoutError('fake timeout'))
        wrapper = transport.Transport(self.ledger, self.case, self.out, backend)
        with self.assertRaises(TimeoutError):
            wrapper(self.req)
        with self.assertRaises(transport.Blocked):
            wrapper(self.req)
        self.assertEqual(backend.call_count, 1)
        self.assertEqual(wrapper.sent, 1)
        self.assertEqual(self.ledger.rows()[-1]['event'], 'stopped')

    def test_non200_incomplete_empty_and_invalid_json_stop(self):
        for index, (status, body) in enumerate([(503, self.body), (200, {'status': 'incomplete', 'output': [1]}),
                (200, {'status': 'completed', 'output': []}), (200, []), (200, b'invalid')]):
            with self.subTest(index=index):
                out = self.out / str(index)
                out.mkdir()
                ledger = transport.Ledger(out / 'ledger')
                ledger.begin(self.case)
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                wrapper = transport.Transport(ledger, self.case, out, lambda *a, **k: transport.Response(raw, status=status))
                with self.assertRaises((transport.Blocked, ValueError)):
                    wrapper(self.req)
                self.assertEqual(wrapper.sent, 1)
                self.assertEqual(ledger.rows()[-1]['event'], 'stopped')
                self.assertTrue((out / (self.case + '_http1_response.raw.json')).exists())

    def test_http_error_body_and_usage_preserved_without_retry(self):
        raw = json.dumps({'usage': {'total_tokens': 7}}).encode()
        backend = mock.Mock(side_effect=error.HTTPError(transport.ENDPOINT, 429, 'fake', {}, io.BytesIO(raw)))
        with self.assertRaises(error.HTTPError):
            self.run_request(backend)
        self.assertEqual(backend.call_count, 1)
        self.assertEqual(self.ledger.rows()[-1]['event'], 'stopped')
        self.assertEqual((self.out / (self.case + '_http1_http_error.raw.json')).read_bytes(), raw)
        self.assertEqual(json.loads((self.out / (self.case + '_http1_error.json')).read_text())['usage'], {'total_tokens': 7})

    def test_exact_current_bearer_echo_is_redacted_and_stops_success_and_error(self):
        fake_key = 'fake-key-echo-offline-only'
        req = request.Request(transport.ENDPOINT, data=self.req.data, method='POST',
                              headers={'Authorization': 'Bearer ' + fake_key})
        for mode in ('success', 'http_error'):
            with self.subTest(mode=mode):
                out = self.out / mode
                out.mkdir()
                ledger = transport.Ledger(out / 'ledger')
                ledger.begin(self.case)
                raw = json.dumps(dict(status='completed', output=[{'text': fake_key}])).encode()
                backend = mock.Mock(return_value=transport.Response(raw))
                if mode == 'http_error':
                    backend.side_effect = error.HTTPError(transport.ENDPOINT, 401, 'fake', {}, io.BytesIO(raw))
                wrapper = transport.Transport(ledger, self.case, out, backend)
                with self.assertRaises(transport.Blocked) as caught:
                    wrapper(req)
                self.assertNotIn(fake_key, str(caught.exception))
                with self.assertRaises(transport.Blocked):
                    wrapper(req)
                self.assertEqual(backend.call_count, 1)
                self.assertEqual(wrapper.sent, 1)
                self.assertEqual(ledger.rows()[-1]['event'], 'stopped')
                suffix = '_response.raw.json' if mode == 'success' else '_http_error.raw.json'
                saved_path = out / (self.case + '_http1' + suffix)
                saved = saved_path.read_bytes()
                self.assertIn(b'[REDACTED_CURRENT_BEARER]', saved)
                preservation = json.loads(Path(str(saved_path) + '.preservation.json').read_text())
                self.assertEqual(preservation['original_sha256'], hashlib.sha256(raw).hexdigest())
                self.assertTrue(preservation['credential_echo_redacted'])
                for path in out.iterdir():
                    self.assertNotIn(fake_key.encode(), path.read_bytes(), str(path))

    def test_live_requires_approval_and_hard_wall_interrupts(self):
        with self.assertRaises(transport.Blocked):
            transport.Transport(self.ledger, self.case, self.out, self.backend, offline=False)
        with self.assertRaises(TimeoutError):
            with transport._hard_deadline(.01):
                time.sleep(.1)

    def test_hard_wall_includes_http_error_body_read(self):
        class SlowErrorBody:
            def read(self, size):
                time.sleep(.2)
                return b'{}'
            def close(self):
                pass
        backend = mock.Mock(side_effect=error.HTTPError(transport.ENDPOINT, 503, 'fake', {}, SlowErrorBody()))
        wrapper = transport.Transport(self.ledger, self.case, self.out, backend, offline=False, paid_authorized=True)
        deadline = transport._hard_deadline
        with mock.patch.object(transport, '_hard_deadline', side_effect=lambda: deadline(.01)):
            with self.assertRaises(TimeoutError):
                wrapper(self.req)
        self.assertEqual(backend.call_count, 1)
        self.assertEqual(wrapper.sent, 1)
        self.assertEqual(self.ledger.rows()[-1]['event'], 'stopped')

    def test_request_preservation_failure_stops_before_backend(self):
        backend = mock.Mock()
        wrapper = transport.Transport(self.ledger, self.case, self.out, backend)
        with mock.patch.object(transport, '_write', side_effect=OSError('fake disk error')):
            with self.assertRaises(OSError):
                wrapper(self.req)
        backend.assert_not_called()
        self.assertEqual(wrapper.sent, 0)
        self.assertEqual(self.ledger.rows()[-1]['event'], 'stopped')

    def test_exact_report_review_binding(self):
        self.run_request()
        report = dict(case=self.case, passed=True, checks={'scope': True},
                      runtime={'status': 'completed', 'text': '결과', 'output_hash': self.hash})
        report_path = self.out / 'report.json'
        raw = json.dumps(report, ensure_ascii=False).encode()
        report_path.write_bytes(raw)
        review = dict(self.review(), report_sha256=hashlib.sha256(raw).hexdigest())
        review_path = self.out / 'review.json'
        review_path.write_text(json.dumps(review, ensure_ascii=False))
        transport.accept_after_review(self.ledger, self.case, report_path, review_path)
        self.assertEqual(self.ledger.rows()[-1]['event'], 'accepted')

    def test_tampered_review_or_report_cannot_advance(self):
        for field in ('report_sha256', 'bound_output_hash', 'notes', 'kind'):
            with self.subTest(field=field):
                out = self.out / field
                out.mkdir()
                ledger = transport.Ledger(out / 'ledger')
                ledger.begin(self.case)
                number = ledger.reserve(self.case, self.hash)
                ledger.finish(self.case, number, self.body)
                report = dict(case=self.case, passed=True, checks={'scope': True},
                              runtime={'status': 'completed', 'text': '결과', 'output_hash': self.hash})
                raw = json.dumps(report).encode()
                (out / 'report').write_bytes(raw)
                review = dict(self.review(), report_sha256=hashlib.sha256(raw).hexdigest())
                review[field] = 'changed'
                (out / 'review').write_text(json.dumps(review))
                with self.assertRaises(transport.Blocked):
                    transport.accept_after_review(ledger, self.case, out / 'report', out / 'review')
                self.assertEqual(ledger.rows()[-1]['event'], 'stopped')


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.binding = dict(run_id='new-run-123', cases=transport.CASES, free_gate_sha256='a' * 64, source_sha256='b' * 64)
        self.auth = dict(approved=True, source='explicit_user_confirmation_with_mandatory_free_gate',
                        per_case_request_cap=4, total_request_cap=40, cq01_request_cap=8,
                        max_output_tokens=1024, timeout_seconds=30, automatic_retries=0,
                        model=transport.MODEL, endpoint=transport.ENDPOINT,
                        input_and_money_caps_unfixed_acknowledged=True, **self.binding)

    def test_invalid_or_stale_authorization_never_calls_loader(self):
        mutations = [dict(self.auth, run_id='old-run'), dict(self.auth, cases=transport.CASES[2:]),
                     dict(self.auth, free_gate_sha256='c' * 64), dict(self.auth, source_sha256='d' * 64),
                     dict(self.auth, automatic_retries=1), dict(self.auth, unknown_budget=5),
                     dict(self.auth, approved=False), {}, dict(self.auth, total_request_cap=41)]
        for auth in mutations:
            with self.subTest(auth=auth):
                loader = mock.Mock()
                with self.assertRaises(transport.Blocked):
                    transport.load_authorized_settings('/unused', auth, loader=loader, **self.binding)
                loader.assert_not_called()

    def test_fake_loader_receives_only_fixed_env_file_and_retains_only_key(self):
        loader = mock.Mock(return_value={'OPENAI_API_KEY': 'fake-key-for-offline-test', 'DATABASE_URL': 'ignored', 'OPENAI_MODEL': 'ignored'})
        settings = transport.load_authorized_settings('/unused', self.auth, loader=loader, **self.binding)
        loader.assert_called_once_with(env_file=Path('/unused/.env.local'), environ={})
        self.assertEqual(settings.api_key, 'fake-key-for-offline-test')
        self.assertEqual(settings.model, transport.MODEL)
        self.assertEqual(settings.timeout_seconds, 30)
        self.assertEqual(settings.max_output_tokens, 1024)
        self.assertFalse(hasattr(settings, 'database_url'))

    def test_no_proxy_inheritance_redirect_or_retry(self):
        opener = mock.Mock()
        with mock.patch.object(transport.request, 'build_opener', return_value=SimpleNamespace(open=opener)) as build:
            settings = SimpleNamespace(model=transport.MODEL, timeout_seconds=30, max_output_tokens=1024, api_key='fake')
            backend = transport.AuthorizedBackend(settings, self.auth, **self.binding)
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsNone(handlers[1].redirect_request(None, None, None, None, None, None))
        req = request.Request(transport.ENDPOINT, data=b'{}', headers={'Authorization': 'Bearer fake'}, method='POST')
        opener.side_effect = error.URLError('fake')
        with self.assertRaises(error.URLError):
            backend(req)
        self.assertEqual(opener.call_count, 1)
        with self.assertRaises(transport.Blocked):
            backend(request.Request('https://example.invalid', data=b'{}'))
        self.assertEqual(opener.call_count, 1)

    def test_auth_does_not_permit_bool_or_float_budget_fields(self):
        for value in (False, 0.0):
            with self.assertRaises(transport.Blocked):
                transport.validate_authorization(dict(self.auth, automatic_retries=value), **self.binding)


if __name__ == '__main__':
    unittest.main()
