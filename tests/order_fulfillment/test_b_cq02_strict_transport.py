"""CQ02 strict wire constraints and fail-closed response guards; no network or DB."""
from copy import deepcopy
import json
import unittest

from examples.order_fulfillment.b_fulfillment_agent import PROMPT, TOOLS, ProvenanceProvider
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAIProvider, OpenAISettings, ProviderError
from ontologics.order_fulfillment.tool_specs import function_tools
from tests.test_openai_provider import Transport, response, tool, text


class CQ02StrictTransportTests(unittest.TestCase):
    task = dict(delivery_round_id='round:strict', as_of='2026-11-01T10:00:00+09:00', data_version=7)

    def history(self):
        return [dict(ok=True, data_version=7, evidence=['evidence:coverage:a', 'evidence:coverage:b', 'evidence:item'],
                     data=dict(snapshot=dict(evidence=['evidence:coverage:a', 'evidence:coverage:b']),
                               items=[dict(evidence=['evidence:item'])])),
                dict(ok=True, data_version=7, data=[dict(id='evidence:coverage:b', type='Evidence', source_id='source:coverage')]),
                dict(ok=True, data_version=7, data=[dict(id='source:coverage', type='SourceDocument')])]

    def provider(self, outputs):
        transport = Transport([response(output) for output in outputs])
        provider = OpenAIProvider(OpenAISettings(api_key='offline-strict-only', max_output_tokens=1024,
                                               timeout_seconds=30), TOOLS, opener=transport)
        return ProvenanceProvider(provider, self.task), provider, transport

    def test_four_phase_wire_enforces_full_enum_schema_and_preserves_history(self):
        baseline = deepcopy(function_tools(TOOLS))
        query = dict(delivery_round_id=self.task['delivery_round_id'], as_of=self.task['as_of'], version=7)
        answer = canonical(dict(evidence=['evidence:coverage:a', 'evidence:coverage:b', 'evidence:item']))
        outputs = [[tool('fulfillment_status', query, cid='strict_query')],
                   [tool('lookup', dict(node_id='evidence:coverage:b', version=7), cid='strict_evidence')],
                   [tool('lookup', dict(node_id='source:coverage', version=7), cid='strict_source')], [text(answer)]]
        guard, provider, transport = self.provider(outputs)
        messages = [dict(role='system', content=PROMPT), dict(role='user', content=canonical(self.task))]
        results = self.history()
        for step in range(4):
            prior = deepcopy(messages)
            reply = guard(model=MODEL, messages=messages)
            self.assertEqual(prior, messages)
            payload = json.loads(transport.requests[-1][0].data)
            self.assertEqual(1024, payload['max_output_tokens'])
            self.assertEqual(30, transport.requests[-1][1])
            self.assertFalse(payload['parallel_tool_calls'])
            if step < 3:
                spec, = payload['tools']
                self.assertTrue(spec['strict'], 'CQ02 enum constraints must be enforced at the API boundary')
                schema = spec['parameters']
                self.assertFalse(schema['additionalProperties'])
                self.assertEqual(set(schema['properties']), set(schema['required']))
                self.assertEqual([7], schema['properties']['version']['enum'])
                if step == 1:
                    self.assertEqual(['evidence:coverage:a', 'evidence:coverage:b'], schema['properties']['node_id']['enum'])
                if step == 2:
                    self.assertEqual(['source:coverage'], schema['properties']['node_id']['enum'])
                messages += [dict(role='assistant', content=reply['text'], tool_calls=reply['tool_calls']),
                             dict(role='tool', name=reply['tool_calls'][0]['name'], call_index=0, content=canonical(results[step]))]
            else:
                self.assertEqual([], payload['tools'])
                self.assertEqual(answer, reply['text'])
                self.assertTrue(guard.final_submission['accepted'])
                ids = [item['call_id'] for item in payload['input'] if item.get('type') == 'function_call_output']
                self.assertEqual(['strict_query', 'strict_evidence', 'strict_source'], ids)
        self.assertEqual(baseline, function_tools(TOOLS))
        self.assertEqual(4, len(provider.calls))
        self.assertNotIn('offline-strict-only', canonical(provider.public_config()))

    def test_wrong_evidence_is_still_rejected_without_retry_even_with_strict_wire(self):
        query = dict(delivery_round_id=self.task['delivery_round_id'], as_of=self.task['as_of'], version=7)
        guard, provider, transport = self.provider([
            [tool('fulfillment_status', query, cid='strict_query')],
            [tool('lookup', dict(node_id='evidence:demo:B-01:contract', version=7), cid='wrong_contract')]])
        messages = [dict(role='user', content=canonical(self.task))]
        first = guard(model=MODEL, messages=messages)
        messages += [dict(role='assistant', content=first['text'], tool_calls=first['tool_calls']),
                     dict(role='tool', name='fulfillment_status', call_index=0, content=canonical(self.history()[0]))]
        with self.assertRaises(ProviderError) as caught:
            guard(model=MODEL, messages=messages)
        self.assertEqual('CQ02_PROVENANCE_STEP', caught.exception.code)
        self.assertEqual(2, len(provider.calls))
        self.assertEqual(2, len(transport.requests))
        self.assertIsNone(guard.final_submission)


if __name__ == '__main__':
    unittest.main()
