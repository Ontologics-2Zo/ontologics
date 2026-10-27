"""Synthetic C operations: demo boards, production lines and per-board production records.

Same pattern as demo.fixture_nodes(): one fictional source file, Evidence locators into it,
and candidate graph nodes. Board batches are written as serial ranges and expanded here.
Warehouse and delivery facts stay as evidence statements until the shared B types exist.
"""
import hashlib
import json
from datetime import timedelta

from ..model import ROOT, instant

OPERATIONS = ROOT / 'data/claim_response/operations.json'
SOURCE_ID = 'source:claim_response:operations'


def load_operations(path=OPERATIONS):
    return json.loads(path.read_text(encoding='utf-8'))


def batch_serials(batch):
    """'C-D', 80 -> ['C-D001', ..., 'C-D080']"""
    if not isinstance(batch.get('count'), int) or batch['count'] < 1 or batch['count'] > 999:
        raise ValueError('INVALID_BATCH: count must be 1-999 for three-digit serials')
    return [f"{batch['prefix']}{number:03d}" for number in range(1, batch['count'] + 1)]


def produced_times(batch):
    """per_day boards per day, interval_minutes apart, starting at first_produced_at each day."""
    first = instant(batch['first_produced_at'])
    per_day, interval = batch['per_day'], batch['interval_minutes']
    if per_day < 1 or interval < 1 or (per_day - 1) * interval >= 24 * 60:
        raise ValueError('INVALID_BATCH: one day of production must fit in 24 hours')
    return [(first + timedelta(days=i // per_day, minutes=(i % per_day) * interval)).isoformat()
            for i in range(batch['count'])]


def claim_response_nodes(path=OPERATIONS):
    """Return SourceDocument, Evidence, ProductionLine, ProductUnit and UnitProductionRecord nodes."""
    raw = load_operations(path)
    source = {'id': SOURCE_ID, 'type': 'SourceDocument', 'origin': 'synthetic', 'evidence': [],
              'url': 'urn:ontologics:claim_response:operations', 'path': path.relative_to(ROOT).as_posix(),
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'version': 'claim-demo-1',
              'license': 'Project demonstration data', 'collected_at': raw['authored_at'],
              'kind': 'synthetic_operations'}

    def evidence(nid, locator, description):
        return {'id': nid, 'type': 'Evidence', 'origin': 'synthetic', 'evidence': [], 'source_id': SOURCE_ID,
                'locator': locator, 'description': description}

    def node(kind, nid, ev, **fields):
        return {'id': nid, 'type': kind, 'origin': 'synthetic', 'evidence': [ev], **fields}

    line_evidence = evidence('evidence:claim_response:lines', '/lines',
                             'Fictional demonstration production lines; not OLIMEX operating records.')
    nodes = [source, line_evidence]
    lines = {}
    for line in raw['lines']:
        lines[line['line_code']] = line['id']
        nodes.append(node('ProductionLine', line['id'], line_evidence['id'], company=raw['company'],
                          line_code=line['line_code'], name=line['name']))
    for number, batch in enumerate(raw['batches']):
        ev = evidence('evidence:claim_response:batch:' + batch['name'], f'/batches/{number}',
                      'Fictional demonstration board batch. ' + batch['statement'])
        nodes.append(ev)
        line_id = lines[batch['line_code']]
        for serial, produced_at in zip(batch_serials(batch), produced_times(batch)):
            unit_id = 'unit:' + serial
            nodes.append(node('ProductUnit', unit_id, ev['id'], serial=serial, company=raw['company'],
                              configuration_id=raw['configuration_id']))
            nodes.append(node('UnitProductionRecord', 'production:' + serial, ev['id'], unit_id=unit_id,
                              production_line_id=line_id, produced_at=produced_at))
    return nodes
