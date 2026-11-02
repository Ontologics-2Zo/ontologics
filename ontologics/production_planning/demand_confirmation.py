"""Host-only confirmation request orchestration over existing public APIs.

No common files, private Service methods, SQL, roles or approval store are
reimplemented. The host supplies its CommonHost and the SAME IdentityProvider.
Persistence is an explicit unbound seam, not an in-memory/file fallback.
Even a storage acknowledgement does not establish verified confirmation here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

from ..identity import IdentityError, UserIdentity
from ..model import canonical, digest
from ..service import DomainError, Service
from .contract_intake import extract_contract_text, read_contract_demand
from .demand_review import build_demand_review

REQUEST_CONTRACT = 'demand-confirmation-request-0.1'


class ConfirmationPersistence(Protocol):
    """Trusted host seam; there is no production implementation in this repo.

    A real adapter must authenticate stored target/revision, recheck authority,
    atomically enforce actor/key/hash replay and concurrent revision changes,
    persist the exact target/scopes and server event time with audit, and offer
    reconnect/current-validity lookup. An ok response is only a submission ack.
    Never implement this by treating the supplied hash/dict as approval proof.
    """
    def submit(self, intent: dict) -> dict:
        ...


@dataclass(frozen=True)
class PreparedDemandTarget:
    """Immutable host-local review material, NOT a signed/authenticated token.

    Keep in trusted application code; do not deserialize it from model/user
    arguments. Python object owners are outside the host security boundary.
    """
    source_path: str
    data_version: int
    author_subject: str
    identity_mode: str
    interpretation_json: str
    payload_json: str
    sha256: str


def _payload(report, author_subject, identity_mode):
    # Use common identity formatting; this empty-permission value grants nothing.
    actor = UserIdentity(author_subject, frozenset()).actor
    return dict(contract_version=REQUEST_CONTRACT, author_subject=author_subject,
                author_actor=actor, identity_mode=identity_mode, report=deepcopy(report))


class DemandConfirmationWorkflow:
    """Compose CommonHost queries and IdentityProvider's existing permissions.

    prepare/submit are explicit trusted application actions, never Agent tools.
    This module's conservative policy separates author and reviewer and requires
    fresh preparation after any payload change. It issues no verified=true.
    """
    def __init__(self, host, identity_provider, *, persistence: ConfirmationPersistence | None = None):
        self._host = host
        self._identities = identity_provider
        self._persistence = persistence

    def _current_reviewer(self, bound):
        try:
            current = self._identities.lookup(bound['subject'])
        except IdentityError as error:
            raise DomainError(error.code, 'Current reviewer identity is unavailable') from None
        except Exception:
            raise DomainError('IDENTITY_PROVIDER_UNAVAILABLE', 'Cannot refresh current reviewer permissions') from None
        if (not isinstance(current, UserIdentity) or current.subject != bound['subject'] or
                current.actor != bound['actor'] or self._identities.mode != bound['identity_mode']):
            raise DomainError('INVALID_IDENTITY', 'Identity provider and bound request must agree')
        if not {'read', 'review'} <= (current.permissions & frozenset(bound['permissions'])):
            raise DomainError('FORBIDDEN', 'Existing read and review permissions are required')
        return current

    @staticmethod
    def _extract(path):
        try:
            return extract_contract_text(path)
        except OSError:
            raise DomainError('SOURCE_UNAVAILABLE', 'Host-selected source cannot be read') from None

    def prepare(self, context, path: Path, interpreted: dict, *, data_version: int) -> dict:
        """Read a host-allowlisted path and exact public snapshot as this user."""
        path = Path(path).resolve()
        extracted = self._extract(path)
        with self._host.request(context) as session:
            bound = session.context
            result = read_contract_demand(session, {'data_version': data_version}, extracted, interpreted)
        review = build_demand_review(result)
        target = None
        if review['status'] == 'needs_confirmation':
            ids = [item['contract_item_candidate']['id'] for item in review['items']]
            if not ids or any(not isinstance(item_id, str) or not item_id for item_id in ids) or len(ids) != len(set(ids)):
                raise DomainError('INVALID_REVIEW_ITEMS', 'Distinct explicit item IDs are required for scoped confirmation')
            payload = _payload(result['report'], bound['subject'], bound['identity_mode'])
            target = PreparedDemandTarget(str(path), data_version, bound['subject'], bound['identity_mode'],
                canonical(interpreted), canonical(payload), digest(payload))
        return dict(target=target, review=review)

    def build_submission(self, context, target: PreparedDemandTarget, *, expected_target_sha256: str,
               scopes: list[dict], note: str, request_key: str) -> dict:
        """Recheck this exact target and build an unrecorded host submission.

        The persistence module reuses this preflight outside DB transactions;
        its stored revision/authority/audit checks remain authoritative.
        """
        if not isinstance(target, PreparedDemandTarget):
            raise DomainError('INVALID_CONFIRMATION_TARGET', 'Use a target prepared in trusted host code')
        try:
            payload = json.loads(target.payload_json)
            interpreted = json.loads(target.interpretation_json)
            intact = (digest(payload) == target.sha256 == expected_target_sha256 and
                      payload == _payload(payload['report'], target.author_subject, target.identity_mode) and
                      payload['report']['source']['path'] == target.source_path and
                      payload['report']['binding']['data_version'] == target.data_version and
                      payload['report']['interpretation'] == interpreted)
        except (TypeError, ValueError, KeyError, IdentityError):
            intact = False
        if not intact:
            raise DomainError('DEMAND_TARGET_CHANGED', 'Target fields/hash differ; prepare and review again')
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
            raise DomainError('INVALID_REQUEST_KEY', 'Use a nonempty key of at most 200 characters')
        if not isinstance(note, str) or not note.strip():
            raise DomainError('INVALID_REVIEW', 'Provide an explicit review note')

        with self._host.request(context) as session:
            bound = session.context
            self._current_reviewer(bound)
            if bound['subject'] == target.author_subject:
                raise DomainError('SELF_APPROVAL_FORBIDDEN', 'Author and reviewer must differ')
            if bound['identity_mode'] != target.identity_mode:
                raise DomainError('IDENTITY_MODE_MISMATCH', 'Prepare again in the same identity environment')
            extracted = self._extract(Path(target.source_path))
            fresh = read_contract_demand(session, {'data_version': target.data_version}, extracted, interpreted)
            review = build_demand_review(fresh)
            if review['status'] == 'query_failed':
                raise DomainError('PUBLIC_QUERY_FAILED', 'Exact public query failed; no submission', fresh['query'])
            if review['status'] != 'needs_confirmation' or digest(_payload(
                    fresh['report'], target.author_subject, target.identity_mode)) != target.sha256:
                raise DomainError('DEMAND_TARGET_CHANGED', 'Source/claims/catalog differ; prepare and review again')

            if not isinstance(scopes, list) or not scopes:
                raise DomainError('INVALID_CONFIRMATION_SCOPE', 'Select explicit item/scope pairs; no implicit full confirmation')
            items = {item['contract_item_candidate']['id']: item for item in review['items']}
            selected, seen = [], set()
            for selection in scopes:
                if (not isinstance(selection, dict) or set(selection) != {'item_id', 'scope'} or
                        not all(isinstance(selection[k], str) for k in ('item_id', 'scope'))):
                    raise DomainError('INVALID_CONFIRMATION_SCOPE', 'Use only item_id and scope strings')
                item_id, scope = selection['item_id'], selection['scope']
                if item_id not in items or (item_id, scope) in seen:
                    raise DomainError('INVALID_CONFIRMATION_SCOPE', 'Use distinct scopes of an existing item')
                row = next((r for r in items[item_id]['review_scopes'] if r['scope'] == scope), None)
                if row is None or row['availability'] != 'candidate_for_review':
                    raise DomainError('SCOPE_NOT_READY', 'Selected scope requires material or resolution')
                seen.add((item_id, scope))
                selected.append(dict(item_id=item_id, scope=scope))
            selected.sort(key=lambda row: (row['item_id'], row['scope']))
            current = self._current_reviewer(bound)
            business_request = dict(target_sha256=target.sha256, scopes=selected, note=note)
            intent = dict(contract_version=REQUEST_CONTRACT, target=payload,
                reviewer_actor=current.actor, reviewer_subject=current.subject,
                authorization=dict(identity_mode=bound['identity_mode'], permissions=sorted(current.permissions)),
                **business_request, request_key=request_key, request_sha256=digest(business_request),
                requested_at=Service.now())

        return intent

    def submit(self, context, target: PreparedDemandTarget, *, expected_target_sha256: str,
               scopes: list[dict], note: str, request_key: str) -> dict:
        """Legacy unverified transport seam; prefer the persisted workflow.

        No adapter still raises PERSISTENCE_UNAVAILABLE. Raw acknowledgements
        cannot grant confirmation. build_submission is also only preflight.
        """
        intent = self.build_submission(context, target, expected_target_sha256=expected_target_sha256,
                                       scopes=scopes, note=note, request_key=request_key)
        if self._persistence is None:
            raise DomainError('PERSISTENCE_UNAVAILABLE', 'No public demand-confirmation persistence adapter is connected')
        try:
            response = self._persistence.submit(deepcopy(intent))
        except Exception:
            raise DomainError('PERSISTENCE_UNAVAILABLE', 'Persistence submission failed; inspect the host audit path') from None
        if not isinstance(response, dict) or type(response.get('ok')) is not bool:
            raise DomainError('INVALID_PERSISTENCE_RESPONSE', 'Expected a submission response with boolean ok')
        return dict(status='submitted' if response['ok'] else 'persistence_failed',
                    confirmation=dict(verified=False, state='unverified'),
                    submission=deepcopy(intent), storage_response=deepcopy(response))
