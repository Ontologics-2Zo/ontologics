"""Conditional production completion for one fully allocated synthetic work.

Common Python owns arithmetic, snapshot scope and unknowns. No estimated counts,
completion events, state transitions, arrivals or approvals are persisted here.
"""
from __future__ import annotations

from datetime import timedelta
from fractions import Fraction
import hashlib
import json

from .fulfillment_validation import _nonnegative_integer
from ..model import ROOT, _positive_integer, instant, validate_graph

FORECAST_RULE_VERSION = 'production-0.1'
WORK = 'work:demo:CQ03:main'
ROUND = 'delivery-round:demo:B-01:1'
SNAPSHOT = 'snapshot:demo:CQ03:10'
AS_OF = '2026-10-27T10:00:00+09:00'
SOURCE_PATH = 'data/order_fulfillment/sources/b_demo_production_01.md'
SOURCE_SHA256 = '6b18f76917db81837a4a06891d9a8331b412c19d1d62f46d0e3a681bc442c810'
REQUIRED_CONFIRMATIONS = ('allocations_complete', 'source_allocations_complete',
    'allocations_disjoint', 'production_state_complete', 'material_disjoint')
COUNTERS = ('produced_quantity', 'good_quantity', 'defective_quantity')


def load_demo_production():
    """Read pinned shared development candidates, without registering/approving."""
    fixture = json.loads((ROOT / 'data/order_fulfillment/b_production_fixture.json').read_text())
    if (fixture['source_path'] != SOURCE_PATH or fixture['source_sha256'] != SOURCE_SHA256
            or hashlib.sha256((ROOT / SOURCE_PATH).read_bytes()).hexdigest() != SOURCE_SHA256):
        raise ValueError('B-CQ03 source bytes differ; new source/evidence IDs are required')
    source = next(n for n in fixture['nodes'] if n['id'] == fixture['source_id'])
    if source['path'] != SOURCE_PATH or source['sha256'] != SOURCE_SHA256:
        raise ValueError('B-CQ03 SourceDocument metadata differs from the pinned source')
    return fixture['nodes']


