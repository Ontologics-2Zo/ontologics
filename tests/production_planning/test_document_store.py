"""Separate document store: real disposable PostgreSQL, no live supplier actions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ontologics.identity import DevelopmentContext, UserIdentity
from ontologics.service import Service
from tests.production_planning.test_documents import DocumentPostgresTests as _Setup


class DocumentStoreAvailabilityTests(unittest.TestCase):
    def test_separate_store_available(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.document_store'), 'Separate document store missing')


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit disposable PostgreSQL required')
class DocumentStorePostgresTests(unittest.TestCase):
    args = _Setup.args
    base_setup = _Setup.setUp

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.document_store'), 'Separate document store missing')
        self.base_setup()
        from ontologics.production_planning.document_store import BusinessDocumentWorkflow
        self.cls = BusinessDocumentWorkflow
        self.store = self.cls(self.host, self.workflow)
        self.reviewer = DevelopmentContext('reviewer')

    def ok(self, r):
        self.assertTrue(r['ok'], r.get('errors'))
        return r['data']

    def err(self, r, code):
        self.assertFalse(r['ok']); self.assertEqual(code, r['errors'][0]['code'])

    def create(self, key='documents-create'):
        return self.ok(self.store.create_documents(self.author, calculation_arguments=self.args(), request_key=key))['documents']

    def get(self, d, who=None):
        return self.ok(self.store.get_document(who or self.author, d['document_ref'], d['document_version']))

    def review(self, d, key='documents-review', who=None, decision='approve'):
        return self.store.review_document(who or self.reviewer, d['document_ref'], d['document_version'],
            expected_sha256=d['content_sha256'], decision=decision, note='개발 시험: 문서 내용 검토만', request_key=key)

    def details(self):
        return dict(recipient='test-only purchasing recipient', sent_at=Service.now(),
            method='test-only external email record; no message sent', source=dict(source_ref='test-only:dispatch',
                sha256='a'*64, locator='development fixture only'))

    def dispatch(self, d, key='documents-dispatch', details=None):
        return self.store.record_manual_dispatch(self.author, d['document_ref'], d['document_version'],
            expected_sha256=d['content_sha256'], details=self.details() if details is None else details, request_key=key)

    def test_create_three_reconnect_and_exact_export(self):
        before = self.workflow.get(self.reader, self.saved['demand_id'])['data']; stock = deepcopy(self.doc)
        with self.host.request(self.reader) as s: graph = s.call('lookup', {'version': 1})['data']
        docs = self.create(); self.assertEqual(3, len(docs))
        self.store = self.cls(self.host, self.workflow)
        for d in docs:
            view = self.get(d)
            self.assertEqual(1, view['document_version']); self.assertTrue(view['document_ref'])
            self.assertEqual('unreviewed', view['state']['review']); self.assertEqual('unsent', view['state']['dispatch'])
            self.assertEqual([], view['events']); self.assertTrue(view['binding_current'])
            self.assertEqual(71, view['payload']['scope']['unknown_stock_components'])
            self.assertIsNone(view['payload']['schedule']['remaining']['completion'])
            self.assertIn(view['document_ref'], view['markdown']); self.assertIn(view['content_sha256'], view['markdown'])
            self.assertNotIn('영속 저장/확정 문서 버전은 없습니다', view['markdown'])
            output = self.ok(self.store.export_document(self.author, d['document_ref'], 1, expected_sha256=d['content_sha256']))
            self.assertEqual(view['markdown'], output['markdown'])
            with tempfile.TemporaryDirectory() as folder:
                output = self.ok(self.store.export_document(self.author, d['document_ref'], 1,
                    expected_sha256=d['content_sha256'], output_directory=folder, filename='정확_버전.md'))
                self.assertEqual(view['markdown'], Path(output['path']).read_text(encoding='utf-8'))
        self.assertEqual(before, self.workflow.get(self.reader, self.saved['demand_id'])['data']); self.assertEqual(stock, self.doc)
        with self.host.request(self.reader) as s: self.assertEqual(graph, s.call('lookup', {'version': 1})['data'])

    def test_review_edit_immutable_old_version_and_new_unreviewed(self):
        d = self.create()[2]; old = self.get(d)
        self.ok(self.review(d)); self.assertEqual('reviewed', self.get(d)['state']['review'])
        self.assertFalse(self.get(d)['payload']['authority']['purchase_authorized'])
        new = self.ok(self.store.revise_document(self.author, d['document_ref'], expected_version=1,
            edits={'text': {'note': '의견 | 수정\n# 승인 아님'}}, request_key='revise-text'))
        self.assertEqual(2, new['document_version'])
        v2 = self.get(new); v1 = self.get(d)
        self.assertEqual(old['markdown'], v1['markdown']); self.assertEqual(old['payload'], v2['payload'])
        self.assertEqual('unreviewed', v2['state']['review']); self.assertEqual([], v2['events'])
        self.assertEqual(1, len(v1['events'])); self.assertFalse(v1['events'][0]['currently_valid'])
        self.err(self.review(d, key='stale-review'), 'DOCUMENT_VERSION_CONFLICT')

    def test_structured_recalculation_failure_preserves_old_version(self):
        d = self.create()[0]; old = self.get(d)
        r = self.store.revise_document(self.author, d['document_ref'], expected_version=1,
            edits={'inputs': {'initial_quantity': 71}}, request_key='bad-quantity')
        self.err(r, 'DOCUMENT_RECALCULATION_FAILED'); self.assertEqual(old['markdown'], self.get(d)['markdown'])
        self.assertEqual(1, self.get(d)['latest_version'])
        ic = deepcopy(self.args()['initial_conditions']); ic['quantity'] = 50
        new = self.ok(self.store.revise_document(self.author, d['document_ref'], expected_version=1,
            edits={'inputs': {'initial_quantity': 50, 'initial_conditions': ic}}, request_key='good-quantity'))
        view = self.get(new)
        self.assertEqual(50, view['payload']['quantities']['remaining_quantity'])
        self.assertEqual('2026-10-26T13:30:00+09:00', view['payload']['schedule']['initial']['completion'])
        self.assertEqual('unreviewed', view['state']['review'])

    def test_create_replay_and_different_input_conflict(self):
        first = self.create(); replay = self.create(); self.assertEqual(first, replay)
        args = self.args(); args['initial_quantity'] = 50
        self.err(self.store.create_documents(self.author, calculation_arguments=args, request_key='documents-create'), 'IDEMPOTENCY_CONFLICT')
        with self.host.request(self.author) as s:
            self.assertEqual(3, s._service.db.execute('SELECT count(*) AS n FROM pp_document_versions').fetchone()['n'])

    def test_review_dispatch_replay_no_duplicate_events(self):
        d = self.create()[2]
        a = self.ok(self.review(d)); b = self.ok(self.review(d)); self.assertEqual(a, b)
        details = self.details()
        first = self.ok(self.dispatch(d, details=details)); second = self.ok(self.dispatch(d, details=details)); self.assertEqual(first, second)
        v = self.get(d); self.assertEqual(2, len(v['events']))
        self.assertEqual('recorded', v['state']['dispatch']); self.assertEqual('unknown', v['state']['purchasing_received'])
        self.assertEqual('unknown', v['state']['ordered']); self.assertEqual('unknown', v['state']['received'])
        self.assertFalse(first['transmission_performed']); self.assertEqual('user:author', first['sender_actor'])
        other = self.details(); other['recipient'] = 'changed'
        self.err(self.dispatch(d, details=other), 'IDEMPOTENCY_CONFLICT')

    def test_other_user_self_review_agent_and_permissions(self):
        d = self.create()[0]
        self.err(self.store.get_document(self.reader, d['document_ref'], 1), 'DOCUMENT_FORBIDDEN')
        self.err(self.review(d, who=self.author), 'SELF_REVIEW_FORBIDDEN')
        self.err(self.store.revise_document(self.reviewer, d['document_ref'], expected_version=1,
            edits={'text': {'note': 'not mine'}}, request_key='not-owner'), 'FORBIDDEN')
        with self.host.request(self.author) as s:
            r = s.call('review_document', {'document_ref': d['document_ref'], 'version': 1})
            self.assertFalse(r['ok'])
        self.identities.users = [u if u.subject != 'reviewer' else UserIdentity('reviewer', frozenset({'read'})) for u in self.identities.users]
        self.err(self.review(d), 'FORBIDDEN')

    def test_revocation_before_replay_and_dispatch(self):
        d = self.create()[0]; self.ok(self.review(d))
        self.identities.users = [u if u.subject != 'reviewer' else UserIdentity('reviewer', frozenset({'read'})) for u in self.identities.users]
        self.err(self.review(d), 'FORBIDDEN')
        self.assertEqual('review_invalidated', self.get(d)['state']['review'])
        self.err(self.dispatch(d), 'DOCUMENT_REVIEW_REQUIRED')
        self.identities.users = [u if u.subject != 'author' else UserIdentity('author', frozenset({'read'})) for u in self.identities.users]
        self.err(self.store.create_documents(self.author, calculation_arguments=self.args(), request_key='documents-create'), 'FORBIDDEN')

    def test_manual_dispatch_requires_review_exact_hash_and_valid_details(self):
        d = self.create()[0]; self.err(self.dispatch(d), 'DOCUMENT_REVIEW_REQUIRED'); self.ok(self.review(d))
        for field, value in (('sent_at', '2026-10-26'), ('sent_at', '2099-01-01T00:00:00Z'), ('recipient', ''), ('sender_actor', 'user:forged'), ('source', {})):
            details = self.details(); details[field] = value
            self.err(self.dispatch(d, key='bad-'+field+'-'+str(value), details=details), 'INVALID_DISPATCH')
        self.err(self.store.record_manual_dispatch(self.author, d['document_ref'], 1, expected_sha256='0'*64,
            details=self.details(), request_key='wrong-hash'), 'DOCUMENT_CONTENT_CHANGED')
        self.assertEqual(1, len(self.get(d)['events']))

    def test_simultaneous_revisions_exactly_one_winner(self):
        d = self.create()[0]
        def revise(i):
            return self.store.revise_document(self.author, d['document_ref'], expected_version=1,
                edits={'text': {'note': str(i)}}, request_key='race-'+str(i))
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(revise, (1, 2)))
        self.assertEqual(1, sum(r['ok'] for r in results))
        self.err(next(r for r in results if not r['ok']), 'DOCUMENT_VERSION_CONFLICT')
        self.assertEqual(2, self.get(d)['latest_version'])

    def test_demand_change_blocks_new_effect_but_keeps_historical_export(self):
        d = self.create()[0]; old = self.get(d)
        self.interpreted['items'][0]['proposed_configuration_id'] = self.args()['schedule_context']['configuration_id']
        target = self.preflight.prepare(self.author, self.path, self.interpreted, data_version=1)['target']
        self.ok(self.workflow.save(self.author, target, demand_id=self.saved['demand_id'], expected_revision=1, request_key='new-demand'))
        self.err(self.review(d), 'DEMAND_TARGET_CHANGED')
        self.err(self.store.revise_document(self.author, d['document_ref'], expected_version=1,
            edits={'text': {'note': 'stale'}}, request_key='stale-edit'), 'DEMAND_TARGET_CHANGED')
        current = self.get(d); self.assertFalse(current['binding_current']); self.assertEqual(old['markdown'], current['markdown'])
        self.ok(self.store.export_document(self.author, d['document_ref'], 1, expected_sha256=d['content_sha256']))

    def test_public_version_change_checked_inside_transaction(self):
        from ontologics.production_planning.document_store import _DocumentStore
        d = self.create()[0]
        with patch.object(Service, '_version', return_value=99):
            self.err(self.review(d), 'DOCUMENT_DATA_VERSION_CHANGED')
        self.assertEqual([], self.get(d)['events'])

    def test_rollback_does_not_keep_partial_bundle_or_request(self):
        from ontologics.production_planning.document_store import _DocumentStore
        original = _DocumentStore._insert_version; count = [0]
        def injected(store, *args, **kwargs):
            result = original(store, *args, **kwargs); count[0] += 1
            if count[0] == 2: raise ValueError('test-only rollback')
            return result
        with patch.object(_DocumentStore, '_insert_version', injected):
            self.err(self.store.create_documents(self.author, calculation_arguments=self.args(), request_key='rollback'), 'INVALID_INPUT')
        self.assertEqual(3, len(self.create('rollback')))
        with self.host.request(self.author) as s:
            self.assertEqual(3, s._service.db.execute('SELECT count(*) AS n FROM pp_document_versions').fetchone()['n'])
            self.assertEqual(1, s._service.db.execute("SELECT count(*) AS n FROM requests WHERE request_key='rollback'").fetchone()['n'])
            self.assertTrue(s._service.db.execute("SELECT count(*) AS n FROM runs WHERE tool='pp_create_documents' AND status='failed'").fetchone()['n'])

    def test_no_changes_bad_fields_identity_failure_and_exact_export_guard(self):
        d = self.create()[0]
        self.err(self.store.revise_document(self.author, d['document_ref'], expected_version=1,
            edits={'text': {}}, request_key='no-changes'), 'DOCUMENT_NO_CHANGES')
        self.err(self.store.revise_document(self.author, d['document_ref'], expected_version=1,
            edits={'inputs': {'planned_input_quantity': 200}}, request_key='bad-field'), 'INVALID_INPUT')
        self.err(self.store.export_document(self.author, d['document_ref'], 1, expected_sha256='0'*64), 'DOCUMENT_CONTENT_CHANGED')
        with tempfile.TemporaryDirectory() as folder:
            self.err(self.store.export_document(self.author, d['document_ref'], 1, expected_sha256=d['content_sha256'],
                output_directory=folder, filename='../outside.md'), 'INVALID_EXPORT_PATH')
        self.identities.failure = True
        self.err(self.store.get_document(self.author, d['document_ref'], 1), 'IDENTITY_PROVIDER_UNAVAILABLE')

    def test_separate_example_persisted_export(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.production_planning_document_store'), 'Separate persistent example missing')
        from examples.production_planning_document_store import run_example
        with tempfile.TemporaryDirectory() as folder:
            result = run_example(folder)
            self.assertEqual(3, len(result['exports']))
            self.assertEqual(2, result['documents'][2]['document_version'])
            self.assertEqual(0, result['model_api_calls']); self.assertEqual(0, result['actual_transmissions'])
            self.assertEqual(0, result['demand_event_count'])
            for d, path in zip(result['documents'], result['exports']):
                self.assertEqual(d['markdown'], Path(path).read_text(encoding='utf-8'))

    def test_revoked_read_cannot_replay_review_or_create(self):
        d = self.create()[0]; self.ok(self.review(d))
        self.identities.users = [u if u.subject != 'reviewer' else UserIdentity('reviewer', frozenset({'review'})) for u in self.identities.users]
        self.err(self.review(d), 'FORBIDDEN')
        self.identities.users = [u if u.subject != 'author' else UserIdentity('author', frozenset({'draft'})) for u in self.identities.users]
        self.err(self.store.create_documents(self.author, calculation_arguments=self.args(), request_key='documents-create'), 'FORBIDDEN')

    def test_demand_confirmer_revocation_invalidates_document_binding(self):
        from tests.production_planning.test_demand_confirmation import SCOPES
        self.identities.users.append(UserIdentity('demand-reviewer', frozenset({'read','review'})))
        self.ok(self.workflow.confirm(DevelopmentContext('demand-reviewer'), self.saved['demand_id'], 1,
            expected_target_sha256=self.saved['target_sha256'], scopes=deepcopy(SCOPES),
            note='test-only demand confirmation', request_key='confirm-before-documents'))
        d = self.create()[0]; self.ok(self.review(d)); before = self.get(d)
        self.assertTrue(before['payload']['binding']['demand_source']['confirmation']['verified_scopes'])
        self.identities.users = [u if u.subject != 'demand-reviewer' else UserIdentity('demand-reviewer', frozenset({'read'})) for u in self.identities.users]
        current = self.get(d)
        self.assertFalse(current['binding_current']); self.assertEqual('review_invalidated', current['state']['review'])
        self.assertEqual(before['markdown'], current['markdown'])
        self.err(self.review(d, key='after-demand-revoked'), 'DEMAND_TARGET_CHANGED')
        self.err(self.dispatch(d), 'DEMAND_TARGET_CHANGED')

    def test_dispatch_cannot_predate_exact_document_version(self):
        d = self.create()[0]; self.ok(self.review(d))
        details = self.details(); details['sent_at'] = '2000-01-01T00:00:00Z'
        self.err(self.dispatch(d, details=details), 'INVALID_DISPATCH')

    def test_get_envelope_keeps_requested_data_version_when_public_changed(self):
        d = self.create()[0]
        with patch.object(Service, '_version', return_value=99):
            result = self.store.get_document(self.author, d['document_ref'], 1)
            self.assertTrue(result['ok']); self.assertFalse(result['data']['binding_current'])
            self.assertEqual(1, result['data_version'])

    def test_other_author_and_corrupted_stored_body_rejected(self):
        d = self.create()[0]
        self.identities.users.append(UserIdentity('other-author', frozenset({'read','draft'})))
        other = DevelopmentContext('other-author')
        self.err(self.store.revise_document(other, d['document_ref'], expected_version=1,
            edits={'text': {'note': 'foreign'}}, request_key='foreign'), 'DOCUMENT_FORBIDDEN')
        self.err(self.store.export_document(other, d['document_ref'], 1,
            expected_sha256=d['content_sha256']), 'DOCUMENT_FORBIDDEN')
        with self.host.request(self.author) as s:
            s._service.db.execute("UPDATE pp_document_versions SET markdown='test-only corruption' WHERE document_ref=%s", (d['document_ref'],))
        self.err(self.store.get_document(self.author, d['document_ref'], 1), 'DOCUMENT_INTEGRITY')

    def test_export_does_not_overwrite_file_or_follow_symlink(self):
        d = self.create()[0]
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            p = Path(folder)/'existing.md'; p.write_text('preserve',encoding='utf-8')
            symlink = Path(folder)/'link.md'; symlink.symlink_to(Path(outside)/'external.md')
            for name in ('existing.md','link.md'):
                self.err(self.store.export_document(self.author, d['document_ref'], 1,
                    expected_sha256=d['content_sha256'],output_directory=folder,filename=name), 'INVALID_EXPORT_PATH')
            self.assertEqual('preserve',p.read_text(encoding='utf-8'))
            self.assertFalse((Path(outside)/'external.md').exists())


del _Setup
