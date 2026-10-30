"""Historical comparison evidence, not a new runtime validation path."""
import copy
import hashlib
import sys
import unittest
from unittest.mock import patch

from ontologics import model
from ontologics.ingest import build_catalog
from ontologics.order_fulfillment import schema_validation
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from tests.order_fulfillment import model_oracle


class HistoricalModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_catalog()['nodes']
        cls.nodes = cls.catalog + load_demo_contract() + load_demo_delivery()

    def test_sources_are_pinned_to_distinct_main_and_b_definitions(self):
        main, legacy = model_oracle.main_model(), model_oracle.legacy_model()
        self.assertEqual(('graph-0.1', 12), (main.SCHEMA_VERSION, len(main.SCHEMA)))
        self.assertEqual(('graph-0.5', 22), (legacy.SCHEMA_VERSION, len(legacy.SCHEMA)))
        self.assertEqual(model_oracle.LEGACY_HASHES['model'],
                         hashlib.sha256(model_oracle.legacy_source().encode()).hexdigest())
        self.assertNotIn('Contract', main.SCHEMA)
        self.assertIn('Contract', legacy.SCHEMA)

    def test_tampered_source_is_rejected_before_execution(self):
        with patch.object(model_oracle.subprocess, 'check_output',
                          return_value=b'raise AssertionError("untrusted source executed")'):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                model_oracle.legacy_model()

    def test_validator_dependencies_are_historical_not_live_b_functions(self):
        legacy = model_oracle.legacy_model()
        with patch('ontologics.order_fulfillment.fulfillment_validation.validate_fulfillment',
                   side_effect=AssertionError('Live fulfillment validator used')), \
             patch('ontologics.order_fulfillment.production_validation.validate_production_observations',
                   side_effect=AssertionError('Live production validator used')), \
             patch('ontologics.order_fulfillment.delivery_validation.validate_delivery_conditions',
                   side_effect=AssertionError('Live delivery validator used')):
            self.assertTrue(legacy.validate_graph(self.nodes)['valid'])

    def test_relative_validator_source_is_also_hash_checked(self):
        original = model_oracle.subprocess.check_output

        def read(command, **kwargs):
            raw = original(command, **kwargs)
            if command[-1].endswith('/fulfillment_validation.py'):
                return raw + b'\nraise AssertionError("untrusted dependency executed")\n'
            return raw

        legacy = model_oracle.legacy_model()
        with patch.object(model_oracle.subprocess, 'check_output', side_effect=read):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                legacy.validate_graph(self.nodes)

    def test_loading_and_mutation_do_not_change_live_modules_or_other_instances(self):
        before = copy.deepcopy((model.SCHEMA, model.SCHEMA_VERSION))
        registered = set(sys.modules)
        first, second = model_oracle.legacy_model(), model_oracle.legacy_model()
        first.SCHEMA['Contract'][0].clear()
        self.assertTrue(second.SCHEMA['Contract'][0])
        self.assertTrue(second.validate_graph(self.nodes)['valid'])
        self.assertEqual(before, (model.SCHEMA, model.SCHEMA_VERSION))
        self.assertFalse({name for name in set(sys.modules) - registered
                          if name.startswith('_b_test_history')})

    def test_whole_graph_checks_are_not_implied_by_b_domain_success(self):
        legacy = model_oracle.legacy_model()
        cases = [('origin', 'real', 'DEMO_OPERATIONS_ONLY'),
                 ('evidence', [], 'EVIDENCE_REQUIRED'),
                 ('unrecognized_field', 'value', 'UNKNOWN_FIELD'),
                 ('duplicate', None, 'DUPLICATE_ID')]
        for field, value, expected_code in cases:
            with self.subTest(field=field):
                nodes = copy.deepcopy(self.nodes)
                contract = next(n for n in nodes if n['type'] == 'Contract')
                if field == 'duplicate':
                    nodes.append(copy.deepcopy(contract))
                else:
                    contract[field] = value
                before = copy.deepcopy(nodes)
                errors = []
                schema_validation.validate_domain({n['id']: n for n in nodes},
                                                  lambda *error: errors.append(error))
                self.assertEqual([], errors)
                report = legacy.validate_graph(nodes)
                self.assertFalse(report['valid'])
                self.assertIn(expected_code, {error['code'] for error in report['errors']})
                self.assertEqual(before, nodes)

    def test_main_common_only_success_does_not_prove_b_graph_compatibility(self):
        main, legacy = model_oracle.main_model(), model_oracle.legacy_model()
        self.assertTrue(main.validate_graph(self.catalog)['valid'])
        self.assertTrue(legacy.validate_graph(self.nodes)['valid'])
        report = main.validate_graph(self.nodes)
        self.assertFalse(report['valid'])
        self.assertEqual({'UNKNOWN_TYPE'}, {error['code'] for error in report['errors']})


if __name__ == '__main__':
    unittest.main()
