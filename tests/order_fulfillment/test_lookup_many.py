"""Bounded complete batch reads reuse confirmed snapshots, permission and audit."""
import copy
import unittest
from unittest.mock import Mock, patch

from ontologics.order_fulfillment.schema import LOOKUP_SCHEMA
from ontologics.service import DomainError, Service as CommonService
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.demo import PRINCIPALS, must
from ontologics.ingest import build_catalog
from ontologics.order_fulfillment.service import AgentGateway, Service
from ontologics.order_fulfillment.tool_specs import B_PRODUCTION_TOOLS, function_tools
from tests.db import database_for_test


class LookupManyContractTests(unittest.TestCase):
    """B input/dispatch checks only; real DB permissions and audit are separate."""
    def setUp(self):
        self.nodes = [dict(id='node:' + kind, type=kind) for kind in LOOKUP_SCHEMA]
        self.nodes.append(dict(id='evidence:second', type='Evidence'))
        self.service = object.__new__(Service)
        self.service._nodes = Mock(return_value=self.nodes)
        self.service._run = Mock(side_effect=lambda actor, tool, args, action: action())

    def error_is(self, code, operation):
        with self.assertRaises(DomainError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)

    def test_all_current_common_and_b_types_preserve_order_version_and_common_run_dispatch(self):
        before = copy.deepcopy(self.nodes)
        for kind in LOOKUP_SCHEMA:
            with self.subTest(kind=kind):
                ids = ['node:' + kind]
                result = self.service.lookup_many('fixed-reader', ids, kind, version=3)
                self.assertTrue(result['ok'])
                self.assertEqual(3, result['data_version'])
                self.assertEqual(ids, [node['id'] for node in result['data']])
                actor, tool, arguments, action = self.service._run.call_args.args
                self.assertEqual(('fixed-reader', 'lookup_many', dict(node_ids=ids, kind=kind, version=3)),
                                 (actor, tool, arguments))
                self.assertTrue(callable(action))
                self.assertEqual({}, self.service._run.call_args.kwargs)
        result = self.service.lookup_many('fixed-reader', ['evidence:second', 'node:Evidence'], 'Evidence', version=3)
        self.assertEqual(['evidence:second', 'node:Evidence'], [n['id'] for n in result['data']])
        self.assertEqual(before, self.nodes)
        self.assertIs(Service._run, CommonService._run)

    def test_invalid_versions_batches_and_kinds_fail_before_snapshot_read(self):
        for version in (None, True, -1, '1', 1.0):
            with self.subTest(version=version):
                self.error_is('INVALID_VERSION', lambda: self.service.lookup_many(
                    'fixed-reader', ['node:Evidence'], 'Evidence', version=version))
        for ids in ([], 'one', True, ['node:Evidence'] * 2, [None], [{}], [['nested']], [' '],
                    [str(i) for i in range(17)]):
            with self.subTest(ids=ids):
                self.error_is('INVALID_INPUT', lambda: self.service.lookup_many('fixed-reader', ids, 'Evidence', version=3))
        for kind in ('UnknownKind', [], None, True):
            with self.subTest(kind=kind):
                self.error_is('INVALID_INPUT', lambda: self.service.lookup_many(
                    'fixed-reader', ['node:Evidence'], kind, version=3))
        self.service._nodes.assert_not_called()
        ids = ['evidence:' + str(i) for i in range(16)]
        self.service._nodes.return_value = [dict(id=nid, type='Evidence') for nid in ids]
        self.assertEqual(16, len(self.service.lookup_many('fixed-reader', ids, 'Evidence', version=3)['data']))

    def test_missing_wrong_kind_and_snapshot_errors_never_return_partial_data(self):
        with patch.object(self.service, '_envelope', wraps=self.service._envelope) as envelope:
            for ids in (['node:Evidence', 'missing'], ['node:Evidence', 'node:Contract']):
                with self.subTest(ids=ids):
                    self.error_is('NOT_FOUND', lambda: self.service.lookup_many('fixed-reader', ids, 'Evidence', version=3))
            self.service._nodes.side_effect = DomainError('VERSION_NOT_FOUND', 'Test snapshot is absent')
            self.error_is('VERSION_NOT_FOUND', lambda: self.service.lookup_many(
                'fixed-reader', ['node:Evidence'], 'Evidence', version=999))
            envelope.assert_not_called()

    def test_inherited_gateway_keeps_bound_actor_and_existing_tool_boundaries(self):
        gateway = AgentGateway(self.service, 'bound-reader', allowed_tools=('lookup_many',))
        args = dict(node_ids=['node:Evidence'], kind='Evidence', version=3)
        self.assertTrue(gateway.call('lookup_many', args)['ok'])
        self.assertEqual('bound-reader', self.service._run.call_args.args[0])
        for authority in ({'actor': 'seed-admin'}, {'principals': {}}, {'unexpected': True}):
            with self.subTest(authority=authority):
                self.error_is('INVALID_TOOL_ARGUMENTS', lambda: gateway.call('lookup_many', {**args, **authority}))
        restricted = AgentGateway(self.service, 'bound-reader', allowed_tools=B_PRODUCTION_TOOLS)
        self.error_is('TOOL_NOT_ALLOWED', lambda: restricted.call('lookup_many', args))
        for privileged in ('review', 'apply', 'bootstrap'):
            with self.subTest(tool=privileged), self.assertRaises(ValueError):
                AgentGateway(self.service, 'bound-reader', allowed_tools=(privileged,))


class LookupManyTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals={**PRINCIPALS,
            'reader': {'kind': 'human', 'roles': ['read']},
            'nonreader': {'kind': 'human', 'roles': ['draft']}})
        self.addCleanup(self.service.close)
        self.nodes = build_catalog()['nodes']
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
        nodes = [n for n in load_demo_delivery() if n['type'] in ('SourceDocument', 'Evidence')]
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
