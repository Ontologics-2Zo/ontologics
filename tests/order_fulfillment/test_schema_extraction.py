"""Extraction-stage parity only; no DB, provider or existing run is executed.

Historical definitions and validators come from pinned local Git objects, not
the active model.py. These tests do not claim common storage integration.
"""
import ast
import copy
import importlib
import json
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

from ontologics import model
from tests.order_fulfillment.model_oracle import legacy_model, legacy_source

from ontologics.order_fulfillment import schema, schema_export, schema_validation
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment
from ontologics.order_fulfillment.production import load_demo_production

legacy = legacy_model()


ITEM = 'contract-item:demo:B-01:1'
ROUND = 'delivery-round:demo:B-01:1'
B_TYPES = {
    'Contract', 'ContractItem', 'DeliveryRound', 'FulfillmentSnapshot',
    'FinishedGoodsStock', 'ProductionWork', 'ProductionObservation',
    'Allocation', 'Shipment', 'DeliveryCondition',
}


def findings(nodes):
    errors = []
    schema_validation.validate_domain(
        {node['id']: node for node in nodes},
        lambda nid, code, detail: errors.append(dict(node_id=nid, code=code, detail=detail)),
    )
    return sorted(errors, key=model.canonical)


class BSchemaReferenceTests(unittest.TestCase):
    def test_fresh_entry_imports_use_b_definitions_and_keep_common_checks(self):
        # A warm interpreter can conceal a circular import through sys.modules.
        script = textwrap.dedent('''
            import hashlib, importlib, json, socket, sys
            from unittest.mock import patch
            attempted = []
            def deny(*args, **kwargs):
                attempted.append(True)
                raise AssertionError('No network or database during B reference regression')
            with patch.object(socket.socket, 'connect', deny), patch.object(socket.socket, 'connect_ex', deny), patch('socket.create_connection', deny), patch('psycopg.connect', deny), patch('psycopg.Connection.connect', deny):
                importlib.import_module(sys.argv[1])
                from ontologics import model
                from ontologics.order_fulfillment import schema, schema_validation, fulfillment_validation, production
                from ontologics.order_fulfillment.contracts import load_demo_contract
                from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
                from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment
                from examples.order_fulfillment import b_agent, b_task_agent
                assert fulfillment_validation._positive_integer is schema_validation._positive_integer
                assert production._positive_integer is schema_validation._positive_integer
                assert b_agent.SCHEMA is schema.SCHEMA
                assert b_task_agent.SCHEMA is schema.SCHEMA
                assert production.validate_input_graph is schema_validation.validate_input_graph
                assert production.instant is fulfillment_validation.instant is model.instant
                assert b_agent.canonical is b_task_agent.canonical is model.canonical
                assert b_task_agent.digest is model.digest
                base = json.loads((model.ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
                for extra in ([], load_demo_fulfillment(), production.load_demo_production(), load_demo_delivery(), load_demo_delivery(mixed=True)):
                    nodes = base + extra
                    errors = []
                    schema_validation.validate_domain({n['id']: n for n in nodes}, lambda *e: errors.append(e))
                    assert not errors, errors
                previous = dict(production_work_id='work:test', observed_at='2026-10-27T09:00:00+09:00',
                                produced_quantity=10, good_quantity=10, defective_quantity=0, unit='piece')
                current = dict(previous, observed_at='2026-10-27T10:00:00+09:00', produced_quantity=30, good_quantity=30)
                assert production.calculate_completion(previous, current, 100)['remaining_seconds'] == 12600
                text = '고정 가상 접수 자료'
                business = dict(task='가상 계약 접수', mode='intake', source_id='source:test', source_text=text,
                    source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    evidence_ids=dict(contract='e:c', item='e:i', round='e:r'), data_version=1, request_key='test',
                    node_fields={k: schema.SCHEMA[k][0] for k in ('Contract', 'ContractItem', 'DeliveryRound')})
                task = b_task_agent.make_intake_task(business)
                assert json.loads(b_task_agent.IntakeAgent().messages(task)[0]['content'])['business_input'] == business
                assert not attempted, 'A prohibited connection was attempted'
        ''')
        for entry in ('ontologics.order_fulfillment.schema_validation',
                      'ontologics.order_fulfillment.fulfillment_validation',
                      'ontologics.order_fulfillment.delivery_validation',
                      'ontologics.order_fulfillment.production',
                      'examples.order_fulfillment.b_agent',
                      'examples.order_fulfillment.b_task_agent'):
            with self.subTest(first_import=entry):
                result = subprocess.run([sys.executable, '-B', '-c', script, entry],
                                        cwd=model.ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_fixed_source_intake_preparation_keeps_unregistered_task_without_database(self):
        from examples.order_fulfillment import b_intake_task as intake
        from ontologics.order_fulfillment.contracts import DEMO_SOURCE_PATH

        raw = (model.ROOT / DEMO_SOURCE_PATH).read_bytes()
        with patch('psycopg.connect') as connect, \
                patch('ontologics.service.Service') as service_factory, \
                patch.object(intake, 'ScriptedIntakeProvider') as provider:
            task = intake.make_demo_task(data_version=1)
            data = task.data()
            messages = intake.IntakeCandidateAgent().messages(task)
        self.assertEqual(raw.decode('utf-8'), data['source_text'])
        self.assertIs(schema.SCHEMA, intake.B_SCHEMA)
        self.assertEqual([data], [json.loads(message['content']) for message in messages
                                 if message['role'] == 'user'])
        self.assertFalse(B_TYPES & {node['type'] for node in data['catalog_nodes']})
        self.assertNotIn('extraction', data)
        self.assertNotIn('draft_id', data)
        self.assertEqual((), task.context.evidence_ids)
        connect.assert_not_called()
        service_factory.assert_not_called()
        provider.assert_not_called()


class LookupSchemaCompositionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(schema, 'lookup_schema', None)),
                        'B lookup metadata must have its own composition function')
        self.assertTrue(hasattr(schema, 'LOOKUP_SCHEMA'))

    def test_default_retains_current_common_and_b_types_and_order(self):
        combined = schema.lookup_schema()
        expected = copy.deepcopy(model.SCHEMA)
        for kind, definition in legacy.SCHEMA.items():
            if kind in B_TYPES:
                expected.setdefault(kind, definition)
        self.assertEqual(expected, combined)
        self.assertEqual(list(expected), list(combined))
        self.assertEqual(combined, schema.LOOKUP_SCHEMA)
        self.assertEqual(B_TYPES, set(schema.SCHEMA))
        self.assertIsNot(combined, model.SCHEMA)
        self.assertIsNot(combined, schema.LOOKUP_SCHEMA)

    def test_common_only_and_explicit_empty_inputs_do_not_depend_on_legacy_b_types(self):
        common_only = {k: copy.deepcopy(v) for k, v in model.SCHEMA.items() if k not in B_TYPES}
        common_only['FutureCommonType'] = (['name'], {})
        before = copy.deepcopy(common_only)
        combined = schema.lookup_schema(common_only)
        self.assertEqual(set(common_only) | B_TYPES, set(combined))
        for kind, definition in common_only.items():
            self.assertEqual(definition, combined[kind])
        self.assertEqual(before, common_only)
        self.assertEqual(schema.SCHEMA, schema.lookup_schema({}))
        self.assertNotIn('FutureCommonType', schema.LOOKUP_SCHEMA,
                         'A test composition must not change the live tool metadata')

    def test_nested_mutations_do_not_change_common_b_or_lookup_definitions(self):
        before = copy.deepcopy((model.SCHEMA, schema.SCHEMA, schema.LOOKUP_SCHEMA))
        combined = schema.lookup_schema()
        combined['SourceDocument'][0].append('unexpected_field')
        combined['Evidence'][1]['source_id'] = 'Contract'
        combined['Contract'][0].clear()
        combined['ContractItem'][1]['configuration_id'] = 'DeliveryRound'
        self.assertEqual(before, (model.SCHEMA, schema.SCHEMA, schema.LOOKUP_SCHEMA))
        self.assertEqual(before[2], schema.lookup_schema())

    def test_conflicting_duplicate_fields_or_references_fail_without_overwriting(self):
        for conflict in ((['different_field'], {'contract_id': 'Contract', 'configuration_id': 'Configuration'}),
                         (schema.SCHEMA['ContractItem'][0], {'contract_id': 'Contract', 'configuration_id': 'ProductModel'})):
            with self.subTest(definition=conflict):
                common = copy.deepcopy(model.SCHEMA)
                common['ContractItem'] = copy.deepcopy(conflict)
                before = copy.deepcopy((common, model.SCHEMA, schema.SCHEMA, schema.LOOKUP_SCHEMA))
                with self.assertRaisesRegex(ValueError, 'ContractItem'):
                    schema.lookup_schema(common)
                self.assertEqual(before, (common, model.SCHEMA, schema.SCHEMA, schema.LOOKUP_SCHEMA))


class SchemaExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads((model.ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        cls.variants = [cls.base, cls.base + load_demo_fulfillment(),
                        cls.base + load_demo_production(),
                        cls.base + load_demo_delivery(),
                        cls.base + load_demo_delivery(mixed=True)]

    def assert_domain_parity(self, nodes):
        before = copy.deepcopy(nodes)
        expected = sorted(legacy.validate_graph(nodes)['errors'], key=model.canonical)
        self.assertEqual(expected, findings(nodes))
        self.assertEqual(before, nodes, 'Extraction must not rewrite source-backed records')

    def assert_reference_parity(self, nodes):
        """Compare B references separately from required-field/scalar checks."""
        before = copy.deepcopy(nodes)
        b_ids = {node['id'] for node in nodes if node['type'] in B_TYPES}
        expected = sorted((e for e in legacy.validate_graph(nodes)['errors']
                           if e['code'] == 'INVALID_REFERENCE' and e['node_id'] in b_ids),
                          key=model.canonical)
        actual = [e for e in findings(nodes) if e['code'] == 'INVALID_REFERENCE']
        self.assertEqual(expected, actual)
        self.assertEqual(before, nodes, 'Reference validation must leave the full index unchanged')
        return actual

    def test_only_the_ten_b_types_are_extracted_with_unchanged_fields(self):
        self.assertEqual(B_TYPES, set(schema.SCHEMA))
        for kind in B_TYPES:
            with self.subTest(kind=kind):
                self.assertEqual(legacy.SCHEMA[kind], schema.SCHEMA[kind])
                self.assertEqual(legacy.ALLOWED_FIELDS[kind], schema.ALLOWED_FIELDS[kind])
        self.assertEqual(B_TYPES, schema.SYNTHETIC)
        self.assertEqual('graph-0.5', schema.EXTRACTED_GRAPH_VERSION)

    def test_optional_and_list_relationships_match_legacy_definitions(self):
        self.assertEqual(legacy.OPTIONAL_REFERENCES, schema.OPTIONAL_REFERENCES)
        self.assertEqual(legacy.LIST_REFERENCES, schema.LIST_REFERENCES)
        self.assertEqual({k: v for k, v in legacy.OPTIONAL_FIELDS.items() if k in B_TYPES},
                         schema.OPTIONAL_FIELDS)

    def test_structured_field_additions_are_complete(self):
        tree = ast.parse(legacy_source())
        structured = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                          if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'structured' for t in node.targets))
        common = {'evidence', 'quantity', 'positions', 'complete', 'conflict', 'specifications'}
        self.assertEqual(structured - common, schema.STRUCTURED_FIELDS)

    def test_imports_and_mutable_definitions_do_not_change_common_globals(self):
        names = ('SCHEMA', 'SCHEMA_VERSION', 'SYNTHETIC', 'OPTIONAL_FIELDS',
                 'OPTIONAL_REFERENCES', 'LIST_REFERENCES', 'ALLOWED_FIELDS')
        before = {name: (hasattr(model, name), copy.deepcopy(getattr(model, name, None))) for name in names}
        for module in (schema, schema_export, schema_validation):
            importlib.reload(module)
        self.assertEqual(before, {name: (hasattr(model, name), getattr(model, name, None)) for name in names})
        for reference in (legacy, model):
            for kind, (fields, refs) in schema.SCHEMA.items():
                if kind in reference.SCHEMA:
                    self.assertIsNot(fields, reference.SCHEMA[kind][0])
                    self.assertIsNot(refs, reference.SCHEMA[kind][1])
                    self.assertIsNot(schema.ALLOWED_FIELDS[kind], reference.ALLOWED_FIELDS[kind])
            for kind, fields in schema.OPTIONAL_FIELDS.items():
                if kind in reference.OPTIONAL_FIELDS:
                    self.assertIsNot(fields, reference.OPTIONAL_FIELDS[kind])

    def test_positive_integer_keeps_boolean_float_and_nonpositive_rejection(self):
        for value in (1, 1000, 2 ** 4096, 0, -1, True, False, 1.0, 1.5,
                      float('nan'), float('inf'), '1', None, [], {}):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(legacy._positive_integer(value), schema_validation._positive_integer(value))

    def test_valid_contract_stock_production_and_delivery_cases_match(self):
        for number, nodes in enumerate(self.variants):
            with self.subTest(variant=number):
                self.assertTrue(legacy.validate_graph(nodes)['valid'])
                self.assert_domain_parity(nodes)

    def test_contract_and_round_quantity_errors_match_without_coercion(self):
        for nid in (ITEM, ROUND):
            for value in (0, -1, True, False, 1.5, 1000.0, '1000', [], {}):
                with self.subTest(node_id=nid, value=value):
                    nodes = copy.deepcopy(self.base)
                    next(n for n in nodes if n['id'] == nid)['quantity'] = value
                    self.assert_domain_parity(nodes)

    def test_arrival_time_unit_and_due_basis_errors_match(self):
        for nid, key, value in ((ROUND, 'due_at', '2026-10-27'),
                                (ROUND, 'due_at', '2026-10-27T15:00:00'),
                                (ROUND, 'due_basis', 'departure'),
                                (ROUND, 'unit', 'box'), (ITEM, 'unit', 'box')):
            with self.subTest(node_id=nid, field=key, value=value):
                nodes = copy.deepcopy(self.base)
                next(n for n in nodes if n['id'] == nid)[key] = value
                self.assert_domain_parity(nodes)

    def test_multiple_rounds_preserve_contract_total_limit(self):
        for quantity in (400, 401):
            with self.subTest(second_round_quantity=quantity):
                nodes = copy.deepcopy(self.base)
                first = next(n for n in nodes if n['id'] == ROUND)
                first['quantity'] = 600
                nodes.append(dict(first, id=ROUND + ':second', quantity=quantity))
                self.assert_domain_parity(nodes)
                self.assertEqual(quantity == 401, any(
                    e['code'] == 'ROUND_QUANTITY_EXCEEDED' for e in findings(nodes)))

    def test_existing_fulfillment_production_and_delivery_validators_are_reused(self):
        changes = [(1, 'FinishedGoodsStock', 'quantity', -1),
                   (2, 'ProductionObservation', 'produced_quantity', -1),
                   (3, 'DeliveryCondition', 'transport_seconds', -1),
                   (4, 'DeliveryCondition', 'dispatch_mode', 'automatic')]
        for variant, kind, key, value in changes:
            with self.subTest(kind=kind, field=key):
                nodes = copy.deepcopy(self.variants[variant])
                next(n for n in nodes if n['type'] == kind)[key] = value
                self.assertTrue(findings(nodes))
                self.assert_domain_parity(nodes)

    def test_missing_confirmations_stay_unknown_without_becoming_invalid_facts(self):
        nodes = copy.deepcopy(self.variants[1])
        snapshot = next(n for n in nodes if n['type'] == 'FulfillmentSnapshot')
        snapshot['confirmations'] = {}
        self.assert_domain_parity(nodes)
        self.assertEqual({}, snapshot['confirmations'])

    def test_b_domain_rules_do_not_replace_required_common_checks(self):
        nodes = copy.deepcopy(self.base)
        next(n for n in nodes if n['type'] == 'Contract')['origin'] = 'real'
        self.assertEqual([], findings(nodes))
        self.assertFalse(legacy.validate_graph(nodes)['valid'])

    def test_b_jsonld_nodes_and_relationship_context_match_existing_export(self):
        seen = set()
        for nodes in self.variants:
            before = copy.deepcopy(nodes)
            exported = legacy.graph_jsonld(nodes)
            expected = {row['id']: row for row in exported['@graph']}
            for node in nodes:
                if node['type'] in B_TYPES:
                    seen.add(node['type'])
                    row = schema_export.jsonld_node(node)
                    self.assertEqual(expected[row['id']], row)
            for key, value in schema_export.jsonld_context().items():
                self.assertEqual(exported['@context'][key], value)
            self.assertEqual(before, nodes)
        self.assertEqual(B_TYPES, seen)
        with self.assertRaises(ValueError):
            schema_export.jsonld_node(self.base[0])

    def test_optional_references_remain_absent_and_list_references_are_urns(self):
        nodes = self.variants[1]
        stock_only = next(n for n in nodes if n['type'] == 'Allocation' and 'stock_id' in n
                          and 'production_work_id' not in n)
        self.assertNotIn('production_work_id', schema_export.jsonld_node(stock_only))
        snapshot = next(n for n in nodes if n['type'] == 'FulfillmentSnapshot')
        self.assertEqual(['urn:ontologics:entity:' + nid for nid in snapshot['round_ids']],
                         schema_export.jsonld_node(snapshot)['round_ids'])

    def test_list_reference_review_cases_match_full_legacy_findings(self):
        for value in ([], [ROUND, ROUND], ['contract:demo:B-01'], ['delivery-round:missing']):
            with self.subTest(round_ids=value):
                nodes = copy.deepcopy(self.variants[1])
                snapshot = next(n for n in nodes if n['type'] == 'FulfillmentSnapshot')
                snapshot['round_ids'] = value
                errors = self.assert_reference_parity(nodes)
                self.assertTrue(errors)
                self.assert_domain_parity(nodes)

    def test_list_reference_shapes_are_rejected_without_type_errors(self):
        for value in (None, '', ROUND, False, 1, {}, (), [''], ['   '], [None], [False], [[]], [{}]):
            with self.subTest(round_ids=value):
                nodes = copy.deepcopy(self.variants[1])
                snapshot = next(n for n in nodes if n['type'] == 'FulfillmentSnapshot')
                snapshot['round_ids'] = value
                errors = self.assert_reference_parity(nodes)
                self.assertIn('round_ids must be a unique nonempty reference list',
                              [e['detail'] for e in errors])
        nodes = copy.deepcopy(self.variants[1])
        next(n for n in nodes if n['type'] == 'FulfillmentSnapshot').pop('round_ids')
        self.assertTrue(self.assert_reference_parity(nodes))

    def test_every_required_b_reference_checks_existing_target_and_type(self):
        covered = set()
        for variant in self.variants:
            for original in variant:
                kind = original['type']
                if kind not in B_TYPES:
                    continue
                for field, target in schema.SCHEMA[kind][1].items():
                    if field in schema.OPTIONAL_REFERENCES.get(kind, set()) or (kind, field) in covered:
                        continue
                    covered.add((kind, field))
                    for value in ('missing:reference', 'source:wrong-type', None, False, [], {}):
                        with self.subTest(kind=kind, field=field, value=value):
                            nodes = copy.deepcopy(variant)
                            wrong_type = next(n['id'] for n in nodes if n['type'] == 'SourceDocument')
                            next(n for n in nodes if n['id'] == original['id'])[field] = (
                                wrong_type if value == 'source:wrong-type' else value)
                            errors = self.assert_reference_parity(nodes)
                            self.assertIn(dict(node_id=original['id'], code='INVALID_REFERENCE',
                                               detail=field + ' -> ' + target), errors)
        expected = {(kind, field) for kind, (_, refs) in schema.SCHEMA.items() for field in refs
                    if field not in schema.OPTIONAL_REFERENCES.get(kind, set())}
        self.assertEqual(expected, covered)

    def test_absent_optional_references_keep_allocation_exactly_one_source_rule(self):
        for fields in ((), ('stock_id',), ('production_work_id',), ('stock_id', 'production_work_id')):
            with self.subTest(present_fields=fields):
                nodes = copy.deepcopy(self.variants[1])
                allocation = next(n for n in nodes if n['type'] == 'Allocation')
                for key in ('stock_id', 'production_work_id'):
                    allocation.pop(key, None)
                for field in fields:
                    target = schema.SCHEMA['Allocation'][1][field]
                    allocation[field] = next(n['id'] for n in nodes if n['type'] == target)
                self.assertEqual([], self.assert_reference_parity(nodes))
                allocation_errors = [e for e in findings(nodes) if e['node_id'] == allocation['id']]
                self.assertEqual(len(fields) != 1, any(
                    e['code'] == 'INVALID_ALLOCATION_TARGET' for e in allocation_errors))

    def test_present_optional_references_do_not_treat_null_or_bad_values_as_absent(self):
        for field in ('stock_id', 'production_work_id'):
            for value in (None, '', False, 0, [], {}, 'missing:reference', ROUND):
                with self.subTest(field=field, value=value):
                    nodes = copy.deepcopy(self.variants[1])
                    allocation = next(n for n in nodes if n['type'] == 'Allocation')
                    for key in ('stock_id', 'production_work_id'):
                        allocation.pop(key, None)
                    allocation[field] = value
                    errors = self.assert_reference_parity(nodes)
                    target = schema.SCHEMA['Allocation'][1][field]
                    self.assertIn(dict(node_id=allocation['id'], code='INVALID_REFERENCE',
                                       detail=field + ' -> ' + target), errors)

    def test_reference_validation_leaves_common_node_checks_to_common_validator(self):
        nodes = copy.deepcopy(self.base)
        next(n for n in nodes if n['type'] == 'ProductRevision')['model_id'] = 'missing:model'
        self.assertEqual([], self.assert_reference_parity(nodes))
        self.assertTrue(any(e['code'] == 'INVALID_REFERENCE' for e in legacy.validate_graph(nodes)['errors']))

    def test_tbox_fragment_preserves_b_terms_without_replacing_common_ontology(self):
        terms = schema_export.tbox_terms().splitlines()
        self.assertEqual(len(terms), len(set(terms)))
        self.assertTrue(set(terms).issubset(set(legacy.tbox_turtle().splitlines())))
        classes = {f'o:{kind} a owl:Class .' for kind in B_TYPES}
        self.assertEqual(classes, {line for line in terms if ' a owl:Class ' in line})
        for context_key in schema_export.jsonld_context():
            self.assertTrue(any(line.startswith(f'o:{context_key} a owl:ObjectProperty ;') for line in terms))
        self.assertFalse(any('owl:Ontology' in line or 'owl:versionInfo' in line for line in terms))
