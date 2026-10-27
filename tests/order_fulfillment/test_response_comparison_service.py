"""CQ06 approved-version comparison, permissions and read invariants in PostgreSQL."""
import copy
import json
import unittest

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, SOURCE_ID, load_demo_delivery
from ontologics.demo import PRINCIPALS
from ontologics.model import ROOT, SCHEMA_VERSION
from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, load_demo_recalculation
from ontologics.order_fulfillment.service import AgentGateway, Service
from ontologics.order_fulfillment.tool_specs import (B_COMPARISON_TOOLS, B_RESPONSE_TOOLS, B_DELIVERY_TOOLS,
    PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS, function_tools)
from tests.db import database_for_test


class ResponseComparisonServiceTests(unittest.TestCase):
    def setUp(self):
        principals = copy.deepcopy(PRINCIPALS)
        principals['comparison-reader'] = {'kind': 'human', 'roles': ['read']}
        self.service = Service(database_for_test(self), principals=principals)
        self.addCleanup(self.service.close)
        nodes = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract() + load_demo_delivery()
        result = self.service.bootstrap('seed-admin', nodes, request_key='comparison-base')
        self.assertTrue(result['ok'], result)
        self.before = result['data_version']
        self.gateway = AgentGateway(self.service, 'comparison-reader', allowed_tools=B_COMPARISON_TOOLS)
        self.golden = json.loads((ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())

    def args(self, after=None, at=AS_OF, **changes):
        result = dict(delivery_round_id=ROUND, before_version=self.before, before_as_of=AS_OF,
                      after_version=self.before if after is None else after, after_as_of=at)
        result.update(changes)
        return result

    def query(self, after=None, at=AS_OF, **changes):
        return self.gateway.call('compare_response_plan', self.args(after, at, **changes))

    def error(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def draft(self, case, suffix=''):
        result = self.service.prepare_draft('product-agent',
            [{'op': 'put', 'node': n} for n in load_demo_recalculation(case)],
            request_key='comparison-draft-' + case + suffix)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['data']['validation']['valid'], result)
        return result['data']

    def approve(self, draft, suffix=''):
        did, dv = draft['draft_id'], draft['draft_version']
        result = self.service.review('reviewer', did, dv, decision='approve',
            note='Scripted development human review of synthetic CQ06 changes; not operational approval.',
            request_key='comparison-review' + suffix)
        self.assertTrue(result['ok'], result)
        result = self.service.apply('workflow', did, dv, request_key='comparison-apply' + suffix)
        self.assertTrue(result['ok'], result)
        return result['data_version']

    def test_D01_D02_D20_D23_real_approval_and_explicit_two_versions(self):
        draft = self.draft('continued11')
        self.error(self.service.apply('workflow', draft['draft_id'], draft['draft_version'],
                                     request_key='before-approval'), 'APPROVAL_REQUIRED')
        pending = self.query()
        self.assertTrue(pending['ok'], pending)
        self.assertEqual(0, pending['data']['quantity_deltas']['on_time_quantity'])
        self.error(self.query(self.before + 1, AFTER_AS_OF['continued11']), 'VERSION_NOT_FOUND')
        validation = self.service.validate_draft('product-agent', draft['draft_id'], draft['draft_version'])
        self.assertTrue(validation['ok'] and validation['data']['validation']['valid'])
        after = self.approve(draft)
        result = self.query(after, AFTER_AS_OF['continued11'])
        self.assertTrue(result['ok'], result)
        data = result['data']
        self.assertEqual((self.before, after), (data['before']['data_version'], data['after']['data_version']))
        self.assertEqual((after, __version__, SCHEMA_VERSION),
                         (result['data_version'], result['contract_version'], result['schema_version']))
        self.assertEqual('comparison-0.1', data['comparison_rule_version'])
        self.assertEqual((600, 690), tuple(data[side]['response_plan']['dispatch_request']['currently_available_quantity']
                                         for side in ('before', 'after')))
        self.assertEqual(-3600, data['time_deltas']['remaining_seconds'])
        self.assertEqual(0, data['time_deltas']['estimated_completion_seconds'])
        old = self.service.response_plan('comparison-reader', ROUND, AS_OF, version=self.before)
        self.assertTrue(old['ok'], old)
        self.assertEqual(600, old['data']['dispatch_request']['currently_available_quantity'])

    def test_D05_combined_approved_change_matches_golden(self):
        # Each variant has its own DB seeded by this test class; changing case
        # within one11:00 graph would create duplicate observations, so only
        # a single representative is applied here; all cases have pure tests.
        after = self.approve(self.draft('combined11'))
        result = self.query(after, AFTER_AS_OF['combined11'])
        self.assertTrue(result['ok'], result)
        expected = self.golden['cases']['combined11']
        for key, value in expected['forecast'].items():
            self.assertEqual(value, result['data']['after']['response_plan']['forecast'][key], key)
        for key, value in expected['time_deltas'].items():
            self.assertEqual(value, result['data']['time_deltas'][key], key)
        self.assertEqual(-120, result['data']['quantity_deltas']['on_time_quantity'])

    def test_D11_explicit_version_boundary(self):
        for name in ('before_version', 'after_version'):
            for value in (None, True, False, -1, '1', 1.0, [], {}):
                with self.subTest(field=name, value=value):
                    self.error(self.query(**{name: value}), 'INVALID_VERSION')
            args = self.args(); args.pop(name)
            self.error(self.gateway.call('compare_response_plan', args), 'INVALID_VERSION')
        self.error(self.query(after_version=self.before+100), 'VERSION_NOT_FOUND')

    def test_D12_D13_D14_time_boundary_and_same_utc(self):
        for name in ('before_as_of', 'after_as_of'):
            for value in (None, '2026-10-27', '2026-10-27T10:00:00', [], {}):
                with self.subTest(field=name, value=value):
                    self.error(self.query(**{name: value}), 'INVALID_TIME')
        same = self.query(before_as_of='2026-10-27T01:00:00Z', after_as_of='2026-10-27T01:00:00+00:00')
        self.assertTrue(same['ok'], same)
        self.assertEqual(0, same['data']['quantity_deltas']['on_time_quantity'])
        self.error(self.query(after_as_of='2026-10-27T11:00:00+09:00'), 'INVALID_TIME')
        after = self.approve(self.draft('transport2h'))
        self.error(self.query(after, before_version=after, after_version=self.before), 'INVALID_VERSION')
        result = self.query(after, after_as_of='2026-10-27T11:00:00+09:00')
        self.assertTrue(result['ok'], result)
        self.assertEqual('partially_unknown', result['data']['comparison_status'])

    def test_D15_D24_invalid_identity_arguments_and_read_permissions(self):
        for rid in ('missing:round', 'contract:demo:B-01'):
            self.error(self.query(delivery_round_id=rid), 'NOT_FOUND')
        for rid in ('', ' ', None, [], {}):
            self.error(self.query(delivery_round_id=rid), 'INVALID_INPUT')
        for extra in ({'actor': 'reviewer'}, {'principals': {}}, {'before_nodes': []}, {'forecast': {}}):
            self.error(self.gateway.call('compare_response_plan', dict(self.args(), **extra)), 'INVALID_TOOL_ARGUMENTS')
        self.error(AgentGateway(self.service, 'unknown').call('compare_response_plan', self.args()), 'FORBIDDEN')
        for tool in ('prepare_draft', 'review', 'apply', 'bootstrap'):
            self.error(self.gateway.call(tool, {}), 'TOOL_NOT_ALLOWED')

    def test_D21_D22_new_provenance_old_sources_immutable(self):
        after = self.approve(self.draft('defects11'))
        result = self.query(after, AFTER_AS_OF['defects11'])
        self.assertTrue(result['ok'], result)
        for side in ('before', 'after'):
            version = result['data'][side]['data_version']
            for eid in result['data']['evidence_by_side'][side]:
                lookup = self.service.lookup('comparison-reader', node_id=eid, version=version)
                self.assertTrue(lookup['ok'], lookup)
                self.assertEqual(eid, lookup['data'][0]['id'])
        old_source = self.service.lookup('comparison-reader', node_id=SOURCE_ID, version=self.before)['data'][0]
        new_source = copy.deepcopy(old_source); new_source['version'] = 'illegally rewritten'
        self.error(self.service.prepare_draft('product-agent', [{'op':'put','node':new_source}],
                                             request_key='rewrite-source'), 'IMMUTABLE_SOURCE')
        self.assertEqual(old_source, self.service.lookup('comparison-reader', node_id=SOURCE_ID, version=after)['data'][0])

    def test_D25_read_does_not_write_business_records_and_is_audited_once(self):
        after = self.approve(self.draft('continued11'))
        graph_before = self.service.lookup('comparison-reader', version=self.before)['data']
        graph_after = self.service.lookup('comparison-reader', version=after)['data']
        def counts():
            return {table:self.service.db.execute('SELECT COUNT(*) AS n FROM '+table).fetchone()['n']
                    for table in ('snapshots', 'drafts', 'review_events', 'runs')}
        prior = counts()
        query = self.query(after, AFTER_AS_OF['continued11'])
        self.assertTrue(query['ok'], query)
        next_counts = counts()
        for table in ('snapshots', 'drafts', 'review_events'):
            self.assertEqual(prior[table], next_counts[table], table)
        self.assertEqual(prior['runs']+1, next_counts['runs'])
        self.assertEqual(graph_before, self.service.lookup('comparison-reader', version=self.before)['data'])
        self.assertEqual(graph_after, self.service.lookup('comparison-reader', version=after)['data'])
        self.assertEqual(after, self.service.lookup('comparison-reader')['data_version'])
        self.assertFalse(query['data']['execution_performed'])

    def test_D23_later_approved_change_does_not_rebind_explicit_pair(self):
        after = self.approve(self.draft('continued11'))
        original = self.query(after, AFTER_AS_OF['continued11'])
        draft = self.draft('transport2h', '-later')
        newer = self.approve(draft, '-later')
        self.assertGreater(newer, after)
        result = self.query(after, AFTER_AS_OF['continued11'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(after, result['data_version'])
        self.assertEqual(original['data']['quantity_deltas'], result['data']['quantity_deltas'])
        self.assertEqual(original['data']['time_deltas'], result['data']['time_deltas'])

    def test_D24_D27_tool_contract_does_not_widen_existing_roles(self):
        spec, = function_tools(['compare_response_plan'])
        self.assertEqual(set(self.args()), set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        self.assertEqual(('compare_response_plan','lookup_many'), B_COMPARISON_TOOLS)
        for tools in (PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS, B_RESPONSE_TOOLS, B_DELIVERY_TOOLS):
            self.assertNotIn('compare_response_plan', tools)
        self.error(AgentGateway(self.service,'comparison-reader',allowed_tools=B_RESPONSE_TOOLS).call(
            'compare_response_plan',self.args()), 'TOOL_NOT_ALLOWED')

    def test_D26_unapproved_and_self_review_not_authorized(self):
        draft = self.draft('continued11')
        self.error(self.service.review('product-agent', draft['draft_id'],draft['draft_version'],
            decision='approve',note='Not a human reviewer',request_key='agent-approve'), 'FORBIDDEN')
        self.error(self.query(self.before+1,AFTER_AS_OF['continued11']), 'VERSION_NOT_FOUND')
        before = self.query()
        self.assertTrue(before['ok'], before)
        self.assertEqual(600,before['data']['after']['response_plan']['dispatch_request']['currently_available_quantity'])
