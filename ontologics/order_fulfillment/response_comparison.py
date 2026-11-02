"""Read-only comparison of two explicitly bound, synthetic delivery inputs.

The existing production/delivery/response functions own all forecast arithmetic.
This module checks comparability and subtracts like-for-like derived values; it
does not approve facts, recover historical model output or execute a response.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from .. import __version__
from ..model import SCHEMA_VERSION, digest, instant
from .production import production_completion
from .response_plan import response_plan

COMPARISON_RULE_VERSION = 'comparison-0.1'
INPUT_COMPARISON_VERSION = 'input-comparison-0.1'
_DISPATCH_QUANTITIES = (
    'delivered_quantity', 'in_transit_quantity', 'new_dispatch_quantity',
    'on_time_new_dispatch_quantity', 'later_new_dispatch_quantity',
    'currently_available_quantity', 'future_quantity', 'unallocated_quantity',
)


class ComparisonError(ValueError):
    """A well-formed request outside the explicit comparison contract."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def _business(node, *, omit=()):
    return {key: value for key, value in node.items()
            if key not in {'evidence', *omit}}


def _scope(nodes, forecast):
    """Freeze the plan even when an as_of/coverage/condition is not confirmed.

    A missing requested snapshot must remain an unknown forecast, rather than
    silently adopting another time. The available single plan is used only to
    check entity identity, never to replace the requested calculation snapshot.
    """
    index = {node['id']: node for node in nodes}
    rid = forecast['delivery_round_id']
    snapshot = forecast.get('snapshot')
    if snapshot is None:
        available = [n for n in nodes if n['type'] == 'FulfillmentSnapshot' and rid in n['round_ids']]
        if len(available) > 1:
            raise ComparisonError('INCOMPARABLE_INPUTS', 'An unconfirmed instant has more than one possible plan')
        snapshot = available[0] if available else None
    sid = snapshot['id'] if snapshot else None
    allocations = [n for n in nodes if n['type'] == 'Allocation'
                   and n['delivery_round_id'] == rid and n['snapshot_id'] == sid]
    sources = {n.get('stock_id', n.get('production_work_id')) for n in allocations}
    aids = {n['id'] for n in allocations}
    # Source-wide allocations protect shared plans/caps, not only this round.
    shared_allocations = [n for n in nodes if n['type'] == 'Allocation'
                          and n.get('stock_id', n.get('production_work_id')) in sources]
    shipments = [n for n in nodes if n['type'] == 'Shipment' and n['allocation_id'] in aids]
    source_nodes = [index[nid] for nid in sorted(sources)]
    fingerprint = {
        'promise': {key: _business(value) for key, value in forecast['promise'].items()},
        'allocations': {n['id']: _business(n, omit=('snapshot_id',)) for n in shared_allocations},
        'sources': {n['id']: _business(n, omit=(
            ('snapshot_id', 'observed_at', 'completed_quantity') if n['type'] == 'ProductionWork'
            else ('snapshot_id',))) for n in source_nodes},
        'shipments': {n['id']: _business(n, omit=('snapshot_id',)) for n in shipments},
    }
    works = [n for n in source_nodes if n['type'] == 'ProductionWork']
    related = [*forecast['promise'].values(), *shared_allocations, *source_nodes, *shipments]
    if snapshot:
        related.append(snapshot)
        related.extend(n for n in nodes if n['type'] == 'DeliveryCondition'
                       and n['snapshot_id'] == sid and n['delivery_round_id'] == rid)
    work_ids = {n['id'] for n in works}
    related.extend(n for n in nodes if n['type'] == 'ProductionObservation'
                   and n['production_work_id'] in work_ids)
    related_by_id = {n['id']: n for n in related}
    evidence_ids = {eid for n in related for eid in n['evidence']}
    for eid in evidence_ids:
        evidence = index[eid]
        related_by_id[eid] = evidence
        related_by_id[evidence['source_id']] = index[evidence['source_id']]
    return fingerprint, works, related_by_id


def _side(nodes, rid, as_of, queried_at):
    response = response_plan(nodes, rid, as_of, queried_at=queried_at)
    scope, works, related = _scope(nodes, response['forecast'])
    production = (production_completion(nodes, works[0]['id'], rid, as_of, queried_at=queried_at)
                  if len(works) == 1 else None)
    return ({'as_of': response['forecast']['as_of'],
             'response_plan': response, 'production': production}, scope, related)


def _difference(before, after):
    return None if before is None or after is None else after - before


