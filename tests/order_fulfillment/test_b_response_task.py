"""CQ05 common host and deterministic draft boundaries; free providers only."""
import copy
import json
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_response_task as response
from ontologics.contracts import AgentTask
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.service import DomainError, Service
from tests.db import database_for_test
from tests.order_fulfillment.test_b_delivery_task import input_nodes


def nodes_for(*, mixed=False, case='normal'):
    if case == 'historical_coverage':
        nodes = input_nodes(mixed=True, case='historical')
        next(node for node in nodes if node['type'] == 'FulfillmentSnapshot')['confirmations']['allocations_complete'] = False
    elif case == 'due16':
        nodes = input_nodes(mixed=mixed)
        next(node for node in nodes if node['id'] == response.ROUND)['due_at'] = '2026-10-27T16:00:00+09:00'
    elif case == 'planned_departure':
        nodes = input_nodes(mixed=True)
        shipment = next(node for node in nodes if node['type'] == 'Shipment' and 'departed_at' in node and 'arrived_at' not in node)
        shipment['planned_departure_at'] = shipment.pop('departed_at')
        shipment['expected_arrival_at'] = '2026-10-27T16:00:00+09:00'
    else:
        nodes = input_nodes(mixed=mixed, case=case)
    return nodes


def task_for(nodes=None, *, as_of=response.AS_OF, queried_at='2026-10-27T10:10:00+09:00'):
    return response.make_response_task(nodes_for() if nodes is None else nodes,
        response.ROUND, as_of, queried_at=queried_at, input_revision='B-CQ05-test-v1', data_version=1)


