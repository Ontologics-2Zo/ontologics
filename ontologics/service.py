"""In-process shared tools. Host-bound identities, immutable drafts, atomic application."""
from __future__ import annotations
import json
import sqlite3
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

class Service:
    """Trusted application boundary. Never expose Service or principal setup to an LLM.

    Principals are supplied by the host's authentication adapter. Demo identities do
    not constitute production authentication. AgentGateway exposes no review/apply.
    """
    def __init__(self, db_path=':memory:', *, principals):
        self.principals = json.loads(json.dumps(principals))
        self.db = sqlite3.connect(str(db_path), isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS snapshots(version INTEGER PRIMARY KEY, nodes TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS drafts(id TEXT NOT NULL, version INTEGER NOT NULL, author TEXT NOT NULL, base_version INTEGER NOT NULL,
          operations TEXT NOT NULL, content_hash TEXT NOT NULL, status TEXT NOT NULL, reviewer TEXT, review_note TEXT,
          applied_version INTEGER, created_at TEXT NOT NULL, PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, actor TEXT NOT NULL, tool TEXT NOT NULL, request_hash TEXT NOT NULL,
          status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, data_version INTEGER, error_code TEXT);
        CREATE TABLE IF NOT EXISTS requests(actor TEXT NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
          response TEXT NOT NULL, PRIMARY KEY(actor,request_key));
        CREATE TABLE IF NOT EXISTS review_events(id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, draft_version INTEGER NOT NULL,
          content_hash TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
        ''')
        if not self.db.execute('SELECT 1 FROM snapshots LIMIT 1').fetchone():
            self.db.execute('INSERT INTO snapshots VALUES(0,?,?,?)', ('[]', self.now(), 'empty'))

    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat()
    def close(self): self.db.close()
    def _version(self): return self.db.execute('SELECT MAX(version) FROM snapshots').fetchone()[0]
    def _nodes(self, version=None):
        version = self._version() if version is None else version
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise DomainError('INVALID_VERSION', 'Data version must be a nonnegative integer')
        row = self.db.execute('SELECT nodes FROM snapshots WHERE version=?', (version,)).fetchone()
        if row is None: raise DomainError('VERSION_NOT_FOUND', 'Unknown data version')
        return json.loads(row[0])
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
        request_hash = digest({'tool': tool, 'invalid_input': True})
        try:
            serialized = canonical({'tool': tool, 'args': args})
            if len(serialized.encode()) > 8_000_000:
                raise DomainError('INPUT_TOO_LARGE', 'Tool input exceeds 8 MB')
            request_hash = digest({'tool': tool, 'args': args})
            self.db.execute('BEGIN IMMEDIATE')
            self._require(actor, permission)
            if permission in ('draft', 'review', 'apply', 'seed') and request_key is None:
                raise DomainError('INVALID_REQUEST_KEY', 'All mutations require an idempotency key')
            if request_key is not None:
                if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
                    raise DomainError('INVALID_REQUEST_KEY', 'Use a nonempty key of at most 200 characters')
                previous = self.db.execute('SELECT * FROM requests WHERE actor=? AND request_key=?', (actor, request_key)).fetchone()
                if previous:
                    if previous['request_hash'] != request_hash:
                        raise DomainError('IDEMPOTENCY_CONFLICT', 'This key was already used for different input')
                    result = json.loads(previous['response'])
                    result['warnings'].append({'code': 'IDEMPOTENT_REPLAY', 'original_run_id': result['run_id']})
                    result['run_id'] = run_id
                else:
                    result = action()
                    result['run_id'] = run_id
                    self.db.execute('INSERT INTO requests VALUES(?,?,?,?)', (actor, request_key, request_hash, canonical(result)))
            else:
                result = action(); result['run_id'] = run_id
            self.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                (run_id, actor, tool, request_hash, 'succeeded', started, self.now(), result['data_version'], None))
            self.db.execute('COMMIT')
            return result
        except (DomainError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            if self.db.in_transaction: self.db.execute('ROLLBACK')
            code = exc.code if isinstance(exc, DomainError) else ('STORAGE_ERROR' if isinstance(exc, sqlite3.Error) else 'INVALID_INPUT')
            err = {'code': code, 'message': str(exc)}
            if isinstance(exc, DomainError) and exc.details is not None: err['details'] = exc.details
            result = self._envelope(ok=False, error=err, run_id=run_id)
            self.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                (run_id, actor, tool, request_hash, 'failed', started, self.now(), result['data_version'], code))
            return result

    def bootstrap(self, actor, nodes, *, request_key):
        """Development seed only; empty database only. Not the S2 registration path."""
        def act():
            if self._version() != 0 or self._nodes(): raise DomainError('SEED_NOT_EMPTY', 'Seed can only initialize an empty development database')
            report = validate_graph(nodes)
            if not report['valid']: raise DomainError('VALIDATION_FAILED', 'Invalid seed', report)
            self.db.execute('INSERT INTO snapshots VALUES(1,?,?,?)', (canonical(nodes), self.now(), 'development_seed'))
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
        row = self.db.execute('SELECT * FROM drafts WHERE id=? AND version=?', (draft_id, version)).fetchone()
        if not row: raise DomainError('DRAFT_NOT_FOUND', 'Unknown draft version')
        if latest and self.db.execute('SELECT MAX(version) FROM drafts WHERE id=?', (draft_id,)).fetchone()[0] != version:
            raise DomainError('SUPERSEDED_DRAFT', 'A newer draft version exists')
        d = dict(row); d['operations'] = json.loads(d['operations'])
        if digest(d['operations']) != d['content_hash']: raise DomainError('DRAFT_INTEGRITY', 'Draft content hash mismatch')
        d['review_history'] = [dict(event) for event in self.db.execute(
            'SELECT * FROM review_events WHERE draft_id=? AND draft_version=? ORDER BY rowid', (draft_id, version))]
        return d

    def prepare_draft(self, actor, operations, *, request_key, draft_id=None, expected_version=None):
        def act():
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
                self.db.execute('UPDATE drafts SET status=? WHERE id=? AND version=?', ('superseded', did, old['version']))
            status = 'awaiting_review' if report['valid'] else 'needs_changes'
            self.db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (did, dv, actor, base, canonical(operations), digest(operations), status, None, None, None, self.now()))
            return self._envelope(ok=True, data={'draft_id': did, 'draft_version': dv, 'base_version': base,
                'status': status, 'content_hash': digest(operations), 'changes': changes, 'validation': report}, warnings=report['warnings'])
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
            self.db.execute('INSERT INTO review_events VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()), draft_id, version, d['content_hash'], actor, decision, note, self.now()))
            self.db.execute('UPDATE drafts SET status=?,reviewer=?,review_note=? WHERE id=? AND version=?', (status, actor, note, draft_id, version))
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
            self.db.execute('INSERT INTO snapshots VALUES(?,?,?,?)', (next_version, canonical(nodes), self.now(), 'approved:' + draft_id + ':' + str(version)))
            self.db.execute('UPDATE drafts SET status=?,applied_version=? WHERE id=? AND version=?', ('applied', next_version, draft_id, version))
            return self._envelope(ok=True, data={'draft_id': draft_id, 'draft_version': version, 'status': 'applied', 'applied_version': next_version})
        return self._run(actor, 'apply', {'draft_id': draft_id, 'version': version}, act, permission='apply', request_key=request_key)

    def run_history(self, actor):
        return self._run(actor, 'run_history', {}, lambda: self._envelope(ok=True, data=[dict(row) for row in self.db.execute('SELECT * FROM runs ORDER BY rowid')]), permission='audit')

class AgentGateway:
    """Host binds identity once. Actor, review, apply and seed are not tool inputs."""
    ALLOWED = frozenset({'lookup', 'configuration', 'compare_configurations', 'component_usage', 'investigate',
                         'compare_investigation', 'prepare_draft', 'get_draft', 'validate_draft', 'export_graph', 'preview_investigation'})
    def __init__(self, service, actor):
        self._service, self._actor = service, actor
    def call(self, tool, arguments):
        if tool not in self.ALLOWED:
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