def _time_difference(before, after):
    if before is None or after is None:
        return None
    delta = instant(after) - instant(before)
    # Retain subsecond input precision without datetime.total_seconds() rounding.
    micros = delta // timedelta(microseconds=1)
    return micros // 1000000 if micros % 1000000 == 0 else micros / 1000000


def _changed_inputs(before, after):
    changes = []
    for nid in sorted(before.keys() | after.keys()):
        old, new = before.get(nid), after.get(nid)
        for field in sorted((old or {}).keys() | (new or {}).keys()):
            old_present, new_present = old is not None and field in old, new is not None and field in new
            old_value, new_value = (old or {}).get(field), (new or {}).get(field)
            if old_present == new_present and old_value == new_value:
                continue
            changes.append(dict(node_id=nid, field=field, before=old_value, after=new_value,
                                before_present=old_present, after_present=new_present,
                                unit=(new or old).get('unit'),
                                evidence_by_side=dict(before=list((old or {}).get('evidence', [])),
                                                      after=list((new or {}).get('evidence', [])))))
    return changes


def compare_response_plan(before_nodes, after_nodes, delivery_round_id, before_as_of, after_as_of,
                          *, before_version, after_version, queried_at):
    """Compare fixed approved inputs under the same current calculation rules.

    The Service supplies approved nodes and authenticated read authority. The
    pure function separately checks values/scope and never accepts forecast JSON.
    A same-version request must have the exact same graph (node order ignored).
    """
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
           for v in (before_version, after_version)):
        raise ComparisonError('INVALID_VERSION', 'Explicit nonnegative integer versions are required')
    if after_version < before_version:
        raise ComparisonError('INVALID_VERSION', 'After version must not precede before version')
    before_at, after_at = _comparison_times(
        delivery_round_id, before_as_of, after_as_of, queried_at)
    if before_version == after_version and before_at != after_at:
        raise ComparisonError('INVALID_TIME', 'One data version must be compared at one explicit instant')

    before, before_scope, before_related = _side(
        before_nodes, delivery_round_id, before_as_of, queried_at)
    after, after_scope, after_related = _side(
        after_nodes, delivery_round_id, after_as_of, queried_at)
    if before_version == after_version:
        before_index = {n['id']: n for n in before_nodes}
        after_index = {n['id']: n for n in after_nodes}
        if before_index != after_index:
            raise ComparisonError('INCOMPARABLE_INPUTS', 'One data version cannot bind different input graphs')
    if before_scope != after_scope:
        raise ComparisonError('INCOMPARABLE_INPUTS',
                              'Comparison requires the same promise, identity, sources, allocations and shipment plan')

    before['data_version'], after['data_version'] = before_version, after_version
    result = _compare_sides(before, after, delivery_round_id, queried_at,
                            before_related, after_related)
    result.update(contract_version=__version__, schema_version=SCHEMA_VERSION)
    result['limitations'].insert(0,
        'Previous approved inputs are recalculated with the same current rules, not historical model output.')
    result['limitations'].append(
        'No automatic refresh, notification, schedule optimisation or unapproved-input preview.')
    return result


def _comparison_times(delivery_round_id, before_as_of, after_as_of, queried_at):
    if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
        raise ComparisonError('INVALID_INPUT', 'An explicit nonempty delivery round ID is required')
    try:
        before_at, after_at = instant(before_as_of), instant(after_as_of)
        instant(queried_at)
    except (TypeError, ValueError) as exc:
        raise ComparisonError('INVALID_TIME', str(exc)) from exc
    if after_at < before_at:
        raise ComparisonError('INVALID_TIME', 'After as_of must not precede before as_of')
    return before_at, after_at


