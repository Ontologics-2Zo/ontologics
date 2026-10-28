"""Actual loopback HTTP + PostgreSQL workflow checks; model calls use a test double."""
import copy
import hashlib
from http.cookies import SimpleCookie
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib import error, parse, request

from ontologics.database import DatabaseError
from ontologics.demo import example_queries, fixture_nodes
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.model import ROOT
from ontologics.web import USERS, WebApplication, WebServer, seed_development
from tests.db import database_for_test


class ScriptedQuality:
    """Exercises the real runtime/tool path without representing a live LLM."""
    def __init__(self, drift=False, skip_tools=False, wrong_evidence=False):
        self.calls = []
        self.drift, self.skip_tools = drift, skip_tools
        self.wrong_evidence = wrong_evidence

    def public_config(self):
        return {'provider': 'scripted-test'}

    def __call__(self, *, model, messages):
        self.calls.append({'mode': 'scripted-test'})
        if len(self.calls) == 1 and not self.skip_tools:
            calls = json.loads(messages[-1]['content'])['supplied_calls']
            if self.drift:
                args = calls[0]['arguments']
                key = 'version' if 'version' in args else 'after_version'
                args[key] += 1
            return {'text': '', 'tool_calls': calls}
        observations = []
        for message in messages:
            if message['role'] != 'tool':
                continue
            data = json.loads(message['content'])['data']
            before = {r['unit_id']: r for r in data['before']['results']} if 'before' in data else {}
            after = data['after']['results'] if 'after' in data else data['results']
            mode = data['query']['mode']
            for row in after:
                old = before.get(row['unit_id'])
                observations.append({'mode': mode, 'unit_id': row['unit_id'],
                    'before': None if old is None else {'status': old['status'], 'evidence': old['evidence']},
                    'after': {'status': row['status'], 'evidence': row['evidence']}})
        if self.wrong_evidence and observations:
            next(x for x in observations if x['unit_id'] == 'unit:U106')['after']['evidence'] = ['evidence:demo:initial']
        return {'text': json.dumps({'observations': observations, 'explanation': 'U106은 안전하고 B의 U104는 보완 전부터 포함이다: evidence:demo:initial'}), 'tool_calls': []}


class WebCase(unittest.TestCase):
    """Shared real-HTTP fixture, with no tests inherited twice by other suites."""
    reserve_l1 = False

    def setUp(self):
        self.database = database_for_test(self)
        self.assertTrue(seed_development(self.database, reserve_l1=self.reserve_l1)['ok'])
        self.host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), self.database)
        self.clients = {}
        self.alice = self.start('alice')
        self.bob = self.start('bob')
        self.reader = self.start('reader')
        _, self.operations = fixture_nodes()

    def start(self, user, factory=None):
        app = WebApplication(self.host, DevelopmentContext('dev-' + user),
                             provider_factory=factory, model='scripted-test')
        server = WebServer(app, 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(stop)
        self.clients[user] = server
        status, result, _ = self.http(server, '/api/context')
        self.assertEqual(200, status, result)
        self.assertEqual('dev-' + user, result['data']['identity']['subject'])
        self.assertEqual('development', result['data']['identity']['identity_mode'])
        server.test_token = result['data']['csrf_token']
        return server

    def http(self, server, path, body=None, *, headers=None, raw=None):
        values = {'Origin': server.origin}
        if getattr(server, 'test_cookie', None):
            values['Cookie'] = server.test_cookie
        data = None
        if body is not None or raw is not None:
            values.update({'Content-Type': 'application/json', 'X-CSRF-Token': server.test_token})
            data = raw if raw is not None else json.dumps(body).encode()
        values.update(headers or {})
        req = request.Request(server.origin + path, data=data, headers=values)
        try:
            response = request.urlopen(req, timeout=15)
        except error.HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            value = json.loads(content) if response.headers.get_content_type() == 'application/json' else content
            cookies = SimpleCookie()
            for header in response.headers.get_all('Set-Cookie', []):
                cookies.load(header)
            if cookies:
                server.test_cookie = '; '.join(name + '=' + morsel.value for name, morsel in cookies.items())
            return response.status, value, response.headers

    def ok(self, response):
        status, result, _ = response
        self.assertEqual(200, status, result)
        self.assertTrue(result['ok'], result)
        return result

    def failed(self, response, status, code):
        actual, result, _ = response
        self.assertEqual(status, actual, result)
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)
        return result

    def tool(self, name, body, server=None):
        return self.http(server or self.alice, '/api/tools/' + name, body)

    def draft(self, key='supplement', **kwargs):
        return self.ok(self.tool('prepare_draft', {'operations': copy.deepcopy(self.operations),
                                                  'request_key': key, **kwargs}))['data']

    def review_body(self, draft, decision='approve', key='review'):
        return {'draft_id': draft['draft_id'], 'version': draft['draft_version'], 'decision': decision,
                'note': '시연용 원본의 제거·전체 이력 근거를 확인한 개발 검토', 'request_key': key}

