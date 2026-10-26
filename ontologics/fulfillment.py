"""One source-backed fulfillment snapshot, without production/arrival forecasting.

The caller supplies a confirmed graph and an exact observation instant. Empty
searches never establish zero inventory; source-backed coverage is required.
"""
from __future__ import annotations

import hashlib
import json
from .model import ROOT, instant, validate_graph

SNAPSHOT = 'snapshot:demo:CQ02:10'
ROUND = 'delivery-round:demo:B-01:1'
OTHER_ROUND = 'delivery-round:demo:B-CQ02-other:1'
AS_OF = '2026-10-27T10:00:00+09:00'
SOURCE_PATH = 'data/sources/b_demo_fulfillment_01.md'
SOURCE_SHA256 = '2a74f77ce99d599227e97d8e85b681d54cfddc7e7076044110908954132f0622'
STATE_KEYS = ('delivered', 'in_transit', 'warehouse', 'in_progress', 'ready', 'waiting')


def load_demo_fulfillment():
    """Load reviewed-shape development candidates; never register or approve them."""
    fixture = json.loads((ROOT / 'data/b_fulfillment_fixture.json').read_text())
    if (fixture['source_path'] != SOURCE_PATH or fixture['source_sha256'] != SOURCE_SHA256
            or hashlib.sha256((ROOT / SOURCE_PATH).read_bytes()).hexdigest() != SOURCE_SHA256):
        raise ValueError('B-CQ02 source bytes differ; new source/evidence IDs are required')
    source = next(n for n in fixture['nodes'] if n['id'] == fixture['source_id'])
    if source['path'] != SOURCE_PATH or source['sha256'] != SOURCE_SHA256:
        raise ValueError('B-CQ02 SourceDocument metadata differs from the pinned source')
    return fixture['nodes']


