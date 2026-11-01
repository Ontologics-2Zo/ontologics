"""Common OpenAI adapter exercised only against local HTTP response doubles."""
import copy
import json
import unittest
from urllib.error import HTTPError

from examples.order_fulfillment import b_model_connection as connection
from examples.order_fulfillment.b_input_task import catalog_evidence
from examples.order_fulfillment.b_task_workflow import CASES, ROLES, case_task
from ontologics.database import DatabaseSettings
from ontologics.openai_provider import ENDPOINT, MODEL, OpenAISettings
from ontologics.paid_budget import CostPolicy, ModelPrice
from ontologics.service import DomainError, Service
from tests.db import database_for_test
from tests.test_openai_provider import Reply, SECRET, response, text, tool


def policy(cap='10'):
    # Deliberately synthetic prices; no credential or network use.
    return CostPolicy(cap, (ModelPrice(MODEL, '1', '2', 400000),))


class LocalModel:
    """Serialize real common tool outputs into the existing free role oracle.

    One function call per Responses response mirrors parallel_tool_calls=false.
    This is transport/contract validation, never evidence of LLM comprehension.
    """
    def __init__(self, role, task, *, failures=(), final=None, final_transform=None):
        self.double = role[2](task)
        self.calls = self.double(model=MODEL, messages=[])['tool_calls']
        self.failures = list(failures)
        self.final = final
        self.final_transform = final_transform
        self.final_text = None
        self.requests = []

    def __call__(self, request, timeout):
        payload = json.loads(request.data)
        self.requests.append((payload, timeout))
        if self.failures:
            raise self.failures.pop(0)
        results = [item for item in payload['input'] if item.get('type') == 'function_call_output']
        if self.final is not None:
            self.final_text = self.final
            output = [text(self.final)]
        elif len(results) < len(self.calls):
            call = self.calls[len(results)]
            output = [tool(call['name'], call['arguments'], cid='b_call_' + str(len(results)))]
        else:
            final = self.double(model=MODEL, messages=[
                {'role': 'tool', 'content': item['output']} for item in results])
            self.final_text = final['text']
            if self.final_transform is not None:
                value = json.loads(self.final_text)
                self.final_transform(value)
                self.final_text = json.dumps(value, ensure_ascii=False)
            output = [text(self.final_text)]
        return Reply(json.dumps(response(output)).encode())


