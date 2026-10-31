"""Adapter unit tests use response stubs, never a replacement database/host.

Positive delivery data is authored solely for each test, removed at cleanup,
and never represents supplementation of the current contract.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ontologics.model import digest
from ontologics.production_planning import validate_planning_context
from ontologics.production_planning import reference_adapter as adapter
from tests.production_planning.test_ontology_extension import catalog, round_node, ROOT


def partial_inputs():
    folder = ROOT / 'data/production_planning'
    context = json.loads((folder / 'request_graph_v1.json').read_text())
    context['data_version'] = 2  # explicit test snapshot; never rewrite the original
    nodes = catalog() + json.loads((folder / 'graph_nodes_v1.json').read_text())['nodes']
    names = {'input:planning:contract': 'contract.txt', 'input:planning:stock': 'stock.json',
             'input:planning:production': 'production.json', 'input:planning:lots:v1': 'lot_reference_v1.json'}
    contents = {ref: (folder / filename).read_bytes() for ref, filename in names.items()}
    return context, nodes, contents


def complete_inputs(test):
    """Trusted test-host mapping with an explicitly separate synthetic source."""
    context, nodes, contents = partial_inputs()
    temp = tempfile.TemporaryDirectory(prefix='planning-test-', dir=ROOT / 'data')
    test.addCleanup(temp.cleanup)
    path = Path(temp.name) / 'delivery-test.json'
    facts = dict(origin='synthetic', notice='Synthetic positive test only; NOT current contract facts',
                 contract_source_ref='input:planning:contract',
                 contract_sha256=contents_sha(contents['input:planning:contract']),
                 contract_item_id='contract-item:planning:001', round_id='round:planning:001',
                 due_at='2026-11-10T12:30:00.123456+09:00', due_basis='arrival',
                 destination='Synthetic test receiving dock', quantity=100, unit='piece')
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + '\n')
    ref, sid, eid = 'input:test:delivery', 'source:test:delivery', 'evidence:test:delivery'
    content = path.read_bytes(); sha = contents_sha(content)
    mapping = dict(source_ref=ref, source_id=sid, path=str(path.relative_to(ROOT)),
                   evidence_id=eid, locator='/')
    test.enterContext(patch.object(adapter, '_DELIVERY_SOURCE', mapping))
    context['contract_item']['delivery_due'] = facts['due_at']
    context['external_inputs'].append(dict(source_ref=ref, sha256=sha))
    contents[ref] = content
    nodes += [dict(id=sid, type='SourceDocument', origin='synthetic', evidence=[],
                   path=mapping['path'], sha256=sha, version=sha, url='urn:test:delivery',
                   license='Project demonstration data', collected_at='2026-10-27T17:10:21+09:00'),
              dict(id=eid, type='Evidence', origin='synthetic', evidence=[], source_id=sid,
                   locator='/', description=facts['notice']),
              round_node(due_at=facts['due_at'], destination=facts['destination'], evidence=[eid])]
    return context, nodes, contents


def contents_sha(content):
    return hashlib.sha256(content).hexdigest()


def envelope(data, *, ok=True, version=2):
    return dict(ok=ok, data=data, evidence=[], data_version=version,
                schema_version='graph-0.5-a-planning-1', rule_version='temporal-0.1',
                contract_version='test-envelope', warnings=[], conflicts=[], errors=[], run_id='unit-test-run')


def responses(nodes):
    ops = [dict(op='put', node=copy.deepcopy(n)) for n in nodes if n['origin'] == 'synthetic']
    draft = envelope(dict(id='test-draft', version=1, author='user:author', base_version=1,
                          operations=ops, content_hash=digest(ops), status='applied',
                          reviewer='user:reviewer', review_note='Synthetic test',
                          applied_version=2, created_at='2026-10-27T17:10:21+09:00', review_history=[]), version=7)
    return draft, envelope(copy.deepcopy(nodes))


class ReferenceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.context, self.nodes, self.contents = partial_inputs()
        self.registration_ref = dict(draft_id='test-draft', draft_version=1)

    def complete(self):
        self.context, self.nodes, self.contents = complete_inputs(self)

    def call(self, *, responses_override=None, registration_ref=None):
        draft, lookup = responses_override or responses(self.nodes)
        session = Mock(spec=['call'])
        session.call.side_effect = [draft, lookup]
        result = adapter.validate_registered_context(session, self.context,
            external_contents=self.contents,
            registration_ref=self.registration_ref if registration_ref is None else registration_ref)
        return result, session, draft, lookup

    def codes(self, result, bucket='errors'):
        return [e['code'] for e in result['report'][bucket]]

    def test_explicit_synthetic_complete_case_checks_exact_draft_then_full_snapshot(self):
        self.complete()
        original = copy.deepcopy((self.context, self.nodes, self.contents))
        result, session, draft, lookup = self.call()
        report = result['report']
        self.assertTrue(report['valid'], report)
        self.assertTrue(report['graph_integration_verified'], report)
        self.assertEqual('needs_confirmation', report['status'])
        self.assertIn('HUMAN_CONFIRMATION_REQUIRED', self.codes(result, 'unresolved'))
        self.assertIn('STOCK_COVERAGE_UNCONFIRMED', self.codes(result, 'unresolved'))
        self.assertIs(result['registration_response'], draft)
        self.assertIs(result['lookup_response'], lookup)
        self.assertEqual([(('get_draft', {'draft_id': 'test-draft', 'version': 1}), {}),
                          (('lookup', {'version': 2}), {})], session.call.call_args_list)
        self.assertEqual(original, (self.context, self.nodes, self.contents))
        standalone = validate_planning_context(self.context,
            reference=dict(data_version=2, nodes=self.nodes), external_contents=self.contents)
        self.assertFalse(standalone['graph_integration_verified'])

    def test_current_date_only_and_missing_round_remain_partial(self):
        result, *_ = self.call()
        self.assertTrue(result['report']['valid'], result)
        self.assertFalse(result['report']['graph_integration_verified'])
        self.assertIn('REGISTERED_CONTEXT_INCOMPLETE', self.codes(result, 'unresolved'))

    def test_missing_or_invalid_registration_never_calls_session(self):
        session = Mock(spec=['call'])
        for ref in (None, {}, {'draft_id': 'x', 'draft_version': True},
                    {'draft_id': 'x', 'draft_version': 1.0}, {'draft_id': ' ', 'draft_version': 1},
                    {'draft_id': 'x', 'draft_version': 1, 'extra': True}):
            with self.subTest(ref=ref):
                result = adapter.validate_registered_context(session, self.context,
                    external_contents=self.contents, registration_ref=ref)
                self.assertFalse(result['report']['graph_integration_verified'])
                self.assertIsNone(result['lookup_response'])
        session.call.assert_not_called()

    def test_local_invalid_request_is_not_sent_to_public_tools(self):
        self.context['data_version'] = True
        result, session, *_ = self.call()
        self.assertIn('INVALID_VERSION', self.codes(result)); session.call.assert_not_called()

    def test_failed_public_responses_preserve_every_envelope_field(self):
        draft, lookup = responses(self.nodes)
        for which in ('draft', 'lookup'):
            failure = envelope(None, ok=False)
            failure.update(errors=[dict(code='FORBIDDEN', message='kept')],
                           warnings=[dict(code='kept')], conflicts=[dict(id='kept')])
            pair = (failure, lookup) if which == 'draft' else (draft, failure)
            result, session, *_ = self.call(responses_override=pair)
            self.assertIsNone(result['report'])
            self.assertIs(result['registration_response' if which == 'draft' else 'lookup_response'], failure)
            self.assertEqual(1 if which == 'draft' else 2, session.call.call_count)

    def test_applied_version_and_exact_identity_are_not_latest_envelope_version(self):
        for field, value, code in [('status', 'approved', 'REGISTRATION_NOT_APPLIED'),
                                  ('applied_version', 3, 'REFERENCE_VERSION_MISMATCH'),
                                  ('applied_version', True, 'REFERENCE_VERSION_MISMATCH'),
                                  ('version', True, 'REGISTRATION_SCOPE_MISMATCH'),
                                  ('id', 'unrelated', 'REGISTRATION_SCOPE_MISMATCH')]:
            draft, lookup = responses(self.nodes); draft['data'][field] = value
            result, session, *_ = self.call(responses_override=(draft, lookup))
            self.assertIn(code, self.codes(result)); self.assertEqual(1, session.call.call_count)
        draft, lookup = responses(self.nodes); lookup['data_version'] = 3
        result, *_ = self.call(responses_override=(draft, lookup))
        self.assertIn('REFERENCE_VERSION_MISMATCH', self.codes(result))

    def test_operations_must_cover_exact_facts_evidence_and_sources(self):
        self.complete()
        for kind in ('Contract', 'ContractItem', 'DeliveryRound', 'ComponentStockSnapshot', 'ComponentLot', 'Evidence', 'SourceDocument'):
            draft, lookup = responses(self.nodes)
            draft['data']['operations'] = [op for op in draft['data']['operations'] if op['node']['type'] != kind]
            draft['data']['content_hash'] = digest(draft['data']['operations'])
            result, *_ = self.call(responses_override=(draft, lookup))
            self.assertIn('REGISTRATION_SCOPE_MISMATCH', self.codes(result), kind)
        draft, lookup = responses(self.nodes)
        draft['data']['operations'][0]['node']['license'] = 'changed'
        draft['data']['content_hash'] = digest(draft['data']['operations'])
        result, *_ = self.call(responses_override=(draft, lookup))
        self.assertIn('REGISTRATION_SCOPE_MISMATCH', self.codes(result))

    def test_request_order_configuration_parent_and_stock_values_must_match(self):
        self.complete()
        for target, field, bad in [('contract-item:planning:001', 'quantity', 101),
                                   ('contract-item:planning:001', 'configuration_id', 'config:esp32-poe:L1:WROOM'),
                                   ('contract:planning:001', 'customer_name', 'Other customer'),
                                   ('stock:planning:001', 'on_hand_quantity', 101),
                                   ('stock:planning:001', 'deductions_disjoint', False),
                                   ('stock:planning:001', 'location', 'different'),
                                   ('stock:planning:001', 'as_of', '2026-10-26T09:00:01+09:00'),
                                   ('lot:planning:lan8710:001', 'supplier', 'Other supplier')]:
            with self.subTest(target=target, field=field):
                nodes = copy.deepcopy(self.nodes)
                next(n for n in nodes if n['id'] == target)[field] = bad
                result, *_ = self.call(responses_override=responses(nodes))
                self.assertIn('REGISTERED_VALUE_MISMATCH', self.codes(result))
                self.assertFalse(result['report']['graph_integration_verified'])

    def test_missing_ids_and_invalid_or_partial_graph_are_errors(self):
        for kind in ('ContractItem', 'ComponentStockSnapshot', 'Configuration', 'SourceDocument'):
            draft, lookup = responses(self.nodes)
            lookup['data'] = [n for n in lookup['data'] if n['type'] != kind]
            result, *_ = self.call(responses_override=(draft, lookup))
            self.assertFalse(result['report']['valid'], result)

    def test_single_full_round_limit_is_adapter_only(self):
        self.complete()
        for quantities in ([40, 60], [99], []):
            nodes = [n for n in self.nodes if n['type'] != 'DeliveryRound']
            for i, q in enumerate(quantities):
                nodes.append(round_node(id=f'round:{i}', quantity=q,
                    evidence=['evidence:test:delivery'], due_at=self.context['contract_item']['delivery_due']))
            result, *_ = self.call(responses_override=responses(nodes))
            self.assertFalse(result['report']['graph_integration_verified'], result)

    def test_optional_presence_and_null_are_not_silently_equal(self):
        self.complete()
        for key, val in [('available_quantity', 70), ('deductions_disjoint', None)]:
            context = copy.deepcopy(self.context)
            nodes = copy.deepcopy(self.nodes)
            stock = next(n for n in nodes if n['id'] == 'stock:planning:001')
            stock[key] = val
            self.context['stocks'][0].pop(key, None)
            result, *_ = self.call(responses_override=responses(nodes))
            self.assertIn('REGISTERED_CONTEXT_INCOMPLETE', self.codes(result, 'unresolved'))
            self.assertFalse(result['report']['graph_integration_verified'])
            self.context = context

    def test_unknown_quantities_remain_unresolved_even_when_linked(self):
        # The fixed current stock source has known values. Unknown request values
        # must not be accepted as a match to that source or to known graph values.
        self.complete()
        self.context['stocks'][0].update(quantity_state='unknown', on_hand_quantity=None)
        result, *_ = self.call()
        self.assertIn('UNKNOWN_QUANTITY', self.codes(result, 'unresolved'))
        self.assertIn('REGISTERED_VALUE_MISMATCH', self.codes(result))

    def test_original_hash_evidence_locator_origin_and_source_mapping(self):
        self.complete()
        for change in ('bytes', 'locator', 'source_path', 'evidence_origin', 'no_bytes', 'no_lots_input'):
            context, nodes, contents = copy.deepcopy((self.context, self.nodes, self.contents))
            if change == 'bytes': contents['input:planning:contract'] += b' changed'
            if change == 'locator':
                next(n for n in nodes if n['id'] == 'evidence:planning:contract:v1:item-001')['locator'] = 'lines 4-5'
            if change == 'source_path':
                source = next(n for n in nodes if n['id'] == 'source:planning:contract:v1')
                source.update(path='data/production_planning/stock.json', sha256=contents_sha(contents['input:planning:stock']))
            if change == 'evidence_origin': next(n for n in nodes if n['id'] == 'evidence:test:delivery')['origin'] = 'real'
            if change == 'no_bytes': contents.pop('input:planning:lots:v1')
            if change == 'no_lots_input': context['external_inputs'] = context['external_inputs'][:3]
            result = adapter.validate_registered_context(Mock(call=Mock(side_effect=responses(nodes))), context,
                external_contents=contents, registration_ref=self.registration_ref)
            self.assertFalse(result['report']['graph_integration_verified'], (change, result))

    def test_adapter_rejects_submicrosecond_precision_while_candidate_still_accepts_it(self):
        self.complete()
        for section, key in [('contract_item', 'delivery_due'), ('stock_scope', 'as_of')]:
            original = self.context[section][key]
            self.context[section][key] = '2026-11-10T12:30:00.1234567+09:00'
            self.assertTrue(validate_planning_context(self.context)['valid'])
            result, *_ = self.call()
            self.assertIn('INVALID_TIME', self.codes(result))
            self.context[section][key] = original

    def test_explicit_known_state_and_equivalent_offsets_match_source_defaults(self):
        self.complete()
        self.context['stocks'][0]['quantity_state'] = 'known'
        self.context['stock_scope']['as_of'] = '2026-10-26T00:00:00Z'
        result, *_ = self.call()
        self.assertTrue(result['report']['graph_integration_verified'], result)

    def test_unknown_unmapped_external_source_cannot_assert_full_mapping(self):
        self.complete()
        self.contents['input:unknown'] = b'unknown source'
        self.context['external_inputs'].append(dict(source_ref='input:unknown',
            sha256=contents_sha(self.contents['input:unknown'])))
        result, *_ = self.call()
        self.assertIn('SOURCE_MAPPING_UNCONFIRMED', self.codes(result, 'unresolved'))
        self.assertFalse(result['report']['graph_integration_verified'])

    def test_round_destination_parent_and_supplement_must_match(self):
        self.complete()
        for field, replacement in [('destination', 'Another dock'), ('due_at', '2026-11-10T13:00:00+09:00')]:
            nodes = copy.deepcopy(self.nodes)
            next(n for n in nodes if n['type'] == 'DeliveryRound')[field] = replacement
            result, *_ = self.call(responses_override=responses(nodes))
            self.assertIn('REGISTERED_VALUE_MISMATCH', self.codes(result))
        nodes = copy.deepcopy(self.nodes)
        other = copy.deepcopy(next(n for n in nodes if n['type'] == 'Contract'))
        other['id'] = 'contract:unrelated'; nodes.append(other)
        next(n for n in nodes if n['type'] == 'ContractItem')['contract_id'] = other['id']
        result, *_ = self.call(responses_override=responses(nodes))
        self.assertIn('REGISTERED_VALUE_MISMATCH', self.codes(result))

    def test_source_backed_unknown_null_can_link_without_claiming_calculation(self):
        self.complete()
        source = next(n for n in self.nodes if n['id'] == 'source:planning:stock:v1')
        old_data = json.loads(self.contents['input:planning:stock'])
        old_data['stocks'][0].update(quantity_state='unknown', on_hand_quantity=None)
        folder = tempfile.TemporaryDirectory(prefix='planning-unknown-test-', dir=ROOT / 'data')
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'stock-test.json'
        path.write_text(json.dumps(old_data))
        raw = path.read_bytes(); sha = contents_sha(raw)
        source.update(path=str(path.relative_to(ROOT)), sha256=sha, version=sha)
        mappings = dict(adapter._SOURCES)
        mappings['input:planning:stock'] = (source['id'], source['path'])
        self.enterContext(patch.object(adapter, '_SOURCES', mappings))
        self.contents['input:planning:stock'] = raw
        next(e for e in self.context['external_inputs'] if e['source_ref'] == 'input:planning:stock')['sha256'] = sha
        self.context['stocks'][0].update(quantity_state='unknown', on_hand_quantity=None)
        next(n for n in self.nodes if n['id'] == 'stock:planning:001').update(quantity_state='unknown', on_hand_quantity=None)
        result, *_ = self.call()
        self.assertTrue(result['report']['graph_integration_verified'], result)
        self.assertIn('UNKNOWN_QUANTITY', self.codes(result, 'unresolved'))

    def test_bool_float_null_registration_and_observation_do_not_coerce(self):
        self.complete()
        for bad in (True, 100.0, None):
            nodes = copy.deepcopy(self.nodes)
            next(n for n in nodes if n['id'] == 'stock:planning:001')['on_hand_quantity'] = bad
            result, *_ = self.call(responses_override=responses(nodes))
            self.assertIn('INVALID_REFERENCE_GRAPH', self.codes(result))
        for bad in (True, 2.0):
            draft, lookup = responses(self.nodes); lookup['data_version'] = bad
            result, *_ = self.call(responses_override=(draft, lookup))
            self.assertFalse(result['report']['graph_integration_verified'])
