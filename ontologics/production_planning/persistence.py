"""A-owned graph persistence over the shared PostgreSQL transaction engine.

This module deliberately owns the schema-bound lifecycle methods. They preserve
shared authorization, review hashes, version checks and atomic replay semantics.
No common globals are modified. Use only with an A-owned database schema.
When the common lifecycle changes, review these explicit methods for parity.
"""
import uuid
from ..service import Service as CommonService, DomainError, configuration_changes
from ..model import canonical, digest
from .graph_validation import validate_graph
from .graph_export import graph_jsonld

class PlanningService(CommonService):
    def bootstrap(self, actor, nodes, *, request_key):
        """Development seed only; empty database only. Not the S2 registration path."""
        def act():
            if self._version() != 0 or self._nodes(): raise DomainError('SEED_NOT_EMPTY', 'Seed can only initialize an empty development database')
            report = validate_graph(nodes)
            if not report['valid']: raise DomainError('VALIDATION_FAILED', 'Invalid seed', report)
            self.db.execute('INSERT INTO snapshots VALUES(1,%s,%s,%s)', (canonical(nodes), self.now(), 'development_seed'))
            return self._envelope(ok=True, data={'node_count': len(nodes), 'mode': 'development_seed'}, warnings=report['warnings'])
        return self._run(actor, 'bootstrap', {'nodes': nodes}, act, permission='seed', request_key=request_key)

    def export_graph(self, actor, *, version=None):
        return self._run(actor, 'export_graph', {'version': version}, lambda: self._envelope(ok=True, data=graph_jsonld(self._nodes(version)), version=version))

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
        from ..temporal import investigate
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
