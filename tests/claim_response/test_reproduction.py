"""6-2: C reads the test team's results and dispositions; it never runs tests or changes a disposition."""
from pathlib import Path
import tempfile
import unittest

from ontologics.claim_response.fixtures import claim_response_nodes
from ontologics.claim_response.fixtures import load_operations
from ontologics.claim_response.reproduction import (
    BLOCKED, CONFIRMED, LOWER_BOUND, NEEDS_REVIEW, RESULT_HEADER, UNDETERMINED, c_operations_replacement,
    read_test_results, reproduction_requirement)
from ontologics.claim_response.requests import prepare_reproduction_request, prepare_test_request
from ontologics.model import ROOT, validate_graph
from tests.claim_response import test_requests as base

RESULTS = ROOT / 'data/claim_response/test_results'
COLLECTED = '2026-10-28T09:00:00+09:00'


def request_and_case():
    claim, case, _, hold = base.scenario()
    request = prepare_test_request(case, base.LINE_C01, hold, prepared_at=base.PREPARED,
                                   external_claim_id=base.CLAIM_ID, configuration_id=base.CONFIG)['node']
    return claim, case, hold, request


class ScenarioResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claim, cls.case, cls.hold, cls.request = request_and_case()

    def read(self, name):
        return read_test_results(RESULTS / name, self.request, self.case, collected_at=COLLECTED)

    def test_four_scenarios_give_the_expected_counts(self):
        expected = {
            'scenario_1.csv': {'tested': 125, 'untested': 0, 'customer_replacement': 5, 'stock_replacement': 12, 'release': 108},
            'scenario_2.csv': {'tested': 125, 'untested': 0, 'customer_replacement': 5, 'stock_replacement': 18, 'release': 102},
            'scenario_3.csv': {'tested': 125, 'untested': 0, 'unresolved': 5, 'hold': 120},
            'scenario_4.csv': {'tested': 25, 'untested': 100, 'customer_replacement': 5, 'stock_replacement': 4, 'hold': 16},
        }
        for name, counts in expected.items():
            with self.subTest(scenario=name):
                result = self.read(name)
                self.assertEqual(NEEDS_REVIEW, result['state'], result['issues'])
                self.assertEqual(dict(counts, requested=125), result['counts'])
                self.assertEqual(counts['tested'], sum(n['type'] == 'QualityTestResult' for n in result['nodes']))
                self.assertEqual(counts['untested'], len(result['untested_unit_ids']))

    def test_dispositions_are_copied_not_decided(self):
        nodes = {n['unit_id']: n for n in self.read('scenario_4.csv')['nodes'] if n['type'] == 'QualityTestResult'}
        self.assertEqual(('fail', 'customer_replacement'), (nodes['unit:C-D001']['outcome'], nodes['unit:C-D001']['disposition']))
        self.assertEqual(('pass', 'hold'), (nodes['unit:C-S020']['outcome'], nodes['unit:C-S020']['disposition']))
        self.assertNotIn('unit:C-S021', nodes)

    def test_result_nodes_are_valid_graph_additions(self):
        for name in ('scenario_1.csv', 'scenario_4.csv'):
            with self.subTest(scenario=name):
                nodes = base.existing_nodes() + claim_response_nodes() + self.claim + [
                    self.case, self.hold['node'], self.request] + self.read(name)['nodes']
                self.assertEqual([], validate_graph(nodes)['errors'])


class ReproductionTests(unittest.TestCase):
    """6-3 / 6-4: MVP scenarios 1-4 -> 10, 16, undetermined, at least 2 (100 untested)."""
    @classmethod
    def setUpClass(cls):
        cls.claim, cls.case, cls.hold, cls.request = request_and_case()
        cls.replacement = c_operations_replacement(load_operations())

    def compute(self, name):
        results = read_test_results(RESULTS / name, self.request, self.case, collected_at=COLLECTED)
        return results, reproduction_requirement(self.case, self.request, results['nodes'], self.replacement,
                                                 configuration_id=base.CONFIG)

    def test_mvp_scenarios(self):
        expected = {
            'scenario_1.csv': (CONFIRMED, 5, 12, 7, 10, 0),
            'scenario_2.csv': (CONFIRMED, 5, 18, 7, 16, 0),
            'scenario_3.csv': (UNDETERMINED, 0, 0, 7, None, 5),
            'scenario_4.csv': (LOWER_BOUND, 5, 4, 7, 2, 100),
        }
        keys = ('state', 'customer_replacement_quantity', 'stock_replacement_quantity',
                'available_replacement_quantity', 'required_good_quantity', 'unresolved_quantity')
        for name, values in expected.items():
            with self.subTest(scenario=name):
                _, result = self.compute(name)
                self.assertEqual(values, tuple(result[k] for k in keys))

    def test_undetermined_is_not_stored_as_zero(self):
        _, result = self.compute('scenario_3.csv')
        self.assertIsNone(result['node'])
        self.assertIsNone(result['required_good_quantity'])
        self.assertTrue(result['reason'])

    def test_requirement_nodes_are_valid_graph_additions(self):
        for name in ('scenario_1.csv', 'scenario_2.csv', 'scenario_4.csv'):
            with self.subTest(scenario=name):
                results, result = self.compute(name)
                node = result['node']
                self.assertEqual(7, len(node['replacement_unit_ids']))
                self.assertEqual(['c_operations_record'], result['basis'])
                graph = base.existing_nodes() + claim_response_nodes() + self.claim + [
                    self.case, self.hold['node'], self.request] + results['nodes'] + [node]
                self.assertEqual([], validate_graph(graph)['errors'])

    def test_more_replacement_stock_than_demand_gives_zero_not_negative(self):
        results, _ = self.compute('scenario_4.csv')
        plenty = dict(self.replacement, unit_ids=['unit:C-D0%02d' % n for n in range(60, 80)])
        result = reproduction_requirement(self.case, self.request, results['nodes'], plenty, configuration_id=base.CONFIG)
        self.assertEqual((LOWER_BOUND, 0), (result['state'], result['required_good_quantity']))


