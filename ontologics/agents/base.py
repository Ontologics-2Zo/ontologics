"""Shared mechanics only; role contracts and business interpretation stay local."""
import json
from ..contracts import AgentSpec, FieldSpec, PayloadSchema
from ..harness import canonical
from ..service import DomainError

INPUT = PayloadSchema((FieldSpec('question', 'string'), FieldSpec('calls', 'array'),
                       FieldSpec('flow', 'string'), FieldSpec('options', 'object')))

def output(*fields):
    return PayloadSchema((FieldSpec('data_version', 'integer'), *fields))

def read_result(version, text, tool_results):
    preview = any(row['name'] in {'get_source_receipt', 'get_draft', 'validate_draft'} for row in tool_results)
    return {'data_version': version, 'explanation': text, 'meaning_validation': 'free_text_unverified',
            'provenance_scope': 'unapproved_candidate_or_draft' if preview else 'confirmed_snapshot'}

class RoleAgent:
    VERSIONS = {}
    def __init__(self, flow, messages):
        schema, tools, instructions = self.CONTRACTS[flow]
        self.role, self.flow = self.ROLE, flow
        self.spec = AgentSpec(self.role, self.VERSIONS.get(flow, '1.0.0'), instructions,
                              INPUT, schema, frozenset(tools))
        self._messages = canonical(messages)

    def messages(self, task):
        if task.data()['flow'] != self.flow:
            raise ValueError('Task flow differs from the fixed role contract')
        return json.loads(self._messages)

    def decode_result(self, text, task, tool_results):
        calls = task.data()['calls']
        for call in calls:
            if not any(row['name'] == call['name'] and row['arguments'] == call['arguments'] for row in tool_results):
                raise DomainError('AI_REQUIRED_TOOLS_MISSING', 'The role did not complete its required common calls')
        results = [next(row['result'] for row in reversed(tool_results)
                        if row['name'] == call['name'] and row['arguments'] == call['arguments']) for call in calls]
        return self.decode(text, task, tool_results, results)

    @classmethod
    def read_settings(cls, calls, flow, extra_prompt):
        return flow, extra_prompt

    def finalize_read(self, request, data, results):
        return None
