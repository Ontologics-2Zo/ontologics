"""Pure B output composition, without graph validation, storage or file export."""
import copy
from ontologics.tbox import build_tbox
import unittest
from unittest.mock import patch

from ontologics import domain_schema, model
from tests.order_fulfillment.model_oracle import legacy_model, main_model

from ontologics.ingest import build_catalog
from ontologics.order_fulfillment import schema, schema_export
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment
from ontologics.order_fulfillment.production import load_demo_production

legacy = legacy_model()
REQUEST_TYPE = 'AdditionalMaterialReviewRequest'
REQUEST_CONTEXT = {key: {'@type': '@id'} for key in (
    'configuration_id', 'baseline_plan_evidence_id', 'contract_item_id', 'delivery_round_id')}
# PR #6 adds the C claim types to the common model; their reference keys join the common context.
C_CONTEXT = {key: {'@type': '@id'} for kind in domain_schema.C_TYPES
             for key in (*domain_schema.C_SCHEMA[kind][1], *domain_schema.LIST_REFERENCES.get(kind, {}))}


def additive_export(nodes):
    """Keep historical nodes/relations, adding explicit JSON literal context."""
    expected = legacy.graph_jsonld(nodes)
    expected['@context'].update(specifications={'@type': '@json'}, confirmations={'@type': '@json'})
    expected['@context'].update(C_CONTEXT)
    expected['@context'].update(REQUEST_CONTEXT)
    return expected


class BSchemaExportTests(unittest.TestCase):
    def test_adapters_reuse_common_exporters(self):
        self.assertIs(model.graph_jsonld, getattr(schema_export, '_common_graph_jsonld', None))
        self.assertIs(model.tbox_turtle, getattr(schema_export, '_common_tbox_turtle', None))

    def test_all_fixture_types_match_legacy_output_without_mutation(self):
        nodes = (build_catalog()['nodes'] + load_demo_contract() + load_demo_fulfillment()
                 + load_demo_production() + load_demo_delivery() + load_demo_delivery(mixed=True))
        self.assertEqual(set(schema.SCHEMA) - {REQUEST_TYPE},
                         {n['type'] for n in nodes} & set(schema.SCHEMA))
        self.assertIn(REQUEST_TYPE, schema.SCHEMA)
        self.assertNotIn(REQUEST_TYPE, {n['type'] for n in nodes},
                         'Historical fixtures must not gain invented request records')
        before = copy.deepcopy((nodes, model.SCHEMA, getattr(model, 'LIST_REFERENCES', {})))
        self.assertEqual(additive_export(nodes), schema_export.graph_jsonld(nodes))
        self.assertEqual(before, (nodes, model.SCHEMA, getattr(model, 'LIST_REFERENCES', {})))

    def test_shared_exporter_receives_full_graph_and_supplies_context(self):
        nodes = build_catalog()['nodes'][:2] + load_demo_contract()
        common = model.graph_jsonld(nodes)
        original = copy.deepcopy(common)
        def strict_export(rows):
            self.assertEqual(nodes, rows)
            return common
        with patch.object(schema_export, '_common_graph_jsonld', side_effect=strict_export) as exporter:
            actual = schema_export.graph_jsonld(nodes)
        exporter.assert_called_once()
        self.assertEqual(additive_export(nodes), actual)
        self.assertEqual(original, common)

    def test_iterator_order_and_duplicate_ids_are_preserved(self):
        common = dict(id='same', type='SourceDocument', evidence=[])
        b_node = dict(id='same', type='Contract', evidence=['e:first', 'e:second'])
        nodes = [b_node, common, dict(b_node, id='last'), common]
        self.assertEqual(additive_export(nodes), schema_export.graph_jsonld(iter(nodes)))
        self.assertEqual(4, len(schema_export.graph_jsonld(nodes)['@graph']))

    def test_empty_and_b_only_graphs_still_reuse_common_context(self):
        b_nodes = [node for node in load_demo_contract() if node['type'] in schema.SCHEMA]
        for nodes in ([], b_nodes):
            with self.subTest(nodes=len(nodes)):
                common = model.graph_jsonld(nodes)
                with patch.object(schema_export, '_common_graph_jsonld', return_value=common) as exporter:
                    self.assertEqual(additive_export(nodes), schema_export.graph_jsonld(nodes))
                exporter.assert_called_once_with(nodes)

    def test_role_context_cannot_override_shared_relation_meaning(self):
        key = next(iter(schema_export.jsonld_context()))
        result = model.graph_jsonld([])
        before = copy.deepcopy(result)
        with patch.object(schema_export, 'jsonld_context', return_value={key: {'@type': 'wrong'}}):
            self.assertEqual(before, schema_export.graph_jsonld([]))
        self.assertEqual(before, result)

    def test_unknown_types_keep_common_exporter_error(self):
        with self.assertRaises(KeyError):
            schema_export.graph_jsonld([dict(id='x', type='UnknownType', evidence=[])])

    def test_optional_list_and_evidence_urns_match_legacy(self):
        # Export-only fixtures: they do not claim graph validity or storage approval.
        nodes = [dict(id='allocation', type='Allocation', stock_id='stock', evidence=['e']),
                 dict(id='work-allocation', type='Allocation', production_work_id='work', evidence=[]),
                 dict(id='snapshot', type='FulfillmentSnapshot', round_ids=['r2', 'r1'], evidence=['e'])]
        self.assertEqual(additive_export(nodes), schema_export.graph_jsonld(nodes))
        rows = schema_export.graph_jsonld(nodes)['@graph']
        self.assertNotIn('production_work_id', rows[0])
        self.assertEqual(['urn:ontologics:entity:r2', 'urn:ontologics:entity:r1'], rows[2]['round_ids'])

    def test_tbox_preserves_common_bytes_version_and_historical_b_terms(self):
        common, actual = model.tbox_turtle(), schema_export.tbox_turtle()
        self.assertTrue(actual.startswith(common))
        self.assertIn('owl:versionInfo "' + model.SCHEMA_VERSION + '"', actual)
        b_names = set(schema.SCHEMA)
        for kind in tuple(b_names):
            if kind not in legacy.SCHEMA:
                self.assertEqual(REQUEST_TYPE, kind)
                continue
            b_names.update(legacy.SCHEMA[kind][1])
            b_names.update(legacy.LIST_REFERENCES.get(kind, {}))
        historical_terms = [line for line in legacy.tbox_turtle().splitlines()
                            if line.split(' ', 1)[0] in {'o:' + name for name in b_names}]
        for term in historical_terms:
            self.assertEqual(1, actual.splitlines().count(term), term)
        if model.SCHEMA == legacy.SCHEMA:
            self.assertEqual(legacy.tbox_turtle(), actual)

    def test_missing_b_terms_are_appended_once_without_rewriting_common_text(self):
        terms = schema_export.tbox_terms().splitlines()
        removed = set(terms[::2])
        common = '\n'.join(line for line in legacy.tbox_turtle().splitlines() if line not in removed) + '\n'
        with patch.object(schema_export, '_common_tbox_turtle', return_value=common):
            actual = schema_export.tbox_turtle()
        self.assertTrue(actual.startswith(common))
        self.assertEqual(common + ''.join(line + '\n' for line in terms
                                         if line not in common.splitlines()), actual)
        for term in terms:
            self.assertEqual(1, actual.splitlines().count(term))

    def test_conflicting_b_class_or_property_declarations_are_rejected(self):
        terms = schema_export.tbox_terms().splitlines()
        for term in (terms[0], next(t for t in terms if 'ObjectProperty' in t)):
            with self.subTest(term=term):
                wrong = term.replace('owl:Class', 'owl:ObjectProperty') if 'owl:Class' in term else term.replace('rdfs:range o:', 'rdfs:range o:Wrong')
                common = legacy.tbox_turtle().replace(term, wrong)
                with patch.object(schema_export, '_common_tbox_turtle', return_value=common):
                    with self.assertRaisesRegex(ValueError, 'declaration'):
                        schema_export.tbox_turtle()


