"""Screen-bound role/tool contracts over HTTP; no live model or auth claim."""
import json

from ontologics.identity import DevelopmentContext
from tests.test_web import WebCase


class ScriptedScreen:
    def __init__(self, *, attack=None, route='knowledge'):
        self.calls = []
        self.attack, self.route = attack, route

    def public_config(self):
        return {'provider': 'scripted-screen-test'}

    def __call__(self, *, model, messages):
        self.calls.append({'mode': 'scripted-screen-test'})
        if len(self.calls) == 1:
            calls = json.loads(messages[-1]['content'])['supplied_calls']
            if self.attack == 'version':
                calls[0]['arguments']['version'] += 1
            elif self.attack == 'approval':
                calls = [{'name': 'review', 'arguments': {}}]
            return {'text': '', 'tool_calls': calls}
        if 'supervisor Agent' in messages[0]['content']:
            return {'text': json.dumps({'role': self.route, 'reason': '현재 범위의 근거 조회로 연결'}), 'tool_calls': []}
        semantic = [json.loads(message['content']) for message in messages
                    if message['role'] == 'tool' and message.get('name') == 'semantic_search']
        if semantic:
            result = semantic[-1]
            ids = [eid for candidate in result['data']['candidates'] for eid in candidate['evidence']]
            answer = {'data_version': result['data_version'], 'answerable': bool(ids),
                      'selected_evidence_ids': ids}
            if self.attack == 'semantic_version':
                answer['data_version'] += 1
            elif self.attack == 'semantic_source_version':
                # A source Git revision must not substitute for the approved data snapshot.
                answer['data_version'] = result['data']['candidates'][0]['source']['version']
            elif self.attack == 'semantic_foreign_evidence':
                answer['selected_evidence_ids'] = ['evidence:demo:initial']
            elif self.attack == 'semantic_unanswerable':
                answer.update(answerable=False, selected_evidence_ids=[])
            return {'text': json.dumps(answer), 'tool_calls': []}
        return {'text': '시험용 설명: 조회 버전과 원본 근거를 확인하세요. 승인·반영 실행은 아닙니다.', 'tool_calls': []}