def fulfillment_status(nodes, delivery_round_id, as_of, *, queried_at):
    """Compute one exact snapshot. Malformed facts fail; incomplete facts stay unknown.

    `complete` means complete evidence for this snapshot's supported format, not
    completed delivery or feasible future fulfillment. Read-only; no current time
    or latest version is silently substituted.
    """
    from .fulfillment_validation import CONFIRMATION_KEYS
    requested = instant(as_of)
    instant(queried_at)
    report = validate_graph(nodes)
    if not report['valid']:
        raise ValueError(report)
    index = {n['id']: n for n in nodes}
    delivery = index.get(delivery_round_id)
    if not delivery or delivery['type'] != 'DeliveryRound':
        raise KeyError('Unknown delivery round')
    item = index[delivery['contract_item_id']]
    contract = index[item['contract_id']]
    config = index[item['configuration_id']]
    revision = index[config['revision_id']]
    product = index[revision['model_id']]
    evidence = set(e for n in (delivery, item, contract, config, revision, product) for e in n['evidence'])
    result = dict(status='needs_confirmation', delivery_round_id=delivery_round_id,
        snapshot_id=None, as_of=as_of, queried_at=queried_at,
        quantity=delivery['quantity'], unit=delivery['unit'], configuration_id=config['id'],
        promise=dict(contract=contract, contract_item=item, delivery_round=delivery,
                     configuration=config, revision=revision, product=product),
        states=None, allocated_quantity=None, remaining_quantity=None, unallocated_quantity=None,
        fulfillment_assessed=False, items=[], stocks=[], evidence=[], unknowns=[], warnings=[])

    def unknown(node_id, field, code):
        result['unknowns'].append(dict(node_id=node_id, field=field, code=code))

    snapshots = [n for n in nodes if n['type'] == 'FulfillmentSnapshot'
                 and delivery_round_id in n['round_ids'] and instant(n['as_of']) == requested]
    if len(snapshots) != 1:
        unknown(delivery_round_id, 'snapshot', 'SNAPSHOT_NOT_CONFIRMED' if not snapshots else 'AMBIGUOUS_SNAPSHOT')
        result['available_as_of'] = sorted({n['as_of'] for n in nodes if n['type'] == 'FulfillmentSnapshot'
                                          and delivery_round_id in n['round_ids']})
        result['evidence'] = sorted(evidence)
        return result
    snapshot = snapshots[0]
    sid = snapshot['id']
    result.update(snapshot_id=sid, as_of=snapshot['as_of'], snapshot=snapshot)
    evidence.update(snapshot['evidence'])
    for key in sorted(CONFIRMATION_KEYS):
        if snapshot['confirmations'].get(key) is not True:
            unknown(sid, key, 'COVERAGE_NOT_CONFIRMED')
    scoped = [n for n in nodes if n.get('snapshot_id') == sid]
    # Check full source scope before filtering the target round. The graph's
    # cross-node validator enforces caps against all rounds in this snapshot.
    for n in scoped:
        evidence.update(n['evidence'])
        if n['type'] == 'ProductionWork' and n['completed_quantity'] != 0:
            unknown(n['id'], 'completed_quantity', 'PARTIAL_PRODUCTION_UNSUPPORTED')
    shipments = {n['allocation_id']: n for n in scoped if n['type'] == 'Shipment'}
    all_allocations = {n['id']: n for n in scoped if n['type'] == 'Allocation'}
    departed_by_stock = {}
    for shipment in shipments.values():
        if 'departed_at' in shipment:
            stock_id = all_allocations[shipment['allocation_id']]['stock_id']
            departed_by_stock[stock_id] = departed_by_stock.get(stock_id, 0) + shipment['quantity']
    stock_known = all(snapshot['confirmations'].get(k) is True for k in
                      ('stock_events_complete', 'stock_observations_before_departures'))
    for stock in sorted((n for n in scoped if n['type'] == 'FinishedGoodsStock'), key=lambda n: n['id']):
        result['stocks'].append(dict(stock_id=stock['id'], observed_at=stock['observed_at'],
            observed_quantity=stock['quantity'], unit=stock['unit'], location=stock['location'],
            departed_quantity=departed_by_stock.get(stock['id'], 0),
            remaining_quantity_as_of=(stock['quantity'] - departed_by_stock.get(stock['id'], 0)) if stock_known else None,
            evidence=stock['evidence']))
    states = dict.fromkeys(STATE_KEYS, 0)
    for allocation in sorted(all_allocations.values(), key=lambda n: n['id']):
        if allocation['delivery_round_id'] != delivery_round_id:
            continue
        shipment = shipments.get(allocation['id'])
        source = index[allocation.get('stock_id', allocation.get('production_work_id'))]
        if source['type'] == 'ProductionWork':
            state = source['production_state'] if source['completed_quantity'] == 0 else None
        elif shipment and 'arrived_at' in shipment:
            state = 'delivered'
        elif shipment and 'departed_at' in shipment:
            state = 'in_transit'
        else:
            state = 'warehouse'
        if (shipment and state == 'in_transit' and shipment.get('expected_arrival_at')
                and instant(shipment['expected_arrival_at']) < requested):
            result['warnings'].append(dict(code='EXPECTED_ARRIVAL_ELAPSED', node_id=shipment['id'],
                detail='Expected time elapsed; actual arrival is not confirmed'))
        result['items'].append(dict(allocation=allocation, source=source, shipment=shipment,
            state=state, classification_confirmed=not result['unknowns'], quantity=allocation['quantity'],
            unit=allocation['unit'], observed_at=source['observed_at'],
            evidence=sorted(set(allocation['evidence'] + source['evidence'] + (shipment['evidence'] if shipment else [])))))
        if state is not None:
            states[state] += allocation['quantity']
    if not result['unknowns']:
        allocated = sum(states.values())
        result.update(status='complete', states=states, allocated_quantity=allocated,
            remaining_quantity=delivery['quantity'] - states['delivered'],
            unallocated_quantity=delivery['quantity'] - allocated)
    result['evidence'] = sorted(evidence)
    return result
