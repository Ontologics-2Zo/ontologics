"""Pinned-main boundary unit checks; no real connection, SQL or approval.

Only the model import is rebound in an isolated Service namespace. Actual main
_nodes/_candidate consume put operations and a supplied snapshot row. Stored-draft
inputs and DB/lock/transaction seams remain test doubles. The SQL recorder proves
branch reachability, not PostgreSQL persistence or atomicity.
"""
import ast
from contextlib import ExitStack
import copy
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from ontologics import host as common_host, service as common_service
from ontologics.identity import IdentityError, UserIdentity
from ontologics.ingest import build_catalog
from ontologics.order_fulfillment import schema, schema_export, schema_validation
from ontologics.order_fulfillment.host import CommonHost as BHost
from ontologics.order_fulfillment.service import AgentGateway as BGateway, Service as BService
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.order_fulfillment.fulfillment import load_demo_fulfillment
from ontologics.order_fulfillment.production import load_demo_production

MAIN_REVISION = 'ca7c391a9e813b6ab9e6fddfb48ab639c09a764b'
ROOT = Path(__file__).resolve().parents[2]


def main_source(path):
    return subprocess.check_output(['git', 'show', MAIN_REVISION + ':' + path], cwd=ROOT).decode()


def isolated_main():
    model = {'__name__': 'isolated_b_boundary_model', '__file__': str(ROOT / 'ontologics/model.py')}
    exec(compile(main_source('ontologics/model.py'), '<main-model>', 'exec'), model)
    tree = ast.parse(main_source('ontologics/service.py'))
    namespace = {'__name__': 'ontologics._b_boundary_service', '__package__': 'ontologics'}
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module == 'model']
    if len(imports) != 1:
        raise AssertionError('Recheck pinned main model import before boundary testing')
    for alias in imports[0].names:
        namespace[alias.asname or alias.name] = model[alias.name]
    tree.body.remove(imports[0])
    exec(compile(tree, '<main-service>', 'exec'), namespace)
    # These seams belong only to this isolated test namespace, not common modules.
    namespace['in_transaction'] = lambda db: db.in_transaction
    namespace['lock_store'] = lambda db, name: db.lock_calls.append(name)
    return model, namespace


