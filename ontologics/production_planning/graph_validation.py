"""Validate host-supplied A reference graphs locally, without registering them.

Common nodes (including source files) use the unchanged common validator.
Every A node is validated here against the full reference index. Unknown or
malformed nodes are sent to the common validator and rejected, never discarded.
This report is not a shared Service validation, approval or persistence receipt.
"""
from __future__ import annotations

from .. import model
from . import schema


def validate_graph(nodes):
    if not isinstance(nodes, list) or len(nodes) > 20000:
        return model.validate_graph(nodes)
    common_nodes, a_nodes = [], []
    for node in nodes:
        kind = node.get('type') if isinstance(node, dict) else None
        (a_nodes if isinstance(kind, str) and kind in schema.SCHEMA else common_nodes).append(node)
    report = model.validate_graph(common_nodes)
    if not a_nodes:
        return report
    errors, warnings = report['errors'], report['warnings']

    def error(node_id, code, detail):
        errors.append({'node_id': node_id, 'code': code, 'detail': detail})

    index = {}
    for node in nodes:
        if not isinstance(node, dict) or not model._text(node.get('id')):
            # Common validation already handles malformed common nodes.
            if isinstance(node, dict) and isinstance(node.get('type'), str) and node['type'] in schema.SCHEMA:
                error(None, 'INVALID_NODE', 'Every node needs a nonempty string id')
            continue
        nid = node['id']
        if nid in index:
            error(nid, 'DUPLICATE_ID', 'IDs must be unique across common and A nodes')
        index[nid] = node

    identities = {}
    for node in a_nodes:
        if not model._text(node.get('id')):
            continue
        nid, kind = node['id'], node['type']
        required, refs = schema.SCHEMA[kind]
        allowed = model.COMMON_FIELDS | set(required) | schema.OPTIONAL_FIELDS.get(kind, set())
        extra = set(node) - allowed
        if extra:
            error(nid, 'UNKNOWN_FIELD', ', '.join(sorted(str(key) for key in extra)))
        if node.get('origin') not in ('real', 'synthetic'):
            error(nid, 'ORIGIN_REQUIRED', 'real or synthetic')
        for key in model.COMMON_FIELDS | set(required):
            if key not in node or node[key] is None or node[key] == '':
                error(nid, 'MISSING_FIELD', key)
        structured = {'evidence', 'quantity'} | schema.STRUCTURED_FIELDS.get(kind, set())
        for key in allowed - structured:
            if key in node and not model._text(node[key]):
                error(nid, 'INVALID_FIELD_TYPE', key + ' must be nonempty text')
        for key, target_type in refs.items():
            target = index.get(node.get(key)) if isinstance(node.get(key), str) else None
            if target is None or target.get('type') != target_type:
                error(nid, 'INVALID_REFERENCE', key + ' -> ' + target_type)
        evidence = node.get('evidence', [])
        if not model._string_list(evidence, allow_empty=True):
            error(nid, 'INVALID_EVIDENCE', 'evidence must be a list of unique nonempty IDs')
            evidence = []
        if not evidence:
            error(nid, 'EVIDENCE_REQUIRED', 'Every factual node needs source locators')
        for eid in evidence:
            target = index.get(eid, {})
            if target.get('type') != 'Evidence':
                error(nid, 'EVIDENCE_NOT_FOUND', eid)
            elif target.get('origin') != node.get('origin'):
                error(nid, 'EVIDENCE_ORIGIN_MISMATCH', eid)
        schema.validate_node_rules(node, index, identities, error, warnings)
    schema.validate_round_totals(index, error)
    return {'valid': not errors, 'errors': errors, 'warnings': warnings}
