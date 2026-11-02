"""C phase-one RDF roundtrip and SHACL failures, without DB or model calls."""
import copy
import unittest

from rdflib import Literal, XSD

from ontologics.graph_rdf import O, entity_uri, validate_rdf
from ontologics.model import C_TYPES
from tests.claim_response import test_schema as fixtures
from tests.test_graph_rdf import snapshot


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
        for kind in C_TYPES:
            with self.subTest(kind=kind):
                self.assertEqual([n for n in self.nodes if n['type'] == kind],
                                 self.projected.lookup(kind=kind))
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
