"""Human-entered business records become evidenced proposals, never approved facts.

The HTTP host chooses a trusted domain builder. Request input cannot choose code,
source paths, an actor, graph operations, or approval rights. Files use the existing
ignored intake directory; drafts, permissions and retries use the common service.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import domain_schema
from .model import ROOT, canonical, instant
from .receipts import source_io
from .service import DomainError


DOMAINS = {'claims': domain_schema.C_TYPES, 'fulfillment': domain_schema.B_TYPES}


@dataclass
class BusinessProposal:
    """Internal trusted-builder output, never accepted from an HTTP/Agent payload.

    Sources/evidence are committed with the domain records in the same draft.
    Attachments are exact server-read bytes keyed by SourceDocument ID.
    Existing builders may continue returning a plain list of bare records.
    """
    records: list
    sources: list = field(default_factory=list)
    attachments: dict = field(default_factory=dict)
    record_evidence: dict = field(default_factory=dict)


def preserve_proposal_sources(proposal, current):
    """Preserve immutable bytes; an existing source ID never changes content."""
    sources = []
    for original in proposal.sources:
        node = dict(original)
        if node.get('type') not in {'SourceDocument', 'Evidence'} or node.get('origin') != 'synthetic':
            raise DomainError('INVALID_BUSINESS_SOURCE', '가상 원본·근거만 추가할 수 있습니다.')
        previous = current.get(node['id'])
        if previous:
            # Collection time/path are fixed by the first approved registration.
            ignored = {'collected_at', 'path'} if node['type'] == 'SourceDocument' else set()
            if {k: v for k, v in node.items() if k not in ignored} != {k: v for k, v in previous.items() if k not in ignored}:
                raise DomainError('SOURCE_ID_CONFLICT', '기존 원본·근거 ID의 내용을 변경할 수 없습니다.')
            node = dict(previous)
        if node['type'] == 'SourceDocument':
            content = proposal.attachments.get(node['id'])
            if not isinstance(content, bytes) or len(content) > 2 * 1024 * 1024:
                raise DomainError('INVALID_BUSINESS_SOURCE', '원본 바이트와 크기를 확인하세요.')
            checksum = hashlib.sha256(content).hexdigest()
            if checksum != node.get('sha256'):
                raise DomainError('SOURCE_HASH_MISMATCH', '변환한 원본과 보존할 바이트가 다릅니다.')
            if previous:
                retained = (ROOT / previous['path']).resolve()
                if not retained.is_relative_to((ROOT / 'data').resolve()) or not retained.is_file():
                    raise DomainError('SOURCE_UNAVAILABLE', '기존 승인 원본을 찾을 수 없습니다.')
                if hashlib.sha256(retained.read_bytes()).hexdigest() != previous['sha256']:
                    raise DomainError('SOURCE_HASH_MISMATCH', '기존 승인 원본의 해시가 다릅니다.')
            suffix = Path(node['path']).suffix.lower()
            if suffix not in {'.eml', '.xlsx', '.csv', '.json'}:
                raise DomainError('INVALID_BUSINESS_SOURCE', '지원하지 않는 원본 형식입니다.')
            directory = (ROOT / 'data/receipts').resolve()
            if not directory.is_relative_to((ROOT / 'data').resolve()):
                raise DomainError('SOURCE_UNAVAILABLE', '원본 보존 경로를 확인하세요.')
            directory.mkdir(exist_ok=True)
            path = directory / ('business-file-' + checksum + suffix)
            with tempfile.NamedTemporaryFile(dir=directory) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                try:
                    os.link(temporary.name, path)
                except FileExistsError:
                    pass
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise DomainError('SOURCE_HASH_MISMATCH', '보존 원본 해시가 일치하지 않습니다.')
            # Previously approved sources keep their path, and must still verify.
            if not previous:
                node['path'] = 'data/receipts/' + path.name
        sources.append(node)
    if len({n['id'] for n in sources}) != len(sources):
        raise DomainError('INVALID_BUSINESS_SOURCE', '원본·근거 ID가 중복됩니다.')
    return sources


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
        built = builder(nodes, action, payload)
        proposal = built if isinstance(built, BusinessProposal) else BusinessProposal(built)
        records = proposal.records
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
        source_nodes = preserve_proposal_sources(proposal, current)
        known = {**current, **{n['id']: n for n in source_nodes}}
        for ids in proposal.record_evidence.values():
            if not isinstance(ids, list) or any(not isinstance(eid, str) or known.get(eid, {}).get('type') != 'Evidence' for eid in ids):
                raise DomainError('INVALID_BUSINESS_SOURCE', '업무 후보의 근거 참조를 확인하세요.')
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
        operations = [{'op': 'put', 'node': n} for n in [source, evidence, *source_nodes]]
        for row in records:
            record = {**row, 'origin': 'synthetic', 'evidence': sorted(set(
                current.get(row['id'], {}).get('evidence', []) + proposal.record_evidence.get(row['id'], []) + [evidence_id]))}
            operations.append({'op': 'put', 'node': record})
        result = service._save_draft(actor, operations, draft_id, expected_version)
        result['data']['registration'] = {'domain': domain, 'action': action,
            'record_ids': [row['id'] for row in records], 'source': source,
            'evidence_id': evidence_id,
            'original_sources': [n for n in source_nodes if n['type'] == 'SourceDocument'],
            'notice': '초안 저장입니다. 작성자와 다른 사람이 정확한 버전과 근거를 검토·승인한 뒤 반영합니다.'}
        return result

    return service._run(actor, domain + '_draft', arguments, source_io(act),
                        permission='draft', request_key=request_key)
