"""B common-screen business risks: partial counters and conserved split dispatch."""
import copy
import json
import unittest

from ontologics.model import ROOT, validate_graph
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery, ROUND, MIXED_WORK, MIXED_SNAPSHOT
from ontologics.order_fulfillment.fulfillment import fulfillment_status
from ontologics.order_fulfillment.fulfillment_validation import CONFIRMATION_KEYS
from ontologics.order_fulfillment.recalculation_demo import load_demo_recalculation
from ontologics.order_fulfillment.workspace import build_records, query
from ontologics.service import DomainError, Service
from tests.db import database_for_test
from tests import test_web as web_fixture


def fixture():
    return json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract() + load_demo_delivery(mixed=True)


def split_payload():
    return {'allocation_id': 'allocation:demo:CQ04:mixed:W', 'parts': [
        {'allocation_id': 'allocation:demo:CQ04:mixed:W', 'shipment_id': 'shipment:web:part1',
         'quantity': 100, 'departed_at': '2026-10-27T10:00:00+09:00', 'arrived_at': '2026-10-27T10:00:00+09:00'},
        {'allocation_id': 'allocation:web:part2', 'shipment_id': 'shipment:web:part2', 'quantity': 150},
    ]}


def progress_payload(confirmed=True):
    return {'production_work_id': MIXED_WORK, 'observation_id': 'observation:web:11',
            'observed_at': '2026-10-27T11:00:00+09:00', 'produced_quantity': 360,
            'good_quantity': 300, 'defective_quantity': 60,
            'snapshot_as_of': '2026-10-27T11:00:00+09:00',
            'confirmations': {key: confirmed for key in CONFIRMATION_KEYS}}


def candidate(nodes, records):
    index = {node['id']: node for node in nodes}
    for record in records:
        index[record['id']] = dict(record, origin='synthetic', evidence=['evidence:demo:CQ04:mixed:assumptions'])
    return list(index.values())


