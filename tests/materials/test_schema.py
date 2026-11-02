"""The extracted rules are local checks, not shared graph validation."""
import copy
import unittest

from ontologics import model
from ontologics.materials import schema


class MaterialsSchemaTests(unittest.TestCase):
    def check_nodes(self, nodes):
        errors, warnings, identities = [], [], {}
        index = {node['id']: node for node in nodes}
        def error(node, code, detail):
            errors.append(code)
        for node in nodes:
            schema.validate_node_rules(node, index, identities, error, warnings)
        schema.validate_round_totals(index, error)
        return errors, [warning['code'] for warning in warnings]

    def test_role_metadata_is_a_detached_subset_of_common_schema(self):
        self.assertEqual(set(schema.SCHEMA), {
            'Contract', 'ContractItem', 'DeliveryRound', 'ComponentStockSnapshot'})
        self.assertLessEqual(set(schema.SCHEMA), set(model.SCHEMA))
        for kind, definition in schema.SCHEMA.items():
            self.assertEqual(definition, model.SCHEMA[kind])
            self.assertIsNot(definition[0], model.SCHEMA[kind][0])
        self.assertEqual(schema.SCHEMA['ContractItem'][1], {
            'contract_id': 'Contract', 'configuration_id': 'Configuration'})
        self.assertEqual(schema.SCHEMA['DeliveryRound'][1], {'contract_item_id': 'ContractItem'})
        self.assertEqual(schema.SCHEMA['ComponentStockSnapshot'][1], {'lot_id': 'ComponentLot'})
        self.assertEqual(schema.OPTIONAL_FIELDS['ComponentStockSnapshot'],
                         schema.STRUCTURED_FIELDS['ComponentStockSnapshot'])

    def test_quantity_origin_and_unit_rules(self):
        for quantity in (True, 1.0, 0, -1):
            with self.subTest(quantity=quantity):
                errors, _ = self.check_nodes([dict(id='item', type='ContractItem',
                    origin='real', quantity=quantity, unit='kg')])
                self.assertEqual(set(errors), {'DEMO_OPERATIONS_ONLY', 'INVALID_QUANTITY', 'UNSUPPORTED_UNIT'})

    def test_multiple_rounds_and_total(self):
        item = dict(id='item', type='ContractItem', origin='synthetic', quantity=10, unit='piece')
        first = dict(id='r1', type='DeliveryRound', origin='synthetic', quantity=5,
                     unit='piece', contract_item_id='item', due_at='2026-11-10T10:00:00+09:00', due_basis='arrival')
        second = dict(first, id='r2')
        self.assertEqual(self.check_nodes([item, first, second]), ([], []))
        second['quantity'] = 6
        self.assertIn('ROUND_QUANTITY_EXCEEDED', self.check_nodes([item, first, second])[0])
        second.update(unit='kg', due_at='2026-11-10', due_basis='dispatch')
        self.assertTrue({'UNIT_MISMATCH', 'INVALID_TIME', 'UNSUPPORTED_DUE_BASIS'} <=
                        set(self.check_nodes([item, second])[0]))

    def test_stock_unknown_and_numeric_rules(self):
        stock = dict(id='stock', type='ComponentStockSnapshot', origin='synthetic',
                     lot_id='lot', location='warehouse', as_of='2026-10-30T10:00:00+09:00',
                     unit='piece', availability_basis='on_hand_less_reserved_and_held',
                     quantity_state='unknown', on_hand_quantity=None, reserved_quantity=None,
                     held_quantity=None, deductions_disjoint=None)
        self.assertEqual(self.check_nodes([stock]), ([], ['UNKNOWN_QUANTITY', 'DEDUCTION_SCOPE_UNCONFIRMED']))
        stock.update(quantity_state='known', on_hand_quantity=True, reserved_quantity=-1,
                     held_quantity=1.0, deductions_disjoint='yes')
        errors, _ = self.check_nodes([stock])
        self.assertEqual(errors.count('INVALID_QUANTITY'), 3)
        self.assertIn('INVALID_DEDUCTION_FLAG', errors)

    def test_stock_identity_and_precision(self):
        stock = dict(id='s1', type='ComponentStockSnapshot', origin='synthetic', lot_id='lot',
                     location='warehouse', as_of='2026-10-30T10:00:00+09:00', unit='piece',
                     availability_basis='reported_available', quantity_state='known', available_quantity=0)
        original = copy.deepcopy(stock)
        other = dict(stock, id='s2', as_of='2026-10-30T01:00:00Z')
        self.assertIn('DUPLICATE_IDENTITY', self.check_nodes([stock, other])[0])
        self.assertEqual(stock, original)
        other['as_of'] = '2026-10-30T01:00:00.1234567Z'
        self.assertIn('INVALID_TIME', self.check_nodes([other])[0])
