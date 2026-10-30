"""Pure B tool contract preserved independently of retired stored fixtures."""
import unittest

from ontologics.order_fulfillment.tool_specs import (
    PRODUCT_TOOLS, QUALITY_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS, function_tools
)


class FulfillmentToolContractTests(unittest.TestCase):
    def test_read_tool_contract_does_not_expand_existing_agent_roles(self):
        spec = function_tools(('fulfillment_status',))[0]
        self.assertEqual({'delivery_round_id', 'as_of', 'version'}, set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        for roles in (PRODUCT_TOOLS, QUALITY_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS):
            self.assertNotIn('fulfillment_status', roles)