class FulfillmentWorkspaceTests(unittest.TestCase):
    def test_split_conserves_quantity_and_never_turns_unshipped_remainder_into_delivery(self):
        nodes = fixture()
        records = build_records(nodes, 'split_allocation', split_payload())
        changed = candidate(nodes, records)
        self.assertTrue(validate_graph(changed)['valid'])
        state = fulfillment_status(changed, ROUND, '2026-10-27T10:00:00+09:00', queried_at='2026-10-27T10:00:00+09:00')
        self.assertEqual(200, state['states']['delivered'])
        self.assertEqual(150, state['states']['warehouse'])
        self.assertEqual(150, state['states']['in_transit'])
        self.assertEqual(1000, state['allocated_quantity'])
        self.assertEqual(800, state['remaining_quantity'])
        for payload, code in ((dict(split_payload(), allocation_id='allocation:demo:CQ04:mixed:D'), 'ALLOCATION_ALREADY_SHIPPED'),
                              (dict(split_payload(), parts=[split_payload()['parts'][0]]), 'SPLIT_QUANTITY_MISMATCH')):
            with self.subTest(code=code), self.assertRaises(DomainError) as caught:
                build_records(nodes, 'split_allocation', payload)
            self.assertEqual(code, caught.exception.code)
        # Allocated capacity cannot be reused through the new-source workflow.
        payload = {'stock_id': 'stock:demo:CQ04:mixed:W', 'delivery_round_id': ROUND,
                   'parts': [{'allocation_id': 'allocation:web:extra', 'shipment_id': 'shipment:web:extra', 'quantity': 1}]}
        report = validate_graph(candidate(nodes, build_records(nodes, 'split_shipments', payload)))
        self.assertFalse(report['valid'])
        self.assertIn('SOURCE_QUANTITY_EXCEEDED', {error['code'] for error in report['errors']})

    def test_partial_progress_keeps_stock_separate_and_new_cutoff_coverage_explicit(self):
        nodes = fixture()
        records = build_records(nodes, 'production_progress', progress_payload())
        changed = candidate(nodes, records)
        self.assertTrue(validate_graph(changed)['valid'], validate_graph(changed))
        state = fulfillment_status(changed, ROUND, '2026-10-27T11:00:00+09:00', queried_at='2026-10-27T11:00:00+09:00')
        self.assertEqual(300, state['states']['production_completed'])
        self.assertEqual(200, state['states']['in_progress'])
        self.assertEqual(250, state['states']['warehouse'])
        incomplete = candidate(nodes, build_records(nodes, 'production_progress', progress_payload(False)))
        state = fulfillment_status(incomplete, ROUND, '2026-10-27T11:00:00+09:00', queried_at='2026-10-27T11:00:00+09:00')
        self.assertEqual('needs_confirmation', state['status'])
        self.assertIsNone(state['states'])
        self.assertEqual(9, len(state['unknowns']))
        for payload, code in ((dict(progress_payload(), good_quantity=199), 'PRODUCTION_COUNTER_DECREASED'),
                              (dict(progress_payload(), good_quantity=True), 'INVALID_QUANTITY'),
                              (dict(progress_payload(), confirmations={}), 'INVALID_CONFIRMATIONS')):
            with self.subTest(code=code), self.assertRaises(DomainError) as caught:
                build_records(nodes, 'production_progress', payload)
            self.assertEqual(code, caught.exception.code)

    def test_adapter_rejects_identity_and_authority_in_query_inputs(self):
        for action, args in [('review', {}), ('fulfillment_status', {'actor': 'bob'}),
                             ('delivery_round', {'delivery_round_id': ROUND})]:
            with self.subTest(action=action), self.assertRaises(DomainError):
                query(None, 'alice', action, args)

    def test_comparison_requires_exact_explicit_versions_without_caller_identity(self):
        args = dict(delivery_round_id=ROUND, before_version=1, after_version=2,
                    before_as_of='2026-10-27T10:00:00+09:00',
                    after_as_of='2026-10-27T10:00:00+09:00')
        invalid = [dict(args, actor='bob'), dict(args, version=2),
                   dict(args, as_of=args['before_as_of'])]
        invalid += [{key: value for key, value in args.items() if key != missing}
                    for missing in args]
        for values in invalid:
            with self.subTest(arguments=values), self.assertRaises(DomainError) as caught:
                query(None, 'alice', 'compare_response_plan', values)
            self.assertEqual('INVALID_INPUT', caught.exception.code)


class FulfillmentWorkspaceApprovalTests(unittest.TestCase):
    def test_shared_draft_approval_exact_version_idempotency_and_b_reads(self):
        from ontologics.business_drafts import prepare_business_draft
        database = database_for_test(self)
        principals = {'alice': {'kind': 'human', 'roles': ['seed', 'read', 'draft', 'review']},
                      'bob': {'kind': 'human', 'roles': ['read', 'review', 'apply']},
                      'reader': {'kind': 'human', 'roles': ['read']}}
        service = Service(database, principals=principals)
        self.addCleanup(service.close)
        def ok(result):
            self.assertTrue(result['ok'], result)
            return result
        ok(service.bootstrap('alice', fixture(), request_key='fixture'))
        version = service._version()
        args = dict(domain='fulfillment', builder=build_records, action='split_allocation',
                    payload=split_payload(), base_version=version, source_text='통제 시험: 창고 W 250개를 100개 도착/150개 미출발로 분할한 가상 원본 ' + database.schema,
                    authored_at='2026-10-27T10:00:00+09:00', notice='팀 공통 회귀용 가상 자료',
                    synthetic_confirmed=True, request_key='split-draft')
        denied = prepare_business_draft(service, 'reader', **args)
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        draft = ok(prepare_business_draft(service, 'alice', **args))['data']
        self.addCleanup((ROOT / draft['registration']['source']['path']).unlink, missing_ok=True)
        self.assertTrue(draft['validation']['valid'], draft)
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertEqual(version, service._version())
        self.assertEqual(did, ok(prepare_business_draft(service, 'alice', **args))['data']['draft_id'])
        self.assertEqual('SELF_APPROVAL_FORBIDDEN', service.review('alice', did, dv, decision='approve', note='test', request_key='self')['errors'][0]['code'])
        ok(service.review('bob', did, dv, decision='approve', note='명시한 분할 수량과 근거 확인', request_key='review'))
        applied = ok(service.apply('bob', did, dv, request_key='apply'))
        self.assertEqual(version + 1, applied['data_version'])
        before = ok(query(service, 'reader', 'fulfillment_status', dict(delivery_round_id=ROUND, as_of='2026-10-27T10:00:00+09:00', version=version)))
        after = ok(query(service, 'reader', 'fulfillment_status', dict(delivery_round_id=ROUND, as_of='2026-10-27T10:00:00+09:00', version=version+1)))
        self.assertEqual(100, before['data']['states']['delivered'])
        self.assertEqual(200, after['data']['states']['delivered'])
        self.assertTrue(after['evidence'])
        self.assertEqual(did, ok(prepare_business_draft(service, 'alice', **args))['data']['draft_id'])
        stale = prepare_business_draft(service, 'alice', **dict(args, request_key='new-stale'))
        self.assertEqual('STALE_BASE', stale['errors'][0]['code'])
        self.assertEqual(version + 1, service._version())


