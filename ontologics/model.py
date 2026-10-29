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
        
        # C 1st phase: claim intake, quality case, production-line trace and containment requests.
    'ProductionLine': (['company', 'line_code', 'name'], {}),
    'UnitProductionRecord': (['unit_id', 'production_line_id', 'produced_at'], {'unit_id': 'ProductUnit', 'production_line_id': 'ProductionLine'}),
    'ClaimIntake': (['external_claim_id', 'customer_name', 'reported_at', 'reported_symptom', 'claimed_quantity', 'reported_serials', 'intake_state'], {}),
    'QualityCase': (['intake_id', 'customer_name', 'reported_at', 'reported_symptom', 'unit_ids'], {'intake_id': 'ClaimIntake'}),
    'QualityScope': (['case_id', 'production_line_id', 'as_of', 'unit_ids', 'scope_state'], {'case_id': 'QualityCase', 'production_line_id': 'ProductionLine'}),
    'ContainmentRequest': (['case_id', 'scope_id', 'request_kind', 'prepared_at', 'document_ref'], {'case_id': 'QualityCase', 'scope_id': 'QualityScope'}),
        # C 2nd phase: test request to the test team, its returned per-board results, reproduction quantity for A.
    'TestRequest': (['case_id', 'unit_ids', 'prepared_at', 'document_ref'], {'case_id': 'QualityCase'}),
    'QualityTestResult': (['case_id', 'test_request_id', 'unit_id', 'outcome', 'disposition', 'observed_at'], {'case_id': 'QualityCase', 'test_request_id': 'TestRequest', 'unit_id': 'ProductUnit'}),
    'ReproductionRequirement': (['case_id', 'configuration_id', 'customer_replacement_quantity', 'stock_replacement_quantity', 'available_replacement_quantity', 'required_good_quantity', 'unresolved_quantity', 'unit', 'assessment_state', 'test_result_ids'], {'case_id': 'QualityCase', 'configuration_id': 'Configuration'}),
}

REAL = {'ProductModel', 'ProductRevision', 'Configuration', 'ComponentModel', 'BOMItem'}
SYNTHETIC = {'ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage', 'InventoryMovement'}
# C 1st phase records are synthetic demonstration operations only.
SYNTHETIC |= {'ProductionLine', 'UnitProductionRecord', 'ClaimIntake', 'QualityCase', 'QualityScope', 'ContainmentRequest'}
# C 2nd phase: test results and dispositions are synthetic records returned by the test team, not real inspections.
SYNTHETIC |= {'TestRequest', 'QualityTestResult', 'ReproductionRequirement'}

COMMON_FIELDS = {'id', 'type', 'origin', 'evidence'}
OPTIONAL_FIELDS = {
    'SourceDocument': {'kind', 'product', 'revision', 'variant', 'sheet_name', 'verification',
                       'metadata_warning', 'git_blob_sha1'},
    'ProductModel': {'specifications'},
    'Configuration': {'identity_notes', 'population_notes'},
    'ComponentModel': {'package', 'identity_scope'},
    'Installation': {'removed_at', 'confirmed_until', 'conflict'},
    'UnitProductionRecord': {'production_work_id'},
    'ClaimIntake': {'reported_conditions', 'requested_action', 'delivery_reference'},
    'QualityScope': {'start', 'end'},
    'ContainmentRequest': {'production_line_id', 'draft_id', 'draft_version'},
    'TestRequest': {'draft_id', 'draft_version'},
    'QualityTestResult': {'note'},
    'ReproductionRequirement': {'replacement_unit_ids', 'document_ref'},
}
# Only explicit contract fields are accepted: operational examples cannot grow
# fault causes, inspection results or operating-time claims through free fields.
ALLOWED_FIELDS = {kind: COMMON_FIELDS | set(required) | OPTIONAL_FIELDS.get(kind, set())
                  for kind, (required, _) in SCHEMA.items()}
SPECIFICATION_FIELDS = {'scope', 'ethernet_interface', 'pcb_dimensions_mm', 'features', 'evidence'}
# C 1st-phase contract details that the generic single-reference/text checks cannot express.
# reported_symptom is the customer's reported wording only; it never asserts a confirmed fault.
# Tests, dispositions and reproduction quantities (C 2nd phase) are intentionally not accepted.
# (C 2nd phase) Test requests, test-team results with dispositions and reproduction quantities are added below
# as C2_TYPES; the 1st-phase lists stay unchanged. C itself neither runs tests nor decides dispositions.
C_TYPES = ('ProductionLine', 'UnitProductionRecord', 'ClaimIntake', 'QualityCase', 'QualityScope', 'ContainmentRequest')
C_LIST_REFS = {'QualityCase': {'unit_ids': 'ProductUnit'}, 'QualityScope': {'unit_ids': 'ProductUnit'}}
# ProductionWork is a B proposal and not yet a defined type, so production_work_id cannot resolve yet.
C_OPTIONAL_REFS = {'UnitProductionRecord': {'production_work_id': 'ProductionWork'},
                   'ContainmentRequest': {'production_line_id': 'ProductionLine'}}
