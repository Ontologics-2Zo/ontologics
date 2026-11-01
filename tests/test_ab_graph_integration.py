"""Real PostgreSQL approvals and RDF/Neo4j parity for the registered A/B types."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from examples.ab_graph_check import candidates, verify
from ontologics.demo import fixture_nodes, must
from ontologics.model import digest
from ontologics.service import Service
from tests.db import database_for_test
from tests.test_graph_integration import graph_settings, register_graph_cleanup


class BusinessGraphIntegrationTests(unittest.TestCase):
    def test_approved_ab_types_and_s1_results_match_all_backends(self):
        database = database_for_test(self)
        settings = graph_settings()
        service = Service(database, principals={}, graph_settings=settings)
        self.addCleanup(service.close)
        register_graph_cleanup(self, service)
        report = verify(database, settings)
        self.assertTrue(report['passed'], report['checks'])
        self.assertEqual(11, len(report['business_types']))
        self.assertEqual([1, 2, 3], report['data_versions'])

    def test_preexisting_core_draft_preserves_hash_and_exact_approval(self):
        database = database_for_test(self)
        principals = {'a': {'kind': 'human', 'roles': ['seed','read','draft','review']},
                      'b': {'kind': 'human', 'roles': ['read','review']},
                      'host': {'kind':'system','roles':['apply']}}
        service = Service(database, principals=principals)
        self.addCleanup(service.close)
        base, _ = candidates()
        operations = fixture_nodes()[1]
        # graph-0.1 responses and existing persisted draft format; no schema column,
        # migration, operation rewriting, approval resetting or new data version.
        with patch('ontologics.service.SCHEMA_VERSION', 'graph-0.1'):
            must(service.bootstrap('a', base, request_key='old-seed'))
            draft = must(service.prepare_draft('a', operations, request_key='old-draft'))['data']
            must(service.review('b', draft['draft_id'], 1, decision='approve', note='Existing exact approval', request_key='old-approval'))
        old = must(service.get_draft('a', draft['draft_id'], 1))['data']
        valid = must(service.validate_draft('a', draft['draft_id'], 1))['data']
        self.assertTrue(valid['validation']['valid'])
        self.assertEqual(digest(operations), old['content_hash'])
        self.assertEqual('approved', old['status'])
        applied = must(service.apply('host', draft['draft_id'], 1, request_key='after-schema-upgrade'))
        self.assertEqual(2, applied['data_version'])
        final = must(service.get_draft('a', draft['draft_id'], 1))['data']
        self.assertEqual(old['operations'], final['operations'])
        self.assertEqual(old['review_history'], final['review_history'])
        self.assertEqual(old['content_hash'], final['content_hash'])
