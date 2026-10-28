"""Supervisor routing output validation."""
import json
from ..base import RoleAgent
from ...service import DomainError
from .schemas import CONTRACTS

class SupervisorAgent(RoleAgent):
    ROLE = 'supervisor'
    CONTRACTS = CONTRACTS

    def decode(self, text, task, tool_results, results):
        try:
            value = json.loads(text)
            hint = task.data()['options'].get('route_hint')
            if (not isinstance(value, dict) or set(value) != {'role', 'reason'} or
                    value['role'] not in {'product', 'knowledge', 'quality'} or
                    not isinstance(value['reason'], str) or (hint and value['role'] != hint)):
                raise ValueError()
            return {'data_version': task.context.data_version, 'target_role': value['role'], 'reason': value['reason']}
        except (ValueError, TypeError, KeyError):
            raise DomainError('AI_ROUTING_INVALID', 'The role returned an invalid routing or input proposal') from None
