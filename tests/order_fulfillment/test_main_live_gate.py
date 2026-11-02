"""Post-merge live policy blockers must stop before credentials/resources."""
import unittest
from unittest.mock import Mock, patch
from examples.order_fulfillment import b_run_transport as transport, b_notebook, b_run


class MainLiveGateTests(unittest.TestCase):
    def test_readiness_explains_contract_and_cost_blockers(self):
        readiness = transport.live_readiness()
        self.assertFalse(readiness['ready'])
        self.assertIn('AGENT_SPEC_DOMAIN_REVIEW_REQUIRED', readiness['codes'])
        self.assertIn('COST_AUTHORIZATION_REVIEW_REQUIRED', readiness['codes'])

    def test_valid_old_authorization_cannot_read_credentials(self):
        from tests.order_fulfillment.test_b_run_transport import AuthorizationTests
        fixture = AuthorizationTests(); fixture.setUp()
        loader = Mock()
        with self.assertRaisesRegex(transport.Blocked, 'COMMON_CONNECTION_REVIEW_REQUIRED'):
            transport.load_authorized_settings('/never-read', fixture.auth, loader=loader, **fixture.binding)
        loader.assert_not_called()

    def test_valid_old_authorization_cannot_build_backend(self):
        from tests.order_fulfillment.test_b_run_transport import AuthorizationTests
        fixture = AuthorizationTests(); fixture.setUp()
        with patch.object(transport.request, 'build_opener') as opener:
            with self.assertRaises(transport.Blocked):
                transport.AuthorizedBackend(Mock(), fixture.auth, **fixture.binding)
        opener.assert_not_called()

    def test_notebook_valid_confirmation_still_cannot_write_or_launch(self):
        demo = b_notebook.Demo(cases=transport.CASES)
        with patch.object(demo, 'approval_draft') as draft, patch.object(demo, '_start') as start:
            with self.assertRaises(transport.Blocked):
                demo.start_live(demo.approval_phrase())
            with self.assertRaises(transport.Blocked):
                demo.start_live_with_token(demo.approval_token())
        draft.assert_not_called(); start.assert_not_called()
        self.assertFalse(demo.control.exists())

    def test_cli_live_stops_before_reading_authorization_or_starting_database(self):
        import contextlib, io
        with patch.object(b_run, 'read_json') as read, patch.object(b_run, 'prepare') as prepare:
            with contextlib.redirect_stderr(io.StringIO()):
                code = b_run.main(['live', '--output', '/private/tmp/never-created',
                                  '--authorization', '/never-read.json', '--free-gate', '/never-read-gate.json'])
        self.assertEqual(code, 1)
        read.assert_not_called(); prepare.assert_not_called()

    def test_short_token_is_run_bound_and_rejects_empty(self):
        one, two = b_notebook.Demo(), b_notebook.Demo()
        self.assertNotEqual(one.approval_token(), two.approval_token())
        with patch.object(one, 'start_live') as start:
            for wrong in ('', '승인', two.approval_token()):
                with self.assertRaises(PermissionError):
                    one.start_live_with_token(wrong)
        start.assert_not_called()
