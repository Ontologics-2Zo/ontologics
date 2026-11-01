"""Approved PostgreSQL source -> RDF validation -> Neo4j -> real HTTP workflows.

Disposable stores, public OLIMEX originals, fictional operating records, and
scripted models. No live-model accuracy or production authentication claim.
"""
from __future__ import annotations

import base64
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import psycopg

from examples.graph_cq_check import GRAPH_PRINCIPALS, verify
from ontologics.demo import CONFIG_L, CONFIG_L1, example_queries, fixture_nodes, must, run_demo
from ontologics.graph_views import GraphSettings
from ontologics.graph_neo4j import Neo4jProjection, ProjectionError
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentIdentityProvider
from ontologics.model import ROOT, SCHEMA, SCHEMA_VERSION, digest
from ontologics.service import AgentGateway, RULE_VERSION, Service
from ontologics.web import USERS, seed_development
from tests.db import database_for_test
from tests.test_agent_workflows import ScriptedReceipt
from tests.test_web import ScriptedQuality, WebCase


def graph_settings():
    return replace(GraphSettings.from_environment(backend='neo4j'), namespace='integration_test')


def register_graph_cleanup(test, service):
    namespace = service.graph_views.namespace
    projection = service.graph_views.projection()
    assert service.schema.startswith('test_')
    def clean():
        with projection._driver.session(database=projection.database) as session:
            session.run('MATCH (n) WHERE n.namespace=$namespace DETACH DELETE n', namespace=namespace).consume()
    test.addCleanup(clean)


class GraphServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.settings = graph_settings()
        self.service = Service(self.database, principals=GRAPH_PRINCIPALS, graph_settings=self.settings)
        self.addCleanup(self.service.close)
        register_graph_cleanup(self, self.service)

    def test_same_supported_cqs_match_after_actual_source_registration_and_supplement(self):
        report = verify(self.database, graph_settings=self.settings)
        self.assertTrue(report['passed'], [k for k, passed in report['checks'].items() if not passed])
        self.assertEqual([2, 3], report['synchronized_versions'])
        self.assertEqual('proposed_not_implemented', report['CQ09']['status'])
        self.assertIn('no cross-revision', report['CQ10_scope'])

    def test_publication_gate_failure_retry_permissions_and_store_isolation(self):
        run_demo(self.database)
        svc = self.service
        def failure(result, code):
            self.assertFalse(result['ok'], result)
            self.assertEqual(code, result['errors'][0]['code'], result)
        failure(svc.configuration('product-agent', CONFIG_L1, version=2), 'GRAPH_NOT_READY')
        failure(svc.graph_views.synchronize('product-agent', 2, request_key='unauthorized'), 'FORBIDDEN')
        failure(AgentGateway(svc, 'product-agent').call('synchronize_graph', {'version': 2}), 'TOOL_NOT_ALLOWED')
        ready = must(svc.graph_views.synchronize('graph-indexer', 2, request_key='sync-2'))
        self.assertEqual(2, ready['data_version'])
        replay = must(svc.graph_views.synchronize('graph-indexer', 2, request_key='sync-2'))
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['warnings']])
        self.assertTrue(svc.configuration('product-agent', CONFIG_L1, version=2)['ok'])
        # Latest is v3. An available older projection must not be substituted.
        failure(svc.configuration('product-agent', CONFIG_L1), 'GRAPH_NOT_READY')

        class MarkerFailure:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def execute(self, statement, *args, **kwargs):
                if str(statement).lstrip().startswith('INSERT INTO graph_projections'):
                    raise psycopg.OperationalError('Injected publication marker failure')
                return self.connection.execute(statement, *args, **kwargs)

        with patch.object(svc, 'db', MarkerFailure(svc.db)):
            failure(svc.graph_views.synchronize('graph-indexer', 3, request_key='sync-3-failure'), 'STORAGE_ERROR')
        nodes = svc._nodes(3)
        # Remote success alone cannot publish a version through the service.
        remote = svc.graph_views.projection().for_version(version=3, content_hash=digest(nodes),
            schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)
        self.assertEqual(nodes, remote.nodes())
        failure(svc.configuration('product-agent', CONFIG_L1, version=3), 'GRAPH_NOT_READY')
        must(svc.graph_views.synchronize('graph-indexer', 3, request_key='sync-3-retry'))
        self.assertTrue(svc.configuration('product-agent', CONFIG_L1, version=3)['ok'])
        self.assertEqual(3, svc._version())
        svc.db.execute('UPDATE graph_projections SET rule_version=%s WHERE data_version=3', ('incompatible',))
        failure(svc.configuration('product-agent', CONFIG_L1, version=3), 'GRAPH_VERSION_CONFLICT')
        must(svc.graph_views.synchronize('graph-indexer', 3, request_key='restore-marker'))

        other_database = database_for_test(self)
        run_demo(other_database)
        with_other_schema = Service(other_database, principals=GRAPH_PRINCIPALS, graph_settings=self.settings)
        self.addCleanup(with_other_schema.close)
        self.assertNotEqual(svc.graph_views.namespace, with_other_schema.graph_views.namespace)
        failure(with_other_schema.configuration('product-agent', CONFIG_L1, version=2), 'GRAPH_NOT_READY')
        with_other_namespace = Service(self.database, principals=GRAPH_PRINCIPALS,
            graph_settings=replace(self.settings, namespace='other_test'))
        self.addCleanup(with_other_namespace.close)
        failure(with_other_namespace.configuration('product-agent', CONFIG_L1, version=2), 'GRAPH_NOT_READY')


