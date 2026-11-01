"""Pure B graph selection regressions; no DB example, approval or file export.

Definitions and expected output come from pinned historical Git objects. These
selection/output tests do not establish storage or whole-application compatibility.
"""
import ast
from collections import deque
import copy
from pathlib import Path
import unittest

from examples.order_fulfillment import b_contract, b_delivery
from ontologics import model
from tests.order_fulfillment.model_oracle import legacy_model

from ontologics.ingest import build_catalog
from ontologics.order_fulfillment import schema, schema_export
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment
from ontologics.order_fulfillment.production import load_demo_production

legacy = legacy_model()


def legacy_selected_nodes(base, candidates):
    """Independent breadth-first oracle using the pre-separation definitions."""
    index = {node['id']: node for node in base + candidates}
    pending = deque(node['id'] for node in candidates)
    selected = set()
    while pending:
        nid = pending.popleft()
        if nid in selected:
            continue
        selected.add(nid)
        node = index[nid]
        pending.extend(node[key] for key in legacy.SCHEMA[node['type']][1] if key in node)
        for field in legacy.LIST_REFERENCES.get(node['type'], {}):
            pending.extend(node[field])
        pending.extend(node['evidence'])
    return [node for nid, node in index.items() if nid in selected]


class RepresentativeGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_catalog()['nodes']
        cls.contract = load_demo_contract()

    def test_remaining_consumers_take_b_metadata_and_b_output_adapters(self):
        for module in (b_contract, b_delivery):
            with self.subTest(module=module.__name__):
                tree = ast.parse(Path(module.__file__).read_text())
                b_imports = {alias.asname or alias.name: alias.name
                             for node in tree.body if isinstance(node, ast.ImportFrom)
                             and node.module == 'ontologics.order_fulfillment.schema'
                             for alias in node.names}
                self.assertEqual('LOOKUP_SCHEMA', b_imports.get('SCHEMA'))
                self.assertEqual(schema.LOOKUP_SCHEMA, module.SCHEMA)
                if module is not b_contract:
                    self.assertEqual('LIST_REFERENCES', b_imports.get('LIST_REFERENCES'))
                    self.assertEqual(schema.LIST_REFERENCES, module.LIST_REFERENCES)
                output_imports = {alias.name for node in tree.body if isinstance(node, ast.ImportFrom)
                                  and node.module == 'ontologics.order_fulfillment.schema_export'
                                  for alias in node.names}
                self.assertIn('graph_jsonld', output_imports)
                # Extraction tests reload schema_export in this interpreter;
                # existing consumers legitimately retain the earlier function.
                self.assertEqual(schema_export.__name__, module.graph_jsonld.__module__)
                self.assertEqual('graph_jsonld', module.graph_jsonld.__name__)
        self.assertEqual(model.ROOT, b_contract.ROOT)
        self.assertEqual(schema_export.__name__, b_contract.tbox_turtle.__module__)
        self.assertEqual('tbox_turtle', b_contract.tbox_turtle.__name__)

    def test_fixed_scenarios_preserve_nodes_order_content_provenance_and_inputs(self):
        base = self.catalog + self.contract
        cases = [('cq01', self.catalog, self.contract),
                 ('cq02', base, load_demo_fulfillment()),
                 ('cq03', base, load_demo_production()),
                 ('cq04_normal', base, load_demo_delivery()),
                 ('cq04_mixed', base, load_demo_delivery(mixed=True))]
        for name, prior, candidates in cases:
            with self.subTest(case=name):
                before = copy.deepcopy((prior, candidates, model.SCHEMA, getattr(model, 'LIST_REFERENCES', {}), legacy.SCHEMA, legacy.LIST_REFERENCES))
                selected = legacy_selected_nodes(prior, candidates)
                graph = b_delivery.representative_graph(prior, candidates)
                self.assertEqual(legacy.graph_jsonld(selected), graph)
                ids = [node['id'] for node in selected]
                self.assertEqual(['urn:ontologics:entity:' + nid for nid in ids],
                                 [node['id'] for node in graph['@graph']])
                self.assertEqual(len(ids), len(set(ids)))
                self.assertLess(len(selected), len(prior + candidates))
                for node in selected:
                    if node['type'] == 'Evidence':
                        self.assertIn(node['source_id'], ids)
                    self.assertTrue(set(node['evidence']) <= set(ids))
                self.assertEqual(before, (prior, candidates, model.SCHEMA, getattr(model, 'LIST_REFERENCES', {}), legacy.SCHEMA, legacy.LIST_REFERENCES))

    def test_absent_optional_sources_and_round_lists_keep_exact_dependency_selection(self):
        candidates = load_demo_fulfillment()
        base = self.catalog + self.contract + candidates
        allocations = [node for node in candidates if node['type'] == 'Allocation']
        self.assertTrue(any('stock_id' not in node for node in allocations))
        self.assertTrue(any('production_work_id' not in node for node in allocations))
        for allocation in allocations:
            with self.subTest(allocation=allocation['id']):
                selected = legacy_selected_nodes(base, [allocation])
                self.assertEqual(legacy.graph_jsonld(selected), b_delivery.representative_graph(base, [allocation]))
        snapshot = next(node for node in candidates if node['type'] == 'FulfillmentSnapshot')
        graph = b_delivery.representative_graph(base, [snapshot])
        ids = {node['id'] for node in graph['@graph']}
        self.assertTrue({'urn:ontologics:entity:' + nid for nid in snapshot['round_ids']} <= ids)
        self.assertTrue({'urn:ontologics:entity:' + n['id'] for n in allocations}.isdisjoint(ids))

    def test_shared_and_cyclic_dependencies_terminate_without_duplicate_nodes(self):
        # Selection-only adversarial fixture, not a graph accepted for storage.
        source = dict(id='source:test', type='SourceDocument', evidence=['e:test'])
        evidence = dict(id='e:test', type='Evidence', source_id='source:test', evidence=[])
        unrelated = dict(id='unused', type='Contract', evidence=[])
        base, candidates = [source, unrelated], [evidence, copy.deepcopy(evidence)]
        before = copy.deepcopy((base, candidates))
        graph = b_delivery.representative_graph(base, candidates)
        self.assertEqual(['urn:ontologics:entity:source:test', 'urn:ontologics:entity:e:test'],
                         [node['id'] for node in graph['@graph']])
        self.assertEqual(before, (base, candidates))

    def test_missing_direct_list_evidence_targets_and_unknown_types_keep_errors(self):
        cases = [dict(id='item', type='ContractItem', contract_id='missing', evidence=[]),
                 dict(id='snapshot', type='FulfillmentSnapshot', round_ids=['missing'], evidence=[]),
                 dict(id='contract', type='Contract', evidence=['missing']),
                 dict(id='unknown', type='UnknownType', evidence=[])]
        for node in cases:
            with self.subTest(kind=node['type']):
                before = copy.deepcopy(node)
                with self.assertRaises(KeyError):
                    b_delivery.representative_graph([], [node])
                self.assertEqual(before, node)
