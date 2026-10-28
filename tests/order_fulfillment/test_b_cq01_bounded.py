"""Offline HTTP boundary tests: no database, environment, or paid requests."""
import json
import unittest
from dataclasses import replace

from examples.order_fulfillment import b_agent
from examples.order_fulfillment.b_cq01_bounded import BoundedOpenAIProvider, INTAKE_PROMPT
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings, ProviderError


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body[:limit]


class CQ01BoundedTests(unittest.TestCase):
    def setUp(self):
        self.settings = OpenAISettings(api_key='offline-test-key', model=MODEL,
                                       timeout_seconds=30, max_output_tokens=1024)
        self.task = {'mode': 'intake', 'source_id': 'source:test',
                     'evidence_ids': {'contract': 'e:c', 'item': 'e:i', 'round': 'e:r'},
                     'data_version': 1, 'request_key': 'draft:test', 'source_text': 'raw model input'}
        self.requests = []
        self.outputs = []

    def opener(self, request, *, timeout):
        self.requests.append((json.loads(request.data), timeout))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        items = [{'type': 'function_call', 'name': call['name'], 'arguments': canonical(call['arguments']),
                  'call_id': 'call_' + str(len(self.requests)) + '_' + str(i)} for i, call in enumerate(output)]
        if not items:
            items = [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"status":"test"}'}]}]
        return FakeResponse({'status': 'completed', 'model': MODEL, 'output': items,
                             'usage': {'input_tokens': 1, 'output_tokens': 1}})

    def invoke(self, provider, history, calls, results=()):
        self.outputs.append(calls)
        response = provider(model=MODEL, messages=history)
        history.append({'role': 'assistant', 'content': response['text'], 'tool_calls': response['tool_calls']})
        for i, (call, result) in enumerate(zip(calls, results)):
            history.append({'role': 'tool', 'name': call['name'], 'call_index': i, 'content': canonical(result)})
        return response

    def test_intake_four_requests_preserve_model_operations_and_exact_validation(self):
        provider = BoundedOpenAIProvider(self.settings, self.task, opener=self.opener)
        history = [{'role': 'user', 'content': canonical(self.task)}]
        read_args = [{'node_id': nid, 'version': 1} for nid in ['source:test', 'e:c', 'e:i', 'e:r']]
        read_args += [{'kind': kind, 'version': 1} for kind in ['ProductModel', 'ProductRevision', 'Configuration']]
        reads = [{'name': 'lookup', 'arguments': args} for args in read_args]
        self.invoke(provider, history, reads, [{'ok': True, 'data_version': 1, 'data': []}] * 7)
        operations = [{'op': 'put', 'node': {'id': 'model-authored'}}]
        prepare = [{'name': 'prepare_draft', 'arguments': {'operations': operations, 'request_key': 'draft:test'}}]
        response = self.invoke(provider, history, prepare,
                               [{'ok': True, 'data': {'draft_id': 'returned-draft', 'draft_version': 3}}])
        self.assertEqual(response['tool_calls'][0]['arguments']['operations'], operations)
        self.invoke(provider, history, [{'name': 'validate_draft', 'arguments': {'draft_id': 'returned-draft', 'version': 3}}],
                    [{'ok': True, 'data': {'validation': {'valid': True}}}])
        self.invoke(provider, history, [])
        for payload, timeout in self.requests:
            self.assertEqual(payload['model'], MODEL)
            self.assertEqual(payload['max_output_tokens'], 1024)
            self.assertTrue(payload['parallel_tool_calls'])
            self.assertFalse(payload['store'])
            self.assertEqual(timeout, 30)
        self.assertEqual([p['tool_choice'] for p, _ in self.requests], [
            'required', {'type': 'function', 'name': 'prepare_draft'},
            {'type': 'function', 'name': 'validate_draft'}, 'none'])
        self.assertNotIn('text', self.requests[0][0])
        self.assertEqual(self.requests[-1][0]['text'], {'format': {'type': 'json_object'}})
        with self.assertRaises(ProviderError) as raised:
            provider(model=MODEL, messages=history)
        self.assertEqual(raised.exception.code, 'LIVE_BUDGET_EXHAUSTED')
        self.assertEqual(len(self.requests), 4)

    def test_promise_batches_all_top_level_evidence_and_deduplicates_sources(self):
        task = {'mode': 'promise', 'data_version': 7, 'delivery_round_id': 'round:test'}
        provider = BoundedOpenAIProvider(self.settings, task, opener=self.opener)
        history = [{'role': 'user', 'content': canonical(task)}]
        self.invoke(provider, history, [{'name': 'delivery_round', 'arguments': {'delivery_round_id': 'round:test', 'version': 7}}],
                    [{'ok': True, 'data_version': 7, 'evidence': ['e:contract', 'e:design']}])
        reads = [{'name': 'lookup', 'arguments': {'node_id': eid, 'version': 7}} for eid in ['e:contract', 'e:design']]
        self.invoke(provider, history, reads, [{'ok': True, 'data_version': 7,
                    'data': [{'id': eid, 'type': 'Evidence', 'source_id': 'shared-source'}]} for eid in ['e:contract', 'e:design']])
        self.invoke(provider, history, [{'name': 'lookup', 'arguments': {'node_id': 'shared-source', 'version': 7}}],
                    [{'ok': True, 'data_version': 7, 'data': [{'id': 'shared-source', 'type': 'SourceDocument'}]}])
        self.invoke(provider, history, [])
        self.assertEqual(len(self.requests), 4)
        self.assertEqual([p['tool_choice'] for p, _ in self.requests], [
            {'type': 'function', 'name': 'delivery_round'}, 'required',
            {'type': 'function', 'name': 'lookup'}, 'none'])

    def test_invalid_validation_stops_before_fourth_http(self):
        provider = BoundedOpenAIProvider(self.settings, self.task, opener=self.opener)
        history = [{'role': 'user', 'content': canonical(self.task)}]
        read_args = [{'node_id': nid, 'version': 1} for nid in ['source:test', 'e:c', 'e:i', 'e:r']]
        read_args += [{'kind': kind, 'version': 1} for kind in ['ProductModel', 'ProductRevision', 'Configuration']]
        self.invoke(provider, history, [{'name': 'lookup', 'arguments': args} for args in read_args],
                    [{'ok': True, 'data_version': 1, 'data': []}] * 7)
        self.invoke(provider, history, [{'name': 'prepare_draft', 'arguments': {
            'operations': [{'op': 'put', 'node': {'id': 'model-authored'}}], 'request_key': 'draft:test'}}],
            [{'ok': True, 'data': {'draft_id': 'returned-draft', 'draft_version': 3}}])
        self.invoke(provider, history, [{'name': 'validate_draft', 'arguments': {
            'draft_id': 'returned-draft', 'version': 3}}],
            [{'ok': True, 'data': {'validation': {'valid': False}}}])
        with self.assertRaises(ProviderError) as raised:
            provider(model=MODEL, messages=history)
        self.assertEqual(raised.exception.code, 'CQ01_BOUNDED_PHASE')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(provider.calls), 3)

    def test_timeout_consumes_attempt_and_fifth_never_sends(self):
        provider = BoundedOpenAIProvider(self.settings, self.task, opener=self.opener)
        for _ in range(4):
            self.outputs.append(TimeoutError('offline timeout'))
            with self.assertRaises(TimeoutError):
                provider(model=MODEL, messages=[])
        self.assertEqual(len(provider.calls), 4)
        self.assertTrue(all(call['status'] == 'failed' for call in provider.calls))
        with self.assertRaises(ProviderError):
            provider(model=MODEL, messages=[])
        self.assertEqual(len(self.requests), 4)

    def test_rejects_wrong_phase_before_tool_execution(self):
        provider = BoundedOpenAIProvider(self.settings, self.task, opener=self.opener)
        self.outputs.append([{'name': 'lookup', 'arguments': {'node_id': 'wrong', 'version': 1}}])
        with self.assertRaises(ProviderError) as raised:
            provider(model=MODEL, messages=[])
        self.assertEqual(raised.exception.code, 'CQ01_BOUNDED_PHASE')
        self.assertEqual(len(provider.calls), 1)

    def test_batch_wire_allows_multiple_calls_but_rejects_partial_response(self):
        provider = BoundedOpenAIProvider(self.settings, self.task, opener=self.opener)
        args = [{'node_id': n, 'version': 1} for n in ['source:test', 'e:c', 'e:i', 'e:r']]
        args += [{'kind': k, 'version': 1} for k in ['ProductModel', 'ProductRevision', 'Configuration']]
        reads = [{'name': 'lookup', 'arguments': a} for a in args]

        def documented_tool_choice(request, *, timeout):
            payload = json.loads(request.data)
            self.outputs.append(reads if payload['tool_choice'] == 'required' else reads[:1])
            return self.opener(request, timeout=timeout)

        provider._open = documented_tool_choice
        response = provider(model=MODEL, messages=[])
        self.assertEqual(response['tool_calls'], reads)
        self.assertEqual(self.requests[0][0]['tool_choice'], 'required')
        self.assertEqual([t['name'] for t in self.requests[0][0]['tools']], ['lookup'])
        # The original real partial response must still fail; never auto-fill it.
        partial = BoundedOpenAIProvider(self.settings, self.task, opener=self.opener)
        self.outputs.append(reads[:1])
        with self.assertRaises(ProviderError) as raised:
            partial(model=MODEL, messages=[])
        self.assertEqual(raised.exception.code, 'CQ01_BOUNDED_PHASE')
        self.assertEqual(len(partial.calls), 1)

    def test_explicit_configuration_and_existing_defaults(self):
        for settings in [replace(self.settings, max_output_tokens=2048), replace(self.settings, timeout_seconds=31)]:
            with self.assertRaises(ProviderError):
                BoundedOpenAIProvider(settings, self.task, opener=self.opener)
        self.assertIn('한 번에 하나씩', b_agent.INTAKE_PROMPT)
        self.assertNotIn('한 번에 하나씩', INTAKE_PROMPT)
        original = b_agent.OpenAIProvider(self.settings, b_agent.B_INTAKE_TOOLS, opener=self.opener)
        self.assertFalse(original.parallel_tool_calls)
        self.assertEqual(original._tool_choice(), 'auto')


if __name__ == '__main__':
    unittest.main()
