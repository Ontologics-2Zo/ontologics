"""Common workflow integration tests using the pinned real OLIMEX catalog."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from ontologics.service import AgentGateway, Service


ROOT = Path(__file__).resolve().parents[1]
LEFT = 'config:esp32-poe:L:WROOM'
RIGHT = 'config:esp32-poe:L1:WROOM'
PRINCIPALS = {
    'developer': {'kind': 'human', 'roles': ['seed', 'read', 'audit']},
    'author': {'kind': 'human', 'roles': ['read', 'draft', 'review']},
    'reviewer': {'kind': 'human', 'roles': ['read', 'review', 'apply', 'audit']},
    'second_reviewer': {'kind': 'human', 'roles': ['read', 'review', 'apply']},
    'agent': {'kind': 'agent', 'roles': ['read', 'draft']},
    'misconfigured_agent': {'kind': 'agent', 'roles': ['read', 'draft', 'review']},
    'reader': {'kind': 'human', 'roles': ['read']},
}


class ServiceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        cls.registration_nodes = [n for n in cls.catalog if n['id'] == RIGHT or
                                  (n['type'] == 'BOMItem' and n['configuration_id'] == RIGHT)]
        excluded = {n['id'] for n in cls.registration_nodes}
        cls.seed_nodes = [n for n in cls.catalog if n['id'] not in excluded]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / 'service.sqlite'
        self.service = Service(self.db_path, principals=PRINCIPALS)
        self.seed = self.service.bootstrap('developer', self.seed_nodes, request_key='seed')
        self.assertTrue(self.seed['ok'], self.seed)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def draft(self, actor='author', key='registration', **kwargs):
        operations = kwargs.pop('operations', [{'op': 'put', 'node': n} for n in self.registration_nodes])
        result = self.service.prepare_draft(actor, operations, request_key=key, **kwargs)
        self.assertTrue(result['ok'], result)
        return result

    def approve(self, draft, actor='reviewer', key='review'):
        data = draft['data']
        result = self.service.review(actor, data['draft_id'], data['draft_version'],
                                     decision='approve', note='원문·변경점·검증 결과 확인', request_key=key)
        self.assertTrue(result['ok'], result)
        return result

    def apply(self, draft, key='apply'):
        data = draft['data']
        return self.service.apply('reviewer', data['draft_id'], data['draft_version'], request_key=key)

    def error_is(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def test_real_catalog_registration_requires_review_then_is_queryable(self):
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')
        draft = self.draft()
        self.assertEqual('awaiting_review', draft['data']['status'])
        self.assertEqual(75, len(draft['data']['changes']))
        self.error_is(self.apply(draft), 'APPROVAL_REQUIRED')
        self.assertEqual(self.seed['data_version'], self.service.lookup('reader')['data_version'])
        self.approve(draft)
        result = self.apply(draft)
        self.assertTrue(result['ok'], result)
        registered = self.service.configuration('reader', RIGHT)
        self.assertTrue(registered['ok'], registered)
        self.assertEqual(74, len(registered['data']['items']))
        self.assertTrue(registered['evidence'])
        self.assertEqual(self.seed['data_version'] + 1, registered['data_version'])

    def test_s2_candidate_comparison_before_approval_preserves_snapshot_and_rejects_stale_base(self):
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')
        self.error_is(self.service.compare_configurations('reader', LEFT, RIGHT), 'NOT_FOUND')
        draft = self.draft()
        args = {'left_id': LEFT, 'right_id': RIGHT, 'draft_id': draft['data']['draft_id'], 'draft_version': 1}
        preview = AgentGateway(self.service, 'agent').call('compare_configurations', args)
        self.assertTrue(preview['ok'], preview)
        self.assertTrue(preview['data']['is_preview'])
        self.assertEqual(draft['data']['draft_id'], preview['data']['draft_id'])
        self.assertEqual(['R50', 'R51', 'R6', 'U2'], [c['position'] for c in preview['data']['changes']])
        self.assertIn('UNAPPLIED_PREVIEW', [w['code'] for w in preview['warnings']])
        self.assertTrue(preview['evidence'])
        self.assertEqual(self.seed['data_version'], preview['data_version'])
        self.assertEqual(self.seed['data_version'], self.service.lookup('reader')['data_version'])
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')
        self.assertEqual('awaiting_review', self.service.get_draft('reader', draft['data']['draft_id'], 1)['data']['status'])
        ambiguous = AgentGateway(self.service, 'agent').call('compare_configurations', dict(args, version=self.seed['data_version']))
        self.error_is(ambiguous, 'INVALID_INPUT')
        left = copy.deepcopy(next(n for n in self.catalog if n['id'] == LEFT))
        left['name'] += ' (changed after proposal)'
        other = self.draft(key='new-base', operations=[{'op': 'put', 'node': left}])
        self.approve(other, key='new-base-review')
        self.assertTrue(self.apply(other, key='new-base-apply')['ok'])
        stale = AgentGateway(self.service, 'agent').call('compare_configurations', args)
        self.error_is(stale, 'STALE_BASE')

    def test_self_approval_is_blocked_even_with_review_role(self):
        draft = self.draft()
        result = self.service.review('author', draft['data']['draft_id'], 1,
            decision='approve', note='self review', request_key='self-review')
        self.error_is(result, 'SELF_APPROVAL_FORBIDDEN')

    def test_agent_cannot_spoof_actor_or_call_privileged_tools(self):
        gateway = AgentGateway(self.service, 'agent')
        self.error_is(gateway.call('review', {}), 'TOOL_NOT_ALLOWED')
        self.error_is(gateway.call('apply', {}), 'TOOL_NOT_ALLOWED')
        self.error_is(gateway.call('bootstrap', {}), 'TOOL_NOT_ALLOWED')
        self.error_is(gateway.call('lookup', {'actor': 'reviewer'}), 'INVALID_TOOL_ARGUMENTS')
        self.error_is(gateway.call('lookup', {'principals': {}}), 'INVALID_TOOL_ARGUMENTS')
        self.error_is(gateway.call('configuration', {'unknown': True}), 'INVALID_TOOL_ARGUMENTS')
        result = gateway.call('prepare_draft', {
            'operations': [{'op': 'put', 'node': n} for n in self.registration_nodes], 'request_key': 'agent-draft'})
        self.assertTrue(result['ok'], result)
        self.assertEqual('agent', self.service.get_draft('reader', result['data']['draft_id'], 1)['data']['author'])

    def test_agent_kind_cannot_review_even_if_host_erroneously_assigns_role(self):
        draft = self.draft()
        result = self.service.review('misconfigured_agent', draft['data']['draft_id'], 1,
            decision='approve', note='not human', request_key='agent-review')
        self.error_is(result, 'HUMAN_REVIEW_REQUIRED')

    def test_read_permission_is_not_draft_or_review_permission(self):
        ops = [{'op': 'put', 'node': n} for n in self.registration_nodes]
        self.error_is(self.service.prepare_draft('reader', ops, request_key='forbidden'), 'FORBIDDEN')
        draft = self.draft()
        self.error_is(self.service.review('reader', draft['data']['draft_id'], 1,
            decision='approve', note='no role', request_key='review'), 'FORBIDDEN')

    def test_revised_draft_needs_new_approval_and_old_version_is_immutable(self):
        original = self.draft()
        self.approve(original)
        changed = copy.deepcopy(self.registration_nodes)
        next(n for n in changed if n['id'] == RIGHT)['name'] += ' (reviewed label)'
        revised = self.draft(key='revise', draft_id=original['data']['draft_id'], expected_version=1,
            operations=[{'op': 'put', 'node': n} for n in changed])
        self.assertEqual(2, revised['data']['draft_version'])
        self.assertEqual('awaiting_review', revised['data']['status'])
        old = self.service.get_draft('reader', original['data']['draft_id'], 1)['data']
        self.assertEqual('superseded', old['status'])
        self.assertEqual(original['data']['content_hash'], old['content_hash'])
        self.error_is(self.apply(original, key='old-apply'), 'SUPERSEDED_DRAFT')
        self.error_is(self.apply(revised, key='new-apply'), 'APPROVAL_REQUIRED')
        self.approve(revised, key='review-revised')
        self.assertTrue(self.apply(revised, key='new-apply')['ok'])

    def test_other_writer_cannot_revise_an_authors_draft(self):
        original = self.draft()
        result = self.service.prepare_draft('agent', [{'op': 'put', 'node': n} for n in self.registration_nodes],
            request_key='hijack', draft_id=original['data']['draft_id'], expected_version=1)
        self.error_is(result, 'DRAFT_OWNER_REQUIRED')

    def test_new_data_invalidates_prior_approval(self):
        first = self.draft()
        self.approve(first)
        left = copy.deepcopy(next(n for n in self.catalog if n['id'] == LEFT))
        left['name'] += ' (revised label)'
        second = self.draft(key='other-change', operations=[{'op': 'put', 'node': left}])
        self.approve(second, key='other-review')
        self.assertTrue(self.apply(second, key='other-apply')['ok'])
        self.error_is(self.apply(first), 'STALE_BASE')
        validation = self.service.validate_draft('reader', first['data']['draft_id'], 1)
        self.assertFalse(validation['data']['validation']['valid'])
        self.assertIn('STALE_BASE', [e['code'] for e in validation['data']['validation']['errors']])
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')

    def test_idempotent_replay_and_key_content_conflict(self):
        first = self.draft()
        replay = self.draft()
        self.assertEqual(first['data']['draft_id'], replay['data']['draft_id'])
        self.assertEqual(first['data']['content_hash'], replay['data']['content_hash'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['warnings']])
        changed = copy.deepcopy(self.registration_nodes)
        next(n for n in changed if n['id'] == RIGHT)['name'] += ' changed'
        result = self.service.prepare_draft('author', [{'op': 'put', 'node': n} for n in changed], request_key='registration')
        self.error_is(result, 'IDEMPOTENCY_CONFLICT')
        self.approve(first)
        applied = self.apply(first)
        self.assertTrue(applied['ok'], applied)
        repeated = self.apply(first)
        self.assertEqual(applied['data_version'], repeated['data_version'])
        fresh_key = self.apply(first, key='different-apply-key')
        self.assertTrue(fresh_key['ok'], fresh_key)
        self.assertEqual(applied['data_version'], fresh_key['data_version'])
        self.assertIn('ALREADY_APPLIED', [w['code'] for w in fresh_key['warnings']])

    def test_mutations_require_non_null_idempotency_keys(self):
        ops = [{'op': 'put', 'node': n} for n in self.registration_nodes]
        self.error_is(self.service.prepare_draft('author', ops, request_key=None), 'INVALID_REQUEST_KEY')
        self.assertEqual(0, self.service.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])
        draft = self.draft()
        self.approve(draft)
        result = self.service.apply('reviewer', draft['data']['draft_id'], 1, request_key=None)
        self.error_is(result, 'INVALID_REQUEST_KEY')
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')
        self.assertEqual(self.seed['data_version'], self.service.lookup('reader')['data_version'])

    def test_non_json_nan_is_recorded_as_failure_without_partial_changes(self):
        invalid = copy.deepcopy(self.registration_nodes)
        next(n for n in invalid if n['type'] == 'BOMItem')['quantity'] = float('nan')
        result = self.service.prepare_draft('author', [{'op': 'put', 'node': n} for n in invalid], request_key='nan-input')
        self.error_is(result, 'INVALID_INPUT')
        self.assertEqual(0, self.service.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])
        self.assertEqual(self.seed['data_version'], self.service.lookup('reader')['data_version'])
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')
        audit = self.service.run_history('developer')['data']
        row = next(r for r in audit if r['id'] == result['run_id'])
        self.assertEqual('failed', row['status'])
        self.assertEqual('INVALID_INPUT', row['error_code'])

    def test_boolean_version_is_not_accepted_as_integer_version_one(self):
        self.error_is(self.service.lookup('reader', version=True), 'INVALID_VERSION')
        self.error_is(self.service.configuration('reader', LEFT, version=True), 'INVALID_VERSION')
        self.error_is(self.service.export_graph('reader', version=True), 'INVALID_VERSION')
        draft = self.draft()
        did = draft['data']['draft_id']
        self.error_is(self.service.get_draft('reader', did, True), 'INVALID_VERSION')
        self.error_is(self.service.validate_draft('reader', did, True), 'INVALID_VERSION')
        self.error_is(self.service.review('reviewer', did, True, decision='approve',
            note='Boolean must not name version one', request_key='boolean-review'), 'INVALID_VERSION')
        self.approve(draft)
        self.error_is(self.service.apply('reviewer', did, True, request_key='boolean-apply'), 'INVALID_VERSION')
        self.error_is(self.service.prepare_draft('author', [{'op': 'put', 'node': n} for n in self.registration_nodes],
            request_key='boolean-revise', draft_id=did, expected_version=True), 'INVALID_VERSION')

    def test_package_only_difference_is_reported(self):
        # Deliberate in-memory mutation tests the comparator; no source fixture is changed.
        right_item = next(n for n in self.registration_nodes if n['type'] == 'BOMItem' and 'U4' in n['positions'])
        component = copy.deepcopy(next(n for n in self.catalog if n['id'] == right_item['component_id']))
        component['package'] = 'TEST-ONLY-PACKAGE-DIFFERENCE'
        ops = [{'op': 'put', 'node': n} for n in [*self.registration_nodes, component]]
        draft = self.draft(operations=ops)
        self.approve(draft)
        self.assertTrue(self.apply(draft)['ok'])
        comparison = self.service.compare_configurations('reader', LEFT, RIGHT)
        self.assertTrue(comparison['ok'], comparison)
        change = next(c for c in comparison['data']['changes'] if c['position'] == 'U4')
        self.assertEqual(change['before']['component'], change['after']['component'])
        self.assertEqual(change['before']['raw_value'], change['after']['raw_value'])
        self.assertEqual(change['before']['population'], change['after']['population'])
        self.assertNotEqual(change['before']['package'], change['after']['package'])
        self.assertEqual('TEST-ONLY-PACKAGE-DIFFERENCE', change['after']['package'])

    def test_identical_registered_content_is_not_registered_twice(self):
        draft = self.draft()
        self.approve(draft)
        self.assertTrue(self.apply(draft)['ok'])
        result = self.service.prepare_draft('author', [{'op': 'put', 'node': n} for n in self.registration_nodes],
            request_key='same-document-new-key')
        self.error_is(result, 'NO_CHANGES')

    def test_invalid_bundle_does_not_partially_register(self):
        invalid = copy.deepcopy(self.registration_nodes)
        next(n for n in invalid if n['type'] == 'BOMItem')['component_id'] = 'component:does-not-exist'
        draft = self.draft(operations=[{'op': 'put', 'node': n} for n in invalid])
        self.assertEqual('needs_changes', draft['data']['status'])
        self.assertFalse(draft['data']['validation']['valid'])
        self.error_is(self.service.review('reviewer', draft['data']['draft_id'], 1,
            decision='approve', note='invalid cannot pass', request_key='invalid-review'), 'VALIDATION_FAILED')
        self.error_is(self.apply(draft), 'APPROVAL_REQUIRED')
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')
        self.assertEqual(self.seed['data_version'], self.service.lookup('reader')['data_version'])

    def test_failure_rolls_back_snapshot_and_retry_applies_once(self):
        draft = self.draft()
        self.approve(draft)
        self.service.db.execute("""CREATE TRIGGER simulate_failure BEFORE UPDATE OF status ON drafts
            WHEN NEW.status = 'applied' BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END""")
        failed = self.apply(draft)
        self.error_is(failed, 'STORAGE_ERROR')
        self.assertEqual(self.seed['data_version'], self.service.lookup('reader')['data_version'])
        self.assertEqual('approved', self.service.get_draft('reader', draft['data']['draft_id'], 1)['data']['status'])
        self.error_is(self.service.configuration('reader', RIGHT), 'NOT_FOUND')
        self.service.db.execute('DROP TRIGGER simulate_failure')
        retry = self.apply(draft)
        self.assertTrue(retry['ok'], retry)
        self.assertEqual(self.seed['data_version'] + 1, retry['data_version'])
        self.assertEqual(retry['data_version'], self.apply(draft)['data_version'])
        runs = self.service.run_history('developer')['data']
        self.assertTrue(any(r['tool'] == 'apply' and r['status'] == 'failed' and r['error_code'] == 'STORAGE_ERROR' for r in runs))

    def test_persistent_versions_and_replay_survive_restart(self):
        draft = self.draft()
        self.approve(draft)
        applied = self.apply(draft)
        self.assertTrue(applied['ok'], applied)
        self.service.close()
        self.service = Service(self.db_path, principals=PRINCIPALS)
        self.assertTrue(self.service.configuration('reader', RIGHT)['ok'])
        self.error_is(self.service.configuration('reader', RIGHT, version=self.seed['data_version']), 'NOT_FOUND')
        old = self.service.configuration('reader', LEFT, version=self.seed['data_version'])
        self.assertTrue(old['ok'], old)
        self.assertEqual(self.seed['data_version'], old['data_version'])
        self.assertEqual(applied['data_version'], self.apply(draft)['data_version'])

    def test_sources_and_evidence_cannot_be_overwritten(self):
        source = copy.deepcopy(next(n for n in self.catalog if n['type'] == 'SourceDocument'))
        source['url'] += '#changed'
        self.error_is(self.service.prepare_draft('author', [{'op': 'put', 'node': source}], request_key='source-rewrite'), 'IMMUTABLE_SOURCE')
        evidence = copy.deepcopy(next(n for n in self.catalog if n['type'] == 'Evidence'))
        evidence['locator'] = 'different row'
        self.error_is(self.service.prepare_draft('author', [{'op': 'put', 'node': evidence}], request_key='evidence-rewrite'), 'IMMUTABLE_SOURCE')

    def test_review_request_and_rejection_are_enforced(self):
        draft = self.draft()
        result = self.service.review('reviewer', draft['data']['draft_id'], 1,
            decision='request_changes', note='추가 자료 확인 필요', request_key='changes')
        self.assertTrue(result['ok'], result)
        self.assertEqual('needs_changes', result['data']['status'])
        self.error_is(self.apply(draft), 'APPROVAL_REQUIRED')
        result = self.service.review('reviewer', draft['data']['draft_id'], 1,
            decision='reject', note='요청 취소', request_key='reject')
        self.assertTrue(result['ok'], result)
        self.assertEqual('rejected', result['data']['status'])
        self.error_is(self.service.prepare_draft('author', [{'op': 'put', 'node': n} for n in self.registration_nodes],
            request_key='reopen', draft_id=draft['data']['draft_id'], expected_version=1), 'DRAFT_CLOSED')

    def test_review_history_retains_every_decision_and_prior_version(self):
        draft = self.draft()
        did = draft['data']['draft_id']
        request = self.service.review('reviewer', did, 1, decision='request_changes',
            note='첫 검토: 원본의 개정 표시를 재확인', request_key='history-request')
        self.assertTrue(request['ok'], request)
        approved = self.service.review('second_reviewer', did, 1, decision='approve',
            note='재검토: PDF 표제와 U6 행으로 확인', request_key='history-approval')
        self.assertTrue(approved['ok'], approved)
        history = self.service.get_draft('reader', did, 1)['data']['review_history']
        self.assertEqual(['request_changes', 'approve'], [event['decision'] for event in history])
        self.assertEqual(['reviewer', 'second_reviewer'], [event['actor'] for event in history])
        self.assertEqual(['첫 검토: 원본의 개정 표시를 재확인', '재검토: PDF 표제와 U6 행으로 확인'],
                         [event['note'] for event in history])
        self.assertTrue(all(event['content_hash'] == draft['data']['content_hash'] for event in history))
        changed = copy.deepcopy(self.registration_nodes)
        next(n for n in changed if n['id'] == RIGHT)['name'] += ' revised'
        revised = self.draft(key='history-revise', draft_id=did, expected_version=1,
            operations=[{'op': 'put', 'node': n} for n in changed])
        self.assertEqual(2, revised['data']['draft_version'])
        self.assertEqual(history, self.service.get_draft('reader', did, 1)['data']['review_history'])
        self.assertEqual([], self.service.get_draft('reader', did, 2)['data']['review_history'])

    def test_s1_preview_remains_unapplied_then_approved_requery_changes_only_data(self):
        from ontologics.demo import fixture_nodes, example_queries
        fictional, supplement = fixture_nodes()
        self.service.close()
        self.service = Service(':memory:', principals=PRINCIPALS)
        seeded = self.service.bootstrap('developer', self.catalog + fictional, request_key='operating-seed')
        self.assertTrue(seeded['ok'], seeded)
        queries = example_queries()
        before = {mode: self.service.investigate('reader', query) for mode, query in queries.items()}
        for result in before.values():
            self.assertTrue(result['ok'], result)
            self.assertEqual('needs_confirmation', result['data']['results'][0]['status'])
        draft = self.draft(operations=supplement)
        gateway = AgentGateway(self.service, 'agent')
        for mode, expected in [('A', 'excluded'), ('B', 'included')]:
            preview = gateway.call('preview_investigation', {'draft_id': draft['data']['draft_id'],
                'version': 1, 'query': queries[mode]})
            self.assertTrue(preview['ok'], preview)
            self.assertTrue(preview['data']['is_preview'])
            self.assertEqual(expected, preview['data']['result']['results'][0]['status'])
            self.assertEqual(seeded['data_version'], preview['data_version'])
            self.assertIn('UNAPPLIED_PREVIEW', [w['code'] for w in preview['warnings']])
            current = self.service.investigate('reader', queries[mode])
            self.assertEqual('needs_confirmation', current['data']['results'][0]['status'])
        self.approve(draft)
        applied = self.apply(draft)
        self.assertTrue(applied['ok'], applied)
        for mode, expected in [('A', 'excluded'), ('B', 'included')]:
            comparison = self.service.compare_investigation('reader', queries[mode],
                seeded['data_version'], applied['data_version'])
            self.assertTrue(comparison['ok'], comparison)
            data = comparison['data']
            self.assertEqual(data['before']['query'], data['after']['query'])
            self.assertEqual('needs_confirmation', data['before']['results'][0]['status'])
            self.assertEqual(expected, data['after']['results'][0]['status'])
            per_unit = {row['unit_id']: row['status'] for row in data['after']['results']}
            self.assertEqual('needs_confirmation', per_unit['unit:U106'])
            self.assertEqual('excluded', per_unit['unit:U107'])
            self.assertIn('evidence:demo:supplement', data['after']['results'][0]['evidence'])

    def test_temporal_conflict_is_forwarded_to_common_envelope(self):
        from ontologics.demo import fixture_nodes, example_queries
        fictional, _ = fixture_nodes()
        # Test-only conflict flag; the checked-in operating fixture stays unchanged.
        installation = next(n for n in fictional if n['type'] == 'Installation' and n['unit_id'] == 'unit:U105')
        installation['conflict'] = True
        self.service.close()
        self.service = Service(':memory:', principals=PRINCIPALS)
        seeded = self.service.bootstrap('developer', self.catalog + fictional, request_key='conflict-seed')
        self.assertTrue(seeded['ok'], seeded)
        result = self.service.investigate('reader', example_queries()['A'])
        self.assertTrue(result['ok'], result)
        row = next(n for n in result['data']['results'] if n['unit_id'] == 'unit:U105')
        self.assertEqual('needs_confirmation', row['status'])
        conflict = next(c for c in result['conflicts'] if c['unit_id'] == 'unit:U105')
        self.assertEqual('CONFLICTING_INSTALLATION', conflict['code'])
        self.assertIn(installation['id'], conflict['installation_ids'])
        self.assertTrue(conflict['evidence'])

    def test_failures_have_run_ids_and_persistent_audit_rows(self):
        denied = self.service.lookup('unknown-identity')
        self.error_is(denied, 'FORBIDDEN')
        self.assertTrue(denied['run_id'])
        audit = self.service.run_history('developer')
        self.assertTrue(audit['ok'], audit)
        row = next(r for r in audit['data'] if r['id'] == denied['run_id'])
        self.assertEqual('failed', row['status'])
        self.assertEqual('FORBIDDEN', row['error_code'])
        self.assertTrue(row['finished_at'])


if __name__ == '__main__':
    unittest.main()
