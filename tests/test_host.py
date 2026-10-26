"""Development identity + real PostgreSQL workflows; no login/live LLM claim."""
import copy
import json
from pathlib import Path
import unittest

from ontologics.demo import CONFIG_L1, example_queries, fixture_nodes
from ontologics.database import DatabaseSettings
from ontologics.host import CommonHost, WORKFLOW_ACTOR
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, IdentityError, UserIdentity
from ontologics.runtime import ModelConfig
from ontologics.service import AgentGateway, Service
from tests.db import database_for_test


ALICE = UserIdentity('dev-alice', frozenset({'read', 'draft', 'review'}))
BOB = UserIdentity('dev-bob', frozenset({'read', 'draft', 'review'}))
READER = UserIdentity('dev-reader', frozenset({'read'}))


class HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = json.loads((Path(__file__).resolve().parents[1] / 'data/catalog.json').read_text())['nodes']
        cls.new_nodes = [n for n in catalog if n['id'] == CONFIG_L1 or n.get('configuration_id') == CONFIG_L1]
        excluded = {n['id'] for n in cls.new_nodes}
        fictional, cls.supplement = fixture_nodes()
        cls.seed_nodes = [n for n in catalog if n['id'] not in excluded] + fictional
        cls.registration = [{'op': 'put', 'node': n} for n in cls.new_nodes]

    def setUp(self):
        self.database = database_for_test(self)
        self.provider = DevelopmentIdentityProvider([ALICE, BOB, READER], enabled=True)
        self.host = CommonHost(self.provider, self.database)
        self.audit = Service(self.database, principals={'fixture': {'kind': 'human', 'roles': ['read', 'seed', 'audit']}})
        self.addCleanup(self.audit.close)
        self.ok(self.audit.bootstrap('fixture', self.seed_nodes, request_key='test-fixture'))

    def ok(self, response):
        self.assertTrue(response['ok'], response)
        return response

    def error(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def draft(self, user=ALICE, key='register-L1', **kwargs):
        args = {'operations': copy.deepcopy(self.registration), 'request_key': key, **kwargs}
        with self.host.request(DevelopmentContext(user.subject)) as session:
            return self.ok(session.agent('product').call('prepare_draft', args))['data']

    def review(self, draft, user=BOB, decision='approve', key='review'):
        with self.host.request(DevelopmentContext(user.subject)) as session:
            return session.review(draft['draft_id'], draft['draft_version'],
                                  decision=decision, note='개발 사용자 검토 대역', request_key=key)

    def apply(self, draft, key='apply'):
        return self.host.apply_approved(draft['draft_id'], draft['draft_version'], request_key=key)

    def test_product_author_is_request_user_and_only_other_user_can_approve(self):
        draft = self.draft()
        with self.host.request(DevelopmentContext(ALICE.subject)) as session:
            self.assertEqual('development', session.context['identity_mode'])
            saved = self.ok(session.call('get_draft', {'draft_id': draft['draft_id'], 'version': 1}))
            self.assertEqual(ALICE.actor, saved['data']['author'])
            self.error(session.call('configuration', {'configuration_id': CONFIG_L1}), 'NOT_FOUND')
        self.error(self.review(draft, ALICE), 'SELF_APPROVAL_FORBIDDEN')
        self.error(self.review(draft, READER), 'FORBIDDEN')
        self.error(self.apply(draft), 'APPROVAL_REQUIRED')
        self.ok(self.review(draft))
        result = self.ok(self.apply(draft))
        self.assertEqual(2, result['data_version'])
        with self.host.request(DevelopmentContext(READER.subject)) as session:
            registered = self.ok(session.call('configuration', {'configuration_id': CONFIG_L1}))
            self.assertEqual(74, len(registered['data']['items']))
            self.assertEqual(128, sum(len(n['positions']) for n in registered['data']['items']))
            self.assertTrue(registered['evidence'])
        runs = self.ok(self.audit.run_history('fixture'))['data']
        self.assertTrue(any(r['actor'] == ALICE.actor and r['tool'] == 'prepare_draft' for r in runs))
        self.assertTrue(any(r['actor'] == BOB.actor and r['tool'] == 'review' and r['status'] == 'succeeded' for r in runs))
        self.assertTrue(any(r['actor'] == WORKFLOW_ACTOR and r['tool'] == 'apply' and r['status'] == 'succeeded' for r in runs))
        self.assertTrue(any(r['actor'] == ALICE.actor and r['error_code'] == 'SELF_APPROVAL_FORBIDDEN' for r in runs))

    def test_request_identity_is_fixed_and_payload_cannot_override_it(self):
        with self.host.request(DevelopmentContext(ALICE.subject)) as alice:
            for field in ('actor', 'principals', 'user_id', 'author', 'reviewer'):
                self.error(alice.agent('product').call('lookup', {field: BOB.subject}), 'INVALID_TOOL_ARGUMENTS')
            with self.host.request(DevelopmentContext(BOB.subject)) as bob:
                self.assertEqual(BOB.actor, bob.context['actor'])
            draft = self.ok(alice.agent('product').call('prepare_draft',
                            {'operations': self.registration, 'request_key': 'fixed-user'}))['data']
            saved = self.ok(alice.call('get_draft', {'draft_id': draft['draft_id'], 'version': 1}))
            self.assertEqual(ALICE.actor, saved['data']['author'])

    def test_agent_role_scope_is_narrower_than_user_review_and_draft_rights(self):
        with self.host.request(DevelopmentContext(ALICE.subject)) as session:
            for tool in ('review', 'apply', 'bootstrap', 'run_history'):
                self.error(session.agent('product').call(tool, {}), 'TOOL_NOT_ALLOWED')
            self.error(session.agent('quality').call('prepare_draft',
                       {'operations': self.registration, 'request_key': 'quality-write'}), 'TOOL_NOT_ALLOWED')
            for role in ('knowledge', 'supervisor'):
                for tool in ('prepare_draft', 'review', 'apply', 'bootstrap'):
                    self.error(session.agent(role).call(tool, {}), 'TOOL_NOT_ALLOWED')
                self.assertTrue(session.agent(role).call('lookup', {'kind': 'ProductModel', 'version': 1})['ok'])
            with self.assertRaises(ValueError):
                session.agent('unregistered-role')
        with self.assertRaises(ValueError):
            AgentGateway(self.audit, 'fixture', allowed_tools={'apply'})
        self.assertEqual(1, self.audit._version())

    def test_retries_are_scoped_to_person_and_keep_draft_and_apply_ids(self):
        first = self.draft()
        self.assertEqual(first['draft_id'], self.draft()['draft_id'])
        second = self.draft(BOB)
        self.assertNotEqual(first['draft_id'], second['draft_id'])
        with self.host.request(DevelopmentContext(ALICE.subject)) as session:
            self.error(session.agent('product').call('prepare_draft',
                       {'operations': self.supplement, 'request_key': 'register-L1'}), 'IDEMPOTENCY_CONFLICT')
        self.ok(self.review(first))
        applied = self.ok(self.apply(first))
        replay = self.ok(self.apply(first))
        self.assertEqual(applied['data_version'], replay['data_version'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['warnings']])
        self.assertEqual(2, self.audit._version())

    def test_revision_requires_new_approval_and_other_author_cannot_revise(self):
        first = self.draft()
        self.ok(self.review(first, decision='request_changes'))
        with self.host.request(DevelopmentContext(BOB.subject)) as session:
            self.error(session.agent('product').call('prepare_draft', {
                'operations': self.registration, 'request_key': 'wrong-author',
                'draft_id': first['draft_id'], 'expected_version': 1}), 'DRAFT_OWNER_REQUIRED')
        revised = self.draft(key='revised', draft_id=first['draft_id'], expected_version=1)
        self.assertEqual(2, revised['draft_version'])
        self.error(self.review(first, key='old-review'), 'SUPERSEDED_DRAFT')
        self.error(self.apply(first, key='old-apply'), 'SUPERSEDED_DRAFT')
        self.error(self.apply(revised), 'APPROVAL_REQUIRED')
        self.ok(self.review(revised, key='new-review'))
        self.ok(self.apply(revised))
        saved = self.ok(self.audit.get_draft('fixture', first['draft_id'], 1))
        self.assertEqual('superseded', saved['data']['status'])
        self.assertEqual('request_changes', saved['data']['review_history'][0]['decision'])

    def test_rejected_draft_and_stale_approved_base_cannot_be_applied(self):
        rejected = self.draft()
        self.ok(self.review(rejected, decision='reject'))
        self.error(self.apply(rejected), 'APPROVAL_REQUIRED')
        registration = self.draft(key='second-registration')
        self.ok(self.review(registration, key='registration-review'))
        supplement = self.draft(key='supplement', operations=self.supplement)
        self.ok(self.review(supplement, key='supplement-review'))
        self.ok(self.apply(supplement, key='supplement-apply'))
        self.error(self.apply(registration, key='stale-apply'), 'STALE_BASE')
        self.assertEqual(2, self.audit._version())

    def test_s1_supplement_keeps_exact_AB_conditions_and_remaining_unknowns(self):
        before = self.audit._version()
        draft = self.draft(key='supplement', operations=self.supplement)
        with self.host.request(DevelopmentContext(ALICE.subject)) as session:
            preview = self.ok(session.call('preview_investigation', {
                'draft_id': draft['draft_id'], 'version': 1, 'query': example_queries()['A']}))
            self.assertTrue(preview['data']['is_preview'])
        self.assertEqual(before, self.audit._version())
        self.ok(self.review(draft))
        after = self.ok(self.apply(draft))['data_version']
        with self.host.request(DevelopmentContext(ALICE.subject)) as session:
            for mode, query in example_queries().items():
                result = self.ok(session.agent('quality').call('compare_investigation',
                    {'query': query, 'before_version': before, 'after_version': after}))
                self.assertEqual(query, result['data']['query'])
                self.assertEqual('needs_confirmation', result['data']['before']['results'][0]['status'])
                self.assertEqual('excluded' if mode == 'A' else 'included', result['data']['after']['results'][0]['status'])
                self.assertEqual('needs_confirmation', result['data']['after']['results'][2]['status'])
                self.assertTrue(result['evidence'])

    def test_identity_provider_is_replaceable_and_approver_permissions_are_refreshed(self):
        class OtherProvider:
            mode = 'test-adapter'
            users = {u.subject: u for u in (ALICE, BOB)}
            def resolve(self, context):
                if context != 'trusted-test-context':
                    raise IdentityError('UNKNOWN_IDENTITY', 'Unrecognized test context')
                return self.lookup(ALICE.subject)
            def lookup(self, subject):
                return self.users[subject]
        provider = OtherProvider()
        other = CommonHost(provider, self.database)
        with other.request('trusted-test-context') as session:
            self.assertEqual('test-adapter', session.context['identity_mode'])
            draft = self.ok(session.agent('product').call('prepare_draft',
                {'operations': self.registration, 'request_key': 'other-provider'}))['data']
        self.ok(self.review(draft))
        provider.users[BOB.subject] = UserIdentity(BOB.subject, frozenset({'read'}))
        self.error(other.apply_approved(draft['draft_id'], 1, request_key='other-apply'), 'FORBIDDEN')
        self.assertEqual(1, self.audit._version())
        provider.users[BOB.subject] = BOB
        self.ok(other.apply_approved(draft['draft_id'], 1, request_key='other-apply'))

    def test_missing_or_mismatched_approver_and_provider_failure_never_apply(self):
        draft = self.draft()
        self.ok(self.review(draft))
        class MissingProvider:
            mode = 'test-adapter'
            def lookup(self, subject):
                raise IdentityError('UNKNOWN_IDENTITY', 'Missing person')
        class MismatchedProvider:
            mode = 'test-adapter'
            def lookup(self, subject):
                return ALICE
        class FailedProvider:
            mode = 'test-adapter'
            def lookup(self, subject):
                raise ConnectionError('private credential data must not be logged')
        for provider, code in ((MissingProvider(), 'FORBIDDEN'), (MismatchedProvider(), 'FORBIDDEN'),
                               (FailedProvider(), 'IDENTITY_PROVIDER_UNAVAILABLE')):
            result = CommonHost(provider, self.database).apply_approved(draft['draft_id'], 1, request_key='check-apply')
            self.error(result, code)
            self.assertNotIn('private credential', json.dumps(result))
        self.assertEqual(1, self.audit._version())

    def test_applied_result_replays_during_identity_outage_without_another_write(self):
        draft = self.draft()
        self.ok(self.review(draft))
        applied = self.ok(self.apply(draft))
        class UnavailableProvider:
            mode = 'test-adapter'
            def lookup(self, subject):
                raise ConnectionError('identity service unavailable')
        host = CommonHost(UnavailableProvider(), self.database)
        replay = self.ok(host.apply_approved(draft['draft_id'], 1, request_key='apply'))
        self.assertEqual(applied['data_version'], replay['data_version'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['warnings']])
        already = self.ok(host.apply_approved(draft['draft_id'], 1, request_key='another-apply'))
        self.assertIn('ALREADY_APPLIED', [w['code'] for w in already['warnings']])
        self.assertEqual(2, self.audit._version())

    def test_runtime_links_scripted_agent_to_request_user_and_enforces_role_before_tools(self):
        calls = []
        def provider(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {'text': '', 'tool_calls': [{'name': 'prepare_draft', 'arguments': {
                    'operations': self.registration, 'request_key': 'runtime-registration'}}]}
            return {'text': '개발용 초안, 사람 검토 대기', 'tool_calls': []}
        messages = [{'role': 'user', 'content': '등록 초안을 작성해줘'}]
        with self.host.request(DevelopmentContext(ALICE.subject)) as session:
            result = session.run_agent('product', provider, messages, config=ModelConfig(model='scripted-test'))
            self.ok(result)
            row = self.audit.db.execute('SELECT actor,config FROM runtime_runs WHERE id=%s', (result['run_id'],)).fetchone()
            self.assertEqual(ALICE.actor, row['actor'])
            self.assertNotIn('review', json.loads(row['config'])['allowed_tools'])
            draft = self.audit.db.execute('SELECT * FROM drafts').fetchone()
            self.assertEqual(ALICE.actor, draft['author'])
            self.error(session.review(draft['id'], draft['version'], decision='approve', note='self', request_key='self'), 'SELF_APPROVAL_FORBIDDEN')
            for role, tool in (('product', 'review'), ('product', 'apply'), ('quality', 'prepare_draft')):
                blocked = session.run_agent(role, lambda **kw: {'text': '', 'tool_calls': [
                    {'name': 'lookup', 'arguments': {}}, {'name': tool, 'arguments': {}}]},
                    messages, config=ModelConfig(model='scripted-test'))
                self.assertEqual('TOOL_NOT_ALLOWED', blocked['error']['code'])
                self.assertEqual([], blocked['tool_run_ids'])
        self.assertEqual(1, self.audit._version())

    def test_concurrent_apply_success_replays_when_identity_lookup_then_fails(self):
        draft = self.draft()
        self.ok(self.review(draft))
        normal_host = self.host
        class InterruptedProvider:
            mode = 'test-adapter'
            def lookup(self, subject):
                must_apply = normal_host.apply_approved(draft['draft_id'], 1, request_key='racing-apply')
                if not must_apply['ok']:
                    raise AssertionError('Concurrent normal apply failed')
                raise ConnectionError('response interrupted')
        host = CommonHost(InterruptedProvider(), self.database)
        result = self.ok(host.apply_approved(draft['draft_id'], 1, request_key='racing-apply'))
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in result['warnings']])
        self.assertEqual(2, self.audit._version())


