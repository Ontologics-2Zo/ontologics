"""C claim response role is registered through the existing host seam only."""
import copy
import json
from pathlib import Path
import unittest

from ontologics.claim_response.fixtures import claim_response_nodes
from ontologics.claim_response.intake import convert_claim
from ontologics.claim_response.registration import (
    CLAIM_RESPONSE, CLAIM_RESPONSE_TOOLS, SPEC, ClaimTraceAgent, expected_trace)
from ontologics.contracts import AgentSpec, AgentTask
from ontologics.harness import PinnedContext
from ontologics.model import ROOT
from ontologics.demo import fixture_nodes
from ontologics.host import CommonHost, ROLE_TOOLS
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.runtime import ModelConfig
from ontologics.service import AgentGateway, Service
from ontologics.tool_specs import function_tools
from tests.db import database_for_test


class ClaimResponseRegistrationTests(unittest.TestCase):
    def test_role_uses_only_approved_existing_tools(self):
        self.assertEqual('claim_response', CLAIM_RESPONSE.name)
        self.assertNotIn(CLAIM_RESPONSE.name, ROLE_TOOLS)
        self.assertEqual(CLAIM_RESPONSE_TOOLS, CLAIM_RESPONSE.allowed_tools)
        self.assertTrue(CLAIM_RESPONSE.allowed_tools <= AgentGateway.ALLOWED)
        for tool in ('review', 'apply', 'bootstrap', 'export_graph'):
            self.assertNotIn(tool, CLAIM_RESPONSE.allowed_tools)
        self.assertEqual(sorted(CLAIM_RESPONSE_TOOLS),
                         sorted(t['name'] for t in function_tools(CLAIM_RESPONSE.allowed_tools)))


class ToolProvider:
    """One selected tool followed by a fixed explanation, never a real model."""
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments
        self.messages = []

    def public_config(self):
        return {'provider': 'scripted-claim-response-test'}

    def __call__(self, *, model, messages):
        self.messages.append(copy.deepcopy(messages))
        if len(self.messages) == 1:
            return {'text': '', 'tool_calls': [{'name': self.name, 'arguments': self.arguments}]}
        return {'text': 'Scripted test double; inspect the tool trace.', 'tool_calls': []}


class ClaimResponseHostTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.alice = UserIdentity('dev-alice', frozenset({'read', 'draft', 'review'}))
        identities = DevelopmentIdentityProvider([self.alice], enabled=True)
        self.host = CommonHost(identities, self.database, agent_registrations=(CLAIM_RESPONSE,))
        self.audit = Service(self.database, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read', 'audit']}})
        self.addCleanup(self.audit.close)
        catalog = json.loads((Path(__file__).resolve().parents[2] / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']
        fictional, _ = fixture_nodes()
        self.assertTrue(self.audit.bootstrap('fixture', catalog + fictional, request_key='claim-response-fixture')['ok'])
        self.config = ModelConfig(model='scripted-claim-response-test')

    def run_role(self, provider):
        with self.host.request(DevelopmentContext(self.alice.subject)) as session:
            return session.run_agent(CLAIM_RESPONSE.name, provider,
                                     [{'role': 'user', 'content': 'Use the supplied common tool.'}],
                                     config=self.config, capture_tool_trace=True)

    def test_registered_role_reads_through_gateway_with_its_instructions(self):
        provider = ToolProvider('lookup', {'kind': 'ProductUnit', 'version': 1})
        result = self.run_role(provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual('system', provider.messages[0][0]['role'])
        self.assertIn(CLAIM_RESPONSE.instructions, provider.messages[0][0]['content'])
        units = result['tool_trace'][0]['result']['data']
        self.assertTrue(units)
        self.assertTrue(all(node['type'] == 'ProductUnit' for node in units))

    def test_unlisted_and_approval_tools_are_rejected(self):
        for name in ('review', 'apply', 'export_graph', 'compare_configurations'):
            with self.subTest(tool=name):
                rejected = self.run_role(ToolProvider(name, {}))
                self.assertFalse(rejected['ok'])
                self.assertEqual('TOOL_NOT_ALLOWED', rejected['error']['code'])
                self.assertEqual([], rejected['tool_trace'])
        self.assertEqual(1, self.audit._version())


class ClaimTraceSpecTests(unittest.TestCase):
    """5A-4: the role is defined by a versioned AgentSpec and registered from it."""
    def test_registration_is_built_from_the_versioned_spec(self):
        self.assertIsInstance(SPEC, AgentSpec)
        self.assertEqual(('claim_response', '1.0.0'), (SPEC.name, SPEC.version))
        self.assertIs(SPEC, CLAIM_RESPONSE.spec)
        self.assertEqual(SPEC.requested_tools, CLAIM_RESPONSE.allowed_tools)
        self.assertEqual(SPEC.instructions, CLAIM_RESPONSE.instructions)
        self.assertEqual({'intake_id', 'question'}, {f.name for f in SPEC.input_schema.fields})
        with self.assertRaises(ValueError):
            SPEC.input_schema.validate({'intake_id': 'x', 'question': 'q', 'approved': True})
        with self.assertRaises(ValueError):
            AgentTask({'intake_id': 'x', 'question': 'q', 'actor': 'someone'}, PinnedContext('trace', 1))


class ScriptedTrace:
    """Asks for the three lookups, then answers; `change` edits the answer to imitate a wrong model."""
    def __init__(self, task, change=None):
        self.intake_id, self.version, self.change = task.data()['intake_id'], task.context.data_version, change

    def public_config(self):
        return {'provider': 'scripted-claim-trace-test'}

    def calls(self):
        return [{'node_id': self.intake_id, 'version': self.version},
                {'kind': 'ProductUnit', 'version': self.version},
                {'kind': 'UnitProductionRecord', 'version': self.version}]

    def __call__(self, *, model, messages):
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        if not results:
            return {'text': '', 'tool_calls': [{'name': 'lookup', 'arguments': a} for a in self.calls()]}
        reads = [{'name': 'lookup', 'arguments': a, 'result': r} for a, r in zip(self.calls(), results[-3:])]
        answer = expected_trace(self.intake_id, self.version, reads) or {
            'data_version': self.version, 'intake_id': self.intake_id, 'matched_unit_ids': [],
            'open_serials': [], 'evidence_ids': []}
        if self.change:
            answer = self.change(dict(answer))
        return {'text': json.dumps(answer), 'tool_calls': []}


class ClaimTraceTaskTests(unittest.TestCase):
    """request.run_task returns a typed result only when it matches the common tool results."""
    def setUp(self):
        database = database_for_test(self)
        self.user = UserIdentity('dev-claim-agent', frozenset({'read', 'draft'}))
        self.host = CommonHost(DevelopmentIdentityProvider([self.user], enabled=True), database,
                               agent_registrations=(CLAIM_RESPONSE,))
        seed = Service(database, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read']}})
        self.addCleanup(seed.close)
        catalog = json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']
        fictional, _ = fixture_nodes()
        claim = convert_claim(ROOT / 'data/claim_response/claims/claim_table.csv',
                              collected_at='2026-10-26T10:30:00+09:00')['nodes']
        self.intake = next(n for n in claim if n['type'] == 'ClaimIntake')
        confirmed = [dict(n, intake_state='confirmed') if n is self.intake else n for n in claim]
        # A second claim that a person has not confirmed yet (same rows, another claim number).
        self.unconfirmed = dict(self.intake, id='claim:CLM-DEMO-C-UNCONFIRMED', external_claim_id='CLM-DEMO-C-UNCONFIRMED')
        self.assertEqual('needs_review', self.unconfirmed['intake_state'])
        seeded = seed.bootstrap('fixture', catalog + fictional + claim_response_nodes() + confirmed + [self.unconfirmed],
                                request_key='claim-trace-seed')
        self.assertTrue(seeded['ok'], seeded)

    def run_trace(self, change=None, intake_id=None):
        task = AgentTask({'intake_id': intake_id or self.intake['id'], 'question': 'Trace the confirmed claim.'},
                         PinnedContext('Claim trace; no defect, cause or approval conclusion', 1))
        with self.host.request(DevelopmentContext(self.user.subject)) as request:
            return request.run_task(SPEC.name, ClaimTraceAgent(), task, ScriptedTrace(task, change),
                                    config=ModelConfig(model='scripted-claim-trace-test'), capture_tool_trace=True)

    def test_typed_result_matches_the_trace_and_cites_read_evidence(self):
        result = self.run_trace()
        self.assertTrue(result['runtime']['ok'], result)
        self.assertEqual(('agent-result-1', 'claim_response', '1.0.0'),
                         (result['schema_version'], result['agent_name'], result['agent_version']))
        payload = result['payload']
        self.assertEqual(['unit:C-D00%d' % n for n in range(1, 6)], payload['matched_unit_ids'])
        self.assertEqual([], payload['open_serials'])
        self.assertEqual('production-line:demo:C-01', payload['production_line_id'])
        self.assertEqual(1, result['context']['data_version'])
        self.assertEqual(set(payload['evidence_ids']), {ref['evidence_id'] for ref in result['evidence_refs']})
        self.assertIsNone(result['error'])

    def test_wrong_answers_are_rejected_without_a_payload(self):
        cases = {
            'dropped board': lambda a: dict(a, matched_unit_ids=a['matched_unit_ids'][1:]),
            'other line': lambda a: dict(a, production_line_id='production-line:demo:C-02'),
            'invented open serial': lambda a: dict(a, open_serials=['C-D999']),
            'missing evidence': lambda a: dict(a, evidence_ids=a['evidence_ids'][:1]),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                result = self.run_trace(change)
                self.assertFalse(result['runtime']['ok'])
                self.assertIsNone(result['payload'])
                self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_citations_must_come_from_this_task_reads(self):
        result = self.run_trace(lambda a: dict(a, evidence_ids=a['evidence_ids'] + ['evidence:not-read']))
        self.assertFalse(result['runtime']['ok'])
        self.assertIsNone(result['payload'])

    def test_unconfirmed_or_unknown_claims_are_not_traced(self):
        for intake_id, code in ((self.unconfirmed['id'], 'AGENT_RESULT_REJECTED'),
                                ('claim:CLM-DOES-NOT-EXIST', 'NOT_FOUND')):
            with self.subTest(intake_id=intake_id):
                result = self.run_trace(intake_id=intake_id)
                self.assertFalse(result['runtime']['ok'])
                self.assertIsNone(result['payload'])
                self.assertEqual(code, result['error']['code'])



class ReadMatchingTests(unittest.TestCase):
    """7-1: the real model added kind=ClaimIntake to the node_id read. That only narrows one node."""
    def test_node_read_may_name_its_kind_but_list_reads_stay_exact(self):
        from ontologics.claim_response.registration import _same_read
        node = {'node_id': 'claim:x', 'version': 3}
        self.assertTrue(_same_read(dict(node), node))
        self.assertTrue(_same_read(dict(node, kind='ClaimIntake'), node))
        self.assertFalse(_same_read(dict(node, kind='ProductUnit'), node))
        units = {'kind': 'ProductUnit', 'version': 3}
        self.assertFalse(_same_read(dict(units, limit=10), units))
        self.assertFalse(_same_read(dict(units, query='C-D'), units))

if __name__ == '__main__':
    unittest.main()
