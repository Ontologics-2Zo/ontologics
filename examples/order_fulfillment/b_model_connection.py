"""B's trusted model connection profile; common runtime/provider own enforcement.

These low-level helpers do not read keys, environment files, or approvals. The
calling host must bind one approved run to its fresh database, inputs and budget
before invoking execute_case. Tests inject a local HTTP opener; this module has
no CLI and does not start a run by itself. The original scripted profiles stay
unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json

from examples.order_fulfillment.b_task_workflow import ROLES
from examples.order_fulfillment.b_input_task import catalog_evidence
from ontologics.database import DatabaseSettings
from ontologics.harness import BudgetLimits, ExecutionBudget, canonical
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.openai_provider import (
    ENDPOINT, INPUT_TOKEN_RESERVATION_FLOOR, MODEL, OpenAISettings)
from ontologics.order_fulfillment.openai_provider import OpenAIProvider
from ontologics.paid_budget import CostLedger, CostPolicy
from ontologics.runtime import ModelConfig
from ontologics.service import DomainError


CONFIG = ModelConfig(MODEL, max_steps=12, max_attempts=2, max_tool_calls=3, max_repairs=0)
ROLE_LIMITS = BudgetLimits(max_model_calls=24, max_tool_calls=6,
                          max_repairs=0, deadline_seconds=120)
SUBJECT = 'b-input-reader'


def _comparison_text_format(task, messages):
    """Offer CQ06 only pinned catalog evidence already observed in this run.

    B input provenance remains in input_sources_by_side, outside the common
    confirmed citation list. The common host still validates the raw response.
    """
    reads = [dict(name='lookup', result=json.loads(message['content']))
             for message in messages
             if message.get('role') == 'tool' and message.get('name') == 'lookup']
    ids = catalog_evidence(task.data()['catalog_nodes'], task.context.data_version, reads)
    if not ids:
        return None
    return dict(type='json_schema', name='b_comparison', strict=True, schema=dict(
        type='object', additionalProperties=False,
        required=['data_version', 'answer_ko', 'evidence_ids'], properties=dict(
            data_version=dict(type='integer'), answer_ko=dict(type='string'),
            evidence_ids=dict(type='array', items=dict(type='string', enum=ids)))))


class _ComparisonProvider(OpenAIProvider):
    def __init__(self, settings, tools, *, task, opener=None):
        super().__init__(settings, tools, opener=opener, require_initial_lookup=True)
        self.task = task
        self._final_format = None

    def public_config(self):
        return dict(super().public_config(), final_evidence_policy='observed-catalog-v1')

    def __call__(self, *, model, messages):
        self._final_format = _comparison_text_format(self.task, messages)
        return super().__call__(model=model, messages=messages)

    def _text_format(self):
        return self._final_format


def model_spec(module):
    """Register a model profile without changing the free role's contract."""
    if module not in tuple(role[0] for role in ROLES):
        raise ValueError('Select an existing B input role')
    return replace(module.SPEC, limits=ROLE_LIMITS)


def new_budget(policy, *, case_count=1, demo=False):
    """One request/campaign ledger; retries consume the same common counters.

    Twelve logical steps can each have two attempts. Normal execution uses
    the common maximum of 64; the optional demo retains its 40-attempt cap.
    Each run_task also consumes one root snapshot lookup outside the role child.
    """
    if type(case_count) is not int or not 1 <= case_count <= 10 or type(demo) is not bool:
        raise ValueError('Select one to ten fixed representatives')
    if policy is not None and not isinstance(policy, CostPolicy):
        raise ValueError('Use the host-owned common CostPolicy')
    limits = BudgetLimits(max_model_calls=min(40 if demo else 64, 24 * case_count),
        max_tool_calls=min(128, 7 * case_count), max_repairs=0, deadline_seconds=600)
    return ExecutionBudget(limits, cost_policy=policy)


