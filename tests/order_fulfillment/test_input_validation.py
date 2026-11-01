"""B input calculations without registering B types in the common graph."""
import copy
import json
import subprocess
import sys
import textwrap
import unittest

from ontologics import model
from ontologics.order_fulfillment import schema, schema_validation
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment
from ontologics.order_fulfillment.production import load_demo_production


ROUND = 'delivery-round:demo:B-01:1'


class InputValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((model.ROOT / 'data/catalog.json').read_text())['nodes']

    def setUp(self):
        self.nodes = copy.deepcopy(self.catalog + load_demo_contract() + load_demo_delivery())

    def assert_invalid(self, nodes, code):
        before = copy.deepcopy(nodes)
        result = schema_validation.validate_input_graph(nodes)
        self.assertFalse(result['valid'], result)
        self.assertIn(code, {e['code'] for e in result['errors']}, result)
        self.assertEqual(before, nodes)

    def test_fixed_inputs_validate_without_mutating_inputs_or_shared_schema(self):
        definitions = copy.deepcopy((model.SCHEMA, schema.SCHEMA))
        for extra in ([], load_demo_fulfillment(), load_demo_production(),
                      load_demo_delivery(), load_demo_delivery(mixed=True)):
            with self.subTest(extra_types=sorted({n['type'] for n in extra})):
                nodes = self.catalog + load_demo_contract() + extra
                before = copy.deepcopy(nodes)
                result = schema_validation.validate_input_graph(nodes)
                self.assertTrue(result['valid'], result)
                self.assertEqual(before, nodes)
        self.assertEqual(definitions, (model.SCHEMA, schema.SCHEMA))

    def test_b_required_scalar_extra_origin_and_evidence_checks(self):
        for field, value, code in (
            ('company', None, 'MISSING_FIELD'),
            ('company', [], 'INVALID_FIELD_TYPE'),
            ('unregistered_claim', 'safe', 'UNKNOWN_FIELD'),
            ('origin', 'real', 'DEMO_OPERATIONS_ONLY'),
            ('evidence', [], 'EVIDENCE_REQUIRED'),
            ('evidence', ['missing'], 'EVIDENCE_NOT_FOUND'),
            ('evidence', {}, 'INVALID_EVIDENCE'),
        ):
            with self.subTest(field=field, value=value):
                nodes = copy.deepcopy(self.nodes)
                next(n for n in nodes if n['type'] == 'Contract')[field] = value
                self.assert_invalid(nodes, code)

    def test_b_references_caps_and_times_still_fail(self):
        for kind, field, value, code in (
            ('ContractItem', 'configuration_id', ROUND, 'INVALID_REFERENCE'),
            ('DeliveryRound', 'quantity', True, 'INVALID_QUANTITY'),
            ('DeliveryRound', 'quantity', 1001, 'ROUND_QUANTITY_EXCEEDED'),
            ('DeliveryRound', 'due_at', '2026-10-27', 'INVALID_TIME'),
            ('Allocation', 'quantity', 1001, 'ALLOCATION_QUANTITY_EXCEEDED'),
            ('DeliveryCondition', 'transport_seconds', -1, 'INVALID_DURATION'),
        ):
            with self.subTest(kind=kind, field=field):
                nodes = copy.deepcopy(self.nodes)
                next(n for n in nodes if n['type'] == kind)[field] = value
                self.assert_invalid(nodes, code)

    def test_b_evidence_must_keep_synthetic_origin(self):
        nodes = copy.deepcopy(self.nodes)
        next(n for n in nodes if n['type'] == 'Contract')['evidence'] = [
            next(n['id'] for n in nodes if n['type'] == 'Evidence' and n['origin'] == 'real')]
        self.assert_invalid(nodes, 'EVIDENCE_ORIGIN_MISMATCH')

    def test_common_source_identity_and_reference_validation_are_reused(self):
        for kind, field, value, code in (
            ('SourceDocument', 'sha256', '0' * 64, 'SOURCE_INTEGRITY'),
            ('SourceDocument', 'path', '../README.md', 'SOURCE_INTEGRITY'),
            ('Evidence', 'source_id', 'missing', 'INVALID_REFERENCE'),
            ('ProductRevision', 'model_id', ROUND, 'INVALID_REFERENCE'),
            ('Configuration', 'origin', 'synthetic', 'REAL_DESIGN_REQUIRED'),
        ):
            with self.subTest(kind=kind, field=field):
                nodes = copy.deepcopy(self.nodes)
                next(n for n in nodes if n['type'] == kind)[field] = value
                self.assert_invalid(nodes, code)
        duplicate = copy.deepcopy(next(n for n in self.nodes if n['type'] == 'Configuration'))
        duplicate['id'] += ':duplicate'
        self.assert_invalid(self.nodes + [duplicate], 'DUPLICATE_IDENTITY')

    def test_malformed_shape_unknown_types_and_duplicate_ids_are_rejected(self):
        for nodes, code in ((None, 'INVALID_GRAPH_SIZE'), ([{}], 'INVALID_NODE'),
                            ([None], 'INVALID_NODE'), ([False], 'INVALID_NODE'),
                            ([dict(id='invalid', type=[])], 'UNKNOWN_TYPE'),
                            ([dict(id='bad', type='UnregisteredBType')], 'UNKNOWN_TYPE'),
                            ([dict(id=[], type='Contract')], 'INVALID_NODE'),
                            (self.nodes + [copy.deepcopy(self.nodes[-1])], 'DUPLICATE_ID'),
                            (self.nodes + [dict(id=self.nodes[0]['id'], type='Contract')], 'DUPLICATE_ID')):
            with self.subTest(code=code):
                self.assert_invalid(nodes, code)

    def test_missing_coverage_stays_valid_but_unknown_for_calculation(self):
        from ontologics.order_fulfillment.delivery_forecast import delivery_forecast
        next(n for n in self.nodes if n['type'] == 'FulfillmentSnapshot')['confirmations'] = {}
        self.assertTrue(schema_validation.validate_input_graph(self.nodes)['valid'])
        result = delivery_forecast(self.nodes, ROUND, '2026-10-27T10:00:00+09:00',
                                   queried_at='2026-10-27T10:10:00+09:00')
        self.assertEqual('needs_confirmation', result['status'])
        self.assertIsNone(result['on_time_quantity'])

    def test_fresh_b_calculations_run_with_original_main_model(self):
        # Substitute only inside a child interpreter; never edit common files or
        # patch shared production globals to make B types acceptable to storage.
        script = textwrap.dedent('''
            import copy, json, socket, sys
            from unittest.mock import patch
            from tests.order_fulfillment.model_oracle import main_model
            import ontologics
            common = main_model()
            sys.modules['ontologics.model'] = common
            ontologics.model = common
            def deny(*args, **kwargs):
                raise AssertionError('No network or DB in the input calculation regression')
            with patch.object(socket.socket, 'connect', deny), patch('psycopg.connect', deny):
                from ontologics.order_fulfillment import schema, schema_validation
                from ontologics.order_fulfillment.contracts import load_demo_contract, delivery_promise
                from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment, fulfillment_status
                from ontologics.order_fulfillment.production import load_demo_production, production_completion, WORK
                from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
                from ontologics.order_fulfillment.delivery_forecast import delivery_forecast
                from ontologics.order_fulfillment.response_plan import response_plan
                from ontologics.order_fulfillment.response_comparison import compare_input_response_plan
                from ontologics.order_fulfillment.recalculation_demo import load_demo_recalculation_input, AFTER_AS_OF
                definitions = copy.deepcopy(common.SCHEMA)
                assert common.SCHEMA_VERSION == 'graph-0.1'
                assert not (set(common.SCHEMA) & set(schema.SCHEMA))
                base = json.loads((common.ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
                rid, at = 'delivery-round:demo:B-01:1', '2026-10-27T10:00:00+09:00'
                assert not common.validate_graph(base)['valid'], 'Common storage must still reject B types'
                assert schema_validation.validate_input_graph(base)['valid']
                promise = delivery_promise(base, rid)
                assert promise['delivery_round']['quantity'] == 1000
                fulfillment = fulfillment_status(base + load_demo_fulfillment(), rid, at, queried_at=at)
                assert fulfillment['states'] == dict(delivered=100, in_transit=150, warehouse=250, in_progress=300, ready=100, waiting=100)
                production = production_completion(base + load_demo_production(), WORK, rid, at, queried_at=at)
                assert production['estimated_completion_at'] == '2026-10-27T14:26:40+09:00'
                nodes = base + load_demo_delivery()
                delivery = delivery_forecast(nodes, rid, at, queried_at=at)
                assert (delivery['on_time_quantity'], delivery['shortfall_quantity']) == (960, 40)
                response = response_plan(nodes, rid, at, queried_at=at)
                assert response['forecast'] == delivery
                golden = json.loads((common.ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())
                for case in ('defects11', 'transport2h'):
                    comparison = compare_input_response_plan(nodes, load_demo_recalculation_input(case),
                        rid, at, AFTER_AS_OF[case], before_revision='baseline',
                        after_revision=case, queried_at='2026-10-27T12:00:00+09:00')
                    assert comparison['quantity_deltas'] == golden['cases'][case]['quantity_deltas']
                    assert comparison['time_deltas'] == golden['cases'][case]['time_deltas']
                    assert 'data_version' not in comparison['after']
                assert common.SCHEMA == definitions
            print('Original main model: B promise and CQ02–06 calculations passed; common storage still rejects B')
        ''')
        result = subprocess.run([sys.executable, '-B', '-c', script], cwd=model.ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)


