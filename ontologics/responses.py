"""Transport-neutral response and request-shape helpers (existing contracts)."""
from . import __version__
from .model import SCHEMA_VERSION
from .service import DomainError, RULE_VERSION

def failure(code, message, *, data=None):
    # HTTP/identity failure may precede a DB request; never invent a data version.
    return {'ok': False, 'data': data, 'evidence': [], 'data_version': None,
            'schema_version': SCHEMA_VERSION, 'rule_version': RULE_VERSION,
            'contract_version': __version__, 'warnings': [], 'conflicts': [],
            'errors': [{'code': code, 'message': message}], 'run_id': None}

def exact_keys(body, required, optional=()):
    if not isinstance(body, dict) or not set(required) <= set(body) or set(body) - set(required) - set(optional):
        raise DomainError('INVALID_INPUT', '요청 필드가 HTTP 계약과 일치하지 않습니다.')
