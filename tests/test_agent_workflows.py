"""Natural-language workflow boundaries over real HTTP/PG; scripted models only."""
import base64
import copy
import json
from unittest.mock import patch

from ontologics.demo import CONFIG_L1, example_queries
from ontologics.identity import DevelopmentContext, UserIdentity
from ontologics.model import ROOT
from tests.test_assistant import ScriptedScreen
from tests.test_web import WebCase


class ScriptedReceipt:
    """Follow actual receipt/save/validation inputs; optionally fail or drift."""
    def __init__(self, *, fail_after_save=False, attack=None):
        self.calls = []
        self.fail_after_save, self.attack = fail_after_save, attack

    def public_config(self):
        return {'provider': 'scripted-receipt-workflow'}

    def __call__(self, *, model, messages):
        self.calls.append({'mode': 'scripted-receipt-workflow'})
        task = json.loads(next(m['content'] for m in messages if m['role'] == 'user'))
        outputs = [m for m in messages if m['role'] == 'tool']
        if self.fail_after_save and len(outputs) >= 2:
            raise RuntimeError('Injected explanation failure after successful draft save')
        if not outputs:
            call = copy.deepcopy(task['receipt_call'])
            if self.attack == 'approval':
                call = {'name': 'review', 'arguments': {}}
        elif len(outputs) == 1:
            call = copy.deepcopy(task['draft_call'])
            if self.attack == 'request_key':
                call['arguments']['request_key'] += '-model-changed'
            elif self.attack == 'receipt':
                call['arguments']['receipt_id'] = 'receipt:' + 'f' * 64
            elif self.attack == 'invented_operations':
                call['arguments']['operations'] = []
        elif len(outputs) == 2:
            draft = json.loads(outputs[-1]['content'])['data']
            call = {'name': 'validate_draft', 'arguments': {'draft_id': draft['draft_id'], 'version': draft['draft_version']}}
            if self.attack == 'draft_version':
                call['arguments']['version'] += 1
        else:
            return {'text': '초안 검증 완료. 승인이나 확정 반영을 수행하지 않았습니다.', 'tool_calls': []}
        return {'text': '', 'tool_calls': [call]}


class ScriptedConditions:
    def __init__(self, proposal):
        self.proposal = proposal
        self.calls = []

    def public_config(self):
        return {'provider': 'scripted-condition-extraction'}

    def __call__(self, *, model, messages):
        self.calls.append({'mode': 'scripted-condition-extraction'})
        if len(self.calls) == 1:
            task = json.loads(messages[-1]['content'])
            return {'text': '', 'tool_calls': task['supplied_calls']}
        return {'text': json.dumps(self.proposal), 'tool_calls': []}