C_ENUMS = {'ClaimIntake': {'intake_state': ('needs_review', 'confirmed', 'rejected')},
           'QualityScope': {'scope_state': ('hold', 'candidate')},
           'ContainmentRequest': {'request_kind': ('production_stop', 'shipment_hold')}}
C_TIMES = {'UnitProductionRecord': ('produced_at',), 'ClaimIntake': ('reported_at',), 'QualityCase': ('reported_at',),
           'QualityScope': ('as_of', 'start', 'end'), 'ContainmentRequest': ('prepared_at',)}
# One production record per board in this MVP; reproduction/rework records are not modelled yet.
C_IDENTITIES = {'ProductionLine': ('company', 'line_code'), 'ClaimIntake': ('customer_name', 'external_claim_id'),
                'UnitProductionRecord': ('unit_id',)}
# C 2nd phase additions. A result row is what the test team returned for one requested board; boards not
# tested get no row. An undetermined reproduction quantity is never stored as 0 (no node is created).
C2_TYPES = ('TestRequest', 'QualityTestResult', 'ReproductionRequirement')
C_TYPES = C_TYPES + C2_TYPES
C_LIST_REFS.update({'TestRequest': {'unit_ids': 'ProductUnit'},
                    'ReproductionRequirement': {'test_result_ids': 'QualityTestResult'}})
# Optional list references: checked and linked only when present.
C_OPTIONAL_LIST_REFS = {'ReproductionRequirement': {'replacement_unit_ids': 'ProductUnit'}}
C_ENUMS.update({'QualityTestResult': {'outcome': ('fail', 'pass', 'not_reproduced'),
                                      'disposition': ('customer_replacement', 'stock_replacement', 'release',
                                                      'hold', 'unresolved')},
                'ReproductionRequirement': {'assessment_state': ('confirmed', 'lower_bound'), 'unit': ('piece',)}})
C_TIMES.update({'TestRequest': ('prepared_at',), 'QualityTestResult': ('observed_at',)})
# One result per requested board, so a board is counted once.
C_IDENTITIES.update({'QualityTestResult': ('test_request_id', 'unit_id'),
                     'ReproductionRequirement': ('case_id', 'configuration_id')})
C2_QUANTITIES = ('customer_replacement_quantity', 'stock_replacement_quantity', 'available_replacement_quantity',
                 'required_good_quantity', 'unresolved_quantity')


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _positive_number(value):
    # Avoid converting arbitrarily large JSON integers to floats.
    return (isinstance(value, int) and not isinstance(value, bool) and value > 0) or (
        isinstance(value, float) and math.isfinite(value) and value > 0)


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


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


