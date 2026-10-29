"""C 2nd phase schema tests: test request, test-team results and reproduction quantities.

The 1st-phase tests in test_schema.py are not modified; this file only adds checks.
C does not run tests or decide dispositions: QualityTestResult rows are what the
test team returned for requested boards.
"""
import copy
import json
import unittest

from ontologics.model import C2_TYPES, C_TYPES, ROOT, SCHEMA, graph_jsonld, tbox_turtle, validate_graph
# Imported as a module so the 1st-phase test class is not collected and run twice from this file.
from tests.claim_response import test_schema as phase1_tests

CONFIG = 'config:esp32-poe:L:WROOM'
OTHER_CONFIG = 'config:esp32-poe:L1:WROOM'
CASE = 'quality-case:test'
REQUEST = 'test-request:test'
EV = ['evidence:test-c']


def unit(serial, configuration_id=CONFIG):
    return {'id': 'unit:test-' + serial, 'type': 'ProductUnit', 'origin': 'synthetic', 'evidence': EV,
            'serial': serial, 'company': 'Demo Assembly Company', 'configuration_id': configuration_id}


def result(serial, outcome, disposition, request=REQUEST):
    return {'id': 'test-result:' + serial, 'type': 'QualityTestResult', 'origin': 'synthetic', 'evidence': EV,
            'case_id': CASE, 'test_request_id': request, 'unit_id': 'unit:test-' + serial, 'outcome': outcome,
            'disposition': disposition, 'observed_at': '2026-10-27T14:00:00+09:00'}


