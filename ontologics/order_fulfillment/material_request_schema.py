"""Detached B metadata for the statically registered additional request type.

Importing or editing these copies never changes the shared runtime contract.
Business validation remains owned by the B package.
"""
from .. import domain_schema

REQUEST_TYPE = 'AdditionalMaterialReviewRequest'
SCHEMA = domain_schema.subset(domain_schema.SCHEMA, {REQUEST_TYPE})
OPTIONAL_REFERENCES = domain_schema.subset(domain_schema.OPTIONAL_REFERENCES, SCHEMA)
OPTIONAL_FIELDS = domain_schema.subset(domain_schema.OPTIONAL_FIELDS, SCHEMA)
STRUCTURED_FIELDS = domain_schema.subset(domain_schema.STRUCTURED_FIELDS, SCHEMA)
INTEGER_MINIMUMS = domain_schema.subset(domain_schema.INTEGER_MINIMUMS, SCHEMA)
