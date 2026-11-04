"""Local demonstration HTTP requests must not approve or call models from another site.

Real HTTP handler and socket, fake business case: no database, approval data or model API.
"""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from examples.claim_response import scenario_app as app


class CaseDouble:
    def __init__(self):
        self.calls = []

    def view(self):
        return {'state': 'ready'}

    def approve_reproduction(self):
        self.calls.append('approve_reproduction')

    def confirm_claim(self):
        self.calls.append('confirm_claim')


class ScenarioHttpTests(unittest.TestCase):
    def setUp(self):
        self.case = CaseDouble()
        for active in (patch.dict(app.CURRENT, {'case': self.case, 'live': None}),
                       patch.object(app, 'input_files', return_value=[]),
                       patch.object(app, 'waiting_claims', return_value=[]),
                       patch.object(app, 'arrived_results', return_value=[])):
            active.start()
            self.addCleanup(active.stop)
        self.server = app.ScenarioServer(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()

    def request(self, path='/api/approve_reproduction', *, method='POST', body=b'{}', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        supplied = {'Origin': self.server.origin, 'Content-Type': 'application/json',
                    'X-CSRF-Token': self.server.csrf_token}
        supplied.update(headers or {})
        try:
            connection.request(method, path, body if method == 'POST' else None, supplied)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw)
        finally:
            connection.close()

    def test_same_origin_browser_can_read_token_and_approve(self):
        status, view = self.request('/api/case', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(view['csrf_token'], self.server.csrf_token)
        self.assertGreaterEqual(len(view['csrf_token']), 32)
        status, result = self.request(headers={'X-CSRF-Token': view['csrf_token']})
        self.assertEqual((status, result['state']), (200, 'ready'))
        self.assertEqual(self.case.calls, ['approve_reproduction'])

    def test_cross_site_and_rebound_host_cannot_read_or_mutate(self):
        for method, path in [('GET', '/api/case'), ('POST', '/api/approve_reproduction'),
                             ('POST', '/api/confirm_claim')]:
            for headers, code in [({'Origin': 'https://unrelated.example'}, 'ORIGIN_REJECTED'),
                                  ({'Origin': 'null'}, 'ORIGIN_REJECTED'),
                                  ({'Sec-Fetch-Site': 'cross-site'}, 'ORIGIN_REJECTED'),
                                  ({'Host': 'unrelated.example'}, 'HOST_REJECTED')]:
                with self.subTest(method=method, path=path, headers=headers):
                    status, result = self.request(path, method=method, headers=headers)
                    self.assertEqual((status, result['error']), (403, code))
        self.assertEqual(self.case.calls, [])

    def test_approval_and_model_action_require_csrf_and_json(self):
        for path in ['/api/approve_reproduction', '/api/confirm_claim']:
            for headers, expected_status, code in [
                    ({'X-CSRF-Token': ''}, 403, 'CSRF_REJECTED'),
                    ({'X-CSRF-Token': 'wrong'}, 403, 'CSRF_REJECTED'),
                    ({'Content-Type': 'text/plain'}, 400, 'JSON_REQUIRED'),
                    ({'Content-Type': 'application/x-www-form-urlencoded'}, 400, 'JSON_REQUIRED'),
                    ({'Transfer-Encoding': 'chunked'}, 400, 'JSON_REQUIRED')]:
                with self.subTest(path=path, headers=headers):
                    status, result = self.request(path, headers=headers)
                    self.assertEqual((status, result['error']), (expected_status, code))
        self.assertEqual(self.case.calls, [])

    def test_invalid_json_or_length_is_rejected_before_action(self):
        for body in [b'[]', b'null', b'{', b'{"number": NaN}', b'\xff']:
            with self.subTest(body=body):
                status, _ = self.request(body=body)
                self.assertEqual(status, 400)
        for size, expected in [('invalid', 400), ('-1', 413), (str(app.MAX_UPLOAD * 2 + 1), 413)]:
            with self.subTest(size=size):
                status, _ = self.request(headers={'Content-Length': size})
                self.assertEqual(status, expected)
        self.assertEqual(self.case.calls, [])


if __name__ == '__main__':
    unittest.main()
