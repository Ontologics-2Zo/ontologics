"""CQ02 recovery gaps: exact operational draft, separate review, immutable reads.

Uses the existing Service/Host, independent expected and scripted read provider.
The runner may save ``evidence`` after success; this module writes no files.
"""
import copy
import hashlib
import json
import unittest
import uuid

from examples.order_fulfillment import b_fulfillment_agent as agent
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.demo import PRINCIPALS
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, canonical
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.fulfillment import AS_OF, ROUND, SOURCE_PATH, load_demo_fulfillment
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CQ02RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.author = UserIdentity('cq02-author', frozenset({'read', 'draft', 'review'}))
        self.reviewer = UserIdentity('cq02-reviewer', frozenset({'read', 'review'}))
        self.reader = UserIdentity('cq02-reader', frozenset({'read'}))
        users = [self.author, self.reviewer, self.reader]
        self.service = Service(self.database, principals={**PRINCIPALS,
            **{u.actor: u.principal() for u in users}})
        self.addCleanup(self.service.close)
        identities = DevelopmentIdentityProvider(users, enabled=True)
        self.host = CommonHost(identities, self.database,
            agent_registrations=tuple(r for r in REGISTRATIONS if r.name == 'b_fulfillment'))
        base = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        seeded = self.service.bootstrap('seed-admin', base, request_key='cq02-recovery-seed')
        self.assertTrue(seeded['ok'], seeded)
        self.before_version = seeded['data_version']
        self.expected = json.loads((ROOT / 'data/order_fulfillment/b_fulfillment_expected.json').read_text())

    def error_is(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def draft(self):
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            response = request.call('prepare_draft', dict(operations=[{'op': 'put', 'node': n}
                for n in load_demo_fulfillment()], request_key='cq02-recovery-draft'))
        self.assertTrue(response['ok'], response)
        self.assertTrue(response['data']['validation']['valid'], response)
        stored = self.service.get_draft(self.author.actor,
            response['data']['draft_id'], response['data']['draft_version'])
        self.assertTrue(stored['ok'], stored)
        self.stored_draft_response = copy.deepcopy(stored)
        return response

    def review(self, did, version, key, user=None):
        with self.host.request(DevelopmentContext((user or self.reviewer).subject)) as request:
            return request.review(did, version, decision='approve',
                note='Development review of exact synthetic CQ02 observations; not operational authentication.',
                request_key=key)

    def rows(self):
        return {table: self.service.db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
            for table, order in [('snapshots', 'version'), ('drafts', 'id,version'),
                                 ('review_events', 'sequence')]}

    def query(self, version, as_of=AS_OF):
        return self.service.fulfillment_status(self.reader.actor, ROUND, as_of, version=version)

    def task(self, version):
        return dict(mode='fulfillment', delivery_round_id=ROUND, as_of=AS_OF,
                    data_version=version, question='Read this exact observed snapshot only.')

    def run_agent(self, task, provider=None, user=None):
        user = user or self.reader
        with self.host.request(DevelopmentContext(user.subject)) as request:
            return request.run_agent('b_fulfillment', provider or agent.ProvenanceProvider(
                agent.ScriptedProvider(self.service, task), task),
                [{'role': 'user', 'content': canonical(task)}],
                config=agent.CONFIG, capture_tool_trace=True)

    def test_operational_draft_exact_review_apply_and_reads_preserve_all_business_rows(self):
        execution_id = str(uuid.uuid4())
        prepared = self.draft()
        draft = prepared['data']
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertEqual(self.author.actor, self.stored_draft_response['data']['author'])
        self.error_is(self.host.apply_approved(did, dv, request_key='cq02-before-approval'), 'APPROVAL_REQUIRED')
        unapproved = self.query(self.before_version)
        self.assertTrue(unapproved['ok'], unapproved)
        self.assertIsNone(unapproved['data']['states'])
        self.error_is(self.review(did, dv, 'cq02-self-review', self.author), 'SELF_APPROVAL_FORBIDDEN')
        reviewed = self.review(did, dv, 'cq02-separate-review')
        self.assertTrue(reviewed['ok'], reviewed)
        self.assertEqual(draft['content_hash'], reviewed['data']['content_hash'])
        self.assertEqual(self.reviewer.actor, reviewed['data']['reviewer'])
        applied = self.host.apply_approved(did, dv, request_key='cq02-exact-apply')
        self.assertTrue(applied['ok'], applied)
        version = applied['data_version']
        self.assertEqual((dv, 2), (applied['data']['draft_version'], version))
        before = self.rows()
        audit_before = self.service.db.execute('SELECT COUNT(*) AS n FROM runs').fetchone()['n']
        fresh = Service(self.database, principals={self.reader.actor: self.reader.principal()})
        try:
            response = fresh.fulfillment_status(self.reader.actor, ROUND, AS_OF, version=version)
        finally:
            fresh.close()
        self.assertTrue(response['ok'], response)
        compared_keys = ('delivery_round_id', 'snapshot_id', 'as_of', 'quantity', 'unit',
                         'configuration_id', 'states', 'allocated_quantity', 'remaining_quantity',
                         'unallocated_quantity', 'fulfillment_assessed')
        comparisons = {k: response['data'][k] == self.expected[k] for k in compared_keys}
        self.assertTrue(all(comparisons.values()), comparisons)
        task = self.task(version)
        result = self.run_agent(task)
        checks, answer = agent.evaluate(self.service, task, result, version, actor=self.reader.actor)
        self.assertTrue(all(checks.values()), checks)
        actual_call = next(t for t in result['tool_trace'] if t['name'] == 'fulfillment_status')
        self.assertEqual(response['data']['states'], actual_call['result']['data']['states'])
        unknown = self.query(version, '2026-10-27T11:00:00+09:00')
        self.assertTrue(unknown['ok'], unknown)
        self.assertIsNone(unknown['data']['states'])
        self.assertIsNone(unknown['data']['remaining_quantity'])
        self.assertTrue(unknown['data']['unknowns'])
        historical = self.query(self.before_version)
        self.assertIsNone(historical['data']['states'])
        self.assertEqual(version, self.service.lookup(self.reader.actor)['data_version'])
        after = self.rows()
        self.assertEqual(before, after)
        audit_after = self.service.db.execute('SELECT COUNT(*) AS n FROM runs').fetchone()['n']
        self.assertGreater(audit_after, audit_before)
        row = self.service.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                                     (result['run_id'],)).fetchone()
        self.assertEqual(self.reader.actor, row['actor'])
        self.assertEqual({'lookup', 'fulfillment_status'}, set(json.loads(row['config'])['allowed_tools']))
        self.assertEqual(result['output_hash'], row['output_hash'])
        inputs = [SOURCE_PATH, 'data/order_fulfillment/b_fulfillment_fixture.json',
                  'data/order_fulfillment/b_fulfillment_expected.json']
        self.evidence = dict(execution_id=execution_id,
            inputs=[dict(path=str(ROOT / p), sha256=hashlib.sha256((ROOT / p).read_bytes()).hexdigest()) for p in inputs],
            query_input=dict(delivery_round_id=ROUND, as_of=AS_OF, data_version=version),
            prepared_response=copy.deepcopy(prepared), draft_read_response=copy.deepcopy(self.stored_draft_response),
            review_response=copy.deepcopy(reviewed),
            apply_response=copy.deepcopy(applied), unapproved_service_response=copy.deepcopy(unapproved),
            service_response=copy.deepcopy(response), agent_runtime_response=copy.deepcopy(result),
            parsed_agent_answer=copy.deepcopy(answer), evaluation=checks, expected_comparison=comparisons,
            unknown_input=dict(delivery_round_id=ROUND, as_of='2026-10-27T11:00:00+09:00', data_version=version),
            unknown_service_response=copy.deepcopy(unknown), historical_service_response=copy.deepcopy(historical),
            business_invariance=dict(equal=before == after, before_sha256=digest(before), after_sha256=digest(after),
                tables={k: len(v) for k, v in before.items()}, confirmed_version=version),
            audit_runs=dict(before=audit_before, after=audit_after), separate_connection_verified=True,
            actual_api_calls=0, response_policy='Full returned objects captured in this execution; parsed answer is labeled separately.')

    def test_superseded_operational_draft_never_substitutes_unapproved_revision(self):
        draft = self.draft()['data']
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertTrue(self.review(did, dv, 'cq02-review-old')['ok'])
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            revised = request.call('prepare_draft', dict(operations=copy.deepcopy(self.stored_draft_response['data']['operations']),
                request_key='cq02-revised', draft_id=did, expected_version=dv))
        self.assertTrue(revised['ok'], revised)
        next_version = revised['data']['draft_version']
        self.assertEqual(dv + 1, next_version)
        self.error_is(self.review(did, dv, 'cq02-review-superseded'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, dv, request_key='cq02-apply-old'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, next_version, request_key='cq02-apply-unapproved'), 'APPROVAL_REQUIRED')
        self.error_is(self.host.apply_approved(did, 999, request_key='cq02-apply-absent'), 'DRAFT_NOT_FOUND')
        self.assertIsNone(self.query(self.before_version)['data']['states'])
        self.assertEqual(self.before_version, self.service.lookup(self.reader.actor)['data_version'])
        self.assertTrue(self.review(did, next_version, 'cq02-review-new')['ok'])
        applied = self.host.apply_approved(did, next_version, request_key='cq02-apply-new')
        self.assertTrue(applied['ok'], applied)
        self.assertEqual(next_version, applied['data']['draft_version'])
        self.assertEqual(self.expected['states'], self.query(applied['data_version'])['data']['states'])

    def test_registered_read_agent_cannot_mutate_or_forge_even_a_reviewer_identity(self):
        self.draft()
        before = self.rows()
        for user in (self.reader, self.author):
            for tool, arguments, code in [('prepare_draft', {}, 'TOOL_NOT_ALLOWED'),
                    ('review', {}, 'TOOL_NOT_ALLOWED'), ('apply', {}, 'TOOL_NOT_ALLOWED'),
                    ('lookup', {'actor': self.reviewer.actor}, 'INVALID_TOOL_ARGUMENTS')]:
                with self.subTest(user=user.actor, tool=tool):
                    def provider(**kwargs):
                        return dict(text='', tool_calls=[dict(name=tool, arguments=arguments)])
                    result = self.run_agent(self.task(self.before_version), provider, user)
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(code, result['error']['code'])
                    self.assertEqual([], result['tool_trace'])
        self.assertEqual(before, self.rows())
        self.assertEqual(self.before_version, self.service.lookup(self.reader.actor)['data_version'])


if __name__ == '__main__':
    unittest.main()
