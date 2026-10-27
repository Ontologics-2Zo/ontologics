"""CQ04 original answers, read-only provenance, and paid-request limits (mock HTTP only)."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from examples.order_fulfillment import b_delivery_agent as cq
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.order_fulfillment.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings, ProviderError
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test
from tests.test_openai_provider import Transport, response, text, tool


READER = UserIdentity('test-cq04-reader', frozenset({'read'}))


class DeliveryAgentTests(unittest.TestCase):
    def setup_case(self, case='normal'):
        database = database_for_test(self)
        service, task = cq.setup(database, case=case)
        self.addCleanup(service.close)
        return database, service, task

    def run_agent(self, service, task, provider):
        guarded = cq.ProvenanceProvider(provider, task)
        result = ModelRuntime(service, cq.ACTOR, guarded, config=cq.CONFIG,
            allowed_tools=cq.TOOLS, capture_tool_trace=True).run([
                dict(role='system', content=cq.PROMPT), dict(role='user', content=canonical(task))])
        return result, guarded

    def test_normal_exact_quantity_time_conditions_and_unmodified_original(self):
        _, service, task = self.setup_case()
        result, guarded = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        checks, answer = cq.evaluate(service, task, result, task['data_version'])
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(4, result['steps'])
        self.assertEqual(3, len(result['tool_trace']))
        self.assertEqual(8, len(answer['evidence']))
        self.assertEqual('shortfall', answer['deadline_status'])
        self.assertEqual((960, 40), (answer['on_time_quantity'], answer['shortfall_quantity']))
        self.assertEqual('2026-10-27T15:26:40+09:00', answer['estimated_full_arrival_at'])
        self.assertEqual(dict(preparation_seconds=0, transport_seconds=3600,
                              dispatch_mode='split_available'), answer['condition'])
        self.assertIs(False, answer['actual_arrival_assessed'])
        self.assertEqual('conditional_arrival_forecast', answer['assessment_scope'])
        self.assertEqual(result['text'], guarded.final_submission['text'])
        self.assertTrue(guarded.final_submission['accepted'])

    def test_mixed_case_and_earlier_deadline_keep_independent_expected_totals(self):
        # 100 delivered + 150 transit + 250 warehouse + production (500 or 470).
        for case, on_time, shortfall in (('mixed', 1000, 0), ('mixed_due14', 970, 30)):
            with self.subTest(case=case):
                _, service, task = self.setup_case(case)
                result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
                checks, answer = cq.evaluate(service, task, result, task['data_version'])
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual((on_time, shortfall),
                                 (answer['on_time_quantity'], answer['shortfall_quantity']))
                self.assertEqual('2026-10-27T14:20:00+09:00', answer['estimated_full_arrival_at'])
                self.assertEqual(13, len(answer['evidence']))
                self.assertEqual(3, len(result['tool_trace']))
                self.assertEqual(4, result['steps'])

    def test_all_at_once_cannot_promote_partial_ready_quantity_to_delivery(self):
        _, service, task = self.setup_case('all_at_once')
        result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        checks, answer = cq.evaluate(service, task, result, task['data_version'])
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual((0, 1000), (answer['on_time_quantity'], answer['shortfall_quantity']))
        self.assertEqual('all_at_once', answer['condition']['dispatch_mode'])
        self.assertEqual('2026-10-27T15:26:40+09:00', answer['estimated_full_arrival_at'])

    def test_incomplete_inputs_preserve_unknown_totals_instead_of_zero(self):
        for case in ('missing_condition', 'missing_latest', 'zero_rate', 'coverage_missing', 'elapsed_eta'):
            with self.subTest(case=case):
                _, service, task = self.setup_case(case)
                result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
                checks, answer = cq.evaluate(service, task, result, task['data_version'])
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual('needs_confirmation', answer['deadline_status'])
                self.assertTrue(answer['unknowns'])
                for field in ('on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at'):
                    self.assertIsNone(answer[field])
                self.assertEqual(4, result['steps'])

    def test_evaluator_rejects_invented_facts_bool_counts_missing_conditions_and_assumptions(self):
        _, service, task = self.setup_case()
        result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        original = json.loads(result['text'])
        mutations = (('on_time_quantity', 1000), ('shortfall_quantity', 0), ('quantity', True),
            ('allocated_quantity', True), ('data_version', True), ('actual_arrival_assessed', True),
            ('estimated_full_arrival_at', '2026-10-27T14:26:40+09:00'),
            ('condition', None), ('assumption_codes', []), ('unknowns', [{'invented': True}]),
            ('assessment_scope', 'guaranteed_delivery'), ('synthetic', False))
        for field, value in mutations:
            with self.subTest(field=field):
                candidate = {**original, field: value}
                raw = canonical(candidate)
                checks, _ = cq.evaluate(service, task, {**result, 'text': raw}, task['data_version'])
                self.assertFalse(all(checks.values()))
                self.assertEqual(raw, canonical(candidate))

    def test_unknown_totals_replaced_with_zero_fail_evaluation(self):
        _, service, task = self.setup_case('missing_condition')
        result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        candidate = json.loads(result['text'])
        candidate.update(on_time_quantity=0, shortfall_quantity=1000)
        checks, _ = cq.evaluate(service, task, {**result, 'text': canonical(candidate)}, task['data_version'])
        self.assertFalse(all(checks.values()))

    def test_evidence_policy_is_independent_of_helper_and_rejects_extra_missing_duplicate(self):
        _, service, task = self.setup_case()
        result, _ = self.run_agent(service, task, cq.ScriptedProvider(service, task))
        answer = json.loads(result['text'])
        for ids in (answer['evidence'][:-1], answer['evidence'] + answer['evidence'][:1],
                    answer['evidence'] + ['evidence:olimex:product-family']):
            with self.subTest(ids=ids):
                with patch.object(cq, 'answer_evidence', return_value=ids):
                    checks, _ = cq.evaluate(service, task,
                        {**result, 'text': canonical({**answer, 'evidence': ids})}, task['data_version'])
                self.assertFalse(checks['exact_answer_evidence'])

    def test_round_evidence_is_required_for_claimed_promise_quantity_and_due(self):
        _, service, task = self.setup_case()
        forecast = cq.query(service, task)
        round_evidence = set(forecast['data']['promise']['delivery_round']['evidence'])
        self.assertTrue(round_evidence)
        self.assertTrue(round_evidence <= set(cq.answer_evidence(forecast)))
        self.assertNotEqual(set(forecast['evidence']), set(cq.answer_evidence(forecast)))

    def test_guard_rejects_wrong_args_ids_version_identity_and_write_before_execution(self):
        _, service, task = self.setup_case()
        correct = dict(name='delivery_forecast', arguments=dict(delivery_round_id=task['delivery_round_id'],
            as_of=task['as_of'], version=task['data_version']))
        wrong_version = deepcopy(correct); wrong_version['arguments']['version'] += 1
        wrong_round = deepcopy(correct); wrong_round['arguments']['delivery_round_id'] = 'delivery-round:other'
        wrong_time = deepcopy(correct); wrong_time['arguments']['as_of'] = '2026-10-27T11:00:00+09:00'
        spoof = deepcopy(correct); spoof['arguments']['actor'] = 'seed-admin'
        for calls in ([], [correct, correct], [wrong_version], [wrong_round], [wrong_time],
                      [spoof], [dict(name='prepare_draft', arguments={})]):
            with self.subTest(calls=calls):
                result, _ = self.run_agent(service, task, lambda **_: dict(text='invented', tool_calls=calls))
                self.assertFalse(result['ok'])
                self.assertEqual('CQ04_PROVENANCE_STEP', result['error']['code'])
                self.assertEqual([], result['tool_trace'])

    def test_incomplete_extra_duplicate_or_wrong_version_array_executes_no_partial_reads(self):
        _, service, task = self.setup_case('mixed')
        for corruption in ('missing', 'extra', 'duplicate', 'wrong_version', 'wrong_kind',
                           'scalar_ids', 'identity_spoof'):
            scripted = cq.ScriptedProvider(service, task)
            def corrupt(**kw):
                value = scripted(**kw)
                calls = value['tool_calls']
                if calls and calls[0]['name'] == 'lookup_many':
                    ids = calls[0]['arguments']['node_ids']
                    if corruption == 'missing': ids.pop()
                    elif corruption == 'extra':
                        ids.append('evidence:olimex:product-family')
                    elif corruption == 'duplicate': ids[-1] = ids[0]
                    elif corruption == 'wrong_version': calls[0]['arguments']['version'] += 1
                    elif corruption == 'wrong_kind': calls[0]['arguments']['kind'] = 'SourceDocument'
                    elif corruption == 'scalar_ids': calls[0]['arguments']['node_ids'] = ids[0]
                    else: calls[0]['arguments']['actor'] = 'seed-admin'
                return value
            with self.subTest(corruption=corruption):
                result, _ = self.run_agent(service, task, corrupt)
                self.assertFalse(result['ok'])
                self.assertEqual('CQ04_PROVENANCE_STEP', result['error']['code'])
                self.assertEqual(['delivery_forecast'], [t['name'] for t in result['tool_trace']])

    def test_atomic_lookup_array_order_can_change_without_changing_evidence_or_steps(self):
        _, service, task = self.setup_case('mixed')
        scripted = cq.ScriptedProvider(service, task)
        def reverse_ids(**kw):
            value = scripted(**kw)
            if value['tool_calls'] and value['tool_calls'][0]['name'] == 'lookup_many':
                value['tool_calls'][0]['arguments']['node_ids'].reverse()
            return value
        result, _ = self.run_agent(service, task, reverse_ids)
        checks, answer = cq.evaluate(service, task, result, task['data_version'])
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(4, result['steps'])
        self.assertEqual(3, len(result['tool_trace']))
        self.assertEqual(13, len(answer['evidence']))

    def test_truncated_and_wrong_citation_final_answers_are_preserved_and_not_retried(self):
        _, service, task = self.setup_case()
        for malformed in (True, False):
            scripted = cq.ScriptedProvider(service, task)
            originals = []
            def alter_final(**kw):
                value = scripted(**kw)
                if not value['tool_calls']:
                    if malformed: value['text'] = '{"status":"shortfall"'
                    else:
                        answer = json.loads(value['text']); answer['evidence'].append('fabricated')
                        value['text'] = canonical(answer)
                    originals.append(value['text'])
                return value
            with self.subTest(malformed=malformed):
                result, guard = self.run_agent(service, task, alter_final)
                self.assertFalse(result['ok'])
                self.assertEqual('CQ04_ANSWER_EVIDENCE', result['error']['code'])
                self.assertEqual(originals[0], guard.final_submission['text'])
                self.assertFalse(guard.final_submission['accepted'])
                self.assertEqual(4, len(scripted.messages))

    def test_source_byte_hash_mismatch_is_rejected(self):
        _, service, task = self.setup_case()
        expected = cq.query(service, task)
        with patch.object(cq.hashlib, 'sha256') as sha:
            sha.return_value.hexdigest.return_value = '0' * 64
            with self.assertRaisesRegex(ValueError, 'hash chain'):
                cq.resolve_provenance(service, expected)

    def test_common_host_uses_request_reader_and_preserves_confirmed_graph(self):
        database, fixture, task = self.setup_case('mixed')
        identities = DevelopmentIdentityProvider([READER], enabled=True)
        host = CommonHost(identities, database, agent_registrations=REGISTRATIONS)
        audit = Service(database, principals={READER.actor: READER.principal()})
        self.addCleanup(audit.close)
        before = audit.lookup(READER.actor, version=task['data_version'])['data']
        scripted = cq.ScriptedProvider(audit, task, actor=READER.actor)
        guarded = cq.ProvenanceProvider(scripted, task)
        with host.request(DevelopmentContext(READER.subject)) as request:
            result = request.run_agent('b_delivery', guarded, [dict(role='user', content=canonical(task))],
                                       config=cq.CONFIG, capture_tool_trace=True)
        checks, _ = cq.evaluate(audit, task, result, task['data_version'], actor=READER.actor)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(before, audit.lookup(READER.actor, version=task['data_version'])['data'])
        row = audit.db.execute('SELECT actor,config FROM runtime_runs WHERE id=%s', (result['run_id'],)).fetchone()
        self.assertEqual(READER.actor, row['actor'])
        self.assertEqual({'delivery_forecast', 'lookup_many'}, set(json.loads(row['config'])['allowed_tools']))

    def test_common_host_nonreader_fails_before_any_model_or_tool_request(self):
        database, _, task = self.setup_case()
        user = UserIdentity('test-cq04-nonreader', frozenset({'draft'}))
        host = CommonHost(DevelopmentIdentityProvider([user], enabled=True), database,
                          agent_registrations=REGISTRATIONS)
        calls = []
        def provider(**kw):
            calls.append(kw)
            return dict(text='should never run', tool_calls=[])
        with host.request(DevelopmentContext(user.subject)) as request:
            result = request.run_agent('b_delivery', provider, [dict(role='user', content=canonical(task))],
                                       config=cq.CONFIG, capture_tool_trace=True)
        self.assertFalse(result['ok'])
        self.assertEqual([], calls)
        self.assertEqual([], result['tool_trace'])

    def test_premature_final_in_source_phase_is_rejected_without_source_reads_or_retry(self):
        _, fixture, task = self.setup_case()
        expected = cq.query(fixture, task)
        ids = cq.answer_evidence(expected)
        raw = canonical(cq.scripted_answer(expected))
        transport = Transport([
            response([tool('delivery_forecast', dict(delivery_round_id=task['delivery_round_id'],
                as_of=task['as_of'], version=task['data_version']), cid='query')]),
            response([tool('lookup_many', dict(node_ids=ids, kind='Evidence',
                version=task['data_version']), cid='evidence_batch')]),
            response([text(raw)])])
        provider = cq.BoundedOpenAIProvider(OpenAISettings('mock-no-real-key'), opener=transport)
        result, guard = self.run_agent(fixture, task, provider)
        self.assertFalse(result['ok'])
        self.assertEqual('CQ04_PROVENANCE_STEP', result['error']['code'])
        self.assertEqual(3, result['steps'])
        self.assertEqual(3, len(transport.requests))
        self.assertIsNone(guard.final_submission)
        self.assertEqual(['delivery_forecast', 'lookup_many'],
                         [entry['name'] for entry in result['tool_trace']])
        self.assertEqual(ids, result['tool_trace'][1]['arguments']['node_ids'])
        source_request = json.loads(transport.requests[-1][0].data)
        self.assertEqual('required', source_request['tool_choice'])
        self.assertTrue(all(node_id.startswith('source:') for node_id in
            source_request['tools'][0]['parameters']['properties']['node_ids']['items']['enum']))

    def test_mock_http_mixed_exact_batches_strict_enums_and_original_call_id_replay(self):
        database, fixture, task = self.setup_case('mixed')
        expected = cq.query(fixture, task)
        ids = cq.answer_evidence(expected)
        sources = sorted({fixture.lookup(cq.ACTOR, node_id=e, version=task['data_version'])['data'][0]['source_id']
                          for e in ids})
        payloads = [response([tool('delivery_forecast', dict(delivery_round_id=task['delivery_round_id'],
                        as_of=task['as_of'], version=task['data_version']), cid='query')]),
            response([tool('lookup_many', dict(node_ids=ids, kind='Evidence',
                version=task['data_version']), cid='evidence_batch')]),
            response([tool('lookup_many', dict(node_ids=sources, kind='SourceDocument',
                version=task['data_version']), cid='source_batch')]),
            response([text(canonical(cq.scripted_answer(expected)))])]
        transport = Transport(payloads)
        provider = cq.BoundedOpenAIProvider(OpenAISettings('mock-no-real-key'), opener=transport)
        host = CommonHost(DevelopmentIdentityProvider([READER], enabled=True), database,
                          agent_registrations=REGISTRATIONS)
        guard = cq.ProvenanceProvider(provider, task)
        with host.request(DevelopmentContext(READER.subject)) as request:
            result = request.run_agent('b_delivery', guard, [dict(role='user', content=canonical(task))],
                                       config=cq.CONFIG, capture_tool_trace=True)
        audit = Service(database, principals={READER.actor: READER.principal()})
        self.addCleanup(audit.close)
        checks, _ = cq.evaluate(audit, task, result, task['data_version'], actor=READER.actor)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(4, len(transport.requests))
        self.assertEqual(13, len(ids))
        self.assertEqual(['required', 'required', 'required', 'none'],
                         [json.loads(req.data)['tool_choice'] for req, _ in transport.requests])
        for req, _ in transport.requests:
            payload = json.loads(req.data)
            self.assertEqual(1024, payload['max_output_tokens'])
            self.assertFalse(payload['parallel_tool_calls'])
            for spec in payload['tools']:
                self.assertTrue(spec['strict'])
                params = spec['parameters']
                self.assertFalse(params['additionalProperties'])
                self.assertEqual(set(params['properties']), set(params['required']))
                for field, prop in params['properties'].items():
                    if field == 'node_ids':
                        self.assertIn('enum', prop['items'])
                        self.assertEqual(prop['minItems'], prop['maxItems'])
                        self.assertEqual(len(prop['items']['enum']), prop['minItems'])
                    else:
                        self.assertIn('enum', prop)
        evidence_payload = json.loads(transport.requests[1][0].data)
        self.assertIn('총 도구 호출 수=1', evidence_payload['input'][-1]['content'])
        self.assertEqual(set(ids), set(evidence_payload['tools'][0]['parameters']['properties']['node_ids']['items']['enum']))
        replay = json.loads(transport.requests[2][0].data)['input']
        self.assertEqual({'query', 'evidence_batch'},
                         {item['call_id'] for item in replay if item.get('type') == 'function_call_output'})
        final_replay = json.loads(transport.requests[3][0].data)['input']
        self.assertEqual({'query', 'evidence_batch', 'source_batch'},
                         {item['call_id'] for item in final_replay if item.get('type') == 'function_call_output'})
        self.assertEqual([], json.loads(transport.requests[-1][0].data)['tools'])


class DeliveryBudgetTests(unittest.TestCase):
    def test_fifth_http_attempt_blocked_before_transport_and_output_limit_forced(self):
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
        self.assertEqual(4, cq.CONFIG.max_steps)

    def test_transport_failures_consume_same_four_attempt_budget_without_auto_retry(self):
        transport = Transport([URLError('offline test')] * 4)
        provider = cq.BoundedOpenAIProvider(OpenAISettings('mock-only'), opener=transport)
        for _ in range(4):
            with self.assertRaises(ConnectionError):
                provider(model=MODEL, messages=[dict(role='user', content='failure')])
        with self.assertRaises(ProviderError):
            provider(model=MODEL, messages=[dict(role='user', content='fifth')])
        self.assertEqual(4, len(transport.requests))
        self.assertTrue(all(c['status'] == 'failed' for c in provider.calls))


if __name__ == '__main__':
    unittest.main()
