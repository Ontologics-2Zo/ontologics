"""Reviewed mail extraction boundaries with local doubles; no DB or paid calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_intake_task as table
from examples.order_fulfillment import b_mail_input as inputs
from examples.order_fulfillment import b_mail_intake_task as mail
from ontologics.harness import BudgetLimits, ExecutionBudget, canonical, request_budget
from ontologics.openai_provider import MODEL, OpenAISettings
from ontologics.paid_budget import CostPolicy, ModelPrice
from tests.test_openai_provider import SECRET, Transport, response, text, tool


DIRECTORY = inputs.ROOT / 'data/order_fulfillment/examples'


def source(name=inputs.INPUTS[0]):
    return inputs.load_input(DIRECTORY / name)


def catalog_reads(task):
    """Successful lookup envelopes containing the actual pinned catalog records."""
    return [dict(name='lookup', result=dict(ok=True, data_version=task.context.data_version,
        data=[deepcopy(n) for n in task.data()['catalog_nodes'] if n['type'] == kind],
        evidence=sorted({eid for n in task.data()['catalog_nodes'] if n['type'] == kind
                         for eid in n['evidence']}))) for kind in table.CATALOG_KINDS]


class MailInputTests(unittest.TestCase):
    def test_both_mail_sources_resolve_only_to_the_separately_reviewed_baseline(self):
        for name in inputs.INPUTS:
            with self.subTest(name=name):
                value = source(name)
                baseline, anchors = inputs.resolve(value)
                self.assertEqual(name.replace('_mail.md', '.md'), baseline['path'].split('/')[-1])
                self.assertEqual(set(mail.FIELDS), set(anchors))
                self.assertTrue(all(anchor in value['text'] for anchor in anchors.values()))
                self.assertEqual(dict(path=baseline['path'], sha256=baseline['sha256'],
                    relationship='separate_reviewed_operational_example'), inputs.operations_source(value))
                role, task = inputs.case_task('cq01_intake', value)
                self.assertEqual((mail, mail.MailIntakeAgent, mail.ScriptedMailProvider), role)
                self.assertEqual(value['text'], task.data()['source_text'])

    def test_forecasts_and_comparisons_keep_reviewed_operational_numbers(self):
        expected = (
            (inputs.INPUTS[0], (400, 300, 100.0, '2026-10-30T13:00:00+09:00'),
             (800, 0, '2026-10-30T15:30:00+09:00'), (800, 750, 0, 50, 7200, 10800)),
            (inputs.INPUTS[1], (500, 450, 50.0, '2026-10-30T19:00:00+09:00'),
             (450, 350, '2026-10-30T22:00:00+09:00'), (450, 400, 350, 400, 9000, 12600)),
        )
        for name, production, arrival, comparison in expected:
            with self.subTest(name=name):
                value = source(name)
                baseline, _ = inputs.resolve(value)
                for case in inputs.terminal.CASES[1:]:
                    self.assertEqual(inputs.terminal.case_task(case, baseline)[1].data(),
                                     inputs.case_task(case, value)[1].data())
                result = inputs.case_task('cq03_normal', value)[1].data()['assessment']
                self.assertEqual(production, tuple(result[k] for k in ('target_quantity',
                    'remaining_quantity', 'good_rate_per_hour', 'estimated_completion_at')))
                result = inputs.case_task('cq04_mixed', value)[1].data()['assessment']
                self.assertEqual(arrival, tuple(result[k] for k in ('on_time_quantity',
                    'shortfall_quantity', 'estimated_full_arrival_at')))
                result = inputs.case_task('cq06_transport2h', value)[1].data()['assessment']
                before, after = (result[side]['forecast'] for side in ('before', 'after'))
                self.assertEqual(comparison, (before['on_time_quantity'], after['on_time_quantity'],
                    before['shortfall_quantity'], after['shortfall_quantity'],
                    before['condition']['transport_seconds'], after['condition']['transport_seconds']))

    def test_task_and_messages_supply_mail_without_private_review_fixtures(self):
        for name in inputs.INPUTS:
            with self.subTest(name=name):
                value = source(name)
                task = mail.make_task(value)
                messages = mail.MailIntakeAgent().messages(task)
                self.assertEqual(task.data(), json.loads(messages[1]['content']))
                self.assertEqual(value['text'], task.data()['source_text'])
                self.assertEqual({field.name for field in table.INPUT.fields}, set(task.data()))
                self.assertEqual({'data_version', 'output_fields', 'candidate_fields', 'quote_fields',
                                  'host_bound_fields', 'rule'}, set(json.loads(messages[0]['content'])))
                serialized = canonical(messages)
                for private in ('baseline_path', 'baseline_sha256', 'mail_sha256', 'anchors',
                                '## 계약 표', '| 계약 수량 |', '```json', 'allocations_complete',
                                'comparison_transport_seconds', 'contract:demo:', 'delivery-round:demo:'):
                    self.assertNotIn(private, serialized)
                self.assertFalse(any(n['type'] in {'Contract', 'ContractItem', 'DeliveryRound'}
                                     for n in task.data()['catalog_nodes']))

    def test_changed_source_or_baseline_hash_stops_before_provider(self):
        value = source()
        for key, changed in (('text', value['text'] + '\n'), ('sha256', '0' * 64)):
            with self.subTest(key=key), patch.object(mail, 'ScriptedMailProvider') as provider:
                with self.assertRaises(ValueError):
                    inputs.case_task('cq01_intake', dict(value, **{key: changed}))
                provider.assert_not_called()
        baseline, _ = inputs.resolve(value)
        with patch.object(inputs.terminal, 'load_input', return_value=dict(baseline, sha256='0' * 64)), \
                patch.object(mail, 'ScriptedMailProvider') as provider:
            with self.assertRaisesRegex(ValueError, 'operational example changed'):
                inputs.case_task('cq01_intake', value)
            provider.assert_not_called()

    def test_arbitrary_or_operational_sources_cannot_be_paired_as_reviewed_mail(self):
        value = source()
        for path in ('../outside.md', 'data/order_fulfillment/examples/unreviewed_mail.md'):
            candidate = dict(value, path=path)
            with self.subTest(path=path), patch.object(inputs, 'load_input', return_value=candidate):
                with self.assertRaises(ValueError):
                    inputs.resolve(candidate)
        baseline, _ = inputs.resolve(value)
        with self.assertRaises(ValueError):
            inputs.resolve(baseline)
        with self.assertRaises(ValueError):
            inputs.resolve(dict(value, baseline_path=baseline['path']))

    def test_reviewed_anchors_cannot_change_after_task_creation(self):
        value = source()
        task = mail.make_task(value)
        baseline, anchors = inputs.resolve(value)
        changed = dict(anchors, quantity='견적 단계에서 논의한 1,000개')
        self.assertIn(changed['quantity'], value['text'])
        with patch.object(inputs, 'resolve', return_value=(baseline, changed)):
            with self.assertRaisesRegex(ValueError, 'reference changed'):
                mail.MailIntakeAgent().messages(task)


class MailAgentTests(unittest.TestCase):
    def setUp(self):
        self.task = mail.make_task(source())
        self.agent = mail.MailIntakeAgent()
        self.reads = catalog_reads(self.task)
        self.raw = self.scripted_output(self.task, self.reads)

    def scripted_output(self, task, reads):
        scripted = mail.ScriptedMailProvider(task)
        messages = self.agent.messages(task)
        first = scripted(model='scripted-test', messages=messages)
        self.assertEqual([dict(name='lookup', arguments=dict(kind=kind,
            version=task.context.data_version)) for kind in table.CATALOG_KINDS], first['tool_calls'])
        self.assertEqual('', first['text'])
        messages += [dict(role='tool', name=read['name'], content=canonical(read['result']))
                     for read in reads]
        final = scripted(model='scripted-test', messages=messages)
        self.assertEqual([], final['tool_calls'])
        raw = json.loads(final['text'])
        # The live model contract omits host-bound node evidence.
        for node in raw['extraction']['nodes']:
            node.pop('evidence', None)
        return raw

    def valid(self, raw=None, reads=None, task=None):
        raw = self.raw if raw is None else raw
        reads = self.reads if reads is None else reads
        task = self.task if task is None else task
        value = self.agent.decode_result(canonical(raw), task, reads)
        return self.agent.validate_result(value, task, reads)

    def test_both_scripted_mails_bind_fourteen_literal_quotes_and_host_evidence(self):
        for name in inputs.INPUTS:
            with self.subTest(name=name):
                task = mail.make_task(source(name))
                reads = catalog_reads(task)
                raw = self.scripted_output(task, reads)
                self.assertTrue(self.valid(raw, reads, task))
                self.assertEqual(14, len(raw['field_evidence']))
                self.assertEqual(set(mail.FIELDS), {row['field'] for row in raw['field_evidence']})
                value = self.agent.decode_result(canonical(raw), task, reads)
                self.assertTrue(all(n['evidence'] for n in value['extraction']['nodes']))
                self.assertEqual(task.data()['input_sources'], value['input_sources'])
                self.assertEqual((800, 800), tuple(n['quantity'] for n in value['extraction']['nodes']
                    if n['type'] in {'ContractItem', 'DeliveryRound'}))
                # Full literal paragraphs, including adjacent context, are also valid quotations.
                for row in raw['field_evidence']:
                    row['quote'] = next(p for p in source(name)['text'].split('\n\n') if row['quote'] in p)
                self.assertTrue(self.valid(raw, reads, task))

    def test_wrong_quantity_product_revision_and_due_are_rejected(self):
        for kind, key, wrong in (
            ('ContractItem', 'quantity', 1000), ('DeliveryRound', 'quantity', 1000),
            ('ContractItem', 'configuration_id', 'config:esp32-poe:L1:WROOM'),
            ('ContractItem', 'configuration_id', 'config:invented'),
            ('DeliveryRound', 'due_at', '2026-10-30T15:00:00+09:00'),
            ('DeliveryRound', 'due_basis', 'dispatch')):
            with self.subTest(kind=kind, key=key, wrong=wrong):
                raw = deepcopy(self.raw)
                next(n for n in raw['extraction']['nodes'] if n['type'] == kind)[key] = wrong
                self.assertFalse(self.valid(raw))

    def test_swapped_duplicate_missing_invented_or_nonliteral_quotes_are_rejected(self):
        for mutation in ('swapped', 'duplicate', 'missing', 'invented_field', 'invented_quote', 'old_quote'):
            with self.subTest(mutation=mutation):
                raw = deepcopy(self.raw)
                rows = raw['field_evidence']
                if mutation == 'swapped':
                    rows[0]['quote'], rows[-1]['quote'] = rows[-1]['quote'], rows[0]['quote']
                elif mutation == 'duplicate':
                    rows.append(deepcopy(rows[0]))
                elif mutation == 'missing':
                    rows.pop()
                elif mutation == 'invented_field':
                    rows[0]['field'] = 'stock_quantity'
                elif mutation == 'invented_quote':
                    rows[0]['quote'] += ' (추정)'
                else:
                    next(r for r in rows if r['field'] == 'quantity')['quote'] = '견적 단계에서 논의한 1,000개'
                self.assertFalse(self.valid(raw))

    def test_wrong_version_and_unobserved_or_duplicate_citations_are_rejected(self):
        for key, wrong in (('data_version', 2), ('evidence_ids', ['evidence:invented']),
                           ('evidence_ids', self.raw['evidence_ids'] * 2)):
            with self.subTest(key=key, wrong=wrong):
                raw = deepcopy(self.raw)
                raw[key] = wrong
                self.assertFalse(self.valid(raw))
        raw = deepcopy(self.raw)
        raw['data_version'] = True
        with self.assertRaises(ValueError):
            self.agent.decode_result(canonical(raw), self.task, self.reads)

    def test_catalog_requires_actual_successful_exact_pinned_version_reads(self):
        variants = [[], self.reads[:-1]]
        for mutation in ('failed', 'wrong_version', 'wrong_node', 'missing_node', 'wrong_tool'):
            reads = deepcopy(self.reads)
            if mutation == 'failed':
                reads[0]['result']['ok'] = False
            elif mutation == 'wrong_version':
                reads[0]['result']['data_version'] = 2
            elif mutation == 'wrong_node':
                reads[0]['result']['data'][0]['name'] = 'Invented model'
            elif mutation == 'missing_node':
                reads[-1]['result']['data'] = [node for node in reads[-1]['result']['data']
                                              if node['id'] != 'config:esp32-poe:L:WROOM']
            else:
                reads[0]['name'] = 'get_snapshot'
            variants.append(reads)
        for index, reads in enumerate(variants):
            with self.subTest(index=index):
                self.assertFalse(self.valid(reads=reads))

    def test_selected_l_chain_alone_is_sufficient_without_reading_unrelated_l1(self):
        selected = {'model:olimex:esp32-poe', 'revision:esp32-poe:L', 'config:esp32-poe:L:WROOM'}
        for name in inputs.INPUTS:
            with self.subTest(name=name):
                task = mail.make_task(source(name))
                reads = catalog_reads(task)
                for read in reads:
                    result = read['result']
                    result['data'] = [node for node in result['data'] if node['id'] in selected]
                    result['evidence'] = sorted({eid for node in result['data']
                                                 for eid in node['evidence']})
                self.assertEqual(selected, {node['id'] for read in reads for node in read['result']['data']})
                raw = self.scripted_output(task, reads)
                self.assertTrue(self.valid(raw, reads, task))
                for missing in range(len(reads)):
                    with self.subTest(missing_kind=table.CATALOG_KINDS[missing]):
                        self.assertFalse(self.valid(raw, reads[:missing] + reads[missing + 1:], task))

    def test_nonready_requires_meaningful_issues_and_accepts_literal_partial_quotes(self):
        for status in ('needs_confirmation', 'invalid'):
            for issue in ('고객 납품 장소를 사람이 확인해야 합니다.', {'reason': '납품 조건 확인 필요'}):
                with self.subTest(status=status, issue=issue):
                    raw = deepcopy(self.raw)
                    raw['extraction'] = dict(status=status, nodes=[], issues=[issue])
                    raw['field_evidence'] = raw['field_evidence'][:1]
                    self.assertTrue(self.valid(raw))
            for issues in ([], [''], ['  '], [{}], [{'reason': ' '}], [{'code': 'UNRESOLVED'}]):
                with self.subTest(status=status, issues=issues):
                    raw = deepcopy(self.raw)
                    raw['extraction'] = dict(status=status, nodes=[], issues=issues)
                    raw['field_evidence'] = []
                    self.assertFalse(self.valid(raw))


class MailProviderTests(unittest.TestCase):
    def test_strict_request_describes_quotes_and_only_observed_catalog_citations(self):
        task = mail.make_task(source())
        agent = mail.MailIntakeAgent()
        transport = Transport([response([tool(cid='catalog-read')]), response([text('{}')])])
        policy = CostPolicy('100', (ModelPrice(MODEL, '1', '2', 400000),))
        budget = ExecutionBudget(BudgetLimits(max_model_calls=4), cost_policy=policy)
        provider = mail.MailProvider(OpenAISettings(SECRET), mail.SPEC.requested_tools,
                                     task=task, opener=transport)
        messages = agent.messages(task)
        with request_budget('mail-provider-test', budget):
            first = provider(model=MODEL, messages=messages)
            read = catalog_reads(task)[0]
            messages += [dict(role='assistant', content=first['text'], tool_calls=first['tool_calls']),
                         dict(role='tool', name='lookup', call_index=0, content=canonical(read['result']))]
            provider(model=MODEL, messages=messages)
        self.assertEqual(2, len(transport.requests))
        first_request, final_request = [json.loads(req.data) for req, _ in transport.requests]
        self.assertNotIn('format', first_request.get('text', {}))
        spec = final_request['text']['format']
        self.assertEqual(('json_schema', mail.SPEC.name, True),
                         (spec['type'], spec['name'], spec['strict']))
        schema = spec['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['properties']), set(schema['required']))
        quote = schema['properties']['field_evidence']['items']
        self.assertFalse(quote['additionalProperties'])
        self.assertEqual({'field', 'quote'}, set(quote['required']))
        self.assertEqual(set(mail.FIELDS), set(quote['properties']['field']['enum']))
        self.assertEqual(dict(type='string'), quote['properties']['quote'])
        self.assertEqual(set(read['result']['evidence']),
                         set(schema['properties']['evidence_ids']['items']['enum']))
        serialized = canonical(spec)
        for private in ('800', '2026-10-30', 'B-DEMO-NORMAL-800', 'L1:WROOM',
                        'baseline_path', 'anchors', '최종 계약 수량은'):
            self.assertNotIn(private, serialized)
        for node in task.data()['catalog_nodes']:
            self.assertNotIn(node['id'], serialized)


if __name__ == '__main__':
    unittest.main()
