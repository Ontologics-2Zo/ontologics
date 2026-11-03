"""B compatibility metadata for types registered by the shared graph contract.

Definitions live in domain_schema. Importing a role never mutates the common
schema or changes approval/storage permissions.
"""
from copy import deepcopy

from ..model import COMMON_FIELDS, SCHEMA as _COMMON_SCHEMA
from .. import domain_schema


# Provenance of the extraction, not a new common or Agent contract version.
EXTRACTED_GRAPH_VERSION = 'graph-0.5'

SCHEMA = domain_schema.subset(domain_schema.SCHEMA, domain_schema.B_TYPES)
SYNTHETIC = set(SCHEMA)
OPTIONAL_REFERENCES = domain_schema.subset(domain_schema.OPTIONAL_REFERENCES, SCHEMA)
LIST_REFERENCES = domain_schema.subset(domain_schema.LIST_REFERENCES, SCHEMA)
OPTIONAL_FIELDS = domain_schema.subset(domain_schema.OPTIONAL_FIELDS, SCHEMA)
# Additions to the common validator's structured-field exceptions only.
STRUCTURED_FIELDS = set().union(*domain_schema.subset(domain_schema.STRUCTURED_FIELDS, SCHEMA).values())
ALLOWED_FIELDS = {kind: COMMON_FIELDS | set(required) | OPTIONAL_FIELDS.get(kind, set())
                  for kind, (required, _) in SCHEMA.items()}


def lookup_schema(common_schema=None):
    """Return detached shared metadata while checking the B compatibility subset.

    Matching duplicates support the current B-extended model during separation.
    Conflicts are configuration errors, never permission to override a domain.
    Shared registration is static; this helper cannot change storage support.
    """
    combined = deepcopy(_COMMON_SCHEMA if common_schema is None else common_schema)
    for kind, definition in SCHEMA.items():
        if kind in combined and combined[kind] != definition:
            raise ValueError('Conflicting common and B lookup schema: ' + kind)
        combined[kind] = deepcopy(definition)
    return combined


# Shared by the B service and tool descriptions for this process's startup.
LOOKUP_SCHEMA = lookup_schema()
