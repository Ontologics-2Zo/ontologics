"""Role flow contracts. Public payload changes require contract review."""
from ...contracts import FieldSpec
from ..base import output
from .instructions import ROUTE_INSTRUCTIONS

CONTRACTS = {
'route': (output(FieldSpec('target_role', 'string'), FieldSpec('reason', 'string')), {'lookup'}, ROUTE_INSTRUCTIONS)
}
