"""A-only editable RFQ documents, separate from immutable calculation results.

Each version claims one immutable chain slot with atomic publication. Concurrent
editors cannot overwrite a slot, and no mutable latest pointer or schema is needed.
"""
import json
import re

from ..model import ROOT, canonical, digest, instant
from ..service import DomainError
from .rfq_handoff import _saved, _rfq_record
from .rfq_handoff_document import sha256, write_once

STORAGE_ROOT=ROOT/'data/receipts/materials/rfq_documents'
MAX_TEXT_BYTES=256*1024
KIND='materials_rfq_document_v1'


def _integrity():
    raise DomainError('RFQ_DOCUMENT_INTEGRITY','보관한 RFQ 문서 버전과 원본 근거를 확인하세요.')


def _valid_id(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value)


def _text_bytes(text):
    if not isinstance(text,str) or not text.strip() or '\x00' in text:
        raise DomainError('INVALID_INPUT','RFQ 본문은 비어 있지 않은 텍스트여야 하며 NUL 문자를 사용할 수 없습니다.')
    try:
        raw=text.encode('utf-8')
    except UnicodeError:
        raise DomainError('INVALID_INPUT','RFQ 본문은 올바른 UTF-8 텍스트여야 합니다.') from None
    if len(raw)>MAX_TEXT_BYTES:
        raise DomainError('INPUT_TOO_LARGE','편집한 RFQ 본문은 UTF-8 기준 256 KiB 이하여야 합니다.')
    return raw


def original_document(rfq):
    """Stable version zero derived from the saved calculation, without writes."""
    return dict(document_id=digest([KIND,'original',rfq['source_kind'],rfq['review_id'],
                                   rfq['sha256'],rfq['saved_payload_sha256']]),
        version=0,text=rfq['text'],sha256=rfq['sha256'],base_rfq_sha256=rfq['sha256'],
        base_saved_payload_sha256=rfq['saved_payload_sha256'],base_document_id=None,
        edited_by=None,edited_at=None,source_kind=rfq['source_kind'],review_id=rfq['review_id'],
        origin=rfq['origin'],filename='RFQ-'+rfq['source_kind']+'-'+rfq['sha256'][:12]+'.txt',
        byte_length=len(rfq['text'].encode('utf-8')))


def metadata(document):
    return {key:value for key,value in document.items() if key!='text'}


