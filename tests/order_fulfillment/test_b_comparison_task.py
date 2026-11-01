"""CQ06 input comparison contracts; no paid provider or B fact storage."""
import copy
import json
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_comparison_task as comparison
from ontologics.contracts import AgentTask
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.service import DomainError, Service
from tests.db import database_for_test


def task_for(before=None, after=None, **changes):
    # Fresh fixture inputs; never feed previously calculated output back as facts.
    before = (json.loads((comparison.ROOT / 'data/catalog.json').read_text())['nodes']
              + comparison.load_demo_contract() + comparison.load_demo_delivery()) if before is None else before
    after = comparison.load_demo_recalculation_input('defects11') if after is None else after
    args = dict(delivery_round_id=comparison.ROUND, before_as_of=comparison.AS_OF,
        after_as_of=comparison.AFTER_AS_OF['defects11'], before_revision='z-input',
        after_revision='a-input', queried_at='2026-10-27T12:00:00+09:00', data_version=1)
    args.update(changes)
    return comparison.make_comparison_task(before, after, **args)


class ComparisonInputTests(unittest.TestCase):
    def catalog_reads(self, task):
        return [dict(name='lookup', arguments=dict(node_id=node['id'], version=1),
                     result=dict(ok=True, data_version=1, data=[node], evidence=node['evidence']))
                for node in task.data()['catalog_nodes']]

    def test_model_narrative_is_preserved_and_python_fields_remain_authoritative(self):
        task = comparison.make_demo_task()
        data = task.data()
        answer = ('변경 요약: 불량률 10%에서 20%, 최근 양품 속도 시간당 90개에서 80개로 변경됐습니다.\n'
                  '기한 내 예상은 960개에서 920개, 부족은 40개에서 80개입니다.\n'
                  '전량 도착 예상은 10월 27일 15:26:40에서 16:00(UTC+09:00)으로 33분 20초 늦어집니다.\n'
                  '운송 1시간 등 가정이 유지되는 조건부 검토 결과이며 이번 Agent는 출고나 승인을 수행하지 않았습니다.')
        agent = comparison.ComparisonAgent()
        decoded = agent.decode_result(json.dumps(dict(data_version=1, answer_ko=answer,
            evidence_ids=data['catalog_nodes'][0]['evidence'],
            assessment={'model_copy': 'ignored'}, input_sources_by_side={})),
            task, self.catalog_reads(task))
        self.assertEqual(answer, decoded['answer_ko'])
        self.assertNotEqual(comparison._answer(data), decoded['answer_ko'])
        self.assertEqual(data['assessment'], decoded['assessment'])
        self.assertEqual(data['input_sources_by_side'], decoded['input_sources_by_side'])
        self.assertTrue(agent.validate_result(decoded, task, self.catalog_reads(task)))

    def test_missing_invalid_or_blank_model_narrative_has_no_python_fallback(self):
        task = comparison.make_demo_task()
        agent = comparison.ComparisonAgent()
        for answer in (None, [], 1):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                agent.decode_result(json.dumps(dict(data_version=1, answer_ko=answer,
                    evidence_ids=[])), task, self.catalog_reads(task))
        with self.assertRaises(ValueError):
            agent.decode_result(json.dumps(dict(data_version=1, evidence_ids=[])),
                                task, self.catalog_reads(task))
        for answer in ('', ' \n '):
            with self.subTest(answer=answer):
                decoded = agent.decode_result(json.dumps(dict(data_version=1, answer_ko=answer,
                    evidence_ids=task.data()['catalog_nodes'][0]['evidence'])), task, self.catalog_reads(task))
                self.assertEqual(answer, decoded['answer_ko'])
                self.assertFalse(agent.validate_result(decoded, task, self.catalog_reads(task)))

    def test_five_independent_goldens_full_material_and_python_text_are_preserved(self):
        golden = json.loads((comparison.ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())
        for case in comparison.CASES:
            with self.subTest(case=case):
                task = comparison.make_demo_task(case=case)
                data = task.data()
                state = data['assessment']
                full = data['review_material']['comparison']
                self.assertEqual(golden['cases'][case]['quantity_deltas'], state['quantity_deltas'])
                self.assertEqual(golden['cases'][case]['time_deltas'], state['time_deltas'])
                for side, expected in (('before', golden['before']), ('after', golden['cases'][case])):
                    for section in ('forecast', 'production', 'dispatch_request'):
                        for key, value in expected[section].items():
                            self.assertEqual(value, state[side][section][key], (side, section, key))
                    self.assertEqual(full[side]['input_sha256'], state[side]['input_sha256'])
                    self.assertEqual('unregistered', state[side]['input_state'])
                    self.assertNotIn('data_version', state[side])
                self.assertEqual(comparison.build_explanation(full), data['review_material']['python_explanation'])
                self.assertEqual(comparison.json_sha256(data['review_material']), state['review_material_sha256'])
                self.assertEqual('unregistered_delivery_input_comparison', state['assessment_scope'])
                self.assertLess(len(comparison.canonical(data).encode()), 100000)
                self.assertEqual((), task.context.evidence_ids)
                self.assertTrue(all(state[key] for key in ('synthetic', 'draft_only', 'review_required')))
                self.assertFalse(state['execution_performed'])

    def test_reuses_pure_function_without_mutating_or_stacking_inputs(self):
        after = comparison.load_demo_recalculation_input('defects11')
        saved = copy.deepcopy(after)
        with patch.object(comparison, 'compare_input_response_plan', wraps=comparison.compare_input_response_plan) as calc:
            task = task_for(after=after)
            self.assertEqual(1, calc.call_count)
            self.assertEqual('z-input', calc.call_args.kwargs['before_revision'])
        self.assertEqual(saved, after)
        next(node for node in after if node['type'] == 'Contract')['customer_name'] = 'mutated'
        self.assertEqual(920, task.data()['assessment']['after']['forecast']['on_time_quantity'])
        independent = comparison.make_demo_task(case='transport2h').data()['assessment']
        self.assertEqual((90, 600, 870), (independent['after']['production']['good_rate_per_hour'],
            independent['after']['production']['latest_good_quantity'], independent['after']['forecast']['on_time_quantity']))

    def test_duration_and_absolute_time_changes_are_distinct(self):
        state = comparison.make_demo_task(case='defects11').data()['assessment']
        self.assertEqual(-1600, state['time_deltas']['remaining_seconds'])
        self.assertEqual(2000, state['time_deltas']['estimated_completion_seconds'])
        self.assertEqual((90, 80), tuple(state[side]['production']['good_rate_per_hour'] for side in ('before', 'after')))
        self.assertEqual((600, 680), tuple(state[side]['production']['latest_good_quantity'] for side in ('before', 'after')))
        unchanged = comparison.make_demo_task(case='continued11').data()['assessment']
        self.assertEqual((-3600, 0), tuple(unchanged['time_deltas'][key] for key in (
            'remaining_seconds', 'estimated_completion_seconds')))

    def test_zero_rate_and_missing_condition_stay_unknown(self):
        state = comparison.make_demo_task(case='zero_rate11').data()['assessment']
        self.assertEqual('partially_unknown', state['comparison_status'])
        self.assertTrue(all(value is None for value in state['quantity_deltas'].values()))
        self.assertTrue(all(value is None for value in state['time_deltas'].values()))
        self.assertIsNone(state['after']['production']['estimated_completion_at'])
        after = [node for node in comparison.load_demo_recalculation_input('defects11') if node['type'] != 'DeliveryCondition']
        data = task_for(after=after).data()
        self.assertIsNone(data['assessment']['after']['forecast']['on_time_quantity'])
        self.assertIn('미확인', comparison._answer(data))

    def test_revision_binding_time_order_and_changed_scope_fail_before_host(self):
        for field in ('before_revision', 'after_revision'):
            for value in ('', ' ', None, [], 'v' * 201):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    task_for(**{field: value})
        for changes in (dict(before_revision='same', after_revision='same'),
                        dict(after_as_of='2026-10-27T09:00:00+09:00'),
                        dict(queried_at='2026-10-27T12:00:00')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                task_for(**changes)
        after = comparison.load_demo_recalculation_input('defects11')
        next(node for node in after if node['type'] == 'Contract')['customer_name'] = 'different'
        with self.assertRaises(ValueError):
            task_for(after=after)
        self.assertEqual('compared', task_for().data()['assessment']['comparison_status'])
        with self.assertRaises(ValueError):
            comparison.run_scripted_example(None)

    def test_same_revision_cannot_bind_changed_graph_at_same_instant(self):
        after = comparison.load_demo_recalculation_input('transport2h')
        with self.assertRaises(ValueError):
            task_for(after=after, before_revision='same', after_revision='same', after_as_of=comparison.AS_OF)

    def test_sources_and_same_id_locators_are_kept_per_side(self):
        after = comparison.load_demo_recalculation_input('defects11')
        evidence = next(node for node in after if node['id'] == 'evidence:demo:CQ04:base:condition')
        evidence['locator'] = 'section E; Markdown lines 45-52'
        data = task_for(after=after).data()
        catalog_ids = {eid for node in data['catalog_nodes'] for eid in node['evidence']}
        for side, count in (('before', 2), ('after', 3)):
            self.assertEqual(count, len(data['input_sources_by_side'][side]))
            self.assertTrue(all(entry['origin'] == 'synthetic' for entry in data['input_sources_by_side'][side]))
            ids = {loc['evidence_id'] for loc in data['input_locators_by_side'][side]}
            self.assertTrue(set(data['review_material']['comparison']['integrity_evidence_by_side'][side]) - catalog_ids <= ids)
        locators = [next(loc['locator'] for loc in data['input_locators_by_side'][side]
                        if loc['evidence_id'] == evidence['id']) for side in ('before', 'after')]
        self.assertNotEqual(*locators)

    def test_catalog_evidence_difference_is_rejected_even_with_identical_business_fields(self):
        after = comparison.load_demo_recalculation_input('defects11')
        config = next(node for node in after if node['id'] == 'config:esp32-poe:L:WROOM')
        config['evidence'] = list(reversed(config['evidence']))
        if len(config['evidence']) == 1:
            other = next(node['id'] for node in after if node['type'] == 'Evidence'
                         and node['origin'] == 'real' and node['id'] not in config['evidence'])
            config['evidence'].append(other)
        with self.assertRaisesRegex(ValueError, 'same complete common catalog'):
            task_for(after=after)

    def test_pin_covers_material_metadata_and_both_provenance_sides(self):
        task = comparison.make_demo_task()
        for field in ('assessment', 'review_material', 'input_sources_by_side', 'input_locators_by_side', 'catalog_nodes'):
            data = task.data()
            data[field] = [] if field == 'catalog_nodes' else {}
            with self.subTest(field=field), self.assertRaises(ValueError):
                comparison.ComparisonAgent().messages(AgentTask(data, task.context))


class ComparisonCommonHostTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.audit = Service(self.database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        self.addCleanup(self.audit.close)
        seeded = self.audit.bootstrap('seed', json.loads((comparison.ROOT / 'data/catalog.json').read_text())['nodes'],
                                      request_key='catalog-only')
        self.assertTrue(seeded['ok'], seeded)
        self.task = comparison.make_demo_task()
        identities = DevelopmentIdentityProvider([
            UserIdentity('reader', frozenset({'read'})), UserIdentity('denied', frozenset())], enabled=True)
        self.host = CommonHost(identities, self.database,
            agent_registrations=(AgentRegistration.from_spec(comparison.SPEC),))

    def run_provider(self, provider):
        with self.host.request(DevelopmentContext('reader')) as request:
            return request.run_task(comparison.SPEC.name, comparison.ComparisonAgent(), self.task,
                provider, config=comparison.CONFIG, capture_tool_trace=True)

    def test_representatives_read_common_catalog_without_b_storage_or_approval(self):
        for case, quantities in (('defects11', (920, 80)), ('transport2h', (870, 130)), ('zero_rate11', (None, None))):
            with self.subTest(case=case):
                self.task = comparison.make_demo_task(case=case)
                result = comparison.run_scripted_example(self.database, case=case)
                self.assertTrue(result['runtime']['ok'], result['runtime'])
                self.assertIsNone(result['error'])
                self.assertEqual(quantities, tuple(result['payload']['assessment']['after']['forecast'][key]
                    for key in ('on_time_quantity', 'shortfall_quantity')))
                self.assertEqual(comparison._answer(self.task.data()), result['payload']['answer_ko'])
                self.assertNotIn('review_material', result['payload'])
                self.assertEqual(2, len(result['evidence_refs']))
                self.assertTrue(all(ref['origin'] == 'real' for ref in result['evidence_refs']))
                self.assertEqual({'model': 2, 'tool': 4, 'repair': 0}, result['request_execution_budget']['attempted_calls'])
                row = self.audit.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                                           (result['runtime']['run_id'],)).fetchone()
                self.assertEqual('user:dev-b-comparison-reader', row['actor'])
                self.assertEqual(['lookup'], json.loads(row['config'])['allowed_tools'])
                self.assertTrue(row['output_hash'])
        self.assertEqual(1, self.audit._version())
        for table in ('drafts', 'review_events'):
            self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM ' + table).fetchone()['n'])
        self.assertFalse(any(node['type'] in {'Contract', 'DeliveryCondition', 'ProductionWork', 'Shipment'} for node in self.audit._nodes()))

    def mutated_result(self, mutate, *, business_echo=False):
        scripted = comparison.ScriptedComparisonProvider(self.task)
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
        self.assertFalse(comparison.ComparisonAgent().validate_result(
            changed, self.task, result['runtime']['tool_trace']))

    def test_sides_deltas_and_business_flags_cannot_change(self):
        for side in ('before', 'after'):
            for key, value in (('as_of', comparison.AS_OF if side == 'after' else comparison.AFTER_AS_OF['defects11']),):
                with self.subTest(side=side, key=key):
                    self.assert_business_fields_preserved(lambda output: output['assessment'][side].update({key: value}))
        for key, value in (('draft_only', False), ('review_required', False), ('execution_performed', True),
            ('synthetic', False), ('input_state', 'approved')):
            with self.subTest(key=key):
                self.assert_business_fields_preserved(lambda output: output['assessment'].update({key: value}))
        self.assert_business_fields_preserved(lambda output: output['assessment']['time_deltas'].update(estimated_completion_seconds=-1600))
        self.assert_business_fields_preserved(lambda output: output['assessment']['quantity_deltas'].update(on_time_quantity=0))
        self.assert_business_fields_preserved(lambda output: output['assessment']['production_deltas'].update(good_rate_per_hour=80))
        self.assert_rejected(lambda output: output.update(evidence_ids=['evidence:demo:CQ04:base:condition']),
                             'AGENT_EVIDENCE_MISMATCH')

    def test_unknown_values_cannot_be_promoted_to_zero_or_certain(self):
        self.task = comparison.make_demo_task(case='zero_rate11')
        self.assert_business_fields_preserved(lambda output: output['assessment']['quantity_deltas'].update(on_time_quantity=0))
        self.assert_business_fields_preserved(lambda output: output['assessment'].update(comparison_status='compared'))

    def test_catalog_mismatch_and_missing_reads_withhold_result(self):
        data = self.task.data()
        value = {key: data[key] for key in ('assessment', 'input_sources_by_side')}
        value.update(data_version=1, answer_ko=comparison._answer(data), evidence_ids=[])
        result = self.run_provider(lambda **kw: {'text': comparison.canonical(value), 'tool_calls': []})
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])
        before = (json.loads((comparison.ROOT / 'data/catalog.json').read_text())['nodes']
                  + comparison.load_demo_contract() + comparison.load_demo_delivery())
        after = comparison.load_demo_recalculation_input('defects11')
        for nodes in (before, after):
            next(node for node in nodes if node['id'] == 'config:esp32-poe:L:WROOM')['name'] = 'different'
        self.task = task_for(before=before, after=after)
        result = self.run_provider(comparison.ScriptedComparisonProvider(self.task))
        self.assertEqual('AGENT_RESULT_REJECTED', result['error']['code'])

    def test_sequential_reads_fit_four_requests(self):
        scripted = comparison.ScriptedComparisonProvider(self.task)
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
                request.run_task(comparison.SPEC.name, comparison.ComparisonAgent(), self.task,
                    lambda **kw: calls.append(1), config=comparison.CONFIG)
        self.assertEqual('FORBIDDEN', denied.exception.code)
        self.assertEqual([], calls)
        def provider(**kwargs):
            calls.append(1)
            return {'text': '', 'tool_calls': [dict(name='prepare_draft', arguments={})]}
        result = self.run_provider(provider)
        self.assertFalse(result['runtime']['ok'])
        self.assertIsNone(result['payload'])
        self.assertEqual(1, len(calls))
        self.assertEqual(0, self.audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])


if __name__ == '__main__':
    unittest.main()