class AssistantTests(WebCase):
    def ask(self, screen, context, *, version=1):
        return self.http(self.alice, '/api/assistant', {'screen': screen, 'context': context,
            'version': version, 'question': '현재 대상의 원본 근거와 미확인 사항을 설명해줘'})

    def test_product_reads_exact_draft_and_validation_without_changing_approval(self):
        self.alice.app.provider_factory = lambda names: ScriptedScreen()
        draft = self.draft()
        context = {'draft_id': draft['draft_id'], 'draft_version': draft['draft_version']}
        explained = self.ok(self.ask('review', context))['data']
        self.assertEqual('product', explained['role'])
        self.assertEqual('matched', explained['consistency'])
        self.assertEqual(['get_draft', 'validate_draft'], [c['name'] for c in explained['tool_calls']])
        expected_ref = {'draft_id': draft['draft_id'], 'version': draft['draft_version']}
        self.assertTrue(all(c['arguments'] == expected_ref for c in explained['tool_calls']))
        self.assertEqual('awaiting_review', explained['results'][0]['data']['status'])
        self.assertTrue(explained['results'][1]['data']['validation']['valid'])
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])
        self.ok(self.http(self.bob, '/api/review', self.review_body(draft)))
        self.failed(self.ask('review', context), 502, 'AI_CONDITION_MISMATCH')
        applied = self.ok(self.ask('review', context, version=2))['data']
        self.assertEqual(['get_draft'], [c['name'] for c in applied['tool_calls']])
        self.assertEqual('applied', applied['results'][0]['data']['status'])
        self.assertEqual(2, applied['results'][0]['data']['applied_version'])

    def test_knowledge_uses_exact_node_version_and_cannot_review_or_drift(self):
        context = {'node_id': 'evidence:demo:initial'}
        self.failed(self.ask('evidence', context), 503, 'AI_NOT_CONFIGURED')
        self.alice.app.provider_factory = lambda names: ScriptedScreen()
        explained = self.ok(self.ask('evidence', context))['data']
        self.assertEqual('knowledge', explained['role'])
        self.assertEqual('matched', explained['consistency'])
        self.assertEqual({'node_id': context['node_id'], 'version': 1}, explained['tool_calls'][0]['arguments'])
        direct = self.ok(self.tool('lookup', {'node_id': context['node_id'], 'version': 1}))
        self.assertEqual(direct['data'], explained['results'][0]['data'])
        for attack in ('version', 'approval'):
            self.alice.app.provider_factory = lambda names, attack=attack: ScriptedScreen(attack=attack)
            blocked = self.failed(self.ask('evidence', context), 502, 'AI_CONDITION_MISMATCH')
            self.assertEqual([], blocked['data']['runtime']['tool_run_ids'])
            self.assertEqual('', blocked['data']['runtime']['text'])
        self.assertEqual([], self.ok(self.http(self.bob, '/api/workspace'))['data']['drafts'])
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])

    def test_supervisor_delegates_to_knowledge_with_same_user_and_snapshot(self):
        self.alice.app.provider_factory = lambda names: ScriptedScreen()
        context = {'node_id': 'evidence:demo:initial'}
        result = self.ok(self.ask('home', context))
        data = result['data']
        self.assertEqual('knowledge', data['role'])
        self.assertEqual('knowledge', data['delegation']['role'])
        self.assertEqual(1, result['data_version'])
        self.assertEqual({'node_id': context['node_id'], 'version': 1}, data['tool_calls'][0]['arguments'])
        ids = [data['runtime']['run_id'], data['delegation']['runtime']['run_id']]
        self.assertEqual(2, len(set(ids)))
        with self.host.request(DevelopmentContext('dev-alice')) as session:
            rows = session._service.db.execute('SELECT actor FROM runtime_runs WHERE id=ANY(%s)', (ids,)).fetchall()
        self.assertEqual(['user:dev-alice'] * 2, [row['actor'] for row in rows])
        self.alice.app.provider_factory = lambda names: ScriptedScreen(route='apply')
        self.failed(self.ask('home', context), 502, 'AI_ROUTING_INVALID')
        self.assertEqual(1, self.ok(self.http(self.alice, '/api/context'))['data_version'])
        # A clearly stated installation investigation cannot be downgraded to
        # string search merely because its A/B timestamps are still missing.
        body = {'screen': 'home', 'context': {}, 'version': 1,
                'question': 'U104와 U106에 L07 로트가 장착됐는지 조사하려고 해. 필요한 조건을 알려줘.'}
        self.alice.app.provider_factory = lambda names: ScriptedScreen(route='knowledge')
        self.failed(self.http(self.alice, '/api/assistant', body), 502, 'AI_ROUTING_INVALID')
        from tests.test_agent_workflows import ScriptedConditions
        providers = [ScriptedScreen(route='quality'), ScriptedConditions({
            'unit_ids': ['unit:U104', 'unit:U106'], 'lot_id': 'lot:demo:lan8710:L07',
            'at': None, 'start': None, 'end': None})]
        self.alice.app.provider_factory = lambda names: providers.pop(0)
        guided = self.ok(self.http(self.alice, '/api/assistant', body))['data']
        self.assertEqual('quality', guided['role'])
        self.assertEqual('quality', guided['delegation']['role'])
        self.assertEqual('needs_input', guided['action'])
        self.assertEqual({'at', 'start', 'end'}, set(guided['required_fields']))
        # A past participle describes existing data, not a registration action.
        body['question'] = '현재 등록된 unit:U104와 unit:U106의 L07 부품 로트 장착 이력을 조회해줘.'
        self.alice.app.provider_factory = lambda names: ScriptedScreen(route='product')
        self.failed(self.http(self.alice, '/api/assistant', body), 502, 'AI_ROUTING_INVALID')
        providers = [ScriptedScreen(route='quality'), ScriptedConditions({
            'unit_ids': ['unit:U104', 'unit:U106'], 'lot_id': 'lot:demo:lan8710:L07',
            'at': None, 'start': None, 'end': None})]
        self.alice.app.provider_factory = lambda names: providers.pop(0)
        existing = self.ok(self.http(self.alice, '/api/assistant', body))['data']
        self.assertEqual('quality', existing['role'])
        self.assertEqual('needs_input', existing['action'])
