"""Order Fulfillment tool schemas; common role schemas remain unchanged."""
from copy import deepcopy

from ..model import SCHEMA
from ..tool_specs import (TEXT, VERSION, obj, PRODUCT_TOOLS, QUALITY_TOOLS,
                          KNOWLEDGE_TOOLS, SUPERVISOR_TOOLS,
                          function_tools as common_function_tools)
from .service import AgentGateway

_B_DEFINITIONS = {
    'lookup_many': ('Read all 1..16 unique node IDs of one kind from one explicit confirmed version. Atomic: any missing or wrong-kind ID fails the entire read. Does not mutate facts or approvals.',
        obj({'node_ids': {'type': 'array', 'items': TEXT, 'minItems': 1, 'maxItems': 16},
             'kind': {'type': 'string', 'enum': list(SCHEMA)}, 'version': VERSION}, ['node_ids', 'kind', 'version'])),
    'compare_response_plan': ('Compare two explicit approved data versions and as_of instants for the same unchanged contract promise and allocation plan. Recomputes each full response plan using current common rules, preserves both drafts and provenance, and returns after-minus-before quantity/time differences; null is not zero. Read-only: no unapproved preview, approval, sending or dispatch execution.',
        obj({'delivery_round_id': TEXT, 'before_version': VERSION, 'before_as_of': TEXT,
             'after_version': VERSION, 'after_as_of': TEXT},
            ['delivery_round_id', 'before_version', 'before_as_of', 'after_version', 'after_as_of'])),
    'delivery_round': ('Read one synthetic contract promise in an explicit data version: customer, configuration, quantity, arrival deadline and destination with evidence. Does not assess fulfillment feasibility.',
        obj({'delivery_round_id': TEXT, 'version': VERSION}, ['delivery_round_id', 'version'])),
    'fulfillment_status': ('Read one exact source-backed fulfillment snapshot at explicit as_of and data version. Count delivered, in-transit, warehouse and unfinished production allocations once; missing coverage is unknown, never zero. Does not forecast future fulfillment.',
        obj({'delivery_round_id': TEXT, 'as_of': TEXT, 'version': VERSION}, ['delivery_round_id', 'as_of', 'version'])),
    'delivery_forecast': ('Read a conditional arrival forecast for one round at explicit as_of and confirmed data version. Uses source-backed dispatch preparation, transport and split/all-at-once conditions; counts each physical allocation once. Missing coverage or future observations stays unknown. Does not establish actual arrival or mutate shipment, approval or promise facts.',
        obj({'delivery_round_id': TEXT, 'as_of': TEXT, 'version': VERSION}, ['delivery_round_id', 'as_of', 'version'])),
    'response_plan': ('Read reviewer-only customer reply and unsent dispatch/transport request-content drafts for one round, explicit as_of and confirmed version. Reuses the common conditional arrival forecast. Excludes delivered and in-transit material from new dispatch, preserves unknowns and all-at-once conditions, and routes past deadlines to historical review. Does not send messages, register requests, approve, or change promises, shipments or operational plans.',
        obj({'delivery_round_id': TEXT, 'as_of': TEXT, 'version': VERSION}, ['delivery_round_id', 'as_of', 'version'])),
    'production_completion': ('Read a conditional production-only completion estimate for one fully allocated work and round at explicit as_of and confirmed data version. Uses latest two valid cumulative observations and exact common arithmetic; missing/stale/unsupported scope stays unknown. Does not establish actual completion, arrival or delivery feasibility; never writes forecast facts.',
        obj({'production_work_id': TEXT, 'delivery_round_id': TEXT, 'as_of': TEXT, 'version': VERSION}, ['production_work_id', 'delivery_round_id', 'as_of', 'version'])),
}

B_INTAKE_TOOLS = ('lookup', 'prepare_draft', 'get_draft', 'validate_draft')
B_PROMISE_TOOLS = ('lookup', 'delivery_round')
B_FULFILLMENT_TOOLS = ('fulfillment_status', 'lookup')
B_PRODUCTION_TOOLS = ('production_completion', 'lookup')
B_DELIVERY_TOOLS = ('delivery_forecast', 'lookup_many')
B_RESPONSE_TOOLS = ('response_plan', 'lookup_many')
B_COMPARISON_TOOLS = ('compare_response_plan', 'lookup_many')


def function_tools(names):
    """Build B and common schemas without mutating shared definitions."""
    if not names or len(set(names)) != len(names) or not set(names) <= AgentGateway.ALLOWED:
        raise ValueError('Select unique tools from the Order Fulfillment gateway')
    result = []
    for name in names:
        if name in _B_DEFINITIONS:
            description, parameters = _B_DEFINITIONS[name]
            spec = {'type': 'function', 'name': name, 'description': description,
                    'parameters': parameters, 'strict': False}
        else:
            spec, = common_function_tools((name,))
        result.append(deepcopy(spec))
    return result
