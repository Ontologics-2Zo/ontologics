"""Provider-facing descriptions of existing common functions; no new authority."""
from .model import SCHEMA
from .service import AgentGateway

TEXT = {'type': 'string'}
VERSION = {'type': 'integer', 'minimum': 0}
DRAFT_VERSION = {'type': 'integer', 'minimum': 1}

def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}

QUERY = obj({'mode': {'type': 'string', 'enum': ['A', 'B']}, 'lot_id': TEXT,
    'unit_ids': {'type': 'array', 'items': TEXT, 'minItems': 1}, 'at': TEXT, 'start': TEXT, 'end': TEXT,
    'period_meaning': {'type': 'string', 'enum': ['installation_overlap']}, 'exclude_unit_id': TEXT},
    ['mode', 'lot_id', 'unit_ids'])

_DEFINITIONS = {
    'get_source_receipt': ('Read this user-owned immutable receipt: source, parsed BOM/records, unknowns, evidence and base version. Not approved data.',
        obj({'receipt_id': TEXT}, ['receipt_id'])),
    'prepare_source_draft': ('Save only the exact host-parsed receipt candidate as an unapproved draft. No operations/author/permission inputs. Same request key as direct save; rejects stale receipt.',
        obj({'receipt_id': TEXT, 'request_key': TEXT, 'draft_id': TEXT, 'expected_version': DRAFT_VERSION}, ['receipt_id', 'request_key'])),
    'semantic_search': ('Search already-indexed evidence using a fixed embedding model and pgvector. Explicit snapshot/origin, top-five candidates, source/locator and partial index coverage. Similarity is not fact, identity or time judgment.',
        obj({'query': TEXT, 'origin': {'type': 'string', 'enum': ['all', 'real', 'synthetic']}, 'version': VERSION}, ['query'])),
    'search_evidence': ('Search confirmed graph text/IDs with real/synthetic and version filters. Lexical candidates, not semantic RAG, factual judgment or identity merging.',
        obj({'query': TEXT, 'origin': {'type': 'string', 'enum': ['all', 'real', 'synthetic']}, 'version': VERSION}, ['query'])),
    'lookup': ('Read graph nodes, including Evidence and SourceDocument for provenance.',
        obj({'node_id': TEXT, 'kind': {'type': 'string', 'enum': list(SCHEMA)}, 'version': VERSION})),
    'configuration': ('Read product, revision, configuration, BOM and source-scoped components; unknown population is not populated.',
        obj({'configuration_id': TEXT, 'version': VERSION}, ['configuration_id'])),
    'component_usage': ('Find BOM use of one exact source-scoped component ID, without cross-revision identity inference.',
        obj({'component_id': TEXT, 'version': VERSION}, ['component_id'])),
    'compare_configurations': ('Compare source descriptions by position; not electrical compatibility. Draft comparison is unapplied preview.',
        obj({'left_id': TEXT, 'right_id': TEXT, 'version': VERSION, 'draft_id': TEXT, 'draft_version': DRAFT_VERSION}, ['left_id', 'right_id'])),
    'investigate': ('A point or B installation-overlap query. Explicit unit scope, timezone; no defect/safety/uptime inference.',
        obj({'query': QUERY, 'version': VERSION}, ['query'])),
    'compare_investigation': ('Run exactly one fixed query against two confirmed snapshot versions and return before/after evidence.',
        obj({'query': QUERY, 'before_version': VERSION, 'after_version': VERSION}, ['query', 'before_version', 'after_version'])),
    'prepare_draft': ('Create or revise an UNAPPROVED draft only. Copy factual values from supplied evidence. Use stable request_key on retry. Check validation.valid separately from ok.',
        obj({'operations': {'type': 'array', 'minItems': 1, 'items': obj({'op': {'const': 'put'},
            'node': {'type': 'object', 'description': 'Node conforming to model.SCHEMA: exact factual fields and evidence IDs; code validates the candidate graph.'}}, ['op', 'node'])},
            'request_key': TEXT, 'draft_id': TEXT, 'expected_version': DRAFT_VERSION}, ['operations', 'request_key'])),
    'get_draft': ('Read an exact draft version and review history; not approval.',
        obj({'draft_id': TEXT, 'version': DRAFT_VERSION}, ['draft_id', 'version'])),
    'validate_draft': ('Validate an exact draft version, including stale base; no confirmed writes.',
        obj({'draft_id': TEXT, 'version': DRAFT_VERSION}, ['draft_id', 'version'])),
    'preview_investigation': ('Query a valid draft candidate; result is unapplied preview, never confirmed data.',
        obj({'draft_id': TEXT, 'version': DRAFT_VERSION, 'query': QUERY}, ['draft_id', 'version', 'query'])),
    'export_graph': ('Export the confirmed JSON-LD graph; usually prefer focused lookups to reduce tokens.', obj({'version': VERSION})),
}

PRODUCT_TOOLS = ('get_source_receipt', 'prepare_source_draft', 'lookup', 'configuration', 'compare_configurations', 'prepare_draft', 'get_draft', 'validate_draft')
QUALITY_TOOLS = ('lookup', 'component_usage', 'investigate', 'compare_investigation')
KNOWLEDGE_TOOLS = ('semantic_search', 'search_evidence', 'lookup', 'configuration', 'component_usage')
SUPERVISOR_TOOLS = ('lookup',)

def function_tools(names):
    """Non-strict JSON schemas retain optional and heterogeneous graph fields.

    model.py and Service remain the authoritative validation boundary.
    """
    if not names or len(set(names)) != len(names) or not set(names) <= AgentGateway.ALLOWED:
        raise ValueError('Select unique tools from AgentGateway.ALLOWED')
    return [{'type': 'function', 'name': name, 'description': _DEFINITIONS[name][0],
             'parameters': _DEFINITIONS[name][1], 'strict': False} for name in names]
