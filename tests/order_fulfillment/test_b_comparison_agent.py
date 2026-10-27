"""CQ06 fixed workflow assertions: free providers only, no paid calls."""
from copy import deepcopy
import json
import re
import unittest

from examples.order_fulfillment import b_comparison_agent as cq
from ontologics.model import ROOT, canonical
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.order_fulfillment.openai_provider import ProviderError
from tests.db import database_for_test


class ComparisonAgentTests(unittest.TestCase):
    def fixture(self, case='defects11'):
        service, task = cq.setup(database_for_test(self), case=case)
        self.addCleanup(service.close)
        return service, task

    def run_agent(self, service, task, provider):
        guard = cq.ProvenanceProvider(provider, task)
        result = ModelRuntime(service, cq.ACTOR, guard, config=cq.CONFIG,
            allowed_tools=cq.TOOLS, capture_tool_trace=True).run([
                {'role':'system','content':cq.PROMPT}, {'role':'user','content':canonical(task)}])
        return result, guard

    def accepted(self, service, task):
        provider=cq.ScriptedProvider(service,task)
        result,guard=self.run_agent(service,task,provider)
        checks,answer=cq.evaluate(service,task,result,task['after_version'])
        self.assertTrue(all(checks.values()),checks)
        return result,guard,answer,provider

    def test_five_cases_match_independent_expected_before_after_and_original_submission(self):
        golden=json.loads((ROOT/'data/order_fulfillment/b_recalculation_expected.json').read_text())
        for case in cq.CASES:
            with self.subTest(case=case):
                service,task=self.fixture(case)
                result,guard,answer,provider=self.accepted(service,task)
                self.assertEqual(4,result['steps'])
                self.assertEqual(4,len(provider.messages))
                self.assertEqual(['compare_response_plan','lookup_many','lookup_many'],
                                 [x['name'] for x in result['tool_trace']])
                self.assertEqual(result['text'],guard.final_submission['text'])
                self.assertTrue(guard.final_submission['accepted'])
                for side,expected in [('before',golden['before']),('after',golden['cases'][case])]:
                    for key,value in expected['forecast'].items():
                        if key in answer[side]: self.assertEqual(value,answer[side][key],key)
                    self.assertEqual(expected['production']['estimated_completion_at'],answer[side]['estimated_completion_at'])
                    self.assertEqual(expected['dispatch_request']['currently_available_quantity'],answer[side]['currently_available_quantity'])
                    self.assertEqual(expected['dispatch_request']['future_quantity'],answer[side]['future_quantity'])
                self.assertEqual(golden['cases'][case]['time_deltas'],answer['time_deltas'])
                for key,value in answer['quantity_deltas'].items():
                    self.assertEqual(golden['cases'][case]['quantity_deltas'][key],value)
                self.assertTrue(answer['synthetic'] and answer['draft_only'] and answer['review_required'])
                self.assertIs(False,answer['execution_performed'])
                if case=='zero_rate11':
                    self.assertEqual('partially_unknown',answer['comparison_status'])
                    self.assertIsNone(answer['after']['on_time_quantity'])
                    self.assertIsNone(answer['quantity_deltas']['on_time_quantity'])

    def test_compact_projection_is_immutable_bounded_and_keeps_both_versions(self):
        service,task=self.fixture()
        original=cq.query(service,task);saved=deepcopy(original)
        compact=cq.compact_response(original)
        self.assertEqual(saved,original)
        self.assertLess(len(canonical(compact).encode()),len(canonical(original).encode()))
        for side in ('before','after'):
            self.assertEqual(original['data'][side]['data_version'],compact['data'][side]['data_version'])
            self.assertEqual(original['data'][side]['response_plan']['forecast']['unknowns'],compact['data'][side]['unknowns'])
            self.assertEqual(original['data'][side]['response_plan']['dispatch_request']['currently_available_quantity'],
                             compact['data'][side]['currently_available_quantity'])
        self.assertEqual(cq.scripted_answer(original),cq.scripted_answer(compact))
        self.assertEqual(cq.build_explanation(original['data']),cq.build_explanation(compact['data']))

    def test_identical_full_and_compact_explanations_preserve_completion_and_no_change(self):
        service,task=self.fixture()
        original=cq.query(service,task)
        original['data']['after']=deepcopy(original['data']['before'])
        compact=cq.compact_response(original)
        full_explanation=cq.build_explanation(original['data'])
        self.assertEqual(full_explanation,cq.build_explanation(compact['data']))
        self.assertIn('변화가 없습니다',full_explanation['basic_text']['change_summary'])

    def test_projection_preserves_original_before_after_drafts_and_their_changes(self):
        service,task=self.fixture()
        original=cq.query(service,task);saved=deepcopy(original)
        compact=cq.compact_response(original)
        self.assertEqual(original['data']['response_changes'],compact['data']['response_changes'])
        for side in ('before','after'):
            self.assertEqual(original['data'][side]['response_plan']['customer_reply'],
                             compact['data'][side]['customer_reply'])
        compact['data']['response_changes'].clear()
        compact['data']['before']['customer_reply']='mutated projection'
        self.assertEqual(saved,original)

    def test_independent_evaluator_rejects_fact_version_type_delta_extra_key_and_prose_mutations(self):
        service,task=self.fixture()
        result,_guard,answer,_provider=self.accepted(service,task)
        variants=[]
        for change in ('quantity','bool','version','delta','extra','prose'):
            candidate=deepcopy(answer)
            if change=='quantity':candidate['after']['on_time_quantity']+=1
            elif change=='bool':candidate['before']['data_version']=True
            elif change=='version':candidate['after']['data_version']=task['before_version']
            elif change=='delta':candidate['time_deltas']['estimated_completion_seconds']=0
            elif change=='extra':candidate['version']=task['after_version']
            else:candidate['customer_reply']='실제 출고 완료했습니다. 납품 보장합니다.'
            mutated=deepcopy(result);mutated['text']=canonical(candidate);variants.append(mutated)
        for candidate in variants:
            with self.subTest(text=candidate['text']):
                checks,kept=cq.evaluate(service,task,candidate,task['after_version'])
                self.assertFalse(all(checks.values()))
                self.assertEqual(candidate['text'],canonical(kept))

    def test_final_evidence_missing_extra_duplicate_or_cross_side_is_rejected_without_repair(self):
        service,task=self.fixture()
        base=cq.ScriptedProvider(service,task)
        for variant in ('missing','extra','duplicate','swap'):
            def altered(*,model,messages):
                reply=base(model=model,messages=messages)
                if not reply['tool_calls']:
                    answer=json.loads(reply['text']);ids=answer['evidence_by_side']['after']
                    if variant=='missing':ids.pop()
                    elif variant=='extra':ids.append('evidence:forged')
                    elif variant=='duplicate':ids.append(ids[0])
                    else:answer['evidence_by_side']['after']=answer['evidence_by_side']['before']
                    reply['text']=canonical(answer)
                return reply
            with self.subTest(variant=variant):
                result,guard=self.run_agent(service,task,altered)
                self.assertFalse(result['ok'])
                self.assertFalse(guard.final_submission['accepted'])
                self.assertTrue(guard.final_submission['text'])
                self.assertEqual(3,len(result['tool_trace']))

    def test_early_final_wrong_phase_arguments_or_identity_never_execute(self):
        service,task=self.fixture()
        expected=cq.query_arguments(task)
        candidates=[{'text':'premature','tool_calls':[]},
            {'text':'','tool_calls':[{'name':'apply','arguments':{}}]},
            {'text':'','tool_calls':[{'name':'compare_response_plan','arguments':{**expected,'actor':'seed-admin'}}]},
            {'text':'','tool_calls':[{'name':'compare_response_plan','arguments':{**expected,'before_version':task['after_version']}}]}]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                result,_=self.run_agent(service,task,lambda **_:candidate)
                self.assertFalse(result['ok'])
                self.assertEqual([],result['tool_trace'])
        self.assertEqual(task['after_version'],service.lookup(cq.ACTOR)['data_version'])

    def test_wrong_evidence_phase_version_or_duplicate_is_blocked_before_lookup(self):
        service,task=self.fixture()
        for variant in ('version','duplicate','kind'):
            provider=cq.ScriptedProvider(service,task)
            def altered(*,model,messages):
                reply=provider(model=model,messages=messages)
                if reply['tool_calls'] and reply['tool_calls'][0]['name']=='lookup_many':
                    args=reply['tool_calls'][0]['arguments']
                    if variant=='version':args['version']=task['after_version']
                    elif variant=='duplicate':args['node_ids'].append(args['node_ids'][0])
                    else:args['kind']='SourceDocument'
                return reply
            result,_=self.run_agent(service,task,altered)
            self.assertFalse(result['ok'])
            self.assertEqual(1,len(result['tool_trace']))

    def test_host_provenance_is_separate_and_resolves_every_integrity_chain_per_version(self):
        service,task=self.fixture()
        original=cq.query(service,task)
        details=cq.resolve_provenance(service,original)
        for side in ('before','after'):
            self.assertEqual(set(original['data']['integrity_evidence_by_side'][side]),
                             {x['evidence']['id'] for x in details[side]})
            self.assertTrue(all(x['data_version']==task[side+'_version'] for x in details[side]))
        self.assertNotEqual(task['before_version'],task['after_version'])

    def test_local_patterns_reject_over_budget_and_multiline_strings_at_each_boundary(self):
        limits={'change_summary':60,'customer_reply':80,'dispatch_summary':45,'confirmations':35}
        self.assertEqual(220,sum(limits.values()))
        for key,limit in limits.items():
            with self.subTest(key=key):
                pattern=cq.TEXT_PATTERNS[key]
                self.assertIsNotNone(re.fullmatch(pattern,'가'*limit))
                for wrong in ('','가'*(limit+1),'가\n나','가\n'):
                    self.assertIsNone(re.fullmatch(pattern,wrong))

    def test_observed_long_explanation_is_rejected_unchanged_without_false_scope_failures(self):
        service,task=self.fixture()
        result,_guard,answer,_provider=self.accepted(service,task)
        # Minimal prose regression derived from the retained failed candidate.
        # Full HTTP/runtime originals remain ignored, not test dependencies.
        original_texts={'change_summary': '기한내 960→920, 부족 40→80으로 악화됐고 완전도착은 15:26:40→16:00으로 늦어졌습니다. 확보 600→680, 미래 400→320이며 조건은 동일합니다. 근거상 11:00 실적과 추정이 반영된 조건부 초안입니다.', 'customer_reply': '가상 자료의 담당자 검토용·미발송·미등록 초안입니다. 11:00 실적 반영으로 기한내 920, 부족 80으로 바뀌었습니다. 전량은 16:00 예상입니다. 조건부 예상이며 확정이 아닙니다.', 'dispatch_summary': '현재확보 680, 미래생산 320 기준의 분할출고 검토초안입니다. 기한내 920, 후속 80이며 전량은 16:00 예상입니다.', 'confirmations': '출고능력·운송조건·고객동의와 새 실적 반영 여부를 확인해 주십시오.'}
        answer.update(original_texts);raw=canonical(answer)
        result['text']=raw
        checks,kept=cq.evaluate(service,task,result,task['after_version'])
        self.assertFalse(checks['nonempty_concise_explanation'])
        self.assertTrue(checks['explicit_draft_scope_labels'])
        self.assertTrue(checks['no_obvious_execution_or_fault_guarantee_claim'])
        self.assertTrue(checks['exact_customer_draft_prefix'])
        self.assertTrue(checks['explicit_conditional_estimate'])
        self.assertEqual([128,104,70,37],[len(kept[k]) for k in cq.TEXT_FIELDS])
        self.assertEqual(raw,result['text']);self.assertEqual(answer,kept)
        self.assertFalse(all(checks.values()))

    def test_local_text_limits_blank_lines_prefix_and_conditional_scope_are_enforced(self):
        service,task=self.fixture()
        result,_guard,answer,_provider=self.accepted(service,task)
        limits={'change_summary':60,'customer_reply':80,'dispatch_summary':45,'confirmations':35}
        bounded=deepcopy(answer)
        for key,limit in limits.items():bounded[key]+='가'*(limit-len(bounded[key]))
        result['text']=canonical(bounded)
        checks,_=cq.evaluate(service,task,result,task['after_version'])
        self.assertTrue(checks['nonempty_concise_explanation'])
        self.assertFalse(checks['exact_python_basic_text'])
        for key,limit in limits.items():
            for wrong in ('가'*(limit+1),' '*limit,'가\n나'):
                with self.subTest(key=key,wrong=wrong):
                    candidate=deepcopy(answer);candidate[key]=wrong;raw=canonical(candidate)
                    result['text']=raw;checks,kept=cq.evaluate(service,task,result,task['after_version'])
                    self.assertFalse(checks['nonempty_concise_explanation'])
                    self.assertEqual(candidate,kept);self.assertEqual(raw,result['text'])
        for text,failed in [('가상 초안·미발송·미등록입니다. 조건부 예상입니다.','exact_customer_draft_prefix'),
                            ('가상 자료의 담당자 검토용·미발송·미등록 초안입니다.','explicit_conditional_estimate')]:
            candidate=deepcopy(answer);candidate['customer_reply']=text;result['text']=canonical(candidate)
            checks,_=cq.evaluate(service,task,result,task['after_version'])
            self.assertFalse(checks[failed]);self.assertFalse(all(checks.values()))

    def test_observed_semantic_failure_and_any_rewrite_are_rejected_without_repair(self):
        service,task=self.fixture()
        result,_guard,answer,_provider=self.accepted(service,task)
        failed={'change_summary':'완성양품이 줄어 기한내 수량↓부족↑전량예상늦춤;분할출고·운송시간은 동일',
                'customer_reply':'가상 자료의 담당자 검토용·미발송·미등록 초안입니다. 조건부 예상으로, 양품 감소와 동일한 분할출고·운송조건에서'}
        for changes in (failed,{'customer_reply':answer['customer_reply'][:-1]},
                        {'change_summary':answer['change_summary']+' '}):
            candidate={**answer,**changes};raw=canonical(candidate)
            mutated={**result,'text':raw}
            checks,kept=cq.evaluate(service,task,mutated,task['after_version'])
            self.assertTrue(checks['nonempty_concise_explanation'])
            self.assertFalse(checks['exact_python_basic_text'])
            self.assertEqual(candidate,kept);self.assertEqual(raw,mutated['text'])

    def test_python_explanation_is_projected_but_original_tool_result_stays_unchanged(self):
        service,task=self.fixture()
        original=cq.query(service,task);saved=deepcopy(original)
        projected=cq.compact_response(original)
        explanation=projected['data']['python_explanation']
        self.assertEqual('python',explanation['producer'])
        self.assertEqual(cq.scripted_answer(original)['customer_reply'],explanation['basic_text']['customer_reply'])
        projected['data']['python_explanation']['basic_text']['customer_reply']='forged'
        self.assertEqual(saved,original)
        self.assertNotEqual('forged',cq.scripted_answer(projected)['customer_reply'])

    def test_failed_or_out_of_order_history_is_rejected_before_provider(self):
        task={'delivery_round_id':'r','before_version':1,'after_version':2,
              'before_as_of':'2026-10-27T10:00:00+09:00','after_as_of':'2026-10-27T11:00:00+09:00'}
        called=[]
        guard=cq.ProvenanceProvider(lambda **kw:called.append(kw),task)
        for history in [
            [{'role':'tool','name':'compare_response_plan','content':canonical({'ok':False,'data_version':2})}],
            [{'role':'tool','name':'lookup_many','content':'{}'}]]:
            with self.assertRaises(ProviderError):guard(model=cq.CONFIG.model,messages=history)
        self.assertEqual([],called)
