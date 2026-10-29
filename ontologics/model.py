"""Executable graph contract; database writes always validate the entire candidate graph."""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 'graph-0.1'
ROOT = Path(__file__).resolve().parents[1]
# Required fields and typed relationships. T-box is generated from these definitions.
SCHEMA = {
    'SourceDocument': (['url', 'sha256', 'path', 'version', 'license', 'collected_at'], {}),
    'Evidence': (['source_id', 'locator', 'description'], {'source_id': 'SourceDocument'}),
    'ProductModel': (['name', 'manufacturer'], {}),
    'ProductRevision': (['model_id', 'revision'], {'model_id': 'ProductModel'}),
    'Configuration': (['revision_id', 'variant', 'name'], {'revision_id': 'ProductRevision'}),
    'ComponentModel': (['name', 'identity_key'], {}),
    'BOMItem': (['configuration_id', 'component_id', 'quantity', 'unit', 'positions', 'population', 'raw_value'], {'configuration_id': 'Configuration', 'component_id': 'ComponentModel'}),
    'ProductUnit': (['serial', 'company', 'configuration_id'], {'configuration_id': 'Configuration'}),
    'ComponentLot': (['company', 'supplier', 'lot_code', 'component_id'], {'component_id': 'ComponentModel'}),
    'Installation': (['unit_id', 'lot_id', 'position', 'installed_at'], {'unit_id': 'ProductUnit', 'lot_id': 'ComponentLot'}),
    'HistoryCoverage': (['unit_id', 'start', 'end', 'complete'], {'unit_id': 'ProductUnit'}),
    'InventoryMovement': (['lot_id', 'quantity', 'occurred_at', 'movement'], {'lot_id': 'ComponentLot'}),
}
REAL = {'ProductModel', 'ProductRevision', 'Configuration', 'ComponentModel', 'BOMItem'}
SYNTHETIC = {'ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage', 'InventoryMovement'}
COMMON_FIELDS = {'id', 'type', 'origin', 'evidence'}
OPTIONAL_FIELDS = {
    'SourceDocument': {'kind', 'product', 'revision', 'variant', 'sheet_name', 'verification',
                       'metadata_warning', 'git_blob_sha1'},
    'ProductModel': {'specifications'},
    'Configuration': {'identity_notes', 'population_notes'},
    'ComponentModel': {'package', 'identity_scope'},
    'Installation': {'removed_at', 'confirmed_until', 'conflict'},
}
# Only explicit contract fields are accepted: operational examples cannot grow
# fault causes, inspection results or operating-time claims through free fields.
ALLOWED_FIELDS = {kind: COMMON_FIELDS | set(required) | OPTIONAL_FIELDS.get(kind, set())
                  for kind, (required, _) in SCHEMA.items()}
SPECIFICATION_FIELDS = {'scope', 'ethernet_interface', 'pcb_dimensions_mm', 'features', 'evidence'}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _positive_number(value):
    # Avoid converting arbitrarily large JSON integers to floats.
    return (isinstance(value, int) and not isinstance(value, bool) and value > 0) or (
        isinstance(value, float) and math.isfinite(value) and value > 0)


