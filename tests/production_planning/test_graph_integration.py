"""Real public-host workflows, exclusively in tests.db's disposable schema.

An explicit TEST_DATABASE_URL is required. No substitute store or implicit
connection to an operational DATABASE_URL is used by these new tests.
"""
import base64
import copy
import hashlib
import json
import os
import unittest
import uuid

from tests.production_planning.test_ontology_extension import catalog, ROOT
from tests.production_planning.test_reference_adapter import complete_inputs, partial_inputs
from ontologics.production_planning.reference_adapter import validate_registered_context


class HistoryBoundaryTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from tests.db import database_for_test
        from ontologics.production_planning.persistence import PlanningService as Service
        self.service = Service(database_for_test(self), principals={
            'user:test': {'kind': 'human', 'roles': ['draft']},
            'user:reader': {'kind': 'human', 'roles': ['read']}})
        self.addCleanup(self.service.close)
        # Construct a real service first. Intake must reject before any DB query
        # or receipt-file creation; authorization still uses the real method.
        db_patch = patch.object(self.service, 'db')
        self.db = db_patch.start()
        self.addCleanup(db_patch.stop)
        self.db.execute.side_effect = AssertionError('Rejected intake accessed the database')
        file_patch = patch('ontologics.receipts.tempfile.NamedTemporaryFile',
                           side_effect=AssertionError('Rejected intake created a receipt file'))
        self.receipt_file = file_patch.start()
        self.addCleanup(file_patch.stop)

    def test_added_types_are_rejected_before_receipt_storage_or_database_access(self):
        from unittest.mock import patch
        from ontologics.receipts import history_intake
        from ontologics.service import DomainError
        for kind in ('Contract', 'ContractItem', 'DeliveryRound', 'ComponentStockSnapshot',
                     'FulfillmentSnapshot', 'FinishedGoodsStock', 'ProductionWork',
                     'ProductionObservation', 'Allocation', 'Shipment', 'DeliveryCondition'):
            raw = dict(origin='synthetic', authored_at='2026-10-27T17:10:21+09:00',
                       notice='Synthetic test', records=[dict(id='test:new', type=kind)])
            with self.subTest(kind=kind):
                with patch.object(self.service, '_require', wraps=self.service._require) as require:
                    with self.assertRaises(DomainError) as raised:
                        history_intake(self.service, 'user:test', 'history.json',
                                       base64.b64encode(json.dumps(raw).encode()).decode())
                    self.assertEqual('INVALID_HISTORY_SOURCE', raised.exception.code)
                    require.assert_called_once_with('user:test', 'draft')
                self.db.execute.assert_not_called()
                self.receipt_file.assert_not_called()

    def test_reader_is_denied_before_parsing_or_storage(self):
        from unittest.mock import patch
        from ontologics.receipts import history_intake
        from ontologics.service import DomainError
        with patch('ontologics.receipts.base64.b64decode',
                   side_effect=AssertionError('Unauthorized intake parsed input')) as decode:
            with self.assertRaises(DomainError) as raised:
                history_intake(self.service, 'user:reader', 'history.json', 'e30=')
            self.assertEqual('FORBIDDEN', raised.exception.code)
            decode.assert_not_called()
        self.db.execute.assert_not_called()
        self.receipt_file.assert_not_called()


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'No explicitly prepared isolated TEST_DATABASE_URL')
class GraphIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests.db import database_for_test
        from ontologics.production_planning.host import PlanningHost as CommonHost
        from ontologics.host import AgentRegistration
        from ontologics.identity import DevelopmentIdentityProvider, UserIdentity
        from ontologics.production_planning.persistence import PlanningService as Service
        self.settings = database_for_test(self)
        self.identities = DevelopmentIdentityProvider([
            UserIdentity('planning-author', frozenset({'read', 'draft', 'review'})),
            UserIdentity('planning-reviewer', frozenset({'read', 'draft', 'review'})),
            UserIdentity('planning-reader', frozenset({'read'}))], enabled=True)
        role = AgentRegistration('planning_reference_reader', 'Read exact versions only', ['lookup', 'get_draft'])
        self.host = CommonHost(self.identities, self.settings, agent_registrations=(role,))
        seed = Service(self.settings, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read']}})
        try:
            self.ok(seed.bootstrap('fixture', catalog(), request_key='planning-test-seed'))
        finally:
            seed.close()
        self.context, self.nodes, self.contents = partial_inputs()

    def request(self, subject='planning-author'):
        from ontologics.identity import DevelopmentContext
        return self.host.request(DevelopmentContext(subject))

    def ok(self, response):
        self.assertTrue(response['ok'], response)
        return response

    def error(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def prepare(self, key='planning-bundle', **kwargs):
        operations = [dict(op='put', node=copy.deepcopy(n)) for n in self.nodes if n['origin'] == 'synthetic']
        with self.request() as session:
            return self.ok(session.call('prepare_draft', dict(operations=operations, request_key=key, **kwargs)))['data']

    def approve(self, draft, subject='planning-reviewer', key='planning-review'):
        with self.request(subject) as session:
            return session.review(draft['draft_id'], draft['draft_version'], decision='approve',
                                  note='Development synthetic graph test; not operational confirmation', request_key=key)

    def apply(self, draft, key='planning-apply'):
        return self.host.apply_approved(draft['draft_id'], draft['draft_version'], request_key=key)

    def validate(self, draft):
        with self.request('planning-reader') as session:
            return validate_registered_context(session, self.context, external_contents=self.contents,
                registration_ref=dict(draft_id=draft['draft_id'], draft_version=draft['draft_version']))

    def run_bundle(self, *, complete):
        if complete:
            self.context, self.nodes, self.contents = complete_inputs(self)
        draft = self.prepare()
        self.assertEqual('awaiting_review', draft['status'])
        self.assertTrue(draft['validation']['valid'])
        before = self.validate(draft)
        self.assertEqual('REGISTRATION_NOT_APPLIED', before['report']['errors'][0]['code'])
        with self.request() as session:
            initial = self.ok(session.call('lookup', {'version': 1}))
            self.assertFalse(any(n['type'] == 'ContractItem' for n in initial['data']))
        self.error(self.approve(draft, 'planning-author', 'self-review'), 'SELF_APPROVAL_FORBIDDEN')
        self.error(self.apply(draft, 'before-approval'), 'APPROVAL_REQUIRED')
        self.ok(self.approve(draft))
        applied = self.ok(self.apply(draft))
        self.assertEqual(2, applied['data']['applied_version'])
        replay = self.ok(self.apply(draft))
        self.assertEqual(applied['data'], replay['data'])
        self.assertEqual(applied['data_version'], replay['data_version'])
        self.assertIn('IDEMPOTENT_REPLAY', [w['code'] for w in replay['warnings']])
        result = self.validate(draft)  # fresh RequestSession / PostgreSQL connection
        self.assertTrue(result['report']['valid'], result)
        self.assertEqual(complete, result['report']['graph_integration_verified'], result)
        self.assertEqual('needs_confirmation', result['report']['status'])
        self.context['data_version'] = 1
        mismatch = self.validate(draft)
        self.assertEqual('REFERENCE_VERSION_MISMATCH', mismatch['report']['errors'][0]['code'])

    def test_current_partial_bundle_persists_without_fabricating_delivery(self):
        self.run_bundle(complete=False)

    def test_explicit_test_delivery_bundle_roundtrips_through_real_host(self):
        self.run_bundle(complete=True)

    def test_old_draft_version_stale_base_retries_and_roles_preserve_common_guards(self):
        first = self.prepare()
        second = self.prepare('parallel-proposal')
        revised = self.prepare('revised-proposal', draft_id=second['draft_id'], expected_version=1)
        self.error(self.approve(second, key='old-version-review'), 'SUPERSEDED_DRAFT')
        self.ok(self.approve(revised, key='revised-review'))
        self.ok(self.approve(first)); self.ok(self.apply(first))
        self.error(self.apply(revised, 'stale-base'), 'STALE_BASE')
        with self.request('planning-reader') as session:
            result = self.ok(session.agent('planning_reference_reader').call('lookup', {'version': 2}))
            self.assertTrue(any(n['type'] == 'ContractItem' for n in result['data']))
            self.error(session.agent('planning_reference_reader').call('calculate_materials', {}), 'TOOL_NOT_ALLOWED')
            self.error(session.call('prepare_draft', {'operations': [], 'request_key': 'reader-write'}), 'FORBIDDEN')

    def test_history_intake_retains_existing_five_type_scope(self):
        from ontologics.demo import fixture_nodes, LOT
        from ontologics.service import DomainError
        fictional, supplement = fixture_nodes()
        records = [n for n in fictional if n['type'] in ('ProductUnit', 'ComponentLot', 'Installation')]
        records += [op['node'] for op in supplement if op['node']['type'] == 'HistoryCoverage']
        records += [dict(id='movement:planning:test', type='InventoryMovement', lot_id=LOT,
                         quantity=10, occurred_at='2026-10-27T17:10:21+09:00', movement='receipt')]
        raw = json.dumps(dict(origin='synthetic', authored_at='2026-10-27T17:10:21+09:00',
            notice='Isolated five-type test ' + uuid.uuid4().hex,
            records=[{k:v for k,v in n.items() if k not in ('origin','evidence')} for n in records])).encode()
        sha = hashlib.sha256(raw).hexdigest()
        path = ROOT / 'data/receipts' / (sha + '.json')
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        with self.request() as session:
            result = self.ok(session.history_intake('five-types.json', base64.b64encode(raw).decode()))
            self.assertTrue(result['data']['validation']['valid'], result)
            self.assertEqual({'ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage', 'InventoryMovement'},
                             {n['type'] for n in result['data']['records']})
            for kind in ('Contract', 'ContractItem', 'DeliveryRound', 'ComponentStockSnapshot',
                     'FulfillmentSnapshot', 'FinishedGoodsStock', 'ProductionWork',
                     'ProductionObservation', 'Allocation', 'Shipment', 'DeliveryCondition'):
                body = dict(origin='synthetic', authored_at='2026-10-27T17:10:21+09:00',
                            notice='Rejected planning type', records=[dict(id='rejected:test', type=kind)])
                with self.assertRaises(DomainError) as caught:
                    session.history_intake('rejected.json', base64.b64encode(json.dumps(body).encode()).decode())
                self.assertEqual('INVALID_HISTORY_SOURCE', caught.exception.code)
