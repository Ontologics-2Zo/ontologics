"""Sequential A -> B flow; all time reasoning stays in the common tools."""
import json

from ontologics.contracts import AgentSpec, FieldSpec, PayloadSchema

INSTRUCTIONS = (
    'Use investigate for pinned query A, then pinned query B, at the pinned data version. '
    'Do not calculate installation status yourself. Return JSON: data_version, '
    'by_mode (A/B each exactly the tool data), evidence_ids (union of tool evidence). '
    'Preserve needs_confirmation and synthetic origin. Never approve or infer defects/safety.'
)
INPUT = PayloadSchema((FieldSpec('purpose', 'string'), FieldSpec('modes', 'array')))
OUTPUT = PayloadSchema((FieldSpec('data_version', 'integer'),
                        FieldSpec('by_mode', 'object'), FieldSpec('evidence_ids', 'array')))
SPEC = AgentSpec('typed_history_reader', '1.0.0', INSTRUCTIONS, INPUT, OUTPUT,
                 frozenset({'investigate'}),
                 validator=lambda value: set(value['by_mode']) == {'A', 'B'})


class HistoryReader:
    def messages(self, task):
        payload = task.data()
        if payload['modes'] != ['A', 'B'] or set(task.context.to_dict()['conditions'].get('queries', {})) != {'A', 'B'}:
            raise ValueError('This example requires explicit pinned A and B queries')
        return [{'role': 'user', 'content': json.dumps({'purpose': payload['purpose'],
                'plan': ['investigate A', 'investigate B', 'return both unchanged tool results']})}]

    def validate_result(self, value, task, tool_results):
        # Compare to actual common-tool observations, not a second time algorithm.
        observed = {r['arguments']['query']['mode']: r['result']['data']
                    for r in tool_results if r['name'] == 'investigate'}
        evidence = {e for r in tool_results if r['name'] == 'investigate'
                    for e in r['result']['evidence']}
        return (set(observed) == {'A', 'B'} and value['by_mode'] == observed
                and set(value['evidence_ids']) == evidence)


class ScriptedHistoryReader:
    """A different internal sequence using the same runtime, not a custom executor."""
    def __init__(self, task):
        self.context = task.context.to_dict()

    def public_config(self):
        return {'provider': 'scripted-history-reader-1'}

    def __call__(self, *, model, messages):
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        if len(results) < 2:
            mode = ['A', 'B'][len(results)]
            return {'text': '', 'tool_calls': [{'name': 'investigate', 'arguments': {
                'query': self.context['conditions']['queries'][mode],
                'version': self.context['data_version']}}]}
        return {'text': json.dumps({'data_version': self.context['data_version'],
            'by_mode': {mode: result['data'] for mode, result in zip(['A', 'B'], results)},
            'evidence_ids': sorted({e for r in results for e in r['evidence']})}), 'tool_calls': []}
