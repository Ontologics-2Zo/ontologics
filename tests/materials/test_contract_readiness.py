import unittest
from ontologics.materials.contract_readiness import evaluate_contract_readiness, preview_fields

class ReadinessTests(unittest.TestCase):
    def fields(self):
        return {k:dict(value=v,state='supported',source='source_validated') for k,v in
                dict(manufacturer='M',product_model='P',revision='L',variant='V',ordered_quantity=100,unit='대').items()}
    def evaluate(self,fields=None,candidates=None):
        return evaluate_contract_readiness(fields or self.fields(), candidates if candidates is not None else [dict(exact=True,configuration_id='c')],basis={'data_version':1})
    def test_missing_delivery_does_not_block_order_requirements(self):
        r=self.evaluate();self.assertTrue(r['ready']);self.assertEqual(100,r['quantity']);self.assertEqual('c',r['configuration_id'])
    def test_ambiguous_and_missing_configuration(self):
        for cs in ([],[dict(exact=True,configuration_id='a'),dict(exact=True,configuration_id='b')]):
            self.assertFalse(self.evaluate(candidates=cs)['ready'])
    def test_quantity_and_unit_validation(self):
        for value in (True,0,-1,1.5,'100대','100,000','1.5'):
            f=self.fields();f['ordered_quantity']['value']=value
            self.assertFalse(self.evaluate(f)['ready'],value)
        f=self.fields();f['ordered_quantity'].update(value='100',source='human_correction');self.assertTrue(self.evaluate(f)['ready'])
        f['unit']['value']='상자';self.assertFalse(self.evaluate(f)['ready'])
    def test_explicit_unresolved_and_correction_override_source(self):
        row={'result':{'items':[{'fields':self.fields()}]},'checks':[{'id':'check','decisions':{
            '0:ordered_quantity':{'state':'corrected','value':'120','reason':'수정'},
            '0:revision':{'state':'unresolved','value':None,'reason':'보류'}}}]}
        items,check=preview_fields(row)
        self.assertEqual('120',items[0]['fields']['ordered_quantity']['value'])
        self.assertIsNone(items[0]['fields']['revision']['value'])
        self.assertFalse(self.evaluate(items[0]['fields'])['ready'])
    def test_conflict_never_uses_value(self):
        f=self.fields();f['ordered_quantity']['state']='conflict'
        self.assertFalse(self.evaluate(f)['ready'])
    def test_conflicting_explicit_configuration_id_blocks_automatic_selection(self):
        f=self.fields();f['configuration_id']=dict(state='conflict',value=None,source='unconfirmed')
        self.assertFalse(self.evaluate(f)['ready'])
