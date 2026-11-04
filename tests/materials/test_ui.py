"""Task10 real HTTP/PG tests; a scripted model is not an API quality test."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from urllib import request, error
from http.cookies import SimpleCookie


class AvailabilityTests(unittest.TestCase):
    def test_planning_http_adapter_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.materials.ui'),
                             'Task10 adapter has not been implemented')


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit disposable PostgreSQL required')
class MaterialsHTTPTests(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        from tests.db import isolated_database
        from examples.materials_ui import make_application
        from ontologics.materials.ui import MaterialsServer
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        settings = {s:self.stack.enter_context(isolated_database()) for s in ('standard','missing_field','stored_conflict')}
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='task10-http-'))
        self.app = make_application(settings, Path(self.temp))
        self.server = MaterialsServer(self.app, 0)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.02}, daemon=True)
        thread.start()
        def close():
            self.server.shutdown(); self.server.server_close(); thread.join(3)
        self.addCleanup(close)
        self.cookie = ''; self.csrf = ''
        self.context()

    def http(self, path, body=None, headers=None):
        h={'Origin':self.server.origin,'Cookie':self.cookie}
        if body is not None: h.update({'Content-Type':'application/json','X-CSRF-Token':self.csrf})
        h.update(headers or {})
        r=request.Request(self.server.origin+path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try: response=request.urlopen(r, timeout=45)
        except error.HTTPError as exc: response=exc
        with response:
            cookie=SimpleCookie(); cookie.load(response.headers.get('Set-Cookie',''))
            if cookie:self.cookie='; '.join(k+'='+v.value for k,v in cookie.items())
            raw=response.read()
            value=json.loads(raw) if response.headers.get_content_type()=='application/json' else raw
            return response.status, value

    def context(self, user=None):
        status,r=self.http('/api/development-user', {'user':user}) if user else self.http('/api/context')
        self.assertEqual(200,status,r); self.csrf=r['data']['csrf_token']; return r

    def call(self, op, body):
        return self.http('/api/tools/planning/'+op, body)

    def intake(self, scenario='standard', text=None):
        _, defaults=self.call('defaults', {'scenario':scenario})
        b=defaults['data']
        raw=b['contract_text'] if text is None else text
        body=dict(scenario=scenario, filename='contract.txt', content_base64=base64.b64encode(raw.encode()).decode(),
                  stock_text=b['stock_text'],production_text=b['production_text'],initial_quantity=70,
                  data_version=b['data_version'],request_key=str(uuid.uuid4()))
        status,r=self.call('intake',body); self.assertEqual(200,status,r)
        self.last_intake_body=body
        return r['data']

    @staticmethod
    def bound(d):
        return {k:d[k] for k in ('scenario','case_id','data_version','revision','target_sha256')}

    def confirmed(self, scenario='standard'):
        d=self.intake(scenario)
        self.context('bob')
        status,r=self.call('confirm',dict(self.bound(d),request_key=str(uuid.uuid4()),note='개발 시험: 주문 수량만 확인'))
        self.assertEqual(200,status,r)
        self.context('alice'); return d

    def calculate(self, d, mode='agent', key=None):
        return self.call('run',dict(self.bound(d),mode=mode,request_key=key or str(uuid.uuid4())))

    def test_external_provider_rejection_never_falls_back_to_fixture(self):
        class InvalidProvider:
            calls=[]
            def __call__(self, **kwargs):
                return dict(text='{}',tool_calls=[])
        self.app.provider_factory=lambda: InvalidProvider()
        self.app.model_name='external-test-provider'
        def forbidden(*args, **kwargs):
            raise AssertionError('Fixture interpretation must not run')
        self.app.interpretation_builder=forbidden
        d=self.intake()
        self.assertEqual('blocked',d['status'])
        self.assertIsNone(d['case_id'])
        self.assertEqual('live',d['execution']['mode'])
        self.assertTrue(d['errors'])

    def test_three_scenarios_real_workflow_and_separate_outputs(self):
        for scenario, expected in [('standard','review_ready'),('missing_field','host_result_available_model_rejected')]:
            d=self.confirmed(scenario); status,r=self.calculate(d)
            self.assertEqual(200,status,r); result=r['data']; self.assertEqual(expected,result['status'])
            self.assertTrue(result['host_result']['available'])
            answer=result['host_result']['answer']; f=answer['facts']
            self.assertEqual([100,70,30],[f['quantities'][k] for k in ('planned_input_quantity','initial_quantity','remaining_quantity')])
            self.assertEqual(3,len(answer['documents'])); self.assertEqual(71,f['scope']['unknown_stock_components'])
            self.assertIsNone(f['schedule']['remaining']['processing_start'])
            self.assertEqual('2026-10-26T14:30:00+09:00',f['schedule']['initial']['completion'])
            self.assertNotIn('runtime',result['model_review']); self.assertEqual(0,result['model_api_calls'])
        d=self.intake('stored_conflict'); self.assertEqual('blocked',d['status'])
        self.assertEqual('STORED_FACT_CONFLICT',d['data_checks'][0]['code'])
        self.assertIsNone(d.get('case_id'))
        case=self.app.scenarios['stored_conflict']
        with case['host'].request(self.app.context) as session:
            self.assertIsNone(session._service.db.execute("SELECT to_regclass('pp_document_versions') AS name").fetchone()['name'])

    def test_requires_actual_confirmation_and_rechecks_revocation(self):
        d=self.intake(); status,r=self.calculate(d); self.assertFalse(r['ok'])
        self.context('bob')
        self.call('confirm',dict(self.bound(d),request_key='confirm',note='주문 수량 확인'))
        self.context('alice')
        from ontologics.identity import UserIdentity
        provider=self.app.host._identities
        provider._users['dev-bob']=UserIdentity('dev-bob',frozenset({'read'}))
        _,r=self.calculate(d); self.assertFalse(r['ok'])

    def test_stale_hash_cancel_and_identity_injection_rejected(self):
        d=self.confirmed()
        for extra in ({'actor':'admin'},{'principals':{}},{'path':'/etc/passwd'}):
            status,r=self.call('run',dict(self.bound(d),mode='agent',request_key='attack',**extra))
            self.assertFalse(r['ok'])
        stale=dict(d,target_sha256='0'*64)
        _,r=self.calculate(stale); self.assertFalse(r['ok'])
        _,r=self.call('cancel',dict(self.bound(d),request_key='cancel')); self.assertTrue(r['ok'])
        _,r=self.calculate(d); self.assertFalse(r['ok'])

    def test_documents_exact_download_and_direct_path(self):
        d=self.confirmed(); _,r=self.calculate(d,'direct',key='direct')
        self.assertTrue(r['ok'],r); self.assertEqual('not_run',r['data']['model_review']['status'])
        refs=r['data']['host_result']['answer']['documents']
        _,again=self.calculate(d,'direct',key='direct')
        self.assertEqual(refs,again['data']['host_result']['answer']['documents'])
        for ref in refs:
            b=dict(self.bound(d),**{k:ref[k] for k in ('document_ref','document_version','content_sha256')})
            _,view=self.call('document',b); self.assertTrue(view['ok'],view)
            _,download=self.call('download',b); self.assertTrue(download['ok'],download)
            self.assertEqual(view['data']['markdown'],download['data']['markdown'])
            _,bad=self.call('download',dict(b,content_sha256='0'*64)); self.assertFalse(bad['ok'])

    def test_http_security_and_static_xss_boundary(self):
        status,html=self.http('/'); self.assertEqual(200,status); self.assertIn('계약 기반 생산계획'.encode('utf-8'),html)
        for headers in ({'X-CSRF-Token':'bad'},{'Origin':'https://evil.example'},{'Host':'evil.example'}):
            status,r=self.call_headers(headers); self.assertEqual(403,status,r)
        status,r=self.http('/api/tools/planning/confirm'); self.assertNotEqual(200,status)
        status,js=self.http('/task10/app.js'); self.assertEqual(200,status); self.assertNotIn(b'innerHTML',js)
        _,defaults=self.call('defaults',{'scenario':'standard'})
        text=defaults['data']['contract_text']+'\n<script>window.task10Pwned=1</script>'
        d=self.intake(text=text); self.assertIn('<script>',d['original']['text'])
        self.assertNotIn(self.temp,json.dumps(d))

    def call_headers(self,headers):
        return self.http('/api/tools/planning/defaults',{'scenario':'standard'},headers)

    def test_intake_replay_permissions_and_bad_json(self):
        d=self.intake(); _,r=self.call('intake',self.last_intake_body)
        self.assertEqual(d['case_id'],r['data']['case_id'])
        self.context('reader'); _,r=self.call('intake',dict(self.last_intake_body,request_key='reader'))
        self.assertFalse(r['ok'])
        self.context('alice'); _,r=self.call('intake',dict(self.last_intake_body,stock_text='{"stocks":[]}',request_key='bad'))
        self.assertFalse(r['ok'])

    def test_malformed_nested_production_is_input_error(self):
        self.intake()
        _,r=self.call('intake',dict(self.last_intake_body,production_text='{"purpose":"test","production_conditions":null}',request_key='null-production'))
        self.assertEqual('INVALID_INPUT',r['errors'][0]['code'])

    def test_changed_public_version_blocks_old_screen(self):
        d=self.confirmed()
        from ontologics.identity import DevelopmentContext
        from ontologics.model import ROOT
        s=self.app.scenarios['standard']; host=s['host']
        with host.request(DevelopmentContext('dev-alice')) as session:
            result=session.call('lookup',{'node_id':'contract-item:planning:001'})
            node=result['data'][0]; node['quantity']=101
            draft=session.call('prepare_draft',{'operations':[{'op':'put','node':node}], 'request_key':'version-change'})
            self.assertTrue(draft['ok'],draft)
        with host.request(DevelopmentContext('dev-bob')) as session:
            review=session.review(draft['data']['draft_id'],draft['data']['draft_version'],decision='approve',note='test correction only',request_key='review-correction')
            self.assertTrue(review['ok'],review)
        applied=host.apply_approved(draft['data']['draft_id'],draft['data']['draft_version'],request_key='apply-correction')
        self.assertTrue(applied['ok'],applied)
        _,r=self.calculate(d);self.assertFalse(r['ok']);self.assertEqual('DEMAND_TARGET_CHANGED',r['errors'][0]['code'])

    def test_document_superseded_version_cannot_download_as_current(self):
        d=self.confirmed();_,r=self.calculate(d)
        ref=r['data']['host_result']['answer']['documents'][0]
        from ontologics.identity import DevelopmentContext
        changed=self.app.scenarios['standard']['store'].revise_document(DevelopmentContext('dev-alice'),ref['document_ref'],expected_version=ref['document_version'],edits={'text':{'note':'test revision'}},request_key='revise-doc')
        self.assertTrue(changed['ok'],changed)
        _,r=self.call('download',dict(self.bound(d),**{k:ref[k] for k in ('document_ref','document_version','content_sha256')}))
        self.assertFalse(r['ok']);self.assertEqual('DOCUMENT_STALE',r['errors'][0]['code'])

    def test_pdf_text_extraction_then_unsupported_template_blocks(self):
        from io import BytesIO
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 50 700 Td (Synthetic order 100 boards) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        out=BytesIO();writer.write(out)
        self.intake();body=dict(self.last_intake_body,filename='order.pdf',content_base64=base64.b64encode(out.getvalue()).decode(),request_key='pdf')
        status,r=self.call('intake',body);self.assertEqual(200,status,r)
        self.assertIn('Synthetic order 100 boards',r['data']['original']['text'])
        self.assertEqual('blocked',r['data']['status'])
        _,r=self.call('intake',dict(body,filename='/etc/order.pdf',request_key='path'))
        self.assertEqual('INVALID_INPUT',r['errors'][0]['code'])


class LauncherIsolationTests(unittest.TestCase):
    def test_test_checkout_isolates_receipts_and_preserves_source(self):
        from examples import task10_sandbox
        self.assertTrue(hasattr(task10_sandbox,'prepare_test_checkout'),'Missing isolated test source copy; do not delete shared receipts')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'repo';root.mkdir();(root/'data/receipts').mkdir(parents=True)
            original=root/'data/receipts/user.json';original.write_text('user source')
            (root/'module.py').write_text('code')
            (root/'.env.local').write_text('test secret')
            copied=task10_sandbox.prepare_test_checkout(root,Path(directory)/'copy')
            (copied/'data/receipts/new-test.json').write_text('fixture')
            self.assertFalse((root/'data/receipts/new-test.json').exists())
            self.assertEqual('user source',original.read_text())
            self.assertEqual('code',(copied/'module.py').read_text())
            self.assertFalse((copied/'.env.local').exists())