def _validate_c_nodes(index, error, warnings):
    """C 1st-phase checks: list/optional references, values, times, counts, identities and paths."""
    identities = {}
    for nid, n in index.items():
        kind = n.get('type')
        if kind not in C_TYPES:
            continue
        for key, target_type in C_LIST_REFS.get(kind, {}).items():
            ids = n.get(key)
            if not _string_list(ids):
                error(nid, 'INVALID_REFERENCE', key + ' must be a nonempty list of unique IDs'); continue
            for ref in ids:
                if index.get(ref, {}).get('type') != target_type:
                    error(nid, 'INVALID_REFERENCE', f'{key} -> {target_type}: {ref}')
        for key, target_type in C_OPTIONAL_REFS.get(kind, {}).items():
            if key in n:
                target = index.get(n[key]) if isinstance(n[key], str) else None
                if target is None or target.get('type') != target_type:
                    error(nid, 'INVALID_REFERENCE', key + ' -> ' + target_type)
        for key, allowed in C_ENUMS.get(kind, {}).items():
            if n.get(key) not in allowed:
                error(nid, 'INVALID_ENUM', key + ' must be one of ' + ', '.join(allowed))
        times = {}
        for key in C_TIMES.get(kind, ()):
            if key in n:
                try: times[key] = instant(n[key])
                except (ValueError, TypeError) as exc: error(nid, 'INVALID_TIME', f'{key}: {exc}')
        if 'start' in times and 'end' in times and times['start'] >= times['end']:
            error(nid, 'INVALID_INTERVAL', 'start must precede end')
        if kind == 'ClaimIntake':
            serials = n.get('reported_serials')
            if not _string_list(serials):
                error(nid, 'INVALID_FIELD_TYPE', 'reported_serials must be unique nonempty text values'); serials = None
            qty = n.get('claimed_quantity')
            if not _positive_int(qty):
                error(nid, 'INVALID_QUANTITY', 'claimed_quantity must be a positive integer piece count')
            elif serials is not None and qty != len(serials):
                # Customer counts and serial lists can disagree; a person resolves it, the graph keeps both.
                warnings.append({'node_id': nid, 'code': 'CLAIM_QUANTITY_SERIAL_MISMATCH'})
        if kind == 'QualityCase':
            intake = index.get(n.get('intake_id')) if isinstance(n.get('intake_id'), str) else None
            if intake and intake.get('type') == 'ClaimIntake' and intake.get('intake_state') != 'confirmed':
                error(nid, 'INTAKE_NOT_CONFIRMED', 'Quality cases start only from a human-confirmed claim intake')
        if kind == 'ContainmentRequest':
            if 'draft_version' in n and not _positive_int(n['draft_version']):
                error(nid, 'INVALID_FIELD_TYPE', 'draft_version must be a positive integer')
            if n.get('request_kind') == 'production_stop' and 'production_line_id' not in n:
                error(nid, 'PRODUCTION_LINE_REQUIRED', 'Production stop requests name the production line')
            scope = index.get(n.get('scope_id')) if isinstance(n.get('scope_id'), str) else None
            if scope and scope.get('type') == 'QualityScope' and scope.get('case_id') != n.get('case_id'):
                error(nid, 'CASE_MISMATCH', 'case_id must match the case of scope_id')
        keys = C_IDENTITIES.get(kind)
        if keys and all(_text(n.get(key)) for key in keys):
            identity = (kind, *(n[key] for key in keys))
            if identity in identities: error(nid, 'DUPLICATE_IDENTITY', identities[identity])
            identities[identity] = nid

