"""CQ03 conditional production with actual common reads; no paid transport."""
import copy
import json
import unittest

from examples.order_fulfillment import b_production_task as production
from ontologics.contracts import AgentTask
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.service import DomainError, Service
from tests.db import database_for_test


def input_nodes(case='normal'):
    nodes = (json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
             + production.load_demo_contract() + production.load_demo_production())
    index = {node['id']: node for node in nodes}
    previous = index['observation:demo:CQ03:09']
    latest = index['observation:demo:CQ03:10']
    work = index[production.WORK]
    if case == 'missing_previous':
        nodes.remove(previous)
    elif case == 'missing_latest':
        nodes.remove(latest)
    elif case == 'coverage':
        index[work['snapshot_id']]['confirmations']['allocations_complete'] = False
    elif case == 'zero_rate':
        previous.update({key: latest[key] for key in ('produced_quantity', 'good_quantity', 'defective_quantity')})
    elif case == 'target_observed':
        nodes.remove(previous)
        latest.update(produced_quantity=1050, good_quantity=1000, defective_quantity=50)
        work['completed_quantity'] = 1000
    elif case != 'normal':
        raise ValueError('Unknown test input case')
    return nodes


def task_for(nodes=None, *, as_of=production.AS_OF, queried_at='2026-10-27T11:10:00+09:00'):
    return production.make_production_task(input_nodes() if nodes is None else nodes,
        production.WORK, production.ROUND, as_of, queried_at=queried_at,
        input_revision='B-CQ03-v1', data_version=1)


