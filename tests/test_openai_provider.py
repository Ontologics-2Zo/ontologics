import io
import json
import tempfile
import unittest
from tests.db import database_for_test
from pathlib import Path
from urllib.error import HTTPError, URLError

from ontologics.openai_provider import MODEL, ENDPOINT, OpenAIProvider, OpenAISettings, ProviderError
from ontologics.runtime import ModelRuntime, ModelConfig
from ontologics.service import Service
from ontologics.tool_specs import function_tools, PRODUCT_TOOLS, QUALITY_TOOLS

SECRET = 'test-secret-not-a-real-key'

def response(output, **fields):
    return {'id':'resp_test','model':MODEL,'status':'completed','output':output,
            'usage':{'input_tokens':4,'output_tokens':3,'total_tokens':7},**fields}

def tool(name='lookup',args=None,cid='call_a'):
    return {'type':'function_call','name':name,'call_id':cid,'arguments':json.dumps(args or {'node_id':'sample'})}

def text(value='done'):
    return {'type':'message','role':'assistant','content':[{'type':'output_text','text':value}]}

class Reply:
    def __init__(self,body): self.body=body; self.status=200; self.headers={'x-request-id':'req_test'}
    def read(self,n): return self.body[:n]
    def __enter__(self): return self
    def __exit__(self,*_): pass

class Transport:
    def __init__(self,items): self.items=list(items);self.requests=[]
    def __call__(self,req,timeout):
        self.requests.append((req,timeout))
        item=self.items.pop(0)
        if isinstance(item,Exception): raise item
        return Reply(json.dumps(item).encode())

