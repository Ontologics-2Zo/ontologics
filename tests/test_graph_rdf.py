"""Real SHACL/SPARQL checks; no database, network, or model API is used."""
import copy
import json
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, RDF

from ontologics.demo import CONFIG_L, CONFIG_L1, example_queries, fixture_nodes
from ontologics.graph_rdf import O, P, RdfSnapshot, entity_uri, validate_rdf
from ontologics.model import ROOT, SCHEMA, SCHEMA_VERSION
from ontologics.service import DomainError, RULE_VERSION, configuration_changes
from ontologics.temporal import investigate


def snapshot(nodes, version=2):
    return RdfSnapshot(nodes, version=version, schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)


def closure(nodes, selected):
    """A small source-backed fixture for structural mutations, no invented facts."""
    index = {n['id']: n for n in nodes}
    wanted, pending = set(), list(selected)
    while pending:
        nid = pending.pop()
        if nid in wanted:
            continue
        wanted.add(nid)
        node = index[nid]
        pending.extend(node['evidence'])
        pending.extend(node[field] for field in SCHEMA[node['type']][1])
    return [n for n in nodes if n['id'] in wanted]


class RdfBudgetBoundaryTests(unittest.TestCase):
    def test_completed_shacl_cannot_dispatch_sparql_after_the_shared_deadline(self):
        from ontologics.graph_views import GraphSettings, GraphViews
        from ontologics.harness import BudgetLimits, ExecutionBudget, request_budget

        clock = [0.0]
        budget = ExecutionBudget(BudgetLimits(deadline_seconds=1), clock=lambda: clock[0])
        views = GraphViews(object(), GraphSettings(backend='rdf'))

        def finished_after_deadline(*args, **kwargs):
            # Deterministic stand-in for one synchronous SHACL invocation which
            # completed after its request's time limit. It is not interrupted.
            clock[0] = 2.0
            return True, Graph(), ''

        with request_budget('reader', budget):
            with patch('ontologics.graph_rdf.validate', side_effect=finished_after_deadline) as shacl:
                with patch.object(Graph, 'query') as query:
                    with self.assertRaises(DomainError) as error:
                        views.rdf(2, []).nodes()
        self.assertEqual('DEADLINE_EXCEEDED', error.exception.code)
        shacl.assert_called_once()
        query.assert_not_called()
        self.assertEqual({}, views._rdf_cache)

        # A previously validated projection also cannot start a query under an
        # expired current request. No new budget is created for cached reads.
        with patch('ontologics.graph_rdf.validate', return_value=(True, Graph(), '')):
            cached = snapshot([])
        with request_budget('reader', budget), patch.object(Graph, 'query') as query:
            with self.assertRaises(DomainError) as error:
                cached.lookup()
        self.assertEqual('DEADLINE_EXCEEDED', error.exception.code)
        query.assert_not_called()


class RdfProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        operational, cls.supplement = fixture_nodes()
        cls.nodes = catalog + operational
        cls.before = snapshot(cls.nodes)
        cls.left_item = next(n for n in catalog if n['type'] == 'BOMItem' and n['configuration_id'] == CONFIG_L)
        cls.right_item = next(n for n in catalog if n['type'] == 'BOMItem' and n['configuration_id'] == CONFIG_L1)
        cls.small_nodes = closure(cls.nodes, [cls.left_item['id'], cls.right_item['id'], 'unit:U106'])
        cls.small = snapshot(cls.small_nodes)

    def test_real_sparql_roundtrip_keeps_order_nested_specs_origin_and_unknowns(self):
        self.assertEqual(self.nodes, self.before.nodes())
        model = next(n for n in self.nodes if n['type'] == 'ProductModel')
        with patch.object(self.before._graph, 'query', wraps=self.before._graph.query) as query:
            actual = self.before.lookup(node_id=model['id'])
        self.assertEqual([model], actual)
        self.assertEqual(model['specifications'], actual[0]['specifications'])
        self.assertTrue(query.called)
        self.assertEqual([n for n in self.nodes if n['type'] == 'BOMItem'], self.before.lookup(kind='BOMItem'))
        unknown = [n for n in self.before.lookup(kind='BOMItem') if n['population'] == 'unknown']
        self.assertTrue(unknown)
        self.assertTrue(all(n['origin'] == 'real' for n in unknown))
        self.assertEqual([], self.before.lookup(kind='UnadoptedDomain'))

    def test_configuration_and_usage_queries_follow_requested_relations_only(self):
        for config_id in (CONFIG_L, CONFIG_L1):
            with self.subTest(configuration=config_id):
                actual = self.before.configuration(config_id)
                expected = [n for n in self.nodes if n['type'] == 'BOMItem' and n['configuration_id'] == config_id]
                self.assertEqual(expected, actual['items'])
                self.assertEqual(74, len(actual['items']))
                self.assertEqual(128, sum(len(n['positions']) for n in actual['items']))
                self.assertEqual(config_id, actual['configuration']['id'])
                self.assertEqual(actual['configuration']['revision_id'], actual['revision']['id'])
                self.assertEqual(actual['revision']['model_id'], actual['product']['id'])
                component = next(n for n in actual['components'] if n['name'].startswith('LAN8710A-EZC'))
                usage = self.before.component_usage(component['id'])
                self.assertEqual([n for n in expected if n['component_id'] == component['id']], usage['items'])
                self.assertEqual(['U4'], usage['items'][0]['positions'])
                self.assertEqual(1, len(usage['items']))
        self.assertEqual(['R50', 'R51', 'R6', 'U2'],
                         [c['position'] for c in configuration_changes(self.before.nodes(), CONFIG_L, CONFIG_L1)])
        with self.assertRaisesRegex(ValueError, 'Unknown configuration'):
            self.before.configuration('config:missing')
        with self.assertRaisesRegex(ValueError, 'Unknown source-scoped component'):
            self.before.component_usage('component:missing')

    def test_id_bindings_do_not_become_sparql_or_external_requests(self):
        malicious_id = 'missing\" } UNION { ?s ?p ?o } # SERVICE <https://invalid.example/>'
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network expected')):
            self.assertEqual([], self.before.lookup(node_id=malicious_id))
            with self.assertRaisesRegex(ValueError, 'Unknown configuration'):
                self.before.configuration(malicious_id)
        # Nontrivial IDs are escaped only in their URI spelling, not in domain data.
        nodes = copy.deepcopy(self.small_nodes)
        model = next(n for n in nodes if n['type'] == 'ProductModel')
        previous_id = model['id']
        model['id'] += ' # " unicode 한글'
        for node in nodes:
            if node.get('model_id') == previous_id:
                node['model_id'] = model['id']
        exported = snapshot(nodes)
        self.assertEqual([model], exported.lookup(node_id=model['id']))

    def test_shacl_rejects_missing_references_origin_conflicts_and_extra_fields(self):
        reference = entity_uri(self.left_item['component_id'])
        evidence_id = self.left_item['evidence'][0]
        evidence = entity_uri(evidence_id)
        unit = entity_uri('unit:U106')
        item = entity_uri(self.left_item['id'])
        mutations = {
            'missing_target': lambda g: g.remove((reference, RDF.type, None)),
            'untyped_unreferenced_entity': lambda g: g.remove((unit, RDF.type, None)),
            'wrong_relation_type': lambda g: g.set((item, O.component_id, unit)),
            'wrong_field_type': lambda g: g.set((item, O.raw_value, Literal(17))),
            'evidence_origin': lambda g: g.set((evidence, O.origin, Literal('synthetic'))),
            'invented_status': lambda g: g.set((item, O.population, Literal('safe'))),
            'missing_evidence': lambda g: g.remove((item, O.evidence, None)),
            'unexpected_field': lambda g: g.set((item, O.failure_cause, Literal('invented'))),
            'negative_version': lambda g: g.set((item, P.data_version, Literal(-1))),
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                graph = self.small.rdf_graph()
                mutate(graph)
                result = validate_rdf(graph)
                self.assertFalse(result['valid'])
                self.assertTrue(result['errors'])
        self.assertTrue(validate_rdf(self.small.rdf_graph())['valid'])

    def test_payload_does_not_hide_rdf_relation_tampering_or_mixed_versions(self):
        changed = snapshot(self.small_nodes)
        item = entity_uri(self.left_item['id'])
        # Both referenced configurations exist and are well typed. The payload
        # check still detects an edge which no longer agrees with the approved JSON.
        changed._graph.set((item, O.configuration_id, entity_uri(CONFIG_L1)))
        with self.assertRaisesRegex(ValueError, 'RDF field disagrees'):
            changed.configuration(CONFIG_L1)
        changed = snapshot(self.small_nodes)
        changed._graph.set((item, P.data_version, Literal(99)))
        with self.assertRaisesRegex(ValueError, 'incomplete or has mixed'):
            changed.nodes()
        # Export callers cannot mutate the snapshot's internal graph.
        exported = self.small.rdf_graph()
        exported.remove((None, None, None))
        self.assertEqual(self.small_nodes, self.small.nodes())

    def test_same_common_temporal_rules_preserve_before_after_and_unknown(self):
        after_nodes = copy.deepcopy(self.nodes)
        updates = {op['node']['id']: op['node'] for op in self.supplement}
        after_nodes = [updates.pop(n['id'], n) for n in after_nodes] + list(updates.values())
        after = snapshot(after_nodes, version=3)
        for mode, query in example_queries().items():
            def evaluate(nodes):
                return investigate(query, *[[n for n in nodes if n['type'] == kind]
                                           for kind in ('ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage')])
            for projection, source in ((self.before, self.nodes), (after, after_nodes)):
                with self.subTest(mode=mode, version=projection.version):
                    self.assertEqual(evaluate(source), evaluate(projection.temporal_nodes()))
            results = {r['unit_id']: r for r in evaluate(after.temporal_nodes())['results']}
            self.assertEqual('excluded' if mode == 'A' else 'included', results['unit:U104']['status'])
            self.assertEqual('needs_confirmation', results['unit:U106']['status'])
            self.assertEqual([], results['unit:U106']['evidence'])
            self.assertIn('evidence:demo:supplement', results['unit:U104']['evidence'])

    def test_domain_validation_and_metadata_cannot_be_bypassed_by_rdf_projection(self):
        invalid = copy.deepcopy(self.small_nodes)
        next(n for n in invalid if n['type'] == 'BOMItem')['population'] = 'safe'
        with self.assertRaisesRegex(ValueError, 'Invalid domain graph'):
            snapshot(invalid)
        with self.assertRaises(ValueError):
            snapshot(self.small_nodes, version=True)
        with self.assertRaises(ValueError):
            RdfSnapshot(self.small_nodes, version=2, schema_version='other', rule_version=RULE_VERSION)
        with self.assertRaises(ValueError):
            RdfSnapshot(self.small_nodes, version=2, schema_version=SCHEMA_VERSION, rule_version='')


if __name__ == '__main__':
    unittest.main()
