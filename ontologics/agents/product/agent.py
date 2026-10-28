"""Actual Product implementation used by the common host registry."""
from ..base import RoleAgent, read_result
from .schemas import CONTRACTS
from .validators import decode_draft


class ProductAgent(RoleAgent):
    ROLE = 'product'
    CONTRACTS = CONTRACTS

    def decode(self, text, task, tool_results, results):
        if self.flow == 'read':
            return read_result(task.context.data_version, text, tool_results)
        return decode_draft(task, tool_results)
