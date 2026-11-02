"""CQ04 normal/mixed common-host contracts; providers are free test doubles."""
import copy
import json
import unittest

from examples.order_fulfillment import b_delivery_task as delivery
from ontologics.contracts import AgentTask
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.service import DomainError, Service
from tests.db import database_for_test


def input_nodes(*, mixed=False, case='normal'):
    nodes = (json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
             + delivery.load_demo_contract() + delivery.load_demo_delivery(mixed=mixed))
    condition = next(node for node in nodes if node['type'] == 'DeliveryCondition')
    observations = sorted((node for node in nodes if node['type'] == 'ProductionObservation'),
                          key=lambda node: node['observed_at'])
    if case == 'due14':
        next(node for node in nodes if node['id'] == delivery.ROUND)['due_at'] = '2026-10-27T14:00:00+09:00'
    elif case == 'historical':
        next(node for node in nodes if node['id'] == delivery.ROUND)['due_at'] = '2026-10-27T09:30:00+09:00'
    elif case == 'transport2h':
        condition['transport_seconds'] = 7200
    elif case == 'all_at_once':
        condition['dispatch_mode'] = 'all_at_once'
    elif case == 'missing_condition':
        nodes.remove(condition)
    elif case == 'missing_latest':
        nodes.remove(observations[-1])
    elif case == 'zero_rate':
        observations[0].update({key: observations[-1][key] for key in (
            'produced_quantity', 'good_quantity', 'defective_quantity')})
    elif case == 'coverage':
        next(node for node in nodes if node['type'] == 'FulfillmentSnapshot')['confirmations']['allocations_complete'] = False
    elif case == 'elapsed_eta':
        next(node for node in nodes if node['type'] == 'Shipment' and 'departed_at' in node
             and 'arrived_at' not in node)['expected_arrival_at'] = '2026-10-27T09:50:00+09:00'
    elif case == 'no_allocations':
        nodes = [node for node in nodes if node['type'] not in {'Allocation', 'Shipment'}]
    elif case != 'normal':
        raise ValueError('Unknown test input case')
    return nodes


def task_for(nodes=None, *, as_of=delivery.AS_OF, queried_at='2026-10-27T10:10:00+09:00'):
    return delivery.make_delivery_task(input_nodes() if nodes is None else nodes,
        delivery.ROUND, as_of, queried_at=queried_at, input_revision='B-CQ04-test-v1', data_version=1)


