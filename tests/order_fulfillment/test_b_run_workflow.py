"""Reusable B workflow tests: fresh schemas and explicit fake HTTP only."""
import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from examples.order_fulfillment import b_run_workflow as workflow
from examples.order_fulfillment.b_run_isolation import SAFE_INPUTS
from ontologics import model
from ontologics.identity import UserIdentity
from ontologics.order_fulfillment.openai_provider import OpenAISettings
from tests.db import isolated_database


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b-workflow-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        manifest = {}
        for relative in SAFE_INPUTS:
            source = model.ROOT / relative
            target = self.inputs / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        (self.inputs / 'safe_input_manifest.json').write_text(json.dumps(manifest, sort_keys=True))

    def run_free(self, database, cases, name='run'):
        return workflow.run_campaign(database_url=database.url, input_root=self.inputs,
            output=self.root / name, cases=cases)

    def test_missing_live_backend_and_review_fail_before_output_or_database(self):
        with patch.object(workflow, 'Service') as service:
            with self.assertRaises(workflow.Blocked):
                workflow.run_campaign(database_url='not-opened', input_root=self.inputs,
                    output=self.root / 'blocked', cases=['cq02_normal'], offline=False)
            service.assert_not_called()
        self.assertFalse((self.root / 'blocked').exists())

    def test_settings_and_case_selection_fail_before_output(self):
        settings = OpenAISettings(api_key='offline-test-only', model=workflow.MODEL,
                                  timeout_seconds=30, max_output_tokens=1024)
        with self.assertRaises(ValueError):
            workflow.run_campaign(database_url='not-opened', input_root=self.inputs,
                output=self.root / 'limits', cases=['cq02_normal'],
                settings=replace(settings, max_output_tokens=2048))
        with self.assertRaises(ValueError):
            workflow.run_campaign(database_url='not-opened', input_root=self.inputs,
                output=self.root / 'selection', cases=['cq03_normal', 'cq02_normal'])
        self.assertFalse((self.root / 'limits').exists())
        self.assertFalse((self.root / 'selection').exists())

    def test_all_ten_use_real_host_providers_with_fake_http_and_drop_owned_schemas(self):
        original = model.ROOT
        with isolated_database() as database:
            summary = self.run_free(database, list(workflow.CASES))
            self.assertTrue(summary['passed'])
            self.assertEqual(summary['completed_representatives'], 10)
            self.assertEqual(summary['http_requests'], 40)
            self.assertEqual(summary['actual_api_attempts'], 0)
            self.assertFalse(summary['live_quality_verified'])
            for case in workflow.CASES:
                report = json.loads((self.root / 'run/reports' / (case + '.json')).read_text())
                self.assertTrue(all(v is True for v in report['checks'].values()), case)
                self.assertEqual(report['provider_submissions'][-1]['text'], report['runtime']['text'])
                self.assertEqual(report['http_requests'], 4)
                self.assertEqual(report['actual_api_attempts'], 0)
                allowed = json.loads(report['persisted_runtime']['config'])['allowed_tools']
                self.assertFalse(set(allowed) & {'review', 'apply'})
            for case in ('defects11', 'transport2h'):
                pair = json.loads((self.root / 'run' / ('cq06_' + case + '_approved_pair.json')).read_text())
                self.assertEqual((pair['before_version'], pair['after_version']), (2, 3))
                self.assertTrue(pair['development_fixture'])
            approval = json.loads((self.root / 'run/cq01_approval.json').read_text())
            self.assertTrue(approval['offline_simulated_review'])
            self.assertNotEqual(approval['request']['author'], approval['request']['reviewer'])
            cleanup = json.loads((self.root / 'run/cleanup.json').read_text())
            self.assertTrue(cleanup['dropped'])
            with psycopg.connect(database.url, autocommit=True) as db:
                rows = db.execute('SELECT nspname FROM pg_namespace WHERE nspname=ANY(%s)',
                                  (cleanup['owned_schemas'],)).fetchall()
                self.assertEqual(rows, [])
        self.assertEqual(model.ROOT, original)

    def test_timeout_stops_first_case_and_restores_roots(self):
        original = model.ROOT
        with isolated_database() as database, patch.object(
                workflow.OfflineStub, '__call__', side_effect=TimeoutError('offline injected timeout')) as fake:
            with self.assertRaises(workflow.Blocked):
                self.run_free(database, ['cq02_normal', 'cq03_normal'])
            self.assertEqual(fake.call_count, 1)
        stopped = json.loads((self.root / 'run/stopped.json').read_text())
        self.assertTrue(stopped['stopped'])
        events = [json.loads(line) for line in (self.root / 'run/http_budget.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'reserved' for e in events), 1)
        self.assertFalse((self.root / 'run/reports/cq03_normal.json').exists())
        self.assertTrue(json.loads((self.root / 'run/cleanup.json').read_text())['dropped'])
        self.assertEqual(model.ROOT, original)

    def test_intake_alone_remains_unapproved_and_standalone_promise_uses_fixture(self):
        with isolated_database() as database:
            summary = self.run_free(database, ['cq01_intake'], name='intake')
            self.assertTrue(summary['cq01_intake_alone_unapproved'])
            self.assertFalse((self.root / 'intake/cq01_approval.json').exists())
            report = json.loads((self.root / 'intake/reports/cq01_intake.json').read_text())
            self.assertEqual(report['after_business_rows']['review_events'], [])
            standalone = self.run_free(database, ['cq01_promise'], name='promise')
            self.assertEqual(standalone['actual_api_attempts'], 0)
            self.assertEqual(standalone['http_requests'], 4)

    def test_draft_callback_cannot_select_an_arbitrary_reviewer(self):
        audit, host = Mock(), Mock()
        audit.get_draft.return_value = dict(ok=True, data=dict(author='user:task11-author',
                                                             content_hash='a' * 64))
        reviewer = UserIdentity(workflow.REVIEWER_SUBJECT, frozenset({'read', 'review'}))
        callback = lambda request: dict(request, decision='approve', reviewer='user:forged', notes='검토')
        with self.assertRaises(workflow.Blocked):
            workflow._approve_intake(host, audit, dict(draft_id='d', draft_version=1), reviewer,
                offline=False, review_wait=callback, persist=lambda *args: None, input_root=self.inputs)
        host.apply_approved.assert_not_called()
        host.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
