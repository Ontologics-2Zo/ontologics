"""Explicit service pricing and request reservations; no default dollar budget.

Prices are trusted deployment configuration, not model/user message fields.
This ledger estimates configured token charges, not a provider billing invoice.
Missing usage retains the full reservation; cache discounts are not assumed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import threading
import uuid

from .service import DomainError

COST_ENV_KEYS = frozenset({'ONTOLOGICS_REQUEST_MAX_COST_USD', 'ONTOLOGICS_MODEL_PRICES_JSON'})


def _money(value, *, positive=False):
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool) or len(str(value)) > 40:
        raise ValueError('Use a bounded decimal string for USD values')
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError('Use finite decimal USD values') from None
    if not amount.is_finite() or amount < 0 or (positive and amount == 0) or abs(amount.adjusted()) > 20:
        raise ValueError('Use finite nonnegative USD values and a positive request cap')
    return amount


def _text(amount):
    return format(amount, 'f')


@dataclass(frozen=True)
class ModelPrice:
    model: str
    input_usd_per_million: str
    output_usd_per_million: str
    max_input_tokens: int

    def __post_init__(self):
        if (not isinstance(self.model, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,120}', self.model)
                or type(self.max_input_tokens) is not int or not 1 <= self.max_input_tokens <= 2_000_000):
            raise ValueError('Use a fixed model name and a trusted input token ceiling')
        object.__setattr__(self, 'input_usd_per_million', _text(_money(self.input_usd_per_million, positive=True)))
        object.__setattr__(self, 'output_usd_per_million', _text(_money(self.output_usd_per_million)))

    def cost(self, input_tokens, output_tokens):
        return (Decimal(input_tokens) * Decimal(self.input_usd_per_million)
                + Decimal(output_tokens) * Decimal(self.output_usd_per_million)) / Decimal(1_000_000)


@dataclass(frozen=True)
class CostPolicy:
    max_cost_usd: str
    prices: tuple[ModelPrice, ...]

    def __post_init__(self):
        object.__setattr__(self, 'max_cost_usd', _text(_money(self.max_cost_usd, positive=True)))
        if (not isinstance(self.prices, (tuple, list)) or not 1 <= len(self.prices) <= 8
                or any(not isinstance(price, ModelPrice) for price in self.prices)
                or len({price.model for price in self.prices}) != len(self.prices)):
            raise ValueError('Use distinct trusted model prices')
        object.__setattr__(self, 'prices', tuple(sorted(self.prices, key=lambda price: price.model)))

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cost_policy_from_environment(values):
    """Parse already-loaded environment data. Never execute or log its contents."""
    cap, raw = (values.get(key) for key in ('ONTOLOGICS_REQUEST_MAX_COST_USD', 'ONTOLOGICS_MODEL_PRICES_JSON'))
    if not cap and not raw:
        return None
    try:
        if not cap or not isinstance(raw, str) or len(raw) > 8192:
            raise ValueError()
        prices = json.loads(raw)
        if not isinstance(prices, dict):
            raise ValueError()
        return CostPolicy(cap, tuple(ModelPrice(model=model, **fields) for model, fields in prices.items()))
    except (TypeError, ValueError, KeyError):
        raise DomainError('COST_CONFIG_INVALID', 'Set an explicit request USD cap and complete trusted model prices') from None


class CostLedger:
    """One thread-safe ledger shared by all children of a host request."""
    def __init__(self, policy):
        if not isinstance(policy, CostPolicy):
            raise ValueError('Use a trusted CostPolicy')
        self.policy = policy
        self._lock = threading.RLock()
        self._reported = Decimal(0)
        self._unknown = Decimal(0)
        self._pending = {}
        self._paid_calls = self._reported_calls = self._unknown_calls = 0
        self._exceeded = False

    def check(self):
        if self._exceeded:
            raise DomainError('COST_RESERVATION_EXCEEDED', 'Reported usage exceeded a trusted reservation; further calls are blocked')

    def reserve(self, model, minimum_input_tokens, output_tokens):
        with self._lock:
            self.check()
            price = next((price for price in self.policy.prices if price.model == model), None)
            if price is None:
                raise DomainError('MODEL_PRICE_REQUIRED', 'The fixed model has no trusted service price')
            if (type(minimum_input_tokens) is not int or price.max_input_tokens < minimum_input_tokens
                    or type(output_tokens) is not int or output_tokens < 0
                    or (output_tokens and Decimal(price.output_usd_per_million) <= 0)):
                raise DomainError('COST_RESERVATION_CONFIG_INVALID', 'Input reservation must cover the fixed provider maximum and output price')
            amount = price.cost(price.max_input_tokens, output_tokens)
            committed = self._reported + self._unknown + sum((row[0] for row in self._pending.values()), Decimal(0))
            if committed + amount > Decimal(self.policy.max_cost_usd):
                raise DomainError('COST_BUDGET_EXCEEDED', 'Remaining request cost budget cannot cover the conservative call reservation')
            key = uuid.uuid4().hex
            self._pending[key] = (amount, price, output_tokens)
            self._paid_calls += 1
            return CostReservation(self, key, amount)

    def settle(self, key, input_tokens, output_tokens):
        with self._lock:
            if key not in self._pending:
                raise ValueError('Cost reservation was already settled')
            amount, price, output_limit = self._pending.pop(key)
            if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
                self._unknown += amount
                self._unknown_calls += 1
                return {'status': 'usage_unreported_reserved', 'reserved_usd': _text(amount), 'reported_cost_usd': None}
            actual = price.cost(input_tokens, output_tokens)
            self._reported += actual
            self._reported_calls += 1
            if input_tokens > price.max_input_tokens or output_tokens > output_limit or actual > amount:
                self._exceeded = True
            return {'status': 'reservation_exceeded' if self._exceeded else 'usage_reported',
                    'reserved_usd': _text(amount), 'reported_cost_usd': _text(actual)}

    def snapshot(self):
        with self._lock:
            pending = sum((row[0] for row in self._pending.values()), Decimal(0))
            return {'schema_version': 'cost-ledger-1', 'policy_fingerprint': self.policy.fingerprint,
                    'max_cost_usd': self.policy.max_cost_usd, 'reported_cost_usd': _text(self._reported),
                    'unreported_reserved_usd': _text(self._unknown), 'in_flight_reserved_usd': _text(pending),
                    'paid_calls': self._paid_calls, 'reported_calls': self._reported_calls,
                    'unreported_calls': self._unknown_calls, 'in_flight_calls': len(self._pending),
                    'reservation_exceeded': self._exceeded,
                    'basis': 'configured_uncached_token_rates; unknown_usage_reserved; not_invoice'}

    def restore(self, snapshot):
        """Add persisted spending. Unfinished reservations become unknown spending."""
        with self._lock:
            if (not isinstance(snapshot, dict) or snapshot.get('schema_version') != 'cost-ledger-1'
                    or snapshot.get('policy_fingerprint') != self.policy.fingerprint
                    or snapshot.get('max_cost_usd') != self.policy.max_cost_usd):
                raise DomainError('COST_POLICY_MISMATCH', 'Resume requires the same trusted prices and request cost cap')
            try:
                reported = _money(snapshot['reported_cost_usd'])
                unknown = _money(snapshot['unreported_reserved_usd']) + _money(snapshot['in_flight_reserved_usd'])
                counters = [snapshot[k] for k in ('paid_calls', 'reported_calls', 'unreported_calls', 'in_flight_calls')]
                if (any(type(value) is not int or value < 0 for value in counters)
                        or counters[0] != sum(counters[1:]) or type(snapshot['reservation_exceeded']) is not bool):
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise DomainError('COST_CHECKPOINT_INVALID', 'Invalid persisted cost accounting') from None
            committed = self._reported + self._unknown + sum((row[0] for row in self._pending.values()), Decimal(0))
            if committed + reported + unknown > Decimal(self.policy.max_cost_usd) or snapshot['reservation_exceeded']:
                raise DomainError('COST_BUDGET_EXCEEDED', 'Persisted spending exhausts the configured request cost budget')
            self._reported += reported
            self._unknown += unknown
            self._paid_calls += counters[0]
            self._reported_calls += counters[1]
            self._unknown_calls += counters[2] + counters[3]


class CostReservation:
    def __init__(self, ledger, key, amount):
        self._ledger, self._key, self._amount = ledger, key, amount
        self.result = None

    def settle(self, input_tokens=None, output_tokens=None):
        if self.result is None:
            self.result = self._ledger.settle(self._key, input_tokens, output_tokens)
        return dict(self.result)