class CPhase2ValidationTests(unittest.TestCase):
    """Requested: claimed C-D001, stored C-S001, C-S002. Test team: C-D001 fail -> customer replacement,
    C-S001 fail -> stock replacement, C-S002 pass -> release. Replacement stock C-R001: 1 + 1 - 1 = 1."""

    @classmethod
    def setUpClass(cls):
        phase1 = phase1_tests.CPhase1ValidationTests
        phase1.setUpClass()
        cls.addClassCleanup(phase1.doClassCleanups)
        cls.base = copy.deepcopy(phase1.real_nodes + phase1.c_nodes)
        # The L1 configuration is added only to check configuration mismatches.
        catalog = {n['id']: n for n in json.loads((ROOT / 'data/catalog.json').read_text())['nodes']}
        present = {n['id'] for n in cls.base}
        stack = [OTHER_CONFIG]
        while stack:
            nid = stack.pop()
            if nid in present:
                continue
            present.add(nid)
            node = catalog[nid]
            cls.base.append(copy.deepcopy(node))
            stack += [node[key] for key in SCHEMA[node['type']][1]] + list(node['evidence'])
        cls.c2_nodes = [
            unit('C-S002'), unit('C-R001'), unit('C-X001', OTHER_CONFIG),
            {'id': REQUEST, 'type': 'TestRequest', 'origin': 'synthetic', 'evidence': EV, 'case_id': CASE,
             'unit_ids': ['unit:test-C-D001', 'unit:test-C-S001', 'unit:test-C-S002', 'unit:test-C-X001'],
             'prepared_at': '2026-10-26T10:45:00+09:00', 'document_ref': 'test_request.md',
             'draft_id': 'draft:test', 'draft_version': 1},
            result('C-D001', 'fail', 'customer_replacement'),
            result('C-S001', 'fail', 'stock_replacement'),
            result('C-S002', 'pass', 'release'),
            {'id': 'reproduction:test', 'type': 'ReproductionRequirement', 'origin': 'synthetic', 'evidence': EV,
             'case_id': CASE, 'configuration_id': CONFIG, 'customer_replacement_quantity': 1,
             'stock_replacement_quantity': 1, 'available_replacement_quantity': 1, 'required_good_quantity': 1,
             'unresolved_quantity': 0, 'unit': 'piece', 'assessment_state': 'confirmed',
             'test_result_ids': ['test-result:C-D001', 'test-result:C-S001', 'test-result:C-S002'],
             'replacement_unit_ids': ['unit:test-C-R001'], 'document_ref': 'reproduction_request.md'},
        ]

    def setUp(self):
        self.nodes = copy.deepcopy(self.base + self.c2_nodes)
        self.index = {n['id']: n for n in self.nodes}
        self.req = self.index['reproduction:test']

    def assert_invalid(self, code):
        outcome = validate_graph(self.nodes)
        self.assertFalse(outcome['valid'], outcome)
        self.assertIn(code, {e['code'] for e in outcome['errors']}, outcome['errors'])

    def add(self, node):
        self.nodes.append(node)
        self.index[node['id']] = node

    def test_phase2_nodes_validate(self):
        outcome = validate_graph(self.nodes)
        self.assertTrue(outcome['valid'], outcome['errors'])

    def test_phase2_types_are_additions(self):
        self.assertEqual(('ProductionLine', 'UnitProductionRecord', 'ClaimIntake', 'QualityCase', 'QualityScope',
                          'ContainmentRequest'), C_TYPES[:6])
        self.assertEqual(('TestRequest', 'QualityTestResult', 'ReproductionRequirement'), C2_TYPES)
        self.assertEqual(C2_TYPES, C_TYPES[6:])
        self.assertEqual(12, len(set(SCHEMA) - set(C_TYPES)))
        # The test team's own method and C dispositions are not graph types.
        self.assertNotIn('QualityTestScenario', SCHEMA)
        self.assertNotIn('QualityDisposition', SCHEMA)

    def test_phase2_records_are_synthetic_only(self):
        self.index['test-result:C-S001']['origin'] = 'real'
        self.assert_invalid('DEMO_OPERATIONS_ONLY')

    def test_value_lists_are_enforced(self):
        for nid, key in (('test-result:C-S001', 'outcome'), ('test-result:C-S001', 'disposition'),
                         ('reproduction:test', 'assessment_state'), ('reproduction:test', 'unit')):
            with self.subTest(key=key):
                old = self.index[nid][key]
                self.index[nid][key] = 'done'
                self.assert_invalid('INVALID_ENUM')
                self.index[nid][key] = old

    def test_times_need_offsets(self):
        for nid, key in ((REQUEST, 'prepared_at'), ('test-result:C-S001', 'observed_at')):
            with self.subTest(key=key):
                self.setUp()
                self.index[nid][key] = '2026-10-27'
                self.assert_invalid('INVALID_TIME')

    def test_results_only_for_requested_boards_of_the_same_case(self):
        self.add(unit('C-S009'))
        self.add(result('C-S009', 'pass', 'release'))
        self.assert_invalid('UNIT_NOT_REQUESTED')
        self.setUp()
        self.add(dict(copy.deepcopy(self.index[CASE]), id='quality-case:test-other'))
        self.index['test-result:C-S002']['case_id'] = 'quality-case:test-other'
        self.assert_invalid('CASE_MISMATCH')

    def test_one_result_per_requested_board(self):
        self.add(dict(result('C-S001', 'fail', 'stock_replacement'), id='test-result:C-S001-again'))
        self.assert_invalid('DUPLICATE_IDENTITY')

    def test_request_needs_existing_boards_and_a_valid_draft_version(self):
        self.index[REQUEST]['unit_ids'] = []
        self.assert_invalid('INVALID_REFERENCE')
        self.setUp()
        self.index[REQUEST]['draft_version'] = 0
        self.assert_invalid('INVALID_FIELD_TYPE')

    def test_customer_and_stock_replacements_target_the_right_boards(self):
        self.index['test-result:C-S001']['disposition'] = 'customer_replacement'
        self.assert_invalid('DISPOSITION_TARGET_MISMATCH')
        self.setUp()
        self.index['test-result:C-D001']['disposition'] = 'stock_replacement'
        self.assert_invalid('DISPOSITION_TARGET_MISMATCH')

    def test_required_good_quantity_follows_the_formula(self):
        self.req['required_good_quantity'] = 2
        self.assert_invalid('QUANTITY_MISMATCH')
        # More replacement stock than demand -> 0, never negative.
        self.setUp()
        del self.req['replacement_unit_ids']
        self.req.update(available_replacement_quantity=5, required_good_quantity=0)
        outcome = validate_graph(self.nodes)
        self.assertTrue(outcome['valid'], outcome['errors'])

    def test_replacement_quantities_match_the_listed_results(self):
        self.req['stock_replacement_quantity'] = 2
        self.req['required_good_quantity'] = 2
        self.assert_invalid('QUANTITY_MISMATCH')

    def test_quantities_are_nonnegative_integers(self):
        for invalid in (-1, 1.5, True, '1', None):
            with self.subTest(invalid=invalid):
                self.setUp()
                self.req['unresolved_quantity'] = invalid
                self.assert_invalid('INVALID_QUANTITY')

    def test_assessment_state_matches_unresolved_quantity(self):
        self.req['unresolved_quantity'] = 100
        self.assert_invalid('ASSESSMENT_STATE_MISMATCH')
        self.req['assessment_state'] = 'lower_bound'
        outcome = validate_graph(self.nodes)
        self.assertTrue(outcome['valid'], outcome['errors'])
        self.req['unresolved_quantity'] = 0
        self.assert_invalid('ASSESSMENT_STATE_MISMATCH')

    def test_replacement_boards_are_checked_when_listed(self):
        for invalid, code in ((['unit:test-C-R001', 'unit:test-C-S002'], 'QUANTITY_MISMATCH'),
                              (['test-result:C-S001'], 'INVALID_REFERENCE'), ([], 'INVALID_REFERENCE'),
                              (['unit:test-C-X001'], 'CONFIGURATION_MISMATCH')):
            with self.subTest(invalid=invalid):
                self.setUp()
                self.req['replacement_unit_ids'] = invalid
                self.assert_invalid(code)

    def test_results_must_match_the_requirement_configuration(self):
        self.add(result('C-X001', 'fail', 'stock_replacement'))
        self.req['test_result_ids'].append('test-result:C-X001')
        self.assert_invalid('CONFIGURATION_MISMATCH')

    def test_shape_mutations_on_phase2_types_never_crash(self):
        for nid in [n['id'] for n in self.c2_nodes if n['type'] in C2_TYPES]:
            node = self.index[nid]
            for key in list(node):
                if key == 'id':
                    continue
                old = node[key]
                for value in (None, [], [[]], {}, {'nested': []}, 0, -1, True):
                    with self.subTest(nid=nid, key=key, value=value):
                        node[key] = value
                        self.assertIsInstance(validate_graph(self.nodes), dict)
                node[key] = old

    def test_jsonld_and_tbox_link_phase2_references(self):
        rows = {row['id']: row for row in graph_jsonld(self.nodes)['@graph']}
        req = rows['urn:ontologics:entity:reproduction:test']
        self.assertEqual(['urn:ontologics:entity:unit:test-C-R001'], req['replacement_unit_ids'])
        self.assertEqual('urn:ontologics:entity:test-result:C-D001', req['test_result_ids'][0])
        self.assertEqual('urn:ontologics:entity:test-request:test',
                         rows['urn:ontologics:entity:test-result:C-D001']['test_request_id'])
        tbox = tbox_turtle()
        for kind in C2_TYPES:
            self.assertIn(f'o:{kind} a owl:Class .', tbox)
        self.assertIn('o:test_request_id a owl:ObjectProperty ; rdfs:range o:TestRequest .', tbox)
        self.assertIn('o:test_result_ids a owl:ObjectProperty ; rdfs:range o:QualityTestResult .', tbox)
        self.assertIn('o:replacement_unit_ids a owl:ObjectProperty ; rdfs:range o:ProductUnit .', tbox)


if __name__ == '__main__':
    unittest.main()
