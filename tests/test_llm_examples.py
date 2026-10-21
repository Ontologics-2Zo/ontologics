"""Offline regression checks for the example evaluator, not LLM quality scores."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from examples.llm_agents import (evaluate_product, evaluate_quality, example_principals,
    quality_task, run_role, setup_product, _json_answer)
from ontologics.demo import LOT, must, run_demo
from ontologics.openai_provider import OpenAISettings
from ontologics.service import Service


class ExampleChecks(unittest.TestCase):
    def test_malformed_answer_types_fail_without_raising(self):
        for role in ('product','quality'):
            for answer in ('null','[]','{}','not json', '{"answers":null}', '{"unknowns":[null]}'):
                with self.subTest(role=role,answer=answer):
                    self.assertIsNone(_json_answer(answer,role))

    def test_failed_model_answer_preserves_role_report_and_runtime(self):
        class InvalidAnswer:
            calls=[]
            def __call__(self,**_):return {'text':'{"answers":null}','tool_calls':[]}
        with tempfile.TemporaryDirectory() as temp:
            report=run_role('product',OpenAISettings('fake-test-key'),Path(temp),
                provider_factory=lambda *_:InvalidAnswer(),execution_mode='test_double')
        self.assertTrue(report['runtime']['ok'])
        self.assertFalse(report['evaluation']['automatic_pass'])
        self.assertIsNone(report['answer'])
        self.assertEqual([],report['evidence_details'])
        self.assertFalse(report['integration']['api_completed'])
        self.assertEqual('completed',report['recorded_runtime']['status'])

    def test_product_draft_evidence_and_unknown_checks_use_real_common_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            service,selected=setup_product(Path(temp)/'product.sqlite')
            try:
                args={'operations':[{'op':'put','node':n} for n in selected],'request_key':'test-example'}
                prepared=must(service.prepare_draft('product-agent',**args))
                d=prepared['data']; validation={'draft_id':d['draft_id'],'version':d['draft_version']}
                checked=must(service.validate_draft('product-agent',**validation))
                answer={'draft_id':d['draft_id'],'draft_version':d['draft_version'],'summary':'검토 대기',
                    'evidence':[e for n in selected for e in n['evidence']],
                    'unknowns':[{'node_id':n['id'],'field':'population','value':'unknown','reason':'원본 NA'} for n in selected if n['population']=='unknown'],
                    'needs_human_review':True}
                result={'ok':True,'text':json.dumps(answer),'tool_trace':[
                    {'name':'prepare_draft','arguments':args,'result':prepared},
                    {'name':'validate_draft','arguments':validation,'result':checked}]}
                self.assertTrue(all(evaluate_product(service,selected,result,1)[0].values()))
                answer['unknowns']=[];answer['evidence'].append('evidence:invented')
                result['text']=json.dumps(answer)
                checks,_=evaluate_product(service,selected,result,1)
                self.assertFalse(checks['unknown_population_explicit'])
                self.assertFalse(checks['answer_cites_source_rows'])
                self.assertEqual(1,service.lookup('product-agent')['data_version'])
            finally:service.close()

    def test_quality_checks_reject_fabricated_citation_and_changed_time(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'quality.sqlite'
            summary,_=run_demo(db)
            service=Service(db,principals=example_principals())
            try:
                task=quality_task(summary['registered_version'],summary['supplemented_version'])
                trace=[]
                for q in task['queries'].values():
                    args={'query':q,'before_version':2,'after_version':3}
                    trace.append({'name':'compare_investigation','arguments':copy.deepcopy(args),
                        'result':must(service.compare_investigation('quality-agent',**args))})
                lot=must(service.lookup('quality-agent',node_id=LOT))['data'][0]
                args={'component_id':lot['component_id'],'version':3}
                trace.append({'name':'component_usage','arguments':args,
                    'result':must(service.component_usage('quality-agent',**args))})
                answer={'unit_id':'unit:U104','lot_id':LOT,'before_version':2,'after_version':3,
                    'answers':[{'mode':mode,'before':'needs_confirmation','after':after,
                        'explanation':'장착 이력 조건이며 안전·가동 판단 아님',
                        'evidence':['evidence:demo:initial','evidence:demo:supplement']}
                        for mode,after in [('A','excluded'),('B','included')]],
                    'real_design_evidence':['evidence:olimex-L-wroom:row-55'],
                    'synthetic_operations':True,'not_established':['failure_cause','safety','operating_time','cross_revision_component_equivalence'],
                    'limitations':['가상 운영 기록']}
                result={'ok':True,'text':json.dumps(answer),'tool_trace':trace}
                self.assertTrue(all(evaluate_quality(service,task,result,3)[0].values()))
                answer['answers'][0]['evidence'].append('evidence:invented')
                result['text']=json.dumps(answer)
                self.assertFalse(evaluate_quality(service,task,result,3)[0]['operations_evidence_cited'])
                trace[0]['arguments']['query']['at']='2026-09-20T11:00:00+09:00'
                checks,_=evaluate_quality(service,task,result,3)
                self.assertFalse(checks['A_exact_conditions_and_versions'])
                self.assertFalse(checks['all_comparisons_match_requested_conditions'])
                answer['answers']=None;result['text']=json.dumps(answer)
                self.assertFalse(evaluate_quality(service,task,result,3)[0]['structured_answer'])
            finally:service.close()


if __name__=='__main__':unittest.main()