class GraphHttpWorkflowTests(WebCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.settings = graph_settings()
        self.assertTrue(seed_development(self.database, reserve_l1=True)['ok'])
        self.indexer = Service(self.database, principals=GRAPH_PRINCIPALS, graph_settings=self.settings)
        self.addCleanup(self.indexer.close)
        register_graph_cleanup(self, self.indexer)
        must(self.indexer.graph_views.synchronize('graph-indexer', 1, request_key='http-seed'))
        self.host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), self.database,
                               graph_settings=self.settings)
        self.clients = {}
        self.alice, self.bob, self.reader = (self.start(user) for user in ('alice', 'bob', 'reader'))
        _, self.operations = fixture_nodes()

    def assert_approved_display(self, version):
        stored = self.indexer.db.execute('SELECT nodes,reason FROM snapshots WHERE version=%s', (version,)).fetchone()
        self.assertTrue(stored['reason'].startswith('approved:'))
        nodes = stored['nodes']
        self.assertEqual(nodes, self.indexer.graph_views.reader(version).nodes())
        projection = self.indexer.graph_views.projection()
        with projection._driver.session(database=projection.database) as session:
            rows = session.run('MATCH (n:OntologicsEntity {namespace:$namespace,version:$version}) '
                'RETURN labels(n) AS labels, properties(n) AS node ORDER BY n.ordinal',
                namespace=self.indexer.graph_views.namespace, version=version).data()
        self.assertEqual(len(nodes), len(rows))
        for expected, row in zip(nodes, rows):
            self.assertEqual(expected, json.loads(row['node']['payload']))
            self.assertEqual({expected['type']}, set(row['labels']) & set(SCHEMA))
            self.assertTrue(row['node']['browser_caption'].strip())
            self.assertEqual(expected['origin'] == 'synthetic', '[가상]' in row['node']['browser_caption'])
        return {r['node']['id']: {'labels': sorted(r['labels']), 'caption': r['node']['browser_caption']}
                for r in rows}

    def test_l1_receipt_and_u104_supplement_keep_approval_versions_and_agent_query_results(self):
        status, html, _ = self.http(self.alice, '/')
        self.assertEqual(200, status)
        self.assertIn('AI 업무 지원'.encode(), html)
        path = ROOT / 'data/sources/ESP32-PoE_Rev_L1-BOM-WROOM.ods'
        receipt = self.ok(self.http(self.alice, '/api/sources/intake',
            {'filename': path.name, 'content_base64': base64.b64encode(path.read_bytes()).decode()}))
        self.assertEqual(12, receipt['data']['unknown_count'])
        comparison = self.ok(self.http(self.alice, '/api/sources/compare',
            {'receipt_id': receipt['data']['receipt_id'], 'left_id': CONFIG_L}))
        self.assertTrue(comparison['data']['is_preview'])
        self.alice.app.provider_factory = lambda names: ScriptedReceipt()
        drafted = self.ok(self.http(self.alice, '/api/assistant', {
            'screen': 'product', 'version': receipt['data_version'],
            'context': {'receipt_id': receipt['data']['receipt_id']}, 'action': 'prepare_draft',
            'request_key': 'http-product', 'question': '접수한 원본 근거와 미확인 상태를 유지해서 등록 초안을 저장해줘.'}))
        self.assertEqual('product', drafted['data']['agent_result']['agent_name'])
        registration = drafted['data']['draft']
        self.failed(self.http(self.alice, '/api/review', self.review_body(registration)), 403, 'SELF_APPROVAL_FORBIDDEN')
        registered = self.ok(self.http(self.bob, '/api/review', self.review_body(registration, key='register-l1')))
        self.assertEqual(2, registered['data']['application']['data_version'])
        self.assertEqual('ready', registered['data']['application']['data']['graph_projection']['status'])
        display_v2 = self.assert_approved_display(2)
        self.assertIn('Configuration', display_v2[CONFIG_L1]['labels'])
        self.assertIn('L1', display_v2[CONFIG_L1]['caption'])
        config = self.ok(self.tool('configuration', {'configuration_id': CONFIG_L1, 'version': 2}))
        self.assertEqual(74, len(config['data']['items']))
        self.assertTrue(all(n['origin'] == 'real' for n in config['data']['items']))

        queries = example_queries()
        before = {m: self.ok(self.tool('investigate', {'query': q, 'version': 2})) for m, q in queries.items()}
        draft = self.draft('http-supplement')
        valid = self.ok(self.tool('validate_draft', {'draft_id': draft['draft_id'], 'version': draft['draft_version']}))
        self.assertTrue(valid['data']['validation']['valid'])
        self.failed(self.http(self.alice, '/api/review', self.review_body(draft)), 403, 'SELF_APPROVAL_FORBIDDEN')
        self.failed(self.http(self.reader, '/api/review', self.review_body(draft)), 403, 'FORBIDDEN')
        self.ok(self.http(self.bob, '/api/review', self.review_body(draft, 'request_changes', 'need-details')))
        revised = self.draft('http-revised', draft_id=draft['draft_id'], expected_version=1)
        self.failed(self.http(self.bob, '/api/review', self.review_body(draft, key='old-version')), 409, 'SUPERSEDED_DRAFT')
        body = self.review_body(revised, key='apply-exact')
        with patch.object(Neo4jProjection, 'sync', side_effect=ProjectionError('GRAPH_UNAVAILABLE', 'Injected outage')):
            approved = self.ok(self.http(self.bob, '/api/review', body))
        application = approved['data']['application']
        self.assertEqual(3, application['data_version'])
        self.assertEqual(2, application['data']['draft_version'])
        self.assertEqual('pending', application['data']['graph_projection']['status'])
        self.assertIn('GRAPH_PUBLICATION_PENDING', [w['code'] for w in application['warnings']])
        self.failed(self.tool('investigate', {'query': queries['A']}), 503, 'GRAPH_NOT_READY')
        self.assertEqual(3, self.indexer._version())
        self.assertEqual(200, self.http(self.alice, '/api/context')[0])
        replay = self.ok(self.http(self.bob, '/api/review', body))
        self.assertEqual(3, replay['data']['application']['data_version'])
        self.assertEqual('ready', replay['data']['application']['data']['graph_projection']['status'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['data']['application']['warnings']])
        display_v3 = self.assert_approved_display(3)
        self.assertEqual(display_v2, self.assert_approved_display(2))
        new_coverage = next(op['node']['id'] for op in self.operations
                            if op['node']['type'] == 'HistoryCoverage' and op['node']['unit_id'] == 'unit:U104')
        self.assertNotIn(new_coverage, display_v2)
        self.assertIn('HistoryCoverage', display_v3[new_coverage]['labels'])
        self.assertIn('[가상]', display_v3[new_coverage]['caption'])
        self.assertIn('U104', display_v3[new_coverage]['caption'])
        direct = {}
        for mode, query in queries.items():
            direct[mode] = self.ok(self.tool('compare_investigation', {'query': query, 'before_version': 2, 'after_version': 3}))
            data = direct[mode]['data']
            self.assertEqual(query, data['query'])
            self.assertEqual(before[mode]['data'], data['before'])
            self.assertEqual('excluded' if mode == 'A' else 'included', data['after']['results'][0]['status'])
            self.assertEqual('needs_confirmation', data['after']['results'][2]['status'])
            self.assertEqual([], data['after']['results'][2]['evidence'])
            self.assertIn('evidence:demo:supplement', direct[mode]['evidence'])
        self.alice.app.provider_factory = lambda names: ScriptedQuality()
        ai = self.ok(self.http(self.alice, '/api/quality', {'queries': queries, 'version': 3,
            'before_version': 2, 'question': '동일 A/B 조건으로 보완 전후 결과와 근거를 설명해줘.'}))['data']
        self.assertEqual('matched', ai['consistency'])
        self.assertEqual('quality', ai['agent_result']['agent_name'])
        for mode in queries:
            self.assertEqual(direct[mode]['data'], ai['results'][mode]['data'])
            self.assertEqual(direct[mode]['evidence'], ai['results'][mode]['evidence'])
            self.assertEqual(3, ai['results'][mode]['data_version'])
        source = self.ok(self.tool('lookup', {'node_id': 'source:demo:operations', 'version': 3}))
        self.assertEqual('synthetic', source['data'][0]['origin'])
        self.assertEqual(3, self.indexer._version())


if __name__ == '__main__':
    unittest.main()
