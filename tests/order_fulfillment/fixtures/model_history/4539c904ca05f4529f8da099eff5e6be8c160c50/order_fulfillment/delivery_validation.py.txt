"""Typed source-backed dispatch assumptions for conditional arrival forecasts."""
from __future__ import annotations

from .fulfillment_validation import _nonnegative_integer

DISPATCH_MODES = ('split_available', 'all_at_once')


def validate_delivery_conditions(index, error):
    """Validate facts, leaving absent conditions as unknown query inputs.

    Scope/quantity/chronology of the existing stock and shipment records continue
    to be validated by the common fulfillment/production validators.
    """
    identities = {}
    for node in index.values():
        if node.get('type') != 'DeliveryCondition':
            continue
        nid = node['id']
        for field in ('preparation_seconds', 'transport_seconds'):
            if not _nonnegative_integer(node.get(field)):
                error(nid, 'INVALID_DURATION', field + ' must be a nonnegative integer second count')
        if node.get('dispatch_mode') not in DISPATCH_MODES:
            error(nid, 'INVALID_DISPATCH_MODE', 'Use split_available or all_at_once explicitly')
        sid, rid = node.get('snapshot_id'), node.get('delivery_round_id')
        snapshot = index.get(sid) if isinstance(sid, str) else None
        delivery = index.get(rid) if isinstance(rid, str) else None
        if not snapshot or snapshot.get('type') != 'FulfillmentSnapshot':
            continue  # Common typed-reference validation supplies the error.
        if not delivery or delivery.get('type') != 'DeliveryRound':
            continue
        round_ids = snapshot.get('round_ids')
        if not isinstance(round_ids, list) or rid not in round_ids:
            error(nid, 'SNAPSHOT_MISMATCH', 'Condition round must be listed in its snapshot')
        key = (sid, rid)
        if key in identities:
            error(nid, 'DUPLICATE_DELIVERY_CONDITION', identities[key])
        identities[key] = nid
