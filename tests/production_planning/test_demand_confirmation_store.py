"""Real PG persistence over the existing Service execution boundary."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.production_planning_contract_intake import ROOT, reference_interpretation
from ontologics.production_planning.host import PlanningHost as CommonHost
from ontologics.identity import DevelopmentContext, UserIdentity
from ontologics.production_planning.contract_intake import extract_contract_text
from ontologics.production_planning.demand_confirmation import DemandConfirmationWorkflow
from ontologics.service import DomainError
from ontologics.production_planning.persistence import PlanningService as Service
from tests.production_planning.test_demand_confirmation import MutableIdentities, AUTHOR, REVIEWER, READER, SCOPES

if importlib.util.find_spec('ontologics.production_planning.demand_confirmation_store'):
    from ontologics.production_planning.demand_confirmation_store import PersistentDemandConfirmationWorkflow, DemandConfirmationStore
else:
    PersistentDemandConfirmationWorkflow = DemandConfirmationStore = None


class StoreAvailabilityTests(unittest.TestCase):
    def test_persistent_workflow_is_implemented(self):
        self.assertIsNotNone(PersistentDemandConfirmationWorkflow, 'Persistent confirmation workflow is missing')


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit isolated TEST_DATABASE_URL required')
class PersistedDemandTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PersistentDemandConfirmationWorkflow, 'Persistent confirmation workflow is missing')
        from tests.db import database_for_test
        self.database = database_for_test(self)
        self.identities = MutableIdentities()
        self.host = CommonHost(self.identities, self.database)
        self.workflow = PersistentDemandConfirmationWorkflow(self.host, self.identities, self.database)
        self.audit = Service(self.database, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read', 'audit']}})
        self.addCleanup(self.audit.close)
        self.ok(self.audit.bootstrap('fixture', json.loads((ROOT/'data/catalog.json').read_text())['nodes'], request_key='persist-seed'))
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'contract.txt'
        self.path.write_bytes((ROOT/'data/production_planning/contract.txt').read_bytes())
        self.interpreted = reference_interpretation(extract_contract_text(self.path), data_version=1)
        self.preflight = DemandConfirmationWorkflow(self.host, self.identities)

    def ok(self, result):
        self.assertTrue(result['ok'], result)
        return result['data']

    def error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def target(self):
        return self.preflight.prepare(DevelopmentContext('author'), self.path, self.interpreted, data_version=1)['target']

    def save(self, **kwargs):
        values = dict(request_key='save-1'); values.update(kwargs)
        return self.workflow.save(DevelopmentContext('author'), self.target(), **values)

    def confirm(self, saved, **kwargs):
        values = dict(expected_target_sha256=saved['target_sha256'], scopes=deepcopy(SCOPES),
                      note='명시적 개발 확인 기록 시험', request_key='confirm-1')
        values.update(kwargs)
        return self.workflow.confirm(DevelopmentContext('reviewer'), saved['demand_id'], saved['revision'], **values)

    def get(self, saved, **kwargs):
        return self.workflow.get(DevelopmentContext('reader'), saved['demand_id'], revision=saved['revision'], **kwargs)

    def test_save_confirm_reconnect_and_original_bytes_retrievable(self):
        saved = self.ok(self.save())
        event = self.ok(self.confirm(saved))
        self.assertTrue(event['recorded'])
        self.workflow = PersistentDemandConfirmationWorkflow(CommonHost(self.identities, self.database), self.identities, self.database)
        self.path.unlink()  # The immutable source was saved, not just its path.
        result = self.ok(self.get(saved))
        self.assertEqual('partially_confirmed', result['status'])
        self.assertEqual(SCOPES, result['confirmation']['verified_scopes'])
        self.assertFalse(result['confirmation']['verified'])
        self.assertEqual('2026-11-10', result['items'][0]['contract_item_candidate']['delivery_due'])
        self.assertEqual(100, result['items'][0]['planned_input_quantity'])
        self.assertIn('개발·검증용 가정 계약', result['original']['text'])
        self.assertEqual(event['event_id'], result['events'][0]['id'])
        self.assertEqual('development', result['identity_mode'])
        self.assertEqual(1, self.ok(self.audit.lookup('fixture')) and self.audit._version())

    def test_no_record_is_confirmed_before_an_explicit_event(self):
        saved = self.ok(self.save())
        result = self.ok(self.get(saved))
        self.assertEqual('needs_confirmation', result['status'])
        self.assertEqual([], result['confirmation']['verified_scopes'])

    def test_persisted_scope_display_matches_effective_confirmation(self):
        saved = self.ok(self.save()); self.ok(self.confirm(saved))
        result = self.ok(self.get(saved))
        scopes = {s['scope']: s for s in result['items'][0]['review_scopes']}
        self.assertEqual('verified', scopes['ordered_quantity']['confirmation'])
        self.assertEqual('not_recorded', scopes['configuration']['confirmation'])

    def test_persisted_result_separates_old_intake_diagnostics_from_current_status(self):
        saved = self.ok(self.save()); self.ok(self.confirm(saved))
        result = self.ok(self.get(saved))
        self.assertNotIn('HUMAN_CONFIRMATION_UNAVAILABLE', {r['code'] for r in result['questions']})
        self.assertIn('HUMAN_CONFIRMATION_UNAVAILABLE', {r['code'] for r in result['intake_report']['questions']})

    def test_reader_revocation_after_request_binding_blocks_read(self):
        saved = self.ok(self.save())
        original = self.identities.lookup
        self.identities.lookup = lambda subject: (UserIdentity('reader', frozenset())
            if subject == 'reader' else original(subject))
        self.error(self.get(saved), 'FORBIDDEN')

    def test_read_revocation_between_preflight_and_commit_blocks_confirmation(self):
        saved = self.ok(self.save())
        original = DemandConfirmationStore.confirm
        def revoked(store, *args, **kwargs):
            self.identities.users = [AUTHOR, READER, UserIdentity('reviewer', frozenset({'review'}))]
            return original(store, *args, **kwargs)
        with patch.object(DemandConfirmationStore, 'confirm', revoked):
            self.error(self.confirm(saved), 'FORBIDDEN')
        self.identities.users = [AUTHOR, READER, REVIEWER]
        self.assertEqual([], self.ok(self.get(saved))['events'])

    def test_self_review_and_read_only_user_cannot_record(self):
        saved = self.ok(self.save())
        for user, code in [('author', 'SELF_APPROVAL_FORBIDDEN'), ('reader', 'FORBIDDEN')]:
            result = self.workflow.confirm(DevelopmentContext(user), saved['demand_id'], saved['revision'],
                expected_target_sha256=saved['target_sha256'], scopes=SCOPES, note='request', request_key='blocked-'+user)
            self.error(result, code)
        self.assertEqual([], self.ok(self.get(saved))['events'])

    def test_revision_invalidates_old_confirmation_without_deleting_history(self):
        first = self.ok(self.save()); self.ok(self.confirm(first))
        self.interpreted['items'][0]['proposed_configuration_id'] = 'config:esp32-poe:L:WROOM'
        second = self.ok(self.save(demand_id=first['demand_id'], expected_revision=1, request_key='save-2'))
        self.assertEqual(2, second['revision'])
        old = self.ok(self.get(first)); current = self.ok(self.get(second))
        self.assertEqual('superseded', old['status'])
        self.assertEqual([], old['confirmation']['verified_scopes'])
        self.assertEqual([], current['confirmation']['verified_scopes'])
        self.assertEqual(2, len(old['events']))  # confirm + supersession event
        self.error(self.confirm(first, request_key='old-new-key'), 'STALE_REVISION')

    def test_source_and_claim_edits_are_new_immutable_revisions(self):
        first = self.ok(self.save()); self.ok(self.confirm(first))
        self.path.write_bytes(self.path.read_bytes()+b'\nAdditional source clause\n')
        e = extract_contract_text(self.path)
        self.interpreted['source_sha256'] = e['source']['sha256']
        second = self.ok(self.save(demand_id=first['demand_id'], expected_revision=1, request_key='source-edit'))
        self.assertNotEqual(first['target_sha256'], second['target_sha256'])
        self.assertEqual('superseded', self.ok(self.get(first))['status'])

    def test_current_reviewer_permission_revocation_invalidates_effective_scope(self):
        saved = self.ok(self.save()); self.ok(self.confirm(saved))
        self.identities.users = [AUTHOR, READER, UserIdentity('reviewer', frozenset({'read'}))]
        result = self.ok(self.get(saved))
        self.assertEqual([], result['confirmation']['verified_scopes'])
        self.assertEqual('permission_revoked', result['events'][0]['effective_state'])
        self.error(self.confirm(saved, request_key='after-revoke'), 'FORBIDDEN')

    def test_permission_provider_outage_never_replays_a_verified_claim(self):
        saved = self.ok(self.save()); self.ok(self.confirm(saved))
        self.identities.failure = True
        self.error(self.get(saved), 'IDENTITY_PROVIDER_UNAVAILABLE')

    def test_same_key_replay_and_different_content_conflict(self):
        saved = self.ok(self.save())
        self.assertEqual(saved, self.ok(self.save()))
        first = self.ok(self.confirm(saved)); replay = self.confirm(saved)
        self.assertEqual(first, self.ok(replay))
        self.assertIn('IDEMPOTENT_REPLAY', {w['code'] for w in replay['warnings']})
        self.error(self.confirm(saved, note='different note'), 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(1, len(self.ok(self.get(saved))['events']))

    def test_successful_confirmation_retry_is_historical_ack_after_revision(self):
        first = self.ok(self.save()); event = self.ok(self.confirm(first))
        self.interpreted['items'][0]['proposed_configuration_id'] = 'config:esp32-poe:L:WROOM'
        self.ok(self.save(demand_id=first['demand_id'], expected_revision=1, request_key='edit'))
        self.assertEqual(event, self.ok(self.confirm(first)))
        self.assertEqual('superseded', self.ok(self.get(first))['status'])

    def test_order_independent_multiple_scopes_and_scope_sensitive_replay(self):
        saved = self.ok(self.save())
        scopes = SCOPES + [{'item_id': 'contract-item:planning:001', 'scope': 'configuration'}]
        event = self.ok(self.confirm(saved, scopes=scopes))
        self.assertEqual(event, self.ok(self.confirm(saved, scopes=list(reversed(scopes)))))
        self.error(self.confirm(saved, scopes=SCOPES), 'IDEMPOTENCY_CONFLICT')

    def test_unavailable_scopes_and_forged_hash_do_not_create_events(self):
        saved = self.ok(self.save())
        self.error(self.confirm(saved, expected_target_sha256='0'*64), 'DEMAND_TARGET_CHANGED')
        for name in ('constraints', 'delivery_arrival'):
            self.error(self.confirm(saved, scopes=[{'item_id': 'contract-item:planning:001', 'scope': name}],
                request_key='scope-'+name), 'SCOPE_NOT_READY')
        self.assertEqual([], self.ok(self.get(saved))['events'])

    def test_source_change_after_save_blocks_a_new_confirmation(self):
        saved = self.ok(self.save())
        self.path.write_bytes(self.path.read_bytes()+b'\nchanged\n')
        self.error(self.confirm(saved), 'DEMAND_TARGET_CHANGED')

    def test_event_failure_rolls_back_event_and_request_then_same_key_can_retry(self):
        saved = self.ok(self.save())
        original = DemandConfirmationStore._append_event
        def fail(store, *args, **kwargs):
            original(store, *args, **kwargs)
            raise DomainError('INJECTED_FAILURE', 'Injected after event insert')
        with patch.object(DemandConfirmationStore, '_append_event', fail):
            self.error(self.confirm(saved), 'INJECTED_FAILURE')
        self.assertEqual([], self.ok(self.get(saved))['events'])
        self.ok(self.confirm(saved))
        self.assertEqual(1, len(self.ok(self.get(saved))['events']))
        runs = self.ok(self.audit.run_history('fixture'))
        self.assertTrue(any(r['error_code'] == 'INJECTED_FAILURE' for r in runs))

    def test_concurrent_same_key_is_one_event(self):
        saved = self.ok(self.save())
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: self.confirm(saved), range(2)))
        self.assertEqual(self.ok(replies[0]), self.ok(replies[1]))
        self.assertEqual(1, len(self.ok(self.get(saved))['events']))

    def test_concurrent_revision_compare_and_swap_has_one_winner(self):
        first = self.ok(self.save())
        # Both concurrent calls submit the same change from revision1. This
        # exercises the CAS race; distinct concurrent payloads remain followup.
        self.interpreted['items'][0]['proposed_configuration_id'] = 'config:esp32-poe:L:WROOM'
        target = self.target()
        def save_one(index):
            return self.workflow.save(DevelopmentContext('author'), target, demand_id=first['demand_id'],
                expected_revision=1, request_key='concurrent-'+str(index))
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(save_one, range(2)))
        self.assertEqual(1, sum(r['ok'] for r in replies))
        self.error(next(r for r in replies if not r['ok']), 'STALE_REVISION')

    def test_tampered_persisted_payload_or_source_is_not_verified(self):
        saved = self.ok(self.save()); self.ok(self.confirm(saved))
        # Test-only corruption through the already isolated fixture connection.
        self.audit.db.execute('UPDATE demand_review_revisions SET source_bytes=%s WHERE demand_id=%s',
                              (b'corrupted', saved['demand_id']))
        self.error(self.get(saved), 'DEMAND_INTEGRITY')
