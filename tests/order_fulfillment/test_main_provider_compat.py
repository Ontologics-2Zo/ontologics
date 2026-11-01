"""B uses the common paid reservations; every HTTP response here is a local double."""
import unittest

from ontologics.harness import BudgetLimits, ExecutionBudget, active_budget, request_budget
from ontologics.paid_budget import CostPolicy, ModelPrice
from ontologics.service import DomainError
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAIProvider, OpenAISettings, ProviderError
from tests.test_openai_provider import SECRET, Transport, response, text


class MainProviderCompatibilityTests(unittest.TestCase):
    def provider(self, items):
        transport = Transport(items)
        return OpenAIProvider(OpenAISettings(SECRET, max_output_tokens=1024), ('lookup',), opener=transport), transport

    def budget(self, cap='1', **kwargs):
        policy = CostPolicy(cap, (ModelPrice(MODEL, '1', '2', 400000),))
        return ExecutionBudget(cost_policy=policy, **kwargs)

    def test_missing_policy_blocks_before_any_http_even_with_explicit_opener(self):
        for budget in (None, ExecutionBudget()):
            provider, transport = self.provider([response([text()])])
            with request_budget('b-missing-policy', budget), self.assertRaises(DomainError) as caught:
                provider(model=MODEL, messages=[])
            self.assertEqual('COST_POLICY_REQUIRED', caught.exception.code)
            self.assertEqual([], transport.requests)
            self.assertEqual([], provider.calls)

    def test_insufficient_conservative_reservation_blocks_before_http(self):
        provider, transport = self.provider([response([text()])])
        with request_budget('b-cost-cap', self.budget('0.4')), self.assertRaises(DomainError) as caught:
            provider(model=MODEL, messages=[])
        self.assertEqual('COST_BUDGET_EXCEEDED', caught.exception.code)
        self.assertEqual([], transport.requests)

    def test_reported_usage_settles_shared_reservation(self):
        budget = self.budget()
        provider, transport = self.provider([response([text()])])
        with request_budget('b-reported', budget):
            provider(model=MODEL, messages=[])
        self.assertEqual(1, len(transport.requests))
        self.assertEqual('usage_reported', provider.calls[0]['cost']['status'])
        self.assertEqual('0.00001', budget.snapshot()['cost']['reported_cost_usd'])
        self.assertEqual(1, budget.snapshot()['attempted_calls']['model'])

    def test_timeout_retains_reservation_and_does_not_retry(self):
        provider, transport = self.provider([TimeoutError('local double only')])
        budget = self.budget()
        with request_budget('b-unknown', budget), self.assertRaises(TimeoutError):
            provider(model=MODEL, messages=[])
        self.assertEqual(1, len(transport.requests))
        self.assertEqual('usage_unreported_reserved', provider.calls[0]['cost']['status'])
        self.assertEqual('0.402048', budget.snapshot()['cost']['unreported_reserved_usd'])

    def test_wrong_model_usage_does_not_release_reservation(self):
        provider, _ = self.provider([response([text()], model='wrong')])
        with request_budget('b-wrong-model', self.budget()), self.assertRaises(ProviderError):
            provider(model=MODEL, messages=[])
        self.assertEqual('usage_unreported_reserved', provider.calls[0]['cost']['status'])

    def test_nested_runtime_charge_not_counted_twice_and_timeout_uses_remaining_time(self):
        ticks = [0.0]
        budget = self.budget(clock=lambda: ticks[0], limits=BudgetLimits(deadline_seconds=10))
        provider, transport = self.provider([response([text()])])
        with request_budget('b-child', budget):
            budget.consume('model')
            ticks[0] = 3.0
            with active_budget(budget, model_already_counted=True):
                provider(model=MODEL, messages=[])
        self.assertEqual(7, transport.requests[0][1])
        self.assertEqual(1, budget.snapshot()['attempted_calls']['model'])

    def test_reservation_overrun_prevents_next_dispatch(self):
        provider, transport = self.provider([response([text()], usage={'input_tokens':400001,'output_tokens':3})])
        budget = self.budget()
        with request_budget('b-overrun', budget):
            with self.assertRaises(DomainError) as caught:
                provider(model=MODEL, messages=[])
            self.assertEqual('COST_RESERVATION_EXCEEDED', caught.exception.code)
            with self.assertRaises(DomainError):
                provider(model=MODEL, messages=[])
        self.assertEqual(1, len(transport.requests))