class ReproductionRequestTests(unittest.TestCase):
    """6-5: A request documents. Confirmed -> request, lower bound -> marked as minimum, undetermined -> none."""
    @classmethod
    def setUpClass(cls):
        cls.claim, cls.case, cls.hold, cls.request = request_and_case()
        cls.replacement = c_operations_replacement(load_operations())

    def build(self, name):
        results = read_test_results(RESULTS / name, self.request, self.case, collected_at=COLLECTED)
        reproduction = reproduction_requirement(self.case, self.request, results['nodes'], self.replacement,
                                                configuration_id=base.CONFIG)
        return results, prepare_reproduction_request(self.case, reproduction, prepared_at=COLLECTED,
                                                     external_claim_id=base.CLAIM_ID)

    def test_confirmed_scenarios_become_requests_with_their_numbers(self):
        for name, required in (('scenario_1.csv', 10), ('scenario_2.csv', 16)):
            with self.subTest(scenario=name):
                _, out = self.build(name)
                [(filename, text)] = out['documents'].items()
                self.assertTrue(out['node']['document_ref'].endswith('/' + filename))
                for phrase in ('# 재생산 요청서 검토 초안', f'**{required}대**', '| 수량 상태 | 확정 |',
                               '| 송부 상태 | 미송부 |', 'C-R001 ~ C-R007', base.CLAIM_ID, base.CONFIG):
                    self.assertIn(phrase, text)
                self.assertNotIn('최소 수량', text)

    def test_lower_bound_is_marked_as_a_minimum(self):
        _, out = self.build('scenario_4.csv')
        text = next(iter(out['documents'].values()))
        for phrase in ('(하한, 추가 확인 필요)', '**2대**', '| 미결 (판정 없음·미검사) | 100대 |', '최소 수량',
                       '최종 재생산 수량으로 쓰지 않는다'):
            self.assertIn(phrase, text)

    def test_undetermined_gets_no_document_and_no_node(self):
        _, out = self.build('scenario_3.csv')
        self.assertEqual((UNDETERMINED, None, {}), (out['state'], out['node'], out['documents']))
        self.assertTrue(out['skipped']['reason'])

    def test_request_node_with_document_ref_is_a_valid_graph_addition(self):
        results, out = self.build('scenario_1.csv')
        graph = base.existing_nodes() + claim_response_nodes() + self.claim + [
            self.case, self.hold['node'], self.request] + results['nodes'] + [out['node']]
        self.assertEqual([], validate_graph(graph)['errors'])


class BrokenResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.case, _, cls.request = request_and_case()

    def read_rows(self, rows, header=RESULT_HEADER):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'results.csv'
            path.write_text('\n'.join([','.join(header)] + rows) + '\n', encoding='utf-8')
            return read_test_results(path, self.request, self.case, collected_at=COLLECTED)

    def test_problems_block_all_result_nodes_for_a_person(self):
        good = 'C-D001,불합격,고객 교체,2026-10-27T10:00:00+09:00,'
        cases = {
            'DUPLICATE_RESULT': [good, good],
            'UNIT_NOT_REQUESTED': ['C-X999,통과,출고 허용,2026-10-27T10:00:00+09:00,'],
            'INVALID_OUTCOME': ['C-D001,고장,고객 교체,2026-10-27T10:00:00+09:00,'],
            'INVALID_DISPOSITION': ['C-D001,불합격,폐기,2026-10-27T10:00:00+09:00,'],
            'TIMEZONE_REQUIRED': ['C-D001,불합격,고객 교체,2026-10-27T10:00:00,'],
            'DISPOSITION_WITHOUT_TEST': ['C-S001,미검사,보류,,'],
        }
        for code, rows in cases.items():
            with self.subTest(code=code):
                result = self.read_rows(rows)
                self.assertEqual(BLOCKED, result['state'])
                self.assertIn(code, {i['code'] for i in result['issues']})
                self.assertFalse(any(n['type'] == 'QualityTestResult' for n in result['nodes']))

    def test_missing_rows_mean_untested_and_header_must_match(self):
        result = self.read_rows(['C-D001,불합격,고객 교체,2026-10-27T10:00:00+09:00,'])
        self.assertEqual((NEEDS_REVIEW, 1, 124), (result['state'], result['counts']['tested'], result['counts']['untested']))
        with self.assertRaisesRegex(ValueError, 'INVALID_RESULT_HEADER'):
            self.read_rows([], header=('board', 'result'))


if __name__ == '__main__':
    unittest.main()
