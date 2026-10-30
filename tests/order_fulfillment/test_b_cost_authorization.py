"""Synthetic operator policy/approval boundaries; no keys, database or HTTP."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_cost_authorization as policy
from ontologics.paid_budget import CostPolicy, ModelPrice
from ontologics.service import DomainError


class CostAuthorizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='b-public-policy-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'public-policy.json'
        # These rates and caps are invented test data, never an actual rate card.
        self.values = dict(ONTOLOGICS_REQUEST_MAX_COST_USD='2',
            ONTOLOGICS_MODEL_PRICES_JSON=json.dumps({policy.MODEL: dict(
                input_usd_per_million='1', output_usd_per_million='2', max_input_tokens=400000)}))
        self.path.write_text(json.dumps(self.values))
        self.cases = ['cq01_intake', 'cq01_promise']
        self.binding = dict(run_id='test-run-unique', source_sha256='a' * 64,
            input_sha256='b' * 64, free_gate_sha256='c' * 64)
        self.guards = []
        for target in ('ontologics.database.environment_values', 'psycopg.connect', 'urllib.request.urlopen',
                       'urllib.request.build_opener'):
            guard = patch(target, side_effect=AssertionError('No credentials, DB or network in policy tests'))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.guards.append(mocked)

    def tearDown(self):
        for mocked in self.guards:
            mocked.assert_not_called()

    def load(self, **kwargs):
        return policy.load_public_policy(self.path, cases=self.cases, **kwargs)

    def approved(self, plan):
        value = policy.prepare_authorization(plan, **self.binding)
        value['approved'] = True  # Explicit simulated human action in a test.
        return value

    def test_public_plan_reuses_common_parser_and_reservation_and_allocates_per_case(self):
        with patch.object(policy, 'cost_policy_from_environment', wraps=policy.cost_policy_from_environment) as parser:
            snapshot = self.load(synthetic=True, total_cap_usd='4')
        parser.assert_called_once_with(self.values)
        public = snapshot.plan()
        self.assertEqual(public['policy_fingerprint'], snapshot.policy.fingerprint)
        self.assertEqual(public['public_policy_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(public['per_case_max_cost_usd'], '2')
        self.assertEqual(public['allocated_max_cost_usd'], '4')
        self.assertEqual(public['first_call_reservation_usd'], '0.402048')
        self.assertTrue(public['synthetic'])
        self.assertEqual((public['per_case_request_cap'], public['total_request_cap'], public['campaign_request_ceiling']), (4, 8, 40))
        self.assertEqual((public['max_output_tokens'], public['timeout_seconds'], public['automatic_retries'], public['max_repairs']), (1024, 30, 0, 0))
        self.assertEqual(public['minimum_input_reservation_tokens'], 400000)

    def test_snapshot_and_display_copies_do_not_change_trusted_policy(self):
        snapshot = self.load(synthetic=True)
        original = snapshot.fingerprint
        self.cases.clear()
        display = snapshot.plan()
        display['cost_policy']['prices'][0]['input_usd_per_million'] = '0'
        display['cases'].clear()
        self.assertEqual(snapshot.fingerprint, original)
        self.assertEqual(snapshot.cases, ('cq01_intake', 'cq01_promise'))
        with self.assertRaises(FrozenInstanceError):
            snapshot.synthetic = False
        self.path.write_text('{}')
        self.assertEqual(snapshot.fingerprint, original)

    def test_unknown_or_credential_fields_and_missing_policy_are_rejected(self):
        for values in ({}, dict(self.values, OPENAI_API_KEY='never-a-real-key'),
                       {'ONTOLOGICS_REQUEST_MAX_COST_USD': '2'}, dict(self.values, approved=True),
                       dict(self.values, ONTOLOGICS_REQUEST_MAX_COST_USD=2)):
            with self.subTest(keys=sorted(values)):
                self.path.write_text(json.dumps(values))
                with self.assertRaises(policy.Blocked):
                    self.load()

    def test_empty_partial_and_invalid_cost_config_are_rejected(self):
        for cap, prices in (('', ''), ('2', ''), ('', '{}'), ('2', '{broken'), ('NaN', '{}')):
            with self.subTest(cap=cap):
                self.path.write_text(json.dumps(dict(ONTOLOGICS_REQUEST_MAX_COST_USD=cap,
                    ONTOLOGICS_MODEL_PRICES_JSON=prices)))
                with self.assertRaises((policy.Blocked, DomainError)):
                    self.load()

    def test_public_file_is_bounded_and_rejects_symlinks_in_file_or_parent(self):
        self.path.write_text(' ' * 16385)
        with self.assertRaises(policy.Blocked):
            self.load()
        self.path.write_text(json.dumps(self.values))
        alias = self.root / 'alias.json'
        alias.symlink_to(self.path)
        parent = self.root / 'alias-parent'
        parent.symlink_to(self.root, target_is_directory=True)
        for path in (alias, parent / self.path.name, Path('relative.json'), self.root / 'missing.json'):
            with self.subTest(path=path.name), self.assertRaises(policy.Blocked):
                policy.load_public_policy(path, cases=self.cases)

    def test_missing_fixed_model_low_input_ceiling_and_zero_output_price_cannot_pass(self):
        cases = (('other-fixed-model', 400000, '2', 'MODEL_PRICE_REQUIRED'),
                 (policy.MODEL, 399999, '2', 'COST_RESERVATION_CONFIG_INVALID'),
                 (policy.MODEL, 400000, '0', 'COST_RESERVATION_CONFIG_INVALID'))
        for model, ceiling, output_price, code in cases:
            with self.subTest(code=code):
                prices = {model: dict(input_usd_per_million='1', output_usd_per_million=output_price,
                                     max_input_tokens=ceiling)}
                self.path.write_text(json.dumps(dict(self.values, ONTOLOGICS_MODEL_PRICES_JSON=json.dumps(prices))))
                with self.assertRaises(DomainError) as caught:
                    self.load()
                self.assertEqual(caught.exception.code, code)

    def test_first_reservation_and_separate_total_cap_must_fit(self):
        self.path.write_text(json.dumps(dict(self.values, ONTOLOGICS_REQUEST_MAX_COST_USD='0.4')))
        with self.assertRaises(DomainError) as caught:
            self.load()
        self.assertEqual(caught.exception.code, 'COST_BUDGET_EXCEEDED')
        self.path.write_text(json.dumps(self.values))
        with self.assertRaises(policy.Blocked):
            self.load(total_cap_usd='3.99')
        self.assertEqual(self.load(total_cap_usd='4').total_cap_usd, '4')

    def test_noncanonical_or_duplicate_cases_and_nonboolean_synthetic_are_rejected(self):
        for cases in ([], ['cq01_promise', 'cq01_intake'], ['cq01_intake'] * 2, ['unknown'], [True]):
            with self.subTest(cases=cases), self.assertRaises(policy.Blocked):
                policy.load_public_policy(self.path, cases=cases)
        with self.assertRaises(policy.Blocked):
            self.load(synthetic=1)

    def test_authorization_preparation_is_unapproved_and_requires_manual_transition(self):
        snapshot = self.load()
        unapproved = policy.prepare_authorization(snapshot, **self.binding)
        self.assertIs(unapproved['approved'], False)
        with self.assertRaises(policy.Blocked):
            policy.validate_cost_authorization(unapproved, snapshot, **self.binding)
        approved = self.approved(snapshot)
        self.assertTrue(policy.validate_cost_authorization(approved, snapshot, **self.binding))
        self.assertNotIn('input_and_money_caps_unfixed_acknowledged', approved)

    def test_synthetic_plan_never_validates_for_paid_use_even_if_approved(self):
        snapshot = self.load(synthetic=True)
        with self.assertRaises(policy.Blocked):
            policy.validate_cost_authorization(self.approved(snapshot), snapshot, **self.binding)

    def test_every_run_hash_binding_and_old_or_extra_fields_are_rejected(self):
        snapshot = self.load()
        approved = self.approved(snapshot)
        for field in self.binding:
            changed = dict(self.binding, **{field: 'another-run' if field == 'run_id' else 'd' * 64})
            with self.subTest(field=field), self.assertRaises(policy.Blocked):
                policy.validate_cost_authorization(approved, snapshot, **changed)
        for changes in ({'schema_version': 'b-paid-authorization-1'}, {'approved': 1},
                        {'input_and_money_caps_unfixed_acknowledged': True}, {'OPENAI_API_KEY': 'forged'}):
            with self.subTest(changes=changes), self.assertRaises(policy.Blocked):
                policy.validate_cost_authorization(dict(approved, **changes), snapshot, **self.binding)

    def test_changed_prices_cap_file_bytes_or_representatives_invalidate_old_approval(self):
        snapshot = self.load()
        approved = self.approved(snapshot)
        self.path.write_text(json.dumps(self.values, indent=2))
        changed_bytes = self.load()
        changed_prices = CostPolicy('2', (ModelPrice(policy.MODEL, '2', '2', 400000),))
        changed_cap = CostPolicy('3', snapshot.policy.prices)
        alternatives = [changed_bytes,
            policy.CostPlan(changed_prices, snapshot.source_sha256, snapshot.cases),
            policy.CostPlan(changed_cap, snapshot.source_sha256, snapshot.cases),
            policy.CostPlan(snapshot.policy, snapshot.source_sha256, ('cq01_intake',))]
        for current in alternatives:
            with self.subTest(fingerprint=current.fingerprint), self.assertRaises(policy.Blocked):
                policy.validate_cost_authorization(approved, current, **self.binding)

    def test_authorization_cannot_override_trusted_policy_or_fixed_limits(self):
        snapshot = self.load()
        approved = self.approved(snapshot)
        for field, value in (('per_case_max_cost_usd', '999'), ('allocated_max_cost_usd', '999'),
                             ('per_case_request_cap', 5), ('automatic_retries', False),
                             ('max_output_tokens', 2048), ('timeout_seconds', 60),
                             ('model', 'forged-model'), ('endpoint', 'https://example.invalid'),
                             ('cases', list(reversed(self.cases))), ('synthetic', True)):
            changed = deepcopy(approved)
            changed['cost_plan'][field] = value
            with self.subTest(field=field), self.assertRaises(policy.Blocked):
                policy.validate_cost_authorization(changed, snapshot, **self.binding)
        changed = deepcopy(approved)
        changed['cost_plan']['cost_policy']['prices'][0]['input_usd_per_million'] = '0.01'
        with self.assertRaises(policy.Blocked):
            policy.validate_cost_authorization(changed, snapshot, **self.binding)

    def test_invalid_binding_never_creates_approval(self):
        snapshot = self.load()
        for field, value in (('run_id', ''), ('run_id', 'bad\nrun'), ('source_sha256', ''),
                             ('input_sha256', 'x' * 64), ('free_gate_sha256', None)):
            with self.subTest(field=field), self.assertRaises(policy.Blocked):
                policy.prepare_authorization(snapshot, **dict(self.binding, **{field: value}))