class DevelopmentIdentityTests(unittest.TestCase):
    def test_request_identity_failure_is_safe_and_has_no_database_fallback(self):
        class FailedProvider:
            mode = 'test-adapter'
            def resolve(self, context):
                raise ConnectionError('private identity provider credentials')
        host = CommonHost(FailedProvider(), DatabaseSettings('postgresql://unused/test'))
        with self.assertRaises(IdentityError) as caught:
            with host.request('trusted-context'):
                self.fail('Failed provider must not create a request')
        self.assertEqual('IDENTITY_PROVIDER_UNAVAILABLE', caught.exception.code)
        self.assertNotIn('private', str(caught.exception))

    def test_explicit_opt_in_and_configured_subjects_only(self):
        with self.assertRaises(IdentityError):
            DevelopmentIdentityProvider([ALICE])
        provider = DevelopmentIdentityProvider([ALICE], enabled=True)
        self.assertEqual(ALICE, provider.resolve(DevelopmentContext(ALICE.subject)))
        for context in (DevelopmentContext('unknown'), {'subject': ALICE.subject}, ALICE.subject):
            with self.assertRaises(IdentityError):
                provider.resolve(context)
        with self.assertRaises(IdentityError):
            DevelopmentIdentityProvider([ALICE, ALICE], enabled=True)
        for permission in ('apply', 'seed', 'index'):
            with self.assertRaises(IdentityError):
                UserIdentity('dev-user', frozenset({permission}))


if __name__ == '__main__':
    unittest.main()
