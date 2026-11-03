"""B-CQ02 snapshot relationships and quantities; no forecast or approval logic."""
from __future__ import annotations

from ..model import _positive_integer, instant


CONFIRMATION_KEYS = frozenset({
    'allocations_complete', 'source_allocations_complete', 'allocations_disjoint',
    'stock_events_complete', 'stock_observations_before_departures',
    'shipment_events_complete', 'production_state_complete', 'material_disjoint',
    'no_reallocation',
})
FULFILLMENT_TYPES = frozenset({
    'FulfillmentSnapshot', 'FinishedGoodsStock', 'ProductionWork', 'Allocation', 'Shipment',
})


def _nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_fulfillment(index, error):
    """Add validation findings without raising on malformed candidate JSON values.

    Missing/false completeness assertions are incomplete knowledge, not invalid
    data. The read tool separately refuses to finalize totals in that case.
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

    def round_context(node):
        round_node = reference(node, 'delivery_round_id', 'DeliveryRound')
        if round_node is None:
            return None, None, None
        item = reference(round_node, 'contract_item_id', 'ContractItem')
        contract = reference(item, 'contract_id', 'Contract') if item else None
        return round_node, item, contract

    source_totals, round_totals, shipment_allocations, snapshot_identities = {}, {}, {}, {}
    for node in index.values():
        kind = node.get('type')
        if not isinstance(kind, str) or kind not in FULFILLMENT_TYPES:
            continue
        nid = node['id']
        if kind == 'FulfillmentSnapshot':
            as_of = parsed(node, 'as_of')
            company = node.get('company')
            if as_of is not None and isinstance(company, str) and company.strip():
                identity = (company, as_of)
                if identity in snapshot_identities:
                    error(nid, 'DUPLICATE_IDENTITY', snapshot_identities[identity])
                snapshot_identities[identity] = nid
            confirmations = node.get('confirmations')
            if not isinstance(confirmations, dict):
                error(nid, 'INVALID_CONFIRMATIONS', 'confirmations must be an object of explicit boolean assertions')
            else:
                for key, value in confirmations.items():
                    if key not in CONFIRMATION_KEYS:
                        error(nid, 'INVALID_CONFIRMATIONS', 'Unknown confirmation: ' + str(key))
                    if not isinstance(value, bool):
                        error(nid, 'INVALID_CONFIRMATIONS', str(key) + ' must be boolean')
            round_ids = node.get('round_ids')
            if isinstance(round_ids, list):
                for rid in round_ids:
                    round_node = index.get(rid) if isinstance(rid, str) else None
                    if round_node and round_node.get('type') == 'DeliveryRound':
                        item = reference(round_node, 'contract_item_id', 'ContractItem')
                        contract = reference(item, 'contract_id', 'Contract') if item else None
                        if contract and contract.get('company') != company:
                            error(nid, 'COMPANY_MISMATCH', 'Snapshot and listed round must belong to the same company')
            continue

        snapshot = reference(node, 'snapshot_id', 'FulfillmentSnapshot')
        as_of = parsed(snapshot, 'as_of') if snapshot else None
        if node.get('unit') != 'piece':
            error(nid, 'UNSUPPORTED_UNIT', 'Only finished-board piece counts are supported')
        if kind in ('FinishedGoodsStock', 'ProductionWork'):
            if snapshot and node.get('company') != snapshot.get('company'):
                error(nid, 'COMPANY_MISMATCH', 'Source and snapshot companies must agree')
            observed = parsed(node, 'observed_at')
            if observed is not None and as_of is not None and observed > as_of:
                error(nid, 'CONFLICTING_TIME', 'Source observation cannot follow its snapshot as_of')
            if kind == 'FinishedGoodsStock':
                if not _nonnegative_integer(node.get('quantity')):
                    error(nid, 'INVALID_QUANTITY', 'Stock quantity must be a nonnegative integer')
            else:
                target, completed = node.get('target_quantity'), node.get('completed_quantity')
                if not _positive_integer(target) or not _nonnegative_integer(completed):
                    error(nid, 'INVALID_QUANTITY', 'Work target must be a positive integer and completed quantity a nonnegative integer')
                elif completed > target:
                    error(nid, 'WORK_QUANTITY_EXCEEDED', 'Completed quantity exceeds the work target')
                state = node.get('production_state')
                if state not in ('in_progress', 'ready', 'waiting'):
                    error(nid, 'INVALID_PRODUCTION_STATE', 'Use in_progress, ready or waiting')
                started = parsed(node, 'started_at')
                parsed(node, 'planned_start_at')
                if state == 'in_progress':
                    if 'started_at' not in node:
                        error(nid, 'MISSING_FIELD', 'in_progress work requires started_at')
                    if started is not None and observed is not None and started > observed:
                        error(nid, 'CONFLICTING_TIME', 'Actual work start cannot follow its observed state')
                elif 'started_at' in node:
                    error(nid, 'CONFLICTING_TIME', 'Unstarted ready/waiting work cannot carry an actual start')
            continue

        quantity = node.get('quantity')
        if not _positive_integer(quantity):
            error(nid, 'INVALID_QUANTITY', 'Allocation and shipment quantities must be positive integers')
        if kind == 'Allocation':
            has_stock, has_work = 'stock_id' in node, 'production_work_id' in node
            if has_stock == has_work:
                error(nid, 'INVALID_ALLOCATION_TARGET', 'Exactly one stock_id or production_work_id is required')
            source = (reference(node, 'stock_id', 'FinishedGoodsStock') if has_stock
                      else reference(node, 'production_work_id', 'ProductionWork'))
            round_node, item, contract = round_context(node)
            if snapshot and round_node:
                ids = snapshot.get('round_ids')
                if not isinstance(ids, list) or round_node['id'] not in ids:
                    error(nid, 'SNAPSHOT_MISMATCH', 'Allocation round must be listed in its snapshot')
            if source:
                if source.get('snapshot_id') != node.get('snapshot_id'):
                    error(nid, 'SNAPSHOT_MISMATCH', 'Allocation and source snapshots must agree')
                if source.get('unit') != node.get('unit'):
                    error(nid, 'UNIT_MISMATCH', 'Allocation and source units must agree')
                if item and source.get('configuration_id') != item.get('configuration_id'):
                    error(nid, 'CONFIGURATION_MISMATCH', 'Source configuration must match the promised configuration')
                if contract and source.get('company') != contract.get('company'):
                    error(nid, 'COMPANY_MISMATCH', 'Source and contract companies must agree')
                if _positive_integer(quantity):
                    source_totals[source['id']] = source_totals.get(source['id'], 0) + quantity
            if round_node:
                if round_node.get('unit') != node.get('unit'):
                    error(nid, 'UNIT_MISMATCH', 'Allocation and round units must agree')
                if snapshot and contract and snapshot.get('company') != contract.get('company'):
                    error(nid, 'COMPANY_MISMATCH', 'Allocation snapshot and contract companies must agree')
                if snapshot and _positive_integer(quantity):
                    key = (snapshot['id'], round_node['id'])
                    round_totals[key] = round_totals.get(key, 0) + quantity
            continue

        allocation = reference(node, 'allocation_id', 'Allocation')
        if allocation:
            if allocation.get('snapshot_id') != node.get('snapshot_id'):
                error(nid, 'SNAPSHOT_MISMATCH', 'Shipment and allocation snapshots must agree')
            if allocation.get('unit') != node.get('unit'):
                error(nid, 'UNIT_MISMATCH', 'Shipment and allocation units must agree')
            if quantity != allocation.get('quantity'):
                error(nid, 'SHIPMENT_QUANTITY_MISMATCH', 'First format supports one shipment for the entire allocation')
            aid = allocation['id']
            if aid in shipment_allocations:
                error(nid, 'DUPLICATE_SHIPMENT', shipment_allocations[aid])
            shipment_allocations[aid] = nid
            stock = reference(allocation, 'stock_id', 'FinishedGoodsStock')
            if stock is None:
                error(nid, 'UNSUPPORTED_SHIPMENT_SOURCE', 'Only stock allocations may ship in the first format')
            round_node, _, _ = round_context(allocation)
            if round_node and node.get('destination') != round_node.get('destination'):
                error(nid, 'DESTINATION_MISMATCH', 'Shipment arrival must be at the promised destination')
        else:
            stock = None
        times = {field: parsed(node, field) for field in
                 ('planned_departure_at', 'departed_at', 'expected_arrival_at', 'arrived_at')}
        departed, arrived = times['departed_at'], times['arrived_at']
        if 'arrived_at' in node and 'departed_at' not in node:
            error(nid, 'MISSING_FIELD', 'Actual arrival requires actual departure')
        if departed is not None and arrived is not None and arrived < departed:
            error(nid, 'CONFLICTING_TIME', 'Actual arrival cannot precede departure')
        for field in ('departed_at', 'arrived_at'):
            if times[field] is not None and as_of is not None and times[field] > as_of:
                error(nid, 'CONFLICTING_TIME', field + ' cannot follow snapshot as_of')
        stock_observed = parsed(stock, 'observed_at') if stock else None
        if departed is not None and stock_observed is not None and departed < stock_observed:
            error(nid, 'CONFLICTING_TIME', 'First format requires departure at or after the stock observation')

    # Source totals include all rounds: filtering first would hide over-allocation.
    for source_id, total in source_totals.items():
        source = index[source_id]
        capacity = source.get('quantity') if source['type'] == 'FinishedGoodsStock' else source.get('target_quantity')
        if _nonnegative_integer(capacity) and total > capacity:
            error(source_id, 'SOURCE_QUANTITY_EXCEEDED', 'All-round allocation sum exceeds the source quantity')
    for (_, round_id), total in round_totals.items():
        capacity = index[round_id].get('quantity')
        if _positive_integer(capacity) and total > capacity:
            error(round_id, 'ALLOCATION_QUANTITY_EXCEEDED', 'Snapshot allocation sum exceeds the round promise')
