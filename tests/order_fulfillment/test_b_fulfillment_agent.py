"""Pure provenance/provider/CLI guards; no stored B graph fixture."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
from ontologics.harness import BudgetLimits, ExecutionBudget, request_budget
from ontologics.paid_budget import CostPolicy, ModelPrice
from unittest import mock
from examples.order_fulfillment.b_fulfillment_agent import PROMPT, TOOLS, ProvenanceProvider, main
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAIProvider, OpenAISettings, ProviderError
from ontologics.order_fulfillment.tool_specs import function_tools


class ProvenanceProviderTests(unittest.TestCase):
    """No network: reproduce the observed wrong route without accepting it."""
    def setUp(self):
        # Explicit synthetic pricing for local HTTP doubles; no network allowance.
        self.cost_policy = CostPolicy('100', (ModelPrice(MODEL, '1', '2', 400000),))
        self.budget = ExecutionBudget(BudgetLimits(max_model_calls=64), cost_policy=self.cost_policy)
        scope = request_budget('b-provider-test', self.budget)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)

    task = dict(delivery_round_id='round:changed', as_of='2026-11-01T10:00:00+09:00', data_version=7)

    def history(self, count=1, *, snapshot=True):
        results = [dict(ok=True, data_version=7,
            evidence=['evidence:new:snapshot', 'evidence:new:item', 'evidence:new:context'], data=dict(
            snapshot={'evidence': ['evidence:new:snapshot']} if snapshot else None,
            items=[dict(evidence=['evidence:new:item', 'evidence:new:secondary'])])),
            dict(ok=True, data_version=7, data=[dict(id='evidence:new:snapshot', type='Evidence',
                 source_id='source:new:snapshot')]),
            dict(ok=True, data_version=7, data=[dict(id='source:new:snapshot', type='SourceDocument')])]
        return [{'role': 'tool', 'name': 'fulfillment_status' if i == 0 else 'lookup',
                 'content': canonical(result)} for i, result in enumerate(results[:count])]

    def invoke(self, response, history):
        provider = mock.Mock(return_value=response)
        wrapper = ProvenanceProvider(provider, self.task)
        value = wrapper(model=MODEL, messages=history)
        self.assertIs(value, response)
        sent = provider.call_args.kwargs['messages']
        if response['tool_calls']:
            provider.assert_called_once_with(model=MODEL, messages=history)
        else:
            self.assertEqual(history, sent[:-1])
            self.assertEqual('system', sent[-1]['role'])
        return value

    def final_reply(self, *, snapshot=True):
        ids = ['evidence:new:item', 'evidence:new:snapshot']
        if not snapshot: ids += ['evidence:new:context']
        return dict(text=canonical(dict(evidence=ids)), tool_calls=[])

    def test_changed_snapshot_ids_are_derived_from_results_without_rewriting_history(self):
        for count, node in [(1, 'evidence:new:snapshot'), (2, 'source:new:snapshot')]:
            with self.subTest(count=count):
                self.invoke(dict(text='', tool_calls=[dict(name='lookup', arguments=dict(
                    node_id=node, version=7))]), self.history(count))
        self.invoke(self.final_reply(), self.history(3))

    def test_contract_evidence_wrong_source_version_and_extra_calls_are_rejected(self):
        for count, args in [(1, dict(node_id='evidence:demo:B-01:contract', version=7)),
                            (2, dict(node_id='source:demo:B-01:contract-v1', version=7)),
                            (1, dict(node_id='evidence:new:snapshot', version=8)),
                            (1, dict(node_id='evidence:new:snapshot', version=True)),
                            (1, dict(node_id='evidence:new:snapshot', version=7, kind='SourceDocument'))]:
            with self.subTest(count=count, args=args), self.assertRaises(ProviderError):
                self.invoke(dict(text='', tool_calls=[dict(name='lookup', arguments=args)]), self.history(count))
        call = dict(name='lookup', arguments=dict(node_id='evidence:new:snapshot', version=7))
        with self.assertRaises(ProviderError):
            self.invoke(dict(text='', tool_calls=[call, call]), self.history())

    def test_early_final_and_query_argument_changes_are_rejected(self):
        for count in (0, 1, 2):
            with self.subTest(count=count), self.assertRaises(ProviderError):
                self.invoke(dict(text='premature', tool_calls=[]), self.history(count))
        with self.assertRaises(ProviderError):
            self.invoke(dict(text='', tool_calls=[dict(name='fulfillment_status', arguments=dict(
                delivery_round_id='wrong', as_of=self.task['as_of'], version=7))]), [])

    def test_missing_snapshot_allows_final_without_fabricated_provenance(self):
        self.invoke(self.final_reply(snapshot=False), self.history(snapshot=False))
        with self.assertRaises(ProviderError):
            self.invoke(dict(text='', tool_calls=[dict(name='lookup', arguments=dict(
                node_id='evidence:new:snapshot', version=7))]), self.history(snapshot=False))

    def test_failed_or_wrong_type_results_do_not_allow_final(self):
        for result in [dict(ok=False), dict(ok=True, data_version=8, data=[]),
                       dict(ok=True, data_version=7, data=[dict(type='ProductModel')])]:
            history = self.history(2)
            history[-1]['content'] = canonical(result)
            with self.subTest(result=result), self.assertRaises(ProviderError):
                self.invoke(dict(text='final', tool_calls=[]), history)

    def test_openai_tools_are_scoped_without_mutating_global_schemas_or_history(self):
        baseline = copy.deepcopy(function_tools(TOOLS))
        provider = OpenAIProvider(OpenAISettings(api_key='fake-local-test-key'), TOOLS)
        wrapper = ProvenanceProvider(provider, self.task)
        self.assertEqual(provider.public_config(), wrapper.public_config())
        self.assertNotIn('fake-local-test-key', canonical(wrapper.public_config()))
        self.assertIsNone(ProvenanceProvider(object(), self.task).public_config)
        for count, name, node in [(0, 'fulfillment_status', None),
                                  (1, 'lookup', 'evidence:new:snapshot'),
                                  (2, 'lookup', 'source:new:snapshot'), (3, None, None)]:
            args = dict(delivery_round_id=self.task['delivery_round_id'], as_of=self.task['as_of'], version=7)
            if node: args = dict(node_id=node, version=7)
            response = dict(text='', tool_calls=[dict(name=name, arguments=args)]) if name else self.final_reply()
            history = self.history(count)
            with mock.patch.object(OpenAIProvider, '__call__', return_value=response) as invoke:
                self.assertIs(wrapper(model=MODEL, messages=history), response)
                sent = invoke.call_args.kwargs['messages']
                self.assertEqual(history, sent if name else sent[:-1])
            self.assertEqual([name] if name else [], [t['name'] for t in provider.tools])
            if node:
                self.assertEqual([node], provider.tools[0]['parameters']['properties']['node_id']['enum'])
                self.assertEqual([7], provider.tools[0]['parameters']['properties']['version']['enum'])
            self.assertEqual(baseline, function_tools(TOOLS))

    def test_rejected_selection_does_not_retry_provider(self):
        provider = mock.Mock(return_value=dict(text='', tool_calls=[dict(name='lookup', arguments=dict(
            node_id='evidence:demo:B-01:contract', version=7))]))
        with self.assertRaises(ProviderError):
            ProvenanceProvider(provider, self.task)(model=MODEL, messages=self.history())
        self.assertEqual(1, provider.call_count)

    def test_fake_http_preserves_original_call_ids_across_four_scoped_requests(self):
        from tests.test_openai_provider import Transport, response, text, tool
        args = dict(delivery_round_id=self.task['delivery_round_id'], as_of=self.task['as_of'], version=7)
        outputs = [[tool('fulfillment_status', args, cid='call_query')],
                   [tool('lookup', dict(node_id='evidence:new:snapshot', version=7), cid='call_evidence')],
                   [tool('lookup', dict(node_id='source:new:snapshot', version=7), cid='call_source')],
                   [text(self.final_reply()['text'])]]
        transport = Transport([response(output) for output in outputs])
        provider = OpenAIProvider(OpenAISettings(api_key='fake-local-test-key', max_output_tokens=1024),
                                  TOOLS, opener=transport)
        wrapper = ProvenanceProvider(provider, self.task)
        messages = [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': canonical(self.task)}]
        results = self.history(3)
        for step in range(4):
            reply = wrapper(model=MODEL, messages=messages)
            if step < 3:
                messages.append(dict(role='assistant', content=reply['text'], tool_calls=reply['tool_calls']))
                messages.append({**results[step], 'call_index': 0})
        payload = json.loads(transport.requests[-1][0].data)
        self.assertEqual([], payload['tools'])
        self.assertEqual('system', payload['input'][-1]['role'])
        self.assertIn('evidence:new:item', payload['input'][-1]['content'])
        self.assertNotIn('evidence:new:context', payload['input'][-1]['content'])
        self.assertEqual(['call_query', 'call_evidence', 'call_source'],
            [item['call_id'] for item in payload['input'] if item.get('type') == 'function_call_output'])
        self.assertEqual([item for output in outputs[:3] for item in output],
            [item for item in payload['input'] if item.get('type') == 'function_call'])
        self.assertEqual(4, len(provider.calls))
        self.assertEqual(16, sum(event['usage']['input_tokens'] for event in provider.calls))
        self.assertTrue(all(json.loads(req.data)['max_output_tokens'] == 1024 for req, _ in transport.requests))

    def test_final_guidance_and_guard_derive_changed_ids_and_counts_without_trimming(self):
        history = self.history(3)
        query = json.loads(history[0]['content'])
        query['data']['items'] = [dict(evidence=['changed:first', 'unused:second']),
                                  dict(evidence=['changed:another']), dict(evidence=['changed:first'])]
        history[0]['content'] = canonical(query)
        reply = dict(text=canonical(dict(evidence=['changed:another', 'changed:first', 'evidence:new:snapshot'])),
                     tool_calls=[])
        provider = mock.Mock(return_value=reply)
        guard = ProvenanceProvider(provider, self.task)
        self.assertIs(reply, guard(model=MODEL, messages=history))
        self.assertEqual(['changed:another', 'changed:first', 'evidence:new:snapshot'],
                         guard.final_submission['expected_evidence'])
        self.assertNotIn('unused:second', provider.call_args.kwargs['messages'][-1]['content'])
        self.assertEqual(query, json.loads(history[0]['content']))

    def test_final_guard_rejects_extra_missing_secondary_duplicate_and_malformed_ids(self):
        for ids in [['evidence:new:item', 'evidence:new:snapshot', 'evidence:new:context'],
                    ['evidence:new:snapshot'], ['evidence:new:secondary', 'evidence:new:snapshot'],
                    ['evidence:new:item', 'evidence:new:snapshot', 'evidence:new:item'],
                    'evidence:new:item', [dict(id='evidence:new:item')]]:
            reply = dict(text=canonical(dict(evidence=ids)), tool_calls=[])
            provider = mock.Mock(return_value=reply)
            guard = ProvenanceProvider(provider, self.task)
            with self.subTest(ids=ids), self.assertRaises(ProviderError) as caught:
                guard(model=MODEL, messages=self.history(3))
            self.assertEqual('CQ02_ANSWER_EVIDENCE', caught.exception.code)
            self.assertEqual(reply['text'], guard.final_submission['text'])
            self.assertFalse(guard.final_submission['accepted'])
            self.assertEqual(1, provider.call_count)

    def test_no_snapshot_rejects_dropped_context_and_final_request_respects_context_budget(self):
        guard = ProvenanceProvider(mock.Mock(return_value=self.final_reply()), self.task)
        with self.assertRaises(ProviderError):
            guard(model=MODEL, messages=self.history(snapshot=False))
        history = self.history(3)
        history.insert(0, dict(role='system', content='x' * 65536))
        provider = mock.Mock(return_value=self.final_reply())
        with self.assertRaises(ProviderError) as caught:
            ProvenanceProvider(provider, self.task)(model=MODEL, messages=history)
        self.assertEqual('CONTEXT_LIMIT', caught.exception.code)
        provider.assert_not_called()

    def test_rejected_final_http_response_preserves_billed_usage_and_raw_submission(self):
        from tests.test_openai_provider import Transport, response, text, tool
        args = dict(delivery_round_id=self.task['delivery_round_id'], as_of=self.task['as_of'], version=7)
        bad_text = canonical(dict(evidence=['evidence:new:snapshot', 'evidence:new:item', 'evidence:new:context']))
        outputs = [[tool('fulfillment_status', args, cid='call_query')],
                   [tool('lookup', dict(node_id='evidence:new:snapshot', version=7), cid='call_evidence')],
                   [tool('lookup', dict(node_id='source:new:snapshot', version=7), cid='call_source')],
                   [text(bad_text)]]
        transport = Transport([response(output) for output in outputs])
        provider = OpenAIProvider(OpenAISettings(api_key='fake-local-test-key'), TOOLS, opener=transport)
        guard = ProvenanceProvider(provider, self.task)
        messages = [{'role': 'user', 'content': canonical(self.task)}]
        results = self.history(3)
        for step in range(3):
            reply = guard(model=MODEL, messages=messages)
            messages.append(dict(role='assistant', content=reply['text'], tool_calls=reply['tool_calls']))
            messages.append({**results[step], 'call_index': 0})
        with self.assertRaises(ProviderError) as caught:
            guard(model=MODEL, messages=messages)
        self.assertEqual('CQ02_ANSWER_EVIDENCE', caught.exception.code)
        self.assertEqual(4, len(transport.requests))
        self.assertEqual(4, len(provider.calls))
        self.assertEqual(12, sum(event['usage']['output_tokens'] for event in provider.calls))
        self.assertEqual(bad_text, guard.final_submission['text'])
        self.assertFalse(guard.final_submission['accepted'])


class BFulfillmentAgentTests(unittest.TestCase):
    def test_existing_cli_report_blocks_configuration_and_execution_before_api(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'existing.json'
            original = b'{"prior_run":"preserve unchanged"}\n'
            output.write_bytes(original)
            with mock.patch('sys.argv', ['b_fulfillment_agent', '--provider', 'openai',
                                         '--output', str(output)]), \
                 mock.patch('examples.order_fulfillment.b_fulfillment_agent.OpenAISettings.from_environment') as settings, \
                 mock.patch('ontologics.database.environment_values') as environment, \
                 mock.patch('examples.order_fulfillment.b_fulfillment_agent.run_example') as execute:
                with self.assertRaisesRegex(SystemExit, 'B_LEGACY_EXECUTION_RETIRED'):
                    main()
                settings.assert_not_called()
                environment.assert_not_called()
                execute.assert_not_called()
            self.assertEqual(original, output.read_bytes())
