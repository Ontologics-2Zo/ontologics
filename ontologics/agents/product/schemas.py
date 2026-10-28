"""Product role payloads. Public contract changes require shared review."""
from ...contracts import FieldSpec
from ..base import output
from .instructions import READ_INSTRUCTIONS, DRAFT_INSTRUCTIONS

CONTRACTS = {
    'read': (output(FieldSpec('explanation', 'string'), FieldSpec('meaning_validation', 'string'),
                    FieldSpec('provenance_scope', 'string')),
             {'get_source_receipt', 'get_draft', 'validate_draft', 'configuration'}, READ_INSTRUCTIONS),
    'draft': (output(FieldSpec('draft_id', 'string'), FieldSpec('draft_version', 'integer'),
                     FieldSpec('validation_valid', 'boolean'), FieldSpec('action', 'string'),
                     FieldSpec('source_receipt_id', 'string'), FieldSpec('provenance_scope', 'string')),
              {'get_source_receipt', 'prepare_source_draft', 'validate_draft'}, DRAFT_INSTRUCTIONS),
}
