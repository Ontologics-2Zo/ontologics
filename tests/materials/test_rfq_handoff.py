"""RFQ handoff uses isolated PostgreSQL and the real common approval pipeline."""
import base64
import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.model import ROOT, digest
from ontologics.materials.workspace import MaterialsWorkspace
from ontologics.service import DomainError
from ontologics.web import USERS, seed_development
from tests.db import database_for_test


class RFQHandoffTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.assertTrue(seed_development(self.database)['ok'])
        self.alice = DevelopmentContext('dev-alice')
        self.bob = DevelopmentContext('dev-bob')
        self.reader = DevelopmentContext('dev-reader')
        self.host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), self.database)
        self.app = MaterialsWorkspace(self.host, self.alice)
        self.cid = 'config:esp32-poe:L:WROOM'
        from ontologics.materials.library_stock import company_stock_scenario
        with self.host.request(self.alice) as s:
            info = s._service.db.info
            self.store_id = digest([info.host, info.port, info.dbname, s._service.schema])
            detail = self.ok(s.call('configuration', {'configuration_id': self.cid}))
            stock = company_stock_scenario(detail, s._service.now())
        self.storage = ROOT/'data/receipts/materials/rfq_handoffs'/self.store_id
        self.addCleanup(shutil.rmtree, self.storage, True)
        self.ok(self.app.materials.stock.store(self.alice, self.cid, stock, 'stock'))
        self.ok(self.app.process_materials.seed_scenario(self.alice, 'process'))
        self.saved = self.dispatch('combined-review', dict(configuration_id=self.cid, quantity=200,
            allowance_percent='10', request_key='rfq-review'))
        self.attachment = b'To: purchasing@example.test\r\nSubject: RFQ handoff\r\n\r\nReported manual handoff.\r\n'
        with self.host.request(self.alice) as s:
            sent = s._service.now()
        self.body = dict(source_kind='workspace', review_id=self.saved['id'], recipient='purchasing@example.test',
            subject='RFQ 구매 검토 요청', message='부족량과 미확인 제외 품목을 검토해 주세요.\n', sent_at=sent,
            evidence_filename='sent.eml', evidence_base64=base64.b64encode(self.attachment).decode(),
            evidence_origin='real', confirmed=True, request_key='handoff-one')

    def ok(self, result):
        self.assertTrue(result['ok'], result)
        return result['data']

    def response(self, action, body, context=None):
        try:
            return self.app.dispatch(context or self.alice, action, body)
        except DomainError as exc:
            return dict(ok=False, errors=[dict(code=exc.code, message=exc.message)])

    def dispatch(self, action, body, context=None):
        return self.ok(self.response(action, body, context))

    def status(self, body=None, context=None):
        body = body or self.body
        return self.dispatch('rfq-handoff-status', {k:body[k] for k in ('source_kind','review_id')}, context)['items']

    def test_manual_receipt_keeps_exact_bytes_and_requires_other_user_approval_then_graph_readback(self):
        result = self.dispatch('rfq-handoff-prepare', self.body)
        item, draft = result['item'], result['draft']['data']
        self.assertEqual('pending_review', item['graph_status'])
        self.assertEqual('user:dev-alice', item['reported_by'])
        self.assertEqual(self.attachment, (ROOT/item['evidence_path']).read_bytes())
        raw = self.saved['payload']['rfq']['text'].encode()
        self.assertEqual(raw, (ROOT/item['rfq_path']).read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), item['rfq_sha256'])
        with self.host.request(self.alice) as s:
            denied = s.review(draft['draft_id'], draft['draft_version'], decision='approve', note='self', request_key='self')
        self.assertFalse(denied['ok'])
        with self.host.request(self.bob) as s:
            self.ok(s.review(draft['draft_id'], draft['draft_version'], decision='approve', note='문서 대조', request_key='review'))
        self.assertEqual('approved_pending_apply', self.status()[0]['graph_status'])
        applied = self.ok(self.host.apply_approved(draft['draft_id'], draft['draft_version'], request_key='apply'))
        status = self.status()[0]
        self.assertEqual('applied', status['graph_status'])
        self.assertEqual(draft['draft_id'], status['draft']['draft_id'])
        with self.host.request(self.alice) as s:
            receipt = self.ok(s.call('lookup', dict(node_id=item['evidence_id'], version=applied['applied_version'])))[0]
        description = json.loads(receipt['description'])
        self.assertEqual('real', receipt['origin'])
        self.assertEqual([], receipt['evidence'])
        self.assertEqual(self.saved['id'], description['rfq']['review_id'])
        self.assertEqual(item['rfq_sha256'], description['rfq']['sha256'])
        self.assertEqual('synthetic', description['rfq']['origin'])
        self.assertEqual('user_reported_manual_handoff', description['kind'])

    def test_failed_common_prepare_survives_reload_and_owner_retries_preserved_receipt(self):
        from ontologics.responses import failure
        with patch('ontologics.service.Service.prepare_draft', return_value=failure('STORAGE_ERROR', 'temporary')):
            response = self.response('rfq-handoff-prepare', self.body)
        self.assertFalse(response['ok'])
        item = response['data']['item']
        self.assertEqual('file_saved', item['graph_status'])
        frozen = (ROOT/item['receipt_path']).read_bytes()
        self.app = MaterialsWorkspace(self.host, self.alice)
        self.assertEqual('file_saved', self.status()[0]['graph_status'])
        retry = dict(source_kind='workspace', review_id=self.saved['id'], receipt_id=item['receipt_id'])
        denied = self.response('rfq-handoff-retry', retry, self.bob)
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        result = self.dispatch('rfq-handoff-retry', retry)
        self.assertEqual('pending_review', result['item']['graph_status'])
        self.assertEqual(frozen, (ROOT/item['receipt_path']).read_bytes())
        self.assertEqual(item['evidence_sha256'], result['item']['evidence_sha256'])
        self.assertEqual(result['draft']['data']['draft_id'], self.dispatch('rfq-handoff-retry', retry)['draft']['data']['draft_id'])
        self.assertFalse(self.status(context=self.bob)[0]['can_retry'])

    def test_retry_refuses_wrong_receipt_binding_and_caller_paths(self):
        item = self.dispatch('rfq-handoff-prepare', self.body)['item']
        for updates in ({'source_kind':'request'}, {'review_id':'wrong'}, {'receipt_id':'../../outside'}):
            body = dict(source_kind='workspace', review_id=self.saved['id'], receipt_id=item['receipt_id'])
            body.update(updates)
            result = self.response('rfq-handoff-retry', body)
            self.assertFalse(result['ok'], result)
        self.assertEqual(1, len(self.status()))

    def test_same_key_replay_is_immutable_and_changed_input_needs_new_key(self):
        first = self.dispatch('rfq-handoff-prepare', self.body)
        frozen = {name: path.read_bytes() for name, path in ((key, ROOT/first['item'][key])
                  for key in ('receipt_path','rfq_path','evidence_path'))}
        second = self.dispatch('rfq-handoff-prepare', self.body)
        self.assertEqual(first['draft']['data']['draft_id'], second['draft']['data']['draft_id'])
        for updates in ({'message':'changed'}, {'recipient':'other@example.test'},
                        {'evidence_base64':base64.b64encode(b'new evidence').decode()}):
            result = self.response('rfq-handoff-prepare', dict(self.body, **updates))
            self.assertEqual('REQUEST_KEY_REUSED', result['errors'][0]['code'])
        next_record = self.dispatch('rfq-handoff-prepare', dict(self.body, request_key='next', message='another handoff'))
        self.assertNotEqual(first['item']['receipt_id'], next_record['item']['receipt_id'])
        self.assertEqual(2, len(self.status()))
        for name, raw in frozen.items():
            self.assertEqual(raw, (ROOT/first['item'][name]).read_bytes())
        later = self.dispatch('combined-review', dict(configuration_id=self.cid, quantity=201,
            allowance_percent='10', request_key='later-review'))
        self.assertNotEqual(self.saved['id'], later['id'])
        self.assertEqual(frozen['rfq_path'], (ROOT/first['item']['rfq_path']).read_bytes())

    def test_permissions_unsupported_fields_and_invalid_report_never_write_receipt(self):
        denied = self.response('rfq-handoff-prepare', self.body, self.reader)
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        invalids = [{'source_kind':'pp_request_reviews'}, {'source_kind':[]}, {'actor':'user:dev-bob'},
            {'rfq_sha256':'f'*64}, {'confirmed':False}, {'confirmed':1}, {'evidence_origin':'verified'},
            {'recipient':'a@example.test\nBcc: b@example.test'}, {'recipient':'a@example.test,b@example.test'},
            {'recipient':''}, {'subject':'a\nb'}, {'subject':'x'*301}, {'message':'x'*8001},
            {'sent_at':'2099-01-01T00:00:00Z'}, {'sent_at':'2020-01-01T00:00:00Z'},
            {'sent_at':'2026-11-04T00:00:00'}, {'evidence_filename':'../sent.eml'},
            {'evidence_filename':'sent.pdf'}, {'evidence_base64':'bad base64'},
            {'evidence_base64':base64.b64encode(b'x'*(256*1024+1)).decode()}, {'request_key':''}]
        for change in invalids:
            with self.subTest(fields=list(change)):
                self.assertFalse(self.response('rfq-handoff-prepare', dict(self.body, **change))['ok'])
        self.assertEqual([], self.status(context=self.reader))
        self.assertFalse(self.storage.exists())

    def test_empty_shortages_cannot_be_registered_and_missing_review_is_not_found(self):
        saved = self.dispatch('combined-review', dict(configuration_id=self.cid, quantity=0,
            allowance_percent='10', request_key='empty-review'))
        response = self.response('rfq-handoff-prepare', dict(self.body, review_id=saved['id']))
        self.assertEqual('NO_CONFIRMED_SHORTAGE', response['errors'][0]['code'])
        response = self.response('rfq-handoff-prepare', dict(self.body, review_id='unknown'))
        self.assertEqual('NOT_FOUND', response['errors'][0]['code'])

    def test_rejection_changes_requested_and_unavailable_graph_are_distinct(self):
        for decision, expected in [('request_changes','changes_requested'), ('reject','rejected')]:
            body = dict(self.body, request_key=decision)
            draft = self.dispatch('rfq-handoff-prepare', body)['draft']['data']
            with self.host.request(self.bob) as s:
                self.ok(s.review(draft['draft_id'], 1, decision=decision, note='문서 대조', request_key=decision))
            self.assertEqual(expected, next(i for i in self.status() if i['request_key']==decision)['graph_status'])
        draft = self.dispatch('rfq-handoff-prepare', self.body)['draft']['data']
        with self.host.request(self.bob) as s:
            self.ok(s.review(draft['draft_id'], 1, decision='approve', note='문서 대조', request_key='approve'))
        self.ok(self.host.apply_approved(draft['draft_id'], 1, request_key='apply'))
        from ontologics.responses import failure
        with patch('ontologics.service.Service.lookup', return_value=failure('GRAPH_PUBLICATION_PENDING','pending')):
            item = next(i for i in self.status() if i['request_key']=='handoff-one')
        self.assertEqual('lookup_failed', item['graph_status'])
        self.assertEqual(draft['draft_id'], item['draft']['draft_id'])
        self.assertEqual('applied', next(i for i in self.status() if i['request_key']=='handoff-one')['graph_status'])

    def test_saved_request_source_is_read_from_request_store_and_origins_stay_separate(self):
        # Test-only saved request fixture exercises the distinct, existing store
        # layout; it is not a new request-registration production path.
        from ontologics.model import canonical
        from ontologics.materials.request_workflow import rfq_document
        request = dict(request_id='MAT-TEST-HANDOFF', revision=2, quantity=200,
            configuration_id=self.cid, need_date='2026-12-10', need_basis='production_complete',
            origin='synthetic', conditions_note='Existing conditions retained',
            contract_item_id='contract-item:fixture:one')
        p = self.saved['payload']
        payload = dict(request=request, rfq=rfq_document(request,p['bom'],p['process']),
            evidence_id='evidence:fixture:request', source_hash='a'*64, source_store='fixture-source-store',
            data_version=p['data_version'], basis_hash='b'*64, stock_scopes=p['stock_scopes'])
        identifier = digest([payload['source_store'],payload['evidence_id'],payload['basis_hash']])
        with self.host.request(self.alice) as s:
            s._service.db.execute('INSERT INTO pp_request_reviews VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (identifier,payload['source_store'],payload['evidence_id'],payload['basis_hash'],canonical(payload),
                 s.context['actor'],self.saved['created_at']))
        body = dict(self.body, source_kind='request', review_id=identifier, evidence_origin='synthetic')
        result = self.dispatch('rfq-handoff-prepare', body)
        item = result['item']
        self.assertEqual(payload['rfq']['text'].encode(), (ROOT/item['rfq_path']).read_bytes())
        self.assertEqual('contract-item:fixture:one', item['rfq']['references']['request']['contract_item_id'])
        self.assertEqual('synthetic', item['evidence_origin'])
        self.assertEqual(1,len(self.status(body)))
        self.assertEqual([],self.status())
        self.assertTrue(result['draft']['data']['validation']['valid'])
        self.assertTrue(all(change['after']['origin']=='synthetic' for change in result['draft']['data']['changes']))

    def test_damaged_preserved_files_cannot_claim_valid_graph_status(self):
        item = self.dispatch('rfq-handoff-prepare', self.body)['item']
        (ROOT/item['evidence_path']).write_bytes(b'corrupted attachment')
        result = self.response('rfq-handoff-status', {'source_kind':'workspace','review_id':self.saved['id']})
        self.assertFalse(result['ok'])
        self.assertEqual('RFQ_INTEGRITY', result['errors'][0]['code'])

    def test_common_exception_keeps_file_saved_receipt_in_failure_response(self):
        with patch('ontologics.service.Service.prepare_draft', side_effect=DomainError('STORAGE_ERROR','temporary')):
            response = self.response('rfq-handoff-prepare', self.body)
        self.assertFalse(response['ok'])
        self.assertIsNotNone(response.get('data'), 'saved receipt must be returned on common failure')
        self.assertEqual('file_saved', response['data']['item']['graph_status'])
        self.assertTrue(response['data']['item']['can_retry'])

    def test_invalid_common_draft_is_not_reported_as_success(self):
        from ontologics.materials.rfq_handoff_document import build_nodes
        def invalid_nodes(path, record):
            nodes = build_nodes(path,record)
            nodes[0]['unsupported_field'] = 'invalid'
            return nodes
        with patch('ontologics.materials.rfq_handoff.build_nodes', side_effect=invalid_nodes):
            response = self.response('rfq-handoff-prepare', self.body)
        self.assertFalse(response['ok'], 'common draft persistence is not validation success')
        self.assertEqual('VALIDATION_FAILED', response['errors'][0]['code'])
        self.assertEqual('changes_requested', response['data']['item']['graph_status'])

    def test_incomplete_artifact_save_can_recover_from_preserved_receipt(self):
        with patch('ontologics.materials.rfq_handoff.preserve_artifacts', side_effect=OSError('disk unavailable')):
            response = self.response('rfq-handoff-prepare', self.body)
        self.assertFalse(response['ok'])
        item = response['data']['item']
        self.assertEqual('file_saved',item['graph_status'])
        self.assertEqual('file_saved',self.status()[0]['graph_status'])
        retry = dict(source_kind='workspace',review_id=self.saved['id'],receipt_id=item['receipt_id'])
        result = self.dispatch('rfq-handoff-retry', retry)
        self.assertEqual('pending_review',result['item']['graph_status'])
        self.assertEqual(self.attachment,(ROOT/result['item']['evidence_path']).read_bytes())

    def test_status_follows_exact_common_rebase_without_rewriting_receipt_or_original_reference(self):
        first = self.dispatch('rfq-handoff-prepare',self.body)
        item = first['item']
        draft = first['draft']['data']
        receipt_before = (ROOT/item['receipt_path']).read_bytes()
        ref_path = (ROOT/item['receipt_path']).parent/'draft.json'
        ref_before = ref_path.read_bytes()
        other = self.dispatch('rfq-handoff-prepare',dict(self.body,request_key='other'))['draft']['data']
        with self.host.request(self.bob) as s:
            self.ok(s.review(other['draft_id'],1,decision='approve',note='other',request_key='other-review'))
        self.ok(self.host.apply_approved(other['draft_id'],1,request_key='other-apply'))
        with self.host.request(self.bob) as s:
            stale = s.review(draft['draft_id'],1,decision='approve',note='stale',request_key='stale-review')
        self.assertFalse(stale['ok'])
        with self.host.request(self.alice) as s:
            revised = self.ok(s.call('prepare_draft',dict(operations=item['draft']['operations'],
                draft_id=draft['draft_id'],expected_version=1,request_key='rebase')))
        current = next(x for x in self.status() if x['receipt_id']==item['receipt_id'])
        self.assertEqual(2,current['draft']['draft_version'])
        self.assertEqual('pending_review',current['graph_status'])
        with self.host.request(self.bob) as s:
            self.ok(s.review(revised['draft_id'],2,decision='approve',note='rebased exact bytes',request_key='rebase-review'))
        self.ok(self.host.apply_approved(revised['draft_id'],2,request_key='rebase-apply'))
        self.assertEqual('applied',next(x for x in self.status() if x['receipt_id']==item['receipt_id'])['graph_status'])
        self.assertEqual(receipt_before,(ROOT/item['receipt_path']).read_bytes())
        self.assertEqual(ref_before,ref_path.read_bytes())

    def test_status_does_not_follow_different_operations_in_a_newer_common_version(self):
        result = self.dispatch('rfq-handoff-prepare',self.body)
        item, draft = result['item'], result['draft']['data']
        # A caller can submit a different proposal through common tools, but it
        # must never become approval for this exact handoff record.
        changed = item['draft']['operations'][:2]
        with self.host.request(self.alice) as s:
            self.ok(s.call('prepare_draft',dict(operations=changed,draft_id=draft['draft_id'],
                expected_version=1,request_key='different-proposal')))
        with self.host.request(self.bob) as s:
            self.ok(s.review(draft['draft_id'],2,decision='approve',note='different proposal',request_key='different-review'))
        self.ok(self.host.apply_approved(draft['draft_id'],2,request_key='different-apply'))
        status = self.status()[0]
        self.assertEqual('changes_requested',status['graph_status'])
        self.assertEqual(1,status['draft']['draft_version'])

    def test_reader_history_finds_prior_rfqs_after_apply_and_a_new_saved_review(self):
        first = self.dispatch('rfq-handoff-prepare',self.body)
        draft = first['draft']['data']
        with self.host.request(self.bob) as s:
            self.ok(s.review(draft['draft_id'],1,decision='approve',note='history test',request_key='history-review'))
        self.ok(self.host.apply_approved(draft['draft_id'],1,request_key='history-apply'))
        newer = self.dispatch('combined-review',dict(configuration_id=self.cid,quantity=201,
            allowance_percent='10',request_key='history-new-rfq'))
        with self.host.request(self.alice) as s:
            sent = s._service.now()
        second = self.dispatch('rfq-handoff-prepare',dict(self.body,review_id=newer['id'],
            sent_at=sent,request_key='history-new-handoff'))
        history = self.dispatch('rfq-handoff-history',{'offset':0},self.reader)
        self.assertEqual(2,history['total'])
        self.assertEqual(0,history['offset'])
        self.assertEqual(10,history['limit'])
        self.assertEqual([newer['id'],self.saved['id']],[item['review_id'] for item in history['items']])
        self.assertEqual(['pending_review','applied'],[item['graph_status'] for item in history['items']])
        self.assertEqual([second['item']['receipt_id'],first['item']['receipt_id']],
                         [item['receipt_id'] for item in history['items']])
        self.assertTrue(all(item['source_kind']=='workspace' for item in history['items']))
        self.assertTrue(all(not item['can_retry'] for item in history['items']))

    def test_history_has_stable_ten_record_pages_and_checks_offset_and_read_permission(self):
        receipts = []
        for number in range(12):
            item = self.dispatch('rfq-handoff-prepare',dict(self.body,request_key='page-'+str(number)))['item']
            receipts.append(item['receipt_id'])
        first = self.dispatch('rfq-handoff-history',{'offset':0},self.reader)
        second = self.dispatch('rfq-handoff-history',{'offset':10},self.reader)
        self.assertEqual(12,first['total'])
        self.assertEqual(12,second['total'])
        self.assertEqual(10,len(first['items']))
        self.assertEqual(2,len(second['items']))
        self.assertEqual(list(reversed(receipts)),[x['receipt_id'] for x in first['items']+second['items']])
        self.assertEqual([],self.dispatch('rfq-handoff-history',{'offset':20},self.reader)['items'])
        for offset in (-1,True,1.5,1000001,'0',None):
            with self.subTest(offset=offset):
                result = self.response('rfq-handoff-history',{'offset':offset})
                self.assertFalse(result['ok'])
                self.assertEqual('INVALID_INPUT',result['errors'][0]['code'])
        self.assertFalse(self.response('rfq-handoff-history',{'offset':0,'actor':'user:dev-alice'})['ok'])
        from ontologics.identity import UserIdentity
        self.host._identities = DevelopmentIdentityProvider([UserIdentity('dev-alice',frozenset())],enabled=True)
        denied = self.response('rfq-handoff-history',{'offset':0})
        self.assertEqual('FORBIDDEN',denied['errors'][0]['code'])
