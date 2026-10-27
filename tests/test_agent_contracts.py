"""Versioned extension compatibility with real local PostgreSQL and scripted models."""
import json
from dataclasses import replace
from pathlib import Path
import unittest

from ontologics.contracts import AgentSpec, AgentTask, FieldSpec, PayloadSchema
from ontologics.demo import fixture_nodes
from ontologics.harness import BudgetLimits, PinnedContext
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.runtime import ModelConfig
from ontologics.service import Service
from tests.db import database_for_test


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self.database=database_for_test(self)
        self.spec=AgentSpec('typed_reader','1.0.0','Read exact data version and preserve unknowns.',
            PayloadSchema((FieldSpec('question','string'),)),
            PayloadSchema((FieldSpec('data_version','integer'),FieldSpec('answer','string'))),
            frozenset({'lookup'}), BudgetLimits(max_model_calls=3))
        identities=DevelopmentIdentityProvider([UserIdentity('a',frozenset({'read'})),UserIdentity('b',frozenset())],enabled=True)
        self.host=CommonHost(identities,self.database,agent_registrations=(AgentRegistration.from_spec(self.spec),))
        self.audit=Service(self.database,principals={'audit':{'kind':'human','roles':['read']}})
        self.addCleanup(self.audit.close)

    def test_runtime_contract_validation_and_old_registration_compatibility(self):
        class Role:
            def messages(self, task):
                return [{'role':'user','content':task.data()['question']}]
        calls=[]
        def provider(**kwargs):
            calls.append(kwargs)
            return {'text':json.dumps({'data_version':0,'answer':'Empty initial graph.'}), 'tool_calls':[]}
        task=AgentTask({'question':'Read graph'},PinnedContext('Read graph',0))
        with self.host.request(DevelopmentContext('a')) as request:
            result=request.run_task(self.spec.name,Role(),task,provider,config=ModelConfig('scripted'))
            self.assertTrue(result['runtime']['ok'])
            self.assertEqual(0,result['payload']['data_version'])
            bad=AgentTask({'question':3},PinnedContext('Read graph',0))
            with self.assertRaises(ValueError): request.run_task(self.spec.name,Role(),bad,provider,config=ModelConfig('scripted'))
        row=self.audit.db.execute('SELECT config FROM runtime_runs WHERE id=%s',(result['runtime']['run_id'],)).fetchone()
        self.assertEqual('1.0.0',json.loads(row['config'])['agent_contract']['agent_version'])
        self.assertIn('Host-pinned task',calls[0]['messages'][1]['content'])
        old=AgentRegistration('old_reader','Existing callers remain supported.',['lookup'])
        self.assertIsNone(old.spec)
        with self.host.request(DevelopmentContext('b')) as request:
            with self.assertRaises(Exception) as denied: request.run_task(self.spec.name,Role(),task,provider,config=ModelConfig('scripted'))
            self.assertEqual('FORBIDDEN',denied.exception.code)
        self.assertEqual(1,len(calls))

    def test_output_shape_repair_does_not_repair_semantic_rejection(self):
        class Role:
            def messages(self, task): return [{'role':'user','content':'Return the output DTO.'}]
        task=AgentTask({'question':'Read graph'},PinnedContext('Read graph',0))
        outputs=iter(['{"data_version":true,"answer":"wrong type"}','{"data_version":0,"answer":"correct type"}'])
        with self.host.request(DevelopmentContext('a')) as request:
            result=request.run_task(self.spec.name,Role(),task,lambda **kw:{'text':next(outputs),'tool_calls':[]},config=ModelConfig('scripted',max_repairs=1))
        self.assertTrue(result['runtime']['ok'])
        self.assertEqual(1,result['runtime']['repairs'])
        rejected=AgentSpec('semantic_reader','1.0.0',self.spec.instructions,self.spec.input_schema,self.spec.output_schema,
            self.spec.requested_tools,validator=lambda value:False)
        identities=DevelopmentIdentityProvider([UserIdentity('a',frozenset({'read'}))],enabled=True)
        host=CommonHost(identities,self.database,agent_registrations=(AgentRegistration.from_spec(rejected),))
        seen=[]
        def provider(**kw):
            seen.append(1)
            return {'text':'{"data_version":0,"answer":"unsupported"}','tool_calls':[]}
        with host.request(DevelopmentContext('a')) as request:
            result=request.run_task(rejected.name,Role(),task,provider,config=ModelConfig('scripted',max_repairs=1))
        self.assertEqual('AGENT_RESULT_REJECTED',result['runtime']['error']['code'])
        self.assertEqual(1,len(seen))

    def test_read_checkpoint_restart_revalidates_identity_version_and_single_use(self):
        class Role:
            def messages(self, task): return [{'role':'user','content':'Read a pinned version.'}]
        task=AgentTask({'question':'Read graph'},PinnedContext('Read graph',0))
        config=ModelConfig('scripted',max_attempts=1)
        class Provider:
            failing = True
            endpoint = 'scripted-local'
            def public_config(self): return {'provider': self.endpoint, 'temperature': 0}
            def __call__(self, **kw):
                if self.failing: raise ConnectionError('private transient detail')
                return {'text':'{"data_version":0,"answer":"empty"}','tool_calls':[]}
        unavailable = Provider()
        with self.host.request(DevelopmentContext('a')) as request:
            failed=request.run_task(self.spec.name,Role(),task,unavailable,config=config)
        checkpoint=failed['runtime']['run_id']
        # New request/connection represents a host restart, not a shared Python state.
        with self.host.request(DevelopmentContext('b')) as request:
            with self.assertRaises(Exception) as denied:
                request.resume_task(checkpoint,self.spec.name,Role(),task,unavailable,config=config)
            self.assertEqual('FORBIDDEN',denied.exception.code)
        unavailable.failing = False
        valid = unavailable
        with self.host.request(DevelopmentContext('a')) as request:
            with self.assertRaises(Exception) as changed:
                request.resume_task(checkpoint,self.spec.name,Role(),AgentTask({'question':'changed'},task.context),valid,config=config)
            self.assertEqual('CHECKPOINT_CONTRACT_MISMATCH',changed.exception.code)
            resumed=request.resume_task(checkpoint,self.spec.name,Role(),task,valid,config=config)
            self.assertTrue(resumed['runtime']['ok'])
            self.assertEqual(checkpoint,resumed['resumed_from'])
            with self.assertRaises(Exception) as duplicate:
                request.resume_task(checkpoint,self.spec.name,Role(),task,valid,config=config)
            self.assertEqual('CHECKPOINT_ALREADY_RESUMED',duplicate.exception.code)
        unavailable.failing = True
        with self.host.request(DevelopmentContext('a')) as request:
            failed=request.run_task(self.spec.name,Role(),task,unavailable,config=config)
        # Simulate an independently approved newer snapshot in this isolated DB.
        self.audit.db.execute("INSERT INTO snapshots VALUES(1,'[]'::jsonb,'test','test fixture')")
        with self.host.request(DevelopmentContext('a')) as request:
            with self.assertRaises(Exception) as stale:
                request.resume_task(failed['runtime']['run_id'],self.spec.name,Role(),task,valid,config=config)
            self.assertEqual('STALE_CHECKPOINT',stale.exception.code)

    def test_resume_rejects_provider_prompt_schema_or_tool_drift(self):
        class Role:
            prompt = 'Read the pinned graph.'
            def messages(self, task): return [{'role':'user','content':self.prompt}]
        class Provider:
            provider_name='local-script'
            calls=0
            def public_config(self): return {'provider':self.provider_name,'temperature':0}
            def __call__(self, **kwargs):
                self.calls += 1
                raise ConnectionError('controlled outage')
        role,provider=Role(),Provider()
        task=AgentTask({'question':'Read'},PinnedContext('Read',0))
        config=ModelConfig('scripted',max_attempts=1)
        with self.host.request(DevelopmentContext('a')) as request:
            failed=request.run_task(self.spec.name,role,task,provider,config=config)
        run_id=failed['runtime']['run_id']
        for variant in ('provider','prompt','instructions','schema','tools','validator','checkpoint_schema'):
            altered=self.spec
            role.prompt='Read the pinned graph.'
            provider.provider_name='local-script'
            role.validate_result = None
            if variant=='provider': provider.provider_name='another-provider'
            if variant=='prompt': role.prompt='Different instructions'
            if variant=='instructions': altered=replace(self.spec,instructions='Different registered instructions')
            if variant=='schema': altered=replace(self.spec,output_schema=PayloadSchema(self.spec.output_schema.fields+(FieldSpec('extra','string',False),)))
            if variant=='tools': altered=replace(self.spec,requested_tools=frozenset({'configuration'}))
            if variant=='validator': role.validate_result=lambda value, task, results: True
            if variant=='checkpoint_schema':
                row=self.audit.db.execute('SELECT config FROM runtime_runs WHERE id=%s',(run_id,)).fetchone()
                stored=json.loads(row['config'])
                stored['read_checkpoint']['schema_version']='read-checkpoint-2'
                self.audit.db.execute('UPDATE runtime_runs SET config=%s WHERE id=%s',(json.dumps(stored),run_id))
            host=CommonHost(self.host._identities,self.database,agent_registrations=(AgentRegistration.from_spec(altered),))
            with self.subTest(variant=variant),host.request(DevelopmentContext('a')) as request:
                with self.assertRaises(Exception) as denied:
                    request.resume_task(run_id,altered.name,role,task,provider,config=config)
                self.assertEqual('CHECKPOINT_CONTRACT_MISMATCH',denied.exception.code)
        self.assertEqual(1,provider.calls)

    def test_evidence_provenance_is_host_resolved_and_unread_citations_are_terminal(self):
        catalog=json.loads((Path(__file__).resolve().parents[1]/'data/catalog.json').read_text())['nodes']
        fictional,_=fixture_nodes()
        self.audit.principals['fixture']={'kind':'human','roles':['read','seed']}
        self.assertTrue(self.audit.bootstrap('fixture',catalog+fictional,request_key='contract-fixture')['ok'])
        product=next(n for n in catalog if n['type']=='ProductModel')
        real_id=product['evidence'][0]
        spec=replace(self.spec,output_schema=PayloadSchema(self.spec.output_schema.fields+(FieldSpec('evidence_ids','array'),)))
        host=CommonHost(self.host._identities,self.database,agent_registrations=(AgentRegistration.from_spec(spec),))
        task=AgentTask({'question':'Read product and board'},PinnedContext('Read product and board',1))
        class Role:
            def messages(self, task): return [{'role':'user','content':'Read product and board evidence.'}]
            def validate_result(self, value, task, results):
                if [r['name'] for r in results] != ['lookup','lookup']: return False
                # A validator receives copies; this cannot alter result/provenance.
                value['answer']='not the result'
                results[0]['result']['data'][0]['origin']='synthetic'
                return True
        valid=[real_id,'evidence:demo:initial']
        unread=next(n['id'] for n in catalog if n['type']=='Evidence' and n['id'] not in product['evidence'])
        for citations, expected in ((valid,None),(['evidence:invented'],'AGENT_EVIDENCE_MISMATCH'),
                                    ([unread],'AGENT_EVIDENCE_MISMATCH'),([False],'AGENT_EVIDENCE_MISMATCH')):
            with self.subTest(citations=citations):
                count=[]
                def provider(**kwargs):
                    count.append(1)
                    if len(count)==1:
                        return {'text':'Read','tool_calls':[
                            {'name':'lookup','arguments':{'node_id':product['id'],'version':1}},
                            {'name':'lookup','arguments':{'node_id':'unit:U104','version':1}}]}
                    return {'text':json.dumps({'data_version':1,'answer':'Grounded reads','evidence_ids':citations}),'tool_calls':[]}
                with host.request(DevelopmentContext('a')) as request:
                    result=request.run_task(spec.name,Role(),task,provider,config=ModelConfig('scripted',max_repairs=1))
                self.assertEqual(2,len(count))
                self.assertEqual(0,result['runtime']['repairs'])
                if expected:
                    self.assertEqual(expected,result['error']['code'])
                    self.assertEqual(result['runtime']['error'],result['error'])
                    self.assertEqual([],result['evidence_refs'])
                else:
                    self.assertTrue(result['runtime']['ok'])
                    self.assertEqual('Grounded reads',result['payload']['answer'])
                    self.assertIsNone(result['error'])
                    refs={r['evidence_id']:r for r in result['evidence_refs']}
                    self.assertEqual(set(valid),set(refs))
                    self.assertEqual('real',refs[real_id]['origin'])
                    self.assertEqual('synthetic',refs['evidence:demo:initial']['origin'])
                    index={n['id']:n for n in catalog+fictional}
                    for eid,ref in refs.items():
                        source=index[index[eid]['source_id']]
                        self.assertEqual((1,source['id'],index[eid]['locator'],source['sha256']),
                            (ref['data_version'],ref['source_id'],ref['locator'],ref['source_sha256']))

    def test_role_result_validator_rejects_tool_disagreement_without_repair(self):
        class Role:
            def messages(self,task): return [{'role':'user','content':'Return confirmed lookup size.'}]
            def validate_result(self,value,task,results):
                return len(results)==1 and value['answer']==str(len(results[0]['result']['data']))
        calls=[]
        def provider(**kwargs):
            calls.append(1)
            if len(calls)==1: return {'text':'Read','tool_calls':[{'name':'lookup','arguments':{'kind':'ProductModel','version':0}}]}
            return {'text':'{"data_version":0,"answer":"999"}','tool_calls':[]}
        with self.host.request(DevelopmentContext('a')) as request:
            result=request.run_task(self.spec.name,Role(),AgentTask({'question':'Read'},PinnedContext('Read',0)),
                provider,config=ModelConfig('scripted',max_repairs=1))
        self.assertEqual('AGENT_RESULT_REJECTED',result['error']['code'])
        self.assertEqual(2,len(calls))
        self.assertEqual(0,result['runtime']['repairs'])

    def test_persisted_legacy_draft_survives_additive_agent_schema(self):
        catalog=json.loads((Path(__file__).resolve().parents[1]/'data/catalog.json').read_text())['nodes']
        fictional,supplement=fixture_nodes()
        alice=UserIdentity('alice',frozenset({'read','draft','review'}))
        bob=UserIdentity('bob',frozenset({'read','review'}))
        # The unchanged pre-contract Service persists its original draft layout.
        legacy=Service(self.database,principals={alice.actor:alice.principal(),
            'fixture':{'kind':'human','roles':['read','seed']}})
        try:
            self.assertTrue(legacy.bootstrap('fixture',catalog+fictional,request_key='legacy-seed')['ok'])
            saved=legacy.prepare_draft(alice.actor,supplement,request_key='legacy-supplement')
            self.assertTrue(saved['ok'])
            draft=saved['data']
        finally:
            legacy.close()
        extended=replace(self.spec,output_schema=PayloadSchema(self.spec.output_schema.fields+(FieldSpec('summary','string',False),)))
        host=CommonHost(DevelopmentIdentityProvider([alice,bob],enabled=True),self.database,
            agent_registrations=(AgentRegistration.from_spec(extended),))
        with host.request(DevelopmentContext('alice')) as request:
            found=request.call('get_draft',{'draft_id':draft['draft_id'],'version':1})
            self.assertTrue(found['ok'])
            self.assertEqual(draft['content_hash'],found['data']['content_hash'])
            denied=request.review(draft['draft_id'],1,decision='approve',note='Self approval attempt',request_key='legacy-self')
            self.assertEqual('SELF_APPROVAL_FORBIDDEN',denied['errors'][0]['code'])
        with host.request(DevelopmentContext('bob')) as request:
            approved=request.review(draft['draft_id'],1,decision='approve',note='Exact saved version',request_key='legacy-approve')
            self.assertTrue(approved['ok'])
        wrong=host.apply_approved(draft['draft_id'],2,request_key='legacy-wrong-version')
        self.assertFalse(wrong['ok'])
        applied=host.apply_approved(draft['draft_id'],1,request_key='legacy-apply')
        self.assertTrue(applied['ok'])
        self.assertEqual(2,applied['data_version'])
        replay=host.apply_approved(draft['draft_id'],1,request_key='legacy-apply')
        self.assertEqual(2,replay['data_version'])
        self.assertEqual(draft['content_hash'],self.audit._draft(draft['draft_id'],1)['content_hash'])

    def test_result_version_is_host_validated_without_repair(self):
        class Role:
            def messages(self, task): return [{'role':'user','content':'Read'}]
        # A custom schema must not weaken the host's reserved integer version.
        for returned_version, pinned_version, kind in ((999,0,'integer'),(False,0,'boolean'),(True,1,'boolean')):
            with self.subTest(returned_version=returned_version):
                if pinned_version == 1:
                    self.audit.db.execute("INSERT INTO snapshots VALUES(1,'[]'::jsonb,'test','test fixture')")
                spec=replace(self.spec,output_schema=PayloadSchema((FieldSpec('data_version',kind),FieldSpec('answer','string'))))
                host=CommonHost(self.host._identities,self.database,agent_registrations=(AgentRegistration.from_spec(spec),))
                calls=[]
                def wrong_version(**kw):
                    calls.append(1)
                    return {'text':json.dumps({'data_version':returned_version,'answer':'unsupported version'}),'tool_calls':[]}
                task=AgentTask({'question':'Read'},PinnedContext('Read',pinned_version))
                with host.request(DevelopmentContext('a')) as request:
                    result=request.run_task(spec.name,Role(),task,wrong_version,config=ModelConfig('scripted',max_repairs=1))
                self.assertFalse(result['runtime']['ok'])
                self.assertEqual('AGENT_RESULT_VERSION_MISMATCH',result['runtime']['error']['code'])
                self.assertEqual(1,len(calls))
                self.assertEqual(0,result['runtime']['repairs'])
                self.assertEqual(pinned_version,result['context']['data_version'])
                self.assertIsNone(result['payload'])

    def test_unknown_schema_authority_fields_and_tool_escalation_are_rejected(self):
        for field in ('actor_id','approved','roles','reviewer'):
            with self.assertRaises(ValueError): FieldSpec(field,'string')
        with self.assertRaises(ValueError): AgentTask({},PinnedContext('Read',0),schema_version='agent-task-2')
        with self.assertRaises(ValueError): PayloadSchema((FieldSpec('question','string'),),version='2')
        with self.assertRaises(ValueError): AgentSpec('bad','1.0.0','Read',self.spec.input_schema,self.spec.output_schema,{'review'})
        with self.assertRaises(ValueError): AgentRegistration.from_spec(AgentSpec('quality','1.0.0','Read',self.spec.input_schema,self.spec.output_schema,{'lookup'}))


if __name__ == '__main__': unittest.main()
