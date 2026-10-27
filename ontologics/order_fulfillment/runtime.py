"""B gateway wiring over the unchanged common model execution loop."""
from ..runtime import ModelConfig, ModelRuntime as CommonModelRuntime
from .service import AgentGateway


class ModelRuntime(CommonModelRuntime):
    """Keep shared budgets, audits and transactions; select the B gateway locally."""

    def __init__(self, service, actor, provider, *, config: ModelConfig,
                 capture_tool_trace=False, allowed_tools=None):
        if allowed_tools is None:
            allowed_tools = tuple(AgentGateway.ALLOWED)
        if (not isinstance(allowed_tools, (list, tuple, set, frozenset))
                or not allowed_tools
                or any(not isinstance(name, str) or not name for name in allowed_tools)
                or len(set(allowed_tools)) != len(allowed_tools)
                or not set(allowed_tools) <= AgentGateway.ALLOWED):
            raise ValueError('allowed_tools must be a nonempty collection of unique exposed tool names')
        # The parent checks configuration/provider/transaction state and sets all
        # execution fields. Its common-only gateway is then replaced on this B
        # instance, without changing the common class or global allowlist.
        super().__init__(service, actor, provider, config=config,
                         capture_tool_trace=capture_tool_trace, allowed_tools=('lookup',))
        self._gateway = AgentGateway(service, actor, allowed_tools=allowed_tools)
