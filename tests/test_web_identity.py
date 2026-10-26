"""Development browser selection is session-scoped, never real authentication."""
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from types import SimpleNamespace
import threading
from unittest.mock import patch

from ontologics.identity import DevelopmentContext
from ontologics.web import WebApplication, WebServer
from tests.test_web import WebCase


class WebIdentityTests(WebCase):
    def browser(self):
        client = SimpleNamespace(origin=self.alice.origin)
        response = self.http(client, '/api/context')
        context = self.ok(response)['data']
        client.test_token = context['csrf_token']
        return client, context, response[2]

    def switch(self, client, user):
        self.ok(self.http(client, '/api/development-user', {'user': user}))
        context = self.ok(self.http(client, '/api/context'))['data']
        client.test_token = context['csrf_token']
        self.assertEqual('dev-' + user, context['identity']['subject'])
        self.assertEqual('development', context['identity']['identity_mode'])
        return context

    def test_one_browser_can_review_as_bob_but_cannot_self_approve_or_write_as_reader(self):
        draft = self.draft()
        self.failed(self.http(self.alice, '/api/review', self.review_body(draft)), 403, 'SELF_APPROVAL_FORBIDDEN')
        original_cookie, original_token = self.alice.test_cookie, self.alice.test_token
        bob = self.switch(self.alice, 'bob')
        self.assertNotEqual(original_cookie, self.alice.test_cookie)
        self.assertNotEqual(original_token, self.alice.test_token)
        self.assertIn('review', bob['identity']['permissions'])
        self.assertNotIn('draft', bob['identity']['permissions'])
        applied = self.ok(self.http(self.alice, '/api/review', self.review_body(draft)))
        self.assertEqual('user:dev-bob', applied['data']['review']['data']['reviewer'])
        self.assertEqual(draft['draft_version'], applied['data']['application']['data']['draft_version'])
        saved = self.ok(self.tool('get_draft', {'draft_id': draft['draft_id'], 'version': draft['draft_version']}))['data']
        self.assertEqual('user:dev-alice', saved['author'])
        self.assertEqual('applied', saved['status'])
        reader = self.switch(self.alice, 'reader')
        self.assertEqual(['read'], reader['identity']['permissions'])
        self.failed(self.tool('prepare_draft', {'operations': self.operations, 'request_key': 'reader-write'}), 403, 'FORBIDDEN')
        self.failed(self.http(self.alice, '/api/review', self.review_body(draft, key='reader-review')), 403, 'FORBIDDEN')

    def test_browser_sessions_are_isolated_and_rotated_credentials_fail_closed(self):
        first, context, headers = self.browser()
        second, other, _ = self.browser()
        self.assertEqual('dev-alice', context['identity']['subject'])
        self.assertIsInstance(context['development_users'], list)
        self.assertEqual(3, len(context['development_users']))
        self.assertNotEqual(first.test_cookie, second.test_cookie)
        self.assertNotEqual(first.test_token, second.test_token)
        cookie = SimpleCookie(headers['Set-Cookie'])
        name = 'ontologics_dev_' + first.origin.rsplit(':', 1)[1]
        self.assertIn(name, cookie)
        self.assertTrue(cookie[name]['httponly'])
        self.assertEqual('strict', cookie[name]['samesite'].lower())
        self.assertEqual('/', cookie[name]['path'])
        self.assertNotIn('alice', cookie[name].value)
        old = SimpleNamespace(origin=first.origin, test_cookie=first.test_cookie, test_token=first.test_token)
        self.switch(first, 'bob')
        unchanged = self.ok(self.http(second, '/api/context'))['data']
        self.assertEqual('dev-alice', unchanged['identity']['subject'])
        self.assertEqual(other['csrf_token'], unchanged['csrf_token'])
        self.assertEqual('dev-alice', self.alice.app.context.subject)
        self.failed(self.http(first, '/api/tools/lookup', {}, headers={'X-CSRF-Token': old.test_token}),
                    403, 'CSRF_REJECTED')
        self.failed(self.http(old, '/api/tools/lookup', {}), 403, 'DEVELOPMENT_SESSION_EXPIRED')
        self.failed(self.http(old, '/api/workspace'), 403, 'DEVELOPMENT_SESSION_EXPIRED')
        recovered = self.ok(self.http(old, '/api/context'))['data']
        self.assertEqual('dev-alice', recovered['identity']['subject'])
        self.assertNotEqual(context['csrf_token'], recovered['csrf_token'])
        # Selection cannot manufacture permissions or bypass the browser boundary.
        self.failed(self.http(first, '/api/development-user', {'user': 'alice', 'permissions': ['apply']}),
                    400, 'INVALID_INPUT')
        self.failed(self.http(first, '/api/development-user', {'user': 'service-admin'}),
                    403, 'UNKNOWN_IDENTITY')
        self.failed(self.http(first, '/api/development-user', {'user': 'alice'},
                    headers={'Origin': 'http://attacker.invalid'}), 403, 'ORIGIN_REJECTED')
        self.failed(self.http(first, '/api/development-user', {'user': 'alice'},
                    headers={'X-CSRF-Token': 'wrong'}), 403, 'CSRF_REJECTED')
        # A future non-development identity adapter must not expose this selector.
        with patch.object(self.host._identities, 'mode', 'test-other-identity-adapter'):
            app = WebApplication(self.host, DevelopmentContext('dev-alice'))
            server = WebServer(app, 0)
            self.assertIsNone(server.sessions)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
            thread.start()
            client = SimpleNamespace(origin=server.origin, test_token=app.csrf_token)
            try:
                self.failed(self.http(client, '/api/development-user', {'user': 'bob'}),
                            403, 'DEVELOPMENT_IDENTITY_DISABLED')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_inflight_draft_keeps_alice_identity_when_same_browser_switches_to_bob(self):
        entered, release = threading.Event(), threading.Event()
        original_dispatch = WebApplication.dispatch
        def paused_dispatch(app, path, body=None):
            if path == '/api/tools/prepare_draft' and body.get('request_key') == 'inflight-alice':
                entered.set()
                if not release.wait(timeout=10):
                    raise TimeoutError('Test did not release the in-flight request')
            return original_dispatch(app, path, body)
        with patch.object(WebApplication, 'dispatch', paused_dispatch), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.tool, 'prepare_draft',
                                  {'operations': self.operations, 'request_key': 'inflight-alice'})
            try:
                self.assertTrue(entered.wait(timeout=5), 'HTTP request did not reach the host boundary')
                self.switch(self.alice, 'bob')
            finally:
                release.set()
            draft = self.ok(pending.result(timeout=15))['data']
        saved = self.ok(self.tool('get_draft', {'draft_id': draft['draft_id'], 'version': draft['draft_version']}))['data']
        self.assertEqual('user:dev-alice', saved['author'])
        self.assertEqual('awaiting_review', saved['status'])
        self.assertEqual('dev-bob', self.ok(self.http(self.alice, '/api/context'))['data']['identity']['subject'])
