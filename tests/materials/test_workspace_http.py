"""Shared main HTTP entry exercises A-only adapter without model calls."""
from tests.test_web import WebCase


class MaterialsWorkspaceHTTPTests(WebCase):
    def test_mail_routes_keep_common_permissions_csrf_and_source_binding(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'MATERIALS_MAIL_TRANSPORT':'local_capture', 'MATERIALS_MAIL_CONTACTS':''}):
            prefix = '/api/planning/'
            handoff = self.handoff_body()
            ref = {key:handoff[key] for key in ('source_kind','review_id')}
            compose = self.ok(self.http(self.reader,prefix+'mail-compose',ref))['data']
            self.assertFalse(compose['transport']['external'])
            body = dict(ref,contact_id=compose['contacts'][0]['id'],subject='견적 자료',message='검토해 주세요.',request_key='http-mail')
            self.failed(self.http(self.alice, prefix+'mail-save', body,
                                 headers={'X-CSRF-Token':'bad'}), 403, 'CSRF_REJECTED')
            self.failed(self.http(self.reader, prefix+'mail-save', body), 403, 'FORBIDDEN')
            self.failed(self.http(self.alice, prefix+'mail-save', dict(body,actor='user:dev-bob')), 400, 'INVALID_INPUT')
            saved = self.ok(self.http(self.alice,prefix+'mail-save',body))['data']['item']
            self.assertEqual('draft',saved['status'])
            sent = self.ok(self.http(self.alice,prefix+'mail-send',dict(draft_id=saved['draft_id'])))['data']['item']
            self.assertEqual('local_captured',sent['status'])
            self.assertEqual('pending_review',sent['graph_status'])
            read = self.ok(self.http(self.reader,prefix+'mail-get',dict(draft_id=saved['draft_id'])))['data']['item']
            self.assertEqual(sent['draft_id'],read['draft_id'])
            self.assertFalse(read['can_send'])
            self.failed(self.http(self.reader,prefix+'mail-send',dict(draft_id=saved['draft_id'])),403,'FORBIDDEN')

    def test_product_review_and_request_read_permissions_through_main_routes(self):
        path = '/api/planning/'
        inventory = self.ok(self.http(self.alice, path+'product-inventory', {'quantity':7}))['data']
        cid = inventory['products'][0]['configuration_id']
        preview = self.ok(self.http(self.alice, path+'process-requirement-preview', {
            'configuration_id':cid,'quantity':7,'allowance_percent':'10'}))['data']
        self.assertTrue(preview['lines'])
        body = {'configuration_id':cid,'quantity':7,'allowance_percent':'10','request_key':'combined-http'}
        self.failed(self.http(self.reader, path+'combined-review', body),403,'FORBIDDEN')
        saved = self.ok(self.http(self.alice, path+'combined-review', body))['data']
        self.assertEqual(7,saved['payload']['quantity'])
        self.assertFalse(saved['payload']['rfq']['sent'])
        self.assertTrue(saved['payload']['rfq']['excluded'])
        self.assertEqual(saved['id'],self.ok(self.http(self.alice,path+'combined-review',body))['data']['id'])
        inbox = self.ok(self.http(self.alice,path+'request-inbox',{}))['data']
        self.assertEqual([],inbox['requests'])
        self.failed(self.http(self.alice,path+'combined-review',dict(body,quantity=-1,request_key='negative')),400,'INVALID_INPUT')
        self.assertEqual(1,self.ok(self.http(self.alice,'/api/context'))['data_version'])

    def handoff_body(self):
        import base64
        import shutil
        from ontologics.identity import DevelopmentContext
        from ontologics.model import ROOT, digest
        from ontologics.materials.library_stock import company_stock_scenario
        context = DevelopmentContext('dev-alice')
        self.ok(self.http(self.alice, '/api/planning/product-inventory', {'quantity':200}))
        app = self.alice.app.workspaces['planning']
        cid = 'config:esp32-poe:L:WROOM'
        with self.host.request(context) as session:
            detail = session.call('configuration', {'configuration_id':cid})['data']
            stock = company_stock_scenario(detail,session._service.now())
            info = session._service.db.info
            store = digest([info.host,info.port,info.dbname,session._service.schema])
        self.addCleanup(shutil.rmtree, ROOT/'data/receipts/materials/rfq_handoffs'/store, True)
        self.addCleanup(shutil.rmtree, ROOT/'data/receipts/materials/rfq_mail'/store, True)
        self.assertTrue(app.materials.stock.store(context,cid,stock,'http-stock')['ok'])
        self.assertTrue(app.process_materials.seed_scenario(context,'http-process')['ok'])
        saved = self.ok(self.http(self.alice,'/api/planning/combined-review',dict(configuration_id=cid,
            quantity=200,allowance_percent='10',request_key='handoff-rfq')))['data']
        with self.host.request(context) as session:
            sent = session._service.now()
        return dict(source_kind='workspace',review_id=saved['id'],recipient='purchasing@example.test',
            subject='견적 검토 자료',message='수동 인계 시험',sent_at=sent,evidence_filename='sent.txt',
            evidence_base64=base64.b64encode(b'manual handoff test\n').decode(),evidence_origin='synthetic',
            confirmed=True,request_key='handoff-http')

    def test_handoff_routes_enforce_fields_csrf_permissions_and_reuse_common_review(self):
        prefix = '/api/planning/'
        body = self.handoff_body()
        self.failed(self.http(self.reader,prefix+'rfq-handoff-prepare',body),403,'FORBIDDEN')
        self.failed(self.http(self.alice,prefix+'rfq-handoff-prepare',dict(body,actor='user:dev-bob')),400,'INVALID_INPUT')
        self.failed(self.http(self.alice,prefix+'rfq-handoff-prepare',body,headers={'X-CSRF-Token':'bad'}),403,'CSRF_REJECTED')
        prepared = self.ok(self.http(self.alice,prefix+'rfq-handoff-prepare',body))['data']
        self.assertEqual('pending_review',prepared['item']['graph_status'])
        self.failed(self.http(self.alice,'/api/review',self.review_body(prepared['draft']['data'])),403,'SELF_APPROVAL_FORBIDDEN')
        review = self.ok(self.http(self.bob,'/api/review',self.review_body(prepared['draft']['data'])))['data']
        self.assertTrue(review['application']['ok'],review)
        status = self.ok(self.http(self.reader,prefix+'rfq-handoff-status',
            {key:body[key] for key in ('source_kind','review_id')}))['data']['items']
        self.assertEqual('applied',status[0]['graph_status'])
        self.assertEqual(prepared['draft']['data']['draft_id'],status[0]['draft']['draft_id'])

    def test_handoff_http_failure_keeps_receipt_and_retries_after_refresh(self):
        from unittest.mock import patch
        from ontologics.responses import failure
        prefix = '/api/planning/'
        body = self.handoff_body()
        with patch('ontologics.service.Service.prepare_draft',return_value=failure('STORAGE_ERROR','temporary')):
            result = self.failed(self.http(self.alice,prefix+'rfq-handoff-prepare',body),503,'STORAGE_ERROR')
        item = result['data']['item']
        self.assertEqual('file_saved',item['graph_status'])
        status_body = {key:body[key] for key in ('source_kind','review_id')}
        self.assertTrue(self.ok(self.http(self.alice,prefix+'rfq-handoff-status',status_body))['data']['items'][0]['can_retry'])
        retry = dict(status_body,receipt_id=item['receipt_id'])
        self.failed(self.http(self.bob,prefix+'rfq-handoff-retry',retry),403,'FORBIDDEN')
        result = self.ok(self.http(self.alice,prefix+'rfq-handoff-retry',retry))['data']
        self.assertEqual('pending_review',result['item']['graph_status'])
        self.assertEqual(item['evidence_sha256'],result['item']['evidence_sha256'])