class ComparisonFormatTests(unittest.TestCase):
    def setUp(self):
        self.role, self.task = case_task('cq06_transport2h', version=7)
        self.nodes = self.task.data()['catalog_nodes']

    def message(self, nodes, **changes):
        result = dict(ok=True, data_version=7, data=copy.deepcopy(nodes))
        result.update(changes)
        return dict(role='tool', name='lookup', content=json.dumps(result))

    def test_only_observed_catalog_ids_are_final_output_choices(self):
        messages = [self.message(self.nodes[:1])]
        value = connection._comparison_text_format(self.task, messages)
        self.assertEqual('json_schema', value['type'])
        self.assertTrue(value['strict'])
        schema = value['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual({'data_version', 'answer_ko', 'evidence_ids'}, set(schema['required']))
        choices = schema['properties']['evidence_ids']['items']['enum']
        self.assertEqual(sorted(self.nodes[0]['evidence']), choices)
        self.assertTrue(all(not eid.startswith('evidence:terminal:') for eid in choices))
        self.assertNotIn('assessment', schema['properties'])
        self.assertNotIn('input_sources_by_side', schema['properties'])

    def test_candidates_accumulate_but_do_not_require_all_reads_or_all_citations(self):
        messages = [self.message([node]) for node in reversed(self.nodes)]
        value = connection._comparison_text_format(self.task, messages + messages[:1])
        field = value['schema']['properties']['evidence_ids']
        self.assertEqual(sorted({eid for node in self.nodes for eid in node['evidence']}),
                         field['items']['enum'])
        self.assertNotIn('const', field)
        self.assertNotIn('minItems', field)

    def test_input_hints_failed_wrong_version_and_mismatched_reads_give_no_candidates(self):
        wrong = dict(self.nodes[0], name='Different observed product')
        histories = ([], [dict(role='user', content=json.dumps(self.task.data()))],
            [self.message(self.nodes[:1], ok=False)],
            [self.message(self.nodes[:1], data_version=8)], [self.message([wrong])])
        for index, messages in enumerate(histories):
            with self.subTest(history=index):
                self.assertIsNone(connection._comparison_text_format(self.task, messages))


class CatalogReadTests(unittest.TestCase):
    def setUp(self):
        self.role, self.task = case_task('cq01_promise', version=7)
        self.nodes = self.task.data()['catalog_nodes']

    def read(self, nodes, **arguments):
        return dict(name='lookup', arguments=arguments,
            result=dict(ok=True, data_version=7, data=copy.deepcopy(nodes),
                        evidence=sorted({eid for node in nodes for eid in node['evidence']})))

    def test_one_relevant_read_is_enough_and_unread_catalog_is_not_required(self):
        node = self.nodes[0]
        read = self.read([node], kind=node['type'], node_id=node['id'], version=7)
        self.assertEqual(sorted(node['evidence']), catalog_evidence(self.nodes, 7, [read]))

    def test_broad_reads_allow_unrelated_nodes_and_read_order_does_not_matter(self):
        extra = dict(self.nodes[0], id='product:unrelated', evidence=['evidence:unrelated'])
        reads = [self.read([node], kind=node['type'], version=7) for node in self.nodes]
        expected = sorted({eid for node in self.nodes for eid in node['evidence']})
        self.assertEqual(expected, catalog_evidence(self.nodes, 7, list(reversed(reads))))
        self.assertEqual(sorted(self.nodes[0]['evidence']), catalog_evidence(self.nodes, 7,
            [self.read([self.nodes[0], extra], kind=self.nodes[0]['type'], version=7)]))

    def test_observed_mismatch_failed_wrong_version_and_unrelated_only_do_not_qualify(self):
        good = self.read([self.nodes[0]], node_id=self.nodes[0]['id'], version=7)
        wrong = copy.deepcopy(self.nodes[1])
        wrong['name'] = '관측한 카탈로그와 다른 입력'
        self.assertIsNone(catalog_evidence(self.nodes, 7,
            [good, self.read([wrong], node_id=wrong['id'], version=7)]))
        for change in ({'ok': False}, {'data_version': 8}, {'data': []}):
            with self.subTest(change=change):
                read = copy.deepcopy(good)
                read['result'].update(change)
                self.assertIsNone(catalog_evidence(self.nodes, 7, [read]))
        self.assertIsNone(catalog_evidence(self.nodes, 7, []))

    def test_remaining_agents_accept_one_read_but_keep_answer_evidence_and_bound_results(self):
        for case in CASES[1:]:
            with self.subTest(case=case):
                role, task = case_task(case, version=7)
                agent = role[1]()
                node = task.data()['catalog_nodes'][0]
                read = self.read([node], kind=node['type'], node_id=node['id'], version=7)
                final = role[2](task)(model=MODEL,
                    messages=[dict(role='tool', content=json.dumps(read['result']))])['text']
                value = agent.decode_result(final, task, [read])
                self.assertTrue(agent.validate_result(value, task, [read]))
                for change in ({'evidence_ids': []}, {'answer_ko': '  '},
                               {'promise' if case == 'cq01_promise' else 'assessment': {}}):
                    invalid = copy.deepcopy(value)
                    invalid.update(change)
                    self.assertFalse(agent.validate_result(invalid, task, [read]))
                self.assertFalse(agent.validate_result(value, task, []))


class ModelProfileTests(unittest.TestCase):
    def test_remaining_roles_do_not_require_model_provenance_transcription(self):
        for case in CASES[1:]:
            with self.subTest(case=case):
                role, task = case_task(case, version=7)
                module, agent, _ = role
                excluded = ({'input_sources_by_side', 'assessment'} if case.startswith('cq06_')
                            else {'input_revision', 'input_sha256', 'input_sources',
                                  'promise' if case == 'cq01_promise' else 'assessment'})
                self.assertTrue(excluded.isdisjoint({f.name for f in module.MODEL_OUTPUT.fields}))
                version = ('1.6.0' if case.startswith('cq06_') else
                           '1.5.0' if case.startswith(('cq04_', 'cq05_')) else '1.4.0')
                self.assertEqual(version, module.SPEC.version)
                self.assertIn('answer_ko', {f.name for f in module.MODEL_OUTPUT.fields})
                messages = agent().messages(task)
                instructions = json.loads(messages[0]['content'])
                self.assertEqual(excluded, set(instructions['host_bound_fields']))
                self.assertEqual([dict(name='lookup', arguments=dict(kind=node['type'], node_id=node['id'], version=7))
                                  for node in task.data()['catalog_nodes']], instructions['lookup_sequence'])
                self.assertEqual(task.data(), json.loads(messages[-1]['content']))
                self.assertNotIn('1024', module.SPEC.instructions)

    def test_answer_style_separates_observation_query_and_display_from_raw_facts(self):
        for module, agent, _ in ROLES:
            with self.subTest(role=module.SPEC.name):
                case = next(case for case in CASES if case_task(case)[0][0] is module)
                _, task = case_task(case)
                before = task.data()
                messages = agent().messages(task)
                style = json.loads(messages[0]['content'])['answer_style']
                self.assertEqual(('as_of', 'queried_at'),
                    (style['observation_time_field'], style['query_time_field']))
                self.assertEqual({'1800': '30분', '7200': '2시간', '10800': '3시간'},
                                 style['duration_examples'])
                self.assertEqual(before, json.loads(messages[-1]['content']))
                self.assertEqual(before, task.data())

    def test_common_profile_keeps_scripted_specs_unchanged(self):
        self.assertEqual((MODEL, 12, 2, 3, 0), (connection.CONFIG.model,
            connection.CONFIG.max_steps, connection.CONFIG.max_attempts,
            connection.CONFIG.max_tool_calls, connection.CONFIG.max_repairs))
        host = connection.make_host(DatabaseSettings('postgresql://not-used/unused', 'new_b_model_profile'), policy())
        for module, _, _ in ROLES:
            with self.subTest(role=module.SPEC.name):
                spec = connection.model_spec(module)
                self.assertEqual(module.SPEC.name, spec.name)
                self.assertEqual(module.SPEC.version, spec.version)
                self.assertEqual(module.SPEC.requested_tools, spec.requested_tools)
                self.assertEqual(module.SPEC.input_schema, spec.input_schema)
                self.assertEqual(module.SPEC.output_schema, spec.output_schema)
                self.assertEqual((24, 6, 0, 120), (spec.limits.max_model_calls,
                    spec.limits.max_tool_calls, spec.limits.max_repairs, spec.limits.deadline_seconds))
                self.assertEqual(4, module.SPEC.limits.max_model_calls)
                self.assertEqual(1, module.CONFIG.max_attempts)
                self.assertEqual(spec, host._agent_registrations[spec.name].spec)

    def test_public_summary_respects_common_root_and_reservation_caps(self):
        summary = connection.policy_summary(policy(), list(CASES))
        self.assertEqual((64, 70, 600), (summary['root_limits']['max_model_calls'],
            summary['root_limits']['max_tool_calls'], summary['root_limits']['deadline_seconds']))
        self.assertEqual(48, connection.new_budget(policy(), case_count=2).limits.max_model_calls)
        self.assertEqual(24, connection.new_budget(policy(), case_count=1).limits.max_model_calls)
        self.assertEqual(40, connection.new_budget(policy(), case_count=2, demo=True).limits.max_model_calls)
        self.assertEqual(40, connection.policy_summary(policy(), list(CASES), demo=True)
                         ['root_limits']['max_model_calls'])
        self.assertEqual((30, 4096), (summary['provider_config']['timeout_seconds'],
            summary['provider_config']['max_output_tokens']))
        self.assertEqual('0.408192', summary['first_call_reservation_usd'])
        self.assertTrue(summary['first_call_budget_sufficient'])
        self.assertIsNone(summary['reservation_error'])
        self.assertEqual(400000, summary['minimum_input_reservation_tokens'])
        self.assertEqual(0, summary['actual_api_attempts'])
        blocked = connection.policy_summary(policy('0.4'), ['cq01_intake'])
        self.assertFalse(blocked['first_call_budget_sufficient'])
        self.assertEqual('COST_BUDGET_EXCEEDED', blocked['reservation_error'])
        self.assertEqual('0.408192', blocked['first_call_reservation_usd'])
        self.assertEqual('0.4', blocked['cost_policy']['max_cost_usd'])

    def test_invalid_profile_arguments_are_rejected_without_resources(self):
        for count in (0, 11, True, '1'):
            with self.subTest(count=count), self.assertRaises(ValueError):
                connection.new_budget(policy(), case_count=count)
        for cases in ([], ['cq01_intake', 'cq01_intake'], ['cq01_promise', 'cq01_intake']):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                connection.policy_summary(policy(), cases)
        with self.assertRaises(ValueError):
            connection.make_host(None, policy())
        with self.assertRaises(ValueError):
            connection.model_spec(object())


class ModelConnectionTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        from ontologics.model import ROOT
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        seed = Service(self.database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        try:
            result = seed.bootstrap('seed', catalog, request_key='b-model-connection-test')
            self.assertTrue(result['ok'])
            self.version = result['data_version']
        finally:
            seed.close()
        self.policy = policy()
        self.host = connection.make_host(self.database, self.policy)
        self.budget = connection.new_budget(self.policy, case_count=2)
        self.settings = OpenAISettings(SECRET)

    def execute(self, case='cq01_intake', *, failures=(), final=None, final_transform=None,
                host=None, budget=None):
        role, task = case_task(case, version=self.version)
        transport = LocalModel(role, task, failures=failures, final=final, final_transform=final_transform)
        result, calls = connection.execute_case(self.host if host is None else host,
            case, task, role, settings=self.settings,
            budget=self.budget if budget is None else budget, opener=transport)
        return result, calls, transport

    def test_every_case_requires_initial_read_then_allows_one_read_and_final_answer(self):
        for case in CASES:
            with self.subTest(case=case):
                role, task = case_task(case, version=self.version)
                transport = LocalModel(role, task)
                transport.calls = transport.calls[:1]
                result, calls = connection.execute_case(self.host, case, task, role,
                    settings=self.settings, budget=connection.new_budget(self.policy), opener=transport)
                self.assertTrue(result['runtime']['ok'], result['error'])
                self.assertEqual(['required', 'auto'],
                                 [payload['tool_choice'] for payload, _ in transport.requests])
                self.assertEqual(2, len(calls))
                self.assertEqual(1, len(result['runtime']['tool_trace']))

    def test_cq06_direct_unread_answer_still_fails_common_evidence_check_without_retry(self):
        case = 'cq06_defects11'
        role, task = case_task(case, version=self.version)
        raw = json.dumps(dict(data_version=self.version, answer_ko='변경 전후 비교 초안',
                              evidence_ids=['evidence:olimex:product-family']), ensure_ascii=False)
        transport = LocalModel(role, task, final=raw)
        result, calls = connection.execute_case(self.host, case, task, role,
            settings=self.settings, budget=connection.new_budget(self.policy), opener=transport)
        self.assertFalse(result['runtime']['ok'])
        self.assertEqual('AGENT_EVIDENCE_MISMATCH', result['error']['code'])
        self.assertEqual('required', transport.requests[0][0]['tool_choice'])
        self.assertEqual(1, len(calls))
        self.assertEqual(raw, calls[0]['model_text'])
        self.assertEqual([], result['runtime']['tool_trace'])

    def test_cq06_transmits_observed_choices_and_common_still_rejects_input_citations(self):
        from examples.order_fulfillment import b_terminal_input as terminal
        from ontologics.model import ROOT
        source = terminal.load_input(ROOT / 'data/order_fulfillment/examples/basic_800_1000.md')
        for case in ('cq06_defects11', 'cq06_transport2h'):
            for invalid in (False, True):
                with self.subTest(case=case, invalid=invalid):
                    role, task = (terminal.case_task(case, source, version=self.version)
                                  if case == 'cq06_transport2h'
                                  else case_task(case, version=self.version))
                    catalog_ids = {eid for node in task.data()['catalog_nodes'] for eid in node['evidence']}
                    input_id = next(eid for eid in task.data()['review_material']['comparison']
                                    ['integrity_evidence_by_side']['before']
                                    if eid not in catalog_ids)
                    def select(value):
                        value['evidence_ids'] = (value['evidence_ids'] + [input_id]
                                                 if invalid else value['evidence_ids'][:1])
                    transport = LocalModel(role, task, final_transform=select)
                    result, calls = connection.execute_case(self.host, case, task, role,
                        settings=self.settings, budget=connection.new_budget(self.policy), opener=transport)
                    self.assertNotIn('text', transport.requests[0][0])
                    observed = set()
                    for index, (payload, _) in enumerate(transport.requests[1:]):
                        node = next(node for node in task.data()['catalog_nodes']
                                    if node['id'] == transport.calls[index]['arguments']['node_id'])
                        observed.update(node['evidence'])
                        choices = payload['text']['format']['schema']['properties']['evidence_ids']['items']['enum']
                        self.assertEqual(sorted(observed), choices)
                        self.assertNotIn(input_id, choices)
                    self.assertEqual(not invalid, result['runtime']['ok'], result['error'])
                    self.assertEqual(4, len(calls))
                    self.assertEqual(transport.final_text, calls[-1]['model_text'])
                    self.assertEqual(0, result['runtime']['repairs'])
                    if invalid:
                        self.assertEqual('AGENT_EVIDENCE_MISMATCH', result['error']['code'])
                        self.assertIsNone(result['payload'])
                    else:
                        self.assertEqual(task.data()['input_sources_by_side'],
                                         result['payload']['input_sources_by_side'])
                        self.assertEqual(1, len(result['payload']['evidence_ids']))

    def test_remaining_cases_accept_main_lookup_with_both_kind_and_node_id(self):
        for case in CASES[1:]:
            with self.subTest(case=case):
                role, task = case_task(case, version=self.version)
                transport = LocalModel(role, task)
                nodes = {node['id']: node for node in task.data()['catalog_nodes']}
                for call in transport.calls:
                    call['arguments']['kind'] = nodes[call['arguments']['node_id']]['type']
                result, calls = connection.execute_case(self.host, case, task, role,
                    settings=self.settings, budget=connection.new_budget(self.policy), opener=transport)
                self.assertTrue(result['runtime']['ok'], result['error'])
                self.assertIsNone(result['error'])
                self.assertEqual(4, len(calls))

    def test_remaining_cases_accept_one_relevant_read_and_common_rejects_unread_citation(self):
        for case in CASES[1:]:
            for unread in (False, True):
                with self.subTest(case=case, unread=unread):
                    role, task = case_task(case, version=self.version)
                    transport = LocalModel(role, task, final_transform=(
                        (lambda value: value.update(evidence_ids=['evidence:not-read'])) if unread else None))
                    transport.calls = transport.calls[:1]
                    node = task.data()['catalog_nodes'][0]
                    transport.calls[0]['arguments']['kind'] = node['type']
                    result, calls = connection.execute_case(self.host, case, task, role,
                        settings=self.settings, budget=connection.new_budget(self.policy), opener=transport)
                    self.assertEqual(2, len(calls))
                    self.assertEqual(not unread, result['runtime']['ok'], result['error'])
                    if unread:
                        self.assertEqual('AGENT_EVIDENCE_MISMATCH', result['error']['code'])

    def test_remaining_cases_accept_broad_kind_reads_and_reordered_reads(self):
        for case in CASES[1:]:
            for broad in (True, False):
                with self.subTest(case=case, broad=broad):
                    role, task = case_task(case, version=self.version)
                    transport = LocalModel(role, task)
                    if broad:
                        node = task.data()['catalog_nodes'][0]
                        transport.calls = [dict(name='lookup', arguments=dict(
                            kind=node['type'], version=self.version))]
                    else:
                        transport.calls.reverse()
                    result, calls = connection.execute_case(self.host, case, task, role,
                        settings=self.settings, budget=connection.new_budget(self.policy), opener=transport)
                    self.assertTrue(result['runtime']['ok'], result['error'])
                    self.assertEqual(2 if broad else 4, len(calls))

    def test_nine_cases_bind_business_results_without_model_transcription(self):
        for case in CASES[1:]:
            with self.subTest(case=case):
                role, task = case_task(case, version=self.version)
                business = 'promise' if case == 'cq01_promise' else 'assessment'
                def minimal(value):
                    value.pop(business, None)
                result, calls, transport = self.execute(case, final_transform=minimal,
                    budget=connection.new_budget(self.policy))
                self.assertTrue(result['runtime']['ok'], result['error'])
                self.assertEqual(task.data()[business], result['payload'][business])
                self.assertNotIn(business, json.loads(transport.final_text))
                self.assertEqual(transport.final_text, calls[-1]['model_text'])
                self.assertEqual(4, len(calls))
                self.assertEqual(json.loads(transport.final_text)['answer_ko'], result['payload']['answer_ko'])

    def test_legacy_business_echo_is_ignored_and_raw_response_is_preserved(self):
        for case in CASES[1:]:
            role, task = case_task(case, version=self.version)
            business = 'promise' if case == 'cq01_promise' else 'assessment'
            for echo in (None, [], {'input_state': 'approved', 'quantity': 0}):
                with self.subTest(case=case, echo=echo):
                    result, calls, transport = self.execute(case,
                        final_transform=lambda value: value.update({business: echo}),
                        budget=connection.new_budget(self.policy))
                    self.assertTrue(result['runtime']['ok'], result['error'])
                    self.assertEqual(task.data()[business], result['payload'][business])
                    self.assertEqual(echo, json.loads(calls[-1]['model_text'])[business])
                    self.assertEqual(transport.final_text, calls[-1]['model_text'])
                    self.assertEqual(4, len(calls))

    def test_business_binding_keeps_model_contract_and_common_version_checks(self):
        for case in CASES[1:]:
            for change in ({'data_version': self.version + 1}, {'unexpected': True},
                           {'evidence_ids': ['evidence:not-read']}):
                with self.subTest(case=case, change=change):
                    result, calls, transport = self.execute(case,
                        final_transform=lambda value: value.update(change),
                        budget=connection.new_budget(self.policy))
                    self.assertFalse(result['runtime']['ok'])
                    self.assertIn(result['error']['code'], ('AGENT_RESULT_VERSION_MISMATCH',
                        'INVALID_FINAL_PAYLOAD', 'AGENT_EVIDENCE_MISMATCH'))
                    self.assertIsNone(result['payload'])
                    self.assertEqual(4, len(calls))
                    self.assertEqual(transport.final_text, calls[-1]['model_text'])

    def test_actual_adapter_payload_and_shared_tool_reads(self):
        result, calls, transport = self.execute()
        self.assertTrue(result['runtime']['ok'], result['error'])
        self.assertEqual('ready', result['payload']['extraction']['status'])
        self.assertEqual(4, len(calls))
        self.assertEqual(4, len(transport.requests))
        self.assertEqual(4, result['request_execution_budget']['attempted_calls']['model'])
        self.assertEqual(4, result['request_execution_budget']['attempted_calls']['tool'])
        self.assertEqual(3, result['runtime']['execution_budget']['attempted_calls']['tool'])
        self.assertEqual(3, len(result['runtime']['tool_trace']))
        for payload, timeout in transport.requests:
            self.assertEqual(MODEL, payload['model'])
            self.assertEqual(4096, payload['max_output_tokens'])
            self.assertEqual(30, timeout)
            self.assertFalse(payload['parallel_tool_calls'])
            self.assertFalse(payload['store'])
        self.assertNotIn(SECRET, json.dumps(calls))
        self.assertEqual(4, result['request_execution_budget']['cost']['paid_calls'])
        self.assertEqual([dict(name='lookup', arguments=dict(kind='ProductModel', version=self.version))],
                         calls[0]['model_tool_calls'])
        self.assertEqual([], calls[-1]['model_tool_calls'])
        self.assertEqual(transport.final_text, calls[-1]['model_text'])
        raw = json.loads(calls[-1]['model_text'])
        self.assertEqual(raw, {key: result['payload'][key] for key in raw})
        for key in ('input_revision', 'input_sha256', 'input_sources'):
            self.assertNotIn(key, raw)
        refs = {ref['evidence_id'] for ref in result['evidence_refs']}
        self.assertEqual(refs, set(result['payload']['evidence_ids']))
        for node in result['payload']['extraction']['nodes']:
            self.assertTrue(node['evidence'])
            self.assertTrue(refs.isdisjoint(node['evidence']))

    def test_rejected_final_citations_are_recorded_without_repair_or_retry(self):
        cases = (
            ('unread', lambda ids: ids + ['evidence:not-read'], 'AGENT_EVIDENCE_MISMATCH'),
            ('synthetic', lambda ids: ids + ['evidence:demo:B-01:contract'], 'AGENT_EVIDENCE_MISMATCH'),
            ('duplicate', lambda ids: ids + ids[:1], 'AGENT_EVIDENCE_MISMATCH'),
            ('non_string', lambda ids: ids + [7], 'AGENT_EVIDENCE_MISMATCH'),
        )
        for label, mutate, code in cases:
            with self.subTest(case=label):
                result, calls, transport = self.execute(
                    final_transform=lambda value: value.update(evidence_ids=mutate(value['evidence_ids'])),
                    budget=connection.new_budget(self.policy))
                self.assertFalse(result['runtime']['ok'])
                self.assertEqual(code, result['error']['code'])
                self.assertIsNone(result['payload'])
                self.assertEqual([], result['evidence_refs'])
                self.assertEqual(4, len(transport.requests))
                self.assertEqual(4, len(calls))
                self.assertEqual(0, result['runtime']['repairs'])
                self.assertEqual(transport.final_text, calls[-1]['model_text'])
                self.assertNotIn(SECRET, json.dumps(calls))

    def test_natural_answer_and_read_subset_or_order_do_not_block_human_review(self):
        answer = '가상 계약 1,000개의 접수 후보를 만들었습니다. 저장 전 원문과 후보를 검토해 주세요.'
        for select in (lambda ids: ids[::-1], lambda ids: ids[:1]):
            with self.subTest(selection=select):
                result, calls, transport = self.execute(final_transform=lambda value: value.update(
                    answer_ko=answer, evidence_ids=select(value['evidence_ids'])),
                    budget=connection.new_budget(self.policy))
                self.assertTrue(result['runtime']['ok'], result['error'])
                self.assertEqual(answer, result['payload']['answer_ko'])
                self.assertTrue(result['payload']['needs_human_review'])
                self.assertEqual(set(result['payload']['evidence_ids']),
                                 {ref['evidence_id'] for ref in result['evidence_refs']})
                self.assertEqual(transport.final_text, calls[-1]['model_text'])
                self.assertEqual(4, len(transport.requests))

    def test_model_metadata_is_not_authority_and_original_text_is_preserved(self):
        def wrong_copy(value):
            value.update(input_sha256='0' * 64, input_revision='bad-copy',
                         input_sources=[{'source_sha256': 'bad-copy'}])
        result, calls, transport = self.execute(final_transform=wrong_copy)
        self.assertTrue(result['runtime']['ok'], result['error'])
        task = case_task('cq01_intake', version=self.version)[1]
        for key in ('input_sha256', 'input_revision', 'input_sources'):
            self.assertEqual(task.data()[key], result['payload'][key])
        self.assertEqual(transport.final_text, calls[-1]['model_text'])
        self.assertEqual('0' * 64, json.loads(calls[-1]['model_text'])['input_sha256'])

    def test_remaining_cases_bind_provenance_and_allow_read_subset_and_natural_prose(self):
        answer = '고정된 가상 업무 자료의 계산과 남은 확인 사항을 검토해 주세요. ' * 100
        for case in CASES[1:]:
            with self.subTest(case=case):
                role, task = case_task(case, version=self.version)
                def legacy_copy(value):
                    value['evidence_ids'] = value['evidence_ids'][:1]
                    value['answer_ko'] = answer
                    if case.startswith('cq06_'):
                        value['assessment'] = copy.deepcopy(task.data()['assessment'])
                        value['input_sources_by_side'] = {'before': [], 'after': []}
                        value['assessment']['review_material_sha256'] = 'bad-copy'
                        for side in ('before', 'after'):
                            value['assessment'][side].update(input_revision='bad-copy', input_sha256='bad-copy')
                    else:
                        value.update(input_revision='bad-copy', input_sha256='bad-copy', input_sources=[])
                        if case.startswith('cq05_'):
                            value['assessment'] = copy.deepcopy(task.data()['assessment'])
                            value['assessment']['review_material_sha256'] = 'bad-copy'
                result, calls, transport = self.execute(case, final_transform=legacy_copy,
                    budget=connection.new_budget(self.policy))
                self.assertTrue(result['runtime']['ok'], result['error'])
                role, task = case_task(case, version=self.version)
                payload = result['payload']
                self.assertEqual(answer, payload['answer_ko'])
                if case.startswith('cq06_'):
                    self.assertEqual(task.data()['input_sources_by_side'], payload['input_sources_by_side'])
                else:
                    for key in ('input_revision', 'input_sha256', 'input_sources'):
                        self.assertEqual(task.data()[key], payload[key])
                if 'assessment' in payload:
                    self.assertEqual(task.data()['assessment'], payload['assessment'])
                self.assertEqual(transport.final_text, calls[-1]['model_text'])
                self.assertEqual(1, len(payload['evidence_ids']))
                self.assertEqual(set(payload['evidence_ids']),
                                 {ref['evidence_id'] for ref in result['evidence_refs']})
                self.assertEqual(4, len(calls))

    def test_all_cases_bind_missing_or_wrong_metadata_without_rewriting_model_text(self):
        for case in CASES:
            role, task = case_task(case, version=self.version)
            for omitted in (True, False):
                with self.subTest(case=case, omitted=omitted):
                    raw_outputs = []
                    def metadata_copy(value):
                        if 'assessment' in task.data():
                            value['assessment'] = copy.deepcopy(task.data()['assessment'])
                        def change(record, field):
                            if omitted:
                                record.pop(field, None)
                            else:
                                record[field] = 'untrusted-model-copy'
                        if case.startswith('cq06_'):
                            change(value, 'input_sources_by_side')
                            change(value['assessment'], 'review_material_sha256')
                            for side in ('before', 'after'):
                                for key in ('input_revision', 'input_sha256'):
                                    change(value['assessment'][side], key)
                        else:
                            for key in ('input_revision', 'input_sha256', 'input_sources'):
                                change(value, key)
                            if case == 'cq01_intake':
                                for node in value['extraction']['nodes']:
                                    change(node, 'evidence')
                            elif case == 'cq03_normal':
                                for assumption in value['assessment']['assumptions']:
                                    change(assumption, 'evidence')
                            elif case.startswith('cq05_'):
                                change(value['assessment'], 'review_material_sha256')
                        raw_outputs.append(json.loads(json.dumps(value)))
                    result, calls, transport = self.execute(case, final_transform=metadata_copy,
                        budget=connection.new_budget(self.policy))
                    self.assertTrue(result['runtime']['ok'], result['error'])
                    role, task = case_task(case, version=self.version)
                    module = role[0]
                    data = task.data()
                    payload = result['payload']
                    if 'assessment' in data:
                        self.assertEqual(data['assessment'], payload['assessment'])
                    elif case == 'cq01_intake':
                        self.assertEqual(module._extract(data), payload['extraction'])
                    else:
                        self.assertEqual(data['promise'], payload['promise'])
                    if case.startswith('cq06_'):
                        self.assertEqual(data['input_sources_by_side'], payload['input_sources_by_side'])
                    else:
                        for key in ('input_revision', 'input_sha256', 'input_sources'):
                            self.assertEqual(data[key], payload[key])
                    self.assertEqual(4, len(calls))
                    self.assertEqual(transport.final_text, calls[-1]['model_text'])
                    self.assertEqual(raw_outputs[-1], json.loads(calls[-1]['model_text']))
                    self.assertEqual(set(payload['evidence_ids']),
                                     {ref['evidence_id'] for ref in result['evidence_refs']})

    def test_intake_candidate_business_fields_are_still_model_owned_and_validated(self):
        mutations = (
            ('cq01_intake', lambda value: value['extraction']['nodes'][0].update(id='other')),
            ('cq01_intake', lambda value: value['extraction']['nodes'][0].update(type='ContractItem')),
            ('cq01_intake', lambda value: value['extraction']['nodes'][1].update(quantity=999)),
        )
        for index, (case, mutate) in enumerate(mutations):
            with self.subTest(case=case, mutation=index):
                result, calls, transport = self.execute(case, final_transform=mutate,
                    budget=connection.new_budget(self.policy))
                self.assertFalse(result['runtime']['ok'])
                self.assertIn(result['error']['code'], ('AGENT_RESULT_REJECTED', 'INVALID_FINAL_PAYLOAD'))
                self.assertIsNone(result['payload'])
                self.assertEqual([], result['evidence_refs'])
                self.assertEqual(4, len(calls))
                self.assertEqual(0, result['runtime']['repairs'])
                self.assertEqual(transport.final_text, calls[-1]['model_text'])

    def test_remaining_cases_allow_evidence_order_but_reject_unread_citations(self):
        for case in CASES[1:]:
            with self.subTest(case=case):
                result, _, _ = self.execute(case, final_transform=lambda value: value.update(
                    evidence_ids=value['evidence_ids'][::-1]), budget=connection.new_budget(self.policy))
                self.assertTrue(result['runtime']['ok'], result['error'])
                result, calls, _ = self.execute(case, final_transform=lambda value: value.update(
                    evidence_ids=value['evidence_ids'] + ['evidence:not-read']),
                    budget=connection.new_budget(self.policy))
                self.assertEqual('AGENT_EVIDENCE_MISMATCH', result['error']['code'])
                self.assertIsNone(result['payload'])
                self.assertEqual(4, len(calls))

    def test_invalid_lookup_versions_are_preserved_and_never_corrected_or_retried(self):
        for arguments in (dict(kind='ProductModel'), dict(kind='ProductModel', version=None),
                dict(kind='ProductModel', version=str(self.version)),
                dict(kind='ProductModel', version=self.version + 1)):
            with self.subTest(arguments=arguments):
                role, task = case_task('cq01_intake', version=self.version)
                transport = LocalModel(role, task)
                transport.calls = [dict(name='lookup', arguments=arguments)]
                result, calls = connection.execute_case(self.host, 'cq01_intake', task, role,
                    settings=self.settings, budget=connection.new_budget(self.policy), opener=transport)
                self.assertFalse(result['runtime']['ok'])
                self.assertEqual('PINNED_CONTEXT_MISMATCH', result['error']['code'])
                self.assertEqual(1, len(transport.requests))
                self.assertEqual([dict(name='lookup', arguments=arguments)], calls[0]['model_tool_calls'])
                self.assertEqual([], result['runtime']['tool_trace'])
                self.assertNotIn(SECRET, json.dumps(calls))

    def test_transient_failure_retries_once_and_keeps_unknown_cost(self):
        for failure in (TimeoutError('local timeout'),
                HTTPError(ENDPOINT, 429, 'local limited', {}, None),
                HTTPError(ENDPOINT, 503, 'local unavailable', {}, None)):
            with self.subTest(kind=type(failure).__name__, code=getattr(failure, 'code', None)):
                budget = connection.new_budget(self.policy)
                result, calls, transport = self.execute(failures=[failure], budget=budget)
                self.assertTrue(result['runtime']['ok'], result['error'])
                self.assertEqual(5, len(transport.requests))
                self.assertEqual(5, len(calls))
                self.assertEqual(5, budget.snapshot()['attempted_calls']['model'])
                self.assertEqual('usage_unreported_reserved', calls[0]['cost']['status'])
                self.assertEqual('0.408192', budget.snapshot()['cost']['unreported_reserved_usd'])
                self.assertEqual(1, budget.snapshot()['cost']['unreported_calls'])

    def test_second_transient_failure_stops_after_two_attempts(self):
        result, calls, transport = self.execute(failures=[TimeoutError(), TimeoutError()])
        self.assertFalse(result['runtime']['ok'])
        self.assertEqual('TRANSIENT_MODEL_ERROR', result['error']['code'])
        self.assertEqual(2, len(transport.requests))
        self.assertEqual(2, len(calls))
        self.assertEqual(0, len(result['runtime']['tool_trace']))

    def test_authentication_and_malformed_final_are_not_retried(self):
        result, calls, transport = self.execute(
            failures=[HTTPError(ENDPOINT, 401, 'local unauthorized', {}, None)])
        self.assertFalse(result['runtime']['ok'])
        self.assertEqual('OPENAI_AUTH_FAILED', result['error']['code'])
        self.assertEqual((1, 1), (len(calls), len(transport.requests)))
        result, calls, transport = self.execute(final='not JSON')
        self.assertFalse(result['runtime']['ok'])
        self.assertEqual('INVALID_FINAL_PAYLOAD', result['error']['code'])
        self.assertEqual((1, 1), (len(calls), len(transport.requests)))
        self.assertEqual(0, result['runtime']['repairs'])
        self.assertEqual('not JSON', calls[-1]['model_text'])

    def test_missing_and_insufficient_cost_policy_block_before_http(self):
        for selected, expected in ((None, 'COST_POLICY_REQUIRED'),
                (policy('0.4'), 'COST_BUDGET_EXCEEDED')):
            with self.subTest(code=expected):
                result, calls, transport = self.execute(
                    host=connection.make_host(self.database, selected),
                    budget=connection.new_budget(selected))
                self.assertFalse(result['runtime']['ok'])
                self.assertEqual(expected, result['error']['code'])
                self.assertEqual([], calls)
                self.assertEqual([], transport.requests)

    def test_mismatching_root_policy_is_rejected_before_http(self):
        role, task = case_task('cq01_intake', version=self.version)
        transport = LocalModel(role, task)
        with self.assertRaises(DomainError) as caught:
            connection.execute_case(self.host, 'cq01_intake', task, role,
                settings=self.settings, budget=connection.new_budget(policy('9')), opener=transport)
        self.assertEqual('COST_POLICY_MISMATCH', caught.exception.code)
        self.assertEqual([], transport.requests)

    def test_two_representatives_share_one_ledger_without_resetting(self):
        first, first_calls, _ = self.execute()
        first_snapshot = json.loads(json.dumps(first['request_execution_budget']))
        second, second_calls, _ = self.execute('cq01_promise')
        self.assertTrue(second['runtime']['ok'], second['error'])
        total = len(first_calls) + len(second_calls)
        self.assertEqual(8, total)
        self.assertEqual(total, second['request_execution_budget']['attempted_calls']['model'])
        self.assertEqual(total, second['request_execution_budget']['cost']['paid_calls'])
        self.assertEqual(4, first_snapshot['attempted_calls']['model'])
        self.assertEqual(first_snapshot, first['request_execution_budget'])
        self.assertGreater(float(second['request_execution_budget']['cost']['reported_cost_usd']),
                           float(first_snapshot['cost']['reported_cost_usd']))

    def test_unapproved_profile_override_is_rejected_before_opener(self):
        role, task = case_task('cq01_intake', version=self.version)
        transport = LocalModel(role, task)
        for settings in (OpenAISettings(SECRET, max_output_tokens=1024),
                OpenAISettings(SECRET, timeout_seconds=31)):
            with self.subTest(settings=settings.public_config()), self.assertRaises(ValueError):
                connection.execute_case(self.host, 'cq01_intake', task, role,
                    settings=settings, budget=self.budget, opener=transport)
        self.assertEqual([], transport.requests)