class BExtensionArtifactTests(unittest.TestCase):
    def test_stored_extension_matches_b_schema_declarations(self):
        stored = (model.ROOT / 'ontology/tbox.ttl').read_text()
        # The artifact is shared: exact common+A+B equality and B preservation.
        self.assertEqual(build_tbox(), stored)
        declarations = [line for line in stored.splitlines() if ' a owl:Class ' in line]
        terms = schema_export.tbox_terms().splitlines()
        self.assertEqual(len(declarations), len(set(declarations)))
        self.assertTrue(set(schema.SCHEMA) <= {
            line.split()[0][2:] for line in declarations})
        for term in terms:
            self.assertEqual(1, stored.splitlines().count(term))

    def test_extension_preserves_common_prefixes_and_ontology_identity(self):
        stored = (model.ROOT / 'ontology/tbox.ttl').read_text()
        prefixes = [line for line in stored.splitlines() if line.startswith('@prefix ')]
        self.assertEqual([
            '@prefix o: <urn:ontologics:> .',
            '@prefix owl: <http://www.w3.org/2002/07/owl#> .',
            '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .',
        ], prefixes)
        self.assertEqual([line for line in build_tbox().splitlines() if 'owl:versionInfo' in line],
                         [line for line in stored.splitlines() if 'owl:versionInfo' in line])
        self.assertEqual(1, stored.count(' a owl:Ontology'))
        common = model.tbox_turtle()
        self.assertTrue(stored.startswith(common))
        self.assertIn('owl:versionInfo "' + model.SCHEMA_VERSION + '"', stored)


class CommonArtifactBoundaryTests(unittest.TestCase):
    """Keep common model/file obligations separate from B composed output."""

    def test_additive_common_schema_preserves_pinned_core_contract(self):
        expected = main_model()
        with self.subTest(contract='version'):
            self.assertEqual('graph-0.1', expected.SCHEMA_VERSION)
            self.assertEqual('graph-0.4', model.SCHEMA_VERSION)
        with self.subTest(contract='types_fields_references'):
            self.assertLessEqual(set(expected.SCHEMA), set(model.SCHEMA))
            for kind, definition in expected.SCHEMA.items():
                self.assertEqual(definition, model.SCHEMA[kind])
            # Existing 11 A/B types + C claim types + one B request.
            self.assertEqual(12 + len(domain_schema.C_SCHEMA),
                             len(set(model.SCHEMA) - set(expected.SCHEMA)))
            self.assertIn(REQUEST_TYPE, model.SCHEMA)
            self.assertNotIn(REQUEST_TYPE, expected.SCHEMA)

    def test_stored_tbox_preserves_main_and_matches_shared_export(self):
        stored = (model.ROOT / 'ontology/tbox.ttl').read_text()
        common = model.tbox_turtle()
        with self.subTest(exporter='active_common'):
            self.assertTrue(stored.startswith(common))
        with self.subTest(exporter='pinned_main'):
            for line in main_model().tbox_turtle().splitlines():
                if 'owl:versionInfo' not in line:
                    self.assertIn(line, common.splitlines())
        with self.subTest(exporter='common_plus_a_plus_b'):
            self.assertEqual(build_tbox(), stored)


if __name__ == '__main__':
    unittest.main()
