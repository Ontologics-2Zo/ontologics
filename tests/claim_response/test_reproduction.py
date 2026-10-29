"""6-2: C reads the test team's results and dispositions; it never runs tests or changes a disposition."""
from pathlib import Path
import tempfile
import unittest

from ontologics.claim_response.fixtures import claim_response_nodes
from ontologics.claim_response.reproduction import BLOCKED, NEEDS_REVIEW, RESULT_HEADER, read_test_results
from ontologics.claim_response.requests import prepare_test_request
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
