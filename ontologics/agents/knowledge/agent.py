"""Knowledge schemas, model-output decoding and evidence display checks."""
import json
from ..base import RoleAgent, read_result
from ...service import DomainError
from ...contracts import withhold_agent_result
from ...model import digest
from ...responses import failure
from .schemas import CONTRACTS
from .instructions import SEMANTIC_PROMPT
from .validators import verified_knowledge_text

class KnowledgeAgent(RoleAgent):
    ROLE = 'knowledge'
    CONTRACTS = CONTRACTS

    @classmethod
    def read_settings(cls, calls, flow, extra_prompt):
        if len(calls) == 1 and calls[0]['name'] == 'semantic_search':
            return 'semantic', extra_prompt + SEMANTIC_PROMPT
        return flow, extra_prompt

    def decode(self, text, task, tool_results, results):
        version = task.context.data_version
        if self.flow == 'read':
            return read_result(version, text, tool_results)
        if self.flow == 'semantic':
            _, selected = verified_knowledge_text(text, results[-1])
            return {'data_version': version, 'answerable': selected['answerable'],
                    'evidence_ids': selected['selected_evidence_ids']}
        try:
            value = json.loads(text)
            if not isinstance(value, dict) or set(value) != {'query'}:
                raise ValueError()
            query = value['query']
            if query is not None and (not isinstance(query, str) or not 1 <= len(query.strip()) <= 200):
                raise ValueError()
            if query and query.strip().casefold() not in task.data()['question'].casefold():
                query = None
            return {'data_version': version, 'search': {'query': query}}
        except (ValueError, TypeError, KeyError):
            raise DomainError('AI_PLAN_INVALID', 'The role returned an invalid routing or input proposal') from None

    def finalize_read(self, request, data, results):
        if self.flow != 'semantic':
            return None
        runtime = data['runtime']
        agent_result = data['agent_result']
        try:
            display, selection = verified_knowledge_text(runtime['text'], results[0])
        except DomainError as exc:
            runtime.update(text='', output_withheld=True)
            withhold_agent_result(agent_result, exc.code, exc.message)
            request.record_agent_check(runtime['run_id'], accepted=False, code=exc.code)
            return failure(exc.code, exc.message, data=data)
        runtime.update(text='', output_withheld=True)
        data.update(display_text=display, display_hash=digest(display),
                    verified_selection=selection, explanation_mode='verified_evidence_template')
        checked = request.record_agent_check(runtime['run_id'], accepted=True, display_hash=data['display_hash'])
        data['verification_run_id'] = checked.get('run_id')
        if not checked['ok']:
            data['display_text'] = ''
            error = checked['errors'][0]
            withhold_agent_result(agent_result, error['code'], error['message'])
            return failure(error['code'], error['message'], data=data)
