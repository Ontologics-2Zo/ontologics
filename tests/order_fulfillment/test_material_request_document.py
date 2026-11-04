import copy
import json
import tempfile
import unittest
from pathlib import Path
from ontologics.model import ROOT, validate_graph
from ontologics.order_fulfillment.material_request_document import (
    validate_request, make_record, render_document, build_nodes, read_record,
)


class RequestDocumentTests(unittest.TestCase):
    def setUp(self):
        self.nodes = json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        self.nodes += [{'id':'evidence:plan', 'type':'Evidence','origin':'synthetic'}]
        self.payload = dict(request_id='MREQ-HB-20261103-001', revision=1,
            configuration_id='config:esp32-poe:L:WROOM', baseline_evidence_id='evidence:plan',
            quantity=30, unit='piece', need_date='2026-11-20', need_basis='production_complete',
            requester='영업운영팀 김민서', requested_at='2026-11-03T09:30:00+09:00',
            reason='기존 계획 200대 외 현장 단말 추가 주문 30대',
            conditions_state='unchanged', conditions_note='기준 계획의 대체 승인 조건 유지',
            relation='additional', origin='synthetic')
        self.provenance = dict(actor='user:author', registered_at='2026-11-03T10:00:00+09:00',
                               reference_data_version=1,store_id='a'*64)

    def test_valid_request_and_document_roundtrip(self):
        validate_request(self.payload, self.nodes)
        record = make_record(self.payload, self.provenance)
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as directory:
            path=Path(directory)/'request.md'; path.write_text(render_document(record))
            self.assertEqual(record, read_record(path))
            nodes=build_nodes(path, record)
            self.assertTrue(validate_graph(nodes)['valid'])
            self.assertEqual({'SourceDocument','Evidence'}, {n['type'] for n in nodes})
            self.assertEqual(record, json.loads(nodes[1]['description']))
            path.write_text(path.read_text().replace('30대', '300대', 1))
            with self.assertRaises(ValueError): read_record(path)

    def test_reject_bad_quantities_dates_unknown_and_identity_fields(self):
        for field,value in [('quantity',0),('quantity',True),('quantity',None),
                            ('need_date','2026-02-30'),('actor','fake'),('revision',True),
                            ('configuration_id','missing'),('baseline_evidence_id','missing')]:
            with self.subTest(field=field,value=value):
                p=copy.deepcopy(self.payload);p[field]=value
                with self.assertRaises(ValueError): validate_request(p,self.nodes)

    def test_customer_arrival_requires_destination(self):
        self.payload['need_basis']='customer_arrival'
        with self.assertRaises(ValueError): validate_request(self.payload,self.nodes)
        self.payload['destination']='한빛물류시스템 평택 물류센터'
        validate_request(self.payload,self.nodes)

    def test_revision_requires_previous_same_request(self):
        old=make_record(self.payload,self.provenance)
        self.nodes.append(dict(id='evidence:previous',type='Evidence',origin='synthetic',description=json.dumps(old)))
        self.payload.update(revision=2, quantity=40, relation='revision',previous_evidence_id='evidence:previous')
        validate_request(self.payload,self.nodes)
        self.payload['request_id']='different'
        with self.assertRaises(ValueError): validate_request(self.payload,self.nodes)

    def test_contract_reference_must_match_configuration(self):
        self.nodes.append(dict(id='item:1',type='ContractItem',configuration_id='different'))
        self.payload['contract_item_id']='item:1'
        with self.assertRaises(ValueError): validate_request(self.payload,self.nodes)

    def test_malformed_unrelated_evidence_does_not_block_request(self):
        record=make_record(self.payload,self.provenance)
        record['request']={}
        self.nodes.append(dict(id='evidence:other',type='Evidence',description=json.dumps(record)))
        validate_request(self.payload,self.nodes)

    def test_real_request_cannot_relabel_synthetic_plan(self):
        self.payload['origin']='real'
        with self.assertRaises(ValueError): validate_request(self.payload,self.nodes)
