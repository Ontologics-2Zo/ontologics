"""Compatibility host for A workflows using the shared identity/approval path."""
from ..host import CommonHost


class MaterialsHost(CommonHost):
    """Use common sessions, shared budgets and approved graph publication."""