def _nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_c2_nodes(index, error, warnings):
    """C 2nd-phase checks: requested boards only, same case across links and the reproduction formula."""
    def get(nid, kind):
        node = index.get(nid) if isinstance(nid, str) else None
        return node if node and node.get('type') == kind else None

    for nid, n in index.items():
        kind = n.get('type')
        if kind == 'TestRequest':
            if 'draft_version' in n and not _positive_int(n['draft_version']):
                error(nid, 'INVALID_FIELD_TYPE', 'draft_version must be a positive integer')
        if kind == 'QualityTestResult':
            request = get(n.get('test_request_id'), 'TestRequest')
            unit = n.get('unit_id') if isinstance(n.get('unit_id'), str) else None
            if request and request.get('case_id') != n.get('case_id'):
                error(nid, 'CASE_MISMATCH', 'case_id must match the case of test_request_id')
            if request and _string_list(request.get('unit_ids')) and unit not in request['unit_ids']:
                error(nid, 'UNIT_NOT_REQUESTED', 'Results are accepted only for boards in the test request')
            case = get(n.get('case_id'), 'QualityCase')
            claimed = set(case['unit_ids']) if case and _string_list(case.get('unit_ids')) else set()
            # Customer replacements are the claimed boards; stock replacements are never the claimed boards.
            if case and n.get('disposition') == 'customer_replacement' and unit not in claimed:
                error(nid, 'DISPOSITION_TARGET_MISMATCH', 'customer_replacement is only for boards in the case')
            if n.get('disposition') == 'stock_replacement' and unit in claimed:
                error(nid, 'DISPOSITION_TARGET_MISMATCH', 'stock_replacement is not for the claimed boards')
        if kind == 'ReproductionRequirement':
            values = {key: n.get(key) for key in C2_QUANTITIES}
            bad = [key for key, value in values.items() if not _nonnegative_int(value)]
            for key in bad:
                error(nid, 'INVALID_QUANTITY', key + ' must be a nonnegative integer piece count')
            if not bad:
                expected = max(0, values['customer_replacement_quantity'] + values['stock_replacement_quantity']
                               - values['available_replacement_quantity'])
                if values['required_good_quantity'] != expected:
                    error(nid, 'QUANTITY_MISMATCH', f'required_good_quantity must be {expected}')
                if (n.get('assessment_state') == 'confirmed') != (values['unresolved_quantity'] == 0):
                    error(nid, 'ASSESSMENT_STATE_MISMATCH', 'confirmed needs 0 unresolved; lower_bound needs some')
            ids = n['test_result_ids'] if _string_list(n.get('test_result_ids')) else []
            counts = {'customer_replacement': 0, 'stock_replacement': 0}
            for rid in ids:
                result = get(rid, 'QualityTestResult')
                if result is None:
                    continue
                if result.get('case_id') != n.get('case_id'):
                    error(nid, 'CASE_MISMATCH', rid + ' belongs to another case')
                if isinstance(result.get('disposition'), str) and result['disposition'] in counts:
                    counts[result['disposition']] += 1
                unit = get(result.get('unit_id'), 'ProductUnit')
                if unit and unit.get('configuration_id') != n.get('configuration_id'):
                    error(nid, 'CONFIGURATION_MISMATCH', rid + ' is for another configuration')
            if not bad and ids and (counts['customer_replacement'] != values['customer_replacement_quantity']
                                    or counts['stock_replacement'] != values['stock_replacement_quantity']):
                error(nid, 'QUANTITY_MISMATCH', 'replacement quantities must equal the listed test results')
            for key, target_type in C_OPTIONAL_LIST_REFS.get(kind, {}).items():
                if key not in n:
                    continue
                refs = n[key]
                if not _string_list(refs):
                    error(nid, 'INVALID_REFERENCE', key + ' must be a nonempty list of unique IDs'); continue
                for ref in refs:
                    unit = get(ref, target_type)
                    if unit is None:
                        error(nid, 'INVALID_REFERENCE', f'{key} -> {target_type}: {ref}')
                    elif unit.get('configuration_id') != n.get('configuration_id'):
                        error(nid, 'CONFIGURATION_MISMATCH', ref + ' is for another configuration')
                if not bad and len(refs) != values['available_replacement_quantity']:
                    error(nid, 'QUANTITY_MISMATCH', key + ' must list available_replacement_quantity boards')

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
        # C 1st phase numeric/list fields; their shapes are checked in _validate_c_nodes().
        structured |= {'claimed_quantity', 'reported_serials', 'unit_ids', 'draft_version'}
        # C 2nd phase numeric/list fields; checked in _validate_c2_nodes().
        structured |= {*C2_QUANTITIES, 'test_result_ids', 'replacement_unit_ids'}
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
    _validate_c_nodes(index, error, warnings)
    _validate_c2_nodes(index, error, warnings)
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
    for refs in (*C_LIST_REFS.values(), *C_OPTIONAL_REFS.values()):
        for key in refs: context[key] = {'@type': '@id'}
    for refs in C_OPTIONAL_LIST_REFS.values():
        for key in refs: context[key] = {'@type': '@id'}
    # IDs use stable URNs rather than accidental JSON-LD prefixes.
    graph = []
    for n in nodes:
        row = dict(n, id='urn:ontologics:entity:' + n['id'])
        for key in SCHEMA[n['type']][1]: row[key] = 'urn:ontologics:entity:' + n[key]
        for key in C_LIST_REFS.get(n['type'], {}): row[key] = ['urn:ontologics:entity:' + ref for ref in n[key]]
        for key in C_OPTIONAL_REFS.get(n['type'], {}):
            if key in n: row[key] = 'urn:ontologics:entity:' + n[key]
        for key in C_OPTIONAL_LIST_REFS.get(n['type'], {}):
            if key in n: row[key] = ['urn:ontologics:entity:' + ref for ref in n[key]]
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
    # C list/optional references; targets that are not defined classes yet (B proposals) are skipped.
    for relations in (*C_LIST_REFS.values(), *C_OPTIONAL_REFS.values()):
        for prop, target in relations.items():
            if target in SCHEMA: refs.setdefault(prop, set()).add(target)
    for relations in C_OPTIONAL_LIST_REFS.values():
        for prop, target in relations.items():
            if target in SCHEMA: refs.setdefault(prop, set()).add(target)
    for prop, targets in refs.items():
        if len(targets) == 1:
            lines.append(f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{next(iter(targets))} .')
    lines += ['o:evidence a owl:ObjectProperty ; rdfs:range o:Evidence .',
              'o:ProductModel owl:disjointWith o:ProductUnit, o:ComponentModel, o:ComponentLot .',
              'o:ComponentModel owl:disjointWith o:ComponentLot .',
              '# Required fields, identity, time and approvals are enforced by Python, not OWL inference.']
    return '\n'.join(lines) + '\n'
