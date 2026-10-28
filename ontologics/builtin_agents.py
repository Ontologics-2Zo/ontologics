"""Compatibility exports. Actual service implementations are in agents/<role>/."""
from .agents.base import INPUT, output
from .agents.registry import BuiltinRole, builtin_task, ROLES

CONTRACTS = {(role, flow): contract for role, cls in ROLES.items()
             for flow, contract in cls.CONTRACTS.items()}
