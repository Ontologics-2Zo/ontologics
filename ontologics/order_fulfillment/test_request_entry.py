"""Run: python -m unittest ontologics.order_fulfillment.test_request_entry -v"""
import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, call

from .request_entry import Handler, candidate_payload

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.text='ESP32 L 30대 추가. 2026-11-20 생산 완료. 설치용. 조건 동일.'
        self.fields=dict(configuration_id='c',baseline_evidence_id='e',quantity=30,need_date='2026-11-20',need_basis='production_complete',reason='설치용',conditions_state='unchanged',conditions_note='조건 동일')
        self.nodes=[dict(id='c',type='Configuration'),dict(id='e',type='Evidence',origin='synthetic')]
        self.answer=dict(status='ready',fields=self.fields,issues=[],quotes={k:self.text for k in self.fields})
    def test_ready_server_identity(self):
        p=candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
        self.assertEqual((p['quantity'],p['unit'],p['origin']),(30,'piece','synthetic'))
        self.assertEqual(p['relation'],'additional')
    def test_missing_is_not_zero(self):
        self.assertIsNone(candidate_payload(dict(status='needs_confirmation',issues=['수량 필요']),self.text,self.nodes,'key','2026-11-03T10:00:00+09:00'))
    def test_fake_reference(self):
        self.fields['configuration_id']='invented'
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
    def test_injected_identity(self):
        self.fields['requester']='admin'
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
    def test_unquoted_quantity(self):
        self.answer['quotes']['quantity']='없는 구절'
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
    def test_zero_rejected(self):
        self.fields['quantity']=0
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')

class RequestEntryHttpTests(unittest.TestCase):
    def setUp(self):
        self.entry = Mock(
            settings=SimpleNamespace(model='test-model'),
            policy=SimpleNamespace(max_cost_usd=1),
        )
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.entry = self.entry
        self.server.csrf = 'test-csrf'
        self.server.host_header = f'127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True,
        )
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        request_headers = {}
        if method == 'POST':
            request_headers = {
                'Content-Type': 'application/json',
                'Origin': f'http://{self.server.host_header}',
                'X-CSRF-Token': self.server.csrf,
            }
        request_headers.update(headers or {})
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def assert_frame_protected(self, headers):
        self.assertEqual(
            ('DENY', "frame-ancestors 'none'"),
            (headers.get('X-Frame-Options'), headers.get('Content-Security-Policy')),
        )

    def assert_reply_protected(self, headers):
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertEqual('nosniff', headers['X-Content-Type-Options'])
        self.assert_frame_protected(headers)

    def test_successful_responses_are_frame_protected(self):
        self.entry.preview.return_value = {'status': 'preview'}
        self.entry.confirm_context.return_value = {'business_context': {'ready': True}}
        self.entry.register.return_value = {'status': 'registered'}
        selection = {'contract_item_id': 'item'}
        cases = (
            ('GET', '/', None, 'text/html; charset=utf-8', None),
            ('GET', '/api/info', None, 'application/json; charset=utf-8',
             {'csrf': 'test-csrf', 'model': 'test-model', 'max_cost_usd': 1}),
            ('POST', '/api/preview', {'text': 'request', 'key': 'key'},
             'application/json; charset=utf-8', {'status': 'preview'}),
            ('POST', '/api/context', {'key': 'key', 'selection': selection},
             'application/json; charset=utf-8', {'business_context': {'ready': True}}),
            ('POST', '/api/register', {'key': 'key'},
             'application/json; charset=utf-8', {'status': 'registered'}),
        )
        for method, path, data, content_type, expected in cases:
            with self.subTest(method=method, path=path):
                body = json.dumps(data) if data is not None else None
                status, headers, raw = self.request(method, path, body)
                self.assertEqual(200, status)
                self.assertEqual(content_type, headers['Content-Type'])
                if expected is None:
                    self.assertIn(b'<script>', raw)
                    self.assertIn(b'<style>', raw)
                else:
                    self.assertEqual(expected, json.loads(raw))
                self.assert_reply_protected(headers)
        self.assertEqual(
            [call.preview('request', 'key'), call.confirm_context('key', selection),
             call.register('key')],
            self.entry.method_calls,
        )

    def test_rejected_requests_are_frame_protected_and_skip_entry(self):
        preview = '{"text":"request","key":"key"}'
        cases = (
            ('GET', '/', None, {'Host': 'example.invalid'}, 403),
            ('POST', '/api/preview', preview, {'Host': 'example.invalid'}, 403),
            ('POST', '/api/preview', preview, {'Origin': 'https://example.invalid'}, 403),
            ('POST', '/api/preview', preview, {'X-CSRF-Token': 'invalid'}, 403),
            ('POST', '/api/preview', '{', {}, 400),
            ('GET', '/missing', None, {}, 404),
            ('POST', '/missing', preview, {}, 404),
            ('PUT', '/api/preview', preview, {}, 501),
        )
        for method, path, body, request_headers, expected_status in cases:
            with self.subTest(method=method, path=path, request_headers=request_headers):
                status, headers, _ = self.request(method, path, body, request_headers)
                self.assertEqual(expected_status, status)
                if expected_status == 501:
                    self.assert_frame_protected(headers)
                else:
                    self.assert_reply_protected(headers)
        self.assertEqual([], self.entry.method_calls)

    def test_processing_error_is_frame_protected(self):
        self.entry.preview.side_effect = RuntimeError('entry failure')
        status, headers, _ = self.request('POST', '/api/preview', '{"text":"request","key":"key"}')
        self.assertEqual(500, status)
        self.assert_reply_protected(headers)

