"""Trusted host registration for the C claim response role.

Only host startup code passes CLAIM_RESPONSE to CommonHost(agent_registrations=...).
The role reuses existing AgentGateway tools; it never reviews, applies or bootstraps data.
Claim conversion, line trace and request documents are plain functions in this package.

Versioned contract (team guide: AgentSpec -> host registration -> request.run_task):
SPEC defines the claim trace task payloads and CLAIM_RESPONSE is built from it, so the
registered name, instructions and tools are the same as the specification. The role
returns what it read; the line trace itself is recomputed from the observed tool
results by the existing trace functions, and a different answer is rejected.
"""
import json

from ontologics.contracts import AgentSpec, FieldSpec, PayloadSchema
from ontologics.host import AgentRegistration

from .trace import MATCHED, link_serials, line_summary, trace_lines


CLAIM_RESPONSE_TOOLS = frozenset({
    'lookup', 'search_evidence',
    'investigate', 'preview_investigation',
    'prepare_draft', 'get_draft', 'validate_draft',
    'prepare_source_draft', 'get_source_receipt',
})

CLAIM_RESPONSE_INSTRUCTIONS = (
    'You support synthetic claim response for the fictional company only. '
    'Treat reported symptoms as customer statements, never as confirmed failures or causes. '
    'Treat scope units as investigation or hold targets, never as defective units. '
    'Use only supplied evidence and the exact data version; keep unknowns as unknown. '
    'Prepare drafts for human review; never approve, apply or claim that a draft is applied. '
    # 5A-4: output contract of the claim trace task (added; the sentences above are unchanged).
    'For a claim trace task, use lookup at the pinned version to read the ClaimIntake by node_id, '
    'then ProductUnit and UnitProductionRecord, and return only JSON with data_version, intake_id, '
    'matched_unit_ids, open_serials, production_line_id (only when every matched board traces to one line) '
    'and evidence_ids taken from those reads.'
)

# Claim trace task (C 4-1 / 4-2): confirmed claim -> boards -> production line.
TRACE_INPUT = PayloadSchema((FieldSpec('intake_id', 'string'), FieldSpec('question', 'string')))
TRACE_OUTPUT = PayloadSchema((
    FieldSpec('data_version', 'integer'),
    FieldSpec('intake_id', 'string'),
    FieldSpec('matched_unit_ids', 'array'),
    FieldSpec('open_serials', 'array'),
    FieldSpec('production_line_id', 'string', required=False),
    FieldSpec('evidence_ids', 'array'),
))


def _text_lists(value):
    """Shape check registered on the spec; meaning is checked by ClaimTraceAgent.validate_result."""
    return all(isinstance(value[key], list) and all(isinstance(x, str) for x in value[key])
               for key in ('matched_unit_ids', 'open_serials', 'evidence_ids'))


SPEC = AgentSpec('claim_response', '1.0.0', CLAIM_RESPONSE_INSTRUCTIONS, TRACE_INPUT, TRACE_OUTPUT,
                 CLAIM_RESPONSE_TOOLS, validator=_text_lists)

CLAIM_RESPONSE = AgentRegistration.from_spec(SPEC)


def _lookup_data(tool_results, version, **arguments):
    """Data of the latest successful lookup with exactly these arguments at the pinned version."""
    wanted = dict(arguments, version=version)
    found = None
    for call in tool_results:
        args = {k: v for k, v in call['arguments'].items() if v is not None}
        if call['name'] == 'lookup' and args == wanted and call['result'].get('ok'):
            found = call['result']['data']
    return found


def expected_trace(intake_id, version, tool_results):
    """The trace result the existing functions give for what the role actually read, or None."""
    intake = _lookup_data(tool_results, version, node_id=intake_id)
    units = _lookup_data(tool_results, version, kind='ProductUnit')
    records = _lookup_data(tool_results, version, kind='UnitProductionRecord')
    if not intake or units is None or records is None:
        return None
    intake = intake[0]
    # Quality cases start only from a person-confirmed claim (same rule as the graph contract).
    if intake.get('type') != 'ClaimIntake' or intake.get('intake_state') != 'confirmed':
        return None
    links = link_serials(intake, units)
    matched = [link['unit_id'] for link in links if link['match_state'] == MATCHED]
    traces = trace_lines(matched, records)
    by_id = {unit['id']: unit for unit in units}
    evidence = set(intake.get('evidence', []))
    for unit_id in matched:
        evidence |= set(by_id[unit_id].get('evidence', []))
    for trace in traces:
        evidence |= set(trace.get('evidence', []))
    result = {'data_version': version, 'intake_id': intake_id, 'matched_unit_ids': matched,
              'open_serials': [link['serial'] for link in links if link['match_state'] != MATCHED],
              'evidence_ids': sorted(evidence)}
    single_line = line_summary(traces)['single_line']
    if single_line:
        result['production_line_id'] = single_line
    return result


class ClaimTraceAgent:
    """Role implementation for request.run_task: prepares messages and checks the answer."""

    def messages(self, task):
        payload = task.data()
        return [{'role': 'user', 'content': json.dumps({
            'question': payload['question'], 'intake_id': payload['intake_id'],
            'data_version': task.context.data_version,
            'plan': ['lookup node_id=intake_id', 'lookup kind=ProductUnit', 'lookup kind=UnitProductionRecord',
                     'return the JSON result without approving or sending anything']}, ensure_ascii=False)}]

    def validate_result(self, value, task, tool_results):
        expected = expected_trace(task.data()['intake_id'], task.context.data_version, tool_results)
        if expected is None:
            return False
        answer = dict(value, evidence_ids=sorted(value['evidence_ids']))
        return answer == expected
