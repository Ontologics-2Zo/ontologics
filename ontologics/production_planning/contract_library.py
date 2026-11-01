"""A-owned immutable original archive, separate from interpreted ontology facts."""
import base64
import binascii
import hashlib
import uuid
from ..service import DomainError

DDL='''CREATE TABLE IF NOT EXISTS pp_contract_originals(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, counterparty TEXT NOT NULL,
 filename TEXT NOT NULL, mime TEXT NOT NULL, content BYTEA NOT NULL,
 sha256 TEXT NOT NULL UNIQUE, byte_length INTEGER NOT NULL,
 received_at TEXT NOT NULL, received_by TEXT NOT NULL)'''
COLUMNS='id,title,counterparty,filename,mime,sha256,byte_length,received_at'


def bounded(value, maximum, required=True):
    if not isinstance(value,str) or len(value)>maximum or (required and not value.strip()) or '\x00' in value:
        raise DomainError('INVALID_INPUT','입력 길이와 필수 항목을 확인해 주세요.')
    return value.strip()


def metadata(row):
    return dict(row,status='received',interpretation_status='not_started',original_version=1)


class ContractLibrary:
    def __init__(self,host,setup_context):
        self.host=host
        # Schema initialization is host-owned; no Agent receives database access.
        with host.request(setup_context) as session:
            session._service.db.execute(DDL)

    def register(self,context,*,title,counterparty,filename,content_base64,request_key):
        title=bounded(title,200);counterparty=bounded(counterparty,200,False)
        filename=bounded(filename,180)
        if '/' in filename or '\\' in filename or any(ord(c)<32 for c in filename):
            raise DomainError('INVALID_INPUT','파일명에 경로나 제어 문자를 사용할 수 없습니다.')
        if not isinstance(content_base64,str) or len(content_base64)>800000:
            raise DomainError('INPUT_TOO_LARGE','600 KB 이하 파일을 선택해 주세요.')
        try:raw=base64.b64decode(content_base64,validate=True)
        except (ValueError,binascii.Error):raise DomainError('INVALID_INPUT','파일 인코딩이 올바르지 않습니다.') from None
        if not 0<len(raw)<=600000:raise DomainError('INPUT_TOO_LARGE','빈 파일 또는 600 KB를 넘는 파일은 등록할 수 없습니다.')
        if filename.lower().endswith('.txt'):
            try:raw.decode('utf-8-sig')
            except UnicodeError:raise DomainError('INVALID_INPUT','TXT 파일은 UTF-8이어야 합니다.') from None
            mime='text/plain'
        elif filename.lower().endswith('.pdf') and raw.startswith(b'%PDF-'):
            mime='application/pdf'
        else:raise DomainError('INVALID_INPUT','TXT 또는 PDF 계약서를 선택해 주세요.')
        sha=hashlib.sha256(raw).hexdigest()
        with self.host.request(context) as session:
            svc=session._service
            def save():
                row=svc.db.execute('SELECT '+COLUMNS+' FROM pp_contract_originals WHERE sha256=%s',(sha,)).fetchone()
                duplicate=row is not None
                if row is None:
                    cid='contract-'+str(uuid.uuid4())
                    svc.db.execute('INSERT INTO pp_contract_originals VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (cid,title,counterparty,filename,mime,raw,sha,len(raw),svc.now(),session.context['actor']))
                    row=svc.db.execute('SELECT '+COLUMNS+' FROM pp_contract_originals WHERE id=%s',(cid,)).fetchone()
                return svc._envelope(ok=True,data=dict(metadata(row),already_saved=duplicate))
            return svc._run(session.context['actor'],'contract_original_register',dict(title=title,counterparty=counterparty,
                filename=filename,sha256=sha),save,permission='draft',request_key=request_key)

    def search(self,context,query='',offset=0):
        query=bounded(query,200,False)
        if type(offset) is not int or not 0<=offset<=100000:raise DomainError('INVALID_INPUT','잘못된 목록 위치입니다.')
        pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        with self.host.request(context) as session:
            svc=session._service
            def read():
                where=' WHERE title ILIKE %s OR counterparty ILIKE %s OR filename ILIKE %s OR id ILIKE %s'
                params=(pattern,)*4
                total=svc.db.execute('SELECT count(*) AS n FROM pp_contract_originals'+where,params).fetchone()['n']
                rows=svc.db.execute('SELECT '+COLUMNS+' FROM pp_contract_originals'+where+' ORDER BY received_at DESC,id LIMIT 30 OFFSET %s',params+(offset,)).fetchall()
                return svc._envelope(ok=True,data=dict(items=[metadata(r) for r in rows],total=total,offset=offset,limit=30))
            return svc._run(session.context['actor'],'contract_original_search',dict(query=query,offset=offset),read)

    def get(self,context,contract_id):
        contract_id=bounded(contract_id,100)
        with self.host.request(context) as session:
            svc=session._service
            def read():
                row=svc.db.execute('SELECT '+COLUMNS+',content FROM pp_contract_originals WHERE id=%s',(contract_id,)).fetchone()
                if row is None:raise DomainError('NOT_FOUND','계약서를 찾을 수 없습니다.')
                raw=bytes(row.pop('content'))
                if len(raw)!=row['byte_length'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                    raise DomainError('SOURCE_HASH_MISMATCH','저장 원본의 무결성을 확인할 수 없습니다.')
                return svc._envelope(ok=True,data=dict(metadata(row),content_base64=base64.b64encode(raw).decode(),
                    text=raw.decode('utf-8-sig') if row['mime']=='text/plain' else None,integrity_verified=True))
            return svc._run(session.context['actor'],'contract_original_get',dict(contract_id=contract_id),read)
