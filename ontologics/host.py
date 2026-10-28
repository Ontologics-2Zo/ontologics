"""In-process user/workflow boundary over existing tools; no login or HTTP server.

Construct providers and select development identities in trusted host code only.
Never expose this host, a RequestSession, or a Service object to an LLM.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
import re
import json
import time
import hashlib
import marshal
from types import MappingProxyType

from .database import DatabaseSettings
from .identity import IdentityError, IdentityProvider, UserIdentity
from .runtime import ModelRuntime
from .harness import BudgetLimits, ExecutionBudget, PinnedContext, build_context_messages, request_budget, canonical
from .contracts import AgentSpec, AgentTask, AgentResult, EvidenceRef
from .service import AgentGateway, DomainError, Service
from .tool_specs import PRODUCT_TOOLS, QUALITY_TOOLS, KNOWLEDGE_TOOLS, SUPERVISOR_TOOLS


WORKFLOW_ACTOR = 'system:host-workflow'
ROLE_TOOLS = {name: frozenset(names) for name, names in {
    'product': PRODUCT_TOOLS, 'quality': QUALITY_TOOLS,
    'knowledge': KNOWLEDGE_TOOLS, 'supervisor': SUPERVISOR_TOOLS}.items()}


@dataclass(frozen=True)
class AgentRegistration:
    """A trusted, local role definition; registration never grants user rights.

    Only host startup code constructs this object. Requests, model output and
    document contents cannot register roles or replace the four built-in roles.
    The provider may expose these existing function_tools; the gateway remains
    the enforcement boundary even if a model requests another tool.
    """
    name: str
    instructions: str
    allowed_tools: frozenset[str]
    spec: AgentSpec | None = None

    def __post_init__(self):
        if (not isinstance(self.name, str) or
                not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', self.name) or self.name in ROLE_TOOLS):
            raise ValueError('Use a distinct extension role name; built-in roles cannot be replaced')
        if (not isinstance(self.instructions, str) or
                not self.instructions.strip() or len(self.instructions) > 16000):
            raise ValueError('Provide bounded, nonempty extension instructions')
        if not isinstance(self.allowed_tools, (tuple, list, set, frozenset)):
            raise ValueError('Select existing Agent tools as a collection')
        if (not self.allowed_tools or any(not isinstance(n, str) for n in self.allowed_tools) or
                len(set(self.allowed_tools)) != len(self.allowed_tools) or
                not set(self.allowed_tools) <= AgentGateway.ALLOWED):
            raise ValueError('Extension tools must be unique existing AgentGateway.ALLOWED tools')
        object.__setattr__(self, 'allowed_tools', frozenset(self.allowed_tools))
        if self.spec is not None and (not isinstance(self.spec, AgentSpec) or self.spec.name != self.name
                or self.spec.instructions != self.instructions or self.spec.requested_tools != self.allowed_tools):
            raise ValueError('Registration and versioned specification must match')

    @classmethod
    def from_spec(cls, spec):
        if not isinstance(spec, AgentSpec):
            raise ValueError('Use a validated AgentSpec')
        return cls(spec.name, spec.instructions, spec.requested_tools, spec)


def _registrations(values):
    if not isinstance(values, (tuple, list)):
        raise ValueError('Configure extension registrations in trusted host code')
    registered = {}
    for definition in values:
        if not isinstance(definition, AgentRegistration) or definition.name in registered:
            raise ValueError('Configure distinct AgentRegistration objects')
        registered[definition.name] = definition
    return MappingProxyType(registered)


def _identity(value, subject=None):
    if not isinstance(value, UserIdentity) or (subject is not None and value.subject != subject):
        raise IdentityError('INVALID_IDENTITY', 'Identity provider returned a mismatched subject')
    return value


class CommonHost:
    """One connection per request; existing DB transactions remain authoritative."""

    def __init__(self, identity_provider: IdentityProvider, database=None, *, schema=None, agent_registrations=(), embedding_provider=None, sandbox_client=None, cost_policy=None):
        self._agent_registrations = _registrations(agent_registrations)
        self._identities = identity_provider
        self._embedding_provider = embedding_provider
        self._sandbox_client = sandbox_client
        self._cost_policy = cost_policy
        self._database = (database if isinstance(database, DatabaseSettings) else
                          DatabaseSettings(database, schema or 'ontologics') if database is not None else
                          DatabaseSettings.from_environment(schema=schema))

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


def _definition_fingerprint(spec, messages, provider, implementation=None):
    """Hash declared public settings; never persist prompts or provider secrets.

    Opaque callbacks without public settings may execute but cannot checkpoint.
    A validator's code identity also participates; its declared version must still
    change when external logic/configuration semantics change.
    """
    describe = getattr(provider, 'public_config', None)
    if not callable(describe):
        return None
    public = describe()
    if not isinstance(public, dict):
        return None
    forbidden = {'api_key', 'password', 'token', 'secret', 'authorization'}
    def contains_secret_keys(value):
        if isinstance(value, dict):
            return any(str(k).lower() in forbidden or contains_secret_keys(v) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return any(contains_secret_keys(v) for v in value)
        return False
    if contains_secret_keys(public):
        return None
    def identity(validator):
        code = getattr(validator, '__code__', None)
        return None if validator is None else {
            'module': getattr(validator, '__module__', type(validator).__module__),
            'name': getattr(validator, '__qualname__', type(validator).__qualname__),
            'code_hash': hashlib.sha256(marshal.dumps(code)).hexdigest() if code is not None else None,
        }
    from .agents.registry import implementation_digest
    definition = {'name': spec.name, 'version': spec.version, 'instructions': spec.instructions,
                  'input_schema': asdict(spec.input_schema), 'output_schema': asdict(spec.output_schema),
                  'tools': sorted(spec.requested_tools), 'limits': asdict(spec.limits),
                  'messages': messages, 'provider': public, 'validator': identity(spec.validator),
                  'result_validator': identity(getattr(implementation, 'validate_result', None)),
                  'result_decoder': identity(getattr(implementation, 'decode_result', None))}
    role_digest = implementation_digest(implementation)
    if role_digest is not None:
        definition['builtin_implementation'] = role_digest
    return hashlib.sha256(canonical(definition).encode()).hexdigest()


class _TaskReads:
    """Observe successful public calls, resolve provenance from immutable facts."""
    CONFIRMED_READS = frozenset({'lookup', 'configuration', 'compare_configurations',
        'component_usage', 'investigate', 'compare_investigation', 'export_graph',
        'search_evidence', 'semantic_search'})

    def __init__(self, service):
        self.service, self.results, self.refs, self._snapshots = service, [], {}, {}

    def observe(self, name, arguments, result):
        self.results.append({'name': name, 'arguments': arguments, 'result': result})
        # Draft/receipt evidence is not an approved snapshot citation. It remains
        # available in the tool result with its existing preview/receipt status.
        if name not in self.CONFIRMED_READS or arguments.get('draft_id') is not None:
            return
        if name == 'compare_investigation':
            data = result['data']
            self._resolve(data['before_version'], data['before'])
            self._resolve(data['after_version'], data['after'])
        else:
            self._resolve(result['data_version'], result['data'])

    def _resolve(self, version, data):
        if type(version) is not int:
            raise DomainError('AGENT_EVIDENCE_UNAVAILABLE', 'Evidence has no confirmed data version')
        ids = set(Service._evidence(data))
        def direct_evidence(value):
            if isinstance(value, dict):
                if value.get('type') == 'Evidence' and isinstance(value.get('id'), str):
                    ids.add(value['id'])
                for child in value.values():
                    direct_evidence(child)
            elif isinstance(value, list):
                for child in value:
                    direct_evidence(child)
        direct_evidence(data)
        if not ids:
            return
        if version not in self._snapshots:
            self._snapshots[version] = {n['id']: n for n in self.service._nodes(version)}
        index = self._snapshots[version]
        for eid in sorted(ids):
            node = index.get(eid, {})
            source = index.get(node.get('source_id'), {})
            if (node.get('type') != 'Evidence' or source.get('type') != 'SourceDocument'
                    or node.get('origin') != source.get('origin')):
                raise DomainError('AGENT_EVIDENCE_UNAVAILABLE', 'Cannot resolve evidence in its confirmed snapshot')
            try:
                ref = EvidenceRef(version, eid, source['id'], node['origin'], node['locator'], source['sha256'])
            except (ValueError, KeyError, TypeError):
                raise DomainError('AGENT_EVIDENCE_UNAVAILABLE', 'Invalid confirmed evidence reference') from None
            self.refs[(version, eid)] = ref

    def selected(self, value):
        if 'evidence_ids' not in value:
            return tuple(self.refs[key] for key in sorted(self.refs))
        selected = value['evidence_ids']
        available = {eid for version, eid in self.refs}
        if (not isinstance(selected, list) or any(not isinstance(eid, str) for eid in selected)
                or len(set(selected)) != len(selected) or not set(selected) <= available):
            raise DomainError('AGENT_EVIDENCE_MISMATCH', 'Citations must refer to evidence actually read in this task')
        return tuple(self.refs[key] for key in sorted(self.refs) if key[1] in selected)


class _RequestGateway(AgentGateway):
    """The compatibility gateway also charges trusted direct Agent calls."""
    def __init__(self, *args, budget, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_budget = budget

    def call(self, tool, arguments):
        self._request_budget.consume('tool')
        result = super().call(tool, arguments)
        self._request_budget.check()
        return result


class RequestSession:
    """Host-facing operations for one resolved person, not an authenticated session."""

    def __init__(self, service, identity, identity_mode, *, agent_registrations=None, budget=None, identity_lookup=None):
        self._service, self._identity = service, identity
        self._identity_mode = identity_mode
        self._budget = budget or ExecutionBudget()
        self._identity_lookup = identity_lookup
        self._agent_registrations = MappingProxyType(dict(agent_registrations or {}))

    @property
    def context(self):
        return {'subject': self._identity.subject, 'actor': self._identity.actor,
                'identity_mode': self._identity_mode, 'permissions': sorted(self._identity.permissions)}

    def call(self, tool, arguments):
        """User query/draft path; no identity, review, apply or seed in arguments."""
        self._budget.consume("tool")
        result = AgentGateway(self._service, self._identity.actor).call(tool, arguments)
        self._budget.check()
        return result

    def _role_tools(self, role):
        if isinstance(role, str):
            if role in ROLE_TOOLS:
                return ROLE_TOOLS[role]
            if role in self._agent_registrations:
                return self._agent_registrations[role].allowed_tools
        raise ValueError('Select a built-in role or a trusted host registration')

    def agent(self, role):
        """Bind a configured role to this user; it cannot exceed user permissions."""
        return _RequestGateway(self._service, self._identity.actor, allowed_tools=self._role_tools(role), budget=self._budget)

    def run_agent(self, role, provider, messages, *, config, capture_tool_trace=False, pinned_context=None, final_validator=None, budget=None, tool_observer=None, task_tools=None):
        selected_budget = self._budget if budget is None else budget
        if not isinstance(selected_budget, ExecutionBudget) or not selected_budget.is_descendant_of(self._budget):
            raise DomainError('EXECUTION_CONTEXT_MISMATCH', 'A role cannot replace its request execution budget')
        allowed = self._role_tools(role)
        if task_tools is not None:
            if not set(task_tools) <= allowed:
                raise DomainError('TOOL_NOT_ALLOWED', 'Task tools exceed the registered role')
            allowed = frozenset(task_tools)
        registration = self._agent_registrations.get(role)
        if registration is not None and isinstance(messages, list):
            # Keep existing built-in prompts/callers unchanged. The caller is
            # trusted host code; end-user text belongs in a user message only.
            messages = [{'role': 'system', 'content':
                'Registered host role: ' + registration.name + '\n' + registration.instructions}, *messages]
        return ModelRuntime(self._service, self._identity.actor, provider, config=config,
                            allowed_tools=allowed, capture_tool_trace=capture_tool_trace,
                            budget=selected_budget, pinned_context=pinned_context, final_validator=final_validator,
                            tool_observer=tool_observer).run(messages)

    def run_task(self, role, implementation, task, provider, *, config, capture_tool_trace=False, _resume_fingerprint=None):
        """Versioned extension path; the implementation only prepares messages.

        Existing run_agent callers retain their original interface. Authority is
        still bound by this request, never by an AgentTask/AgentResult payload.
        """
        spec = self._task_spec(role, implementation, task)
        spec.input_schema.validate(task.data())
        if not callable(getattr(implementation, 'messages', None)):
            raise ValueError('Implementation must prepare messages for the common runtime')
        # Revalidate current access and a real snapshot; a schema-valid version
        # is not proof that the requested data exists or may be read.
        current_version = self._service._version()
        snapshot = self.call('lookup', {'kind': 'ProductModel', 'version': task.context.data_version})
        if not snapshot['ok']:
            raise DomainError(snapshot['errors'][0]['code'], 'Cannot read task snapshot')
        supplied = implementation.messages(task)
        if not isinstance(supplied, list):
            raise ValueError('Role implementation must supply a message list')
        messages = self._task_messages(implementation, task, supplied)
        definition_hash = _definition_fingerprint(spec, messages, provider, implementation)
        if _resume_fingerprint is not None and definition_hash != _resume_fingerprint:
            raise DomainError('CHECKPOINT_CONTRACT_MISMATCH', 'Task definition changed during resume')
        reads = _TaskReads(self._service)
        role_validator = getattr(implementation, 'validate_result', None)
        if role_validator is not None and not callable(role_validator):
            raise ValueError('The optional result validator must be callable')
        validated_payload = None
        def validate_result(text):
            nonlocal validated_payload
            decoder = getattr(implementation, 'decode_result', None)
            value = (spec.validate_payload(decoder(text, task, json.loads(canonical(reads.results))))
                     if callable(decoder) else spec.validate_result(text))
            if 'data_version' in value and (type(value['data_version']) is not int or
                    value['data_version'] != task.context.data_version):
                raise DomainError('AGENT_RESULT_VERSION_MISMATCH', 'Result version differs from the host-pinned task')
            reads.selected(value)
            if role_validator is not None:
                # Validators receive detached data, never the observer or host.
                try:
                    valid = role_validator(json.loads(canonical(value)), task, json.loads(canonical(reads.results)))
                except Exception:
                    valid = False
                if valid is not True:
                    raise DomainError('AGENT_RESULT_REJECTED', 'Role result differs from the observed common tool results')
            validated_payload = value
            return value
        runtime = self.run_agent(role, provider, messages, config=config,
            capture_tool_trace=capture_tool_trace, pinned_context=task.context,
            final_validator=validate_result, budget=self._budget.child(spec.limits), tool_observer=reads.observe,
            task_tools=spec.requested_tools)
        payload = validated_payload if runtime['ok'] else None
        result = AgentResult(spec.name, spec.version, payload, runtime, context=task.context,
                             evidence_refs=reads.selected(payload) if payload is not None else ())
        metadata = {'agent_name': spec.name, 'agent_version': spec.version,
                    'task_schema_version': task.schema_version,
                    'input_schema_version': spec.input_schema.version,
                    'output_schema_version': spec.output_schema.version, 'definition_hash': definition_hash}
        row = self._service.db.execute('SELECT config FROM runtime_runs WHERE id=%s', (runtime['run_id'],)).fetchone()
        saved = json.loads(row['config'])
        saved['agent_contract'] = metadata
        if definition_hash is not None and not spec.requested_tools & {'prepare_draft', 'prepare_source_draft'}:
            usage = self._budget.snapshot()
            # A restart cannot replenish either the outer or role-specific caps.
            for key, value in asdict(spec.limits).items():
                usage['limits'][key] = min(usage['limits'][key], value)
            task_hash = hashlib.sha256(canonical({'payload': task.data(), 'context': task.context.to_dict()}).encode()).hexdigest()
            saved['read_checkpoint'] = {
                'schema_version': 'read-checkpoint-1', 'task_hash': task_hash,
                'base_version': current_version, 'data_version': task.context.data_version,
                'budget': usage, 'expires_at': time.time() + max(0, usage['limits']['deadline_seconds'] - usage['elapsed_seconds']),
                'consumed': False,
            }
        self._service.db.execute('UPDATE runtime_runs SET config=%s WHERE id=%s',
                                (json.dumps(saved, sort_keys=True), runtime['run_id']))
        response = result.to_dict()
        response['request_execution_budget'] = self._budget.snapshot()
        response['read_restart'] = {
            'checkpoint_available': 'read_checkpoint' in saved and runtime['status'] in {'failed', 'step_limit'},
            'run_id': runtime['run_id'], 'mode': 'returned_failed_read_restart',
            'requires': ['current_identity', 'same_task_and_contract', 'unchanged_snapshot',
                         'same_cost_policy', 'remaining_budget', 'one_use'],
            'crash_recovery': False,
        }
        return response

    def _task_spec(self, role, implementation, task):
        if not isinstance(task, AgentTask):
            raise ValueError('Select an AgentTask')
        from .agents.registry import is_builtin
        if is_builtin(implementation):
            if implementation.role != role:
                raise ValueError('Builtin role does not match the task')
            return implementation.spec
        registration = self._agent_registrations.get(role)
        if registration is None or registration.spec is None:
            raise ValueError('Select a registered versioned AgentSpec')
        return registration.spec

    @staticmethod
    def _task_messages(implementation, task, supplied):
        from .agents.registry import is_builtin
        pinned = build_context_messages(task.context, [], profile='structured')
        if is_builtin(implementation):
            # Preserve the real role's existing first system/user messages. The
            # new host context is a system message, never an extra user request.
            return supplied[:1] + pinned[:1] + supplied[1:]
        return pinned + supplied

    def resume_task(self, checkpoint_run_id, role, implementation, task, provider, *, config, capture_tool_trace=False):
        """Restart an interrupted/failed read-only task once, with remaining limits.

        This replays reads from the pinned input, not a persisted model transcript.
        Mutating Product tasks use existing draft/idempotency recovery instead.
        Revalidate identity, current snapshot, role/schema/model and prior budget;
        no checkpoint grants review/apply authority or repeats arbitrary writes.
        """
        if not callable(self._identity_lookup):
            raise DomainError('CHECKPOINT_IDENTITY_UNAVAILABLE', 'Resume requires current trusted identity lookup')
        try:
            refreshed = _identity(self._identity_lookup(self._identity.subject), self._identity.subject)
        except Exception:
            raise DomainError('CHECKPOINT_IDENTITY_UNAVAILABLE', 'Current identity could not be verified') from None
        self._service.principals[refreshed.actor] = refreshed.principal()
        self._identity = refreshed
        self._service._require(self._identity.actor, 'read')
        spec = self._task_spec(role, implementation, task)
        if spec.requested_tools & {'prepare_draft', 'prepare_source_draft'}:
            raise DomainError('CHECKPOINT_WRITE_FORBIDDEN', 'Checkpoint replay is read-only')
        row = self._service.db.execute('SELECT * FROM runtime_runs WHERE id=%s', (checkpoint_run_id,)).fetchone()
        if row is None or row['actor'] != self._identity.actor:
            raise DomainError('CHECKPOINT_NOT_ACCESSIBLE', 'Checkpoint is unavailable for this request identity')
        saved = json.loads(row['config'])
        checkpoint = saved.get('read_checkpoint', {})
        metadata = saved.get('agent_contract', {})
        expected_hash = hashlib.sha256(canonical({'payload': task.data(), 'context': task.context.to_dict()}).encode()).hexdigest()
        if not callable(getattr(implementation, 'messages', None)):
            raise DomainError('CHECKPOINT_CONTRACT_MISMATCH', 'Task implementation is unavailable')
        supplied = implementation.messages(task)
        if not isinstance(supplied, list):
            raise DomainError('CHECKPOINT_CONTRACT_MISMATCH', 'Task implementation returned invalid messages')
        definition_hash = _definition_fingerprint(spec, self._task_messages(implementation, task, supplied), provider, implementation)
        if (definition_hash is None or metadata.get('definition_hash') != definition_hash or checkpoint.get('schema_version') != 'read-checkpoint-1' or checkpoint.get('task_hash') != expected_hash
                or metadata.get('agent_name') != spec.name or metadata.get('agent_version') != spec.version
                or any(saved.get(k) != value for k, value in asdict(config).items())):
            raise DomainError('CHECKPOINT_CONTRACT_MISMATCH', 'Resume requires the same task, contract, model and settings')
        if checkpoint.get('consumed'):
            raise DomainError('CHECKPOINT_ALREADY_RESUMED', 'Checkpoint has already been consumed')
        if row['status'] not in {'failed', 'step_limit'}:
            raise DomainError('CHECKPOINT_NOT_RESUMABLE', 'Only a returned failed read task can restart')
        if checkpoint['base_version'] != self._service._version():
            raise DomainError('STALE_CHECKPOINT', 'Confirmed data changed; start a new reviewed task context')
        remaining = checkpoint['expires_at'] - time.time()
        if remaining <= 0:
            raise DomainError('DEADLINE_EXCEEDED', 'Checkpoint deadline expired; do not silently reset the budget')
        previous = checkpoint['budget']
        limits = dict(previous['limits'])
        limits['deadline_seconds'] = min(limits['deadline_seconds'], remaining)
        resumed_budget = self._budget.child(BudgetLimits(**limits))
        resumed_budget.restore_usage(previous['attempted_calls'])
        resumed_budget.restore_cost(previous.get('cost'))
        # Atomic one-use reservation prevents two processes resetting the same
        # persisted budget. A crash after reservation requires an explicit new task.
        checkpoint['consumed'] = True
        saved['read_checkpoint'] = checkpoint
        updated = self._service.db.execute('UPDATE runtime_runs SET config=%s WHERE id=%s AND config=%s',
            (json.dumps(saved, sort_keys=True), checkpoint_run_id, row['config']))
        if updated.rowcount != 1:
            raise DomainError('CHECKPOINT_ALREADY_RESUMED', 'Another request consumed this checkpoint')
        original = self._budget
        self._budget = resumed_budget
        try:
            result = self.run_task(role, implementation, task, provider, config=config, capture_tool_trace=capture_tool_trace,
                                   _resume_fingerprint=definition_hash)
        finally:
            self._budget = original
        result['resumed_from'] = checkpoint_run_id
        return result

    def review(self, draft_id, version, *, decision, note, request_key):
        """Explicit host action by the resolved user, never part of Agent tools."""
        return self._service.review(self._identity.actor, draft_id, version,
                                    decision=decision, note=note, request_key=request_key)

    def intake(self, filename, content_base64):
        from .receipts import source_intake
        return source_intake(self._service, self._identity.actor, filename, content_base64)

    def source_draft(self, **arguments):
        from .receipts import source_draft
        return source_draft(self._service, self._identity.actor, **arguments)

    def source_compare(self, **arguments):
        from .receipts import source_compare
        return source_compare(self._service, self._identity.actor, **arguments)

    def workspace(self):
        from .receipts import workspace
        return workspace(self._service, self._identity.actor)

    def history_intake(self, filename, content_base64):
        from .receipts import history_intake
        return history_intake(self._service, self._identity.actor, filename, content_base64)

    def record_agent_check(self, runtime_id, *, accepted, code=None, display_hash=None):
        def act():
            if not accepted:
                raise DomainError(code or 'AI_RESULT_MISMATCH', 'Model answer verification failed')
            return self._service._envelope(ok=True, data={'runtime_id': runtime_id, 'accepted': True})
        return self._service._run(self._identity.actor, 'agent_answer_validation',
                                 {'runtime_id': runtime_id, 'accepted': accepted, 'code': code, 'display_hash': display_hash}, act)
