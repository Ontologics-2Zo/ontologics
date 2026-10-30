"""Pure legacy registration metadata; execution uses the seven input task roles."""
import unittest
from examples.order_fulfillment.b_agent import INTAKE_PROMPT, PROMISE_PROMPT
from examples.order_fulfillment.b_fulfillment_agent import PROMPT as FULFILLMENT_PROMPT
from examples.order_fulfillment.b_production_agent import PROMPT as PRODUCTION_PROMPT
from examples.order_fulfillment.b_delivery_agent import PROMPT as DELIVERY_PROMPT
from examples.order_fulfillment.b_response_agent import PROMPT as RESPONSE_PROMPT
from examples.order_fulfillment.b_comparison_agent import PROMPT as COMPARISON_PROMPT
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.order_fulfillment.tool_specs import B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS, B_PRODUCTION_TOOLS, B_DELIVERY_TOOLS, B_RESPONSE_TOOLS, B_COMPARISON_TOOLS


class BHostAgentTests(unittest.TestCase):
    def test_registrations_reuse_existing_prompts_and_exact_role_tools(self):
        self.assertEqual(7, len(REGISTRATIONS))
        expected = {'b_intake': (INTAKE_PROMPT, B_INTAKE_TOOLS),
                    'b_promise': (PROMISE_PROMPT, B_PROMISE_TOOLS),
                    'b_fulfillment': (FULFILLMENT_PROMPT, B_FULFILLMENT_TOOLS),
                    'b_production': (PRODUCTION_PROMPT, B_PRODUCTION_TOOLS),
                    'b_delivery': (DELIVERY_PROMPT, B_DELIVERY_TOOLS),
                    'b_response': (RESPONSE_PROMPT, B_RESPONSE_TOOLS),
                    'b_comparison': (COMPARISON_PROMPT, B_COMPARISON_TOOLS)}
        self.assertEqual(set(expected), {r.name for r in REGISTRATIONS})
        for registration in REGISTRATIONS:
            prompt, tools = expected[registration.name]
            self.assertEqual(prompt, registration.instructions)
            self.assertEqual(frozenset(tools), registration.allowed_tools)
