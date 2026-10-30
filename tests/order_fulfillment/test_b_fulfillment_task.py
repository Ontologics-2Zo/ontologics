"""CQ02 input calculation and real common host; scripted providers, no API."""
import copy
import json
import unittest

from examples.order_fulfillment import b_fulfillment_task as fulfillment
from ontologics.contracts import AgentTask
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.service import DomainError, Service
from tests.db import database_for_test


EXPECTED = dict(delivered=100, in_transit=150, warehouse=250, in_progress=300, ready=100, waiting=100)


def input_nodes(*, missing=False, coverage_missing=False):
    nodes = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + fulfillment.load_demo_contract()
    if not missing:
        nodes += fulfillment.load_demo_fulfillment()
    if coverage_missing:
        next(node for node in nodes if node['type'] == 'FulfillmentSnapshot')['confirmations']['allocations_complete'] = False
    return nodes


def task_for(nodes=None, *, as_of=fulfillment.AS_OF):
    return fulfillment.make_fulfillment_task(input_nodes() if nodes is None else nodes,
        fulfillment.ROUND, as_of, queried_at='2026-10-27T11:10:00+09:00',
        input_revision='B-CQ02-v1', data_version=1)


class FulfillmentInputTests(unittest.TestCase):
    def test_normal_snapshot_values_sources_and_input_are_frozen(self):
        nodes = input_nodes()
        original = copy.deepcopy(nodes)
        task = task_for(nodes)
        self.assertEqual(original, nodes)
        data = task.data()
        state = data['assessment']
        self.assertEqual(EXPECTED, state['states'])
        self.assertEqual((1000, 900, 0), (state['allocated_quantity'], state['remaining_quantity'], state['unallocated_quantity']))
        self.assertEqual('promise_minus_delivered', state['remaining_quantity_meaning'])
        self.assertFalse(state['fulfillment_assessed'])
        self.assertFalse(state['future_feasibility_assessed'])
        self.assertEqual('unregistered', state['input_state'])
        self.assertEqual(fulfillment.AS_OF, state['as_of'])
        self.assertEqual(2, len(data['input_sources']))
        self.assertEqual(25, len(data['input_locators']))
        self.assertTrue(all(source['origin'] == 'synthetic' for source in data['input_sources']))
        self.assertEqual((), task.context.evidence_ids)
        self.assertLess(len(fulfillment.canonical(data).encode()), 100000)
        next(node for node in nodes if node['type'] == 'DeliveryRound')['quantity'] = 1
        data['assessment']['states']['delivered'] = 1
        self.assertEqual(EXPECTED, task.data()['assessment']['states'])

    def test_missing_snapshot_different_observation_and_coverage_keep_null(self):
        cases = [(input_nodes(missing=True), fulfillment.AS_OF),
                 (input_nodes(), '2026-10-27T11:00:00+09:00'),
                 (input_nodes(coverage_missing=True), fulfillment.AS_OF)]
        for nodes, at in cases:
            with self.subTest(at=at, nodes=len(nodes)):
                state = task_for(nodes, as_of=at).data()['assessment']
                self.assertEqual('needs_confirmation', state['status'])
                self.assertTrue(state['unknowns'])
                for key in ('states', 'allocated_quantity', 'remaining_quantity', 'unallocated_quantity'):
                    self.assertIsNone(state[key])

    def test_confirmed_empty_allocations_are_zero_and_distinct_from_unknown(self):
        nodes = [node for node in input_nodes() if node['type'] not in {'Allocation', 'Shipment'}]
        state = task_for(nodes).data()['assessment']
        self.assertEqual('complete', state['status'])
        self.assertEqual(dict.fromkeys(EXPECTED, 0), state['states'])
        self.assertEqual((0, 1000, 1000), (state['allocated_quantity'], state['remaining_quantity'], state['unallocated_quantity']))

    def test_elapsed_arrival_warning_is_not_actual_arrival(self):
        nodes = input_nodes()
        next(node for node in nodes if node['id'] == 'shipment:demo:CQ02:T')['expected_arrival_at'] = '2026-10-27T09:50:00+09:00'
        state = task_for(nodes).data()['assessment']
        self.assertEqual(EXPECTED, state['states'])
        self.assertEqual('EXPECTED_ARRIVAL_ELAPSED', state['warnings'][0]['code'])

    def test_partial_production_reaches_task_and_mock_answer_without_becoming_warehouse(self):
        nodes = input_nodes()
        next(node for node in nodes if node['id'] == 'work:demo:CQ02:R')['completed_quantity'] = 100
        task = task_for(nodes)
        state = task.data()['assessment']
        self.assertEqual('complete', state['status'])
        self.assertEqual(dict(EXPECTED, in_progress=200, production_completed=100), state['states'])
        self.assertEqual(900, state['remaining_quantity'])
        provider = fulfillment.ScriptedFulfillmentProvider(task)
        text = provider(model='unused', messages=[{'role': 'tool', 'content': json.dumps({'evidence': []})}])['text']
        answer = json.loads(text)['answer_ko']
        self.assertIn('생산 완료·미출고 100', answer)
        self.assertEqual(fulfillment.AS_OF, state['as_of'])
        self.assertEqual('2026-10-27T11:10:00+09:00', state['queried_at'])

    def test_bad_input_scope_and_implicit_database_are_rejected(self):
        with self.assertRaises(ValueError):
            fulfillment.run_scripted_example(None)
        task = task_for()
        for key, value in (('input_sha256', '0' * 64), ('input_revision', 'other')):
            data = task.data()
            data[key] = value
            with self.assertRaises(ValueError):
                fulfillment.FulfillmentAgent().messages(AgentTask(data, task.context))
        for revision, at in (('', fulfillment.AS_OF), ('v1', '2026-10-27')):
            with self.assertRaises(ValueError):
                fulfillment.make_fulfillment_task(input_nodes(), fulfillment.ROUND, at,
                    queried_at='2026-10-27T11:00:00+09:00', input_revision=revision, data_version=1)
        for target in ('', [], {}, None):
            with self.assertRaises(ValueError):
                fulfillment.make_fulfillment_task(input_nodes(), target, fulfillment.AS_OF,
                    queried_at='2026-10-27T11:00:00+09:00', input_revision='v1', data_version=1)


class FulfillmentCommonHostTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.audit = Service(self.database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        self.addCleanup(self.audit.close)
        seeded = self.audit.bootstrap('seed', json.loads((ROOT / 'data/catalog.json').read_text())['nodes'],
                                      request_key='catalog-only')
        self.assertTrue(seeded['ok'], seeded)
        self.task = task_for()
        identities = DevelopmentIdentityProvider([
            UserIdentity('reader', frozenset({'read'})), UserIdentity('denied', frozenset())], enabled=True)
        self.host = CommonHost(identities, self.database,
            agent_registrations=(AgentRegistration.from_spec(fulfillment.SPEC),))

    def run_provider(self, provider):
        with self.host.request(DevelopmentContext('reader')) as request:
            return request.run_task(fulfillment.SPEC.name, fulfillment.FulfillmentAgent(), self.task,
                provider, config=fulfillment.CONFIG, capture_tool_trace=True)

    def test_example_uses_real_common_reads_and_audit_without_b_storage(self):
        result = fulfillment.run_scripted_example(self.database)
        self.assertTrue(result['runtime']['ok'], result['runtime'])
        self.assertIsNone(result['error'])
        self.assertEqual(EXPECTED, result['payload']['assessment']['states'])
        self.assertIn('잔여 약속은 추가 생산 필요량이 아닙니다.', result['payload']['answer_ko'])
        self.assertEqual(2, len(result['evidence_refs']))
        self.assertTrue(all(ref['origin'] == 'real' for ref in result['evidence_refs']))
        self.assertEqual(1, self.audit._version())
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertFalse(any(node['type'] in {'Contract', 'FulfillmentSnapshot', 'Allocation'} for node in self.audit._nodes()))
        row = self.audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                                   (result['runtime']['run_id'],)).fetchone()
        self.assertEqual('user:dev-b-fulfillment-reader', row['actor'])
        config = json.loads(row['config'])
        self.assertEqual(['lookup'], config['allowed_tools'])
        self.assertEqual('b_fulfillment', config['agent_contract']['agent_name'])
        self.assertTrue(row['output_hash'])
        self.assertEqual({'model': 2, 'tool': 4, 'repair': 0}, result['request_execution_budget']['attempted_calls'])

    def test_incomplete_cases_run_successfully_without_inventing_zero(self):
        for nodes, at in ((input_nodes(missing=True), fulfillment.AS_OF),
                          (input_nodes(), '2026-10-27T11:00:00+09:00'),
                          (input_nodes(coverage_missing=True), fulfillment.AS_OF)):
            with self.subTest(at=at, nodes=len(nodes)):
                self.task = task_for(nodes, as_of=at)
                result = self.run_provider(fulfillment.ScriptedFulfillmentProvider(self.task))
                self.assertTrue(result['runtime']['ok'], result['runtime'])
                self.assertIsNone(result['payload']['assessment']['states'])
                self.assertIn('미확인', result['payload']['answer_ko'])

    def mutated_result(self, mutate, *, business_echo=False):
        scripted = fulfillment.ScriptedFulfillmentProvider(self.task)
        calls = []
        def provider(**kwargs):
            calls.append(1)
            response = scripted(**kwargs)
            if response['text']:
                output = json.loads(response['text'])
                if business_echo:
                    output['assessment'] = copy.deepcopy(self.task.data()['assessment'])
                mutate(output)
                response['text'] = json.dumps(output, ensure_ascii=False)
            return response
        result = self.run_provider(provider)
        self.assertEqual(2, len(calls))
        return result

    def assert_rejected(self, mutate, code='AGENT_RESULT_REJECTED'):
        result = self.mutated_result(mutate)
        self.assertEqual(code, result['error']['code'])
        self.assertIsNone(result['payload'])
        self.assertEqual([], result['evidence_refs'])

    def assert_business_fields_preserved(self, mutate):
        result = self.mutated_result(mutate, business_echo=True)
        self.assertTrue(result['runtime']['ok'], result['error'])
        self.assertEqual(self.task.data()['assessment'], result['payload']['assessment'])
        changed = copy.deepcopy(result['payload'])
        mutate(changed)
        self.assertFalse(fulfillment.FulfillmentAgent().validate_result(
            changed, self.task, result['runtime']['tool_trace']))

    def test_model_quantities_scope_and_time_cannot_replace_b_result(self):
        for key, value in (('remaining_quantity', 0), ('delivery_round_id', 'other'),
                           ('as_of', '2026-10-27T11:00:00+09:00'), ('queried_at', '2027-01-01T00:00:00+09:00'),
                           ('status', 'needs_confirmation'), ('input_state', 'confirmed'),
                           ('fulfillment_assessed', True), ('future_feasibility_assessed', True),
                           ('remaining_quantity_meaning', 'production_shortfall')):
            with self.subTest(field=key):
                self.assert_business_fields_preserved(lambda output: output['assessment'].update({key: value}))
        self.assert_business_fields_preserved(lambda output: output['assessment']['states'].update(delivered=1000))
        self.assert_rejected(lambda output: output.update(evidence_ids=['evidence:demo:CQ02:stock-W']),
                             'AGENT_EVIDENCE_MISMATCH')

    def test_unknown_cannot_be_changed_to_zero(self):
        self.task = task_for(input_nodes(coverage_missing=True))
        self.assert_business_fields_preserved(lambda output: output['assessment'].update(states=dict.fromkeys(EXPECTED, 0),
            allocated_quantity=0, remaining_quantity=1000, unallocated_quantity=1000, unknowns=[]))

    def test_no_lookup_and_wrong_catalog_are_rejected(self):
        data = self.task.data()
        value = {key: data[key] for key in ('assessment', 'input_revision', 'input_sha256', 'input_sources')}
        value.update(data_version=1, answer_ko='미등록 입력 현황입니다.', evidence_ids=[])
        result = self.run_provider(lambda **kw: {'text': json.dumps(value), 'tool_calls': []})
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])
        nodes = input_nodes()
        next(node for node in nodes if node['id'] == 'config:esp32-poe:L:WROOM')['name'] = 'different configuration input'
        self.task = task_for(nodes)
        result = self.run_provider(fulfillment.ScriptedFulfillmentProvider(self.task))
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_three_sequential_lookups_fit_four_requests(self):
        scripted = fulfillment.ScriptedFulfillmentProvider(self.task)
        nodes = self.task.data()['catalog_nodes']
        calls = []
        def provider(**kwargs):
            calls.append(1)
            count = sum(message['role'] == 'tool' for message in kwargs['messages'])
            if count < 3:
                return {'text': '', 'tool_calls': [dict(name='lookup', arguments=dict(node_id=nodes[count]['id'], version=1))]}
            return scripted(**kwargs)
        result = self.run_provider(provider)
        self.assertTrue(result['runtime']['ok'], result['runtime'])
        self.assertEqual(4, len(calls))
        self.assertEqual(4, result['request_execution_budget']['attempted_calls']['model'])

    def test_permission_and_forbidden_tool_stop_dispatch(self):
        calls = []
        with self.host.request(DevelopmentContext('denied')) as request:
            with self.assertRaises(DomainError) as denied:
                request.run_task(fulfillment.SPEC.name, fulfillment.FulfillmentAgent(), self.task,
                    lambda **kw: calls.append(1), config=fulfillment.CONFIG)
        self.assertEqual('FORBIDDEN', denied.exception.code)
        self.assertEqual([], calls)
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