class DeliveryInputTests(unittest.TestCase):
    def test_mixed_model_input_distinguishes_total_arrival_from_new_shippable_stock(self):
        task = task_for(input_nodes(mixed=True))
        messages = delivery.DeliveryAgent().messages(task)
        data = json.loads(next(message['content'] for message in messages if message['role'] == 'user'))
        rows = {row['source_state']: row for row in data['calculation_details']['breakdown']}
        self.assertEqual(1000, data['assessment']['on_time_quantity'])
        self.assertEqual((100, 150), (rows['delivered']['quantity'], rows['in_transit']['quantity']))
        current = rows['warehouse']['known_available_quantity'] + rows['in_progress']['known_available_quantity']
        future = rows['in_progress']['quantity'] - rows['in_progress']['known_available_quantity']
        self.assertEqual((450, 300), (current, future))
        self.assertEqual(1000, rows['delivered']['quantity'] + rows['in_transit']['quantity'] + current + future)
        instructions = AgentRegistration.from_spec(delivery.SPEC).instructions
        self.assertIn('on_time_quantity', instructions)
        self.assertIn('known_available_quantity', instructions)
        self.assertIn('현재 출고 가능', instructions)
        self.assertIn('미래 생산', instructions)
        self.assertNotIn('1000', instructions)
        self.assertNotIn('450', instructions)

    def test_natural_paraphrases_need_no_titles_and_do_not_replace_bound_business_values(self):
        task = task_for(input_nodes(mixed=True))
        node = task.data()['catalog_nodes'][0]
        reads = [dict(name='lookup', result=dict(ok=True, data_version=1, data=[node]))]
        prose = (
            '가상 입력의 조건부 예상은 기한 내 1,000개입니다. 현재 출고 검토 가능한 450개와 미래 생산 300개를 구분합니다.',
            '이미 납품한 100개와 운송 중인 150개는 새 출고에서 제외합니다. 남은 750개 중 현재 확보분은 450개입니다.',
        )
        for answer in prose:
            with self.subTest(answer=answer):
                raw = json.dumps(dict(data_version=1, answer_ko=answer, evidence_ids=node['evidence']), ensure_ascii=False)
                decoded = delivery.DeliveryAgent().decode_result(raw, task, reads)
                self.assertTrue(delivery.DeliveryAgent().validate_result(decoded, task, reads))
                self.assertEqual(answer, decoded['answer_ko'])
                self.assertEqual(task.data()['assessment'], decoded['assessment'])

    def test_normal_and_mixed_match_golden_with_frozen_details_and_sources(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                nodes = input_nodes(mixed=mixed)
                original = copy.deepcopy(nodes)
                task = task_for(nodes)
                self.assertEqual(original, nodes)
                data = task.data()
                state = data['assessment']
                filename = 'b_delivery_mixed_expected.json' if mixed else 'b_delivery_expected.json'
                golden = json.loads((ROOT / 'data/order_fulfillment' / filename).read_text())
                for key in ('delivery_round_id', 'snapshot_id', 'as_of', 'deadline_status', 'on_time_quantity',
                            'shortfall_quantity', 'estimated_full_arrival_at', 'configuration_id', 'unit', 'forecast_rule_version'):
                    self.assertEqual(golden[key], state[key], key)
                self.assertEqual('unregistered', state['input_state'])
                self.assertFalse(state['actual_arrival_assessed'])
                self.assertEqual(dict(preparation_seconds=0, transport_seconds=3600, dispatch_mode='split_available'), state['condition'])
                self.assertEqual(2, len(data['input_sources']))
                self.assertTrue(all(source['origin'] == 'synthetic' for source in data['input_sources']))
                self.assertTrue(set(golden['assumptions_evidence']) <= {loc['evidence_id'] for loc in data['input_locators']})
                details = data['calculation_details']
                self.assertEqual(4 if mixed else 1, len(details['breakdown']))
                self.assertEqual([a['code'] for a in details['assumptions']], state['assumption_codes'])
                self.assertEqual([w['code'] for w in details['warnings']], state['warning_codes'])
                self.assertEqual((), task.context.evidence_ids)
                self.assertLess(len(delivery.canonical(data).encode()), 100000)
                data['calculation_details']['breakdown'][0]['on_time_quantity'] = 1
                with self.assertRaises(ValueError):
                    delivery.DeliveryAgent().messages(AgentTask(data, task.context))
                next(node for node in nodes if node['type'] == 'DeliveryCondition')['transport_seconds'] = 1
                self.assertEqual(3600, task.data()['assessment']['condition']['transport_seconds'])

    def test_dispatch_transport_and_deadline_variants_preserve_existing_arithmetic(self):
        for mixed, case, on_time, shortfall, full in (
            (False, 'all_at_once', 0, 1000, '2026-10-27T15:26:40+09:00'),
            (False, 'transport2h', 870, 130, '2026-10-27T16:26:40+09:00'),
            (True, 'due14', 970, 30, '2026-10-27T14:20:00+09:00')):
            with self.subTest(mixed=mixed, case=case):
                state = task_for(input_nodes(mixed=mixed, case=case)).data()['assessment']
                self.assertEqual((on_time, shortfall, full),
                    (state['on_time_quantity'], state['shortfall_quantity'], state['estimated_full_arrival_at']))

    def test_unknown_totals_do_not_become_known_partial_sums_or_zero(self):
        for mixed, case in ((False, 'missing_condition'), (False, 'missing_latest'),
                            (False, 'zero_rate'), (False, 'coverage'),
                            (True, 'elapsed_eta'), (True, 'all_at_once')):
            with self.subTest(mixed=mixed, case=case):
                data = task_for(input_nodes(mixed=mixed, case=case)).data()
                state = data['assessment']
                self.assertEqual('needs_confirmation', state['deadline_status'])
                self.assertTrue(state['unknowns'])
                for key in ('on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at'):
                    self.assertIsNone(state[key], key)
        empty = task_for(input_nodes(case='no_allocations')).data()['assessment']
        self.assertEqual((0, 1000), (empty['on_time_quantity'], empty['shortfall_quantity']))
        self.assertEqual([], empty['unknowns'])

    def test_historical_deadline_uses_events_without_backdating_current_production(self):
        nodes = input_nodes(mixed=True, case='historical')
        before = copy.deepcopy(nodes)
        data = task_for(nodes).data()
        state = data['assessment']
        self.assertTrue(state['actual_arrival_assessed'])
        self.assertEqual((100, 900), (state['on_time_quantity'], state['shortfall_quantity']))
        # The existing calculator keeps this scope label; the flag distinguishes
        # a past event assessment without rewriting the calculation contract.
        self.assertEqual('conditional_arrival_forecast', state['assessment_scope'])
        self.assertIsNone(state['estimated_full_arrival_at'])
        work = next(row for row in data['calculation_details']['breakdown'] if row['source_id'].startswith('work:'))
        self.assertEqual(('not_delivered_by_deadline', 0), (work['source_state'], work['on_time_quantity']))
        self.assertEqual(before, nodes)

    def test_explicit_scope_input_hash_time_and_database_are_required(self):
        with self.assertRaises(ValueError):
            delivery.run_scripted_example(None)
        for bad in ('', [], {}, None):
            for field in ('delivery_round_id', 'input_revision'):
                kwargs = dict(nodes=input_nodes(), delivery_round_id=delivery.ROUND, as_of=delivery.AS_OF,
                    queried_at='2026-10-27T10:10:00+09:00', input_revision='v1', data_version=1)
                kwargs[field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    delivery.make_delivery_task(**kwargs)
        original = task_for()
        for field in ('input_sha256', 'input_revision'):
            data = original.data()
            data[field] = 'other'
            with self.assertRaises(ValueError):
                delivery.DeliveryAgent().messages(AgentTask(data, original.context))
        same = task_for(as_of='2026-10-27T01:00:00Z', queried_at='2026-10-28T10:00:00+09:00').data()['assessment']
        self.assertEqual(original.data()['assessment']['estimated_full_arrival_at'], same['estimated_full_arrival_at'])
        self.assertEqual(delivery.AS_OF, same['as_of'])
        other = task_for(as_of='2026-10-27T11:00:00+09:00').data()['assessment']
        self.assertEqual('needs_confirmation', other['deadline_status'])
        with self.assertRaises(ValueError):
            task_for(as_of='2026-10-27')


class DeliveryCommonHostTests(unittest.TestCase):
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
            agent_registrations=(AgentRegistration.from_spec(delivery.SPEC),))

    def run_provider(self, provider):
        with self.host.request(DevelopmentContext('reader')) as request:
            return request.run_task(delivery.SPEC.name, delivery.DeliveryAgent(), self.task,
                provider, config=delivery.CONFIG, capture_tool_trace=True)

    def test_normal_and_mixed_examples_use_common_reads_without_b_storage(self):
        for mixed, counts in ((False, (960, 40)), (True, (1000, 0))):
            with self.subTest(mixed=mixed):
                result = delivery.run_scripted_example(self.database, mixed=mixed)
                self.assertTrue(result['runtime']['ok'], result['runtime'])
                self.assertIsNone(result['error'])
                state = result['payload']['assessment']
                self.assertEqual(counts, (state['on_time_quantity'], state['shortfall_quantity']))
                self.assertIn('조건부 계산', result['payload']['answer_ko'])
                self.assertNotIn('calculation_details', result['payload'])
                self.assertEqual(2, len(result['evidence_refs']))
                self.assertTrue(all(ref['origin'] == 'real' for ref in result['evidence_refs']))
                self.assertEqual({'model': 2, 'tool': 4, 'repair': 0}, result['request_execution_budget']['attempted_calls'])
                row = self.audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                                           (result['runtime']['run_id'],)).fetchone()
                self.assertEqual('user:dev-b-delivery-reader', row['actor'])
                config = json.loads(row['config'])
                self.assertEqual(['lookup'], config['allowed_tools'])
                self.assertEqual('b_delivery', config['agent_contract']['agent_name'])
                self.assertTrue(row['output_hash'])
        self.assertEqual(1, self.audit._version())
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertFalse(any(node['type'] in {'Contract', 'DeliveryCondition', 'ProductionWork', 'Shipment'} for node in self.audit._nodes()))

    def test_unknown_and_historical_results_keep_their_distinct_meanings(self):
        for mixed, case in ((False, 'missing_condition'), (False, 'missing_latest'),
                            (False, 'coverage'), (False, 'zero_rate'), (True, 'elapsed_eta'), (True, 'historical')):
            with self.subTest(mixed=mixed, case=case):
                self.task = task_for(input_nodes(mixed=mixed, case=case))
                result = self.run_provider(delivery.ScriptedDeliveryProvider(self.task))
                self.assertTrue(result['runtime']['ok'], result['runtime'])
                state = result['payload']['assessment']
                if case == 'historical':
                    self.assertTrue(state['actual_arrival_assessed'])
                    self.assertEqual(100, state['on_time_quantity'])
                    self.assertIn('과거 기한', result['payload']['answer_ko'])
                else:
                    self.assertIsNone(state['on_time_quantity'])
                    self.assertIn('미확인', result['payload']['answer_ko'])

    def test_common_host_delivers_quantity_guidance_and_preserves_natural_answer(self):
        self.task = task_for(input_nodes(mixed=True))
        scripted = delivery.ScriptedDeliveryProvider(self.task)
        seen = []
        answer = ('이 회차의 조건부 예상은 기한 내 1,000개이며 '
                  '현재 새 출고 검토 물량은 확보분 450개와 미래 생산 300개입니다. '
                  '이미 납품한 100개와 운송 중 150개는 새 출고에서 제외합니다.')
        def provider(**kwargs):
            seen.extend(kwargs['messages'])
            response = scripted(**kwargs)
            if response['text']:
                value = json.loads(response['text'])
                value['answer_ko'] = answer
                response['text'] = json.dumps(value, ensure_ascii=False)
            return response
        result = self.run_provider(provider)
        self.assertTrue(result['runtime']['ok'], result['error'])
        self.assertTrue(any(message['role'] == 'system' and delivery.SPEC.instructions in message['content']
                            for message in seen))
        self.assertEqual(answer, result['payload']['answer_ko'])
        self.assertEqual(self.task.data()['assessment'], result['payload']['assessment'])

    def mutated_result(self, mutate, *, business_echo=False):
        scripted = delivery.ScriptedDeliveryProvider(self.task)
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
        self.assertFalse(delivery.DeliveryAgent().validate_result(
            changed, self.task, result['runtime']['tool_trace']))

    def test_model_counts_deadline_and_conditions_cannot_replace_b_result(self):
        for key, value in (('on_time_quantity', 1000), ('shortfall_quantity', 0),
                           ('estimated_full_arrival_at', delivery.AS_OF), ('due_at', delivery.AS_OF),
                           ('as_of', '2026-10-27T11:00:00+09:00'), ('queried_at', delivery.AS_OF),
                           ('delivery_round_id', 'other'), ('deadline_status', 'met'),
                           ('actual_arrival_assessed', True), ('input_state', 'confirmed'),
                           ('assumption_codes', []), ('warning_codes', [])):
            with self.subTest(field=key):
                self.assert_business_fields_preserved(lambda output: output['assessment'].update({key: value}))
        self.assert_business_fields_preserved(lambda output: output['assessment']['condition'].update(transport_seconds=0))
        self.assert_rejected(lambda output: output.update(evidence_ids=['evidence:demo:CQ04:base:assumptions']),
                             'AGENT_EVIDENCE_MISMATCH')

    def test_unknown_totals_and_historical_flags_cannot_be_rewritten(self):
        self.task = task_for(input_nodes(mixed=True, case='elapsed_eta'))
        self.assert_business_fields_preserved(lambda output: output['assessment'].update(deadline_status='met',
            on_time_quantity=1000, shortfall_quantity=0, unknowns=[]))
        self.task = task_for(input_nodes(mixed=True, case='historical'))
        self.assert_business_fields_preserved(lambda output: output['assessment'].update(actual_arrival_assessed=False))

    def test_missing_reads_and_mismatched_catalog_are_rejected(self):
        data = self.task.data()
        value = {key: data[key] for key in ('assessment', 'input_revision', 'input_sha256', 'input_sources')}
        value.update(data_version=1, answer_ko='미등록 입력의 납기 영향입니다.', evidence_ids=[])
        result = self.run_provider(lambda **kw: {'text': json.dumps(value), 'tool_calls': []})
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])
        nodes = input_nodes()
        next(node for node in nodes if node['id'] == 'config:esp32-poe:L:WROOM')['name'] = 'different input configuration'
        self.task = task_for(nodes)
        result = self.run_provider(delivery.ScriptedDeliveryProvider(self.task))
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_sequential_reads_fit_four_requests(self):
        scripted = delivery.ScriptedDeliveryProvider(self.task)
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

    def test_permission_and_forbidden_write_stop_dispatch(self):
        calls = []
        with self.host.request(DevelopmentContext('denied')) as request:
            with self.assertRaises(DomainError) as denied:
                request.run_task(delivery.SPEC.name, delivery.DeliveryAgent(), self.task,
                    lambda **kw: calls.append(1), config=delivery.CONFIG)
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
