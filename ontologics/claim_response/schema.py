"""C claim response business rules and compatibility exports of shared graph metadata.

Definitions live in domain_schema (C_SCHEMA). The common validator calls
validate_claim_nodes() only when a C type is present; generic shape, reference,
list-reference, origin and evidence checks stay in model.validate_graph.
Importing this module never registers a type or changes approval/storage.
"""
from __future__ import annotations

from .. import domain_schema
from ..model import _string_list, _text, instant

SCHEMA = domain_schema.subset(domain_schema.C_SCHEMA, domain_schema.C_TYPES)
OPTIONAL_FIELDS = domain_schema.subset(domain_schema.OPTIONAL_FIELDS, SCHEMA)
OPTIONAL_REFERENCES = domain_schema.subset(domain_schema.OPTIONAL_REFERENCES, SCHEMA)
LIST_REFERENCES = domain_schema.subset(domain_schema.LIST_REFERENCES, SCHEMA)
STRUCTURED_FIELDS = domain_schema.subset(domain_schema.STRUCTURED_FIELDS, SCHEMA)

# reported_symptom is the customer's reported wording only; it never asserts a confirmed fault.
ENUMS = {'ClaimIntake': {'intake_state': ('needs_review', 'confirmed', 'rejected')},
         'QualityScope': {'scope_state': ('hold', 'candidate')},
         'ContainmentRequest': {'request_kind': ('production_stop', 'shipment_hold')}}
TIMES = {'UnitProductionRecord': ('produced_at',), 'ClaimIntake': ('reported_at',), 'QualityCase': ('reported_at',),
         'QualityScope': ('as_of', 'start', 'end'), 'ContainmentRequest': ('prepared_at',)}
# One production record per board in this MVP; reproduction/rework records are not modelled yet.
IDENTITIES = {'ProductionLine': ('company', 'line_code'), 'ClaimIntake': ('customer_name', 'external_claim_id'),
              'UnitProductionRecord': ('unit_id',)}


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_claim_nodes(index, error, warnings):
    """Append C-only diagnostics for one validation run (values, times, counts, paths, identities)."""
    identities = {}
    for nid, n in index.items():
        kind = n.get('type')
        if not isinstance(kind, str) or kind not in SCHEMA:
            continue
        for key, allowed in ENUMS.get(kind, {}).items():
            if n.get(key) not in allowed:
                error(nid, 'INVALID_ENUM', key + ' must be one of ' + ', '.join(allowed))
        times = {}
        for key in TIMES.get(kind, ()):
            if key in n:
                try: times[key] = instant(n[key])
                except (ValueError, TypeError) as exc: error(nid, 'INVALID_TIME', f'{key}: {exc}')
        if 'start' in times and 'end' in times and times['start'] >= times['end']:
            error(nid, 'INVALID_INTERVAL', 'start must precede end')
        if kind == 'UnitProductionRecord' and 'production_work_id' in n:
            # Unsupported link: the shared ProductionWork type exists, but which work a C board
            # belongs to is not agreed with B (I02). Any value is rejected until that contract exists.
            error(nid, 'PRODUCTION_WORK_UNSUPPORTED', 'production_work_id is not supported yet; leave it out')
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
        keys = IDENTITIES.get(kind)
        if keys and all(_text(n.get(key)) for key in keys):
            identity = (kind, *(n[key] for key in keys))
            if identity in identities: error(nid, 'DUPLICATE_IDENTITY', identities[identity])
            identities[identity] = nid
