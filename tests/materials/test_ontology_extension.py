"""A business rules survive additive shared registration of reviewed types."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

from ontologics import domain_schema, model
from ontologics.materials.graph_validation import validate_graph
from ontologics.materials.graph_export import graph_jsonld
from ontologics.materials.export_tbox import build_tbox
from tests.order_fulfillment.model_oracle import main_model

ROOT = Path(__file__).resolve().parents[2]


def catalog():
    return json.loads((ROOT / 'data/catalog.json').read_text())['nodes']


def planning_nodes():
    """Independent, explicit synthetic facts with real local source bytes."""
    nodes = []
    for name, filename in [('contract', 'contract.txt'), ('stock', 'stock.json'),
                           ('lots', 'lot_reference_v1.json')]:
        path = 'data/production_planning/' + filename
        sha = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        nodes.append(dict(id=f'source:planning:{name}:v1', type='SourceDocument',
                          origin='synthetic', evidence=[], path=path, sha256=sha,
                          version=sha, url=f'urn:ontologics:planning:{name}:v1',
                          license='Project demonstration data',
                          collected_at='2026-10-27T17:10:21+09:00'))
    def evidence(eid, source, locator):
        nodes.append(dict(id=eid, type='Evidence', origin='synthetic', evidence=[],
                          source_id=f'source:planning:{source}:v1', locator=locator,
                          description='Synthetic development reference; not operational approval'))
    evidence('evidence:planning:contract:v1:parties', 'contract', 'lines 4-5')
    evidence('evidence:planning:contract:v1:item-001', 'contract', 'lines 3, 8-13')
    nodes.extend([
        dict(id='contract:planning:001', type='Contract', origin='synthetic',
             evidence=['evidence:planning:contract:v1:parties'],
             company='Planning Reference Company', customer_name='Reference Customer (가정)'),
        dict(id='contract-item:planning:001', type='ContractItem', origin='synthetic',
             evidence=['evidence:planning:contract:v1:item-001'],
             contract_id='contract:planning:001', configuration_id='config:esp32-poe:L:WROOM',
             quantity=100, unit='piece'),
    ])
    scope = json.loads((ROOT / 'data/production_planning/stock.json').read_text())
    lots = json.loads((ROOT / 'data/production_planning/lot_reference_v1.json').read_text())
    for i, (lot, stock) in enumerate(zip(lots['lots'], scope['stocks'])):
        le = f'evidence:planning:lots:v1:row-{i}'
        se = f'evidence:planning:stock:v1:row-{i}'
        evidence(le, 'lots', f'/lots/{i}')
        evidence(se, 'stock', f'/stocks/{i}, /stock_scope')
        nodes.append(dict(lot, type='ComponentLot', origin='synthetic', evidence=[le]))
        snapshot = {k: v for k, v in stock.items() if k not in ('component_id', 'bom_item_id')}
        snapshot.update(type='ComponentStockSnapshot', origin='synthetic', evidence=[se],
                        location=scope['stock_scope']['location'], as_of=scope['stock_scope']['as_of'],
                        quantity_state='known')
        nodes.append(snapshot)
    return nodes


def round_node(**updates):
    value = dict(id='round:planning:001', type='DeliveryRound', origin='synthetic',
                 evidence=['evidence:planning:contract:v1:item-001'],
                 contract_item_id='contract-item:planning:001', quantity=100, unit='piece',
                 due_at='2026-11-10T12:30:00+09:00', due_basis='arrival',
                 destination='Explicit synthetic test destination; not current contract')
    value.update(updates)
    return value


class OntologyExtensionTests(unittest.TestCase):
    def setUp(self):
        self.nodes = catalog() + planning_nodes()
        self.index = {n['id']: n for n in self.nodes}
        self.item = self.index['contract-item:planning:001']
        self.stock = self.index['stock:planning:001']

    def report(self):
        return validate_graph(self.nodes)

    def reject(self, code):
        result = self.report()
        self.assertFalse(result['valid'], result)
        self.assertIn(code, [e['code'] for e in result['errors']], result)

    def test_contract_and_stock_partial_structure_is_valid_without_invented_round(self):
        self.assertTrue(self.report()['valid'], self.report())

    def test_b_multiple_rounds_remain_valid_but_total_cannot_exceed_order(self):
        self.nodes += [round_node(quantity=40), round_node(id='round:second', quantity=60)]
        self.assertTrue(self.report()['valid'], self.report())
        self.nodes[-1]['quantity'] = 61
        self.reject('ROUND_QUANTITY_EXCEEDED')

    def test_order_and_round_quantities_reject_bool_float_null_and_nonpositive(self):
        for target in ('item', 'round'):
            for bad in (True, False, 100.0, 0, -1, None, '100', [], {}):
                with self.subTest(target=target, bad=bad):
                    self.setUp()
                    n = self.item if target == 'item' else round_node()
                    if target == 'round': self.nodes.append(n)
                    n['quantity'] = bad
                    self.reject('INVALID_QUANTITY')
        self.setUp()
        self.item['quantity'] = 10 ** 400
        self.assertTrue(self.report()['valid'])

    def test_graph_aliases_and_request_only_fields_are_rejected(self):
        for nid, field in [('contract:planning:001', 'customer'), ('contract:planning:001', 'contract_number'),
                           (self.item['id'], 'ordered_quantity'), (self.item['id'], 'delivery_due'),
                           (self.item['id'], 'delivery_due_precision')]:
            self.index[nid][field] = 'invented'
            self.reject('UNKNOWN_FIELD')
            del self.index[nid][field]

    def test_required_text_fields_and_references_remain_strict(self):
        for nid, key in [('contract:planning:001', 'company'), ('contract:planning:001', 'customer_name'),
                         (self.item['id'], 'contract_id'), (self.item['id'], 'configuration_id'),
                         (self.stock['id'], 'lot_id'), (self.stock['id'], 'quantity_state')]:
            for bad in (None, '', True, [], {}, 1):
                with self.subTest(nid=nid, key=key, bad=bad):
                    original = self.index[nid][key]
                    self.index[nid][key] = bad
                    self.assertFalse(self.report()['valid'])
                    self.index[nid][key] = original
        self.item['contract_id'] = self.item['configuration_id']
        self.reject('INVALID_REFERENCE')

    def test_round_time_basis_destination_and_piece_are_required(self):
        self.nodes.append(round_node())
        for key, bad, code in [('due_at', '2026-11-10', 'INVALID_TIME'),
                               ('due_at', '2026-11-10T12:30:00', 'INVALID_TIME'),
                               ('due_at', None, 'INVALID_TIME'),
                               ('destination', '', 'MISSING_FIELD'),
                               ('due_basis', 'departure', 'UNSUPPORTED_DUE_BASIS'),
                               ('unit', 'box', 'UNSUPPORTED_UNIT')]:
            with self.subTest(key=key, bad=bad):
                original = self.nodes[-1][key]; self.nodes[-1][key] = bad
                self.reject(code); self.nodes[-1][key] = original

    def test_snapshot_unknown_null_and_required_quantities_are_distinct(self):
        self.stock['on_hand_quantity'] = None
        self.reject('INVALID_QUANTITY')
        self.stock['quantity_state'] = 'unknown'
        result = self.report()
        self.assertTrue(result['valid'], result)
        self.assertIn('UNKNOWN_QUANTITY', [w['code'] for w in result['warnings']])
        del self.stock['on_hand_quantity']
        self.reject('MISSING_FIELD')

    def test_stock_quantities_disjoint_and_time_precision(self):
        for field in ('on_hand_quantity', 'reserved_quantity', 'held_quantity', 'available_quantity'):
            for bad in (True, False, -1, 1.0, '1', [], {}):
                self.setUp(); self.stock[field] = bad; self.reject('INVALID_QUANTITY')
        for bad in (0, 1, 'true', [], {}):
            self.setUp(); self.stock['deductions_disjoint'] = bad; self.reject('INVALID_DEDUCTION_FLAG')
        for value in (None, False):
            self.setUp(); self.stock['deductions_disjoint'] = value
            result = self.report(); self.assertTrue(result['valid'], result)
            self.assertIn('DEDUCTION_SCOPE_UNCONFIRMED', [w['code'] for w in result['warnings']])
        for bad in ('2026-10-26', '2026-10-26T09:00+09:00', '2026-10-26T09:00:00.1234567+09:00'):
            self.setUp(); self.stock['as_of'] = bad; self.reject('INVALID_TIME')

    def test_reported_available_and_unknown_state_do_not_calculate_inventory(self):
        for k in ('on_hand_quantity', 'reserved_quantity', 'held_quantity'):
            del self.stock[k]
        self.stock.update(availability_basis='reported_available', available_quantity=70)
        self.assertTrue(self.report()['valid'], self.report())
        self.assertEqual(70, self.stock['available_quantity'])
        self.stock['quantity_state'] = 'unknown'
        self.assertIn('UNKNOWN_QUANTITY', [w['code'] for w in self.report()['warnings']])
        del self.stock['available_quantity']; self.reject('MISSING_FIELD')

    def test_snapshot_identity_compares_instants_and_not_offset_text(self):
        duplicate = dict(self.stock, id='stock:duplicate', as_of='2026-10-26T00:00:00Z')
        self.nodes.append(duplicate); self.reject('DUPLICATE_IDENTITY')
        duplicate['as_of'] = '2026-10-26T00:00:01Z'
        self.assertTrue(self.report()['valid'])

    def test_new_facts_and_evidence_retain_synthetic_origin_and_source_integrity(self):
        self.nodes.append(round_node())
        for n in self.nodes:
            if n['type'] in ('Contract', 'ContractItem', 'DeliveryRound', 'ComponentStockSnapshot'):
                n['origin'] = 'real'; self.reject('DEMO_OPERATIONS_ONLY'); n['origin'] = 'synthetic'
        for ev, code in [([], 'EVIDENCE_REQUIRED'), (['missing'], 'EVIDENCE_NOT_FOUND'),
                         (['source:planning:contract:v1'], 'EVIDENCE_NOT_FOUND'),
                         (self.item['evidence'] * 2, 'INVALID_EVIDENCE')]:
            original = self.item['evidence']; self.item['evidence'] = ev
            self.reject(code); self.item['evidence'] = original
        source = self.index['source:planning:stock:v1']; source['sha256'] = '0' * 64
        self.reject('SOURCE_INTEGRITY')

    def test_serialization_and_generated_artifacts_preserve_existing_facts(self):
        graph = graph_jsonld(self.nodes)
        row = next(n for n in graph['@graph'] if n['id'].endswith(self.item['id']))
        self.assertEqual('urn:ontologics:entity:contract:planning:001', row['contract_id'])
        self.assertIn('o:ComponentStockSnapshot a owl:Class', build_tbox())
        old = json.loads(subprocess.check_output(['git', 'show', 'HEAD:data/initial_graph.jsonld'], cwd=ROOT))
        new = json.loads((ROOT / 'data/initial_graph.jsonld').read_text())
        self.assertEqual(old['@graph'], new['@graph'])
        self.assertEqual(474, len(new['@graph']))
        self.assertEqual({'@type': '@id'}, new['@context']['contract_id'])
        self.assertEqual({'@type': '@id'}, new['@context']['contract_item_id'])
        self.assertEqual(build_tbox(), (ROOT / 'ontology/tbox.ttl').read_text())
        self.assertTrue(model.validate_graph(catalog())['valid'])
        round_graph = graph_jsonld(self.nodes + [round_node()])['@graph'][-1]
        self.assertEqual('urn:ontologics:entity:contract-item:planning:001', round_graph['contract_item_id'])

    def test_existing_schema_and_history_allowlist_are_preserved(self):
        old = main_model()
        for k, v in old.SCHEMA.items(): self.assertEqual(v, model.SCHEMA[k])
        original_types = old.SYNTHETIC
        self.assertLessEqual(original_types, model.SYNTHETIC)
        self.assertEqual(model.SYNTHETIC - original_types, {
            'Contract', 'ContractItem', 'DeliveryRound', 'ComponentStockSnapshot',
            'FulfillmentSnapshot', 'FinishedGoodsStock', 'ProductionWork',
            'ProductionObservation', 'Allocation', 'Shipment', 'DeliveryCondition'} | set(domain_schema.C_SCHEMA))
