"""In-process user/workflow boundary over existing tools; no login or HTTP server.

Construct providers and select development identities in trusted host code only.
Never expose this host, a RequestSession, or a Service object to an LLM.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import re
from types import MappingProxyType

from .database import DatabaseSettings
from .identity import IdentityError, IdentityProvider, UserIdentity
from .runtime import ModelRuntime
from .service import AgentGateway, DomainError, Service
from .tool_specs import PRODUCT_TOOLS, QUALITY_TOOLS, KNOWLEDGE_TOOLS, SUPERVISOR_TOOLS


WORKFLOW_ACTOR = 'system:host-workflow'
ROLE_TOOLS = {name: frozenset(names) for name, names in {
    'product': PRODUCT_TOOLS, 'quality': QUALITY_TOOLS,
    'knowledge': KNOWLEDGE_TOOLS, 'supervisor': SUPERVISOR_TOOLS}.items()}


@dataclass(frozen=True)
class AgentRegistration:
    """A trusted, local role definition; registration never grants user rights.

    Only host startup code constructs this object. Requests, model output and
    document contents cannot register roles or replace the four built-in roles.
    The provider may expose these existing function_tools; the gateway remains
    the enforcement boundary even if a model requests another tool.
    """
    name: str
    instructions: str
    allowed_tools: frozenset[str]

    def __post_init__(self):
        if (not isinstance(self.name, str) or
                not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', self.name) or self.name in ROLE_TOOLS):
            raise ValueError('Use a distinct extension role name; built-in roles cannot be replaced')
        if (not isinstance(self.instructions, str) or
                not self.instructions.strip() or len(self.instructions) > 16000):
            raise ValueError('Provide bounded, nonempty extension instructions')
        if not isinstance(self.allowed_tools, (tuple, list, set, frozenset)):
            raise ValueError('Select existing Agent tools as a collection')
        if (not self.allowed_tools or any(not isinstance(n, str) for n in self.allowed_tools) or
                len(set(self.allowed_tools)) != len(self.allowed_tools) or
                not set(self.allowed_tools) <= AgentGateway.ALLOWED):
            raise ValueError('Extension tools must be unique existing AgentGateway.ALLOWED tools')
        object.__setattr__(self, 'allowed_tools', frozenset(self.allowed_tools))


def _registrations(values):
    if not isinstance(values, (tuple, list)):
        raise ValueError('Configure extension registrations in trusted host code')
    registered = {}
    for definition in values:
        if not isinstance(definition, AgentRegistration) or definition.name in registered:
            raise ValueError('Configure distinct AgentRegistration objects')
        registered[definition.name] = definition
    return MappingProxyType(registered)


def _identity(value, subject=None):
    if not isinstance(value, UserIdentity) or (subject is not None and value.subject != subject):
        raise IdentityError('INVALID_IDENTITY', 'Identity provider returned a mismatched subject')
    return value


class CommonHost:
    """One connection per request; existing DB transactions remain authoritative."""

    def __init__(self, identity_provider: IdentityProvider, database=None, *, schema=None, agent_registrations=(), embedding_provider=None):
        self._agent_registrations = _registrations(agent_registrations)
        self._identities = identity_provider
        self._embedding_provider = embedding_provider
        self._database = (database if isinstance(database, DatabaseSettings) else
                          DatabaseSettings(database, schema or 'ontologics') if database is not None else
                          DatabaseSettings.from_environment(schema=schema))

    @contextmanager
    def request(self, context):
        # Snapshot once. Neither model arguments nor later user selection changes it.
        try:
            user = _identity(self._identities.resolve(context))
        except IdentityError:
            raise
        except Exception:
            raise IdentityError('IDENTITY_PROVIDER_UNAVAILABLE', 'Cannot resolve request identity') from None
        service = Service(self._database, principals={user.actor: user.principal()})
        service.embedding_provider = self._embedding_provider
        try:
            yield RequestSession(service, user, self._identities.mode, agent_registrations=self._agent_registrations)
        finally:
            service.close()

    def apply_approved(self, draft_id, version, *, request_key):
        """Trusted workflow entry, never an Agent tool or user-selectable actor.

        Refresh the stored approver's permissions. Service rechecks approval,
        exact version/hash, current base and graph validity inside its transaction.
        No latest-version substitution or automatic approval is performed here.
        """
        service = Service(self._database, principals={
            WORKFLOW_ACTOR: {'kind': 'system', 'roles': ['read', 'apply']},
        })
        try:
            draft = service.get_draft(WORKFLOW_ACTOR, draft_id, version)
            if not draft['ok']:
                return draft
            # This is recovery of a completed write, not a fresh approval. Keep
            # Service's exact-key replay / ALREADY_APPLIED behavior available
            # even when the identity provider is temporarily unavailable.
            if draft['data']['status'] == 'applied':
                return service.apply(WORKFLOW_ACTOR, draft_id, version, request_key=request_key)
            reviewer = draft['data']['reviewer']
            if isinstance(reviewer, str) and reviewer.startswith('user:'):
                subject = reviewer[len('user:'):]
                try:
                    user = _identity(self._identities.lookup(subject), subject)
                    service.principals[user.actor] = user.principal()
                except IdentityError:
                    # Missing/revoked identities must fail the existing permission
                    # check; no stale permission cache or development fallback.
                    pass
                except Exception:
                    def unavailable():
                        raise DomainError('IDENTITY_PROVIDER_UNAVAILABLE', 'Cannot verify current approver permissions')
                    # Preserve apply's request hash so a concurrent completed
                    # retry can still replay its success under the store lock.
                    return service._run(WORKFLOW_ACTOR, 'apply',
                        {'draft_id': draft_id, 'version': version}, unavailable, permission='apply', request_key=request_key)
            return service.apply(WORKFLOW_ACTOR, draft_id, version, request_key=request_key)
        finally:
            service.close()


class RequestSession:
    """Host-facing operations for one resolved person, not an authenticated session."""

    def __init__(self, service, identity, identity_mode, *, agent_registrations=None):
        self._service, self._identity = service, identity
        self._identity_mode = identity_mode
        self._agent_registrations = MappingProxyType(dict(agent_registrations or {}))

    @property
    def context(self):
        return {'subject': self._identity.subject, 'actor': self._identity.actor,
                'identity_mode': self._identity_mode, 'permissions': sorted(self._identity.permissions)}

    def call(self, tool, arguments):
        """User query/draft path; no identity, review, apply or seed in arguments."""
        return AgentGateway(self._service, self._identity.actor).call(tool, arguments)

    def _role_tools(self, role):
        if isinstance(role, str):
            if role in ROLE_TOOLS:
                return ROLE_TOOLS[role]
            if role in self._agent_registrations:
                return self._agent_registrations[role].allowed_tools
        raise ValueError('Select a built-in role or a trusted host registration')

    def agent(self, role):
        """Bind a configured role to this user; it cannot exceed user permissions."""
        return AgentGateway(self._service, self._identity.actor, allowed_tools=self._role_tools(role))

    def run_agent(self, role, provider, messages, *, config, capture_tool_trace=False):
        allowed = self._role_tools(role)
        registration = self._agent_registrations.get(role)
        if registration is not None and isinstance(messages, list):
            # Keep existing built-in prompts/callers unchanged. The caller is
            # trusted host code; end-user text belongs in a user message only.
            messages = [{'role': 'system', 'content':
                'Registered host role: ' + registration.name + '\n' + registration.instructions}, *messages]
        return ModelRuntime(self._service, self._identity.actor, provider, config=config,
                            allowed_tools=allowed, capture_tool_trace=capture_tool_trace).run(messages)

    def review(self, draft_id, version, *, decision, note, request_key):
        """Explicit host action by the resolved user, never part of Agent tools."""
        return self._service.review(self._identity.actor, draft_id, version,
                                    decision=decision, note=note, request_key=request_key)

    def intake(self, filename, content_base64):
        from .receipts import source_intake
        return source_intake(self._service, self._identity.actor, filename, content_base64)

    def source_draft(self, **arguments):
        from .receipts import source_draft
        return source_draft(self._service, self._identity.actor, **arguments)

    def source_compare(self, **arguments):
        from .receipts import source_compare
        return source_compare(self._service, self._identity.actor, **arguments)

    def workspace(self):
        from .receipts import workspace
        return workspace(self._service, self._identity.actor)

    def history_intake(self, filename, content_base64):
        from .receipts import history_intake
        return history_intake(self._service, self._identity.actor, filename, content_base64)

    def record_agent_check(self, runtime_id, *, accepted, code=None, display_hash=None):
        def act():
            if not accepted:
                raise DomainError(code or 'AI_RESULT_MISMATCH', 'Model answer verification failed')
            return self._service._envelope(ok=True, data={'runtime_id': runtime_id, 'accepted': True})
        return self._service._run(self._identity.actor, 'agent_answer_validation',
                                 {'runtime_id': runtime_id, 'accepted': accepted, 'code': code, 'display_hash': display_hash}, act)
