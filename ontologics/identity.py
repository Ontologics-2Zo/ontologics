"""Trusted host identity seam. Development selection is NOT authentication."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


USER_PERMISSIONS = frozenset({'read', 'draft', 'review', 'audit'})


class IdentityError(ValueError):
    """Safe, credential-free error before a user request can be bound."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UserIdentity:
    # Stable internal person ID, not a display name or Agent role name.
    subject: str
    permissions: frozenset[str]

    def __post_init__(self):
        if (not isinstance(self.subject, str) or not 1 <= len(self.subject) <= 120
                or self.subject != self.subject.strip()):
            raise IdentityError('INVALID_IDENTITY', 'Use a stable nonempty user subject')
        if isinstance(self.permissions, str):
            raise IdentityError('INVALID_IDENTITY', 'Permissions must be a collection')
        permissions = frozenset(self.permissions)
        if not permissions <= USER_PERMISSIONS:
            raise IdentityError('INVALID_IDENTITY', 'User permissions cannot include system capabilities')
        object.__setattr__(self, 'permissions', permissions)

    @property
    def actor(self):
        return 'user:' + self.subject

    def principal(self):
        # Human-role stand-in in development; not proof of a verified login.
        return {'kind': 'human', 'roles': sorted(self.permissions)}


class IdentityProvider(Protocol):
    mode: str

    def resolve(self, context: object) -> UserIdentity:
        """Resolve trusted request context; never take identity from tool arguments."""
        ...

    def lookup(self, subject: str) -> UserIdentity:
        """Read current permissions, including a previous approver at apply time."""
        ...


@dataclass(frozen=True)
class DevelopmentContext:
    subject: str


class DevelopmentIdentityProvider:
    mode = 'development'

    def __init__(self, users, *, enabled=False):
        if enabled is not True:
            raise IdentityError('DEVELOPMENT_IDENTITY_DISABLED', 'Explicit development opt-in is required')
        self._users = {}
        for user in users:
            if not isinstance(user, UserIdentity) or user.subject in self._users:
                raise IdentityError('INVALID_IDENTITY', 'Configure distinct UserIdentity subjects')
            self._users[user.subject] = user

    def resolve(self, context):
        if not isinstance(context, DevelopmentContext):
            raise IdentityError('INVALID_IDENTITY_CONTEXT', 'Development selection is host context, not a request body')
        return self.lookup(context.subject)

    def lookup(self, subject):
        if not isinstance(subject, str) or subject not in self._users:
            raise IdentityError('UNKNOWN_IDENTITY', 'No configured development user')
        return self._users[subject]
