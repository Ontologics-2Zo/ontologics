"""Compatibility host for A workflows using the shared identity/approval path."""
from ..host import CommonHost


class PlanningHost(CommonHost):
    """Use common sessions, shared budgets and approved graph publication."""
