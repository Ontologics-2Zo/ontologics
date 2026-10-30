"""Pure CQ03 target/coverage boundaries retained after stored-graph retirement."""
import copy
import unittest

from ontologics.ingest import build_catalog
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.production import (
    AS_OF, ROUND, WORK, REQUIRED_CONFIRMATIONS,
    calculate_completion, load_demo_production, production_completion,
)
from ontologics.order_fulfillment.schema_validation import validate_input_graph


class CQ03InputBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.base = build_catalog()['nodes'] + load_demo_contract()

    def test_invalid_target_types_and_false_coverage_never_yield_a_forecast(self):
        nodes = self.base + load_demo_production()
        observations = sorted((n for n in nodes if n['type'] == 'ProductionObservation'), key=lambda n: n['observed_at'])
        for target in (True, False, 1000.0, '1000', -1, 0, None, [], {}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                calculate_completion(observations[0], observations[1], target)
        for flag in REQUIRED_CONFIRMATIONS:
            with self.subTest(flag=flag):
                changed = copy.deepcopy(nodes)
                snapshot = next(n for n in changed if n['type'] == 'FulfillmentSnapshot')
                snapshot['confirmations'][flag] = False
                self.assertTrue(validate_input_graph(changed)['valid'])
                result = production_completion(changed, WORK, ROUND, AS_OF, queried_at='2026-10-27T10:10:00+09:00')
                self.assertEqual('needs_confirmation', result['status'])
                self.assertIsNone(result['estimated_completion_at'])
                self.assertIsNone(result['remaining_seconds'])
                self.assertTrue(any(u['field'] == flag and u['code'] == 'COVERAGE_NOT_CONFIRMED' for u in result['unknowns']))


if __name__ == '__main__':
    unittest.main()