class ProviderTests(unittest.TestCase):
    def provider(self,items,tools=('lookup',)):
        transport=Transport(items)
        return OpenAIProvider(OpenAISettings(SECRET),tools,opener=transport),transport

    def test_fixed_endpoint_configuration_and_no_secret_in_public_metadata(self):
        p,t=self.provider([response([text()])])
        self.assertEqual({'text':'done','tool_calls':[]},p(model=MODEL,messages=[{'role':'user','content':'hi'}]))
        req,timeout=t.requests[0];payload=json.loads(req.data)
        self.assertEqual(ENDPOINT,req.full_url)
        self.assertEqual('Bearer '+SECRET,req.get_header('Authorization'))
        self.assertEqual(MODEL,payload['model']);self.assertFalse(payload['store'])
        self.assertEqual({'effort':'none'},payload['reasoning'])
        self.assertFalse(payload['parallel_tool_calls'])
        self.assertEqual(30,timeout)
        self.assertNotIn(SECRET,repr(p.settings)+json.dumps(p.public_config())+json.dumps(p.calls))

    def test_replays_exact_reasoning_and_original_multiple_call_ids(self):
        output=[{'type':'reasoning','id':'rs_a','encrypted_content':'encrypted','summary':[]},tool(cid='call_first'),tool(args={'node_id':'second'},cid='call_second')]
        p,t=self.provider([response(output),response([text('final')])])
        initial=[{'role':'system','content':'instruction'},{'role':'user','content':'question'}]
        first=p(model=MODEL,messages=initial)
        history=initial+[{'role':'assistant','content':first['text'],'tool_calls':first['tool_calls']},
            {'role':'tool','name':'lookup','call_index':0,'content':'{"ok":true}'},
            {'role':'tool','name':'lookup','call_index':1,'content':'{"ok":true}'}]
        p(model=MODEL,messages=history)
        actual=json.loads(t.requests[1][0].data)['input']
        self.assertEqual(output,actual[2:5])
        self.assertEqual(['call_first','call_second'],[x['call_id'] for x in actual[5:]])
        history[-1]['call_index']=0
        with self.assertRaises(ProviderError) as e:p(model=MODEL,messages=history)
        self.assertEqual('OPENAI_HISTORY_INVALID',e.exception.code)
        self.assertEqual(2,len(t.requests))

    def test_incomplete_response_does_not_release_partial_tool_calls(self):
        p,_=self.provider([response([tool()],status='incomplete')])
        with self.assertRaises(ProviderError) as e:p(model=MODEL,messages=[])
        self.assertEqual('OPENAI_RESPONSE_INCOMPLETE',e.exception.code)
        self.assertEqual(7, p.calls[0]['usage']['total_tokens'])

    def test_usage_details_are_subsets_and_untrusted_metadata_is_not_logged(self):
        usage = {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15,
                 'input_tokens_details': {'cached_tokens': 4, 'secret': SECRET},
                 'output_tokens_details': {'reasoning_tokens': 2, 'secret': SECRET}}
        p, _ = self.provider([response([text()], usage=usage)])
        p(model=MODEL, messages=[])
        actual = p.calls[0]['usage']
        self.assertEqual(15, actual['total_tokens'])
        self.assertEqual({'cached_tokens': 4}, actual['input_tokens_details'])
        self.assertEqual({'reasoning_tokens': 2}, actual['output_tokens_details'])
        self.assertNotIn(SECRET, json.dumps(p.calls))

    def test_unexposed_tool_and_model_switch_are_rejected(self):
        p,t=self.provider([response([tool(name='apply')])])
        with self.assertRaises(ProviderError):p(model='other',messages=[])
        self.assertFalse(t.requests)
        with self.assertRaises(ProviderError) as e:p(model=MODEL,messages=[])
        self.assertEqual('TOOL_NOT_ALLOWED',e.exception.code)

    def test_invalid_arguments_duplicate_calls_and_missing_reasoning_state_rejected(self):
        cases=[([tool(args=[])],'OPENAI_ARGUMENTS_INVALID'),
               ([tool(),tool()],'OPENAI_RESPONSE_INVALID'),
               ([{'type':'reasoning','summary':[]}],'OPENAI_REASONING_STATE_MISSING')]
        bad=tool();bad['arguments']='{"quantity":NaN}'
        cases.append(([bad],'OPENAI_ARGUMENTS_INVALID'))
        # tool(args=[]) helper defaults to dict, create explicit array argument.
        cases[0][0][0]['arguments']='[]'
        for output,code in cases:
            with self.subTest(code=code):
                p,_=self.provider([response(output)])
                with self.assertRaises(ProviderError) as e:p(model=MODEL,messages=[])
                self.assertEqual(code,e.exception.code)

    def test_http_errors_redacted_and_classified(self):
        for status,expected in [(401,'OPENAI_AUTH_FAILED'),(403,'OPENAI_ACCESS_DENIED'),(404,'OPENAI_MODEL_UNAVAILABLE'),(400,'OPENAI_REQUEST_REJECTED')]:
            p,_=self.provider([HTTPError(ENDPOINT,status,SECRET,{'x-request-id':'req_fail'},io.BytesIO(SECRET.encode()))])
            with self.assertRaises(ProviderError) as e:p(model=MODEL,messages=[])
            self.assertEqual(expected,e.exception.code)
            self.assertNotIn(SECRET,str(e.exception)+json.dumps(p.calls))
        for status in (429,500):
            p,_=self.provider([HTTPError(ENDPOINT,status,SECRET,{},io.BytesIO(SECRET.encode()))])
            with self.assertRaises(ConnectionError):p(model=MODEL,messages=[])
            self.assertNotIn(SECRET,json.dumps(p.calls))

    def test_network_errors_do_not_echo_credentials(self):
        for exc,kind in [(URLError(SECRET),ConnectionError),(TimeoutError(SECRET),TimeoutError)]:
            p,_=self.provider([exc])
            with self.assertRaises(kind) as e:p(model=MODEL,messages=[])
            self.assertNotIn(SECRET,str(e.exception)+json.dumps(p.calls))

    def test_malformed_content_and_usage_end_in_recorded_safe_failure(self):
        for body in (response([{'type':'message','content':None}]),
                     response([{'type':'message','content':['invalid']}]),
                     response([text()], usage=None)):
            p,_=self.provider([body])
            with self.assertRaises(ProviderError) as e:p(model=MODEL,messages=[])
            self.assertEqual('OPENAI_RESPONSE_INVALID',e.exception.code)
            self.assertEqual('failed',p.calls[0]['status'])
            self.assertEqual('OPENAI_RESPONSE_INVALID',p.calls[0]['error_code'])

    def test_environment_is_data_not_shell_and_environment_overrides_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'.env.local'
            path.write_text('OPENAI_API_KEY="local-fake"\nOPENAI_TIMEOUT_SECONDS=12\n')
            s=OpenAISettings.from_environment(path,{'OPENAI_API_KEY':SECRET})
            self.assertEqual(SECRET,s.api_key);self.assertEqual(12,s.timeout_seconds)
            path.write_text('UNKNOWN_SECRET='+SECRET)
            with self.assertRaises(ProviderError) as e:OpenAISettings.from_environment(path,{})
            self.assertNotIn(SECRET,str(e.exception))
        with self.assertRaises(ProviderError) as e:OpenAISettings.from_environment(environ={})
        self.assertEqual('OPENAI_KEY_MISSING',e.exception.code)

    def test_safe_provider_error_code_and_config_persist_in_runtime(self):
        p,t=self.provider([HTTPError(ENDPOINT,401,SECRET,{},io.BytesIO(SECRET.encode()))])
        service=Service(database_for_test(self), principals={'agent':{'kind':'agent','roles':['read']}})
        try:
            result=ModelRuntime(service,'agent',p,config=ModelConfig(model=MODEL)).run([{'role':'user','content':'hi'}])
            self.assertEqual('OPENAI_AUTH_FAILED',result['error']['code'])
            self.assertEqual(1,len(t.requests))
            rows=[dict(r) for r in service.db.execute('SELECT * FROM runtime_runs')]
            self.assertEqual(ENDPOINT,json.loads(rows[0]['config'])['provider']['endpoint'])
            self.assertNotIn(SECRET,json.dumps(rows)+json.dumps(result))
        finally:service.close()

    def test_transient_retry_is_bounded_by_existing_runtime(self):
        p,t=self.provider([TimeoutError(),response([text()])])
        service=Service(database_for_test(self), principals={'agent':{'kind':'agent','roles':['read']}})
        try:
            result=ModelRuntime(service,'agent',p,config=ModelConfig(model=MODEL)).run([{'role':'user','content':'hi'}])
            self.assertTrue(result['ok']);self.assertEqual(2,len(t.requests))
        finally:service.close()

    def test_role_specs_never_expose_authority(self):
        for names in (PRODUCT_TOOLS,QUALITY_TOOLS):
            specs=function_tools(names)
            self.assertFalse({'review','apply','bootstrap'} & {s['name'] for s in specs})
            self.assertTrue(all('actor' not in s['parameters']['properties'] for s in specs))
        with self.assertRaises(ValueError):function_tools(['review'])

if __name__=='__main__':unittest.main()
