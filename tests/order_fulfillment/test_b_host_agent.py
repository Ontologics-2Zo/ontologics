"""B roles through CommonHost: development identities and scripted providers only."""
import json
from pathlib import Path
import tempfile
import unittest

from examples.order_fulfillment.b_agent import ACTOR as LEGACY_ACTOR, INTAKE_PROMPT, PROMISE_PROMPT, setup_intake
from examples.order_fulfillment.b_fulfillment_agent import PROMPT as FULFILLMENT_PROMPT
from examples.order_fulfillment.b_production_agent import PROMPT as PRODUCTION_PROMPT
from examples.order_fulfillment.b_delivery_agent import PROMPT as DELIVERY_PROMPT
from examples.order_fulfillment.b_response_agent import PROMPT as RESPONSE_PROMPT
from examples.order_fulfillment.b_comparison_agent import PROMPT as COMPARISON_PROMPT
from examples.order_fulfillment.b_host_agent import REGISTRATIONS, run_scripted_example
from ontologics.order_fulfillment.contracts import DEMO_SOURCE_PATH
from ontologics.order_fulfillment.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.order_fulfillment.runtime import ModelConfig
from ontologics.order_fulfillment.service import Service
from ontologics.order_fulfillment.tool_specs import B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS, B_PRODUCTION_TOOLS, B_DELIVERY_TOOLS, B_RESPONSE_TOOLS, B_COMPARISON_TOOLS
from tests.db import database_for_test


AUTHOR = UserIdentity('test-b-author', frozenset({'read', 'draft', 'review'}))
READER = UserIdentity('test-b-reader', frozenset({'read'}))


