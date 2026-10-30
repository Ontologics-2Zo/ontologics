"""Conditional customer-arrival arithmetic over one explicit source-backed input.

No prediction is written as a shipment, production completion or actual arrival.
Real records and synthetic calculations retain their distinct meanings. The
common Service supplies approval/identity/version when used through storage;
the pure input calculation itself grants none of those authorities.
"""
from __future__ import annotations

from datetime import timedelta
from fractions import Fraction

from .fulfillment_validation import CONFIRMATION_KEYS
from ..model import instant
from .schema_validation import validate_input_graph
from .production import calculate_completion

FORECAST_RULE_VERSION = 'delivery-0.1'


def _seconds(delta):
    return Fraction(delta // timedelta(microseconds=1), 1000000)


def _ceil(value):
    return -(-value.numerator // value.denominator)


def delivery_forecast(nodes, delivery_round_id, as_of, *, queried_at):
    """Evaluate the fixed allocation plan, never silently selecting latest facts.

    Exact rational rates, whole elapsed readiness seconds rounded up, and whole
    available seconds rounded down use one conservative precision policy. Counts
    are integral pieces. Missing coverage or unknown future rates null totals;
    completely confirmed unallocated quantities are a known plan shortfall.
    """
    requested = instant(as_of)
    instant(queried_at)
    if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
        raise ValueError('An explicit nonempty delivery round ID is required')
    report = validate_input_graph(nodes)
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
    due = instant(delivery['due_at'])
    quantity = delivery['quantity']
    evidence = {e for n in (delivery, item, contract, config, revision, product) for e in n['evidence']}
    result = dict(status='needs_confirmation', deadline_status='needs_confirmation',
        forecast_rule_version=FORECAST_RULE_VERSION, assessment_scope='conditional_arrival_forecast',
        delivery_round_id=delivery_round_id, snapshot_id=None, as_of=as_of, queried_at=queried_at,
        company=contract['company'], configuration_id=config['id'], unit=delivery['unit'],
        quantity=quantity, due_at=delivery['due_at'], due_basis=delivery['due_basis'],
        destination=delivery['destination'], allocated_quantity=None, unallocated_quantity=None,
        on_time_quantity=None, shortfall_quantity=None, estimated_full_arrival_at=None,
        synthetic=True, actual_arrival_assessed=False,
        promise=dict(contract=contract, contract_item=item, delivery_round=delivery,
                     configuration=config, revision=revision, product=product),
        condition=None, allocations=[], breakdown=[], evidence=[], unknowns=[], assumptions=[],
        precision_policy='exact_rate_ceil_ready_seconds_floor_horizon_seconds_floor_pieces',
        warnings=[dict(code='CONDITIONAL_ARRIVAL_FORECAST',
            detail='A fixed-plan conditional estimate is not an actual shipment, arrival or delivery guarantee.')])

    def unknown(nid, field, code):
        result['unknowns'].append(dict(node_id=nid, field=field, code=code))

    def cite(*entries):
        for node in entries:
            if node:
                evidence.update(node['evidence'])

    def finish():
        result['evidence'] = sorted(evidence)
        return result

    snapshots = [n for n in nodes if n['type'] == 'FulfillmentSnapshot'
                 and delivery_round_id in n['round_ids'] and instant(n['as_of']) == requested]
    result['available_as_of'] = sorted({n['as_of'] for n in nodes if n['type'] == 'FulfillmentSnapshot'
                                        and delivery_round_id in n['round_ids']})
    if len(snapshots) != 1:
        unknown(delivery_round_id, 'snapshot', 'SNAPSHOT_NOT_CONFIRMED' if not snapshots else 'AMBIGUOUS_SNAPSHOT')
        return finish()
    snapshot = snapshots[0]
    sid = snapshot['id']
    result.update(snapshot_id=sid, as_of=snapshot['as_of'], snapshot=snapshot)
    requested = instant(snapshot['as_of'])  # Stable display offset from the confirmed observation.
    cite(snapshot)
    for key in sorted(CONFIRMATION_KEYS):
        if snapshot['confirmations'].get(key) is not True:
            unknown(sid, key, 'COVERAGE_NOT_CONFIRMED')
    scoped = [n for n in nodes if n.get('snapshot_id') == sid]
    conditions = [n for n in scoped if n['type'] == 'DeliveryCondition'
                  and n['delivery_round_id'] == delivery_round_id]
    if len(conditions) != 1:
        unknown(delivery_round_id, 'condition', 'DELIVERY_CONDITION_REQUIRED')
    else:
        result['condition'] = conditions[0]
        cite(conditions[0])
    if result['unknowns']:
        return finish()
    condition = conditions[0]
    delay = condition['preparation_seconds'] + condition['transport_seconds']
    try:
        cutoff = due - timedelta(seconds=delay)
        immediate_arrival = requested + timedelta(seconds=delay)
    except OverflowError as exc:
        raise ValueError('Dispatch duration exceeds the supported datetime range') from exc
    # All-round source allocation evidence supports caps and absence of duplicate
    # allocation; only the requested round's pieces enter the numeric result.
    all_allocations = [n for n in scoped if n['type'] == 'Allocation']
    for allocation in all_allocations:
        cite(allocation, index[allocation.get('stock_id', allocation.get('production_work_id'))])
    allocations = sorted((a for a in all_allocations if a['delivery_round_id'] == delivery_round_id),
                         key=lambda a: a['id'])
    shipments = {n['allocation_id']: n for n in scoped if n['type'] == 'Shipment'}
    result['allocations'] = allocations
    allocated = sum(a['quantity'] for a in allocations)
    result.update(allocated_quantity=allocated, unallocated_quantity=quantity-allocated)
    for code, text in (
        ('FIXED_DISPATCH_DURATION', 'Preparation and transit durations apply separately to each unsent piece.'),
        ('UNLIMITED_DISPATCH_CAPACITY', 'Available pieces may be prepared and dispatched without batch, vehicle or calendar constraints.')):
        result['assumptions'].append(dict(code=code, kind='calculation_condition', detail=text,
                                          evidence=condition['evidence']))
    work_ids = {a['production_work_id'] for a in allocations if 'production_work_id' in a}
    if len(work_ids) > 1:
        unknown(sid, 'production_work_id', 'MULTIPLE_PRODUCTION_WORKS_UNSUPPORTED')
    mode = condition['dispatch_mode']
    if mode == 'all_at_once' and (allocated != quantity or any(
            shipments.get(a['id'], {}).get('departed_at') for a in allocations)):
        unknown(condition['id'], 'dispatch_mode', 'ALL_AT_ONCE_FULL_UNSENT_ROUND_REQUIRED')
    if result['unknowns']:
        return finish()

    historical = due < requested
    work_forecasts = {}
    for wid in sorted(work_ids):
        work = index[wid]
        attached = [a for a in all_allocations if a.get('production_work_id') == wid]
        if (sum(a['quantity'] for a in attached) != work['target_quantity']
                or any(a['delivery_round_id'] != delivery_round_id for a in attached)):
            unknown(wid, 'allocations', 'WORK_FULL_ALLOCATION_REQUIRED')
            work_forecasts[wid] = None
            continue
        if historical:
            # Complete event history alone decides a past deadline; present
            # production counters must never be back-projected into that past.
            work_forecasts[wid] = None
            continue
        if instant(work['observed_at']) != requested:
            unknown(wid, 'observed_at', 'WORK_STATE_NOT_CURRENT')
            work_forecasts[wid] = None
            continue
        if work['production_state'] != 'in_progress':
            unknown(wid, 'production_state', 'WORK_NOT_STARTED')
            work_forecasts[wid] = None
            continue
        observations = sorted((n for n in nodes if n['type'] == 'ProductionObservation'
                               and n['production_work_id'] == wid), key=lambda n: instant(n['observed_at']))
        if not observations or instant(observations[-1]['observed_at']) != requested:
            unknown(wid, 'observations', 'LATEST_OBSERVATION_REQUIRED')
            work_forecasts[wid] = None
            continue
        selected = observations[-2:]
        latest = selected[-1]
        cite(*selected)
        calculated = calculate_completion(selected[0], latest, work['target_quantity']) if len(selected) == 2 else None
        rate = (Fraction(calculated['good_rate_fraction']['numerator'],
                         calculated['good_rate_fraction']['denominator']) / 3600) if calculated else None
        remaining = work['target_quantity'] - latest['good_quantity']
        if remaining and (rate is None or not rate):
            unknown(wid, 'observations', 'PREVIOUS_OBSERVATION_REQUIRED' if rate is None else 'ZERO_GOOD_PRODUCTION_RATE')
        work_forecasts[wid] = dict(work=work, latest=latest, selected=selected,
                                   rate=rate, calculated=calculated)
        for code, text in (
            ('RECENT_TREND_CONTINUES', 'Future good output assumes continued latest-interval production without interruption.'),
            ('GOOD_UNITS_USABLE', 'Observed and forecast good units are usable for this full-work allocation.')):
            result['assumptions'].append(dict(code=code, kind='calculation_condition', detail=text,
                                              evidence=work['evidence']))

    # Keep per-allocation quantities disjoint even when the same full work has
    # several allocations to this round. The cumulative prefix partitions both
    # current completed pieces and future output in stable ID order.
    work_prefix = {}
    for allocation in allocations:
        aid, q = allocation['id'], allocation['quantity']
        source = index[allocation.get('stock_id', allocation.get('production_work_id'))]
        shipment = shipments.get(aid)
        cite(shipment)
        row = dict(allocation_id=aid, source_id=source['id'], quantity=q, unit='piece',
                   source_state=None, on_time_quantity=None, known_available_quantity=None,
                   estimated_arrival_at=None, actual_arrival_at=None,
                   evidence=sorted(set(allocation['evidence'] + source['evidence'] +
                                       (shipment['evidence'] if shipment else []))))
        if shipment and 'arrived_at' in shipment:
            arrival = instant(shipment['arrived_at'])
            row.update(source_state='delivered', on_time_quantity=q if arrival <= due else 0,
                       known_available_quantity=q, actual_arrival_at=shipment['arrived_at'],
                       estimated_arrival_at=shipment['arrived_at'])
        elif historical:
            row.update(source_state='not_delivered_by_deadline', on_time_quantity=0,
                       known_available_quantity=0)
        elif shipment and 'departed_at' in shipment:
            row['source_state'] = 'in_transit'
            eta = shipment.get('expected_arrival_at')
            if not eta:
                unknown(shipment['id'], 'expected_arrival_at', 'EXPECTED_ARRIVAL_REQUIRED')
            elif instant(eta) < requested or instant(eta) < instant(shipment['departed_at']):
                unknown(shipment['id'], 'expected_arrival_at', 'EXPECTED_ARRIVAL_ELAPSED')
            else:
                row.update(on_time_quantity=q if instant(eta) <= due else 0,
                           estimated_arrival_at=eta, known_available_quantity=q)
        elif source['type'] == 'FinishedGoodsStock':
            row.update(source_state='warehouse', on_time_quantity=q if immediate_arrival <= due else 0,
                       known_available_quantity=q, estimated_arrival_at=immediate_arrival.isoformat())
        else:
            row['source_state'] = source['production_state']
            forecast = work_forecasts[source['id']]
            prefix = work_prefix.get(source['id'], 0)
            work_prefix[source['id']] = prefix + q
            row.update(actual_completion_at=None, selected_observation_ids=[],
                       good_rate_per_hour=None, good_rate_fraction=None)
            if forecast:
                latest, rate = forecast['latest'], forecast['rate']
                good = latest['good_quantity']
                row.update(known_available_quantity=min(q, max(0, good-prefix)),
                    selected_observation_ids=[n['id'] for n in forecast['selected']],
                    evidence=sorted(set(row['evidence'] + [e for n in forecast['selected'] for e in n['evidence']])))
                if forecast['calculated']:
                    row.update(good_rate_per_hour=forecast['calculated']['good_rate_per_hour'],
                               good_rate_fraction=forecast['calculated']['good_rate_fraction'])
                if prefix + q <= good:
                    row.update(on_time_quantity=q if immediate_arrival <= due else 0,
                               estimated_arrival_at=immediate_arrival.isoformat())
                elif rate:
                    elapsed = max(0, _seconds(cutoff-requested).__floor__())
                    available = good + (rate * elapsed).__floor__() if cutoff >= requested else 0
                    ready_seconds = _ceil(Fraction(prefix + q - good, 1) / rate)
                    try:
                        arrival = requested + timedelta(seconds=ready_seconds + delay)
                    except OverflowError as exc:
                        raise ValueError('Forecast exceeds the supported datetime range') from exc
                    row.update(on_time_quantity=min(q, max(0, available-prefix)),
                               estimated_arrival_at=arrival.isoformat())
        result['breakdown'].append(row)

    # Coverage/scope/rate failures are kept explicit even when some rows are
    # computable. Their known portions must not be promoted into exact totals.
    if result['unknowns'] or any(r['on_time_quantity'] is None for r in result['breakdown']):
        return finish()
    on_time = sum(r['on_time_quantity'] for r in result['breakdown'])
    if mode == 'all_at_once' and not historical:
        arrivals = [instant(r['estimated_arrival_at']) for r in result['breakdown']]
        full = max(arrivals) if arrivals else None
        on_time = quantity if full and full <= due else 0
        for row in result['breakdown']:
            row.update(on_time_quantity=row['quantity'] if on_time else 0,
                       estimated_arrival_at=full.isoformat() if full else None)
    else:
        arrivals = [instant(r['estimated_arrival_at']) for r in result['breakdown']
                    if r['estimated_arrival_at'] is not None]
        full = max(arrivals) if len(arrivals) == len(allocations) and arrivals else None
    full_arrival = full.isoformat() if full is not None and allocated == quantity else None
    status = 'met' if on_time == quantity else 'shortfall'
    result.update(status=status, deadline_status=status, on_time_quantity=on_time,
                  shortfall_quantity=quantity-on_time, estimated_full_arrival_at=full_arrival,
                  actual_arrival_assessed=historical)
    return finish()
