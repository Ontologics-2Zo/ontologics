"""Trusted extension registration reuses the host; no live model/auth claim."""
import copy
import json
from pathlib import Path
import unittest

from ontologics.database import DatabaseSettings
from ontologics.demo import fixture_nodes
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.runtime import ModelConfig
from ontologics.service import Service
from ontologics.tool_specs import function_tools
from tests.db import database_for_test


class RegistrationTests(unittest.TestCase):
    def test_only_distinct_trusted_roles_and_existing_nonapproval_tools_are_registered(self):
        for name, instructions, names in (
            ('product', 'custom', ['lookup']), ('../role', 'custom', ['lookup']),
            ('role', '', ['lookup']), ('role', ' ' * 16001 + 'x', ['lookup']),
            ('role', 'custom', []), ('role', 'custom', 'lookup'),
            ('role', 'custom', ['lookup', 'lookup']),
            *[('role', 'custom', [tool]) for tool in ('review', 'apply', 'bootstrap', 'run_history', 'unknown')],
        ):
            with self.subTest(name=name, tools=names), self.assertRaises(ValueError):
                AgentRegistration(name, instructions, names)
        tools = ['lookup']
        definition = AgentRegistration('extension_demo', 'Read supplied evidence only.', tools)
        tools.append('prepare_draft')
        self.assertEqual(frozenset({'lookup'}), definition.allowed_tools)
        self.assertEqual(['lookup'], [tool['name'] for tool in function_tools(definition.allowed_tools)])
        identities = DevelopmentIdentityProvider([], enabled=True)
        database = DatabaseSettings('postgresql://unused/example', 'test_extension_registration')
        for registrations in ((definition, definition), ({'name': 'injected'},), {'role': definition}):
            with self.assertRaises(ValueError):
                CommonHost(identities, database, agent_registrations=registrations)
        host = CommonHost(identities, database, agent_registrations=(definition,))
        with self.assertRaises(TypeError):
            host._agent_registrations['another'] = definition


class ToolProvider:
    """One selected tool followed by a fixed explanation, never a real model."""
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments
        self.messages = []

    def public_config(self):
        return {'provider': 'scripted-extension-contract'}

    def __call__(self, *, model, messages):
        self.messages.append(copy.deepcopy(messages))
        if len(self.messages) == 1:
            return {'text': '', 'tool_calls': [{'name': self.name, 'arguments': self.arguments}]}
        return {'text': 'The scoped common-tool response is available in the trace; this is a test double.',
                'tool_calls': []}


class ExtensionHostTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.alice = UserIdentity('dev-alice', frozenset({'read', 'draft', 'review'}))
        self.reader = UserIdentity('dev-reader', frozenset({'read'}))
        identities = DevelopmentIdentityProvider([self.alice, self.reader], enabled=True)
        self.lookup = AgentRegistration('extension_lookup', 'Read the supplied version and preserve evidence.', ['lookup'])
        self.drafter = AgentRegistration('extension_draft', 'Create an unapproved evidence-backed draft only.',
                                         ['prepare_draft', 'get_draft'])
        self.host = CommonHost(identities, self.database, agent_registrations=(self.lookup, self.drafter))
        self.basic_host = CommonHost(identities, self.database)
        self.audit = Service(self.database, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read', 'audit']}})
        self.addCleanup(self.audit.close)
        catalog = json.loads((Path(__file__).resolve().parents[1] / 'data/catalog.json').read_text())['nodes']
        fictional, self.supplement = fixture_nodes()
        self.assertTrue(self.audit.bootstrap('fixture', catalog + fictional, request_key='extension-fixture')['ok'])
        self.config = ModelConfig(model='scripted-extension-contract')

    def run_role(self, role, user, provider):
        with self.host.request(DevelopmentContext(user.subject)) as session:
            return session.run_agent(role, provider, [{'role': 'user', 'content': 'Use the supplied common tool.'}],
                                     config=self.config, capture_tool_trace=True)

    def test_registered_runtime_has_bound_actor_instructions_and_narrow_tool_scope(self):
        arguments = {'kind': 'ProductModel', 'version': 1}
        provider = ToolProvider('lookup', arguments)
        result = self.run_role('extension_lookup', self.alice, provider)
        self.assertTrue(result['ok'], result)
        self.assertIn(self.lookup.instructions, provider.messages[0][0]['content'])
        self.assertEqual('system', provider.messages[0][0]['role'])
        self.assertEqual(arguments, result['tool_trace'][0]['arguments'])
        with self.host.request(DevelopmentContext(self.alice.subject)) as request:
            direct = request.call('lookup', arguments)
            self.assertEqual(direct['data'], result['tool_trace'][0]['result']['data'])
            self.assertEqual(1, result['tool_trace'][0]['result']['data_version'])
            self.assertTrue(request.agent('quality').call('lookup', arguments)['ok'])
        row = self.audit.db.execute('SELECT actor,status,config FROM runtime_runs WHERE id=%s', (result['run_id'],)).fetchone()
        self.assertEqual(self.alice.actor, row['actor'])
        self.assertEqual('completed', row['status'])
        self.assertEqual(['lookup'], json.loads(row['config'])['allowed_tools'])
        with self.basic_host.request(DevelopmentContext(self.alice.subject)) as request:
            with self.assertRaises(ValueError):
                request.agent('extension_lookup')
        for name, args, code in (
            ('review', {}, 'TOOL_NOT_ALLOWED'), ('prepare_draft', {}, 'TOOL_NOT_ALLOWED'),
            ('lookup', {'kind': 'ProductModel', 'actor': 'user:dev-bob'}, 'INVALID_TOOL_ARGUMENTS'),
        ):
            rejected = self.run_role('extension_lookup', self.alice, ToolProvider(name, args))
            self.assertFalse(rejected['ok'])
            self.assertEqual(code, rejected['error']['code'])
            self.assertEqual([], rejected['tool_trace'])
        self.assertEqual(1, self.audit._version())

    def test_extension_draft_permission_never_escalates_user_or_approves_data(self):
        arguments = {'operations': self.supplement, 'request_key': 'extension-supplement'}
        denied = self.run_role('extension_draft', self.reader, ToolProvider('prepare_draft', arguments))
        self.assertFalse(denied['ok'])
        self.assertEqual('FORBIDDEN', denied['error']['code'])
        created = self.run_role('extension_draft', self.alice, ToolProvider('prepare_draft', arguments))
        self.assertTrue(created['ok'], created)
        draft = created['tool_trace'][0]['result']['data']
        replayed = self.run_role('extension_draft', self.alice, ToolProvider('prepare_draft', arguments))
        self.assertEqual(draft['draft_id'], replayed['tool_trace'][0]['result']['data']['draft_id'])
        with self.host.request(DevelopmentContext(self.alice.subject)) as request:
            record = request.agent('extension_draft').call('get_draft',
                {'draft_id': draft['draft_id'], 'version': draft['draft_version']})['data']
            self.assertEqual(self.alice.actor, record['author'])
            self.assertEqual('awaiting_review', record['status'])
            self.assertIsNone(record['reviewer'])
            for tool in ('review', 'apply', 'bootstrap'):
                self.assertEqual('TOOL_NOT_ALLOWED', request.agent('extension_draft').call(tool, {})['errors'][0]['code'])
            decision = request.review(draft['draft_id'], draft['draft_version'], decision='approve',
                                      note='Extension remains bound to Alice', request_key='extension-self-review')
            self.assertEqual('SELF_APPROVAL_FORBIDDEN', decision['errors'][0]['code'])
        self.assertEqual(1, self.audit._version())
        self.assertEqual('APPROVAL_REQUIRED', self.host.apply_approved(draft['draft_id'], draft['draft_version'],
                         request_key='extension-unapproved')['errors'][0]['code'])

    def test_distinct_typed_flows_share_tools_evidence_and_time_results(self):
        from examples.extension_agent import run_contract_example
        from ontologics.demo import example_queries
        model = run_contract_example(self.database, version=1, flow='model')
        history = run_contract_example(self.database, version=1, flow='history')
        for result in (model, history):
            self.assertTrue(result['runtime']['ok'], result)
            self.assertEqual('agent-result-1', result['schema_version'])
            self.assertEqual(1, result['context']['data_version'])
            self.assertIsNone(result['error'])
            self.assertTrue(result['evidence_refs'])
            self.assertTrue(all(ref['data_version'] == 1 and ref['source_sha256']
                                for ref in result['evidence_refs']))
        self.assertEqual(['lookup'], [r['name'] for r in model['runtime']['tool_trace']])
        self.assertEqual(['investigate', 'investigate'], [r['name'] for r in history['runtime']['tool_trace']])
        self.assertEqual(2, model['runtime']['execution_budget']['attempted_calls']['model'])
        self.assertEqual(3, history['runtime']['execution_budget']['attempted_calls']['model'])
        with self.host.request(DevelopmentContext(self.reader.subject)) as request:
            for mode, query in example_queries().items():
                direct = request.call('investigate', {'query': query, 'version': 1})
                self.assertEqual(direct['data'], history['payload']['by_mode'][mode])
                by_unit = {r['unit_id']: r for r in direct['data']['results']}
                self.assertEqual('needs_confirmation', by_unit['unit:U106']['status'])
        self.assertEqual(1, self.audit._version())

    def test_role_output_cannot_drop_evidence_or_replace_common_AB_judgement(self):
        from examples.extension_agent import run_contract_example
        from examples.agents.history_reader import ScriptedHistoryReader
        from ontologics.contracts import AgentTask
        from ontologics.harness import PinnedContext
        from ontologics.demo import example_queries
        task = AgentTask({'purpose': 'Read', 'modes': ['A', 'B']},
                         PinnedContext('Read', 1, conditions={'queries': example_queries()}))
        class MisleadingProvider(ScriptedHistoryReader):
            def __call__(self, **kwargs):
                response = super().__call__(**kwargs)
                if not response['tool_calls']:
                    value = json.loads(response['text'])
                    next(r for r in value['by_mode']['A']['results']
                         if r['unit_id'] == 'unit:U106')['status'] = 'excluded'
                    response['text'] = json.dumps(value)
                return response
        result = run_contract_example(self.database, version=1, flow='history', provider=MisleadingProvider(task))
        self.assertFalse(result['runtime']['ok'])
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])
        self.assertIsNone(result['payload'])
        self.assertEqual(0, result['runtime']['repairs'])
        from examples.agents.model_reader import ScriptedModelReader
        for flow, base in [('model', ScriptedModelReader), ('history', ScriptedHistoryReader)]:
            with self.subTest(missing_evidence=flow):
                class MissingEvidence(base):
                    def __call__(self, **kwargs):
                        response = super().__call__(**kwargs)
                        if not response['tool_calls']:
                            value = json.loads(response['text'])
                            value['evidence_ids'] = []
                            response['text'] = json.dumps(value)
                        return response
                result = run_contract_example(self.database, version=1, flow=flow, provider=MissingEvidence(task))
                self.assertFalse(result['runtime']['ok'])
                self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])
                self.assertIsNone(result['payload'])


if __name__ == '__main__':
    unittest.main()
