"""The C example runs end to end on a fresh schema and stops at human review."""
from pathlib import Path
import tempfile
import unittest

import json

from examples.claim_response.run_demo import CLAIMS, TEST_RESULTS, live_agent_settings, run_demo
from ontologics.claim_response.registration import expected_trace
from ontologics.openai_provider import OpenAISettings
from ontologics.paid_budget import cost_policy_from_environment
from ontologics.service import DomainError
from tests.db import database_for_test


class RunDemoTests(unittest.TestCase):
    def run_once(self, claim, results=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return run_demo(database_for_test(self), claim_path=claim, request_dir=folder.name,
                        results_path=results), Path(folder.name)

    def test_csv_claim_runs_from_file_to_unsent_request_drafts(self):
        summary, folder = self.run_once(CLAIMS / 'claim_table.csv')
        self.assertEqual({'seed': 1, 'claim_registered': 2, 'claim_confirmed': 3, 'after_request_drafts': 3},
                         summary['versions'])
        self.assertEqual(['unit:C-D00%d' % n for n in range(1, 6)], summary['links']['matched'])
        self.assertEqual([], summary['links']['open'])
        self.assertEqual('production-line:demo:C-01', summary['production_line_id'])
        agent = summary['agent_trace']
        self.assertEqual((True, 'claim_response', '1.0.0', 'agent-result-1', None),
                         (agent['ok'], agent['agent_name'], agent['agent_version'], agent['schema_version'], agent['error']))
        self.assertEqual(summary['links']['matched'], agent['payload']['matched_unit_ids'])
        self.assertEqual(summary['production_line_id'], agent['payload']['production_line_id'])
        self.assertEqual(3, agent['payload']['data_version'])
        self.assertGreater(agent['evidence_refs'], 0)
        self.assertEqual(('ready', 120, 'C-S001 ~ C-S120'),
                         (summary['hold_scope']['state'], summary['hold_scope']['count'], summary['hold_scope']['units']))
        self.assertEqual(['c_operations_record'], summary['hold_scope']['basis'])
        self.assertEqual((75, 'C-D006 ~ C-D080'), (summary['candidate_scope']['count'], summary['candidate_scope']['units']))
        self.assertEqual(['production_stop', 'shipment_hold'], sorted(r['kind'] for r in summary['requests']))
        self.assertEqual(('awaiting_review', '미검토', '미송부', '미확인'),
                         tuple(summary['request_draft'][k] for k in ('status', 'review_label', 'sending', 'on_site')))
        files = sorted(folder.glob('*.md'))
        self.assertEqual(2, len(files))
        self.assertEqual(sorted(str(f) for f in files), sorted(str(Path(d)) for d in summary['documents']))
        text = next(f for f in files if f.name.endswith('shipment_hold.md')).read_text(encoding='utf-8')
        self.assertIn('C-S001 ~ C-S120 (120대)', text)
        self.assertIn('| 송부 상태 | 미송부 |', text)

    def test_mail_and_excel_claims_reach_the_same_result(self):
        csv, _ = self.run_once(CLAIMS / 'claim_table.csv')
        for name in ('claim_mail.eml', 'claim_sheet.xlsx'):
            with self.subTest(file=name):
                other, _ = self.run_once(CLAIMS / name)
                for key in ('links', 'production_line_id', 'quality_case_id', 'hold_scope', 'candidate_scope'):
                    self.assertEqual(csv[key], other[key], key)


class RunDemoPhase2Tests(unittest.TestCase):
    """2nd phase: test request -> test-team results -> reproduction quantity -> unsent request for A."""
    run_once = RunDemoTests.run_once

    def test_confirmed_scenario_ends_with_an_unsent_request_for_a(self):
        summary, folder = self.run_once(CLAIMS / 'claim_table.csv', TEST_RESULTS / 'scenario_1.csv')
        phase2 = summary['phase2']
        self.assertEqual(3, summary['versions']['after_request_drafts'])
        self.assertEqual({'after_request_review': 4, 'test_request_sent': 5, 'after_reproduction_draft': 5},
                         phase2['versions'])
        self.assertEqual(125, phase2['test_request']['count'])
        self.assertEqual(('confirmed', 10, 0), tuple(phase2['reproduction'][k] for k in (
            'state', 'required_good_quantity', 'unresolved_quantity')))
        self.assertEqual(('awaiting_review', '미송부'),
                         (phase2['reproduction_draft']['status'], phase2['reproduction_draft']['sending']))
        names = sorted(f.name for f in folder.glob('*.md'))
        self.assertEqual(4, len(names))
        text = (folder / next(n for n in names if n.startswith('reproduction_'))).read_text(encoding='utf-8')
        for phrase in ('**10대**', '| 검토 상태 | 미검토 |', '| 송부 상태 | 미송부 |'):
            self.assertIn(phrase, text)

    def test_undetermined_scenario_drafts_results_without_a_request(self):
        summary, folder = self.run_once(CLAIMS / 'claim_table.csv', TEST_RESULTS / 'scenario_3.csv')
        self.assertEqual(('undetermined', None), (summary['phase2']['reproduction']['state'],
                                                  summary['phase2']['reproduction']['required_good_quantity']))
        self.assertEqual('awaiting_review', summary['phase2']['reproduction_draft']['status'])
        self.assertFalse(list(folder.glob('reproduction_*.md')))


class FakeModel:
    """Stands in for OpenAIProvider in tests: no network, no cost. Reads like a model would."""
    def __init__(self, settings, tools, wrong=False):
        self.tools, self.wrong, self.calls = tools, wrong, []

    def public_config(self):
        return {'provider': 'fake-model-test'}

    def __call__(self, *, model, messages):
        self.calls.append({'status': 'completed', 'model': model})
        task = json.loads(next(m['content'] for m in messages if m['role'] == 'user' and 'lookup_arguments' in m['content']))
        # Like the real 7-1 run: the model also names kind on the node_id read.
        arguments = [dict(task['lookup_arguments'][0], kind='ClaimIntake')] + task['lookup_arguments'][1:]
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        if len(results) < len(arguments):
            return {'text': '', 'tool_calls': [{'name': 'lookup', 'arguments': arguments[len(results)]}]}
        calls = [{'name': 'lookup', 'arguments': a, 'result': r} for a, r in zip(arguments, results)]
        answer = expected_trace(task['intake_id'], task['data_version'], calls)
        if self.wrong:
            answer = dict(answer, matched_unit_ids=answer['matched_unit_ids'][:-1])
        return {'text': json.dumps(answer), 'tool_calls': []}


def live(wrong=False):
    values = {'ONTOLOGICS_REQUEST_MAX_COST_USD': '1', 'ONTOLOGICS_MODEL_PRICES_JSON': json.dumps(
        {'gpt-5.4-mini-2026-03-17': {'input_usd_per_million': '0.75', 'output_usd_per_million': '4.50',
                                     'max_input_tokens': 400000}})}
    return {'settings': OpenAISettings(api_key='test-key-not-real'), 'cost_policy': cost_policy_from_environment(values),
            'provider_factory': lambda settings, tools: FakeModel(settings, tools, wrong)}


class LiveAgentWiringTests(unittest.TestCase):
    """7-1 wiring without network: the model path uses the cost policy and the same result check."""
    run_once = RunDemoTests.run_once

    def run_live(self, wrong=False):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return run_demo(database_for_test(self), claim_path=CLAIMS / 'claim_table.csv', request_dir=folder.name,
                        live=live(wrong))['live_agent_trace']

    def test_missing_cost_policy_blocks_before_any_call(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = Path(folder.name) / '.env.test'
        env.write_text('OPENAI_API_KEY=test-key-not-real\n', encoding='utf-8')
        with self.assertRaises(DomainError) as failure:
            live_agent_settings(env)
        self.assertEqual('COST_POLICY_REQUIRED', failure.exception.code)

    def test_model_answer_matching_its_reads_is_accepted(self):
        report = self.run_live()
        self.assertTrue(all(report['automatic_checks'].values()), report['automatic_checks'])
        self.assertEqual(['lookup'] * 3, [c['name'] for c in report['tool_calls']])
        self.assertEqual('production-line:demo:C-01', report['answer']['production_line_id'])
        self.assertGreater(report['evidence_refs'], 0)
        self.assertNotIn('test-key-not-real', json.dumps(report))

    def test_model_answer_that_differs_is_rejected(self):
        report = self.run_live(wrong=True)
        self.assertEqual('AGENT_RESULT_REJECTED', report['runtime']['error']['code'])
        self.assertIsNone(report['answer'])
        self.assertFalse(report['automatic_checks']['answer_equals_recomputed_trace'])
        # The rejected text and the trace from the same reads are kept so a person can see the difference.
        self.assertEqual(4, len(json.loads(report['final_text'])['matched_unit_ids']))
        self.assertEqual(5, len(report['expected_from_reads']['matched_unit_ids']))





class ScenarioAppTests(unittest.TestCase):
    """Claim app: the 5 scenario input files, used like a person would, give the intended answers."""
    def drive(self, claim, results=(), live_settings=None):
        import shutil
        import uuid
        from examples.claim_response.scenario_app import INPUTS, UPLOADS, ClaimCase
        uploads, docs = UPLOADS / ('test-' + uuid.uuid4().hex[:8]), tempfile.TemporaryDirectory()
        self.addCleanup(shutil.rmtree, uploads, True)
        self.addCleanup(docs.cleanup)
        case = ClaimCase(database_for_test(self), docs_dir=docs.name, uploads_dir=uploads, live=live_settings)
        self.addCleanup(case.close)
        case.upload_claim(claim, (INPUTS / claim).read_bytes())
        case.approve_claim()
        case.confirm_claim()
        if case.state == 'containment_ready':
            case.approve_containment()
            case.send_test_request()
            for name in results:
                case.upload_results(name, (INPUTS / name).read_bytes())
            if case.state == 'reproduction_ready':
                case.approve_reproduction()
        return case, {e['title']: dict(e['rows']) for e in case.events}

    def test_full_test_gives_six(self):
        case, ev = self.drive('1_메일_전수시험_클레임.eml', ['1_테스트팀_결과.csv'])
        self.assertEqual('done', case.state)
        self.assertEqual('3대 · C-D021 ~ C-D023', ev['보드·생산라인 추적']['연결된 보드'])
        self.assertTrue(ev['테스트 의뢰서']['의뢰 대상'].startswith('123대'))
        self.assertEqual(('6대', '확정'), (ev['재생산 수량·A 요청서']['재생산 필요'], ev['재생산 수량·A 요청서']['상태']))
        # After the reviews every document says reviewed, and still unsent.
        docs = {n: text for e in case.events for n, text in e['documents'].items()}
        self.assertEqual(4, len(docs))
        for name, text in docs.items():
            with self.subTest(document=name):
                self.assertIn('| 검토 상태 | 검토·그래프 반영 완료 |', text)
                self.assertIn('| 송부 상태 | 미송부 |', text)

    def test_sample_test_gives_a_lower_bound(self):
        case, ev = self.drive('2_엑셀_표본시험_클레임.xlsx', ['2_테스트팀_결과.csv'])
        self.assertEqual('34대 / 90대', ev['테스트팀 결과 받음']['시험 / 미검사'])
        self.assertEqual('3대', ev['재생산 수량·A 요청서']['재생산 필요'])
        self.assertIn('하한', ev['재생산 수량·A 요청서']['상태'])

    def test_not_reproduced_stays_undetermined(self):
        case, ev = self.drive('3_CSV_증상미재현_클레임.csv', ['3_테스트팀_결과.csv'])
        self.assertEqual('미정', ev['재생산 수량']['재생산 필요'])
        self.assertIn('만들지 않음', ev['재생산 수량']['A 요청서'])

    def test_mistyped_serial_stops_the_trace(self):
        case, ev = self.drive('4_메일_일련번호오기재_클레임.eml')
        self.assertEqual('trace_blocked', case.state)
        self.assertEqual('C-D730 (not_found)', ev['보드·생산라인 추적 — 사람 확인 필요']['못 찾은 번호'])
        self.assertNotIn('중단 요청서 초안', ev)

    def test_broken_result_file_is_blocked_then_the_fix_goes_through(self):
        case, ev = self.drive('5_CSV_결과파일오류_클레임.csv', ['5_테스트팀_결과_오류.csv', '5_테스트팀_결과_수정본.csv'])
        blocked = ev['테스트팀 결과 — 오류로 차단']
        self.assertEqual(('안 함 (0건)', '3건'), (blocked['저장'], blocked['오류']))
        self.assertNotIn('시험 / 미검사', blocked)
        rows = blocked['고칠 행']
        for code in ('DUPLICATE_RESULT', 'UNIT_NOT_REQUESTED', 'TIMEZONE_REQUIRED'):
            self.assertIn(code, rows)
        self.assertEqual(('3대', '확정'), (ev['재생산 수량·A 요청서']['재생산 필요'], ev['재생산 수량·A 요청서']['상태']))
        self.assertEqual('done', case.state)

    def test_model_trace_is_checked_against_the_functions(self):
        # Fake model, no network: right answer is accepted, a wrong one is rejected; the flow continues on functions.
        _, ev = self.drive('1_메일_전수시험_클레임.eml', live_settings=live())
        model = ev['C Agent 추적 (실제 모델)']
        self.assertEqual(('예', '4/4 통과', 'C-D021 ~ C-D023'), (model['함수 계산과 일치'], model['자동 검사'], model['모델 답: 보드']))
        _, ev = self.drive('1_메일_전수시험_클레임.eml', live_settings=live(wrong=True))
        model = ev['C Agent 추적 (실제 모델)']
        self.assertEqual(('아니요 (거부됨)', 'AGENT_RESULT_REJECTED'), (model['함수 계산과 일치'], model['거부 이유']))
        self.assertIn('중단 요청서 초안', ev)

    def test_actions_out_of_order_are_refused(self):
        from examples.claim_response.scenario_app import ActionError, ClaimCase
        case = ClaimCase(database_for_test(self))
        with self.assertRaises(ActionError):
            case.approve_containment()
        with self.assertRaises(ActionError):
            case.upload_claim('note.txt', b'x')

if __name__ == '__main__':
    unittest.main()
