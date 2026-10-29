"""Real Neo4j projection invariants; no model calls and no shared data deletion."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
import unittest
import uuid
from unittest.mock import MagicMock, patch

from ontologics.database import environment_values
from ontologics.demo import CONFIG_L, CONFIG_L1, LOT, example_queries, fixture_nodes
from ontologics.graph_neo4j import Neo4jProjection, ProjectionError
from ontologics.model import SCHEMA_VERSION, digest
from ontologics.service import RULE_VERSION
from ontologics.temporal import investigate

ROOT = Path(__file__).resolve().parents[1]


class Neo4jProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        values = environment_values()
        cls.url = values.get('TEST_NEO4J_URL')
        cls.user = values.get('TEST_NEO4J_USER')
        cls.password = values.get('TEST_NEO4J_PASSWORD')
        if not all((cls.url, cls.user, cls.password)):
            raise RuntimeError('Set TEST_NEO4J_URL, TEST_NEO4J_USER and TEST_NEO4J_PASSWORD for real graph integration tests')
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        operational, cls.supplement = fixture_nodes()
        cls.fixture = catalog + operational

    def setUp(self):
        self.namespace = 'test_' + uuid.uuid4().hex
        self.nodes = copy.deepcopy(self.fixture)
        self.projection = Neo4jProjection(self.url, self.user, self.password, namespace=self.namespace)
        self.addCleanup(self.projection.close)
        self.addCleanup(self.clean)

    def clean(self):
        # Only the unique namespace owned by this test is removed.
        assert self.namespace.startswith('test_') and len(self.namespace) == 37
        with self.projection._driver.session(database=self.projection.database) as session:
            session.run('MATCH (n) WHERE n.namespace=$namespace DETACH DELETE n', namespace=self.namespace).consume()

    def sync(self, version=1, nodes=None):
        return self.projection.sync(self.nodes if nodes is None else nodes, version=version,
                                    schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)

    def snapshot(self, version=1, nodes=None, **overrides):
        args = dict(version=version, content_hash=digest(self.nodes if nodes is None else nodes),
                    schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)
        args.update(overrides)
        return self.projection.for_version(**args)

    def error(self, code, fn):
        with self.assertRaises(ProjectionError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def run_cypher(self, query, **params):
        with self.projection._driver.session(database=self.projection.database) as session:
            return session.run(query, namespace=self.namespace, **params).data()

    def test_relationship_queries_preserve_source_scope_payload_order_and_evidence(self):
        published = self.sync()
        self.assertEqual('ready', published['state'])
        snap = self.snapshot()
        self.assertEqual(self.nodes, snap.nodes())
        config = snap.configuration(CONFIG_L1)
        index = {n['id']: n for n in self.nodes}
        expected_items = [n for n in self.nodes if n['type'] == 'BOMItem' and n['configuration_id'] == CONFIG_L1]
        self.assertEqual(expected_items, config['items'])
        self.assertEqual(index[CONFIG_L1], config['configuration'])
        self.assertEqual(index[index[CONFIG_L1]['revision_id']], config['revision'])
        self.assertEqual(index[config['revision']['model_id']], config['product'])
        self.assertEqual([index[cid] for cid in sorted({n['component_id'] for n in expected_items})], config['components'])
        component = next(n for n in config['components'] if n['name'].startswith('LAN8710A-EZC'))
        usage = snap.component_usage(component['id'])
        self.assertEqual([n for n in expected_items if n['component_id'] == component['id']], usage['items'])
        self.assertEqual(1, len(usage['items']))
        self.assertEqual(['U4'], usage['items'][0]['positions'])
        for eid in usage['items'][0]['evidence']:
            evidence = snap.lookup(node_id=eid)[0]
            source = snap.lookup(node_id=evidence['source_id'])[0]
            self.assertEqual(index[eid], evidence)
            self.assertEqual('real', source['origin'])
            self.assertEqual(index[source['id']]['sha256'], source['sha256'])
        self.error('NOT_FOUND', lambda: snap.configuration('"}) MATCH (n) DETACH DELETE n //'))
        self.error('NOT_FOUND', lambda: snap.lookup(node_id="x' OR 1=1"))
        self.assertEqual(self.nodes, snap.nodes())

    def test_approved_version_and_rules_are_exact_and_same_version_cannot_change(self):
        self.sync()
        changed = copy.deepcopy(self.nodes)
        next(n for n in changed if n['id'] == CONFIG_L)['name'] += ' approved revision label'
        self.sync(2, changed)
        self.assertNotEqual(self.snapshot().configuration(CONFIG_L)['configuration']['name'],
                            self.snapshot(2, changed).configuration(CONFIG_L)['configuration']['name'])
        self.error('GRAPH_NOT_READY', lambda: self.snapshot(3))
        self.error('GRAPH_VERSION_CONFLICT', lambda: self.snapshot(2))
        self.error('GRAPH_VERSION_CONFLICT', lambda: self.snapshot(rule_version='temporal-other'))
        self.error('GRAPH_VERSION_CONFLICT', lambda: self.sync(1, changed))
        self.assertEqual(self.nodes, self.snapshot().nodes())

    def test_identical_concurrent_retries_do_not_duplicate_nodes_or_relationships(self):
        # Initialize constraints once, then let two same-version writers race.
        self.projection._ensure_constraints()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.sync(), range(2)))
        self.assertEqual([False, True], sorted(result['replayed'] for result in results))
        replay = self.sync()
        self.assertTrue(replay['replayed'])
        counts = self.run_cypher('MATCH (n:OntologicsEntity {namespace:$namespace,version:1}) '
                                'RETURN count(n) AS nodes')[0]
        edges = self.run_cypher('MATCH (:OntologicsEntity {namespace:$namespace,version:1})-[r]->() '
                               'RETURN count(r) AS edges')[0]
        self.assertEqual(len(self.nodes), counts['nodes'])
        self.assertEqual(replay['relationship_count'], edges['edges'])
        self.assertEqual(self.nodes, self.snapshot().nodes())

    def test_failed_transaction_is_unpublished_and_retry_recovers_without_touching_previous(self):
        self.sync(1)
        with patch.object(self.projection, '_write_edges', side_effect=RuntimeError('injected before commit')):
            with self.assertRaisesRegex(RuntimeError, 'injected before commit'):
                self.sync(2)
        self.error('GRAPH_NOT_READY', lambda: self.snapshot(2))
        count = self.run_cypher('MATCH (n) WHERE n.namespace=$namespace AND n.version=2 RETURN count(n) AS n')[0]['n']
        self.assertEqual(0, count)
        self.assertEqual(self.nodes, self.snapshot(1).nodes())
        self.assertFalse(self.sync(2)['replayed'])
        self.assertEqual(self.nodes, self.snapshot(2).nodes())

    def test_readers_cannot_observe_staged_nodes_before_atomic_publication(self):
        self.sync(1)
        staged, release = Event(), Event()
        write_edges = self.projection._write_edges

        def pause_before_edges(tx, metadata, edges):
            staged.set()
            if not release.wait(15):
                raise RuntimeError('test did not release the staged transaction')
            write_edges(tx, metadata, edges)

        with patch.object(self.projection, '_write_edges', side_effect=pause_before_edges):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.sync, 2)
                try:
                    self.assertTrue(staged.wait(10), 'writer did not reach its uncommitted stage')
                    self.error('GRAPH_NOT_READY', lambda: self.snapshot(2))
                    self.assertEqual(self.nodes, self.snapshot(1).nodes())
                finally:
                    release.set()
                self.assertEqual('ready', pending.result(timeout=15)['state'])
        self.assertEqual(self.nodes, self.snapshot(2).nodes())

    def test_existing_reader_rechecks_corruption_missing_edges_and_wrong_origin(self):
        self.sync()
        snap = self.snapshot()
        self.run_cypher('MATCH (n:OntologicsEntity {namespace:$namespace,version:1,id:$id}) '
                        'SET n.origin="synthetic"', id=CONFIG_L)
        self.error('GRAPH_INTEGRITY', snap.nodes)
        self.run_cypher('MATCH (n:OntologicsEntity {namespace:$namespace,version:1,id:$id}) '
                        'SET n.origin="real"', id=CONFIG_L)
        self.run_cypher('MATCH (n:OntologicsEntity {namespace:$namespace,version:1,id:$id})-[r]->() DELETE r', id=CONFIG_L)
        self.error('GRAPH_INTEGRITY', lambda: snap.configuration(CONFIG_L))
        self.error('GRAPH_INTEGRITY', self.sync)

    def test_cross_version_relationships_are_never_served_as_normal_results(self):
        self.sync(1); self.sync(2)
        self.run_cypher('MATCH (a:OntologicsEntity {namespace:$namespace,version:1,id:$id}) '
                        'MATCH (b:OntologicsEntity {namespace:$namespace,version:2,id:$id}) '
                        'CREATE (a)-[:OntologicsLink {field:"evidence",ordinal:999}]->(b)', id=CONFIG_L)
        self.error('GRAPH_INTEGRITY', lambda: self.snapshot(1))
        self.error('GRAPH_INTEGRITY', lambda: self.snapshot(2))

    def test_temporal_read_preserves_ab_unknowns_and_supplement_evidence(self):
        self.sync(1)
        changed = {n['id']: copy.deepcopy(n) for n in self.nodes}
        for operation in self.supplement:
            changed[operation['node']['id']] = operation['node']
        after = list(changed.values())
        self.sync(2, after)
        for mode, query in example_queries().items():
            results = []
            for snap in (self.snapshot(), self.snapshot(2, after)):
                nodes = snap.temporal_nodes()
                groups = [[n for n in nodes if n['type'] == k]
                          for k in ('ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage')]
                results.append(investigate(query, *groups))
            self.assertEqual(results[0]['query'], results[1]['query'])
            before_row = next(r for r in results[0]['results'] if r['unit_id'] == 'unit:U104')
            after_row = next(r for r in results[1]['results'] if r['unit_id'] == 'unit:U104')
            self.assertEqual('needs_confirmation', before_row['status'])
            self.assertEqual('excluded' if mode == 'A' else 'included', after_row['status'])
            self.assertIn('evidence:demo:supplement', after_row['evidence'])
            unknown = next(r for r in results[1]['results'] if r['unit_id'] == 'unit:U106')
            self.assertEqual('needs_confirmation', unknown['status'])
            self.assertEqual([], unknown['evidence'])

    def test_mutation_between_verification_and_query_is_not_returned(self):
        self.sync()
        snapshot = self.snapshot()
        verified, changed = Event(), Event()
        original = self.projection._verify

        def pause(tx, expected, **kwargs):
            result = original(tx, expected, **kwargs)
            verified.set()
            self.assertTrue(changed.wait(5), 'Concurrent mutation did not complete')
            return result

        def mutate():
            self.assertTrue(verified.wait(5), 'Reader did not verify')
            altered = copy.deepcopy(next(n for n in self.nodes if n['id'] == CONFIG_L))
            altered['name'] = 'not an approved name'
            self.run_cypher('MATCH (n:OntologicsEntity {namespace:$namespace,version:1,id:$id}) '
                            'SET n.payload=$payload', id=CONFIG_L, payload=json.dumps(altered))
            changed.set()

        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(self.projection, '_verify', pause):
            writer = pool.submit(mutate)
            self.error('GRAPH_INTEGRITY', lambda: snapshot.configuration(CONFIG_L))
            writer.result(timeout=5)

    def test_namespace_isolation_and_invalid_source_do_not_write(self):
        self.sync()
        other = Neo4jProjection(self.url, self.user, self.password, namespace='test_' + uuid.uuid4().hex)
        try:
            self.error('GRAPH_NOT_READY', lambda: other.for_version(version=1, content_hash=digest(self.nodes),
                                        schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION))
        finally:
            other.close()
        invalid = copy.deepcopy(self.nodes)
        next(n for n in invalid if n['type'] == 'ProductUnit')['origin'] = 'real'
        self.error('GRAPH_VALIDATION_FAILED', lambda: self.sync(2, invalid))
        self.error('GRAPH_NOT_READY', lambda: self.snapshot(2))
        self.assertEqual(self.nodes, self.snapshot().nodes())


class Neo4jDeadlineTests(unittest.TestCase):
    """Shared budget boundaries are checked without a database or model call."""
    def test_transaction_and_constraints_use_remaining_deadline_without_driver_retries(self):
        from ontologics.harness import BudgetLimits, ExecutionBudget, active_budget
        from ontologics.service import DomainError
        now = [0.0]
        budget = ExecutionBudget(BudgetLimits(deadline_seconds=2), clock=lambda: now[0])
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        tx = MagicMock()
        callbacks = []
        def execute(callback):
            callbacks.append(callback)
            return callback(tx)
        session.execute_read.side_effect = execute
        with patch('ontologics.graph_neo4j.GraphDatabase.driver', return_value=driver) as create:
            projection = Neo4jProjection('bolt://example.invalid', 'test', 'test', namespace='deadline_test')
        self.assertEqual(0, create.call_args.kwargs['max_transaction_retry_time'])
        with active_budget(budget):
            now[0] = .5
            projection._ensure_constraints()
            self.assertTrue(all(call.args[0].timeout == 1.5 for call in session.run.call_args_list))
            self.assertEqual('ok', projection._execute(lambda tx: 'ok'))
            self.assertEqual(1.5, callbacks[-1].timeout)
            def crossing(tx):
                tx.run('RETURN 1')
                now[0] = 2
                tx.run('RETURN 2')
            with self.assertRaises(DomainError) as caught:
                projection._execute(crossing)
            self.assertEqual('DEADLINE_EXCEEDED', caught.exception.code)
            self.assertEqual(1, tx.run.call_count)
            before = driver.session.call_count
            with self.assertRaises(DomainError):
                projection._execute(lambda tx: tx.run('RETURN 3'))
            self.assertEqual(before, driver.session.call_count)
        projection.close()

    def test_constraint_dispatch_stops_after_deadline_and_unbudgeted_timeout_is_bounded(self):
        from ontologics.harness import BudgetLimits, ExecutionBudget, active_budget
        from ontologics.service import DomainError
        now = [0.0]
        budget = ExecutionBudget(BudgetLimits(deadline_seconds=1), clock=lambda: now[0])
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value.consume.side_effect = lambda: now.__setitem__(0, 1)
        with patch('ontologics.graph_neo4j.GraphDatabase.driver', return_value=driver):
            projection = Neo4jProjection('bolt://example.invalid', 'test', 'test', namespace='deadline_test')
        with active_budget(budget), self.assertRaises(DomainError) as caught:
            projection._ensure_constraints()
        self.assertEqual('DEADLINE_EXCEEDED', caught.exception.code)
        self.assertEqual(1, session.run.call_count)
        self.assertFalse(projection._constraints_ready)
        session.execute_read.side_effect = lambda callback: callback(MagicMock())
        projection._execute(lambda tx: 'no shared budget')
        self.assertEqual(30, session.execute_read.call_args.args[0].timeout)
        projection.close()


if __name__ == '__main__':
    unittest.main()
