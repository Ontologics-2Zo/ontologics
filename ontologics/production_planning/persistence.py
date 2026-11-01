"""Compatibility name for the shared, versioned PostgreSQL graph lifecycle.

A/B domain types are registered in the common model. Approval, exact-version
application, validation, export and projection reads therefore use one shared
implementation rather than an A-specific copy.
"""
from ..service import Service as CommonService


class PlanningService(CommonService):
    """Retained import path; all persistence rules are inherited unchanged."""
