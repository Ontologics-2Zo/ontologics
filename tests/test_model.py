import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ontologics.model import ALLOWED_FIELDS, ROOT, SCHEMA, validate_graph


class GraphValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        index = {n['id']: n for n in cls.catalog}
        cls.bom = next(n for n in cls.catalog if n['type'] == 'BOMItem' and n['population'] == 'populated')
        dependencies = set()

        def visit(nid):
            if nid in dependencies:
                return
            dependencies.add(nid)
            node = index[nid]
            for key in SCHEMA[node['type']][1]:
                visit(node[key])
            for eid in node['evidence']:
                visit(eid)

        visit(cls.bom['id'])
        cls.real_nodes = [copy.deepcopy(n) for n in cls.catalog if n['id'] in dependencies]
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.json', prefix='test-evidence-', dir=ROOT / 'data', delete=False) as stream:
            stream.write(b'{"scope":"synthetic validation fixture"}\n')
            cls.source_path = Path(stream.name)
        cls.addClassCleanup(cls.source_path.unlink)
        cls.synthetic_nodes = [
            {'id': 'source:test-operations', 'type': 'SourceDocument', 'origin': 'synthetic', 'evidence': [],
             'url': 'urn:demo:validation-fixture', 'path': str(cls.source_path.relative_to(ROOT)),
             'sha256': hashlib.sha256(cls.source_path.read_bytes()).hexdigest(), 'version': 'test-1',
             'license': 'Synthetic test fixture', 'collected_at': '2026-10-22T00:10:00+09:00',
             'kind': 'synthetic_operations'},
            {'id': 'evidence:test-operations', 'type': 'Evidence', 'origin': 'synthetic', 'evidence': [],
             'source_id': 'source:test-operations', 'locator': '/scope', 'description': 'Synthetic test records'},
            {'id': 'unit:test', 'type': 'ProductUnit', 'origin': 'synthetic', 'evidence': ['evidence:test-operations'],
             'serial': 'U104', 'company': 'Example assembly company', 'configuration_id': cls.bom['configuration_id']},
            {'id': 'lot:test', 'type': 'ComponentLot', 'origin': 'synthetic', 'evidence': ['evidence:test-operations'],
             'company': 'Example assembly company', 'supplier': 'Demo supplier', 'lot_code': 'L07',
             'component_id': cls.bom['component_id']},
            {'id': 'installation:test', 'type': 'Installation', 'origin': 'synthetic', 'evidence': ['evidence:test-operations'],
             'unit_id': 'unit:test', 'lot_id': 'lot:test', 'position': cls.bom['positions'][0],
             'installed_at': '2026-08-20T09:00:00+09:00'},
            {'id': 'coverage:test', 'type': 'HistoryCoverage', 'origin': 'synthetic', 'evidence': ['evidence:test-operations'],
             'unit_id': 'unit:test', 'start': '2026-08-01T00:00:00+09:00', 'end': '2026-09-21T00:00:00+09:00', 'complete': True},
            {'id': 'movement:test', 'type': 'InventoryMovement', 'origin': 'synthetic', 'evidence': ['evidence:test-operations'],
             'lot_id': 'lot:test', 'quantity': 10, 'occurred_at': '2026-08-01T00:00:00+09:00', 'movement': 'receipt'},
        ]

    def setUp(self):
        self.nodes = copy.deepcopy(self.real_nodes + self.synthetic_nodes)
        self.index = {n['id']: n for n in self.nodes}

    def assert_invalid(self, code=None):
        result = validate_graph(self.nodes)
        self.assertFalse(result['valid'], result)
        self.assertTrue(all({'node_id', 'code', 'detail'} <= set(e) for e in result['errors']))
        if code:
            self.assertIn(code, {e['code'] for e in result['errors']})
        return result

    def test_real_catalog_retains_verified_metadata_and_population_warnings(self):
        result = validate_graph(self.catalog)
        self.assertTrue(result['valid'], result['errors'])
        self.assertTrue(any(w['code'] == 'POPULATION_UNCONFIRMED' for w in result['warnings']))

    def test_allowed_synthetic_operations_validate(self):
        result = validate_graph(self.nodes)
        self.assertTrue(result['valid'], result['errors'])

    def test_malformed_nodes_and_unhashable_types_return_structured_errors(self):
        for invalid in (None, [], 'node', 42, {'id': 'bad', 'type': []}, {'id': [], 'type': 'ProductUnit'}):
            with self.subTest(invalid=invalid):
                self.nodes.append(invalid)
                self.assert_invalid()
                self.nodes.pop()

    def test_malformed_reference_and_positions_do_not_crash(self):
        cases = [('evidence:test-operations', 'source_id', []),
                 ('installation:test', 'unit_id', {}), ('installation:test', 'lot_id', []),
                 (self.bom['id'], 'positions', [[]]), (self.bom['id'], 'positions', None),
                 (self.bom['id'], 'positions', {'R1': True}),
                 (self.bom['id'], 'configuration_id', []), ('installation:test', 'position', [])]
        for nid, key, value in cases:
            with self.subTest(nid=nid, key=key, value=value):
                prior = self.index[nid][key]
                self.index[nid][key] = value
                self.assert_invalid()
                self.index[nid][key] = prior

    def test_unknown_factual_fields_are_rejected_on_each_supported_type(self):
        for node in self.nodes:
            for field in ('fault_cause', 'test_result', 'uptime'):
                with self.subTest(kind=node['type'], field=field):
                    node[field] = 'unapproved fabricated operational fact'
                    self.assert_invalid('UNKNOWN_FIELD')
                    del node[field]

    def test_nested_specifications_are_typed_and_cannot_hide_new_fact_fields(self):
        product = next(n for n in self.nodes if n['type'] == 'ProductModel')
        specs = product['specifications']
        specs['fault_cause'] = 'fabricated'
        self.assert_invalid('UNKNOWN_SPECIFICATION_FIELD')
        del specs['fault_cause']
        for key, invalid in [('evidence', [[]]), ('pcb_dimensions_mm', [True, 28]),
                             ('features', [{'fault_cause': 'fabricated'}]), ('scope', [])]:
            with self.subTest(key=key):
                old = specs[key]
                specs[key] = invalid
                self.assert_invalid()
                specs[key] = old

    def test_empty_positions_only_allowed_when_explicitly_not_confirmed_populated(self):
        bom = self.index[self.bom['id']]
        bom['positions'] = []
        self.assert_invalid('INVALID_POSITIONS')
        # Remove the installation dependent on this source position.
        self.nodes = [n for n in self.nodes if n['type'] != 'Installation']
        bom['population'] = 'unknown'
        self.assertTrue(validate_graph(self.nodes)['valid'])
        del bom['positions']
        self.assert_invalid('MISSING_FIELD')

    def test_source_hash_and_path_constraints_remain_enforced(self):
        source = self.index['source:test-operations']
        source['sha256'] = '0' * 64
        self.assert_invalid('SOURCE_INTEGRITY')
        source['path'] = '../outside-data.json'
        self.assert_invalid('SOURCE_INTEGRITY')
        source['path'] = []
        self.assert_invalid('SOURCE_INTEGRITY')

    def test_optional_git_blob_hash_is_checked(self):
        source = self.index['source:test-operations']
        data = self.source_path.read_bytes()
        source['git_blob_sha1'] = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        self.assertTrue(validate_graph(self.nodes)['valid'])
        source['git_blob_sha1'] = '0' * 40
        self.assert_invalid('SOURCE_INTEGRITY')

    def test_real_and_synthetic_provenance_cannot_be_interchanged(self):
        real_evidence = self.index[self.bom['id']]['evidence'][0]
        self.index['lot:test']['evidence'] = [real_evidence]
        self.assert_invalid('EVIDENCE_ORIGIN_MISMATCH')
        self.index['evidence:test-operations']['origin'] = 'real'
        self.assert_invalid('SOURCE_ORIGIN_MISMATCH')

    def test_semantic_operational_constraints_remain_enforced(self):
        installation = self.index['installation:test']
        installation['installed_at'] = '2026-08-20'
        self.assert_invalid('INVALID_TIME')
        installation['installed_at'] = '2026-08-20T09:00:00+09:00'
        installation['position'] = 'NOT_IN_BOM'
        self.assert_invalid('CONFIGURATION_MISMATCH')
        installation['conflict'] = 'false'
        self.assert_invalid('INVALID_CONFLICT_FLAG')
        self.index['coverage:test']['complete'] = 1
        self.assert_invalid('INVALID_COVERAGE')

    def test_piece_quantity_rejects_fraction_boolean_and_nonfinite_values(self):
        for invalid in (True, 1.5, float('nan'), float('inf'), [], {}):
            with self.subTest(invalid=invalid):
                self.index[self.bom['id']]['quantity'] = invalid
                self.assert_invalid('INVALID_QUANTITY')

    def test_shape_mutations_are_total_across_every_present_field(self):
        # Exercise every field/type boundary with JSON containers and null. This
        # catches unguarded index/set membership paths reached by agent extraction.
        representatives = {n['type']: n for n in self.nodes}
        for node in representatives.values():
            for key in list(node):
                if key == 'id':
                    continue
                old = node[key]
                for value in (None, [], [[]], {}, {'nested': []}):
                    with self.subTest(kind=node['type'], key=key, value=value):
                        node[key] = value
                        result = validate_graph(self.nodes)
                        self.assertIsInstance(result, dict)
                        self.assertIn('valid', result)
                node[key] = old

    def test_allowed_field_contract_is_explicit_for_all_node_types(self):
        self.assertEqual(set(SCHEMA), set(ALLOWED_FIELDS))
        self.assertEqual({'id', 'type', 'origin', 'evidence', 'unit_id', 'lot_id', 'position',
                          'installed_at', 'removed_at', 'confirmed_until', 'conflict'}, ALLOWED_FIELDS['Installation'])


if __name__ == '__main__':
    unittest.main()
