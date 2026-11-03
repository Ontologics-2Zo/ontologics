"""Trusted host helpers over existing common tools; no new gateway or DB writes."""
from __future__ import annotations
import os
import hashlib
from pathlib import Path
import tempfile

from ..model import ROOT, digest
from ..service import DomainError
from .material_request_document import (
    KIND, validate_request, make_record, render_document, read_record, build_nodes, decode_record,
)

STORAGE_ROOT = ROOT/'data/receipts/order_fulfillment/material_requests'


def _ok(result):
    if not result['ok']:
        error=result['errors'][0]
        raise DomainError(error['code'],error.get('message','공통 도구 실행 실패'))
    return result


def _write_once(path, text):
    """Publish a complete immutable artifact; no truncated file on concurrent retry."""
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as out:
        temp=Path(out.name)
        try:
            out.write(text.encode('utf-8'));out.flush();os.fsync(out.fileno())
            try: os.link(temp,path)
            except FileExistsError: pass
        finally: temp.unlink(missing_ok=True)


def prepare_request(session, payload, request_key):
    """Return a common draft and exact readback IDs, never A receipt/approval.

    References are explicitly pinned to the original read version, even if another
    transaction advances the store. Approval uses the common draft base checks.
    Retry the same key with the same payload; edited requests require a new key.
    """
    context=session.context
    if not {'read','draft'} <= set(context['permissions']):
        raise DomainError('FORBIDDEN','요청 등록에는 읽기·초안 권한이 필요합니다.')
    if not isinstance(request_key,str) or not 1<=len(request_key)<=200 or not request_key.strip():
        raise ValueError('재시도에 사용할 요청 키가 필요합니다.')
    root=STORAGE_ROOT.resolve()
    if not root.is_relative_to((ROOT/'data/receipts').resolve()):
        raise ValueError('원문 보존 위치는 data/receipts 내부여야 합니다.')
    info=session._service.db.info
    store_id=digest([info.host,info.port,info.dbname,session._service.schema])
    path=root/(digest([store_id,context['actor'],request_key])+'.md')
    if path.is_symlink(): raise ValueError('원문 경로에 심볼릭 링크를 사용할 수 없습니다.')
    if path.exists():
        record=read_record(path)
        if record['request']!=payload or record['provenance']['actor']!=context['actor']:
            raise DomainError('REQUEST_KEY_REUSED','같은 요청 키에 다른 내용을 사용할 수 없습니다.')
    else:
        snapshot=_ok(session.call('lookup',{}))
        validate_request(payload,snapshot['data'])
        record=make_record(payload,dict(actor=context['actor'],registered_at=session._service.now(),
                                        reference_data_version=snapshot['data_version'],store_id=store_id))
        _write_once(path,render_document(record))
        # Another same-key call may have published first. Always use those bytes.
        record=read_record(path)
        if record['request']!=payload or record['provenance']['actor']!=context['actor']:
            raise DomainError('REQUEST_KEY_REUSED','같은 요청 키에 다른 내용을 사용할 수 없습니다.')
    # Revalidate recorded references after a file-only failure, against that exact snapshot.
    pinned=_ok(session.call('lookup',{'version':record['provenance']['reference_data_version']}))
    validate_request(record['request'],pinned['data'])
    nodes=build_nodes(path,record)
    draft=_ok(session.call('prepare_draft',{'operations':[{'op':'put','node':n} for n in nodes],
                                          'request_key':request_key}))
    if not draft['data']['validation']['valid']:
        raise DomainError('VALIDATION_FAILED','요청 초안의 공통 검증에 실패했습니다.',draft['data']['validation'])
    return dict(draft=draft,source_id=nodes[0]['id'],evidence_id=nodes[1]['id'],
                source_path=nodes[0]['path'],reference_data_version=record['provenance']['reference_data_version'],
                state='draft_prepared',a_received=False)


def read_request(session, evidence_id, *, version):
    """Read exact registered evidence through the configured common backend.

    This returns document content, not a permission to act on it. No read/receipt
    business status is written. File integrity remains a common registration rule.
    """
    if type(version) is not int or version<0:
        raise ValueError('정확한 승인 데이터 버전이 필요합니다.')
    evidence=_ok(session.call('lookup',{'node_id':evidence_id,'version':version}))['data'][0]
    if evidence['type']!='Evidence': raise ValueError('요청 근거 ID가 필요합니다.')
    source=_ok(session.call('lookup',{'node_id':evidence['source_id'],'version':version}))['data'][0]
    if source['type']!='SourceDocument' or source.get('kind')!=KIND:
        raise ValueError('자재 검토 요청 문서가 아닙니다.')
    record=decode_record(evidence['description'])
    if (source['origin']!=record['request']['origin'] or evidence['origin']!=source['origin']
            or source['version']!=str(record['request']['revision'])):
        raise ValueError('요청 문서와 근거 메타데이터가 다릅니다.')
    if hashlib.sha256(render_document(record).encode('utf-8')).hexdigest()!=source['sha256']:
        raise ValueError('조회된 근거 내용과 원문 해시가 다릅니다.')
    return dict(record=record,source=source,evidence=evidence,data_version=version,a_received=False)
