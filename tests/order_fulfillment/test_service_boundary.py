"""Offline B dispatch/authority tests; PostgreSQL transactions are tested separately."""
import copy
import json
import unittest

from ontologics.model import ROOT
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.service import AgentGateway, DomainError, Service


B_TOOLS = frozenset({'lookup_many', 'delivery_round', 'fulfillment_status',
                     'production_completion', 'delivery_forecast', 'response_plan',
                     'compare_response_plan'})


class _SnapshotService(Service):
    """In-memory action harness, deliberately not a database or approval substitute."""
    def __init__(self, nodes):
        self.nodes = tuple(copy.deepcopy(nodes))
        self.principals = {'reader': {'kind': 'agent', 'roles': ['read']},
                           'writer': {'kind': 'agent', 'roles': ['draft']}}
        self.calls = []

    def _version(self):
        return 7

    def _nodes(self, version=None):
        if version not in (None, 7):
            raise DomainError('VERSION_NOT_FOUND', 'Unknown test snapshot')
        return self.nodes

    def _run(self, actor, tool, args, action, *, permission='read', request_key=None):
        self.calls.append((actor, tool, copy.deepcopy(args), permission, request_key))
        try:
            self._require(actor, permission)
            return action()
        except DomainError as exc:
            return self._envelope(ok=False, error={'code': exc.code, 'message': exc.message})