class WebTests(WebCase):
    def test_http_s1_revision_review_apply_and_fixed_condition_comparison(self):
        queries = example_queries()
        initial = {m: self.ok(self.tool('investigate', {'query': q, 'version': 1})) for m, q in queries.items()}
        draft = self.draft()
        replay = self.draft()
        self.assertEqual(draft['draft_id'], replay['draft_id'])
        saved = self.ok(self.tool('get_draft', {'draft_id': draft['draft_id'], 'version': 1}))['data']
        self.assertEqual('user:dev-alice', saved['author'])
        self.assertTrue(self.ok(self.tool('validate_draft', {'draft_id': draft['draft_id'], 'version': 1}))['data']['validation']['valid'])
        for server, code in ((self.alice, 'SELF_APPROVAL_FORBIDDEN'), (self.reader, 'FORBIDDEN')):
            rejected = self.failed(self.http(server, '/api/review', self.review_body(draft)), 403, code)
            self.assertIsNone(rejected['data']['application'])
        changed = self.ok(self.http(self.bob, '/api/review', self.review_body(draft, 'request_changes', 'changes')))
        self.assertEqual('needs_changes', changed['data']['review']['data']['status'])
        revised = self.draft('revision', draft_id=draft['draft_id'], expected_version=1)
        self.assertEqual(2, revised['draft_version'])
        self.failed(self.http(self.bob, '/api/review', self.review_body(draft, key='old')), 409, 'SUPERSEDED_DRAFT')
        body = self.review_body(revised, key='approve-v2')
        approved = self.ok(self.http(self.bob, '/api/review', body))
        self.assertEqual('approved', approved['data']['review']['data']['status'])
        applied = approved['data']['application']
        self.assertEqual('applied', applied['data']['status'])
        self.assertEqual(2, applied['data']['draft_version'])
        after = applied['data_version']
        self.assertEqual(2, after)
        replay = self.ok(self.http(self.bob, '/api/review', body))
        self.assertEqual(after, replay['data']['application']['data_version'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['data']['application']['warnings']])
        for mode, query in queries.items():
            comparison = self.ok(self.tool('compare_investigation', {'query': query, 'before_version': 1, 'after_version': after}))
            data = comparison['data']
            self.assertEqual(query, data['query'])
            self.assertEqual(initial[mode]['data'], data['before'])
            self.assertEqual('needs_confirmation', data['before']['results'][0]['status'])
            self.assertEqual('excluded' if mode == 'A' else 'included', data['after']['results'][0]['status'])
            self.assertEqual('needs_confirmation', data['after']['results'][2]['status'])
            self.assertIn('evidence:demo:supplement', comparison['evidence'])
            self.assertEqual('temporal-0.1', comparison['rule_version'])
        # The removal instant is outside the installation interval, for A and B.
        boundary_a = {**queries['A'], 'at': '2026-09-12T09:00:00+09:00'}
        boundary_b = {**queries['B'], 'start': boundary_a['at'], 'end': '2026-09-13T00:00:00+09:00'}
        for query in (boundary_a, boundary_b):
            self.assertEqual('excluded', self.ok(self.tool('investigate', {'query': query, 'version': after}))['data']['results'][0]['status'])

    def test_apply_failure_preserves_approval_then_same_request_recovers_and_stale_draft_is_blocked(self):
        first, stale = self.draft('first'), self.draft('stale')
        body = self.review_body(first)
        with patch.object(self.host, 'apply_approved', side_effect=DatabaseError('Injected apply connection failure')):
            result = self.failed(self.http(self.bob, '/api/review', body), 503, 'STORAGE_ERROR')
        self.assertEqual('approved', result['data']['review']['data']['status'])
        self.assertFalse(result['data']['application']['ok'])
        current = self.ok(self.http(self.alice, '/api/context'))
        self.assertEqual(1, current['data_version'])
        recovered = self.ok(self.http(self.bob, '/api/review', body))
        self.assertEqual(2, recovered['data']['application']['data_version'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in recovered['data']['review']['warnings']])
        self.failed(self.http(self.bob, '/api/review', self.review_body(stale, key='stale-review')), 409, 'STALE_BASE')
        self.assertEqual(2, self.ok(self.http(self.alice, '/api/context'))['data_version'])

    def test_http_boundary_blocks_identity_authority_cross_origin_and_secret_paths(self):
        for key in ('actor', 'principals', 'author', 'reviewer', 'user_id'):
            self.failed(self.tool('lookup', {key: 'dev-bob'}), 400, 'INVALID_TOOL_ARGUMENTS')
        for name in ('review', 'apply', 'bootstrap', 'run_history'):
            self.failed(self.tool(name, {}), 403, 'TOOL_NOT_ALLOWED')
        self.failed(self.tool('prepare_draft', {'operations': self.operations, 'request_key': 'reader'}, self.reader), 403, 'FORBIDDEN')
        self.failed(self.http(self.bob, '/api/tools/lookup', {}, headers={'Origin': self.alice.origin}), 403, 'ORIGIN_REJECTED')
        self.failed(self.http(self.bob, '/api/tools/lookup', {}, headers={'X-CSRF-Token': 'wrong'}), 403, 'CSRF_REJECTED')
        self.failed(self.http(self.bob, '/api/context', headers={'Host': 'attacker.invalid'}), 403, 'HOST_REJECTED')
        for path in ('/.env.local', '/static/../../.env', '/api/apply', '/api/context?user=bob'):
            self.failed(self.http(self.alice, path), 404, 'ROUTE_NOT_FOUND')
        for raw in (b'{', b'[]', b'{"version":NaN}'):
            self.failed(self.http(self.alice, '/api/tools/lookup', raw=raw), 400, 'INVALID_INPUT')
        self.failed(self.http(self.alice, '/api/tools/lookup', raw=b'{}', headers={'Content-Length': '1000001'}), 413, 'INPUT_TOO_LARGE')
        _, _, headers = self.http(self.alice, '/api/context')
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))

    def test_source_download_keeps_version_origin_locator_and_verifies_bytes(self):
        evidence = self.ok(self.tool('lookup', {'node_id': 'evidence:demo:supplement', 'version': 1}))['data'][0]
        self.assertEqual('synthetic', evidence['origin'])
        self.assertEqual('/supplement', evidence['locator'])
        source = self.ok(self.tool('lookup', {'node_id': evidence['source_id'], 'version': 1}))['data'][0]
        path = '/api/source?' + parse.urlencode({'source_id': source['id'], 'version': 1})
        status, content, headers = self.http(self.alice, path)
        self.assertEqual(200, status)
        self.assertEqual(source['sha256'], hashlib.sha256(content).hexdigest())
        self.assertIn('attachment', headers['Content-Disposition'])
        original_read = Path.read_bytes
        def changed_bytes(file):
            return b'tampered' if file == (ROOT / source['path']).resolve() else original_read(file)
        with patch.object(Path, 'read_bytes', changed_bytes):
            self.failed(self.http(self.alice, path), 400, 'SOURCE_HASH_MISMATCH')

    def test_scripted_agent_calls_same_tools_conditions_versions_and_cannot_drift(self):
        queries = example_queries()
        body = {'queries': queries, 'version': 1, 'question': '미확인 이력과 A/B 결과를 설명해줘'}
        self.failed(self.http(self.alice, '/api/quality', body), 503, 'AI_NOT_CONFIGURED')
        self.alice.app.provider_factory = lambda names: ScriptedQuality()
        explained = self.ok(self.http(self.alice, '/api/quality', body))
        self.assertEqual('matched', explained['data']['consistency'])
        contract = explained['data']['agent_result']
        self.assertEqual('quality', contract['agent_name'])
        self.assertEqual(explained['data']['verified_observations'], contract['payload']['observations'])
        self.assertTrue(contract['evidence_refs'])
        self.assertTrue(all(ref['data_version'] == 1 for ref in contract['evidence_refs']))
        self.assertNotIn('U106은 안전', explained['data']['display_text'])
        self.assertTrue(explained['data']['runtime']['output_withheld'])
        self.assertEqual('', explained['data']['runtime']['text'])
        self.assertNotIn('보완 전부터 포함', explained['data']['runtime']['text'])
        self.assertEqual('scripted-test', explained['data']['runtime']['model'])
        self.assertEqual(2, len(explained['data']['tool_calls']))
        for mode in ('A', 'B'):
            direct = self.ok(self.tool('investigate', {'query': queries[mode], 'version': 1}))
            self.assertEqual(direct['data'], explained['data']['results'][mode]['data'])
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])
        for factory, code in ((lambda names: ScriptedQuality(drift=True), 'AI_CONDITION_MISMATCH'),
                              (lambda names: ScriptedQuality(skip_tools=True), 'AI_REQUIRED_TOOLS_MISSING')):
            self.alice.app.provider_factory = factory
            result = self.failed(self.http(self.alice, '/api/quality', body), 502, code)
            self.assertEqual([], result['data']['runtime']['tool_run_ids'])
            self.assertEqual('not_verified', result['data']['consistency'])
        self.alice.app.provider_factory = lambda names: ScriptedQuality(wrong_evidence=True)
        rejected = self.failed(self.http(self.alice, '/api/quality', body), 502, 'AI_FACT_MISMATCH')
        self.assertEqual('', rejected['data']['runtime']['text'])
        self.assertEqual('not_verified', rejected['data']['consistency'])
        self.assertIsNone(rejected['data']['agent_result']['payload'])
        self.assertEqual('AI_FACT_MISMATCH', rejected['data']['agent_result']['error']['code'])
        class CrossModeComparison(ScriptedQuality):
            def __call__(self, **kwargs):
                response = super().__call__(**kwargs)
                if response['text']:
                    value = json.loads(response['text'])
                    # A/B are separate questions, never before/after phases.
                    for row in value['observations']:
                        if row['mode'] == 'B': row['before'] = copy.deepcopy(row['after'])
                    response['text'] = json.dumps(value)
                return response
        self.alice.app.provider_factory = lambda names: CrossModeComparison()
        rejected = self.failed(self.http(self.alice, '/api/quality', body), 502, 'AI_FACT_MISMATCH')
        self.assertIsNone(rejected['data']['agent_result']['payload'])
        self.alice.app.provider_factory = lambda names: ScriptedQuality()
        draft = self.draft()
        self.ok(self.http(self.bob, '/api/review', self.review_body(draft)))
        compared = self.ok(self.http(self.alice, '/api/quality', {**body, 'version': 2, 'before_version': 1}))
        self.assertEqual('matched', compared['data']['consistency'])
        self.assertTrue(all(call['name'] == 'compare_investigation' for call in compared['data']['tool_calls']))
        self.assertEqual('excluded', compared['data']['results']['A']['data']['after']['results'][0]['status'])
        self.assertEqual('included', compared['data']['results']['B']['data']['after']['results'][0]['status'])

    def test_typed_quality_withholds_success_payload_when_post_model_audit_fails(self):
        from ontologics.web import failure
        self.alice.app.provider_factory = lambda names: ScriptedQuality()
        body = {'queries': example_queries(), 'version': 1, 'question': '같은 A/B 조건을 설명해줘'}
        with patch('ontologics.host.RequestSession.record_agent_check',
                   return_value=failure('STORAGE_ERROR', 'Controlled audit persistence failure')):
            failed = self.failed(self.http(self.alice, '/api/quality', body), 503, 'STORAGE_ERROR')
        typed = failed['data']['agent_result']
        self.assertIsNone(typed['payload'])
        self.assertEqual([], typed['evidence_refs'])
        self.assertFalse(typed['runtime']['ok'])
        self.assertFalse(failed['data']['runtime']['ok'])
        self.assertEqual(typed['runtime'], failed['data']['runtime'])
        self.assertEqual('completed', typed['runtime']['execution_status'])
        self.assertEqual('post_runtime_validation', typed['runtime']['failure_stage'])
        self.assertEqual(failed['errors'][0]['code'], typed['error']['code'])
        self.assertEqual('', failed['data']['display_text'])
        self.assertFalse(typed['read_restart']['checkpoint_available'])


if __name__ == '__main__':
    unittest.main()
