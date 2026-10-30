"""Presentation regression: no process, credential, DB or external request."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from examples.order_fulfillment import b_notebook_ui as ui


def fake_demo():
    demo = Mock()
    demo.cases = ('cq01_intake', 'cq01_promise')
    demo.plan_summary.return_value = dict(model='fixed-model', max_output_tokens=1024,
        timeout_seconds=30, automatic_retries=0, total_request_cap=8,
        representatives=[dict(case=c, label=c, question='고정 질문') for c in demo.cases])
    demo.live_readiness.return_value = dict(ready=False, issues=['공통 계약 검토 필요'])
    demo.status.return_value = dict(mode='live', started=False, running=False,
        exit_code=None, requests_reserved=0, events=[], overall_passed=False)
    demo.result.return_value = dict(available=False)
    demo.input_preview.side_effect = lambda case: dict(label=case, question='검토할 고정 질문',
        source_text='검토할 가상 원문', source_path='data/fixed.md', source_sha256='c' * 64)
    return demo


class NotebookPresentationTests(unittest.TestCase):
    def test_plan_is_inert_and_explains_readiness_and_limits(self):
        demo = fake_demo()
        html = ui.plan_html(demo)
        self.assertIn('공통 계약 검토 필요', html)
        self.assertIn('1,024', html)
        self.assertIn('8', html)
        demo.start_free.assert_not_called()
        demo.start_live_with_token.assert_not_called()

    def test_model_korean_is_prominent_source_is_excluded_and_html_escaped(self):
        value = dict(available=True, case='cq01_intake', execution_mode='actual_paid',
            model_text=json.dumps({'summary': '새 모델 답변 <script>',
                'source_text': '가상 입력 원문'}, ensure_ascii=False),
            answer={'evidence': ['evidence:new'], 'source_text': '가상 입력 원문'},
            tool_trace=[dict(name='lookup', result={'ok': True, 'data_version': 2})],
            actual_api_attempts=4, http_requests=4, usage=[], automatic_passed=True,
            report_sha256='a' * 64)
        html = ui.result_html(value)
        self.assertIn('새 모델 답변 &lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertLess(html.index('새 모델 답변'), html.index('<details>'))
        self.assertIn('이번 실행의 실제 LLM 응답', html)
        self.assertIn('evidence:new', html)
        self.assertIn('모델 원문 JSON', html)
        self.assertNotIn('가상 입력 원문', html[:html.index('<details>')])

    def test_model_numeric_time_and_condition_facts_are_visible_before_details(self):
        value = dict(available=True, case='cq04_normal', execution_mode='actual_paid',
            model_text=json.dumps(dict(summary='조건부 예상', on_time_quantity=960,
                shortfall_quantity=40, estimated_full_arrival_at='2026-10-27T15:26:40+09:00',
                condition=dict(transport_seconds=3600, split_allowed=True),
                source_text=dict(private_input_quantity=8888), input=dict(hidden_input=7777))),
            answer=dict(on_time_quantity=999), automatic_passed=True)
        html = ui.result_html(value)
        primary = html[:html.index('<details>')]
        self.assertIn('모델 구조화 답', primary)
        for fact in ('on_time_quantity', '960', 'shortfall_quantity', '40',
                     '2026-10-27T15:26:40+09:00', 'condition.transport_seconds', '3600',
                     'condition.split_allowed', 'true'):
            self.assertIn(fact, primary)
        for hidden in ('8888', '7777', 'private_input_quantity', 'hidden_input', '999'):
            self.assertNotIn(hidden, primary)

    def test_offline_result_is_never_labelled_actual_success(self):
        html = ui.result_html(dict(available=True, case='cq01_intake',
            execution_mode='offline_http_double', model_text='무료 모형 답변',
            answer={}, actual_api_attempts=0, http_requests=4, automatic_passed=True))
        self.assertIn('무료 HTTP 모의 응답', html)
        self.assertNotIn('이번 실행의 실제 LLM 응답', html)

    def test_readiness_blocks_before_paid_approval_prompt(self):
        demo = fake_demo(); read = Mock()
        console = ui.Console(demo, read=read, show=Mock())
        console.start_live()
        read.assert_not_called()
        demo.start_live_with_token.assert_not_called()

    def test_paid_confirmation_forwards_only_human_token_and_refuses_duplicate(self):
        demo = fake_demo(); demo.live_readiness.return_value = dict(ready=True, issues=[])
        demo.status.side_effect = lambda mode: dict(started=False, running=False, overall_passed=mode == 'free')
        demo.approval_token.return_value = '유료 승인 this-run'
        console = ui.Console(demo, read=Mock(return_value='유료 승인 wrong-run'), show=Mock())
        console.start_live()
        demo.start_live_with_token.assert_not_called()
        console.read.return_value = '유료 승인 this-run'
        with patch.object(console, 'monitor'):
            console.start_live()
        demo.start_live_with_token.assert_called_once_with('유료 승인 this-run')
        demo.status.side_effect = None
        demo.status.return_value = dict(started=True, running=False)
        console.start_live()
        self.assertEqual(demo.start_live_with_token.call_count, 1)

    def test_missing_free_gate_blocks_before_paid_confirmation(self):
        demo = fake_demo(); demo.live_readiness.return_value = dict(ready=True, issues=[])
        read = Mock()
        ui.Console(demo, read=read, show=Mock()).start_live()
        read.assert_not_called()
        demo.approval_token.assert_not_called()
        demo.start_live_with_token.assert_not_called()

    def test_semantic_answer_is_shown_immediately_before_manual_notes(self):
        demo = fake_demo(); request = dict(case='cq01_intake', report_sha256='a' * 64)
        order = []
        def show(html):
            order.append(('show', html))
        answers = iter(['통과', '원문과 답변·근거를 대조했습니다.'])
        def read(prompt):
            order.append(('read', prompt)); return next(answers)
        console = ui.Console(demo, read=read, show=show)
        console.semantic_review(request)
        self.assertEqual(order[0][0], 'show')
        self.assertIn('의미 검토', order[0][1])
        self.assertIn('검토할 고정 질문', order[0][1])
        self.assertIn('검토할 가상 원문', order[0][1])
        self.assertIn('data/fixed.md', order[0][1])
        demo.result.assert_called_once_with('live', case='cq01_intake')
        demo.submit_review.assert_called_once_with('원문과 답변·근거를 대조했습니다.',
            passed=True, case='cq01_intake')
        demo.submit_draft_review.assert_not_called()

    def test_blank_review_does_not_pass_or_generate_notes(self):
        demo = fake_demo(); console = ui.Console(demo, read=Mock(return_value=''), show=Mock())
        self.assertFalse(console.semantic_review(dict(case='cq01_intake')))
        demo.submit_review.assert_not_called()
        demo.stop.assert_called_once_with('live')

    def test_draft_review_is_separate_and_displays_exact_version_and_hash(self):
        demo = fake_demo(); shown = Mock()
        console = ui.Console(demo, read=Mock(side_effect=['승인', '정확한 초안 버전을 확인했습니다.']), show=shown)
        console.draft_review(dict(draft_id='draft:new', version=3, content_hash='b' * 64,
            author='user:author', reviewer='user:reviewer', draft_material={'operations': []}))
        html = shown.call_args.args[0]
        for expected in ('draft:new', '3', 'b' * 64, 'user:author', 'user:reviewer', '별도 업무 승인'):
            self.assertIn(expected, html)
        self.assertIn('<details open><summary>정확 초안 내용·변경 operations', html)
        demo.submit_draft_review.assert_called_once_with('정확한 초안 버전을 확인했습니다.', decision='approve')
        demo.submit_review.assert_not_called()

    def test_monitor_failure_stops_and_never_launches_following_cases(self):
        demo = fake_demo(); demo.status.return_value = dict(started=True, running=True, events=[])
        demo.review_request.side_effect = ValueError('invalid exact report')
        console = ui.Console(demo, show=Mock())
        with self.assertRaises(ValueError):
            console.monitor('live')
        demo.stop.assert_called_once_with('live')
        demo.start_live_with_token.assert_not_called()

    def test_monitor_polls_bounded_wait_and_updates_automatically(self):
        demo = fake_demo()
        running = dict(started=True, running=True, events=[])
        done = dict(started=True, running=False, events=[], overall_passed=True)
        demo.status.side_effect = [running, done]
        demo.review_request.return_value = None; demo.draft_review_request.return_value = None
        console = ui.Console(demo, show=Mock())
        with patch.object(console, 'show_results'):
            console.monitor('live')
        demo.wait.assert_called_once_with('live', seconds=1)

    def test_notebook_template_never_reads_local_ignored_notebook_and_is_fresh(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('no template file read')):
            first = ui.notebook_document()
            first['cells'][0]['source'][0] = 'mutated'
            second = ui.notebook_document()
        self.assertNotEqual(first['cells'][0]['source'][0], second['cells'][0]['source'][0])

    def test_notebook_default_controls_disabled_and_cells_compile(self):
        root = Path(__file__).resolve().parents[2]
        book = ui.notebook_document()
        local = root / 'notebooks/CQ01_06_실제실행_간소화.ipynb'
        if local.exists():
            self.assertEqual(book, json.loads(local.read_text()))
        codes = [''.join(c['source']) for c in book['cells'] if c['cell_type'] == 'code']
        self.assertIn('ENABLE_CONTROLS = False', '\n'.join(codes))
        self.assertNotIn('approval_phrase(', '\n'.join(codes))
        for index, code in enumerate(codes):
            compile(code, f'notebook-cell-{index}', 'exec')


if __name__ == '__main__':
    unittest.main()