class ServiceDispatchTests(unittest.TestCase):
    def setUp(self):
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        self.service = _SnapshotService(catalog + load_demo_contract())
        self.before = copy.deepcopy(self.service.nodes)
        self.expected = json.loads((ROOT / 'data/order_fulfillment/b_contract_expected.json').read_text())
        self.gateway = AgentGateway(self.service, 'reader', allowed_tools={'delivery_round', 'lookup_many'})

    def tearDown(self):
        self.assertEqual(self.before, self.service.nodes)

    def assert_error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'])

    def promise(self, **extra):
        args = {'delivery_round_id': self.expected['delivery_round_id'], 'version': 7}
        args.update(extra)
        return self.gateway.call('delivery_round', args)

    def test_cq01_promise_keeps_exact_chain_version_and_not_assessed_warning(self):
        result = self.promise()
        self.assertTrue(result['ok'], result)
        self.assertEqual(7, result['data_version'])
        data = result['data']
        self.assertEqual({'contract', 'contract_item', 'delivery_round', 'configuration',
                          'revision', 'product', 'fulfillment_assessed'}, set(data))
        self.assertEqual(self.expected['contract_id'], data['contract']['id'])
        self.assertEqual(self.expected['contract_item_id'], data['contract_item']['id'])
        self.assertEqual(self.expected['configuration_id'], data['configuration']['id'])
        self.assertEqual(self.expected['quantity'], data['delivery_round']['quantity'])
        self.assertIs(False, data['fulfillment_assessed'])
        self.assertEqual(['FULFILLMENT_NOT_ASSESSED'], [w['code'] for w in result['warnings']])
        actor, tool, args, permission, request_key = self.service.calls[-1]
        self.assertEqual(('reader', 'delivery_round', 'read', None), (actor, tool, permission, request_key))
        self.assertEqual({'delivery_round_id': self.expected['delivery_round_id'], 'version': 7}, args)

    def test_cq01_rejects_invalid_versions_and_rounds(self):
        for bad in (None, True, False, -1, '7', 7.0, [], {}):
            with self.subTest(version=bad):
                self.assert_error(self.promise(version=bad), 'INVALID_VERSION')
        for bad in ('', ' ', None, 3, [], {}):
            with self.subTest(round=bad):
                self.assert_error(self.promise(delivery_round_id=bad), 'INVALID_INPUT')
        self.assert_error(self.promise(delivery_round_id='round:not-found'), 'NOT_FOUND')
        self.assert_error(self.promise(delivery_round_id=self.expected['contract_item_id']), 'NOT_FOUND')
        self.assert_error(self.promise(version=8), 'VERSION_NOT_FOUND')

    def test_gateway_preserves_host_identity_and_rejects_extra_arguments(self):
        for key in ('actor', 'principals'):
            with self.subTest(key=key):
                self.assert_error(self.promise(**{key: 'writer'}), 'INVALID_TOOL_ARGUMENTS')
        self.assert_error(self.promise(unexpected=True), 'INVALID_TOOL_ARGUMENTS')
        self.assert_error(self.gateway.call('delivery_round', []), 'INVALID_TOOL_ARGUMENTS')
        self.assert_error(self.gateway.call('delivery_round', {}), 'INVALID_TOOL_ARGUMENTS')
        self.assertTrue(all(row[0] == 'reader' for row in self.service.calls))

    def test_b_tool_still_requires_existing_read_permission(self):
        for actor in ('unknown', 'writer'):
            with self.subTest(actor=actor):
                gateway = AgentGateway(self.service, actor, allowed_tools={'delivery_round'})
                self.assert_error(gateway.call('delivery_round', {
                    'delivery_round_id': self.expected['delivery_round_id'], 'version': 7}), 'FORBIDDEN')

    def test_gateway_rejects_approval_mutations_and_unselected_b_tools(self):
        for tool in ('review', 'apply', 'bootstrap', 'response_plan', 'fulfillment_status'):
            with self.subTest(tool=tool):
                self.assert_error(self.gateway.call(tool, {}), 'TOOL_NOT_ALLOWED')
        for tools in ({'review'}, {'apply'}, {'unknown'}, set()):
            with self.subTest(tools=tools):
                with self.assertRaises(ValueError):
                    AgentGateway(self.service, 'reader', allowed_tools=tools)

    def test_batch_preserves_input_order_and_rejects_partial_or_wrong_kind(self):
        ids = [self.expected['contract_item_id'], self.expected['contract_id']]
        first = self.gateway.call('lookup_many', {'node_ids': [ids[0]], 'kind': 'ContractItem', 'version': 7})
        self.assertTrue(first['ok'], first)
        self.assertEqual([ids[0]], [n['id'] for n in first['data']])
        # Both model configurations share one known kind, returned in caller order.
        configs = [n['id'] for n in self.service.nodes if n['type'] == 'Configuration'][:2]
        configs.reverse()
        found = self.gateway.call('lookup_many', {'node_ids': configs, 'kind': 'Configuration', 'version': 7})
        self.assertTrue(found['ok'], found)
        self.assertEqual(configs, [n['id'] for n in found['data']])
        for node_ids, kind in ((ids, 'ContractItem'), ([ids[0], 'missing'], 'ContractItem')):
            result = self.gateway.call('lookup_many', {'node_ids': node_ids, 'kind': kind, 'version': 7})
            self.assert_error(result, 'NOT_FOUND')
            self.assertIsNone(result['data'])

    def test_batch_rejects_bad_collection_kind_and_version(self):
        cid = self.expected['contract_id']
        for ids in ([], [cid, cid], [''], [True], cid, {}, [cid] * 17):
            with self.subTest(ids=ids):
                self.assert_error(self.gateway.call('lookup_many', {
                    'node_ids': ids, 'kind': 'Contract', 'version': 7}), 'INVALID_INPUT')
        for kind in ('unknown', None, [], {}):
            with self.subTest(kind=kind):
                self.assert_error(self.gateway.call('lookup_many', {
                    'node_ids': [cid], 'kind': kind, 'version': 7}), 'INVALID_INPUT')
        for version in (None, True, -1, '7', 7.0):
            with self.subTest(version=version):
                self.assert_error(self.gateway.call('lookup_many', {
                    'node_ids': [cid], 'kind': 'Contract', 'version': version}), 'INVALID_VERSION')


class SharedBoundaryInheritanceTests(unittest.TestCase):
    def test_database_authority_audit_and_approval_implementations_are_inherited(self):
        from ontologics.service import Service as CommonService
        for method in ('__init__', '_run', '_nodes', '_envelope', '_require',
                       'bootstrap', 'prepare_draft', 'validate_draft', 'review', 'apply'):
            with self.subTest(method=method):
                self.assertIs(getattr(CommonService, method), getattr(Service, method))
        self.assertEqual(B_TOOLS, {name for name in Service.__dict__ if not name.startswith('__')})

    def test_gateway_dispatch_and_identity_checks_are_inherited(self):
        from ontologics.service import AgentGateway as CommonGateway
        self.assertIs(CommonGateway.call, AgentGateway.call)
        self.assertIs(CommonGateway.__init__, AgentGateway.__init__)
        self.assertEqual(CommonGateway.ALLOWED | B_TOOLS, AgentGateway.ALLOWED)
        self.assertTrue({'review', 'apply', 'bootstrap'}.isdisjoint(AgentGateway.ALLOWED))
