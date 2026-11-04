"""Human-entered business records become evidenced proposals, never approved facts.

The HTTP host chooses a trusted domain builder. Request input cannot choose code,
source paths, an actor, graph operations, or approval rights. Files use the existing
ignored intake directory; drafts, permissions and retries use the common service.
"""
from __future__ import annotations

import hashlib
import os
import tempfile

from . import domain_schema
from .model import ROOT, canonical, instant
from .receipts import source_io
from .service import DomainError


DOMAINS = {'claims': domain_schema.C_TYPES, 'fulfillment': domain_schema.B_TYPES}


def _text(value, maximum):
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum


def prepare_business_draft(service, actor, *, domain, builder, action, payload,
                           base_version, source_text, authored_at, notice,
                           synthetic_confirmed, request_key, draft_id=None,
                           expected_version=None):
    """Persist immutable submitted evidence and a version-pinned common draft.

    ``builder(nodes, action, payload)`` is trusted role code returning domain
    records without origin/evidence. It must not perform writes. The entire
    candidate, existing evidence, author and exact approval rules remain common.
    """
    if domain not in DOMAINS or not callable(builder):
        raise ValueError('Use a host-selected business domain and builder')
    arguments = dict(action=action, payload=payload, base_version=base_version,
        source_text=source_text, authored_at=authored_at, notice=notice,
        synthetic_confirmed=synthetic_confirmed, draft_id=draft_id,
        expected_version=expected_version)

    def act():
        if (not isinstance(base_version, int) or isinstance(base_version, bool)
                or base_version < 1 or not isinstance(payload, dict)
                or not _text(action, 60) or not _text(source_text, 50000)
                or not _text(notice, 1000) or synthetic_confirmed is not True):
            raise DomainError('INVALID_BUSINESS_INPUT',
                '기준 버전·업무 입력·원문·가상 자료 설명과 시연용 가상 기록 확인이 필요합니다.')
        try:
            instant(authored_at)
        except (ValueError, TypeError):
            raise DomainError('INVALID_TIME', '근거 작성 시각에 시간대를 포함하세요.') from None
        if base_version != service._version():
            raise DomainError('STALE_BASE', '승인 데이터가 변경됐습니다. 최신 기록을 확인하고 다시 작성하세요.')
        nodes = service._read_nodes(base_version)
        current = {node['id']: node for node in nodes}
        records = builder(nodes, action, payload)
        if (not isinstance(records, list) or not 1 <= len(records) <= 100
                or any(not isinstance(row, dict) or row.get('type') not in DOMAINS[domain]
                    or not _text(row.get('id'), 300) or {'origin', 'evidence'} & set(row)
                    for row in records)
                or len({row['id'] for row in records}) != len(records)):
            raise DomainError('INVALID_BUSINESS_RECORDS', '업무 후보의 형식과 중복 식별자를 확인하세요.')
        for row in records:
            previous = current.get(row['id'])
            if previous and (previous['type'] != row['type'] or previous['origin'] != 'synthetic'):
                raise DomainError('BUSINESS_ID_CONFLICT', '기존 개체의 종류·실제/가상 구분을 변경할 수 없습니다.')
        document = {'origin': 'synthetic', 'domain': domain, 'action': action,
            'authored_at': authored_at, 'notice': notice, 'source_text': source_text,
            'submitted_fields': payload}
        content = canonical(document).encode('utf-8')
        checksum = hashlib.sha256(content).hexdigest()
        directory = (ROOT / 'data/receipts').resolve()
        if not directory.is_relative_to((ROOT / 'data').resolve()):
            raise DomainError('SOURCE_UNAVAILABLE', '원본 보존 경로를 확인하세요.')
        directory.mkdir(exist_ok=True)
        path = directory / ('business-' + checksum + '.json')
        with tempfile.NamedTemporaryFile(dir=directory) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            try:
                os.link(temporary.name, path)
            except FileExistsError:
                pass
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise DomainError('SOURCE_HASH_MISMATCH', '접수 원본 해시가 일치하지 않습니다.')
        source_id = 'source:business:' + checksum
        evidence_id = 'evidence:business:' + checksum
        source = {'id': source_id, 'type': 'SourceDocument', 'origin': 'synthetic',
            'evidence': [], 'url': 'urn:ontologics:business:' + checksum,
            'path': 'data/receipts/' + path.name, 'sha256': checksum, 'version': checksum,
            'license': 'User-submitted project demonstration data',
            'collected_at': current.get(source_id, {}).get('collected_at', service.now()),
            'kind': 'synthetic_' + domain}
        evidence = {'id': evidence_id, 'type': 'Evidence', 'origin': 'synthetic',
            'evidence': [], 'source_id': source_id, 'locator': '/submitted_fields',
            'description': '사용자가 입력한 시연용 가상 업무 기록. 신고·계획은 실제 고장·생산·출하 확인이 아님. ' + notice}
        operations = [{'op': 'put', 'node': source}, {'op': 'put', 'node': evidence}]
        for row in records:
            record = {**row, 'origin': 'synthetic', 'evidence': sorted(set(
                current.get(row['id'], {}).get('evidence', []) + [evidence_id]))}
            operations.append({'op': 'put', 'node': record})
        result = service._save_draft(actor, operations, draft_id, expected_version)
        result['data']['registration'] = {'domain': domain, 'action': action,
            'record_ids': [row['id'] for row in records], 'source': source,
            'evidence_id': evidence_id,
            'notice': '초안 저장입니다. 작성자와 다른 사람이 정확한 버전과 근거를 검토·승인한 뒤 반영합니다.'}
        return result

    return service._run(actor, domain + '_draft', arguments, source_io(act),
                        permission='draft', request_key=request_key)
