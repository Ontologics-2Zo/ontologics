import copy
import hashlib
import json
from pathlib import Path
import unittest

from examples.materials_inputs import load_reference_inputs
from ontologics.materials import validate_planning_context

ROOT = Path(__file__).resolve().parents[2]


class ReferenceInputTests(unittest.TestCase):
    def test_reference_files_match_hashes_and_actual_catalog_bom(self):
        request, contents = load_reference_inputs()
        reference = {'data_version': 1, 'nodes': json.loads((ROOT / 'data/catalog.json').read_text())['nodes']}
        report = validate_planning_context(request, reference=reference, external_contents=contents)
        self.assertTrue(report['valid'], report)
        self.assertEqual(3, len(report['verified_external_inputs']))
        self.assertEqual(100, request['planned_input_quantity'])
        self.assertEqual(0, request['contract_item']['fulfilled_quantity'])
        self.assertEqual(0, request['contract_item']['allocated_quantity'])
        self.assertEqual('2026-10-26T09:00:00+09:00', request['stock_scope']['as_of'])
        index = {n['id']: n for n in reference['nodes']}
        for row, expected in zip(request['stocks'], [(55, 1, 100, 30), (9, 8, 600, 40), (14, 11, 1200, 100)]):
            line, per_board, on_hand, reserved = expected
            bom = index[row['bom_item_id']]
            self.assertTrue(bom['id'].endswith('row-' + str(line)))
            self.assertEqual(bom['component_id'], row['component_id'])
            self.assertEqual(per_board, bom['quantity'])
            self.assertEqual(on_hand, row['on_hand_quantity'])
            self.assertEqual(reserved, row['reserved_quantity'])
            self.assertEqual('populated', bom['population'])
        self.assertEqual('synthetic', request['production_conditions']['origin'])
        self.assertFalse(report['graph_integration_verified'])

    def test_original_contract_text_is_preserved_and_content_change_is_detected(self):
        request, contents = load_reference_inputs()
        for entry in request['external_inputs']:
            self.assertEqual(entry['sha256'], hashlib.sha256(contents[entry['source_ref']]).hexdigest())
        contents = copy.deepcopy(contents)
        contents['input:planning:contract'] += b' changed'
        report = validate_planning_context(request, external_contents=contents)
        self.assertIn('HASH_MISMATCH', [e['code'] for e in report['errors']])


    def test_partial_graph_request_preserves_original_candidate_and_does_not_claim_integration(self):
        request, contents = load_reference_inputs()
        enriched = json.loads((ROOT / 'data/production_planning/request_graph_v1.json').read_text())
        self.assertEqual(request['contract_item'], enriched['contract_item'])
        self.assertEqual(request['external_inputs'], enriched['external_inputs'][:3])
        contents['input:planning:lots:v1'] = (ROOT / 'data/production_planning/lot_reference_v1.json').read_bytes()
        nodes = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        nodes += json.loads((ROOT / 'data/production_planning/graph_nodes_v1.json').read_text())['nodes']
        report = validate_planning_context(enriched, reference={'data_version': 1, 'nodes': nodes}, external_contents=contents)
        self.assertTrue(report['valid'], report)
        self.assertFalse(report['graph_integration_verified'])
        self.assertFalse(any(n['type'] == 'DeliveryRound' for n in nodes))
        self.assertEqual(4, len(report['verified_external_inputs']))
        lots = json.loads(contents['input:planning:lots:v1'])
        self.assertEqual('synthetic', lots['origin'])
        self.assertEqual([
            ('lot:planning:lan8710:001', 'component:olimex-L-wroom:32d420c6c802a2c2', 'PP-LOT-001'),
            ('lot:planning:2k2:001', 'component:olimex-L-wroom:a39f8e8d3a8a9f86', 'PP-LOT-002'),
            ('lot:planning:10k:001', 'component:olimex-L-wroom:7e7b047ff0528a6c', 'PP-LOT-003')],
            [(n['id'], n['component_id'], n['lot_code']) for n in lots['lots']])
        for lot in lots['lots']:
            self.assertEqual('Planning Reference Company', lot['company'])
            self.assertEqual('Planning Demo Supplier', lot['supplier'])
        for node in nodes:
            if node['type'] == 'ComponentStockSnapshot':
                self.assertNotIn('available_quantity', node)  # no derived value saved as observation
