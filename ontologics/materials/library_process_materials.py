"""A-only process-material evidence and explicit synthetic shared company stock.

Reference consumption is not approved demand. This is a local relation view,
not a registration of new types in the common ontology or a stock reservation.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / 'data/production_planning/process_materials_v1.json'
UNITS = {'process-material:kester:np560:7048230910': 'g',
         'process-material:kester:275:2470687601': 'g',
         'process-material:kester:951': 'g',
         'process-material:mgchemicals:824': 'mL',
         'process-material:kester:sac305:bar': 'g'}


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('시간대가 있는 재고 기준 시각이 필요합니다.')
    return result


def amount(value):
    if not isinstance(value, str):
        raise ValueError('수량은 단위가 지정된 십진 문자열이어야 합니다.')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('재고 수량이 올바르지 않습니다.') from None
    if not result.is_finite() or not 0 <= result <= 1000000000:
        raise ValueError('재고 수량의 범위를 확인하세요.')
    return result


def decimal_text(value):
    return format(value.normalize(), 'f') if value else '0'


def load_reference(path=REFERENCE):
    data = json.loads(Path(path).read_text())
    if data.get('format') != 'process-material-reference/1.0':
        raise ValueError('지원하지 않는 공정 자재 자료입니다.')
    for source in data['sources'] + [data['existing_catalog']]:
        file = (ROOT / source['path']).resolve()
        if not file.is_relative_to(ROOT / 'data'):
            raise ValueError('공정 자재 원문 경로가 올바르지 않습니다.')
        if hashlib.sha256(file.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('공정 자재 원문이 변경됐습니다. 근거를 다시 확인하세요.')
    material_ids = {m['id'] for m in data['materials']}
    evidence_ids = {e['id'] for e in data['evidence']}
    source_ids = {s['id'] for s in data['sources']}
    if material_ids != set(UNITS) or len(material_ids) != len(data['materials']):
        raise ValueError('공정 자재 품목을 확인하세요.')
    if any(e['source_id'] not in source_ids for e in data['evidence']):
        raise ValueError('근거 원문 연결이 누락되었습니다.')
    if any(not set(m['evidence']) <= evidence_ids for m in data['materials']):
        raise ValueError('자재 근거 연결이 누락되었습니다.')
    supplement = json.loads((Path(__file__).with_name('data') / 'per_board_usage_v1.json').read_text())
    if supplement['reference_sha256'] != hashlib.sha256(Path(path).read_bytes()).hexdigest():
        raise ValueError('대당 사용량의 원문 기준이 변경되었습니다. 치수 산정을 다시 수행하세요.')
    if supplement.get('format') != 'process-per-board-basis/1.0':
        raise ValueError('대당 사용량 자료 형식을 확인하세요.')
    for config in supplement['configurations']:
        if {m['material_id'] for m in config['materials']} != material_ids:
            raise ValueError('대당 사용량 자재 연결을 확인하세요.')
        for m in config['materials']:
            if not set(m['evidence']) <= evidence_ids or m['unit'] != UNITS[m['material_id']]:
                raise ValueError('대당 사용량 근거/단위를 확인하세요.')
            if m['base_per_board'] is not None:
                amount(m['base_per_board'])
    data['per_board_supplement'] = supplement
    from .library_usage_history import supplement_from_history
    return supplement_from_history(data)


def scenario(reference, as_of):
    """Deliberate user-authorized fixture, never invoked by a read operation."""
    at = instant(as_of)
    counts = [('500', '200', '50'), ('453.59237', '100', '25'),
              ('20', '5', '2'), ('1000', '200', '100'), ('2000', '1000', '0')]
    lots = []
    for material, (on_hand, reserved, held) in zip(reference['materials'], counts):
        mid = material['id']
        lots.append(dict(id='process-demo-lot:' + mid, material_id=mid,
                         unit=UNITS[mid], on_hand=on_hand, reserved=reserved, held=held,
                         expires_at=(at + timedelta(days=365)).isoformat(),
                         expiry_basis='테스트를 위한 사용기한 가정이며 제조사 유효기간 규격 아님',
                         deductions_disjoint=True))
    return dict(origin='synthetic', version=1, company='Planning Reference Company',
                location='공정 자재 공용 창고', as_of=as_of,
                basis='사용자 요청에 따른 회사 보유량 가정. 실재고·실제 예약·실측 유효기간 아님.',
                lots=lots)


def validate_stock(stock, reference):
    if stock.get('origin') != 'synthetic' or not stock.get('company') or not stock.get('location'):
        raise ValueError('회사·창고·가상 재고 구분이 필요합니다.')
    instant(stock['as_of'])
    ids = set()
    if not isinstance(stock.get('lots'), list):
        raise ValueError('재고 로트 목록이 필요합니다.')
    for lot in stock['lots']:
        mid = lot['material_id']
        if mid not in UNITS or lot['unit'] != UNITS[mid] or lot.get('deductions_disjoint') is not True:
            raise ValueError('자재·단위·중복 없는 차감 기준을 확인하세요.')
        if not isinstance(lot['id'], str) or not lot['id'] or lot['id'] in ids:
            raise ValueError('재고 로트 식별자는 중복될 수 없습니다.')
        ids.add(lot['id'])
        hand, reserved, held = [amount(lot[k]) for k in ('on_hand', 'reserved', 'held')]
        if reserved + held > hand:
            raise ValueError('예약·보류량이 보유량보다 큽니다.')
        if lot.get('expires_at'):
            instant(lot['expires_at'])
    return stock


def inspect_materials(reference, stock, configuration_id, quantity, evaluated_at):
    if type(quantity) is not int or not 0 <= quantity <= 10000000:
        raise ValueError('검토 수량은 0~10,000,000 사이 정수여야 합니다.')
    configurations = {c['configuration_id']: c for c in reference['configuration_links']}
    if configuration_id not in configurations:
        raise ValueError('공정 자재와 연결된 제품 구성이 없습니다.')
    now = instant(evaluated_at)
    if stock is not None:
        validate_stock(stock, reference)
        if instant(stock['as_of']) > now:
            raise ValueError('미래 시점의 재고는 사용할 수 없습니다.')
    config = configurations[configuration_id]
    usage_config = next((c for c in reference.get('per_board_supplement', {}).get('configurations', [])
                         if c['configuration_id'] == configuration_id), None)
    usage = {m['material_id']: m for m in usage_config['materials']} if usage_config else {}
    rows = []
    nodes, edges = {}, []
    def node(id, type, label, **extra):
        nodes[id] = dict(id=id, type=type, label=label, **extra)
    def edge(a, b, relation):
        edges.append(dict(source=a, target=b, relation=relation))
    for cid in configurations:
        node(cid, 'Configuration', 'ESP32-POE ' + cid.split(':')[-2] + ' WROOM')
    for source in reference['sources']:
        node(source['id'], 'SourceDocument', Path(source['path']).name, url=source['url'])
    for ev in reference['evidence']:
        node(ev['id'], 'Evidence', ev['description'], locator=ev['locator'])
        edge(ev['id'], ev['source_id'], 'source_document')
    for link in configurations.values():
        for ev in link['evidence']:
            edge(link['configuration_id'], ev, 'supported_by')
    for material in reference['materials']:
        mid = material['id']
        if configuration_id not in material['configuration_refs']:
            continue
        lots = []
        for lot in (stock or {}).get('lots', []):
            if lot['material_id'] != mid:
                continue
            expired = bool(lot.get('expires_at') and instant(lot['expires_at']) <= now)
            available = Decimal(0) if expired else amount(lot['on_hand']) - amount(lot['reserved']) - amount(lot['held'])
            lots.append(dict(lot, available=decimal_text(available), expired=expired))
            node(lot['id'], 'SyntheticStockLot', '가상 공용 로트', unit=lot['unit'], available=decimal_text(available))
            edge(mid, lot['id'], 'stock_lot')
        # A missing material lot is unknown, never silently interpreted as zero stock.
        totals = {k: decimal_text(sum((amount(l[k]) for l in lots), Decimal(0))) if lots else None
                  for k in ('on_hand', 'reserved', 'held', 'available')}
        totals.update(lots=lots, unit=UNITS[mid], state='synthetic' if lots else 'unknown')
        node(mid, 'ProcessMaterialCandidate', material['name'], application_status='unconfirmed')
        process_id = 'process-review:' + mid
        node(process_id, 'ProcessReview', material['process'], application_status='unconfirmed')
        edge(process_id, mid, 'candidate_material')
        for cid in material['configuration_refs']:
            edge(cid, process_id, 'requires_process_review')
        for ev in material['evidence']:
            edge(mid, ev, 'supported_by')
        required, shortage = None, None
        reference_case = None
        status = 'usage_unknown'
        if mid == config['flux_reference_scenario']['material_id']:
            reference_case = config['flux_reference_scenario']
            required = [decimal_text(Decimal(x) * quantity) for x in reference_case['wet_flux_deposited_g_per_board']]
            if totals['available'] is not None:
                shortage = [decimal_text(max(Decimal(0), Decimal(x) - amount(totals['available']))) for x in required]
                status = 'reference_shortage' if Decimal(shortage[1]) > 0 else 'reference_covered'
            else:
                status = 'stock_unknown'
        relevant_evidence = set(material['evidence'])
        if reference_case:
            relevant_evidence.update(reference_case['evidence'])
        # Include parameter/derived-value evidence, not just the product identity source.
        def collect(value):
            if isinstance(value, dict):
                relevant_evidence.update(value.get('evidence', []))
                for x in value.values(): collect(x)
            elif isinstance(value, list):
                for x in value: collect(x)
        collect(material['parameters'])
        collect(usage.get(mid, {}))
        evidences = [e for e in reference['evidence'] if e['id'] in relevant_evidence]
        for ev in evidences:
            if ev['id'] not in material['evidence']:
                edge(mid, ev['id'], 'calculation_basis')
        source_ids = {e['source_id'] for e in evidences}
        rows.append(dict(material, stock=totals, required=None, purchase_quantity=None,
                         usage_basis=usage.get(mid),
                         reference_required=required, reference_shortage=shortage, reference_case=reference_case,
                         status=status, used_by=material['configuration_refs'],
                         evidence_details=evidences, sources=[s for s in reference['sources'] if s['id'] in source_ids]))
    return dict(configuration_id=configuration_id, configurations=list(configurations), quantity=quantity,
                evaluated_at=evaluated_at, stock_scope={k: stock[k] for k in ('origin','company','location','as_of','basis')} if stock else None,
                materials=rows, production_ready=False, purchase_authorized=False,
                board_geometry=usage_config['board'] if usage_config else None,
                summary=dict(material_count=len(rows), unknown_demand_count=len(rows),
                             reference_shortage_count=sum(r['status']=='reference_shortage' for r in rows)),
                graph=dict(scope='materials_reference_view', nodes=list(nodes.values()), edges=edges),
                reference_version=reference['data_version'])


class LibraryProcessMaterials:
    def __init__(self, host, context):
        self.host = host
        with host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_process_material_stock(
                id TEXT PRIMARY KEY, payload JSONB NOT NULL, content_hash TEXT NOT NULL,
                actor TEXT NOT NULL, created_at TEXT NOT NULL)''')

    @staticmethod
    def latest(svc):
        from ..model import digest
        from ..service import DomainError
        row = svc.db.execute('SELECT * FROM pp_process_material_stock ORDER BY created_at DESC,id DESC LIMIT 1').fetchone()
        if row and digest(row['payload']) != row['content_hash']:
            raise DomainError('PROCESS_STOCK_INTEGRITY', '공정 자재 재고의 무결성을 확인하지 못했습니다.')
        return row

    def store(self, context, payload, request_key):
        """Append an explicit synthetic observation without changing history."""
        from ..model import canonical, digest
        from ..service import DomainError
        payload = deepcopy(payload)
        with self.host.request(context) as session:
            svc = session._service
            def save():
                try:
                    if not isinstance(payload, dict):
                        raise ValueError('공정 자재 재고 객체가 필요합니다.')
                    for field in ('company', 'location', 'as_of', 'basis'):
                        if not isinstance(payload.get(field), str) or not payload[field].strip():
                            raise ValueError('회사·창고·기준 시각·재고 근거를 입력하세요.')
                    if not isinstance(payload.get('lots'), list):
                        raise ValueError('재고 로트 목록이 필요합니다.')
                    for lot in payload['lots']:
                        if not isinstance(lot, dict):
                            raise ValueError('재고 로트 객체가 필요합니다.')
                        if lot.get('expires_at') is not None and not isinstance(lot['expires_at'], str):
                            raise ValueError('사용기한은 시간대가 있는 시각이어야 합니다.')
                    validate_stock(payload, load_reference())
                    if instant(payload['as_of']) > instant(svc.now()):
                        raise ValueError('미래 시점의 재고는 등록할 수 없습니다.')
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    raise DomainError('INVALID_INPUT', str(exc)) from None
                self.latest(svc)
                key = digest(payload)
                svc.db.execute('''INSERT INTO pp_process_material_stock VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(id) DO NOTHING''',
                    (key, canonical(payload), key, session.context['actor'], svc.now()))
                return svc._envelope(ok=True, data=dict(id=key, lots=len(payload['lots'])))
            return svc._run(session.context['actor'], 'process_material_stock_store',
                            dict(payload=payload), save, permission='review', request_key=request_key)

    def seed_scenario(self, context, request_key):
        from ..model import canonical, digest
        from ..service import DomainError
        with self.host.request(context) as session:
            svc = session._service
            def save():
                old = self.latest(svc)
                if old:
                    return svc._envelope(ok=True, data=dict(id=old['id'], preserved=True))
                reference = load_reference()
                payload = scenario(reference, svc.now())
                validate_stock(payload, reference)
                key = digest(payload)
                svc.db.execute('INSERT INTO pp_process_material_stock VALUES(%s,%s,%s,%s,%s)',
                               (key,canonical(payload),key,session.context['actor'],svc.now()))
                return svc._envelope(ok=True, data=dict(id=key, preserved=False))
            return svc._run(session.context['actor'], 'process_material_stock_scenario', {}, save,
                            permission='review', request_key=request_key)

    def inspect(self, context, configuration_id, quantity):
        from ..model import digest
        from ..service import DomainError
        with self.host.request(context) as session:
            svc = session._service
            def read():
                try:
                    reference = load_reference()
                    row = self.latest(svc)
                    data = inspect_materials(reference, row['payload'] if row else None,
                                             configuration_id, quantity, svc.now())
                except (ValueError, KeyError, OSError, TypeError) as exc:
                    raise DomainError('PROCESS_MATERIAL_INPUT', str(exc)) from None
                data['stock_snapshot_id'] = row['id'] if row else None
                data['reference_hash'] = digest(reference)
                return svc._envelope(ok=True, data=data)
            return svc._run(session.context['actor'], 'process_material_stock_get',
                            dict(configuration_id=configuration_id,quantity=quantity), read)
