"""Small versioned extension DTOs; no inheritance of host authority.

Schemas validate a bounded JSON object at runtime. They do not migrate database
facts or redefine model.SCHEMA, and old AgentRegistration callers remain valid.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import re
from typing import Protocol

from .harness import BudgetLimits, PinnedContext, canonical
from .service import DomainError

AUTHORITY_FIELDS = frozenset({'actor', 'actor_id', 'principals', 'role', 'roles', 'approved', 'reviewer'})


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str
    required: bool = True

    def __post_init__(self):
        if (not isinstance(self.name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', self.name)
                or self.name in AUTHORITY_FIELDS or self.kind not in {'string', 'integer', 'boolean', 'object', 'array'}
                or type(self.required) is not bool):
            raise ValueError('Use a supported data field, never a host authority field')


@dataclass(frozen=True)
class PayloadSchema:
    fields: tuple[FieldSpec, ...]
    version: str = '1'

    def __post_init__(self):
        if (not isinstance(self.fields, (list, tuple)) or not 1 <= len(self.fields) <= 32
                or any(not isinstance(x, FieldSpec) for x in self.fields)
                or len({x.name for x in self.fields}) != len(self.fields) or self.version != '1'):
            raise ValueError('Use unique fields and a supported payload schema version')
        object.__setattr__(self, 'fields', tuple(self.fields))

    def validate(self, value):
        if not isinstance(value, dict) or set(value) - {x.name for x in self.fields}:
            raise ValueError('Payload has unknown fields or is not an object')
        types = {'string': str, 'integer': int, 'boolean': bool, 'object': dict, 'array': list}
        for spec in self.fields:
            if spec.name not in value:
                if spec.required:
                    raise ValueError('Payload is missing a required field')
                continue
            if type(value[spec.name]) is not types[spec.kind]:
                raise ValueError('Payload field type does not match schema')
        encoded = canonical(value)
        if len(encoded.encode()) > 100000:
            raise ValueError('Payload exceeds the contract limit')
        return json.loads(encoded)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    version: str
    instructions: str
    input_schema: PayloadSchema
    output_schema: PayloadSchema
    requested_tools: frozenset[str]
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    validator: object = None

    def __post_init__(self):
        from .service import AgentGateway
        if (not isinstance(self.name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', self.name)
                or not isinstance(self.version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', self.version)
                or not isinstance(self.instructions, str) or not 1 <= len(self.instructions.strip()) <= 16000
                or not isinstance(self.input_schema, PayloadSchema) or not isinstance(self.output_schema, PayloadSchema)
                or not isinstance(self.limits, BudgetLimits)):
            raise ValueError('Invalid versioned Agent specification')
        if (not isinstance(self.requested_tools, (tuple, list, set, frozenset)) or not self.requested_tools
                or any(not isinstance(x, str) for x in self.requested_tools)
                or len(set(self.requested_tools)) != len(self.requested_tools)
                or not set(self.requested_tools) <= AgentGateway.ALLOWED):
            raise ValueError('Select unique existing public tools; specification does not grant permission')
        if self.validator is not None and not callable(self.validator):
            raise ValueError('Use a trusted callable result validator')
        object.__setattr__(self, 'requested_tools', frozenset(self.requested_tools))

    def validate_result(self, text):
        return self.validate_payload(json.loads(text))

    def validate_payload(self, payload):
        value = self.output_schema.validate(payload)
        if self.validator is not None and self.validator(value) is not True:
            raise DomainError('AGENT_RESULT_REJECTED', 'Registered semantic validation rejected the result')
        return value


@dataclass(frozen=True)
class AgentTask:
    payload: dict
    context: PinnedContext
    schema_version: str = 'agent-task-1'
    _serialized: str = field(init=False, repr=False)

    def __post_init__(self):
        if (self.schema_version != 'agent-task-1' or not isinstance(self.context, PinnedContext)
                or not isinstance(self.payload, dict) or set(self.payload) & AUTHORITY_FIELDS):
            raise ValueError('Invalid task or unsupported envelope version')
        encoded = canonical(self.payload)
        if len(encoded.encode()) > 100000:
            raise ValueError('Task payload is too large')
        object.__setattr__(self, '_serialized', encoded)

    def data(self):
        return json.loads(self._serialized)


@dataclass(frozen=True)
class EvidenceRef:
    """Host-resolved reference from a successful read of a confirmed snapshot.

    This proves reference provenance, not that every free-text claim follows
    from it. Role validators still compare structured conclusions with tools.
    """
    data_version: int
    evidence_id: str
    source_id: str
    origin: str
    locator: str
    source_sha256: str

    def __post_init__(self):
        if (type(self.data_version) is not int or self.data_version < 0
                or self.origin not in {'real', 'synthetic'}
                or any(not isinstance(value, str) or not value.strip()
                       for value in (self.evidence_id, self.source_id, self.locator))
                or not isinstance(self.source_sha256, str)
                or not re.fullmatch(r'[a-f0-9]{64}', self.source_sha256)):
            raise ValueError('Use an evidence reference resolved from a confirmed snapshot')


@dataclass(frozen=True)
class AgentError:
    """The existing runtime error contract, without provider response bodies."""
    code: str
    message: str
    error_type: str | None = None

    @classmethod
    def from_runtime(cls, value):
        if value is None:
            return None
        if (not isinstance(value, dict) or not isinstance(value.get('code'), str)
                or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', value['code'])
                or not isinstance(value.get('message'), str)):
            raise ValueError('Use the common runtime error envelope')
        error_type = value.get('error_type')
        if error_type is not None and not isinstance(error_type, str):
            raise ValueError('Invalid runtime error type')
        return cls(value['code'], value['message'], error_type)

    def to_dict(self):
        value = {'code': self.code, 'message': self.message}
        if self.error_type is not None:
            value['error_type'] = self.error_type
        return value


@dataclass(frozen=True)
class AgentResult:
    agent_name: str
    agent_version: str
    payload: dict | None
    runtime: dict
    schema_version: str = 'agent-result-1'
    context: PinnedContext | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def to_dict(self):
        from dataclasses import asdict
        error = AgentError.from_runtime(self.runtime.get('error'))
        return {'schema_version': self.schema_version, 'agent_name': self.agent_name,
                'agent_version': self.agent_version, 'payload': self.payload, 'runtime': self.runtime,
                'context': self.context.to_dict() if self.context is not None else None,
                'evidence_refs': [asdict(ref) for ref in self.evidence_refs],
                'error': error.to_dict() if error is not None else None}


class AgentProtocol(Protocol):
    """Trusted role prepares messages; common host executes tools.

    An optional validate_result(value, task, tool_results) -> bool hook receives
    detached copies of successful tool reads. It cannot approve or mutate facts.
    """
    def messages(self, task: AgentTask) -> list[dict]: ...


def withhold_agent_result(result, code, message):
    """A later host parity/audit failure must not expose a successful payload.

    Keep transport execution status separately: a completed model call is not
    a successfully verified application result, and its DB trace is unchanged.
    """
    error = AgentError.from_runtime({'code': code, 'message': message}).to_dict()
    runtime = result['runtime']
    runtime.update({'execution_status': runtime.get('status'),
        'ok': False, 'status': 'failed', 'error': error, 'text': '', 'output_withheld': True,
        'failure_stage': 'post_runtime_validation'})
    result.update(payload=None, evidence_refs=[], error=error)
    if 'read_restart' in result:
        result['read_restart'] = {**result['read_restart'], 'checkpoint_available': False}
