"""Public operator policy and run-bound approval preparation, without key access.

The operator supplies a separate JSON file containing only the two common cost
settings. Neither approval files nor model output are a source of trusted prices.
These helpers do not enable live readiness, load credentials, or send requests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from ontologics.paid_budget import COST_ENV_KEYS, CostLedger, CostPolicy, cost_policy_from_environment
from examples.order_fulfillment.b_run_isolation import checked_path
from examples.order_fulfillment.b_run_transport import Blocked, CASES, ENDPOINT, MODEL


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


@dataclass(frozen=True)
class CostPlan:
    """A detached snapshot of trusted public configuration for one new run."""

    policy: CostPolicy
    source_sha256: str
    cases: tuple[str, ...]
    synthetic: bool = False
    total_cap_usd: str | None = None
    reservation_usd: str = field(init=False)

    def __post_init__(self):
        if (not isinstance(self.policy, CostPolicy) or not _sha(self.source_sha256)
                or type(self.synthetic) is not bool
                or not isinstance(self.cases, (list, tuple)) or not self.cases
                or any(not isinstance(case, str) for case in self.cases)
                or list(self.cases) != [case for case in CASES if case in self.cases]):
            raise Blocked('Use one canonical representative sequence and a trusted public policy snapshot')
        object.__setattr__(self, 'cases', tuple(self.cases))
        # Exercise the existing reservation rules, without provider or transport.
        reservation = CostLedger(self.policy).reserve(MODEL, 400000, 1024)
        object.__setattr__(self, 'reservation_usd', reservation.settle()['reserved_usd'])
        if self.total_cap_usd is not None:
            cap = CostPolicy(self.total_cap_usd, self.policy.prices).max_cost_usd
            if Decimal(self.policy.max_cost_usd) * len(self.cases) > Decimal(cap):
                raise Blocked('Selected representative allocations exceed the separate total budget')
            object.__setattr__(self, 'total_cap_usd', cap)

    def _body(self):
        return dict(schema_version='b-cost-plan-1', synthetic=self.synthetic,
            public_policy_sha256=self.source_sha256, policy_fingerprint=self.policy.fingerprint,
            cost_policy=asdict(self.policy), cases=list(self.cases), model=MODEL, endpoint=ENDPOINT,
            per_case_request_cap=4, total_request_cap=4 * len(self.cases), campaign_request_ceiling=40,
            max_output_tokens=1024, timeout_seconds=30, automatic_retries=0, max_repairs=0,
            minimum_input_reservation_tokens=400000, first_call_reservation_usd=self.reservation_usd,
            per_case_max_cost_usd=self.policy.max_cost_usd,
            allocated_max_cost_usd=format(Decimal(self.policy.max_cost_usd) * len(self.cases), 'f'),
            separate_total_cap_usd=self.total_cap_usd,
            basis='configured_uncached_token_rates; unknown_usage_reserved; not_invoice')

    @property
    def fingerprint(self):
        return _hash(self._body())

    def plan(self):
        """Return display/storage data; editing it cannot change this snapshot."""
        return json.loads(_json(dict(self._body(), plan_fingerprint=self.fingerprint)))


def load_public_policy(path, *, cases, synthetic=False, total_cap_usd=None):
    """Read an explicit operator-owned public JSON snapshot, never an env file.

    The caller is responsible for choosing the trusted operator path. A later
    attempt must reload that path before comparing its current plan with a gate
    and approval. Passing approval-provided settings here would violate ownership.
    """
    try:
        target = checked_path(Path(path))
        with target.open('rb') as stream:
            raw = stream.read(16385)
        values = json.loads(raw)
        if (len(raw) > 16384 or not isinstance(values, dict) or set(values) != COST_ENV_KEYS
                or any(type(value) is not str for value in values.values())):
            raise ValueError()
    except (OSError, UnicodeError, ValueError, TypeError):
        raise Blocked('Use a bounded public JSON file with exactly the two common cost settings') from None
    policy = cost_policy_from_environment(values)
    if policy is None:
        raise Blocked('Explicit operator prices and a per-request USD cap are required')
    return CostPlan(policy, hashlib.sha256(raw).hexdigest(), cases, synthetic, total_cap_usd)


def prepare_authorization(plan, *, run_id, source_sha256, input_sha256, free_gate_sha256):
    """Prepare an unapproved record; only a person's explicit action may approve."""
    if (not isinstance(plan, CostPlan) or not isinstance(run_id, str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', run_id) is None
            or not all(_sha(value) for value in (source_sha256, input_sha256, free_gate_sha256))):
        raise Blocked('Bind the policy to one run and exact source, input and completed free gate hashes')
    return dict(schema_version='b-paid-authorization-2', approved=False,
        source='explicit_user_confirmation_with_mandatory_free_gate', run_id=run_id,
        source_sha256=source_sha256, input_sha256=input_sha256, free_gate_sha256=free_gate_sha256,
        cost_plan=plan.plan())


def validate_cost_authorization(auth, plan, *, run_id, source_sha256, input_sha256, free_gate_sha256):
    """Validate against current trusted configuration; never trust auth prices."""
    expected = prepare_authorization(plan, run_id=run_id, source_sha256=source_sha256,
        input_sha256=input_sha256, free_gate_sha256=free_gate_sha256)
    expected['approved'] = True
    try:
        valid = (not plan.synthetic and isinstance(auth, dict) and auth.get('approved') is True
                 and _json(auth) == _json(expected))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise Blocked('Fresh explicit approval must match this run, free gate and current nonsynthetic policy')
    return True
