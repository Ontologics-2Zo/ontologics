"""A-owned immutable mail drafts, local test mailbox and explicit SMTP submission.

The saved MIME is the submitted MIME. An immutable attempt claim prevents retry
after process death or an ambiguous SMTP response. Graph preparation is separate.
"""
import base64
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
import json
import os
import re
import secrets

from ..model import ROOT, canonical, digest, instant
from ..service import DomainError
from .rfq_handoff import RFQHandoff, _binding, _saved
from .rfq_handoff_document import sha256, write_once
from .rfq_document import RFQDocument, metadata as document_metadata, original_document
from .rfq_mail_transport import submit as smtp_submit

STORAGE_ROOT = ROOT/'data/receipts/materials/rfq_mail'
MAX_MAIL_BYTES = 4*1024*1024
CONTACTS = (
    dict(id='purchasing-main', name='구매팀 · 견적 접수', email='purchasing@example.test', origin='synthetic'),
    dict(id='purchasing-parts', name='구매팀 · 전자부품 담당', email='parts-buyer@example.test', origin='synthetic'),
    dict(id='purchasing-process', name='구매팀 · 공정자재 담당', email='process-buyer@example.test', origin='synthetic'),
)
LOCAL = dict(kind='local_capture',label='서비스 시험 메일함',external=False)
SMTP = dict(kind='smtp',label='회사 메일 서버',external=True)


def _invalid(message='메일 입력 내용을 확인하세요.'):
    raise DomainError('INVALID_INPUT',message)


