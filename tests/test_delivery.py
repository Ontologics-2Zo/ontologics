"""B-CQ01 graph boundaries and real PostgreSQL registration/query behavior."""
from __future__ import annotations

import copy
import json
import unittest

from ontologics.contracts import load_demo_contract
from ontologics.demo import PRINCIPALS
from ontologics.model import ROOT, SCHEMA_VERSION, graph_jsonld, tbox_turtle, validate_graph
from ontologics.service import AgentGateway, Service
from ontologics.tool_specs import PRODUCT_TOOLS, QUALITY_TOOLS, function_tools
from tests.db import database_for_test


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
        report = validate_graph(self.nodes)
        self.assertFalse(report['valid'], report)
        self.assertTrue(report['errors'])
        return report

    def test_new_contract_graph_is_valid_with_real_configuration_and_synthetic_promise(self):
        report = validate_graph(self.nodes)
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
        self.assertTrue(validate_graph(self.nodes)['valid'])

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
        self.assertTrue(validate_graph(self.nodes)['valid'])
        second['quantity'] = 401
        self.assert_invalid()
        second['quantity'] = 400
        other_item = dict(self.index[ITEM], id='contract-item:demo:B-01:2', quantity=1)
        other_round = dict(first, id='delivery-round:demo:B-01:3', contract_item_id=other_item['id'], quantity=1)
        self.nodes.extend((other_item, other_round))
        self.assertTrue(validate_graph(self.nodes)['valid'])

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
                        report = validate_graph(self.nodes)
                        self.assertIn('valid', report)
                        self.assertFalse(report['valid'], report)
                node[field] = previous

    def test_jsonld_and_generated_tbox_retain_contract_relationships(self):
        graph = graph_jsonld(self.nodes)
        exported = {node['id']: node for node in graph['@graph']}
        urn = 'urn:ontologics:entity:'
        self.assertEqual(urn + CONTRACT, exported[urn + ITEM]['contract_id'])
        self.assertEqual(urn + CONFIGURATION, exported[urn + ITEM]['configuration_id'])
        self.assertEqual(urn + ITEM, exported[urn + ROUND]['contract_item_id'])
        self.assertEqual('arrival', exported[urn + ROUND]['due_basis'])
        self.assertEqual(1000, exported[urn + ROUND]['quantity'])
        for key in ('contract_id', 'contract_item_id', 'configuration_id'):
            self.assertEqual({'@type': '@id'}, graph['@context'][key])
        turtle = tbox_turtle()
        for kind in ('Contract', 'ContractItem', 'DeliveryRound'):
            self.assertIn('o:' + kind + ' a owl:Class', turtle)
        self.assertIn('owl:versionInfo "' + SCHEMA_VERSION + '"', turtle)


class DeliveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals=PRINCIPALS)
        self.addCleanup(self.service.close)
        self.catalog = catalog_nodes()
        self.contract = load_demo_contract()
        self.seed = self.service.bootstrap('seed-admin', self.catalog, request_key='delivery-seed')
        self.assertTrue(self.seed['ok'], self.seed)
        self.gateway = AgentGateway(self.service, 'product-agent')

    def error_is(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def prepare(self, nodes=None, key='contract-draft'):
        operations = [{'op': 'put', 'node': node} for node in (self.contract if nodes is None else nodes)]
        response = self.gateway.call('prepare_draft', {'operations': operations, 'request_key': key})
        self.assertTrue(response['ok'], response)
        return response['data']

    def register(self):
        draft = self.prepare()
        self.assertTrue(draft['validation']['valid'], draft)
        reviewed = self.service.review('reviewer', draft['draft_id'], draft['draft_version'],
                                      decision='approve', note='Test-only synthetic source review', request_key='contract-review')
        self.assertTrue(reviewed['ok'], reviewed)
        applied = self.service.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='contract-apply')
        self.assertTrue(applied['ok'], applied)
        return applied['data_version']

    def test_confirmed_promise_requires_review_and_matches_golden_meaning(self):
        before = self.seed['data_version']
        self.error_is(self.service.delivery_round('product-agent', ROUND, version=before), 'NOT_FOUND')
        draft = self.prepare()
        self.assertEqual('awaiting_review', draft['status'])
        validation = self.gateway.call('validate_draft', {'draft_id': draft['draft_id'], 'version': draft['draft_version']})
        self.assertTrue(validation['data']['validation']['valid'], validation)
        blocked = self.service.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='before-approval')
        self.error_is(blocked, 'APPROVAL_REQUIRED')
        self.error_is(self.service.delivery_round('product-agent', ROUND, version=before), 'NOT_FOUND')
        reviewed = self.service.review('reviewer', draft['draft_id'], draft['draft_version'],
                                      decision='approve', note='Synthetic example only', request_key='approve')
        self.assertTrue(reviewed['ok'], reviewed)
        applied = self.service.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='apply')
        self.assertTrue(applied['ok'], applied)
        result = self.gateway.call('delivery_round', {'delivery_round_id': ROUND, 'version': applied['data_version']})
        self.assertTrue(result['ok'], result)
        self.assertEqual(applied['data_version'], result['data_version'])
        data = result['data']
        self.assertEqual('DEMO-B', data['contract']['company'])
        self.assertEqual('가상고객사 A', data['contract']['customer_name'])
        self.assertEqual(CONFIGURATION, data['configuration']['id'])
        self.assertEqual('ESP32-POE Rev L WROOM', data['configuration']['name'])
        self.assertEqual(1000, data['contract_item']['quantity'])
        self.assertEqual(1000, data['delivery_round']['quantity'])
        self.assertEqual('piece', data['delivery_round']['unit'])
        self.assertEqual('2026-10-27T15:00:00+09:00', data['delivery_round']['due_at'])
        self.assertEqual('arrival', data['delivery_round']['due_basis'])
        self.assertEqual('가상고객사 A 수령창고', data['delivery_round']['destination'])
        self.assertFalse(data['fulfillment_assessed'])
        self.assertIn('FULFILLMENT_NOT_ASSESSED', [warning['code'] for warning in result['warnings']])
        expected_evidence = set()
        for key in ('contract', 'contract_item', 'delivery_round', 'configuration'):
            expected_evidence.update(data[key]['evidence'])
        self.assertTrue(expected_evidence.issubset(result['evidence']))
        for key in ('schema_version', 'rule_version', 'contract_version', 'run_id'):
            self.assertTrue(result[key])
        self.error_is(self.service.delivery_round('product-agent', ROUND, version=before), 'NOT_FOUND')
        configuration = self.service.configuration('product-agent', CONFIGURATION, version=applied['data_version'])
        self.assertEqual(74, len(configuration['data']['items']))

    def test_lookup_requires_explicit_valid_version_and_exact_round_id(self):
        version = self.register()
        for invalid in (None, True, False, -1, 1.5, '2', [], {}):
            with self.subTest(version=invalid):
                response = self.service.delivery_round('product-agent', ROUND, version=invalid)
                self.error_is(response, 'INVALID_VERSION')
        self.error_is(self.service.delivery_round('product-agent', ROUND, version=version + 100), 'VERSION_NOT_FOUND')
        self.error_is(self.service.delivery_round('product-agent', 'delivery-round:missing', version=version), 'NOT_FOUND')
        self.error_is(self.service.delivery_round('product-agent', ITEM, version=version), 'NOT_FOUND')
        for invalid in ('', '   ', [], {}):
            with self.subTest(delivery_round_id=invalid):
                self.error_is(self.service.delivery_round('product-agent', invalid, version=version), 'INVALID_INPUT')

    def test_invalid_contract_bundle_cannot_be_approved_or_change_confirmed_version(self):
        invalid = copy.deepcopy(self.contract)
        next(node for node in invalid if node['id'] == ROUND)['quantity'] = 1001
        draft = self.prepare(invalid)
        self.assertFalse(draft['validation']['valid'], draft)
        self.assertEqual('needs_changes', draft['status'])
        self.error_is(self.service.review('reviewer', draft['draft_id'], draft['draft_version'],
                                         decision='approve', note='Must fail', request_key='reject-invalid'), 'VALIDATION_FAILED')
        self.error_is(self.service.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='invalid-apply'), 'APPROVAL_REQUIRED')
        self.assertEqual(self.seed['data_version'], self.service.lookup('product-agent')['data_version'])
        self.error_is(self.service.delivery_round('product-agent', ROUND, version=self.seed['data_version']), 'NOT_FOUND')

    def test_existing_source_and_evidence_cannot_be_overwritten_after_registration(self):
        version = self.register()
        for kind, field in (('SourceDocument', 'license'), ('Evidence', 'description')):
            node = copy.deepcopy(next(node for node in self.contract if node['type'] == kind))
            node[field] += ' changed'
            result = self.gateway.call('prepare_draft', {
                'operations': [{'op': 'put', 'node': node}], 'request_key': 'overwrite-' + kind})
            self.error_is(result, 'IMMUTABLE_SOURCE')
        self.assertEqual(version, self.service.lookup('product-agent')['data_version'])

    def test_gateway_keeps_read_permission_identity_and_privileged_tool_boundaries(self):
        version = self.register()
        arguments = {'delivery_round_id': ROUND, 'version': version}
        self.error_is(AgentGateway(self.service, 'unknown-identity').call('delivery_round', arguments), 'FORBIDDEN')
        self.error_is(self.gateway.call('delivery_round', dict(arguments, actor='reviewer')), 'INVALID_TOOL_ARGUMENTS')
        self.error_is(self.gateway.call('delivery_round', dict(arguments, principals={})), 'INVALID_TOOL_ARGUMENTS')
        for tool in ('review', 'apply', 'bootstrap'):
            self.error_is(self.gateway.call(tool, {}), 'TOOL_NOT_ALLOWED')
        self.error_is(self.gateway.call('delivery_round', {'delivery_round_id': ROUND}), 'INVALID_VERSION')
        history = self.service.run_history('seed-admin')
        self.assertTrue(history['ok'], history)
        delivery_calls = [row for row in history['data'] if row['tool'] == 'delivery_round']
        self.assertTrue(delivery_calls)
        self.assertTrue(any(row['status'] == 'failed' for row in delivery_calls))


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
