"""New-only Task09 continuation; scripted tests never call external APIs."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request


class LiveEntryTests(unittest.TestCase):
    def test_live_entry_rejects_unbudgeted_transport_before_database_or_api(self):
        from examples.materials_agent_workflow import run_example
        with self.assertRaisesRegex(ValueError, "budget-enforced"):
            run_example("unused", live=True)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.materials.api_budget'),'budget missing')
        from ontologics.materials.api_budget import BudgetedOpener
        self.cls=BudgetedOpener
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'budget.json'

    def request(self):
        return Request('https://api.openai.com/v1/responses',data=json.dumps(dict(model='gpt-5.4-mini-2026-03-17',max_output_tokens=4096,store=False,reasoning={'effort':'none'},input=[{'role':'user','content':'test'}],tools=[{'type':'function','name':'lookup'}])).encode())

    def transport(self,usage=None):
        from io import BytesIO
        class Response(BytesIO):
            status=200
            headers={}
        return lambda *a,**kw:Response(json.dumps({'usage':usage if usage is not None else {'input_tokens':1000,'output_tokens':100}}).encode())

    def test_usage_cost_persists_and_reopened_opener_obeys_call_cap(self):
        b=self.cls(self.path,open_fn=self.transport(),max_calls=2)
        for _ in range(2):
            with b(self.request(),timeout=1) as response: self.assertTrue(response.read())
        self.assertEqual(2,b.summary()['attempts']);self.assertEqual(2400000,b.summary()['accounted_nanodollars'])
        b=self.cls(self.path,open_fn=self.transport(),max_calls=2)
        with self.assertRaises(Exception) as caught:b(self.request(),timeout=1)
        self.assertEqual('API_BUDGET_EXHAUSTED',caught.exception.code)
        self.assertEqual(2,b.summary()['attempts'])

    def test_network_failures_and_missing_usage_keep_worst_case_reservation(self):
        def fail(*a,**kw):raise TimeoutError('secret must not be persisted')
        b=self.cls(self.path,open_fn=fail)
        with self.assertRaises(TimeoutError):b(self.request(),timeout=1)
        self.assertEqual(318432000,b.summary()['accounted_nanodollars'])
        b=self.cls(self.path,open_fn=self.transport({}))
        with b(self.request(),timeout=1) as r:r.read()
        self.assertEqual(636864000,b.summary()['accounted_nanodollars'])
        self.assertNotIn('secret',self.path.read_text())

    def test_money_cap_blocks_before_transport_and_cannot_raise_session_limits(self):
        b=self.cls(self.path,open_fn=self.transport({}),max_nanodollars=400000000)
        with b(self.request(),timeout=1) as r:r.read()
        with self.assertRaises(Exception) as caught:b(self.request(),timeout=1)
        self.assertEqual('API_BUDGET_EXHAUSTED',caught.exception.code)
        with self.assertRaises(ValueError):self.cls(self.path,max_calls=25)
        with self.assertRaises(ValueError):self.cls(self.path,max_nanodollars=2000000001)
        with self.assertRaises(ValueError):self.cls(self.path,max_nanodollars=2000000000)

    def test_wrong_model_endpoint_tools_or_nonfinite_usage_not_discounted(self):
        b=self.cls(self.path,open_fn=self.transport({'input_tokens':-1,'output_tokens':10}))
        req=self.request();body=json.loads(req.data);body['model']='other';req.data=json.dumps(body).encode()
        with self.assertRaises(ValueError):b(req,timeout=1)
        req=self.request();req.full_url='https://example.com'
        with self.assertRaises(ValueError):b(req,timeout=1)
        with b(self.request(),timeout=1) as r:r.read()
        self.assertEqual(318432000,b.summary()['accounted_nanodollars'])


from tests.materials.test_documents import DocumentPostgresTests as _Setup


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Explicit disposable PG required')
class WorkflowTests(unittest.TestCase):
    args=_Setup.args
    base_setup=_Setup.setUp

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.materials.agent_workflow'),'workflow missing')
        self.base_setup()
        from ontologics.materials import agent_workflow
        from ontologics.materials.host import MaterialsHost as CommonHost
        from ontologics.materials.document_store import BusinessDocumentWorkflow
        self.a=agent_workflow
        self.host=CommonHost(self.identities,self.settings,agent_registrations=(self.a.REGISTRATION,))
        self.store=BusinessDocumentWorkflow(self.host,self.workflow)

    def run_review(self,**kw):
        from examples.materials_agent_workflow import ScriptedProvider
        from ontologics.runtime import ModelConfig
        return self.a.run_review(self.host,self.workflow,self.store,self.author,
            calculation_arguments=kw.pop('calculation_arguments',self.args()),request_key='followup-documents',
            provider_factory=kw.pop('provider_factory',ScriptedProvider),config=ModelConfig(model='followup-fixture'),**kw)

    def test_conditional_documents_ready_with_explicit_followup_owners(self):
        before=self.workflow.get(self.author,self.saved['demand_id'])['data']
        r=self.run_review();self.assertTrue(r['accepted'],r['errors']);a=r['answer']
        self.assertEqual('review_ready',a['status']);self.assertTrue(a['conditional'])
        self.assertEqual((100,70,30),tuple(a['facts']['quantities'][k] for k in ('planned_input_quantity','initial_quantity','remaining_quantity')))
        self.assertEqual([],a['questions']);self.assertTrue(a['facts']['follow_up'])
        self.assertEqual('requesting_user',r['delivery']['recipient']);self.assertFalse(r['delivery']['transmitted'])
        self.assertEqual(before,self.workflow.get(self.author,self.saved['demand_id'])['data'])
        self.assertEqual(['configuration'],[t['name'] for t in r['runtime']['tool_trace']])
        self.assertEqual(3,len(a['documents']))

    def test_stored_contract_conflict_blocks_before_calculation_not_choose_a_number(self):
        from ontologics.service import Service
        original=Service.lookup
        def conflicting(service,actor,**kw):
            r=original(service,actor,**kw)
            if kw.get('kind')=='ContractItem' and r['ok']:
                r=deepcopy(r);r['data'][0]['quantity']=120
            return r
        with patch.object(Service,'lookup',conflicting),patch.object(self.store,'create_documents',side_effect=AssertionError('must not calculate')):
            r=self.run_review()
        self.assertEqual('awaiting_confirmation',r['answer']['status']);self.assertEqual('data_check',r['next_action'])
        issue=r['answer']['questions'][0]
        self.assertEqual('STORED_FACT_CONFLICT',issue['code']);self.assertEqual((100,120),(issue['source_value'],issue['stored_value']))
        self.assertEqual('data_owner',issue['owner']);self.assertIn('점검',issue['action'])
        self.assertEqual({},r['answer']['facts']);self.assertIsNone(r['runtime'])

    def test_stale_public_version_or_cancel_never_creates_documents(self):
        args=self.args();args['data_version']=2
        self.assertFalse(self.run_review(calculation_arguments=args)['accepted'])
        r=self.run_review(cancelled=True);self.assertEqual('CANCELLED',r['errors'][0])

    def test_model_cannot_change_facts_or_call_calculation_tool(self):
        from examples.materials_agent_workflow import ScriptedProvider
        def factory(task,tools):
            p=ScriptedProvider(task,tools)
            def bad(**kw):
                out=p(**kw)
                if not out['tool_calls']:
                    a=json.loads(out['text']);a['facts']['quantities']['initial_quantity']=100;out['text']=json.dumps(a)
                return out
            return bad
        self.assertFalse(self.run_review(provider_factory=factory)['accepted'])
        f=lambda *a:lambda **kw:dict(text='',tool_calls=[dict(name='create_documents',arguments={})])
        r=self.run_review(provider_factory=f);self.assertEqual('TOOL_NOT_ALLOWED',r['errors'][0]);self.assertEqual(3,len(r['answer']['documents']))

    def test_resume_reads_existing_confirmation_and_rejects_revocation(self):
        from tests.materials.test_demand_confirmation import SCOPES
        from ontologics.identity import DevelopmentContext,UserIdentity
        self.assertEqual('CONFIRMATION_REQUIRED',self.run_review(resume_scopes=SCOPES)['errors'][0])
        self.assertTrue(self.workflow.confirm(DevelopmentContext('reviewer'),self.saved['demand_id'],1,
            expected_target_sha256=self.saved['target_sha256'],scopes=SCOPES,note='test-only fixture',request_key='followup-confirm')['ok'])
        r=self.run_review(resume_scopes=SCOPES);self.assertTrue(r['accepted'],r['errors'])
        self.identities.users=[u if u.subject!='reviewer' else UserIdentity('reviewer',frozenset({'read'})) for u in self.identities.users]
        self.assertEqual('CONFIRMATION_REQUIRED',self.run_review(resume_scopes=SCOPES)['errors'][0])

    def test_interpretation_uses_existing_parser_same_role_no_automatic_confirmation(self):
        from ontologics.runtime import ModelConfig
        p=lambda **kw:dict(text=json.dumps(self.interpreted),tool_calls=[])
        r=self.a.interpret_source(self.host,self.author,self.path,data_version=1,provider=p,config=ModelConfig(model='fixture',max_steps=1,max_attempts=1))
        self.assertTrue(r['accepted'],r['errors']);self.assertEqual(100,r['report']['items'][0]['fields']['ordered_quantity']['value'])
        self.assertFalse(r['human_confirmation']);self.assertEqual('materials_review',self.a.REGISTRATION.name)

    def test_real_pg_stored_conflict_example_has_no_documents_or_model_review(self):
        from examples.materials_agent_workflow import run_example
        with tempfile.TemporaryDirectory() as folder:
            r=run_example(folder,scenario='stored_conflict')
            self.assertFalse(r['accepted']);self.assertEqual([],r['exports'])
            self.assertIn('STORED_FACT_CONFLICT',r['interpretation']['errors'])
            self.assertNotIn('review',r)

    def test_contract_claim_conflict_requests_source_inspection(self):
        from ontologics.runtime import ModelConfig
        # Both quantities have exact source quotes in this separate temporary source.
        from ontologics.materials.contract_intake import extract_contract_text
        from examples.materials_contract_intake import reference_interpretation
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'conflict.txt';p.write_text(self.path.read_text()+'\n주문 수량: 120 piece\n')
            ext=extract_contract_text(p);v=deepcopy(self.interpreted);v['source_sha256']=ext['source']['sha256']
            seg=ext['segments'][-1];start=seg['text'].index('120')
            v['items'][0]['fields']['ordered_quantity'].append(dict(value=120,evidence=[dict(segment_id=seg['id'],quote='120',start=start,end=start+3)]))
            r=self.a.interpret_source(self.host,self.author,p,data_version=1,provider=lambda **kw:dict(text=json.dumps(v),tool_calls=[]),config=ModelConfig(model='fixture',max_steps=1))
        self.assertFalse(r['accepted']);self.assertEqual('data_check',r['next_action'])
        self.assertTrue(any(i['code']=='SOURCE_FIELD_UNRESOLVED' for i in r['data_checks']))


del _Setup
