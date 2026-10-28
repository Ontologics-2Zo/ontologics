"""CQ01 gaps: same Agent draft -> separate reviewer -> exact Host apply -> read.

Real PostgreSQL via tests.db; scripted model and development people only.
Existing source, independent expected, validation and authority paths are reused.
"""
import copy
import json
import unittest

from examples.order_fulfillment import b_agent
from examples.order_fulfillment.b_contract import promise_values
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, canonical
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.runtime import ModelConfig
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test


class CQ01RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.audit, self.task = b_agent.setup_intake(self.database)
        self.addCleanup(self.audit.close)
        # Author has review permission so SELF_APPROVAL, rather than missing
        # permission, must be the reason their own review is refused.
        self.author = UserIdentity('cq01-author', frozenset({'read', 'draft', 'review'}))
        self.reviewer = UserIdentity('cq01-reviewer', frozenset({'read', 'review'}))
        identities = DevelopmentIdentityProvider([self.author, self.reviewer], enabled=True)
        self.host = CommonHost(identities, self.database,
            agent_registrations=tuple(r for r in REGISTRATIONS if r.name in {'b_intake', 'b_promise'}))
        self.audit.principals.update({u.actor: u.principal() for u in (self.author, self.reviewer)})
        self.expected = json.loads((ROOT / 'data/order_fulfillment/b_contract_expected.json').read_text())

    def error_is(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def run_agent(self, mode, task, provider=None):
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            result = request.run_agent('b_' + mode,
                provider or b_agent.ScriptedProvider(self.audit, task, actor=self.author.actor),
                [{'role': 'user', 'content': canonical(task)}],
                config=ModelConfig(model='cq01-scripted-recovery', max_steps=16), capture_tool_trace=True)
        return result

    def intake(self):
        result = self.run_agent('intake', self.task)
        checks, answer = b_agent.evaluate(self.audit, self.task, result, 1, actor=self.author.actor)
        self.assertTrue(all(checks.values()), checks)
        draft = self.audit.get_draft(self.author.actor, answer['draft_id'], answer['draft_version'])
        self.assertTrue(draft['ok'], draft)
        self.assertEqual(self.author.actor, draft['data']['author'])
        self.assertEqual('awaiting_review', draft['data']['status'])
        return answer, draft['data']

    def review(self, did, version, key, user=None):
        user = user or self.reviewer
        with self.host.request(DevelopmentContext(user.subject)) as request:
            return request.review(did, version, decision='approve',
                note='Development review of exact CQ01 synthetic draft; not real authentication.', request_key=key)

    def frozen_business_rows(self):
        return {table: self.audit.db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
                for table, order in [('snapshots', 'version'), ('drafts', 'id,version'), ('review_events', 'sequence')]}

    def test_same_agent_draft_is_reviewed_applied_and_read_without_business_mutation(self):
        answer, draft = self.intake()
        did, dv = answer['draft_id'], answer['draft_version']
        round_id = self.expected['delivery_round_id']
        self.error_is(self.audit.delivery_round(self.author.actor, round_id, version=1), 'NOT_FOUND')
        self.error_is(self.host.apply_approved(did, dv, request_key='cq01-before-review'), 'APPROVAL_REQUIRED')
        self.error_is(self.review(did, dv, 'cq01-self-review', self.author), 'SELF_APPROVAL_FORBIDDEN')
        self.assertEqual([], self.audit.db.execute('SELECT * FROM review_events').fetchall())
        reviewed = self.review(did, dv, 'cq01-distinct-review')
        self.assertTrue(reviewed['ok'], reviewed)
        self.assertEqual(draft['content_hash'], reviewed['data']['content_hash'])
        self.assertEqual(self.reviewer.actor, reviewed['data']['reviewer'])
        applied = self.host.apply_approved(did, dv, request_key='cq01-exact-apply')
        self.assertTrue(applied['ok'], applied)
        version = applied['data_version']
        self.assertEqual(2, version)
        # A separate real connection must see the committed result.
        with_service = Service(self.database, principals={self.author.actor: self.author.principal()})
        try:
            result = with_service.delivery_round(self.author.actor, round_id, version=version)
            self.assertTrue(result['ok'], result)
            actual = promise_values(result['data'])
            self.assertEqual({k: self.expected[k] for k in actual}, actual)
        finally:
            with_service.close()
        persisted = self.audit.get_draft(self.author.actor, did, dv)['data']
        self.assertEqual(('applied', version, draft['content_hash']),
                         (persisted['status'], persisted['applied_version'], persisted['content_hash']))
        before = self.frozen_business_rows()
        task = dict(mode='promise', delivery_round_id=round_id, data_version=version,
                    not_assessed=b_agent.NOT_ASSESSED, task='Read the approved CQ01 promise only.')
        runtime = self.run_agent('promise', task)
        checks, promise = b_agent.evaluate(self.audit, task, runtime, version, actor=self.author.actor)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(actual, promise['promise'])
        self.error_is(self.audit.delivery_round(self.author.actor, round_id, version=1), 'NOT_FOUND')
        self.assertEqual(before, self.frozen_business_rows())
        row = self.audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s', (runtime['run_id'],)).fetchone()
        self.assertEqual(self.author.actor, row['actor'])
        self.assertEqual({'lookup', 'delivery_round'}, set(json.loads(row['config'])['allowed_tools']))
        self.assertEqual(runtime['output_hash'], row['output_hash'])
        print('CQ01_RECOVERY_FLOW=' + canonical(dict(draft_id=did, draft_version=dv,
            author=draft['author'], reviewer=self.reviewer.actor, content_hash=draft['content_hash'],
            applied_data_version=version, promise=promise, evaluation=checks,
            business_tables_unchanged=True, separate_connection_verified=True, actual_api_calls=0)))

    def test_superseded_and_unapproved_versions_never_substitute_latest(self):
        answer, draft = self.intake()
        did, dv = answer['draft_id'], answer['draft_version']
        self.assertTrue(self.review(did, dv, 'cq01-review-v1')['ok'])
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            revised = request.call('prepare_draft', dict(operations=copy.deepcopy(draft['operations']),
                request_key='cq01-revise', draft_id=did, expected_version=dv))
        self.assertTrue(revised['ok'], revised)
        next_dv = revised['data']['draft_version']
        self.assertEqual(dv + 1, next_dv)
        self.error_is(self.review(did, dv, 'cq01-old-review'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, dv, request_key='cq01-old-apply'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, next_dv, request_key='cq01-unapproved-v2'), 'APPROVAL_REQUIRED')
        self.error_is(self.host.apply_approved(did, 999, request_key='cq01-unknown-version'), 'DRAFT_NOT_FOUND')
        self.assertEqual(1, self.audit.lookup(self.author.actor)['data_version'])
        self.assertTrue(self.review(did, next_dv, 'cq01-review-v2')['ok'])
        applied = self.host.apply_approved(did, next_dv, request_key='cq01-apply-v2')
        self.assertTrue(applied['ok'], applied)
        self.assertEqual(next_dv, applied['data']['draft_version'])
        self.assertEqual(2, applied['data_version'])

    def test_both_registered_agents_cannot_use_review_apply_or_forge_identity(self):
        self.intake()
        before = self.frozen_business_rows()
        for mode in ('intake', 'promise'):
            for name, arguments, code in [('review', {}, 'TOOL_NOT_ALLOWED'), ('apply', {}, 'TOOL_NOT_ALLOWED'),
                    ('lookup', {'actor': self.reviewer.actor}, 'INVALID_TOOL_ARGUMENTS')]:
                with self.subTest(role=mode, tool=name):
                    def adversarial(**kwargs):
                        return {'text': '', 'tool_calls': [{'name': name, 'arguments': arguments}]}
                    result = self.run_agent(mode, self.task, adversarial)
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(code, result['error']['code'])
                    self.assertEqual([], result['tool_trace'])
        self.assertEqual(before, self.frozen_business_rows())
        self.assertEqual(1, self.audit.lookup(self.author.actor)['data_version'])


if __name__ == '__main__':
    unittest.main()
