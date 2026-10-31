"""Review presentation must retain diagnostics without granting confirmation."""
import copy
import importlib.util
import json
import os
import unittest

from tests.production_planning.test_contract_intake import (
    IntakeFixture, ROOT, TEXT, claim, catalog_bundle,
)
from ontologics.production_planning.contract_intake import (
    extract_contract_text, prepare_contract_demand, read_contract_demand,
)

if importlib.util.find_spec('ontologics.production_planning.demand_review'):
    from ontologics.production_planning.demand_review import build_demand_review
else:
    build_demand_review = None


class ReviewFixture(IntakeFixture):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(build_demand_review, 'Demand review presentation is missing')

    def result(self):
        # A local-catalog unit fixture, never counted as a real public PG query.
        return dict(query=dict(lookup_response=None, configuration_responses=[],
                               configurations=self.bundle, errors=[]), report=self.prepare())

    def view(self, **kwargs):
        return build_demand_review(self.result(), **kwargs)

    def scope(self, view, name, index=0):
        return next(s for s in view['items'][index]['review_scopes'] if s['scope'] == name)


class DemandReviewTests(ReviewFixture, unittest.TestCase):
    def test_current_reference_contract_shows_values_evidence_and_scope(self):
        from examples.production_planning_contract_intake import reference_interpretation
        extracted = extract_contract_text(ROOT/'data/production_planning/contract.txt')
        interpreted = reference_interpretation(extracted, data_version=1)
        report = prepare_contract_demand(extracted, interpreted, self.bundle)
        view = build_demand_review(dict(query=self.result()['query'], report=report))
        self.assertEqual('needs_confirmation', view['status'])
        self.assertEqual(1, view['data_version'])
        item = view['items'][0]
        self.assertEqual('contract-item:planning:001', item['contract_item_candidate']['id'])
        self.assertEqual('config:esp32-poe:L:WROOM', item['contract_item_candidate']['configuration_id'])
        for field, want, line in [('ordered_quantity', 100, 9), ('fulfilled_quantity', 0, 10),
                                  ('allocated_quantity', 0, 10), ('planned_input_quantity', 100, 11)]:
            row = item['fields'][field]
            self.assertEqual(('supported', want), (row['state'], row['value']))
            self.assertEqual(f'text:line:{line}', row['claims'][0]['evidence'][0]['segment_id'])
            self.assertEqual('candidate_for_review', self.scope(view, field)['availability'])
        self.assertEqual('candidate_for_review', self.scope(view, 'configuration')['availability'])
        self.assertFalse(view['confirmation']['verified'])
        self.assertEqual(report, view['revision']['after'])

    def test_date_precision_and_unstated_constraints_cannot_be_completed(self):
        view = self.view()
        item = view['items'][0]
        self.assertEqual(('2026-11-10', 'date'), (
            item['contract_item_candidate']['delivery_due'],
            item['contract_item_candidate']['delivery_due_precision']))
        self.assertEqual('candidate_for_review', self.scope(view, 'delivery_value')['availability'])
        self.assertEqual('needs_material', self.scope(view, 'delivery_arrival')['availability'])
        self.assertEqual('needs_business_decision', self.scope(view, 'constraints')['availability'])
        self.assertEqual('unspecified', item['contract_item_candidate']['constraints_state'])
        self.assertEqual(dict(revision_change=None, substitution=None), item['permissions'])
        self.assertTrue(any(q['code'] == 'DUE_TIME_UNCONFIRMED' for q in view['questions']))

    def test_missing_counts_never_become_zero_or_order(self):
        fields = self.interpreted['items'][0]['fields']
        for field in ('fulfilled_quantity', 'allocated_quantity', 'planned_input_quantity'):
            del fields[field]
        view = self.view()
        for field in ('fulfilled_quantity', 'allocated_quantity', 'planned_input_quantity'):
            self.assertIsNone(view['items'][0]['fields'][field]['value'])
            self.assertEqual('needs_material', self.scope(view, field)['availability'])

    def test_conflicting_claims_and_questions_remain_visible(self):
        self.replace_text(TEXT+'Amendment: 120 piece\n')
        self.interpreted['items'][0]['fields']['ordered_quantity'].append(claim(self.extracted, '120', 120))
        view = self.view()
        row = view['items'][0]['fields']['ordered_quantity']
        self.assertEqual('conflict', row['state'])
        self.assertEqual([100, 120], [c['value'] for c in row['claims']])
        self.assertIsNone(row['value'])
        self.assertEqual('needs_material', self.scope(view, 'ordered_quantity')['availability'])
        self.assertTrue(any(q['code'] == 'CONFLICTING_VALUES' for q in view['questions']))

    def test_unsupported_claim_is_not_a_candidate_value(self):
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['evidence'] = []
        view = self.view()
        row = view['items'][0]['fields']['ordered_quantity']
        self.assertEqual(('unsupported', None), (row['state'], row['value']))
        self.assertEqual(100, row['claims'][0]['value'])
        self.assertEqual('needs_material', self.scope(view, 'ordered_quantity')['availability'])

    def test_invalid_claim_retains_wrong_value_and_citation(self):
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = 120
        view = self.view()
        self.assertEqual('invalid', view['status'])
        row = view['items'][0]['fields']['ordered_quantity']
        self.assertEqual(('invalid', None, 120, '100'), (
            row['state'], row['value'], row['claims'][0]['value'], row['claims'][0]['evidence'][0]['quote']))
        self.assertEqual('blocked_by_report_errors', self.scope(view, 'ordered_quantity')['availability'])
        self.assertIn('EVIDENCE_VALUE_MISMATCH', {e['code'] for e in view['errors']})

    def test_model_only_candidates_are_catalog_values_not_source_claims(self):
        for field in ('revision', 'variant'):
            del self.interpreted['items'][0]['fields'][field]
        view = self.view()
        item = view['items'][0]
        self.assertEqual({'L', 'L1'}, {c['revision'] for c in item['configuration_candidates']})
        self.assertEqual({'WROOM'}, {c['variant'] for c in item['configuration_candidates']})
        self.assertEqual('missing', item['fields']['variant']['state'])
        self.assertIsNone(item['contract_item_candidate']['configuration_id'])
        self.assertEqual('needs_material', self.scope(view, 'configuration')['availability'])

    def test_model_only_selection_proposal_still_needs_source_revision_and_variant(self):
        for field in ('revision', 'variant'):
            del self.interpreted['items'][0]['fields'][field]
        self.interpreted['items'][0]['proposed_configuration_id'] = 'config:esp32-poe:L1:WROOM'
        view = self.view()
        self.assertEqual('config:esp32-poe:L1:WROOM', view['items'][0]['proposed_configuration_id'])
        self.assertEqual('needs_material', self.scope(view, 'configuration')['availability'])

    def test_invalid_source_blocks_all_review_scopes(self):
        self.extracted['source']['sha256'] = '0'*64
        view = self.view()
        self.assertEqual('invalid', view['status'])
        self.assertEqual({'blocked_by_report_errors'},
                         {scope['availability'] for scope in view['items'][0]['review_scopes']})

    def test_valid_flag_provenance_and_confirmation_claims_never_issue_authority(self):
        self.interpreted.update(confirmed=True, confirmation_ref='invented', confirmation={'verified': True})
        current = self.result()
        current['query']['configurations']['provenance'] = 'trusted_approved'
        current['report'].update(status='confirmed', valid=True)
        current['report']['confirmation']['verified'] = True
        view = build_demand_review(current)
        self.assertEqual('needs_confirmation', view['status'])
        self.assertFalse(view['confirmation']['verified'])
        self.assertEqual('unavailable', view['confirmation']['state'])
        self.assertEqual('invented', view['revision']['after']['interpretation']['confirmation_ref'])

    def test_previous_is_untrusted_display_history_even_if_tampered(self):
        previous = self.result()
        previous['report']['confirmation']['verified'] = True
        previous['report']['binding_sha256'] = 'forged-binding'
        previous['report']['items'][0]['planned_input_quantity'] = 999
        view = self.view(previous=previous)
        self.assertFalse(view['confirmation']['verified'])
        self.assertEqual(100, view['items'][0]['planned_input_quantity'])
        self.assertEqual(999, view['revision']['before']['items'][0]['planned_input_quantity'])
        self.assertFalse(view['revision']['history_verified'])

    def test_failed_lookup_keeps_original_envelope_and_no_normal_review_items(self):
        response = dict(ok=False, data=None, data_version=1,
                        errors=[dict(code='VERSION_NOT_FOUND')], run_id='failure-run')
        class Session:
            def call(self, tool, args):
                return copy.deepcopy(response)
        result = read_contract_demand(Session(), {'data_version': 1}, self.extracted, self.interpreted)
        view = build_demand_review(result)
        self.assertEqual('query_failed', view['status'])
        self.assertEqual([], view['items'])
        self.assertIsNone(view['revision']['after'])
        self.assertEqual(response, view['query']['lookup_response'])
        self.assertEqual('VERSION_NOT_FOUND', view['query']['lookup_response']['errors'][0]['code'])

    def test_query_errors_and_partial_configuration_failures_are_not_empty_success(self):
        for mode in ('exception', 'detail_failure', 'empty_bundle'):
            result = self.result()
            if mode == 'exception':
                result['query']['errors'] = [dict(code='PUBLIC_QUERY_EXCEPTION')]
            elif mode == 'detail_failure':
                result['query']['configuration_responses'] = [dict(ok=False, errors=[dict(code='DB_FAILURE')])]
            else:
                result['query']['configurations'] = None
            view = build_demand_review(result)
            self.assertEqual('query_failed', view['status'], mode)
            self.assertEqual([], view['items'])
            self.assertFalse(view['confirmation']['verified'])

    def test_all_inputs_and_public_responses_are_independent_copies(self):
        current, previous = self.result(), self.result()
        snapshot = copy.deepcopy((current, previous))
        view = build_demand_review(current, previous=previous)
        self.assertEqual(snapshot, (current, previous))
        view['query']['configurations']['candidates'][0]['configuration']['id'] = 'changed'
        view['items'][0]['fields']['ordered_quantity']['claims'][0]['value'] = 999
        view['revision']['before']['items'].clear()
        self.assertEqual(snapshot, (current, previous))

    def test_unchanged_history_has_no_changes(self):
        before = self.result()
        view = self.view(previous=before)
        self.assertFalse(view['revision']['changed'])
        self.assertEqual([], view['revision']['changes'])

    def test_source_quantity_selection_version_and_citation_changes_show_before_after(self):
        before = self.result()
        variants = []
        self.replace_text(TEXT+'Additional clause.\n')
        variants.append((self.result(), 'source.sha256'))
        self.replace_text(TEXT)
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = 120
        variants.append((self.result(), 'items[0].fields.ordered_quantity.claims[0].value'))
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = 100
        self.interpreted['items'][0]['proposed_configuration_id'] = 'config:esp32-poe:L:WROOM'
        variants.append((self.result(), 'items[0].proposed_configuration_id'))
        self.interpreted['items'][0].pop('proposed_configuration_id')
        self.interpreted['data_version'] = self.bundle['data_version'] = 2
        variants.append((self.result(), 'binding.data_version'))
        self.interpreted['data_version'] = self.bundle['data_version'] = 1
        self.interpreted['items'][0]['fields']['ordered_quantity'] = [claim(self.extracted, '100', 100, occurrence=1)]
        variants.append((self.result(), 'items[0].fields.ordered_quantity.claims[0].evidence[0].segment_id'))
        for result, expected_path in variants:
            with self.subTest(path=expected_path):
                view = build_demand_review(result, previous=before)
                self.assertTrue(view['revision']['changed'])
                changes = {c['path']: c for c in view['revision']['changes']}
                self.assertIn(expected_path, changes)
                self.assertNotEqual(changes[expected_path]['before'], changes[expected_path]['after'])
                self.assertEqual(100, view['revision']['before']['items'][0]['fields']['ordered_quantity']['value'])
                self.assertEqual(result['report'], view['revision']['after'])
                self.assertFalse(view['confirmation']['verified'])

    def test_unknown_and_malformed_claims_not_in_binding_are_retained_in_history(self):
        before = self.result()
        self.interpreted['items'][0]['fields']['unexpected'] = [{'value': 999, 'evidence': []}]
        self.interpreted['items'][0]['fields']['unit'] = 'malformed-claims'
        view = self.view(previous=before)
        after = view['revision']['after']['interpretation']['items'][0]['fields']
        self.assertEqual([{'value': 999, 'evidence': []}], after['unexpected'])
        self.assertEqual('malformed-claims', after['unit'])
        changes = {c['path']: c for c in view['revision']['changes']}
        self.assertFalse(changes['interpretation.items[0].fields.unexpected']['before_present'])
        self.assertTrue(changes['interpretation.items[0].fields.unexpected']['after_present'])

    def test_deleted_and_type_changed_rejected_claims_remain_in_comparison(self):
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = True
        before = self.result()
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = 1
        self.interpreted['items'][0]['fields'].pop('fulfilled_quantity')
        view = self.view(previous=before)
        changes = {c['path']: c for c in view['revision']['changes']}
        scalar = changes['interpretation.items[0].fields.ordered_quantity[0].value']
        self.assertIs(True, scalar['before'])
        self.assertIs(type(scalar['after']), int)
        removed = changes['interpretation.items[0].fields.fulfilled_quantity']
        self.assertTrue(removed['before_present'])
        self.assertFalse(removed['after_present'])

    def test_correction_keeps_the_rejected_claim_and_actual_candidate_before_after(self):
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = 120
        before = self.result()
        self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value'] = 100
        view = self.view(previous=before)
        old = view['revision']['before']['items'][0]
        new = view['revision']['after']['items'][0]
        self.assertIsNone(old['contract_item_candidate']['ordered_quantity'])
        self.assertEqual(120, old['fields']['ordered_quantity']['claims'][0]['value'])
        self.assertEqual('100', old['fields']['ordered_quantity']['claims'][0]['evidence'][0]['quote'])
        self.assertEqual(100, new['contract_item_candidate']['ordered_quantity'])
        self.assertFalse(view['confirmation']['verified'])

    def test_multiple_items_have_separate_scope_paths(self):
        self.interpreted['items'].append(dict(fields={}))
        view = self.view()
        self.assertEqual('items[1].fields.ordered_quantity', self.scope(view, 'ordered_quantity', 1)['paths'][0])
        self.assertEqual('candidate_for_review', self.scope(view, 'ordered_quantity', 0)['availability'])
        self.assertEqual('needs_material', self.scope(view, 'ordered_quantity', 1)['availability'])


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit isolated TEST_DATABASE_URL required')
class PublicPostgresDemandReviewTests(ReviewFixture, unittest.TestCase):
    def test_public_pg_exact_version_reconnect_and_failure_remain_unconfirmed(self):
        from tests.db import database_for_test
        from ontologics.production_planning.host import PlanningHost as CommonHost
        from ontologics.identity import DevelopmentIdentityProvider, UserIdentity, DevelopmentContext
        from ontologics.production_planning.persistence import PlanningService as Service
        from examples.production_planning_contract_intake import reference_interpretation
        settings = database_for_test(self)
        seed = Service(settings, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read']}})
        try:
            seeded = seed.bootstrap('fixture', json.loads((ROOT/'data/catalog.json').read_text())['nodes'],
                                    request_key='review-seed')
            self.assertTrue(seeded['ok'])
        finally:
            seed.close()
        host = CommonHost(DevelopmentIdentityProvider([
            UserIdentity('review-reader', frozenset({'read'}))], enabled=True), settings)
        extracted = extract_contract_text(ROOT/'data/production_planning/contract.txt')
        interpreted = reference_interpretation(extracted, data_version=1)
        for _ in range(2):
            with host.request(DevelopmentContext('review-reader')) as session:
                view = build_demand_review(read_contract_demand(session, {'data_version': 1}, extracted, interpreted))
            self.assertEqual('needs_confirmation', view['status'])
            self.assertEqual(1, view['query']['lookup_response']['data_version'])
            self.assertEqual(2, len(view['query']['configuration_responses']))
            self.assertEqual(100, view['items'][0]['planned_input_quantity'])
            self.assertFalse(view['confirmation']['verified'])
        interpreted['data_version'] = 999
        with host.request(DevelopmentContext('review-reader')) as session:
            view = build_demand_review(read_contract_demand(session, {'data_version': 999}, extracted, interpreted))
        self.assertEqual('query_failed', view['status'])
        self.assertEqual('VERSION_NOT_FOUND', view['query']['lookup_response']['errors'][0]['code'])
