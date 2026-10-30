"""CQ01 input promise over the real common host, with scripted providers only."""
import copy
import json
import unittest

from examples.order_fulfillment import b_promise_agent as promise
from ontologics.contracts import AgentTask
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.service import DomainError, Service
from tests.db import database_for_test


def input_nodes():
    return json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + promise.load_demo_contract()


def task_for(nodes=None):
    return promise.make_promise_task(input_nodes() if nodes is None else nodes,
        'delivery-round:demo:B-01:1', input_revision='B-01-v1', data_version=1)


class PromiseInputTests(unittest.TestCase):
    def test_task_freezes_input_and_keeps_unregistered_sources_separate(self):
        nodes = input_nodes()
        task = task_for(nodes)
        next(n for n in nodes if n['type'] == 'DeliveryRound')['quantity'] = 5
        data = task.data()
        self.assertEqual(1000, data['promise']['quantity'])
        self.assertEqual('unregistered', data['promise']['input_state'])
        self.assertFalse(data['promise']['fulfillment_assessed'])
        self.assertEqual('synthetic', data['input_sources'][0]['origin'])
        self.assertEqual((), task.context.evidence_ids)
        self.assertLess(len(promise.canonical(data).encode()), 100000)
        self.assertEqual(frozenset({'lookup'}), promise.SPEC.requested_tools)

    def test_changed_payload_cannot_reuse_pinned_input_context(self):
        task = task_for()
        data = task.data()
        data['promise']['quantity'] = 1
        with self.assertRaises(ValueError):
            promise.PromiseAgent().messages(AgentTask(data, task.context))

    def test_bad_b_input_or_implicit_revision_fails_before_agent_execution(self):
        with self.assertRaises(ValueError):
            promise.run_scripted_example(None)
        for revision in ('', ' ', None):
            with self.assertRaises(ValueError):
                promise.make_promise_task(input_nodes(), 'delivery-round:demo:B-01:1',
                                          input_revision=revision, data_version=1)
        nodes = input_nodes()
        next(n for n in nodes if n['type'] == 'DeliveryRound')['quantity'] = -1
        with self.assertRaises(ValueError):
            task_for(nodes)


class PromiseCommonHostTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.audit = Service(self.database, principals={
            'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        self.addCleanup(self.audit.close)
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        seeded = self.audit.bootstrap('seed', catalog, request_key='catalog-only')
        self.assertTrue(seeded['ok'], seeded)
        self.task = task_for()
        self.role = promise.PromiseAgent()
        identities = DevelopmentIdentityProvider([
            UserIdentity('reader', frozenset({'read'})), UserIdentity('denied', frozenset())], enabled=True)
        self.host = CommonHost(identities, self.database,
            agent_registrations=(AgentRegistration.from_spec(promise.SPEC),))

    def run_provider(self, provider):
        with self.host.request(DevelopmentContext('reader')) as request:
            return request.run_task(promise.SPEC.name, self.role, self.task, provider,
                                     config=promise.CONFIG, capture_tool_trace=True)

    def test_example_runs_with_actual_common_reads_and_runtime_audit(self):
        result = promise.run_scripted_example(self.database)
        self.assertTrue(result['runtime']['ok'], result['runtime'])
        self.assertIsNone(result['error'])
        self.assertEqual('b_promise', result['agent_name'])
        self.assertEqual(1000, result['payload']['promise']['quantity'])
        self.assertIn('미등록', result['payload']['answer_ko'])
        self.assertTrue(result['evidence_refs'])
        self.assertTrue(all(ref['origin'] == 'real' for ref in result['evidence_refs']))
        self.assertFalse(any(ref['source_id'].startswith('source:demo:B-01') for ref in result['evidence_refs']))
        self.assertEqual(1, self.audit._version())
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertFalse(any(node['type'] in {'Contract', 'ContractItem', 'DeliveryRound'}
                             for node in self.audit._nodes()))
        row = self.audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                                   (result['runtime']['run_id'],)).fetchone()
        self.assertEqual('user:dev-b-promise-reader', row['actor'])
        config = json.loads(row['config'])
        self.assertEqual(['lookup'], config['allowed_tools'])
        self.assertEqual('b_promise', config['agent_contract']['agent_name'])
        self.assertTrue(row['output_hash'])
        self.assertEqual(2, result['request_execution_budget']['attempted_calls']['model'])

    def test_wrong_model_promise_cannot_replace_b_result(self):
        for field, value in (('quantity', 1), ('due_at', '2027-01-01T00:00:00+09:00'),
                             ('configuration_id', 'other'), ('input_state', 'confirmed'),
                             ('fulfillment_assessed', True)):
            with self.subTest(field=field):
                self.assert_business_fields_preserved(lambda output: output['promise'].update({field: value}))
        self.assert_rejected(lambda output: output.update(evidence_ids=['evidence:demo:B-01:round']),
                             code='AGENT_EVIDENCE_MISMATCH')

    def mutated_result(self, mutate, *, business_echo=False):
        scripted = promise.ScriptedPromiseProvider(self.task)
        calls = []
        def provider(**kwargs):
            calls.append(1)
            response = scripted(**kwargs)
            if response['text']:
                output = json.loads(response['text'])
                if business_echo:
                    output['promise'] = copy.deepcopy(self.task.data()['promise'])
                mutate(output)
                response['text'] = json.dumps(output, ensure_ascii=False)
            return response
        result = self.run_provider(provider)
        self.assertEqual(2, len(calls))
        return result

    def assert_rejected(self, mutate, *, code='AGENT_RESULT_REJECTED'):
        result = self.mutated_result(mutate)
        self.assertEqual(code, result['error']['code'])
        self.assertIsNone(result['payload'])
        self.assertEqual([], result['evidence_refs'])

    def assert_business_fields_preserved(self, mutate):
        result = self.mutated_result(mutate, business_echo=True)
        self.assertTrue(result['runtime']['ok'], result['error'])
        self.assertEqual(self.task.data()['promise'], result['payload']['promise'])
        changed = copy.deepcopy(result['payload'])
        mutate(changed)
        self.assertFalse(promise.PromiseAgent().validate_result(
            changed, self.task, result['runtime']['tool_trace']))

    def test_claim_without_observed_lookup_is_rejected(self):
        data = self.task.data()
        output = {key: data[key] for key in ('promise', 'input_revision', 'input_sha256', 'input_sources')}
        output.update(data_version=1, answer_ko='미등록 입력입니다.', evidence_ids=[])
        result = self.run_provider(lambda **kw: {'text': json.dumps(output), 'tool_calls': []})
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_actual_catalog_mismatch_is_rejected(self):
        nodes = input_nodes()
        next(n for n in nodes if n['id'] == 'config:esp32-poe:L:WROOM')['name'] = 'different input catalog'
        self.task = task_for(nodes)
        result = self.run_provider(promise.ScriptedPromiseProvider(self.task))
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_three_sequential_lookups_fit_four_requests_without_retry(self):
        scripted = promise.ScriptedPromiseProvider(self.task)
        nodes = self.task.data()['catalog_nodes']
        calls = []
        def provider(**kwargs):
            calls.append(1)
            read_count = sum(message['role'] == 'tool' for message in kwargs['messages'])
            if read_count < len(nodes):
                return {'text': '', 'tool_calls': [dict(name='lookup', arguments=dict(
                    node_id=nodes[read_count]['id'], version=1))]}
            return scripted(**kwargs)
        result = self.run_provider(provider)
        self.assertTrue(result['runtime']['ok'], result['runtime'])
        self.assertEqual(4, len(calls))
        self.assertEqual(4, result['request_execution_budget']['attempted_calls']['model'])

    def test_host_permission_check_precedes_provider(self):
        calls = []
        with self.host.request(DevelopmentContext('denied')) as request:
            with self.assertRaises(DomainError) as denied:
                request.run_task(promise.SPEC.name, self.role, self.task,
                    lambda **kw: calls.append(kw), config=promise.CONFIG)
        self.assertEqual('FORBIDDEN', denied.exception.code)
        self.assertEqual([], calls)

    def test_forbidden_tool_stops_without_later_model_call(self):
        calls = []
        def provider(**kwargs):
            calls.append(1)
            return {'text': '', 'tool_calls': [dict(name='prepare_draft', arguments={})]}
        result = self.run_provider(provider)
        self.assertFalse(result['runtime']['ok'])
        self.assertEqual(1, len(calls))
        self.assertIsNone(result['payload'])
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])


if __name__ == '__main__':
    unittest.main()
