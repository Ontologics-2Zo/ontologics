"""Immutable RFQ handoff artifacts; references are document metadata, not facts."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ..model import ROOT, canonical
from ..service import DomainError

KIND = 'materials_rfq_handoff'


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def write_once(path, raw):
    """Atomically publish complete bytes and preserve the first writer on retry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DomainError('RFQ_INTEGRITY', '보존 문서 경로에 심볼릭 링크를 사용할 수 없습니다.')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                pass
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temp.unlink(missing_ok=True)
    if path.is_symlink():
        raise DomainError('RFQ_INTEGRITY', '보존 문서 경로를 확인하세요.')
    return path.read_bytes()


def read_record(path):
    try:
        if path.is_symlink():
            raise ValueError('symbolic receipt')
        raw = path.read_bytes()
        record = json.loads(raw)
        if raw != (canonical(record) + '\n').encode():
            raise ValueError('noncanonical receipt')
        if record['kind'] != KIND or record['receipt_id'] != path.parent.name:
            raise ValueError('receipt identity')
        if sha256(record['rfq']['text'].encode()) != record['rfq']['sha256']:
            raise ValueError('RFQ digest')
        evidence = base64.b64decode(record['input']['evidence_base64'], validate=True)
        if sha256(evidence) != record['evidence_sha256']:
            raise ValueError('evidence digest')
        return record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DomainError('RFQ_INTEGRITY', '보존된 RFQ 인계 문서의 무결성을 확인하세요.') from exc


def artifact_bytes(record):
    return {'rfq.txt': record['rfq']['text'].encode(),
            'evidence' + Path(record['input']['evidence_filename']).suffix.lower():
                base64.b64decode(record['input']['evidence_base64'], validate=True)}


def preserve_artifacts(path, record):
    for name, raw in artifact_bytes(record).items():
        if write_once(path.parent/name, raw) != raw:
            raise DomainError('RFQ_INTEGRITY', '보존된 RFQ 또는 증빙 파일이 변경됐습니다.')


def description(record):
    p = record['input']
    automation = record.get('automation')
    result = dict(kind='service_mail_submission' if automation else 'user_reported_manual_handoff', receipt_id=record['receipt_id'],
        recipient=p['recipient'], subject=p['subject'], message=p['message'], sent_at=p['sent_at'],
        evidence_origin=p['evidence_origin'], evidence_filename=p['evidence_filename'],
        evidence_sha256=record['evidence_sha256'], reported_by=record['reported_by'],
        reported_at=record['reported_at'], confirmed=True, provider_verified=False,
        rfq={k:v for k,v in record['rfq'].items() if k != 'text'})
    if automation:
        result.update(automation, automated=True, provider_verified=automation['status']=='smtp_accepted',
                      external_delivery_confirmed=False)
    return result


def build_nodes(path, record):
    """Each evidence only cites its own source and retains that source's origin."""
    identifier = record['receipt_id']
    p = record['input']
    files = [('rfq', path.parent/'rfq.txt', record['rfq']['origin']),
             ('attachment', path.parent/('evidence'+Path(p['evidence_filename']).suffix.lower()), p['evidence_origin']),
             ('receipt', path, p['evidence_origin'])]
    nodes = []
    for label, file, origin in files:
        raw = file.read_bytes()
        if label != 'receipt' and raw != artifact_bytes(record)[file.name]:
            raise DomainError('RFQ_INTEGRITY', '보존된 RFQ 또는 증빙 파일이 변경됐습니다.')
        sid = f'source:materials:rfq-handoff:{identifier}:{label}'
        eid = f'evidence:materials:rfq-handoff:{identifier}:{label}'
        nodes.append(dict(id=sid, type='SourceDocument', origin=origin, evidence=[],
            kind=KIND+'_'+label, path=str(file.relative_to(ROOT)), url=file.as_uri(),
            sha256=sha256(raw), version='1', license='Internal service submission record; rights unverified' if record.get('automation') else 'User-provided internal record; rights unverified',
            collected_at=record['reported_at']))
        nodes.append(dict(id=eid, type='Evidence', origin=origin, evidence=[], source_id=sid,
            locator='whole document', description=canonical(description(record)) if label == 'receipt'
            else canonical(dict(kind=KIND+'_'+label, receipt_id=identifier, sha256=sha256(raw),
                                user_reported=not bool(record.get('automation')), provider_verified=False))))
    return nodes
