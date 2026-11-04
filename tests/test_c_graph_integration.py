"""C approvals, graph reads and additive-schema compatibility.

Real PostgreSQL/Neo4j, isolated test schemas/namespaces, public OLIMEX sources
and existing synthetic fixtures. No production records or model API calls.
"""
from contextlib import ExitStack
import copy
from dataclasses import replace
from importlib import import_module
import json
import unittest
from unittest.mock import patch

from examples.ab_graph_check import candidates
from examples.graph_cq_check import comparable
from ontologics.demo import CONFIG_L, example_queries, fixture_nodes, must
from ontologics.domain_schema import C2_TYPES, C_TYPES
from ontologics.graph_display import DISPLAY_NAMES
from ontologics.graph_neo4j import Neo4jProjection, ProjectionError
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.model import digest
from ontologics.service import Service
from ontologics.web import USERS
from tests.claim_response import test_schema as c_fixture
from tests import test_c_graph_rdf as c_rdf_fixture
from tests.db import database_for_test
from tests.test_graph_integration import graph_settings, register_graph_cleanup


# Keep the old C1 fixture coverage; the separate C2 path requires nonempty reads
# for all nine C types rather than comparing empty second-phase result sets.
C1_TYPES = tuple(kind for kind in C_TYPES if kind not in C2_TYPES)

PRINCIPALS = {
    'fixture': {'kind': 'human', 'roles': ['seed', 'read']},
    'indexer': {'kind': 'system', 'roles': ['read', 'index']},
}


