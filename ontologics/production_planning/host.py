"""A request host: shared identity/session boundaries with A persistence.

Service construction is explicit; common host and model globals stay unchanged.
"""
from contextlib import contextmanager
from ..host import CommonHost, RequestSession, WORKFLOW_ACTOR, _identity
from ..identity import IdentityError
from ..harness import request_budget
from ..service import DomainError
from .persistence import PlanningService as Service

class PlanningHost(CommonHost):
    @contextmanager
    def request(self, context, *, budget=None):
        # Snapshot once. Neither model arguments nor later user selection changes it.
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
                yield RequestSession(service, user, self._identities.mode, agent_registrations=self._agent_registrations, budget=shared_budget, identity_lookup=self._identities.lookup)
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
