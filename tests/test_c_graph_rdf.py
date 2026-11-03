"""C source-preserving RDF roundtrips and SHACL failures, without DB or models."""
import copy
import unittest

from rdflib import Literal, XSD

from ontologics.graph_rdf import O, entity_uri, validate_rdf
from ontologics.domain_schema import C2_QUANTITIES, C2_TYPES
from ontologics.model import C_TYPES
from tests.claim_response import test_schema as fixtures
from tests.claim_response import test_schema_phase2 as phase2_fixtures
from tests.test_graph_rdf import snapshot


def phase2_nodes(add_cleanup):
    """Existing synthetic C2 records, with two explicitly named replacement boards.

    Both RDF and approval tests use the same source-backed fixture. Its tests
    remain in their role module; importing a TestCase here would collect it twice.
    """
    fixture = phase2_fixtures.CPhase2ValidationTests
    fixture.setUpClass()
    add_cleanup(fixture.doClassCleanups)
    nodes = copy.deepcopy(fixture.base + fixture.c2_nodes)
    nodes.append(phase2_fixtures.unit('C-R002'))
    requirement = next(n for n in nodes if n['type'] == 'ReproductionRequirement')
    requirement.update(available_replacement_quantity=2, required_good_quantity=0,
                       replacement_unit_ids=['unit:test-C-R002', 'unit:test-C-R001'])
    return nodes


class ClaimRdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = fixtures.CPhase1ValidationTests
        fixture.setUpClass()
        cls.addClassCleanup(fixture.doClassCleanups)
        cls.nodes = copy.deepcopy(fixture.real_nodes + fixture.c_nodes)
        intake = next(n for n in cls.nodes if n['type'] == 'ClaimIntake')
        # A text list is not an ID relationship, and its order belongs to the payload.
        intake.update(claimed_quantity=2, reported_serials=['C-S001', 'C-D001'])
        cls.projected = snapshot(cls.nodes)

    def test_c_types_and_multiple_reported_serials_roundtrip_exactly(self):
        self.assertEqual(self.nodes, self.projected.nodes())
        for kind in (kind for kind in C_TYPES if kind not in C2_TYPES):
            with self.subTest(kind=kind):
                expected = [n for n in self.nodes if n['type'] == kind]
                self.assertTrue(expected)
                self.assertEqual(expected, self.projected.lookup(kind=kind))
        graph = self.projected.rdf_graph()
        intake = entity_uri('claim-intake:test')
        self.assertEqual({Literal('C-S001'), Literal('C-D001')}, set(graph.objects(intake, O.reported_serials)))
        self.assertEqual(XSD.integer, graph.value(intake, O.claimed_quantity).datatype)
        case = entity_uri('quality-case:test')
        self.assertEqual({entity_uri('unit:test-C-D001')}, set(graph.objects(case, O.unit_ids)))

    def test_c_numeric_text_list_and_reference_corruption_is_rejected(self):
        mutations = [
            ('claim-intake:test', 'claimed_quantity', Literal(0)),
            ('claim-intake:test', 'claimed_quantity', Literal(1.5)),
            ('claim-intake:test', 'claimed_quantity', Literal(True)),
            ('containment:test-production-stop', 'draft_version', Literal(0)),
            ('containment:test-production-stop', 'draft_version', Literal('1')),
            ('claim-intake:test', 'reported_serials', Literal('')),
            ('claim-intake:test', 'reported_serials', Literal(123)),
            ('claim-intake:test', 'reported_serials', None),
            ('quality-scope:test-hold', 'unit_ids', entity_uri('production-line:test-C-01')),
            ('quality-case:test', 'origin', Literal('real')),
        ]
        for nid, field, value in mutations:
            with self.subTest(node=nid, field=field, value=str(value)):
                graph = self.projected.rdf_graph()
                graph.remove((entity_uri(nid), O[field], None))
                if value is not None:
                    graph.add((entity_uri(nid), O[field], value))
                self.assertFalse(validate_rdf(graph)['valid'])

    def test_optional_absence_and_payload_tampering_remain_distinct(self):
        graph = self.projected.rdf_graph()
        self.assertFalse(list(graph.objects(entity_uri('containment:test-shipment-hold'), O.draft_version)))
        self.assertFalse(list(graph.objects(entity_uri('production:test-C-D001'), O.production_work_id)))
        modified = snapshot(self.nodes)
        modified._graph.set((entity_uri('claim-intake:test'), O.reported_serials, Literal('other-serial')))
        # Structurally valid text still cannot replace the approved payload value.
        self.assertTrue(validate_rdf(modified.rdf_graph())['valid'])
        with self.assertRaisesRegex(ValueError, 'RDF field disagrees'):
            modified.lookup(node_id='claim-intake:test')


