"""Host-owned output survives rejected model copies, never stale context."""
import importlib.util
import json
import os
import unittest
from unittest.mock import patch
from tests.production_planning import test_agent_workflow as setup
from examples.production_planning_agent_workflow import ScriptedProvider
from ontologics.runtime import ModelConfig


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Disposable PG required')
class ResultSeparationTests(unittest.TestCase):
    args=setup.WorkflowTests.args
    base_setup=setup.WorkflowTests.base_setup

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.agent_result_separation'))
        setup.WorkflowTests.setUp(self)
        from ontologics.production_planning.agent_result_separation import run_review_preserving_host
        self.entry=run_review_preserving_host

    def run_case(self, mutate=None, after=None, **kwargs):
        def factory(task, tools):
            p=ScriptedProvider(task,tools)
            def call(**kw):
                out=p(**kw)
                if not out['tool_calls']:
                    if mutate:
                        answer=json.loads(out['text']);mutate(answer);out['text']=json.dumps(answer)
                    if after:after()
                return out
            return call
        return self.entry(self.host,self.workflow,self.store,self.author,
            calculation_arguments=self.args(),request_key='separated-docs',provider_factory=factory,
            config=ModelConfig(model='separation-fixture'),**kwargs)

    def test_missing_null_field_preserves_host_and_rejects_model(self):
        r=self.run_case(lambda a:a['facts']['schedule']['remaining'].pop('processing_start'))
        self.assertTrue(r['host_result']['available']);self.assertFalse(r['model_review']['accepted'])
        self.assertIsNone(r['host_result']['answer']['facts']['schedule']['remaining']['processing_start'])
        self.assertNotIn('processing_start',json.loads(r['model_review']['runtime']['text'])['facts']['schedule']['remaining'])
        self.assertEqual('host_result_available_model_rejected',r['status'])
        self.assertEqual(['PLANNING_FACT_MISMATCH'],r['model_review']['errors'])

    def test_wrong_quantity_never_becomes_host_fact(self):
        r=self.run_case(lambda a:a['facts']['quantities'].update(initial_quantity=120))
        self.assertTrue(r['host_result']['available']);self.assertFalse(r['model_review']['accepted'])
        self.assertEqual(70,r['host_result']['answer']['facts']['quantities']['initial_quantity'])

    def test_valid_model_is_separate_from_host_answer(self):
        r=self.run_case();self.assertTrue(r['model_review']['accepted']);self.assertTrue(r['host_result']['available'])
        self.assertEqual('host_workflow',r['host_result']['origin'])
        self.assertNotIn('runtime',r['host_result'])

    def test_cancel_or_confirmation_missing_cannot_be_recovered(self):
        r=self.run_case(cancelled=True);self.assertFalse(r['host_result']['available'])
        r=self.run_case(resume_scopes=[dict(item_id='contract-item:planning:001',scope='ordered_quantity')])
        self.assertFalse(r['host_result']['available'])

    def test_changed_current_context_after_bad_model_cannot_be_recovered(self):
        from ontologics.production_planning import agent_result_separation as module
        original=module._snapshot
        calls=[]
        def changing(*args,**kwargs):
            calls.append(1)
            if len(calls)>1:raise ValueError('DOCUMENT_STALE')
            return original(*args,**kwargs)
        with patch.object(module,'_snapshot',side_effect=changing):
            r=self.run_case(lambda a:a['facts']['schedule']['remaining'].pop('processing_start'))
        self.assertEqual(2,len(calls))
        self.assertFalse(r['host_result']['available']);self.assertEqual({},r['host_result']['answer'])

    def test_resume_uses_actual_confirmation_then_retains_host_result(self):
        from ontologics.identity import DevelopmentContext
        scopes=[dict(item_id='contract-item:planning:001',scope='ordered_quantity')]
        self.assertTrue(self.workflow.confirm(DevelopmentContext('reviewer'),self.saved['demand_id'],1,
            expected_target_sha256=self.saved['target_sha256'],scopes=scopes,note='test-only fixture',request_key='separation-confirm')['ok'])
        r=self.run_case(lambda a:a['facts']['schedule']['remaining'].pop('processing_start'),resume_scopes=scopes)
        self.assertTrue(r['host_result']['available']);self.assertFalse(r['model_review']['accepted'])

    def test_stored_conflict_blocks_before_calculation_and_model(self):
        from copy import deepcopy
        from ontologics.service import Service
        original=Service.lookup
        def conflict(service,actor,**kwargs):
            r=original(service,actor,**kwargs)
            if kwargs.get('kind')=='ContractItem' and r['ok']:
                r=deepcopy(r);r['data'][0]['quantity']=120
            return r
        with patch.object(Service,'lookup',conflict),patch.object(self.store,'create_documents',side_effect=AssertionError('must not calculate')):
            r=self.run_case()
        self.assertFalse(r['host_result']['available']);self.assertEqual('data_check',r['model_review']['next_action'])
        self.assertIsNone(r['model_review']['runtime'])
