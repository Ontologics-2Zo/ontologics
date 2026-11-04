"""Arithmetic boundaries: explicit 1:1 allocations, never catalog assertions."""
import unittest
from ontologics.materials.library_replacements import replacement_result
from ontologics.service import DomainError

class ReplacementArithmeticTests(unittest.TestCase):
    def setUp(self):
        self.base=dict(components=[dict(component_id='a',name='A',D=120,A=80,S=40),dict(component_id='b',name='B',D=100,A=80,S=20)])
        self.reports={k:dict(id=k,payload=dict(source_id=source,target_id='c',target_name='C',status='compatible',known_blocker=False,available=30,stock_basis='fixture',stock_as_of='2026-10-31T00:00:00Z',location='test',technical_basis='test evidence',contract_basis='test agreement')) for k,source in [('r1','a'),('r2','b')]}
    def calculate(self,*pairs):return replacement_result(self.base,self.reports,[dict(review_id=k,quantity=n) for k,n in pairs])
    def test_partial_replacement_preserves_original(self):
        r=self.calculate(('r1',30));self.assertEqual([10,20,0],[c['S'] for c in r['components']]);self.assertEqual(40,self.base['components'][0]['S'])
    def test_replacement_shortage_becomes_purchase(self):
        r=self.calculate(('r1',40));self.assertEqual([0,20,10],[c['S'] for c in r['components']])
    def test_shared_stock_not_counted_twice(self):
        r=self.calculate(('r1',30),('r2',20));self.assertEqual(20,r['components'][-1]['S'])
    def test_rejects_invalid_duplicate_and_excess(self):
        for pairs in [(('r1',41),),(('r1',True),),(('r1',-1),),(('r1',1),('r1',1))]:
            with self.assertRaises(DomainError):self.calculate(*pairs)
    def test_rejects_unknown_and_conflicting_stock(self):
        self.reports['r1']['payload']['status']='unconfirmed'
        with self.assertRaises(DomainError):self.calculate(('r1',10))
        self.reports['r1']['payload']['status']='compatible';self.reports['r2']['payload']['available']=40
        with self.assertRaises(DomainError):self.calculate(('r1',10),('r2',10))
    def test_existing_target_demand_is_counted(self):
        self.base['components'].append(dict(component_id='c',name='C',D=20,A=30,S=0))
        r=self.calculate(('r1',30));self.assertEqual(20,r['components'][-1]['S'])