class RFQDocument:
    @staticmethod
    def _root(session,source_kind,review_id):
        root=STORAGE_ROOT.resolve()
        if not root.is_relative_to((ROOT/'data/receipts').resolve()): _integrity()
        info=session._service.db.info
        store=digest([info.host,info.port,info.dbname,session._service.schema])
        binding=digest([source_kind,review_id])
        if (root/store).is_symlink() or (root/store/binding).is_symlink(): _integrity()
        return root/store/binding,store

    @staticmethod
    def _read(path,store,original,parent):
        try:
            if path.is_symlink(): raise ValueError()
            raw=path.read_bytes();record=json.loads(raw)
            if (not isinstance(record,dict) or set(record)!={'kind','store_id','document','request_key'}
                    or raw!=(canonical(record)+'\n').encode() or record['kind']!=KIND or record['store_id']!=store):
                raise ValueError()
            doc=record['document'];key=record['request_key']
            if (not isinstance(doc,dict) or set(doc)!=set(original)
                    or not isinstance(key,str) or not key.strip() or len(key)>200 or any(ord(c)<32 for c in key)
                    or type(doc['version']) is not int or doc['version']!=parent['version']+1
                    or path.name!=f"v{doc['version']:09d}.json" or doc['base_document_id']!=parent['document_id']
                    or not isinstance(doc['edited_by'],str) or not doc['edited_by']
                    or doc['document_id']!=digest([KIND,doc['source_kind'],doc['review_id'],doc['edited_by'],key])):
                raise ValueError()
            for field in ('source_kind','review_id','base_rfq_sha256','base_saved_payload_sha256','origin'):
                if doc[field]!=original[field]: raise ValueError()
            text=doc['text']
            if not isinstance(text,str) or not text.strip() or '\x00' in text: raise ValueError()
            encoded=text.encode('utf-8')
            if (not 0<len(encoded)<=MAX_TEXT_BYTES or sha256(encoded)!=doc['sha256']
                    or type(doc['byte_length']) is not int or doc['byte_length']!=len(encoded)
                    or doc['filename']!=f"RFQ-{doc['source_kind']}-v{doc['version']}-{doc['sha256'][:12]}.txt"):
                raise ValueError()
            instant(doc['edited_at'])
            return record
        except (OSError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):
            _integrity()

    @classmethod
    def _versions(cls,session,root,store,original):
        parent=original;records=[];identifiers={original['document_id']}
        for path in sorted(root.glob('v*.json')):
            session._budget.check()
            record=cls._read(path,store,original,parent)
            parent=record['document']
            if parent['document_id'] in identifiers: _integrity()
            identifiers.add(parent['document_id']);records.append(record)
        return records

    def resolve(self,session,source_kind,review_id,document_id=None,*,row=None):
        """Omitting a selection intentionally preserves legacy original-only mail."""
        session._service._require(session.context['actor'],'read')
        row=row or _saved(session._service,source_kind,review_id)
        rfq=_rfq_record(row,source_kind);original=original_document(rfq)
        if document_id is None or document_id==original['document_id']:
            return original,rfq
        if not _valid_id(document_id):
            raise DomainError('INVALID_INPUT','선택한 RFQ 문서 ID를 확인하세요.')
        root,store=self._root(session,source_kind,review_id)
        records=self._versions(session,root,store,original)
        selected=next((record['document'] for record in records if record['document']['document_id']==document_id),None)
        if not selected:
            raise DomainError('NOT_FOUND','이 자재 검토에 저장된 RFQ 문서가 아닙니다.')
        return selected,rfq

    def get(self,session,source_kind,review_id):
        session._service._require(session.context['actor'],'read')
        rfq=_rfq_record(_saved(session._service,source_kind,review_id),source_kind)
        original=original_document(rfq)
        root,store=self._root(session,source_kind,review_id)
        records=self._versions(session,root,store,original)
        return dict(document=records[-1]['document'] if records else original)

    def save(self,session,source_kind,review_id,base_document_id,text,request_key):
        for permission in ('read','draft'):
            session._service._require(session.context['actor'],permission)
        raw=_text_bytes(text)
        if (not _valid_id(base_document_id) or not isinstance(request_key,str) or not request_key.strip()
                or len(request_key)>200 or any(ord(c)<32 or ord(c)==127 for c in request_key)):
            raise DomainError('INVALID_INPUT','기준 RFQ 문서 ID와 200자 이하 재시도 키가 필요합니다.')
        rfq=_rfq_record(_saved(session._service,source_kind,review_id),source_kind)
        original=original_document(rfq)
        root,store=self._root(session,source_kind,review_id)
        records=self._versions(session,root,store,original)
        identifier=digest([KIND,source_kind,review_id,session.context['actor'],request_key])

        def replay(record):
            doc=record['document']
            if doc['document_id']!=identifier: return None
            if (record['request_key']!=request_key or doc['text']!=text or doc['base_document_id']!=base_document_id
                    or doc['edited_by']!=session.context['actor']):
                raise DomainError('REQUEST_KEY_REUSED','같은 저장 키에 다른 RFQ 수정 내용을 사용할 수 없습니다.')
            return dict(document=doc)

        for record in records:
            result=replay(record)
            if result is not None: return result
        latest=records[-1]['document'] if records else original
        if latest['document_id']!=base_document_id:
            raise DomainError('RFQ_DOCUMENT_CONFLICT','다른 담당자가 RFQ를 수정했습니다. 최신 저장본과 비교한 뒤 다시 편집하세요.')
        version=latest['version']+1
        doc=dict(original,document_id=identifier,version=version,text=text,sha256=sha256(raw),byte_length=len(raw),
            base_document_id=base_document_id,edited_by=session.context['actor'],edited_at=session._service.now(),
            filename=f'RFQ-{source_kind}-v{version}-{sha256(raw)[:12]}.txt')
        record=dict(kind=KIND,store_id=store,document=doc,request_key=request_key)
        path=root/f'v{version:09d}.json'
        session._budget.check()
        try:
            write_once(path,(canonical(record)+'\n').encode())
        except OSError:
            # Atomic publication may succeed before directory fsync reports an
            # error. Recover that exact slot instead of manufacturing version 2.
            if not path.is_file():
                raise DomainError('RFQ_DOCUMENT_STORAGE_FAILED','RFQ 수정본을 보관하지 못했습니다. 같은 내용과 키로 다시 저장하세요.') from None
        winner=self._read(path,store,original,latest)
        result=replay(winner)
        if result is not None: return result
        raise DomainError('RFQ_DOCUMENT_CONFLICT','다른 담당자가 먼저 RFQ를 저장했습니다. 작성한 내용을 유지하고 최신본과 비교하세요.')

    def dispatch(self,session,action,body):
        method=self.get if action=='rfq-document-get' else self.save
        return session._service._envelope(ok=True,data=method(session,**body))
