"""Edited RFQ documents retain calculation provenance and immutable mail binding."""
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
import json
import shutil
import threading
import unittest
from unittest.mock import patch

from ontologics.materials.workspace import MaterialsWorkspace
from ontologics.model import ROOT, canonical, digest
from tests.materials import test_rfq_mail as mail_tests


class RFQDocumentTests(unittest.TestCase):
    ok=mail_tests.RFQMailTests.ok
    response=mail_tests.RFQMailTests.response
    dispatch=mail_tests.RFQMailTests.dispatch
    send=mail_tests.RFQMailTests.send
    file=mail_tests.RFQMailTests.file

    def setUp(self):
        mail_tests.RFQMailTests.setUp(self)
        self.document_root=ROOT/'data/receipts/materials/rfq_documents'/self.store_id
        self.addCleanup(shutil.rmtree,self.document_root,True)
        self.original=self.dispatch('rfq-document-get',self.ref)['document']
        self.edited_text=self.original['text'].replace('미발송 초안','구매팀 검토본')+'\n희망 납기: 구매팀 확인 후 회신 부탁드립니다.\n'

    def save_doc(self,**updates):
        return self.dispatch('rfq-document-save',dict(self.ref,base_document_id=self.original['document_id'],
            text=self.edited_text,request_key='rfq-edit-one',**updates))['document']

    def test_original_is_read_only_and_edit_retains_exact_text_provenance_and_restart_latest(self):
        self.assertEqual(0,self.original['version'])
        self.assertEqual(self.saved['payload']['rfq']['text'],self.original['text'])
        self.assertFalse(self.document_root.exists())
        before=canonical(self.saved['payload'])
        edited=self.save_doc()
        self.assertEqual(1,edited['version'])
        self.assertEqual(self.edited_text,edited['text'])
        self.assertEqual(self.original['sha256'],edited['base_rfq_sha256'])
        self.assertEqual(self.original['document_id'],edited['base_document_id'])
        self.assertEqual('user:dev-alice',edited['edited_by'])
        self.assertTrue(edited['edited_at'])
        self.app=MaterialsWorkspace(self.host,self.alice)
        self.assertEqual(edited,self.dispatch('rfq-document-get',self.ref)['document'])
        self.assertEqual(edited,self.dispatch('rfq-document-get',self.ref,self.reader)['document'])
        with self.host.request(self.alice) as s:
            row=s._service.db.execute('SELECT payload FROM pp_workspace_material_reviews WHERE id=%s',(self.saved['id'],)).fetchone()
        self.assertEqual(before,canonical(row['payload']))

    def test_retry_returns_original_success_after_later_version_and_changed_key_payload_fails(self):
        first=self.save_doc()
        second=self.dispatch('rfq-document-save',dict(self.ref,base_document_id=first['document_id'],
            text=first['text']+'두 번째 수정\n',request_key='rfq-edit-two'))['document']
        self.assertEqual(2,second['version'])
        self.assertEqual(first,self.save_doc())
        changed=self.response('rfq-document-save',dict(self.ref,base_document_id=self.original['document_id'],
            text='different',request_key='rfq-edit-one'))
        self.assertEqual('REQUEST_KEY_REUSED',changed['errors'][0]['code'])
        conflict=self.response('rfq-document-save',dict(self.ref,base_document_id=self.original['document_id'],
            text='stale browser',request_key='stale'))
        self.assertEqual('RFQ_DOCUMENT_CONFLICT',conflict['errors'][0]['code'])
        self.assertEqual(second,self.dispatch('rfq-document-get',self.ref)['document'])

    def test_concurrent_editors_claim_one_version_without_overwriting_either_input(self):
        from ontologics.materials.rfq_handoff_document import write_once
        barrier=threading.Barrier(2)
        def publish(path,raw):
            if path.name=='v000000001.json': barrier.wait(timeout=10)
            return write_once(path,raw)
        bodies=[dict(self.ref,base_document_id=self.original['document_id'],text='담당자 '+str(i),request_key='race-'+str(i)) for i in range(2)]
        with patch('ontologics.materials.rfq_document.write_once',side_effect=publish):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda body:self.response('rfq-document-save',body),bodies))
        self.assertEqual(1,sum(result['ok'] for result in results))
        failure=next(result for result in results if not result['ok'])
        self.assertEqual('RFQ_DOCUMENT_CONFLICT',failure['errors'][0]['code'])
        winner=next(result['data']['document'] for result in results if result['ok'])
        self.assertEqual(winner,self.dispatch('rfq-document-get',self.ref)['document'])
        self.assertEqual(1,len(list(self.document_root.glob('*/*.json'))))

    def test_permissions_blank_nul_size_and_bad_binding_do_not_write(self):
        body=dict(self.ref,base_document_id=self.original['document_id'],text=self.edited_text,request_key='edit')
        self.assertEqual('FORBIDDEN',self.response('rfq-document-save',body,self.reader)['errors'][0]['code'])
        for change in ({'text':' '},{'text':'bad\x00text'},{'text':'x'*(256*1024+1)},
                       {'base_document_id':'../bad'},{'request_key':''},{'edited_by':'user:dev-bob'}):
            self.assertFalse(self.response('rfq-document-save',dict(body,**change))['ok'],change.keys())
        self.assertFalse(self.document_root.exists())

    def test_tampered_document_cannot_be_read_or_attached(self):
        edited=self.save_doc()
        path=next(self.document_root.glob('*/v000000001.json'))
        data=json.loads(path.read_text());data['document']['text']='tampered'
        path.write_text(canonical(data)+'\n')
        self.assertEqual('RFQ_DOCUMENT_INTEGRITY',self.response('rfq-document-get',self.ref)['errors'][0]['code'])
        self.assertFalse(self.response('mail-save',dict(self.mail_body,rfq_document_id=edited['document_id']))['ok'])

    def test_selected_edit_is_exact_mime_and_graph_source_old_mail_survives_later_edit(self):
        edited=self.save_doc()
        compose=self.dispatch('mail-compose',dict(self.ref,rfq_document_id=edited['document_id']))
        self.assertEqual(edited['document_id'],compose['rfq_document']['document_id'])
        self.assertNotIn('확인된 부족 자재',compose['suggested']['message'])
        self.assertNotIn('미확인 자재',compose['suggested']['message'])
        item=self.dispatch('mail-save',dict(self.mail_body,rfq_document_id=edited['document_id']))['item']
        old_mail=self.file(item,'mail')
        mime=BytesParser(policy=policy.default).parsebytes(old_mail)
        self.assertEqual(edited['text'].encode(),list(mime.iter_attachments())[0].get_payload(decode=True))
        self.assertEqual(edited['text'].encode(),self.file(item,'rfq'))
        sent=self.send(item)
        self.assertEqual(edited['document_id'],sent['rfq_document']['document_id'])
        handoff=sent['handoff']
        self.assertEqual(edited['text'].encode(),(ROOT/handoff['rfq_path']).read_bytes())
        self.assertEqual(edited['sha256'],handoff['rfq_sha256'])
        self.assertEqual(edited['base_rfq_sha256'],handoff['rfq']['document']['base_rfq_sha256'])
        self.assertEqual('user:dev-alice',handoff['rfq']['document']['edited_by'])
        self.dispatch('rfq-document-save',dict(self.ref,base_document_id=edited['document_id'],
            text='나중에 새로 수정한 RFQ\n',request_key='later-edit'))
        self.app=MaterialsWorkspace(self.host,self.alice)
        self.assertEqual(old_mail,self.file(item,'mail'))
        reopened=self.dispatch('mail-get',dict(draft_id=item['draft_id']))['item']
        self.assertEqual(edited['document_id'],reopened['rfq_document']['document_id'])
        self.assertEqual(edited['text'].encode(),self.file(item,'rfq'))

    def test_omitted_document_id_keeps_original_and_other_review_document_is_rejected(self):
        edited=self.save_doc()
        original_compose=self.dispatch('mail-compose',self.ref)
        self.assertEqual(self.original['sha256'],original_compose['attachment']['sha256'])
        item=self.dispatch('mail-save',self.mail_body)['item']
        self.assertEqual(self.original['text'].encode(),self.file(item,'rfq'))
        other=self.dispatch('combined-review',dict(configuration_id=self.cid,quantity=201,
            allowance_percent='10',request_key='another-rfq'))
        for action,body in [('mail-compose',dict(source_kind='workspace',review_id=other['id'],rfq_document_id=edited['document_id'])),
                            ('mail-save',dict(self.mail_body,review_id=other['id'],request_key='cross-review',rfq_document_id=edited['document_id']))]:
            self.assertFalse(self.response(action,body)['ok'])
        self.assertEqual('draft',self.dispatch('mail-get',dict(draft_id=item['draft_id']))['item']['status'])

    def test_mail_key_cannot_silently_change_selected_document(self):
        edited=self.save_doc()
        self.dispatch('mail-save',self.mail_body)
        result=self.response('mail-save',dict(self.mail_body,rfq_document_id=edited['document_id']))
        self.assertEqual('REQUEST_KEY_REUSED',result['errors'][0]['code'])
