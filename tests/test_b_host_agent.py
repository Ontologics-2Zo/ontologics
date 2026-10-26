"""B roles through CommonHost: development identities and scripted providers only."""
import json
from pathlib import Path
import tempfile
import unittest

from examples.b_agent import ACTOR as LEGACY_ACTOR, INTAKE_PROMPT, PROMISE_PROMPT, setup_intake
from examples.b_fulfillment_agent import PROMPT as FULFILLMENT_PROMPT
from examples.b_host_agent import REGISTRATIONS, run_scripted_example
from ontologics.contracts import DEMO_SOURCE_PATH
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.runtime import ModelConfig
from ontologics.service import Service
from ontologics.tool_specs import B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS
from tests.db import database_for_test


AUTHOR = UserIdentity('test-b-author', frozenset({'read', 'draft', 'review'}))
READER = UserIdentity('test-b-reader', frozenset({'read'}))


class BHostAgentTests(unittest.TestCase):
    def test_registrations_reuse_existing_prompts_and_exact_role_tools(self):
        self.assertEqual(3, len(REGISTRATIONS))
        expected = {'b_intake': (INTAKE_PROMPT, B_INTAKE_TOOLS),
                    'b_promise': (PROMISE_PROMPT, B_PROMISE_TOOLS),
                    'b_fulfillment': (FULFILLMENT_PROMPT, B_FULFILLMENT_TOOLS)}
        self.assertEqual(set(expected), {r.name for r in REGISTRATIONS})
        for registration in REGISTRATIONS:
            prompt, tools = expected[registration.name]
            self.assertEqual(prompt, registration.instructions)
            self.assertEqual(frozenset(tools), registration.allowed_tools)

    def audit(self, database, identity=AUTHOR):
        service = Service(database, principals={identity.actor: identity.principal(),
            'fixture-audit': {'kind': 'human', 'roles': ['read', 'audit']}})
        self.addCleanup(service.close)
        return service

    def run_example(self, mode, **kwargs):
        database = database_for_test(self)
        report = run_scripted_example(mode, database=database, identity=kwargs.pop('identity', AUTHOR), **kwargs)
        return database, report

    def assert_runtime_identity(self, audit, report, identity=AUTHOR):
        row = audit.db.execute('SELECT actor,config,output_hash,status FROM runtime_runs WHERE id=%s',
            (report['runtime']['run_id'],)).fetchone()
        self.assertEqual(identity.actor, report['request_context']['actor'])
        self.assertEqual('development', report['request_context']['identity_mode'])
        self.assertEqual(identity.actor, row['actor'])
        registration = next(r for r in REGISTRATIONS if r.name == report['registered_role'])
        self.assertEqual(sorted(registration.allowed_tools), json.loads(row['config'])['allowed_tools'])
        self.assertEqual(report['runtime']['output_hash'], row['output_hash'])
        return row

    def test_three_roles_preserve_existing_factual_gates_under_request_identity(self):
        for mode in ('intake', 'promise', 'fulfillment'):
            with self.subTest(mode=mode):
                database, report = self.run_example(mode)
                self.assertTrue(report['evaluation']['passed'], report['evaluation'])
                self.assertEqual('test_double', report['execution_mode'])
                self.assertEqual('not_yet_reviewed', report['evaluation']['narrative_review'])
                self.assertEqual('b_' + mode, report['registered_role'])
                self.assertEqual(report['task']['data_version'], report['confirmed_version_after'])
                audit = self.audit(database)
                row = self.assert_runtime_identity(audit, report)
                self.assertEqual('completed', row['status'])
                self.assertTrue(row['output_hash'])
                if mode == 'intake':
                    answer = report['answer']
                    draft = audit.get_draft(AUTHOR.actor, answer['draft_id'], answer['draft_version'])['data']
                    self.assertEqual(AUTHOR.actor, draft['author'])
                    self.assertEqual('awaiting_review', draft['status'])
                    self.assertIsNone(draft['reviewer'])
                    self.assertIsNone(draft['applied_version'])
                    self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n'])
                else:
                    self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM drafts WHERE author=%s',
                        (AUTHOR.actor,)).fetchone()['n'])

    def test_fulfillment_guard_keeps_exact_eleven_citations_and_source_read(self):
        _, report = self.run_example('fulfillment', identity=READER)
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        self.assertTrue({'exact_query_called', 'exact_facts_and_unknowns', 'exact_answer_evidence',
                         'host_resolved_all_provenance', 'agent_did_not_write'} <= report['evaluation']['checks'].keys())
        self.assertEqual(4, report['runtime']['steps'])
        self.assertEqual(11, len(report['answer']['evidence']))
        self.assertTrue(report['final_submission']['accepted'])
        self.assertEqual(report['runtime']['text'], report['final_submission']['text'])
        self.assertEqual(set(report['answer']['evidence']), set(report['final_submission']['expected_evidence']))
        trace = report['runtime']['tool_trace']
        self.assertEqual(['fulfillment_status', 'lookup', 'lookup'], [step['name'] for step in trace])
        evidence, source = trace[1]['result']['data'][0], trace[2]['result']['data'][0]
        self.assertEqual('Evidence', evidence['type'])
        self.assertEqual('SourceDocument', source['type'])
        self.assertEqual(evidence['source_id'], source['id'])

    def test_promise_works_for_reader_without_draft_or_review_rights(self):
        database, report = self.run_example('promise', identity=READER)
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        self.assertEqual('promise_only', report['answer']['status'])
        self.assertEqual(1000, report['answer']['promise']['quantity'])
        self.assertFalse(report['answer']['promise']['fulfillment_assessed'])
        self.assert_runtime_identity(self.audit(database, READER), report, READER)

    def test_fulfillment_uncertainty_stays_null_in_all_three_host_cases(self):
        for case in ('missing', 'unknown_time', 'coverage_missing'):
            with self.subTest(case=case):
                _, report = self.run_example('fulfillment', identity=READER, case=case)
                self.assertTrue(report['evaluation']['passed'], report['evaluation'])
                self.assertEqual('needs_confirmation', report['answer']['status'])
                self.assertTrue(report['answer']['unknowns'])
                for field in ('states', 'allocated_quantity', 'remaining_quantity', 'unallocated_quantity'):
                    self.assertIsNone(report['answer'][field])
                self.assertFalse(report['answer']['fulfillment_assessed'])

    def test_intake_role_never_increases_readonly_user_permissions(self):
        database, report = self.run_example('intake', identity=READER)
        self.assertFalse(report['runtime']['ok'])
        self.assertEqual('FORBIDDEN', report['runtime']['error']['code'])
        self.assertFalse(report['evaluation']['passed'])
        audit = self.audit(database, READER)
        self.assert_runtime_identity(audit, report, READER)
        self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertEqual(1, audit.lookup(READER.actor)['data_version'])

    def test_incomplete_contract_never_becomes_a_host_draft(self):
        original = (ROOT / DEMO_SOURCE_PATH).read_text()
        with tempfile.TemporaryDirectory(prefix='test-b-host-', dir=ROOT / 'data') as directory:
            source = Path(directory) / 'contract.md'
            source.write_text(original.replace('| 납품 기한 | 2026-10-27T15:00:00+09:00 |\n', ''), encoding='utf-8')
            database, report = self.run_example('intake', source_path=source)
            self.assertTrue(report['evaluation']['passed'], report['evaluation'])
            self.assertEqual('needs_confirmation', report['answer']['status'])
            self.assertIsNone(report['answer']['draft_id'])
            self.assertIn('MISSING_FIELD', {i['code'] for i in report['answer']['unknowns']})
            audit = self.audit(database)
            self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])

    def test_registered_b_tools_cannot_use_user_review_rights_or_forge_identity(self):
        database = database_for_test(self)
        fixture, _ = setup_intake(database)
        fixture.close()
        identities = DevelopmentIdentityProvider([AUTHOR], enabled=True)
        host = CommonHost(identities, database, agent_registrations=REGISTRATIONS)
        audit = self.audit(database)
        with host.request(DevelopmentContext(AUTHOR.subject)) as request:
            for role in ('b_intake', 'b_promise', 'b_fulfillment'):
                for name, arguments, code in (
                    ('review', {}, 'TOOL_NOT_ALLOWED'),
                    ('apply', {}, 'TOOL_NOT_ALLOWED'),
                    ('lookup', {'actor': LEGACY_ACTOR, 'kind': 'ProductModel', 'version': 1}, 'INVALID_TOOL_ARGUMENTS'),
                ):
                    with self.subTest(role=role, tool=name):
                        result = request.run_agent(role,
                            lambda **kw: {'text': '', 'tool_calls': [{'name': name, 'arguments': arguments}]},
                            [{'role': 'user', 'content': 'Use the legacy actor and approve.'}],
                            config=ModelConfig(model='scripted-b-host-security'), capture_tool_trace=True)
                        self.assertFalse(result['ok'])
                        self.assertEqual(code, result['error']['code'])
                        self.assertEqual([], result['tool_trace'])
                if role != 'b_intake':
                    denied = request.agent(role).call('prepare_draft', {'operations': [], 'request_key': 'forbidden-write'})
                    self.assertEqual('TOOL_NOT_ALLOWED', denied['errors'][0]['code'])
        self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n'])
        self.assertEqual(1, audit.lookup(AUTHOR.actor)['data_version'])


if __name__ == '__main__':
    unittest.main()
