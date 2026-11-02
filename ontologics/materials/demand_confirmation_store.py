"""Host-only persisted demand review, using the existing server extension pattern.

Like retrieval.EvidenceIndex, all domain SQL runs INSIDE Service._run. Existing
permission checks, schema lock, transactions, requests replay and runs audit are
reused unchanged. These internal dependencies are intentional and regression
tested. The explicit A Service reuses that execution engine; no Agent write tool is added.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

from ..identity import IdentityError, UserIdentity
from ..model import canonical, digest
from ..service import DomainError
from .persistence import MaterialsService as Service
from .contract_intake import extract_contract_text, read_contract_demand
from .demand_confirmation import DemandConfirmationWorkflow, PreparedDemandTarget
from .demand_review import build_demand_review

DDL = '''
CREATE TABLE IF NOT EXISTS demand_review_revisions(
 demand_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
 data_version INTEGER NOT NULL REFERENCES snapshots(version),
 author TEXT NOT NULL, identity_mode TEXT NOT NULL,
 payload JSONB NOT NULL, target_sha256 TEXT NOT NULL,
 source_bytes BYTEA NOT NULL, extraction JSONB NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(demand_id,revision));
CREATE TABLE IF NOT EXISTS demand_review_events(
 id TEXT PRIMARY KEY, demand_id TEXT NOT NULL, revision INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('confirm','supersede')),
 payload JSONB NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
 sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
 FOREIGN KEY(demand_id,revision) REFERENCES demand_review_revisions(demand_id,revision));
'''


def _scope_order(scopes):
    if (isinstance(scopes, list) and all(isinstance(s, dict) and set(s) == {'item_id', 'scope'} and
            all(isinstance(v, str) for v in s.values()) for s in scopes)):
        return sorted(deepcopy(scopes), key=lambda s: (s['item_id'], s['scope']))
    return deepcopy(scopes)


class DemandConfirmationStore:
    """Trusted server extension over a real Service, not a model tool adapter."""
    def __init__(self, service, bound, identity_provider):
        self.service, self.bound, self.identities = service, bound, identity_provider
        self.actor = bound['actor']

    def _current_user(self, subject, *, permission=None):
        try:
            user = self.identities.lookup(subject)
        except IdentityError:
            if subject != self.bound['subject']:
                return None
            raise DomainError('FORBIDDEN', 'Current request identity is no longer available') from None
        except Exception:
            raise DomainError('IDENTITY_PROVIDER_UNAVAILABLE', 'Cannot verify current user permissions') from None
        if not isinstance(user, UserIdentity) or user.subject != subject:
            raise DomainError('INVALID_IDENTITY', 'Provider must return the exact requested subject')
        if permission is not None:
            permissions = user.permissions & frozenset(self.bound['permissions'])
            self.service.principals[user.actor] = UserIdentity(user.subject, permissions).principal()
            self.service._require(user.actor, permission)
        return user

    def _row(self, demand_id, revision=None):
        if not isinstance(demand_id, str) or not 1 <= len(demand_id) <= 200:
            raise DomainError('INVALID_INPUT', 'Use a persisted demand ID')
        if revision is not None and (type(revision) is not int or revision < 1):
            raise DomainError('INVALID_VERSION', 'Use an exact positive revision')
        if self.service.db.execute("SELECT to_regclass('demand_review_revisions') AS name").fetchone()['name'] is None:
            raise DomainError('DEMAND_NOT_FOUND', 'No demand review has been saved')
        latest = self.service.db.execute('SELECT MAX(revision) AS revision FROM demand_review_revisions WHERE demand_id=%s',
                                         (demand_id,)).fetchone()['revision']
        if latest is None:
            raise DomainError('DEMAND_NOT_FOUND', 'Demand review not found')
        row = self.service.db.execute('SELECT * FROM demand_review_revisions WHERE demand_id=%s AND revision=%s',
                                      (demand_id, latest if revision is None else revision)).fetchone()
        if row is None:
            raise DomainError('DEMAND_NOT_FOUND', 'Exact demand revision not found')
        return dict(row), latest

    @staticmethod
    def _integrity(row):
        try:
            payload, extraction = row['payload'], deepcopy(row['extraction'])
            source = bytes(row['source_bytes'])
            report = payload['report']
            valid = (digest(payload) == row['target_sha256'] and
                payload['author_actor'] == row['author'] and payload['identity_mode'] == row['identity_mode'] and
                UserIdentity(payload['author_subject'], frozenset()).actor == row['author'] and
                report['binding']['data_version'] == row['data_version'] and
                hashlib.sha256(source).hexdigest() == report['source']['sha256'] and
                len(source) == report['source']['byte_length'] and
                canonical(extraction['source']) == canonical(report['source']) and
                canonical(extraction['text']) == canonical(report['original']['text']) and
                canonical(extraction['segments']) == canonical(report['original']['segments']))
        except (TypeError, ValueError, KeyError):
            valid = False
        if not valid:
            raise DomainError('DEMAND_INTEGRITY', 'Stored demand payload/source is inconsistent')
        extraction['source_bytes'] = source
        target = PreparedDemandTarget(report['source']['path'], row['data_version'], payload['author_subject'],
            row['identity_mode'], canonical(report['interpretation']), canonical(payload), row['target_sha256'])
        return target, extraction

    def _append_event(self, demand_id, revision, kind, payload):
        event = dict(id=str(uuid.uuid4()), demand_id=demand_id, revision=revision,
                     kind=kind, payload=deepcopy(payload), created_at=Service.now())
        checksum = digest(event)
        self.service.db.execute('''INSERT INTO demand_review_events
            (id,demand_id,revision,kind,payload,content_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)''',
            (event['id'], demand_id, revision, kind, canonical(payload), checksum, event['created_at']))
        return event

    def save(self, target, extraction, *, demand_id, expected_revision, request_key, preflight_error=None):
        args = dict(target_payload=target.payload_json if isinstance(target, PreparedDemandTarget) else None,
                    demand_id=demand_id, expected_revision=expected_revision)
        def act():
            self._current_user(self.bound['subject'], permission='draft')
            self.service._require(self.actor, 'read')
            if preflight_error:
                raise preflight_error
            if not isinstance(target, PreparedDemandTarget):
                raise DomainError('INVALID_CONFIRMATION_TARGET', 'Save a freshly prepared host target')
            payload = json.loads(target.payload_json)
            if (payload['author_actor'] != self.actor or target.author_subject != self.bound['subject'] or
                    target.identity_mode != self.bound['identity_mode']):
                raise DomainError('DRAFT_OWNER_REQUIRED', 'Only the bound author may save this target')
            self.service._nodes(target.data_version)  # Exact snapshot must still exist.
            self.service.db.execute(DDL)
            revision, did = 1, demand_id or str(uuid.uuid4())
            if demand_id is None:
                if expected_revision is not None:
                    raise DomainError('INVALID_VERSION', 'A new demand has no previous revision')
            else:
                if type(expected_revision) is not int or expected_revision < 1:
                    raise DomainError('INVALID_VERSION', 'Updating a demand requires expected_revision')
                old, latest = self._row(demand_id)
                self._integrity(old)
                if old['author'] != self.actor:
                    raise DomainError('DRAFT_OWNER_REQUIRED', 'Only the saved author may revise demand')
                if expected_revision != latest:
                    raise DomainError('STALE_REVISION', 'Demand changed; reload and review again')
                if old['target_sha256'] == target.sha256:
                    raise DomainError('NO_CHANGES', 'Reuse the identical saved revision')
                revision = latest + 1
                self._append_event(did, latest, 'supersede', dict(actor=self.actor,
                    next_revision=revision, next_target_sha256=target.sha256))
            metadata = deepcopy(extraction)
            raw = metadata.pop('source_bytes')
            check = dict(payload=payload, extraction=metadata, source_bytes=raw,
                target_sha256=target.sha256, author=self.actor, identity_mode=target.identity_mode,
                data_version=target.data_version)
            self._integrity(check)
            self.service.db.execute('''INSERT INTO demand_review_revisions
                (demand_id,revision,data_version,author,identity_mode,payload,target_sha256,source_bytes,extraction,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (did, revision, target.data_version, self.actor, target.identity_mode, canonical(payload),
                 target.sha256, raw, canonical(metadata), Service.now()))
            return self.service._envelope(ok=True, data=dict(demand_id=did, revision=revision,
                target_sha256=target.sha256, source_preserved=True), version=target.data_version)
        return self.service._run(self.actor, 'save_demand_review', args, act, permission='draft', request_key=request_key)

    def load(self, demand_id, revision=None):
        def act():
            self._current_user(self.bound['subject'], permission='read')
            row, latest = self._row(demand_id, revision)
            self._integrity(row)
            return self.service._envelope(ok=True, data=dict(row=row, current_revision=latest), version=row['data_version'])
        # Internal host payload contains source bytes; never expose it as a tool
        # or use a request key (which would JSON-cache bytes in requests).
        return self.service._run(self.actor, 'load_demand_review', dict(demand_id=demand_id, revision=revision), act)

    def confirm(self, demand_id, revision, *, expected_target_sha256, scopes, note, request_key,
                intent=None, preflight_error=None):
        args = dict(demand_id=demand_id, revision=revision, expected_target_sha256=expected_target_sha256,
                    scopes=_scope_order(scopes), note=note)
        def act():
            user = self._current_user(self.bound['subject'], permission='review')
            self.service._require(self.actor, 'read')
            row, latest = self._row(demand_id, revision)
            self._integrity(row)
            if latest != revision:
                raise DomainError('STALE_REVISION', 'Only the current demand revision can receive a new confirmation')
            if row['target_sha256'] != expected_target_sha256:
                raise DomainError('DEMAND_TARGET_CHANGED', 'Confirm the exact displayed target hash')
            if row['author'] == self.actor:
                raise DomainError('SELF_APPROVAL_FORBIDDEN', 'Author and reviewer must differ')
            if preflight_error:
                raise preflight_error
            if (intent is None or intent['reviewer_actor'] != self.actor or
                    canonical(intent['target']) != canonical(row['payload']) or
                    intent['authorization']['identity_mode'] != row['identity_mode'] or
                    self.bound['identity_mode'] != row['identity_mode']):
                raise DomainError('DEMAND_TARGET_CHANGED', 'Preflight must match this stored target and reviewer')
            event = self._append_event(demand_id, revision, 'confirm', dict(
                reviewer_subject=user.subject, reviewer_actor=user.actor, identity_mode=row['identity_mode'],
                permissions=sorted(user.permissions), target_sha256=expected_target_sha256,
                scopes=deepcopy(intent['scopes']), note=note))
            return self.service._envelope(ok=True, data=dict(recorded=True, event_id=event['id'],
                demand_id=demand_id, revision=revision, target_sha256=expected_target_sha256,
                recorded_scopes=event['payload']['scopes'], recorded_at=event['created_at'],
                identity_mode=row['identity_mode'], current_validity_requires_lookup=True), version=row['data_version'])
        # Successful replay is a historical acknowledgement. Because preflight
        # errors are raised only inside act, a completed same-key write remains
        # recoverable after a later source/revision change, as in common apply.
        return self.service._run(self.actor, 'confirm_demand_review', args, act, permission='review', request_key=request_key)

    def evaluate(self, demand_id, revision, query):
        def act():
            self._current_user(self.bound['subject'], permission='read')
            row, latest = self._row(demand_id, revision)
            self._integrity(row)
            report = query['report']
            if report is None:
                raise DomainError('PUBLIC_QUERY_FAILED', 'Exact public query failed', query['query'])
            if canonical(report) != canonical(row['payload']['report']):
                raise DomainError('DEMAND_INTEGRITY', 'Recomputed source/claims/catalog differ from the stored target')
            view = build_demand_review(query)
            if view['status'] != 'needs_confirmation':
                raise DomainError('DEMAND_INTEGRITY', 'Stored demand no longer passes original checks')
            events, verified = [], set()
            for raw_event in self.service.db.execute('''SELECT * FROM demand_review_events
                    WHERE demand_id=%s AND revision=%s ORDER BY sequence''', (demand_id, row['revision'])):
                event = dict(raw_event)
                if digest({k: event[k] for k in ('id', 'demand_id', 'revision', 'kind', 'payload', 'created_at')}) != event['content_hash']:
                    raise DomainError('DEMAND_INTEGRITY', 'Stored confirmation event content differs from its hash')
                state = 'superseded'
                if event['kind'] == 'confirm' and row['revision'] == latest:
                    p = event['payload']
                    user = self._current_user(p['reviewer_subject'])
                    valid = (user is not None and {'read', 'review'} <= user.permissions and
                        user.actor == p['reviewer_actor'] and user.actor != row['author'] and
                        p['target_sha256'] == row['target_sha256'] and
                        p['identity_mode'] == row['identity_mode'] == self.bound['identity_mode'] == self.identities.mode)
                    state = 'effective' if valid else 'permission_revoked'
                    if valid:
                        verified.update((s['item_id'], s['scope']) for s in p['scopes'])
                event['effective_state'] = state
                events.append(event)
            required = {(i['contract_item_candidate']['id'], s['scope']) for i in view['items'] for s in i['review_scopes']}
            if not verified <= required:
                raise DomainError('DEMAND_INTEGRITY', 'Stored event names an unknown item or scope')
            pending = required - verified
            full = bool(required) and not pending and row['revision'] == latest
            status = ('superseded' if row['revision'] != latest else 'confirmed' if full else
                      'partially_confirmed' if verified else 'needs_confirmation')
            def pairs(values):
                return [dict(item_id=item_id, scope=scope) for item_id, scope in sorted(values)]
            recorded = {(s['item_id'], s['scope']) for event in events if event['kind'] == 'confirm'
                        for s in event['payload']['scopes']}
            for item in view['items']:
                for scope in item['review_scopes']:
                    pair = (item['contract_item_candidate']['id'], scope['scope'])
                    scope['confirmation'] = ('verified' if pair in verified else
                                             'invalidated' if pair in recorded else 'not_recorded')
            # The immutable intake report correctly describes its earlier phase.
            # Do not present its missing-persistence diagnostic as current state.
            unresolved = [deepcopy(r) for r in report['unresolved'] if r['code'] != 'HUMAN_CONFIRMATION_UNAVAILABLE']
            questions = [deepcopy(r) for r in report['questions'] if r['code'] != 'HUMAN_CONFIRMATION_UNAVAILABLE']
            if pending:
                unresolved.append(dict(path='confirmation', code='DEMAND_CONFIRMATION_PENDING',
                    message='Outstanding item scopes require confirmation or source supplementation', scopes=pairs(pending)))
                questions.append(dict(path='confirmation', code='DEMAND_CONFIRMATION_PENDING',
                    question='Which outstanding scopes can be confirmed with supported source material?', scopes=pairs(pending)))
            return self.service._envelope(ok=True, data=dict(demand_id=demand_id, revision=row['revision'],
                current_revision=latest, target_sha256=row['target_sha256'], identity_mode=row['identity_mode'],
                status=status, source=report['source'], original=report['original'], items=view['items'],
                errors=report['errors'], unresolved=unresolved, questions=questions, events=events, intake_report=deepcopy(report),
                confirmation=dict(verified=full, state=status, verified_scopes=pairs(verified), pending_scopes=pairs(pending))),
                version=row['data_version'])
        return self.service._run(self.actor, 'get_demand_review', dict(demand_id=demand_id, revision=revision), act)


class PersistentDemandConfirmationWorkflow:
    """Compose the existing host and same-database server extension.

    User identity comes ONLY from the existing host request. Service is the
    A persistence adapter, with that trusted user principal, never an Agent argument.
    Queries/preflight occur outside mutation transactions; persisted checks and
    events/replay/audit are inside Service._run's original schema lock.
    """
    def __init__(self, host, identity_provider, database):
        self.host, self.identities, self.database = host, identity_provider, database
        self.preflight = DemandConfirmationWorkflow(host, identity_provider)

    @contextmanager
    def _request(self, context):
        with self.host.request(context) as session:
            bound = session.context
            user = UserIdentity(bound['subject'], frozenset(bound['permissions']))
            service = Service(self.database, principals={user.actor: user.principal()})
            try:
                yield session, DemandConfirmationStore(service, bound, self.identities)
            finally:
                service.close()

    def save(self, context, target, *, request_key, demand_id=None, expected_revision=None):
        failure, extraction = None, None
        try:
            if not isinstance(target, PreparedDemandTarget):
                raise DomainError('INVALID_CONFIRMATION_TARGET', 'Use a host-prepared target')
            fresh = self.preflight.prepare(context, Path(target.source_path), json.loads(target.interpretation_json),
                                           data_version=target.data_version)
            if fresh['target'] is None or fresh['target'] != target:
                raise DomainError('DEMAND_TARGET_CHANGED', 'Reprepare and review the source before saving')
            extraction = extract_contract_text(Path(target.source_path))
        except DomainError as error:
            failure = error
        except (OSError, ValueError, TypeError):
            failure = DomainError('DEMAND_TARGET_CHANGED', 'Host source/target is no longer available or consistent')
        with self._request(context) as (_, store):
            return store.save(target, extraction, demand_id=demand_id, expected_revision=expected_revision,
                              request_key=request_key, preflight_error=failure)

    def confirm(self, context, demand_id, revision, *, expected_target_sha256, scopes, note, request_key):
        with self._request(context) as (_, store):
            loaded = store.load(demand_id, revision)
            if not loaded['ok']:
                return loaded
            target, _ = store._integrity(loaded['data']['row'])
        intent, failure = None, None
        try:
            intent = self.preflight.build_submission(context, target, expected_target_sha256=expected_target_sha256,
                scopes=scopes, note=note, request_key=request_key)
        except DomainError as error:
            failure = error
        with self._request(context) as (_, store):
            return store.confirm(demand_id, revision, expected_target_sha256=expected_target_sha256,
                scopes=scopes, note=note, request_key=request_key, intent=intent, preflight_error=failure)

    def get(self, context, demand_id, *, revision=None):
        with self._request(context) as (session, store):
            loaded = store.load(demand_id, revision)
            if not loaded['ok']:
                return loaded
            row = loaded['data']['row']
            target, extracted = store._integrity(row)
            query = read_contract_demand(session, {'data_version': target.data_version}, extracted,
                                         json.loads(target.interpretation_json))
            return store.evaluate(demand_id, row['revision'], query)
