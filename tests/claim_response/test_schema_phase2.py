"""C 2nd phase schema tests: quality tests, dispositions and reproduction quantities.

The 1st-phase tests in test_schema.py are not modified; this file only adds checks.
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
EV = ['evidence:test-c']


def unit(serial, configuration_id=CONFIG):
    return {'id': 'unit:test-' + serial, 'type': 'ProductUnit', 'origin': 'synthetic', 'evidence': EV,
            'serial': serial, 'company': 'Demo Assembly Company', 'configuration_id': configuration_id}


def result(rid, serial, outcome):
    return {'id': 'test-result:' + rid, 'type': 'QualityTestResult', 'origin': 'synthetic', 'evidence': EV,
            'case_id': CASE, 'scenario_id': 'test-scenario:boot', 'unit_id': 'unit:test-' + serial,
            'observed_at': '2026-10-27T14:00:00+09:00', 'observation': '전원 인가 후 부팅 확인', 'outcome': outcome}


def disposition(serial, kind, results):
    return {'id': 'disposition:' + serial, 'type': 'QualityDisposition', 'origin': 'synthetic', 'evidence': EV,
            'case_id': CASE, 'unit_id': 'unit:test-' + serial, 'disposition': kind,
            'reason': '가정 판정', 'decided_at': '2026-10-28T10:00:00+09:00',
            'test_result_ids': ['test-result:' + r for r in results]}


class CPhase2ValidationTests(unittest.TestCase):
    """Claimed C-D001 fails -> customer replacement; stored C-S001 fails -> stock replacement;
    C-S002 passes -> release; one replacement board C-R001 -> 1 + 1 - 1 = 1 good board to reproduce."""

    @classmethod
    def setUpClass(cls):
        phase1 = phase1_tests.CPhase1ValidationTests
        phase1.setUpClass()
        cls.addClassCleanup(phase1.doClassCleanups)
        cls.base = copy.deepcopy(phase1.real_nodes + phase1.c_nodes)
        # The L1 configuration is added only to check configuration mismatches.
        catalog = {n['id']: n for n in json.loads((ROOT / 'data/catalog.json').read_text())['nodes']}
        present = {n['id'] for n in cls.base}
        extra, stack = [], [OTHER_CONFIG]
        while stack:
            nid = stack.pop()
            if nid in present:
                continue
            present.add(nid)
            node = catalog[nid]
            extra.append(copy.deepcopy(node))
            stack += [node[key] for key in SCHEMA[node['type']][1]] + list(node['evidence'])
        cls.base += extra
        cls.c2_nodes = [
            unit('C-S002'), unit('C-R001'), unit('C-X001', OTHER_CONFIG),
            {'id': 'test-scenario:boot', 'type': 'QualityTestScenario', 'origin': 'synthetic', 'evidence': EV,
             'case_id': CASE, 'name': '전원 인가 후 부팅 확인', 'symptom': '부팅 실패',
             'procedure': '정해 둔 전원 인가 절차', 'criterion': '30초 안에 상태 표시', 'target_selection': 'full'},
            result('D001', 'C-D001', 'fail'), result('S001', 'C-S001', 'fail'), result('S002', 'C-S002', 'pass'),
            disposition('C-D001', 'customer_replacement', ['D001']),
            disposition('C-S001', 'stock_replacement', ['S001']),
            disposition('C-S002', 'release', ['S002']),
            {'id': 'reproduction:test', 'type': 'ReproductionRequirement', 'origin': 'synthetic', 'evidence': EV,
             'case_id': CASE, 'configuration_id': CONFIG, 'customer_replacement_quantity': 1,
             'stock_replacement_quantity': 1, 'available_replacement_quantity': 1, 'required_good_quantity': 1,
             'unresolved_quantity': 0, 'unit': 'piece', 'assessment_state': 'confirmed',
             'disposition_ids': ['disposition:C-D001', 'disposition:C-S001', 'disposition:C-S002'],
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
        self.assertEqual(C2_TYPES, C_TYPES[6:])
        self.assertEqual(12, len(set(SCHEMA) - set(C_TYPES)))

    def test_phase2_records_are_synthetic_only(self):
        self.index['test-result:S001']['origin'] = 'real'
        self.assert_invalid('DEMO_OPERATIONS_ONLY')

    def test_value_lists_are_enforced(self):
        for nid, key in (('test-scenario:boot', 'target_selection'), ('test-result:S001', 'outcome'),
                         ('disposition:C-S001', 'disposition'), ('reproduction:test', 'assessment_state'),
                         ('reproduction:test', 'unit')):
            with self.subTest(key=key):
                old = self.index[nid][key]
                self.index[nid][key] = 'done'
                self.assert_invalid('INVALID_ENUM')
                self.index[nid][key] = old

    def test_times_need_offsets(self):
        for nid, key in (('test-result:S001', 'observed_at'), ('disposition:C-S001', 'decided_at')):
            with self.subTest(key=key):
                self.setUp()
                self.index[nid][key] = '2026-10-27'
                self.assert_invalid('INVALID_TIME')

    def test_result_case_must_match_scenario_case(self):
        self.add(dict(copy.deepcopy(self.index[CASE]), id='quality-case:test-other'))
        self.index['test-result:S002']['case_id'] = 'quality-case:test-other'
        self.assert_invalid('CASE_MISMATCH')

    def test_disposition_needs_results_for_the_same_board(self):
        self.index['disposition:C-S001']['test_result_ids'] = ['test-result:S002']
        self.assert_invalid('TEST_RESULT_MISMATCH')
        self.setUp()
        self.index['disposition:C-S001']['test_result_ids'] = []
        self.assert_invalid('INVALID_REFERENCE')

    def test_several_failed_tests_still_mean_one_disposition(self):
        second = dict(result('S001b', 'C-S001', 'fail'), observed_at='2026-10-27T15:00:00+09:00')
        self.add(second)
        self.index['disposition:C-S001']['test_result_ids'].append(second['id'])
        outcome = validate_graph(self.nodes)
        self.assertTrue(outcome['valid'], outcome['errors'])
        self.add(dict(copy.deepcopy(self.index['disposition:C-S001']), id='disposition:C-S001-again'))
        self.assert_invalid('DUPLICATE_IDENTITY')

    def test_customer_and_stock_replacements_target_the_right_boards(self):
        self.index['disposition:C-S001']['disposition'] = 'customer_replacement'
        self.assert_invalid('DISPOSITION_TARGET_MISMATCH')
        self.setUp()
        self.index['disposition:C-D001']['disposition'] = 'stock_replacement'
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

    def test_replacement_quantities_match_the_listed_dispositions(self):
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
                              (['test-result:S001'], 'INVALID_REFERENCE'), ([], 'INVALID_REFERENCE'),
                              (['unit:test-C-X001'], 'CONFIGURATION_MISMATCH')):
            with self.subTest(invalid=invalid):
                self.setUp()
                self.req['replacement_unit_ids'] = invalid
                self.assert_invalid(code)

    def test_dispositions_must_match_case_and_configuration(self):
        self.add(unit('C-X002', OTHER_CONFIG))
        self.add(dict(result('X002', 'C-X002', 'fail')))
        self.add(disposition('C-X002', 'stock_replacement', ['X002']))
        self.req['disposition_ids'].append('disposition:C-X002')
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
                        outcome = validate_graph(self.nodes)
                        self.assertIsInstance(outcome, dict)
                node[key] = old

    def test_jsonld_and_tbox_link_phase2_references(self):
        rows = {row['id']: row for row in graph_jsonld(self.nodes)['@graph']}
        req = rows['urn:ontologics:entity:reproduction:test']
        self.assertEqual(['urn:ontologics:entity:unit:test-C-R001'], req['replacement_unit_ids'])
        self.assertEqual('urn:ontologics:entity:disposition:C-D001', req['disposition_ids'][0])
        tbox = tbox_turtle()
        for kind in C2_TYPES:
            self.assertIn(f'o:{kind} a owl:Class .', tbox)
        self.assertIn('o:test_result_ids a owl:ObjectProperty ; rdfs:range o:QualityTestResult .', tbox)
        self.assertIn('o:replacement_unit_ids a owl:ObjectProperty ; rdfs:range o:ProductUnit .', tbox)


if __name__ == '__main__':
    unittest.main()
