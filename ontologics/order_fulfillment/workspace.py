"""Common-screen B adapters; existing B arithmetic and shared approval remain owners.

Builders return proposals without origin/evidence. business_drafts attaches the
submitted immutable source and saves a person-bound draft. Nothing here dispatches
goods, converts completion into stock, or writes approved knowledge.
"""
from copy import deepcopy

from ..model import instant
from ..service import DomainError
from .service import Service as FulfillmentService
from .fulfillment_validation import CONFIRMATION_KEYS


QUERY_ARGUMENTS = {
    'delivery_round': {'delivery_round_id', 'version'},
    'fulfillment_status': {'delivery_round_id', 'as_of', 'version'},
    'delivery_forecast': {'delivery_round_id', 'as_of', 'version'},
    'response_plan': {'delivery_round_id', 'as_of', 'version'},
    'production_completion': {'production_work_id', 'delivery_round_id', 'as_of', 'version'},
}
TIMES = {'planned_departure_at', 'departed_at', 'expected_arrival_at', 'arrived_at'}


def query(service, actor, action, arguments):
    """Run reviewed B reads on the host's existing service and selected backend."""
    if (not isinstance(action, str) or action not in QUERY_ARGUMENTS
            or not isinstance(arguments, dict) or set(arguments) != QUERY_ARGUMENTS[action]):
        raise DomainError('INVALID_INPUT', 'Select a supported B query with explicit version and exact arguments')
    return getattr(FulfillmentService, action)(service, actor, **arguments)


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise DomainError('INVALID_INPUT', '업무 입력의 필수 항목과 허용 항목을 확인하세요.')


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise DomainError('INVALID_INPUT', '비어 있지 않은 식별자가 필요합니다.')
    return value


