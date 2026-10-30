import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from ontologics.order_fulfillment.contracts import (
    DEMO_EVIDENCE_IDS, DEMO_SOURCE_PATH, DEMO_SOURCE_SHA256, ROOT,
    extract_contract, load_demo_contract,
)


class ContractExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / DEMO_SOURCE_PATH).read_text()
        cls.catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        cls.expected = json.loads((ROOT / 'data/order_fulfillment/b_contract_expected.json').read_text())

    def extract(self, text=None, configurations=None):
        return extract_contract(self.text if text is None else text,
                                self.catalog if configurations is None else configurations,
                                evidence_ids=DEMO_EVIDENCE_IDS)

    def assert_stops(self, text, status, code):
        result = self.extract(text)
        self.assertEqual(result['status'], status, result)
        self.assertEqual(result['nodes'], [])
        self.assertIn(code, {i['code'] for i in result['issues']})
        self.assertTrue(all({'field', 'code', 'message', 'locator'} == set(i) for i in result['issues']))
        return result

    def test_fixed_table_matches_independent_golden_answer(self):
        result = self.extract()
        self.assertEqual(result['status'], 'ready', result)
        self.assertEqual(result['issues'], [])
        contract, item, delivery = result['nodes']
        expected = self.expected
        self.assertEqual((contract['id'], item['id'], delivery['id']),
                         tuple(expected[k] for k in ('contract_id', 'contract_item_id', 'delivery_round_id')))
        for key in ('company', 'customer_name'):
            self.assertEqual(contract[key], expected[key])
        self.assertEqual(item['configuration_id'], expected['configuration_id'])
        self.assertEqual(item['quantity'], expected['contract_quantity'])
        for key in ('quantity', 'unit', 'due_at', 'due_basis', 'destination'):
            self.assertEqual(delivery[key], expected[key])
        self.assertIs(type(item['quantity']), int)
        self.assertIs(type(delivery['quantity']), int)
        self.assertTrue(all(n['origin'] == 'synthetic' for n in result['nodes']))
        self.assertNotIn('observed_at', delivery)
        self.assertNotIn('arrived_at', delivery)
        self.assertNotIn('contract_valid_until', contract)

    def test_missing_due_destination_and_revision_need_supplementation(self):
        for field in ('납품 기한', '납품 장소', '설계 개정'):
            with self.subTest(field=field):
                text = '\n'.join(line for line in self.text.splitlines() if not line.startswith('| ' + field + ' |'))
                result = self.assert_stops(text, 'needs_confirmation', 'MISSING_FIELD')
                self.assertIn(field, {i['field'] for i in result['issues']})

    def test_empty_cell_needs_supplementation(self):
        self.assert_stops(self.text.replace('| 고객사 | 가상고객사 A |', '| 고객사 | |'),
                          'needs_confirmation', 'MISSING_FIELD')

    def test_fraction_boolean_quoted_zero_and_negative_source_quantities_are_invalid(self):
        for field in ('계약 수량', '회차 약속 수량'):
            for invalid in ('0', '-1', '1.5', 'true', '"1000"'):
                with self.subTest(field=field, value=invalid):
                    self.assert_stops(self.text.replace(f'| {field} | 1000 |', f'| {field} | {invalid} |'),
                                      'invalid', 'INVALID_QUANTITY')

    def test_source_time_without_time_or_offset_is_not_filled_in(self):
        for value in ('2026-10-27', '2026-10-27T15:00:00', 'not-a-time'):
            with self.subTest(value=value):
                self.assert_stops(self.text.replace(self.expected['due_at'], value),
                                  'needs_confirmation', 'INCOMPLETE_TIME')

    def test_departure_deadline_is_not_converted_to_arrival(self):
        self.assert_stops(self.text.replace('| 기한 기준 | 고객 도착 |', '| 기한 기준 | 출고 |'),
                          'needs_confirmation', 'UNSUPPORTED_DUE_BASIS')

    def test_conflicting_source_rows_report_all_line_locations(self):
        original = '| 납품 기한 | 2026-10-27T15:00:00+09:00 |'
        other = '| 납품 기한 | 2026-10-27T16:00:00+09:00 |'
        result = self.assert_stops(self.text.replace(original, original + '\n' + other),
                                  'needs_confirmation', 'CONFLICTING_FIELD')
        conflict = next(i for i in result['issues'] if i['code'] == 'CONFLICTING_FIELD')
        self.assertEqual(conflict['field'], '납품 기한')
        self.assertIn('lines 23,24', conflict['locator'])

    def test_partial_round_needs_confirmation_and_excessive_round_is_invalid(self):
        self.assert_stops(self.text.replace('| 회차 약속 수량 | 1000 |', '| 회차 약속 수량 | 600 |'),
                          'needs_confirmation', 'PARTIAL_ROUND_UNSUPPORTED')
        self.assert_stops(self.text.replace('| 회차 약속 수량 | 1000 |', '| 회차 약속 수량 | 1001 |'),
                          'invalid', 'ROUND_EXCEEDS_ITEM')

    def test_box_unit_and_multiple_round_scope_are_not_assumed(self):
        self.assert_stops(self.text.replace('| 수량 단위 | piece |', '| 수량 단위 | box |'),
                          'invalid', 'UNSUPPORTED_UNIT')
        for field in ('계약 항목 번호', '납품 회차'):
            self.assert_stops(self.text.replace(f'| {field} | 1 |', f'| {field} | 2 |'),
                              'needs_confirmation', 'UNSUPPORTED_SCOPE')

    def test_configuration_requires_exact_model_revision_variant(self):
        for field, original, wrong in [('제품 모델', 'ESP32-POE', 'ESP32'), ('설계 개정', 'L', 'L9'),
                                       ('구성', 'WROOM', 'WROVER')]:
            with self.subTest(field=field):
                self.assert_stops(self.text.replace(f'| {field} | {original} |', f'| {field} | {wrong} |'),
                                  'needs_confirmation', 'CONFIGURATION_NOT_UNIQUE')
        # Two exact candidates must not be arbitrarily selected by list order.
        duplicate = copy.deepcopy(next(n for n in self.catalog if n.get('id') == self.expected['configuration_id']))
        duplicate['id'] = 'config:duplicate'
        result = self.extract(configurations=self.catalog + [duplicate])
        self.assertEqual(result['status'], 'needs_confirmation')
        self.assertEqual(result['nodes'], [])

    def test_invalid_text_format_evidence_and_configuration_input_fail_closed(self):
        for value in (None, {}, 'plain prose', self.text.replace('## 계약 표', '## other')):
            with self.subTest(value=type(value).__name__):
                if isinstance(value, str):
                    self.assert_stops(value, 'invalid', 'UNSUPPORTED_FORMAT')
                else:
                    result = extract_contract(value, self.catalog, evidence_ids=DEMO_EVIDENCE_IDS)
                    self.assertEqual(result['status'], 'invalid')
        for evidence in (None, {}, {'contract': 'same', 'item': 'same', 'round': 'same'}):
            self.assertEqual(extract_contract(self.text, self.catalog, evidence_ids=evidence)['status'], 'invalid')
        self.assertEqual(extract_contract(self.text, {}, evidence_ids=DEMO_EVIDENCE_IDS)['status'], 'invalid')

    def test_source_extra_fields_or_repeated_table_need_explicit_mapping(self):
        text = self.text.replace('| 납품 장소 |', '| 출고 승인 | yes |\n| 납품 장소 |')
        self.assert_stops(text, 'needs_confirmation', 'UNSUPPORTED_FIELD')
        self.assert_stops(self.text + '\n## 계약 표\n', 'invalid', 'UNSUPPORTED_FORMAT')

    def test_fixture_hash_metadata_and_locators_are_preserved(self):
        fixture = load_demo_contract()
        self.assertEqual(len(fixture), 7)
        source, *rest = fixture
        self.assertEqual(source['sha256'], self.expected['source_sha256'])
        self.assertEqual(hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest(), DEMO_SOURCE_SHA256)
        self.assertEqual(source['origin'], 'synthetic')
        self.assertEqual(source['id'], self.expected['source_id'])
        evidence = [n for n in rest if n['type'] == 'Evidence']
        self.assertEqual({n['id'] for n in evidence}, set(DEMO_EVIDENCE_IDS.values()))
        self.assertTrue(all(n['source_id'] == source['id'] and 'lines ' in n['locator'] for n in evidence))
        self.assertNotIn('JunhwaDocs', source['path'])

    def test_changed_raw_source_is_rejected_without_rewriting_source_or_evidence(self):
        original_read_bytes = type(ROOT).read_bytes

        def altered_bytes(path):
            raw = original_read_bytes(path)
            return raw + b'changed' if path == ROOT / DEMO_SOURCE_PATH else raw

        with patch.object(type(ROOT), 'read_bytes', altered_bytes):
            with self.assertRaisesRegex(ValueError, 'SOURCE_HASH_MISMATCH'):
                load_demo_contract()


if __name__ == '__main__':
    unittest.main()
