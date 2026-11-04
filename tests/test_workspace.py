"""Actual HTTP/PG overview boundaries; no production data or model calls."""
import copy

from examples.ab_graph_check import candidates
from ontologics.graph_views import GraphSettings
from ontologics.identity import DevelopmentIdentityProvider, UserIdentity
from ontologics.model import digest
from ontologics.service import Service
from tests import test_web as web_fixture
from tests.claim_response import test_schema as claim_fixture


class DashboardTests(web_fixture.WebCase):
    def audit(self):
        service = Service(self.database, principals={'audit': {'kind': 'human', 'roles': ['read']}})
        self.addCleanup(service.close)
        return service

    def test_http_approved_groups_pin_version_preserve_evidence_and_do_not_mutate_facts(self):
        fixture = claim_fixture.CPhase1ValidationTests
        fixture.setUpClass()
        self.addCleanup(fixture.doClassCleanups)
        _, business = candidates()
        records = copy.deepcopy(business + fixture.c_nodes)
        line = next(node for node in fixture.c_nodes if node['type'] == 'ProductionLine')
        records.extend(dict(copy.deepcopy(line), id=f'line:dashboard-{i}', line_code=f'DASH-{i}')
                       for i in range(21))
        draft = self.ok(self.tool('prepare_draft', {'operations': [
            {'op': 'put', 'node': node} for node in records], 'request_key': 'dashboard-business'}))['data']
        self.assertTrue(draft['validation']['valid'], draft)
        # A proposal is visible only in the draft ledger, never in approved graph counts.
        before = self.ok(self.http(self.alice, '/api/dashboard'))
        self.assertEqual(0, before['data']['groups']['planning']['total'])
        self.assertEqual(1, before['data']['draft_total'])
        applied = self.ok(self.http(self.bob, '/api/review', self.review_body(draft)))
        version = applied['data_version']
        audit = self.audit()
        immutable_before = digest({'nodes': audit._nodes(),
            'drafts': audit.db.execute('SELECT * FROM drafts ORDER BY id,version').fetchall(),
            'reviews': audit.db.execute('SELECT * FROM review_events ORDER BY sequence').fetchall()})
        result = self.ok(self.http(self.reader, '/api/dashboard'))
        data = result['data']
        self.assertEqual(version, result['data_version'])
        self.assertEqual(version, data['version'])
        self.assertEqual(1, data['draft_counts']['applied'])
        self.assertEqual({'planning', 'fulfillment', 'claims'}, set(data['groups']))
        self.assertIn('Contract', data['groups']['fulfillment']['kinds'])
        self.assertIn('ContractItem', data['groups']['fulfillment']['kinds'])
        approved = {node['id']: node for node in audit._nodes(version)}
        for name, group in data['groups'].items():
            expected = [node for node in approved.values() if node['type'] in group['kinds']]
            self.assertEqual(len(expected), group['total'], name)
            self.assertEqual(min(len(expected), 20), len(group['records']), name)
            for node in group['records']:
                self.assertEqual(approved[node['id']], node)
                self.assertEqual('synthetic', node['origin'])
                self.assertTrue(set(node['evidence']) <= set(result['evidence']))
        self.assertTrue(data['groups']['claims']['truncated'])
        self.assertEqual(20, len(data['groups']['claims']['records']))
        # The selected RDF path returns the same pinned facts, not a second SQL-only summary.
        self.host._graph_settings = GraphSettings(backend='rdf')
        # Cold RDF/SHACL projection can exceed the generic HTTP fixture's 15s
        # on shared CI runners. This is a parity/integrity check, not a latency SLO;
        # keep a finite test-client wait without changing service budget checks.
        rdf = self.ok(self.http(self.reader, '/api/dashboard', timeout=60))
        self.assertEqual(data, rdf['data'])
        self.assertEqual(result['evidence'], rdf['evidence'])
        immutable_after = digest({'nodes': audit._nodes(),
            'drafts': audit.db.execute('SELECT * FROM drafts ORDER BY id,version').fetchall(),
            'reviews': audit.db.execute('SELECT * FROM review_events ORDER BY sequence').fetchall()})
        self.assertEqual(immutable_before, immutable_after)
        run = audit.db.execute('SELECT actor,tool,status,data_version FROM runs WHERE id=%s',
                               (result['run_id'],)).fetchone()
        self.assertEqual({'actor': 'user:dev-reader', 'tool': 'dashboard',
                          'status': 'succeeded', 'data_version': version}, run)

    def test_latest_draft_totals_do_not_count_revisions_or_truncate_at_one_hundred(self):
        draft = self.draft()
        revised = self.draft(key='revise', draft_id=draft['draft_id'], expected_version=1)
        audit = self.audit()
        # Bulk ledger fixture avoids 101 unrelated validation/approval runs. No user DB is used.
        for i in range(101):
            audit.db.execute('''INSERT INTO drafts SELECT %s,version,author,base_version,operations,
                content_hash,status,reviewer,review_note,applied_version,created_at FROM drafts
                WHERE id=%s AND version=2''', (f'dashboard-copy-{i:03}', revised['draft_id']))
        result = self.ok(self.http(self.alice, '/api/dashboard'))
        data = result['data']
        self.assertEqual(102, data['draft_total'])
        self.assertEqual({'awaiting_review': 102}, data['draft_counts'])
        self.assertEqual(100, data['draft_limit'])
        self.assertEqual(100, len(data['drafts']))
        self.assertTrue(all(row['version'] == 2 for row in data['drafts']))

    def test_http_identity_and_read_permission_are_resolved_on_every_dashboard_request(self):
        self.ok(self.http(self.reader, '/api/dashboard'))
        self.host._identities = DevelopmentIdentityProvider([
            UserIdentity('dev-alice', frozenset())], enabled=True)
        self.failed(self.http(self.alice, '/api/dashboard'), 403, 'FORBIDDEN')
        audit = self.audit()
        latest = audit.db.execute("SELECT actor,status,error_code FROM runs WHERE tool='dashboard' "
                                  'ORDER BY sequence DESC LIMIT 1').fetchone()
        self.assertEqual({'actor': 'user:dev-alice', 'status': 'failed', 'error_code': 'FORBIDDEN'}, latest)
        self.failed(self.http(self.alice, '/api/dashboard?actor=dev-bob'), 404, 'ROUTE_NOT_FOUND')
        self.failed(self.http(self.alice, '/api/dashboard', {}), 404, 'ROUTE_NOT_FOUND')

    def test_unpublished_selected_projection_fails_instead_of_returning_sql_or_zero_counts(self):
        self.host._graph_settings = GraphSettings(backend='neo4j', uri='bolt://127.0.0.1:1',
            user='test-only', password='test-only', namespace='dashboard_unpublished')
        # Missing publication is checked in real PG before any Neo4j network call.
        result = self.failed(self.http(self.alice, '/api/dashboard'), 503, 'GRAPH_NOT_READY')
        self.assertIsNone(result['data'])
        self.assertEqual([], result['evidence'])
        self.ok(self.http(self.alice, '/api/context'))