class FulfillmentWorkspaceHTTPTests(web_fixture.WebCase):
    def test_source_backed_comparison_is_read_only_and_preserves_exact_versions(self):
        def approve(records, key):
            draft = self.ok(self.tool('prepare_draft', {'operations': [
                {'op': 'put', 'node': row} for row in records], 'request_key': key}))['data']
            self.assertTrue(draft['validation']['valid'], draft)
            return self.ok(self.http(self.bob, '/api/review',
                                    self.review_body(draft, key=key + '-review')))['data_version']

        before = approve(load_demo_contract() + load_demo_delivery(), 'comparison-base')
        after = approve(load_demo_recalculation('transport2h'), 'comparison-transport')
        at = '2026-10-27T10:00:00+09:00'
        args = dict(delivery_round_id=ROUND, before_version=before, after_version=after,
                    before_as_of=at, after_as_of=at)

        def compare(arguments):
            return self.http(self.reader, '/api/fulfillment/query',
                             {'action': 'compare_response_plan', 'arguments': arguments})

        result = self.ok(compare(args))
        self.assertEqual(after, result['data_version'])
        data = result['data']
        self.assertEqual(before, data['before']['data_version'])
        self.assertEqual(after, data['after']['data_version'])
        self.assertEqual(960, data['before']['response_plan']['forecast']['on_time_quantity'])
        self.assertEqual(870, data['after']['response_plan']['forecast']['on_time_quantity'])
        self.assertEqual(-90, data['quantity_deltas']['on_time_quantity'])
        self.assertEqual(0, data['time_deltas']['estimated_completion_seconds'])
        self.assertEqual(3600, data['time_deltas']['estimated_full_arrival_seconds'])
        self.assertFalse(data['execution_performed'])
        self.assertTrue(result['evidence'])

        for overrides, status, code in [
            ({'actor': 'bob'}, 400, 'INVALID_INPUT'),
            ({'before_version': True}, 400, 'INVALID_VERSION'),
            ({'after_version': -1}, 400, 'INVALID_VERSION'),
            ({'after_version': after + 100}, 404, 'VERSION_NOT_FOUND'),
            ({'before_version': after, 'after_version': before}, 400, 'INVALID_VERSION'),
            ({'before_version': after, 'after_as_of': '2026-10-27T11:00:00+09:00'},
             400, 'INVALID_TIME'),
        ]:
            with self.subTest(overrides=overrides):
                self.failed(compare(dict(args, **overrides)), status, code)

        # Reading old/new snapshots and rejecting invalid comparisons never
        # publishes a new graph or changes the source-backed conditions.
        for version, seconds in ((before, 3600), (after, 7200)):
            lookup = self.ok(self.tool('lookup', {'kind': 'DeliveryCondition', 'version': version},
                                       server=self.reader))
            self.assertEqual(version, lookup['data_version'])
            self.assertEqual(seconds, lookup['data'][0]['transport_seconds'])
        latest = self.ok(self.tool('lookup', {'kind': 'DeliveryCondition'}, server=self.reader))
        self.assertEqual(after, latest['data_version'])

    def test_partial_progress_split_separate_approval_and_exact_version_requery(self):
        records = load_demo_contract() + load_demo_delivery(mixed=True)
        initial = self.ok(self.tool('prepare_draft', {'operations': [
            {'op': 'put', 'node': row} for row in records], 'request_key': 'b-base'}))['data']
        self.assertTrue(initial['validation']['valid'], initial)
        base = self.ok(self.http(self.bob, '/api/review', self.review_body(initial, key='b-base-review')))['data_version']

        def request_body(action, payload, version, key):
            return dict(domain='fulfillment', action=action, payload=payload,
                        base_version=version, source_text='명시된 입력값의 가상 원본 ' + self.database.schema + ' ' + action,
                        authored_at='2026-10-27T11:00:00+09:00', notice='통제 HTTP 시험 자료 · 실제 생산/출하 아님',
                        synthetic_confirmed=True, request_key=key)

        def state(version, at):
            return self.ok(self.http(self.reader, '/api/fulfillment/query', {
                'action': 'fulfillment_status', 'arguments': {'delivery_round_id': ROUND, 'as_of': at, 'version': version}}))

        progress = request_body('production_progress', progress_payload(), base, 'partial-production')
        self.failed(self.http(self.reader, '/api/business/draft', progress), 403, 'FORBIDDEN')
        draft = self.ok(self.http(self.alice, '/api/business/draft', progress))['data']
        self.addCleanup((ROOT / draft['registration']['source']['path']).unlink, missing_ok=True)
        self.assertTrue(draft['validation']['valid'], draft)
        self.assertEqual(200, state(base, '2026-10-27T10:00:00+09:00')['data']['states']['production_completed'])
        self.failed(self.http(self.alice, '/api/review', self.review_body(draft, key='self-partial')), 403, 'SELF_APPROVAL_FORBIDDEN')
        completed = self.ok(self.http(self.bob, '/api/review', self.review_body(draft, key='approve-partial')))['data_version']
        partial = state(completed, '2026-10-27T11:00:00+09:00')
        self.assertEqual(300, partial['data']['states']['production_completed'])
        self.assertEqual(200, partial['data']['states']['in_progress'])
        self.assertEqual(250, partial['data']['states']['warehouse'])

        split = request_body('split_allocation', split_payload(), completed, 'split-dispatch')
        split_draft = self.ok(self.http(self.alice, '/api/business/draft', split))['data']
        self.addCleanup((ROOT / split_draft['registration']['source']['path']).unlink, missing_ok=True)
        self.assertTrue(split_draft['validation']['valid'], split_draft)
        final = self.ok(self.http(self.bob, '/api/review', self.review_body(split_draft, key='approve-split')))['data_version']
        after = state(final, '2026-10-27T11:00:00+09:00')
        self.assertEqual(200, after['data']['states']['delivered'])
        self.assertEqual(150, after['data']['states']['warehouse'])
        self.assertEqual(800, after['data']['remaining_quantity'])
        self.assertEqual(completed, partial['data_version'])
        self.assertEqual(final, after['data_version'])
        self.assertTrue(after['evidence'])
        self.assertEqual(100, state(completed, '2026-10-27T11:00:00+09:00')['data']['states']['delivered'])
        replay = self.ok(self.http(self.alice, '/api/business/draft', split))['data']
        self.assertEqual(split_draft['draft_id'], replay['draft_id'])
        self.failed(self.http(self.alice, '/api/business/draft', dict(split, request_key='stale-new-request')), 409, 'STALE_BASE')
