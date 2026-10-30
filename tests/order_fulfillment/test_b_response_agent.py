"""Pure provider request/token/retry budgets; no stored B graph fixture."""
import json
import unittest
from ontologics.harness import BudgetLimits, ExecutionBudget, request_budget
from ontologics.paid_budget import CostPolicy, ModelPrice
from urllib.error import URLError
from examples.order_fulfillment import b_response_agent as cq
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings, ProviderError
from tests.test_openai_provider import Transport, response, text


class ResponseBudgetTests(unittest.TestCase):
    def setUp(self):
        # Explicit synthetic pricing for local HTTP doubles; no network allowance.
        self.cost_policy = CostPolicy('100', (ModelPrice(MODEL, '1', '2', 400000),))
        self.budget = ExecutionBudget(BudgetLimits(max_model_calls=64), cost_policy=self.cost_policy)
        scope = request_budget('b-provider-test', self.budget)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)

    def test_D32_fifth_http_attempt_is_blocked_and_output_limit_is_forced(self):
        transport = Transport([response([text()])] * 4)
        provider = cq.BoundedOpenAIProvider(OpenAISettings('mock-only',max_output_tokens=8192),opener=transport)
        for _ in range(4):provider(model=MODEL,messages=[dict(role='user',content='budget')])
        with self.assertRaises(ProviderError) as caught:
            provider(model=MODEL,messages=[dict(role='user',content='fifth')])
        self.assertEqual('LIVE_BUDGET_EXHAUSTED',caught.exception.code)
        self.assertEqual(4,len(transport.requests))
        self.assertTrue(all(json.loads(req.data)['max_output_tokens']==1024 for req,_ in transport.requests))
        self.assertEqual(1,cq.CONFIG.max_attempts)
        self.assertEqual(4,cq.CONFIG.max_steps)

    def test_D32_failed_transport_consumes_budget_and_never_retries_automatically(self):
        transport=Transport([URLError('offline')] * 4)
        provider=cq.BoundedOpenAIProvider(OpenAISettings('mock-only'),opener=transport)
        for attempt in range(4):
            with self.assertRaises(ConnectionError):
                provider(model=MODEL,messages=[dict(role='user',content='failure')])
            self.assertEqual(attempt+1,len(transport.requests))
        with self.assertRaises(ProviderError):
            provider(model=MODEL,messages=[dict(role='user',content='fifth')])
        self.assertEqual(4,len(provider.calls))
        self.assertTrue(all(entry['status']=='failed' for entry in provider.calls))
