"""Cumulative synthetic production observations; no forecast or state mutation."""
from __future__ import annotations

from .fulfillment_validation import _nonnegative_integer
from ..model import instant


COUNTER_FIELDS = ('produced_quantity', 'good_quantity', 'defective_quantity')


def validate_production_observations(index, error):
    """Validate every observation before a read selects its latest interval.

    Missing observations and stale work states remain incomplete knowledge. A
    recorded counter conflict, future observation or duplicate is invalid data.
    The validator deliberately does not impose CQ03's full-allocation scope on
    otherwise legal work/round graphs.
    """
    def reference(node, field, kind):
        value = node.get(field)
        target = index.get(value) if isinstance(value, str) else None
        return target if target is not None and target.get('type') == kind else None

    def parsed(node, field):
        if field not in node:
            return None
        try:
            return instant(node[field])
        except (TypeError, ValueError) as exc:
            error(node['id'], 'INVALID_TIME', field + ': ' + str(exc))
            return None

    by_work = {}
    for observation in index.values():
        if observation.get('type') != 'ProductionObservation':
            continue
        nid = observation['id']
        counters_valid = all(_nonnegative_integer(observation.get(key)) for key in COUNTER_FIELDS)
        if not counters_valid:
            error(nid, 'INVALID_QUANTITY', 'Production counters must be nonnegative integers, not booleans, floats or text')
        elif observation['produced_quantity'] != observation['good_quantity'] + observation['defective_quantity']:
            error(nid, 'PRODUCTION_TOTAL_MISMATCH', 'Produced quantity must equal good plus defective quantity')
        if observation.get('unit') != 'piece':
            error(nid, 'UNSUPPORTED_UNIT', 'Only finished-board piece counts are supported')
        observed = parsed(observation, 'observed_at')
        work = reference(observation, 'production_work_id', 'ProductionWork')
        if work is None:
            # model.validate_graph already reports the typed reference error.
            continue
        if observation.get('unit') != work.get('unit'):
            error(nid, 'UNIT_MISMATCH', 'Production observation and work units must agree')
        work_time = parsed(work, 'observed_at')
        started = parsed(work, 'started_at')
        snapshot = reference(work, 'snapshot_id', 'FulfillmentSnapshot')
        snapshot_time = parsed(snapshot, 'as_of') if snapshot else None
        if observed is not None:
            if started is not None and observed < started:
                error(nid, 'CONFLICTING_TIME', 'Production observation cannot precede the actual work start')
            if snapshot_time is not None and observed > snapshot_time:
                error(nid, 'CONFLICTING_TIME', 'Production observation cannot follow snapshot as_of')
            if work_time is not None and observed > work_time:
                error(nid, 'CONFLICTING_TIME', 'Production observation cannot follow the observed work state')
            if (work_time is not None and observed <= work_time
                    and _nonnegative_integer(observation.get('good_quantity'))
                    and _nonnegative_integer(work.get('completed_quantity'))):
                good, completed = observation['good_quantity'], work['completed_quantity']
                if good > completed or (observed == work_time and good != completed):
                    error(nid, 'PRODUCTION_COMPLETION_MISMATCH',
                          'Good count cannot exceed a later work completed count; same-instant counts must agree')
            by_work.setdefault(work['id'], []).append((observed, observation))
        if (work.get('production_state') in ('ready', 'waiting') and counters_valid
                and any(observation[key] > 0 for key in COUNTER_FIELDS)):
            error(nid, 'CONFLICTING_PRODUCTION_STATE', 'Unstarted ready/waiting work cannot have positive production counters')

    for observations in by_work.values():
        observations.sort(key=lambda entry: (entry[0], entry[1]['id']))
        for (before_at, before), (after_at, after) in zip(observations, observations[1:]):
            if before_at == after_at:
                error(after['id'], 'DUPLICATE_PRODUCTION_OBSERVATION', before['id'])
            for field in COUNTER_FIELDS:
                if (_nonnegative_integer(before.get(field)) and _nonnegative_integer(after.get(field))
                        and after[field] < before[field]):
                    error(after['id'], 'PRODUCTION_COUNTER_DECREASED', field + ' decreased after ' + before['id'])
