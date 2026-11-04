import unittest
from decimal import Decimal
from ontologics.materials.library_process_materials import load_reference, inspect_materials


class PerBoardMaterialsTests(unittest.TestCase):
    def test_geometry_supplement_is_loaded_and_not_approved(self):
        ref = load_reference()
        supplement = ref['per_board_supplement']
        self.assertEqual(2, len(supplement['configurations']))
        for c in supplement['configurations']:
            self.assertEqual('1.6', c['board']['thickness_mm'])
            self.assertEqual('2228.331621', c['board']['area_mm2'])
            paste, wire, flux, ipa, bar = c['materials']
            self.assertGreater(Decimal(paste['base_per_board']), 0)
            self.assertLess(Decimal(paste['base_per_board']), 1)
            self.assertEqual('0.2674', flux['base_per_board'])
            for m in c['materials']:
                self.assertFalse(m['production_approved'])
            for m in (wire, ipa, bar):
                self.assertIsNotNone(m['base_per_board'])
                self.assertEqual('synthetic_history_estimate',m['origin'])
            self.assertGreater(c['paste_geometry']['unknown_population_area_mm2'], 0)
            self.assertEqual(72, c['paste_geometry']['paste_line_count'])
            self.assertEqual(10, c['paste_geometry']['zero_delta_trapezoids'])

    def test_runtime_exposes_basis_without_replacing_saved_demand(self):
        data = inspect_materials(load_reference(), None, 'config:esp32-poe:L:WROOM', 100,
                                 '2026-11-02T09:00:00+00:00')
        for m in data['materials']:
            self.assertIn('usage_basis', m)
            self.assertIsNone(m['required'])
        self.assertIn('10%', data['materials'][0]['usage_basis']['allowance_basis'])
