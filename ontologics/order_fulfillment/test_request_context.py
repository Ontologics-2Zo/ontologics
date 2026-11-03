import unittest
from .request_context import resolve_context
class ContextTests(unittest.TestCase):
 def setUp(self):
  self.p={'configuration_id':'config:x','baseline_evidence_id':'e:plan','origin':'synthetic'}
  self.nodes=[{'id':'e:plan','type':'Evidence','description':'기존 목표 200대','source_id':'s:plan'}, {'id':'config:x','type':'Configuration','name':'제품 X'}, {'id':'ct:1','type':'Contract','customer_name':'고객 A'}, {'id':'item:1','type':'ContractItem','contract_id':'ct:1','configuration_id':'config:x','quantity':200,'origin':'synthetic','evidence':[]}, {'id':'round:1','type':'DeliveryRound','contract_item_id':'item:1','quantity':100,'due_at':'2026-11-20T00:00:00+09:00'}]
 def test_same_product_is_not_a_contract_link(self):
  r=resolve_context(self.p,self.nodes,'추가 30대');self.assertFalse(r['ready']);self.assertEqual(len(r['candidates']),1)
 def test_plan_explicit_reference(self):
  self.nodes[0]['description']+=' item:1 round:1'
  r=resolve_context(self.p,self.nodes,'추가 30대');self.assertTrue(r['ready']);self.assertEqual(r['references']['contract_item_id'],'item:1')
 def test_user_confirmed_chain(self):
  r=resolve_context(self.p,self.nodes,'추가',{'contract_item_id':'item:1','delivery_round_id':'round:1'})
  self.assertTrue(r['ready']);self.assertEqual(r['basis'],'사용자 연결 확인')
 def test_cross_contract_round_rejected(self):
  self.nodes.append({'id':'round:other','type':'DeliveryRound','contract_item_id':'item:other'})
  with self.assertRaises(ValueError):resolve_context(self.p,self.nodes,'추가',{'contract_item_id':'item:1','delivery_round_id':'round:other'})
 def test_plan_conflict_rejected(self):
  self.nodes[0]['description']+=' item:1'
  self.nodes.append(dict(self.nodes[3],id='item:2'))
  with self.assertRaises(ValueError):resolve_context(self.p,self.nodes,'추가',{'contract_item_id':'item:2'})
 def test_many_rounds_need_confirmation(self):
  self.nodes[0]['description']+=' item:1'
  self.nodes.append(dict(self.nodes[4],id='round:2'))
  self.assertFalse(resolve_context(self.p,self.nodes,'추가')['ready'])
 def test_wrong_configuration_not_candidate(self):
  self.nodes[3]['configuration_id']='other'
  self.assertEqual(resolve_context(self.p,self.nodes,'추가')['candidates'],[])
