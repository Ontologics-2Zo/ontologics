"""B input validation, separate from common graph registration and approval.

Common nodes retain the common validator's source/identity/time checks. B input
nodes use their own schema and domain checks without registering common types.
The lower-level domain functions also serve the existing extraction regressions.
"""
from ..model import COMMON_FIELDS, _string_list, _text, instant
from ..model import validate_graph as validate_common_graph
from .schema import ALLOWED_FIELDS, LIST_REFERENCES, OPTIONAL_REFERENCES, SCHEMA, STRUCTURED_FIELDS


def _positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_input_graph(nodes):
    """Validate supplied B business inputs; this never approves or stores them.

    SourceDocument/Evidence and design nodes remain common nodes, even for a
    synthetic B source. Only B business types are excluded from the common
    validator, then checked against the full input index here.
    """
    if not isinstance(nodes, list) or len(nodes) > 20000:
        return {'valid': False, 'errors': [dict(node_id=None, code='INVALID_GRAPH_SIZE',
                detail='A list with at most 20000 nodes is required')], 'warnings': []}
    business, common = [], []
    for node in nodes:
        if (isinstance(node, dict) and isinstance(node.get('type'), str)
                and node['type'] in SCHEMA):
            business.append(node)
        else:
            common.append(node)
    report = validate_common_graph(common)
    errors = list(report['errors'])

    def error(nid, code, detail):
        errors.append(dict(node_id=nid, code=code, detail=detail))

    index = {}
    for node in nodes:
        if not isinstance(node, dict) or not _text(node.get('id')):
            continue
        nid = node['id']
        previous = index.get(nid)
        if previous is not None and any(
                isinstance(n.get('type'), str) and n['type'] in SCHEMA for n in (previous, node)):
            error(nid, 'DUPLICATE_ID', 'IDs must be unique')
        index[nid] = node

    for node in business:
        nid, kind = node.get('id'), node['type']
        if not _text(nid):
            error(None, 'INVALID_NODE', 'Every node needs a nonempty string id')
            continue
        extra = set(node) - ALLOWED_FIELDS[kind]
        if extra:
            error(nid, 'UNKNOWN_FIELD', ', '.join(sorted(str(k) for k in extra)))
        if node.get('origin') not in ('real', 'synthetic'):
            error(nid, 'ORIGIN_REQUIRED', 'real or synthetic')
        if node.get('origin') != 'synthetic':
            error(nid, 'DEMO_OPERATIONS_ONLY', 'B examples require synthetic business inputs')
        for key in COMMON_FIELDS | set(SCHEMA[kind][0]):
            if key not in node or node[key] is None or node[key] == '':
                error(nid, 'MISSING_FIELD', key)
        for key in ALLOWED_FIELDS[kind] - STRUCTURED_FIELDS - {'evidence', 'quantity'}:
            if key in node and not _text(node[key]):
                error(nid, 'INVALID_FIELD_TYPE', key + ' must be nonempty text')
        evidence = node.get('evidence', [])
        if not _string_list(evidence, allow_empty=True):
            error(nid, 'INVALID_EVIDENCE', 'evidence must be a list of unique nonempty IDs')
            evidence = []
        if not evidence:
            error(nid, 'EVIDENCE_REQUIRED', 'Every factual node needs source locators')
        for eid in evidence:
            source = index.get(eid, {})
            if source.get('type') != 'Evidence':
                error(nid, 'EVIDENCE_NOT_FOUND', eid)
            elif source.get('origin') != node.get('origin'):
                error(nid, 'EVIDENCE_ORIGIN_MISMATCH', eid)
    validate_domain(index, error)
    return {'valid': not errors, 'errors': errors, 'warnings': list(report['warnings'])}


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
