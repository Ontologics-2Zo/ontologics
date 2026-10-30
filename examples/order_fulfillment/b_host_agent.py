"""Retired stored-graph role metadata retained for pure compatibility checks.

Only b_run tasks is supported. These historical registrations are not
registered by that launcher and do not enable B graph storage.
"""
from __future__ import annotations

import sys

from examples.order_fulfillment import b_agent, b_fulfillment_agent, b_production_agent, b_delivery_agent, b_response_agent, b_comparison_agent
from examples.order_fulfillment.b_task_agent import INTAKE_SPEC
from ontologics.order_fulfillment.host import AgentRegistration
from ontologics.order_fulfillment.tool_specs import B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS, B_PRODUCTION_TOOLS, B_DELIVERY_TOOLS, B_RESPONSE_TOOLS, B_COMPARISON_TOOLS


REGISTRATIONS = (
    AgentRegistration('b_intake', b_agent.INTAKE_PROMPT, B_INTAKE_TOOLS),
    AgentRegistration('b_promise', b_agent.PROMISE_PROMPT, B_PROMISE_TOOLS),
    AgentRegistration('b_fulfillment', b_fulfillment_agent.PROMPT, B_FULFILLMENT_TOOLS),
    AgentRegistration('b_production', b_production_agent.PROMPT, B_PRODUCTION_TOOLS),
    AgentRegistration('b_delivery', b_delivery_agent.PROMPT, B_DELIVERY_TOOLS),
    AgentRegistration('b_response', b_response_agent.PROMPT, B_RESPONSE_TOOLS),
    AgentRegistration('b_comparison', b_comparison_agent.PROMPT, B_COMPARISON_TOOLS),
)

# Historical typed metadata only; supported tasks register their own seven specs.
TYPED_INTAKE_REGISTRATION = AgentRegistration.from_spec(INTAKE_SPEC)


def run_scripted_example(mode, *, database=None, identity=None, case='normal', source_path=None):
    """Reject the retired stored-graph path before accessing inputs or resources."""
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise RuntimeError(LEGACY_EXECUTION_MESSAGE)


def main():
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise SystemExit(LEGACY_EXECUTION_MESSAGE)


if __name__ == '__main__':
    sys.exit(main())