class DeliveryPromiseTests(unittest.TestCase):
    def setUp(self):
        self.nodes = json.loads((model.ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()

    def test_input_projection_has_promise_only_and_no_approval_or_forecast(self):
        from ontologics.order_fulfillment.contracts import delivery_promise
        before = copy.deepcopy(self.nodes)
        result = delivery_promise(self.nodes, ROUND)
        self.assertEqual({'contract', 'contract_item', 'delivery_round', 'configuration',
                          'revision', 'product', 'fulfillment_assessed', 'input_state'}, set(result))
        self.assertEqual('unregistered', result['input_state'])
        self.assertIs(False, result['fulfillment_assessed'])
        self.assertEqual(1000, result['delivery_round']['quantity'])
        result['delivery_round']['quantity'] = 1
        self.assertEqual(before, self.nodes, 'Returned input data must not mutate the supplied input')

    def test_projection_rejects_bad_target_and_invalid_input(self):
        from ontologics.order_fulfillment.contracts import delivery_promise
        for target in (None, False, [], ''):
            with self.subTest(target=target), self.assertRaises(ValueError):
                delivery_promise(self.nodes, target)
        for target in ('missing', self.nodes[0]['id']):
            with self.subTest(target=target), self.assertRaises(KeyError):
                delivery_promise(self.nodes, target)
        next(n for n in self.nodes if n['type'] == 'DeliveryRound')['quantity'] = -1
        with self.assertRaises(ValueError):
            delivery_promise(self.nodes, ROUND)
