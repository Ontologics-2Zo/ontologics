"""A mail workflow: isolated PostgreSQL, durable local mailbox, common approval."""
import base64
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
import json
import os
import shutil
import unittest
from unittest.mock import patch

from ontologics.materials.workspace import MaterialsWorkspace
from ontologics.model import ROOT
from tests.materials import test_rfq_handoff as handoff_tests


class RFQMailTests(unittest.TestCase):
    ok = handoff_tests.RFQHandoffTests.ok
    response = handoff_tests.RFQHandoffTests.response
    dispatch = handoff_tests.RFQHandoffTests.dispatch

    def setUp(self):
        clean = {key:'' for key in os.environ if key.startswith('MATERIALS_MAIL_') or key.startswith('MATERIALS_SMTP_')}
        self.env = patch.dict(os.environ, dict(clean, MATERIALS_MAIL_TRANSPORT='local_capture'))
        self.env.start(); self.addCleanup(self.env.stop)
        handoff_tests.RFQHandoffTests.setUp(self)
        self.mail_root = ROOT/'data/receipts/materials/rfq_mail'/self.store_id
        self.addCleanup(shutil.rmtree, self.mail_root, True)
        self.ref = dict(source_kind='workspace', review_id=self.saved['id'])
        self.compose = self.dispatch('mail-compose', self.ref)
        self.mail_body = dict(self.ref, contact_id=self.compose['contacts'][0]['id'],
            subject='RFQ 구매 검토 요청', message='저장된 부족량을 검토해 주세요.\n', request_key='mail-one')

    def save(self, **updates):
        return self.dispatch('mail-save', dict(self.mail_body, **updates))['item']

    def send(self, item):
        return self.dispatch('mail-send', dict(draft_id=item['draft_id']))['item']

    def get(self, item, context=None):
        return self.dispatch('mail-get', dict(draft_id=item['draft_id']), context)['item']

    def file(self, item, kind):
        return base64.b64decode(self.dispatch('mail-file', dict(draft_id=item['draft_id'],kind=kind))['content_base64'])

    def test_save_is_not_send_and_preserves_exact_rfq_and_mime_for_reopen(self):
        with patch('ontologics.materials.rfq_mail.smtp_submit') as smtp:
            item = self.save()
        smtp.assert_not_called()
        self.assertEqual('draft', item['status'])
        self.assertTrue(item['can_send'])
        self.assertIsNone(item['sent_at'])
        self.assertFalse((self.mail_root/item['draft_id']/'attempt.json').exists())
        eml = self.file(item, 'mail')
        parsed = BytesParser(policy=policy.default).parsebytes(eml)
        attachments = list(parsed.iter_attachments())
        self.assertEqual(1, len(attachments))
        self.assertEqual(self.saved['payload']['rfq']['text'].encode(), attachments[0].get_payload(decode=True))
        self.assertEqual(self.file(item, 'rfq'), attachments[0].get_payload(decode=True))
        self.assertEqual(self.mail_body['subject'], str(parsed['Subject']))
        self.assertEqual(self.mail_body['message'], parsed.get_body().get_content().replace('\r\n','\n'))
        self.app = MaterialsWorkspace(self.host, self.alice)
        self.assertEqual(item, self.get(item))
        self.assertEqual(eml, self.file(item, 'mail'))
        self.assertEqual(item['draft_id'], self.save()['draft_id'])
        changed = self.response('mail-save', dict(self.mail_body, subject='Changed'))
        self.assertEqual('REQUEST_KEY_REUSED', changed['errors'][0]['code'])

    def test_local_send_auto_records_then_requires_other_person_common_approval(self):
        draft = self.save()
        with patch('ontologics.materials.rfq_mail.smtp_submit') as smtp:
            item = self.send(draft)
        smtp.assert_not_called()
        self.assertEqual('local_captured', item['status'])
        self.assertFalse(item['transport']['external'])
        self.assertFalse(item['can_send'])
        self.assertTrue(item['sent_at'])
        self.assertEqual('pending_review', item['graph_status'])
        handoff = item['handoff']
        self.assertEqual('synthetic', handoff['evidence_origin'])
        common = handoff['draft']
        with self.host.request(self.alice) as s:
            denied = s.review(common['id'], common['version'], decision='approve', note='self', request_key='self')
        self.assertFalse(denied['ok'])
        with self.host.request(self.bob) as s:
            self.ok(s.review(common['id'], common['version'], decision='approve', note='문서 확인', request_key='approve'))
        self.ok(self.host.apply_approved(common['id'], common['version'], request_key='apply'))
        self.assertEqual('applied', self.get(item)['graph_status'])
        receipt_evidence = next(op['node'] for op in common['operations'] if op['node']['id']==handoff['evidence_id'])
        receipt = json.loads(receipt_evidence['description'])
        self.assertEqual('service_mail_submission', receipt['kind'])
        self.assertEqual('local_captured', receipt['status'])
        self.assertFalse(receipt['provider_verified'])
        self.assertFalse(receipt['external_delivery_confirmed'])

    def test_graph_failure_retry_uses_preserved_mail_without_transmission(self):
        from ontologics.responses import failure
        item = self.save()
        with patch('ontologics.service.Service.prepare_draft', return_value=failure('STORAGE_ERROR','temporary')):
            sent = self.send(item)
        self.assertEqual('local_captured', sent['status'])
        self.assertEqual('file_saved', sent['graph_status'])
        raw = self.file(sent, 'mail')
        self.app = MaterialsWorkspace(self.host, self.alice)
        with patch('ontologics.materials.rfq_mail.smtp_submit') as smtp:
            retried = self.dispatch('mail-graph-retry',dict(draft_id=item['draft_id']))['item']
            again = self.send(item)
        smtp.assert_not_called()
        self.assertEqual('pending_review',retried['graph_status'])
        self.assertEqual(retried['handoff']['draft']['id'],again['handoff']['draft']['id'])
        self.assertEqual(raw,self.file(retried,'mail'))

    def test_owner_isolation_permissions_and_history_survive_restart(self):
        item = self.save()
        for action, body in [('mail-get',dict(draft_id=item['draft_id'])),
                ('mail-send',dict(draft_id=item['draft_id'])),
                ('mail-file',dict(draft_id=item['draft_id'],kind='mail')),
                ('mail-graph-retry',dict(draft_id=item['draft_id']))]:
            denied = self.response(action,body,self.bob)
            self.assertFalse(denied['ok'],action)
        self.assertEqual('FORBIDDEN',self.response('mail-save',self.mail_body,self.reader)['errors'][0]['code'])
        self.assertTrue(self.response('mail-compose',self.ref,self.reader)['ok'])
        self.assertEqual([],self.dispatch('mail-history',dict(offset=0,source_kind='',review_id=''),self.bob)['items'])
        self.send(item)
        self.app = MaterialsWorkspace(self.host,self.alice)
        items = self.dispatch('mail-history',dict(offset=0,source_kind='',review_id=''))['items']
        self.assertEqual([item['draft_id']],[row['draft_id'] for row in items])
        self.assertEqual('local_captured',items[0]['status'])
        shared=self.dispatch('mail-history',dict(offset=0,source_kind='',review_id=''),self.reader)['items']
        self.assertEqual([item['draft_id']],[row['draft_id'] for row in shared])
        self.assertFalse(shared[0]['can_send'])
        self.assertEqual('local_captured',self.get(item,self.reader)['status'])
        self.assertTrue(self.dispatch('mail-file',dict(draft_id=item['draft_id'],kind='mail'),self.reader)['content_base64'])
        self.assertFalse(self.response('mail-send',dict(draft_id=item['draft_id']),self.bob)['ok'])

    def smtp_settings(self):
        return patch.dict(os.environ,dict(MATERIALS_MAIL_TRANSPORT='smtp',
            MATERIALS_MAIL_FROM='materials@example.org',MATERIALS_SMTP_HOST='smtp.example.org',
            MATERIALS_SMTP_PORT='587',MATERIALS_SMTP_TLS='starttls',
            MATERIALS_MAIL_CONTACTS=json.dumps([dict(id='buyer',name='구매 담당',email='buyer@example.org',origin='real')])) )

    def test_smtp_acceptance_is_recorded_as_submission_not_delivery_with_mixed_origins(self):
        with self.smtp_settings():
            item = self.save(contact_id='buyer')
            with patch('ontologics.materials.rfq_mail.smtp_submit',side_effect=lambda session,config,**kwargs:
                    dict(status='smtp_accepted',accepted_at=session._service.now())) as smtp:
                sent = self.send(item)
                duplicate = self.send(item)
        self.assertEqual(1,smtp.call_count)
        self.assertEqual(self.file(item,'mail'),smtp.call_args.kwargs['raw'])
        self.assertEqual('smtp_accepted',sent['status'])
        self.assertEqual(sent['sent_at'],duplicate['sent_at'])
        nodes = [op['node'] for op in sent['handoff']['draft']['operations']]
        self.assertEqual('real',sent['handoff']['evidence_origin'])
        self.assertEqual({'synthetic'}, {n['origin'] for n in nodes if n['id'].endswith(':rfq')})
        self.assertEqual({'real'}, {n['origin'] for n in nodes if not n['id'].endswith(':rfq')})
        desc=json.loads(next(n['description'] for n in nodes if n['id']==sent['handoff']['evidence_id']))
        self.assertTrue(desc['provider_verified'])
        self.assertFalse(desc['external_delivery_confirmed'])

    def test_smtp_rejected_unknown_and_crash_do_not_retry_after_restart(self):
        with self.smtp_settings():
            for status in ('rejected','unknown'):
                item=self.save(contact_id='buyer',request_key=status)
                with patch('ontologics.materials.rfq_mail.smtp_submit',return_value=dict(status=status,code='SAFE_CODE')) as smtp:
                    first=self.send(item)
                    self.app=MaterialsWorkspace(self.host,self.alice)
                    second=self.send(item)
                self.assertEqual(1,smtp.call_count)
                self.assertEqual(status,first['status'])
                self.assertEqual(status,second['status'])
                self.assertIsNone(second['sent_at'])
                self.assertIsNone(second['handoff'])
            crash=self.save(contact_id='buyer',request_key='crash')
            with patch('ontologics.materials.rfq_mail.smtp_submit',side_effect=SystemExit('process died')):
                with self.assertRaises(SystemExit): self.send(crash)
            self.app=MaterialsWorkspace(self.host,self.alice)
            with patch('ontologics.materials.rfq_mail.smtp_submit') as smtp:
                recovered=self.send(crash)
            smtp.assert_not_called()
            self.assertEqual('unknown',recovered['status'])

    def test_concurrent_sends_have_one_durable_winner(self):
        import threading
        entered,release=threading.Event(),threading.Event()
        with self.smtp_settings():
            item=self.save(contact_id='buyer')
            def submit(session,config,**kwargs):
                entered.set(); release.wait(10)
                return dict(status='smtp_accepted',accepted_at=session._service.now())
            with patch('ontologics.materials.rfq_mail.smtp_submit',side_effect=submit) as smtp:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first=pool.submit(self.send,item)
                    self.assertTrue(entered.wait(10))
                    second=pool.submit(self.send,item)
                    other=second.result(timeout=10)
                    release.set(); accepted=first.result(timeout=20)
            self.assertEqual(1,smtp.call_count)
            self.assertEqual('unknown',other['status'])
            self.assertEqual('smtp_accepted',accepted['status'])

    def test_seeded_test_address_never_reaches_external_transport(self):
        with self.smtp_settings(),patch.dict(os.environ,{'MATERIALS_MAIL_CONTACTS':''}):
            item=self.save()
            with patch('ontologics.materials.rfq_mail.smtp_submit') as smtp:
                sent=self.send(item)
        smtp.assert_not_called()
        self.assertEqual('local_captured',sent['status'])
        self.assertFalse(sent['transport']['external'])

    def test_validation_tamper_and_retarget_never_send(self):
        for updates in ({'subject':'bad\r\nBcc: attacker@example.org'},{'contact_id':'unknown'},
                        {'request_key':''},{'message':''},{'sender':'attacker@example.org'}):
            self.assertFalse(self.response('mail-save',dict(self.mail_body,**updates))['ok'])
        item=self.save()
        (self.mail_root/item['draft_id']/'message.eml').write_bytes(b'tampered')
        with patch('ontologics.materials.rfq_mail.smtp_submit') as smtp:
            self.assertFalse(self.response('mail-send',dict(draft_id=item['draft_id']))['ok'])
        smtp.assert_not_called()

    def test_same_save_recovers_missing_artifacts_after_interrupted_publication(self):
        from ontologics.materials.rfq_mail import RFQMail
        with patch.object(RFQMail,'_artifacts',side_effect=OSError('disk unavailable')):
            self.assertFalse(self.response('mail-save',self.mail_body)['ok'])
        self.app=MaterialsWorkspace(self.host,self.alice)
        item=self.save()
        self.assertEqual('draft',item['status'])
        self.assertEqual(self.saved['payload']['rfq']['text'].encode(),self.file(item,'rfq'))
        self.assertTrue(self.file(item,'mail'))

    def test_accepted_but_outcome_storage_failure_is_unknown_and_never_resends(self):
        from ontologics.materials.rfq_mail import _publish
        with self.smtp_settings():
            item=self.save(contact_id='buyer')
            def publish(path,record):
                if path.name=='outcome.json': raise OSError('disk unavailable')
                return _publish(path,record)
            with patch('ontologics.materials.rfq_mail._publish',side_effect=publish),patch(
                    'ontologics.materials.rfq_mail.smtp_submit',side_effect=lambda session,config,**kwargs:
                    dict(status='smtp_accepted',accepted_at=session._service.now())) as smtp:
                result=self.send(item)
            self.assertEqual(1,smtp.call_count)
            self.assertEqual('unknown',result['status'])
            self.assertIsNone(result['sent_at'])
            self.app=MaterialsWorkspace(self.host,self.alice)
            with patch('ontologics.materials.rfq_mail.smtp_submit') as retry:
                reopened=self.send(item)
            retry.assert_not_called()
            self.assertEqual('unknown',reopened['status'])

    def test_admin_retargeting_requires_new_draft_and_does_not_transmit_old_one(self):
        with self.smtp_settings():
            item=self.save(contact_id='buyer')
            with patch.dict(os.environ,{'MATERIALS_MAIL_FROM':'changed@example.org'}),patch(
                    'ontologics.materials.rfq_mail.smtp_submit') as smtp:
                response=self.response('mail-send',dict(draft_id=item['draft_id']))
            self.assertEqual('MAIL_CONFIGURATION_CHANGED',response['errors'][0]['code'])
            smtp.assert_not_called()
            self.assertEqual('draft',self.get(item)['status'])
