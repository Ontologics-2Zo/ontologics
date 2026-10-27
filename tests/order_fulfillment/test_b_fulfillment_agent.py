"""B-CQ02 wiring, factual boundaries and cost controls; no paid API calls."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from examples.order_fulfillment.b_fulfillment_agent import (
    ACTOR, PROMPT, TOOLS, ProvenanceProvider, ScriptedProvider, evaluate, main, run_example, setup,
)
from ontologics.model import ROOT, canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAIProvider, OpenAISettings, ProviderError
from ontologics.order_fulfillment.runtime import ModelConfig, ModelRuntime
from ontologics.order_fulfillment.tool_specs import B_FULFILLMENT_TOOLS, function_tools


class ProvenanceProviderTests(unittest.TestCase):
    """No network: reproduce the observed wrong route without accepting it."""
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
from tests.db import database_for_test


class BFulfillmentAgentTests(unittest.TestCase):
    def fixture(self, case='normal'):
        service, task = setup(database_for_test(self), case=case)
        self.addCleanup(service.close)
        return service, task

    def execute(self, service, task, provider=None):
        return ModelRuntime(
            service, ACTOR, provider or ScriptedProvider(service, task),
            config=ModelConfig(model=MODEL, max_steps=4, max_attempts=1,
                               max_tool_calls=1, max_context_bytes=65536,
                               max_tool_result_bytes=32768),
            allowed_tools=TOOLS, capture_tool_trace=True,
        ).run([{'role': 'system', 'content': PROMPT},
               {'role': 'user', 'content': canonical(task)}])

    def passing(self):
        service, task = self.fixture()
        before = service.lookup(ACTOR)['data_version']
        result = self.execute(service, task)
        checks, answer = evaluate(service, task, result, before)
        self.assertTrue(all(checks.values()), checks)
        return service, task, result, answer, before

    def assert_rejected(self, service, task, result, before):
        checks, _ = evaluate(service, task, result, before)
        self.assertFalse(all(checks.values()), checks)

    def test_normal_four_step_query_matches_independent_answer_without_api(self):
        report = run_example(database=database_for_test(self))
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        self.assertEqual(4, report['runtime']['steps'])
        self.assertEqual([], report['provider_calls'])
        self.assertEqual('test_double', report['execution_mode'])
        self.assertEqual('not_yet_reviewed', report['evaluation']['narrative_review'])
        golden = json.loads((ROOT / 'data/order_fulfillment/b_fulfillment_expected.json').read_text())
        for key in ('states', 'allocated_quantity', 'remaining_quantity',
                    'unallocated_quantity', 'as_of', 'delivery_round_id'):
            self.assertEqual(golden[key], report['answer'][key])
        self.assertFalse(report['answer']['fulfillment_assessed'])
        self.assertTrue(report['answer']['synthetic'])

    def test_guard_stops_observed_wrong_contract_route_before_lookup_without_retry(self):
        providers = []
        class WrongContractProvider(ScriptedProvider):
            def __call__(self, *, model, messages):
                reply = super().__call__(model=model, messages=messages)
                if len(self.messages) == 2:
                    reply['tool_calls'][0]['arguments']['node_id'] = 'evidence:demo:B-01:contract'
                return reply
        def factory(service, task):
            provider = WrongContractProvider(service, task)
            providers.append(provider)
            return provider
        report = run_example(database=database_for_test(self), provider_factory=factory)
        self.assertFalse(report['evaluation']['passed'])
        self.assertFalse(report['runtime']['ok'])
        self.assertEqual(2, len(providers[0].messages))
        self.assertEqual(['fulfillment_status'], [item['name'] for item in report['runtime']['tool_trace']])
        self.assertFalse(report['evaluation']['checks']['provenance_read'])

    def test_guard_preserves_all_unknown_paths_without_api(self):
        for case, steps in [('missing', 2), ('unknown_time', 2), ('coverage_missing', 4)]:
            with self.subTest(case=case):
                report = run_example(database=database_for_test(self), case=case)
                self.assertTrue(report['evaluation']['passed'], report['evaluation'])
                self.assertEqual(steps, report['runtime']['steps'])
                self.assertIsNone(report['answer']['states'])

    def test_evaluator_rejects_full_envelope_and_single_extra_context_evidence(self):
        service, task, result, answer, before = self.passing()
        query = service.fulfillment_status(ACTOR, task['delivery_round_id'], task['as_of'],
                                           version=task['data_version'])
        self.assertEqual(11, len(answer['evidence']))
        self.assertEqual(27, len(query['evidence']))
        extra = next(e for e in query['evidence'] if e not in answer['evidence'])
        for ids in [query['evidence'], answer['evidence'] + [extra], answer['evidence'][1:]]:
            changed = copy.deepcopy(result)
            changed['text'] = canonical({**answer, 'evidence': ids})
            checks, _ = evaluate(service, task, changed, before)
            self.assertFalse(checks['exact_answer_evidence'], checks)
            self.assertFalse(all(checks.values()))

    def test_full_envelope_answer_is_rejected_without_trimming_or_retry(self):
        providers = []
        class EnvelopeProvider(ScriptedProvider):
            def __call__(self, *, model, messages):
                reply = super().__call__(model=model, messages=messages)
                if not reply['tool_calls']:
                    query = json.loads(next(m['content'] for m in messages if m['role'] == 'tool'))
                    answer = json.loads(reply['text'])
                    answer['evidence'] = query['evidence']
                    reply['text'] = canonical(answer)
                return reply
        def factory(service, task):
            provider = EnvelopeProvider(service, task)
            providers.append(provider)
            return provider
        report = run_example(database=database_for_test(self), provider_factory=factory)
        self.assertFalse(report['evaluation']['passed'])
        self.assertFalse(report['runtime']['ok'])
        self.assertEqual(4, len(providers[0].messages))
        self.assertEqual(27, len(json.loads(report['final_submission']['text'])['evidence']))
        self.assertEqual(11, len(report['final_submission']['expected_evidence']))
        self.assertFalse(report['final_submission']['accepted'])

    def test_missing_wrong_time_and_incomplete_coverage_keep_null_counts(self):
        for case in ('missing', 'unknown_time', 'coverage_missing'):
            with self.subTest(case=case):
                service, task = self.fixture(case)
                before = service.lookup(ACTOR)['data_version']
                result = self.execute(service, task)
                checks, answer = evaluate(service, task, result, before)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual('needs_confirmation', answer['status'])
                for key in ('states', 'allocated_quantity', 'remaining_quantity',
                            'unallocated_quantity'):
                    self.assertIsNone(answer[key])
                expected = service.fulfillment_status(
                    ACTOR, task['delivery_round_id'], task['as_of'],
                    version=task['data_version'])['data']
                self.assertEqual(expected['unknowns'], answer['unknowns'])
                self.assertTrue(answer['unknowns'])

    def test_mutated_counts_version_time_and_forecast_are_rejected(self):
        service, task, result, answer, before = self.passing()
        variants = []
        for key, value in [('data_version', before + 1),
                           ('as_of', '2026-10-27T11:00:00+09:00'),
                           ('remaining_quantity', 1000),
                           ('unallocated_quantity', 100),
                           ('fulfillment_assessed', True),
                           ('delivery_round_id', 'delivery-round:another')]:
            changed = copy.deepcopy(answer)
            changed[key] = value
            variants.append((key, changed))
        changed = copy.deepcopy(answer)
        changed['states']['warehouse'] += 100
        variants.append(('other_round_leak', changed))
        for label, changed in variants:
            with self.subTest(field=label):
                modified = copy.deepcopy(result)
                modified['text'] = canonical(changed)
                self.assert_rejected(service, task, modified, before)

    def test_boolean_text_and_extra_fields_are_not_well_typed_answers(self):
        service, task, result, answer, before = self.passing()
        variants = []
        for key, value in [('data_version', True),
                           ('allocated_quantity', '1000'),
                           ('remaining_quantity', True),
                           ('synthetic', 'true'),
                           ('fulfillment_assessed', 0),
                           ('summary', []),
                           ('evidence', [['not-an-id']])]:
            changed = copy.deepcopy(answer)
            changed[key] = value
            variants.append((key, changed))
        changed = copy.deepcopy(answer)
        changed['states']['waiting'] = True
        variants.append(('bool_state', changed))
        changed = copy.deepcopy(answer)
        changed['invented_prediction'] = 960
        variants.append(('extra', changed))
        for label, changed in variants:
            with self.subTest(field=label):
                modified = copy.deepcopy(result)
                modified['text'] = canonical(changed)
                self.assert_rejected(service, task, modified, before)

    def test_malformed_json_and_nonobject_answers_fail_without_evaluator_crash(self):
        service, task, result, _, before = self.passing()
        for text in ('not JSON', '[]', 'null', 'true', '{"states":NaN}'):
            with self.subTest(text=text):
                modified = copy.deepcopy(result)
                modified['text'] = text
                self.assert_rejected(service, task, modified, before)

    def test_invented_or_missing_evidence_cannot_cover_the_answer(self):
        service, task, result, answer, before = self.passing()
        for evidence in ([], ['evidence:invented'], answer['evidence'][:-1]):
            with self.subTest(evidence=evidence):
                modified = copy.deepcopy(result)
                changed = copy.deepcopy(answer)
                changed['evidence'] = evidence
                modified['text'] = canonical(changed)
                self.assert_rejected(service, task, modified, before)

    def test_required_evidence_and_source_reads_are_verified_from_trace(self):
        service, task, result, _, before = self.passing()
        reads = [step for step in result['tool_trace'] if step['name'] == 'lookup']
        self.assertEqual(2, len(reads))
        for omitted in reads:
            with self.subTest(omitted=omitted['arguments']):
                modified = copy.deepcopy(result)
                modified['tool_trace'] = [step for step in result['tool_trace']
                                          if step != omitted]
                self.assert_rejected(service, task, modified, before)

    def test_correct_answer_without_actual_query_call_is_rejected(self):
        service, task, result, _, before = self.passing()
        modified = copy.deepcopy(result)
        modified['tool_trace'] = [step for step in result['tool_trace']
                                  if step['name'] != 'fulfillment_status']
        self.assert_rejected(service, task, modified, before)

    def test_wrong_query_version_or_time_is_rejected_even_with_correct_answer(self):
        service, task, result, _, before = self.passing()
        for key, value in [('version', before - 1),
                           ('as_of', '2026-10-27T11:00:00+09:00'),
                           ('delivery_round_id', 'delivery-round:other')]:
            with self.subTest(field=key):
                modified = copy.deepcopy(result)
                step = next(t for t in modified['tool_trace']
                            if t['name'] == 'fulfillment_status')
                step['arguments'][key] = value
                self.assert_rejected(service, task, modified, before)

    def test_read_only_role_cannot_request_draft_review_or_apply(self):
        service, task = self.fixture()
        before = service.lookup(ACTOR)['data_version']
        drafts = service.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n']
        reviews = service.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n']
        for tool in ('prepare_draft', 'review', 'apply'):
            with self.subTest(tool=tool):
                def hostile(**_):
                    return {'text': '', 'tool_calls': [{'name': tool, 'arguments': {}}]}
                result = self.execute(service, task, hostile)
                self.assertFalse(result['ok'])
                self.assertEqual('TOOL_NOT_ALLOWED', result['error']['code'])
                self.assertEqual([], result['tool_trace'])
        self.assertEqual(before, service.lookup(ACTOR)['data_version'])
        self.assertEqual(drafts, service.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n'])
        self.assertEqual(reviews, service.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n'])
        self.assertEqual(('fulfillment_status', 'lookup'), tuple(TOOLS))
        self.assertEqual(B_FULFILLMENT_TOOLS, TOOLS)

    def test_four_request_budget_stops_a_provider_that_keeps_calling_tools(self):
        service, task = self.fixture()
        calls = []
        def stubborn(**_):
            calls.append(True)
            return {'text': '', 'tool_calls': [{'name': 'fulfillment_status', 'arguments': {
                'delivery_round_id': task['delivery_round_id'], 'as_of': task['as_of'],
                'version': task['data_version']}}]}
        result = self.execute(service, task, stubborn)
        self.assertFalse(result['ok'])
        self.assertEqual('STEP_LIMIT', result['error']['code'])
        self.assertEqual(4, len(calls))
        self.assertEqual(4, len(result['tool_trace']))

    def test_common_service_denies_draft_even_without_runtime_allowlist(self):
        service, _ = self.fixture()
        self.assertEqual('agent', service.principals[ACTOR]['kind'])
        self.assertEqual(['read'], service.principals[ACTOR]['roles'])
        before = service.lookup(ACTOR)['data_version']
        denied = service.prepare_draft(ACTOR, [], request_key='read-only-must-not-draft')
        self.assertFalse(denied['ok'])
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        self.assertEqual(before, service.lookup(ACTOR)['data_version'])

    def test_transient_provider_failure_is_not_retried(self):
        service, task = self.fixture()
        calls = []
        def broken(**_):
            calls.append(True)
            raise ConnectionError('simulated transient failure; no network')
        result = self.execute(service, task, broken)
        self.assertFalse(result['ok'])
        self.assertEqual(1, len(calls))
        self.assertEqual([], result['tool_trace'])

    def test_example_applies_four_steps_one_attempt_and_output_cap_without_network(self):
        # A fake local key plus injected test double never reaches OpenAIProvider.
        settings = OpenAISettings(api_key='test-placeholder-not-a-real-key',
                                  max_output_tokens=4096)
        report = run_example(database=database_for_test(self), settings=settings,
                             provider_factory=lambda service, task: ScriptedProvider(service, task))
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        self.assertEqual('test_double', report['execution_mode'])
        self.assertEqual([], report['provider_calls'])
        self.assertEqual(1024, report['provider_config']['max_output_tokens'])
        self.assertEqual(4, report['runtime_config']['max_steps'])
        self.assertEqual(1, report['runtime_config']['max_attempts'])
        self.assertEqual(1, report['runtime_config']['max_tool_calls'])

    def test_existing_cli_report_blocks_configuration_and_execution_before_api(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'existing.json'
            original = b'{"prior_run":"preserve unchanged"}\n'
            output.write_bytes(original)
            with mock.patch('sys.argv', ['b_fulfillment_agent', '--provider', 'openai',
                                         '--output', str(output)]), \
                 mock.patch('examples.order_fulfillment.b_fulfillment_agent.OpenAISettings.from_environment') as settings, \
                 mock.patch('examples.order_fulfillment.b_fulfillment_agent.environment_values') as environment, \
                 mock.patch('examples.order_fulfillment.b_fulfillment_agent.run_example') as execute:
                with self.assertRaisesRegex(ValueError, 'Preserve the prior report'):
                    main()
                settings.assert_not_called()
                environment.assert_not_called()
                execute.assert_not_called()
            self.assertEqual(original, output.read_bytes())

    def test_missing_provider_usage_is_unknown_cost_instead_of_zero(self):
        def factory(service, task):
            provider = ScriptedProvider(service, task)
            provider.calls = [{'status': 'failed'}]  # Deliberate no-network receipt.
            return provider
        report = run_example(database=database_for_test(self), provider_factory=factory)
        self.assertTrue(report['evaluation']['passed'], report['evaluation'])
        self.assertEqual('test_double', report['execution_mode'])
        self.assertTrue(report['usage_incomplete'])
        self.assertIsNone(report['estimated_standard_usd'])
        self.assertEqual([{'status': 'failed'}], report['provider_calls'])

    def test_duplicate_and_near_match_evidence_ids_are_not_silently_repaired(self):
        service, task, result, answer, before = self.passing()
        near_match = 'evidence:demo:CQ02:allocation-work-P'
        for added in (answer['evidence'][0], near_match):
            with self.subTest(added=added):
                modified = copy.deepcopy(result)
                changed = copy.deepcopy(answer)
                changed['evidence'].append(added)
                modified['text'] = canonical(changed)
                self.assert_rejected(service, task, modified, before)

    def test_contract_provenance_does_not_replace_snapshot_provenance(self):
        service, task, result, _, before = self.passing()
        modified = copy.deepcopy(result)
        reads = [step for step in modified['tool_trace'] if step['name'] == 'lookup']
        self.assertEqual(2, len(reads))
        for step, node_id in zip(reads, ('evidence:demo:B-01:contract',
                                        'source:demo:B-01:contract-v1')):
            step['arguments'] = {'node_id': node_id, 'version': task['data_version']}
            step['result'] = service.lookup(ACTOR, node_id=node_id, version=task['data_version'])
            self.assertTrue(step['result']['ok'])
            self.assertEqual(task['data_version'], step['result']['data_version'])
        checks, _ = evaluate(service, task, modified, before)
        self.assertTrue(checks['structured_answer'])
        self.assertTrue(checks['exact_facts_and_unknowns'])
        self.assertTrue(checks['exact_read_versions'])
        self.assertTrue(checks['evidence_covers_result'])
        self.assertFalse(checks['provenance_read'])
        self.assertFalse(all(checks.values()))


if __name__ == '__main__':
    unittest.main()