def calculate_completion(previous, current, target_quantity):
    """Exact interval arithmetic, with final positive duration rounded up once.

    Both counters are cumulative. Returned float rates are display values only;
    duration uses Fraction throughout and is independent of those display rates.
    This pure function does not validate graph references or decide round scope.
    """
    if not _positive_integer(target_quantity):
        raise ValueError('A positive integer good-unit target is required')
    for observation in (previous, current):
        if not isinstance(observation, dict) or not all(
                _nonnegative_integer(observation.get(k)) for k in COUNTERS):
            raise ValueError('Nonnegative integer cumulative counters are required')
        if observation['produced_quantity'] != observation['good_quantity'] + observation['defective_quantity']:
            raise ValueError('Produced quantity must equal good plus defective quantity')
        if observation.get('unit') != 'piece':
            raise ValueError('Only piece observations are supported')
    work_ids = [observation.get('production_work_id') for observation in (previous, current)]
    if (not all(isinstance(value, str) and value.strip() for value in work_ids)
            or work_ids[0] != work_ids[1]):
        raise ValueError('Both observations must refer to the same explicit work ID')
    before, latest = instant(previous.get('observed_at')), instant(current.get('observed_at'))
    elapsed = Fraction((latest - before) // timedelta(microseconds=1), 1000000)
    if elapsed <= 0:
        raise ValueError('The previous observation must strictly precede the current one')
    deltas = {k: current[k] - previous[k] for k in COUNTERS}
    if any(value < 0 for value in deltas.values()):
        raise ValueError('Cumulative production counters cannot decrease')
    if current['good_quantity'] > target_quantity:
        raise ValueError('Good quantity cannot exceed the current work target contract')
    good_rate = Fraction(deltas['good_quantity'] * 3600, 1) / elapsed
    produced_rate = Fraction(deltas['produced_quantity'] * 3600, 1) / elapsed
    defect_ratio = (Fraction(deltas['defective_quantity'], deltas['produced_quantity'])
                    if deltas['produced_quantity'] else None)
    remaining = target_quantity - current['good_quantity']
    seconds, completion = None, None
    try:
        if not remaining:
            seconds = 0
        elif good_rate:
            exact_seconds = Fraction(remaining * 3600, 1) / good_rate
            seconds = -(-exact_seconds.numerator // exact_seconds.denominator)
            completion = (latest + timedelta(seconds=seconds)).isoformat()
        return dict(interval_seconds=float(elapsed), produced_increment=deltas['produced_quantity'],
            good_increment=deltas['good_quantity'], defective_increment=deltas['defective_quantity'],
            good_rate_per_hour=float(good_rate), produced_rate_per_hour=float(produced_rate),
            recent_defect_ratio=float(defect_ratio) if defect_ratio is not None else None,
            good_rate_fraction=dict(numerator=good_rate.numerator, denominator=good_rate.denominator),
            remaining_quantity=remaining, remaining_seconds=seconds, estimated_completion_at=completion)
    except OverflowError as exc:
        raise ValueError('Forecast exceeds the supported numeric or datetime range') from exc


def production_completion(nodes, production_work_id, delivery_round_id, as_of, *, queried_at):
    """Read one explicit instant of one confirmed graph; never silently use latest.

    Existing valid split allocations/stale work remain valid stored facts but are
    outside this query's first format. Missing observations/coverage stay unknown;
    contradictory graph facts fail before interval selection.
    """
    requested = instant(as_of)
    instant(queried_at)
    for value in (production_work_id, delivery_round_id):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Explicit nonempty work and delivery round IDs are required')
    report = validate_graph(nodes)
    if not report['valid']:
        raise ValueError(report)
    index = {n['id']: n for n in nodes}
    work, delivery = index.get(production_work_id), index.get(delivery_round_id)
    if not work or work['type'] != 'ProductionWork':
        raise KeyError('Unknown production work')
    if not delivery or delivery['type'] != 'DeliveryRound':
        raise KeyError('Unknown delivery round')
    item = index[delivery['contract_item_id']]
    contract = index[item['contract_id']]
    config = index[item['configuration_id']]
    revision = index[config['revision_id']]
    product = index[revision['model_id']]
    snapshot = index[work['snapshot_id']]
    evidence = {e for n in (work, delivery, item, contract, config, revision, product, snapshot)
                for e in n['evidence']}
    result = dict(status='needs_confirmation', forecast_rule_version=FORECAST_RULE_VERSION,
        production_work_id=production_work_id, delivery_round_id=delivery_round_id,
        snapshot_id=snapshot['id'], as_of=as_of, queried_at=queried_at,
        available_as_of=[snapshot['as_of']], configuration_id=config['id'], unit=work['unit'],
        target_quantity=work['target_quantity'], work_completed_quantity=work['completed_quantity'],
        work_observed_at=work['observed_at'], latest_good_quantity=None, remaining_quantity=None,
        observation_as_of=None, selected_observation_ids=[], observations=[],
        interval_seconds=None, produced_increment=None, good_increment=None, defective_increment=None,
        good_rate_per_hour=None, produced_rate_per_hour=None, good_rate_fraction=None,
        recent_defect_ratio=None, remaining_seconds=None, estimated_completion_at=None,
        target_observed_at=None, actual_completion_at=None, synthetic=True,
        fulfillment_assessed=False, work=work, snapshot=snapshot, allocations=[],
        promise=dict(contract=contract, contract_item=item, delivery_round=delivery,
                     configuration=config, revision=revision, product=product),
        assumptions=[dict(code='RECENT_TREND_CONTINUES', kind='calculation_condition',
            detail='Conditional estimate assumes continued recent interval production without interruption.',
            evidence=list(work['evidence'])),
            dict(code='GOOD_UNITS_USABLE', kind='calculation_condition',
            detail='Conditional estimate assumes good units are usable for this fully allocated round.',
            evidence=list(work['evidence']))],
        evidence=[], unknowns=[], warnings=[dict(code='CONDITIONAL_PRODUCTION_ONLY',
            detail='No actual first completion, operating time, dispatch, customer arrival or delivery feasibility is established.')])

    def unknown(node_id, field, code):
        result['unknowns'].append(dict(node_id=node_id, field=field, code=code))

    def finish():
        result['evidence'] = sorted(evidence)
        return result

    if instant(snapshot['as_of']) != requested:
        unknown(snapshot['id'], 'as_of', 'SNAPSHOT_NOT_CONFIRMED')
        return finish()
    result['as_of'] = snapshot['as_of']
    if instant(work['observed_at']) != requested:
        unknown(work['id'], 'observed_at', 'WORK_STATE_NOT_CURRENT')
    if delivery_round_id not in snapshot['round_ids']:
        unknown(delivery_round_id, 'snapshot', 'ROUND_NOT_IN_WORK_SNAPSHOT')
    if (work['company'] != contract['company'] or work['configuration_id'] != config['id']
            or work['unit'] != delivery['unit']):
        unknown(work['id'], 'delivery_round_id', 'WORK_ROUND_SCOPE_MISMATCH')
    for key in REQUIRED_CONFIRMATIONS:
        if snapshot['confirmations'].get(key) is not True:
            unknown(snapshot['id'], key, 'COVERAGE_NOT_CONFIRMED')
    allocations = [n for n in nodes if n['type'] == 'Allocation' and n['snapshot_id'] == snapshot['id']]
    work_allocations = [n for n in allocations if n.get('production_work_id') == production_work_id]
    round_allocations = [n for n in allocations if n['delivery_round_id'] == delivery_round_id]
    result['allocations'] = sorted({n['id']: n for n in work_allocations + round_allocations}.values(), key=lambda n: n['id'])
    evidence.update(e for n in result['allocations'] for e in n['evidence'])
    full_work = (bool(work_allocations)
        and all(n['delivery_round_id'] == delivery_round_id for n in work_allocations)
        and all(n.get('production_work_id') == production_work_id for n in round_allocations)
        and sum(n['quantity'] for n in work_allocations) == work['target_quantity'] == delivery['quantity'])
    if not full_work:
        unknown(work['id'], 'allocations', 'SINGLE_WORK_FULL_ALLOCATION_REQUIRED')
    if work['production_state'] != 'in_progress':
        unknown(work['id'], 'production_state', 'WORK_NOT_STARTED')
    if result['unknowns']:
        return finish()
    observations = sorted((n for n in nodes if n['type'] == 'ProductionObservation'
                           and n['production_work_id'] == production_work_id), key=lambda n: instant(n['observed_at']))
    result['available_observation_ids'] = [n['id'] for n in observations]
    if not observations or instant(observations[-1]['observed_at']) != requested:
        unknown(work['id'], 'observations', 'LATEST_OBSERVATION_REQUIRED')
        return finish()
    latest = observations[-1]
    selected = observations[-2:]
    result.update(latest_good_quantity=latest['good_quantity'],
        remaining_quantity=work['target_quantity'] - latest['good_quantity'],
        observation_as_of=latest['observed_at'], observations=selected,
        selected_observation_ids=[n['id'] for n in selected])
    evidence.update(e for n in selected for e in n['evidence'])
    if len(selected) == 2:
        result.update(calculate_completion(selected[0], selected[1], work['target_quantity']))
    if result['remaining_quantity'] == 0:
        result.update(status='target_observed', remaining_seconds=0, target_observed_at=latest['observed_at'])
        return finish()
    if len(selected) != 2:
        unknown(work['id'], 'observations', 'PREVIOUS_OBSERVATION_REQUIRED')
    elif not result['good_rate_per_hour']:
        unknown(work['id'], 'good_rate_per_hour', 'ZERO_GOOD_PRODUCTION_RATE')
    else:
        result['status'] = 'forecast'
    return finish()
