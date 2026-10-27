"""Free HTTP doubles exercise CQ05's real host/provider replay boundary."""
import json
import unittest
from copy import deepcopy

from examples.order_fulfillment import b_response_agent as response_agent
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.order_fulfillment.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings, ProviderError
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test
from tests.test_openai_provider import SECRET, Transport, response, text, tool


READER = UserIdentity('test-response-http-reader', frozenset({'read'}))


class ResponseProviderTests(unittest.TestCase):
    def assert_final_format(self, payload):
        format_spec = payload['text']['format']
        self.assertEqual('json_schema', format_spec['type'])
        self.assertIs(True, format_spec['strict'])
        schema = format_spec['schema']
        self.assertEqual('object', schema['type'])
        self.assertIs(False, schema['additionalProperties'])
        self.assertEqual(response_agent.ANSWER_FIELDS, set(schema['properties']))
        self.assertEqual(response_agent.ANSWER_FIELDS, set(schema['required']))
        self.assertEqual(19, len(schema['required']))
        self.assertNotIn('version', schema['properties'])
        properties = schema['properties']
        alternatives = properties['condition']['anyOf']
        self.assertEqual({'object', 'null'}, {alternative['type'] for alternative in alternatives})
        condition = next(alternative for alternative in alternatives if alternative['type'] == 'object')
        self.assertIs(False, condition['additionalProperties'])
        condition_keys = {'preparation_seconds', 'transport_seconds', 'dispatch_mode'}
        self.assertEqual(condition_keys, set(condition['properties']))
        self.assertEqual(condition_keys, set(condition['required']))
        quantities = properties['dispatch_quantities']
        self.assertEqual('object', quantities['type'])
        self.assertIs(False, quantities['additionalProperties'])
        self.assertEqual(set(response_agent.QUANTITY_FIELDS), set(quantities['properties']))
        self.assertEqual(set(response_agent.QUANTITY_FIELDS), set(quantities['required']))
        for prop in quantities['properties'].values():
            self.assertEqual({'integer', 'null'}, set(prop['type']))
        for key in ('on_time_quantity', 'shortfall_quantity'):
            self.assertEqual({'integer', 'null'}, set(properties[key]['type']))
        for key in ('estimated_full_arrival_at', 'snapshot_id'):
            self.assertEqual({'string', 'null'}, set(properties[key]['type']))
        self.assertEqual('integer', properties['data_version']['type'])
        for key in ('draft_only', 'review_required', 'execution_performed'):
            self.assertEqual('boolean', properties[key]['type'])

    def fixture(self, case='normal'):
        database = database_for_test(self)
        fixture, task = response_agent.setup(database, case=case)
        try:
            plan = response_agent.query(fixture, task)
            evidence_ids = response_agent.answer_evidence(plan)
            evidence = fixture.lookup_many(response_agent.ACTOR, node_ids=evidence_ids,
                kind='Evidence', version=task['data_version'])
            source_ids = sorted({node['source_id'] for node in evidence['data']})
        finally:
            fixture.close()
        audit = Service(database, principals={READER.actor: READER.principal()})
        self.addCleanup(audit.close)
        host = CommonHost(DevelopmentIdentityProvider([READER], enabled=True), database,
                          agent_registrations=REGISTRATIONS)
        return task, plan, evidence_ids, source_ids, audit, host

    def run_host(self, host, task, provider):
        guarded = response_agent.ProvenanceProvider(provider, task)
        with host.request(DevelopmentContext(READER.subject)) as session:
            result = session.run_agent('b_response', guarded,
                [{'role': 'user', 'content': canonical(task)}],
                config=response_agent.CONFIG, capture_tool_trace=True)
        return result, guarded

    def test_host_http_replays_original_calls_and_reasoning_with_compact_observations(self):
        for case, expected_count in [('normal', 8), ('mixed', 13)]:
            with self.subTest(case=case):
                task, plan, evidence_ids, source_ids, audit, host = self.fixture(case)
                version = task['data_version']
                calls = [
                    tool('response_plan', dict(delivery_round_id=task['delivery_round_id'],
                         as_of=task['as_of'], version=version), 'cq05_query_original'),
                    tool('lookup_many', dict(node_ids=evidence_ids, kind='Evidence', version=version),
                         'cq05_evidence_original'),
                    tool('lookup_many', dict(node_ids=source_ids, kind='SourceDocument', version=version),
                         'cq05_sources_original'),
                ]
                reasoning = [dict(type='reasoning', id='rs_cq05_' + str(index),
                                  encrypted_content='opaque_cq05_' + str(index), summary=[])
                             for index in range(3)]
                final_text = canonical(response_agent.scripted_answer(plan))
                transport = Transport([response([reasoning[index], call])
                                       for index, call in enumerate(calls)] + [response([text(final_text)])])
                provider = response_agent.BoundedOpenAIProvider(OpenAISettings(SECRET), opener=transport)
                result, guarded = self.run_host(host, task, provider)
                self.assertTrue(result['ok'], result['error'])
                checks, _answer = response_agent.evaluate(audit, task, result, version, actor=READER.actor)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual(final_text, result['text'])
                self.assertEqual(final_text, guarded.final_submission['text'])
                self.assertTrue(guarded.final_submission['accepted'])
                self.assertEqual(expected_count, len(evidence_ids))
                self.assertEqual(4, len(provider.calls))
                self.assertEqual(4, len(transport.requests))
                self.assertEqual(4, result['steps'])
                trace = result['tool_trace']
                self.assertEqual(['response_plan', 'lookup_many', 'lookup_many'], [t['name'] for t in trace])
                full_plan = trace[0]['result']
                untouched = deepcopy(full_plan)
                compact = response_agent.compact_response(full_plan)
                self.assertIn('promise', full_plan['data']['forecast'])
                self.assertNotIn('promise', compact['data']['forecast'])
                self.assertLess(len(canonical(compact).encode()), len(canonical(full_plan).encode()))
                self.assertEqual(untouched, full_plan)
                self.assertEqual(response_agent.scripted_answer(plan),
                                 response_agent.scripted_answer(full_plan))
                for index, (request, timeout) in enumerate(transport.requests):
                    payload = json.loads(request.data)
                    self.assertEqual(1024, payload['max_output_tokens'])
                    self.assertIs(False, payload['parallel_tool_calls'])
                    self.assertIs(False, payload['store'])
                    self.assertEqual({'effort': 'none'}, payload['reasoning'])
                    self.assertEqual(['reasoning.encrypted_content'], payload['include'])
                    self.assertEqual(30, timeout)
                    self.assertEqual('required' if index < 3 else 'none', payload['tool_choice'])
                    if index < 3:
                        self.assertNotIn('text', payload)
                        self.assertEqual(1, len(payload['tools']))
                        spec = payload['tools'][0]
                        self.assertTrue(spec['strict'])
                        self.assertEqual(calls[index]['name'], spec['name'])
                        parameters = spec['parameters']
                        self.assertIs(False, parameters['additionalProperties'])
                        self.assertEqual(set(parameters['properties']), set(parameters['required']))
                        arguments = json.loads(calls[index]['arguments'])
                        for key, value in arguments.items():
                            prop = parameters['properties'][key]
                            if key == 'node_ids':
                                self.assertEqual(sorted(value), prop['items']['enum'])
                                self.assertEqual(len(value), prop['minItems'])
                                self.assertEqual(len(value), prop['maxItems'])
                            else:
                                self.assertEqual([value], prop['enum'])
                    else:
                        self.assertEqual([], payload['tools'])
                        self.assert_final_format(payload)
                    replay_calls = [item for item in payload['input'] if item.get('type') == 'function_call']
                    replay_reasoning = [item for item in payload['input'] if item.get('type') == 'reasoning']
                    outputs = [item for item in payload['input'] if item.get('type') == 'function_call_output']
                    self.assertEqual(calls[:index], replay_calls)
                    self.assertEqual(reasoning[:index], replay_reasoning)
                    self.assertEqual([call['call_id'] for call in calls[:index]],
                                     [item['call_id'] for item in outputs])
                    if outputs:
                        self.assertEqual(compact, json.loads(outputs[0]['output']))
                with self.assertRaises(ProviderError) as caught:
                    provider(model=MODEL, messages=[])
                self.assertEqual('LIVE_BUDGET_EXHAUSTED', caught.exception.code)
                self.assertEqual(4, len(transport.requests))
                self.assertEqual(version, audit.lookup(READER.actor)['data_version'])
                self.assertEqual(0, audit.db.execute('SELECT COUNT(*) AS n FROM drafts WHERE author=%s',
                    (READER.actor,)).fetchone()['n'])
                self.assertNotIn(SECRET, canonical(provider.public_config()) + canonical(provider.calls))

    def test_schema_request_does_not_repair_or_accept_an_extra_final_key(self):
        task, plan, evidence_ids, source_ids, audit, host = self.fixture('mixed')
        version = task['data_version']
        malformed = response_agent.scripted_answer(plan)
        malformed['version'] = version
        raw_final = canonical(malformed)
        transport = Transport([
            response([tool('response_plan', dict(delivery_round_id=task['delivery_round_id'],
                as_of=task['as_of'], version=version), 'extra_query')]),
            response([tool('lookup_many', dict(node_ids=evidence_ids, kind='Evidence',
                version=version), 'extra_evidence')]),
            response([tool('lookup_many', dict(node_ids=source_ids, kind='SourceDocument',
                version=version), 'extra_source')]),
            response([text(raw_final)]),
        ])
        provider = response_agent.BoundedOpenAIProvider(OpenAISettings(SECRET), opener=transport)
        result, guarded = self.run_host(host, task, provider)
        # A dishonest/malformed HTTP response can ignore the requested schema.
        # The raw candidate must survive for the independent evaluator to reject.
        self.assertTrue(result['ok'], result['error'])
        self.assertEqual(raw_final, result['text'])
        self.assertEqual(raw_final, guarded.final_submission['text'])
        self.assertEqual(20, len(json.loads(result['text'])))
        self.assertEqual(version, json.loads(result['text'])['version'])
        checks, answer = response_agent.evaluate(audit, task, result, version, actor=READER.actor)
        self.assertFalse(checks['structured_answer'])
        self.assertFalse(all(checks.values()))
        self.assertEqual(malformed, answer)
        self.assertEqual(4, len(transport.requests))
        self.assert_final_format(json.loads(transport.requests[-1][0].data))
        self.assertEqual(version, audit.lookup(READER.actor)['data_version'])

    def test_host_timeout_is_one_recorded_attempt_without_automatic_retry_or_tool_execution(self):
        task, _plan, _evidence, _sources, audit, host = self.fixture()
        transport = Transport([TimeoutError()])
        provider = response_agent.BoundedOpenAIProvider(OpenAISettings(SECRET), opener=transport)
        result, guarded = self.run_host(host, task, provider)
        self.assertFalse(result['ok'])
        self.assertEqual(1, len(provider.calls))
        self.assertEqual(1, len(transport.requests))
        self.assertEqual('failed', provider.calls[0]['status'])
        self.assertEqual([], result['tool_trace'])
        self.assertIsNone(guarded.final_submission)
        self.assertEqual(task['data_version'], audit.lookup(READER.actor)['data_version'])

    def test_host_incomplete_http_output_never_releases_a_partial_tool_call(self):
        task, _plan, _evidence, _sources, audit, host = self.fixture()
        transport = Transport([response([tool('response_plan', dict(
            delivery_round_id=task['delivery_round_id'], as_of=task['as_of'],
            version=task['data_version']), 'cq05_partial')], status='incomplete')])
        provider = response_agent.BoundedOpenAIProvider(OpenAISettings(SECRET), opener=transport)
        result, _guarded = self.run_host(host, task, provider)
        self.assertFalse(result['ok'])
        self.assertEqual('OPENAI_RESPONSE_INCOMPLETE', result['error']['code'])
        self.assertEqual(1, len(transport.requests))
        self.assertEqual([], result['tool_trace'])
        self.assertEqual({'input_tokens': 4, 'output_tokens': 3, 'total_tokens': 7}, provider.calls[0]['usage'])
        self.assertEqual(task['data_version'], audit.lookup(READER.actor)['data_version'])


if __name__ == '__main__':
    unittest.main()