# Opt-in real PostgreSQL + Neo4j test; only creates its own development schema.
import os
@unittest.skipUnless(os.environ.get('REQUEST_ENTRY_INTEGRATION')=='1','explicit integration environment required')
class IntegrationTests(unittest.TestCase):
    def test_registration_replay_and_readback(self):
        import json, tempfile
        from dataclasses import replace
        from pathlib import Path
        from uuid import uuid4
        from ..database import DatabaseSettings
        from ..graph_views import GraphSettings
        from ..service import Service
        from ..model import ROOT
        from ..openai_provider import OpenAISettings, MODEL
        from ..paid_budget import cost_policy_from_environment
        from .request_entry import Entry
        from examples.order_fulfillment.b_material_request_registration import fixture_nodes
        from .contracts import load_demo_contract
        db=DatabaseSettings.from_environment(schema='live_request_'+uuid4().hex,env_file=os.environ['REQUEST_ENTRY_DB_ENV'])
        graph=replace(GraphSettings.from_environment(env_file=os.environ['REQUEST_ENTRY_GRAPH_ENV'],backend='neo4j'),namespace='materials_live_request')
        svc=Service(db,principals={'seed':{'kind':'human','roles':['read','seed']}})
        try:self.assertTrue(svc.bootstrap('seed',fixture_nodes()+load_demo_contract(),request_key='seed')['ok'])
        finally:svc.close()
        text='검증용 ESP32-POE Rev L WROOM 7대 추가. HB-20261102-01과 동일. 2026-11-20 생산 완료. 별도 시험 설치. 조건 동일.'
        fields=dict(configuration_id='config:esp32-poe:L:WROOM',baseline_evidence_id='evidence:demo:plan:HB-20261102-01',quantity=7,need_date='2026-11-20',need_basis='production_complete',reason='별도 시험 설치',conditions_state='unchanged',conditions_note='기준 계획과 동일')
        class Provider:
            def __call__(self,**kw):return dict(text=json.dumps(dict(status='ready',issues=[],fields=fields,quotes={k:text for k in fields})),tool_calls=[])
        policy=cost_policy_from_environment(json.loads((Path(__file__).parent/'request_entry_cost.example.json').read_text()))
        with tempfile.TemporaryDirectory() as tmp:
            entry=Entry(db,graph,OpenAISettings('test-key',model=MODEL),policy,store=tmp,provider_factory=Provider)
            key=uuid4().hex
            preview=entry.preview(text,key);self.assertEqual(preview['status'],'preview')
            self.assertEqual(entry.preview(text,key),preview)
            with self.assertRaises(ValueError):entry.preview(text+'수정',key)
            with self.assertRaises(ValueError): entry.register(key)
            linked=entry.confirm_context(key,{'contract_item_id':'contract-item:demo:B-01:1','delivery_round_id':'delivery-round:demo:B-01:1'})
            self.assertTrue(linked['business_context']['ready'])
            result=entry.register(key);self.assertEqual(result['status'],'registered')
            self.assertEqual(result['payload']['delivery_round_id'],'delivery-round:demo:B-01:1')
            self.assertEqual(result['registered_version'],2)
            self.assertEqual(entry.register(key),result)
            saved=json.loads((Path(tmp)/(key+'.json')).read_text())
            self.assertIn(text,saved['payload']['reason'])


if __name__=='__main__': unittest.main()
