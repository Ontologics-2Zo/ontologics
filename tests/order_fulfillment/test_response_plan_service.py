"""Pure response-plan tool contract after stored-graph fixture retirement."""
import unittest

from ontologics.order_fulfillment.tool_specs import (
    B_DELIVERY_TOOLS, B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS,
    B_PRODUCTION_TOOLS, KNOWLEDGE_TOOLS, PRODUCT_TOOLS, QUALITY_TOOLS,
    SUPERVISOR_TOOLS, function_tools,
)


class ResponsePlanToolContractTests(unittest.TestCase):
    def test_schema_does_not_grant_existing_agents_response_authority(self):
        spec = function_tools(('response_plan',))[0]
        self.assertEqual({'delivery_round_id', 'as_of', 'version'}, set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        for roles in (PRODUCT_TOOLS, QUALITY_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS,
                      B_FULFILLMENT_TOOLS, B_PRODUCTION_TOOLS, B_DELIVERY_TOOLS, KNOWLEDGE_TOOLS, SUPERVISOR_TOOLS):
            self.assertNotIn('response_plan', roles)
