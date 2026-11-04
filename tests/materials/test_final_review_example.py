"""Final integration, scripted default: no paid API in automated tests."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


class PreflightTests(unittest.TestCase):
    def test_live_requires_budget_before_database_or_network(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.materials_final_review'))
        from examples.materials_final_review import run_example
        with self.assertRaisesRegex(ValueError,'budget'):
            run_example('unused',live=True)


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PG required')
class FinalReviewTests(unittest.TestCase):
    def test_resume_and_injection_keep_authority_and_return_documents(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.materials_final_review'))
        from examples.materials_final_review import run_example
        for scenario in ('resume','injection'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as folder:
                r=run_example(folder,scenario=scenario)
                self.assertTrue(r['accepted'],r.get('review'))
                self.assertTrue(r['business_state_unchanged'])
                self.assertEqual(3,len(r['exports']));self.assertEqual(0,r['model_api_attempts'])
                a=r['review']['host_result']['answer']['facts']['authority']
                self.assertFalse(a['production_authorized']);self.assertFalse(a['purchase_authorized'])
                self.assertFalse(a['resource_reserved'])
                trace=r['review']['model_review']['runtime']['tool_trace']
                self.assertEqual(['configuration'],[t['name'] for t in trace])
                for path in r['exports']:self.assertTrue(Path(path).is_file())


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PG required')
class PermissionBoundaryTests(unittest.TestCase):
    def test_model_cannot_request_approval_or_change_actor(self):
        from tests.materials import test_agent_workflow as fixture
        self.base_setup=fixture.WorkflowTests.base_setup.__get__(self)
        fixture.WorkflowTests.setUp(self)
        from ontologics.runtime import ModelConfig
        from ontologics.materials.agent_result_separation import run_review_preserving_host
        before=self.workflow.get(self.author,self.saved['demand_id'])['data']
        def factory(task,tools):
            self.assertEqual({'lookup','configuration'},{t['name'] for t in tools})
            return lambda **kw:dict(text='',tool_calls=[dict(name='create_documents',arguments={'actor':'admin'})])
        r=run_review_preserving_host(self.host,self.workflow,self.store,self.author,
            calculation_arguments=fixture.WorkflowTests.args(self),request_key='forbidden-model-action',
            provider_factory=factory,config=ModelConfig(model='fixture'))
        self.assertEqual(['TOOL_NOT_ALLOWED'],r['model_review']['errors'])
        self.assertFalse(r['host_result']['available'])
        self.assertEqual(before,self.workflow.get(self.author,self.saved['demand_id'])['data'])