class ProductionInputTests(unittest.TestCase):
    def test_golden_calculation_provenance_and_input_remain_frozen(self):
        nodes = input_nodes()
        original = copy.deepcopy(nodes)
        task = task_for(nodes)
        self.assertEqual(original, nodes)
        data = task.data()
        state = data['assessment']
        golden = json.loads((ROOT / 'data/order_fulfillment/b_production_expected.json').read_text())
        for key in ('production_work_id', 'delivery_round_id', 'snapshot_id', 'as_of', 'status',
                    'configuration_id', 'unit', 'target_quantity', 'latest_good_quantity',
                    'selected_observation_ids', 'good_rate_per_hour', 'produced_rate_per_hour',
                    'recent_defect_ratio', 'remaining_quantity', 'remaining_seconds', 'estimated_completion_at'):
            self.assertEqual(golden[key], state[key], key)
        self.assertEqual('unregistered', state['input_state'])
        self.assertFalse(state['fulfillment_assessed'])
        self.assertIsNone(state['actual_completion_at'])
        self.assertEqual({'RECENT_TREND_CONTINUES', 'GOOD_UNITS_USABLE'}, {a['code'] for a in state['assumptions']})
        self.assertTrue(all(a['kind'] == 'calculation_condition' for a in state['assumptions']))
        self.assertEqual(2, len(data['input_sources']))
        self.assertTrue(all(source['origin'] == 'synthetic' for source in data['input_sources']))
        self.assertTrue(set(golden['assumptions_evidence']) <= {loc['evidence_id'] for loc in data['input_locators']})
        self.assertEqual((), task.context.evidence_ids)
        self.assertLess(len(production.canonical(data).encode()), 100000)
        next(node for node in nodes if node['id'] == production.WORK)['completed_quantity'] = 1
        data['assessment']['latest_good_quantity'] = 1
        self.assertEqual(600, task.data()['assessment']['latest_good_quantity'])

    def test_missing_observations_coverage_and_other_instant_preserve_unknown(self):
        cases = [('missing_previous', production.AS_OF, 600, 'PREVIOUS_OBSERVATION_REQUIRED'),
                 ('missing_latest', production.AS_OF, None, 'LATEST_OBSERVATION_REQUIRED'),
                 ('coverage', production.AS_OF, None, 'COVERAGE_NOT_CONFIRMED'),
                 ('normal', '2026-10-27T11:00:00+09:00', None, 'SNAPSHOT_NOT_CONFIRMED')]
        for case, at, good, code in cases:
            with self.subTest(case=case, at=at):
                state = task_for(input_nodes(case), as_of=at).data()['assessment']
                self.assertEqual('needs_confirmation', state['status'])
                self.assertIn(code, [u['code'] for u in state['unknowns']])
                self.assertEqual(good, state['latest_good_quantity'])
                self.assertIsNone(state['good_rate_per_hour'])
                self.assertIsNone(state['estimated_completion_at'])

    def test_zero_rate_and_target_observed_do_not_invent_actual_completion(self):
        zero = task_for(input_nodes('zero_rate')).data()['assessment']
        self.assertEqual(('needs_confirmation', 0, 400),
                         (zero['status'], zero['good_rate_per_hour'], zero['remaining_quantity']))
        self.assertIsNone(zero['recent_defect_ratio'])
        self.assertIsNone(zero['estimated_completion_at'])
        target = task_for(input_nodes('target_observed')).data()['assessment']
        self.assertEqual(('target_observed', 0, 0),
                         (target['status'], target['remaining_quantity'], target['remaining_seconds']))
        self.assertEqual(production.AS_OF, target['target_observed_at'])
        self.assertIsNone(target['actual_completion_at'])
        self.assertIsNone(target['estimated_completion_at'])

    def test_query_clock_and_utc_offset_do_not_move_observation_or_forecast(self):
        original = task_for().data()['assessment']
        other = task_for(as_of='2026-10-27T01:00:00Z', queried_at='2026-10-28T12:00:00+09:00').data()['assessment']
        for key in ('as_of', 'observation_as_of', 'selected_observation_ids', 'latest_good_quantity', 'estimated_completion_at'):
            self.assertEqual(original[key], other[key], key)
        self.assertNotEqual(original['queried_at'], other['queried_at'])

    def test_terminal_mixed_task_pins_requested_work_and_observation_clock(self):
        from examples.order_fulfillment import b_terminal_input as terminal
        source = terminal.load_input(ROOT / 'data/order_fulfillment/examples/terminal_800.md')
        role, task = terminal.case_task('cq03_normal', source)
        state = task.data()['assessment']
        self.assertIs(production, role[0])
        self.assertEqual('forecast', state['status'])
        self.assertEqual('requested_production_work', state['assessment_scope'])
        self.assertEqual((400, 100, 300, 100.0, '2026-10-30T13:00:00+09:00'), tuple(
            state[key] for key in ('target_quantity', 'latest_good_quantity', 'remaining_quantity',
                                   'good_rate_per_hour', 'estimated_completion_at')))
        self.assertEqual(('2026-10-30T10:00:00+09:00', '2026-10-30T10:05:00+09:00'),
                         (state['as_of'], state['queried_at']))
        self.assertFalse(state['fulfillment_assessed'])
        self.assertIsNone(state['actual_completion_at'])

    def test_terminal_basic_work_forecast_remains_fourteen(self):
        from examples.order_fulfillment import b_terminal_input as terminal
        source = terminal.load_input(ROOT / 'data/order_fulfillment/examples/basic_800_1000.md')
        _, task = terminal.case_task('cq03_normal', source)
        state = task.data()['assessment']
        self.assertEqual(('forecast', 800, 400, 400, '2026-10-30T14:00:00+09:00'), tuple(
            state[key] for key in ('status', 'target_quantity', 'latest_good_quantity',
                                   'remaining_quantity', 'estimated_completion_at')))

    def test_bad_input_pinned_payload_and_implicit_database_are_rejected(self):
        with self.assertRaises(ValueError):
            production.run_scripted_example(None)
        task = task_for()
        for key, value in (('input_sha256', '0' * 64), ('input_revision', 'other')):
            data = task.data()
            data[key] = value
            with self.assertRaises(ValueError):
                production.ProductionAgent().messages(AgentTask(data, task.context))
        for bad in ('', [], {}, None):
            for field in ('production_work_id', 'delivery_round_id', 'input_revision'):
                kwargs = dict(nodes=input_nodes(), production_work_id=production.WORK,
                    delivery_round_id=production.ROUND, as_of=production.AS_OF,
                    queried_at='2026-10-27T11:00:00+09:00', input_revision='v1', data_version=1)
                kwargs[field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    production.make_production_task(**kwargs)
        nodes = input_nodes()
        next(node for node in nodes if node['id'] == 'observation:demo:CQ03:10')['good_quantity'] = 601
        with self.assertRaises(ValueError):
            task_for(nodes)
        with self.assertRaises(ValueError):
            task_for(as_of='2026-10-27')


class ProductionCommonHostTests(unittest.TestCase):
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
            agent_registrations=(AgentRegistration.from_spec(production.SPEC),))

    def run_provider(self, provider):
        with self.host.request(DevelopmentContext('reader')) as request:
            return request.run_task(production.SPEC.name, production.ProductionAgent(), self.task,
                provider, config=production.CONFIG, capture_tool_trace=True)

    def test_example_runs_common_lookup_and_audit_without_b_storage(self):
        result = production.run_scripted_example(self.database)
        self.assertTrue(result['runtime']['ok'], result['runtime'])
        self.assertIsNone(result['error'])
        self.assertEqual('2026-10-27T14:26:40+09:00', result['payload']['assessment']['estimated_completion_at'])
        self.assertIn('조건부 예상', result['payload']['answer_ko'])
        self.assertEqual(2, len(result['evidence_refs']))
        self.assertTrue(all(ref['origin'] == 'real' for ref in result['evidence_refs']))
        self.assertEqual(1, self.audit._version())
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertFalse(any(node['type'] in {'Contract', 'ProductionWork', 'ProductionObservation'} for node in self.audit._nodes()))
        row = self.audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                                   (result['runtime']['run_id'],)).fetchone()
        self.assertEqual('user:dev-b-production-reader', row['actor'])
        config = json.loads(row['config'])
        self.assertEqual(['lookup'], config['allowed_tools'])
        self.assertEqual('b_production', config['agent_contract']['agent_name'])
        self.assertTrue(row['output_hash'])
        self.assertEqual({'model': 2, 'tool': 4, 'repair': 0}, result['request_execution_budget']['attempted_calls'])

    def test_incomplete_zero_and_target_cases_retain_their_meaning(self):
        for case in ('missing_previous', 'missing_latest', 'coverage', 'zero_rate', 'target_observed'):
            with self.subTest(case=case):
                self.task = task_for(input_nodes(case))
                result = self.run_provider(production.ScriptedProductionProvider(self.task))
                self.assertTrue(result['runtime']['ok'], result['runtime'])
                state = result['payload']['assessment']
                self.assertIsNone(state['actual_completion_at'])
                self.assertIsNone(state['estimated_completion_at'])
                if case == 'target_observed':
                    self.assertIn('최초 실제 완료 시각은 미확인', result['payload']['answer_ko'])
                else:
                    self.assertEqual('needs_confirmation', state['status'])
                    self.assertIn('미확인', result['payload']['answer_ko'])

    def mutated_result(self, mutate, *, business_echo=False):
        scripted = production.ScriptedProductionProvider(self.task)
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
        self.assertFalse(production.ProductionAgent().validate_result(
            changed, self.task, result['runtime']['tool_trace']))

    def test_model_calculation_scope_time_and_assumptions_cannot_replace_b_result(self):
        for key, value in (('latest_good_quantity', 1000), ('remaining_quantity', 0), ('good_rate_per_hour', 100),
                           ('recent_defect_ratio', 0), ('estimated_completion_at', production.AS_OF),
                           ('production_work_id', 'other'), ('delivery_round_id', 'other'),
                           ('as_of', '2026-10-27T11:00:00+09:00'), ('queried_at', production.AS_OF),
                           ('selected_observation_ids', []), ('status', 'target_observed'),
                           ('assumptions', []), ('warnings', []), ('actual_completion_at', production.AS_OF),
                           ('input_state', 'confirmed'), ('fulfillment_assessed', True)):
            with self.subTest(field=key):
                self.assert_business_fields_preserved(lambda output: output['assessment'].update({key: value}))
        self.assert_rejected(lambda output: output.update(evidence_ids=['evidence:demo:CQ03:assumptions']),
                             'AGENT_EVIDENCE_MISMATCH')

    def test_missing_data_cannot_become_zero_or_a_forecast(self):
        self.task = task_for(input_nodes('missing_latest'))
        self.assert_business_fields_preserved(lambda output: output['assessment'].update(status='forecast',
            latest_good_quantity=0, remaining_quantity=1000, good_rate_per_hour=90, unknowns=[],
            estimated_completion_at='2026-10-27T14:26:40+09:00'))

    def test_missing_reads_and_mismatched_catalog_are_rejected(self):
        data = self.task.data()
        value = {key: data[key] for key in ('assessment', 'input_revision', 'input_sha256', 'input_sources')}
        value.update(data_version=1, answer_ko='미등록 입력의 조건부 예상입니다.', evidence_ids=[])
        result = self.run_provider(lambda **kw: {'text': json.dumps(value), 'tool_calls': []})
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])
        nodes = input_nodes()
        next(node for node in nodes if node['id'] == 'config:esp32-poe:L:WROOM')['name'] = 'different input configuration'
        self.task = task_for(nodes)
        result = self.run_provider(production.ScriptedProductionProvider(self.task))
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_three_sequential_lookups_fit_four_requests(self):
        scripted = production.ScriptedProductionProvider(self.task)
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
                request.run_task(production.SPEC.name, production.ProductionAgent(), self.task,
                    lambda **kw: calls.append(1), config=production.CONFIG)
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
