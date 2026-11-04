"""Local reference validation must not weaken common graph checks."""
import copy
import unittest

from ontologics import model
from ontologics.production_planning.graph_validation import validate_graph
from tests.production_planning.test_ontology_extension import catalog, planning_nodes


class LocalGraphValidationTests(unittest.TestCase):
    def setUp(self):
        self.nodes = catalog() + planning_nodes()

    def codes(self, nodes):
        return {item['code'] for item in validate_graph(nodes)['errors']}

    def test_valid_reference_uses_shared_types_without_mutating_input(self):
        before = copy.deepcopy(self.nodes)
        self.assertTrue(validate_graph(self.nodes)['valid'])
        self.assertEqual(self.nodes, before)
        self.assertIn('Contract', model.SCHEMA)
        self.assertEqual(validate_graph(self.nodes), model.validate_graph(self.nodes))

    def test_unknown_and_malformed_nodes_are_not_filtered_out(self):
        for node, code in [(None, 'INVALID_NODE'),
                           ({'id': 'unknown', 'type': 'Unknown'}, 'UNKNOWN_TYPE'),
                           ({'id': 'bad', 'type': []}, 'UNKNOWN_TYPE')]:
            with self.subTest(code=code):
                self.assertIn(code, self.codes(self.nodes + [node]))

    def test_duplicate_ids_across_common_and_a_are_rejected(self):
        node = next(n for n in self.nodes if n['type'] == 'Contract')
        self.nodes.append(dict(node, id=self.nodes[0]['id']))
        self.assertIn('DUPLICATE_ID', self.codes(self.nodes))

    def test_common_integrity_and_a_evidence_and_relations_are_checked(self):
        source = next(n for n in self.nodes if n['type'] == 'SourceDocument')
        source['sha256'] = '0' * 64
        item = next(n for n in self.nodes if n['type'] == 'ContractItem')
        item.update(configuration_id='missing', evidence=['missing'])
        self.assertTrue({'SOURCE_INTEGRITY', 'INVALID_REFERENCE', 'EVIDENCE_NOT_FOUND'}
                        <= self.codes(self.nodes))

    def test_common_only_results_are_unchanged(self):
        nodes = catalog()
        self.assertEqual(validate_graph(nodes), model.validate_graph(nodes))
        nodes.append(copy.deepcopy(nodes[0]))
        self.assertEqual(validate_graph(nodes), model.validate_graph(nodes))
