"""Typed adapters for the four real screen roles, preserving their tool flows.

Legacy provider text stays compatible. Structured conclusions are checked by
the same existing role validators; free explanation is explicitly unverified.
Only trusted application code selects these fixed contracts, never model output.
"""
import json

from .contracts import AgentSpec, AgentTask, FieldSpec, PayloadSchema
from .harness import PinnedContext, canonical
from .service import DomainError


INPUT = PayloadSchema((FieldSpec('question', 'string'), FieldSpec('calls', 'array'),
                       FieldSpec('flow', 'string'), FieldSpec('options', 'object')))


def output(*fields):
    return PayloadSchema((FieldSpec('data_version', 'integer'), *fields))


CONTRACTS = {
    ('product', 'read'): (output(FieldSpec('explanation', 'string'), FieldSpec('meaning_validation', 'string'),
                               FieldSpec('provenance_scope', 'string')),
        {'get_source_receipt', 'get_draft', 'validate_draft', 'configuration'},
        'Explain an exact product configuration or unapproved candidate; never imply approval.'),
    ('product', 'draft'): (output(FieldSpec('draft_id', 'string'), FieldSpec('draft_version', 'integer'),
                                FieldSpec('validation_valid', 'boolean'), FieldSpec('action', 'string'),
                                FieldSpec('source_receipt_id', 'string'), FieldSpec('provenance_scope', 'string')),
        {'get_source_receipt', 'prepare_source_draft', 'validate_draft'},
        'Read the exact receipt, prepare its unapproved draft, validate that version; never approve.'),
    ('knowledge', 'read'): (output(FieldSpec('explanation', 'string'), FieldSpec('meaning_validation', 'string'),
                                 FieldSpec('provenance_scope', 'string')),
        {'lookup', 'configuration', 'component_usage', 'search_evidence'},
        'Explain confirmed source-scoped evidence without merging identities or inferring safety.'),
    ('knowledge', 'semantic'): (output(FieldSpec('answerable', 'boolean'), FieldSpec('evidence_ids', 'array')),
        {'semantic_search'}, 'Select only returned candidate evidence at the pinned data version.'),
    ('knowledge', 'plan'): (output(FieldSpec('search', 'object')),
        {'lookup'}, 'Propose only a search term explicitly present in the request; missing terms remain null.'),
    ('quality', 'conditions'): (output(FieldSpec('proposal', 'object')),
        {'lookup'}, 'Propose explicit board, lot and timezone-aware A/B inputs; never fill missing conditions.'),
    ('quality', 'investigation'): (output(FieldSpec('observations', 'array'), FieldSpec('evidence_ids', 'array')),
        {'investigate', 'compare_investigation'},
        'Preserve exact common-tool A/B judgments, per-board evidence and needs_confirmation; no defect/safety conclusion.'),
    ('supervisor', 'route'): (output(FieldSpec('target_role', 'string'), FieldSpec('reason', 'string')),
        {'lookup'}, 'Route to Product, Knowledge or Quality; routing never performs or authorizes a mutation.'),
}


