"""Pure comparison tool schema and bound gateway authority; no B storage."""
import unittest
from unittest.mock import Mock

from ontologics.order_fulfillment.service import AgentGateway, Service, DomainError
from ontologics.order_fulfillment.tool_specs import (
    B_COMPARISON_TOOLS, B_RESPONSE_TOOLS, B_DELIVERY_TOOLS,
    PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS, function_tools,
)


class ResponseComparisonToolContractTests(unittest.TestCase):
    def test_D24_D27_tool_contract_does_not_widen_existing_roles(self):
        arguments = dict(delivery_round_id='round:test', before_version=1,
                         before_as_of='2026-10-27T10:00:00+09:00', after_version=2,
                         after_as_of='2026-10-27T11:00:00+09:00')
        spec, = function_tools(['compare_response_plan'])
        self.assertEqual(set(arguments), set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        self.assertEqual(('compare_response_plan', 'lookup_many'), B_COMPARISON_TOOLS)
        for tools in (PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS, B_RESPONSE_TOOLS, B_DELIVERY_TOOLS):
            self.assertNotIn('compare_response_plan', tools)
        service = object.__new__(Service)
        service._run = Mock(side_effect=lambda actor, tool, args, action: action())
        service._nodes = Mock()
        with self.assertRaises(DomainError) as caught:
            AgentGateway(service, 'comparison-reader', allowed_tools=B_RESPONSE_TOOLS).call(
                'compare_response_plan', arguments)
        self.assertEqual('TOOL_NOT_ALLOWED', caught.exception.code)
        self.assertEqual('comparison-reader', service._run.call_args.args[0])
        service._nodes.assert_not_called()
