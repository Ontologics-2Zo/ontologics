"""CQ04 condition contract and existing source/time/scope invariants; no DB or LLM."""
import copy
import hashlib
import json
import unittest

from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.model import ROOT
from ontologics.order_fulfillment.schema_validation import validate_input_graph
from ontologics.order_fulfillment.schema_export import graph_jsonld as b_graph_jsonld, tbox_turtle as b_tbox_turtle

ROUND='delivery-round:demo:B-01:1'


class DeliveryConditionValidationTests(unittest.TestCase):
    def setUp(self):
        self.load()

    def load(self, mixed=False):
        self.candidates=load_demo_delivery(mixed=mixed)
        self.nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']+load_demo_contract()+self.candidates
        self.condition=next(n for n in self.nodes if n['type']=='DeliveryCondition')
        self.snapshot=next(n for n in self.nodes if n['type']=='FulfillmentSnapshot')
        self.index={n['id']:n for n in self.nodes}

    def valid(self):
        report=validate_input_graph(self.nodes)
        self.assertTrue(report['valid'],report)
        return report

    def invalid(self,code=None):
        report=validate_input_graph(self.nodes)
        self.assertFalse(report['valid'],report)
        if code:self.assertIn(code,{e['code'] for e in report['errors']},report)
        return report

    def test_D16_shared_base_and_mixed_condition_graphs_are_valid_and_read_only(self):
        for mixed in (False,True):
            with self.subTest(mixed=mixed):
                self.load(mixed=mixed)
                before=copy.deepcopy(self.nodes)
                self.valid()
                self.assertEqual(before,self.nodes)

    def test_D16_each_required_condition_field_is_required(self):
        for field in ('snapshot_id','delivery_round_id','preparation_seconds','transport_seconds','dispatch_mode'):
            value=self.condition.pop(field)
            with self.subTest(field=field):self.invalid('MISSING_FIELD')
            self.condition[field]=value
        self.valid()

    def test_D17_duration_types_exclude_bool_float_text_nan_and_nested_json(self):
        for field in ('preparation_seconds','transport_seconds'):
            original=self.condition[field]
            for value in (-1,True,False,0.0,1.5,'3600',None,[],{},[[]],float('nan')):
                self.condition[field]=value
                with self.subTest(field=field,value=value):self.invalid()
            self.condition[field]=original
        self.valid()

    def test_D12_explicit_zero_durations_are_valid(self):
        self.condition.update(preparation_seconds=0,transport_seconds=0)
        self.valid()

    def test_D18_only_two_explicit_dispatch_modes_are_accepted(self):
        for value in ('split_available','all_at_once'):
            self.condition['dispatch_mode']=value
            self.valid()
        for value in ('split','',None,True,[],{},['split_available']):
            self.condition['dispatch_mode']=value
            with self.subTest(value=value):self.invalid()

    def test_D22_condition_references_require_correct_existing_types(self):
        for field in ('snapshot_id','delivery_round_id'):
            original=self.condition[field]
            wrong_type=ROUND if field=='snapshot_id' else self.snapshot['id']
            for value in ('missing:id',wrong_type,None,[],{},[[]]):
                self.condition[field]=value
                with self.subTest(field=field,value=value):self.invalid('INVALID_REFERENCE')
            self.condition[field]=original
        self.valid()

    def test_D22_condition_round_must_be_listed_in_snapshot(self):
        self.snapshot['round_ids']=[]
        self.invalid()

    def test_D20_malformed_snapshot_round_list_returns_findings_instead_of_crashing(self):
        for value in (None, 1, True, 'round', {}, [[]]):
            self.snapshot['round_ids'] = value
            with self.subTest(round_ids=value):
                self.invalid()

    def test_D22_condition_cannot_link_other_company_round(self):
        self.index['contract:demo:B-01']['company']='other-company'
        self.invalid('COMPANY_MISMATCH')

    def test_D23_same_snapshot_round_cannot_have_two_conflicting_conditions(self):
        duplicate=dict(self.condition,id=self.condition['id']+':duplicate',transport_seconds=7200)
        self.nodes.append(duplicate)
        self.invalid()
        duplicate['transport_seconds']=self.condition['transport_seconds']
        self.invalid()

    def test_D35_condition_requires_synthetic_origin_and_synthetic_evidence(self):
        self.condition['origin']='real'
        self.invalid('DEMO_OPERATIONS_ONLY')
        self.condition['origin']='synthetic'
        original=self.condition['evidence'][:]
        self.condition['evidence']=[]
        self.invalid('EVIDENCE_REQUIRED')
        self.condition['evidence']=self.index['config:esp32-poe:L:WROOM']['evidence'][:]
        self.invalid('EVIDENCE_ORIGIN_MISMATCH')
        self.condition['evidence']=original
        self.valid()

    def test_unknown_condition_fields_cannot_hide_destinations_or_new_forecast_facts(self):
        for field in ('company','destination','on_time_quantity','actual_arrival_at','future_shipment'):
            self.condition[field]='unsupported'
            with self.subTest(field=field):self.invalid('UNKNOWN_FIELD')
            del self.condition[field]

    def test_malformed_condition_shapes_return_findings_without_crashing(self):
        for field in tuple(self.condition):
            original=self.condition[field]
            for value in (None,[],[[]],{'nested':[]}):
                self.condition[field]=value
                with self.subTest(field=field,value=value):self.invalid()
            self.condition[field]=original
        self.valid()

    def test_D22_source_configuration_unit_and_shipment_destination_mismatch_rejected(self):
        self.load(mixed=True)
        work=next(n for n in self.nodes if n['type']=='ProductionWork')
        original=work['configuration_id']
        work['configuration_id']='config:esp32-poe:L1:WROOM'
        self.invalid('CONFIGURATION_MISMATCH')
        work['configuration_id']=original
        work['unit']='box'
        self.invalid('UNSUPPORTED_UNIT')
        work['unit']='piece'
        shipment=next(n for n in self.nodes if n['type']=='Shipment')
        shipment['destination']='other customer location'
        self.invalid('DESTINATION_MISMATCH')

    def test_D19_duplicate_utc_observation_and_invalid_time_are_rejected(self):
        observation=next(n for n in self.nodes if n['type']=='ProductionObservation')
        duplicate=dict(observation,id=observation['id']+':duplicate',observed_at='2026-10-27T00:00:00Z')
        # The original earlier sample is09:00+09, equal to00:00Z.
        self.nodes.append(duplicate)
        self.invalid('DUPLICATE_PRODUCTION_OBSERVATION')
        self.nodes.remove(duplicate)
        observation['observed_at']='2026-10-27T09:00:00'
        self.invalid('INVALID_TIME')

    def test_D32_source_overlap_confirmation_is_explicit_not_inferred_from_ids(self):
        # Absence of this assertion is valid incomplete data; the read tool must return unknown.
        self.snapshot['confirmations'].pop('material_disjoint')
        self.valid()

    def test_D35_shared_sources_are_hash_pinned_and_each_evidence_locator_present(self):
        for mixed in (False,True):
            self.load(mixed=mixed)
            sources={n['id']:n for n in self.candidates if n['type']=='SourceDocument'}
            for source in sources.values():
                self.assertEqual(source['sha256'],hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest())
            for evidence in (n for n in self.candidates if n['type']=='Evidence'):
                self.assertIn(evidence['source_id'],sources)
                self.assertTrue(evidence['locator'])

    def test_schema_tbox_and_jsonld_expose_typed_condition_relationships(self):
        graph=b_graph_jsonld(self.nodes)
        condition=next(n for n in graph['@graph'] if n['type']=='DeliveryCondition')
        self.assertEqual('urn:ontologics:entity:'+self.snapshot['id'],condition['snapshot_id'])
        self.assertEqual('urn:ontologics:entity:'+ROUND,condition['delivery_round_id'])
        self.assertIn('o:DeliveryCondition a owl:Class .',b_tbox_turtle())


if __name__=='__main__':
    unittest.main()
