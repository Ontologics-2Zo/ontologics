"""CQ05 read-only facade and authorization checks using disposable PostgreSQL."""
import copy
import hashlib
import json
import unittest

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.demo import PRINCIPALS
from ontologics.order_fulfillment.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, SCHEMA_VERSION
from ontologics.order_fulfillment.service import AgentGateway, Service
from ontologics.order_fulfillment.tool_specs import (B_DELIVERY_TOOLS, B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS,
    B_PRODUCTION_TOOLS, KNOWLEDGE_TOOLS, PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS,
    function_tools)
from tests.db import database_for_test


class ResponsePlanServiceTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        principals = copy.deepcopy(PRINCIPALS)
        principals['response-reader'] = {'kind': 'human', 'roles': ['read']}
        self.service = Service(self.database, principals=principals)
        self.addCleanup(self.service.close)
        nodes = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        result = self.service.bootstrap('seed-admin', nodes, request_key='response-base')
        self.assertTrue(result['ok'], result)
        self.before = result['data_version']
        self.gateway = AgentGateway(self.service, 'response-reader', allowed_tools={'response_plan', 'lookup'})
        self.candidates = load_demo_delivery()

    def arguments(self, version, **changes):
        result = dict(delivery_round_id=ROUND, as_of=AS_OF, version=version)
        result.update(changes)
        return result

    def query(self, version, **changes):
        return self.gateway.call('response_plan', self.arguments(version, **changes))

    def error_is(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def draft(self, key='response-draft'):
        result = self.service.prepare_draft('product-agent', [{'op': 'put', 'node': n} for n in self.candidates],
                                            request_key=key)
        self.assertTrue(result['ok'], result)
        return result['data']

    def register(self, mixed=False):
        if mixed:
            self.candidates = load_demo_delivery(mixed=True)
        draft = self.draft()
        self.assertTrue(draft['validation']['valid'], draft)
        return self.approve_apply(draft)

    def approve_apply(self, draft):
        did, dv = draft['draft_id'], draft['draft_version']
        reviewed = self.service.review('reviewer', did, dv, decision='approve',
            note='Scripted test human checked synthetic response inputs; no operational approval.', request_key='response-review')
        self.assertTrue(reviewed['ok'], reviewed)
        result = self.service.apply('workflow', did, dv, request_key='response-apply')
        self.assertTrue(result['ok'], result)
        return result['data_version']

    def counts(self):
        return {table: self.service.db.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n']
                for table in ('snapshots', 'drafts', 'review_events')}

    def test_D22_pending_invisible_and_exact_approved_version_preserved(self):
        draft = self.draft()
        before = self.query(self.before)
        self.assertTrue(before['ok'], before)
        self.assertEqual('needs_confirmation', before['data']['response_state'])
        self.assertIsNone(before['data']['dispatch_request']['new_dispatch_quantity'])
        self.error_is(self.service.apply('workflow', draft['draft_id'], draft['draft_version'],
                                        request_key='response-before-review'), 'APPROVAL_REQUIRED')
        version = self.approve_apply(draft)
        result = self.query(version)
        self.assertTrue(result['ok'], result)
        self.assertEqual((version, SCHEMA_VERSION, __version__, 'temporal-0.1'),
            (result['data_version'], result['schema_version'], result['contract_version'], result['rule_version']))
        self.assertEqual('response-0.1', result['data']['response_rule_version'])
        self.assertEqual('delivery-0.1', result['data']['forecast']['forecast_rule_version'])
        self.assertEqual((960, 40), (result['data']['forecast']['on_time_quantity'],
                                    result['data']['forecast']['shortfall_quantity']))
        self.assertEqual('needs_confirmation', self.query(self.before)['data']['response_state'])

    def test_D21_D22_D23_explicit_binding_and_argument_boundary(self):
        version = self.register()
        for value in (None, True, False, -1, '2', 2.0, [], {}):
            with self.subTest(version=value): self.error_is(self.query(value), 'INVALID_VERSION')
        self.error_is(self.query(version + 100), 'VERSION_NOT_FOUND')
        for rid in ('missing:round', 'contract:demo:B-01'):
            self.error_is(self.query(version, delivery_round_id=rid), 'NOT_FOUND')
        for rid in ('', ' ', None, [], {}):
            self.error_is(self.query(version, delivery_round_id=rid), 'INVALID_INPUT')
        for stamp in ('2026-10-27', '2026-10-27T10:00:00', None, [], {}):
            self.error_is(self.query(version, as_of=stamp), 'INVALID_TIME')
        for field in ('delivery_round_id', 'as_of'):
            args = self.arguments(version); args.pop(field)
            self.error_is(self.gateway.call('response_plan', args), 'INVALID_TOOL_ARGUMENTS')
        args = self.arguments(version); args.pop('version')
        self.error_is(self.gateway.call('response_plan', args), 'INVALID_VERSION')
        for injected in ({'actor': 'reviewer'}, {'principals': {}}, {'forecast': {'on_time_quantity': 1000}}):
            self.error_is(self.gateway.call('response_plan', dict(self.arguments(version), **injected)),
                          'INVALID_TOOL_ARGUMENTS')
        self.error_is(AgentGateway(self.service, 'unknown').call('response_plan', self.arguments(version)), 'FORBIDDEN')

    def test_D08_D25_D27_D28_read_creates_no_registered_or_approved_work(self):
        version = self.register(mixed=True)
        graph = self.service.lookup('response-reader', version=version)['data']
        counts = self.counts()
        for _ in range(2):
            result = self.query(version)
            self.assertTrue(result['ok'], result)
            data = result['data']; request = data['dispatch_request']
            self.assertEqual((100, 150, 750, 750, 0, 450, 300), tuple(request[k] for k in (
                'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity',
                'on_time_new_dispatch_quantity', 'later_new_dispatch_quantity',
                'currently_available_quantity', 'future_quantity')))
            self.assertEqual(2, len(request['items']))
            self.assertFalse(data['execution_performed'])
            self.assertTrue(data['draft_only'] and data['review_required'])
            for key in ('carrier', 'vehicle', 'detailed_address', 'contact', 'request_number'):
                self.assertIsNone(request[key], key)
        self.assertEqual(counts, self.counts())
        self.assertEqual(graph, self.service.lookup('response-reader', version=version)['data'])
        self.assertEqual(version, self.service.lookup('response-reader')['data_version'])
        for tool in ('review', 'apply', 'prepare_draft', 'bootstrap'):
            self.error_is(self.gateway.call(tool, {}), 'TOOL_NOT_ALLOWED')
        self.error_is(self.service.prepare_draft('response-reader', [{'op': 'put', 'node': self.candidates[-1]}],
                                                request_key='reader-write'), 'FORBIDDEN')
        history = self.service.run_history('seed-admin')['data']
        self.assertTrue(any(r['tool'] == 'response_plan' and r['actor'] == 'response-reader'
                            and r['status'] == 'succeeded' for r in history))

    def test_D20_D26_recompute_at_bound_time_without_retiming_facts(self):
        version = self.register()
        utc = self.query(version, as_of='2026-10-27T01:00:00Z')
        self.assertTrue(utc['ok'], utc)
        self.assertEqual(960, utc['data']['dispatch_request']['on_time_new_dispatch_quantity'])
        changed = self.query(version, as_of='2026-10-27T11:00:00+09:00')
        self.assertTrue(changed['ok'], changed)
        self.assertEqual('needs_confirmation', changed['data']['response_state'])
        self.assertIsNone(changed['data']['dispatch_request']['new_dispatch_quantity'])
        self.assertEqual(version, self.service.lookup('response-reader')['data_version'])

    def test_D15_incomplete_scope_keeps_unknown_and_withholds_numbers(self):
        next(n for n in self.candidates if n['type'] == 'FulfillmentSnapshot')['confirmations']['allocations_complete'] = False
        version = self.register()
        result = self.query(version)
        self.assertTrue(result['ok'], result)
        self.assertEqual('needs_confirmation', result['data']['response_state'])
        self.assertTrue(result['data']['forecast']['unknowns'])
        self.assertIsNone(result['data']['dispatch_request']['new_dispatch_quantity'])
        self.assertEqual([], result['data']['dispatch_request']['items'])
        self.assertEqual('not_assessed', result['data']['dispatch_request']['planning_status'])

    def test_D24_invalid_inputs_remain_unapprovable(self):
        next(n for n in self.candidates if n['type'] == 'DeliveryCondition')['transport_seconds'] = True
        draft = self.draft()
        self.assertFalse(draft['validation']['valid'])
        self.error_is(self.service.review('reviewer', draft['draft_id'], draft['draft_version'], decision='approve',
            note='Malformed synthetic input must not be approved.', request_key='response-bad-review'), 'VALIDATION_FAILED')
        self.error_is(self.service.apply('workflow', draft['draft_id'], draft['draft_version'],
                                        request_key='response-bad-apply'), 'APPROVAL_REQUIRED')
        self.assertEqual(self.before, self.service.lookup('response-reader')['data_version'])

    def test_D30_selected_citations_resolve_without_changing_source(self):
        version = self.register()
        result = self.query(version)
        self.assertTrue(result['ok'], result)
        self.assertEqual(8, len(result['data']['evidence']))
        self.assertEqual(12, len(result['evidence']))
        self.assertTrue(set(result['data']['evidence']).issubset(result['evidence']))
        for eid in result['evidence']:
            evidence = self.service.lookup('response-reader', node_id=eid, version=version)['data'][0]
            self.assertEqual('Evidence', evidence['type'])
            self.assertTrue(evidence['locator'])
            source = self.service.lookup('response-reader', node_id=evidence['source_id'], version=version)['data'][0]
            self.assertEqual(source['sha256'], hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest())

    def test_D21_common_host_test_extension_does_not_grant_existing_agents_authority(self):
        version = self.register()
        reader = UserIdentity('cq05-test-reader', frozenset({'read'}))
        denied = UserIdentity('cq05-test-denied', frozenset())
        identity = DevelopmentIdentityProvider([reader, denied], enabled=True)
        registration = AgentRegistration('cq05_test_read', 'Test-only boundary.', frozenset({'response_plan'}))
        host = CommonHost(identity, self.database, agent_registrations=(registration,))
        with host.request(DevelopmentContext(reader.subject)) as session:
            result = session.agent('cq05_test_read').call('response_plan', self.arguments(version))
            self.assertTrue(result['ok'], result)
            self.assertEqual(960, result['data']['forecast']['on_time_quantity'])
            self.error_is(session.agent('product').call('response_plan', self.arguments(version)), 'TOOL_NOT_ALLOWED')
            self.error_is(session.agent('cq05_test_read').call('apply', {}), 'TOOL_NOT_ALLOWED')
        with host.request(DevelopmentContext(denied.subject)) as session:
            self.error_is(session.agent('cq05_test_read').call('response_plan', self.arguments(version)), 'FORBIDDEN')
        spec = function_tools(('response_plan',))[0]
        self.assertEqual({'delivery_round_id', 'as_of', 'version'}, set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        for roles in (PRODUCT_TOOLS, QUALITY_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS,
                      B_FULFILLMENT_TOOLS, B_PRODUCTION_TOOLS, B_DELIVERY_TOOLS, KNOWLEDGE_TOOLS, SUPERVISOR_TOOLS):
            self.assertNotIn('response_plan', roles)


if __name__ == '__main__':
    unittest.main()
