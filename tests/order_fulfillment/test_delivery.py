"""Pure B contract graph and tool-schema boundaries; no stored B graph."""
from __future__ import annotations

import copy
import json
import unittest

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.model import ROOT, SCHEMA_VERSION
from ontologics.order_fulfillment.schema_validation import validate_input_graph
from ontologics.order_fulfillment.schema_export import graph_jsonld as b_graph_jsonld, tbox_turtle as b_tbox_turtle
from ontologics.order_fulfillment.tool_specs import PRODUCT_TOOLS, QUALITY_TOOLS, function_tools


CONTRACT = 'contract:demo:B-01'
ITEM = 'contract-item:demo:B-01:1'
ROUND = 'delivery-round:demo:B-01:1'
CONFIGURATION = 'config:esp32-poe:L:WROOM'


def catalog_nodes():
    return json.loads((ROOT / 'data/catalog.json').read_text())['nodes']


class DeliveryGraphTests(unittest.TestCase):
    def setUp(self):
        self.nodes = catalog_nodes() + load_demo_contract()
        self.index = {node['id']: node for node in self.nodes}

    def assert_invalid(self):
        report = validate_input_graph(self.nodes)
        self.assertFalse(report['valid'], report)
        self.assertTrue(report['errors'])
        return report

    def test_new_contract_graph_is_valid_with_real_configuration_and_synthetic_promise(self):
        report = validate_input_graph(self.nodes)
        self.assertTrue(report['valid'], report)
        self.assertEqual('real', self.index[CONFIGURATION]['origin'])
        for node_id in (CONTRACT, ITEM, ROUND):
            self.assertEqual('synthetic', self.index[node_id]['origin'])
            self.assertTrue(self.index[node_id]['evidence'])
        self.assertEqual('DEMO-B', self.index[CONTRACT]['company'])
        self.assertEqual('가상고객사 A', self.index[CONTRACT]['customer_name'])

    def test_contract_piece_counts_reject_non_integer_and_nonpositive_values(self):
        for node_id in (ITEM, ROUND):
            node = self.index[node_id]
            previous = node['quantity']
            for value in (0, -1, 1.5, 1000.0, True, False, '1000', None, [], {}, float('nan'), float('inf')):
                with self.subTest(node_id=node_id, quantity=value):
                    node['quantity'] = value
                    self.assert_invalid()
            node['quantity'] = previous

    def test_required_contract_fields_cannot_be_missing_or_empty(self):
        fields = {CONTRACT: ('company', 'customer_name'),
                  ITEM: ('contract_id', 'configuration_id', 'quantity', 'unit'),
                  ROUND: ('contract_item_id', 'quantity', 'unit', 'due_at', 'due_basis', 'destination')}
        for node_id, required in fields.items():
            node = self.index[node_id]
            for field in required:
                with self.subTest(node_id=node_id, field=field):
                    previous = node.pop(field)
                    self.assert_invalid()
                    node[field] = ''
                    self.assert_invalid()
                    node[field] = previous

    def test_round_arrival_time_requires_explicit_clock_and_offset(self):
        node = self.index[ROUND]
        for invalid in ('2026-10-27', '2026-10-27T15:00:00', 'not-a-time', [], {}):
            with self.subTest(due_at=invalid):
                node['due_at'] = invalid
                self.assert_invalid()
        node['due_at'] = '2026-10-27T06:00:00Z'
        self.assertTrue(validate_input_graph(self.nodes)['valid'])

    def test_departure_and_unsupported_units_are_not_silently_arrival_pieces(self):
        for node_id, field, invalid in ((ROUND, 'due_basis', 'departure'),
                                        (ROUND, 'due_basis', 'acceptance'),
                                        (ROUND, 'unit', 'box'), (ITEM, 'unit', 'box')):
            with self.subTest(node_id=node_id, field=field, value=invalid):
                node = self.index[node_id]
                previous = node[field]
                node[field] = invalid
                self.assert_invalid()
                node[field] = previous

    def test_references_reject_missing_wrong_type_and_container_values(self):
        references = ((ITEM, 'contract_id', CONFIGURATION),
                      (ITEM, 'configuration_id', CONTRACT),
                      (ROUND, 'contract_item_id', CONTRACT))
        for node_id, field, wrong_type in references:
            node = self.index[node_id]
            previous = node[field]
            for invalid in ('missing:entity', wrong_type, [], {}):
                with self.subTest(node_id=node_id, field=field, value=invalid):
                    node[field] = invalid
                    self.assert_invalid()
            node[field] = previous

    def test_round_total_must_not_exceed_its_item_and_other_items_do_not_leak(self):
        first = self.index[ROUND]
        first['quantity'] = 600
        second = dict(first, id='delivery-round:demo:B-01:2', quantity=400)
        self.nodes.append(second)
        self.assertTrue(validate_input_graph(self.nodes)['valid'])
        second['quantity'] = 401
        self.assert_invalid()
        second['quantity'] = 400
        other_item = dict(self.index[ITEM], id='contract-item:demo:B-01:2', quantity=1)
        other_round = dict(first, id='delivery-round:demo:B-01:3', contract_item_id=other_item['id'], quantity=1)
        self.nodes.extend((other_item, other_round))
        self.assertTrue(validate_input_graph(self.nodes)['valid'])

    def test_contract_provenance_cannot_use_real_design_as_synthetic_contract_evidence(self):
        contract = self.index[CONTRACT]
        contract['evidence'] = self.index[CONFIGURATION]['evidence'][:]
        self.assert_invalid()
        contract['evidence'] = []
        self.assert_invalid()
        contract['origin'] = 'real'
        self.assert_invalid()

    def test_unapproved_factual_fields_are_still_rejected_on_new_types(self):
        for node_id in (CONTRACT, ITEM, ROUND):
            with self.subTest(node_id=node_id):
                node = self.index[node_id]
                node['good_quantity'] = 1000
                self.assert_invalid()
                del node['good_quantity']

    def test_shape_mutations_on_new_types_return_validation_reports(self):
        for node_id in (CONTRACT, ITEM, ROUND):
            node = self.index[node_id]
            for field in list(node):
                if field == 'id':
                    continue
                previous = node[field]
                for invalid in (None, [], [[]], {}, {'nested': []}):
                    with self.subTest(node_id=node_id, field=field, value=invalid):
                        node[field] = invalid
                        report = validate_input_graph(self.nodes)
                        self.assertIn('valid', report)
                        self.assertFalse(report['valid'], report)
                node[field] = previous

    def test_jsonld_and_generated_tbox_retain_contract_relationships(self):
        graph = b_graph_jsonld(self.nodes)
        exported = {node['id']: node for node in graph['@graph']}
        urn = 'urn:ontologics:entity:'
        self.assertEqual(urn + CONTRACT, exported[urn + ITEM]['contract_id'])
        self.assertEqual(urn + CONFIGURATION, exported[urn + ITEM]['configuration_id'])
        self.assertEqual(urn + ITEM, exported[urn + ROUND]['contract_item_id'])
        self.assertEqual('arrival', exported[urn + ROUND]['due_basis'])
        self.assertEqual(1000, exported[urn + ROUND]['quantity'])
        for key in ('contract_id', 'contract_item_id', 'configuration_id'):
            self.assertEqual({'@type': '@id'}, graph['@context'][key])
        turtle = b_tbox_turtle()
        for kind in ('Contract', 'ContractItem', 'DeliveryRound'):
            self.assertIn('o:' + kind + ' a owl:Class', turtle)
        self.assertIn('owl:versionInfo "' + SCHEMA_VERSION + '"', turtle)


class DeliveryToolSpecTests(unittest.TestCase):
    def test_new_read_tool_has_explicit_version_and_does_not_expand_existing_role_lists(self):
        tool = function_tools(('delivery_round',))[0]
        self.assertEqual('delivery_round', tool['name'])
        self.assertEqual({'delivery_round_id', 'version'}, set(tool['parameters']['required']))
        self.assertFalse(tool['parameters']['additionalProperties'])
        self.assertEqual({'type': 'integer', 'minimum': 0}, tool['parameters']['properties']['version'])
        self.assertNotIn('delivery_round', PRODUCT_TOOLS)
        self.assertNotIn('delivery_round', QUALITY_TOOLS)


if __name__ == '__main__':
    unittest.main()
