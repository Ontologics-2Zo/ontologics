"""Hanbit fixture registration uses disposable common approval storage only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ontologics.database import environment_values
from ontologics.graph_neo4j import Neo4jProjection
from ontologics.graph_views import GraphSettings
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, digest
from ontologics.host import CommonHost
from ontologics.service import DomainError, Service
from tests.db import database_for_test
from ontologics.materials.business_case_graph import register_business_case


class BusinessCaseGraphTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.users = [UserIdentity('hanbit-author', frozenset({'read', 'draft'})),
                      UserIdentity('hanbit-reviewer', frozenset({'read', 'review'}))]
        self.provider = DevelopmentIdentityProvider(self.users, enabled=True)
        self.host = CommonHost(self.provider, self.database, graph_settings=GraphSettings())
        self.service = Service(self.database, principals={'seed': {'kind': 'human', 'roles': ['seed', 'read']}})
        self.addCleanup(self.service.close)
        self.before = json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        result = self.service.bootstrap('seed', self.before, request_key='seed-catalog')
        self.assertTrue(result['ok'], result)
        storage = ROOT/'data/receipts/materials'
        storage.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=storage)
        self.addCleanup(temporary.cleanup)
        self.storage = Path(temporary.name)
        for name, target in [('ontologics.materials.business_case_graph.STORAGE_ROOT', self.storage/'originals'),
                             ('ontologics.order_fulfillment.material_request_registration.STORAGE_ROOT', self.storage/'requests')]:
            override = patch(name, target)
            override.start()
            self.addCleanup(override.stop)

    def run_case(self, **kwargs):
        return register_business_case(self.host, author_subject='hanbit-author',
                                      reviewer_subject='hanbit-reviewer', **kwargs)

    def snapshot(self, version=None):
        with self.host.request(DevelopmentContext('hanbit-author')) as session:
            response = session.call('lookup', {} if version is None else {'version': version})
        self.assertTrue(response['ok'], response)
        return response

    def test_registration_keeps_180_contract_and_separate_60_request_without_production_facts(self):
        result = self.run_case(expected_version=1)
        self.assertEqual((1, 2, 3), (result['before_version'], result['baseline_version'], result['after_version']))
        self.assertEqual(8, result['after_nodes']-result['before_nodes'])
        self.assertEqual(60, result['quantity'])
        self.assertEqual('2026-11-27', result['need_date'])
        self.assertEqual('production_complete', result['need_basis'])
        after = self.snapshot()['data']
        index = {node['id']: node for node in after}
        self.assertEqual(180, index[result['contract_item_id']]['quantity'])
        self.assertEqual('주식회사 온톨로직스', index[result['contract_id']]['company'])
        self.assertEqual('한빛시설관리 주식회사', index[result['contract_id']]['customer_name'])
        original_ids = {node['id'] for node in self.before}
        added = [node for node in after if node['id'] not in original_ids]
        self.assertEqual({'Contract', 'ContractItem', 'SourceDocument', 'Evidence'}, {n['type'] for n in added})
        self.assertTrue(all(n['origin']=='synthetic' for n in added))
        self.assertEqual(digest(self.before), digest(self.snapshot(1)['data']))
        self.assertTrue(result['existing_nodes_unchanged'])
        self.assertTrue(result['development_review'])

    def test_repeated_registration_adds_no_version_or_nodes(self):
        first = self.run_case()
        repeated = self.run_case(expected_version=1)
        self.assertEqual((3, 3), (repeated['before_version'], repeated['after_version']))
        self.assertEqual(first['after_nodes'], repeated['after_nodes'])
        self.assertTrue(repeated['already_registered'])
        self.assertEqual(first['evidence_id'], repeated['evidence_id'])
        self.assertEqual(3, self.snapshot()['data_version'])

    def test_conflicting_existing_contract_is_refused_without_new_version(self):
        result = self.run_case()
        index = {node['id']: node for node in self.snapshot()['data']}
        changed = copy.deepcopy(index[result['contract_item_id']])
        changed['quantity'] = 181
        with self.host.request(DevelopmentContext('hanbit-author')) as session:
            draft = session.call('prepare_draft', {'operations': [{'op': 'put', 'node': changed}],
                                                  'request_key': 'different-contract'})
        self.assertTrue(draft['ok'], draft)
        draft = draft['data']
        with self.host.request(DevelopmentContext('hanbit-reviewer')) as session:
            reviewed = session.review(draft['draft_id'], draft['draft_version'], decision='approve',
                                      note='별도 충돌 검증 기록', request_key='different-review')
        self.assertTrue(reviewed['ok'], reviewed)
        applied = self.host.apply_approved(draft['draft_id'], draft['draft_version'], request_key='different-apply')
        self.assertTrue(applied['ok'], applied)
        with self.assertRaises(DomainError) as error:
            self.run_case()
        self.assertEqual('BUSINESS_CASE_CONFLICT', error.exception.code)
        self.assertEqual(4, self.snapshot()['data_version'])

    def test_separate_development_review_is_required_before_artifacts_are_written(self):
        with self.assertRaises(DomainError):
            register_business_case(self.host, author_subject='hanbit-author', reviewer_subject='hanbit-author')
        self.provider.mode = 'authenticated'
        with self.assertRaises(DomainError):
            self.run_case()
        self.assertEqual([], list(self.storage.rglob('*')))

    def test_optional_neo4j_host_publishes_exact_versions_without_prior_projection(self):
        values = environment_values()
        if not all(values.get(k) for k in ('TEST_NEO4J_URL', 'TEST_NEO4J_USER', 'TEST_NEO4J_PASSWORD')):
            self.skipTest('Explicit isolated Neo4j test connection is not configured')
        namespace = 'test_hanbit_'+uuid4().hex
        settings = GraphSettings(backend='neo4j', uri=values['TEST_NEO4J_URL'],
                                 user=values['TEST_NEO4J_USER'], password=values['TEST_NEO4J_PASSWORD'],
                                 namespace=namespace)
        projection_host = CommonHost(self.provider, self.database, graph_settings=settings)
        projection = Neo4jProjection(settings.uri, settings.user, settings.password, namespace=namespace)
        self.addCleanup(projection.close)
        def cleanup():
            with projection._driver.session(database=projection.database) as session:
                session.run('MATCH (n) WHERE n.namespace STARTS WITH $prefix DETACH DELETE n',
                            prefix=namespace+':').consume()
        self.addCleanup(cleanup)
        result = self.run_case(projection_host=projection_host, expected_version=1)
        self.assertTrue(result['projection_readback_equal'])
        with projection_host.request(DevelopmentContext('hanbit-author')) as session:
            baseline = session.call('lookup', {'version': 2})
            latest = session.call('lookup', {'version': 3})
        self.assertTrue(baseline['ok'], baseline)
        self.assertTrue(latest['ok'], latest)
        self.assertEqual(result['after_nodes']-2, len(baseline['data']))
        self.assertEqual(result['after_nodes'], len(latest['data']))
        repeated = self.run_case(projection_host=projection_host)
        self.assertTrue(repeated['already_registered'])
        self.assertEqual(3, repeated['after_version'])


if __name__ == '__main__':
    unittest.main()
