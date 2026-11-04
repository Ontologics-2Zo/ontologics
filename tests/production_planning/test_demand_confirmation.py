"""Host-only confirmation requests; storage doubles are not confirmation evidence."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, IdentityError, UserIdentity
from ontologics.model import digest
from ontologics.service import DomainError
from tests.production_planning.test_contract_intake import ROOT, catalog_bundle
from examples.production_planning_contract_intake import reference_interpretation
from ontologics.production_planning.contract_intake import extract_contract_text

if importlib.util.find_spec('ontologics.production_planning.demand_confirmation'):
    from ontologics.production_planning.demand_confirmation import DemandConfirmationWorkflow
else:
    DemandConfirmationWorkflow = None

AUTHOR = UserIdentity('author', frozenset({'read', 'draft', 'review'}))
REVIEWER = UserIdentity('reviewer', frozenset({'read', 'review'}))
READER = UserIdentity('reader', frozenset({'read'}))
SCOPES = [{'item_id': 'contract-item:planning:001', 'scope': 'ordered_quantity'}]


class MutableIdentities:
    """Real common development identity behavior with a replaceable provider."""
    mode = 'development'

    def __init__(self):
        self.users = [AUTHOR, REVIEWER, READER]
        self.failure = None

    def resolve(self, context):
        return DevelopmentIdentityProvider(self.users, enabled=True).resolve(context)

    def lookup(self, subject):
        if self.failure:
            raise RuntimeError('credential-like detail must not escape')
        return DevelopmentIdentityProvider(self.users, enabled=True).lookup(subject)


class CatalogHost:
    """Public-query unit double. Real CommonHost is exercised in PG tests."""
    def __init__(self, identities):
        self.identities = identities
        self.nodes = json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        self.bundle = catalog_bundle()
        self.failed_query = False
        self.after_query = None

    @contextmanager
    def request(self, context):
        user = self.identities.resolve(context)
        host = self
        class Session:
            @property
            def context(self):
                return dict(subject=user.subject, actor=user.actor, identity_mode='development',
                            permissions=sorted(user.permissions))

            def call(self, tool, arguments):
                if host.after_query:
                    host.after_query()
                if host.failed_query or arguments['version'] != 1:
                    return dict(ok=False, data=None, data_version=1,
                                errors=[{'code': 'VERSION_NOT_FOUND'}], run_id='unit-failure')
                data = (host.nodes if tool == 'lookup' else next(c for c in host.bundle['candidates']
                        if c['configuration']['id'] == arguments['configuration_id']))
                return dict(ok=True, data=deepcopy(data), data_version=1, errors=[], run_id='unit-query')
        yield Session()


class CapturePersistence:
    """Capture-only transport double. Does not store or authenticate a record."""
    def __init__(self):
        self.intents = []
        self.response = dict(ok=True, data={'event_id': 'test-double-only'}, verified=True, status='confirmed')
        self.failure = False

    def submit(self, intent):
        self.intents.append(deepcopy(intent))
        if self.failure:
            raise RuntimeError('credential-like detail must not escape')
        return deepcopy(self.response)


class ConfirmationFixture:
    def setUp(self):
        self.assertIsNotNone(DemandConfirmationWorkflow, 'Separate confirmation module is missing')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'contract.txt'
        self.path.write_bytes((ROOT/'data/production_planning/contract.txt').read_bytes())
        self.interpreted = reference_interpretation(extract_contract_text(self.path), data_version=1)
        self.identities = MutableIdentities()
        self.host = CatalogHost(self.identities)
        self.persistence = CapturePersistence()
        self.workflow = DemandConfirmationWorkflow(self.host, self.identities, persistence=self.persistence)

    def prepare(self, **kwargs):
        return self.workflow.prepare(DevelopmentContext('author'), self.path, self.interpreted,
                                     data_version=1, **kwargs)

    def submit(self, target, **kwargs):
        values = dict(expected_target_sha256=target.sha256, scopes=deepcopy(SCOPES),
                      note='개발 시험의 명시적 확인 요청', request_key='confirm-1')
        values.update(kwargs)
        return self.workflow.submit(DevelopmentContext('reviewer'), target, **values)

    def error(self, code, call):
        with self.assertRaises(DomainError) as caught:
            call()
        self.assertEqual(code, caught.exception.code)
        return caught.exception


class DemandConfirmationTests(ConfirmationFixture, unittest.TestCase):
    def test_prepare_binds_existing_request_identity_and_exact_source(self):
        prepared = self.prepare()
        target = prepared['target']
        self.assertEqual('author', target.author_subject)
        self.assertEqual('development', target.identity_mode)
        payload = json.loads(target.payload_json)
        self.assertEqual('user:author', payload['author_actor'])
        self.assertEqual('6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962',
                         payload['report']['source']['sha256'])
        self.assertEqual(1, payload['report']['binding']['data_version'])
        self.assertEqual('needs_confirmation', prepared['review']['status'])
        self.assertFalse(prepared['review']['confirmation']['verified'])

    def test_submit_partial_scope_uses_current_common_identity_not_input_claims(self):
        self.interpreted.update(confirmed=True, confirmation_ref='invented', reviewer='forged')
        response = self.submit(self.prepare()['target'])
        intent = response['submission']
        self.assertEqual('user:reviewer', intent['reviewer_actor'])
        self.assertEqual('user:author', intent['target']['author_actor'])
        self.assertEqual(SCOPES, intent['scopes'])
        self.assertEqual(['read', 'review'], intent['authorization']['permissions'])
        self.assertEqual('submitted', response['status'])
        self.assertFalse(response['confirmation']['verified'])
        self.assertTrue(response['storage_response']['verified'])  # preserved raw claim only

    def test_dict_is_not_a_host_prepared_target(self):
        self.error('INVALID_CONFIRMATION_TARGET', lambda: self.workflow.submit(
            DevelopmentContext('reviewer'), {'confirmed': True}, expected_target_sha256='forged',
            scopes=SCOPES, note='claim', request_key='key'))

    def test_self_confirmation_is_forbidden(self):
        target = self.prepare()['target']
        self.error('SELF_APPROVAL_FORBIDDEN', lambda: self.workflow.submit(
            DevelopmentContext('author'), target, expected_target_sha256=target.sha256,
            scopes=SCOPES, note='self', request_key='self'))

    def test_read_permission_does_not_grant_review_permission(self):
        target = self.prepare()['target']
        self.error('FORBIDDEN', lambda: self.workflow.submit(
            DevelopmentContext('reader'), target, expected_target_sha256=target.sha256,
            scopes=SCOPES, note='reader', request_key='reader'))

    def test_revoked_permission_during_query_is_checked_before_submission(self):
        target = self.prepare()['target']
        self.host.after_query = lambda: setattr(self.identities, 'users', [AUTHOR, READER,
                                               UserIdentity('reviewer', frozenset({'read'}))])
        self.error('FORBIDDEN', lambda: self.submit(target))
        self.assertEqual([], self.persistence.intents)

    def test_lookup_failure_is_safe_and_does_not_use_cached_permissions(self):
        target = self.prepare()['target']
        self.identities.failure = True
        error = self.error('IDENTITY_PROVIDER_UNAVAILABLE', lambda: self.submit(target))
        self.assertNotIn('credential-like', str(error))
        self.assertEqual([], self.persistence.intents)

    def test_mismatched_provider_subject_is_rejected(self):
        target = self.prepare()['target']
        self.identities.lookup = lambda subject: AUTHOR
        self.error('INVALID_IDENTITY', lambda: self.submit(target))

    def test_original_change_after_prepare_invalidates_target(self):
        target = self.prepare()['target']
        self.path.write_bytes(self.path.read_bytes()+b'\nChanged source\n')
        self.error('DEMAND_TARGET_CHANGED', lambda: self.submit(target))
        self.assertEqual([], self.persistence.intents)

    def test_expected_hash_and_tampered_target_fields_are_rejected(self):
        target = self.prepare()['target']
        self.error('DEMAND_TARGET_CHANGED', lambda: self.submit(target, expected_target_sha256='0'*64))
        for edited in (replace(target, author_subject='someone'), replace(target, data_version=2),
                       replace(target, interpretation_json='{}'), replace(target, sha256='0'*64)):
            self.error('DEMAND_TARGET_CHANGED', lambda: self.submit(edited))

    def test_query_failure_and_invalid_interpretation_produce_no_target(self):
        self.host.failed_query = True
        prepared = self.prepare()
        self.assertIsNone(prepared['target'])
        self.assertEqual('query_failed', prepared['review']['status'])
        self.host.failed_query = False
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = 120
        prepared = self.prepare()
        self.assertIsNone(prepared['target'])
        self.assertEqual('invalid', prepared['review']['status'])

    def test_source_missing_at_submit_is_a_safe_source_failure(self):
        target = self.prepare()['target']
        self.path.unlink()
        self.error('SOURCE_UNAVAILABLE', lambda: self.submit(target))

    def test_unavailable_unknown_and_duplicate_scopes_are_not_confirmable(self):
        target = self.prepare()['target']
        for scope in ('delivery_arrival', 'constraints', 'made-up'):
            self.error('SCOPE_NOT_READY', lambda: self.submit(target, scopes=[
                {'item_id': 'contract-item:planning:001', 'scope': scope}]))
        self.error('INVALID_CONFIRMATION_SCOPE', lambda: self.submit(target, scopes=SCOPES*2))
        self.error('INVALID_CONFIRMATION_SCOPE', lambda: self.submit(target, scopes=[
            {'item_id': 'missing-item', 'scope': 'ordered_quantity'}]))

    def test_no_implicit_full_confirmation_and_no_empty_or_extra_input_fields(self):
        target = self.prepare()['target']
        for scopes in ([], 'all', [{'item_id': 'contract-item:planning:001', 'scope': 'ordered_quantity', 'confirmed': True}]):
            self.error('INVALID_CONFIRMATION_SCOPE', lambda: self.submit(target, scopes=scopes))
        for key in ('', None, 'k'*201):
            self.error('INVALID_REQUEST_KEY', lambda: self.submit(target, request_key=key))
        self.error('INVALID_REVIEW', lambda: self.submit(target, note=' '))

    def test_missing_persistence_stops_without_issuing_confirmation(self):
        workflow = DemandConfirmationWorkflow(self.host, self.identities)
        target = workflow.prepare(DevelopmentContext('author'), self.path, self.interpreted, data_version=1)['target']
        self.error('PERSISTENCE_UNAVAILABLE', lambda: workflow.submit(
            DevelopmentContext('reviewer'), target, expected_target_sha256=target.sha256,
            scopes=SCOPES, note='request', request_key='key'))

    def test_storage_failure_is_not_submitted_and_exceptions_are_redacted(self):
        target = self.prepare()['target']
        self.persistence.response = dict(ok=False, errors=[{'code': 'STALE_REVISION'}], run_id='backend-run')
        response = self.submit(target)
        self.assertEqual('persistence_failed', response['status'])
        self.assertEqual('STALE_REVISION', response['storage_response']['errors'][0]['code'])
        self.assertFalse(response['confirmation']['verified'])
        self.persistence.failure = True
        error = self.error('PERSISTENCE_UNAVAILABLE', lambda: self.submit(target))
        self.assertNotIn('credential-like', str(error))

    def test_same_business_request_hash_is_stable_across_attempts(self):
        target = self.prepare()['target']
        first = self.submit(target)['submission']
        second = self.submit(target)['submission']
        self.assertEqual(first['request_sha256'], second['request_sha256'])
        changed = self.submit(target, note='different note')['submission']
        self.assertNotEqual(first['request_sha256'], changed['request_sha256'])
        self.assertIn('requested_at', first)
        self.assertNotIn('recorded_at', first)

    def test_target_and_interpretation_cannot_be_mutated_through_outputs(self):
        original = deepcopy(self.interpreted)
        prepared = self.prepare()
        prepared['review']['items'][0]['fields']['ordered_quantity']['value'] = 999
        response = self.submit(prepared['target'])
        self.assertEqual(100, response['submission']['target']['report']['items'][0]['fields']['ordered_quantity']['value'])
        self.assertEqual(original, self.interpreted)

    def test_duplicate_item_identifiers_cannot_form_a_target(self):
        self.interpreted['items'].append(deepcopy(self.interpreted['items'][0]))
        self.error('INVALID_REVIEW_ITEMS', self.prepare)

    def test_changed_catalog_selection_or_evidence_requires_a_new_target(self):
        target = self.prepare()['target']
        self.host.bundle['candidates'][0]['configuration']['evidence'] = ['changed-evidence']
        self.error('PUBLIC_QUERY_FAILED', lambda: self.submit(target))


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit isolated TEST_DATABASE_URL required')
class PostgresConfirmationTests(ConfirmationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from tests.db import database_for_test
        from ontologics.production_planning.persistence import PlanningService as Service
        from ontologics.production_planning.host import PlanningHost as CommonHost
        self.database = database_for_test(self)
        seed = Service(self.database, principals={'fixture': {'kind': 'human', 'roles': ['read', 'seed']}})
        try:
            self.assertTrue(seed.bootstrap('fixture', json.loads((ROOT/'data/catalog.json').read_text())['nodes'],
                                           request_key='module-seed')['ok'])
        finally:
            seed.close()
        self.host = CommonHost(self.identities, self.database)
        self.workflow = DemandConfirmationWorkflow(self.host, self.identities)

    def test_real_common_host_identity_exact_query_and_unbound_persistence(self):
        prepared = self.prepare()
        self.assertEqual(1, prepared['review']['query']['lookup_response']['data_version'])
        self.assertEqual('author', prepared['target'].author_subject)
        self.error('PERSISTENCE_UNAVAILABLE', lambda: self.submit(prepared['target']))

    def test_new_workflow_reconnect_uses_same_payload_without_claiming_persistence(self):
        first = self.prepare()['target']
        self.workflow = DemandConfirmationWorkflow(self.host, self.identities)
        second = self.prepare()['target']
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.payload_json, second.payload_json)
        self.error('PERSISTENCE_UNAVAILABLE', lambda: self.submit(first))

    def test_common_agent_gateway_still_cannot_submit_confirmation(self):
        with self.host.request(DevelopmentContext('reviewer')) as session:
            result = session.call('confirm_demand', {'confirmed': True})
        self.assertFalse(result['ok'])
        self.assertEqual('TOOL_NOT_ALLOWED', result['errors'][0]['code'])
