"""SMTP submission boundary: no real network, credentials or mail delivery."""
import importlib
import importlib.util
import io
import smtplib
import ssl
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ontologics.harness import ExecutionBudget
from ontologics.service import DomainError


class SMTPBoundary:
    def __init__(self, *, fail=None, stage='sendmail', response=None, slow=False):
        self.fail, self.stage, self.response, self.slow = fail, stage, response, slow
        self.calls = []
        self.released = threading.Event()
        self.sock = SimpleNamespace(shutdown=lambda how: self.released.set(),
                                    settimeout=lambda value: self.calls.append(('timeout', value)))

    def _step(self, name, *args):
        self.calls.append((name, *args))
        if name == self.stage and self.fail is not None:
            raise self.fail

    def connect(self, host, port):
        self._step('connect', host, port)
        return 220, b'ready'

    def ehlo(self):
        self._step('ehlo')
        return 250, b'ready'

    def starttls(self, *, context):
        self._step('starttls', context)
        return 220, b'ready'

    def login(self, username, password):
        self._step('login', username, password)

    def sendmail(self, sender, recipients, raw):
        self._step('sendmail', sender, recipients, raw)
        if self.slow:
            if not self.released.wait(1):
                raise AssertionError('Whole-call timer did not interrupt the SMTP socket')
            raise TimeoutError('private server text')
        return {} if self.response is None else self.response

    def close(self):
        self._step('close')


class RFQMailTransportTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(host='smtp.example.org', port=587, tls='starttls',
                           username='configured-user', password='private-password')
        self.raw = b'From: a@example.org\r\nTo: b@example.org\r\n\r\nexact MIME\r\n'
        self.session = SimpleNamespace(_budget=ExecutionBudget(),
                                       _service=SimpleNamespace(now=lambda: '2026-11-04T12:00:00Z'))

    def submit(self, boundary, *, config=None):
        name = 'ontologics.materials.rfq_mail_transport'
        self.assertIsNotNone(importlib.util.find_spec(name), 'SMTP submission boundary is not implemented')
        module = importlib.import_module(name)
        with patch.object(module.smtplib, 'SMTP', return_value=boundary) as smtp, \
                patch.object(module.smtplib, 'SMTP_SSL', return_value=boundary) as secure:
            result = module.submit(self.session, self.config if config is None else config,
                                   sender='a@example.org', recipient='b@example.org', raw=self.raw)
        return result, smtp, secure

    def test_starttls_authentication_preserves_exact_bytes_and_accepted_time(self):
        boundary = SMTPBoundary()
        result, smtp, secure = self.submit(boundary)
        self.assertEqual({'status':'smtp_accepted', 'accepted_at':'2026-11-04T12:00:00Z'}, result)
        smtp.assert_called_once()
        secure.assert_not_called()
        self.assertGreater(smtp.call_args.kwargs['timeout'], 0)
        self.assertLessEqual(smtp.call_args.kwargs['timeout'], 8)
        self.assertEqual('smtp.example.org', getattr(boundary, '_host', None))
        calls = [row[0] for row in boundary.calls if row[0] != 'timeout']
        self.assertEqual(['connect','ehlo','starttls','ehlo','login','sendmail','close'], calls)
        context = next(row[1] for row in boundary.calls if row[0] == 'starttls')
        self.assertTrue(context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertEqual(('sendmail','a@example.org',['b@example.org'],self.raw),
                         next(row for row in boundary.calls if row[0] == 'sendmail'))
        self.assertNotIn('private-password', repr(result))

    def test_implicit_tls_and_optional_relay_credentials(self):
        config = dict(self.config, tls='ssl', port=465, username='', password='')
        boundary = SMTPBoundary()
        result, smtp, secure = self.submit(boundary, config=config)
        self.assertEqual('smtp_accepted', result['status'])
        smtp.assert_not_called()
        secure.assert_called_once()
        self.assertTrue(secure.call_args.kwargs['context'].check_hostname)
        self.assertEqual('smtp.example.org', getattr(boundary, '_host', None))
        self.assertNotIn('starttls', [row[0] for row in boundary.calls])
        self.assertNotIn('login', [row[0] for row in boundary.calls])

    def test_explicit_smtp_rejection_has_no_accepted_time_and_never_retries(self):
        errors = [smtplib.SMTPSenderRefused(550,b'private server text','a@example.org'),
                  smtplib.SMTPRecipientsRefused({'b@example.org':(550,b'private server text')}),
                  smtplib.SMTPDataError(554,b'private server text')]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                boundary = SMTPBoundary(fail=error)
                result, _, _ = self.submit(boundary)
                self.assertEqual('rejected', result['status'])
                self.assertNotIn('accepted_at', result)
                self.assertNotIn('private server text', repr(result))
                self.assertEqual(1, sum(row[0] == 'sendmail' for row in boundary.calls))

    def test_pre_data_connection_tls_and_auth_failures_are_definite_rejections(self):
        for stage, error in [('connect', TimeoutError('private server text')),
                             ('starttls', smtplib.SMTPNotSupportedError('private server text')),
                             ('login', smtplib.SMTPAuthenticationError(535,b'private server text'))]:
            with self.subTest(stage=stage):
                boundary = SMTPBoundary(stage=stage, fail=error)
                result, _, _ = self.submit(boundary)
                self.assertEqual('rejected', result['status'])
                self.assertNotIn('sendmail', [row[0] for row in boundary.calls])
                self.assertNotIn('private server text', repr(result))

    def test_disconnect_or_timeout_during_submission_is_unknown_without_retry(self):
        for error in (TimeoutError('private server text'),
                      smtplib.SMTPServerDisconnected('private server text')):
            with self.subTest(error=type(error).__name__):
                boundary = SMTPBoundary(fail=error)
                result, _, _ = self.submit(boundary)
                self.assertEqual('unknown', result['status'])
                self.assertNotIn('accepted_at', result)
                self.assertEqual(1, sum(row[0] == 'sendmail' for row in boundary.calls))
                self.assertNotIn('private server text', repr(result))

    def test_cleanup_failure_cannot_downgrade_known_acceptance(self):
        result, _, _ = self.submit(SMTPBoundary(stage='close', fail=OSError('private cleanup text')))
        self.assertEqual('smtp_accepted', result['status'])
        self.assertIn('accepted_at', result)

    def test_whole_call_deadline_interrupts_submission_and_reports_unknown(self):
        self.session._budget = SimpleNamespace(consume=Mock(), check=Mock(), remaining_seconds=lambda:0.04)
        boundary = SMTPBoundary(slow=True)
        started = time.monotonic()
        result, _, _ = self.submit(boundary)
        self.assertEqual('unknown', result['status'])
        self.assertTrue(boundary.released.is_set())
        self.assertLess(time.monotonic() - started, 0.8)

    def test_cancelled_common_budget_prevents_any_network(self):
        self.session._budget.cancel()
        boundary = SMTPBoundary()
        with self.assertRaises(DomainError) as caught:
            self.submit(boundary)
        self.assertEqual('EXECUTION_CANCELLED', caught.exception.code)
        self.assertEqual([], boundary.calls)

    def test_insecure_or_incomplete_configuration_is_rejected_before_network(self):
        for config in [dict(self.config,tls='none'),dict(self.config,port=0),
                       dict(self.config,password=''),dict(self.config,host='bad\nhost')]:
            with self.subTest(config_keys=list(config)):
                boundary = SMTPBoundary()
                result, smtp, secure = self.submit(boundary, config=config)
                self.assertEqual({'status':'rejected','code':'SMTP_CONFIG_INVALID'}, result)
                smtp.assert_not_called(); secure.assert_not_called()

    def test_test_domain_recipient_never_reaches_smtp(self):
        name = 'ontologics.materials.rfq_mail_transport'
        self.assertIsNotNone(importlib.util.find_spec(name), 'SMTP submission boundary is not implemented')
        module = importlib.import_module(name)
        with patch.object(module.smtplib, 'SMTP') as smtp, patch.object(module.smtplib, 'SMTP_SSL') as secure:
            result = module.submit(self.session,self.config,sender='a@example.org',
                                   recipient='purchasing@example.test',raw=self.raw)
        self.assertEqual({'status':'rejected','code':'SMTP_TEST_RECIPIENT'},result)
        smtp.assert_not_called(); secure.assert_not_called()

    def test_real_smtplib_uses_configured_tls_hostname_and_preserved_mime(self):
        from ontologics.materials import rfq_mail_transport as transport
        for tls in ('ssl','starttls'):
            with self.subTest(tls=tls):
                greeting = b'220 smtp.example.org\r\n250 smtp.example.org\r\n'
                if tls == 'starttls':
                    greeting = (b'220 smtp.example.org\r\n250-smtp.example.org\r\n250 STARTTLS\r\n'
                                b'220 ready for TLS\r\n250 smtp.example.org\r\n')
                replies = io.BytesIO(greeting+b'250 sender accepted\r\n250 recipient accepted\r\n'
                                    b'354 send data\r\n250 queued\r\n')
                sent = []
                sock = SimpleNamespace(makefile=lambda mode: replies, sendall=sent.append,
                                       settimeout=lambda value: None, shutdown=lambda how: None,
                                       close=lambda: None)
                context = SimpleNamespace(wrap_socket=Mock(return_value=sock))
                config = dict(self.config,tls=tls,username='',password='')
                with patch.object(transport.socket,'create_connection',return_value=sock), \
                        patch.object(transport.ssl,'create_default_context',return_value=context):
                    result = transport.submit(self.session,config,sender='a@example.org',
                                              recipient='b@example.org',raw=self.raw)
                self.assertEqual('smtp_accepted',result['status'])
                context.wrap_socket.assert_called_once_with(sock,server_hostname='smtp.example.org')
                self.assertIn(self.raw+b'.\r\n',sent)
                self.assertEqual(1,sum(data.lower() == b'data\r\n' for data in sent))


if __name__ == '__main__':
    unittest.main()
