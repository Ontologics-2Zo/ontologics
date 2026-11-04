"""No database: prove declaration generation does not register runtime types."""
import copy
import unittest
from ontologics import model
from examples.order_fulfillment.b_material_request_boundary import inspect_boundary


class MaterialRequestBoundaryTests(unittest.TestCase):
    def test_declaration_is_not_runtime_registration(self):
        before = copy.deepcopy(model.SCHEMA)
        result = inspect_boundary()
        self.assertTrue(result['tbox_declaration_generated'])
        self.assertFalse(result['runtime_registered'])
        self.assertFalse(result['validation']['valid'])
        self.assertIn('UNKNOWN_TYPE', [e['code'] for e in result['validation']['errors']])
        self.assertEqual(before, model.SCHEMA)
        self.assertFalse(result['database_tested'])
        self.assertEqual('blocked', result['status'])
