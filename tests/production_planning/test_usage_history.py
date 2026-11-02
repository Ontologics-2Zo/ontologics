import copy
import unittest
from ontologics.production_planning.library_process_materials import load_reference
from ontologics.production_planning.library_usage_history import estimate, load_history

class UsageHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history=load_history()
        self.row=self.history['records'][0]
    def get(self,h=None):
        r=self.row
        return estimate(h or self.history,r['configuration_id'],r['material_id'],r['route_id'],r['unit'])
    def test_weighted_usage_and_separate_setup(self):
        from decimal import Decimal as D
        e=self.get()
        self.assertEqual(6,e['sample_count'])
        self.assertEqual('synthetic_history_estimate',e['origin'])
        self.assertGreater(D(e['base_per_board']),0)
        self.assertGreater(D(e['setup_quantity']),0)
        total=sum(D(r['issued'])-D(r['returned'])-D(r['setup_used']) for r in e['records'])
        self.assertAlmostEqual(float(total/D(e['total_boards'])),float(e['base_per_board']),places=5)
    def test_filter_and_insufficient_history(self):
        h=copy.deepcopy(self.history)
        for r in h['records']:
            if r['material_id']==self.row['material_id']:r['route_id']='different'
        self.assertIsNone(self.get(h))
        h=copy.deepcopy(self.history);h['records']=h['records'][:2]
        self.assertIsNone(self.get(h))
    def test_synthetic_history_fills_only_missing_basis(self):
        ref=load_reference()
        for c in ref['per_board_supplement']['configurations']:
            self.assertTrue(all(m['base_per_board'] is not None for m in c['materials']))
            self.assertEqual('derived_conditional',c['materials'][0]['origin'])
            for i in (1,3,4):
                self.assertEqual('synthetic_history_estimate',c['materials'][i]['origin'])
                self.assertFalse(c['materials'][i]['production_approved'])
