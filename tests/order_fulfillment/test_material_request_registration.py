"""Shared request transition boundary and RDF parity, without DB writes."""
from copy import deepcopy
import json
import unittest

from examples.order_fulfillment.b_material_request import load_examples
from ontologics.model import ROOT, SCHEMA_VERSION, validate_graph
from ontologics.service import DomainError, Service
from ontologics.graph_rdf import RdfSnapshot, O, XSD
from ontologics.temporal import RULE_VERSION


class RequestRegistrationBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = load_examples()
        cls.facts = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + cls.demo['supporting_nodes']

    def setUp(self):
        self.new = deepcopy(self.demo['requests']['new_30'])
        self.revised = deepcopy(self.demo['requests']['revised_40'])
        self.service = Service.__new__(Service)

    def candidate(self, nodes, base, requests):
        self.service._nodes = lambda version: deepcopy(nodes)
        return self.service._candidate([{'op': 'put', 'node': node} for node in requests], base)

    def rejected(self, nodes, base, request, code):
        with self.assertRaises(DomainError) as caught:
            self.candidate(nodes, base, [request])
        self.assertEqual('VALIDATION_FAILED', caught.exception.code)
        self.assertIn(code, {e['code'] for e in caught.exception.details['errors']})

    def test_initial_request_uses_actual_base_and_first_revision(self):
        self.new['reference_data_version'] = 7
        nodes, changes = self.candidate(self.facts, 7, [self.new])
        self.assertTrue(validate_graph(nodes)['valid'])
        self.assertEqual([self.new['id']], [c['id'] for c in changes])
        self.rejected(self.facts, 7, dict(self.new, request_revision=2), 'INVALID_REQUEST_REVISION')
        for value in (0, 6, 8, True, 7.0):
            self.rejected(self.facts, 7, dict(self.new, reference_data_version=value), 'REFERENCE_VERSION_MISMATCH')

    def test_generic_candidate_cannot_skip_revision_or_new_evidence(self):
        previous = self.facts + [self.new]
        for revision in (1, 3):
            self.rejected(previous, 1, dict(self.revised, request_revision=revision,
                          reference_data_version=1), 'INVALID_REQUEST_REVISION')
        self.rejected(previous, 1, dict(self.revised, reference_data_version=1,
                      evidence=self.new['evidence']), 'NEW_EVIDENCE_REQUIRED')

    def test_valid_replacement_and_independent_request_keep_distinct_quantities(self):
        self.revised['reference_data_version'] = 1
        independent = deepcopy(self.demo['requests']['independent_10'])
        independent['reference_data_version'] = 1
        nodes, changes = self.candidate(self.facts + [self.new], 1, [self.revised, independent])
        self.assertEqual({self.revised['id']: 40, independent['id']: 10},
                         {n['id']: n['quantity'] for n in nodes if n['type'] == self.new['type']})
        self.assertEqual(2, len(changes))

    def test_changed_scope_is_rejected_by_shared_candidate(self):
        self.rejected(self.facts + [self.new], 1,
                      dict(self.revised, reference_data_version=1, need_date='2026-11-07'), 'REQUEST_SCOPE_CHANGED')

    def test_supplementation_can_fill_unknown_baseline_and_need(self):
        for missing in ('baseline', 'need', 'both'):
            with self.subTest(missing=missing):
                previous = deepcopy(self.new)
                previous.update(readiness='needs_supplementation',
                                supplementation_reason='기준 계획·필요 날짜 확인 대기')
                if missing in ('baseline', 'both'):
                    previous.pop('baseline_plan_evidence_id')
                if missing in ('need', 'both'):
                    previous['need_mode'] = 'unknown'
                    for field in ('need_basis', 'need_date', 'need_at'):
                        previous.pop(field, None)
                self.assertTrue(validate_graph(self.facts + [previous])['valid'])
                amended = dict(self.revised, reference_data_version=1)
                nodes, changes = self.candidate(self.facts + [previous], 1, [amended])
                self.assertTrue(validate_graph(nodes)['valid'])
                self.assertEqual('ready', changes[0]['after']['readiness'])
                self.assertEqual(previous, changes[0]['before'])

    def test_supplementation_cannot_erase_or_change_confirmed_scope(self):
        previous = dict(self.new, readiness='needs_supplementation',
                        supplementation_reason='변경 조건 확인 대기', conditions_state='unknown')
        for fields in ({'need_date': '2026-11-07'},
                       {'configuration_id': 'config:esp32-poe:L1:WROOM'},
                       {'baseline_plan_evidence_id': self.revised['evidence'][-1]},
                       {'need_mode': 'unknown'}):
            with self.subTest(fields=fields):
                amended = dict(self.revised, reference_data_version=1, **fields)
                self.rejected(self.facts + [previous], 1, amended, 'REQUEST_SCOPE_CHANGED')

    def test_unchanged_historical_request_does_not_need_current_reference_version(self):
        independent = deepcopy(self.demo['requests']['independent_10'])
        independent['reference_data_version'] = 12
        nodes, changes = self.candidate(self.facts + [self.new], 12, [self.new, independent])
        self.assertEqual([independent['id']], [c['id'] for c in changes])
        self.assertEqual(0, next(n for n in nodes if n['id'] == self.new['id'])['reference_data_version'])

    def test_rdf_and_shacl_accept_integer_request_and_preserve_date_only(self):
        snapshot = RdfSnapshot(self.facts + [self.new], version=0,
                               schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)
        from ontologics.graph_rdf import entity_uri
        for field in ('quantity', 'request_revision', 'reference_data_version'):
            term = snapshot._graph.value(entity_uri(self.new['id']), O[field])
            self.assertEqual(XSD.integer, term.datatype)
            self.assertEqual(self.new[field], int(term))
        self.assertEqual(self.new['need_date'],
                         str(snapshot._graph.value(entity_uri(self.new['id']), O.need_date)))
        self.assertNotIn('need_at', self.new)

    def test_request_projection_has_a_caption_without_changing_canonical_facts(self):
        from ontologics.graph_display import browser_caption
        from ontologics.model import digest
        index = {node['id']: node for node in self.facts + [self.new]}
        before = digest(self.new)
        self.assertEqual('추가 부품 검토 요청 [가상] ' + self.new['id'],
                         browser_caption(self.new, index))
        self.assertEqual(before, digest(self.new))

    def test_rdf_rejects_invalid_request_numbers(self):
        for field, value in (('quantity', True), ('quantity', 30.0),
                             ('request_revision', 0), ('reference_data_version', -1)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                RdfSnapshot(self.facts + [dict(self.new, **{field: value})], version=0,
                            schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)
