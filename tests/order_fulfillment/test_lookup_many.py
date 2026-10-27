"""Bounded complete batch reads reuse confirmed snapshots, permission and audit."""
import unittest

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.demo import PRINCIPALS, must
from ontologics.ingest import build_catalog
from ontologics.order_fulfillment.service import AgentGateway, Service
from ontologics.order_fulfillment.tool_specs import B_PRODUCTION_TOOLS, function_tools
from tests.db import database_for_test


class LookupManyTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals={**PRINCIPALS,
            'reader': {'kind': 'human', 'roles': ['read']},
            'nonreader': {'kind': 'human', 'roles': ['draft']}})
        self.addCleanup(self.service.close)
        self.nodes = build_catalog()['nodes'] + load_demo_contract()
        self.version = must(self.service.bootstrap('seed-admin', self.nodes, request_key='batch-seed'))['data_version']
        self.ids = [n['id'] for n in self.nodes if n['type'] == 'Evidence']
        self.assertGreaterEqual(len(self.ids), 16)

    def query(self, ids=None, *, kind='Evidence', version=1, actor='reader'):
        return self.service.lookup_many(actor, self.ids[:2] if ids is None else ids, kind, version=version)

    def error_is(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)
        self.assertIsNone(result['data'])

    def test_exact_version_order_and_nodes_match_single_reads_without_graph_writes(self):
        before = must(self.service.lookup('reader', version=self.version))['data']
        ids = list(reversed(self.ids[:16]))
        result = must(self.query(ids))
        self.assertEqual(self.version, result['data_version'])
        self.assertEqual(ids, [n['id'] for n in result['data']])
        self.assertEqual([must(self.service.lookup('reader', node_id=nid, version=self.version))['data'][0]
                          for nid in ids], result['data'])
        self.assertEqual(before, must(self.service.lookup('reader', version=self.version))['data'])
        row = self.service.db.execute('SELECT actor,tool,status,data_version FROM runs WHERE id=%s',
                                      (result['run_id'],)).fetchone()
        self.assertEqual(('reader', 'lookup_many', 'succeeded', self.version), tuple(row.values()))

    def test_malformed_duplicate_and_overlimit_batches_fail_without_partial_data(self):
        for ids in ([], 'one', True, [self.ids[0], self.ids[0]], [None], [{}], [['nested']], [' '],
                    [str(i) for i in range(17)]):
            with self.subTest(ids=ids):
                self.error_is(self.query(ids), 'INVALID_INPUT')

    def test_missing_and_wrong_kind_nodes_fail_whole_batch(self):
        self.error_is(self.query([self.ids[0], 'missing:id']), 'NOT_FOUND')
        self.error_is(self.query(self.ids[:2], kind='SourceDocument'), 'NOT_FOUND')
        for kind in ('UnknownKind', [], None, True):
            with self.subTest(kind=kind):
                self.error_is(self.query(kind=kind), 'INVALID_INPUT')

    def test_version_is_explicit_and_not_silently_replaced(self):
        for version in (None, True, -1, '1', 1.0):
            with self.subTest(version=version):
                self.error_is(self.query(version=version), 'INVALID_VERSION')
        self.error_is(self.query(version=999), 'VERSION_NOT_FOUND')
        self.error_is(self.service.lookup_many('reader', self.ids[:2], 'Evidence'), 'INVALID_VERSION')

    def test_nonreader_is_denied_before_lookup_and_audit_uses_request_actor(self):
        result = self.query(actor='nonreader')
        self.assertFalse(result['ok'])
        row = self.service.db.execute('SELECT actor,status FROM runs WHERE id=%s', (result['run_id'],)).fetchone()
        self.assertEqual('nonreader', row['actor'])
        self.assertEqual('failed', row['status'])

    def test_pending_nodes_hidden_and_historical_version_stays_unchanged_after_apply(self):
        nodes = load_demo_delivery()
        evidence_id = next(n['id'] for n in nodes if n['type'] == 'Evidence')
        draft = must(self.service.prepare_draft('product-agent', [{'op': 'put', 'node': n} for n in nodes],
                                                request_key='batch-draft'))['data']
        self.error_is(self.query([evidence_id]), 'NOT_FOUND')
        must(self.service.review('reviewer', draft['draft_id'], draft['draft_version'], decision='approve',
                                 note='Synthetic batch lookup test fixture', request_key='batch-review'))
        version = must(self.service.apply('workflow', draft['draft_id'], draft['draft_version'],
                                         request_key='batch-apply'))['data_version']
        self.error_is(self.query([evidence_id], version=self.version), 'NOT_FOUND')
        self.assertEqual(evidence_id, must(self.query([evidence_id], version=version))['data'][0]['id'])

    def test_gateway_identity_and_existing_role_scope_remain_bound(self):
        gateway = AgentGateway(self.service, 'reader', allowed_tools=('lookup_many',))
        args = dict(node_ids=self.ids[:2], kind='Evidence', version=self.version)
        self.assertTrue(gateway.call('lookup_many', args)['ok'])
        self.error_is(gateway.call('lookup_many', {**args, 'actor': 'seed-admin'}), 'INVALID_TOOL_ARGUMENTS')
        self.error_is(gateway.call('lookup_many', {**args, 'unexpected': True}), 'INVALID_TOOL_ARGUMENTS')
        existing = AgentGateway(self.service, 'reader', allowed_tools=B_PRODUCTION_TOOLS)
        self.error_is(existing.call('lookup_many', args), 'TOOL_NOT_ALLOWED')

    def test_provider_schema_requires_bounded_ids_kind_and_version(self):
        spec = function_tools(('lookup_many',))[0]
        self.assertEqual({'node_ids', 'kind', 'version'}, set(spec['parameters']['required']))
        ids = spec['parameters']['properties']['node_ids']
        self.assertEqual((1, 16), (ids['minItems'], ids['maxItems']))
        self.assertFalse(spec['parameters']['additionalProperties'])
