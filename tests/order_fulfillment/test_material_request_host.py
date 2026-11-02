"""B request drafts through real PostgreSQL and development human identities.

These tests do not claim live authentication, A intake or Neo4j publication.
"""
from copy import deepcopy
import json
import unittest

from examples.order_fulfillment.b_material_request import load_examples
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.order_fulfillment.host import CommonHost
from ontologics.service import Service
from tests.db import database_for_test


AUTHOR = UserIdentity('b-material-author', frozenset({'read', 'draft', 'review'}))
REVIEWER = UserIdentity('b-material-reviewer', frozenset({'read', 'draft', 'review'}))
READER = UserIdentity('b-material-reader', frozenset({'read'}))
MANAGED = {'type', 'origin', 'request_revision', 'reference_data_version'}
REQUEST_TYPE = 'AdditionalMaterialReviewRequest'


class MaterialRequestHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples = load_examples()
        cls.catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']

    def setUp(self):
        self.database = database_for_test(self)
        self.identities = DevelopmentIdentityProvider([AUTHOR, REVIEWER, READER], enabled=True)
        self.host = CommonHost(self.identities, self.database)
        self.audit = Service(self.database, principals={
            'fixture': {'kind': 'human', 'roles': ['seed', 'read', 'audit']}})
        self.addCleanup(self.audit.close)
        self.ok(self.audit.bootstrap('fixture', self.catalog, request_key='seed-catalog'))

    def ok(self, response):
        self.assertTrue(response['ok'], response)
        return response

    def error(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def business_request(self, name='new_30'):
        return {key: deepcopy(value) for key, value in self.examples['requests'][name].items()
                if key not in MANAGED}

    def support(self, request):
        evidence = [node for node in self.examples['supporting_nodes']
                    if node['id'] in request['evidence']]
        source_ids = {node['source_id'] for node in evidence}
        return deepcopy([node for node in self.examples['supporting_nodes']
                         if node['id'] in source_ids or node in evidence])

    def prepare(self, request=None, *, user=AUTHOR, key='new-request', **kwargs):
        request = self.business_request() if request is None else request
        arguments = dict(supporting_nodes=self.support(request), expected_data_version=1,
                         expected_request_revision=0, request_key=key)
        arguments.update(kwargs)
        with self.host.request(DevelopmentContext(user.subject)) as session:
            return session.material_request_draft(request, **arguments)

    def review(self, draft, *, user=REVIEWER, key='review-request'):
        with self.host.request(DevelopmentContext(user.subject)) as session:
            return session.review(draft['draft_id'], draft['draft_version'], decision='approve',
                                  note='개발용 원문·추가 수량·근거 대조 검토 대역', request_key=key)

    def apply(self, draft, *, key='apply-request'):
        return self.host.apply_approved(draft['draft_id'], draft['draft_version'], request_key=key)

    def approve_and_apply(self, draft, *, key='normal'):
        self.ok(self.review(draft, key=key + '-review'))
        return self.ok(self.apply(draft, key=key + '-apply'))

    def lookup(self, *, version, node_id=None, kind=None):
        with self.host.request(DevelopmentContext(READER.subject)) as session:
            return session.call('lookup', dict(node_id=node_id, kind=kind, version=version))

    def test_new_replacement_and_independent_demand_preserve_approved_history(self):
        first = self.ok(self.prepare())['data']
        self.assertTrue(first['validation']['valid'], first)
        self.assertEqual('awaiting_review', first['status'])
        self.error(self.lookup(version=1, node_id=self.business_request()['id']), 'NOT_FOUND')
        self.assertEqual(2, self.approve_and_apply(first, key='new')['data_version'])

        changed = self.ok(self.prepare(self.business_request('revised_40'), key='amend',
                         expected_data_version=2, expected_request_revision=1))['data']
        self.assertEqual(3, self.approve_and_apply(changed, key='amend')['data_version'])
        independent = self.ok(self.prepare(self.business_request('independent_10'), key='independent',
                             expected_data_version=3))['data']
        self.assertEqual(4, self.approve_and_apply(independent, key='independent')['data_version'])

        old = self.ok(self.lookup(version=2, node_id=self.business_request()['id']))['data'][0]
        current = self.ok(self.lookup(version=4, kind=REQUEST_TYPE))['data']
        self.assertEqual((30, 1, 1), (old['quantity'], old['request_revision'], old['reference_data_version']))
        self.assertEqual({self.business_request()['id']: (40, 2, 2),
                          self.business_request('independent_10')['id']: (10, 1, 3)},
                         {node['id']: (node['quantity'], node['request_revision'], node['reference_data_version'])
                          for node in current})
        for node in current:
            for eid in node['evidence']:
                evidence = self.ok(self.lookup(version=4, node_id=eid))['data'][0]
                source = self.ok(self.lookup(version=4, node_id=evidence['source_id']))['data'][0]
                self.assertEqual('synthetic', source['origin'])
                self.assertTrue(source['sha256'])

    def test_approved_incomplete_request_is_completed_without_losing_history(self):
        incomplete = self.business_request()
        incomplete.update(need_mode='unknown', conditions_state='unknown',
                          readiness='needs_supplementation',
                          supplementation_reason='개발 시험: 기준 계획과 필요 날짜 확인 대기')
        for field in ('baseline_plan_evidence_id', 'need_basis', 'need_date'):
            incomplete.pop(field)
        first = self.ok(self.prepare(incomplete))['data']
        self.assertTrue(first['validation']['valid'], first)
        self.error(self.review(first, user=AUTHOR, key='incomplete-self-review'), 'SELF_APPROVAL_FORBIDDEN')
        self.assertEqual(2, self.approve_and_apply(first, key='incomplete')['data_version'])

        complete = self.business_request('revised_40')
        draft = self.ok(self.prepare(complete, key='complete', expected_data_version=2,
                                     expected_request_revision=1))['data']
        self.assertTrue(draft['validation']['valid'], draft)
        self.assertEqual('awaiting_review', draft['status'])
        self.assertEqual(3, self.approve_and_apply(draft, key='complete')['data_version'])
        old = self.ok(self.lookup(version=2, node_id=incomplete['id']))['data'][0]
        current = self.ok(self.lookup(version=3, node_id=incomplete['id']))['data'][0]
        self.assertEqual(('needs_supplementation', 'unknown', 1),
                         (old['readiness'], old['need_mode'], old['request_revision']))
        self.assertNotIn('baseline_plan_evidence_id', old)
        self.assertNotIn('need_date', old)
        self.assertEqual(('ready', 'explicit', 2, 2, 40),
                         (current['readiness'], current['need_mode'], current['request_revision'],
                          current['reference_data_version'], current['quantity']))
        self.assertEqual(complete['need_date'], current['need_date'])
        self.assertEqual(complete['baseline_plan_evidence_id'], current['baseline_plan_evidence_id'])

        # Returning to a supplementation status must not unlock confirmed scope.
        for field, value in (('need_date', '2026-11-07'),
                             ('baseline_plan_evidence_id', incomplete['evidence'][-1])):
            with self.subTest(field=field):
                changed = dict(complete, readiness='needs_supplementation',
                               supplementation_reason='개발 시험: 확정 범위 변경 시도',
                               evidence=complete['evidence'] + [incomplete['evidence'][-1]],
                               **{field: value})
                response = self.prepare(changed, key='confirmed-' + field,
                                        expected_data_version=3, expected_request_revision=2)
                self.error(response, 'VALIDATION_FAILED')
                self.assertIn('REQUEST_SCOPE_CHANGED',
                              {error['code'] for error in response['errors'][0]['details']['errors']})
        self.assertEqual(current, self.ok(self.lookup(version=3, node_id=incomplete['id']))['data'][0])

    def test_missing_baseline_is_completed_while_confirmed_date_is_preserved(self):
        incomplete = self.business_request()
        incomplete.pop('baseline_plan_evidence_id')
        incomplete.update(readiness='needs_supplementation',
                          supplementation_reason='개발 시험: 기준 계획 확인 대기')
        first = self.ok(self.prepare(incomplete))['data']
        self.assertTrue(first['validation']['valid'], first)
        self.approve_and_apply(first, key='baseline-pending')
        complete = self.business_request('revised_40')
        draft = self.ok(self.prepare(complete, key='baseline-complete', expected_data_version=2,
                                     expected_request_revision=1))['data']
        self.assertTrue(draft['validation']['valid'], draft)
        self.assertEqual(3, self.approve_and_apply(draft, key='baseline-complete')['data_version'])
        stored = self.ok(self.lookup(version=3, node_id=incomplete['id']))['data'][0]
        self.assertEqual(incomplete['need_date'], stored['need_date'])
        self.assertEqual(complete['baseline_plan_evidence_id'], stored['baseline_plan_evidence_id'])
        self.assertEqual(('ready', 2), (stored['readiness'], stored['request_revision']))

    def test_actor_is_host_bound_and_registration_is_not_an_agent_tool(self):
        draft = self.ok(self.prepare())['data']
        with self.host.request(DevelopmentContext(AUTHOR.subject)) as session:
            stored = self.ok(session.call('get_draft', {'draft_id': draft['draft_id'], 'version': 1}))['data']
            self.assertEqual(AUTHOR.actor, stored['author'])
            for tool in ('prepare_material_request', 'material_request_draft'):
                self.error(session.agent('product').call(tool, {}), 'TOOL_NOT_ALLOWED')
        for field in ('actor', 'author', 'reviewer', 'principals'):
            request = dict(self.business_request(), **{field: REVIEWER.actor})
            response = self.prepare(request, key='identity-' + field)
            if response['ok']:
                self.assertFalse(response['data']['validation']['valid'], response)
                self.assertEqual('needs_changes', response['data']['status'])
            else:
                self.assertEqual('INVALID_INPUT', response['errors'][0]['code'], response)

    def test_reader_cannot_register_and_author_cannot_self_approve(self):
        self.error(self.prepare(user=READER), 'FORBIDDEN')
        draft = self.ok(self.prepare())['data']
        self.error(self.review(draft, user=AUTHOR, key='self-review'), 'SELF_APPROVAL_FORBIDDEN')
        self.error(self.review(draft, user=READER, key='reader-review'), 'FORBIDDEN')
        self.error(self.apply(draft, key='unapproved'), 'APPROVAL_REQUIRED')
        self.approve_and_apply(draft)

    def test_exact_retry_replays_after_data_advances_but_changed_input_conflicts(self):
        original = self.prepare(key='retry')
        draft = self.ok(original)['data']
        self.approve_and_apply(draft)
        replay = self.ok(self.prepare(key='retry'))
        self.assertEqual(draft['draft_id'], replay['data']['draft_id'])
        self.assertEqual(draft['draft_version'], replay['data']['draft_version'])
        self.assertIn('IDEMPOTENT_REPLAY', {warning['code'] for warning in replay['warnings']})
        self.error(self.prepare(dict(self.business_request(), quantity=31), key='retry'), 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(1, self.audit.db.execute('SELECT count(*) AS count FROM drafts').fetchone()['count'])

    def test_stale_data_and_approved_request_revision_are_rejected(self):
        self.error(self.prepare(expected_data_version=0, key='old-base'), 'STALE_BASE')
        self.error(self.prepare(expected_request_revision=1, key='wrong-new-revision'), 'STALE_REQUEST_REVISION')
        self.approve_and_apply(self.ok(self.prepare())['data'])
        self.error(self.prepare(self.business_request('revised_40'), key='old-revision',
                               expected_data_version=2, expected_request_revision=0), 'STALE_REQUEST_REVISION')
        self.error(self.prepare(self.business_request('revised_40'), key='old-data',
                               expected_data_version=1, expected_request_revision=1), 'STALE_BASE')
        for field in ('expected_data_version', 'expected_request_revision'):
            for value in (True, -1, 1.0, '1'):
                arguments = dict(expected_data_version=2, expected_request_revision=1)
                arguments[field] = value
                self.error(self.prepare(key=field + repr(value), **arguments),
                           'INVALID_VERSION' if field == 'expected_data_version' else 'INVALID_INPUT')

    def test_host_managed_metadata_cannot_be_supplied_by_caller(self):
        for field, value in (('type', REQUEST_TYPE), ('origin', 'real'),
                             ('request_revision', 7), ('reference_data_version', 1)):
            self.error(self.prepare(dict(self.business_request(), **{field: value}), key='managed-' + field),
                       'INVALID_INPUT')

    def test_support_bundle_cannot_modify_other_business_domains(self):
        invalid = [self.catalog[0], dict(self.examples['supporting_nodes'][0], origin='real'),
                   {'id': 'unrelated', 'type': 'Shipment', 'origin': 'synthetic'}]
        for index, node in enumerate(invalid):
            self.error(self.prepare(supporting_nodes=[node], key='support-' + str(index)), 'INVALID_REQUEST_SUPPORT')
        self.error(self.prepare(supporting_nodes={}, key='support-object'), 'INVALID_REQUEST_SUPPORT')

    def test_missing_reference_and_tampered_source_never_get_approved(self):
        for name in ('configuration', 'evidence', 'hash'):
            request = self.business_request()
            support = self.support(request)
            if name == 'configuration':
                request['configuration_id'] = 'configuration:not-registered'
            elif name == 'evidence':
                request['evidence'].append('evidence:not-registered')
            else:
                next(node for node in support if node['type'] == 'SourceDocument')['sha256'] = '0' * 64
            draft = self.ok(self.prepare(request, supporting_nodes=support, key='invalid-' + name))['data']
            self.assertEqual('needs_changes', draft['status'])
            self.assertFalse(draft['validation']['valid'])
            self.error(self.review(draft, key='review-invalid-' + name), 'VALIDATION_FAILED')
            self.error(self.apply(draft, key='apply-invalid-' + name), 'APPROVAL_REQUIRED')
        self.assertEqual([], self.ok(self.lookup(version=1, kind=REQUEST_TYPE))['data'])

    def test_draft_revisions_do_not_increment_unapproved_business_revision(self):
        original = self.ok(self.prepare())['data']
        revised = self.ok(self.prepare(self.business_request('revised_40'), key='draft-fix',
                         draft_id=original['draft_id'], expected_version=1))['data']
        self.assertEqual((original['draft_id'], 2), (revised['draft_id'], revised['draft_version']))
        request = next(change['after'] for change in revised['changes'] if change['after']['type'] == REQUEST_TYPE)
        self.assertEqual((1, 1, 40), (request['request_revision'], request['reference_data_version'], request['quantity']))
        self.error(self.review(original, key='old-draft-review'), 'SUPERSEDED_DRAFT')
        self.approve_and_apply(revised)

    def test_competing_approved_drafts_cannot_overwrite_new_approved_data(self):
        first = self.ok(self.prepare())['data']
        second = self.ok(self.prepare(self.business_request('independent_10'), key='competing'))['data']
        self.ok(self.review(first, key='first-review'))
        self.ok(self.review(second, key='second-review'))
        self.ok(self.apply(first, key='first-apply'))
        self.error(self.apply(second, key='second-apply'), 'STALE_BASE')
        current = self.ok(self.lookup(version=2, kind=REQUEST_TYPE))['data']
        self.assertEqual([30], [node['quantity'] for node in current])

    def test_amendment_requires_new_evidence_and_preserves_scope(self):
        self.approve_and_apply(self.ok(self.prepare())['data'])
        original = self.business_request()
        changed = self.business_request('revised_40')
        changed['evidence'] = original['evidence']
        report = self.prepare(changed, key='no-new-evidence', expected_data_version=2, expected_request_revision=1)
        self.error(report, 'VALIDATION_FAILED')
        self.assertIn('NEW_EVIDENCE_REQUIRED', {item['code'] for item in report['errors'][0]['details']['errors']})
        changed = dict(self.business_request('revised_40'), need_date='2026-11-07')
        report = self.prepare(changed, key='changed-scope', expected_data_version=2, expected_request_revision=1)
        self.error(report, 'VALIDATION_FAILED')
        self.assertIn('REQUEST_SCOPE_CHANGED', {item['code'] for item in report['errors'][0]['details']['errors']})

    def test_input_objects_and_original_evidence_remain_immutable(self):
        request = self.business_request()
        support = self.support(request)
        before = deepcopy((request, support))
        draft = self.ok(self.prepare(request, supporting_nodes=support))['data']
        self.assertEqual(before, (request, support))
        self.approve_and_apply(draft)
        amended = self.business_request('revised_40')
        support = self.support(amended)
        next(node for node in support if node['type'] == 'Evidence')['description'] = 'changed old evidence'
        self.error(self.prepare(amended, supporting_nodes=support, key='overwrite-evidence',
                               expected_data_version=2, expected_request_revision=1), 'IMMUTABLE_SOURCE')

    def test_draft_revision_is_owner_bound_and_requires_exact_version(self):
        draft = self.ok(self.prepare())['data']
        self.error(self.prepare(self.business_request('revised_40'), user=REVIEWER, key='other-owner',
                               draft_id=draft['draft_id'], expected_version=1), 'DRAFT_OWNER_REQUIRED')
        for values in ({'draft_id': draft['draft_id']}, {'expected_version': 1}):
            self.error(self.prepare(key='unpaired-' + next(iter(values)), **values), 'INVALID_INPUT')


if __name__ == '__main__':
    unittest.main()
