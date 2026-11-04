"""Explicit C intake and human-selected case proposals for the common workspace.

No symptom diagnosis, test result invention, stock membership inference, or
production-work linking occurs here. Approval is always the common host's job.
"""
from __future__ import annotations

from ..model import digest
from ..service import DomainError


CLAIM_REQUIRED = {'external_claim_id', 'customer_name', 'reported_at',
                  'reported_symptom', 'claimed_quantity', 'reported_serials'}
CLAIM_OPTIONAL = {'reported_conditions', 'requested_action', 'delivery_reference'}


def _fields(payload, required, optional=()):
    if not isinstance(payload, dict) or not required <= set(payload) or set(payload) - required - set(optional):
        raise DomainError('INVALID_CLAIM_INPUT', '클레임의 필수 입력과 허용 항목을 확인하세요.')


def _demo(value):
    return isinstance(value, str) and any(word in value.casefold() for word in ('demo', '가상', '가정', '시연'))


def _bare(record):
    return {key: value for key, value in record.items() if key not in {'origin', 'evidence'}}


def build_records(nodes, action, payload):
    index = {node['id']: node for node in nodes}
    if action == 'intake':
        _fields(payload, CLAIM_REQUIRED, CLAIM_OPTIONAL)
        # The current public policy permits demonstration records only, not
        # uploading real customer information to the synthetic graph.
        if (not _demo(payload.get('external_claim_id')) or not _demo(payload.get('customer_name'))
                or ('delivery_reference' in payload and not _demo(payload['delivery_reference']))):
            raise DomainError('DEMO_CLAIM_REQUIRED', '시연용 가상 고객·클레임·납품 번호에 DEMO 또는 가상 표시를 포함하세요.')
        identity = (payload['customer_name'], payload['external_claim_id'])
        if any(node['type'] == 'ClaimIntake' and
                (node['customer_name'], node['external_claim_id']) == identity for node in nodes):
            raise DomainError('CLAIM_ALREADY_REGISTERED', '같은 고객·클레임 번호가 이미 승인됐습니다. 기존 접수 기록을 확인하세요.')
        return [{'id': 'claim-intake:submitted:' + digest(identity), 'type': 'ClaimIntake',
                 **payload, 'intake_state': 'needs_review'}]
    if action == 'confirm_case':
        _fields(payload, {'intake_id', 'unit_ids', 'serials_confirmed'})
        intake = index.get(payload['intake_id']) if isinstance(payload['intake_id'], str) else None
        if not intake or intake['type'] != 'ClaimIntake':
            raise DomainError('NOT_FOUND', '승인된 클레임 접수 기록을 선택하세요.')
        if intake['intake_state'] == 'rejected':
            raise DomainError('INVALID_STATE', '반려된 접수로 품질 사건을 만들 수 없습니다.')
        units = payload['unit_ids']
        if (payload['serials_confirmed'] is not True or not isinstance(units, list)
                or not units or any(not isinstance(nid, str) for nid in units)
                or len(set(units)) != len(units)):
            raise DomainError('CLAIM_UNITS_REQUIRED', '신고 일련번호와 직접 대조한 개별 보드를 선택하고 확인하세요.')
        for nid in units:
            unit = index.get(nid)
            if not unit or unit['type'] != 'ProductUnit' or unit['serial'] not in intake['reported_serials']:
                raise DomainError('CLAIM_SERIAL_MISMATCH', '선택한 보드가 신고 일련번호에 없습니다. 근거를 보완하고 다시 확인하세요.')
        if any(node['type'] == 'QualityCase' and node['intake_id'] == intake['id'] for node in nodes):
            raise DomainError('CLAIM_CASE_EXISTS', '이 접수의 품질 사건이 이미 등록됐습니다. 기존 사건을 확인하세요.')
        claim = dict(_bare(intake), intake_state='confirmed')
        case = {'id': 'quality-case:submitted:' + digest(intake['id']), 'type': 'QualityCase',
                'intake_id': intake['id'], 'unit_ids': units,
                **{key: intake[key] for key in ('customer_name', 'reported_at', 'reported_symptom')}}
        return [claim, case]
    raise DomainError('INVALID_CLAIM_ACTION', '지원하지 않는 클레임 작업입니다.')
