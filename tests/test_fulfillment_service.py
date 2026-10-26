"""Real PostgreSQL approval/version/authority boundary for B-CQ02; no LLM."""
import copy
import json
import unittest

from ontologics import __version__
from ontologics.contracts import load_demo_contract
from ontologics.demo import PRINCIPALS
from ontologics.fulfillment import AS_OF, ROUND, SNAPSHOT, load_demo_fulfillment
from ontologics.model import ROOT, SCHEMA_VERSION
from ontologics.service import Service, AgentGateway
from ontologics.tool_specs import PRODUCT_TOOLS, QUALITY_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS, function_tools
from tests.db import database_for_test


class FulfillmentServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals=PRINCIPALS)
        self.addCleanup(self.service.close)
        nodes = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        seed = self.service.bootstrap('seed-admin', nodes, request_key='status-base')
        self.assertTrue(seed['ok'], seed)
        self.before = seed['data_version']
        self.gateway = AgentGateway(self.service, 'product-agent')
        self.candidates = load_demo_fulfillment()

    def error_is(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def draft(self, nodes=None, key='status-draft'):
        response = self.gateway.call('prepare_draft', {'operations': [{'op': 'put', 'node': n}
            for n in (self.candidates if nodes is None else nodes)], 'request_key': key})
        self.assertTrue(response['ok'], response)
        return response['data']

    def register(self, nodes=None):
        draft = self.draft(nodes)
        self.assertTrue(draft['validation']['valid'], draft)
        review = self.service.review('reviewer', draft['draft_id'], draft['draft_version'],
            decision='approve', note='Synthetic snapshot reviewed in integration test', request_key='status-review')
        self.assertTrue(review['ok'], review)
        applied = self.service.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='status-apply')
        self.assertTrue(applied['ok'], applied)
        return applied['data_version']

    def query(self, version, **kwargs):
        return self.gateway.call('fulfillment_status', dict(delivery_round_id=ROUND, as_of=AS_OF, version=version, **kwargs))

    def test_unapproved_snapshot_stays_invisible_then_reviewed_version_matches_golden(self):
        result = self.query(self.before)
        self.assertTrue(result['ok'], result)
        self.assertEqual('needs_confirmation', result['data']['status'])
        self.assertIsNone(result['data']['states'])
        draft = self.draft()
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertEqual('awaiting_review', draft['status'])
        self.assertTrue(self.service.validate_draft('product-agent', did, dv)['data']['validation']['valid'])
        self.error_is(self.service.apply('workflow', did, dv, request_key='status-early'), 'APPROVAL_REQUIRED')
        self.assertEqual('needs_confirmation', self.query(self.before)['data']['status'])
        self.assertTrue(self.service.review('reviewer', did, dv, decision='approve', note='Synthetic source checked', request_key='status-ok')['ok'])
        applied = self.service.apply('workflow', did, dv, request_key='status-final')
        self.assertTrue(applied['ok'], applied)
        version = applied['data_version']
        result = self.query(version)
        expected = json.loads((ROOT / 'data/b_fulfillment_expected.json').read_text())
        self.assertTrue(result['ok'], result)
        for key in ('states', 'allocated_quantity', 'remaining_quantity', 'unallocated_quantity', 'fulfillment_assessed'):
            self.assertEqual(expected[key], result['data'][key])
        self.assertEqual(version, result['data_version'])
        self.assertEqual(SCHEMA_VERSION, result['schema_version'])
        self.assertEqual(__version__, result['contract_version'])
        self.assertEqual('temporal-0.1', result['rule_version'])
        self.assertEqual('needs_confirmation', self.query(self.before)['data']['status'])
        self.assertEqual(version, self.service.lookup('product-agent')['data_version'])
        self.assertIn('FULFILLMENT_NOT_ASSESSED', [w['code'] for w in result['warnings']])

    def test_explicit_version_exact_id_and_as_of_are_required(self):
        version = self.register()
        for bad in (None, True, False, -1, '2', 2.0, [], {}):
            with self.subTest(version=bad):
                self.error_is(self.query(bad), 'INVALID_VERSION')
        self.error_is(self.query(version + 100), 'VERSION_NOT_FOUND')
        for bad in ('missing:round', 'contract:demo:B-01'):
            self.error_is(self.service.fulfillment_status('product-agent', bad, AS_OF, version=version), 'NOT_FOUND')
        for bad in ('', '  ', [], {}):
            self.error_is(self.service.fulfillment_status('product-agent', bad, AS_OF, version=version), 'INVALID_INPUT')
        for bad in ('2026-10-27', '2026-10-27T10:00:00', [], {}, None):
            self.error_is(self.service.fulfillment_status('product-agent', ROUND, bad, version=version), 'INVALID_TIME')
        self.error_is(self.gateway.call('fulfillment_status', {'delivery_round_id': ROUND, 'version': version}), 'INVALID_TOOL_ARGUMENTS')
        self.error_is(self.gateway.call('fulfillment_status', {'delivery_round_id': ROUND, 'as_of': AS_OF}), 'INVALID_VERSION')

    def test_other_time_is_unknown_and_utc_equivalence_does_not_change_facts(self):
        version = self.register()
        utc = self.service.fulfillment_status('product-agent', ROUND, '2026-10-27T01:00:00Z', version=version)
        self.assertTrue(utc['ok'], utc)
        self.assertEqual(AS_OF, utc['data']['as_of'])
        other = self.service.fulfillment_status('product-agent', ROUND, '2026-10-27T11:00:00+09:00', version=version)
        self.assertTrue(other['ok'], other)
        self.assertEqual('needs_confirmation', other['data']['status'])
        self.assertIsNone(other['data']['states'])
        self.assertIsNone(other['data']['remaining_quantity'])
        self.assertEqual([AS_OF], other['data']['available_as_of'])
        self.assertEqual(version, self.service.lookup('product-agent')['data_version'])

    def test_invalid_other_round_overallocation_cannot_be_approved_or_applied(self):
        nodes = copy.deepcopy(self.candidates)
        index = {n['id']: n for n in nodes}
        index['delivery-round:demo:B-CQ02-other:1']['quantity'] = 301
        index['contract-item:demo:B-CQ02-other:1']['quantity'] = 301
        index['allocation:demo:CQ02:OTHER-W']['quantity'] = 101
        draft = self.draft(nodes)
        self.assertEqual('needs_changes', draft['status'])
        self.assertFalse(draft['validation']['valid'])
        self.assertIn('SOURCE_QUANTITY_EXCEEDED', [e['code'] for e in draft['validation']['errors']])
        self.error_is(self.service.review('reviewer', draft['draft_id'], draft['draft_version'],
            decision='approve', note='Must not pass', request_key='status-invalid-review'), 'VALIDATION_FAILED')
        self.error_is(self.service.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='status-invalid-apply'), 'APPROVAL_REQUIRED')
        self.assertEqual(self.before, self.service.lookup('product-agent')['data_version'])

    def test_incomplete_coverage_can_be_recorded_but_totals_are_not_asserted(self):
        snapshot = next(n for n in self.candidates if n['id'] == SNAPSHOT)
        snapshot['confirmations'].pop('shipment_events_complete')
        version = self.register()
        result = self.query(version)
        self.assertTrue(result['ok'], result)
        self.assertEqual('needs_confirmation', result['data']['status'])
        self.assertIsNone(result['data']['states'])
        self.assertIsNone(result['data']['remaining_quantity'])
        self.assertTrue(any(u['field'] == 'shipment_events_complete' for u in result['data']['unknowns']))

    def test_provenance_resolves_in_same_version_and_sources_remain_immutable(self):
        version = self.register()
        result = self.query(version)
        self.assertTrue(result['evidence'])
        snapshot = next(n for n in self.candidates if n['id'] == SNAPSHOT)
        self.assertTrue(set(snapshot['evidence']) <= set(result['evidence']))
        for eid in result['evidence']:
            evidence = self.service.lookup('product-agent', node_id=eid, version=version)
            self.assertTrue(evidence['ok'], evidence)
            source = self.service.lookup('product-agent', node_id=evidence['data'][0]['source_id'], version=version)
            self.assertTrue(source['ok'], source)
            self.assertTrue(source['data'][0]['sha256'])
            self.assertTrue(evidence['data'][0]['locator'])
        source = copy.deepcopy(next(n for n in self.candidates if n['type'] == 'SourceDocument'))
        source['license'] += ' modified'
        self.error_is(self.gateway.call('prepare_draft', {'operations': [{'op': 'put', 'node': source}],
            'request_key': 'status-source-overwrite'}), 'IMMUTABLE_SOURCE')

    def test_gateway_keeps_identity_review_boundary_and_audits_reads(self):
        version = self.register()
        args = dict(delivery_round_id=ROUND, as_of=AS_OF, version=version)
        self.error_is(AgentGateway(self.service, 'unknown').call('fulfillment_status', args), 'FORBIDDEN')
        for extra in ({'actor': 'reviewer'}, {'principals': {}}):
            self.error_is(self.gateway.call('fulfillment_status', dict(args, **extra)), 'INVALID_TOOL_ARGUMENTS')
        for tool in ('review', 'apply', 'bootstrap'):
            self.error_is(self.gateway.call(tool, {}), 'TOOL_NOT_ALLOWED')
        self.assertTrue(self.gateway.call('fulfillment_status', args)['ok'])
        history = self.service.run_history('seed-admin')['data']
        self.assertTrue(any(r['tool'] == 'fulfillment_status' and r['status'] == 'succeeded' for r in history))
        self.assertTrue(any(r['tool'] == 'fulfillment_status' and r['status'] == 'failed' for r in history))

    def test_read_tool_contract_does_not_expand_existing_agent_roles(self):
        spec = function_tools(('fulfillment_status',))[0]
        self.assertEqual({'delivery_round_id', 'as_of', 'version'}, set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        for roles in (PRODUCT_TOOLS, QUALITY_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS):
            self.assertNotIn('fulfillment_status', roles)


if __name__ == '__main__':
    unittest.main()
