import unittest
import os
from tests.production_planning import test_process_rfq as base
CID, MID = base.CID, base.MID
from ontologics.production_planning.library_process_rfq import calculate_automatic

class AutoDemandRulesTests(unittest.TestCase):
    def setUp(self):
        base.ReviewRulesTests.setUp(self)
        # Preserve the missing-basis behavior independently of the new fixture.
        for i in (1,3,4):self.data['materials'][i]['usage_basis']['base_per_board']=None
    def test_quantity_allowance_and_unknown(self):
        a=calculate_automatic(self.data,'10')
        self.assertEqual('22.9977',a[0]['required'])
        self.assertEqual('29.414',a[2]['required'])
        self.assertEqual('16.414',a[2]['shortage'])
        self.assertEqual(3,sum(r['status']=='unregistered' for r in a))
        self.data['quantity']=200
        b=calculate_automatic(self.data,'20')
        self.assertEqual('64.176',b[2]['required'])
        self.assertEqual('51.176',b[2]['shortage'])
    def test_invalid_allowance(self):
        for x in ('-1','101','NaN','1e-99999',True):
            with self.assertRaises(ValueError):calculate_automatic(self.data,x)

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL required')
class AutoDemandDatabaseTests(unittest.TestCase):
    setUp = base.ProcessRFQDatabaseTests.setUp
    ok = base.ProcessRFQDatabaseTests.ok
    create = base.ProcessRFQDatabaseTests.create
    def test_auto_preview_review_and_rfq(self):
        for q in (100,200):
            preview=self.ok(self.app.dispatch('/api/tools/contracts/process-requirement-preview',dict(configuration_id=CID,quantity=q,allowance_percent='10')))
            review=self.ok(self.app.dispatch('/api/tools/contracts/process-requirement-auto',dict(configuration_id=CID,quantity=q,allowance_percent='10',job_name='자동 계산 검증',basis_hash=preview['basis_hash'],reviewed=True,request_key='auto-'+str(q))))
            self.assertEqual('automatic_per_board',review['payload']['requirement_source'])
            draft=self.ok(self.create(review,request_key='auto-rfq-'+str(q)))['payload']
            self.assertEqual(str(q),str(draft['quantity']))
            self.assertEqual('16.414' if q==100 else '45.828',draft['lines'][0]['shortage'])
            self.assertIn('대당 기준',draft['text'])
            self.assertEqual(0,len(draft['unknown_materials']))
            saved=self.ok(self.app.process_rfq.inspect(self.context,CID,review['id']))
            self.assertEqual(draft['text'],saved['drafts'][0]['payload']['text'])
    def test_stale_preview_cannot_save(self):
        r=self.app.dispatch('/api/tools/contracts/process-requirement-auto',dict(configuration_id=CID,quantity=100,allowance_percent='10',job_name='검증',basis_hash='old',reviewed=True,request_key='stale-auto'))
        self.assertFalse(r['ok'])
    def test_history_based_ipa_shortage_reaches_rfq(self):
        ipa='process-material:mgchemicals:824'
        preview=self.ok(self.app.process_rfq.preview(self.context,CID,300,'10'))
        line=next(x for x in preview['lines'] if x['material_id']==ipa)
        self.assertEqual('826.23817',line['required'])
        self.assertEqual('126.23817',line['shortage'])
        self.assertEqual('synthetic_history_estimate',line['basis_origin'])
        review=self.ok(self.app.process_rfq.automatic_review(self.context,CID,300,'10','이력 기준 검증',preview['basis_hash'],True,'history-auto'))
        draft=self.ok(self.create(review,material_ids=[ipa],request_key='history-ipa-rfq'))['payload']
        self.assertEqual(line['shortage'],draft['lines'][0]['shortage'])
        self.assertIn('가상 생산 이력',draft['text'])
        self.assertIn('준비량 10 mL',draft['text'])
        zero=self.ok(self.app.process_rfq.preview(self.context,CID,0,'10'))
        self.assertTrue(all(x['required']=='0' for x in zero['lines']))
