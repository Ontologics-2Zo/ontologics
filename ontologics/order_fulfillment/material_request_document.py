"""Request document format, not a new ontology type or common validation rule.

All business references below are document content. A consumer must validate them
against the recorded snapshot before calculation; evidence is not an instruction.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from ..model import ROOT, canonical, digest, instant

FORMAT = 'materials-request-document-1'
KIND = 'materials_review_request'
REQUIRED = {'request_id', 'revision', 'configuration_id', 'baseline_evidence_id',
            'quantity', 'unit', 'need_date', 'need_basis', 'requester', 'requested_at',
            'reason', 'conditions_state', 'conditions_note', 'relation', 'origin'}
OPTIONAL = {'contract_item_id', 'delivery_round_id', 'destination', 'previous_evidence_id',
            'related_evidence_id'}
MARKER = '\n## 시스템 보존 기록\n\n```json\n'


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4000


def _validate_fields(payload):
    if not isinstance(payload, dict) or set(payload)-REQUIRED-OPTIONAL or REQUIRED-set(payload):
        raise ValueError('요청 필수 항목과 허용 항목을 확인하세요.')
    for key in set(payload)-{'quantity','revision'}:
        if not _text(payload[key]):
            raise ValueError('미확인 또는 비어 있는 항목: '+key)
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}',payload['request_id'])
            or any(type(payload[k]) is not int or payload[k] <= 0 for k in ('quantity','revision'))):
        raise ValueError('요청 번호와 양의 정수 수량·개정 번호가 필요합니다.')
    if payload['unit'] != 'piece' or payload['origin'] not in ('real','synthetic'):
        raise ValueError('완제품 단위와 실제/가상 구분을 확인하세요.')
    if payload['need_basis'] not in ('production_complete','dispatch','customer_arrival'):
        raise ValueError('필요 날짜 기준을 확인하세요.')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',payload['need_date']):
        raise ValueError('필요 날짜는 YYYY-MM-DD입니다. 임의 시간을 추가하지 않습니다.')
    date.fromisoformat(payload['need_date']); instant(payload['requested_at'])
    if payload['conditions_state'] not in ('unchanged','changed'):
        raise ValueError('조건 미확인은 보완 후 제출하세요.')
    if payload['need_basis']=='customer_arrival' and not payload.get('destination'):
        raise ValueError('고객 도착 기준은 도착지가 필요합니다.')


def validate_request(payload, nodes):
    """Validate a ready request against an explicitly pinned approved snapshot."""
    _validate_fields(payload)
    index={n['id']:n for n in nodes}
    def node(key,kind):
        found=index.get(payload[key])
        if not found or found['type']!=kind:
            raise ValueError('등록된 '+kind+' 참조가 아닙니다: '+key)
        if kind=='Evidence' and found.get('origin')!=payload['origin']:
            raise ValueError('기준·관련 근거의 실제/가상 구분이 요청과 다릅니다.')
        return found
    node('configuration_id','Configuration'); node('baseline_evidence_id','Evidence')
    if 'contract_item_id' in payload:
        item=node('contract_item_id','ContractItem')
        if item['configuration_id']!=payload['configuration_id']:
            raise ValueError('계약 품목과 요청 제품이 다릅니다.')
    if 'delivery_round_id' in payload:
        delivery=node('delivery_round_id','DeliveryRound')
        if 'contract_item_id' not in payload or delivery['contract_item_id']!=payload['contract_item_id']:
            raise ValueError('납품 차수의 계약 품목이 다릅니다.')
    relation=payload['relation']
    if relation not in ('additional','revision','independent'):
        raise ValueError('요청 관계를 확인하세요.')
    if relation=='revision':
        if 'previous_evidence_id' not in payload: raise ValueError('이전 요청 근거가 필요합니다.')
        previous=decode_record(node('previous_evidence_id','Evidence')['description'])['request']
        if (previous['request_id']!=payload['request_id'] or previous['revision']+1!=payload['revision']
                or previous['origin']!=payload['origin']):
            raise ValueError('같은 요청의 다음 개정이어야 합니다.')
    elif payload['revision']!=1 or 'previous_evidence_id' in payload:
        raise ValueError('수정이 아닌 요청은 새 번호의 개정 1로 제출하세요.')
    if relation=='independent' and 'related_evidence_id' not in payload:
        raise ValueError('독립 수요로 구분한 관련 요청의 근거가 필요합니다.')
    if 'related_evidence_id' in payload:
        related=decode_record(node('related_evidence_id','Evidence')['description'])['request']
        if related['request_id']==payload['request_id']:
            raise ValueError('독립 요청에는 다른 요청 번호가 필요합니다.')
    # Reject already published same/later revisions and branches of an old revision.
    for n in nodes:
        if n.get('type')!='Evidence': continue
        try: prior=decode_record(n.get('description',''))['request']
        except (ValueError,TypeError,KeyError): continue
        if prior['request_id']==payload['request_id'] and prior['revision']>=payload['revision']:
            raise ValueError('동일 또는 후속 개정이 이미 등록되어 있습니다. 재전송은 기존 요청 키를 사용하세요.')
    return payload


def make_record(payload, provenance):
    _validate_fields(payload)
    if set(provenance)!={'actor','registered_at','reference_data_version','store_id'}:
        raise ValueError('신뢰된 실행 주체·시간·기준 버전이 필요합니다.')
    if not _text(provenance['actor']) or not provenance['actor'].startswith('user:'):
        raise ValueError('사용자 실행 주체가 필요합니다.')
    if not isinstance(provenance['store_id'],str) or not re.fullmatch(r'[a-f0-9]{64}',provenance['store_id']):
        raise ValueError('조회 저장소 식별이 필요합니다.')
    instant(provenance['registered_at'])
    if type(provenance['reference_data_version']) is not int or provenance['reference_data_version']<0:
        raise ValueError('기준 데이터 버전이 필요합니다.')
    return json.loads(canonical(dict(format=FORMAT,request=payload,provenance=provenance)))


def decode_record(text):
    record=json.loads(text)
    if (not isinstance(record,dict) or set(record)!={'format','request','provenance'}
            or record['format']!=FORMAT or not isinstance(record['request'],dict)):
        raise ValueError('지원하는 요청 문서 기록이 아닙니다.')
    if make_record(record['request'],record['provenance'])!=record:
        raise ValueError('요청 기록이 일치하지 않습니다.')
    return record


def render_document(record):
    p=record['request']; provenance=record['provenance']
    basis={'production_complete':'생산 완료','dispatch':'출고','customer_arrival':'고객 도착'}
    relation={'additional':'추가 생산 검토','revision':'기존 요청 수정 (기존 수량 대체)',
              'independent':'독립 수요 추가 생산 검토'}
    def cell(value): return str(value).replace('|','\\|').replace('\n','<br>')
    rows=[('요청 번호 / 개정',f"{p['request_id']} / {p['revision']}"),
          ('요청 구분',relation[p['relation']]),('요청자 / 요청일',f"{p['requester']} / {p['requested_at']}"),
          ('수신','자재 관리 담당 (materials)'),('제품 구성',p['configuration_id']),
          ('관련 기존 계획 근거',p['baseline_evidence_id']),('추가 정상 완제품',f"{p['quantity']}대"),
          ('필요 날짜',f"{p['need_date']} / {basis[p['need_basis']]} 기준"),
          ('요청 사유·수량 근거',p['reason']),('변경 조건',p['conditions_state']+' / '+p['conditions_note'])]
    labels={'contract_item_id':'계약 품목','delivery_round_id':'납품 차수','destination':'도착지',
            'previous_evidence_id':'대체할 이전 요청 근거','related_evidence_id':'별도 수요로 구분한 요청 근거'}
    rows.extend((label,p[k]) for k,label in labels.items() if k in p)
    lines=['# 추가 생산 자재 검토 요청서','']
    if p['origin']=='synthetic': lines+=['자료 구분: 가상 운영 기록. 실제 발주·생산·납품 지시가 아닙니다.','']
    lines+=['| 항목 | 작성 내용 |','| --- | --- |']+[f'| {label} | {cell(value)} |' for label,value in rows]
    lines+=['','자재 확보 가능 여부와 부족 자재 검토를 요청합니다. 생산 승인 및 A 접수 완료를 의미하지 않습니다.',
            f"기준 데이터 버전: {provenance['reference_data_version']} / 저장소 식별: {provenance['store_id']}",
            f"등록 실행자: {provenance['actor']} / 기록 시각: {provenance['registered_at']}"]
    return '\n'.join(lines)+'\n'+MARKER+canonical(record)+'\n```\n'


def read_record(path):
    text=Path(path).read_text(encoding='utf-8')
    if len(text)>100_000: raise ValueError('요청 원문이 너무 큽니다.')
    if MARKER not in text: raise ValueError('요청 원문의 보존 기록이 없습니다.')
    raw=text.rsplit(MARKER,1)[1]
    if not raw.endswith('\n```\n'): raise ValueError('요청 원문 형식이 다릅니다.')
    record=decode_record(raw[:-5])
    if render_document(record)!=text: raise ValueError('원문 표시값과 보존 기록이 다릅니다.')
    return record


def build_nodes(path, record):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/'data') or read_record(path)!=record:
        raise ValueError('data/ 내부의 변경되지 않은 요청 원문이 필요합니다.')
    p=record['request']; provenance=record['provenance']
    token=digest([KIND,p['request_id'],p['revision']])
    sid='source:materials-request:'+token; eid='evidence:materials-request:'+token
    return [dict(id=sid,type='SourceDocument',origin=p['origin'],evidence=[],kind=KIND,
                 url='urn:ontologics:materials-request:'+token,path=str(path.relative_to(ROOT)),
                 sha256=hashlib.sha256(path.read_bytes()).hexdigest(),version=str(p['revision']),
                 license='Internal request; not licensed for redistribution',collected_at=provenance['registered_at']),
            dict(id=eid,type='Evidence',origin=p['origin'],evidence=[],source_id=sid,
                 locator='요청서 전체 및 시스템 보존 기록',description=canonical(record))]
