"""Offline geometry checks; run with the generator's Shapely environment."""
import json
import unittest

try:
    import shapely
except ImportError:
    shapely = None
if shapely is not None:
    from ontologics.materials.build_usage_basis import build, geometry, parse, OUTPUT


@unittest.skipUnless(shapely is not None, 'Offline generator requires Shapely 2')
class UsageGeometryTests(unittest.TestCase):
    def test_overlapping_lines_union_and_zero_delta_trapezoid(self):
        b = parse('''(kicad_pcb (setup)
          (module test (at 0 0 90) (fp_text reference U1)
            (pad 1 smd trapezoid (at 0 0 90) (size 2 1) (layers F.Paste)
              (solder_paste_margin 0.1))
            (fp_line (start -0.5 0) (end 0.5 0) (layer F.Paste) (width 0.2))
            (fp_line (start -0.5 0) (end 0.5 0) (layer F.Paste) (width 0.2))))''')
        result = geometry(b, {'U1': 'populated'})
        self.assertAlmostEqual(2.2 * 1.2, result['populated_area_mm2'])
        self.assertEqual(2, result['paste_line_count'])
        self.assertEqual(1, result['zero_delta_trapezoids'])

    def test_unknown_population_is_separate_and_shapes_fail_closed(self):
        b = parse('''(kicad_pcb (setup)
          (module test (at 0 0) (fp_text reference U1)
            (pad 1 smd rect (at 0 0) (size 2 1) (layers B.Paste))))''')
        result = geometry(b, {'U1': 'unknown'})
        self.assertEqual(0, result['populated_area_mm2'])
        self.assertEqual(2, result['unknown_population_area_mm2'])
        b[2][4][3] = 'custom'
        with self.assertRaises(ValueError):
            geometry(b, {'U1': 'populated'})

    def test_checked_in_data_reproduces_from_pinned_sources(self):
        result = build()
        self.assertEqual(json.loads(OUTPUT.read_text()), result)
        a, b = result['configurations']
        self.assertAlmostEqual(1.1, a['paste_geometry']['populated_area_mm2'] - b['paste_geometry']['populated_area_mm2'])
        # 0.004 inch converted to mm and grams/cm3 converted to grams/mm3.
        for c in result['configurations']:
            expected = c['paste_geometry']['populated_area_mm2'] * .1016 * (7.37 / (.117 * 7.37 + .883)) / 1000
            self.assertLessEqual(expected, float(c['materials'][0]['base_per_board']))
            self.assertLess(float(c['materials'][0]['base_per_board']) - expected, .000001)

    def test_synthetic_history_reproduces_and_joint_scopes_do_not_overlap(self):
        from ontologics.materials.build_usage_history import build as history_build, OUT
        result=history_build()
        self.assertEqual(json.loads(OUT.read_text()),result)
        self.assertEqual(36,len(result['records']))
        for a in result['geometry_anchors']:
            hand={(p['reference'],p['pin']) for p in a['hand_pins']}
            wave={(p['reference'],p['pin']) for p in a['wave_pins']}
            self.assertFalse(hand & wave)
            self.assertEqual(2,len(hand));self.assertEqual(14,len(wave))
