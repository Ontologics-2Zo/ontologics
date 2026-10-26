"""Pinned OLIMEX file upload -> review -> registration over HTTP and PostgreSQL."""
import base64
import copy
import hashlib
import json
from urllib import parse
import uuid
from unittest.mock import patch

from ontologics.demo import CONFIG_L, CONFIG_L1, example_queries
from ontologics.identity import DevelopmentContext
from ontologics.model import ROOT
from tests.test_web import WebCase


class IntakeTests(WebCase):
    reserve_l1 = True

    @staticmethod
    def upload(revision='L1', *, filename=None):
        path = ROOT / f'data/sources/ESP32-PoE_Rev_{revision}-BOM-WROOM.ods'
        return {'filename': filename or path.name,
                'content_base64': base64.b64encode(path.read_bytes()).decode('ascii')}

    def intake(self, revision='L1', **kwargs):
        return self.ok(self.http(self.alice, '/api/sources/intake', self.upload(revision, **kwargs)))

    def source_draft(self, receipt, key='register-L1', **kwargs):
        return self.ok(self.http(self.alice, '/api/sources/draft', {
            'receipt_id': receipt['data']['receipt_id'], 'request_key': key, **kwargs}))

    def test_actual_source_intake_preview_review_registration_and_s1_reuse(self):
        self.failed(self.tool('configuration', {'configuration_id': CONFIG_L1}), 404, 'NOT_FOUND')
        before = {m: self.ok(self.tool('investigate', {'query': q, 'version': 1}))['data']
                  for m, q in example_queries().items()}
        # File labels do not decide the revision: these are L1 bytes under an L filename.
        receipt = self.intake(filename='ESP32-PoE_Rev_L-BOM-WROOM.ods')
        data = receipt['data']
        self.assertEqual('L1', data['revision'])
        self.assertEqual(CONFIG_L1, data['configuration_id'])
        self.assertFalse(data['existing_configuration'])
        self.assertEqual('user:dev-alice', data['received_by'])
        self.assertEqual(1, data['base_version'])
        self.assertEqual(74, len(data['rows']))
        self.assertEqual(128, sum(len(row['positions']) for row in data['rows']))
        self.assertEqual(12, data['unknown_count'])
        self.assertEqual(12, sum(row['population'] == 'unknown' for row in data['rows']))
        source = data['source']
        self.assertEqual('source:olimex-L1-wroom', source['id'])
        self.assertEqual('real', source['origin'])
        self.assertEqual(hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest(), source['sha256'])
        self.assertTrue(source['metadata_warning'])
        self.assertTrue(all(op['node']['origin'] == 'real' for op in data['operations']))
        self.assertFalse(any(op['node']['type'] in {'ProductUnit', 'Installation', 'HistoryCoverage'} for op in data['operations']))
        repeated = self.intake()
        self.assertEqual(data['receipt_id'], repeated['data']['receipt_id'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in repeated['warnings']])
        preview = self.ok(self.http(self.alice, '/api/sources/compare',
            {'receipt_id': data['receipt_id'], 'left_id': CONFIG_L}))
        self.assertTrue(preview['data']['is_preview'])
        self.assertIsNone(preview['data']['draft_id'])
        self.assertEqual({'R50', 'R51', 'R6', 'U2'}, {c['position'] for c in preview['data']['changes']})
        self.assertEqual(1, preview['data_version'])
        self.assertEqual([], self.ok(self.http(self.alice, '/api/workspace'))['data']['drafts'])
        proposal = self.source_draft(receipt)
        draft = proposal['data']
        self.assertTrue(draft['validation']['valid'])
        self.assertEqual('awaiting_review', draft['status'])
        self.assertEqual(1, proposal['data_version'])
        saved = self.ok(self.tool('get_draft', {'draft_id': draft['draft_id'], 'version': draft['draft_version']}))['data']
        self.assertEqual('user:dev-alice', saved['author'])
        self.assertEqual(data['operations'], saved['operations'])
        reviewed = self.review_body(draft)
        reviewed['note'] = '실제 OLIMEX L1 WROOM 원본 행·개정 차이·미확인 실장 표기를 확인한 개발 검토'
        applied = self.ok(self.http(self.bob, '/api/review', reviewed))
        self.assertEqual('applied', applied['data']['application']['data']['status'])
        version = applied['data_version']
        self.assertEqual(2, version)
        configuration = self.ok(self.tool('configuration', {'configuration_id': CONFIG_L1, 'version': version}))
        expected = [n for n in json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
                    if n.get('configuration_id') == CONFIG_L1]
        self.assertEqual({n['id']: n for n in expected}, {n['id']: n for n in configuration['data']['items']})
        self.assertTrue(configuration['evidence'])
        for mode, query in example_queries().items():
            self.assertEqual(before[mode], self.ok(self.tool('investigate', {'query': query, 'version': version}))['data'])
        workspace = self.ok(self.http(self.bob, '/api/workspace'))['data']
        self.assertEqual([(draft['draft_id'], 'applied', 2)],
                         [(d['draft_id'], d['status'], d['applied_data_version']) for d in workspace['drafts']])

    def test_unverified_bytes_permissions_and_receipt_owner_fail_without_confirmed_writes(self):
        valid = self.upload()
        self.failed(self.http(self.alice, '/api/sources/intake',
            {**valid, 'content_base64': base64.b64encode(b'not the pinned ODS').decode()}), 400, 'UNSUPPORTED_SOURCE')
        self.failed(self.http(self.alice, '/api/sources/intake', {**valid, 'content_base64': '%%%'}), 400, 'INVALID_INPUT')
        self.failed(self.http(self.alice, '/api/sources/intake', {**valid, 'actor': 'user:dev-bob'}), 400, 'INVALID_INPUT')
        self.failed(self.http(self.reader, '/api/sources/intake', valid), 403, 'FORBIDDEN')
        receipt = self.intake()['data']['receipt_id']
        self.failed(self.http(self.bob, '/api/sources/compare', {'receipt_id': receipt, 'left_id': CONFIG_L}),
                    403, 'RECEIPT_OWNER_REQUIRED')
        self.failed(self.http(self.bob, '/api/sources/draft', {'receipt_id': receipt, 'request_key': 'not-author'}),
                    403, 'FORBIDDEN')
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])
        self.assertEqual([], self.ok(self.http(self.bob, '/api/workspace'))['data']['drafts'])

    def test_receipt_and_draft_persist_across_hosts_but_stale_new_work_requires_reintake(self):
        receipt = self.intake()
        # A fresh HTTP host has no in-memory upload state; the same person can resume.
        self.alice = self.start('alice')
        proposal = self.source_draft(receipt)
        draft = proposal['data']
        resumed = self.source_draft(receipt)
        self.assertEqual(draft['draft_id'], resumed['data']['draft_id'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in resumed['warnings']])
        row = self.ok(self.http(self.bob, '/api/workspace'))['data']['drafts'][0]
        self.assertEqual(draft['draft_id'], row['draft_id'])
        self.assertEqual('awaiting_review', row['status'])
        self.ok(self.http(self.bob, '/api/review', self.review_body(draft)))
        # A lost successful draft response remains recoverable after its base changed.
        replay = self.source_draft(receipt)
        self.assertEqual(draft['draft_id'], replay['data']['draft_id'])
        self.assertEqual(1, replay['data_version'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['warnings']])
        stale_body = {'receipt_id': receipt['data']['receipt_id'], 'request_key': 'new-request'}
        self.failed(self.http(self.alice, '/api/sources/draft', stale_body), 409, 'STALE_RECEIPT')
        self.failed(self.http(self.alice, '/api/sources/compare',
            {'receipt_id': receipt['data']['receipt_id'], 'left_id': CONFIG_L}), 409, 'STALE_RECEIPT')
        fresh = self.intake()
        self.assertNotEqual(receipt['data']['receipt_id'], fresh['data']['receipt_id'])
        self.assertTrue(fresh['data']['existing_configuration'])
        self.assertEqual([], fresh['data']['operations'])
        self.failed(self.http(self.alice, '/api/sources/draft',
            {'receipt_id': fresh['data']['receipt_id'], 'request_key': 'duplicate-registration'}), 409, 'NO_CHANGES')
        self.assertEqual(2, self.ok(self.http(self.bob, '/api/workspace'))['data']['version'])


