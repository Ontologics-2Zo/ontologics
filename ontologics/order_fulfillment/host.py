"""Order Fulfillment composition of shared identity, approval and execution code."""
from contextlib import contextmanager
from dataclasses import dataclass

from ..host import (AgentRegistration as CommonAgentRegistration,
                    CommonHost as BaseCommonHost,
                    RequestSession as CommonRequestSession, WORKFLOW_ACTOR, _identity)
from ..identity import IdentityError
from ..harness import ExecutionBudget, request_budget
from ..service import DomainError
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
        if self.spec is not None:
            # Typed registration remains inside the public common-tool contract.
            CommonAgentRegistration(self.name, self.instructions, self.allowed_tools, self.spec)


class _RequestGateway(AgentGateway):
    """Apply the common request counters to direct B gateway calls."""

    def __init__(self, *args, budget, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_budget = budget

    def call(self, tool, arguments):
        self._request_budget.consume('tool')
        result = super().call(tool, arguments)
        self._request_budget.check()
        return result


class RequestSession(CommonRequestSession):
    """Select B gateways while inheriting all person-bound host operations."""

    def call(self, tool, arguments):
        self._budget.consume('tool')
        result = AgentGateway(self._service, self._identity.actor).call(tool, arguments)
        self._budget.check()
        return result

    def agent(self, role):
        return _RequestGateway(self._service, self._identity.actor,
                               allowed_tools=self._role_tools(role), budget=self._budget)

    def run_agent(self, role, provider, messages, *, config, capture_tool_trace=False,
                  pinned_context=None, final_validator=None, budget=None,
                  tool_observer=None, task_tools=None):
        selected_budget = self._budget if budget is None else budget
        if not isinstance(selected_budget, ExecutionBudget) or not selected_budget.is_descendant_of(self._budget):
            raise DomainError('EXECUTION_CONTEXT_MISMATCH', 'A role cannot replace its request execution budget')
        allowed = self._role_tools(role)
        if task_tools is not None:
            if not set(task_tools) <= allowed:
                raise DomainError('TOOL_NOT_ALLOWED', 'Task tools exceed the registered role')
            allowed = frozenset(task_tools)
        registration = self._agent_registrations.get(role)
        if registration is not None and isinstance(messages, list):
            messages = [{'role': 'system', 'content':
                'Registered host role: ' + registration.name + '\n' + registration.instructions},
                *messages]
        return ModelRuntime(self._service, self._identity.actor, provider, config=config,
                            allowed_tools=allowed, capture_tool_trace=capture_tool_trace,
                            budget=selected_budget, pinned_context=pinned_context,
                            final_validator=final_validator, tool_observer=tool_observer).run(messages)


class CommonHost(BaseCommonHost):
    """Resolve one user, construct a B service and close it per request."""

    @contextmanager
    def request(self, context, *, budget=None):
        try:
            user = _identity(self._identities.resolve(context))
        except IdentityError:
            raise
        except Exception:
            raise IdentityError('IDENTITY_PROVIDER_UNAVAILABLE', 'Cannot resolve request identity') from None
        service = Service(self._database, principals={user.actor: user.principal()})
        service.embedding_provider = self._embedding_provider
        service.sandbox_client = self._sandbox_client
        try:
            with request_budget(user.actor, budget, cost_policy=self._cost_policy) as shared_budget:
                yield RequestSession(service, user, self._identities.mode,
                                     agent_registrations=self._agent_registrations,
                                     budget=shared_budget, identity_lookup=self._identities.lookup)
        finally:
            service.close()
