"""CQ03 explicit confirmed-version/read authority against isolated PostgreSQL; no LLM."""
import copy
import hashlib
import json
import unittest

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.demo import PRINCIPALS
from ontologics.order_fulfillment.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, SCHEMA_VERSION
from ontologics.order_fulfillment.production import AS_OF, ROUND, SNAPSHOT, WORK, load_demo_production
from ontologics.order_fulfillment.service import AgentGateway, Service
from ontologics.order_fulfillment.tool_specs import (B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS,
    KNOWLEDGE_TOOLS, PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS, function_tools)
from tests.db import database_for_test


class ProductionServiceTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        principals = copy.deepcopy(PRINCIPALS)
        principals['production-reader'] = {'kind': 'human', 'roles': ['read']}
        self.service = Service(self.database, principals=principals)
        self.addCleanup(self.service.close)
        base = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        seeded = self.service.bootstrap('seed-admin', base, request_key='production-base')
        self.assertTrue(seeded['ok'], seeded)
        self.before = seeded['data_version']
        self.gateway = AgentGateway(self.service, 'production-reader',
                                    allowed_tools={'production_completion', 'lookup'})
        self.candidates = load_demo_production()

    def error_is(self, result, code):
        self.assertFalse(result['ok'], result)
        self.assertEqual(code, result['errors'][0]['code'], result)

    def draft(self, nodes=None):
        result = self.service.prepare_draft('product-agent',
            [{'op': 'put', 'node': n} for n in (self.candidates if nodes is None else nodes)],
            request_key='production-draft')
        self.assertTrue(result['ok'], result)
        return result['data']

    def approve_and_apply(self, draft):
        did, dv = draft['draft_id'], draft['draft_version']
        reviewed = self.service.review('reviewer', did, dv, decision='approve',
            note='Synthetic production source checked by test human role', request_key='production-review')
        self.assertTrue(reviewed['ok'], reviewed)
        applied = self.service.apply('workflow', did, dv, request_key='production-apply')
        self.assertTrue(applied['ok'], applied)
        return applied['data_version']

    def register(self, nodes=None):
        draft = self.draft(nodes)
        self.assertTrue(draft['validation']['valid'], draft)
        return self.approve_and_apply(draft)

    def arguments(self, version, **changes):
        return dict(dict(production_work_id=WORK, delivery_round_id=ROUND,
                         as_of=AS_OF, version=version), **changes)

    def query(self, version, **changes):
        return self.gateway.call('production_completion', self.arguments(version, **changes))

    def test_unapproved_production_is_invisible_then_exact_confirmed_version_matches_golden(self):
        self.error_is(self.query(self.before), 'NOT_FOUND')
        draft = self.draft()
        self.assertEqual('awaiting_review', draft['status'])
        self.assertTrue(draft['validation']['valid'], draft)
        did, dv = draft['draft_id'], draft['draft_version']
        validated = self.service.validate_draft('product-agent', did, dv)
        self.assertTrue(validated['ok'], validated)
        self.assertTrue(validated['data']['validation']['valid'])
        self.error_is(self.service.apply('workflow', did, dv, request_key='production-premature'),
                      'APPROVAL_REQUIRED')
        self.error_is(self.query(self.before), 'NOT_FOUND')
        version = self.approve_and_apply(draft)
        before_read = self.service.lookup('production-reader', version=version)['data']
        result = self.query(version)
        self.assertTrue(result['ok'], result)
        golden = json.loads((ROOT / 'data/order_fulfillment/b_production_expected.json').read_text())
        for key in ('production_work_id', 'delivery_round_id', 'snapshot_id', 'as_of', 'status',
                    'configuration_id', 'unit', 'target_quantity', 'latest_good_quantity',
                    'selected_observation_ids', 'good_rate_per_hour', 'produced_rate_per_hour',
                    'recent_defect_ratio', 'remaining_quantity', 'remaining_seconds',
                    'estimated_completion_at', 'fulfillment_assessed'):
            with self.subTest(key=key):
                self.assertEqual(golden[key], result['data'][key])
        self.assertEqual('production-0.1', result['data']['forecast_rule_version'])
        self.assertEqual((version, SCHEMA_VERSION, __version__, 'temporal-0.1'),
            (result['data_version'], result['schema_version'], result['contract_version'], result['rule_version']))
        self.assertEqual(before_read, self.service.lookup('production-reader', version=version)['data'])
        self.assertEqual(version, self.service.lookup('production-reader')['data_version'])
        self.error_is(self.query(self.before), 'NOT_FOUND')
        self.assertEqual('applied', self.service.get_draft('production-reader', did, dv)['data']['status'])

    def test_explicit_version_exact_work_round_and_offset_are_required(self):
        version = self.register()
        for bad in (None, True, False, -1, '2', 2.0, [], {}):
            with self.subTest(version=bad):
                self.error_is(self.query(bad), 'INVALID_VERSION')
        self.error_is(self.query(version + 100), 'VERSION_NOT_FOUND')
        for field in ('production_work_id', 'delivery_round_id'):
            for bad in ('missing:id', 'contract:demo:B-01'):
                with self.subTest(field=field, bad=bad):
                    self.error_is(self.query(version, **{field: bad}), 'NOT_FOUND')
            for bad in ('', '  ', [], {}, None):
                with self.subTest(field=field, bad=bad):
                    self.error_is(self.query(version, **{field: bad}), 'INVALID_INPUT')
        for bad in ('2026-10-27', '2026-10-27T10:00:00', [], {}, None):
            with self.subTest(as_of=bad):
                self.error_is(self.query(version, as_of=bad), 'INVALID_TIME')
        args = self.arguments(version)
        for missing in ('production_work_id', 'delivery_round_id', 'as_of'):
            incomplete = dict(args)
            incomplete.pop(missing)
            self.error_is(self.gateway.call('production_completion', incomplete), 'INVALID_TOOL_ARGUMENTS')
        args.pop('version')
        self.error_is(self.gateway.call('production_completion', args), 'INVALID_VERSION')

    def test_utc_equivalence_does_not_advance_observation_or_reconstruct_other_time(self):
        version = self.register()
        utc = self.query(version, as_of='2026-10-27T01:00:00Z')
        self.assertTrue(utc['ok'], utc)
        self.assertEqual(AS_OF, utc['data']['as_of'])
        self.assertEqual('2026-10-27T14:26:40+09:00', utc['data']['estimated_completion_at'])
        unknown = self.query(version, as_of='2026-10-27T11:00:00+09:00')
        self.assertTrue(unknown['ok'], unknown)
        self.assertEqual('needs_confirmation', unknown['data']['status'])
        self.assertIsNone(unknown['data']['estimated_completion_at'])
        self.assertIsNone(unknown['data']['remaining_seconds'])
        self.assertEqual(version, self.service.lookup('production-reader')['data_version'])

    def test_missing_source_allocation_coverage_is_valid_data_but_unknown_forecast(self):
        snapshot = next(n for n in self.candidates if n['id'] == SNAPSHOT)
        snapshot['confirmations'].pop('source_allocations_complete')
        version = self.register()
        result = self.query(version)
        self.assertTrue(result['ok'], result)
        self.assertEqual('needs_confirmation', result['data']['status'])
        self.assertIsNone(result['data']['estimated_completion_at'])
        self.assertIsNone(result['data']['remaining_seconds'])
        self.assertTrue(any(u['field'] == 'source_allocations_complete' for u in result['data']['unknowns']))

    def test_invalid_observation_cannot_be_approved_or_applied(self):
        observation = next(n for n in self.candidates if n['type'] == 'ProductionObservation')
        observation['produced_quantity'] += 1
        draft = self.draft()
        self.assertEqual('needs_changes', draft['status'])
        self.assertFalse(draft['validation']['valid'])
        self.assertIn('PRODUCTION_TOTAL_MISMATCH', [e['code'] for e in draft['validation']['errors']])
        did, dv = draft['draft_id'], draft['draft_version']
        self.error_is(self.service.review('reviewer', did, dv, decision='approve',
            note='Must reject contradictory production counts', request_key='production-invalid-review'),
            'VALIDATION_FAILED')
        self.error_is(self.service.apply('workflow', did, dv, request_key='production-invalid-apply'),
                      'APPROVAL_REQUIRED')
        self.assertEqual(self.before, self.service.lookup('production-reader')['data_version'])
        self.error_is(self.query(self.before), 'NOT_FOUND')

    def test_returned_provenance_resolves_in_exact_version_and_source_is_immutable(self):
        version = self.register()
        result = self.query(version)
        self.assertTrue(result['ok'], result)
        golden = json.loads((ROOT / 'data/order_fulfillment/b_production_expected.json').read_text())
        self.assertTrue(set(golden['assumptions_evidence']) <= set(result['evidence']))
        observations = [n for n in self.candidates if n['type'] == 'ProductionObservation']
        for observation in observations:
            self.assertTrue(set(observation['evidence']) <= set(result['evidence']))
        for eid in result['evidence']:
            evidence = self.service.lookup('production-reader', node_id=eid, version=version)
            self.assertTrue(evidence['ok'], evidence)
            node = evidence['data'][0]
            self.assertEqual('Evidence', node['type'])
            self.assertTrue(node['locator'])
            source = self.service.lookup('production-reader', node_id=node['source_id'], version=version)
            self.assertTrue(source['ok'], source)
            document = source['data'][0]
            self.assertEqual('SourceDocument', document['type'])
            self.assertEqual(document['sha256'], hashlib.sha256((ROOT / document['path']).read_bytes()).hexdigest())
        source = copy.deepcopy(next(n for n in self.candidates if n['type'] == 'SourceDocument'))
        source['license'] += ' changed'
        changed = self.service.prepare_draft('product-agent', [{'op': 'put', 'node': source}],
            request_key='production-source-overwrite')
        self.error_is(changed, 'IMMUTABLE_SOURCE')

    def test_read_identity_cannot_be_overridden_or_receive_mutation_authority(self):
        version = self.register()
        args = self.arguments(version)
        self.error_is(AgentGateway(self.service, 'unknown').call('production_completion', args), 'FORBIDDEN')
        for override in ({'actor': 'reviewer'}, {'principals': {}}):
            self.error_is(self.gateway.call('production_completion', dict(args, **override)),
                          'INVALID_TOOL_ARGUMENTS')
        for tool in ('prepare_draft', 'review', 'apply', 'bootstrap'):
            self.error_is(self.gateway.call(tool, {}), 'TOOL_NOT_ALLOWED')
        direct_draft = self.service.prepare_draft('production-reader',
            [{'op': 'put', 'node': self.candidates[-1]}], request_key='reader-write')
        self.error_is(direct_draft, 'FORBIDDEN')
        self.assertTrue(self.query(version)['ok'])
        history = self.service.run_history('seed-admin')['data']
        self.assertTrue(any(r['tool'] == 'production_completion' and r['actor'] == 'production-reader'
                            and r['status'] == 'succeeded' for r in history))
        self.assertTrue(any(r['tool'] == 'production_completion' and r['status'] == 'failed' for r in history))
        self.assertEqual(version, self.service.lookup('production-reader')['data_version'])

    def test_tool_schema_and_existing_roles_keep_separate_authority(self):
        spec = function_tools(('production_completion',))[0]
        self.assertEqual({'production_work_id', 'delivery_round_id', 'as_of', 'version'},
                         set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        for roles in (PRODUCT_TOOLS, QUALITY_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS,
                      B_FULFILLMENT_TOOLS, KNOWLEDGE_TOOLS, SUPERVISOR_TOOLS):
            self.assertNotIn('production_completion', roles)

    def test_trusted_host_read_extension_uses_request_user_without_granting_read(self):
        version = self.register()
        reader = UserIdentity('cq03-test-reader', frozenset({'read'}))
        no_rights = UserIdentity('cq03-test-no-rights', frozenset())
        identities = DevelopmentIdentityProvider([reader, no_rights], enabled=True)
        registration = AgentRegistration('cq03_test_read', 'Test-only common read boundary.',
                                         frozenset({'production_completion'}))
        host = CommonHost(identities, self.database, agent_registrations=(registration,))
        with host.request(DevelopmentContext(reader.subject)) as session:
            result = session.agent('cq03_test_read').call('production_completion', self.arguments(version))
            self.assertTrue(result['ok'], result)
            self.assertEqual(90, result['data']['good_rate_per_hour'])
            self.error_is(session.agent('product').call('production_completion', self.arguments(version)),
                          'TOOL_NOT_ALLOWED')
            self.error_is(session.agent('cq03_test_read').call('prepare_draft', {}), 'TOOL_NOT_ALLOWED')
        with host.request(DevelopmentContext(no_rights.subject)) as session:
            self.error_is(session.agent('cq03_test_read').call('production_completion',
                          self.arguments(version)), 'FORBIDDEN')
        history = self.service.run_history('seed-admin')['data']
        self.assertTrue(any(r['tool'] == 'production_completion' and r['actor'] == reader.actor
                            and r['status'] == 'succeeded' for r in history))
        self.assertTrue(any(r['tool'] == 'production_completion' and r['actor'] == no_rights.actor
                            and r['error_code'] == 'FORBIDDEN' for r in history))


if __name__ == '__main__':
    unittest.main()