def _compare_sides(before, after, delivery_round_id, queried_at, before_related, after_related):
    old_response, new_response = before['response_plan'], after['response_plan']
    old_forecast, new_forecast = old_response['forecast'], new_response['forecast']
    old_production, new_production = before['production'] or {}, after['production'] or {}
    quantities = {key: _difference(old_forecast[key], new_forecast[key])
                  for key in ('on_time_quantity', 'shortfall_quantity')}
    quantities.update({key: _difference(old_response['dispatch_request'][key],
                                       new_response['dispatch_request'][key])
                       for key in _DISPATCH_QUANTITIES})
    times = dict(
        remaining_seconds=_difference(old_production.get('remaining_seconds'), new_production.get('remaining_seconds')),
        estimated_completion_seconds=_time_difference(old_production.get('estimated_completion_at'),
                                                      new_production.get('estimated_completion_at')),
        estimated_full_arrival_seconds=_time_difference(old_forecast['estimated_full_arrival_at'],
                                                        new_forecast['estimated_full_arrival_at']))
    selected, integrity = {}, {}
    for label, side in (('before', before), ('after', after)):
        response, production = side['response_plan'], side['production'] or {}
        selected[label] = sorted(set(response['evidence']) | {
            eid for node in production.get('observations', []) for eid in node['evidence']})
        integrity[label] = sorted(set(response['forecast']['evidence']) | set(production.get('evidence', [])))
    partial = (any(s['response_plan']['forecast']['status'] == 'needs_confirmation'
                   or s['response_plan']['forecast']['unknowns'] for s in (before, after))
               or any(s['production'] and s['production']['status'] == 'needs_confirmation' for s in (before, after)))
    changed = _changed_inputs(before_related, after_related)
    response_changes = [dict(field=field, before=old_response[field], after=new_response[field])
                        for field in ('response_state', 'customer_reply', 'dispatch_request', 'actions', 'confirmations')
                        if old_response[field] != new_response[field]]
    result = dict(
        comparison_rule_version=COMPARISON_RULE_VERSION, assessment_scope='delivery_response_comparison',
        delivery_round_id=delivery_round_id, queried_at=queried_at,
        synthetic=True, draft_only=True, review_required=True, execution_performed=False,
        comparison_status='partially_unknown' if partial else 'compared',
        before=before, after=after, quantity_deltas=quantities, time_deltas=times,
        production_deltas={key: _difference(old_production.get(key), new_production.get(key))
                           for key in ('good_rate_per_hour', 'produced_rate_per_hour', 'recent_defect_ratio',
                                       'remaining_quantity', 'latest_good_quantity')},
        changed_inputs=changed, response_changes=response_changes,
        evidence=sorted(set(selected['before']) | set(selected['after'])),
        evidence_by_side=selected, integrity_evidence_by_side=integrity,
        limitations=[
            'Deltas mean after minus before; a missing value remains null rather than zero.',
            'Input changes are observed together; no causal attribution or fault cause is established.',
            'Conditional synthetic forecasts and reviewer drafts do not send, register, approve or execute work.'])
    return deepcopy(result)


def compare_input_response_plan(before_nodes, after_nodes, delivery_round_id,
                                before_as_of, after_as_of, *, before_revision,
                                after_revision, queried_at):
    """Compare two validated, unregistered synthetic input revisions.

    Revisions are opaque input identifiers, not database versions. The hash is
    computed here from canonical input nodes, never accepted from the model.
    Node-list order does not change the hash; field/list contents still do.
    Source bytes and locators retain the existing B input validation boundary.
    No data_version or approved database provenance is produced by this path.
    """
    before_at, after_at = _comparison_times(
        delivery_round_id, before_as_of, after_as_of, queried_at)
    if any(not isinstance(revision, str) or not revision.strip()
           for revision in (before_revision, after_revision)):
        raise ComparisonError('INVALID_INPUT_REVISION', 'Explicit nonempty input revisions are required')
    from .schema_validation import validate_input_graph
    hashes = []
    for nodes in (before_nodes, after_nodes):
        report = validate_input_graph(nodes)
        if not report['valid']:
            raise ValueError(report)
        hashes.append(digest(sorted(nodes, key=lambda node: node['id'])))
    if before_revision == after_revision:
        if before_at != after_at:
            raise ComparisonError('INVALID_TIME', 'One input revision must bind one explicit instant')
        if hashes[0] != hashes[1]:
            raise ComparisonError('INCOMPARABLE_INPUTS', 'One input revision cannot bind different input graphs')
    before, before_scope, before_related = _side(
        before_nodes, delivery_round_id, before_as_of, queried_at)
    after, after_scope, after_related = _side(
        after_nodes, delivery_round_id, after_as_of, queried_at)
    if before_scope != after_scope:
        raise ComparisonError('INCOMPARABLE_INPUTS',
                              'Comparison requires the same promise, identity, sources, allocations and shipment plan')
    for side, revision, input_hash in ((before, before_revision, hashes[0]),
                                       (after, after_revision, hashes[1])):
        side.update(input_revision=revision, input_sha256=input_hash,
                    input_state='unregistered', synthetic=True)
    result = _compare_sides(before, after, delivery_round_id, queried_at,
                            before_related, after_related)
    result.update(input_comparison_version=INPUT_COMPARISON_VERSION,
                  assessment_scope='unregistered_delivery_input_comparison',
                  input_state='unregistered')
    result['limitations'].insert(0,
        'Unregistered synthetic input revisions are recalculated; this is not approved database version comparison.')
    return result