class BHostAgentTests(unittest.TestCase):
    def test_registrations_reuse_existing_prompts_and_exact_role_tools(self):
        self.assertEqual(7, len(REGISTRATIONS))
        expected = {'b_intake': (INTAKE_PROMPT, B_INTAKE_TOOLS),
                    'b_promise': (PROMISE_PROMPT, B_PROMISE_TOOLS),
                    'b_fulfillment': (FULFILLMENT_PROMPT, B_FULFILLMENT_TOOLS),
                    'b_production': (PRODUCTION_PROMPT, B_PRODUCTION_TOOLS),
                    'b_delivery': (DELIVERY_PROMPT, B_DELIVERY_TOOLS),
                    'b_response': (RESPONSE_PROMPT, B_RESPONSE_TOOLS),
                    'b_comparison': (COMPARISON_PROMPT, B_COMPARISON_TOOLS)}
        self.assertEqual(set(expected), {r.name for r in REGISTRATIONS})
        for registration in REGISTRATIONS:
            prompt, tools = expected[registration.name]
            self.assertEqual(prompt, registration.instructions)
            self.assertEqual(frozenset(tools), registration.allowed_tools)

    def test_comparison_role_is_read_only_and_preserves_both_approved_versions(self):
        self.assertIn('b_comparison', {r.name for r in REGISTRATIONS})
        database, report = self.run_example('comparison', identity=READER, case='defects11')
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        self.assertEqual('b_comparison', report['registered_role'])
        self.assertEqual(report['task']['after_version'], report['confirmed_version_after'])
        self.assertNotEqual(report['task']['before_version'], report['task']['after_version'])
        self.assertEqual((920, 80), tuple(report['answer']['after'][key]
                                        for key in ('on_time_quantity', 'shortfall_quantity')))
        audit = self.audit(database, READER)
        self.assertEqual('completed', self.assert_runtime_identity(audit, report, READER)['status'])
        self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM drafts WHERE author=%s',
                         (READER.actor,)).fetchone()['n'])
        self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM review_events WHERE actor=%s',
                         (READER.actor,)).fetchone()['n'])

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

    def test_five_roles_preserve_factual_gates_under_request_identity(self):
        for mode in ('intake', 'promise', 'fulfillment', 'production', 'delivery'):
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

    def test_response_reader_preserves_request_binding_and_exact_selected_evidence(self):
        for case, new_quantity, evidence_count in [('normal', 1000, 8), ('mixed', 750, 13)]:
            with self.subTest(case=case):
                database, report = self.run_example('response', identity=READER, case=case)
                self.assertTrue(report['evaluation']['passed'], report['evaluation'])
                self.assertEqual('b_response', report['registered_role'])
                self.assertEqual(4, report['runtime']['steps'])
                answer = report['answer']
                self.assertEqual(new_quantity, answer['dispatch_quantities']['new_dispatch_quantity'])
                self.assertEqual(evidence_count, len(answer['evidence']))
                self.assertTrue(answer['draft_only'] and answer['review_required'])
                self.assertFalse(answer['execution_performed'])
                self.assertEqual(report['runtime']['text'], report['final_submission']['text'])
                self.assertTrue(report['final_submission']['accepted'])
                self.assertEqual(['response_plan', 'lookup_many', 'lookup_many'],
                                 [t['name'] for t in report['runtime']['tool_trace']])
                self.assert_runtime_identity(self.audit(database, READER), report, READER)
                self.assertEqual(0, report['api_requests'])

    def test_response_unknowns_and_other_roles_keep_read_only_limits(self):
        database, report = self.run_example('response', identity=READER, case='missing_condition')
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        self.assertEqual('needs_confirmation', report['answer']['response_state'])
        self.assertTrue(all(v is None for v in report['answer']['dispatch_quantities'].values()))
        identities = DevelopmentIdentityProvider([AUTHOR], enabled=True)
        host = CommonHost(identities, database, agent_registrations=REGISTRATIONS)
        with host.request(DevelopmentContext(AUTHOR.subject)) as session:
            for role in ('product', 'b_intake', 'b_promise', 'b_fulfillment', 'b_production', 'b_delivery'):
                result = session.agent(role).call('response_plan', {})
                self.assertEqual('TOOL_NOT_ALLOWED', result['errors'][0]['code'])
            for name in ('prepare_draft', 'review', 'apply'):
                result = session.agent('b_response').call(name, {})
                self.assertEqual('TOOL_NOT_ALLOWED', result['errors'][0]['code'])
            forged = session.agent('b_response').call('response_plan', {'actor': 'reviewer'})
            self.assertEqual('INVALID_TOOL_ARGUMENTS', forged['errors'][0]['code'])
        denied = UserIdentity('response-denied', frozenset())
        host = CommonHost(DevelopmentIdentityProvider([denied], enabled=True), database, agent_registrations=REGISTRATIONS)
        with host.request(DevelopmentContext(denied.subject)) as session:
            task = report['task']
            result = session.agent('b_response').call('response_plan', dict(delivery_round_id=task['delivery_round_id'],
                as_of=task['as_of'], version=task['data_version']))
            self.assertEqual('FORBIDDEN', result['errors'][0]['code'])

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

    def test_production_reader_uses_guarded_host_scope_and_leaves_facts_unchanged(self):
        database, report = self.run_example('production', identity=READER)
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        answer = report['answer']
        self.assertEqual('forecast', answer['status'])
        self.assertEqual('2026-10-27T14:26:40+09:00', answer['estimated_completion_at'])
        self.assertEqual(400, answer['remaining_quantity'])
        self.assertEqual(16000, answer['remaining_seconds'])
        self.assertIsNone(answer['actual_completion_at'])
        self.assertFalse(answer['fulfillment_assessed'])
        self.assertTrue(report['final_submission']['accepted'])
        self.assertEqual(report['runtime']['text'], report['final_submission']['text'])
        self.assertEqual(set(answer['evidence']), set(report['final_submission']['expected_evidence']))
        trace = report['runtime']['tool_trace']
        self.assertEqual('production_completion', trace[0]['name'])
        self.assertTrue(all(step['name'] in B_PRODUCTION_TOOLS for step in trace))
        lookups = [step['result']['data'][0] for step in trace if step['name'] == 'lookup']
        self.assertIn('Evidence', {node['type'] for node in lookups})
        self.assertIn('SourceDocument', {node['type'] for node in lookups})
        audit = self.audit(database, READER)
        row = self.assert_runtime_identity(audit, report, READER)
        self.assertEqual('completed', row['status'])
        work = audit.lookup(READER.actor, node_id='work:demo:CQ03:main',
                            version=report['task']['data_version'])['data'][0]
        self.assertEqual(600, work['completed_quantity'])
        self.assertEqual('in_progress', work['production_state'])
        self.assertEqual(report['task']['data_version'], report['confirmed_version_after'])
        self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM drafts WHERE author=%s',
                                           (READER.actor,)).fetchone()['n'])
        self.assertEqual(0, report['api_requests'])

    def test_production_unknown_cases_keep_null_forecasts_and_request_identity(self):
        for case, code in (('missing_latest', 'LATEST_OBSERVATION_REQUIRED'),
                           ('zero_rate', 'ZERO_GOOD_PRODUCTION_RATE')):
            with self.subTest(case=case):
                database, report = self.run_example('production', identity=READER, case=case)
                self.assertTrue(report['evaluation']['passed'], report['evaluation'])
                answer = report['answer']
                self.assertEqual('needs_confirmation', answer['status'])
                self.assertIn(code, {unknown['code'] for unknown in answer['unknowns']})
                self.assertIsNone(answer['estimated_completion_at'])
                self.assertIsNone(answer['remaining_seconds'])
                self.assertIsNone(answer['actual_completion_at'])
                self.assertFalse(answer['fulfillment_assessed'])
                self.assertEqual(report['task']['data_version'], report['confirmed_version_after'])
                self.assert_runtime_identity(self.audit(database, READER), report, READER)
                self.assertEqual(0, report['api_requests'])

    def test_production_target_observation_is_not_first_actual_completion(self):
        database, report = self.run_example('production', identity=READER, case='target_observed')
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        answer = report['answer']
        self.assertEqual('target_observed', answer['status'])
        self.assertEqual(0, answer['remaining_quantity'])
        self.assertEqual(0, answer['remaining_seconds'])
        self.assertEqual('2026-10-27T10:00:00+09:00', answer['target_observed_at'])
        self.assertIsNone(answer['estimated_completion_at'])
        self.assertIsNone(answer['actual_completion_at'])
        self.assertFalse(answer['fulfillment_assessed'])
        audit = self.audit(database, READER)
        self.assert_runtime_identity(audit, report, READER)
        work = audit.lookup(READER.actor, node_id='work:demo:CQ03:main',
                            version=report['task']['data_version'])['data'][0]
        self.assertEqual('in_progress', work['production_state'])
        self.assertEqual(1000, work['completed_quantity'])
        self.assertEqual(report['task']['data_version'], report['confirmed_version_after'])
        self.assertEqual(0, report['api_requests'])

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
            for role in ('b_intake', 'b_promise', 'b_fulfillment', 'b_production'):
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
