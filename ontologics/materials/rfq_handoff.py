"""Trusted A host adapter: saved RFQ -> durable manual receipt -> common draft."""
import base64
import binascii
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from ..model import ROOT, canonical, digest, instant
from ..service import DomainError
from ..responses import failure
from .rfq_handoff_document import (
    KIND, build_nodes, description, preserve_artifacts, read_record, sha256, write_once,
)

STORAGE_ROOT = ROOT/'data/receipts/materials/rfq_handoffs'
TABLES = {'workspace': 'pp_workspace_material_reviews', 'request': 'pp_request_reviews'}


def _input_error(message):
    raise DomainError('INVALID_INPUT', message)


def _binding(source_kind, review_id):
    if not isinstance(source_kind, str) or source_kind not in TABLES:
        _input_error('RFQ 출처는 workspace 또는 request여야 합니다.')
    if not isinstance(review_id, str) or not 1 <= len(review_id) <= 200:
        _input_error('저장된 RFQ 검토 ID가 필요합니다.')


def _validate(p, created_at, now):
    if (not isinstance(p['recipient'], str) or len(p['recipient']) > 254
            or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", p['recipient'])):
        _input_error('수신자는 줄바꿈 없는 이메일 주소 한 개여야 합니다.')
    for key, maximum in (('subject', 300), ('message', 8000)):
        value = p[key]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
            _input_error('제목은 300자, 본문은 8,000자 이내로 입력하세요.')
    if any(c in p['subject'] for c in '\r\n'):
        _input_error('제목에 줄바꿈을 사용할 수 없습니다.')
    if p['confirmed'] is not True or p['evidence_origin'] not in ('real', 'synthetic'):
        _input_error('수동 인계 확인과 실제/가상 증빙 구분이 필요합니다.')
    if not isinstance(p['request_key'], str) or not p['request_key'].strip() or len(p['request_key']) > 200:
        _input_error('200자 이내의 고정 재시도 키가 필요합니다.')
    try:
        sent = instant(p['sent_at'])
        if not instant(created_at) <= sent <= instant(now):
            raise ValueError('range')
    except (TypeError, ValueError):
        _input_error('발송 시각은 시간대를 포함하며 RFQ 검토 이후, 현재 이전이어야 합니다.')
    name = p['evidence_filename']
    if (not isinstance(name, str) or not 1 <= len(name) <= 200 or name != Path(name).name
            or any(c in name for c in '\r\n\x00\\') or Path(name).suffix.lower() not in ('.eml', '.txt')):
        _input_error('증빙은 .eml 또는 .txt 파일이어야 합니다.')
    encoded = p['evidence_base64']
    if not isinstance(encoded, str) or len(encoded) > 4*((256*1024+2)//3):
        _input_error('증빙 파일은 256 KiB 이하여야 합니다.')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        _input_error('증빙 파일의 base64 형식이 올바르지 않습니다.')
    if not raw or len(raw) > 256*1024:
        _input_error('비어 있지 않은 256 KiB 이하 증빙 파일이 필요합니다.')
    return raw


def _saved(svc, source_kind, review_id):
    _binding(source_kind, review_id)
    row = svc.db.execute('SELECT * FROM '+TABLES[source_kind]+' WHERE id=%s', (review_id,)).fetchone()
    if not row:
        raise DomainError('NOT_FOUND', '저장된 RFQ 검토를 찾을 수 없습니다.')
    p = row['payload']
    if source_kind == 'workspace':
        valid = digest(p) == row['content_hash'] and row['configuration_id'] == p.get('configuration_id')
    else:
        valid = (row['basis_hash'] == p.get('basis_hash') and row['source_store'] == p.get('source_store')
                 and row['evidence_id'] == p.get('evidence_id')
                 and row['id'] == digest([row['source_store'], row['evidence_id'], row['basis_hash']]))
    if not valid:
        raise DomainError('RFQ_INTEGRITY', '저장된 RFQ 검토의 무결성을 확인하세요.')
    rfq = p.get('rfq', {})
    if not isinstance(rfq.get('text'), str) or not rfq['text'].strip():
        raise DomainError('RFQ_INTEGRITY', '저장된 RFQ 원문이 없습니다.')
    try:
        lines = rfq.get('lines')
        positive = isinstance(lines, list) and bool(lines) and all(
            not isinstance(r.get('shortage'), bool) and Decimal(str(r.get('shortage'))).is_finite()
            and Decimal(str(r['shortage'])) > 0 for r in lines)
    except (InvalidOperation, TypeError, KeyError, AttributeError):
        positive = False
    if not positive:
        raise DomainError('NO_CONFIRMED_SHORTAGE', '확인된 부족량이 있는 RFQ만 인계할 수 있습니다.')
    return row


def _rfq_record(row, source_kind):
    p = row['payload']
    request = p.get('request', {})
    # Calculations can combine real designs and synthetic stock. Their output
    # remains synthetic if any operational basis is synthetic or unspecified.
    scopes = [x for x in p.get('stock_scopes', {}).values() if x]
    origin = 'real' if scopes and all(x.get('origin') == 'real' for x in scopes) else 'synthetic'
    if request.get('origin') == 'synthetic':
        origin = 'synthetic'
    references = {key:p[key] for key in ('contract_id', 'item_index', 'link_id', 'interpretation_id',
                  'evidence_id', 'source_hash', 'source_store') if key in p}
    if request:
        references['request'] = request
    return dict(source_kind=source_kind, review_id=row['id'], sha256=sha256(p['rfq']['text'].encode()),
        text=p['rfq']['text'], origin=origin, saved_payload_sha256=digest(p), basis_hash=p['basis_hash'],
        reviewed_at=row['created_at'], reviewed_by=row['actor'], data_version=p['data_version'],
        configuration_id=p.get('configuration_id', request.get('configuration_id')),
        references=references)


class RFQHandoff:
    def __init__(self, host):
        self.host = host

    @staticmethod
    def _root(session):
        root = STORAGE_ROOT.resolve()
        if not root.is_relative_to((ROOT/'data/receipts').resolve()):
            raise DomainError('RFQ_INTEGRITY', '인계 문서는 data/receipts 내부에 보존해야 합니다.')
        info = session._service.db.info
        store = digest([info.host, info.port, info.dbname, session._service.schema])
        if (root/store).is_symlink():
            raise DomainError('RFQ_INTEGRITY', '인계 저장소 경로를 확인하세요.')
        return root/store, store

    @staticmethod
    def _base_item(session, path, record):
        p, identifier = record['input'], record['receipt_id']
        return dict(description(record), source_kind=p['source_kind'], review_id=p['review_id'],
            automated=bool(record.get('automation')),
            request_key=p['request_key'], graph_status='file_saved', draft=None,
            can_retry=(session.context['actor'] == record['reported_by']
                       and 'draft' in session.context['permissions']),
            rfq_sha256=record['rfq']['sha256'], rfq_path=str((path.parent/'rfq.txt').relative_to(ROOT)),
            evidence_path=str((path.parent/('evidence'+Path(p['evidence_filename']).suffix.lower())).relative_to(ROOT)),
            receipt_path=str(path.relative_to(ROOT)), source_id=f'source:materials:rfq-handoff:{identifier}:receipt',
            evidence_id=f'evidence:materials:rfq-handoff:{identifier}:receipt')

    @classmethod
    def _item(cls, session, path, record):
        item = cls._base_item(session, path, record)
        ref_path = path.parent/'draft.json'
        try:
            nodes = build_nodes(path, record)
        except OSError:
            item['graph_errors'] = [dict(code='HANDOFF_FILE_SAVE_FAILED', message='보존 문서에서 파일을 복구해야 합니다.')]
            if ref_path.exists():
                item.update(graph_status='lookup_failed', can_retry=False)
            return item
        if not ref_path.exists():
            return item
        if ref_path.is_symlink():
            raise DomainError('RFQ_INTEGRITY', '초안 참조 경로를 확인하세요.')
        try:
            ref = json.loads(ref_path.read_text())
            if set(ref) != {'draft_id', 'version'} or not isinstance(ref['draft_id'], str) or type(ref['version']) is not int:
                raise ValueError('invalid draft reference')
        except (OSError, ValueError, TypeError) as exc:
            raise DomainError('RFQ_INTEGRITY', '보존된 공통 초안 참조를 확인하세요.') from exc
        item.update(can_retry=False, draft=dict(draft_id=ref['draft_id'], draft_version=ref['version']))
        try:
            result = session.call('get_draft', ref)
        except DomainError as exc:
            result = failure(exc.code, exc.message)
        if not result['ok']:
            item.update(graph_status='lookup_failed', graph_errors=result['errors'])
            return item
        draft = result['data']
        operations = [{'op':'put', 'node':n} for n in nodes]
        if draft['operations'] != operations:
            raise DomainError('RFQ_INTEGRITY', '공통 초안과 보존된 인계 문서가 다릅니다.')
        if draft['status'] == 'superseded':
            # A human may rebase the same immutable document through common
            # draft tools. Discover its exact version, then use the common read
            # boundary; never follow a revision that changes this handoff.
            latest = session._service.db.execute('SELECT MAX(version) FROM drafts WHERE id=%s',
                (draft['id'],)).fetchone()['max']
            if latest and latest > draft['version']:
                try:
                    newer = session.call('get_draft', dict(draft_id=draft['id'], version=latest))
                except DomainError as exc:
                    newer = failure(exc.code, exc.message)
                if not newer['ok']:
                    item.update(graph_status='lookup_failed', graph_errors=newer['errors'])
                    return item
                if newer['data']['operations'] == operations:
                    draft = newer['data']
                else:
                    item['graph_errors'] = [dict(code='HANDOFF_DRAFT_CHANGED',
                        message='새 공통 초안의 내용이 보존된 인계 기록과 달라 연결하지 않았습니다.')]
        item['draft'] = dict(draft, draft_id=draft['id'], draft_version=draft['version'])
        item['graph_status'] = {'awaiting_review':'pending_review', 'needs_changes':'changes_requested',
            'rejected':'rejected', 'superseded':'changes_requested', 'approved':'approved_pending_apply',
            'applied':'approved_pending_apply'}.get(draft['status'], 'lookup_failed')
        if draft['status'] == 'applied':
            try:
                result = session.call('lookup', {'version':draft['applied_version']})
            except DomainError as exc:
                result = failure(exc.code, exc.message)
            if not result['ok']:
                item.update(graph_status='lookup_failed', graph_errors=result['errors'])
            else:
                index = {n['id']:n for n in result['data']}
                item['graph_status'] = 'applied' if all(index.get(n['id']) == n for n in nodes) else 'lookup_failed'
        return item

    @classmethod
    def _submit(cls, session, path, record):
        draft = None
        try:
            preserve_artifacts(path, record)
            nodes = build_nodes(path, record)
            draft = session.call('prepare_draft', {'operations':[{'op':'put', 'node':n} for n in nodes],
                'request_key':'materials-rfq-handoff:'+record['receipt_id']})
            if draft['ok']:
                ref = dict(draft_id=draft['data']['draft_id'], version=draft['data']['draft_version'])
                if write_once(path.parent/'draft.json', (canonical(ref)+'\n').encode()) != (canonical(ref)+'\n').encode():
                    raise DomainError('RFQ_INTEGRITY', '보존된 초안 참조가 다릅니다.')
            result = dict(draft)
            if draft['ok'] and not draft['data']['validation']['valid']:
                result.update(ok=False, errors=[dict(code='VALIDATION_FAILED', message='공통 초안 검증에 실패했습니다.')])
        except DomainError as exc:
            result = failure(exc.code, exc.message)
        except OSError:
            result = failure('HANDOFF_FILE_SAVE_FAILED', '인계 원문은 보존됐습니다. 같은 기록으로 파일·초안 저장을 재시도하세요.')
        result['data'] = dict(item=cls._item(session, path, record), draft=draft)
        return result

    def mail_item(self, session, mail_id):
        root, store = self._root(session)
        identifier = digest([mail_id, 'service-mail-submission'])
        path = root/identifier/'receipt.json'
        if path.parent.is_symlink():
            raise DomainError('RFQ_INTEGRITY', '인계 문서 경로를 확인하세요.')
        if not path.exists():
            return None
        record = read_record(path)
        if record['store_id'] != store or record.get('automation', {}).get('mail_draft_id') != mail_id:
            raise DomainError('RFQ_INTEGRITY', '메일과 보관한 인계 기록이 다릅니다.')
        return self._item(session, path, record)

    def automated(self, session, *, mail, attempt, outcome):
        """Trusted server-created submission receipt; never a fabricated user report."""
        if (mail['owner'] != session.context['actor']
                or outcome['status'] not in ('local_captured', 'smtp_accepted')
                or outcome['draft_sha256'] != digest(mail) or outcome['attempt_sha256'] != digest(attempt)):
            raise DomainError('RFQ_INTEGRITY', '보관된 메일 접수 결과를 확인하세요.')
        session._service._require(session.context['actor'], 'draft')
        root, store = self._root(session)
        if mail['store_id'] != store:
            raise DomainError('RFQ_INTEGRITY', '메일과 인계 기록 저장소가 다릅니다.')
        identifier = digest([mail['draft_id'], 'service-mail-submission'])
        path = root/identifier/'receipt.json'
        if path.parent.is_symlink():
            raise DomainError('RFQ_INTEGRITY', '인계 문서 경로를 확인하세요.')
        origin = 'real' if outcome['status'] == 'smtp_accepted' else 'synthetic'
        p = dict(source_kind=mail['input']['source_kind'], review_id=mail['input']['review_id'],
            recipient=mail['recipient'], subject=mail['input']['subject'], message=mail['input']['message'],
            sent_at=outcome['accepted_at'], evidence_filename='message.eml', evidence_base64=mail['mail_base64'],
            evidence_origin=origin, confirmed=True, request_key='service-mail:'+mail['draft_id'])
        automation = dict(mail_draft_id=mail['draft_id'], status=outcome['status'], transport=mail['transport'],
            sender=mail['sender'], message_id=mail['message_id'], prepared_at=mail['prepared_at'],
            attempted_at=attempt['attempted_at'], accepted_at=outcome['accepted_at'])
        record = dict(kind=KIND, receipt_id=identifier, store_id=store, input=p,
            reported_by=mail['owner'], reported_at=outcome['accepted_at'], evidence_sha256=mail['mail_sha256'],
            rfq=mail['rfq'], automation=automation)
        raw = (canonical(record)+'\n').encode()
        if write_once(path, raw) != raw:
            raise DomainError('RFQ_INTEGRITY', '보관한 자동 메일 접수 기록이 다릅니다.')
        return self._submit(session, path, read_record(path))

    def prepare(self, context, source_kind, review_id, recipient, subject, message, sent_at,
                evidence_filename, evidence_base64, evidence_origin, confirmed, request_key):
        p = dict(source_kind=source_kind, review_id=review_id, recipient=recipient, subject=subject,
            message=message, sent_at=sent_at, evidence_filename=evidence_filename,
            evidence_base64=evidence_base64, evidence_origin=evidence_origin, confirmed=confirmed,
            request_key=request_key)
        with self.host.request(context) as s:
            svc = s._service
            for permission in ('read', 'draft'):
                svc._require(s.context['actor'], permission)
            row = _saved(svc, source_kind, review_id)
            raw = _validate(p, row['created_at'], svc.now())
            root, store = self._root(s)
            identifier = digest([s.context['actor'], request_key])
            path = root/identifier/'receipt.json'
            if path.parent.is_symlink():
                raise DomainError('RFQ_INTEGRITY', '인계 문서 경로를 확인하세요.')
            record = dict(kind=KIND, receipt_id=identifier, store_id=store, input=p,
                reported_by=s.context['actor'], reported_at=svc.now(), evidence_sha256=sha256(raw),
                rfq=_rfq_record(row, source_kind))
            try:
                write_once(path, (canonical(record)+'\n').encode())
            except OSError:
                if not path.is_file():
                    return failure('HANDOFF_FILE_SAVE_FAILED', '인계 문서를 보존하지 못했습니다. 같은 내용과 키로 재시도하세요.')
                # Publication may have succeeded before a directory fsync failed.
                record = read_record(path)
                response = failure('HANDOFF_FILE_SAVE_FAILED', '인계 문서 저장 확인이 필요합니다. 같은 기록으로 재시도하세요.')
                response['data'] = dict(item=self._base_item(s,path,record), draft=None)
                return response
            record = read_record(path)
            if record['input'] != p or record['reported_by'] != s.context['actor'] or record['store_id'] != store:
                raise DomainError('REQUEST_KEY_REUSED', '같은 요청 키에 다른 인계 내용을 사용할 수 없습니다.')
            return self._submit(s, path, record)

    def retry(self, context, source_kind, review_id, receipt_id):
        _binding(source_kind, review_id)
        if not isinstance(receipt_id, str) or not re.fullmatch('[0-9a-f]{64}', receipt_id):
            _input_error('보존된 인계 기록 ID가 필요합니다.')
        with self.host.request(context) as s:
            for permission in ('read', 'draft'):
                s._service._require(s.context['actor'], permission)
            root, store = self._root(s)
            path = root/receipt_id/'receipt.json'
            if path.parent.is_symlink():
                raise DomainError('RFQ_INTEGRITY', '인계 문서 경로를 확인하세요.')
            if not path.is_file():
                raise DomainError('NOT_FOUND', '보존된 인계 기록이 없습니다.')
            record = read_record(path)
            if record['reported_by'] != s.context['actor']:
                raise DomainError('FORBIDDEN', '원래 인계 기록 작성자만 초안 저장을 재시도할 수 있습니다.')
            if record['store_id'] != store or record['input']['source_kind'] != source_kind or record['input']['review_id'] != review_id:
                raise DomainError('NOT_FOUND', '선택한 RFQ의 인계 기록이 아닙니다.')
            return self._submit(s, path, record)

    @classmethod
    def _records(cls, session):
        root, store = cls._root(session)
        records = []
        for path in root.glob('*/receipt.json'):
            session._budget.check()
            if path.parent.is_symlink():
                raise DomainError('RFQ_INTEGRITY', '인계 문서 경로를 확인하세요.')
            record = read_record(path)
            if record['store_id'] != store:
                raise DomainError('RFQ_INTEGRITY', '인계 문서의 저장소가 다릅니다.')
            records.append((path, record))
        return sorted(records, key=lambda row:(row[1]['reported_at'],row[1]['receipt_id']), reverse=True)

    def status(self, context, source_kind, review_id):
        _binding(source_kind, review_id)
        with self.host.request(context) as s:
            svc = s._service
            svc._require(s.context['actor'], 'read')
            items = [self._item(s, path, record) for path, record in self._records(s)
                     if record['input']['source_kind'] == source_kind and record['input']['review_id'] == review_id]
            return svc._envelope(ok=True, data=dict(items=items))

    def history(self, context, offset):
        if type(offset) is not int or not 0 <= offset <= 1000000:
            _input_error('조회 시작 위치는 0~1,000,000 사이 정수여야 합니다.')
        with self.host.request(context) as s:
            svc = s._service
            svc._require(s.context['actor'], 'read')
            records = self._records(s)
            items = [self._item(s,path,record) for path,record in records[offset:offset+10]]
            return svc._envelope(ok=True, data=dict(items=items,total=len(records),offset=offset,limit=10))
