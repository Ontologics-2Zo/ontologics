"""In-process shared tools. Host-bound identities, immutable drafts, atomic application."""
from __future__ import annotations
import json
import psycopg
from .database import DatabaseSettings, connect, in_transaction, lock_store
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .model import SCHEMA_VERSION, canonical, digest, validate_graph, graph_jsonld
from . import __version__

RULE_VERSION = 'temporal-0.1'

class DomainError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

def configuration_changes(nodes, left_id, right_id):
    idx = {n['id']: n for n in nodes}
    for cid in (left_id, right_id):
        if idx.get(cid, {}).get('type') != 'Configuration': raise DomainError('NOT_FOUND', 'Unknown configuration')
    def positions(cid):
        return {p: {'component': idx[n['component_id']]['name'], 'population': n['population'],
                    'package': idx[n['component_id']].get('package'),
                    'raw_value': n['raw_value'], 'evidence': n['evidence'], 'item_id': n['id']}
                for n in nodes if n['type'] == 'BOMItem' and n['configuration_id'] == cid for p in n['positions']}
    left, right = positions(left_id), positions(right_id)
    changes = []
    for p in sorted(left.keys() | right.keys()):
        a, b = left.get(p), right.get(p)
        equal = a and b and all(a[k] == b[k] for k in ('component', 'package', 'population', 'raw_value'))
        if not equal: changes.append({'position': p, 'before': a, 'after': b})
    return changes