def _integer(value, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise DomainError('INVALID_QUANTITY', '수량은 정수이며 음수·소수·참/거짓 값을 사용할 수 없습니다.')
    return value


def _at(value):
    try:
        return instant(value)
    except (TypeError, ValueError):
        raise DomainError('INVALID_TIME', '시간대가 포함된 정확한 시각을 입력하세요.') from None


def _record(index, identifier, kind):
    node = index.get(_text(identifier))
    if node is None or node.get('type') != kind:
        raise DomainError('NOT_FOUND', '선택한 승인 버전에서 ' + kind + ' 기록을 찾을 수 없습니다.')
    return node


def _clean(node):
    return {key: deepcopy(value) for key, value in node.items() if key not in ('origin', 'evidence')}


def build_records(nodes, action, payload):
    """Prepare explicit form values; full shared graph validation runs afterwards."""
    index = {node['id']: node for node in nodes}
    if action == 'production_progress':
        _fields(payload, {'production_work_id', 'observation_id', 'observed_at',
                          'produced_quantity', 'good_quantity', 'defective_quantity'},
                {'snapshot_as_of', 'confirmations'})
        work = _record(index, payload['production_work_id'], 'ProductionWork')
        observation_id = _text(payload['observation_id'])
        if observation_id in index:
            raise DomainError('RECORD_EXISTS', '새 생산 누계 기록에는 새 ID를 사용하세요.')
        if work['production_state'] != 'in_progress':
            raise DomainError('INVALID_STATE', '기존에 진행 중으로 기록된 작업만 누계를 보완할 수 있습니다.')
        observed = _at(payload['observed_at'])
        snapshot = _record(index, work['snapshot_id'], 'FulfillmentSnapshot')
        snapshot_change = []
        if 'snapshot_as_of' in payload or 'confirmations' in payload:
            if ('snapshot_as_of' not in payload or 'confirmations' not in payload
                    or not isinstance(payload['confirmations'], dict)
                    or set(payload['confirmations']) != CONFIRMATION_KEYS
                    or any(type(value) is not bool for value in payload['confirmations'].values())):
                raise DomainError('INVALID_CONFIRMATIONS', '새 기준 시각에는 9개 완전성 조건을 각각 참/거짓으로 확인해야 합니다.')
            if _at(payload['snapshot_as_of']) < _at(snapshot['as_of']):
                raise DomainError('CONFLICTING_TIME', '기준 시각을 과거로 되돌릴 수 없습니다.')
            snapshot = _clean(snapshot)
            snapshot.update(as_of=payload['snapshot_as_of'], confirmations=deepcopy(payload['confirmations']))
            snapshot_change = [snapshot]
        if observed < _at(work['observed_at']) or observed > _at(snapshot['as_of']):
            raise DomainError('CONFLICTING_TIME', '누계 시각은 기존 작업 관측 이후·선택 기준 시각 이내여야 합니다. 이후 시점은 별도 기준 기록이 필요합니다.')
        counters = {key: _integer(payload[key]) for key in
                    ('produced_quantity', 'good_quantity', 'defective_quantity')}
        if counters['good_quantity'] < work['completed_quantity']:
            raise DomainError('PRODUCTION_COUNTER_DECREASED', '진행 누계에서 이전 정상 완료 수량을 줄일 수 없습니다.')
        updated = _clean(work)
        updated.update(observed_at=payload['observed_at'], completed_quantity=counters['good_quantity'])
        observation = dict(id=observation_id, type='ProductionObservation',
                           production_work_id=work['id'], observed_at=payload['observed_at'],
                           unit='piece', **counters)
        return [*snapshot_change, updated, observation]
    if action not in ('split_shipments', 'split_allocation'):
        raise DomainError('INVALID_INPUT', '지원하는 부분 생산·분할 출하 작업을 선택하세요.')
    required = {'stock_id', 'delivery_round_id', 'parts'} if action == 'split_shipments' else {'allocation_id', 'parts'}
    _fields(payload, required)
    original = None
    if action == 'split_allocation':
        original = _record(index, payload['allocation_id'], 'Allocation')
        if 'stock_id' not in original:
            raise DomainError('UNSUPPORTED_SHIPMENT_SOURCE', '생산 배정을 재고 출하로 변환하지 않습니다.')
        if any(n.get('type') == 'Shipment' and n.get('allocation_id') == original['id'] for n in nodes):
            raise DomainError('ALLOCATION_ALREADY_SHIPPED', '출하 기록이 연결된 배정은 이 화면에서 재분할하지 않습니다.')
        stock_id, round_id = original['stock_id'], original['delivery_round_id']
    else:
        stock_id, round_id = payload['stock_id'], payload['delivery_round_id']
    stock = _record(index, stock_id, 'FinishedGoodsStock')
    delivery = _record(index, round_id, 'DeliveryRound')
    parts = payload['parts']
    if not isinstance(parts, list) or not 1 <= len(parts) <= 20:
        raise DomainError('INVALID_INPUT', '출하 회차를 1~20개 입력하세요.')
    records, seen = [], set()
    for number, part in enumerate(parts):
        _fields(part, {'allocation_id', 'shipment_id', 'quantity'}, TIMES)
        aid, sid = _text(part['allocation_id']), _text(part['shipment_id'])
        if original is not None and number == 0 and aid != original['id']:
            raise DomainError('INVALID_INPUT', '첫 분할 배정은 기존 배정 ID를 유지해야 합니다.')
        for identifier in (aid, sid):
            allowed_existing = original is not None and number == 0 and identifier == aid == original['id']
            if identifier in seen or (identifier in index and not allowed_existing):
                raise DomainError('RECORD_EXISTS', '분할 회차의 새 ID는 기존 기록 및 서로 중복될 수 없습니다.')
            seen.add(identifier)
        quantity = _integer(part['quantity'], positive=True)
        times = {field: part[field] for field in TIMES if field in part}
        for value in times.values():
            _at(value)
        records.extend([
            dict(id=aid, type='Allocation', snapshot_id=stock['snapshot_id'],
                 delivery_round_id=delivery['id'], stock_id=stock['id'], quantity=quantity, unit='piece'),
            dict(id=sid, type='Shipment', snapshot_id=stock['snapshot_id'], allocation_id=aid,
                 quantity=quantity, unit='piece', destination=delivery['destination'], **times),
        ])
    if original is not None and sum(part['quantity'] for part in parts) != original['quantity']:
        raise DomainError('SPLIT_QUANTITY_MISMATCH', '분할 회차 합계가 기존 배정 전체 수량과 같아야 합니다.')
    return records
