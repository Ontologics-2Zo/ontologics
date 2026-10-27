"""Claim trace: serials -> boards -> production lines -> hold / candidate scopes (C 4th step).

Functions take plain graph nodes and return results plus candidate nodes; they never
approve or apply anything. trace_claim() reads the nodes through the existing
Service.lookup() so the same rules run on a confirmed data version.
Unknowns stay explicit: a serial without one matching board, or a board without one
production record, is reported for people instead of being guessed.
"""
from collections import defaultdict

from ..model import digest, instant
from .fixtures import batch_serials

MATCHED, NOT_FOUND, AMBIGUOUS = 'matched', 'not_found', 'ambiguous'
TRACED, NEEDS_HISTORY, CONFLICT = 'traced', 'needs_production_history', 'conflict'
TRACE_KINDS = ('ClaimIntake', 'ProductUnit', 'UnitProductionRecord', 'ProductionLine')


def by_type(nodes):
    grouped = defaultdict(list)
    for node in nodes:
        grouped[node.get('type')].append(node)
    return grouped


# ------------------------------------------------------------------ 4-1

def link_serials(intake, units, *, configuration_id=None):
    """Match each reported serial to exactly one ProductUnit by serial.

    configuration_id is the claim's product candidate (from the intake report);
    a matched board with another configuration gets a warning, not a silent pass.
    """
    by_serial = defaultdict(list)
    for unit in units:
        by_serial[unit.get('serial')].append(unit)
    links = []
    for serial in intake['reported_serials']:
        found = by_serial.get(serial, [])
        link = {'serial': serial, 'unit_id': None, 'match_state': NOT_FOUND, 'warnings': []}
        if len(found) == 1:
            unit = found[0]
            link.update(unit_id=unit['id'], match_state=MATCHED, configuration_id=unit.get('configuration_id'))
            if configuration_id and unit.get('configuration_id') != configuration_id:
                link['warnings'].append({'code': 'CONFIGURATION_MISMATCH',
                                         'message': f"{unit['id']} is {unit.get('configuration_id')}, claim names {configuration_id}"})
        elif len(found) > 1:
            link.update(match_state=AMBIGUOUS, candidates=sorted(u['id'] for u in found))
        links.append(link)
    return links


def prepare_quality_case(intake, links):
    """Build a QualityCase candidate only when a person confirmed the claim and every serial matched.

    Returns {'state', 'reason', 'node'}; node is None unless state == 'ready'.
    """
    if intake.get('intake_state') != 'confirmed':
        return {'state': 'waiting_for_confirmation', 'reason': 'claim intake is not confirmed by a person',
                'node': None}
    open_links = [link['serial'] for link in links if link['match_state'] != MATCHED]
    if open_links:
        return {'state': 'blocked', 'reason': 'serials need a person to resolve: ' + ', '.join(open_links),
                'node': None}
    node = {'id': 'quality-case:' + intake['external_claim_id'] + ':' + digest(intake['id'])[:12],
            'type': 'QualityCase', 'origin': intake['origin'], 'evidence': list(intake['evidence']),
            'intake_id': intake['id'], 'customer_name': intake['customer_name'],
            'reported_at': intake['reported_at'], 'reported_symptom': intake['reported_symptom'],
            'unit_ids': [link['unit_id'] for link in links]}
    return {'state': 'ready', 'reason': None, 'node': node}


# ------------------------------------------------------------------ 4-2

def trace_lines(unit_ids, records, lines=()):
    """Find each board's production line from its UnitProductionRecord; never infer a missing line."""
    by_unit = defaultdict(list)
    for record in records:
        by_unit[record.get('unit_id')].append(record)
    known_lines = {line['id']: line for line in lines}
    traces = []
    for unit_id in unit_ids:
        found = by_unit.get(unit_id, [])
        trace = {'unit_id': unit_id, 'state': NEEDS_HISTORY, 'production_line_id': None}
        if len(found) == 1:
            record = found[0]
            trace.update(state=TRACED, production_line_id=record['production_line_id'], record_id=record['id'],
                         produced_at=record['produced_at'], evidence=list(record.get('evidence', [])))
            line = known_lines.get(record['production_line_id'])
            if line:
                trace['line_code'] = line.get('line_code')
        elif len(found) > 1:
            trace.update(state=CONFLICT, record_ids=sorted(r['id'] for r in found))
        traces.append(trace)
    return traces


def line_summary(traces):
    """Lines that produced the traced boards, and boards that still need production history."""
    lines = sorted({t['production_line_id'] for t in traces if t['state'] == TRACED})
    return {'production_line_ids': lines,
            'single_line': lines[0] if len(lines) == 1 and all(t['state'] == TRACED for t in traces) else None,
            'unresolved_unit_ids': [t['unit_id'] for t in traces if t['state'] != TRACED]}


# ------------------------------------------------------------- DB entry