class HistoryIntakeTests(WebCase):
    def records_document(self):
        return {'origin': 'synthetic', 'authored_at': '2026-10-26T10:00:00+09:00',
            'notice': 'New user-submitted fictional records for a test: ' + uuid.uuid4().hex,
            'records': [{k: v for k, v in op['node'].items() if k not in {'origin', 'evidence'}}
                        for op in self.operations]}

    def history_upload(self, document):
        raw = json.dumps(document, ensure_ascii=False).encode()
        checksum = hashlib.sha256(raw).hexdigest()
        path = ROOT / 'data/receipts' / (checksum + '.json')
        if not path.exists():
            def remove_created_source():
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
                    path.unlink()
            self.addCleanup(remove_created_source)
        return {'filename': 'submitted-history.json', 'content_base64': base64.b64encode(raw).decode()}, raw, path

    def test_new_history_evidence_requires_review_and_preserves_exact_source_and_AB_versions(self):
        body, raw, path = self.history_upload(self.records_document())
        with patch('ontologics.receipts.tempfile.NamedTemporaryFile', side_effect=OSError('Injected source I/O failure')):
            self.failed(self.http(self.alice, '/api/history/intake', body), 503, 'SOURCE_STORAGE_ERROR')
        self.assertFalse(path.exists())
        receipt = self.ok(self.http(self.alice, '/api/history/intake', body))
        with self.host.request(DevelopmentContext('dev-alice')) as session:
            runs = session._service.db.execute("SELECT status,error_code FROM runs WHERE tool='history_intake' ORDER BY sequence").fetchall()
        self.assertEqual([('failed', 'SOURCE_STORAGE_ERROR'), ('succeeded', None)],
                         [(run['status'], run['error_code']) for run in runs])
        data = receipt['data']
        self.assertTrue(data['validation']['valid'])
        self.assertEqual('synthetic', data['source']['origin'])
        self.assertEqual(raw, path.read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), data['source']['sha256'])
        self.assertEqual('user:dev-alice', data['received_by'])
        evidence = [op['node'] for op in data['operations'] if op['node']['type'] == 'Evidence']
        self.assertEqual({'/records/0', '/records/1'}, {node['locator'] for node in evidence})
        self.assertTrue(all(node['source_id'] == data['source']['id'] for node in evidence))
        self.assertEqual(1, receipt['data_version'])
        proposal = self.ok(self.http(self.alice, '/api/sources/draft', {
            'receipt_id': data['receipt_id'], 'request_key': 'history-draft'}))['data']
        self.assertTrue(proposal['validation']['valid'])
        # The reviewer can inspect the newly submitted source in this exact draft,
        # while the confirmed graph still does not contain that document.
        draft_path = '/api/draft-source?' + parse.urlencode({'source_id': data['source']['id'],
            'draft_id': proposal['draft_id'], 'version': proposal['draft_version']})
        status, content, headers = self.http(self.bob, draft_path)
        self.assertEqual(200, status)
        self.assertEqual(raw, content)
        self.assertIn('attachment', headers['Content-Disposition'])
        source_path = '/api/source?' + parse.urlencode({'source_id': data['source']['id'], 'version': 1})
        self.failed(self.http(self.bob, source_path), 404, 'NOT_FOUND')
        applied = self.ok(self.http(self.bob, '/api/review', self.review_body(proposal)))
        after = applied['data_version']
        self.assertEqual(2, after)
        confirmed_path = '/api/source?' + parse.urlencode({'source_id': data['source']['id'], 'version': after})
        self.assertEqual(raw, self.http(self.alice, confirmed_path)[1])
        for mode, query in example_queries().items():
            comparison = self.ok(self.tool('compare_investigation', {
                'query': query, 'before_version': 1, 'after_version': after}))
            self.assertEqual(query, comparison['data']['query'])
            self.assertEqual('needs_confirmation', comparison['data']['before']['results'][0]['status'])
            self.assertEqual('excluded' if mode == 'A' else 'included', comparison['data']['after']['results'][0]['status'])
            self.assertEqual('needs_confirmation', comparison['data']['after']['results'][2]['status'])
            self.assertTrue({node['id'] for node in evidence} <= set(comparison['evidence']))
            self.assertNotIn('evidence:demo:supplement', comparison['evidence'])

    def test_real_or_fault_fields_are_rejected_and_invalid_graph_cannot_be_approved(self):
        valid = self.records_document()
        real = {**valid, 'origin': 'real'}
        fault = copy.deepcopy(valid)
        fault['records'][0]['failure_cause'] = 'unsupported defect conclusion'
        for document in (real, fault):
            body, _, path = self.history_upload(document)
            self.failed(self.http(self.alice, '/api/history/intake', body), 400, 'INVALID_HISTORY_SOURCE')
            self.assertFalse(path.exists())
        invalid = copy.deepcopy(valid)
        invalid['records'][0]['lot_id'] = 'lot:not-registered'
        body, _, _ = self.history_upload(invalid)
        receipt = self.ok(self.http(self.alice, '/api/history/intake', body))
        self.assertFalse(receipt['data']['validation']['valid'])
        draft = self.ok(self.http(self.alice, '/api/sources/draft', {
            'receipt_id': receipt['data']['receipt_id'], 'request_key': 'invalid-history-draft'}))['data']
        self.assertEqual('needs_changes', draft['status'])
        self.assertFalse(draft['validation']['valid'])
        self.failed(self.http(self.bob, '/api/review', self.review_body(draft)), 400, 'VALIDATION_FAILED')
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])
