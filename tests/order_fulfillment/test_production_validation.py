"""In-memory validation of cumulative production facts; no model or DB calls."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.model import ROOT, SCHEMA_VERSION, graph_jsonld, tbox_turtle, validate_graph


WORK = 'work:test:CQ03'
SNAPSHOT = 'snapshot:test:CQ03'
ROUND = 'delivery-round:demo:B-01:1'
CONFIGURATION = 'config:esp32-poe:L:WROOM'
PREVIOUS = 'observation:test:CQ03:09'
LATEST = 'observation:test:CQ03:10'
EVIDENCE = 'evidence:test:CQ03'
AS_OF = '2026-10-27T10:00:00+09:00'


class ProductionObservationValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.md', prefix='test-production-',
                                         dir=ROOT / 'data', delete=False) as stream:
            stream.write(b'Synthetic production validation fixture: cumulative observations, work and allocation.\n')
            cls.source_path = Path(stream.name)
        cls.addClassCleanup(cls.source_path.unlink)
        cls.source = dict(id='source:test:CQ03', type='SourceDocument', origin='synthetic', evidence=[],
                          path=str(cls.source_path.relative_to(ROOT)),
                          sha256=hashlib.sha256(cls.source_path.read_bytes()).hexdigest(),
                          url='urn:ontologics:test:production-observations', version='test-1',
                          license='Project synthetic test fixture', collected_at=AS_OF)

    def setUp(self):
        def fact(nid, kind, **fields):
            return dict(id=nid, type=kind, origin='synthetic', evidence=[EVIDENCE], **fields)
        evidence = dict(id=EVIDENCE, type='Evidence', origin='synthetic', evidence=[],
                        source_id=self.source['id'], locator='Markdown line 1',
                        description='Synthetic production validation fixture')
        self.nodes = copy.deepcopy(self.base + [self.source, evidence,
            fact(SNAPSHOT, 'FulfillmentSnapshot', company='DEMO-B', as_of=AS_OF,
                 round_ids=[ROUND], confirmations={}),
            fact(WORK, 'ProductionWork', snapshot_id=SNAPSHOT, company='DEMO-B',
                 configuration_id=CONFIGURATION, observed_at=AS_OF, target_quantity=1000,
                 completed_quantity=600, unit='piece', production_state='in_progress',
                 started_at='2026-10-27T08:00:00+09:00'),
            fact('allocation:test:CQ03', 'Allocation', snapshot_id=SNAPSHOT,
                 delivery_round_id=ROUND, production_work_id=WORK, quantity=1000, unit='piece'),
            fact(PREVIOUS, 'ProductionObservation', production_work_id=WORK,
                 observed_at='2026-10-27T09:00:00+09:00', produced_quantity=550,
                 good_quantity=510, defective_quantity=40, unit='piece'),
            fact(LATEST, 'ProductionObservation', production_work_id=WORK,
                 observed_at=AS_OF, produced_quantity=650, good_quantity=600,
                 defective_quantity=50, unit='piece')])
        self.index = {node['id']: node for node in self.nodes}

    def valid(self):
        result = validate_graph(self.nodes)
        self.assertTrue(result['valid'], result)
        return result

    def invalid(self, code=None):
        result = validate_graph(self.nodes)
        self.assertFalse(result['valid'], result)
        if code:
            self.assertIn(code, {item['code'] for item in result['errors']}, result)
        return result

    def test_base_records_validate_and_preserve_partial_work(self):
        before = copy.deepcopy(self.nodes)
        self.valid()
        self.assertEqual(600, self.index[WORK]['completed_quantity'])
        self.assertEqual(before, self.nodes)

    def test_counter_types_exclude_bool_float_text_and_nested_json(self):
        node = self.index[PREVIOUS]
        for field in ('produced_quantity', 'good_quantity', 'defective_quantity'):
            original = node[field]
            for value in (-1, True, False, 0.0, 1.5, '1', None, [], {}, [[]]):
                with self.subTest(field=field, value=value):
                    node[field] = value
                    self.invalid('INVALID_QUANTITY')
            node[field] = original
        self.valid()

    def test_zero_counters_are_valid(self):
        self.index[PREVIOUS].update(produced_quantity=0, good_quantity=0, defective_quantity=0)
        self.valid()

    def test_total_requires_good_plus_defective(self):
        self.index[PREVIOUS]['produced_quantity'] = 551
        self.invalid('PRODUCTION_TOTAL_MISMATCH')

    def test_each_counter_must_not_decrease_even_on_old_rows(self):
        cases = [dict(produced_quantity=700, good_quantity=660, defective_quantity=40),
                 dict(produced_quantity=650, good_quantity=610, defective_quantity=40),
                 dict(produced_quantity=550, good_quantity=490, defective_quantity=60)]
        node = self.index[PREVIOUS]
        for values in cases:
            with self.subTest(counters=values):
                node.update(values)
                self.invalid('PRODUCTION_COUNTER_DECREASED')

    def test_storage_order_is_irrelevant_to_counter_order(self):
        self.nodes.reverse()
        self.valid()

    def test_same_instant_duplicates_are_rejected_even_with_same_values(self):
        duplicate = copy.deepcopy(self.index[LATEST])
        duplicate.update(id=LATEST + ':duplicate', observed_at='2026-10-27T01:00:00Z')
        self.nodes.append(duplicate)
        self.invalid('DUPLICATE_PRODUCTION_OBSERVATION')
        duplicate.update(produced_quantity=651, defective_quantity=51)
        self.invalid('DUPLICATE_PRODUCTION_OBSERVATION')

    def test_separate_works_can_have_same_observed_instant(self):
        work = copy.deepcopy(self.index[WORK]); work['id'] = WORK + ':other'
        observation = copy.deepcopy(self.index[LATEST])
        observation.update(id=LATEST + ':other', production_work_id=work['id'])
        self.nodes.extend([work, observation])
        self.valid()

    def test_observation_time_requires_clock_and_offset_and_handles_bad_json(self):
        node = self.index[LATEST]
        for value in ('2026-10-27', '2026-10-27T10:00:00', 'bad-time', None, [], {}, [[]]):
            with self.subTest(value=value):
                node['observed_at'] = value
                self.invalid('INVALID_TIME')

    def test_observation_cannot_precede_actual_start(self):
        self.index[PREVIOUS]['observed_at'] = '2026-10-27T07:59:59+09:00'
        self.invalid('CONFLICTING_TIME')
        self.index[PREVIOUS]['observed_at'] = '2026-10-27T08:00:00+09:00'
        self.valid()

    def test_future_observation_not_silently_filtered(self):
        self.index[LATEST]['observed_at'] = '2026-10-27T10:00:01+09:00'
        self.invalid('CONFLICTING_TIME')

    def test_observation_cannot_follow_stale_observed_work_state(self):
        self.index[WORK].update(observed_at='2026-10-27T09:00:00+09:00', completed_quantity=510)
        self.invalid('CONFLICTING_TIME')

    def test_same_time_observation_must_match_completed_work_count(self):
        self.index[LATEST].update(good_quantity=599, defective_quantity=51)
        self.invalid('PRODUCTION_COMPLETION_MISMATCH')

    def test_historical_good_count_cannot_exceed_later_work_completion_when_latest_is_missing(self):
        self.nodes[:] = [node for node in self.nodes if node['id'] != LATEST]
        for good in (700, 1001):
            with self.subTest(historical_good=good):
                self.index[PREVIOUS].update(produced_quantity=good + 40,
                                            good_quantity=good, defective_quantity=40)
                self.invalid('PRODUCTION_COMPLETION_MISMATCH')
        # Different observation instants are not themselves contradictory:
        # the historical cumulative count can legitimately be lower.
        self.index[PREVIOUS].update(produced_quantity=550, good_quantity=510, defective_quantity=40)
        self.valid()

    def test_missing_latest_and_missing_all_observations_remain_valid(self):
        self.nodes[:] = [node for node in self.nodes if node['id'] != LATEST]
        self.valid()
        self.nodes[:] = [node for node in self.nodes if node['type'] != 'ProductionObservation']
        self.valid()

    def test_stale_snapshot_work_and_observation_are_valid_but_not_retimed(self):
        self.nodes[:] = [node for node in self.nodes if node['id'] != LATEST]
        self.index[WORK].update(observed_at='2026-10-27T09:00:00+09:00', completed_quantity=510)
        self.valid()
        self.assertEqual(AS_OF, self.index[SNAPSHOT]['as_of'])

    def test_reference_must_resolve_to_production_work(self):
        node = self.index[LATEST]
        for value in ('missing:work', ROUND, None, [], {}, [['nested']]):
            with self.subTest(value=value):
                node['production_work_id'] = value
                self.invalid('INVALID_REFERENCE')

    def test_observation_unit_must_be_piece_and_match_work(self):
        node = self.index[LATEST]
        for value in ('box', '', None, [], {}):
            with self.subTest(value=value):
                node['unit'] = value
                self.invalid('UNSUPPORTED_UNIT')
                self.invalid('UNIT_MISMATCH')

    def test_real_origin_or_real_evidence_cannot_claim_production(self):
        node = self.index[LATEST]
        node['origin'] = 'real'
        self.invalid('DEMO_OPERATIONS_ONLY')
        node['origin'] = 'synthetic'
        node['evidence'] = self.index[CONFIGURATION]['evidence'][:]
        self.invalid('EVIDENCE_ORIGIN_MISMATCH')
        node['evidence'] = []
        self.invalid('EVIDENCE_REQUIRED')

    def test_unknown_company_quality_and_uptime_fields_cannot_hide_in_observation(self):
        node = self.index[LATEST]
        for field in ('company', 'configuration_id', 'fault_cause', 'test_result', 'uptime', 'defect_rate'):
            with self.subTest(field=field):
                node[field] = 'unsupported'
                self.invalid('UNKNOWN_FIELD')
                del node[field]

    def test_missing_each_required_observation_field_returns_report(self):
        node = self.index[LATEST]
        for field in ('production_work_id', 'observed_at', 'produced_quantity', 'good_quantity',
                      'defective_quantity', 'unit'):
            with self.subTest(field=field):
                value = node.pop(field)
                self.invalid('MISSING_FIELD')
                node[field] = value

    def test_ready_or_waiting_positive_counters_conflict_without_invented_start(self):
        work = self.index[WORK]
        del work['started_at']
        for state in ('ready', 'waiting'):
            work['production_state'] = state
            self.invalid('CONFLICTING_PRODUCTION_STATE')

    def test_unstarted_work_with_zero_observation_is_allowed(self):
        work = self.index[WORK]
        work.update(production_state='ready', completed_quantity=0)
        del work['started_at']
        for nid in (PREVIOUS, LATEST):
            self.index[nid].update(produced_quantity=0, good_quantity=0, defective_quantity=0)
        self.valid()
        work['started_at'] = '2026-10-27T08:00:00+09:00'
        self.invalid('CONFLICTING_TIME')

    def test_existing_work_target_cap_preserved_but_total_can_exceed_target(self):
        self.index[LATEST].update(produced_quantity=1100, good_quantity=600, defective_quantity=500)
        self.valid()
        self.index[WORK]['completed_quantity'] = 1001
        self.index[LATEST].update(produced_quantity=1501, good_quantity=1001)
        self.invalid('WORK_QUANTITY_EXCEEDED')

    def test_malformed_shapes_on_every_observation_field_do_not_raise(self):
        node = self.index[LATEST]
        for field in list(node):
            old = node[field]
            for value in (None, [], [[]], {'nested': []}):
                with self.subTest(field=field, value=value):
                    node[field] = value
                    self.invalid()
            node[field] = old
        self.valid()

    def test_jsonld_tbox_and_schema_include_observation_relationship(self):
        self.assertEqual('graph-0.5', SCHEMA_VERSION)
        graph = graph_jsonld(self.nodes)
        self.assertEqual({'@type': '@id'}, graph['@context']['production_work_id'])
        node = next(node for node in graph['@graph'] if node['type'] == 'ProductionObservation')
        self.assertEqual('urn:ontologics:entity:' + WORK, node['production_work_id'])
        self.assertIn('o:ProductionObservation a owl:Class .', tbox_turtle())
        self.assertEqual(tbox_turtle(), (ROOT / 'ontology/tbox.ttl').read_text())


if __name__ == '__main__':
    unittest.main()
