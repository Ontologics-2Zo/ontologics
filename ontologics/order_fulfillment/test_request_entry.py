"""Run: python -m unittest ontologics.order_fulfillment.test_request_entry -v"""
import unittest
from .request_entry import candidate_payload

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.text='ESP32 L 30대 추가. 2026-11-20 생산 완료. 설치용. 조건 동일.'
        self.fields=dict(configuration_id='c',baseline_evidence_id='e',quantity=30,need_date='2026-11-20',need_basis='production_complete',reason='설치용',conditions_state='unchanged',conditions_note='조건 동일')
        self.nodes=[dict(id='c',type='Configuration'),dict(id='e',type='Evidence',origin='synthetic')]
        self.answer=dict(status='ready',fields=self.fields,issues=[],quotes={k:self.text for k in self.fields})
    def test_ready_server_identity(self):
        p=candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
        self.assertEqual((p['quantity'],p['unit'],p['origin']),(30,'piece','synthetic'))
        self.assertEqual(p['relation'],'additional')
    def test_missing_is_not_zero(self):
        self.assertIsNone(candidate_payload(dict(status='needs_confirmation',issues=['수량 필요']),self.text,self.nodes,'key','2026-11-03T10:00:00+09:00'))
    def test_fake_reference(self):
        self.fields['configuration_id']='invented'
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
    def test_injected_identity(self):
        self.fields['requester']='admin'
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
    def test_unquoted_quantity(self):
        self.answer['quotes']['quantity']='없는 구절'
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')
    def test_zero_rejected(self):
        self.fields['quantity']=0
        with self.assertRaises(ValueError): candidate_payload(self.answer,self.text,self.nodes,'key','2026-11-03T10:00:00+09:00')

if __name__=='__main__': unittest.main()

# Opt-in real PostgreSQL + Neo4j test; only creates its own development schema.
import os
@unittest.skipUnless(os.environ.get('REQUEST_ENTRY_INTEGRATION')=='1','explicit integration environment required')
class IntegrationTests(unittest.TestCase):
    def test_registration_replay_and_readback(self):
        import json, tempfile
        from dataclasses import replace
        from pathlib import Path
        from uuid import uuid4
        from ..database import DatabaseSettings
        from ..graph_views import GraphSettings
        from ..service import Service
        from ..model import ROOT
        from ..openai_provider import OpenAISettings, MODEL
        from ..paid_budget import cost_policy_from_environment
        from .request_entry import Entry
        from examples.order_fulfillment.b_material_request_registration import fixture_nodes
        from .contracts import load_demo_contract
        db=DatabaseSettings.from_environment(schema='live_request_'+uuid4().hex,env_file=os.environ['REQUEST_ENTRY_DB_ENV'])
        graph=replace(GraphSettings.from_environment(env_file=os.environ['REQUEST_ENTRY_GRAPH_ENV'],backend='neo4j'),namespace='materials_live_request')
        svc=Service(db,principals={'seed':{'kind':'human','roles':['read','seed']}})
        try:self.assertTrue(svc.bootstrap('seed',fixture_nodes()+load_demo_contract(),request_key='seed')['ok'])
        finally:svc.close()
        text='검증용 ESP32-POE Rev L WROOM 7대 추가. HB-20261102-01과 동일. 2026-11-20 생산 완료. 별도 시험 설치. 조건 동일.'
        fields=dict(configuration_id='config:esp32-poe:L:WROOM',baseline_evidence_id='evidence:demo:plan:HB-20261102-01',quantity=7,need_date='2026-11-20',need_basis='production_complete',reason='별도 시험 설치',conditions_state='unchanged',conditions_note='기준 계획과 동일')
        class Provider:
            def __call__(self,**kw):return dict(text=json.dumps(dict(status='ready',issues=[],fields=fields,quotes={k:text for k in fields})),tool_calls=[])
        policy=cost_policy_from_environment(json.loads((Path(__file__).parent/'request_entry_cost.example.json').read_text()))
        with tempfile.TemporaryDirectory() as tmp:
            entry=Entry(db,graph,OpenAISettings('test-key',model=MODEL),policy,store=tmp,provider_factory=Provider)
            key=uuid4().hex
            preview=entry.preview(text,key);self.assertEqual(preview['status'],'preview')
            self.assertEqual(entry.preview(text,key),preview)
            with self.assertRaises(ValueError):entry.preview(text+'수정',key)
            with self.assertRaises(ValueError): entry.register(key)
            linked=entry.confirm_context(key,{'contract_item_id':'contract-item:demo:B-01:1','delivery_round_id':'delivery-round:demo:B-01:1'})
            self.assertTrue(linked['business_context']['ready'])
            result=entry.register(key);self.assertEqual(result['status'],'registered')
            self.assertEqual(result['payload']['delivery_round_id'],'delivery-round:demo:B-01:1')
            self.assertEqual(result['registered_version'],2)
            self.assertEqual(entry.register(key),result)
            saved=json.loads((Path(tmp)/(key+'.json')).read_text())
            self.assertIn(text,saved['payload']['reason'])