class AgentWorkflowTests(WebCase):
    reserve_l1 = True

    def upload_l1(self, server=None):
        path = ROOT / 'data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.ods'
        return self.ok(self.http(server or self.alice, '/api/sources/intake',
            {'filename': path.name, 'content_base64': base64.b64encode(path.read_bytes()).decode()}))

    def ask_draft(self, receipt, key='agent-receipt', *, server=None, context=None):
        return self.http(server or self.alice, '/api/assistant', {
            'screen': 'product', 'version': receipt['data_version'],
            'context': {'receipt_id': receipt['data']['receipt_id'], **(context or {})},
            'question': '접수한 원본 근거와 미확인 상태를 유지해서 등록 초안을 저장해줘.',
            'action': 'prepare_draft', 'request_key': key})

    def ask_quality(self, question):
        return self.http(self.alice, '/api/assistant', {'screen': 'investigation', 'version': 1,
            'context': {}, 'question': question})

    def test_saved_draft_readback_failure_withholds_typed_result_and_retries_same_draft(self):
        from ontologics.host import RequestSession
        from ontologics.web import failure
        receipt = self.upload_l1()
        self.alice.app.provider_factory = lambda names: ScriptedReceipt()
        original = RequestSession.call
        def readback_failed(request, tool, arguments):
            if tool == 'get_draft':
                return failure('STORAGE_ERROR', 'Controlled readback failure')
            return original(request, tool, arguments)
        with patch.object(RequestSession, 'call', readback_failed):
            failed = self.failed(self.ask_draft(receipt, 'readback-retry'), 502, 'AI_DRAFT_INCOMPLETE')['data']
        typed = failed['agent_result']
        self.assertEqual('draft_saved_model_failed', failed['action'])
        self.assertIsNone(typed['payload'])
        self.assertFalse(typed['runtime']['ok'])
        self.assertEqual(typed['runtime'], failed['runtime'])
        self.assertEqual('completed', typed['runtime']['execution_status'])
        self.assertEqual('AI_DRAFT_INCOMPLETE', typed['error']['code'])
        retried = self.ok(self.ask_draft(receipt, 'readback-retry'))['data']
        self.assertEqual('draft_prepared', retried['action'])
        self.assertEqual(failed['draft']['draft_id'], retried['draft']['draft_id'])
        self.assertEqual(1, retried['draft']['draft_version'])

    def test_source_draft_failure_recovers_one_version_then_revision_requires_exact_human_approval(self):
        receipt = self.upload_l1()
        self.assertEqual(12, receipt['data']['unknown_count'])
        self.alice.app.provider_factory = lambda names: ScriptedReceipt(fail_after_save=True)
        partial = self.failed(self.ask_draft(receipt), 502, 'MODEL_ERROR')['data']
        self.assertEqual('draft_saved_model_failed', partial['action'])
        self.assertEqual('product', partial['agent_result']['agent_name'])
        self.assertIsNone(partial['agent_result']['payload'])
        self.assertEqual('MODEL_ERROR', partial['agent_result']['error']['code'])
        self.assertEqual('', partial['runtime']['text'])
        first = partial['draft']
        self.assertEqual('awaiting_review', partial['draft_record']['status'])
        self.assertEqual('user:dev-alice', partial['draft_record']['author'])
        self.assertEqual(receipt['data']['operations'], partial['draft_record']['operations'])
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])
        # Retry a completed write even though the prior model turn failed.
        self.alice.app.provider_factory = lambda names: ScriptedReceipt()
        recovered = self.ok(self.ask_draft(receipt))['data']
        self.assertEqual('draft_prepared', recovered['action'])
        self.assertEqual(first['draft_id'], recovered['agent_result']['payload']['draft_id'])
        self.assertEqual(receipt['data']['receipt_id'], recovered['agent_result']['payload']['source_receipt_id'])
        self.assertEqual('unapproved_candidate_or_draft', recovered['agent_result']['payload']['provenance_scope'])
        self.assertEqual([], recovered['agent_result']['evidence_refs'])
        self.assertIsNone(recovered['agent_result']['error'])
        self.assertEqual('matched', recovered['consistency'])
        self.assertEqual('', recovered['runtime']['text'])
        self.assertTrue(recovered['runtime']['output_withheld'])
        self.assertIn('초안 v1', recovered['display_text'])
        with self.host.request(DevelopmentContext('dev-alice')) as request:
            stored = request._service.db.execute('SELECT output_hash FROM runtime_runs WHERE id=%s',
                (recovered['runtime']['run_id'],)).fetchone()
        self.assertEqual(stored['output_hash'], recovered['runtime']['output_hash'])
        self.assertEqual(first['draft_id'], recovered['draft']['draft_id'])
        self.assertEqual(1, recovered['draft']['draft_version'])
        self.assertEqual(['get_source_receipt', 'prepare_source_draft', 'validate_draft'],
                         [call['name'] for call in recovered['tool_calls']])
        direct = self.ok(self.http(self.alice, '/api/sources/draft', {
            'receipt_id': receipt['data']['receipt_id'], 'request_key': 'agent-receipt'}))
        self.assertEqual(first['draft_id'], direct['data']['draft_id'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in direct['warnings']])
        self.assertEqual(1, len(self.ok(self.http(self.alice, '/api/workspace'))['data']['drafts']))
        self.failed(self.http(self.alice, '/api/review', self.review_body(first)), 403, 'SELF_APPROVAL_FORBIDDEN')
        self.ok(self.http(self.bob, '/api/review', self.review_body(first, decision='request_changes', key='request-source-changes')))
        revised = self.ok(self.ask_draft(receipt, key='agent-receipt-revised', context={
            'draft_id': first['draft_id'], 'draft_version': first['draft_version']}))['data']['draft']
        self.assertEqual(2, revised['draft_version'])
        self.failed(self.http(self.bob, '/api/review', self.review_body(first, key='old-source-review')), 409, 'SUPERSEDED_DRAFT')
        applied = self.ok(self.http(self.bob, '/api/review', self.review_body(revised, key='revised-source-review')))
        self.assertEqual(2, applied['data_version'])
        self.assertEqual(2, applied['data']['application']['data']['draft_version'])
        config = self.ok(self.tool('configuration', {'configuration_id': CONFIG_L1, 'version': 2}))
        self.assertEqual(74, len(config['data']['items']))
        self.assertEqual(12, sum(row['population'] == 'unknown' for row in config['data']['items']))
        self.assertTrue(config['evidence'])

    def test_receipt_scope_permission_order_and_stale_base_cannot_be_bypassed_by_model(self):
        receipt = self.upload_l1()
        self.alice.app.provider_factory = lambda names: ScriptedReceipt()
        self.bob.app.provider_factory = lambda names: ScriptedReceipt()
        self.failed(self.ask_draft(receipt, server=self.bob), 403, 'RECEIPT_OWNER_REQUIRED')
        # Receipt owner remains the same, but their current request has no draft permission.
        with patch.dict(self.host._identities._users,
                        {'dev-alice': UserIdentity('dev-alice', frozenset({'read'}))}):
            self.failed(self.ask_draft(receipt, key='revoked-draft'), 403, 'FORBIDDEN')
        for attack in ('approval', 'request_key', 'receipt', 'invented_operations'):
            with self.subTest(attack=attack):
                self.alice.app.provider_factory = lambda names, attack=attack: ScriptedReceipt(attack=attack)
                denied = self.failed(self.ask_draft(receipt, key='attack-' + attack), 502, 'AI_CONDITION_MISMATCH')
                self.assertEqual('draft_not_saved', denied['data']['action'])
        self.assertEqual([], self.ok(self.http(self.alice, '/api/workspace'))['data']['drafts'])
        # Another approved change advances the base without granting permission to use an old receipt.
        unrelated = self.draft(key='change-base')
        self.ok(self.http(self.bob, '/api/review', self.review_body(unrelated, key='change-base-review')))
        self.alice.app.provider_factory = lambda names: ScriptedReceipt()
        self.failed(self.ask_draft(receipt, key='stale-receipt'), 409, 'STALE_RECEIPT')
        self.failed(self.tool('configuration', {'configuration_id': CONFIG_L1, 'version': 2}), 404, 'NOT_FOUND')

    def test_operating_receipt_agent_draft_preserves_evidence_and_fixed_AB_comparison(self):
        queries = example_queries()
        before = {m: self.ok(self.tool('investigate', {'query': q, 'version': 1}))['data']
                  for m, q in queries.items()}
        raw = {'origin': 'synthetic', 'authored_at': '2026-10-26T12:00:00+09:00',
               'notice': 'AI 업무 통합 시험용 가상 이력이며 실제 회사 기록이 아님',
               'records': [{k:v for k,v in op['node'].items() if k not in ('origin', 'evidence')}
                           for op in self.operations]}
        receipt = self.ok(self.http(self.alice, '/api/history/intake', {'filename': 'agent-history-test.json',
            'content_base64': base64.b64encode(json.dumps(raw).encode()).decode()}))
        self.alice.app.provider_factory = lambda names: ScriptedReceipt()
        saved = self.ok(self.ask_draft(receipt, key='agent-history'))['data']
        draft = saved['draft']
        self.assertEqual(receipt['data']['operations'], saved['draft_record']['operations'])
        self.assertTrue(all(op['node']['origin'] == 'synthetic' for op in saved['draft_record']['operations']))
        new_evidence = {op['node']['id'] for op in saved['draft_record']['operations'] if op['node']['type'] == 'Evidence'}
        self.assertTrue(new_evidence)
        self.ok(self.http(self.bob, '/api/review', self.review_body(draft, key='agent-history-review')))
        for mode, query in queries.items():
            result = self.ok(self.tool('compare_investigation', {'query': query, 'before_version': 1, 'after_version': 2}))
            self.assertEqual(query, result['data']['query'])
            self.assertEqual(before[mode], result['data']['before'])
            by_id = {row['unit_id']:row for row in result['data']['after']['results']}
            self.assertEqual('excluded' if mode == 'A' else 'included', by_id['unit:U104']['status'])
            self.assertEqual('needs_confirmation', by_id['unit:U106']['status'])
            self.assertTrue(new_evidence.intersection(by_id['unit:U104']['evidence']))

    def test_quality_missing_conditions_are_questions_and_complete_conditions_are_only_proposals(self):
        proposed = {'unit_ids': ['unit:U104'], 'lot_id': 'lot:demo:lan8710:L07',
            'at': '2026-09-20T10:00:00+09:00', 'start': '2026-09-01T00:00:00+09:00',
            'end': '2026-09-21T00:00:00+09:00'}
        self.alice.app.provider_factory = lambda names: ScriptedConditions(proposed)
        missing = self.ok(self.ask_quality('장착 이력을 조사해줘.'))['data']
        self.assertEqual('needs_input', missing['action'])
        self.assertEqual(set(proposed), set(missing['required_fields']))
        self.assertEqual([], missing['partial_conditions']['unit_ids'])
        self.assertIsNone(missing['partial_conditions']['lot_id'])
        self.assertEqual('not_executed', missing['consistency'])
        full_question = 'unit:U104의 lot:demo:lan8710:L07에 대해 A ' + proposed['at'] + ' B ' + proposed['start'] + ' 부터 ' + proposed['end'] + ' 까지 조사해줘.'
        complete = self.ok(self.ask_quality(full_question))['data']
        self.assertEqual('confirm_conditions', complete['action'])
        self.assertEqual('not_executed', complete['consistency'])
        self.assertEqual('', complete['runtime']['text'])
        self.assertEqual({'mode': 'A', 'unit_ids': proposed['unit_ids'], 'lot_id': proposed['lot_id'], 'at': proposed['at']}, complete['proposed_queries']['A'])
        self.assertEqual(proposed['start'], complete['proposed_queries']['B']['start'])
        self.assertEqual(proposed['end'], complete['proposed_queries']['B']['end'])
        self.assertTrue(all(call['name'] == 'lookup' for call in complete['tool_calls']))
        with self.host.request(DevelopmentContext('dev-alice')) as request:
            count = request._service.db.execute("SELECT count(*) AS n FROM runs WHERE tool='investigate'").fetchone()['n']
        self.assertEqual(0, count)
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])

    def test_knowledge_search_preserves_origin_snapshot_and_direct_tool_result(self):
        self.alice.app.provider_factory = lambda names: ScriptedScreen()
        body = {'screen': 'evidence', 'version': 1, 'context': {'search_query': 'LAN8710', 'origin': 'real'},
                'question': 'LAN8710 원본 근거와 적용 범위를 찾아줘.'}
        result = self.ok(self.http(self.alice, '/api/assistant', body))['data']
        self.assertEqual('knowledge', result['role'])
        self.assertEqual('matched', result['consistency'])
        arguments = {'query': 'LAN8710', 'origin': 'real', 'version': 1}
        direct = self.ok(self.tool('search_evidence', arguments))
        self.assertEqual(direct['data'], result['results'][0]['data'])
        self.assertEqual(arguments, result['tool_calls'][0]['arguments'])
        self.assertTrue(result['results'][0]['data']['matches'])
        self.assertTrue(all(n['origin'] == 'real' for n in result['results'][0]['data']['matches']))
        self.assertTrue(direct['data']['candidate_only'])
        self.assertEqual('lexical', direct['data']['mode'])
        self.alice.app.provider_factory = lambda names: ScriptedScreen(attack='version')
        denied = self.failed(self.http(self.alice, '/api/assistant', body), 502, 'AI_CONDITION_MISMATCH')
        self.assertEqual([], denied['data']['runtime']['tool_run_ids'])