class Rows:
    def __init__(self, rows=()):
        self.rows = rows

    def fetchone(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None

    def __iter__(self):
        return iter(copy.deepcopy(self.rows))


class SQLRecorder:
    """Capture known query branches only; never run SQL or model a database."""
    def __init__(self, snapshot_nodes):
        self.calls = []
        self.lock_calls = []
        self.closed = False
        self.in_transaction = False
        self.draft = None
        self.latest_draft_version = 2
        self.snapshot_nodes = copy.deepcopy(snapshot_nodes)

    def execute(self, sql, parameters=None):
        self.calls.append((sql, copy.deepcopy(parameters)))
        if sql in ('BEGIN', 'COMMIT', 'ROLLBACK'):
            self.in_transaction = sql == 'BEGIN'
        elif sql == 'SELECT MAX(version) FROM snapshots':
            return Rows([{'max': 1}])
        elif sql == 'SELECT nodes FROM snapshots WHERE version=%s':
            return Rows([{'nodes': self.snapshot_nodes}] if parameters == (1,) else [])
        elif sql.startswith('SELECT * FROM requests'):
            return Rows()
        elif sql.startswith('SELECT * FROM drafts'):
            found = self.draft and parameters == (self.draft['id'], self.draft['version'])
            return Rows([self.draft] if found else [])
        elif sql.startswith('SELECT MAX(version) FROM drafts'):
            return Rows([{'max': self.latest_draft_version}])
        elif sql.startswith('SELECT * FROM review_events'):
            return Rows()
        elif not sql.startswith(('INSERT INTO ', 'UPDATE ')):
            raise AssertionError('Unexpected query branch: ' + sql)
        return Rows()

    def close(self):
        self.closed = True


class StorageBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.attempts = []
        def deny(*args, **kwargs):
            cls.attempts.append(True)
            raise AssertionError('Network/DB connections forbidden in free boundary tests')
        guards = ExitStack()
        cls.addClassCleanup(guards.close)
        guards.enter_context(patch.object(socket.socket, 'connect', deny))
        guards.enter_context(patch.object(socket.socket, 'connect_ex', deny))
        for name in ('socket.create_connection', 'psycopg.connect', 'psycopg.Connection.connect'):
            guards.enter_context(patch(name, deny))
        cls.main_model, cls.main_service = isolated_main()
        cls.catalog = build_catalog()['nodes']
        cls.contract = load_demo_contract()

    @classmethod
    def tearDownClass(cls):
        if cls.attempts:
            raise AssertionError('A forbidden connection was attempted')

    def make_service(self, nodes=None, *, status='awaiting_review'):
        nodes = self.catalog + self.contract if nodes is None else nodes
        # A common-only positive control must add a real change to a valid base.
        base_nodes = self.catalog[:-1] if nodes == self.catalog else self.catalog
        self.assertTrue(self.main_model['validate_graph'](base_nodes)['valid'])
        base_index = {node['id']: node for node in base_nodes}
        ns = self.main_service
        service = object.__new__(ns['Service'])
        service.schema = 'unit_boundary_only'
        service.db = SQLRecorder(base_nodes)
        service.principals = {
            'user:author': {'kind': 'human', 'roles': ['read', 'draft', 'review']},
            'user:reviewer': {'kind': 'human', 'roles': ['read', 'review']},
            'user:reader': {'kind': 'human', 'roles': ['read']},
            'agent:test': {'kind': 'agent', 'roles': ['read', 'review']},
            common_host.WORKFLOW_ACTOR: {'kind': 'system', 'roles': ['read', 'apply']},
        }
        operations = [{'op': 'put', 'node': copy.deepcopy(node)} for node in nodes
                      if base_index.get(node['id']) != node]
        draft = dict(id='draft:boundary', version=2, author='user:author', base_version=1,
                     operations=operations, content_hash=ns['digest'](operations), status=status,
                     reviewer='user:reviewer' if status == 'approved' else None)
        service._candidate = Mock(wraps=service._candidate)
        service._draft = Mock(return_value=draft)
        service.db.draft = draft
        return service, draft

    def assert_no_confirmation_writes(self, service):
        writes = [sql for sql, _ in service.db.calls if sql.startswith(('INSERT INTO snapshots', 'UPDATE drafts', 'INSERT INTO review_events'))]
        self.assertEqual([], writes)

    def assert_error(self, result, code):
        self.assertFalse(result['ok'])
        self.assertEqual(code, result['errors'][0]['code'])

    def test_fixture_operations_use_put_and_reach_the_main_candidate_contract(self):
        service, draft = self.make_service()
        self.assertEqual({'put'}, {op['op'] for op in draft['operations']})
        self.assertEqual(len(self.contract), len(draft['operations']))
        nodes, changes = service._candidate(draft['operations'], draft['base_version'])
        self.assertEqual(self.catalog + self.contract, nodes)
        self.assertEqual({n['id'] for n in self.contract}, {row['id'] for row in changes})
        self.assertTrue(all(row['before'] is None for row in changes))

    def test_candidate_reads_exact_base_and_preserves_inputs_and_change_details(self):
        service, draft = self.make_service(self.catalog)
        before = copy.deepcopy((draft['operations'], service.db.snapshot_nodes))
        nodes, changes = service._candidate(draft['operations'], 1)
        self.assertEqual(self.catalog, nodes)
        self.assertEqual([{'id': self.catalog[-1]['id'], 'before': None,
                           'after': self.catalog[-1]}], changes)
        self.assertEqual(before, (draft['operations'], service.db.snapshot_nodes))
        self.assertEqual([('SELECT nodes FROM snapshots WHERE version=%s', (1,))], service.db.calls)
        self.assert_no_confirmation_writes(service)

    def test_candidate_rejects_non_put_shapes_duplicate_operations_and_size_limits(self):
        node = self.contract[0]
        valid = {'op': 'put', 'node': node}
        cases = [([], 'INVALID_OPERATIONS'), ([valid] * 2001, 'INVALID_OPERATIONS'),
                 ([{'op': 'upsert', 'node': node}], 'INVALID_OPERATION'),
                 ([{'op': 'delete', 'node': node}], 'INVALID_OPERATION'),
                 ([dict(valid, extra=True)], 'INVALID_OPERATION'),
                 ([{'op': 'put', 'node': []}], 'INVALID_OPERATION'),
                 ([valid, valid], 'DUPLICATE_OPERATION')]
        for operations, expected in cases:
            with self.subTest(expected=expected, size=len(operations)):
                service, _ = self.make_service()
                before = copy.deepcopy(operations)
                with self.assertRaises(self.main_service['DomainError']) as caught:
                    service._candidate(operations, 1)
                self.assertEqual(expected, caught.exception.code)
                self.assertEqual(before, operations)
                self.assert_no_confirmation_writes(service)

    def test_candidate_keeps_type_origin_and_source_immutability_guards(self):
        base = self.catalog[0]
        source = next(n for n in self.catalog if n['type'] == 'SourceDocument')
        cases = [(dict(base, type='Contract'), 'TYPE_CHANGE'),
                 (dict(base, origin='synthetic' if base['origin'] == 'real' else 'real'), 'ORIGIN_CHANGE'),
                 (dict(source, version=source['version'] + ':changed'), 'IMMUTABLE_SOURCE'),
                 ({'id': '', 'type': 'Contract'}, 'INVALID_NODE_ID')]
        for node, expected in cases:
            with self.subTest(expected=expected):
                service, _ = self.make_service()
                with self.assertRaises(self.main_service['DomainError']) as caught:
                    service._candidate([{'op': 'put', 'node': node}], 1)
                self.assertEqual(expected, caught.exception.code)
                self.assert_no_confirmation_writes(service)

    def test_identical_put_is_not_a_new_draft(self):
        service, _ = self.make_service()
        operations = [{'op': 'put', 'node': self.catalog[0]}]
        result = service.prepare_draft('user:author', operations, request_key='unit-no-change')
        self.assert_error(result, 'NO_CHANGES')
        self.assertFalse(any(sql.startswith('INSERT INTO drafts') for sql, _ in service.db.calls))
        self.assert_no_confirmation_writes(service)

    def test_document_only_candidate_is_valid_without_confirming_b_business_nodes(self):
        documents = [n for n in self.contract if n['type'] in ('SourceDocument', 'Evidence')]
        service, draft = self.make_service(self.catalog + documents)
        nodes, _ = service._candidate(draft['operations'], 1)
        self.assertTrue(self.main_model['validate_graph'](nodes)['valid'])
        self.assertTrue(set(schema.SCHEMA).isdisjoint(n['type'] for n in nodes))
        result = service.prepare_draft('user:author', draft['operations'], request_key='unit-documents')
        self.assertTrue(result['ok'])
        self.assertEqual('awaiting_review', result['data']['status'])
        full_service, full_draft = self.make_service()
        full = full_service.prepare_draft('user:author', full_draft['operations'], request_key='unit-b-candidate')
        self.assertEqual('needs_changes', full['data']['status'])
        self.assert_no_confirmation_writes(service)
        self.assert_no_confirmation_writes(full_service)

    def test_candidate_keeps_explicit_base_version_and_missing_version_error(self):
        for version in (0, True, -1):
            with self.subTest(version=version):
                service, draft = self.make_service()
                with self.assertRaises(self.main_service['DomainError']) as caught:
                    service._candidate(draft['operations'], version)
                self.assertEqual('VERSION_NOT_FOUND' if version == 0 else 'INVALID_VERSION', caught.exception.code)
                self.assert_no_confirmation_writes(service)

    def test_main_accepts_common_catalog_and_rejects_all_ten_b_types(self):
        validate = self.main_model['validate_graph']
        self.assertTrue(validate(self.catalog)['valid'])
        seen = set()
        for extra in ([], load_demo_fulfillment(), load_demo_production(), load_demo_delivery(), load_demo_delivery(mixed=True)):
            nodes = self.catalog + self.contract + extra
            before = copy.deepcopy(nodes)
            service, draft = self.make_service(nodes)
            candidate, changes = service._candidate(draft['operations'], draft['base_version'])
            self.assertEqual(nodes, candidate)
            self.assertEqual({op['node']['id'] for op in draft['operations']},
                             {row['id'] for row in changes})
            errors = []
            schema_validation.validate_domain({n['id']: n for n in nodes}, lambda *e: errors.append(e))
            self.assertEqual([], errors)
            report = validate(candidate)
            b_nodes = {n['id']: n['type'] for n in nodes if n['type'] in schema.SCHEMA}
            unknown = {e['node_id'] for e in report['errors'] if e['code'] == 'UNKNOWN_TYPE'}
            self.assertFalse(report['valid'])
            self.assertEqual(set(b_nodes), unknown)
            seen.update(b_nodes.values())
            self.assertEqual(before, nodes)
        self.assertEqual(set(schema.SCHEMA), seen)

    def test_b_domain_success_is_not_full_graph_validation(self):
        # Contract has no B-only business rule; structural/source checks are absent.
        node = dict(id='contract:incomplete', type='Contract', origin='real', evidence=[], unexpected='x')
        errors = []
        schema_validation.validate_domain({node['id']: node}, lambda *e: errors.append(e))
        self.assertEqual([], errors)
        # B structural checks belong to the input validator, independent of
        # whether the active common model already recognises this B type.
        current = schema_validation.validate_input_graph([node])
        self.assertFalse(current['valid'])
        self.assertTrue({'UNKNOWN_FIELD', 'MISSING_FIELD', 'DEMO_OPERATIONS_ONLY', 'EVIDENCE_REQUIRED'} <= {e['code'] for e in current['errors']})
        self.assertEqual('UNKNOWN_TYPE', self.main_model['validate_graph']([node])['errors'][0]['code'])

    def test_common_fields_do_not_provide_an_arbitrary_b_payload_slot(self):
        nodes = copy.deepcopy(self.catalog)
        node = next(n for n in nodes if n['type'] == 'ProductModel')
        node['b_extension'] = {'contract': 'unit-only hypothetical mapping'}
        report = self.main_model['validate_graph'](nodes)
        self.assertFalse(report['valid'])
        self.assertTrue(any(e['node_id'] == node['id'] and e['code'] == 'UNKNOWN_FIELD'
                            for e in report['errors']))

    def test_b_rules_still_reject_business_quantity_and_typed_reference_errors(self):
        nodes = copy.deepcopy(self.contract)
        item = next(n for n in nodes if n['type'] == 'ContractItem')
        item.update(quantity=True, configuration_id='missing')
        errors = []
        schema_validation.validate_domain({n['id']: n for n in nodes}, lambda nid, code, detail: errors.append(code))
        self.assertTrue({'INVALID_QUANTITY', 'INVALID_REFERENCE'} <= set(errors))

    def test_lookup_and_output_composition_do_not_register_b_for_storage(self):
        model = self.main_model
        before = copy.deepcopy(model['SCHEMA'])
        combined = schema.lookup_schema(model['SCHEMA'])
        self.assertEqual(22, len(combined))
        with patch.object(schema_export, '_common_graph_jsonld', model['graph_jsonld']), patch.object(schema_export, '_common_tbox_turtle', model['tbox_turtle']):
            self.assertTrue(schema_export.graph_jsonld(self.contract)['@graph'])
            self.assertIn('o:Contract a owl:Class .', schema_export.tbox_turtle())
        self.assertEqual(before, model['SCHEMA'])
        self.assertEqual(12, len(model['SCHEMA']))
        self.assertFalse(model['validate_graph'](self.catalog + self.contract)['valid'])

    def test_current_b_inherits_common_mutations_without_a_validator_injection_hook(self):
        for name in ('prepare_draft', 'validate_draft', 'review', 'apply'):
            self.assertIs(getattr(common_service.Service, name), getattr(BService, name))
        self.assertIs(common_host.CommonHost.apply_approved, BHost.apply_approved)
        init = self.main_service['Service'].__init__
        self.assertEqual(('self', 'database', 'principals', 'schema'), init.__code__.co_varnames[:init.__code__.co_argcount + init.__code__.co_kwonlyargcount])
        self.assertIs(self.main_model['validate_graph'], self.main_service['validate_graph'])

    def test_prepare_b_draft_records_needs_changes_without_confirming_data(self):
        service, draft = self.make_service()
        result = service.prepare_draft('user:author', draft['operations'], request_key='unit-prepare')
        self.assertTrue(result['ok'])
        self.assertEqual('needs_changes', result['data']['status'])
        self.assertFalse(result['data']['validation']['valid'])
        inserts = [args for sql, args in service.db.calls if sql.startswith('INSERT INTO drafts')]
        self.assertEqual(1, len(inserts))
        self.assertEqual('needs_changes', inserts[0][6])
        self.assert_no_confirmation_writes(service)

    def test_validate_b_draft_returns_ok_envelope_but_invalid_graph(self):
        service, _ = self.make_service()
        result = service.validate_draft('user:author', 'draft:boundary', 2)
        self.assertTrue(result['ok'])
        self.assertFalse(result['data']['validation']['valid'])
        self.assert_no_confirmation_writes(service)

    def test_b_review_approval_is_rejected_before_review_write(self):
        service, _ = self.make_service()
        result = service.review('user:reviewer', 'draft:boundary', 2, decision='approve', note='unit-only decision', request_key='unit-review')
        self.assert_error(result, 'VALIDATION_FAILED')
        self.assert_no_confirmation_writes(service)
        self.assertIn('ROLLBACK', [sql for sql, _ in service.db.calls])

    def test_request_changes_is_feedback_and_does_not_confirm_b_data(self):
        service, _ = self.make_service()
        result = service.review('user:reviewer', 'draft:boundary', 2, decision='request_changes',
                                note='unit-only request for changes', request_key='unit-feedback')
        self.assertTrue(result['ok'])
        self.assertEqual('needs_changes', result['data']['status'])
        service._candidate.assert_not_called()
        self.assertFalse(any(sql.startswith('INSERT INTO snapshots') for sql, _ in service.db.calls))
        updates = [args for sql, args in service.db.calls if sql.startswith('UPDATE drafts')]
        self.assertEqual('needs_changes', updates[0][0])

    def test_even_preapproved_b_fixture_is_revalidated_before_snapshot_write(self):
        # Supplied stored state, not an approval performed by this test or an Agent.
        service, _ = self.make_service(status='approved')
        result = service.apply(common_host.WORKFLOW_ACTOR, 'draft:boundary', 2, request_key='unit-apply')
        self.assert_error(result, 'VALIDATION_FAILED')
        self.assert_no_confirmation_writes(service)

    def test_common_graph_positive_control_reaches_the_same_mutation_branches(self):
        service, draft = self.make_service(self.catalog)
        result = service.prepare_draft('user:author', draft['operations'], request_key='unit-common-prepare')
        self.assertTrue(result['ok'])
        self.assertEqual('awaiting_review', result['data']['status'])
        service, _ = self.make_service(self.catalog)
        result = service.review('user:reviewer', 'draft:boundary', 2, decision='approve', note='unit-only control', request_key='unit-common-review')
        self.assertTrue(result['ok'])
        self.assertTrue(any(sql.startswith('INSERT INTO review_events') for sql, _ in service.db.calls))
        service, _ = self.make_service(self.catalog, status='approved')
        result = service.apply(common_host.WORKFLOW_ACTOR, 'draft:boundary', 2, request_key='unit-common-apply')
        self.assertTrue(result['ok'])
        self.assertTrue(any(sql.startswith('INSERT INTO snapshots') for sql, _ in service.db.calls))

    def test_real_permission_and_self_review_checks_run_before_b_validation(self):
        for actor, expected in (('user:author', 'SELF_APPROVAL_FORBIDDEN'), ('user:reader', 'FORBIDDEN'), ('agent:test', 'HUMAN_REVIEW_REQUIRED')):
            with self.subTest(actor=actor):
                service, _ = self.make_service()
                result = service.review(actor, 'draft:boundary', 2, decision='approve', note='unit-only decision', request_key='unit-review')
                self.assert_error(result, expected)
                self.assert_no_confirmation_writes(service)
                service._candidate.assert_not_called()

    def test_apply_checks_existing_approval_and_current_reviewer_permissions(self):
        for status, reviewer_kind, roles, reviewer, expected in (
                ('awaiting_review', 'human', ['read', 'review'], None, 'APPROVAL_REQUIRED'),
                ('approved', 'human', ['read'], 'user:reviewer', 'FORBIDDEN'),
                ('approved', 'agent', ['read', 'review'], 'user:reviewer', 'HUMAN_REVIEW_REQUIRED'),
                ('approved', 'human', ['read', 'review'], 'user:author', 'SELF_APPROVAL_FORBIDDEN')):
            with self.subTest(expected=expected):
                service, draft = self.make_service(status=status)
                draft['reviewer'] = reviewer
                service.principals['user:reviewer'] = {'kind': reviewer_kind, 'roles': roles}
                result = service.apply(common_host.WORKFLOW_ACTOR, 'draft:boundary', 2, request_key='unit-apply')
                self.assert_error(result, expected)
                self.assert_no_confirmation_writes(service)
                service._candidate.assert_not_called()

    def test_stale_base_blocks_review_and_apply_before_revalidation(self):
        for action in ('review', 'apply'):
            with self.subTest(action=action):
                service, draft = self.make_service(status='approved' if action == 'apply' else 'awaiting_review')
                draft['base_version'] = 0
                result = (service.review('user:reviewer', 'draft:boundary', 2, decision='approve', note='unit-only decision', request_key='unit-review')
                          if action == 'review' else service.apply(common_host.WORKFLOW_ACTOR, 'draft:boundary', 2, request_key='unit-apply'))
                self.assert_error(result, 'STALE_BASE')
                self.assert_no_confirmation_writes(service)
                service._candidate.assert_not_called()

    def test_missing_request_key_blocks_mutation_before_action(self):
        service, draft = self.make_service()
        result = service.prepare_draft('user:author', draft['operations'], request_key=None)
        self.assert_error(result, 'INVALID_REQUEST_KEY')
        service._candidate.assert_not_called()
        self.assert_no_confirmation_writes(service)

    def test_real_draft_lookup_keeps_exact_version_hash_and_latest_checks(self):
        for mode, expected in (('missing-version', 'DRAFT_NOT_FOUND'), ('superseded', 'SUPERSEDED_DRAFT'), ('changed-hash', 'DRAFT_INTEGRITY')):
            with self.subTest(mode=mode):
                service, draft = self.make_service()
                del service._draft
                version = 1 if mode == 'missing-version' else 2
                if mode == 'superseded':
                    service.db.latest_draft_version = 3
                if mode == 'changed-hash':
                    draft['operations'][0]['node']['id'] += ':tampered'
                with self.assertRaises(self.main_service['DomainError']) as caught:
                    service._draft('draft:boundary', version)
                self.assertEqual(expected, caught.exception.code)
                self.assert_no_confirmation_writes(service)

    def test_real_draft_lookup_preserves_requested_version_and_hash(self):
        service, draft = self.make_service()
        del service._draft
        result = service._draft('draft:boundary', 2)
        self.assertEqual(2, result['version'])
        self.assertEqual(draft['content_hash'], result['content_hash'])
        self.assertEqual(('draft:boundary', 2), service.db.calls[0][1])

    def test_b_agent_gateway_cannot_expose_review_or_apply(self):
        service = Mock()
        service._run.side_effect = lambda actor, tool, args, action: action()
        gateway = BGateway(service, 'user:author')
        for tool in ('review', 'apply', 'bootstrap'):
            with self.subTest(tool=tool):
                self.assertNotIn(tool, BGateway.ALLOWED)
                with self.assertRaises(common_service.DomainError) as caught:
                    gateway.call(tool, {})
                self.assertEqual('TOOL_NOT_ALLOWED', caught.exception.code)
        service.review.assert_not_called()
        service.apply.assert_not_called()

    def test_host_uses_common_service_and_refreshes_reviewer_before_b_rejection(self):
        source = ast.parse(main_source('ontologics/host.py'))
        host = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'CommonHost')
        method = next(n for n in host.body if isinstance(n, ast.FunctionDef) and n.name == 'apply_approved')
        for permissions, expected in ((frozenset({'read', 'review'}), 'VALIDATION_FAILED'), (frozenset({'read'}), 'FORBIDDEN')):
            with self.subTest(permissions=permissions):
                service, _ = self.make_service(status='approved')
                service.principals = {common_host.WORKFLOW_ACTOR: {'kind': 'system', 'roles': ['read', 'apply']}}
                factory = Mock(return_value=service)
                namespace = dict(Service=factory, WORKFLOW_ACTOR=common_host.WORKFLOW_ACTOR,
                                 _identity=common_host._identity, IdentityError=IdentityError,
                                 DomainError=self.main_service['DomainError'])
                exec(compile(ast.Module(body=[method], type_ignores=[]), '<main-host-apply>', 'exec'), namespace)
                identities = Mock()
                identities.lookup.return_value = UserIdentity('reviewer', permissions)
                database = object()
                result = namespace['apply_approved'](SimpleNamespace(_database=database, _identities=identities), 'draft:boundary', 2, request_key='unit-host-apply')
                self.assert_error(result, expected)
                factory.assert_called_once_with(database, principals={common_host.WORKFLOW_ACTOR: {'kind': 'system', 'roles': ['read', 'apply']}})
                identities.lookup.assert_called_once_with('reviewer')
                self.assertEqual([call('draft:boundary', 2, latest=False),
                                  call('draft:boundary', 2)], service._draft.call_args_list)
                self.assertTrue(service.db.closed)
                self.assert_no_confirmation_writes(service)


if __name__ == '__main__':
    unittest.main()
