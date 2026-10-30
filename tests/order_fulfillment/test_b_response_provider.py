"""Pure CQ05 provider output schema; no stored-graph Host fixture."""
import unittest
from ontologics.harness import BudgetLimits, ExecutionBudget, request_budget
from ontologics.paid_budget import CostPolicy, ModelPrice
from copy import deepcopy
from examples.order_fulfillment import b_response_agent as response_agent
from ontologics.order_fulfillment.openai_provider import MODEL, OpenAISettings
from tests.test_openai_provider import SECRET, Transport


class ResponseProviderTests(unittest.TestCase):
    def setUp(self):
        # Synthetic prices and explicit local HTTP doubles, never a paid request.
        self.cost_policy = CostPolicy('100', (ModelPrice(MODEL, '1', '2', 400000),))
        self.budget = ExecutionBudget(BudgetLimits(max_model_calls=64), cost_policy=self.cost_policy)
        scope = request_budget('b-provider-test', self.budget)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)

    def assert_final_format(self, payload):
        format_spec = payload['text']['format']
        self.assertEqual('json_schema', format_spec['type'])
        self.assertIs(True, format_spec['strict'])
        schema = format_spec['schema']
        self.assertEqual('object', schema['type'])
        self.assertIs(False, schema['additionalProperties'])
        self.assertEqual(response_agent.ANSWER_FIELDS, set(schema['properties']))
        self.assertEqual(response_agent.ANSWER_FIELDS, set(schema['required']))
        self.assertEqual(19, len(schema['required']))
        self.assertNotIn('version', schema['properties'])
        properties = schema['properties']
        alternatives = properties['condition']['anyOf']
        self.assertEqual({'object', 'null'}, {alternative['type'] for alternative in alternatives})
        condition = next(alternative for alternative in alternatives if alternative['type'] == 'object')
        self.assertIs(False, condition['additionalProperties'])
        condition_keys = {'preparation_seconds', 'transport_seconds', 'dispatch_mode'}
        self.assertEqual(condition_keys, set(condition['properties']))
        self.assertEqual(condition_keys, set(condition['required']))
        quantities = properties['dispatch_quantities']
        self.assertEqual('object', quantities['type'])
        self.assertIs(False, quantities['additionalProperties'])
        self.assertEqual(set(response_agent.QUANTITY_FIELDS), set(quantities['properties']))
        self.assertEqual(set(response_agent.QUANTITY_FIELDS), set(quantities['required']))
        for prop in quantities['properties'].values():
            self.assertEqual({'integer', 'null'}, set(prop['type']))
        for key in ('on_time_quantity', 'shortfall_quantity'):
            self.assertEqual({'integer', 'null'}, set(properties[key]['type']))
        for key in ('estimated_full_arrival_at', 'snapshot_id'):
            self.assertEqual({'string', 'null'}, set(properties[key]['type']))
        self.assertEqual('integer', properties['data_version']['type'])
        for key in ('draft_only', 'review_required', 'execution_performed'):
            self.assertEqual('boolean', properties[key]['type'])

    def test_final_format_preserves_strict_schema_without_database_or_http(self):
        transport = Transport([])
        provider = response_agent.BoundedOpenAIProvider(OpenAISettings(SECRET), opener=transport)
        self.assertIsNone(provider._text_format())
        provider.tools = []
        spec = provider._text_format()
        self.assert_final_format({'text': {'format': spec}})
        saved = deepcopy(spec)
        spec['schema']['required'].clear()
        self.assertEqual(saved, provider._text_format())
        self.assertEqual([], transport.requests)
