"""Role flow contracts. Public payload changes require contract review."""
from ...contracts import FieldSpec
from ..base import output
from .instructions import READ_INSTRUCTIONS, SEMANTIC_INSTRUCTIONS, PLAN_INSTRUCTIONS

CONTRACTS = {
'read': (output(FieldSpec('explanation', 'string'), FieldSpec('meaning_validation', 'string'),
                                 FieldSpec('provenance_scope', 'string')), {'lookup', 'configuration', 'component_usage', 'search_evidence'}, READ_INSTRUCTIONS),
'semantic': (output(FieldSpec('answerable', 'boolean'), FieldSpec('evidence_ids', 'array')), {'semantic_search'}, SEMANTIC_INSTRUCTIONS),
'plan': (output(FieldSpec('search', 'object')), {'lookup'}, PLAN_INSTRUCTIONS)
}
