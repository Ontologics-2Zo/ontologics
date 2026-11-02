"""B request state and transition rules reused by common graph registration.

Source/request content agreement and free-text demand relationships still need
trusted host and human checks. Pure functions never approve or persist data;
the shared service supplies its locked base version and before/after changes.
"""
from datetime import date
import re

from ..model import instant, validate_graph
from .material_request_schema import INTEGER_MINIMUMS, REQUEST_TYPE


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _integer(value, minimum):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _ids(value):
    return (isinstance(value, list) and bool(value)
            and all(_text(item) for item in value) and len(set(value)) == len(value))


def validate_material_requests(index, error):
    """Check B business rules; common validation owns shape, refs and evidence."""
    for nid, node in index.items():
        if node.get('type') != REQUEST_TYPE:
            continue
        for field, minimum in INTEGER_MINIMUMS[REQUEST_TYPE].items():
            if not _integer(node.get(field), minimum):
                error(nid, 'INVALID_QUANTITY' if field == 'quantity' else 'INVALID_FIELD_TYPE', field)
        if node.get('unit') != 'piece':
            error(nid, 'UNSUPPORTED_UNIT', 'Additional normal finished pieces only')

        evidence = node.get('evidence')
        baseline = node.get('baseline_plan_evidence_id')
        if _ids(evidence) and isinstance(baseline, str):
            if baseline not in evidence:
                error(nid, 'INVALID_EVIDENCE', 'Baseline Evidence must also appear in evidence')
            if not any(eid != baseline for eid in evidence):
                error(nid, 'REQUEST_EVIDENCE_REQUIRED', 'Request source evidence is required in addition to the baseline')

        def reference(field, kind, owner=node):
            value = owner.get(field)
            target = index.get(value) if isinstance(value, str) else None
            return target if target is not None and target.get('type') == kind else None

        item = reference('contract_item_id', 'ContractItem')
        delivery = reference('delivery_round_id', 'DeliveryRound')
        round_item = reference('contract_item_id', 'ContractItem', delivery) if delivery else None
        for related in (item, round_item):
            if related is not None and related.get('configuration_id') != node.get('configuration_id'):
                error(nid, 'CONFIGURATION_MISMATCH', 'Request and related item products must agree')
        if delivery and item and delivery.get('contract_item_id') != item['id']:
            error(nid, 'CONTRACT_ITEM_MISMATCH', 'Round must belong to the specified item')

        if 'recipient_role' in node and node['recipient_role'] != 'materials':
            error(nid, 'INVALID_ENUM', 'recipient_role')
        enums = {
            'need_mode': ('inherit_round', 'explicit', 'unknown'),
            'conditions_state': ('unchanged', 'changed', 'unknown'),
            'readiness': ('ready', 'needs_supplementation'),
        }
        for field, choices in enums.items():
            if node.get(field) not in choices:
                error(nid, 'INVALID_ENUM', field)
        mode = node.get('need_mode')
        date_fields = {'need_basis', 'need_date', 'need_at'} & set(node)
        if mode in ('inherit_round', 'unknown') and date_fields:
            error(nid, 'CONFLICTING_NEED', 'Inherited/unknown need must not also specify a separate date')
        if mode == 'inherit_round':
            if delivery is None:
                error(nid, 'DELIVERY_ROUND_REQUIRED', 'Inherited need requires a registered round')
            else:
                if delivery.get('due_basis') != 'arrival':
                    error(nid, 'UNSUPPORTED_DUE_BASIS', 'Existing rounds support arrival only')
                try:
                    instant(delivery.get('due_at'))
                except (TypeError, ValueError) as exc:
                    error(nid, 'INVALID_TIME', 'Inherited due_at: ' + str(exc))
                if not _text(node.get('destination', delivery.get('destination'))):
                    error(nid, 'DESTINATION_REQUIRED', 'Customer arrival requires a destination')
        if mode == 'explicit':
            if node.get('need_basis') not in ('production_complete', 'dispatch', 'arrival'):
                error(nid, 'INVALID_ENUM', 'need_basis')
            if ('need_date' in node) == ('need_at' in node):
                error(nid, 'INVALID_NEED_DATE', 'Exactly one of need_date and need_at is required')
            if 'need_date' in node:
                value = node['need_date']
                try:
                    if not isinstance(value, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
                        raise ValueError('Calendar date YYYY-MM-DD required')
                    date.fromisoformat(value)
                except ValueError as exc:
                    error(nid, 'INVALID_NEED_DATE', str(exc))
            if 'need_at' in node:
                try:
                    instant(node['need_at'])
                except (TypeError, ValueError) as exc:
                    error(nid, 'INVALID_TIME', str(exc))
            destination = node.get('destination', delivery.get('destination') if delivery else None)
            if node.get('need_basis') == 'arrival' and not _text(destination):
                error(nid, 'DESTINATION_REQUIRED', 'Customer arrival requires a destination')

        if node.get('conditions_state') == 'changed' and not _text(node.get('conditions_note')):
            error(nid, 'CONDITIONS_NOTE_REQUIRED', 'Describe the changed conditions')
        if node.get('readiness') == 'ready':
            if (not _text(baseline) or mode == 'unknown' or node.get('conditions_state') == 'unknown'):
                error(nid, 'REQUEST_NOT_READY', 'Baseline, need and conditions must be confirmed')
        elif node.get('readiness') == 'needs_supplementation' and not _text(node.get('supplementation_reason')):
            error(nid, 'SUPPLEMENTATION_REASON_REQUIRED', 'Describe what must be confirmed')


def validate_request_candidate(nodes):
    """Validate a full candidate using the registered common graph contract.

    Caller supplies one full candidate state, not all revisions of the same ID.
    Common validation still checks source files/hashes and existing node types.
    No DB, approved version pinning or transaction is created here.
    """
    errors = []

    def error(nid, code, detail):
        errors.append({'node_id': nid, 'code': code, 'detail': detail})

    if not isinstance(nodes, list) or len(nodes) > 20000:
        error(None, 'INVALID_GRAPH_SIZE', 'A list with at most 20000 nodes is required')
        return {'valid': False, 'errors': errors, 'warnings': [], 'scope': 'b_request_candidate'}
    index = {}
    for node in nodes:
        if not isinstance(node, dict) or not _text(node.get('id')):
            error(None, 'INVALID_NODE', 'Every node needs a nonempty string id')
            continue
        if node['id'] in index:
            error(node['id'], 'DUPLICATE_ID', 'Only one revision per candidate state')
        index[node['id']] = node
    common = validate_graph(nodes)
    errors.extend(common['errors'])
    return {'valid': not errors, 'errors': errors, 'warnings': common['warnings'],
            'scope': 'b_request_candidate'}


def validate_request_transition(previous, current, *, expected_request_revision):
    """Check revision replacement only, after candidate shape validation.

    This pure comparison neither implements idempotency nor protects concurrent
    writers. A trusted registration path must repeat checks under the common
    transaction lock before preparing a draft.
    """
    errors = []

    def error(code, detail):
        errors.append({'node_id': current.get('id') if isinstance(current, dict) else None,
                       'code': code, 'detail': detail})

    if (not isinstance(current, dict) or current.get('type') != REQUEST_TYPE
            or (previous is not None and (not isinstance(previous, dict) or previous.get('type') != REQUEST_TYPE))):
        error('INVALID_REQUEST', 'Expected request nodes, with None for a new request')
    elif not _integer(expected_request_revision, 0):
        error('INVALID_REQUEST_REVISION', 'Expected revision must be a nonnegative integer')
    else:
        actual = 0 if previous is None else previous.get('request_revision')
        if not _integer(actual, 0 if previous is None else 1) or actual != expected_request_revision:
            error('STALE_REQUEST_REVISION', 'Expected revision does not match the previous request')
        if (not _integer(current.get('request_revision'), 1)
                or current['request_revision'] != expected_request_revision + 1):
            error('INVALID_REQUEST_REVISION', 'New revision must increment the expected revision by one')
        if previous is not None:
            if current.get('id') != previous.get('id'):
                error('REQUEST_ID_MISMATCH', 'An amendment keeps the same request ID')
            scope_fields = ('configuration_id', 'contract_item_id', 'baseline_plan_evidence_id',
                            'need_mode', 'delivery_round_id', 'need_basis', 'need_date', 'need_at')
            # Only facts still unknown in the approved request may be filled.
            # Changing readiness must never unlock an already confirmed scope.
            supplement_fields = set()
            if previous.get('readiness') == 'needs_supplementation':
                if previous.get('baseline_plan_evidence_id') is None:
                    supplement_fields.add('baseline_plan_evidence_id')
                if previous.get('need_mode') == 'unknown':
                    supplement_fields.update(('need_mode', 'need_basis', 'need_date', 'need_at'))
                    if previous.get('delivery_round_id') is None:
                        supplement_fields.add('delivery_round_id')
            if any(current.get(field) != previous.get(field)
                   for field in scope_fields if field not in supplement_fields):
                error('REQUEST_SCOPE_CHANGED', 'Different product, contract, baseline or need scope requires a separate request')
            if not _ids(current.get('evidence')) or not _ids(previous.get('evidence')):
                error('INVALID_EVIDENCE', 'Validate candidate evidence before transition checks')
            elif not set(current['evidence']) - set(previous['evidence']):
                error('NEW_EVIDENCE_REQUIRED', 'An amendment needs new source evidence')
    return {'valid': not errors, 'errors': errors, 'scope': 'b_request_transition'}


def validate_request_changes(changes, base_version):
    """Check only changed requests, against the shared transaction's base.

    Unchanged historical requests keep their original reference_data_version.
    Callers must retain shared permission, lock, exact-draft and stale-base checks.
    """
    errors = []
    for change in changes:
        current, previous = change['after'], change['before']
        if current.get('type') != REQUEST_TYPE:
            continue
        expected = previous.get('request_revision') if previous is not None else 0
        errors.extend(validate_request_transition(previous, current,
                      expected_request_revision=expected)['errors'])
        reference_version = current.get('reference_data_version')
        if not _integer(reference_version, 0) or reference_version != base_version:
            errors.append({'node_id': current.get('id'), 'code': 'REFERENCE_VERSION_MISMATCH',
                           'detail': 'Changed requests must reference the actual draft base version'})
    return {'valid': not errors, 'errors': errors, 'scope': 'b_request_changes'}
