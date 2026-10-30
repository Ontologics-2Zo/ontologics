"""Pure B tool contract preserved independently of retired stored fixtures."""
import unittest

from ontologics.order_fulfillment.tool_specs import (
    B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS, B_PRODUCTION_TOOLS, KNOWLEDGE_TOOLS, PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS, function_tools
)


class DeliveryForecastToolContractTests(unittest.TestCase):
    def test_D21_tool_schema_and_existing_roles_keep_separate_authority(self):
        spec=function_tools(('delivery_forecast',))[0]
        self.assertEqual({'delivery_round_id','as_of','version'},set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        for roles in (PRODUCT_TOOLS,QUALITY_TOOLS,B_INTAKE_TOOLS,B_PROMISE_TOOLS,
                      B_FULFILLMENT_TOOLS,B_PRODUCTION_TOOLS,KNOWLEDGE_TOOLS,SUPERVISOR_TOOLS):
            self.assertNotIn('delivery_forecast',roles)
