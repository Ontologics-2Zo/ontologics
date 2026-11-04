"""Read-only adapter for the one-bundle Task 01 planning reference mapping.

The caller supplies a trusted RequestSession and original bytes, not an Agent
tool. Only public get_draft/lookup are called. This is neither an extractor nor
an approval system; it does not establish production feasibility or real stock.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..model import canonical, digest, instant
from .contracts import validate_planning_context

# Explicit host-maintained mappings, never inferred from IDs or caller paths.
_SOURCES = {
    'input:planning:contract': ('source:planning:contract:v1', 'data/production_planning/contract.txt'),
    'input:planning:stock': ('source:planning:stock:v1', 'data/production_planning/stock.json'),
    'input:planning:lots:v1': ('source:planning:lots:v1', 'data/production_planning/lot_reference_v1.json'),
}
_CONTRACT_SHA = '6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962'
_ITEM_ID = 'contract-item:planning:001'
_CONTRACT_ID = 'contract:planning:001'
_PARTIES_EVIDENCE = 'evidence:planning:contract:v1:parties'
_ITEM_EVIDENCE = 'evidence:planning:contract:v1:item-001'
# The file is deliberately absent until supported supplementation is obtained.
# Trusted tests replace this mapping with an explicitly separate test source.
_DELIVERY_SOURCE = dict(source_ref='input:planning:delivery:v1',
                        source_id='source:planning:delivery:v1',
                        path='data/production_planning/contract_delivery_v1.json',
                        evidence_id='evidence:planning:delivery:v1:round-001', locator='/')
_STOCK_FIELDS = ('on_hand_quantity', 'reserved_quantity', 'held_quantity',
                 'available_quantity', 'deductions_disjoint')
_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})')


def validate_registered_context(session, context: dict, *, external_contents: dict | None = None,
                                registration_ref: dict | None = None) -> dict:
    """Return untouched public response envelopes plus a separate local report.

    registration_ref is {draft_id, draft_version}; context.data_version must be
    the exact applied_version. Existing multiple approved bundles are outside
    this initial mapping. Missing mappings prevent verification, never fallback.
    """
    report = validate_planning_context(context, external_contents=external_contents)
    result = dict(registration_response=None, lookup_response=None, report=report)

    def error(path, code, message):
        report['errors'].append(dict(path=path, code=code, message=message))
        report.update(valid=False, status='invalid', graph_integration_verified=False)

    def pending(path, code, message):
        report['unresolved'].append(dict(path=path, code=code, message=message))

    if not report['valid']:
        return result
    if registration_ref is None:
        pending('registration_ref', 'APPROVAL_REFERENCE_REQUIRED', 'Exact applied draft reference required')
        return result
    if (not isinstance(registration_ref, dict) or set(registration_ref) != {'draft_id', 'draft_version'}
            or not isinstance(registration_ref.get('draft_id'), str) or not registration_ref['draft_id'].strip()
            or any(c.isspace() for c in registration_ref['draft_id'])
            or type(registration_ref.get('draft_version')) is not int or registration_ref['draft_version'] < 1):
        error('registration_ref', 'INVALID_REGISTRATION_REFERENCE', 'Nonempty draft_id and positive integer draft_version required')
        return result

    version = context['data_version']
    registration = session.call('get_draft', dict(draft_id=registration_ref['draft_id'],
                                                 version=registration_ref['draft_version']))
    result['registration_response'] = registration
    if not registration['ok']:
        result['report'] = None
        return result
    draft = registration['data']
    if (not isinstance(draft, dict) or draft.get('id') != registration_ref['draft_id']
            or type(draft.get('version')) is not int or draft['version'] != registration_ref['draft_version']):
        error('registration_ref', 'REGISTRATION_SCOPE_MISMATCH', 'Response must identify the exact requested draft version')
        return result
    if draft.get('status') != 'applied':
        error('registration_ref', 'REGISTRATION_NOT_APPLIED', 'Exact draft has not been applied')
        return result
    if type(draft.get('applied_version')) is not int or draft['applied_version'] != version:
        error('data_version', 'REFERENCE_VERSION_MISMATCH', 'Request version must equal draft applied_version')
        return result
    lookup = session.call('lookup', {'version': version})
    result['lookup_response'] = lookup
    if not lookup['ok']:
        result['report'] = None
        return result
    report = validate_planning_context(context,
        reference={'data_version': lookup['data_version'], 'nodes': lookup['data']},
        external_contents=external_contents)
    result['report'] = report
    if not report['valid']:
        return result
    index = {n['id']: n for n in lookup['data']}
    value = report['normalized']
    item, scope = value['contract_item'], value['stock_scope']
    required_ids = set()
    mapping_complete = True

    def incomplete(path, message, code='REGISTERED_CONTEXT_INCOMPLETE'):
        nonlocal mapping_complete
        mapping_complete = False
        pending(path, code, message)

    def node(nid, kind, path):
        n = index.get(nid)
        if n is None or n['type'] != kind:
            error(path, 'INVALID_REFERENCE', 'Expected registered ' + kind)
            return None
        required_ids.add(nid)
        return n

    def equal(actual, expected, path):
        # Python equality would accept True == 1 and 100.0 == 100.
        if canonical(actual) != canonical(expected):
            error(path, 'REGISTERED_VALUE_MISMATCH', 'Registered value differs from request or mapped source')

    def compare_time(actual, expected, path):
        try:
            if not (isinstance(actual, str) and _TIME.fullmatch(actual)
                    and isinstance(expected, str) and _TIME.fullmatch(expected)):
                raise ValueError('Seconds, at most six fractional digits and explicit UTC offset required')
            if instant(actual) != instant(expected):
                error(path, 'REGISTERED_VALUE_MISMATCH', 'Registered instant differs from request or source')
        except (ValueError, TypeError, OverflowError) as exc:
            error(path, 'INVALID_TIME', str(exc))

    inputs = {entry['source_ref']: entry['sha256'] for entry in value['external_inputs']}
    known_sources = set(_SOURCES) | {'input:planning:production'}
    if _DELIVERY_SOURCE is not None:
        known_sources.add(_DELIVERY_SOURCE['source_ref'])
    for ref in inputs.keys() - known_sources:
        incomplete('external_inputs', 'No trusted source mapping for: ' + ref, 'SOURCE_MAPPING_UNCONFIRMED')

    def source(ref, sid, path):
        raw = external_contents.get(ref) if isinstance(external_contents, dict) else None
        if ref not in report['verified_external_inputs'] or not isinstance(raw, bytes):
            incomplete('external_inputs', 'Mapped source bytes and request hash are required: ' + ref,
                       'SOURCE_MAPPING_UNCONFIRMED')
            return None
        s = node(sid, 'SourceDocument', 'external_inputs.' + ref)
        if s:
            equal(s['path'], path, 'external_inputs.' + ref + '.path')
            equal(s['sha256'], inputs.get(ref), 'external_inputs.' + ref + '.sha256')
            equal(s['version'], inputs.get(ref), 'external_inputs.' + ref + '.version')
            equal(s['origin'], 'synthetic', 'external_inputs.' + ref + '.origin')
        return raw

    def evidence(fact, eid, sid, locator, path):
        if fact is None:
            return
        equal(fact['evidence'], [eid], path + '.evidence')
        e = node(eid, 'Evidence', path + '.evidence')
        if e:
            for k, expected in [('source_id', sid), ('locator', locator), ('origin', 'synthetic')]:
                equal(e[k], expected, path + '.evidence.' + k)

    def json_source(raw, path):
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('origin') != 'synthetic':
                raise ValueError('Mapped source must be an explicitly synthetic object')
            return data
        except (ValueError, UnicodeError) as exc:
            error(path, 'INVALID_SOURCE_MAPPING', str(exc))
            return None

    mapped = {ref: source(ref, sid, path) for ref, (sid, path) in _SOURCES.items()}
    if mapped['input:planning:contract'] is not None:
        # The human-inspected line mapping below is valid only for these bytes.
        equal(hashlib.sha256(mapped['input:planning:contract']).hexdigest(), _CONTRACT_SHA,
              'external_inputs.input:planning:contract.sha256')
    contract = node(_CONTRACT_ID, 'Contract', 'contract_item.contract_id')
    registered_item = node(item['id'], 'ContractItem', 'contract_item.id')
    equal(item['id'], _ITEM_ID, 'contract_item.id')
    if contract:
        equal(contract['company'], 'Planning Reference Company', 'contract.company')
        equal(contract['company'], scope['company'], 'stock_scope.company')
        equal(contract['customer_name'], 'Reference Customer (가정)', 'contract.customer_name')
        evidence(contract, _PARTIES_EVIDENCE, _SOURCES['input:planning:contract'][0], 'lines 4-5', 'contract')
    if registered_item:
        for k, expected in [('contract_id', _CONTRACT_ID), ('configuration_id', item['configuration_id']),
                            ('quantity', item['ordered_quantity']), ('unit', item['unit'])]:
            equal(registered_item[k], expected, 'contract_item.' + k)
        # Original contract's explicit configuration/quantity mapping.
        equal(registered_item['configuration_id'], 'config:esp32-poe:L:WROOM', 'contract_item.configuration_id')
        equal(registered_item['quantity'], 100, 'contract_item.quantity')
        evidence(registered_item, _ITEM_EVIDENCE, _SOURCES['input:planning:contract'][0],
                 'lines 3, 8-13', 'contract_item')

    stock_data = json_source(mapped['input:planning:stock'], 'external_inputs.stock')
    lot_data = json_source(mapped['input:planning:lots:v1'], 'external_inputs.lots')
    stock_rows = stock_data.get('stocks') if stock_data else None
    lot_rows = lot_data.get('lots') if lot_data else None
    if not (isinstance(stock_rows, list) and all(isinstance(s, dict) for s in stock_rows)
            and isinstance(lot_rows, list) and all(isinstance(s, dict) for s in lot_rows)):
        incomplete('stocks', 'Mapped stock and lot source rows are required', 'SOURCE_MAPPING_UNCONFIRMED')
    else:
        for i, stock in enumerate(value['stocks']):
            path = f'stocks[{i}]'
            rows = [(j, s) for j, s in enumerate(stock_rows) if s.get('id') == stock['id']]
            lots = [(j, s) for j, s in enumerate(lot_rows) if s.get('id') == stock['lot_id']]
            if len(rows) != 1 or len(lots) != 1:
                incomplete(path, 'Exact source row mapping required', 'SOURCE_MAPPING_UNCONFIRMED')
                continue
            row_index, raw_stock = rows[0]
            lot_index, raw_lot = lots[0]
            snapshot = node(stock['id'], 'ComponentStockSnapshot', path + '.id')
            lot = node(stock['lot_id'], 'ComponentLot', path + '.lot_id')
            if lot:
                for k in ('company', 'supplier', 'lot_code', 'component_id'):
                    equal(lot[k], raw_lot.get(k), path + '.lot.' + k)
                equal(lot['component_id'], stock['component_id'], path + '.component_id')
                equal(lot['company'], scope['company'], path + '.lot.company')
                evidence(lot, f'evidence:planning:lots:v1:row-{lot_index}', _SOURCES['input:planning:lots:v1'][0],
                         f'/lots/{lot_index}', path + '.lot')
            if not snapshot:
                continue
            for k in ('lot_id', 'unit', 'availability_basis'):
                equal(snapshot[k], stock[k], path + '.' + k)
            equal(snapshot['quantity_state'], stock.get('quantity_state', 'known'), path + '.quantity_state')
            equal(snapshot['location'], scope['location'], path + '.location')
            compare_time(snapshot['as_of'], scope['as_of'], path + '.as_of')
            for k in _STOCK_FIELDS:
                if (k in stock) != (k in snapshot):
                    incomplete(path + '.' + k, 'Optional field presence must match request and registered observation')
                elif k in stock:
                    equal(snapshot[k], stock[k], path + '.' + k)
            # Also compare to the original observation, not just to a matching
            # request/graph pair that could both repeat a wrong value.
            equal(dict(stock, quantity_state=stock.get('quantity_state', 'known')),
                  dict(raw_stock, quantity_state=raw_stock.get('quantity_state', 'known')), path + '.source_row')
            raw_scope = stock_data.get('stock_scope')
            if not isinstance(raw_scope, dict):
                error('stock_scope.source', 'INVALID_SOURCE_MAPPING', 'Source stock_scope is required')
            else:
                for k in ('company', 'location'):
                    equal(scope[k], raw_scope.get(k), 'stock_scope.source.' + k)
                compare_time(scope['as_of'], raw_scope.get('as_of'), 'stock_scope.source.as_of')
            evidence(snapshot, f'evidence:planning:stock:v1:row-{row_index}', _SOURCES['input:planning:stock'][0],
                     f'/stocks/{row_index}, /stock_scope', path)

    rounds = [n for n in index.values() if n['type'] == 'DeliveryRound' and n['contract_item_id'] == item['id']]
    due = item.get('delivery_due')
    if len(rounds) != 1:
        incomplete('contract_item.delivery_due', 'A requires exactly one full delivery round; no selection or aggregation')
    if due is None or item.get('delivery_due_precision') != 'datetime':
        incomplete('contract_item.delivery_due', 'Date-only/missing due date and absent destination cannot establish a round')
    if len(rounds) == 1:
        delivery = rounds[0]
        required_ids.add(delivery['id'])
        if delivery['quantity'] != item['ordered_quantity']:
            incomplete('delivery_round.quantity', 'A supports only a single full-quantity round')
        equal(delivery['unit'], item['unit'], 'delivery_round.unit')
        if due is not None and item.get('delivery_due_precision') == 'datetime':
            compare_time(delivery['due_at'], due, 'contract_item.delivery_due')
        d = _DELIVERY_SOURCE
        if d is None:
            incomplete('delivery_round', 'Trusted delivery source mapping is absent', 'SOURCE_MAPPING_UNCONFIRMED')
        else:
            raw = source(d['source_ref'], d['source_id'], d['path'])
            supplement = json_source(raw, 'delivery_round.source')
            if supplement is not None:
                if not isinstance(supplement.get('notice'), str) or not supplement['notice'].strip():
                    error('delivery_round.source.notice', 'INVALID_SOURCE_MAPPING', 'Explicit synthetic supplementation notice required')
                for k, expected in [('contract_source_ref', 'input:planning:contract'),
                                    ('contract_sha256', _CONTRACT_SHA), ('contract_item_id', item['id']),
                                    ('round_id', delivery['id']), ('quantity', delivery['quantity']),
                                    ('unit', delivery['unit']), ('due_basis', 'arrival'),
                                    ('destination', delivery['destination'])]:
                    equal(supplement.get(k), expected, 'delivery_round.source.' + k)
                compare_time(delivery['due_at'], supplement.get('due_at'), 'delivery_round.source.due_at')
            evidence(delivery, d['evidence_id'], d['source_id'], d['locator'], 'delivery_round')
    # Strict time precision is adapter-only; the original candidate function
    # and B's common instant semantics remain unchanged.
    compare_time(scope['as_of'], scope['as_of'], 'stock_scope.as_of')
    if due is not None and item.get('delivery_due_precision') == 'datetime':
        compare_time(due, due, 'contract_item.delivery_due')

    operations = draft.get('operations')
    puts = {}
    if not isinstance(operations, list):
        error('registration_ref', 'REGISTRATION_SCOPE_MISMATCH', 'Applied operations are required')
    else:
        if digest(operations) != draft.get('content_hash'):
            error('registration_ref', 'REGISTRATION_SCOPE_MISMATCH', 'Applied operations hash differs')
        for op in operations:
            n = op.get('node') if isinstance(op, dict) else None
            if (not isinstance(op, dict) or op.get('op') != 'put' or not isinstance(n, dict)
                    or not isinstance(n.get('id'), str) or n['id'] in puts):
                error('registration_ref', 'REGISTRATION_SCOPE_MISMATCH', 'One bundle of unique put nodes required')
                continue
            puts[n['id']] = n
        for nid in sorted(required_ids):
            if nid not in puts or canonical(puts[nid]) != canonical(index[nid]):
                error('registration_ref', 'REGISTRATION_SCOPE_MISMATCH', 'Applied put differs from snapshot or is absent: ' + nid)

    if report['valid'] and mapping_complete:
        report['graph_integration_verified'] = True
        report['unresolved'] = [r for r in report['unresolved'] if r['code'] != 'EXTENSION_INTEGRATION_PENDING']
    return result