def make_host(database, policy):
    if not isinstance(database, DatabaseSettings):
        raise ValueError('Pass explicit fresh development DatabaseSettings')
    if policy is not None and not isinstance(policy, CostPolicy):
        raise ValueError('Use the host-owned common CostPolicy')
    identities = DevelopmentIdentityProvider([
        UserIdentity(SUBJECT, frozenset({'read'}))], enabled=True)
    return CommonHost(identities, database, cost_policy=policy,
        agent_registrations=tuple(AgentRegistration.from_spec(model_spec(module))
                                  for module, _, _ in ROLES))


def policy_summary(policy, cases, *, demo=False):
    """Display only trusted public limits/prices, including blocked reservation.

    A cap below the conservative reservation is a preflight finding, not an
    excuse to invent a larger budget or change the common reservation policy.
    """
    from examples.order_fulfillment.b_run_transport import CASES
    if (not isinstance(cases, (list, tuple)) or not cases
            or list(cases) != [case for case in CASES if case in cases]):
        raise ValueError('Select a unique canonical fixed representative sequence')
    if not isinstance(policy, CostPolicy):
        raise ValueError('Use the host-owned common CostPolicy')
    reservation = None
    code = None
    try:
        pending = CostLedger(policy).reserve(MODEL, INPUT_TOKEN_RESERVATION_FLOOR, 4096)
        reservation = pending.settle()['reserved_usd']
    except DomainError as error:
        code = error.code
        price = next((price for price in policy.prices if price.model == MODEL), None)
        if (code == 'COST_BUDGET_EXCEEDED' and price is not None
                and price.max_input_tokens >= INPUT_TOKEN_RESERVATION_FLOOR):
            reservation = format(price.cost(price.max_input_tokens, 4096), 'f')
    return dict(schema_version='b-model-connection-1', model_config=asdict(CONFIG),
        provider_config=dict(provider='openai', endpoint=ENDPOINT, model=MODEL,
            timeout_seconds=30, max_output_tokens=4096),
        role_limits=asdict(ROLE_LIMITS),
        root_limits=asdict(new_budget(policy, case_count=len(cases), demo=demo).limits),
        policy_fingerprint=policy.fingerprint, cost_policy=asdict(policy),
        first_call_reservation_usd=reservation,
        first_call_budget_sufficient=code is None, reservation_error=code,
        minimum_input_reservation_tokens=INPUT_TOKEN_RESERVATION_FLOOR,
        cases=list(cases), actual_api_attempts=0)


def execute_case(host, case, task, role, *, settings, budget, opener=None):
    """Trusted run owner supplies approval/lifecycle and one shared budget.

    opener=None selects the real common HTTP transport; callers must not invoke
    this helper until they have obtained the separate run-bound authorization.
    """
    from examples.order_fulfillment.b_run_transport import CASES
    if case not in CASES or role not in ROLES:
        raise ValueError('Select a fixed B representative and registered role')
    if (not isinstance(settings, OpenAISettings) or settings.model != MODEL
            or settings.timeout_seconds != 30 or settings.max_output_tokens != 4096):
        raise ValueError('Use the fixed common model profile: 4096 tokens and 30 seconds')
    if not isinstance(budget, ExecutionBudget):
        raise ValueError('Pass the shared host-owned ExecutionBudget')
    module, agent, _ = role
    if module.SPEC.name == 'b_comparison':
        provider = _ComparisonProvider(settings, module.SPEC.requested_tools, task=task, opener=opener)
    else:
        provider = OpenAIProvider(settings, module.SPEC.requested_tools, opener=opener,
                                  require_initial_lookup=True)
    def observed_provider(*, model, messages):
        value = provider(model=model, messages=messages)
        # Preserve normalized output before common context/result validation,
        # including rejected final text. Never correct it or collect HTTP secrets.
        provider.calls[-1]['model_tool_calls'] = json.loads(canonical(value['tool_calls']))
        provider.calls[-1]['model_text'] = value['text']
        return value
    observed_provider.public_config = provider.public_config
    with host.request(DevelopmentContext(SUBJECT), budget=budget) as request:
        result = request.run_task(module.SPEC.name, agent(), task, observed_provider,
                                  config=CONFIG, capture_tool_trace=True)
    return json.loads(canonical(result)), json.loads(canonical(provider.calls))