def _string_list(value, *, allow_empty=False):
    return (isinstance(value, list) and (allow_empty or bool(value))
            and all(_text(item) for item in value) and len(set(value)) == len(value))

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def instant(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('Explicit time and UTC offset are required; date-only facts need supplementation')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Timezone offset is required')
    return parsed

def validate_graph(nodes):
    errors, warnings = [], []
    def error(node, code, detail):
        errors.append({'node_id': node, 'code': code, 'detail': detail})
    if not isinstance(nodes, list) or len(nodes) > 20000:
        error(None, 'INVALID_GRAPH_SIZE', 'A list with at most 20000 nodes is required')
        return {'valid': False, 'errors': errors, 'warnings': []}
    index = {}
    for n in nodes:
        if not isinstance(n, dict) or not _text(n.get('id')):
            error(None, 'INVALID_NODE', 'Every node needs a nonempty string id'); continue
        if n['id'] in index:
            error(n['id'], 'DUPLICATE_ID', 'IDs must be unique')
        index[n['id']] = n
    identities = {}
    for nid, n in index.items():
        kind = n.get('type')
        if not isinstance(kind, str) or kind not in SCHEMA:
            error(nid, 'UNKNOWN_TYPE', str(kind)); continue
        extra = set(n) - ALLOWED_FIELDS[kind]
        if extra:
            error(nid, 'UNKNOWN_FIELD', ', '.join(sorted(str(k) for k in extra)))
        if n.get('origin') not in ('real', 'synthetic'):
            error(nid, 'ORIGIN_REQUIRED', 'real or synthetic')
        if kind in REAL and n.get('origin') != 'real':
            error(nid, 'REAL_DESIGN_REQUIRED', 'Design/specification/BOM must use real sources')
        if kind in SYNTHETIC and n.get('origin') != 'synthetic':
            error(nid, 'DEMO_OPERATIONS_ONLY', 'No OLIMEX internal operations are supplied')
        required, refs = SCHEMA[kind]
        for key in [*COMMON_FIELDS, *required]:
            if key not in n or n[key] is None or n[key] == '':
                error(nid, 'MISSING_FIELD', key)
        # Required and optional scalar fields are strings unless explicitly typed
        # below. Checking shape before lookup/set operations makes validation total
        # for malformed JSON values instead of raising TypeError midway through it.
        structured = {'evidence', 'quantity', 'positions', 'complete', 'conflict', 'specifications'}
        for key in ALLOWED_FIELDS[kind] - structured:
            if key in n and not (key in ('removed_at', 'confirmed_until') and n[key] is None) and not _text(n[key]):
                error(nid, 'INVALID_FIELD_TYPE', key + ' must be nonempty text')
        for key, target_type in refs.items():
            target = index.get(n.get(key)) if isinstance(n.get(key), str) else None
            if target is None or target.get('type') != target_type:
                error(nid, 'INVALID_REFERENCE', key + ' -> ' + target_type)
        evidence = n.get('evidence', [])
        if not _string_list(evidence, allow_empty=True):
            error(nid, 'INVALID_EVIDENCE', 'evidence must be a list of unique nonempty IDs'); evidence = []
        if kind not in ('SourceDocument', 'Evidence') and not evidence:
            error(nid, 'EVIDENCE_REQUIRED', 'Every factual node needs source locators')
        for eid in evidence:
            e = index.get(eid, {})
            if e.get('type') != 'Evidence':
                error(nid, 'EVIDENCE_NOT_FOUND', eid)
            elif e.get('origin') != n.get('origin'):
                error(nid, 'EVIDENCE_ORIGIN_MISMATCH', eid)
        if kind == 'Evidence':
            source = index.get(n.get('source_id'), {}) if isinstance(n.get('source_id'), str) else {}
            if source.get('origin') != n.get('origin'):
                error(nid, 'SOURCE_ORIGIN_MISMATCH', 'Evidence must retain source origin')
            if not _text(n.get('locator')): error(nid, 'INVALID_LOCATOR', 'locator must be nonempty text')
        if kind == 'SourceDocument':
            try:
                if not _text(n.get('path')) or not _text(n.get('sha256')):
                    raise ValueError('Source path and SHA-256 must be nonempty text')
                path = (ROOT / n['path']).resolve()
                if not path.is_relative_to(ROOT / 'data') or not path.is_file():
                    raise ValueError('Sources must be existing files inside data/')
                source_bytes = path.read_bytes()
                if hashlib.sha256(source_bytes).hexdigest() != n['sha256']:
                    raise ValueError('Source SHA-256 does not match local bytes')
                if 'git_blob_sha1' in n:
                    blob_bytes = b'blob ' + str(len(source_bytes)).encode() + b'\0' + source_bytes
                    if hashlib.sha1(blob_bytes).hexdigest() != n['git_blob_sha1']:
                        raise ValueError('Git blob SHA-1 does not match local bytes')
                instant(n['collected_at'])
            except (KeyError, TypeError, ValueError, OSError) as exc:
                error(nid, 'SOURCE_INTEGRITY', str(exc))
        keys = {'ProductModel': ('manufacturer', 'name'), 'ProductRevision': ('model_id', 'revision'),
                'Configuration': ('revision_id', 'variant'), 'ComponentModel': ('identity_key',),
                'ProductUnit': ('company', 'serial'), 'ComponentLot': ('company', 'supplier', 'component_id', 'lot_code')}.get(kind)
        if keys and all(_text(n.get(key)) for key in keys):
            identity = (kind, *(canonical(n.get(k)) for k in keys))
            if identity in identities: error(nid, 'DUPLICATE_IDENTITY', identities[identity])
            identities[identity] = nid
        if kind == 'ProductModel' and 'specifications' in n:
            specs = n['specifications']
            if not isinstance(specs, dict):
                error(nid, 'INVALID_SPECIFICATIONS', 'specifications must be an object')
            else:
                extra_specs = set(specs) - SPECIFICATION_FIELDS
                if extra_specs:
                    error(nid, 'UNKNOWN_SPECIFICATION_FIELD', ', '.join(sorted(str(k) for k in extra_specs)))
                if not _text(specs.get('scope')):
                    error(nid, 'SPECIFICATION_SCOPE_REQUIRED', 'State the revision/product applicability limit')
                spec_evidence = specs.get('evidence')
                if not _string_list(spec_evidence) or not set(spec_evidence).issubset(evidence):
                    error(nid, 'INVALID_SPECIFICATION_EVIDENCE', 'Use nonempty parent Evidence IDs')
                if 'ethernet_interface' in specs and not _text(specs['ethernet_interface']):
                    error(nid, 'INVALID_SPECIFICATIONS', 'ethernet_interface must be text')
                if 'features' in specs and not _string_list(specs['features']):
                    error(nid, 'INVALID_SPECIFICATIONS', 'features must be unique nonempty text values')
                if 'pcb_dimensions_mm' in specs:
                    dims = specs['pcb_dimensions_mm']
                    if not isinstance(dims, list) or len(dims) != 2 or not all(_positive_number(x) for x in dims):
                        error(nid, 'INVALID_SPECIFICATIONS', 'pcb_dimensions_mm needs two positive finite dimensions')
        if kind == 'BOMItem':
            qty = n.get('quantity')
            if not _positive_number(qty) or (isinstance(qty, float) and not qty.is_integer()):
                error(nid, 'INVALID_QUANTITY', 'Positive finite integer source piece quantity required')
            if n.get('unit') != 'piece': error(nid, 'UNSUPPORTED_UNIT', 'Only source piece counts supported')
            if n.get('population') not in ('populated', 'not_populated', 'unknown'):
                error(nid, 'INVALID_POPULATION', 'Explicit mounting status required')
            pos = n.get('positions')
            if not _string_list(pos, allow_empty=n.get('population') != 'populated'):
                error(nid, 'INVALID_POSITIONS', 'Unique string reference designators required')
            elif n.get('population') == 'populated' and len(pos) != qty:
                error(nid, 'QUANTITY_POSITION_MISMATCH', 'Quantity must match source designators')
            if n.get('population') == 'unknown':
                warnings.append({'node_id': nid, 'code': 'POPULATION_UNCONFIRMED'})
        if kind in ('Installation', 'HistoryCoverage', 'InventoryMovement'):
            fields = {'Installation': ['installed_at', 'removed_at', 'confirmed_until'],
                      'HistoryCoverage': ['start', 'end'], 'InventoryMovement': ['occurred_at']}[kind]
            times = {}
            for key in fields:
                if n.get(key) is not None:
                    try: times[key] = instant(n[key])
                    except (ValueError, TypeError) as exc: error(nid, 'INVALID_TIME', f'{key}: {exc}')
            if kind == 'HistoryCoverage':
                if n.get('complete') is not True: error(nid, 'INVALID_COVERAGE', 'Coverage nodes assert complete history only')
                if 'start' in times and 'end' in times and times['start'] >= times['end']:
                    error(nid, 'INVALID_INTERVAL', 'start must precede end')
            if kind == 'Installation':
                if 'conflict' in n and not isinstance(n['conflict'], bool):
                    error(nid, 'INVALID_CONFLICT_FLAG', 'conflict must be boolean')
                for end in ('removed_at', 'confirmed_until'):
                    if end in times and 'installed_at' in times and times[end] <= times['installed_at']:
                        error(nid, 'INVALID_INTERVAL', 'End must follow installation')
                if all(k in times for k in ('removed_at', 'confirmed_until')) and times['confirmed_until'] > times['removed_at']:
                    error(nid, 'CONFLICTING_TIME', 'Confirmation cannot exceed removal')
                unit = index.get(n.get('unit_id'), {}) if isinstance(n.get('unit_id'), str) else {}
                lot = index.get(n.get('lot_id'), {}) if isinstance(n.get('lot_id'), str) else {}
                matches = [b for b in index.values() if b.get('type') == 'BOMItem'
                           and b.get('configuration_id') == unit.get('configuration_id')
                           and b.get('component_id') == lot.get('component_id')
                           and isinstance(b.get('positions'), list) and _text(n.get('position'))
                           and n['position'] in b['positions'] and b.get('population') == 'populated']
                if not matches: error(nid, 'CONFIGURATION_MISMATCH', 'Installed lot component/position needs a verified BOM link')
            if kind == 'InventoryMovement':
                q = n.get('quantity')
                if not _positive_number(q):
                    error(nid, 'INVALID_QUANTITY', 'Positive movement quantity required')
                if n.get('movement') not in ('receipt', 'issue'):
                    error(nid, 'INVALID_MOVEMENT', 'receipt or issue; issue never implies installation')
    # Two populated BOM lines cannot assign different parts to one physical position.
    positions = {}
    for n in index.values():
        if n.get('type') == 'BOMItem' and n.get('population') == 'populated' and isinstance(n.get('configuration_id'), str):
            if not _string_list(n.get('positions')):
                continue
            for pos in n['positions']:
                key = (n.get('configuration_id'), pos)
                if key in positions: error(n['id'], 'DUPLICATE_BOM_POSITION', positions[key])
                positions[key] = n['id']
    return {'valid': not errors, 'errors': errors, 'warnings': warnings}

def graph_jsonld(nodes):
    context = {'@vocab': 'urn:ontologics:', 'id': '@id', 'type': '@type', 'evidence': {'@type': '@id'}}
    for _, refs in SCHEMA.values():
        for key in refs: context[key] = {'@type': '@id'}
    # IDs use stable URNs rather than accidental JSON-LD prefixes.
    graph = []
    for n in nodes:
        row = dict(n, id='urn:ontologics:entity:' + n['id'])
        for key in SCHEMA[n['type']][1]: row[key] = 'urn:ontologics:entity:' + n[key]
        row['evidence'] = ['urn:ontologics:entity:' + e for e in n.get('evidence', [])]
        graph.append(row)
    return {'@context': context, '@graph': graph}

def tbox_turtle():
    lines = ['@prefix o: <urn:ontologics:> .', '@prefix owl: <http://www.w3.org/2002/07/owl#> .',
             '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .',
             'o:Ontology a owl:Ontology ; owl:versionInfo "' + SCHEMA_VERSION + '" .']
    for kind in SCHEMA: lines.append(f'o:{kind} a owl:Class .')
    refs = {}
    for kind, (_, relations) in SCHEMA.items():
        for prop, target in relations.items(): refs.setdefault(prop, set()).add(target)
    for prop, targets in refs.items():
        if len(targets) == 1:
            lines.append(f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{next(iter(targets))} .')
    lines += ['o:evidence a owl:ObjectProperty ; rdfs:range o:Evidence .',
              'o:ProductModel owl:disjointWith o:ProductUnit, o:ComponentModel, o:ComponentLot .',
              'o:ComponentModel owl:disjointWith o:ComponentLot .',
              '# Required fields, identity, time and approvals are enforced by Python, not OWL inference.']
    return '\n'.join(lines) + '\n'