class Service:
    """Trusted application boundary. Never expose Service or principal setup to an LLM.

    Principals are supplied by the host's authentication adapter. Demo identities do
    not constitute production authentication. AgentGateway exposes no review/apply.
    """
    def __init__(self, database=None, *, principals, schema=None):
        self.principals = json.loads(json.dumps(principals))
        settings = (database if isinstance(database, DatabaseSettings) else
                    DatabaseSettings(database, schema or 'ontologics') if database is not None else
                    DatabaseSettings.from_environment(schema=schema))
        self.schema = settings.schema
        self.db = connect(settings)

    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat()
    def close(self): self.db.close()
    def _version(self): return self.db.execute('SELECT MAX(version) FROM snapshots').fetchone()['max']
    def _nodes(self, version=None):
        version = self._version() if version is None else version
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise DomainError('INVALID_VERSION', 'Data version must be a nonnegative integer')
        row = self.db.execute('SELECT nodes FROM snapshots WHERE version=%s', (version,)).fetchone()
        if row is None: raise DomainError('VERSION_NOT_FOUND', 'Unknown data version')
        return row['nodes']
    def _require(self, actor, permission):
        principal = self.principals.get(actor, {})
        roles = principal.get('roles', [])
        if permission not in roles: raise DomainError('FORBIDDEN', 'Identity lacks ' + permission)
        if permission == 'review' and principal.get('kind') != 'human':
            raise DomainError('HUMAN_REVIEW_REQUIRED', 'Only authenticated human reviewers can decide')
    def _envelope(self, *, ok, data=None, error=None, version=None, warnings=None, run_id=None):
        return {'ok': ok, 'data': data, 'evidence': self._evidence(data),
                'data_version': self._version() if version is None else version,
                'schema_version': SCHEMA_VERSION, 'rule_version': RULE_VERSION,
                'contract_version': __version__, 'warnings': warnings or [], 'conflicts': self._conflicts(data),
                'errors': [] if error is None else [error], 'run_id': run_id}
    @staticmethod
    def _conflicts(value):
        found = []
        def visit(v):
            if isinstance(v, dict):
                if isinstance(v.get('reasons'), list) and 'conflicting_installation_evidence' in v['reasons']:
                    found.append({'unit_id': v.get('unit_id'), 'code': 'CONFLICTING_INSTALLATION',
                        'installation_ids': v.get('unresolved_intervals', []), 'evidence': v.get('evidence', [])})
                for sub in v.values(): visit(sub)
            elif isinstance(v, list):
                for sub in v: visit(sub)
        visit(value)
        return found
    @staticmethod
    def _evidence(value):
        ids = set()
        def visit(v):
            if isinstance(v, dict):
                if isinstance(v.get('evidence'), list): ids.update(x for x in v['evidence'] if isinstance(x, str))
                for sub in v.values(): visit(sub)
            elif isinstance(v, list):
                for sub in v: visit(sub)
        visit(value)
        return sorted(ids)
    def _run(self, actor, tool, args, action, *, permission='read', request_key=None):
        run_id, started = str(uuid.uuid4()), self.now()
        if not self.db.closed and in_transaction(self.db):
            # Never commit or roll back a transaction owned by the caller.
            return {'ok': False, 'data': None, 'evidence': [], 'data_version': None,
                'schema_version': SCHEMA_VERSION, 'rule_version': RULE_VERSION, 'contract_version': __version__,
                'warnings': [{'code': 'AUDIT_NOT_RECORDED'}], 'conflicts': [],
                'errors': [{'code': 'TRANSACTION_BOUNDARY', 'message': 'Call shared tools outside caller transactions'}], 'run_id': run_id}
        request_hash = digest({'tool': tool, 'invalid_input': True})
        try:
            serialized = canonical({'tool': tool, 'args': args})
            if len(serialized.encode()) > 8_000_000:
                raise DomainError('INPUT_TOO_LARGE', 'Tool input exceeds 8 MB')
            request_hash = digest({'tool': tool, 'args': args})
            self.db.execute('BEGIN')
            lock_store(self.db, self.schema)
            self._require(actor, permission)
            if permission in ('draft', 'review', 'apply', 'seed', 'index') and request_key is None:
                raise DomainError('INVALID_REQUEST_KEY', 'All mutations require an idempotency key')
            if request_key is not None:
                if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
                    raise DomainError('INVALID_REQUEST_KEY', 'Use a nonempty key of at most 200 characters')
                previous = self.db.execute('SELECT * FROM requests WHERE actor=%s AND request_key=%s', (actor, request_key)).fetchone()
                if previous:
                    if previous['request_hash'] != request_hash:
                        raise DomainError('IDEMPOTENCY_CONFLICT', 'This key was already used for different input')
                    result = previous['response']
                    result['warnings'].append({'code': 'IDEMPOTENT_REPLAY', 'original_run_id': result['run_id']})
                    result['run_id'] = run_id
                else:
                    result = action()
                    result['run_id'] = run_id
                    self.db.execute('INSERT INTO requests VALUES(%s,%s,%s,%s)', (actor, request_key, request_hash, canonical(result)))
            else:
                result = action(); result['run_id'] = run_id
            self.db.execute('INSERT INTO runs(id,actor,tool,request_hash,status,started_at,finished_at,data_version,error_code) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (run_id, actor, tool, request_hash, 'succeeded', started, self.now(), result['data_version'], None))
            self.db.execute('COMMIT')
            return result
        except (DomainError, ValueError, TypeError, KeyError, psycopg.Error) as exc:
            if not self.db.closed and in_transaction(self.db):
                try: self.db.execute('ROLLBACK')
                except psycopg.Error: pass
            code = exc.code if isinstance(exc, DomainError) else ('STORAGE_ERROR' if isinstance(exc, psycopg.Error) else 'INVALID_INPUT')
            err = {'code': code, 'message': 'PostgreSQL operation failed' if isinstance(exc, psycopg.Error) else str(exc)}
            if isinstance(exc, DomainError) and exc.details is not None: err['details'] = exc.details
            try:
                result = self._envelope(ok=False, error=err, run_id=run_id)
                self.db.execute('INSERT INTO runs(id,actor,tool,request_hash,status,started_at,finished_at,data_version,error_code) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (run_id, actor, tool, request_hash, 'failed', started, self.now(), result['data_version'], code))
            except psycopg.Error:
                result = {'ok': False, 'data': None, 'evidence': [], 'data_version': None,
                    'schema_version': SCHEMA_VERSION, 'rule_version': RULE_VERSION, 'contract_version': __version__,
                    'warnings': [{'code': 'AUDIT_NOT_RECORDED'}], 'conflicts': [],
                    'errors': [{'code': 'STORAGE_ERROR', 'message': 'PostgreSQL unavailable; audit persistence unconfirmed'}], 'run_id': run_id}

            return result

    def bootstrap(self, actor, nodes, *, request_key):
        """Development seed only; empty database only. Not the S2 registration path."""
        def act():
            if self._version() != 0 or self._nodes(): raise DomainError('SEED_NOT_EMPTY', 'Seed can only initialize an empty development database')
            report = validate_graph(nodes)
            if not report['valid']: raise DomainError('VALIDATION_FAILED', 'Invalid seed', report)
            self.db.execute('INSERT INTO snapshots VALUES(1,%s,%s,%s)', (canonical(nodes), self.now(), 'development_seed'))
            return self._envelope(ok=True, data={'node_count': len(nodes), 'mode': 'development_seed'}, warnings=report['warnings'])
        return self._run(actor, 'bootstrap', {'nodes': nodes}, act, permission='seed', request_key=request_key)

    def lookup(self, actor, *, node_id=None, kind=None, version=None):
        def act():
            nodes = self._nodes(version)
            found = [n for n in nodes if (node_id is None or n['id'] == node_id) and (kind is None or n['type'] == kind)]
            if node_id and not found: raise DomainError('NOT_FOUND', 'Unknown node ID')
            return self._envelope(ok=True, data=found, version=version)
        return self._run(actor, 'lookup', {'node_id': node_id, 'kind': kind, 'version': version}, act)

    def configuration(self, actor, configuration_id, *, version=None):
        def act():
            nodes = self._nodes(version); idx = {n['id']: n for n in nodes}
            config = idx.get(configuration_id)
            if not config or config['type'] != 'Configuration': raise DomainError('NOT_FOUND', 'Unknown configuration')
            items = [n for n in nodes if n['type'] == 'BOMItem' and n['configuration_id'] == configuration_id]
            revision = idx[config['revision_id']]
            return self._envelope(ok=True, data={'configuration': config, 'revision': revision,
                'product': idx[revision['model_id']], 'items': items,
                'components': [idx[cid] for cid in sorted({n['component_id'] for n in items})]}, version=version)
        return self._run(actor, 'configuration', {'configuration_id': configuration_id, 'version': version}, act)

    def delivery_round(self, actor, delivery_round_id, *, version=None):
        """Read a contract promise in one explicit snapshot; no fulfillment forecast."""
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise DomainError('INVALID_VERSION', 'Delivery queries require an explicit nonnegative data version')
            if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
                raise DomainError('INVALID_INPUT', 'delivery_round_id must be nonempty text')
            idx = {n['id']: n for n in self._nodes(version)}
            round_node = idx.get(delivery_round_id)
            if not round_node or round_node['type'] != 'DeliveryRound':
                raise DomainError('NOT_FOUND', 'Unknown delivery round in the requested data version')
            item = idx[round_node['contract_item_id']]
            config = idx[item['configuration_id']]
            revision = idx[config['revision_id']]
            return self._envelope(ok=True, data={'contract': idx[item['contract_id']],
                'contract_item': item, 'delivery_round': round_node, 'configuration': config,
                'revision': revision, 'product': idx[revision['model_id']],
                'fulfillment_assessed': False}, version=version,
                warnings=[{'code': 'FULFILLMENT_NOT_ASSESSED',
                           'detail': 'Contract promise only; stock, production and transport are not assessed'}])
        return self._run(actor, 'delivery_round', {'delivery_round_id': delivery_round_id, 'version': version}, act)

    def fulfillment_status(self, actor, delivery_round_id, as_of, *, version=None):
        """Read an exact source-backed snapshot; never infer future feasibility."""
        from .fulfillment import fulfillment_status
        from .model import instant
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise DomainError('INVALID_VERSION', 'Fulfillment queries require an explicit nonnegative data version')
            if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
                raise DomainError('INVALID_INPUT', 'delivery_round_id must be nonempty text')
            try:
                instant(as_of)
            except (ValueError, TypeError) as exc:
                raise DomainError('INVALID_TIME', str(exc)) from exc
            nodes = self._nodes(version)
            if not any(n['id'] == delivery_round_id and n['type'] == 'DeliveryRound' for n in nodes):
                raise DomainError('NOT_FOUND', 'Unknown delivery round in the requested data version')
            try:
                data = fulfillment_status(nodes, delivery_round_id, as_of, queried_at=self.now())
            except ValueError as exc:
                raise DomainError('VALIDATION_FAILED', 'Invalid fulfillment graph', exc.args[0]) from exc
            return self._envelope(ok=True, data=data, version=version,
                warnings=data['warnings'] + [{'code': 'FULFILLMENT_NOT_ASSESSED',
                    'detail': 'Snapshot only; production completion and future arrival are not forecast'}])
        return self._run(actor, 'fulfillment_status',
            {'delivery_round_id': delivery_round_id, 'as_of': as_of, 'version': version}, act)

    def compare_configurations(self, actor, left_id, right_id, *, version=None, draft_id=None, draft_version=None):
        def act():
            if draft_id is not None:
                if version is not None: raise DomainError('INVALID_INPUT', 'Choose a confirmed data version or a draft preview')
                draft = self._draft(draft_id, draft_version)
                if draft['base_version'] != self._version(): raise DomainError('STALE_BASE', 'Revise against latest data before preview')
                nodes, _ = self._candidate(draft['operations'], draft['base_version'])
                report = validate_graph(nodes)
                if not report['valid']: raise DomainError('VALIDATION_FAILED', 'Invalid draft cannot be compared', report)
            else:
                if draft_version is not None: raise DomainError('INVALID_INPUT', 'draft_id is required for draft comparison')
                nodes = self._nodes(version)
            changes = configuration_changes(nodes, left_id, right_id)
            return self._envelope(ok=True, data={'left': left_id, 'right': right_id, 'changes': changes,
                'is_preview': draft_id is not None, 'draft_id': draft_id, 'draft_version': draft_version,
                'comparison_basis': 'source descriptions by reference designator; not electrical equivalence'}, version=version,
                warnings=[{'code': 'UNAPPLIED_PREVIEW'}] if draft_id else [])
        return self._run(actor, 'compare_configurations', {'left': left_id, 'right': right_id, 'version': version,
            'draft_id': draft_id, 'draft_version': draft_version}, act)

    def component_usage(self, actor, component_id, *, version=None):
        def act():
            nodes = self._nodes(version)
            if not any(n['id'] == component_id and n['type'] == 'ComponentModel' for n in nodes):
                raise DomainError('NOT_FOUND', 'Unknown source-scoped component')
            items = [n for n in nodes if n['type'] == 'BOMItem' and n['component_id'] == component_id]
            return self._envelope(ok=True, data={'component_id': component_id, 'items': items,
                'scope': 'exact component identity; unverified cross-revision equivalence is not merged'}, version=version)
        return self._run(actor, 'component_usage', {'component_id': component_id, 'version': version}, act)

    def investigate(self, actor, query, *, version=None):
        from .temporal import investigate
        def act():
            nodes = self._nodes(version)
            def group(kind): return [n for n in nodes if n['type'] == kind]
            result = investigate(query, group('ProductUnit'), group('ComponentLot'), group('Installation'), group('HistoryCoverage'))
            return self._envelope(ok=True, data=result, version=version)
        return self._run(actor, 'investigate', {'query': query, 'version': version}, act)

    def compare_investigation(self, actor, query, before_version, after_version):
        from .temporal import investigate
        def act():
            def evaluate(version):
                nodes = self._nodes(version)
                return investigate(query, *[[n for n in nodes if n['type'] == k] for k in ('ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage')])
            before, after = evaluate(before_version), evaluate(after_version)
            return self._envelope(ok=True, data={'query': query, 'before_version': before_version,
                'after_version': after_version, 'before': before, 'after': after,
                'condition_hash': digest(query)}, version=after_version)
        return self._run(actor, 'compare_investigation', {'query': query, 'before': before_version, 'after': after_version}, act)

    def export_graph(self, actor, *, version=None):
        return self._run(actor, 'export_graph', {'version': version}, lambda: self._envelope(ok=True, data=graph_jsonld(self._nodes(version)), version=version))

    def _candidate(self, operations, base_version):
        if not isinstance(operations, list) or not operations or len(operations) > 2000:
            raise DomainError('INVALID_OPERATIONS', 'Provide 1..2000 put operations')
        index = {n['id']: n for n in self._nodes(base_version)}
        changed, seen = [], set()
        for op in operations:
            if not isinstance(op, dict) or set(op) != {'op', 'node'} or op['op'] != 'put' or not isinstance(op['node'], dict):
                raise DomainError('INVALID_OPERATION', 'Only {op: put, node: object} is supported; no deletion')
            node = op['node']; nid = node.get('id')
            if not isinstance(nid, str) or not nid: raise DomainError('INVALID_NODE_ID', 'Missing node id')
            if nid in seen: raise DomainError('DUPLICATE_OPERATION', 'One operation per node per bundle')
            seen.add(nid)
            old = index.get(nid)
            if old and old['type'] != node.get('type'): raise DomainError('TYPE_CHANGE', 'An ID cannot change its type')
            if old and old['origin'] != node.get('origin'): raise DomainError('ORIGIN_CHANGE', 'An ID cannot change its real/synthetic origin')
            if old and old['type'] in ('SourceDocument', 'Evidence') and old != node:
                raise DomainError('IMMUTABLE_SOURCE', 'New source or evidence versions need new IDs')
            if old != node: changed.append({'id': nid, 'before': old, 'after': node})
            index[nid] = node
        return list(index.values()), changed

    def _draft(self, draft_id, version, *, latest=True):
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise DomainError('INVALID_DRAFT_ID', 'Draft ID must be nonempty text')
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise DomainError('INVALID_VERSION', 'Draft version must be a positive integer')
        row = self.db.execute('SELECT * FROM drafts WHERE id=%s AND version=%s', (draft_id, version)).fetchone()
        if not row: raise DomainError('DRAFT_NOT_FOUND', 'Unknown draft version')
        if latest and self.db.execute('SELECT MAX(version) FROM drafts WHERE id=%s', (draft_id,)).fetchone()['max'] != version:
            raise DomainError('SUPERSEDED_DRAFT', 'A newer draft version exists')
        d = dict(row)
        if digest(d['operations']) != d['content_hash']: raise DomainError('DRAFT_INTEGRITY', 'Draft content hash mismatch')
        d['review_history'] = [dict(event) for event in self.db.execute(
            'SELECT * FROM review_events WHERE draft_id=%s AND draft_version=%s ORDER BY sequence', (draft_id, version))]
        return d

    def _save_draft(self, actor, operations, draft_id=None, expected_version=None):
        base = self._version()
        nodes, changes = self._candidate(operations, base)
        report = validate_graph(nodes)
        if not changes: raise DomainError('NO_CHANGES', 'The exact content already exists; reuse the existing registration')
        did, dv = draft_id or str(uuid.uuid4()), 1
        if draft_id:
            old = self._draft(draft_id, expected_version)
            if old['author'] != actor: raise DomainError('DRAFT_OWNER_REQUIRED', 'Only the original author may revise this draft')
            if old['status'] in ('applied', 'rejected'): raise DomainError('DRAFT_CLOSED', 'Create a new proposal for a closed draft')
            dv = old['version'] + 1
            self.db.execute('UPDATE drafts SET status=%s WHERE id=%s AND version=%s', ('superseded', did, old['version']))
        status = 'awaiting_review' if report['valid'] else 'needs_changes'
        self.db.execute('INSERT INTO drafts VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (did, dv, actor, base, canonical(operations), digest(operations), status, None, None, None, self.now()))
        return self._envelope(ok=True, data={'draft_id': did, 'draft_version': dv, 'base_version': base,
            'status': status, 'content_hash': digest(operations), 'changes': changes, 'validation': report}, warnings=report['warnings'])

    def prepare_draft(self, actor, operations, *, request_key, draft_id=None, expected_version=None):
        def act():
            return self._save_draft(actor, operations, draft_id, expected_version)
        return self._run(actor, 'prepare_draft', {'operations': operations, 'draft_id': draft_id, 'expected_version': expected_version}, act,
                         permission='draft', request_key=request_key)

    def get_draft(self, actor, draft_id, version):
        return self._run(actor, 'get_draft', {'draft_id': draft_id, 'version': version},
                         lambda: self._envelope(ok=True, data=self._draft(draft_id, version, latest=False)))

    def validate_draft(self, actor, draft_id, version):
        def act():
            draft = self._draft(draft_id, version)
            nodes, changes = self._candidate(draft['operations'], draft['base_version'])
            report = validate_graph(nodes)
            if draft['base_version'] != self._version():
                report['valid'] = False; report['errors'].append({'code': 'STALE_BASE', 'detail': 'Revise against latest data before approval'})
            return self._envelope(ok=True, data={'draft_id': draft_id, 'draft_version': version,
                'status': draft['status'], 'validation': report, 'changes': changes})
        return self._run(actor, 'validate_draft', {'draft_id': draft_id, 'version': version}, act)

    def preview_investigation(self, actor, draft_id, version, query):
        """Validate then query a candidate without changing any confirmed snapshot."""
        from .temporal import investigate
        def act():
            draft = self._draft(draft_id, version)
            if draft['base_version'] != self._version():
                raise DomainError('STALE_BASE', 'Revise against latest data before preview')
            nodes, _ = self._candidate(draft['operations'], draft['base_version'])
            report = validate_graph(nodes)
            if not report['valid']: raise DomainError('VALIDATION_FAILED', 'Invalid draft cannot be previewed', report)
            result = investigate(query, *[[n for n in nodes if n['type'] == k]
                for k in ('ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage')])
            return self._envelope(ok=True, data={'is_preview': True, 'draft_id': draft_id,
                'draft_version': version, 'content_hash': draft['content_hash'], 'result': result},
                warnings=[{'code': 'UNAPPLIED_PREVIEW', 'detail': 'Proposed result; not approved data'}])
        return self._run(actor, 'preview_investigation', {'draft_id': draft_id, 'version': version, 'query': query}, act)

    def review(self, actor, draft_id, version, *, decision, note, request_key):
        def act():
            d = self._draft(draft_id, version)
            if d['author'] == actor: raise DomainError('SELF_APPROVAL_FORBIDDEN', 'Author and reviewer must differ')
            if d['status'] not in ('awaiting_review', 'needs_changes'):
                raise DomainError('INVALID_STATE', 'This draft is not awaiting a review decision')
            if decision not in ('approve', 'request_changes', 'reject') or not isinstance(note, str) or not note.strip():
                raise DomainError('INVALID_REVIEW', 'Use approve/request_changes/reject and a review note')
            if decision == 'approve':
                if d['base_version'] != self._version(): raise DomainError('STALE_BASE', 'Revise against latest data')
                nodes, _ = self._candidate(d['operations'], d['base_version'])
                report = validate_graph(nodes)
                if not report['valid']: raise DomainError('VALIDATION_FAILED', 'Correct draft before approval', report)
            status = {'approve': 'approved', 'request_changes': 'needs_changes', 'reject': 'rejected'}[decision]
            self.db.execute('INSERT INTO review_events(id,draft_id,draft_version,content_hash,actor,decision,note,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (str(uuid.uuid4()), draft_id, version, d['content_hash'], actor, decision, note, self.now()))
            self.db.execute('UPDATE drafts SET status=%s,reviewer=%s,review_note=%s WHERE id=%s AND version=%s', (status, actor, note, draft_id, version))
            return self._envelope(ok=True, data={'draft_id': draft_id, 'draft_version': version, 'status': status, 'reviewer': actor, 'content_hash': d['content_hash']})
        return self._run(actor, 'review', {'draft_id': draft_id, 'version': version, 'decision': decision, 'note': note}, act,
                         permission='review', request_key=request_key)

    def apply(self, actor, draft_id, version, *, request_key):
        def act():
            d = self._draft(draft_id, version)
            if d['status'] == 'applied':
                return self._envelope(ok=True, data={'draft_id': draft_id, 'draft_version': version,
                    'status': 'applied', 'applied_version': d['applied_version']}, version=d['applied_version'], warnings=[{'code': 'ALREADY_APPLIED'}])
            if d['status'] != 'approved' or not d['reviewer']:
                raise DomainError('APPROVAL_REQUIRED', 'An exact approved draft version is required')
            self._require(d['reviewer'], 'review')
            if d['reviewer'] == d['author']: raise DomainError('SELF_APPROVAL_FORBIDDEN', 'Author cannot approve')
            if d['base_version'] != self._version(): raise DomainError('STALE_BASE', 'New data requires revision and fresh approval')
            nodes, _ = self._candidate(d['operations'], d['base_version'])
            report = validate_graph(nodes)
            if not report['valid']: raise DomainError('VALIDATION_FAILED', 'Revalidation failed', report)
            next_version = self._version() + 1
            self.db.execute('INSERT INTO snapshots VALUES(%s,%s,%s,%s)', (next_version, canonical(nodes), self.now(), 'approved:' + draft_id + ':' + str(version)))
            self.db.execute('UPDATE drafts SET status=%s,applied_version=%s WHERE id=%s AND version=%s', ('applied', next_version, draft_id, version))
            return self._envelope(ok=True, data={'draft_id': draft_id, 'draft_version': version, 'status': 'applied', 'applied_version': next_version})
        return self._run(actor, 'apply', {'draft_id': draft_id, 'version': version}, act, permission='apply', request_key=request_key)

    def get_source_receipt(self, actor, receipt_id):
        from .receipts import receipt
        def act():
            data = dict(receipt(self, actor, receipt_id))
            # The parsed candidate is immutable. Models inspect source facts but
            # never regenerate or replace its operations in their tool arguments.
            operations = data.pop('operations', [])
            data['evidence'] = sorted({ref for op in operations for ref in op['node'].get('evidence', [])})
            data['evidence_records'] = [op['node'] for op in operations if op['node']['type'] == 'Evidence']
            return self._envelope(ok=True, data=data, version=data['base_version'])
        return self._run(actor, 'get_source_receipt', {'receipt_id': receipt_id}, act)

    def prepare_source_draft(self, actor, receipt_id, request_key, draft_id=None, expected_version=None):
        from .receipts import source_draft
        return source_draft(self, actor, receipt_id, request_key, draft_id, expected_version)

    def search_evidence(self, actor, query, *, origin='all', version=None):
        def act():
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or origin not in {'all', 'real', 'synthetic'}:
                raise DomainError('INVALID_INPUT', '검색어와 실제/가상 자료 구분을 확인하세요.')
            needle = query.strip().casefold()
            nodes = [n for n in self._nodes(version) if (origin == 'all' or n['origin'] == origin)
                     and needle in canonical(n).casefold()]
            return self._envelope(ok=True, data={'matches': nodes[:60], 'total': len(nodes), 'limit': 60,
                'query': query.strip(), 'origin': origin, 'mode': 'lexical', 'candidate_only': True}, version=version)
        return self._run(actor, 'search_evidence', {'query': query, 'origin': origin, 'version': version}, act)

    def semantic_search(self, actor, query, *, origin='all', version=None):
        from .retrieval import EvidenceIndex
        from .openai_provider import ProviderError
        provider = getattr(self, 'embedding_provider', None)
        def prepare():
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000 or origin not in {'all', 'real', 'synthetic'}:
                raise DomainError('INVALID_INPUT', '검색 질문과 실제/가상 구분을 확인하세요.')
            if provider is None:
                raise DomainError('EMBEDDINGS_NOT_CONFIGURED', '임베딩 API가 설정되지 않았습니다. 문자열 검색은 사용할 수 있습니다.')
            selected = self._version() if version is None else version
            nodes = self._nodes(selected)
            ids = [n['id'] for n in nodes if n['type'] == 'Evidence' and (origin == 'all' or n['origin'] == origin)]
            indexed = self.db.execute('SELECT COUNT(*) AS count FROM evidence_vectors WHERE data_version=%s AND model=%s AND dimensions=%s AND evidence_id=ANY(%s)',
                (selected, provider.model, provider.dimensions, ids)).fetchone()['count']
            if not indexed:
                raise DomainError('EVIDENCE_INDEX_NOT_READY', '선택한 데이터 버전·자료 구분의 근거 색인이 없습니다. 다른 버전으로 자동 대체하지 않습니다.')
            return self._envelope(ok=True, data={'indexed': indexed, 'total': len(ids), 'complete': indexed == len(ids)}, version=selected)
        prepared = self._run(actor, 'semantic_search_prepare', {'query': query, 'origin': origin, 'version': version}, prepare)
        if not prepared['ok']:
            return prepared
        selected = prepared['data_version']
        cache = getattr(self, '_query_embeddings', {})
        key = digest([provider.model, provider.dimensions, query.strip()])
        try:
            if key not in cache:
                # External API call is outside every database transaction. The
                # request-local cache avoids calling it again for direct/AI parity.
                cache[key] = provider.embed([query.strip()])[0]
                self._query_embeddings = cache
        except (ProviderError, TimeoutError, ConnectionError) as exc:
            code = getattr(exc, 'code', 'EMBEDDING_UNAVAILABLE')
            def failed():
                raise DomainError(code, '검색 질문의 임베딩을 생성하지 못했습니다. 같은 조건으로 재시도하세요.')
            return self._run(actor, 'semantic_search', {'query': query, 'origin': origin, 'version': selected}, failed)
        result = EvidenceIndex(self, actor, model=provider.model, dimensions=provider.dimensions).search(
            cache[key], version=selected, origin=None if origin == 'all' else origin, limit=5)
        if result['ok']:
            result['data'].update(mode='semantic', query=query.strip(), origin=origin, index_coverage=prepared['data'])
            if not prepared['data']['complete']:
                result['warnings'].append({'code': 'PARTIAL_EVIDENCE_INDEX', 'detail': '검색은 이 버전에서 색인된 근거만 포함합니다.'})
        return result

    def run_history(self, actor):
        return self._run(actor, 'run_history', {}, lambda: self._envelope(ok=True, data=[dict(row) for row in self.db.execute('SELECT * FROM runs ORDER BY sequence')]), permission='audit')

class AgentGateway:
    """Host binds identity once. Actor, review, apply and seed are not tool inputs."""
    ALLOWED = frozenset({'lookup', 'configuration', 'compare_configurations', 'component_usage', 'investigate',
                         'compare_investigation', 'prepare_draft', 'get_draft', 'validate_draft', 'export_graph', 'preview_investigation',
                         'delivery_round', 'fulfillment_status',
                         'get_source_receipt', 'prepare_source_draft', 'search_evidence', 'semantic_search'})
    def __init__(self, service, actor, *, allowed_tools=None):
        self._service, self._actor = service, actor
        self.allowed_tools = self.ALLOWED if allowed_tools is None else frozenset(allowed_tools)
        if not self.allowed_tools or not self.allowed_tools <= self.ALLOWED:
            raise ValueError('Gateway tools must be a nonempty subset of AgentGateway.ALLOWED')
    def call(self, tool, arguments):
        if tool not in self.allowed_tools:
            return self._service._run(self._actor, 'blocked_tool', {'tool': tool},
                lambda: (_ for _ in ()).throw(DomainError('TOOL_NOT_ALLOWED', 'Tool is not exposed to service Agents')))
        if not isinstance(arguments, dict) or 'actor' in arguments or 'principals' in arguments:
            return self._service._run(self._actor, 'blocked_arguments', {'tool': tool},
                lambda: (_ for _ in ()).throw(DomainError('INVALID_TOOL_ARGUMENTS', 'Identity must be bound by the host')))
        try:
            return getattr(self._service, tool)(self._actor, **arguments)
        except TypeError:
            return self._service._run(self._actor, 'invalid_tool_arguments', {'tool': tool},
                lambda: (_ for _ in ()).throw(DomainError('INVALID_TOOL_ARGUMENTS', 'Arguments do not match the tool contract')))
