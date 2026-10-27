"""Order Fulfillment composition of shared identity, approval and execution code."""
from contextlib import contextmanager
from dataclasses import dataclass

from ..host import (AgentRegistration as CommonAgentRegistration,
                    CommonHost as BaseCommonHost,
                    RequestSession as CommonRequestSession, WORKFLOW_ACTOR, _identity)
from ..identity import IdentityError
from .runtime import ModelRuntime
from .service import AgentGateway, Service


@dataclass(frozen=True)
class AgentRegistration(CommonAgentRegistration):
    """A host-only B role; it never grants user permissions or approval tools."""

    def __post_init__(self):
        # Reuse common name/instruction checks with an existing harmless tool.
        CommonAgentRegistration(self.name, self.instructions, ('lookup',))
        if not isinstance(self.allowed_tools, (tuple, list, set, frozenset)):
            raise ValueError('Select Agent tools as a collection')
        if (not self.allowed_tools or any(not isinstance(name, str) for name in self.allowed_tools)
                or len(set(self.allowed_tools)) != len(self.allowed_tools)
                or not set(self.allowed_tools) <= AgentGateway.ALLOWED):
            raise ValueError('Extension tools must be unique Order Fulfillment gateway tools')
        object.__setattr__(self, 'allowed_tools', frozenset(self.allowed_tools))


class RequestSession(CommonRequestSession):
    """Select B gateways while inheriting all person-bound host operations."""

    def call(self, tool, arguments):
        return AgentGateway(self._service, self._identity.actor).call(tool, arguments)

    def agent(self, role):
        return AgentGateway(self._service, self._identity.actor,
                            allowed_tools=self._role_tools(role))

    def run_agent(self, role, provider, messages, *, config, capture_tool_trace=False):
        allowed = self._role_tools(role)
        registration = self._agent_registrations.get(role)
        if registration is not None and isinstance(messages, list):
            messages = [{'role': 'system', 'content':
                'Registered host role: ' + registration.name + '\n' + registration.instructions},
                *messages]
        return ModelRuntime(self._service, self._identity.actor, provider, config=config,
                            allowed_tools=allowed, capture_tool_trace=capture_tool_trace).run(messages)


class CommonHost(BaseCommonHost):
    """Resolve one user, construct a B service and close it per request."""

    @contextmanager
    def request(self, context):
        try:
            user = _identity(self._identities.resolve(context))
        except IdentityError:
            raise
        except Exception:
            raise IdentityError('IDENTITY_PROVIDER_UNAVAILABLE', 'Cannot resolve request identity') from None
        service = Service(self._database, principals={user.actor: user.principal()})
        service.embedding_provider = self._embedding_provider
        try:
            yield RequestSession(service, user, self._identities.mode,
                                 agent_registrations=self._agent_registrations)
        finally:
            service.close()
