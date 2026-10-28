"""Quality flow payloads; changing public fields requires common contract review."""
from ...contracts import FieldSpec
from ..base import output
from .instructions import CONDITIONS_INSTRUCTIONS, INVESTIGATION_INSTRUCTIONS


CONTRACTS = {
    'conditions': (output(FieldSpec('proposal', 'object')),
        {'lookup'}, CONDITIONS_INSTRUCTIONS),
    'investigation': (output(FieldSpec('observations', 'array'), FieldSpec('evidence_ids', 'array')),
        {'investigate', 'compare_investigation'}, INVESTIGATION_INSTRUCTIONS),
}
