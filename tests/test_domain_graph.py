"""Shared registration preserves core facts and rejects unsafe A/B candidates."""
import copy
import subprocess
import sys
import unittest

from ontologics import domain_schema, model
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment
from ontologics.order_fulfillment.production import load_demo_production
from tests.production_planning.test_ontology_extension import catalog, planning_nodes


class SharedDomainGraphTests(unittest.TestCase):
    def mixed(self, extra=None):
        return catalog() + planning_nodes() + load_demo_contract() + (
            load_demo_fulfillment() if extra is None else extra)

    def codes(self, nodes):
        return {e['code'] for e in model.validate_graph(nodes)['errors']}

    def test_original_facts_unchanged_and_all_reviewed_types_validate_together(self):
        core = catalog()
        before = model.digest(core)
        self.assertTrue(model.validate_graph(core)['valid'])
        self.assertEqual(before, model.digest(core))
        observed = set()
        for extra in ([], load_demo_fulfillment(), load_demo_production(),
                      load_demo_delivery(), load_demo_delivery(mixed=True)):
            with self.subTest(types=sorted({n['type'] for n in extra})):
                nodes = self.mixed(extra)
                original = copy.deepcopy(nodes)
                report = model.validate_graph(nodes)
                self.assertTrue(report['valid'], report)
                self.assertEqual(nodes, original)
                observed.update(n['type'] for n in nodes)
        self.assertEqual(set(domain_schema.SCHEMA), observed & set(domain_schema.SCHEMA))
        self.assertEqual(len(domain_schema.SCHEMA), 11)
        self.assertEqual(model.SCHEMA_VERSION, 'graph-0.2')

    def test_shared_contract_rules_run_once_and_reject_bool_quantities(self):
        nodes = self.mixed([])
        item = next(n for n in nodes if n['type'] == 'ContractItem')
        item['quantity'] = True
        errors = model.validate_graph(nodes)['errors']
        matches = [e for e in errors if e['node_id'] == item['id'] and e['code'] == 'INVALID_QUANTITY']
        self.assertEqual(len(matches), 1, errors)

    def test_optional_allocation_reference_absence_differs_from_null_or_two_targets(self):
        nodes = self.mixed()
        allocation = next(n for n in nodes if n['type'] == 'Allocation' and 'stock_id' in n)
        self.assertNotIn('production_work_id', allocation)
        self.assertTrue(model.validate_graph(nodes)['valid'])
        allocation['production_work_id'] = None
        self.assertTrue({'INVALID_REFERENCE', 'INVALID_ALLOCATION_TARGET'} <= self.codes(nodes))
        allocation.pop('production_work_id')
        allocation.pop('stock_id')
        self.assertIn('INVALID_ALLOCATION_TARGET', self.codes(nodes))

    def test_round_list_and_confirmation_object_are_validated_not_free_text(self):
        for changes, code in (({'round_ids': []}, 'INVALID_REFERENCE'),
                              ({'round_ids': [None]}, 'INVALID_REFERENCE'),
                              ({'confirmations': []}, 'INVALID_CONFIRMATIONS'),
                              ({'confirmations': {'invented': True}}, 'INVALID_CONFIRMATIONS'),
                              ({'confirmations': {'allocations_complete': 1}}, 'INVALID_CONFIRMATIONS')):
            nodes = self.mixed()
            snapshot = next(n for n in nodes if n['type'] == 'FulfillmentSnapshot')
            snapshot.update(changes)
            with self.subTest(changes=changes):
                self.assertIn(code, self.codes(nodes))

    def test_unknown_component_stock_keeps_null_and_boolean_distinctions(self):
        nodes = self.mixed([])
        stock = next(n for n in nodes if n['type'] == 'ComponentStockSnapshot')
        stock.update(availability_basis='reported_available', quantity_state='unknown',
                     available_quantity=None, deductions_disjoint=None)
        report = model.validate_graph(nodes)
        self.assertTrue(report['valid'], report)
        self.assertIn({'node_id': stock['id'], 'code': 'UNKNOWN_QUANTITY'}, report['warnings'])
        exported = next(n for n in model.graph_jsonld(nodes)['@graph']
                        if n['id'] == 'urn:ontologics:entity:' + stock['id'])
        self.assertIn('available_quantity', exported)
        self.assertIsNone(exported['available_quantity'])
        stock['quantity_state'] = 'known'
        self.assertIn('INVALID_QUANTITY', self.codes(nodes))
        stock.update(available_quantity=True, deductions_disjoint=1)
        self.assertTrue({'INVALID_QUANTITY', 'INVALID_DEDUCTION_FLAG'} <= self.codes(nodes))

    def test_extension_origin_evidence_and_counter_rules_remain_enforced(self):
        nodes = self.mixed(load_demo_production())
        observation = next(n for n in nodes if n['type'] == 'ProductionObservation')
        observation.update(origin='real', evidence=['missing'], produced_quantity=-1)
        self.assertTrue({'DEMO_OPERATIONS_ONLY', 'EVIDENCE_NOT_FOUND', 'INVALID_QUANTITY'} <= self.codes(nodes))

    def test_jsonld_encodes_list_relations_and_json_without_manufacturing_optional_refs(self):
        nodes = self.mixed()
        export = model.graph_jsonld(nodes)
        self.assertEqual(export['@context']['round_ids'], {'@type': '@id'})
        self.assertEqual(export['@context']['confirmations'], {'@type': '@json'})
        snapshot = next(n for n in nodes if n['type'] == 'FulfillmentSnapshot')
        mapped = next(n for n in export['@graph'] if n['id'] == 'urn:ontologics:entity:' + snapshot['id'])
        self.assertEqual(mapped['round_ids'], ['urn:ontologics:entity:' + rid for rid in snapshot['round_ids']])
        self.assertEqual(mapped['confirmations'], snapshot['confirmations'])
        allocation = next(n for n in export['@graph'] if n['type'] == 'Allocation' and 'stock_id' in n)
        self.assertNotIn('production_work_id', allocation)
        self.assertIn('o:round_ids a owl:ObjectProperty ; rdfs:range o:DeliveryRound .', model.tbox_turtle())

    def test_cold_imports_and_detached_role_metadata_do_not_mutate_shared_schema(self):
        for module in ('ontologics.model', 'ontologics.production_planning.schema',
                       'ontologics.order_fulfillment.schema_validation'):
            result = subprocess.run([sys.executable, '-c',
                'import importlib,sys; importlib.import_module(sys.argv[1]); '
                'from ontologics import model; '
                'from ontologics.production_planning import schema as a; '
                'from ontologics.order_fulfillment import schema as b; '
                'assert len(model.SCHEMA)==23; '
                'a.SCHEMA["Contract"][0].append("private"); '
                'assert "private" not in model.SCHEMA["Contract"][0]; '
                'assert "private" not in b.SCHEMA["Contract"][0]', module],
                cwd=model.ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
