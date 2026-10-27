"""CQ03 original-answer, provenance, permission and HTTP budget gates (no paid API)."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from examples.order_fulfillment import b_production_agent as cq
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.order_fulfillment.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings, ProviderError
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test
from tests.test_openai_provider import Transport, response, text, tool


READER = UserIdentity('test-cq03-reader', frozenset({'read'}))


class ProductionAgentTests(unittest.TestCase):
    def setup_case(self, case='normal'):
        database = database_for_test(self)
        service, task = cq.setup(database, case=case)
        self.addCleanup(service.close)
        return database, service, task

    def run_agent(self, service, task, provider):
        from ontologics.order_fulfillment.runtime import ModelRuntime
        guard = cq.ProvenanceProvider(provider, task)
        result = ModelRuntime(service, cq.ACTOR, guard, config=cq.CONFIG,
            allowed_tools=cq.TOOLS, capture_tool_trace=True).run([
                {'role': 'system', 'content': cq.PROMPT}, {'role': 'user', 'content': canonical(task)}])
        return result, guard

    def test_normal_scripted_exact_facts_citations_and_no_writes(self):
        _, service, task = self.setup_case()
        result, guard = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        checks, answer = cq.evaluate(service, task, result, task['data_version'])
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(4, result['steps'])
        self.assertEqual(8, len(result['tool_trace']))
        self.assertEqual(6, len(answer['evidence']))
        self.assertEqual('2026-10-27T14:26:40+09:00', answer['estimated_completion_at'])
        self.assertEqual(90, answer['good_rate_per_hour'])
        self.assertEqual(16000, answer['remaining_seconds'])
        self.assertIsNone(answer['actual_completion_at'])
        self.assertEqual(result['text'], guard.final_submission['text'])
        self.assertTrue(guard.final_submission['accepted'])

    def test_exception_variants_preserve_unknowns_and_target_observed(self):
        for case in ('missing_latest', 'zero_rate', 'target_observed'):
            with self.subTest(case=case):
                _, service, task = self.setup_case(case)
                result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
                checks, answer = cq.evaluate(service, task, result, task['data_version'])
                self.assertTrue(all(checks.values()), checks)
                self.assertIsNone(answer['estimated_completion_at'])
                self.assertIsNone(answer['actual_completion_at'])
                if case == 'target_observed':
                    self.assertEqual('target_observed', answer['status'])
                    self.assertEqual(0, answer['remaining_seconds'])
                    self.assertEqual(task['as_of'], answer['target_observed_at'])
                else:
                    self.assertEqual('needs_confirmation', answer['status'])
                    self.assertTrue(answer['unknowns'])
                    self.assertIsNone(answer['remaining_seconds'])

    def test_evaluator_rejects_fabricated_facts_boolean_numbers_and_missing_conditions(self):
        _, service, task = self.setup_case()
        result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        original = json.loads(result['text'])
        for field, bad in (('remaining_quantity', 401), ('target_quantity', True),
                           ('actual_completion_at', task['as_of']), ('fulfillment_assessed', True),
                           ('data_version', True), ('assumption_codes', []),
                           ('selected_observation_ids', []), ('recent_defect_ratio', 0),
                           ('estimated_completion_at', '2026-10-27T14:00:00+09:00')):
            with self.subTest(field=field):
                answer = deepcopy(original); answer[field] = bad
                changed = {**result, 'text': canonical(answer)}
                checks, _ = cq.evaluate(service, task, changed, task['data_version'])
                self.assertFalse(all(checks.values()))
                self.assertEqual(canonical(answer), changed['text'])

    def test_evaluator_citation_policy_is_independent_and_rejects_extra_missing_duplicate(self):
        _, service, task = self.setup_case()
        result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        answer = json.loads(result['text'])
        variants = [answer['evidence'][:-1], answer['evidence'] + [answer['evidence'][0]],
                    answer['evidence'] + ['evidence:demo:B-01:contract']]
        for ids in variants:
            with self.subTest(ids=ids):
                bad = {**result, 'text': canonical({**answer, 'evidence': ids})}
                # Helper corruption cannot make a matching wrong answer pass the evaluator.
                with patch.object(cq, 'answer_evidence', return_value=ids):
                    checks, _ = cq.evaluate(service, task, bad, task['data_version'])
                self.assertFalse(checks['exact_answer_evidence'])

    def test_guard_rejects_extra_duplicate_missing_wrong_version_or_write_before_execution(self):
        _, service, task = self.setup_case()
        correct = dict(name='production_completion', arguments=dict(production_work_id=task['production_work_id'],
            delivery_round_id=task['delivery_round_id'], as_of=task['as_of'], version=task['data_version']))
        wrong = deepcopy(correct); wrong['arguments']['version'] += 1
        spoof = deepcopy(correct); spoof['arguments']['actor'] = 'seed-admin'
        for calls in ([], [correct, correct], [wrong], [spoof], [dict(name='prepare_draft', arguments={})]):
            with self.subTest(calls=calls):
                result, _ = self.run_agent(service, task, lambda **_: dict(text='invented', tool_calls=calls))
                self.assertFalse(result['ok'])
                self.assertEqual('CQ03_PROVENANCE_STEP', result['error']['code'])
                self.assertEqual([], result['tool_trace'])

    def test_missing_evidence_batch_is_not_silently_repaired(self):
        _, service, task = self.setup_case()
        scripted = cq.ScriptedProvider(service, task)
        def omit(**kw):
            result = scripted(**kw)
            if result['tool_calls'] and result['tool_calls'][0]['name'] == 'lookup':
                result['tool_calls'].pop()
            return result
        result, _ = self.run_agent(service, task, omit)
        self.assertFalse(result['ok'])
        self.assertEqual('CQ03_PROVENANCE_STEP', result['error']['code'])
        self.assertEqual(['production_completion'], [t['name'] for t in result['tool_trace']])

    def test_observed_eleven_lookup_failure_is_blocked_before_any_batch_read(self):
        _, service, task = self.setup_case()
        scripted = cq.ScriptedProvider(service, task)
        extras = ['evidence:demo:B-01:contract', 'evidence:demo:B-01:item',
                  'evidence:demo:B-01:round', 'evidence:olimex-L-wroom:identity',
                  'evidence:olimex:product-family']
        def replay(**kw):
            result = scripted(**kw)
            if result['tool_calls'] and result['tool_calls'][0]['name'] == 'lookup':
                result['tool_calls'].extend(dict(name='lookup', arguments=dict(node_id=e, version=task['data_version'])) for e in extras)
            return result
        result, _ = self.run_agent(service, task, replay)
        self.assertFalse(result['ok'])
        self.assertEqual('CQ03_PROVENANCE_STEP', result['error']['code'])
        self.assertEqual(['production_completion'], [t['name'] for t in result['tool_trace']])
        self.assertEqual(2, len(scripted.messages))

    def test_observed_duplicate_complete_batches_are_rejected_without_partial_reads(self):
        _, service, task = self.setup_case()
        scripted = cq.ScriptedProvider(service, task)
        def duplicate(**kw):
            result = scripted(**kw)
            if result['tool_calls'] and result['tool_calls'][0]['name'] == 'lookup':
                result['tool_calls'] *= 2
            return result
        result, _ = self.run_agent(service, task, duplicate)
        self.assertFalse(result['ok'])
        self.assertEqual('CQ03_PROVENANCE_STEP', result['error']['code'])
        self.assertEqual(['production_completion'], [t['name'] for t in result['tool_trace']])

    def test_truncated_final_json_is_preserved_and_rejected(self):
        _, service, task = self.setup_case()
        scripted = cq.ScriptedProvider(service, task)
        def truncate(**kw):
            result = scripted(**kw)
            if not result['tool_calls']:
                result['text'] = '{"status":"forecast"'
            return result
        result, guard = self.run_agent(service, task, truncate)
        self.assertFalse(result['ok'])
        self.assertEqual('CQ03_ANSWER_EVIDENCE', result['error']['code'])
        self.assertEqual('{"status":"forecast"', guard.final_submission['text'])

    def test_final_evidence_failure_preserves_original_submission_without_retry(self):
        _, service, task = self.setup_case()
        scripted = cq.ScriptedProvider(service, task)
        raw = []
        def bad_final(**kw):
            result = scripted(**kw)
            if not result['tool_calls']:
                answer = json.loads(result['text']); answer['evidence'].append('fabricated')
                result['text'] = canonical(answer); raw.append(result['text'])
            return result
        result, guard = self.run_agent(service, task, bad_final)
        self.assertFalse(result['ok'])
        self.assertEqual('CQ03_ANSWER_EVIDENCE', result['error']['code'])
        self.assertEqual(raw[0], guard.final_submission['text'])
        self.assertFalse(guard.final_submission['accepted'])
        self.assertEqual(4, len(scripted.messages))

    def test_local_source_hash_failure_is_not_accepted(self):
        _, service, task = self.setup_case()
        result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        response = cq.query(service, task)
        with patch.object(cq.hashlib, 'sha256') as sha:
            sha.return_value.hexdigest.return_value = '0' * 64
            with self.assertRaisesRegex(ValueError, 'hash chain'):
                cq.resolve_provenance(service, response)

    def test_exact_http_four_round_call_id_replay_through_common_host(self):
        database, fixture, task = self.setup_case()
        expected = cq.query(fixture, task)
        ids = cq.answer_evidence(expected)
        source_ids = sorted({fixture.lookup(cq.ACTOR, node_id=eid, version=task['data_version'])['data'][0]['source_id'] for eid in ids})
        payloads = [response([tool('production_completion', dict(production_work_id=task['production_work_id'],
            delivery_round_id=task['delivery_round_id'], as_of=task['as_of'], version=task['data_version']), cid='query')]),
            response([tool('lookup', dict(node_id=eid, version=task['data_version']), cid='evidence_' + str(i)) for i, eid in enumerate(ids)]),
            response([tool('lookup', dict(node_id=sid, version=task['data_version']), cid='source_' + str(i)) for i, sid in enumerate(source_ids)]),
            response([text(canonical(cq.scripted_answer(expected)))])]
        transport = Transport(payloads)
        provider = cq.BoundedOpenAIProvider(OpenAISettings('mock-no-real-key'), opener=transport)
        identities = DevelopmentIdentityProvider([READER], enabled=True)
        host = CommonHost(identities, database, agent_registrations=REGISTRATIONS)
        guard = cq.ProvenanceProvider(provider, task)
        with host.request(DevelopmentContext(READER.subject)) as request:
            result = request.run_agent('b_production', guard, [{'role': 'user', 'content': canonical(task)}],
                config=cq.CONFIG, capture_tool_trace=True)
        audit = Service(database, principals={READER.actor: READER.principal()})
        self.addCleanup(audit.close)
        checks, _ = cq.evaluate(audit, task, result, task['data_version'], actor=READER.actor)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(4, len(transport.requests))
        for req, _ in transport.requests:
            payload = json.loads(req.data)
            self.assertEqual(1024, payload['max_output_tokens'])
            self.assertTrue(payload['parallel_tool_calls'])
            for schema in payload['tools']:
                self.assertTrue(schema['strict'])
                params = schema['parameters']
                self.assertFalse(params['additionalProperties'])
                self.assertEqual(set(params['properties']), set(params['required']))
                self.assertTrue(all('enum' in prop for prop in params['properties'].values()))
        evidence_payload = json.loads(transport.requests[1][0].data)
        self.assertIn('총 도구 호출 수=6', evidence_payload['input'][-1]['content'])
        self.assertEqual('system', evidence_payload['input'][-1]['role'])
        evidence_schema = evidence_payload['tools'][0]
        self.assertEqual(set(ids), set(evidence_schema['parameters']['properties']['node_id']['enum']))
        from ontologics.order_fulfillment.tool_specs import function_tools
        self.assertFalse(function_tools(('lookup',))[0]['strict'])
        replay = json.loads(transport.requests[2][0].data)['input']
        self.assertEqual({'query'} | {'evidence_' + str(i) for i in range(6)},
            {x['call_id'] for x in replay if x.get('type') == 'function_call_output'})
        self.assertEqual([], json.loads(transport.requests[-1][0].data)['tools'])
        self.assertEqual(READER.actor, audit.db.execute('SELECT actor FROM runtime_runs WHERE id=%s', (result['run_id'],)).fetchone()['actor'])


class ProductionBudgetTests(unittest.TestCase):
    def test_fifth_http_attempt_is_blocked_before_transport_and_tokens_are_forced(self):
        transport = Transport([response([text()])] * 4)
        provider = cq.BoundedOpenAIProvider(OpenAISettings('mock-only', max_output_tokens=8192), opener=transport)
        for _ in range(4):
            provider(model=MODEL, messages=[dict(role='user', content='budget')])
        with self.assertRaises(ProviderError) as caught:
            provider(model=MODEL, messages=[dict(role='user', content='forbidden fifth')])
        self.assertEqual('LIVE_BUDGET_EXHAUSTED', caught.exception.code)
        self.assertEqual(4, len(transport.requests))
        self.assertTrue(all(json.loads(req.data)['max_output_tokens'] == 1024 for req, _ in transport.requests))
        self.assertEqual(1, cq.CONFIG.max_attempts)

    def test_failed_transports_consume_budget_without_retry(self):
        transport = Transport([URLError('offline test')] * 4)
        provider = cq.BoundedOpenAIProvider(OpenAISettings('mock-only'), opener=transport)
        for _ in range(4):
            with self.assertRaises(ConnectionError):
                provider(model=MODEL, messages=[dict(role='user', content='failure')])
        with self.assertRaises(ProviderError):
            provider(model=MODEL, messages=[dict(role='user', content='fifth')])
        self.assertEqual(4, len(transport.requests))
        self.assertTrue(all(c['status'] == 'failed' for c in provider.calls))
