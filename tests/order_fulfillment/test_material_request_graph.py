"""Approved B requests through real PostgreSQL/RDF/Neo4j and common lookup.

Each test owns its database schema and graph namespace. Development identities
stand in for people; successful reads do not imply A intake or processing.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

from examples.order_fulfillment.b_material_request import load_examples
from ontologics.graph_neo4j import Neo4jProjection, ProjectionError
from ontologics.graph_views import GraphSettings
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, digest
from ontologics.order_fulfillment.host import CommonHost
from ontologics.service import Service
from tests.db import database_for_test
from tests.test_graph_integration import register_graph_cleanup


REQUEST_TYPE = 'AdditionalMaterialReviewRequest'
MANAGED = {'type', 'origin', 'request_revision', 'reference_data_version'}
AUTHOR = UserIdentity('b-graph-author', frozenset({'read', 'draft'}))
REVIEWER = UserIdentity('b-graph-reviewer', frozenset({'read', 'review'}))
READER = UserIdentity('b-graph-reader', frozenset({'read'}))
NO_READ = UserIdentity('b-graph-no-read', frozenset({'draft'}))


class MaterialRequestGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples = load_examples()
        cls.catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']

    def setUp(self):
        self.database = database_for_test(self)
        self.settings = replace(GraphSettings.from_environment(backend='neo4j'),
                                namespace='test_b_material_' + uuid.uuid4().hex)
        identities = DevelopmentIdentityProvider([AUTHOR, REVIEWER, READER, NO_READ], enabled=True)
        self.hosts = {backend: CommonHost(identities, self.database,
            graph_settings=replace(self.settings, backend=backend))
            for backend in ('postgres', 'rdf', 'neo4j')}
        self.host = self.hosts['neo4j']
        self.audit = Service(self.database, principals={
            'fixture': {'kind': 'human', 'roles': ['seed', 'read']},
            'indexer': {'kind': 'system', 'roles': ['read', 'index']}},
            graph_settings=self.settings)
        self.addCleanup(self.audit.close)
        register_graph_cleanup(self, self.audit)
        self.ok(self.audit.bootstrap('fixture', self.catalog, request_key='catalog-seed'))

    def ok(self, response):
        self.assertTrue(response['ok'], response)
        return response

    def error(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def request(self, name):
        return {key: deepcopy(value) for key, value in self.examples['requests'][name].items()
                if key not in MANAGED}

    def prepare(self, request, *, base, revision, key):
        evidence_ids = set(request['evidence'])
        sources = {node['source_id'] for node in self.examples['supporting_nodes']
                   if node['type'] == 'Evidence' and node['id'] in evidence_ids}
        support = deepcopy([node for node in self.examples['supporting_nodes']
                            if node['id'] in evidence_ids | sources])
        with self.host.request(DevelopmentContext(AUTHOR.subject)) as session:
            draft = self.ok(session.material_request_draft(request, supporting_nodes=support,
                expected_data_version=base, expected_request_revision=revision, request_key=key))['data']
        self.assertTrue(draft['validation']['valid'], draft)
        self.assertEqual('awaiting_review', draft['status'])
        with self.host.request(DevelopmentContext(REVIEWER.subject)) as session:
            self.ok(session.review(draft['draft_id'], draft['draft_version'], decision='approve',
                note='Development stand-in: compare B request and original evidence',
                request_key=key + '-review'))
        return draft

    def apply(self, draft, key):
        return self.ok(self.host.apply_approved(draft['draft_id'], draft['draft_version'],
                                                request_key=key))

    def lookup(self, version, *, backend='neo4j', user=READER, **arguments):
        with self.hosts[backend].request(DevelopmentContext(user.subject)) as session:
            return session.call('lookup', dict(arguments, version=version))

    def business_hash(self):
        # Existing read-audit tables may change; business snapshots and approval
        # records must remain byte-for-byte equivalent across graph reads.
        return digest({table: sorted(row['payload'] for row in self.audit.db.execute(
            'SELECT row_to_json(t)::text AS payload FROM ' + table + ' AS t').fetchall())
            for table in ('snapshots', 'drafts', 'review_events')})

    def assert_parity(self, version, expected_requests):
        before = self.business_hash()
        results = {}
        for backend in ('postgres', 'rdf', 'neo4j'):
            with self.hosts[backend].request(DevelopmentContext(READER.subject)) as session:
                nodes = self.ok(session.call('lookup', {'kind': REQUEST_TYPE, 'version': version}))
                self.assertEqual(version, nodes['data_version'])
                # Backend agreement alone could preserve the same registration
                # error in all stores. Also compare the entire reviewed input.
                self.assertCountEqual(expected_requests, [node['id'] for node in nodes['data']])
                self.assertEqual(expected_requests, {node['id']: node for node in nodes['data']})
                results[backend] = {'requests': nodes['data'], 'evidence': nodes['evidence'], 'by_id': {}}
                for request_id, expected in expected_requests.items():
                    response = self.ok(session.call('lookup', {'node_id': request_id, 'version': version}))
                    self.assertEqual(version, response['data_version'])
                    self.assertEqual([expected], response['data'])
                    request = response['data'][0]
                    related = {}
                    for evidence_id in request['evidence']:
                        evidence_result = self.ok(session.call('lookup',
                            {'node_id': evidence_id, 'version': version}))
                        self.assertEqual(version, evidence_result['data_version'])
                        self.assertEqual(1, len(evidence_result['data']))
                        evidence = evidence_result['data'][0]
                        self.assertEqual((evidence_id, 'Evidence'), (evidence['id'], evidence['type']))
                        source_result = self.ok(session.call('lookup',
                            {'node_id': evidence['source_id'], 'version': version}))
                        self.assertEqual(version, source_result['data_version'])
                        self.assertEqual(1, len(source_result['data']))
                        source = source_result['data'][0]
                        self.assertEqual((evidence['source_id'], 'SourceDocument'), (source['id'], source['type']))
                        self.assertEqual('synthetic', source['origin'])
                        self.assertTrue(source['path'])
                        self.assertRegex(source['sha256'], r'^[0-9a-f]{64}$')
                        self.assertEqual(source['sha256'],
                            hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest())
                        related[evidence_id] = (evidence, source)
                    results[backend]['by_id'][request_id] = (request, related)
        self.assertEqual(results['postgres'], results['rdf'])
        self.assertEqual(results['postgres'], results['neo4j'])
        self.assertEqual(before, self.business_hash())
        return {node['id']: node for node in results['neo4j']['requests']}

    def assert_ready(self, applied):
        self.assertEqual('ready', applied['data']['graph_projection']['status'], applied)
        version = applied['data_version']
        self.assertTrue(self.audit.db.execute('SELECT reason FROM snapshots WHERE version=%s',
                                             (version,)).fetchone()['reason'].startswith('approved:'))
        # A successful response/replay alone is insufficient: read and verify
        # the actual remote snapshot against the current PostgreSQL authority.
        self.assertEqual(self.audit._nodes(version), self.audit.graph_views.reader(version).nodes())

    def test_new_amended_and_independent_requests_match_common_reads_and_history(self):
        primary = self.request('new_30')['id']
        independent = self.request('independent_10')['id']
        # Seeded PostgreSQL facts are deliberately not public in this namespace.
        self.error(self.lookup(1, kind=REQUEST_TYPE), 'GRAPH_NOT_READY')
        for name, base, revision in (('new_30', 1, 0), ('revised_40', 2, 1), ('independent_10', 3, 0)):
            draft = self.prepare(self.request(name), base=base, revision=revision, key=name)
            applied = self.apply(draft, name + '-apply')
            self.assertEqual(base + 1, applied['data_version'])
            self.assert_ready(applied)
        expected_new = dict(self.examples['requests']['new_30'], reference_data_version=1)
        expected_amended = dict(self.examples['requests']['revised_40'], reference_data_version=2)
        expected_independent = dict(self.examples['requests']['independent_10'], reference_data_version=3)
        old = self.assert_parity(2, {primary: expected_new})[primary]
        self.assertEqual((30, 1, 1), (old['quantity'], old['request_revision'], old['reference_data_version']))
        amended = self.assert_parity(3, {primary: expected_amended})
        self.assertEqual({primary}, set(amended))
        self.assertEqual((40, 2, 2), (amended[primary]['quantity'],
            amended[primary]['request_revision'], amended[primary]['reference_data_version']))
        expected_current = {primary: expected_amended, independent: expected_independent}
        current = self.assert_parity(4, expected_current)
        self.assertEqual({primary: (40, 2, 2), independent: (10, 1, 3)},
            {key: (node['quantity'], node['request_revision'], node['reference_data_version'])
             for key, node in current.items()})
        self.assertEqual(50, sum(node['quantity'] for node in current.values()))
        replay = self.apply(draft, 'independent_10-apply')
        self.assert_ready(replay)
        self.assertIn('IDEMPOTENT_REPLAY', {warning['code'] for warning in replay['warnings']})
        first_sync = self.ok(self.audit.graph_views.synchronize('indexer', 4, request_key='verify-latest'))
        replay_sync = self.ok(self.audit.graph_views.synchronize('indexer', 4, request_key='verify-latest'))
        self.assertEqual(first_sync['data'], replay_sync['data'])
        self.assertIn('IDEMPOTENT_REPLAY', {warning['code'] for warning in replay_sync['warnings']})
        self.assertEqual(self.audit._nodes(4), self.audit.graph_views.reader(4).nodes())
        self.error(self.lookup(4, user=NO_READ, node_id=primary), 'FORBIDDEN')
        self.error(self.lookup(999, node_id=primary), 'VERSION_NOT_FOUND')
        self.assertEqual(3, self.audit.db.execute('SELECT count(*) AS count FROM drafts').fetchone()['count'])

        # Deliberate fault injection only in this test's disposable namespace:
        # a missing request/evidence edge must never fall back to PostgreSQL.
        before = self.business_hash()
        projection = self.audit.graph_views.projection()
        with projection._driver.session(database=projection.database) as session:
            summary = session.run(
                'MATCH (n:OntologicsEntity {namespace:$namespace,version:$version,id:$id}) '
                '-[r:OntologicsLink {field:"evidence",ordinal:0}]->() DELETE r',
                namespace=self.audit.graph_views.namespace, version=4, id=primary).consume()
        self.assertEqual(1, summary.counters.relationships_deleted)
        self.error(self.lookup(4, node_id=primary), 'GRAPH_INTEGRITY')
        self.assertEqual(40, self.ok(self.lookup(4, backend='postgres', node_id=primary))['data'][0]['quantity'])
        self.assertEqual(30, self.ok(self.lookup(2, node_id=primary))['data'][0]['quantity'])
        # A cached successful sync is not a live health check. A fresh key
        # revalidates and refuses to silently overwrite a damaged projection.
        cached = self.ok(self.audit.graph_views.synchronize('indexer', 4, request_key='verify-latest'))
        self.assertIn('IDEMPOTENT_REPLAY', {warning['code'] for warning in cached['warnings']})
        self.assertEqual('ready', cached['data']['status'])
        self.error(self.lookup(4, kind=REQUEST_TYPE), 'GRAPH_INTEGRITY')
        self.error(self.audit.graph_views.synchronize('indexer', 4,
            request_key='inspect-damaged-latest'), 'GRAPH_INTEGRITY')
        self.error(self.lookup(None, node_id=primary), 'GRAPH_INTEGRITY')
        self.assertEqual(before, self.business_hash())

    def test_pending_publication_recovers_and_supplementation_preserves_approved_history(self):
        incomplete = self.request('new_30')
        incomplete.update(need_mode='unknown', conditions_state='unknown', readiness='needs_supplementation',
                          supplementation_reason='Development fixture: need date and baseline unconfirmed')
        for field in ('baseline_plan_evidence_id', 'need_basis', 'need_date'):
            incomplete.pop(field)
        draft = self.prepare(incomplete, base=1, revision=0, key='incomplete')
        with patch.object(Neo4jProjection, 'sync', side_effect=ProjectionError('GRAPH_UNAVAILABLE', 'Test outage')):
            applied = self.apply(draft, 'incomplete-apply')
        self.assertEqual(2, applied['data_version'])
        self.assertEqual('pending', applied['data']['graph_projection']['status'])
        self.assertIn('GRAPH_PUBLICATION_PENDING', {warning['code'] for warning in applied['warnings']})
        self.assertEqual('needs_supplementation', self.ok(self.lookup(2, backend='postgres',
            node_id=incomplete['id']))['data'][0]['readiness'])
        self.error(self.lookup(2, node_id=incomplete['id']), 'GRAPH_NOT_READY')
        self.error(self.lookup(None, node_id=incomplete['id']), 'GRAPH_NOT_READY')
        recovered = self.apply(draft, 'incomplete-apply')
        self.assert_ready(recovered)
        self.assertEqual(2, self.audit._version())
        self.assertEqual(1, self.audit.db.execute('SELECT count(*) AS count FROM drafts').fetchone()['count'])
        complete = self.prepare(self.request('revised_40'), base=2, revision=1, key='complete')
        self.assert_ready(self.apply(complete, 'complete-apply'))
        expected_incomplete = dict(incomplete, type=REQUEST_TYPE, origin='synthetic',
                                   request_revision=1, reference_data_version=1)
        expected_complete = dict(self.examples['requests']['revised_40'], reference_data_version=2)
        old = self.assert_parity(2, {incomplete['id']: expected_incomplete})[incomplete['id']]
        current = self.assert_parity(3, {incomplete['id']: expected_complete})[incomplete['id']]
        self.assertEqual(('needs_supplementation', 'unknown', 1),
                         (old['readiness'], old['need_mode'], old['request_revision']))
        self.assertNotIn('baseline_plan_evidence_id', old)
        self.assertNotIn('need_date', old)
        self.assertEqual(('ready', 'explicit', 2, 2, 40), (current['readiness'], current['need_mode'],
            current['request_revision'], current['reference_data_version'], current['quantity']))
        self.assertEqual(self.request('revised_40')['need_date'], current['need_date'])
        self.assertEqual(self.request('revised_40')['baseline_plan_evidence_id'], current['baseline_plan_evidence_id'])
