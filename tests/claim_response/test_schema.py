"""C 1st phase schema extension tests. Existing types, A/B types and their tests are not modified."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ontologics import domain_schema
from ontologics.model import ALLOWED_FIELDS, C_TYPES, REAL, ROOT, SCHEMA, graph_jsonld, tbox_turtle, validate_graph


class CPhase1ValidationTests(unittest.TestCase):
    """C 1st phase: claim intake, quality case, production-line trace and containment requests."""
    CONFIG = 'config:esp32-poe:L:WROOM'

    @classmethod
    def setUpClass(cls):
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        index = {n['id']: n for n in catalog}
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

        visit(cls.CONFIG)
        cls.real_nodes = [copy.deepcopy(n) for n in catalog if n['id'] in dependencies]
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.json', prefix='test-c-claim-', dir=ROOT / 'data', delete=False) as stream:
            stream.write(b'{"scope":"synthetic C phase 1 fixture"}\n')
            cls.source_path = Path(stream.name)
        cls.addClassCleanup(cls.source_path.unlink)
        ev = ['evidence:test-c']
        symptom = '전원 인가 후 상태 표시 없이 부팅되지 않음'

        def unit(serial):
            return {'id': 'unit:test-' + serial, 'type': 'ProductUnit', 'origin': 'synthetic', 'evidence': ev,
                    'serial': serial, 'company': 'Demo Assembly Company', 'configuration_id': cls.CONFIG}

        cls.c_nodes = [
            {'id': 'source:test-c', 'type': 'SourceDocument', 'origin': 'synthetic', 'evidence': [],
             'url': 'urn:demo:c-claim-fixture', 'path': str(cls.source_path.relative_to(ROOT)),
             'sha256': hashlib.sha256(cls.source_path.read_bytes()).hexdigest(), 'version': 'test-1',
             'license': 'Synthetic test fixture', 'collected_at': '2026-10-26T10:00:00+09:00', 'kind': 'synthetic_claim'},
            {'id': 'evidence:test-c', 'type': 'Evidence', 'origin': 'synthetic', 'evidence': [],
             'source_id': 'source:test-c', 'locator': '/scope', 'description': 'Synthetic C claim records'},
            unit('C-D001'), unit('C-S001'),
            {'id': 'production-line:test-C-01', 'type': 'ProductionLine', 'origin': 'synthetic', 'evidence': ev,
             'company': 'Demo Assembly Company', 'line_code': 'C-01', 'name': 'Demo line C-01'},
            {'id': 'production:test-C-D001', 'type': 'UnitProductionRecord', 'origin': 'synthetic', 'evidence': ev,
             'unit_id': 'unit:test-C-D001', 'production_line_id': 'production-line:test-C-01',
             'produced_at': '2026-09-14T09:00:00+09:00'},
            {'id': 'production:test-C-S001', 'type': 'UnitProductionRecord', 'origin': 'synthetic', 'evidence': ev,
             'unit_id': 'unit:test-C-S001', 'production_line_id': 'production-line:test-C-01',
             'produced_at': '2026-10-05T09:00:00+09:00'},
            {'id': 'claim-intake:test', 'type': 'ClaimIntake', 'origin': 'synthetic', 'evidence': ev,
             'external_claim_id': 'CLM-DEMO-C-20261026-001', 'customer_name': '가정 고객사 C-01',
             'reported_at': '2026-10-26T10:00:00+09:00', 'reported_symptom': symptom,
             'requested_action': '원인 확인 및 교환 가능 여부 회신 요청', 'delivery_reference': 'DO-DEMO-C-001',
             'claimed_quantity': 1, 'reported_serials': ['C-D001'], 'intake_state': 'confirmed'},
            {'id': 'quality-case:test', 'type': 'QualityCase', 'origin': 'synthetic', 'evidence': ev,
             'intake_id': 'claim-intake:test', 'customer_name': '가정 고객사 C-01',
             'reported_at': '2026-10-26T10:00:00+09:00', 'reported_symptom': symptom, 'unit_ids': ['unit:test-C-D001']},
            {'id': 'quality-scope:test-hold', 'type': 'QualityScope', 'origin': 'synthetic', 'evidence': ev,
             'case_id': 'quality-case:test', 'production_line_id': 'production-line:test-C-01',
             'as_of': '2026-10-26T10:15:00+09:00', 'unit_ids': ['unit:test-C-S001'], 'scope_state': 'hold'},
            {'id': 'containment:test-production-stop', 'type': 'ContainmentRequest', 'origin': 'synthetic', 'evidence': ev,
             'case_id': 'quality-case:test', 'scope_id': 'quality-scope:test-hold', 'request_kind': 'production_stop',
             'prepared_at': '2026-10-26T10:15:00+09:00', 'document_ref': '05_생산중단_요청서_검토초안.md',
             'production_line_id': 'production-line:test-C-01', 'draft_id': 'draft:test', 'draft_version': 1},
            {'id': 'containment:test-shipment-hold', 'type': 'ContainmentRequest', 'origin': 'synthetic', 'evidence': ev,
             'case_id': 'quality-case:test', 'scope_id': 'quality-scope:test-hold', 'request_kind': 'shipment_hold',
             'prepared_at': '2026-10-26T10:15:00+09:00', 'document_ref': '06_출고배송중단_요청서_검토초안.md'},
        ]

    def setUp(self):
        self.nodes = copy.deepcopy(self.real_nodes + self.c_nodes)
        self.index = {n['id']: n for n in self.nodes}

    def assert_invalid(self, code):
        result = validate_graph(self.nodes)
        self.assertFalse(result['valid'], result)
        self.assertIn(code, {e['code'] for e in result['errors']}, result['errors'])
        return result

    def copy_node(self, nid, new_id, **changes):
        node = dict(copy.deepcopy(self.index[nid]), id=new_id, **changes)
        self.nodes.append(node)
        self.index[new_id] = node
        return node

    def test_c_phase1_nodes_validate(self):
        result = validate_graph(self.nodes)
        self.assertTrue(result['valid'], result['errors'])

    def test_existing_types_are_unchanged_and_c_types_are_additions(self):
        self.assertEqual({'SourceDocument', 'Evidence', 'ProductModel', 'ProductRevision', 'Configuration',
                          'ComponentModel', 'BOMItem', 'ProductUnit', 'ComponentLot', 'Installation',
                          'HistoryCoverage', 'InventoryMovement'}, set(SCHEMA) - set(C_TYPES) - set(domain_schema.SCHEMA))
        # C definitions remain separate from the A/B set and its B request addition.
        self.assertEqual(12, len(domain_schema.SCHEMA))
        self.assertFalse(set(C_TYPES) & (domain_schema.A_TYPES | domain_schema.B_TYPES))
        self.assertEqual({'ProductModel', 'ProductRevision', 'Configuration', 'ComponentModel', 'BOMItem'}, REAL)
        self.assertEqual({'id', 'type', 'origin', 'evidence', 'serial', 'company', 'configuration_id'}, ALLOWED_FIELDS['ProductUnit'])

    def test_c_records_are_synthetic_only(self):
        self.index['claim-intake:test']['origin'] = 'real'
        self.assert_invalid('DEMO_OPERATIONS_ONLY')

    def test_unapproved_fact_fields_are_rejected_on_c_types(self):
        for nid in [n['id'] for n in self.c_nodes if n['type'] in C_TYPES]:
            for field in ('fault_cause', 'root_cause', 'test_result', 'disposition'):
                with self.subTest(nid=nid, field=field):
                    self.index[nid][field] = 'unapproved fabricated quality fact'
                    self.assert_invalid('UNKNOWN_FIELD')
                    del self.index[nid][field]

    def test_list_references_need_existing_unique_units(self):
        case = self.index['quality-case:test']
        for invalid in ([], 'unit:test-C-D001', ['unit:missing'], ['production-line:test-C-01'],
                        ['unit:test-C-D001', 'unit:test-C-D001'], [None], [[]]):
            with self.subTest(invalid=invalid):
                case['unit_ids'] = invalid
                self.assert_invalid('INVALID_REFERENCE')

    def test_optional_references_are_checked_only_when_present(self):
        record = self.index['production:test-C-D001']
        record['production_work_id'] = 'work:any'
        # Unsupported link: any value is rejected until the B production-work contract (I02) is agreed.
        result = self.assert_invalid('PRODUCTION_WORK_UNSUPPORTED')
        self.assertIn('INVALID_REFERENCE', {e['code'] for e in result['errors']})
        del record['production_work_id']
        self.index['containment:test-shipment-hold']['production_line_id'] = 'unit:test-C-D001'
        self.assert_invalid('INVALID_REFERENCE')

    def test_production_work_link_is_rejected_even_for_an_existing_work(self):
        # The shared ProductionWork type exists now (B), but C has no agreed work link yet.
        self.nodes.append({'id': 'work:test', 'type': 'ProductionWork', 'origin': 'synthetic', 'evidence': ['evidence:test-c']})
        self.index['production:test-C-D001']['production_work_id'] = 'work:test'
        codes = {(e['node_id'], e['code']) for e in validate_graph(self.nodes)['errors']}
        self.assertIn(('production:test-C-D001', 'PRODUCTION_WORK_UNSUPPORTED'), codes)
        self.assertNotIn(('production:test-C-D001', 'INVALID_REFERENCE'), codes)

    def test_value_lists_are_enforced(self):
        for nid, key in (('claim-intake:test', 'intake_state'), ('quality-scope:test-hold', 'scope_state'),
                         ('containment:test-shipment-hold', 'request_kind')):
            with self.subTest(key=key):
                old = self.index[nid][key]
                self.index[nid][key] = 'done'
                self.assert_invalid('INVALID_ENUM')
                self.index[nid][key] = old

    def test_times_need_offsets_and_scope_interval_is_ordered(self):
        self.index['claim-intake:test']['reported_at'] = '2026-10-26'
        self.assert_invalid('INVALID_TIME')
        self.index['claim-intake:test']['reported_at'] = '2026-10-26T10:00:00+09:00'
        scope = self.index['quality-scope:test-hold']
        scope['start'], scope['end'] = '2026-10-01T00:00:00+09:00', '2026-09-01T00:00:00+09:00'
        self.assert_invalid('INVALID_INTERVAL')
        scope['end'] = '2026-10-27T00:00:00+09:00'
        self.assertTrue(validate_graph(self.nodes)['valid'])

    def test_claim_counts_and_serial_lists_are_typed(self):
        intake = self.index['claim-intake:test']
        for invalid in (0, True, 1.5, '1', None):
            with self.subTest(invalid=invalid):
                intake['claimed_quantity'] = invalid
                self.assert_invalid('INVALID_QUANTITY')
        intake['claimed_quantity'] = 1
        intake['reported_serials'] = ['C-D001', 'C-D001']
        self.assert_invalid('INVALID_FIELD_TYPE')
        intake['reported_serials'] = ['C-D001']
        self.index['containment:test-production-stop']['draft_version'] = 0
        self.assert_invalid('INVALID_FIELD_TYPE')

    def test_claim_quantity_serial_mismatch_is_a_warning_for_people(self):
        self.index['claim-intake:test']['claimed_quantity'] = 2
        result = validate_graph(self.nodes)
        self.assertTrue(result['valid'], result['errors'])
        self.assertIn('CLAIM_QUANTITY_SERIAL_MISMATCH', {w['code'] for w in result['warnings']})

    def test_duplicate_lines_claims_and_production_records_are_rejected(self):
        for nid, new_id in (('production-line:test-C-01', 'production-line:test-dup'),
                            ('claim-intake:test', 'claim-intake:test-dup'),
                            ('production:test-C-D001', 'production:test-dup')):
            with self.subTest(nid=nid):
                self.setUp()
                self.copy_node(nid, new_id)
                self.assert_invalid('DUPLICATE_IDENTITY')

    def test_case_needs_confirmed_intake(self):
        self.index['claim-intake:test']['intake_state'] = 'needs_review'
        self.assert_invalid('INTAKE_NOT_CONFIRMED')

    def test_production_stop_names_line_and_request_matches_scope_case(self):
        del self.index['containment:test-production-stop']['production_line_id']
        self.assert_invalid('PRODUCTION_LINE_REQUIRED')
        self.setUp()
        self.copy_node('quality-case:test', 'quality-case:test-other')
        self.index['containment:test-shipment-hold']['case_id'] = 'quality-case:test-other'
        self.assert_invalid('CASE_MISMATCH')

    def test_request_line_must_match_the_scope_line(self):
        # Same line as the scope: valid (fixture). Another existing line: rejected, never auto-allowed.
        self.assertTrue(validate_graph(self.nodes)['valid'])
        self.copy_node('production-line:test-C-01', 'production-line:test-C-02', line_code='C-02', name='Demo line C-02')
        for nid in ('containment:test-production-stop', 'containment:test-shipment-hold'):
            with self.subTest(nid=nid):
                self.index[nid]['production_line_id'] = 'production-line:test-C-02'
                result = self.assert_invalid('LINE_MISMATCH')
                self.assertIn(nid, {e['node_id'] for e in result['errors'] if e['code'] == 'LINE_MISMATCH'})
                self.index[nid]['production_line_id'] = 'production-line:test-C-01'
                self.assertTrue(validate_graph(self.nodes)['valid'])

    def test_shape_mutations_on_c_types_never_crash(self):
        for nid in [n['id'] for n in self.c_nodes if n['type'] in C_TYPES]:
            node = self.index[nid]
            for key in list(node):
                if key == 'id':
                    continue
                old = node[key]
                for value in (None, [], [[]], {}, {'nested': []}, 0, True):
                    with self.subTest(nid=nid, key=key, value=value):
                        node[key] = value
                        result = validate_graph(self.nodes)
                        self.assertIsInstance(result, dict)
                        self.assertIn('valid', result)
                node[key] = old

    def test_jsonld_links_list_and_optional_references(self):
        graph = graph_jsonld(self.nodes)
        rows = {row['id']: row for row in graph['@graph']}
        self.assertEqual({'@type': '@id'}, graph['@context']['unit_ids'])
        self.assertEqual(['urn:ontologics:entity:unit:test-C-D001'], rows['urn:ontologics:entity:quality-case:test']['unit_ids'])
        stop = rows['urn:ontologics:entity:containment:test-production-stop']
        self.assertEqual('urn:ontologics:entity:production-line:test-C-01', stop['production_line_id'])
        self.assertNotIn('production_line_id', rows['urn:ontologics:entity:containment:test-shipment-hold'])

    def test_tbox_adds_c_classes_and_properties_with_shared_ranges(self):
        tbox = tbox_turtle()
        for kind in C_TYPES:
            self.assertIn(f'o:{kind} a owl:Class .', tbox)
        self.assertIn('o:unit_ids a owl:ObjectProperty ; rdfs:range o:ProductUnit .', tbox)
        self.assertIn('o:production_line_id a owl:ObjectProperty ; rdfs:range o:ProductionLine .', tbox)
        # production_work_id keeps the single range already declared by B (ProductionWork).
        self.assertEqual(1, tbox.count('o:production_work_id a owl:ObjectProperty ; rdfs:range o:ProductionWork .'))


if __name__ == '__main__':
    unittest.main()
