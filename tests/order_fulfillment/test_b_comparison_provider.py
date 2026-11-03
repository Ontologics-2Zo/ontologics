"""Pure CQ06 binding and transport budgets; no stored B graph fixture."""
import unittest
from ontologics.harness import BudgetLimits, ExecutionBudget, request_budget
from ontologics.paid_budget import CostPolicy, ModelPrice
from examples.order_fulfillment import b_comparison_agent as cq
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings, ProviderError
from tests.test_openai_provider import SECRET, Transport


class ComparisonProviderTests(unittest.TestCase):
    def setUp(self):
        # Synthetic prices and explicit local HTTP doubles, never a paid request.
        self.cost_policy = CostPolicy('100', (ModelPrice(MODEL, '1', '2', 400000),))
        self.budget = ExecutionBudget(BudgetLimits(max_model_calls=64), cost_policy=self.cost_policy)
        scope = request_budget('b-provider-test', self.budget)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)

    def test_unbound_final_schema_stops_before_http(self):
        transport=Transport([])
        provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
        provider.tools=[]
        with self.assertRaises(ProviderError) as found:
            provider(model=MODEL,messages=[{'role':'user','content':'final'}])
        self.assertEqual('CQ06_BASIC_TEXT_BINDING',found.exception.code)
        self.assertEqual([],transport.requests)

    def test_failed_http_requests_also_consume_four_request_budget(self):
        transport=Transport([TimeoutError() for _ in range(4)])
        provider=cq.BoundedOpenAIProvider(OpenAISettings(SECRET),opener=transport)
        for _ in range(4):
            with self.assertRaises(TimeoutError):provider(model=MODEL,messages=[{'role':'user','content':'test'}])
        with self.assertRaises(ProviderError) as found:provider(model=MODEL,messages=[])
        self.assertEqual('LIVE_BUDGET_EXHAUSTED',found.exception.code)
        self.assertEqual(4,len(transport.requests));self.assertTrue(all(x['status']=='failed' for x in provider.calls))