def trace_claim(service, actor, intake_id, *, version, configuration_id=None):
    """Run 4-1 and 4-2 on one confirmed data version through the existing read API."""
    def read(kind=None, node_id=None):
        result = service.lookup(actor, kind=kind, node_id=node_id, version=version)
        if not result['ok']:
            raise LookupError(result['errors'])
        return result['data']

    intake = read(node_id=intake_id)[0]
    if intake['type'] != 'ClaimIntake':
        raise ValueError('NOT_A_CLAIM_INTAKE: ' + intake_id)
    links = link_serials(intake, read('ProductUnit'), configuration_id=configuration_id)
    matched = [link['unit_id'] for link in links if link['match_state'] == MATCHED]
    traces = trace_lines(matched, read('UnitProductionRecord'), read('ProductionLine'))
    return {'version': version, 'intake_id': intake_id, 'links': links,
            'quality_case': prepare_quality_case(intake, links),
            'traces': traces, 'lines': line_summary(traces)}


# ------------------------------------------------------------ 4-3 / 4-4
# Temporary source: C operations record values. When the shared B types
# (FinishedGoodsStock, Shipment with board lists) exist, replace only these two
# adapters; stored_scope() and candidate_scope() keep the same inputs.

C_OPERATIONS_BASIS = 'c_operations_record'


def c_operations_stock(raw):
    """Stock observations from data/claim_response/operations.json (temporary, not a stock record)."""
    return [{'unit_ids': ['unit:' + s for s in batch_serials(b)], 'location': b['stock']['location'],
             'observed_at': b['stock']['observed_at'], 'complete': b['stock']['complete'] is True,
             'evidence': ['evidence:claim_response:batch:' + b['name']], 'basis': C_OPERATIONS_BASIS}
            for b in raw['batches'] if 'stock' in b]


def c_operations_shipments(raw):
    """Shipments from data/claim_response/operations.json (temporary, not a shipment record)."""
    return [{'unit_ids': ['unit:' + s for s in batch_serials(b)], 'departed_at': b['shipment']['departed_at'],
             'customer_name': b['shipment']['customer_name'],
             'external_document': b['shipment']['external_document'],
             'complete': b['shipment']['complete'] is True,
             'evidence': ['evidence:claim_response:batch:' + b['name']], 'basis': C_OPERATIONS_BASIS}
            for b in raw['batches'] if 'shipment' in b]


def _line_units(records, line_id):
    return {r['unit_id'] for r in records if r.get('production_line_id') == line_id}


def _scope_node(case, line_id, as_of, unit_ids, state, evidence):
    return {'id': case['id'].replace('quality-case:', 'quality-scope:', 1) + ':' + state,
            'type': 'QualityScope', 'origin': case['origin'], 'evidence': sorted(set(evidence)),
            'case_id': case['id'], 'production_line_id': line_id, 'as_of': as_of,
            'unit_ids': sorted(unit_ids), 'scope_state': state}


def stored_scope(case, line_id, records, stock, shipments=(), *, as_of=None):
    """4-3: boards from the claim's line that are in stock at as_of -> QualityScope(hold).

    Uses the latest stock observation at or before as_of. An incomplete list never
    becomes a scope; boards that departed between the observation and as_of are removed.
    """
    as_of = as_of or case['reported_at']
    moment = instant(as_of)
    seen = [o for o in stock if instant(o['observed_at']) <= moment]
    if not seen:
        return {'state': 'no_stock_observation', 'node': None, 'basis': None}
    latest_at = max(instant(o['observed_at']) for o in seen)
    latest = [o for o in seen if instant(o['observed_at']) == latest_at]
    basis = sorted({o['basis'] for o in latest})
    if not all(o['complete'] for o in latest):
        return {'state': 'stock_list_incomplete', 'node': None, 'basis': basis}
    in_stock = {u for o in latest for u in o['unit_ids']}
    departed = {u for s in shipments for u in s['unit_ids'] if latest_at < instant(s['departed_at']) <= moment}
    units = (in_stock & _line_units(records, line_id)) - departed - set(case['unit_ids'])
    if not units:
        return {'state': 'empty', 'node': None, 'basis': basis}
    evidence = [e for o in latest for e in o['evidence']]
    return {'state': 'ready', 'basis': basis, 'observed_at': latest_at.isoformat(),
            'departed_after_observation': sorted(departed & in_stock),
            'node': _scope_node(case, line_id, as_of, units, 'hold', evidence)}


def candidate_scope(case, line_id, records, shipments, *, as_of=None):
    """4-4: boards from the claim's line already shipped by as_of, minus the claimed boards -> QualityScope(candidate)."""
    as_of = as_of or case['reported_at']
    moment = instant(as_of)
    departed = [s for s in shipments if instant(s['departed_at']) <= moment]
    if not departed:
        return {'state': 'no_shipment', 'node': None, 'basis': None}
    basis = sorted({s['basis'] for s in departed})
    if not all(s['complete'] for s in departed):
        return {'state': 'shipment_list_incomplete', 'node': None, 'basis': basis}
    shipped = {u for s in departed for u in s['unit_ids']}
    units = (shipped & _line_units(records, line_id)) - set(case['unit_ids'])
    if not units:
        return {'state': 'empty', 'node': None, 'basis': basis}
    evidence = [e for s in departed for e in s['evidence']]
    return {'state': 'ready', 'basis': basis, 'node': _scope_node(case, line_id, as_of, units, 'candidate', evidence)}
