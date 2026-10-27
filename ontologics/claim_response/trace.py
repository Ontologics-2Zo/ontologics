"""Claim trace: customer serials -> boards -> production lines (C 4th step).

Functions take plain graph nodes and return results plus candidate nodes; they never
approve or apply anything. trace_claim() reads the nodes through the existing
Service.lookup() so the same rules run on a confirmed data version.
Unknowns stay explicit: a serial without one matching board, or a board without one
production record, is reported for people instead of being guessed.
"""
from collections import defaultdict

from ..model import digest

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
