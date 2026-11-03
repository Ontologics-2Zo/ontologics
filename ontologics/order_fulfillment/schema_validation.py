"""B domain rules reused by common validation and B input adapters.

The shared validator owns shape, origin, evidence and graph references. The
lower-level functions remain available for domain and extraction regressions.
Validation alone never approves or stores facts.
"""
from ..model import _string_list, instant
from ..model import validate_graph as validate_common_graph
from .schema import LIST_REFERENCES, OPTIONAL_REFERENCES, SCHEMA


def _positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_input_graph(nodes):
    """Validate the complete shared graph; this never approves or stores it."""
    return validate_common_graph(nodes)


def validate_references(index, error):
    """Check B references against the full index, preserving legacy findings.

    Only an absent optional field is skipped. A supplied null/empty/malformed
    value is invalid. Lists must contain unique nonempty text IDs resolving to
    the declared type. Common node references remain the common validator's job.
    """
    for nid, node in index.items():
        kind = node.get('type')
        if not isinstance(kind, str) or kind not in SCHEMA:
            continue
        for key, target_type in SCHEMA[kind][1].items():
            if key in OPTIONAL_REFERENCES.get(kind, set()) and key not in node:
                continue
            target = index.get(node.get(key)) if isinstance(node.get(key), str) else None
            if target is None or target.get('type') != target_type:
                error(nid, 'INVALID_REFERENCE', key + ' -> ' + target_type)
        for key, target_type in LIST_REFERENCES.get(kind, {}).items():
            values = node.get(key)
            if not _string_list(values):
                error(nid, 'INVALID_REFERENCE', key + ' must be a unique nonempty reference list')
            else:
                for value in values:
                    if index.get(value, {}).get('type') != target_type:
                        error(nid, 'INVALID_REFERENCE', key + ' -> ' + target_type)


def validate_contracts(index, error):
    """Preserve the existing contract/round rules and finding details."""
    for nid, node in index.items():
        kind = node.get('type')
        if kind in ('ContractItem', 'DeliveryRound'):
            if not _positive_integer(node.get('quantity')):
                error(nid, 'INVALID_QUANTITY', 'Contract and round quantities must be positive integers, not booleans or floats')
            if node.get('unit') != 'piece':
                error(nid, 'UNSUPPORTED_UNIT', 'Only finished-board piece counts are supported')
        if kind == 'DeliveryRound':
            try:
                instant(node.get('due_at'))
            except (ValueError, TypeError) as exc:
                error(nid, 'INVALID_TIME', f'due_at: {exc}')
            if node.get('due_basis') != 'arrival':
                error(nid, 'UNSUPPORTED_DUE_BASIS', 'Only an explicit customer-arrival deadline is supported')
            item = index.get(node.get('contract_item_id'), {}) if isinstance(node.get('contract_item_id'), str) else {}
            if item.get('type') == 'ContractItem' and node.get('unit') != item.get('unit'):
                error(nid, 'UNIT_MISMATCH', 'Round and contract item units must agree')

    round_totals = {}
    for node in index.values():
        if node.get('type') == 'DeliveryRound' and isinstance(node.get('contract_item_id'), str) and _positive_integer(node.get('quantity')):
            item_id = node['contract_item_id']
            round_totals[item_id] = round_totals.get(item_id, 0) + node['quantity']
    for item_id, total in round_totals.items():
        item = index.get(item_id, {})
        if item.get('type') == 'ContractItem' and _positive_integer(item.get('quantity')) and total > item['quantity']:
            error(item_id, 'ROUND_QUANTITY_EXCEEDED', 'Sum of delivery rounds exceeds the contract item quantity')


def validate_domain(index, error):
    """Run B-only rules, reusing the three existing B validators.

    Shape, required/scalar fields, origins and evidence need validate_input_graph
    or equivalent caller checks. Common nodes retain common validation. No
    approval is implied here.
    """
    # These validators also consume B helpers; import after this module is ready.
    from .delivery_validation import validate_delivery_conditions
    from .fulfillment_validation import validate_fulfillment
    from .production_validation import validate_production_observations

    validate_references(index, error)
    validate_contracts(index, error)
    validate_fulfillment(index, error)
    validate_production_observations(index, error)
    validate_delivery_conditions(index, error)
