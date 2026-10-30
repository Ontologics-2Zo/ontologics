"""Explicit terminal example input, reusing B task builders and common checks.

One UTF-8 Markdown source contains the existing contract table and one JSON
operational record. These are synthetic, unregistered inputs, never DB facts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .b_run_isolation import ROOT, checked_path
from . import b_task_workflow as workflow
from ontologics.order_fulfillment.contracts import extract_contract
from ontologics.order_fulfillment.schema_validation import validate_input_graph


CASES = ('cq01_intake', 'cq01_promise', 'cq02_normal', 'cq03_normal',
         'cq04_mixed', 'cq05_mixed', 'cq06_transport2h')


def load_input(path):
    path = checked_path(Path(path))
    if (not path.is_relative_to(ROOT / 'data/order_fulfillment')
            or path.suffix != '.md' or path.stat().st_size > 100000):
        raise ValueError('Use a bounded Markdown input under data/order_fulfillment/')
    raw = path.read_bytes()
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(raw).hexdigest(),
                text=raw.decode('utf-8'))


def validate_input(value):
    if (not isinstance(value, dict) or set(value) != {'path', 'sha256', 'text'}
            or not isinstance(value['path'], str)
            or load_input(ROOT / value['path']) != value):
        raise ValueError('The explicit terminal source changed')
    matches = re.findall(r'^```json\s*\n(.*?)^```\s*$', value['text'], re.M | re.S)
    if len(matches) != 1:
        raise ValueError('Exactly one operational JSON block is required')
    data = json.loads(matches[0])
    if not isinstance(data, dict) or set(data) != {
            'as_of', 'queried_at', 'confirmations', 'warehouse', 'production',
            'delivered', 'in_transit', 'condition', 'comparison_transport_seconds'}:
        raise ValueError('Explicit contract and operational input fields required')
    return data


def _nodes(value, data):
    """Map explicit observations, without inventing events or coverage claims."""
    catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
    prefix = 'terminal:' + value['sha256'][:16]
    source_id = 'source:' + prefix
    eid = lambda part: 'evidence:' + prefix + ':' + part
    source = dict(id=source_id, type='SourceDocument', origin='synthetic', evidence=[],
        url='urn:ontologics:synthetic:' + prefix, path=value['path'], sha256=value['sha256'],
        version=value['sha256'], license='Project synthetic example; not an actual business record.',
        collected_at=data['queried_at'])
    references = [dict(id=eid(part), type='Evidence', origin='synthetic', evidence=[],
        source_id=source_id, locator=locator, description='Explicit synthetic terminal input')
        for part, locator in (('contract', '계약 표: 계약 번호·우리 회사 식별·고객사'),
                             ('item', '계약 표: 제품 모델·설계 개정·구성·계약 수량'),
                             ('round', '계약 표: 회차 약속 수량·납품 기한·납품 장소'),
                             ('operations', '운영 현황 JSON: observations and explicit confirmations'),
                             ('comparison', '운영 현황 JSON: comparison_transport_seconds'))]
    evidence = {part: eid(part) for part in ('contract', 'item', 'round')}
    extracted = extract_contract(value['text'], catalog, evidence_ids=evidence)
    if extracted['status'] != 'ready':
        raise ValueError('Correct the example contract table before running the CQ sequence')
    contract, item, delivery = extracted['nodes']
    nodes = catalog + [source] + references + extracted['nodes']
    snapshot_id, work_id = 'snapshot:' + prefix, 'work:' + prefix

    def add(kind, identity, part='operations', **fields):
        node = dict(id=identity, type=kind, origin='synthetic', evidence=[eid(part)], **fields)
        nodes.append(node)
        return node

    add('FulfillmentSnapshot', snapshot_id, company=contract['company'], as_of=data['as_of'],
        round_ids=[delivery['id']], confirmations=deepcopy(data['confirmations']))
    production = data['production']
    add('ProductionWork', work_id, snapshot_id=snapshot_id, company=contract['company'],
        configuration_id=item['configuration_id'], observed_at=production['observed_at'],
        target_quantity=production['target_quantity'], completed_quantity=production['completed_quantity'],
        production_state=production['production_state'], unit='piece',
        **{key: production[key] for key in ('started_at', 'planned_start_at') if key in production})
    for index, observation in enumerate(production['observations']):
        add('ProductionObservation', 'observation:' + prefix + ':' + str(index),
            production_work_id=work_id, **observation)
    add('Allocation', 'allocation:' + prefix + ':production', snapshot_id=snapshot_id,
        delivery_round_id=delivery['id'], production_work_id=work_id,
        quantity=production['target_quantity'], unit='piece')
    for part in ('delivered', 'in_transit', 'warehouse'):
        record = data[part]
        if type(record['quantity']) is int and record['quantity'] == 0:
            if any(key in record for key in ('departed_at', 'arrived_at', 'expected_arrival_at')):
                raise ValueError('Zero quantity cannot carry a shipment event')
            continue
        stock_id, allocation_id = 'stock:' + prefix + ':' + part, 'allocation:' + prefix + ':' + part
        add('FinishedGoodsStock', stock_id, snapshot_id=snapshot_id, company=contract['company'],
            configuration_id=item['configuration_id'], observed_at=record['stock_observed_at'],
            location=record['location'], quantity=record['quantity'], unit='piece')
        add('Allocation', allocation_id, snapshot_id=snapshot_id,
            delivery_round_id=delivery['id'], stock_id=stock_id, quantity=record['quantity'], unit='piece')
        if part != 'warehouse':
            times = {key: record[key] for key in ('departed_at', 'arrived_at', 'expected_arrival_at')
                     if key in record}
            add('Shipment', 'shipment:' + prefix + ':' + part, snapshot_id=snapshot_id,
                allocation_id=allocation_id, quantity=record['quantity'], unit='piece',
                destination=delivery['destination'], **times)
    add('DeliveryCondition', 'condition:' + prefix, snapshot_id=snapshot_id,
        delivery_round_id=delivery['id'], **data['condition'])
    report = validate_input_graph(nodes)
    if not report['valid']:
        raise ValueError(report)
    return catalog, [source] + references[:3], evidence, nodes, delivery['id'], work_id


def case_task(case, value, *, version=1):
    if case not in CASES:
        raise ValueError('Select a supported terminal-input CQ role')
    data = validate_input(value)
    catalog, sources, evidence, nodes, round_id, work_id = _nodes(value, data)
    role = workflow.ROLES[CASES.index(case)]
    module = role[0]
    revision = 'terminal-' + value['sha256']
    options = dict(input_revision=revision, data_version=version)
    timing = dict(queried_at=data['queried_at'], **options)
    if case == 'cq01_intake':
        task = module.make_intake_task(catalog, sources, value['text'], evidence_ids=evidence, **options)
    elif case == 'cq01_promise':
        task = module.make_promise_task(nodes, round_id, **options)
    elif case == 'cq03_normal':
        task = module.make_production_task(nodes, work_id, round_id, data['as_of'], **timing)
    elif case == 'cq06_transport2h':
        after = deepcopy(nodes)
        condition = next(node for node in after if node['type'] == 'DeliveryCondition')
        condition.update(transport_seconds=data['comparison_transport_seconds'],
                         evidence=['evidence:terminal:' + value['sha256'][:16] + ':comparison'])
        task = module.make_comparison_task(nodes, after, round_id, data['as_of'], data['as_of'],
            before_revision=revision, after_revision=revision + '-transport',
            queried_at=data['queried_at'], data_version=version)
    else:
        name = {'cq02_normal': 'make_fulfillment_task',
                'cq04_mixed': 'make_delivery_task', 'cq05_mixed': 'make_response_task'}[case]
        builder = getattr(module, name)
        task = builder(nodes, round_id, data['as_of'], **timing)
    return role, task
