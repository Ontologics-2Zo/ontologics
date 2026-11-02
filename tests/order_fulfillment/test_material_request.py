"""Common-validated B candidate checks; no model, database or approval calls."""
from copy import deepcopy
import json
import unittest

from examples.order_fulfillment.b_material_request import load_examples, run_example
from ontologics import domain_schema, model
from ontologics.order_fulfillment import material_request_schema as schema
from ontologics.order_fulfillment.material_request_validation import (
    validate_request_candidate, validate_request_transition,
)


class MaterialRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = load_examples()
        cls.catalog = json.loads((model.ROOT / 'data/catalog.json').read_text())['nodes']

    def setUp(self):
        self.request = deepcopy(self.demo['requests']['new_30'])
        self.facts = deepcopy(self.catalog + self.demo['supporting_nodes'])

    def report(self, request=None, *, facts=None):
        return validate_request_candidate((self.facts if facts is None else facts)
                                          + [self.request if request is None else request])

    def assert_code(self, report, code):
        self.assertFalse(report['valid'], report)
        self.assertIn(code, {error['code'] for error in report['errors']}, report)

    def round_facts(self, configuration_id=None):
        common = {'origin': 'synthetic', 'evidence': self.request['evidence']}
        contract = dict(common, id='contract:material-review:demo', type='Contract',
                        company='Demo factory', customer_name='Demo customer')
        item = dict(common, id='item:material-review:demo', type='ContractItem',
                    contract_id=contract['id'], configuration_id=configuration_id or self.request['configuration_id'],
                    quantity=100, unit='piece')
        delivery = dict(common, id='round:material-review:demo', type='DeliveryRound',
                        contract_item_id=item['id'], quantity=50, unit='piece',
                        due_at='2026-11-06T15:00:00+09:00', due_basis='arrival', destination='Demo customer dock')
        return contract, item, delivery

    def test_three_examples_and_replacement_quantities(self):
        result = run_example()
        for report in (*result['candidates'].values(), *result['transitions'].values()):
            self.assertTrue(report['valid'], report)
        self.assertEqual({'material-review:demo:001': 40, 'material-review:demo:002': 10},
                         result['latest_additional_quantities'])
        self.assertFalse(result['stored'])
        self.assertFalse(result['approved'])
        self.assertFalse(result['neo4j_verified'])

    def test_request_is_statically_registered_and_validation_does_not_mutate_schema(self):
        before = deepcopy((model.SCHEMA, domain_schema.SCHEMA))
        self.assertTrue(self.report()['valid'])
        common = model.validate_graph(self.facts + [self.request])
        self.assertTrue(common['valid'], common)
        self.assertEqual([], common['errors'])
        self.assertEqual(before, (model.SCHEMA, domain_schema.SCHEMA))
        self.assertIn(schema.REQUEST_TYPE, model.SCHEMA)
        self.assertIsNot(schema.SCHEMA[schema.REQUEST_TYPE], domain_schema.SCHEMA[schema.REQUEST_TYPE])

    def test_raw_sources_have_valid_hashes_and_distinct_evidence(self):
        self.assertTrue(model.validate_graph(self.facts)['valid'])
        ids = [n['id'] for n in self.demo['supporting_nodes']]
        self.assertEqual(8, len(set(ids)))
        baseline = self.request['baseline_plan_evidence_id']
        for request in self.demo['requests'].values():
            self.assertIn(baseline, request['evidence'])
        new, revised = (self.demo['requests'][key] for key in ('new_30', 'revised_40'))
        self.assertTrue(set(revised['evidence']) - set(new['evidence']))

    def test_inputs_are_not_modified(self):
        nodes = self.facts + [self.request]
        before = deepcopy(nodes)
        self.assertTrue(validate_request_candidate(nodes)['valid'])
        self.assertEqual(before, nodes)

    def test_rejects_invalid_finished_quantity_including_booleans_and_floats(self):
        for quantity in (None, 0, -1, True, False, 30.0, 1.5, '30', [], {}):
            with self.subTest(quantity=quantity):
                self.assert_code(self.report(dict(self.request, quantity=quantity)), 'INVALID_QUANTITY')
        self.assert_code(self.report(dict(self.request, unit='component')), 'UNSUPPORTED_UNIT')

    def test_metadata_versions_are_integers_not_boolean_or_graph_revision_substitutes(self):
        for field, values in (('request_revision', (0, -1, True, 1.0, '1')),
                              ('reference_data_version', (-1, True, 0.0, 'graph-0.3'))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_code(self.report(dict(self.request, **{field: value})), 'INVALID_FIELD_TYPE')

    def test_missing_required_fields_and_unknown_fields_are_rejected(self):
        for field in ('configuration_id', 'quantity', 'evidence', 'reason', 'readiness'):
            request = dict(self.request)
            del request[field]
            with self.subTest(field=field):
                self.assert_code(self.report(request), 'MISSING_FIELD')
        self.assert_code(self.report(dict(self.request, read_by_a=True)), 'UNKNOWN_FIELD')

    def test_common_validation_reports_each_violation_once(self):
        cases = (
            ('configuration_id', None, 'MISSING_FIELD'),
            ('reason', None, 'MISSING_FIELD'),
            ('origin', 'real', 'DEMO_OPERATIONS_ONLY'),
            ('read_by_a', True, 'UNKNOWN_FIELD'),
            ('configuration_id', 'missing-product', 'INVALID_REFERENCE'),
            ('evidence', ['missing-evidence'], 'EVIDENCE_NOT_FOUND'),
            ('evidence', [self.request['evidence'][0]] * 2, 'INVALID_EVIDENCE'),
        )
        for field, value, code in cases:
            request = dict(self.request, **{field: value})
            if value is None:
                del request[field]
            with self.subTest(field=field, code=code):
                errors = [error for error in self.report(request)['errors']
                          if error['node_id'] == request['id'] and error['code'] == code]
                self.assertEqual(1, len(errors), errors)

    def test_recipient_role_is_materials_without_implying_a_processing(self):
        self.assertEqual('materials', self.request['recipient_role'])
        for role in ('production_planning', 'claim_response', 'arbitrary', '', None, [], {}):
            with self.subTest(role=role):
                self.assert_code(self.report(dict(self.request, recipient_role=role)), 'INVALID_ENUM')

    def test_a_specific_request_does_not_require_redundant_recipient_role(self):
        request = dict(self.request)
        del request['recipient_role']
        self.assertTrue(self.report(request)['valid'], self.report(request))

    def test_unregistered_configuration_and_false_real_origin_are_rejected(self):
        self.assert_code(self.report(dict(self.request, configuration_id='external-document-12')), 'INVALID_REFERENCE')
        self.assert_code(self.report(dict(self.request, origin='real')), 'DEMO_OPERATIONS_ONLY')

    def test_missing_duplicate_wrong_origin_and_baseline_only_evidence_are_rejected(self):
        for evidence, code in (([], 'EVIDENCE_REQUIRED'), (['missing'], 'EVIDENCE_NOT_FOUND'),
                               ([self.request['evidence'][0]] * 2, 'INVALID_EVIDENCE'),
                               ([self.request['baseline_plan_evidence_id']], 'REQUEST_EVIDENCE_REQUIRED'),
                               (['evidence:olimex:L:WROOM:row-1'], 'EVIDENCE_NOT_FOUND')):
            with self.subTest(evidence=evidence):
                self.assert_code(self.report(dict(self.request, evidence=evidence)), code)
        real_eid = next(n['id'] for n in self.catalog if n['type'] == 'Evidence')
        self.assert_code(self.report(dict(self.request, evidence=self.request['evidence'] + [real_eid])),
                         'EVIDENCE_ORIGIN_MISMATCH')

    def test_source_hash_and_evidence_source_are_checked_by_common_validation(self):
        for field, value in (('sha256', '0' * 64), ('path', 'data/sources/not-present.txt')):
            facts = deepcopy(self.facts)
            source = next(n for n in facts if n['id'] == 'source:material-review:demo:request_30')
            source[field] = value
            self.assertFalse(self.report(facts=facts)['valid'])
        facts = deepcopy(self.facts)
        evidence = next(n for n in facts if n['id'] == self.request['evidence'][1])
        evidence['source_id'] = 'missing-source'
        self.assert_code(self.report(facts=facts), 'INVALID_REFERENCE')

    def test_date_only_is_preserved_without_invented_time(self):
        self.assertTrue(self.report()['valid'])
        self.assertEqual('2026-11-06', self.request['need_date'])
        self.assertNotIn('need_at', self.request)
        for value in ('2026-02-30', '20261106', '2026-11-06T00:00:00+09:00', None, [], {}):
            with self.subTest(value=value):
                self.assert_code(self.report(dict(self.request, need_date=value)), 'INVALID_NEED_DATE')

    def test_explicit_timestamp_requires_offset_and_excludes_date_only(self):
        request = dict(self.request, need_at='2026-11-06T15:00:00+09:00')
        del request['need_date']
        self.assertTrue(self.report(request)['valid'])
        for value in ('2026-11-06', '2026-11-06T15:00:00', [], None):
            with self.subTest(value=value):
                self.assert_code(self.report(dict(request, need_at=value)), 'INVALID_TIME')
        self.assert_code(self.report(dict(request, need_date='2026-11-06')), 'INVALID_NEED_DATE')
        del request['need_at']
        self.assert_code(self.report(request), 'INVALID_NEED_DATE')

    def test_unknown_need_and_conditions_require_supplementation(self):
        request = dict(self.request, need_mode='unknown', readiness='needs_supplementation',
                       conditions_state='unknown', supplementation_reason='Confirm need and baseline conditions')
        del request['need_date']
        del request['need_basis']
        del request['baseline_plan_evidence_id']
        self.assertTrue(self.report(request)['valid'])
        self.assert_code(self.report(dict(request, readiness='ready')), 'REQUEST_NOT_READY')
        del request['supplementation_reason']
        self.assert_code(self.report(request), 'SUPPLEMENTATION_REASON_REQUIRED')

    def test_ready_requires_baseline_and_changed_conditions_need_description(self):
        request = dict(self.request)
        del request['baseline_plan_evidence_id']
        self.assert_code(self.report(request), 'REQUEST_NOT_READY')
        self.assert_code(self.report(dict(self.request, conditions_state='changed')), 'CONDITIONS_NOTE_REQUIRED')
        self.assertTrue(self.report(dict(self.request, conditions_state='changed', conditions_note='New packaging'))['valid'])

    def test_arrival_needs_destination_and_can_reuse_round_destination(self):
        request = dict(self.request, need_basis='arrival')
        self.assert_code(self.report(request), 'DESTINATION_REQUIRED')
        self.assertTrue(self.report(dict(request, destination='Customer dock'))['valid'])
        contract, item, delivery = self.round_facts()
        self.assertTrue(self.report(dict(request, delivery_round_id=delivery['id']),
                                    facts=self.facts + [contract, item, delivery])['valid'])

    def test_round_only_reference_still_checks_product(self):
        contract, item, delivery = self.round_facts('config:esp32-poe:L1:WROOM')
        request = dict(self.request, delivery_round_id=delivery['id'])
        self.assertNotIn('contract_item_id', request)
        self.assert_code(self.report(request, facts=self.facts + [contract, item, delivery]), 'CONFIGURATION_MISMATCH')

    def test_item_product_and_round_ownership_are_checked(self):
        contract, item, delivery = self.round_facts()
        other = dict(item, id='item:other')
        request = dict(self.request, delivery_round_id=delivery['id'], contract_item_id=other['id'])
        self.assert_code(self.report(request, facts=self.facts + [contract, item, other, delivery]), 'CONTRACT_ITEM_MISMATCH')
        item['configuration_id'] = 'config:esp32-poe:L1:WROOM'
        self.assert_code(self.report(dict(self.request, contract_item_id=item['id']),
                                    facts=self.facts + [contract, item]), 'CONFIGURATION_MISMATCH')

    def test_inherited_need_needs_actual_round_and_cannot_mix_explicit_dates(self):
        request = dict(self.request, need_mode='inherit_round')
        del request['need_basis']
        del request['need_date']
        self.assert_code(self.report(request), 'DELIVERY_ROUND_REQUIRED')
        contract, item, delivery = self.round_facts()
        request['delivery_round_id'] = delivery['id']
        facts = self.facts + [contract, item, delivery]
        self.assertTrue(self.report(request, facts=facts)['valid'])
        self.assert_code(self.report(dict(request, need_date='2026-11-06'), facts=facts), 'CONFLICTING_NEED')
        delivery['due_basis'] = 'production_complete'
        self.assert_code(self.report(request, facts=facts), 'UNSUPPORTED_DUE_BASIS')

    def test_unknown_need_can_be_completed_with_a_confirmed_round(self):
        previous = dict(self.request, need_mode='unknown', readiness='needs_supplementation',
                        supplementation_reason='납품 회차 확인 대기')
        previous.pop('need_basis')
        previous.pop('need_date')
        contract, item, delivery = self.round_facts()
        facts = self.facts + [contract, item, delivery]
        current = dict(self.demo['requests']['revised_40'], need_mode='inherit_round',
                       delivery_round_id=delivery['id'])
        current.pop('need_basis')
        current.pop('need_date')
        self.assertTrue(self.report(previous, facts=facts)['valid'])
        self.assertTrue(self.report(current, facts=facts)['valid'])
        self.assertTrue(validate_request_transition(previous, current,
                        expected_request_revision=1)['valid'])

        other_round = dict(delivery, id='round:already-confirmed')
        previous['delivery_round_id'] = other_round['id']
        self.assertTrue(self.report(previous, facts=facts + [other_round])['valid'])
        self.assert_code(validate_request_transition(previous, current,
                         expected_request_revision=1), 'REQUEST_SCOPE_CHANGED')

    def test_bad_json_fields_do_not_raise(self):
        fields = set(self.request) - {'type', 'id'}
        for field in fields:
            for bad in ([], {}, None):
                with self.subTest(field=field, bad=bad):
                    self.assertFalse(self.report(dict(self.request, **{field: bad}))['valid'])

    def test_duplicate_ids_and_malformed_graphs_are_rejected(self):
        self.assert_code(validate_request_candidate(self.facts + [self.request, self.request]), 'DUPLICATE_ID')
        self.assert_code(validate_request_candidate([None]), 'INVALID_NODE')
        for value in (None, {}, 'graph'):
            self.assert_code(validate_request_candidate(value), 'INVALID_GRAPH_SIZE')

    def test_transition_is_replacement_not_sum_and_rejects_stale_revision(self):
        previous = self.demo['requests']['new_30']
        current = self.demo['requests']['revised_40']
        before = deepcopy((previous, current))
        self.assertTrue(validate_request_transition(previous, current, expected_request_revision=1)['valid'])
        self.assertEqual(40, current['quantity'])
        self.assertEqual(before, (previous, current))
        self.assert_code(validate_request_transition(current, current, expected_request_revision=1), 'STALE_REQUEST_REVISION')
        for expected in (True, -1, '1', None):
            self.assert_code(validate_request_transition(previous, current, expected_request_revision=expected),
                             'INVALID_REQUEST_REVISION')

    def test_transition_requires_new_evidence_and_incremented_revision(self):
        previous = self.demo['requests']['new_30']
        current = self.demo['requests']['revised_40']
        self.assert_code(validate_request_transition(previous, dict(current, evidence=previous['evidence']),
                                                    expected_request_revision=1), 'NEW_EVIDENCE_REQUIRED')
        self.assert_code(validate_request_transition(previous, dict(current, request_revision=1),
                                                    expected_request_revision=1), 'INVALID_REQUEST_REVISION')
        self.assert_code(validate_request_transition(None, current, expected_request_revision=0), 'INVALID_REQUEST_REVISION')

    def test_scope_changes_need_separate_requests_and_independent_demands_keep_ids(self):
        previous = self.demo['requests']['new_30']
        current = self.demo['requests']['revised_40']
        for field, value in (('configuration_id', 'config:esp32-poe:L1:WROOM'),
                             ('need_date', '2026-11-09'), ('need_basis', 'arrival'),
                             ('contract_item_id', 'item:different-contract'),
                             ('baseline_plan_evidence_id', 'evidence:different-baseline')):
            with self.subTest(field=field):
                self.assert_code(validate_request_transition(previous, dict(current, **{field: value}),
                                                            expected_request_revision=1), 'REQUEST_SCOPE_CHANGED')
        independent = self.demo['requests']['independent_10']
        self.assert_code(validate_request_transition(previous, independent, expected_request_revision=1), 'REQUEST_ID_MISMATCH')
        self.assertTrue(validate_request_transition(None, independent, expected_request_revision=0)['valid'])


if __name__ == '__main__':
    unittest.main()
