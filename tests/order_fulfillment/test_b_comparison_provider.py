"""Free HTTP doubles for the exact CQ06 provider/replay/output boundary."""
from copy import deepcopy
import json
import unittest

from examples.order_fulfillment import b_comparison_agent as cq
from ontologics.order_fulfillment.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings, ProviderError
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test
from tests.test_openai_provider import SECRET, Transport, response, text, tool

READER=UserIdentity('cq06-http-reader',frozenset({'read'}))


class ComparisonProviderTests(unittest.TestCase):
    def fixture(self,case='defects11'):
        database=database_for_test(self)
        fixture,task=cq.setup(database,case=case)
        try:plan=cq.query(fixture,task)
        finally:fixture.close()
        audit=Service(database,principals={READER.actor:READER.principal()});self.addCleanup(audit.close)
        host=CommonHost(DevelopmentIdentityProvider([READER],enabled=True),database,
            agent_registrations=(AgentRegistration('b_comparison',cq.PROMPT,cq.TOOLS),))
        calls=[tool('compare_response_plan',cq.query_arguments(task),'comparison_original'),
            *[tool('lookup_many',dict(node_ids=plan['data']['evidence_by_side'][side],kind='Evidence',
                version=task[side+'_version']),side+'_original') for side in ('before','after')]]
        return task,plan,calls,audit,host

    def run_host(self,host,task,provider):
        guard=cq.ProvenanceProvider(provider,task)
        with host.request(DevelopmentContext(READER.subject)) as session:
            result=session.run_agent('b_comparison',guard,[{'role':'user','content':canonical(task)}],
                config=cq.CONFIG,capture_tool_trace=True)
        return result,guard

    def test_original_calls_reasoning_and_versions_replay_with_strict_final_schema(self):
        for case in ('defects11','transport2h'):
            with self.subTest(case=case):
                task,plan,calls,audit,host=self.fixture(case)
                reasoning=[dict(type='reasoning',id='rs_cq06_'+str(i),encrypted_content='opaque_'+str(i),summary=[]) for i in range(3)]
                final=canonical(cq.scripted_answer(plan))
                transport=Transport([response([reasoning[i],call]) for i,call in enumerate(calls)]+[response([text(final)])])
                provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
                result,guard=self.run_host(host,task,provider)
                self.assertTrue(result['ok'],result['error'])
                checks,_=cq.evaluate(audit,task,result,task['after_version'],actor=READER.actor)
                self.assertTrue(all(checks.values()),checks)
                self.assertEqual(final,result['text']);self.assertEqual(final,guard.final_submission['text'])
                self.assertEqual(4,len(provider.calls));self.assertEqual(4,len(transport.requests))
                self.assertEqual(['compare_response_plan','lookup_many','lookup_many'],[t['name'] for t in result['tool_trace']])
                self.assertEqual([task['before_version'],task['after_version']],
                    [t['arguments']['version'] for t in result['tool_trace'][1:]])
                original=deepcopy(result['tool_trace'][0]['result']);compact=cq.compact_response(original)
                self.assertEqual(original,result['tool_trace'][0]['result'])
                self.assertLess(len(canonical(compact).encode()),len(canonical(original).encode()))
                for i,(request,timeout) in enumerate(transport.requests):
                    payload=json.loads(request.data)
                    self.assertEqual(1024,payload['max_output_tokens']);self.assertEqual(30,timeout)
                    self.assertIs(False,payload['parallel_tool_calls']);self.assertIs(False,payload['store'])
                    self.assertEqual({'effort':'none'},payload['reasoning'])
                    self.assertEqual(['reasoning.encrypted_content'],payload['include'])
                    self.assertEqual('required' if i<3 else 'none',payload['tool_choice'])
                    if i<3:
                        self.assertEqual(1,len(payload['tools']));spec=payload['tools'][0]
                        self.assertTrue(spec['strict']);self.assertEqual(calls[i]['name'],spec['name'])
                        arguments=json.loads(calls[i]['arguments']);properties=spec['parameters']['properties']
                        self.assertEqual(set(arguments),set(properties));self.assertFalse(spec['parameters']['additionalProperties'])
                        for key,value in arguments.items():
                            if key=='node_ids':
                                self.assertEqual(sorted(value),properties[key]['items']['enum'])
                                self.assertEqual(len(value),properties[key]['minItems']);self.assertEqual(len(value),properties[key]['maxItems'])
                            else:self.assertEqual([value],properties[key]['enum'])
                    else:
                        self.assertEqual([],payload['tools'])
                        fmt=payload['text']['format'];self.assertTrue(fmt['strict'])
                        self.assertEqual(cq.ANSWER_FIELDS,set(fmt['schema']['properties']))
                        self.assertFalse(fmt['schema']['additionalProperties'])
                        for key in cq.TEXT_FIELDS:
                            self.assertNotIn('pattern',fmt['schema']['properties'][key])
                            self.assertEqual('string',fmt['schema']['properties'][key]['type'])
                            self.assertEqual(cq.FINAL_ANSWER_FORMAT['schema']['properties'][key]['description'],
                                             fmt['schema']['properties'][key]['description'])
                            self.assertEqual([cq.scripted_answer(plan)[key]],fmt['schema']['properties'][key]['enum'])
                            self.assertNotIn('maxLength',fmt['schema']['properties'][key])
                        self.assertIn(cq.FINAL_TEXT_INSTRUCTIONS,payload['input'][-1]['content'])
                        for side in ('before','after'):
                            self.assertEqual(set(cq.SIDE_FIELDS),set(fmt['schema']['properties'][side]['properties']))
                    replay_calls=[x for x in payload['input'] if x.get('type')=='function_call']
                    replay_reason=[x for x in payload['input'] if x.get('type')=='reasoning']
                    outputs=[x for x in payload['input'] if x.get('type')=='function_call_output']
                    self.assertEqual(calls[:i],replay_calls);self.assertEqual(reasoning[:i],replay_reason)
                    self.assertEqual([x['call_id'] for x in calls[:i]],[x['call_id'] for x in outputs])
                    if outputs:self.assertEqual(compact,json.loads(outputs[0]['output']))
                with self.assertRaises(ProviderError) as found:provider(model=MODEL,messages=[])
                self.assertEqual('LIVE_BUDGET_EXHAUSTED',found.exception.code);self.assertEqual(4,len(transport.requests))
                self.assertNotIn(SECRET,canonical(provider.calls)+canonical(provider.public_config()))

    def test_extra_final_key_is_preserved_and_independently_rejected(self):
        task,plan,calls,audit,host=self.fixture()
        candidate=cq.scripted_answer(plan);candidate['version']=task['after_version'];raw=canonical(candidate)
        transport=Transport([response([c]) for c in calls]+[response([text(raw)])])
        provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
        result,guard=self.run_host(host,task,provider)
        self.assertTrue(result['ok']);self.assertEqual(raw,result['text']);self.assertEqual(raw,guard.final_submission['text'])
        checks,answer=cq.evaluate(audit,task,result,task['after_version'],actor=READER.actor)
        self.assertFalse(checks['structured_answer']);self.assertFalse(all(checks.values()));self.assertEqual(candidate,answer)
        self.assertEqual(4,len(transport.requests))

    def test_short_but_incorrect_prose_is_rejected_and_raw_submission_is_preserved(self):
        task,plan,calls,_audit,host=self.fixture()
        candidate=cq.scripted_answer(plan)
        candidate['customer_reply']='가상 자료의 담당자 검토용·미발송·미등록 초안입니다. 조건부 예상으로, 양품 감소와 동일한 분할출고·운송조건에서'
        raw=canonical(candidate)
        transport=Transport([response([c]) for c in calls]+[response([text(raw)])])
        provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
        result,guard=self.run_host(host,task,provider)
        self.assertFalse(result['ok']);self.assertEqual('CQ06_BASIC_TEXT',result['error']['code'])
        self.assertEqual(raw,guard.final_submission['text'])
        self.assertFalse(guard.final_submission['basic_text_accepted'])
        self.assertEqual(4,len(transport.requests))

    def test_unbound_final_schema_stops_before_http(self):
        transport=Transport([])
        provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
        provider.tools=[]
        with self.assertRaises(ProviderError) as found:
            provider(model=MODEL,messages=[{'role':'user','content':'final'}])
        self.assertEqual('CQ06_BASIC_TEXT_BINDING',found.exception.code)
        self.assertEqual([],transport.requests)

    def test_timeout_incomplete_and_refusal_stop_without_retry_or_tool_execution(self):
        for item in (TimeoutError(),response([],status='incomplete'),
                     response([{'type':'message','role':'assistant','content':[{'type':'refusal','refusal':'No'}]}])):
            with self.subTest(item=type(item).__name__):
                task,_plan,_calls,_audit,host=self.fixture()
                transport=Transport([item]);provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
                result,guard=self.run_host(host,task,provider)
                self.assertFalse(result['ok']);self.assertEqual([],result['tool_trace']);self.assertIsNone(guard.final_submission)
                self.assertEqual(1,len(transport.requests));self.assertEqual(1,len(provider.calls))

    def test_failed_http_requests_also_consume_four_request_budget(self):
        transport=Transport([TimeoutError() for _ in range(4)])
        provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
        for _ in range(4):
            with self.assertRaises(TimeoutError):provider(model=MODEL,messages=[{'role':'user','content':'test'}])
        with self.assertRaises(ProviderError) as found:provider(model=MODEL,messages=[])
        self.assertEqual('LIVE_BUDGET_EXHAUSTED',found.exception.code)
        self.assertEqual(4,len(transport.requests));self.assertTrue(all(x['status']=='failed' for x in provider.calls))
