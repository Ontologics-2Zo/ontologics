"""Transport-neutral entry point for the shared AI panel request contract."""
from dataclasses import dataclass
from ..service import DomainError
from ..responses import exact_keys
from ..openai_provider import MODEL
from .registry import run_screen
from .supervisor.workflow import route
from .product.workflow import prepare_receipt_draft
from .execution import attach_delegation

@dataclass(frozen=True)
class AgentApplication:
    """Host-bound services shared by HTTP, CLI and in-process callers.

    Context must come from a trusted IdentityProvider boundary. This object does
    not grant authority or accept model-supplied identity/roles.
    """
    host: object
    context: object
    provider_factory: object = None
    model: str = MODEL

def assistant(app, body):
    exact_keys(body, ('question', 'version', 'screen', 'context'), ('action', 'request_key'))
    question, version, screen, context = (body[k] for k in ('question', 'version', 'screen', 'context'))
    screens = {'home': 'supervisor', 'product': 'product', 'review': 'product',
               'history': 'knowledge', 'evidence': 'knowledge', 'investigation': 'quality'}
    if (not isinstance(screen, str) or screen not in screens or type(version) is not int or version < 0 or
            not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000):
        raise DomainError('INVALID_INPUT', '화면·데이터 버전·질문을 확인하세요.')
    exact_keys(context, (), ('configuration_id', 'node_id', 'kind', 'draft_id', 'draft_version', 'queries', 'before_version', 'receipt_id', 'search_query', 'origin', 'search_mode'))
    if context.get('search_mode', 'lexical') not in {'lexical', 'semantic'}:
        raise DomainError('INVALID_INPUT', '검색 방식을 확인하세요.')
    action = body.get('action', 'explain')
    if action not in {'explain', 'prepare_draft'} or (action == 'explain' and 'request_key' in body):
        raise DomainError('INVALID_INPUT', '지원하는 AI 업무 요청을 선택하세요.')
    role, delegation = screens[screen], None
    if context.get('receipt_id') or (screen == 'history' and context.get('draft_id')):
        role = 'product'
    with app.host.request(app.context) as request:
        if action == 'prepare_draft':
            return prepare_receipt_draft(app, request, body)
        if role == 'supervisor':
            delegation, error = route(app, request, question, version)
            if error is not None:
                return error
            role = delegation['role']
        result = run_screen(role, app, request, body)
        return attach_delegation(result, delegation)
