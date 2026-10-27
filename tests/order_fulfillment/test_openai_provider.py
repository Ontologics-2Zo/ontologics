"""B provider extensions; shared transport fixtures never perform HTTP calls."""
import hashlib
import io
import json
import unittest
from urllib.error import HTTPError, URLError

from ontologics.openai_provider import OpenAIProvider as CommonOpenAIProvider
from ontologics.order_fulfillment.openai_provider import (
    ENDPOINT, MODEL, OpenAIProvider, OpenAISettings, ProviderError,
)
from ontologics.order_fulfillment.tool_specs import B_PROMISE_TOOLS
from tests.test_openai_provider import SECRET, Transport, response, text, tool


class ProviderTests(unittest.TestCase):
    def provider(self, items, tools=('lookup',), **kwargs):
        transport = Transport(items)
        return OpenAIProvider(OpenAISettings(SECRET), tools, opener=transport, **kwargs), transport

    def test_parallel_tools_default_and_explicit_false_preserve_request_and_metadata(self):
        for kwargs in ({}, {'parallel_tool_calls': False}):
            with self.subTest(kwargs=kwargs):
                provider, transport = self.provider([response([text()])], **kwargs)
                provider(model=MODEL, messages=[{'role': 'user', 'content': 'hi'}])
                payload = json.loads(transport.requests[0][0].data)
                self.assertIs(False, payload['parallel_tool_calls'])
                self.assertEqual('auto', payload['tool_choice'])
                self.assertNotIn('text', payload)
                self.assertIs(False, provider.public_config()['parallel_tool_calls'])
                self.assertIs(False, provider.settings.public_config()['parallel_tool_calls'])

    def test_parallel_tools_opt_in_preserves_multiple_calls_and_reports_actual_setting(self):
        output = [tool(args={'node_id': 'evidence:first'}, cid='call_first'),
                  tool(args={'node_id': 'evidence:second'}, cid='call_second')]
        provider, transport = self.provider([response(output), response([text('final')])],
                                            parallel_tool_calls=True)
        messages = [{'role': 'user', 'content': 'Read both evidence locators.'}]
        first = provider(model=MODEL, messages=messages)
        self.assertEqual(['evidence:first', 'evidence:second'],
                         [call['arguments']['node_id'] for call in first['tool_calls']])
        messages += [{'role': 'assistant', 'content': first['text'], 'tool_calls': first['tool_calls']},
                     {'role': 'tool', 'name': 'lookup', 'call_index': 0, 'content': '{"ok":true}'},
                     {'role': 'tool', 'name': 'lookup', 'call_index': 1, 'content': '{"ok":true}'}]
        provider(model=MODEL, messages=messages)
        for req, _ in transport.requests:
            self.assertIs(True, json.loads(req.data)['parallel_tool_calls'])
        replay = json.loads(transport.requests[1][0].data)['input']
        self.assertEqual(output, replay[1:3])
        self.assertEqual(['call_first', 'call_second'], [item['call_id'] for item in replay[3:]])
        self.assertIs(True, provider.public_config()['parallel_tool_calls'])
        self.assertIs(False, provider.settings.public_config()['parallel_tool_calls'])
        self.assertNotIn(SECRET, json.dumps(provider.public_config()))

    def test_parallel_tools_requires_a_real_boolean_before_any_transport_request(self):
        for invalid in (0, 1, 0.0, 1.0, None, 'true', [], {}):
            with self.subTest(invalid=invalid):
                transport = Transport([])
                with self.assertRaises(ProviderError) as caught:
                    OpenAIProvider(OpenAISettings(SECRET), ('lookup',), opener=transport,
                                   parallel_tool_calls=invalid)
                self.assertEqual('OPENAI_CONFIG_INVALID', caught.exception.code)
                self.assertEqual([], transport.requests)

    def test_rejected_response_retains_usage_without_releasing_partial_tools(self):
        for body, code in ((response([tool()], status='incomplete'), 'OPENAI_RESPONSE_INCOMPLETE'),
                           (response([tool(name='apply')]), 'TOOL_NOT_ALLOWED')):
            with self.subTest(code=code):
                provider, transport = self.provider([body])
                with self.assertRaises(ProviderError) as caught:
                    provider(model=MODEL, messages=[])
                self.assertEqual(code, caught.exception.code)
                self.assertEqual(1, len(transport.requests))
                self.assertEqual('failed', provider.calls[0]['status'])
                self.assertEqual(body['usage'], provider.calls[0]['usage'])
                self.assertEqual('resp_test', provider.calls[0]['response_id'])
                self.assertEqual({}, provider._outputs)
                self.assertNotIn(SECRET, json.dumps(provider.calls))

    def test_usage_metadata_excludes_negative_boolean_or_text_counts(self):
        provider, _ = self.provider([response([text()], usage={
            'input_tokens': -1, 'output_tokens': True, 'total_tokens': '7', 'unknown': SECRET})])
        provider(model=MODEL, messages=[])
        self.assertEqual({}, provider.calls[0]['usage'])
        self.assertNotIn(SECRET, json.dumps(provider.calls))

    def test_role_hooks_and_actual_transmitted_request_hash(self):
        class FinalProvider(OpenAIProvider):
            def _tool_choice(self):
                return 'none'

            def _text_format(self):
                return {'type': 'json_schema', 'name': 'b_answer', 'strict': True,
                        'schema': {'type': 'object', 'properties': {}, 'required': [],
                                   'additionalProperties': False}}

        transport = Transport([response([text('{}')])])
        provider = FinalProvider(OpenAISettings(SECRET), B_PROMISE_TOOLS, opener=transport,
                                 parallel_tool_calls=True)
        provider(model=MODEL, messages=[{'role': 'user', 'content': 'question'}])
        req, timeout = transport.requests[0]
        payload = json.loads(req.data)
        self.assertEqual(ENDPOINT, req.full_url)
        self.assertEqual(30, timeout)
        self.assertEqual('none', payload['tool_choice'])
        self.assertEqual(provider._text_format(), payload['text']['format'])
        self.assertEqual(set(B_PROMISE_TOOLS), {item['name'] for item in payload['tools']})
        self.assertEqual(hashlib.sha256(req.data).hexdigest(), provider.calls[0]['request_hash'])
        self.assertFalse(payload['store'])
        self.assertEqual({'effort': 'none'}, payload['reasoning'])

    def test_shared_replay_keeps_reasoning_and_original_call_ids(self):
        output = [{'type': 'reasoning', 'id': 'rs_a', 'encrypted_content': 'encrypted', 'summary': []},
                  tool(name='delivery_round', args={'delivery_round_id': 'round:test', 'version': 1},
                       cid='original_b_call')]
        provider, transport = self.provider([response(output), response([text('final')])], B_PROMISE_TOOLS)
        initial = [{'role': 'user', 'content': 'question'}]
        first = provider(model=MODEL, messages=initial)
        history = initial + [{'role': 'assistant', 'content': first['text'], 'tool_calls': first['tool_calls']},
                             {'role': 'tool', 'name': 'delivery_round', 'call_index': 0, 'content': '{"ok":true}'}]
        provider(model=MODEL, messages=history)
        replay = json.loads(transport.requests[1][0].data)['input']
        self.assertEqual(output, replay[1:3])
        self.assertEqual('original_b_call', replay[3]['call_id'])
        history[-1]['call_index'] = 1
        with self.assertRaises(ProviderError) as caught:
            provider(model=MODEL, messages=history)
        self.assertEqual('OPENAI_HISTORY_INVALID', caught.exception.code)
        self.assertEqual(2, len(transport.requests))

    def test_common_provider_does_not_gain_b_tools_or_options(self):
        with self.assertRaises(ValueError):
            CommonOpenAIProvider(OpenAISettings(SECRET), B_PROMISE_TOOLS, opener=Transport([]))
        with self.assertRaises(TypeError):
            CommonOpenAIProvider(OpenAISettings(SECRET), ('lookup',), opener=Transport([]),
                                 parallel_tool_calls=True)
        self.assertIs(OpenAIProvider._input, CommonOpenAIProvider._input)
        self.assertIs(OpenAIProvider._normalize, CommonOpenAIProvider._normalize)

    def test_http_and_network_errors_stay_redacted_without_retry(self):
        cases = [(HTTPError(ENDPOINT, 401, SECRET, {}, io.BytesIO(SECRET.encode())), ProviderError),
                 (HTTPError(ENDPOINT, 429, SECRET, {}, io.BytesIO(SECRET.encode())), ConnectionError),
                 (URLError(SECRET), ConnectionError), (TimeoutError(SECRET), TimeoutError)]
        for error, kind in cases:
            with self.subTest(kind=kind):
                provider, transport = self.provider([error])
                with self.assertRaises(kind) as caught:
                    provider(model=MODEL, messages=[])
                self.assertEqual(1, len(transport.requests))
                self.assertEqual('failed', provider.calls[0]['status'])
                self.assertNotIn(SECRET, str(caught.exception) + json.dumps(provider.calls))


if __name__ == '__main__':
    unittest.main()
