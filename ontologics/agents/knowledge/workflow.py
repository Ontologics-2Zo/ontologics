"""Knowledge relationship, lexical and semantic retrieval flows."""
import json
from .instructions import SEARCH_PROMPT_1, SEARCH_PROMPT_2, SEARCH_PROMPT_3
from ...service import DomainError
from ..execution import run_reads, needs_input, finish_screen

def knowledge_search(app, request, question, version, context):
    """Extract one explicit search term; never turn a candidate into a fact."""
    if context.get('search_mode') == 'semantic':
        result = run_reads(app, request, 'knowledge', question,
            [{'name': 'semantic_search', 'arguments': {'query': question, 'origin': context.get('origin', 'all'), 'version': version}}],
            SEARCH_PROMPT_3)
        if isinstance(result.get('data'), dict):
            result['data']['conditions'] = {'version': version, 'search_query': question, 'search_mode': 'semantic'}
        return result
    plan = run_reads(app, request, 'knowledge', question,
        [{'name': 'lookup', 'arguments': {'kind': 'ProductModel', 'version': version}}],
        SEARCH_PROMPT_1, flow='plan')
    if not plan['ok']:
        return plan
    try:
        data = json.loads(plan['data']['runtime']['text'])
        if not isinstance(data, dict) or set(data) != {'query'}:
            raise ValueError()
        query = data['query']
        if query is not None and (not isinstance(query, str) or not 1 <= len(query.strip()) <= 200):
            raise ValueError()
    except (ValueError, TypeError):
        raise DomainError('AI_PLAN_INVALID', '검색어를 확인하지 못했습니다. 제품·부품·문서의 이름 또는 ID를 알려주세요.') from None
    if not query or query.strip().casefold() not in question.casefold():
        return needs_input(request, version, 'knowledge', ['search_query'],
            ['어떤 제품·부품·로트·문서의 근거를 찾을까요? 이름 또는 ID를 알려주세요.'],
            'evidence', runtime=plan['data']['runtime'], agent_result=plan['data']['agent_result'])
    query = query.strip()
    result = run_reads(app, request, 'knowledge', question,
        [{'name': 'search_evidence', 'arguments': {'query': query, 'origin': context.get('origin', 'all'), 'version': version}}],
        SEARCH_PROMPT_2)
    if isinstance(result.get('data'), dict):
        result['data']['search_plan_runtime'] = plan['data']['runtime']
        result['data']['search_plan_agent_result'] = plan['data']['agent_result']
        result['data']['conditions'] = {'version': version, 'search_query': query, 'origin': context.get('origin', 'all')}
    return result

def run_screen(app, request, body):
    question, version, screen, context = (body[k] for k in ('question', 'version', 'screen', 'context'))
    role = 'knowledge'
    if context.get('search_query'):
        calls = [{'name': 'semantic_search' if context.get('search_mode') == 'semantic' else 'search_evidence',
                  'arguments': {'query': context['search_query'], 'origin': context.get('origin', 'all'), 'version': version}}]
        return finish_screen(run_reads(app, request, role, question, calls), body)
    if not context.get('node_id') and screen in {'home', 'evidence'}:
        return knowledge_search(app, request, question, version, context)
    if context.get('node_id'):
        args = {'node_id': context['node_id'], 'version': version}
    else:
        kind = context.get('kind', 'Evidence')
        if kind not in {'ProductModel', 'ProductRevision', 'Configuration', 'BOMItem', 'ComponentModel', 'ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage', 'SourceDocument', 'Evidence'}:
            raise DomainError('INVALID_INPUT', '조회할 근거·이력 종류를 확인하세요.')
        args = {'kind': kind, 'version': version}
    calls = [{'name': 'lookup', 'arguments': args}]
    return finish_screen(run_reads(app, request, role, question, calls), body)