class ClaimGraphIntegrationTests(unittest.TestCase):
    def open_service(self, database, settings=None):
        service = Service(database, principals=PRINCIPALS, graph_settings=settings)
        self.addCleanup(service.close)
        return service

    def assert_error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def display_rows(self, service, version):
        projection = service.graph_views.projection()
        with projection._driver.session(database=projection.database, default_access_mode='READ') as session:
            return session.run(
                'MATCH (n:OntologicsEntity {namespace:$namespace,version:$version}) '
                'RETURN labels(n) AS labels, properties(n) AS node ORDER BY n.ordinal',
                namespace=service.graph_views.namespace, version=version).data()

    def test_c_exact_approval_graph_parity_display_and_s1_supplement(self):
        self._assert_c_approval_and_reads(include_phase2=False)

    def test_c2_exact_approval_publication_retry_graph_parity_and_s1_supplement(self):
        self._assert_c_approval_and_reads(include_phase2=True)

    def _assert_c_approval_and_reads(self, *, include_phase2):
        base, ab_records = candidates()
        kinds = C_TYPES if include_phase2 else C1_TYPES
        if include_phase2:
            existing = {n['id'] for n in base + ab_records}
            added = [n for n in c_rdf_fixture.phase2_nodes(self.addCleanup) if n['id'] not in existing]
        else:
            fixture = c_fixture.CPhase1ValidationTests
            fixture.setUpClass()
            self.addCleanup(fixture.doClassCleanups)
            added = copy.deepcopy(fixture.c_nodes)
            intake = next(n for n in added if n['type'] == 'ClaimIntake')
            intake.update(claimed_quantity=2, reported_serials=['C-D001', 'C-S001'])
            case = next(n for n in added if n['type'] == 'QualityCase')
            case['unit_ids'] = ['unit:test-C-D001', 'unit:test-C-S001']
        # Includes both scalar numeric fields and a multi-valued literal list;
        # these valid C values previously failed the common SHACL projection.
        self.assertEqual(1, next(n for n in added if n['id'] == 'containment:test-production-stop')['draft_version'])

        database = database_for_test(self)
        settings = graph_settings()
        audit = self.open_service(database, settings)
        register_graph_cleanup(self, audit)
        host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), database, graph_settings=settings)
        must(audit.bootstrap('fixture', base + ab_records, request_key='c-seed'))
        if include_phase2:
            # An older ready projection must never substitute for a failed new one.
            must(audit.graph_views.synchronize('indexer', 1, request_key='c-seed-sync'))
        old_hash = digest(audit._nodes(1))
        operations = [{'op': 'put', 'node': n} for n in added]
        with host.request(DevelopmentContext('dev-alice')) as request:
            draft = must(request.call('prepare_draft', {
                'operations': operations, 'request_key': 'c-draft'}))['data']
            self.assertTrue(draft['validation']['valid'])
            self.assert_error(request.review(draft['draft_id'], 1, decision='approve',
                note='Self-approval test', request_key='c-self'), 'SELF_APPROVAL_FORBIDDEN')
            revised = must(request.call('prepare_draft', {
                'operations': operations, 'draft_id': draft['draft_id'],
                'expected_version': 1, 'request_key': 'c-revise'}))['data']
        self.assertEqual(1, audit._version())
        self.assertEqual(old_hash, digest(audit._nodes(1)))
        self.assert_error(host.apply_approved(draft['draft_id'], 2,
            request_key='c-unapproved'), 'APPROVAL_REQUIRED')
        with host.request(DevelopmentContext('dev-bob')) as request:
            self.assert_error(request.review(draft['draft_id'], 1, decision='approve',
                note='Stale version test', request_key='c-stale'), 'SUPERSEDED_DRAFT')
            reviewed = must(request.review(draft['draft_id'], 2, decision='approve',
                note='Review synthetic C source records', request_key='c-review'))
        self.assertEqual(digest(operations), reviewed['data']['content_hash'])
        self.assertEqual(2, revised['draft_version'])
        if include_phase2:
            with patch.object(Neo4jProjection, 'sync',
                              side_effect=ProjectionError('GRAPH_UNAVAILABLE', 'Injected C2 outage')):
                pending = must(host.apply_approved(draft['draft_id'], 2, request_key='c-apply'))
            self.assertEqual(2, pending['data_version'])
            self.assertEqual('pending', pending['data']['graph_projection']['status'])
            self.assertIn('GRAPH_PUBLICATION_PENDING', [w['code'] for w in pending['warnings']])
            self.assertEqual(2, audit._version())
            self.assert_error(audit.lookup('fixture', kind='ReproductionRequirement'), 'GRAPH_NOT_READY')
            self.assertTrue(audit.configuration('fixture', CONFIG_L, version=1)['ok'])
            pending_draft = must(audit.get_draft('fixture', draft['draft_id'], 2))['data']
        applied = must(host.apply_approved(draft['draft_id'], 2, request_key='c-apply'))
        self.assertEqual(2, applied['data_version'])
        self.assertEqual('ready', applied['data']['graph_projection']['status'])
        replay = must(host.apply_approved(draft['draft_id'], 2, request_key='c-apply'))
        self.assertEqual(2, replay['data_version'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['warnings']])
        if include_phase2:
            self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in applied['warnings']])
            retried_draft = must(audit.get_draft('fixture', draft['draft_id'], 2))['data']
            for field in ('operations', 'content_hash', 'review_history', 'author'):
                self.assertEqual(pending_draft[field], retried_draft[field], field)

        readers = {backend: self.open_service(database, replace(settings, backend=backend))
                   for backend in ('postgres', 'rdf', 'neo4j')}
        results = {}
        for backend, reader in readers.items():
            results[backend] = {kind: comparable(must(reader.lookup('fixture', kind=kind, version=2)))
                                for kind in kinds}
            results[backend]['export'] = comparable(must(reader.export_graph('fixture', version=2)))
            results[backend]['source'] = comparable(must(reader.lookup('fixture',
                node_id='source:test-c', version=2)))
        self.assertEqual(results['postgres'], results['rdf'])
        self.assertEqual(results['postgres'], results['neo4j'])
        for kind in kinds:
            self.assertTrue(results['postgres'][kind]['data'], kind)
            self.assertTrue(all(n['origin'] == 'synthetic' for n in results['postgres'][kind]['data']))
            self.assertIn('evidence:test-c', results['postgres'][kind]['evidence'])

        display_before = self.display_rows(audit, 2)
        expected = {n['id']: n for n in audit._nodes(2)}
        for row in display_before:
            node = json.loads(row['node']['payload'])
            self.assertEqual(expected[node['id']], node)
            if node['type'] in C_TYPES:
                self.assertIn(node['type'], row['labels'])
                self.assertIn(DISPLAY_NAMES[node['type']], row['node']['browser_caption'])
                self.assertIn('[가상]', row['node']['browser_caption'])

        # C/A/B business facts must not change core temporal semantics.
        with host.request(DevelopmentContext('dev-alice')) as request:
            supplement = must(request.call('prepare_draft', {
                'operations': fixture_nodes()[1], 'request_key': 'c-supplement'}))['data']
        with host.request(DevelopmentContext('dev-bob')) as request:
            must(request.review(supplement['draft_id'], 1, decision='approve',
                note='Source-backed U104 supplement', request_key='c-supplement-review'))
        supplemented = must(host.apply_approved(supplement['draft_id'], 1, request_key='c-supplement-apply'))
        self.assertEqual('ready', supplemented['data']['graph_projection']['status'])
        comparisons = {backend: {mode: comparable(must(reader.compare_investigation('fixture', query, 2, 3)))
                                 for mode, query in example_queries().items()}
                       for backend, reader in readers.items()}
        self.assertEqual(comparisons['postgres'], comparisons['rdf'])
        self.assertEqual(comparisons['postgres'], comparisons['neo4j'])
        for mode in ('A', 'B'):
            rows = comparisons['postgres'][mode]['data']['after']['results']
            self.assertEqual('excluded' if mode == 'A' else 'included', rows[0]['status'])
            self.assertEqual('needs_confirmation', rows[2]['status'])
        self.assertEqual(display_before, self.display_rows(audit, 2))
        self.assertEqual(old_hash, digest(audit._nodes(1)))

    def test_graph_02_approved_draft_and_old_namespace_are_preserved(self):
        self._assert_previous_approval_and_namespace('graph-0.2')

    def test_graph_03_c1_approved_draft_and_old_namespace_are_preserved(self):
        self._assert_previous_approval_and_namespace('graph-0.3')

    def _assert_previous_approval_and_namespace(self, previous_version):
        database = database_for_test(self)
        old_settings = replace(graph_settings(), namespace='c_compat_old')
        old = self.open_service(database, old_settings)
        register_graph_cleanup(self, old)
        old_host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), database,
                              graph_settings=old_settings)
        base, ab_records = candidates()
        operations = fixture_nodes()[1]
        if previous_version == 'graph-0.3':
            fixture = c_fixture.CPhase1ValidationTests
            fixture.setUpClass()
            self.addCleanup(fixture.doClassCleanups)
            operations = [{'op': 'put', 'node': n} for n in copy.deepcopy(fixture.c_nodes)]
        # The persisted draft format has no schema migration. Construct the
        # previous envelope/projection version while keeping approved facts and
        # operations identical, then switch only the common schema contract.
        # Load all constants before patching; a lazy RDF import under the old
        # model value would retain that value after the patch context exits.
        modules = [import_module('ontologics.' + name)
                   for name in ('model', 'service', 'responses', 'graph_views', 'graph_rdf')]
        with ExitStack() as previous:
            for module in modules:
                previous.enter_context(patch.object(module, 'SCHEMA_VERSION', previous_version))
            must(old.bootstrap('fixture', base + ab_records, request_key='old-seed'))
            must(old.graph_views.synchronize('indexer', 1, request_key='old-sync'))
            with old_host.request(DevelopmentContext('dev-alice')) as request:
                draft = must(request.call('prepare_draft', {
                    'operations': operations, 'request_key': 'old-draft'}))['data']
            with old_host.request(DevelopmentContext('dev-bob')) as request:
                must(request.review(draft['draft_id'], 1, decision='approve',
                    note='Previously approved source correction', request_key='old-review'))
        before = must(old.get_draft('fixture', draft['draft_id'], 1))['data']
        old_display = self.display_rows(old, 1)
        old_snapshot_hash = digest(old._nodes(1))
        self.assert_error(old.configuration('fixture', CONFIG_L, version=1), 'GRAPH_VERSION_CONFLICT')
        self.assert_error(old.graph_views.synchronize('indexer', 1, request_key='no-overwrite'),
                          'GRAPH_VERSION_CONFLICT')

        fresh_settings = replace(old_settings, namespace='c_compat_new')
        fresh = self.open_service(database, fresh_settings)
        register_graph_cleanup(self, fresh)
        self.assertNotEqual(old.graph_views.namespace, fresh.graph_views.namespace)
        self.assert_error(fresh.configuration('fixture', CONFIG_L, version=1), 'GRAPH_NOT_READY')
        must(fresh.graph_views.synchronize('indexer', 1, request_key='new-sync'))
        self.assertTrue(fresh.configuration('fixture', CONFIG_L, version=1)['ok'])
        self.assertEqual(old_snapshot_hash, digest(fresh.graph_views.reader(1).nodes()))
        self.assertTrue(must(fresh.validate_draft('fixture', draft['draft_id'], 1))['data']['validation']['valid'])
        host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), database, graph_settings=fresh_settings)
        applied = must(host.apply_approved(draft['draft_id'], 1, request_key='old-approved-apply'))
        self.assertEqual(2, applied['data_version'])
        self.assertEqual('ready', applied['data']['graph_projection']['status'])
        after = must(fresh.get_draft('fixture', draft['draft_id'], 1))['data']
        for field in ('operations', 'content_hash', 'review_history', 'author'):
            self.assertEqual(before[field], after[field], field)
        self.assertEqual(digest(operations), after['content_hash'])
        self.assertEqual(old_snapshot_hash, digest(fresh._nodes(1)))
        self.assertEqual(old_display, self.display_rows(old, 1))
