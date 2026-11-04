"""Common-screen adapter for A's existing original-to-material-review workflow.

This owns no calculation, identity or approval rules. Contract reading checks
remain A's preparatory records; they are not approval of graph/production facts.
Construct once in trusted host startup, with the same database and identity
provider as the common screen. No seed or cross-schema data copy is performed.
"""
import base64
import binascii

from ..responses import exact_keys
from ..service import DomainError
from .contract_library import ContractLibrary
from .library_review import LibraryReview, REGISTRATION
from .library_product_review import LibraryProductReview
from .library_materials import LibraryMaterials


FIELDS = {
    'search': ('query', 'offset'),
    'get': ('contract_id',),
    'register': ('title', 'counterparty', 'filename', 'content_base64', 'request_key'),
    'interpret': ('contract_id', 'request_key'),
    'review': ('contract_id', 'interpretation_id', 'result_sha256', 'decisions', 'note', 'request_key'),
    'planning': ('contract_id',),
    'connect': ('contract_id', 'interpretation_id', 'review_id', 'data_version',
                'item_index', 'configuration_id', 'request_key'),
    'materials': ('contract_id', 'item_index'),
    'materials-calculate': ('contract_id', 'item_index', 'link_id', 'quantity', 'reason', 'request_key'),
}


def _text_original(filename, encoded):
    if isinstance(filename, str) and filename.lower().endswith('.pdf'):
        raise DomainError('A_PDF_SANDBOX_REQUIRED', '공통 화면의 A 계약 PDF 해석은 격리 파서 연결이 필요합니다. 현재는 UTF-8 TXT 원본을 접수하세요.')
    if not isinstance(filename, str) or not filename.lower().endswith('.txt'):
        raise DomainError('UNSUPPORTED_CONTRACT_FORMAT', '공통 화면의 계약 해석은 UTF-8 TXT만 지원합니다.')
    if not isinstance(encoded, str) or len(encoded) > 800000:
        raise DomainError('INPUT_TOO_LARGE', '600 KB 이하의 UTF-8 TXT를 선택하세요.')
    try:
        raw = base64.b64decode(encoded, validate=True)
        raw.decode('utf-8-sig')
    except (ValueError, binascii.Error, UnicodeError):
        raise DomainError('UNSUPPORTED_CONTRACT_FORMAT', '올바른 base64 형식의 UTF-8 TXT 원본이 필요합니다.') from None
    if raw.lstrip().startswith(b'%PDF-'):
        raise DomainError('A_PDF_SANDBOX_REQUIRED', 'PDF 파일의 확장자를 TXT로 바꿔 해석할 수 없습니다. 격리 파서 연결이 필요합니다.')



class MaterialsWorkspace:
    def __init__(self, host, setup_context, *, provider_factory=None, model=None):
        self.host = host
        self.library = ContractLibrary(host, setup_context)
        factory = None if provider_factory is None else lambda: provider_factory(REGISTRATION.allowed_tools)
        self.review = LibraryReview(self.library, setup_context, factory, model)
        self.planning = LibraryProductReview(self.review, setup_context)
        self.materials = LibraryMaterials(self.planning, setup_context)

    def dispatch(self, context, action, body):
        if action not in FIELDS:
            raise DomainError('ROUTE_NOT_FOUND', '지원하지 않는 생산·자재 작업입니다.')
        exact_keys(body, FIELDS[action])
        # All nested Library requests retain this actor and common request
        # budget. A database query is not a new independently funded request.
        with self.host.request(context) as session:
            session._budget.consume('tool')
            if action == 'search':
                result = self.library.search(context, **body)
            elif action == 'get':
                result = self.library.get(context, **body)
                if result['ok']:
                    reviewed = self.review.latest(context, body['contract_id'])
                    if reviewed['ok']:
                        result['data']['review'] = reviewed['data']
                    else:
                        result = reviewed
            elif action == 'register':
                # Source receipt never implicitly starts a paid model call.
                _text_original(body['filename'], body['content_base64'])
                result = self.library.register(context, **body)
            elif action == 'interpret':
                original = self.library.get(context, body['contract_id'])
                if not original['ok']:
                    session._budget.check()
                    return original
                _text_original(original['data']['filename'], original['data']['content_base64'])
                result = self.review.interpret(context, **body)
            elif action == 'review':
                result = self.review.confirm(context, scopes=[], **body)
            elif action == 'planning':
                result = self.planning.inspect(context, **body)
            elif action == 'connect':
                result = self.planning.connect(context, **body)
            elif action == 'materials':
                result = self.materials.get(context, **body)
            else:
                result = self.materials.calculate(context, **body)
            session._budget.check()
            return result
