"""C claim response role is registered through the existing host seam only."""
import copy
import json
from pathlib import Path
import unittest

from ontologics.claim_response.registration import CLAIM_RESPONSE, CLAIM_RESPONSE_TOOLS
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


if __name__ == '__main__':
    unittest.main()
