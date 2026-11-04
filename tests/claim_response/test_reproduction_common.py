"""Common C proposals must match the approved graph's configuration-specific rules."""
from copy import deepcopy
import unittest

from ontologics.claim_response.reproduction import reproduction_requirements
from ontologics.model import canonical, validate_graph
from tests.claim_response import test_schema_phase2 as fixture


class CommonReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.CPhase2ValidationTests.setUpClass()
        cls.addClassCleanup(fixture.CPhase2ValidationTests.doClassCleanups)
        cls.base = deepcopy(fixture.CPhase2ValidationTests.base + fixture.CPhase2ValidationTests.c2_nodes)

    def setUp(self):
        self.nodes = [deepcopy(node) for node in self.base if node['type'] != 'ReproductionRequirement']
        self.index = {node['id']: node for node in self.nodes}
        self.replacement = {'unit_ids': ['unit:test-C-R001'], 'configuration_id': fixture.CONFIG,
                            'evidence': fixture.EV, 'basis': ['explicit_demo_stock']}

    def calculate(self):
        before = canonical(self.nodes)
        result = reproduction_requirements(self.nodes, fixture.CASE, self.replacement)
        self.assertEqual(before, canonical(self.nodes), 'proposal generation must not mutate approved records')
        # Substitute proposals for matching existing records, just as a put draft does.
        candidate = {node['id']: node for node in self.nodes}
        candidate.update({node['id']: node for node in result['nodes']})
        validation = validate_graph(list(candidate.values()))
        self.assertTrue(validation['valid'], validation['errors'])
        return result

    def add_request_result(self, outcome, disposition):
        request = dict(deepcopy(self.index[fixture.REQUEST]), id='test-request:again',
                       unit_ids=['unit:test-C-D001'])
        result = dict(fixture.result('C-D001', outcome, disposition, request=request['id']), id='test-result:again')
        self.nodes.extend([request, result])
        return result

    def test_mixed_configuration_proposals_count_separately(self):
        self.index['unit:test-C-S001']['configuration_id'] = fixture.OTHER_CONFIG
        result = self.calculate()
        by_config = {node['configuration_id']: node for node in result['nodes']}
        self.assertEqual(2, len({node['id'] for node in result['nodes']}))
        first, second = by_config[fixture.CONFIG], by_config[fixture.OTHER_CONFIG]
        self.assertEqual((1, 0, 1, 0), tuple(first[key] for key in
            ('customer_replacement_quantity', 'stock_replacement_quantity',
             'available_replacement_quantity', 'required_good_quantity')))
        self.assertEqual((0, 1, 0, 1), tuple(second[key] for key in
            ('customer_replacement_quantity', 'stock_replacement_quantity',
             'available_replacement_quantity', 'required_good_quantity')))
        self.assertEqual(['test-result:C-S001'], second['test_result_ids'])

    def test_multiple_requests_deduplicate_boards_and_leave_conflicts_unresolved(self):
        additional = self.add_request_result('fail', 'customer_replacement')
        same = self.calculate()['nodes'][0]
        self.assertEqual((1, 1, 0), (same['customer_replacement_quantity'],
                         same['stock_replacement_quantity'], same['unresolved_quantity']))
        self.assertEqual(3, len(same['test_result_ids']))
        additional.update(outcome='pass', disposition='release')
        conflict = self.calculate()['nodes'][0]
        self.assertEqual((0, 1, 1, 'lower_bound'), (conflict['customer_replacement_quantity'],
                         conflict['stock_replacement_quantity'], conflict['unresolved_quantity'],
                         conflict['assessment_state']))
        self.assertNotIn('test-result:again', conflict['test_result_ids'])
        self.assertNotIn('test-result:C-D001', conflict['test_result_ids'])

    def test_other_case_replacement_demand_cannot_offset_this_case(self):
        other_case = dict(deepcopy(self.index[fixture.CASE]), id='quality-case:other',
                          unit_ids=['unit:test-C-R001'])
        other_request = dict(deepcopy(self.index[fixture.REQUEST]), id='test-request:other',
                             case_id=other_case['id'], unit_ids=['unit:test-C-R001'])
        other_result = dict(fixture.result('C-R001', 'fail', 'customer_replacement', request=other_request['id']),
                            case_id=other_case['id'])
        self.nodes.extend([other_case, other_request, other_result])
        result = self.calculate()
        self.assertEqual([{'unit_id': 'unit:test-C-R001', 'reason': 'REPLACEMENT_TARGET_CONFLICT'}],
                         result['excluded_replacements'])
        node = result['nodes'][0]
        self.assertEqual((0, 2), (node['available_replacement_quantity'], node['required_good_quantity']))
        self.assertNotIn('replacement_unit_ids', node)

    def test_recalculation_reuses_existing_case_configuration_id(self):
        existing = self.calculate()['nodes'][0]
        existing['id'] = 'reproduction:legacy-id'
        self.nodes.append(existing)
        self.index['test-result:C-S002'].update(outcome='fail', disposition='stock_replacement')
        result = self.calculate()['nodes'][0]
        self.assertEqual('reproduction:legacy-id', result['id'])
        self.assertEqual((2, 2), (result['stock_replacement_quantity'], result['required_good_quantity']))

    def test_configuration_without_known_demand_remains_undetermined(self):
        self.index['unit:test-C-S001']['configuration_id'] = fixture.OTHER_CONFIG
        self.index['test-result:C-S001']['disposition'] = 'unresolved'
        result = self.calculate()
        self.assertEqual([fixture.CONFIG], [node['configuration_id'] for node in result['nodes']])
        self.assertEqual([{'configuration_id': fixture.OTHER_CONFIG, 'state': 'undetermined',
                           'unresolved_quantity': 1, 'required_good_quantity': None,
                           'conflicting_unit_ids': []}], result['undetermined'])


if __name__ == '__main__':
    unittest.main()