class ResponseInputTests(unittest.TestCase):
    def test_draft_author_has_recipient_logistics_and_separate_quantity_scopes(self):
        for mixed, quantities in ((False, (0, 0, 600, 400, 1000, 960, 40)),
                                  (True, (100, 150, 450, 300, 750, 750, 0))):
            with self.subTest(mixed=mixed):
                data = task_for(nodes_for(mixed=mixed)).data()
                request = data['review_material']['dispatch_request']
                keys = ('delivered_quantity', 'in_transit_quantity', 'currently_available_quantity',
                        'future_quantity', 'new_dispatch_quantity',
                        'on_time_new_dispatch_quantity', 'later_new_dispatch_quantity')
                self.assertEqual(quantities, tuple(request[key] for key in keys))
                self.assertEqual('가상고객사 A', request['customer_name'])
                self.assertEqual('가상고객사 A 수령창고', request['destination'])
                self.assertTrue(all(request[key] is None for key in response.LOGISTICS))
                self.assertEqual({'data_version', 'answer_ko', 'evidence_ids'},
                                 {field.name for field in response.MODEL_OUTPUT.fields})
        self.assertIn('출고 요청 초안', response.SPEC.instructions)
        self.assertIn('고객 회신 초안', response.SPEC.instructions)
        self.assertNotIn('answer_ko는 별도 요약 설명', response.SPEC.instructions)

    def test_normal_and_mixed_match_independent_golden_and_keep_original_drafts_separate(self):
        golden = json.loads((ROOT / 'data/order_fulfillment/b_response_expected.json').read_text())
        for mixed, case in ((False, 'normal'), (True, 'mixed15')):
            with self.subTest(case=case):
                nodes = nodes_for(mixed=mixed)
                before = copy.deepcopy(nodes)
                with patch.object(response, 'response_plan', wraps=response.response_plan) as planner:
                    task = task_for(nodes)
                    planner.assert_called_once_with(nodes, response.ROUND, response.AS_OF,
                        queried_at='2026-10-27T10:10:00+09:00')
                self.assertEqual(before, nodes)
                data = task.data()
                state = data['assessment']
                expected = golden['cases'][case]
                self.assertEqual(expected['response_state'], state['response_state'])
                for section in ('forecast', 'dispatch_request'):
                    for key, value in expected[section].items():
                        self.assertEqual(value, state[section][key], (section, key))
                for key in ('draft_only', 'review_required', 'synthetic'):
                    self.assertIs(True, state[key])
                self.assertIs(False, state['execution_performed'])
                self.assertEqual('unregistered', state['input_state'])
                self.assertTrue(all(state['dispatch_request'][key] is None for key in response.LOGISTICS))
                original = response.response_plan(nodes, response.ROUND, response.AS_OF,
                    queried_at='2026-10-27T10:10:00+09:00')
                material = data['review_material']
                for key in ('dispatch_request', 'customer_reply', 'actions', 'confirmations', 'evidence'):
                    self.assertEqual(original[key], material[key], key)
                self.assertIn('담당자 검토용·미발송·미등록 초안', material['customer_reply'])
                self.assertEqual(golden['selected_evidence']['mixed' if mixed else 'normal'], material['evidence'])
                self.assertEqual(response.json_sha256(material), state['review_material_sha256'])
                self.assertEqual([entry['code'] for entry in material['actions']], state['action_codes'])
                self.assertEqual([entry['code'] for entry in material['confirmations']], state['confirmation_codes'])
                self.assertEqual(2, len(data['input_sources']))
                self.assertTrue(all(source['origin'] == 'synthetic' for source in data['input_sources']))
                self.assertTrue(set(material['evidence']) <= {loc['evidence_id'] for loc in data['input_locators']})
                self.assertLess(len(response.canonical(data).encode()), 100000)
                self.assertEqual((), task.context.evidence_ids)
                data['review_material']['customer_reply'] = '발송 완료'
                with self.assertRaises(ValueError):
                    response.ResponseAgent().messages(AgentTask(data, task.context))
                next(node for node in nodes if node['type'] == 'DeliveryCondition')['transport_seconds'] = 1
                self.assertEqual(3600, task.data()['assessment']['forecast']['condition']['transport_seconds'])

    def test_deadline_and_dispatch_variants_keep_total_and_new_dispatch_counts_distinct(self):
        golden = json.loads((ROOT / 'data/order_fulfillment/b_response_expected.json').read_text())['cases']
        for mixed, case, key in ((True, 'due14', 'mixed14'), (False, 'due16', 'due16'),
                                 (False, 'transport2h', 'transport2h'), (False, 'all_at_once', 'all_at_once15')):
            with self.subTest(case=case):
                state = task_for(nodes_for(mixed=mixed, case=case)).data()['assessment']
                for section in ('forecast', 'dispatch_request'):
                    for field, value in golden[key][section].items():
                        self.assertEqual(value, state[section][field], (section, field))
                self.assertTrue(state['review_required'])
                self.assertFalse(state['execution_performed'])

    def test_unknown_dispatch_totals_remain_null_and_confirmed_empty_remains_zero(self):
        for mixed, case in ((False, 'missing_condition'), (False, 'missing_latest'),
                            (False, 'zero_rate'), (False, 'coverage'), (True, 'elapsed_eta')):
            with self.subTest(case=case):
                data = task_for(nodes_for(mixed=mixed, case=case)).data()
                state = data['assessment']
                self.assertEqual('needs_confirmation', state['response_state'])
                self.assertEqual('not_assessed', state['dispatch_request']['planning_status'])
                self.assertTrue(all(state['dispatch_request'][key] is None for key in response.QUANTITIES))
                self.assertEqual([], data['review_material']['dispatch_request']['items'])
                self.assertTrue(state['forecast']['unknowns'])
                self.assertEqual(['CONFIRM_INPUTS'], state['action_codes'])
        empty = task_for(nodes_for(case='no_allocations')).data()['assessment']
        self.assertEqual((0, 1000), (empty['dispatch_request']['new_dispatch_quantity'],
                                   empty['dispatch_request']['unallocated_quantity']))
        self.assertIn('CONFIRM_SUPPLY', empty['action_codes'])

    def test_historical_review_precedes_missing_coverage_without_creating_reship_quantities(self):
        for case in ('historical', 'historical_coverage'):
            with self.subTest(case=case):
                data = task_for(nodes_for(mixed=True, case=case)).data()
                state = data['assessment']
                self.assertEqual('historical_review', state['response_state'])
                self.assertEqual('historical_only', state['dispatch_request']['planning_status'])
                self.assertTrue(all(state['dispatch_request'][key] is None for key in response.QUANTITIES))
                self.assertEqual([], data['review_material']['dispatch_request']['items'])
                self.assertEqual(['REVIEW_HISTORY'], state['action_codes'])
                if case == 'historical':
                    self.assertTrue(state['forecast']['actual_arrival_assessed'])
                    self.assertEqual((100, 900), (state['forecast']['on_time_quantity'], state['forecast']['shortfall_quantity']))
                else:
                    self.assertTrue(state['forecast']['unknowns'])
                    self.assertIsNone(state['forecast']['shortfall_quantity'])

    def test_planned_departure_does_not_count_as_actual_in_transit(self):
        state = task_for(nodes_for(case='planned_departure')).data()['assessment']
        self.assertEqual((100, 0, 900), tuple(state['dispatch_request'][key] for key in (
            'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity')))

    def test_explicit_database_target_revision_hash_and_times_are_required(self):
        with self.assertRaises(ValueError):
            response.run_scripted_example(None)
        for field in ('input_revision', 'delivery_round_id'):
            for bad in ('', ' ', None, [], {}):
                kwargs = dict(nodes=nodes_for(), delivery_round_id=response.ROUND, as_of=response.AS_OF,
                    queried_at='2026-10-27T10:10:00+09:00', input_revision='v1', data_version=1)
                kwargs[field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    response.make_response_task(**kwargs)
        for field in ('as_of', 'queried_at'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                task_for(**{field: '2026-10-27T10:00:00'})
        original = task_for()
        data = original.data()
        data['input_revision'] = 'other'
        with self.assertRaises(ValueError):
            response.ResponseAgent().messages(AgentTask(data, original.context))
        with self.assertRaises(KeyError):
            response.make_response_task(nodes_for(), 'missing', response.AS_OF,
                queried_at=response.AS_OF, input_revision='v1', data_version=1)


class ResponseCommonHostTests(unittest.TestCase):
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
            agent_registrations=(AgentRegistration.from_spec(response.SPEC),))

    def run_provider(self, provider):
        with self.host.request(DevelopmentContext('reader')) as request:
            return request.run_task(response.SPEC.name, response.ResponseAgent(), self.task,
                provider, config=response.CONFIG, capture_tool_trace=True)

    def test_normal_and_mixed_use_common_reads_without_b_storage_or_approval(self):
        for mixed, quantities in ((False, (1000, 600, 400)), (True, (750, 450, 300))):
            with self.subTest(mixed=mixed):
                result = response.run_scripted_example(self.database, mixed=mixed)
                self.assertTrue(result['runtime']['ok'], result['runtime'])
                self.assertIsNone(result['error'])
                state = result['payload']['assessment']
                self.assertEqual(quantities, tuple(state['dispatch_request'][key] for key in (
                    'new_dispatch_quantity', 'currently_available_quantity', 'future_quantity')))
                self.assertIn('미발송', result['payload']['answer_ko'])
                self.assertNotIn('review_material', result['payload'])
                self.assertEqual(2, len(result['evidence_refs']))
                self.assertTrue(all(ref['origin'] == 'real' for ref in result['evidence_refs']))
                self.assertEqual({'model': 2, 'tool': 4, 'repair': 0}, result['request_execution_budget']['attempted_calls'])
                row = self.audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                                           (result['runtime']['run_id'],)).fetchone()
                self.assertEqual('user:dev-b-response-reader', row['actor'])
                config = json.loads(row['config'])
                self.assertEqual(['lookup'], config['allowed_tools'])
                self.assertEqual('b_response', config['agent_contract']['agent_name'])
                self.assertTrue(row['output_hash'])
        self.assertEqual(1, self.audit._version())
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n'])
        self.assertFalse(any(node['type'] in {'Contract', 'DeliveryCondition', 'ProductionWork', 'Shipment'} for node in self.audit._nodes()))

    def test_unknown_historical_and_all_at_once_are_explained_as_review_states(self):
        for mixed, case, label in ((False, 'missing_condition', '미확인'),
            (True, 'historical', '과거'), (True, 'historical_coverage', '과거'),
            (False, 'all_at_once', '일괄'), (False, 'no_allocations', '검토 0')):
            with self.subTest(case=case):
                self.task = task_for(nodes_for(mixed=mixed, case=case))
                result = self.run_provider(response.ScriptedResponseProvider(self.task))
                self.assertTrue(result['runtime']['ok'], result['runtime'])
                self.assertIn(label, result['payload']['answer_ko'])
                self.assertTrue(result['payload']['assessment']['draft_only'])
                self.assertFalse(result['payload']['assessment']['execution_performed'])

    def mutated_result(self, mutate, *, business_echo=False):
        scripted = response.ScriptedResponseProvider(self.task)
        calls = []
        def provider(**kwargs):
            calls.append(1)
            value = scripted(**kwargs)
            if value['text']:
                output = json.loads(value['text'])
                if business_echo:
                    output['assessment'] = copy.deepcopy(self.task.data()['assessment'])
                mutate(output)
                value['text'] = json.dumps(output, ensure_ascii=False)
            return value
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
        self.assertFalse(response.ResponseAgent().validate_result(
            changed, self.task, result['runtime']['tool_trace']))

    def test_counts_flags_conditions_logistics_and_sources_cannot_change(self):
        for key, value in (('draft_only', False), ('review_required', False), ('execution_performed', True),
            ('synthetic', False), ('response_state', 'met'), ('input_state', 'confirmed'),
            ('action_codes', []), ('confirmation_codes', [])):
            with self.subTest(field=key):
                self.assert_business_fields_preserved(lambda output: output['assessment'].update({key: value}))
        for key, value in (('new_dispatch_quantity', 600), ('currently_available_quantity', 1000),
            ('future_quantity', 0), ('on_time_new_dispatch_quantity', 1000), ('later_new_dispatch_quantity', 0),
            ('planning_status', 'sent'), ('carrier', 'invented'), ('request_number', 'invented')):
            with self.subTest(field=key):
                self.assert_business_fields_preserved(lambda output: output['assessment']['dispatch_request'].update({key: value}))
        for key, value in (('on_time_quantity', 1000), ('due_at', response.AS_OF),
            ('as_of', '2026-10-27T11:00:00+09:00'), ('queried_at', response.AS_OF), ('assumption_codes', [])):
            with self.subTest(field=key):
                self.assert_business_fields_preserved(lambda output: output['assessment']['forecast'].update({key: value}))
        self.assert_business_fields_preserved(lambda output: output['assessment']['forecast']['condition'].update(dispatch_mode='all_at_once'))
        self.assert_rejected(lambda output: output.update(evidence_ids=['evidence:demo:CQ04:base:assumptions']),
                             'AGENT_EVIDENCE_MISMATCH')

    def test_free_text_is_left_for_human_review_and_cannot_grant_execution(self):
        scripted = response.ScriptedResponseProvider(self.task)
        for prose in ('미등록 검토 자료입니다.',
                      '승인 완료라는 문구는 사용하지 않고 조건을 다시 검토해야 합니다.',
                      '업무 요약: 조건부 납기 검토입니다.\n'
                      '출고 요청 초안: 물류 담당자님, 확보된 600개와 생산 예정 400개를 구분해 분할 출고를 검토해 주세요.\n'
                      '고객 회신 초안: 고객님, 기한 내 960개 도착과 40개 부족이 예상되어 후속 일정을 협의하고자 합니다.\n'
                      '담당자 확인: 운송사와 연락처는 미확인입니다. 두 문안은 미발송 검토 초안입니다.'):
            with self.subTest(prose=prose):
                def provider(**kwargs):
                    value = scripted(**kwargs)
                    if value['text']:
                        output = json.loads(value['text'])
                        output['answer_ko'] = prose
                        value['text'] = json.dumps(output, ensure_ascii=False)
                    return value
                result = self.run_provider(provider)
                self.assertTrue(result['runtime']['ok'], result['error'])
                self.assertEqual(prose, result['payload']['answer_ko'])
                self.assertTrue(result['payload']['assessment']['review_required'])
                self.assertFalse(result['payload']['assessment']['execution_performed'])
                self.assertEqual(1, self.audit._version())
                self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])

    def test_unknown_and_historical_cannot_be_promoted_into_numeric_new_dispatch(self):
        for case in ('missing_condition', 'historical', 'historical_coverage'):
            with self.subTest(case=case):
                self.task = task_for(nodes_for(mixed=True, case=case))
                self.assert_business_fields_preserved(lambda output: output['assessment']['dispatch_request'].update(new_dispatch_quantity=0))
        self.assert_business_fields_preserved(lambda output: output['assessment'].update(response_state='shortfall'))

    def test_missing_or_mismatched_catalog_reads_withhold_result(self):
        data = self.task.data()
        value = {key: data[key] for key in ('assessment', 'input_revision', 'input_sha256', 'input_sources')}
        value.update(data_version=1, answer_ko='가상 미등록 미발송 검토 초안입니다.', evidence_ids=[])
        result = self.run_provider(lambda **kw: {'text': json.dumps(value), 'tool_calls': []})
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])
        nodes = nodes_for()
        next(node for node in nodes if node['id'] == 'config:esp32-poe:L:WROOM')['name'] = 'different input'
        self.task = task_for(nodes)
        result = self.run_provider(response.ScriptedResponseProvider(self.task))
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_sequential_reads_fit_four_requests(self):
        scripted = response.ScriptedResponseProvider(self.task)
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
                request.run_task(response.SPEC.name, response.ResponseAgent(), self.task,
                    lambda **kw: calls.append(1), config=response.CONFIG)
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
