"""Deterministic request validation; not an ontology, approval or calculator.

The host may supply a full reference graph and external bytes. Neither is
accepted from Agent arguments by a registered tool (no such tool exists yet).
Human confirmation is deliberately unresolved until a trusted host adapter is
available. A successful report never establishes operational facts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import re

from ..model import instant
from .graph_validation import validate_graph


def validate_planning_context(context: dict, *, reference: dict | None = None,
                              external_contents: dict | None = None) -> dict:
    """Return valid/status/normalized/errors/unresolved and verified references.

    reference: host-owned {'data_version': int, 'nodes': full_graph_list}.
    external_contents: host-owned source_ref -> original bytes, never paths.
    Missing values are errors; explicit quantity_state='unknown' permits null
    stock quantities, which remain null. No DB, network or automatic writes.
    Local A graph validation delegates common nodes/source integrity to the
    unchanged common validator. It does not register facts or approve writes.
    """
    errors, unresolved, warnings = [], [], []
    verified, verified_sources = set(), set()

    def issue(collection, path, code, message):
        collection.append({'path': path, 'code': code, 'message': message})

    def error(path, code, message):
        issue(errors, path, code, message)

    def pending(path, code, message):
        issue(unresolved, path, code, message)

    def object_fields(value, path, allowed, required=()):
        if not isinstance(value, dict):
            error(path, 'INVALID_OBJECT', 'Expected an object')
            return {}
        for key in value.keys() - allowed:
            error(path + '.' + str(key) if path else str(key), 'UNKNOWN_FIELD', 'Field is outside this request contract')
        for key in required:
            if key not in value:
                error(path + '.' + key if path else key, 'MISSING_FIELD', 'Explicit value required; no default applied')
        return value

    def text(value, path, *, reference_id=False):
        if not isinstance(value, str) or not value.strip() or (reference_id and any(c.isspace() for c in value)):
            error(path, 'INVALID_REFERENCE' if reference_id else 'INVALID_TEXT', 'Nonempty text required; IDs cannot contain whitespace')
            return None
        return value.strip()

    def quantity(value, path, *, positive=False, unknown=False):
        if value is None and unknown:
            pending(path, 'UNKNOWN_QUANTITY', 'Quantity is explicitly unknown; it is not zero')
        elif type(value) is not int or value < (1 if positive else 0):
            error(path, 'INVALID_QUANTITY', 'Expected a positive integer' if positive else 'Expected a nonnegative integer')

    def unit(value, path):
        if value != 'piece':
            error(path, 'INVALID_UNIT', 'Only piece is supported; board and component quantities stay separate')

    def time_value(value, path, *, allow_date=False):
        try:
            if allow_date and isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                date.fromisoformat(value)
                return 'date'
            instant(value)
            return 'datetime'
        except (TypeError, ValueError, OverflowError):
            error(path, 'INVALID_TIME', 'Expected a calendar date or offset-aware datetime' if allow_date else 'Expected an offset-aware datetime')
            return None

    def rows(value, path):
        if not isinstance(value, list) or not value:
            error(path, 'INVALID_LIST', 'Expected a nonempty list')
            return []
        return value

    def unique(value, seen, path):
        if value is not None:
            if value in seen:
                error(path, 'DUPLICATE_REFERENCE', 'Reference must occur only once')
            seen.add(value)

    allowed = {'contract_item', 'planned_input_quantity', 'unit', 'stock_scope', 'stocks',
               'data_version', 'external_inputs', 'confirmation_ref', 'production_conditions'}
    if not isinstance(context, dict):
        error('', 'INVALID_OBJECT', 'Planning context must be an object')
        normalized = None
        value = {}
    else:
        value = deepcopy(context)
        normalized = value
        object_fields(value, '', allowed, ('contract_item', 'planned_input_quantity', 'unit',
            'stock_scope', 'stocks', 'data_version', 'external_inputs'))

    if type(value.get('data_version')) is not int or value['data_version'] < 0:
        error('data_version', 'INVALID_VERSION', 'Explicit nonnegative integer graph version required')
    quantity(value.get('planned_input_quantity'), 'planned_input_quantity', positive=True)
    unit(value.get('unit'), 'unit')

    index = None
    if reference is None:
        pending('reference', 'REFERENCE_NOT_SUPPLIED', 'Graph reference existence and type have not been checked')
    elif (not isinstance(reference, dict) or type(reference.get('data_version')) is not int
          or reference['data_version'] < 0 or not isinstance(reference.get('nodes'), list)):
        error('reference', 'INVALID_REFERENCE_GRAPH', 'Host reference requires data_version and complete nodes')
    elif reference['data_version'] != value.get('data_version'):
        error('data_version', 'REFERENCE_VERSION_MISMATCH', 'Request and reference graph versions differ')
    else:
        report = validate_graph(reference['nodes'])
        warnings.extend(report['warnings'])
        if not report['valid']:
            error('reference', 'INVALID_REFERENCE_GRAPH', 'Planning reference graph validation failed')
            errors[-1]['details'] = report['errors']
        else:
            index = {node['id']: node for node in reference['nodes']}

    def graph_ref(ref, path, kind, *, unlinked=False):
        cleaned = text(ref, path, reference_id=True)
        if cleaned is None or index is None:
            return None
        node = index.get(cleaned)
        if node is None and unlinked:
            pending(path, 'REFERENCE_NOT_LINKED', 'External operating reference is not a registered graph fact')
        elif node is None or node.get('type') != kind:
            error(path, 'INVALID_REFERENCE', 'Expected an existing ' + kind)
        else:
            verified.add(cleaned)
            return node
        return None

    contract = object_fields(value.get('contract_item'), 'contract_item', {
        'id', 'configuration_id', 'ordered_quantity', 'fulfilled_quantity', 'allocated_quantity',
        'unit', 'delivery_due', 'delivery_due_precision', 'constraints_state'},
        ('id', 'configuration_id', 'ordered_quantity', 'fulfilled_quantity', 'allocated_quantity', 'unit'))
    contract_id = text(contract.get('id'), 'contract_item.id', reference_id=True)
    if index is not None and contract_id in index and index[contract_id]['type'] != 'ContractItem':
        error('contract_item.id', 'INVALID_REFERENCE', 'Existing graph ID belongs to a different type')
    graph_ref(contract.get('configuration_id'), 'contract_item.configuration_id', 'Configuration')
    for key in ('ordered_quantity', 'fulfilled_quantity', 'allocated_quantity'):
        quantity(contract.get(key), 'contract_item.' + key, positive=key == 'ordered_quantity')
    unit(contract.get('unit'), 'contract_item.unit')
    if 'delivery_due_precision' in contract and (
            contract['delivery_due_precision'] not in ('date', 'datetime') or contract.get('delivery_due') is None):
        error('contract_item.delivery_due_precision', 'INVALID_PRECISION', 'Precision requires a corresponding date value and must be date or datetime')
    if 'delivery_due' not in contract or contract['delivery_due'] is None:
        pending('contract_item.delivery_due', 'DUE_DATE_UNCONFIRMED', 'Customer due date is not confirmed')
    else:
        precision = time_value(contract['delivery_due'], 'contract_item.delivery_due', allow_date=True)
        if precision:
            if contract.get('delivery_due_precision', precision) != precision:
                error('contract_item.delivery_due_precision', 'INVALID_PRECISION', 'Precision must match the original value')
            contract['delivery_due_precision'] = precision
    state = contract.get('constraints_state', 'unspecified')
    if state not in ('unspecified', 'requires_review'):
        error('contract_item.constraints_state', 'INVALID_STATE', 'Only unspecified/requires_review are supported before host confirmation')
    pending('contract_item.constraints_state', 'CONSTRAINTS_UNCONFIRMED', 'No contract permission is inferred from unspecified restrictions')

    # Type availability and candidate reference validity do not prove an applied
    # registration. Only reference_adapter checks the exact draft and snapshot.
    pending('reference', 'EXTENSION_INTEGRATION_PENDING', 'Registered graph integration requires the public-session adapter')

    scope = object_fields(value.get('stock_scope'), 'stock_scope', {'company', 'location', 'as_of'},
                          ('company', 'location', 'as_of'))
    for key in ('company', 'location'):
        cleaned = text(scope.get(key), 'stock_scope.' + key)
        if cleaned:
            scope[key] = cleaned
    time_value(scope.get('as_of'), 'stock_scope.as_of')

    stock_ids = set()
    for i, raw in enumerate(rows(value.get('stocks'), 'stocks')):
        path = 'stocks[' + str(i) + ']'
        stock = object_fields(raw, path, {'id', 'lot_id', 'component_id', 'bom_item_id',
            'on_hand_quantity', 'reserved_quantity', 'held_quantity', 'available_quantity',
            'unit', 'availability_basis', 'deductions_disjoint', 'quantity_state'},
            ('id', 'lot_id', 'component_id', 'bom_item_id', 'unit', 'availability_basis'))
        sid = text(stock.get('id'), path + '.id', reference_id=True)
        unique(sid, stock_ids, path + '.id')
        if index is not None and sid in index and index[sid]['type'] != 'ComponentStockSnapshot':
            error(path + '.id', 'INVALID_REFERENCE', 'Existing graph ID belongs to a different type')
        graph_ref(stock.get('component_id'), path + '.component_id', 'ComponentModel')
        bom = graph_ref(stock.get('bom_item_id'), path + '.bom_item_id', 'BOMItem')
        lot = graph_ref(stock.get('lot_id'), path + '.lot_id', 'ComponentLot', unlinked=True)
        if bom and (bom['component_id'] != stock.get('component_id') or
                    bom['configuration_id'] != contract.get('configuration_id')):
            error(path + '.bom_item_id', 'REFERENCE_MISMATCH', 'BOM item must connect the requested configuration and exact component')
        if lot and (lot['component_id'] != stock.get('component_id') or lot['company'] != scope.get('company')):
            error(path + '.lot_id', 'REFERENCE_MISMATCH', 'Lot component and company must match the request scope')
        if bom and bom['population'] == 'unknown':
            pending(path + '.bom_item_id', 'POPULATION_UNCONFIRMED', 'BOM population is unknown; it is not zero demand')
        basis = stock.get('availability_basis')
        if basis not in ('on_hand_less_reserved_and_held', 'reported_available'):
            error(path + '.availability_basis', 'INVALID_BASIS', 'Use on_hand_less_reserved_and_held or reported_available')
        required_quantities = ('available_quantity',) if basis == 'reported_available' else (
            'on_hand_quantity', 'reserved_quantity', 'held_quantity')
        quantity_state = stock.get('quantity_state', 'known')
        if quantity_state not in ('known', 'unknown'):
            error(path + '.quantity_state', 'INVALID_STATE', 'Use known or unknown')
        elif quantity_state == 'unknown':
            pending(path + '.quantity_state', 'UNKNOWN_QUANTITY', 'Observation state remains unknown even when some counts are supplied')
        for key in required_quantities:
            if key not in stock:
                error(path + '.' + key, 'MISSING_FIELD', 'Explicit stock quantity or unknown null is required')
        for key in ('on_hand_quantity', 'reserved_quantity', 'held_quantity', 'available_quantity'):
            if key in stock:
                quantity(stock[key], path + '.' + key, unknown=quantity_state == 'unknown')
        disjoint = stock.get('deductions_disjoint')
        if disjoint is not None and type(disjoint) is not bool:
            error(path + '.deductions_disjoint', 'INVALID_STATE', 'Use true, false or null')
        elif basis == 'on_hand_less_reserved_and_held' and disjoint is not True:
            pending(path + '.deductions_disjoint', 'DEDUCTION_SCOPE_UNCONFIRMED', 'Reserved and held scopes must be confirmed before deduction')
        unit(stock.get('unit'), path + '.unit')
    pending('stocks', 'STOCK_COVERAGE_UNCONFIRMED', 'Listed stocks do not establish coverage of every required BOM component')

    if external_contents is not None and not isinstance(external_contents, dict):
        error('external_contents', 'INVALID_OBJECT', 'Host contents must map source references to bytes')
        external_contents = None
    source_ids = set()
    for i, raw in enumerate(rows(value.get('external_inputs'), 'external_inputs')):
        path = 'external_inputs[' + str(i) + ']'
        source = object_fields(raw, path, {'source_ref', 'sha256'}, ('source_ref', 'sha256'))
        ref = text(source.get('source_ref'), path + '.source_ref', reference_id=True)
        unique(ref, source_ids, path + '.source_ref')
        sha = source.get('sha256')
        if not isinstance(sha, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', sha):
            error(path + '.sha256', 'INVALID_HASH', 'Expected a SHA-256 hexadecimal digest')
        else:
            source['sha256'] = sha.lower()
            content = external_contents.get(ref) if external_contents is not None and ref else None
            if content is None:
                pending(path, 'SOURCE_BYTES_NOT_SUPPLIED', 'Digest syntax checked; original bytes were not supplied by the host')
            elif not isinstance(content, bytes):
                error(path, 'INVALID_SOURCE_BYTES', 'Original source content must be bytes')
            elif hashlib.sha256(content).hexdigest() != source['sha256']:
                error(path + '.sha256', 'HASH_MISMATCH', 'Digest does not match host-supplied source bytes')
            elif ref:
                verified_sources.add(ref)

    confirmation = value.get('confirmation_ref')
    if confirmation is None:
        pending('confirmation_ref', 'HUMAN_CONFIRMATION_REQUIRED', 'No host confirmation reference supplied')
    else:
        text(confirmation, 'confirmation_ref', reference_id=True)
        pending('confirmation_ref', 'HUMAN_CONFIRMATION_UNVERIFIED', 'An input reference alone cannot authenticate a human confirmation')

    if 'production_conditions' in value:
        conditions = object_fields(value['production_conditions'], 'production_conditions', {
            'resource_ref', 'available_at', 'setup_minutes', 'boards_per_hour', 'loss_quantity',
            'defect_quantity', 'rework_quantity', 'interruption_policy', 'origin'},
            ('resource_ref', 'available_at', 'setup_minutes', 'boards_per_hour', 'loss_quantity',
             'defect_quantity', 'rework_quantity', 'interruption_policy', 'origin'))
        text(conditions.get('resource_ref'), 'production_conditions.resource_ref', reference_id=True)
        time_value(conditions.get('available_at'), 'production_conditions.available_at')
        for key in ('setup_minutes', 'boards_per_hour', 'loss_quantity', 'defect_quantity', 'rework_quantity'):
            quantity(conditions.get(key), 'production_conditions.' + key, positive=key == 'boards_per_hour')
        if conditions.get('origin') != 'synthetic':
            error('production_conditions.origin', 'SYNTHETIC_CONDITIONS_REQUIRED', 'Only explicitly assumed operating conditions are supported in Task 01')
        if conditions.get('interruption_policy') != 'none_assumed':
            error('production_conditions.interruption_policy', 'INVALID_STATE', 'Task 01 reference condition is none_assumed')
    else:
        pending('production_conditions', 'PRODUCTION_CONDITIONS_UNCONFIRMED', 'Production conditions were not supplied')

    return {'valid': not errors, 'status': 'invalid' if errors else 'needs_confirmation',
        'normalized': normalized, 'errors': errors, 'unresolved': unresolved, 'warnings': warnings,
        'verified_references': sorted(verified), 'verified_external_inputs': sorted(verified_sources),
        'confirmation_state': 'unverified' if confirmation is not None else 'missing',
        'graph_integration_verified': False}
