"""Document drafts, real upstream arithmetic; supplier positive data are test-only."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from tests.production_planning.test_scheduling import plan, resource, initial, context, schedules
from tests.production_planning.test_procurement import offer, zero_costs
from ontologics.production_planning.scheduling import estimate_material_ready, estimate_production_schedule


def inputs(full=True, scenario='virtual_remaining', priced=False):
    p = plan(full=full)
    sc = context()
    offers = []
    cc = dict(sc, rounding={'USD': '0.01', 'KRW': '1'}, costs={})
    if priced:
        for j, row in enumerate(p['remaining_materials']['components']):
            if row['S']:
                o = offer(row['component_id'], 'test-only:quote:' + str(j))
                o.update(valid_from='2026-10-25T00:00:00Z', valid_until='2026-11-01T00:00:00Z')
                offers.append(o)
        zero_costs(cc)
    timing = schedules(p) if priced else []
    if priced:
        from ontologics.production_planning.procurement import purchase_quantity
        for item, row in zip(timing, p['remaining_materials']['components']):
            item['quantity'] = max(row['D'], purchase_quantity(row['S'], 50, 20))
    ready = estimate_material_ready(p, timing, sc,
        scenario='virtual_remaining', offers=offers, cost_context=cc)
    s = estimate_production_schedule(p, resource(), ready, initial_conditions=initial(p))
    c = dict(configuration_id=p['configuration_id'], data_version=1, scenario=scenario,
        demand_source=deepcopy(p['demand']['demand_source']), comparisons=[], evidence=[], constraints=[])
    return c, {'schedule': s}


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ontologics.production_planning.documents'), 'Task08 missing')
        from ontologics.production_planning import documents
        self.d = documents

    def build(self, **kw):
        return self.d.build_business_documents(*inputs(**kw))

    def test_three_drafts_exact_rows_and_unknown_scope(self):
        docs = self.build()
        self.assertEqual(['production_plan', 'substitution_review', 'purchase_request'], [d['kind'] for d in docs])
        p = docs[0]['payload']
        self.assertEqual((100, 70, 30), tuple(p['quantities'][k] for k in ('planned_input_quantity', 'initial_quantity', 'remaining_quantity')))
        self.assertEqual((74, 71, 11), tuple(p['scope'][k] for k in ('bom_rows', 'unknown_stock_components', 'unknown_population_rows')))
        expected = {'55': (1, 100, 70, 30, 70, 0, 30, 30), '9': (8, 800, 560, 240, 560, 0, 240, 240), '14': (11, 1100, 1100, 0, 770, 330, 330, 0)}
        for row in p['components']:
            number = row['bom_rows'][0]['id'].split('-')[-1]
            if number in expected:
                self.assertEqual(expected[number], tuple(row[k] for k in ('q', 'D', 'A', 'S', 'virtual_used_quantity', 'remaining_available_quantity', 'remaining_required_quantity', 'remaining_shortage')))
        self.assertIsNone(p['scope']['whole_bom_material_limit'])
        for d in docs:
            self.assertIsNone(d['document_ref']); self.assertIsNone(d['document_version'])
            self.assertEqual('unreviewed', d['state']['review']); self.assertEqual('unsent', d['state']['dispatch'])
            self.assertFalse(d['payload']['authority']['purchase_authorized'])
            self.assertIn(d['content_sha256'], d['markdown'])

    def test_quote_request_is_not_order_and_unknown_is_not_zero(self):
        d = self.build()[2]; p = d['payload']['procurement']
        self.assertEqual('견적·납기 확인 요청', d['title'])
        self.assertEqual([30, 240], sorted(i['shortage_quantity'] for i in p['purchase_review']))
        self.assertEqual({}, p['known_item_totals']); self.assertIsNone(p['total_by_currency'])
        self.assertEqual(71, sum(i['status'] == 'shortage_unknown' for i in p['items']))
        zero = next(i for i in p['items'] if i['shortage_quantity'] == 0)
        self.assertEqual((0, 'not_required'), (zero['purchase_quantity'], zero['status']))

    def test_initial_hypothesis_and_remaining_unknown_both_rendered(self):
        d = self.build()[0]; s = d['payload']['schedule']; body = d['markdown']
        self.assertEqual('2026-10-26T14:30:00+09:00', s['initial']['completion'])
        self.assertIsNone(s['remaining']['completion']); self.assertIsNone(s['customer_arrival_compliance'])
        for text in ('14:30', '11:00', '10:00', '별도 가정', '71', '미정', '실행 아님'):
            self.assertIn(text, body)

    def test_independent_original_scope_is_not_added(self):
        docs = self.build(scenario='original')
        self.assertEqual('original', docs[2]['payload']['binding']['scenario'])
        self.assertEqual([30, 240], sorted(i['shortage_quantity'] for i in docs[2]['payload']['procurement']['purchase_review']))

    def test_positive_review_requires_prices_costs_and_all_material_timing(self):
        self.assertEqual('구매 검토 요청', self.build(full=False, priced=True)[2]['title'])
        c, r = inputs(full=False, priced=True)
        s = r['schedule']; ready_inputs = s['material_ready']['inputs']
        ready_inputs['schedules'] = []
        s['material_ready'] = estimate_material_ready(**ready_inputs)
        r['schedule'] = estimate_production_schedule(s['plan'], s['resource'], s['material_ready'], initial_conditions=s['initial_conditions'])
        d = self.d.build_business_documents(c, r)[2]
        self.assertEqual('견적·납기 확인 요청', d['title'])
        self.assertIsNotNone(d['payload']['procurement']['total_by_currency'])

    def test_money_strings_mixed_currencies_partial_totals(self):
        c, r = inputs(full=False, priced=True); s = r['schedule']; args = s['material_ready']['inputs']
        args['offers'][0]['currency'] = 'KRW'
        args['offers'][0]['tiers'][1]['amount'] = '12.123456789012'
        ready = estimate_material_ready(**args)
        r['schedule'] = estimate_production_schedule(s['plan'], s['resource'], ready, initial_conditions=s['initial_conditions'])
        d = self.d.build_business_documents(c, r)[2]; p = d['payload']['procurement']
        self.assertEqual({'USD', 'KRW'}, set(p['known_item_totals']))
        self.assertTrue(all(isinstance(v, str) for v in p['known_item_totals'].values()))
        self.assertIsNone(p['total_by_currency']); self.assertEqual('견적·납기 확인 요청', d['title'])
        self.assertIn('12.123456789012', d['markdown'])

    def test_tampered_derivation_binding_and_virtual_observation_rejected(self):
        for change in ('quantity', 'completion', 'version', 'source', 'virtual', 'authorization'):
            c, r = inputs()
            if change == 'quantity': r['schedule']['plan']['components'][0]['D'] = 999
            if change == 'completion': r['schedule']['initial']['completion'] = '2026-10-26T12:00:00+09:00'
            if change == 'version': c['data_version'] = 2
            if change == 'source': c['demand_source'] = {'revision': 99}
            if change == 'virtual': r['schedule']['plan']['input_materials']['stock']['state'] = 'virtual'
            if change == 'authorization': r['schedule']['purchase_authorized'] = True
            with self.subTest(change=change), self.assertRaises(ValueError): self.d.build_business_documents(c, r)

    def test_substitution_scope_no_u2_invented_and_no_caller_candidate(self):
        ds = self.build(); p = ds[1]['payload']['substitution']
        self.assertEqual([], p['candidates']); self.assertEqual('none_in_reviewed_scope', p['states']['candidate_discovery'])
        self.assertTrue(p['required_review'])
        c, r = inputs(); c['candidates'] = [{'approved': True}]
        with self.assertRaises(ValueError): self.d.build_business_documents(c, r)

    def test_free_text_edits_locked_payload_separate_and_escaped(self):
        d = self.build()[0]; before = deepcopy(d)
        text = '검토 | 의견\n# approved\n<script>alert(1)</script>\n```'
        edited = self.d.edit_document_text(d, {'note': text})
        self.assertEqual(before, d); self.assertEqual(d['payload'], edited['payload'])
        self.assertNotEqual(d['content_sha256'], edited['content_sha256'])
        self.assertNotIn('<script>', edited['markdown']); self.assertNotIn('\n# approved', edited['markdown'])
        self.assertEqual('unreviewed', edited['state']['review'])
        for key in ('title', 'payload', 'review', 'purchase_quantity', 'document_version'):
            with self.assertRaises(ValueError): self.d.edit_document_text(d, {key: 'changed'})

    def test_escaped_free_text_keeps_visible_punctuation(self):
        import html
        text = '한글 _part_ & "note" | [문구] #1'
        d = self.d.edit_document_text(self.build()[0], {'note': text})
        line = next(line for line in d['markdown'].splitlines() if line.startswith('note: '))
        self.assertEqual('note: ' + text, html.unescape(line))

    def test_determinism_and_no_aliases(self):
        c, r = inputs(); before = deepcopy((c, r)); first = self.d.build_business_documents(c, r)
        self.assertEqual(first, self.d.build_business_documents(c, r)); self.assertEqual(before, (c, r))
        first[0]['payload']['components'].clear()
        self.assertEqual(74, len(first[1]['payload']['components'])); self.assertEqual(before, (c, r))

    def test_export_exact_digest_no_overwrite_traversal_or_symlink(self):
        d = self.build()[0]
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            path = self.d.export_document_markdown(d, folder, '계획.md', expected_sha256=d['content_sha256'])
            self.assertEqual(d['markdown'], Path(path).read_text(encoding='utf-8'))
            with self.assertRaises(FileExistsError): self.d.export_document_markdown(d, folder, '계획.md', expected_sha256=d['content_sha256'])
            for name in ('../outside.md', '/tmp/outside.md', 'nested/file.md', 'x.txt'):
                with self.assertRaises(ValueError): self.d.export_document_markdown(d, folder, name, expected_sha256=d['content_sha256'])
            (Path(folder)/'link.md').symlink_to(Path(outside)/'outside.md')
            with self.assertRaises(FileExistsError): self.d.export_document_markdown(d, folder, 'link.md', expected_sha256=d['content_sha256'])
            with self.assertRaises(ValueError): self.d.export_document_markdown(d, folder, 'new.md', expected_sha256='0'*64)
            corrupt = deepcopy(d); corrupt['payload']['quantities']['initial_quantity'] = 999
            with self.assertRaises(ValueError): self.d.export_document_markdown(corrupt, folder, 'new.md', expected_sha256=d['content_sha256'])

    def test_plan_locked_flags_and_query_preview_cannot_be_reported(self):
        for mode in ('authorized', 'target', 'preview', 'error'):
            c, r = inputs()
            if mode == 'authorized': r['schedule']['plan']['production_authorized'] = True
            if mode == 'target': r['schedule']['plan']['target_quantity'] = 999
            if mode == 'preview': r['query'] = {'lookup_response': {'ok': True, 'is_preview': True, 'data_version': 1}}
            if mode == 'error': r['query'] = {'lookup_response': {'ok': False, 'errors': [{'code': 'FAILED'}], 'data_version': 1}}
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.d.build_business_documents(c, r)

    def test_empty_zero_and_unknown_have_distinct_purchase_titles(self):
        from tests.production_planning.test_materials import fixture, CONFIG
        from tests.production_planning.test_planning import demand_for
        from ontologics.production_planning.materials import available_stock, calculate_material_requirements
        from ontologics.production_planning.planning import build_production_plan
        for mode in ('zero', 'unknown', 'empty', 'error'):
            doc, scope, rows = fixture()
            if mode == 'unknown': doc['stocks'] = []
            if mode == 'empty': rows = []
            if mode == 'error': doc['stocks'][0]['on_hand_quantity'] = -1
            m = calculate_material_requirements(rows, available_stock([doc], scope), 0 if mode == 'zero' else 100,
                configuration_id=CONFIG, data_version=1, demand_source={'state': 'unconfirmed'}, bom_complete=True)
            if mode == 'error':
                with self.assertRaises(ValueError): build_production_plan(demand_for(m), m, 0)
                continue
            p = build_production_plan(demand_for(m), m, 0)
            ready = estimate_material_ready(p, [], context(), scenario='virtual_remaining')
            schedule = estimate_production_schedule(p, resource(), ready)
            c = dict(configuration_id=CONFIG, data_version=1, scenario='virtual_remaining', demand_source=m['demand_source'])
            d = self.d.build_business_documents(c, {'schedule': schedule})[2]
            self.assertEqual('구매 신청 불필요' if mode == 'zero' else '구매 대상 미정', d['title'])
            self.assertFalse(d['payload']['purchase_request_eligible'])


from tests.production_planning.test_scheduling import SchedulingPostgresTests as _PG


class DocumentPostgresTests(unittest.TestCase):
    setUp = _PG.setUp

    def args(self):
        return dict(demand_id=self.saved['demand_id'], item_id='contract-item:planning:001', data_version=1,
            expected_revision=self.saved['revision'], expected_target_sha256=self.saved['target_sha256'],
            snapshots=[self.doc], scope=self.scope, initial_quantity=70, resource=resource(),
            schedules=[], schedule_context=context(), initial_conditions=initial(plan()))

    def call(self, **changes):
        from ontologics.production_planning.documents import build_saved_business_documents
        args = self.args(); args.update(changes)
        return build_saved_business_documents(self.workflow, self.host, self.reader, **args)

    def test_current_demand_public_provenance_and_no_writes(self):
        before = self.workflow.get(self.reader, self.saved['demand_id'])['data']; stock = deepcopy(self.doc)
        with self.host.request(self.reader) as session: graph = session.call('lookup', {'version': 1})['data']
        r = self.call(); d = r['documents'][0]
        self.assertEqual(self.saved['target_sha256'], d['payload']['binding']['demand_source']['target_sha256'])
        self.assertIn('2026-11-10', d['markdown'])
        self.assertEqual('temporal-0.1', d['payload']['query_provenance']['lookup_response']['rule_version'])
        self.assertEqual(before, self.workflow.get(self.reader, self.saved['demand_id'])['data'])
        self.assertEqual(stock, self.doc); self.assertEqual([], before['events'])
        with self.host.request(self.reader) as session: self.assertEqual(graph, session.call('lookup', {'version': 1})['data'])

    def test_structured_edit_calls_fresh_calculation_without_old_success(self):
        from ontologics.production_planning.documents import recalculate_business_documents
        previous = self.call()['documents']; before = deepcopy(previous)
        args = self.args(); changed_resource = resource(); changed_resource['boards_per_hour'] = 10
        r = recalculate_business_documents(previous, self.workflow, self.host, self.reader,
            calculation_arguments=args, edits={'resource': changed_resource})
        self.assertEqual('2026-10-26T18:00:00+09:00', r['documents'][0]['payload']['schedule']['initial']['completion'])
        self.assertEqual(before, previous)
        r = recalculate_business_documents(previous, self.workflow, self.host, self.reader,
            calculation_arguments=args, edits={'initial_quantity': 71})
        self.assertIsNone(r['documents']); self.assertTrue(r['errors']); self.assertTrue(r['original_preserved'])
        with self.assertRaises(ValueError): recalculate_business_documents(previous, self.workflow, self.host, self.reader,
            calculation_arguments=args, edits={'planned_input_quantity': 200})

    def test_stale_revision_hash_version_fail_without_documents(self):
        for change in ({'expected_revision': 99}, {'expected_target_sha256': '0'*64}, {'data_version': 2}):
            self.assertIsNone(self.call(**change)['documents'])

    def test_changed_current_demand_does_not_inherit_old_draft(self):
        from ontologics.production_planning.documents import recalculate_business_documents
        previous = self.call()['documents']; before = deepcopy(previous)
        self.interpreted['items'][0]['proposed_configuration_id'] = context()['configuration_id']
        target = self.preflight.prepare(self.author, self.path, self.interpreted, data_version=1)['target']
        changed = self.workflow.save(self.author, target, demand_id=self.saved['demand_id'],
            expected_revision=1, request_key='documents-demand-revision')
        self.assertTrue(changed['ok'])
        r = recalculate_business_documents(previous, self.workflow, self.host, self.reader,
            calculation_arguments=self.args(), edits={'remaining_setup_minutes': 0})
        self.assertIsNone(r['documents']); self.assertEqual(before, previous)

    def test_quantity_price_and_missing_assumption_recompute(self):
        from ontologics.production_planning.documents import recalculate_business_documents
        previous = self.call()['documents']
        def change(edits):
            return recalculate_business_documents(previous, self.workflow, self.host, self.reader,
                calculation_arguments=self.args(), edits=edits)['documents'][0]['payload']
        p = change({'initial_quantity': 50})
        self.assertIsNone(p['schedule']['initial']['completion'])
        ic = initial(plan()); ic['quantity'] = 50
        p = change({'initial_quantity': 50, 'initial_conditions': ic})
        self.assertEqual('2026-10-26T13:30:00+09:00', p['schedule']['initial']['completion'])
        self.assertEqual(50, p['quantities']['remaining_quantity'])
        lan = next(c for c in p['components'] if c['component_id'] == 'component:olimex-L-wroom:32d420c6c802a2c2')
        self.assertEqual((50, 20, 50, 30), tuple(lan[k] for k in ('virtual_used_quantity', 'remaining_available_quantity', 'remaining_required_quantity', 'remaining_shortage')))
        quote = offer(lan['component_id']); quote.update(valid_from='2026-10-25T00:00:00Z', valid_until='2026-11-01T00:00:00Z')
        cc = dict(context(), rounding={'USD': '0.01'}, costs={})
        p = change({'offers': [quote], 'cost_context': cc})
        self.assertEqual({'USD': '7.50'}, p['procurement']['known_item_totals'])
        self.assertIsNone(p['procurement']['total_by_currency']); self.assertFalse(p['purchase_request_eligible'])

    def test_preview_failure_and_revoked_permission(self):
        from unittest.mock import patch
        from ontologics.host import RequestSession
        original = RequestSession.call
        for mode in ('preview', 'failure'):
            def altered(session, tool, args):
                r = original(session, tool, args)
                if tool == 'lookup':
                    if mode == 'preview': r['is_preview'] = True
                    else: r.update(ok=False, data=None)
                return r
            with patch.object(RequestSession, 'call', altered): self.assertIsNone(self.call()['documents'])
        from ontologics.identity import UserIdentity
        from ontologics.service import DomainError
        self.identities.users = [u if u.subject != 'reader' else UserIdentity('reader', frozenset()) for u in self.identities.users]
        try: r = self.call()
        except DomainError as exc: self.assertEqual('FORBIDDEN', exc.code)
        else: self.assertIsNone(r['documents'])

    def test_example_exports_three_utf8_drafts(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.production_planning_documents'), 'Task08 example missing')
        from examples.production_planning_documents import run_example
        with tempfile.TemporaryDirectory() as folder:
            r = run_example(folder)
            self.assertEqual(3, len(r['exports']))
            self.assertEqual(0, r['model_api_calls']); self.assertEqual(0, r['demand_event_count'])
            for doc, path in zip(r['documents'], r['exports']):
                self.assertEqual(doc['markdown'], Path(path).read_text(encoding='utf-8'))
                self.assertEqual(r['production_source_sha256'], doc['payload']['schedule']['resource'].get('source', {}).get('sha256'))


del _PG