class ClaimPhase2RdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = phase2_nodes(cls.addClassCleanup)
        cls.projected = snapshot(cls.nodes)

    def test_c2_nonempty_reads_quantities_and_two_replacement_boards_roundtrip(self):
        self.assertEqual(self.nodes, self.projected.nodes())
        for kind in C_TYPES:
            with self.subTest(kind=kind):
                expected = [n for n in self.nodes if n['type'] == kind]
                self.assertTrue(expected)
                self.assertEqual(expected, self.projected.lookup(kind=kind))
        graph = self.projected.rdf_graph()
        requirement = entity_uri('reproduction:test')
        for field in C2_QUANTITIES:
            self.assertEqual(XSD.integer, graph.value(requirement, O[field]).datatype, field)
        self.assertEqual(XSD.integer,
                         graph.value(entity_uri('test-request:test'), O.draft_version).datatype)
        self.assertEqual({Literal('unit:test-C-R001'), Literal('unit:test-C-R002')},
                         set(graph.objects(requirement, O.replacement_unit_ids)))
        self.assertEqual({entity_uri('test-result:C-D001'), entity_uri('test-result:C-S001'),
                          entity_uri('test-result:C-S002')},
                         set(graph.objects(requirement, O.test_result_ids)))

    def test_c2_invalid_numeric_list_and_reference_values_are_rejected_by_shacl(self):
        mutations = [('reproduction:test', field, Literal(-1)) for field in C2_QUANTITIES]
        mutations += [
            ('reproduction:test', 'required_good_quantity', Literal(True)),
            ('reproduction:test', 'required_good_quantity', Literal(0.5)),
            ('reproduction:test', 'required_good_quantity', Literal('0')),
            ('test-request:test', 'draft_version', Literal(0)),
            ('test-request:test', 'draft_version', Literal('1')),
            ('reproduction:test', 'replacement_unit_ids', Literal('')),
            ('reproduction:test', 'replacement_unit_ids', Literal(123)),
            ('reproduction:test', 'test_result_ids', entity_uri('unit:test-C-D001')),
            ('test-request:test', 'unit_ids', entity_uri('production-line:test-C-01')),
            ('test-result:C-D001', 'origin', Literal('real')),
        ]
        for nid, field, value in mutations:
            with self.subTest(node=nid, field=field, value=str(value)):
                graph = self.projected.rdf_graph()
                graph.remove((entity_uri(nid), O[field], None))
                graph.add((entity_uri(nid), O[field], value))
                self.assertFalse(validate_rdf(graph)['valid'])

    def test_c2_optional_absence_and_approved_payload_integrity(self):
        nodes = copy.deepcopy(self.nodes)
        next(n for n in nodes if n['type'] == 'TestRequest').pop('draft_version')
        next(n for n in nodes if n['type'] == 'ReproductionRequirement').pop('replacement_unit_ids')
        self.assertEqual(nodes, snapshot(nodes).nodes())
        changed = snapshot(self.nodes)
        changed._graph.set((entity_uri('reproduction:test'), O.replacement_unit_ids,
                            Literal('unit:test-C-S001')))
        self.assertTrue(validate_rdf(changed.rdf_graph())['valid'])
        with self.assertRaisesRegex(ValueError, 'RDF field disagrees'):
            changed.lookup(node_id='reproduction:test')
