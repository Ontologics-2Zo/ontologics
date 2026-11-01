"""A ontology definitions and rules, consumed by the explicit A validator.

The common schema is unchanged. graph_validation, graph_export and export_tbox
compose common and A definitions for the A-owned execution and export paths.
These rule functions alone do not constitute approval or a whole-graph report.
"""
from __future__ import annotations

import re

from ..model import instant, _text

# Version of the formerly combined model, retained for migration traceability.
SCHEMA_VERSION = 'graph-0.5-a-planning-1'

SCHEMA = {
    'Contract': (['company', 'customer_name'], {}),
    'ContractItem': (['contract_id', 'configuration_id', 'quantity', 'unit'], {'contract_id': 'Contract', 'configuration_id': 'Configuration'}),
    'DeliveryRound': (['contract_item_id', 'quantity', 'unit', 'due_at', 'due_basis', 'destination'], {'contract_item_id': 'ContractItem'}),
    'ComponentStockSnapshot': (['lot_id', 'location', 'as_of', 'unit', 'availability_basis', 'quantity_state'], {'lot_id': 'ComponentLot'}),
}

OPTIONAL_FIELDS = {
    'ComponentStockSnapshot': {'available_quantity', 'deductions_disjoint', 'held_quantity', 'on_hand_quantity', 'reserved_quantity'},
}

# Additional non-text fields formerly added to the common shape validator.
STRUCTURED_FIELDS = {
    'ComponentStockSnapshot': set(OPTIONAL_FIELDS['ComponentStockSnapshot']),
}


def validate_node_rules(n, index, identities, error, warnings):
    """Append A-only diagnostics; index/identities belong to one validation run.

    Input nodes must already have been checked by a graph/shape validator.
    error(node_id, code, detail) and warnings collect diagnostics.
    """
    nid, kind = n['id'], n.get('type')
    if kind in SCHEMA and n.get('origin') != 'synthetic':
        error(nid, 'DEMO_OPERATIONS_ONLY', 'Planning reference facts must be synthetic')
    # Rules visible inside the received B model only; no claim of parity
    # with B's unavailable fulfillment/production/delivery modules.
    if kind in ('ContractItem', 'DeliveryRound'):
        if type(n.get('quantity')) is not int or n['quantity'] <= 0:
            error(nid, 'INVALID_QUANTITY', 'Contract and round quantities must be positive integers, not booleans or floats')
        if n.get('unit') != 'piece':
            error(nid, 'UNSUPPORTED_UNIT', 'Only finished-board piece counts are supported')
    if kind == 'DeliveryRound':
        try:
            instant(n.get('due_at'))
        except (ValueError, TypeError) as exc:
            error(nid, 'INVALID_TIME', f'due_at: {exc}')
        if n.get('due_basis') != 'arrival':
            error(nid, 'UNSUPPORTED_DUE_BASIS', 'Only an explicit customer-arrival deadline is supported')
        item = index.get(n.get('contract_item_id'), {}) if isinstance(n.get('contract_item_id'), str) else {}
        if item.get('type') == 'ContractItem' and n.get('unit') != item.get('unit'):
            error(nid, 'UNIT_MISMATCH', 'Round and contract item units must agree')
    if kind == 'ComponentStockSnapshot':
        basis, state = n.get('availability_basis'), n.get('quantity_state')
        if basis not in ('on_hand_less_reserved_and_held', 'reported_available'):
            error(nid, 'INVALID_BASIS', 'Explicit stock observation basis required')
        if state not in ('known', 'unknown'):
            error(nid, 'INVALID_QUANTITY_STATE', 'known or unknown required')
        needed = ('available_quantity',) if basis == 'reported_available' else (
            'on_hand_quantity', 'reserved_quantity', 'held_quantity')
        for key in needed:
            if key not in n: error(nid, 'MISSING_FIELD', key)
        for key in ('on_hand_quantity', 'reserved_quantity', 'held_quantity', 'available_quantity'):
            if key in n and not (state == 'unknown' and n[key] is None):
                if type(n[key]) is not int or n[key] < 0:
                    error(nid, 'INVALID_QUANTITY', key + ' requires a nonnegative integer; null only for unknown')
        if state == 'unknown':
            warnings.append({'node_id': nid, 'code': 'UNKNOWN_QUANTITY'})
        flag = n.get('deductions_disjoint')
        if flag is not None and type(flag) is not bool:
            error(nid, 'INVALID_DEDUCTION_FLAG', 'deductions_disjoint must be boolean or null')
        if basis == 'on_hand_less_reserved_and_held' and flag is not True:
            warnings.append({'node_id': nid, 'code': 'DEDUCTION_SCOPE_UNCONFIRMED'})
        if n.get('unit') != 'piece': error(nid, 'UNSUPPORTED_UNIT', 'Only component piece counts supported')
        try:
            if not isinstance(n.get('as_of'), str) or not re.fullmatch(
                    r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', n['as_of']):
                raise ValueError('Explicit seconds, at most six fractional digits and UTC offset required')
            observed = instant(n['as_of'])
            if _text(n.get('lot_id')) and _text(n.get('location')):
                identity = (kind, n['lot_id'], n['location'], observed)
                if identity in identities: error(nid, 'DUPLICATE_IDENTITY', identities[identity])
                identities[identity] = nid
        except (ValueError, TypeError) as exc:
            error(nid, 'INVALID_TIME', f'as_of: {exc}')


def validate_round_totals(index, error):
    """Check the existing multiple-round quantity limit against contract items."""
    # Preserve B's multiple-round contract. A's single full round restriction
    # belongs to its adapter, not the shared ontology.
    round_totals = {}
    for n in index.values():
        if (n.get('type') == 'DeliveryRound' and isinstance(n.get('contract_item_id'), str)
                and type(n.get('quantity')) is int and n['quantity'] > 0):
            item_id = n['contract_item_id']
            round_totals[item_id] = round_totals.get(item_id, 0) + n['quantity']
    for item_id, total in round_totals.items():
        item = index.get(item_id, {})
        if item.get('type') == 'ContractItem' and type(item.get('quantity')) is int and item['quantity'] > 0 and total > item['quantity']:
            error(item_id, 'ROUND_QUANTITY_EXCEEDED', 'Sum of delivery rounds exceeds the contract item quantity')
