"""Deterministic explanation contracts; no database, model, or network."""
from copy import deepcopy
import unittest

from examples.order_fulfillment.b_comparison_narrative import build_explanation
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.ingest import build_catalog
from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, CASES, load_demo_recalculation
from ontologics.order_fulfillment.response_comparison import compare_response_plan


class ComparisonNarrativeTests(unittest.TestCase):
    @staticmethod
    def comparison(case='defects11'):
        before=build_catalog()['nodes']+load_demo_contract()+load_demo_delivery()
        updates={n['id']:n for n in load_demo_recalculation(case)}
        after={n['id']:deepcopy(n) for n in before};after.update(updates)
        return compare_response_plan(before,list(after.values()),ROUND,AS_OF,AFTER_AS_OF[case],
            before_version=2,after_version=3,queried_at=AFTER_AS_OF[case])

    def test_defects_distinguishes_increasing_available_stock_from_decreasing_good_rate(self):
        data=self.comparison();saved=deepcopy(data);result=build_explanation(data)
        self.assertEqual(saved,data)
        facts={r['field']:r for r in result['reasons']}
        self.assertEqual((600,680,'대','증가'),tuple(facts['currently_available_quantity'][k]
            for k in ('before','after','unit','direction')))
        self.assertEqual((90,80,'대/시간','감소'),tuple(facts['good_rate_per_hour'][k]
            for k in ('before','after','unit','direction')))
        self.assertEqual('최근 정상완료 속도 감소를 반영해 납품 예상을 재계산했습니다.',result['basic_text']['change_summary'])
        self.assertIn('기한 내 920대·부족 80대입니다.',result['basic_text']['customer_reply'])
        self.assertEqual('증가',facts['recent_defect_ratio']['direction'])
        self.assertEqual('동일',facts['transport_seconds']['direction'])

    def test_transport_change_does_not_claim_production_stock_or_rate_declined(self):
        result=build_explanation(self.comparison('transport2h'))
        facts={r['field']:r for r in result['reasons']}
        self.assertEqual((3600,7200,'초','증가'),tuple(facts['transport_seconds'][k]
            for k in ('before','after','unit','direction')))
        for field in ('good_rate_per_hour','currently_available_quantity','estimated_completion_at'):
            self.assertEqual('동일',facts[field]['direction'])
        self.assertEqual('운송시간 증가를 반영해 납품 예상을 재계산했습니다.',result['basic_text']['change_summary'])
        self.assertIn('기한 내 870대·부족 130대입니다.',result['basic_text']['customer_reply'])

    def test_five_cases_are_complete_bounded_and_null_is_not_zero(self):
        limits=dict(change_summary=60,customer_reply=80,dispatch_summary=45,confirmations=35)
        for case in CASES:
            with self.subTest(case=case):
                result=build_explanation(self.comparison(case));texts=result['basic_text']
                self.assertEqual('python',result['producer'])
                self.assertEqual(set(limits),set(texts))
                for field,text in texts.items():
                    self.assertTrue(text.endswith('.'));self.assertNotIn('\n',text)
                    self.assertLessEqual(len(text),limits[field])
                self.assertLessEqual(sum(map(len,texts.values())),220)
                self.assertTrue(texts['customer_reply'].startswith('가상 자료의 담당자 검토용·미발송·미등록 초안입니다.'))
                self.assertIn('조건부 예상',texts['customer_reply'])
                if case=='zero_rate11':
                    self.assertIn('미확인',texts['customer_reply']);self.assertNotIn('0대',texts['customer_reply'])
                    self.assertIn('0 또는 미확인',texts['change_summary'])
                elif case=='continued11':
                    self.assertIn('실적·조회 시각',texts['change_summary'])
                elif case=='combined11':
                    self.assertIn('정상완료 속도·운송시간',texts['change_summary'])

    def test_identical_inputs_and_missing_condition_or_metric_do_not_invent_changes(self):
        data=self.comparison();data['after']=deepcopy(data['before'])
        result=build_explanation(data)
        self.assertIn('변화가 없습니다',result['basic_text']['change_summary'])
        self.assertTrue(all(r['direction']=='동일' for r in result['reasons']))
        data['after']['production']=None
        data['after']['response_plan']['forecast']['condition']=None
        result=build_explanation(data)
        facts={r['field']:r for r in result['reasons']}
        self.assertIsNone(facts['good_rate_per_hour']['after'])
        self.assertEqual('미확인',facts['good_rate_per_hour']['direction'])
        self.assertIn('미확인',result['basic_text']['dispatch_summary'])
        self.assertIn('기한 내 960대·부족 40대입니다.',result['basic_text']['customer_reply'])

    def test_empty_available_quantity_is_zero_but_unknown_forecast_is_not_zero(self):
        data=self.comparison();data['after']['response_plan']['forecast'].update(on_time_quantity=0,shortfall_quantity=1000)
        text=build_explanation(data)['basic_text']['customer_reply']
        self.assertIn('기한 내 0대·부족 1000대입니다.',text)
        data['after']['response_plan']['forecast']['shortfall_quantity']=None
        self.assertIn('미확인',build_explanation(data)['basic_text']['customer_reply'])

    def test_non_synthetic_or_non_draft_or_executed_results_cannot_receive_demo_labels(self):
        for field,value in [('synthetic',False),('draft_only',False),('review_required',False),('execution_performed',True)]:
            data=self.comparison();data[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):build_explanation(data)

    def test_reversed_rate_and_other_condition_changes_use_values_not_case_names(self):
        data=self.comparison()
        data['before'],data['after']=data['after'],data['before']
        self.assertIn('속도 증가',build_explanation(data)['basic_text']['change_summary'])
        data=self.comparison('transport2h')
        condition=data['after']['response_plan']['forecast']['condition']
        condition.update(preparation_seconds=600,dispatch_mode='all_at_once')
        data['after']['production']['good_rate_per_hour']=80
        explanation=build_explanation(data)
        self.assertIn('일괄출고',explanation['basic_text']['dispatch_summary'])
        self.assertIn('준비시간',explanation['basic_text']['change_summary'])
        self.assertIn('출고방식',explanation['basic_text']['change_summary'])

    def test_missing_before_condition_does_not_hide_known_after_forecast(self):
        before=build_catalog()['nodes']+load_demo_contract()+load_demo_delivery()
        after=deepcopy(before)
        before=[n for n in before if n['type']!='DeliveryCondition']
        data=compare_response_plan(before,after,ROUND,AS_OF,AS_OF,
            before_version=2,after_version=3,queried_at=AS_OF)
        self.assertEqual('partially_unknown',data['comparison_status'])
        text=build_explanation(data)['basic_text']
        self.assertIn('기한 내 960대·부족 40대입니다.',text['customer_reply'])
        self.assertIn('전후 변화 확인',text['change_summary'])

    def test_mixed_forecast_known_even_when_direct_production_estimate_is_unknown(self):
        nodes=build_catalog()['nodes']+load_demo_contract()+load_demo_delivery(mixed=True)
        data=compare_response_plan(nodes,nodes,ROUND,AS_OF,AS_OF,
            before_version=2,after_version=2,queried_at=AS_OF)
        self.assertEqual('partially_unknown',data['comparison_status'])
        self.assertIn('기한 내 1000대·부족 0대입니다.',build_explanation(data)['basic_text']['customer_reply'])