def _address(value):
    return (isinstance(value,str) and len(value)<=254 and re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",value))


def _test_address(value):
    return value.rsplit('@',1)[-1].lower().endswith('.test')


def _text(value,maximum,*,header=False):
    return (isinstance(value,str) and bool(value.strip()) and len(value)<=maximum
            and '\x00' not in value and (not header or not any(ord(c)<32 or ord(c)==127 for c in value)))


def _settings():
    """Only trusted host environment can choose sender, recipients or SMTP server."""
    mode=os.environ.get('MATERIALS_MAIL_TRANSPORT','local_capture').strip() or 'local_capture'
    sender=dict(name=os.environ.get('MATERIALS_MAIL_FROM_NAME','생산·자재').strip() or '생산·자재',
                email=os.environ.get('MATERIALS_MAIL_FROM','materials@example.test').strip() or 'materials@example.test')
    try:
        if mode not in ('local_capture','smtp') or not _address(sender['email']) or not _text(sender['name'],100,header=True):
            raise ValueError()
        configured=os.environ.get('MATERIALS_MAIL_CONTACTS','').strip()
        contacts=json.loads(configured) if configured else [dict(row) for row in CONTACTS]
        if not isinstance(contacts,list) or not 1<=len(contacts)<=100:
            raise ValueError()
        seen=set()
        for row in contacts:
            if (not isinstance(row,dict) or set(row)!={'id','name','email','origin'}
                    or not _text(row['id'],100,header=True) or row['id'] in seen
                    or not _text(row['name'],150,header=True) or not _address(row['email'])
                    or row['origin'] not in ('real','synthetic')
                    or (_test_address(row['email']) and row['origin']!='synthetic')):
                raise ValueError()
            seen.add(row['id'])
            row['transport']=dict(SMTP if mode=='smtp' and not _test_address(row['email']) else LOCAL)
        config=None
        if mode=='smtp':
            host=os.environ.get('MATERIALS_SMTP_HOST','').strip()
            tls=os.environ.get('MATERIALS_SMTP_TLS','starttls').strip()
            port=int(os.environ.get('MATERIALS_SMTP_PORT','465' if tls=='ssl' else '587') or '587')
            username=os.environ.get('MATERIALS_SMTP_USERNAME','')
            password=os.environ.get('MATERIALS_SMTP_PASSWORD','')
            if (not host or len(host)>253 or any(c.isspace() or ord(c)<32 or ord(c)==127 or c in '/@\\' for c in host)
                    or tls not in ('ssl','starttls') or not 1<=port<=65535
                    or bool(username)!=bool(password)):
                raise ValueError()
            config=dict(host=host,port=port,tls=tls,username=username,password=password)
            if any(row['transport']['external'] for row in contacts) and _test_address(sender['email']):
                raise ValueError()
        return dict(sender=sender,contacts=contacts,config=config)
    except (ValueError,TypeError,KeyError):
        raise DomainError('MAIL_CONFIGURATION','관리자가 메일 발신자·구매팀 주소·SMTP 설정을 확인해야 합니다.') from None


def _configuration_hash(settings,contact):
    config=settings['config'] or {}
    # Credentials are never written. Password rotation does not retarget a draft.
    return digest(dict(sender=settings['sender'],contact=contact,
        server={key:config.get(key) for key in ('host','port','tls','username')} if contact['transport']['external'] else {}))


def _read(path):
    try:
        if path.is_symlink(): raise ValueError()
        raw=path.read_bytes()
        data=json.loads(raw)
        if not isinstance(data,dict) or raw!=(canonical(data)+'\n').encode(): raise ValueError()
        return data
    except (OSError,ValueError,TypeError,RecursionError):
        raise DomainError('MAIL_INTEGRITY','보관한 메일 기록의 무결성을 확인하세요.') from None


def _publish(path,data):
    raw=(canonical(data)+'\n').encode()
    return write_once(path,raw)==raw


class RFQMail:
    def __init__(self,host):
        self.host=host
        self.handoffs=RFQHandoff(host)
        self.documents=RFQDocument()

    @staticmethod
    def _root(session):
        root=STORAGE_ROOT.resolve()
        if not root.is_relative_to((ROOT/'data/receipts').resolve()):
            raise DomainError('MAIL_INTEGRITY','메일 기록은 data/receipts 내부에 보관해야 합니다.')
        info=session._service.db.info
        store=digest([info.host,info.port,info.dbname,session._service.schema])
        if (root/store).is_symlink(): raise DomainError('MAIL_INTEGRITY','메일 저장소 경로를 확인하세요.')
        return root/store,store

    @classmethod
    def _path(cls,session,draft_id):
        if not isinstance(draft_id,str) or not re.fullmatch('[0-9a-f]{64}',draft_id): _invalid('저장한 메일 ID가 필요합니다.')
        root,store=cls._root(session)
        path=root/draft_id/'draft.json'
        if path.parent.is_symlink(): raise DomainError('MAIL_INTEGRITY','메일 문서 경로를 확인하세요.')
        return path,store

    @staticmethod
    def _artifacts(path,record,*,create=False):
        try:
            mail=base64.b64decode(record['mail_base64'],validate=True)
            rfq=record['rfq']['text'].encode('utf-8')
            if sha256(mail)!=record['mail_sha256'] or sha256(rfq)!=record['rfq']['sha256']:
                raise ValueError()
            for name,raw in (('message.eml',mail),('rfq.txt',rfq)):
                file=path.with_name(name)
                if file.is_symlink(): raise ValueError()
                actual=write_once(file,raw) if create else file.read_bytes()
                if actual!=raw: raise ValueError()
            return mail,rfq
        except (OSError,ValueError,KeyError,TypeError):
            raise DomainError('MAIL_INTEGRITY','보관한 메일 또는 RFQ 원본을 확인하세요.') from None

    @classmethod
    def _load(cls,session,draft_id,*,owner_only=False,recover_artifacts=False):
        path,store=cls._path(session,draft_id)
        if not path.is_file(): raise DomainError('NOT_FOUND','저장한 메일을 찾을 수 없습니다.')
        record=_read(path)
        if record.get('kind')!='materials_rfq_mail_v1' or record.get('draft_id')!=draft_id or record.get('store_id')!=store:
            raise DomainError('MAIL_INTEGRITY','보관한 메일의 식별 정보를 확인하세요.')
        if record.get('owner')!=session.context['actor']:
            _,outcome=cls._outcome(path,record)
            if owner_only or not outcome or outcome['status'] not in ('local_captured','smtp_accepted'):
                raise DomainError('NOT_FOUND','저장한 메일을 찾을 수 없습니다.')
        cls._artifacts(path,record,create=recover_artifacts)
        return path,record

    @staticmethod
    def _source_title(session,row):
        p=row['payload']
        if p.get('contract_id'):
            original=session._service.db.execute('SELECT title FROM pp_contract_originals WHERE id=%s',(p['contract_id'],)).fetchone()
            if original: return f"{original['title']} · 품목 {p.get('item_index',0)+1}"
            return f"계약 {p['contract_id']} · 품목 {p.get('item_index',0)+1}"
        if p.get('request'):
            r=p['request']
            return f"추가 요청 {r.get('request_id',row['id'])} · 개정 {r.get('revision','확인 필요')}"
        return f"{p.get('configuration_id','제품별 자재 검토')} · {p.get('quantity','')}대"

    def _selected_rfq(self,session,row,source_kind,rfq_document_id):
        document,rfq=self.documents.resolve(session,source_kind,row['id'],rfq_document_id,row=row)
        # Calculation identity/hash remain explicit. The submitted RFQ text and
        # content hash refer to this selected immutable document version only.
        rfq=dict(rfq,original_sha256=rfq['sha256'],text=document['text'],sha256=document['sha256'],
                 document=document_metadata(document))
        return document,rfq

    def compose(self,session,source_kind,review_id,rfq_document_id=None):
        row=_saved(session._service,source_kind,review_id)
        selected,rfq=self._selected_rfq(session,row,source_kind,rfq_document_id)
        settings=_settings()
        title=self._source_title(session,row)
        p=row['payload']; doc=p['rfq']; quantity=p.get('quantity',p.get('request',{}).get('quantity'))
        suggested=dict(subject=f'[견적·조달 검토 요청] {title}'[:300],
            message=f"구매팀 담당자님,\n\n{title}의 자재 검토 결과를 전달드립니다.\n"
            f"검토 수량은 {quantity}대이며, 확인된 부족 자재 {len(doc['lines'])}종을 첨부 RFQ에 정리했습니다.\n"
            "견적과 조달 가능 여부, 예상 입고일을 확인해 주세요.\n"
            f"미확인 자재 {len(doc.get('excluded',[]))}종은 견적 수량에서 제외했으니 첨부 내역을 함께 확인해 주세요.\n\n"
            f"RFQ 검토 번호: {review_id}\n감사합니다.")
        if selected['version']>0:
            suggested['message']=(f"구매팀 담당자님,\n\n{title}의 RFQ 수정본 v{selected['version']}을 전달드립니다.\n"
                "첨부한 RFQ 문서의 품목·수량·요청 조건을 기준으로 견적과 조달 가능 여부, 예상 입고일을 확인해 주세요.\n"
                "이 문서는 계산 결과를 바탕으로 담당자가 수정하여 저장한 문서입니다.\n\n"
                f"RFQ 검토 번호: {review_id}\n감사합니다.")
        return dict(contacts=settings['contacts'],sender=settings['sender'],transport=settings['contacts'][0]['transport'],
            suggested=suggested,attachment=self._attachment(rfq),rfq_document=document_metadata(selected),
            source_title=title,**dict(source_kind=source_kind,review_id=review_id))

    @staticmethod
    def _attachment(rfq):
        document=rfq.get('document') or document_metadata(original_document(rfq))
        return dict(filename=document['filename'],sha256=rfq['sha256'],
                    byte_length=len(rfq['text'].encode('utf-8')),document_id=document['document_id'],version=document['version'])

    def save(self,session,source_kind,review_id,contact_id,subject,message,request_key,rfq_document_id=None):
        _binding(source_kind,review_id)
        if (not _text(subject,300,header=True) or not _text(message,8000)
                or not _text(request_key,200,header=True) or not _text(contact_id,100,header=True)):
            _invalid('구매팀 수신자, 300자 이하 제목, 8,000자 이하 본문과 재시도 키를 확인하세요.')
        p=dict(source_kind=source_kind,review_id=review_id,contact_id=contact_id,subject=subject,message=message,request_key=request_key)
        if rfq_document_id is not None:
            p['rfq_document_id']=rfq_document_id
        identifier=digest([session.context['actor'],request_key])
        path,store=self._path(session,identifier)
        if path.exists():
            path,record=self._load(session,identifier,owner_only=True,recover_artifacts=True)
            if record['input']!=p: raise DomainError('REQUEST_KEY_REUSED','같은 저장 키에 다른 메일 내용을 사용할 수 없습니다.')
            return dict(item=self._item(session,path,record))
        row=_saved(session._service,source_kind,review_id)
        settings=_settings()
        contact=next((r for r in settings['contacts'] if r['id']==contact_id),None)
        if not contact: _invalid('등록된 구매팀 담당자를 선택하세요.')
        _,rfq=self._selected_rfq(session,row,source_kind,rfq_document_id); attachment=self._attachment(rfq)
        if attachment['byte_length']>2*1024*1024: raise DomainError('INPUT_TOO_LARGE','RFQ 첨부는 2 MiB 이하여야 합니다.')
        prepared=session._service.now()
        mime=EmailMessage(policy=policy.SMTP)
        mime['From']=formataddr((settings['sender']['name'],settings['sender']['email']))
        mime['To']=formataddr((contact['name'],contact['email']))
        mime['Subject']=subject; mime['Date']=format_datetime(instant(prepared))
        mime['Message-ID']=f'<materials-rfq-{identifier}@{settings["sender"]["email"].rsplit("@",1)[1]}>'
        mime.set_content(message)
        mime.add_attachment(rfq['text'].encode('utf-8'),maintype='text',subtype='plain',filename=attachment['filename'])
        raw=mime.as_bytes(policy=policy.SMTP)
        if len(raw)>MAX_MAIL_BYTES: raise DomainError('INPUT_TOO_LARGE','메일과 첨부의 전체 크기를 줄여 주세요.')
        record=dict(kind='materials_rfq_mail_v1',draft_id=identifier,store_id=store,owner=session.context['actor'],
            input=p,recipient=contact['email'],contact=dict(contact),sender=settings['sender'],transport=contact['transport'],
            config_hash=_configuration_hash(settings,contact),source_title=self._source_title(session,row),prepared_at=prepared,
            message_id=str(mime['Message-ID']),mail_base64=base64.b64encode(raw).decode('ascii'),mail_sha256=sha256(raw),rfq=rfq)
        try:
            _publish(path,record)
            record=_read(path)
            if record['input']!=p or record['owner']!=session.context['actor'] or record['store_id']!=store:
                raise DomainError('REQUEST_KEY_REUSED','같은 저장 키에 다른 메일 내용을 사용할 수 없습니다.')
            self._artifacts(path,record,create=True)
        except OSError:
            raise DomainError('MAIL_STORAGE_FAILED','메일 초안을 보관하지 못했습니다. 같은 내용과 키로 다시 저장하세요.') from None
        return dict(item=self._item(session,path,record))

    @staticmethod
    def _outcome(path,record):
        attempt_path=path.with_name('attempt.json')
        if not attempt_path.exists(): return None,None
        attempt=_read(attempt_path)
        if attempt.get('draft_sha256')!=digest(record) or not isinstance(attempt.get('attempted_at'),str):
            raise DomainError('MAIL_INTEGRITY','보관한 메일 발송 시도와 초안이 다릅니다.')
        outcome_path=path.with_name('outcome.json')
        if not outcome_path.exists(): return attempt,dict(status='unknown',code='MAIL_OUTCOME_UNKNOWN')
        outcome=_read(outcome_path)
        if (outcome.get('attempt_sha256')!=digest(attempt) or outcome.get('draft_sha256')!=digest(record)
                or outcome.get('status') not in ('local_captured','smtp_accepted','rejected','unknown')):
            raise DomainError('MAIL_INTEGRITY','보관한 메일 발송 결과를 확인하세요.')
        if outcome['status'] in ('local_captured','smtp_accepted'):
            expected='smtp_accepted' if record['transport']['external'] else 'local_captured'
            try:
                if outcome['status']!=expected or instant(outcome['accepted_at'])<instant(attempt['attempted_at']): raise ValueError()
            except (ValueError,KeyError,TypeError):
                raise DomainError('MAIL_INTEGRITY','보관한 메일 접수 결과를 확인하세요.') from None
        return attempt,outcome

    def _handoff(self,session,record):
        return self.handoffs.mail_item(session,record['draft_id'])

    def _item(self,session,path,record):
        attempt,outcome=self._outcome(path,record)
        status=outcome['status'] if outcome else 'draft'
        handoff=self._handoff(session,record) if status in ('local_captured','smtp_accepted') else None
        graph_status=handoff['graph_status'] if handoff else 'file_saved' if status in ('local_captured','smtp_accepted') else 'not_created'
        owner=record['owner']; writable='draft' in session.context['permissions'] and owner==session.context['actor']
        return dict(draft_id=record['draft_id'],owner=owner,can_send=writable and status=='draft',
            can_retry_graph=writable and status in ('local_captured','smtp_accepted') and graph_status=='file_saved',
            contact_id=record['input']['contact_id'],recipient=record['recipient'],sender=record['sender'],transport=record['transport'],
            subject=record['input']['subject'],message=record['input']['message'],status=status,prepared_at=record['prepared_at'],
            attempted_at=attempt['attempted_at'] if attempt else None,sent_at=outcome.get('accepted_at') if outcome else None,
            attachment=self._attachment(record['rfq']),source_kind=record['input']['source_kind'],review_id=record['input']['review_id'],
            rfq_document=record['rfq'].get('document') or document_metadata(original_document(record['rfq'])),
            source_title=record['source_title'],graph_status=graph_status,handoff=handoff,message_available=True,
            external_delivery_confirmed=False,status_code=outcome.get('code') if outcome else None)

    def _register(self,session,record,attempt,outcome):
        return self.handoffs.automated(session,mail=record,attempt=attempt,outcome=outcome)

    def send(self,session,draft_id):
        path,record=self._load(session,draft_id,owner_only=True)
        if path.with_name('attempt.json').exists(): return dict(item=self._item(session,path,record))
        settings=_settings()
        contact=next((r for r in settings['contacts'] if r['id']==record['input']['contact_id']),None)
        if not contact or _configuration_hash(settings,contact)!=record['config_hash']:
            raise DomainError('MAIL_CONFIGURATION_CHANGED','메일 발신자·수신자 또는 전송 설정이 바뀌었습니다. 내용을 확인해 새 초안을 저장하세요.')
        if record['transport']['external'] and _test_address(record['recipient']):
            raise DomainError('MAIL_CONFIGURATION','시험 수신자는 외부 메일 서버로 보낼 수 없습니다.')
        session._budget.check()
        attempt=dict(draft_sha256=digest(record),attempt_id=secrets.token_hex(16),attempted_at=session._service.now())
        try:
            if not _publish(path.with_name('attempt.json'),attempt): return dict(item=self._item(session,path,record))
        except OSError:
            if path.with_name('attempt.json').exists(): return dict(item=self._item(session,path,record))
            raise DomainError('MAIL_STORAGE_FAILED','발송 시도를 기록하지 못해 메일을 보내지 않았습니다.') from None
        raw,_=self._artifacts(path,record)
        try:
            if record['transport']['external']:
                result=smtp_submit(session,settings['config'],sender=record['sender']['email'],recipient=record['recipient'],raw=raw)
            else:
                session._budget.consume('tool');session._budget.check()
                result=dict(status='local_captured',accepted_at=session._service.now())
        except DomainError:
            result=dict(status='rejected',code='MAIL_REQUEST_CANCELLED')
        except (OSError,ValueError):
            result=dict(status='unknown',code='MAIL_OUTCOME_UNKNOWN')
        outcome=dict(result,draft_sha256=digest(record),attempt_sha256=digest(attempt))
        try:
            if not _publish(path.with_name('outcome.json'),outcome):
                return dict(item=self._item(session,path,record))
        except OSError:
            return dict(item=self._item(session,path,record))
        if result['status'] in ('local_captured','smtp_accepted'):
            try: self._register(session,record,attempt,outcome)
            except (DomainError,OSError): pass  # Submission survives independent graph errors.
        return dict(item=self._item(session,path,record))

    def get(self,session,draft_id):
        path,record=self._load(session,draft_id)
        return dict(item=self._item(session,path,record))

    def history(self,session,offset,source_kind,review_id):
        if type(offset) is not int or not 0<=offset<=1000000: _invalid('목록 조회 위치를 확인하세요.')
        if source_kind!='' or review_id!='': _binding(source_kind,review_id)
        root,store=self._root(session);records=[]
        for path in root.glob('*/draft.json'):
            session._budget.check()
            if path.parent.is_symlink(): raise DomainError('MAIL_INTEGRITY','메일 저장소 경로를 확인하세요.')
            record=_read(path)
            if record.get('owner')!=session.context['actor']:
                _,outcome=self._outcome(path,record)
                if not outcome or outcome['status'] not in ('local_captured','smtp_accepted'): continue
            if record.get('store_id')!=store or record.get('draft_id')!=path.parent.name:
                raise DomainError('MAIL_INTEGRITY','메일 저장소 식별 정보를 확인하세요.')
            p=record['input']
            if source_kind and (p['source_kind']!=source_kind or p['review_id']!=review_id): continue
            records.append((path,record))
        records.sort(key=lambda row:(row[1]['prepared_at'],row[1]['draft_id']),reverse=True)
        items=[]
        for path,record in records[offset:offset+10]:
            self._artifacts(path,record)
            items.append(self._item(session,path,record))
        return dict(items=items,total=len(records),offset=offset,limit=10)

    def file(self,session,draft_id,kind):
        if kind not in ('rfq','mail'): _invalid('메일 또는 RFQ 원본을 선택하세요.')
        path,record=self._load(session,draft_id)
        mail,rfq=self._artifacts(path,record)
        return dict(filename=self._attachment(record['rfq'])['filename'] if kind=='rfq' else 'RFQ-mail-'+draft_id[:12]+'.eml',
                    mime='text/plain;charset=utf-8' if kind=='rfq' else 'message/rfc822',
                    content_base64=base64.b64encode(rfq if kind=='rfq' else mail).decode('ascii'))

    def graph_retry(self,session,draft_id):
        path,record=self._load(session,draft_id,owner_only=True)
        attempt,outcome=self._outcome(path,record)
        if not outcome or outcome['status'] not in ('local_captured','smtp_accepted'):
            raise DomainError('MAIL_NOT_SUBMITTED','접수된 메일 기록만 그래프에 등록할 수 있습니다.')
        self._register(session,record,attempt,outcome)
        return dict(item=self._item(session,path,record))

    def dispatch(self,session,action,body):
        session._service._require(session.context['actor'],'read')
        if action in ('mail-save','mail-send','mail-graph-retry'):
            session._service._require(session.context['actor'],'draft')
        method={'mail-compose':self.compose,'mail-save':self.save,'mail-send':self.send,'mail-get':self.get,
                'mail-history':self.history,'mail-file':self.file,'mail-graph-retry':self.graph_retry}[action]
        return session._service._envelope(ok=True,data=method(session,**body))