class BuiltinRole:
    def __init__(self, role, flow, messages):
        schema, tools, instructions = CONTRACTS[(role, flow)]
        self.role, self.flow = role, flow
        version = '1.0.1' if (role, flow) == ('quality', 'investigation') else '1.0.0'
        self.spec = AgentSpec(role, version, instructions, INPUT, schema, frozenset(tools))
        self._messages = canonical(messages)

    def messages(self, task):
        if task.data()['flow'] != self.flow:
            raise ValueError('Task flow differs from the fixed role contract')
        return json.loads(self._messages)

    def decode_result(self, text, task, tool_results):
        payload, version = task.data(), task.context.data_version
        calls = payload['calls']
        for call in calls:
            if not any(row['name'] == call['name'] and row['arguments'] == call['arguments'] for row in tool_results):
                raise DomainError('AI_REQUIRED_TOOLS_MISSING', 'The role did not complete its required common calls')
        results = [next(row['result'] for row in reversed(tool_results)
                        if row['name'] == call['name'] and row['arguments'] == call['arguments']) for call in calls]
        if self.flow == 'read':
            preview = any(row['name'] in {'get_source_receipt', 'get_draft', 'validate_draft'} for row in tool_results)
            return {'data_version': version, 'explanation': text, 'meaning_validation': 'free_text_unverified',
                    'provenance_scope': 'unapproved_candidate_or_draft' if preview else 'confirmed_snapshot'}
        if self.flow == 'draft':
            writes = [row['result']['data'] for row in tool_results if row['name'] == 'prepare_source_draft']
            validations = [row for row in tool_results if row['name'] == 'validate_draft']
            if not writes or not validations:
                raise DomainError('AI_DRAFT_INCOMPLETE', 'Exact draft save and validation are required')
            draft, checked = writes[-1], validations[-1]
            if checked['arguments'] != {'draft_id': draft['draft_id'], 'version': draft['draft_version']}:
                raise DomainError('AI_CONDITION_MISMATCH', 'Validation must use the saved draft version')
            return {'data_version': version, 'draft_id': draft['draft_id'], 'draft_version': draft['draft_version'],
                    'validation_valid': checked['result']['data']['validation']['valid'], 'action': 'draft_prepared',
                    'source_receipt_id': next(call['arguments']['receipt_id'] for call in calls
                                              if call['name'] == 'get_source_receipt'),
                    'provenance_scope': 'unapproved_candidate_or_draft'}
        if self.flow == 'investigation':
            from .web import verified_quality_text
            by_mode = {row['arguments']['query']['mode']: row['result'] for row in tool_results}
            _, observations = verified_quality_text(text, by_mode)
            ids = sorted({eid for row in observations for phase in ('before', 'after')
                          if row[phase] is not None for eid in row[phase]['evidence']})
            return {'data_version': version, 'observations': observations, 'evidence_ids': ids}
        if self.flow == 'semantic':
            from .assistant import verified_knowledge_text
            _, selected = verified_knowledge_text(text, results[-1])
            return {'data_version': version, 'answerable': selected['answerable'],
                    'evidence_ids': selected['selected_evidence_ids']}
        if self.flow == 'conditions':
            from .assistant import validated_quality_proposal
            proposal = validated_quality_proposal(text, payload['question'], results)
            return {'data_version': version, 'proposal': proposal}
        try:
            value = json.loads(text)
            if self.flow == 'route':
                hint = payload['options'].get('route_hint')
                if (not isinstance(value, dict) or set(value) != {'role', 'reason'} or
                        value['role'] not in {'product', 'knowledge', 'quality'} or
                        not isinstance(value['reason'], str) or (hint and value['role'] != hint)):
                    raise ValueError()
                return {'data_version': version, 'target_role': value['role'], 'reason': value['reason']}
            if not isinstance(value, dict) or set(value) != {'query'}:
                raise ValueError()
            query = value['query']
            if query is not None and (not isinstance(query, str) or not 1 <= len(query.strip()) <= 200):
                raise ValueError()
            if query and query.strip().casefold() not in payload['question'].casefold():
                query = None
            return {'data_version': version, 'search': {'query': query}}
        except (ValueError, TypeError, KeyError):
            code = 'AI_ROUTING_INVALID' if self.flow == 'route' else 'AI_PLAN_INVALID'
            raise DomainError(code, 'The role returned an invalid routing or input proposal') from None


def builtin_task(role, flow, question, version, calls, messages, *, options=None, conditions=None):
    implementation = BuiltinRole(role, flow, messages)
    task = AgentTask({'question': question, 'calls': calls, 'flow': flow, 'options': options or {}},
                     PinnedContext(implementation.spec.instructions, version, conditions=conditions or {}))
    return implementation, task
