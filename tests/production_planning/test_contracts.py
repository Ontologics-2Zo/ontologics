import copy
import hashlib
import json
from pathlib import Path
import unittest

from ontologics.production_planning import validate_planning_context

ROOT = Path(__file__).resolve().parents[2]
CONFIG = 'config:esp32-poe:L:WROOM'
COMPONENT = 'component:olimex-L-wroom:32d420c6c802a2c2'
BOM = 'bom:esp32-poe:L:WROOM:row-55'


def context():
    return {
        'contract_item': {'id': 'contract-item:planning:001', 'configuration_id': CONFIG,
            'ordered_quantity': 100, 'fulfilled_quantity': 0, 'allocated_quantity': 0,
            'unit': 'piece', 'delivery_due': '2026-11-10', 'constraints_state': 'unspecified'},
        'planned_input_quantity': 100, 'unit': 'piece', 'data_version': 1,
        'stock_scope': {'company': 'Planning Reference Company', 'location': 'warehouse:reference',
            'as_of': '2026-10-26T09:00:00+09:00'},
        'stocks': [{'id': 'stock:planning:001', 'lot_id': 'lot:planning:001',
            'component_id': COMPONENT, 'bom_item_id': BOM,
            'on_hand_quantity': 100, 'reserved_quantity': 30, 'held_quantity': 0,
            'unit': 'piece', 'availability_basis': 'on_hand_less_reserved_and_held',
            'deductions_disjoint': True}],
        'external_inputs': [{'source_ref': 'input:contract',
            'sha256': hashlib.sha256(b'reference contract').hexdigest()}],
        'confirmation_ref': None,
        'production_conditions': {'resource_ref': 'resource:planning:001',
            'available_at': '2026-10-26T10:00:00+09:00', 'setup_minutes': 60,
            'boards_per_hour': 20, 'loss_quantity': 0, 'defect_quantity': 0,
            'rework_quantity': 0, 'interruption_policy': 'none_assumed', 'origin': 'synthetic'},
    }


class PlanningContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = {'data_version': 1,
            'nodes': json.loads((ROOT / 'data/catalog.json').read_text())['nodes']}

    def validate(self, value, **kwargs):
        return validate_planning_context(value, reference=self.reference,
            external_contents={'input:contract': b'reference contract'}, **kwargs)

    def assert_error(self, value, path, code):
        report = self.validate(value)
        self.assertFalse(report['valid'], report)
        self.assertIn((path, code), [(e['path'], e['code']) for e in report['errors']])

    def test_normal_input_preserves_precision_and_does_not_mutate_inputs(self):
        value = context()
        original, reference = copy.deepcopy(value), copy.deepcopy(self.reference)
        report = self.validate(value)
        self.assertTrue(report['valid'], report)
        self.assertEqual('needs_confirmation', report['status'])
        self.assertEqual('date', report['normalized']['contract_item']['delivery_due_precision'])
        self.assertEqual('2026-11-10', report['normalized']['contract_item']['delivery_due'])
        self.assertEqual('2026-10-26T09:00:00+09:00', report['normalized']['stock_scope']['as_of'])
        self.assertIn(CONFIG, report['verified_references'])
        self.assertEqual(original, value)
        self.assertEqual(reference, self.reference)

    def test_piece_quantities_are_strict_integers_with_field_specific_bounds(self):
        for key in ('planned_input_quantity',):
            for bad in (-1, 0, True, False, 1.5, 100.0, '100', None, float('nan')):
                with self.subTest(key=key, bad=bad):
                    value = context(); value[key] = bad
                    self.assert_error(value, key, 'INVALID_QUANTITY')
        for key in ('on_hand_quantity', 'reserved_quantity', 'held_quantity'):
            for bad in (-1, True, 1.1, 1.0, '1', None):
                with self.subTest(key=key, bad=bad):
                    value = context(); value['stocks'][0][key] = bad
                    self.assert_error(value, 'stocks[0].' + key, 'INVALID_QUANTITY')
        value = context(); value['stocks'][0]['on_hand_quantity'] = 0
        self.assertTrue(self.validate(value)['valid'])  # balance calculation is Task 03

    def test_required_fields_and_version_do_not_default(self):
        for key in ('contract_item', 'planned_input_quantity', 'unit', 'stock_scope',
                    'stocks', 'data_version', 'external_inputs'):
            with self.subTest(key=key):
                value = context(); del value[key]
                self.assert_error(value, key, 'MISSING_FIELD')
        for bad in (-1, True, 1.0, '1', None):
            value = context(); value['data_version'] = bad
            self.assert_error(value, 'data_version', 'INVALID_VERSION')
        value = context(); del value['stock_scope']['company']
        self.assert_error(value, 'stock_scope.company', 'MISSING_FIELD')

    def test_missing_counts_are_errors_but_explicit_unknown_counts_remain_unknown(self):
        value = context(); del value['contract_item']['fulfilled_quantity']
        self.assert_error(value, 'contract_item.fulfilled_quantity', 'MISSING_FIELD')
        value = context(); value['stocks'][0]['on_hand_quantity'] = None
        value['stocks'][0]['quantity_state'] = 'unknown'
        report = self.validate(value)
        self.assertTrue(report['valid'], report)
        self.assertIsNone(report['normalized']['stocks'][0]['on_hand_quantity'])
        self.assertIn('UNKNOWN_QUANTITY', [r['code'] for r in report['unresolved']])

    def test_unknown_state_remains_unconfirmed_even_if_all_present_counts_are_integers(self):
        value = context()
        value['stocks'][0]['quantity_state'] = 'unknown'
        report = self.validate(value)
        self.assertTrue(report['valid'], report)
        self.assertIn(('stocks[0].quantity_state', 'UNKNOWN_QUANTITY'),
                      [(r['path'], r['code']) for r in report['unresolved']])

    def test_wrong_units_and_malformed_reference_ids_fail(self):
        value = context(); value['unit'] = 'board'
        self.assert_error(value, 'unit', 'INVALID_UNIT')
        for key in ('id', 'configuration_id'):
            value = context(); value['contract_item'][key] = []
            self.assert_error(value, 'contract_item.' + key, 'INVALID_REFERENCE')
        value = context(); value['stocks'][0]['lot_id'] = ' '
        self.assert_error(value, 'stocks[0].lot_id', 'INVALID_REFERENCE')

    def test_graph_ids_types_and_bom_relationships_are_checked(self):
        for key, replacement in (('configuration_id', 'config:missing'),
                                  ('configuration_id', COMPONENT)):
            value = context(); value['contract_item'][key] = replacement
            self.assert_error(value, 'contract_item.' + key, 'INVALID_REFERENCE')
        value = context(); value['stocks'][0]['component_id'] = 'component:missing'
        self.assert_error(value, 'stocks[0].component_id', 'INVALID_REFERENCE')
        value = context(); value['stocks'][0]['bom_item_id'] = 'bom:esp32-poe:L:WROOM:row-9'
        self.assert_error(value, 'stocks[0].bom_item_id', 'REFERENCE_MISMATCH')

    def test_reference_absence_and_present_extension_are_not_integration_success(self):
        report = validate_planning_context(context())
        self.assertTrue(report['valid'], report)
        self.assertEqual([], report['verified_references'])
        self.assertIn('REFERENCE_NOT_SUPPLIED', [r['code'] for r in report['unresolved']])
        report = self.validate(context())
        self.assertIn('EXTENSION_INTEGRATION_PENDING', [r['code'] for r in report['unresolved']])
        self.assertFalse(report['graph_integration_verified'])

    def test_reference_version_and_existing_graph_validator_are_not_bypassed(self):
        reference = copy.deepcopy(self.reference); reference['data_version'] = 2
        report = validate_planning_context(context(), reference=reference)
        self.assertIn('REFERENCE_VERSION_MISMATCH', [e['code'] for e in report['errors']])
        reference = copy.deepcopy(self.reference)
        reference['nodes'].append({'id': 'contract:invented', 'type': 'ContractItem'})
        report = validate_planning_context(context(), reference=reference)
        self.assertFalse(report['valid'])
        self.assertIn('INVALID_REFERENCE_GRAPH', [e['code'] for e in report['errors']])

    def test_a_known_graph_id_cannot_be_relabelled_as_a_contract_item(self):
        value = context(); value['contract_item']['id'] = COMPONENT
        self.assert_error(value, 'contract_item.id', 'INVALID_REFERENCE')

    def test_delivery_precision_cannot_stand_alone_or_have_a_malformed_type(self):
        for precision in ({'bogus': True}, [], False, None, 'date', 'datetime'):
            for missing in (True, False):
                with self.subTest(precision=precision, missing=missing):
                    value = context()
                    if missing:
                        del value['contract_item']['delivery_due']
                    else:
                        value['contract_item']['delivery_due'] = None
                    value['contract_item']['delivery_due_precision'] = precision
                    self.assert_error(value, 'contract_item.delivery_due_precision', 'INVALID_PRECISION')

    def test_stock_id_cannot_relabel_an_existing_component_or_configuration(self):
        for existing in (COMPONENT, CONFIG, BOM):
            value = context(); value['stocks'][0]['id'] = existing
            self.assert_error(value, 'stocks[0].id', 'INVALID_REFERENCE')

    def test_reported_available_is_not_reduced_again_and_no_calculation_is_returned(self):
        value = context()
        stock = value['stocks'][0]
        stock['availability_basis'] = 'reported_available'
        stock['available_quantity'] = 70
        report = self.validate(value)
        self.assertTrue(report['valid'], report)
        self.assertEqual(70, report['normalized']['stocks'][0]['available_quantity'])
        self.assertNotIn('shortage_quantity', report['normalized']['stocks'][0])
        del stock['available_quantity']
        self.assert_error(value, 'stocks[0].available_quantity', 'MISSING_FIELD')

    def test_unknown_bom_population_and_unverified_source_bytes_stay_unresolved(self):
        value = context()
        bom = next(n for n in self.reference['nodes'] if n['type'] == 'BOMItem' and
                   n['configuration_id'] == CONFIG and n['population'] == 'unknown')
        value['stocks'][0].update(bom_item_id=bom['id'], component_id=bom['component_id'])
        report = validate_planning_context(value, reference=self.reference)
        self.assertTrue(report['valid'], report)
        self.assertIn('POPULATION_UNCONFIRMED', [r['code'] for r in report['unresolved']])
        self.assertIn('SOURCE_BYTES_NOT_SUPPLIED', [r['code'] for r in report['unresolved']])

    def test_malformed_nested_fields_are_total_and_never_confirmed(self):
        base = context()
        for section in ('contract_item', 'stock_scope', 'production_conditions'):
            for key in base[section]:
                for bad in ([], {}, True):
                    value = copy.deepcopy(base); value[section][key] = bad
                    report = self.validate(value)
                    self.assertFalse(report['valid'], (section, key, bad, report))
        for key in base['stocks'][0]:
            for bad in ([], {}):
                value = copy.deepcopy(base); value['stocks'][0][key] = bad
                self.assertFalse(self.validate(value)['valid'], (key, bad))

    def test_datetime_precision_preserved_and_timezone_required_for_instants(self):
        value = context(); value['contract_item']['delivery_due'] = '2026-11-10T12:30:00.123456+09:00'
        report = self.validate(value)
        self.assertEqual(value['contract_item']['delivery_due'], report['normalized']['contract_item']['delivery_due'])
        self.assertEqual('datetime', report['normalized']['contract_item']['delivery_due_precision'])
        for bad in ('2026-02-30', '2026-10-26', '2026-10-26T09:00:00', [], ''):
            value = context(); value['stock_scope']['as_of'] = bad
            self.assert_error(value, 'stock_scope.as_of', 'INVALID_TIME')

    def test_confirmation_reference_is_not_human_confirmation(self):
        value = context(); value['confirmation_ref'] = 'confirmation:invented'
        report = self.validate(value)
        self.assertEqual('unverified', report['confirmation_state'])
        self.assertIn('HUMAN_CONFIRMATION_UNVERIFIED', [r['code'] for r in report['unresolved']])
        value['confirmed'] = True
        self.assert_error(value, 'confirmed', 'UNKNOWN_FIELD')

    def test_external_hashes_are_verified_against_host_supplied_bytes(self):
        value = context(); value['external_inputs'][0]['sha256'] = 'broken'
        self.assert_error(value, 'external_inputs[0].sha256', 'INVALID_HASH')
        value = context(); value['external_inputs'][0]['sha256'] = '0' * 64
        self.assert_error(value, 'external_inputs[0].sha256', 'HASH_MISMATCH')
        value = context(); value['external_inputs'][0]['sha256'] = value['external_inputs'][0]['sha256'].upper()
        self.assertTrue(self.validate(value)['valid'])

    def test_malformed_json_shapes_return_errors_without_exceptions(self):
        for value in (None, [], True, 'request'):
            self.assertFalse(validate_planning_context(value)['valid'])
        for key in context():
            for malformed in ([], {}, False):
                with self.subTest(key=key, malformed=malformed):
                    value = context(); value[key] = malformed
                    self.assertFalse(self.validate(value)['valid'])

    def test_unknown_fields_and_duplicate_stock_or_source_refs_are_rejected(self):
        value = context(); value['stocks'][0]['approved'] = True
        self.assert_error(value, 'stocks[0].approved', 'UNKNOWN_FIELD')
        value = context(); value['stocks'].append(copy.deepcopy(value['stocks'][0]))
        self.assert_error(value, 'stocks[1].id', 'DUPLICATE_REFERENCE')
        value = context(); value['external_inputs'].append(copy.deepcopy(value['external_inputs'][0]))
        self.assert_error(value, 'external_inputs[1].source_ref', 'DUPLICATE_REFERENCE')

    def test_stock_basis_and_production_conditions_are_explicit(self):
        value = context(); value['stocks'][0]['availability_basis'] = 'available'
        self.assert_error(value, 'stocks[0].availability_basis', 'INVALID_BASIS')
        value = context(); value['production_conditions']['boards_per_hour'] = False
        self.assert_error(value, 'production_conditions.boards_per_hour', 'INVALID_QUANTITY')
        value = context(); value['stocks'][0]['deductions_disjoint'] = None
        self.assertIn('DEDUCTION_SCOPE_UNCONFIRMED', [r['code'] for r in self.validate(value)['unresolved']])


if __name__ == '__main__':
    unittest.main()
