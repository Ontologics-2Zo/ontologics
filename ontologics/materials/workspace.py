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
from .library_process_materials import LibraryProcessMaterials
from .library_process_rfq import LibraryProcessRFQ
from .request_workflow import RequestWorkflow
from .workspace_review import WorkspaceReview
from .rfq_handoff import RFQHandoff
from .rfq_mail import RFQMail
from .rfq_document import RFQDocument


FIELDS = {
    'rfq-document-get': ('source_kind','review_id'),
    'rfq-document-save': ('source_kind','review_id','base_document_id','text','request_key'),
    'mail-compose': ('source_kind','review_id'),
    'mail-save': ('source_kind','review_id','contact_id','subject','message','request_key'),
    'mail-send': ('draft_id',),
    'mail-history': ('offset','source_kind','review_id'),
    'mail-get': ('draft_id',),
    'mail-file': ('draft_id','kind'),
    'mail-graph-retry': ('draft_id',),
    'rfq-handoff-prepare': ('source_kind', 'review_id', 'recipient', 'subject', 'message', 'sent_at',
                            'evidence_filename', 'evidence_base64', 'evidence_origin', 'confirmed', 'request_key'),
    'rfq-handoff-status': ('source_kind', 'review_id'),
    'rfq-handoff-history': ('offset',),
    'rfq-handoff-retry': ('source_kind', 'review_id', 'receipt_id'),
    'product-inventory': ('quantity',),
    'process-requirement-preview': ('configuration_id', 'quantity', 'allowance_percent'),
    'combined-review': ('configuration_id', 'quantity', 'allowance_percent', 'request_key'),
    'contract-combined-review': ('contract_id', 'item_index', 'link_id', 'quantity', 'reason', 'allowance_percent', 'request_key'),
    'request-inbox': (),
    'request-seen': ('evidence_id',),
    'request-review': ('evidence_id', 'data_version', 'allowance_percent', 'request_key'),
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
OPTIONAL_FIELDS = {'mail-compose': ('rfq_document_id',), 'mail-save': ('rfq_document_id',)}


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
    def __init__(self, host, setup_context, *, provider_factory=None, model=None, request_host=None):
        self.host = host
        self.library = ContractLibrary(host, setup_context)
        factory = None if provider_factory is None else lambda: provider_factory(REGISTRATION.allowed_tools)
        self.review = LibraryReview(self.library, setup_context, factory, model)
        self.planning = LibraryProductReview(self.review, setup_context)
        self.materials = LibraryMaterials(self.planning, setup_context)
        self.process_materials = LibraryProcessMaterials(host, setup_context)
        self.process_rfq = LibraryProcessRFQ(self.process_materials, setup_context)
        from .workspace_source import configured_request_host
        request_host = request_host or configured_request_host(host)
        self.requests = RequestWorkflow(self.materials, self.process_rfq, setup_context, request_host)
        self.combined = WorkspaceReview(self.materials, self.process_rfq, setup_context)
        self.handoffs = RFQHandoff(host)
        self.mail = RFQMail(host)
        self.rfq_documents = RFQDocument()

    def dispatch(self, context, action, body):
        if action not in FIELDS:
            raise DomainError('ROUTE_NOT_FOUND', '지원하지 않는 생산·자재 작업입니다.')
        exact_keys(body, FIELDS[action], OPTIONAL_FIELDS.get(action, ()))
        # All nested Library requests retain this actor and common request
        # budget. A database query is not a new independently funded request.
        with self.host.request(context) as session:
            session._budget.consume('tool')
            if action.startswith('rfq-document-'):
                result = self.rfq_documents.dispatch(session, action, body)
            elif action.startswith('mail-'):
                result = self.mail.dispatch(session, action, body)
            elif action == 'rfq-handoff-prepare':
                result = self.handoffs.prepare(context, **body)
            elif action == 'rfq-handoff-retry':
                result = self.handoffs.retry(context, **body)
            elif action == 'rfq-handoff-history':
                result = self.handoffs.history(context, **body)
            elif action == 'rfq-handoff-status':
                result = self.handoffs.status(context, **body)
            elif action == 'product-inventory':
                result = self.materials.inventory(context, **body)
            elif action == 'process-requirement-preview':
                result = self.process_rfq.preview(context, **body)
            elif action == 'combined-review':
                result = self.combined.review(context, **body)
            elif action == 'contract-combined-review':
                result = self.combined.contract_review(context, **body)
            elif action == 'request-inbox':
                result = self.requests.inbox(context)
            elif action == 'request-seen':
                result = self.requests.seen(context, **body)
            elif action == 'request-review':
                result = self.requests.review(context, **body)
            elif action == 'search':
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
                result = self.combined.contract_state(context, **body)
            else:
                result = self.materials.calculate(context, **body)
            session._budget.check()
            return result
