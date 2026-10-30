"""B schemas compose with common tools without extending their registry."""
import copy
import unittest

from ontologics import tool_specs as common
from ontologics import model
from ontologics.order_fulfillment import schema, tool_specs as b
from ontologics.order_fulfillment.service import B_TOOLS


class ToolSchemaTests(unittest.TestCase):
    def test_batch_kind_enum_preserves_current_common_and_b_type_order(self):
        before = copy.deepcopy(model.SCHEMA)
        common_tool = common.function_tools(('lookup',))
        spec, = b.function_tools(('lookup_many',))
        kinds = spec['parameters']['properties']['kind']['enum']
        self.assertEqual(list(schema.lookup_schema(model.SCHEMA)), kinds)
        self.assertTrue(set(schema.SCHEMA) <= set(kinds))
        self.assertEqual(before, model.SCHEMA)
        self.assertEqual(common_tool, common.function_tools(('lookup',)))

    def test_all_b_tools_exist_only_in_b_registry(self):
        specs = b.function_tools(sorted(B_TOOLS))
        self.assertEqual(sorted(B_TOOLS), [spec['name'] for spec in specs])
        for name in B_TOOLS:
            with self.subTest(tool=name), self.assertRaises(ValueError):
                common.function_tools((name,))

    def test_common_specs_and_role_lists_are_preserved(self):
        for names in (common.PRODUCT_TOOLS, common.QUALITY_TOOLS,
                      common.KNOWLEDGE_TOOLS, common.SUPERVISOR_TOOLS):
            with self.subTest(tools=names):
                self.assertEqual(common.function_tools(names), b.function_tools(names))
        for name in ('PRODUCT_TOOLS', 'QUALITY_TOOLS', 'KNOWLEDGE_TOOLS', 'SUPERVISOR_TOOLS'):
            self.assertEqual(getattr(common, name), getattr(b, name))

    def test_returned_nested_schemas_cannot_change_other_calls(self):
        before = b.function_tools(('lookup', 'delivery_round', 'lookup_many'))
        modified = b.function_tools(('lookup', 'delivery_round', 'lookup_many'))
        modified[0]['parameters']['properties']['kind']['enum'].clear()
        modified[1]['parameters']['required'].clear()
        modified[2]['parameters']['properties']['node_ids']['items']['type'] = 'number'
        self.assertEqual(before, b.function_tools(('lookup', 'delivery_round', 'lookup_many')))
        self.assertEqual(common.function_tools(('lookup',))[0], before[0])

    def test_role_schemas_preserve_explicit_versions_and_reject_authority(self):
        for name in B_TOOLS:
            spec, = b.function_tools((name,))
            fields = spec['parameters']['properties']
            required = spec['parameters']['required']
            self.assertTrue({'actor', 'principals'}.isdisjoint(fields))
            for field in fields:
                if field.endswith('version'):
                    self.assertIn(field, required)
        for invalid in ((), ('review',), ('apply',), ('bootstrap',), ('lookup', 'lookup')):
            with self.subTest(tools=invalid), self.assertRaises(ValueError):
                b.function_tools(invalid)
