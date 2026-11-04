"""A screen adapter composing existing BOM/process calculators into a saved RFQ draft."""
from decimal import Decimal

from ..model import digest
from ..service import DomainError
from .materials import calculate_material_requirements
from .library_process_rfq import calculate_automatic, LibraryProcessRFQ


def shortage_draft(bom, process, *, quantity, configuration_id, contract=None):
    lines, excluded = [], []
    for item in bom['components']:
        row = dict(kind='bom', id=item['component_id'], name=item.get('name', item['component_id']),
                   required=item['D'], available=item['A'], shortage=item['S'], unit='piece',
                   basis='등록 BOM × 검토 생산 수량')
        if row['shortage'] is None:
            excluded.append(dict(row, reason='BOM 또는 재고 미확인'))
        elif row['shortage'] > 0:
            lines.append(row)
    for item in process:
        row = dict(kind='process', id=item['material_id'], name=item['name'], required=item['required'],
                   available=item['available'], shortage=item['shortage'], unit=item['unit'], basis=item.get('basis'))
        if row['shortage'] is None:
            excluded.append(dict(row, reason='사용량 기준 미등록 또는 재고 미확인'))
        elif Decimal(row['shortage']) > 0:
            lines.append(row)
    text = ['자재 견적요청서 (RFQ) — 미발송 초안',
            f'제품: {configuration_id} / 생산 검토량: {quantity}대']
    if contract:
        text += [f"계약 원본 보관함: {contract['contract_id']} / 품목 순번: {contract['item_index']+1}",
                 f"제품 연결 근거: {contract['link_id']} / 수량 근거: {contract['reason']}"]
    else:
        text.append('검토 목적: 제품별 자재 비교 (계약·추가 요청 수요와 자동 합산하지 않음)')
    text += ['공급사·희망 입고일·가격·MOQ·포장 단위: 구매팀 확인 필요', '', '견적 문의 수량 (확인된 부족량):']
    text += [f"- {r['name']}: {r['shortage']} {r['unit']} (필요 {r['required']} − 가용 {r['available']}); {r['basis']}" for r in lines]
    text += ['', f'미확인으로 제외: {len(excluded)}종',
             '공정 수량은 근거에 따른 조건부 추정이며 초기 장비 충전량은 미설정 시 미포함입니다.',
             '각 검토는 현재 가용 재고를 기준으로 하므로 여러 초안을 단순 합산해 발주하지 마세요.',
             '생산 승인·재고 예약·사용 차감·발주·외부 발송 없음.']
    return dict(status='draft_not_sent' if lines else 'no_confirmed_shortage', lines=lines, excluded=excluded,
                text='\n'.join(text)+'\n', sent=False, stock_reserved=False, supplier=None, desired_arrival_date=None)


class WorkspaceReview:
    def __init__(self, materials, process_rfq, context):
        self.materials, self.process_rfq, self.host = materials, process_rfq, materials.host
        with self.host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_workspace_material_reviews(
                id TEXT PRIMARY KEY,configuration_id TEXT NOT NULL,payload JSONB NOT NULL,
                content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

    def review(self, context, configuration_id, quantity, allowance_percent, request_key, *, contract=None):
        if type(quantity) is not int or not 0 <= quantity <= 10000000:
            raise DomainError('INVALID_INPUT', '생산 검토 수량은 0~10,000,000 사이 정수여야 합니다.')
        with self.host.request(context) as session:
            result = session.call('configuration', {'configuration_id': configuration_id})
            if not result['ok']:
                return result
            detail, version = result['data'], result['data_version']
            svc = session._service
            binding = dict(configuration_id=configuration_id, quantity=quantity,
                           allowance_percent=allowance_percent, contract=contract)
            def save():
                if svc._version() != version:
                    raise DomainError('STALE_REVISION', '제품 데이터가 변경됐습니다. 다시 검토하세요.')
                owner = None
                if contract:
                    self._check_contract(svc, contract, version)
                    owner = (contract['contract_id'], contract['item_index'])
                stock = self.materials.stock.calculate(self.materials.stock.latest(svc, configuration_id), svc, owner)
                bom = calculate_material_requirements(detail['items'], stock, quantity,
                    configuration_id=configuration_id, data_version=version,
                    demand_source=dict(purpose='contract_material_review' if contract else 'product_material_comparison',
                                       contract=contract), bom_complete=True)
                names = {n['id']: n['name'] for n in detail['components']}
                for row in bom['components']:
                    row['name'] = names.get(row['component_id'], row['component_id'])
                process, basis = self.process_rfq.current(svc, configuration_id, quantity)
                try:
                    rows = calculate_automatic(process, allowance_percent)
                except ValueError as exc:
                    raise DomainError('INVALID_INPUT', str(exc)) from None
                scopes = dict(bom=stock.get('scope'), process=process.get('stock_scope'))
                if all(scopes.values()) and scopes['bom'].get('company') != scopes['process'].get('company'):
                    raise DomainError('STOCK_COMPANY_MISMATCH', 'BOM과 공정 자재의 재고 소유 회사가 다릅니다.')
                payload = dict(configuration_id=configuration_id, quantity=quantity, allowance_percent=allowance_percent,
                    bom=bom, process=rows, process_basis=process, process_basis_hash=basis, data_version=version,
                    stock_scopes=scopes, evaluated_at=svc.now(), stock_reserved=False,
                    rfq=shortage_draft(bom, rows, quantity=quantity, configuration_id=configuration_id, contract=contract))
                if contract:
                    payload.update(contract)
                payload['basis_hash'] = digest(dict(bom=bom, process_basis=basis, process=rows, contract=contract))
                payload['rfq']['text'] += f"제품 데이터 버전: {version}\n계산 기준: {payload['basis_hash']}\n"
                for label, scope in scopes.items():
                    if scope:
                        payload['rfq']['text'] += f"{label} 재고 범위: {scope.get('company')} / {scope.get('location')} / {scope.get('as_of')}\n"
                return svc._envelope(ok=True, data=LibraryProcessRFQ.store(
                    svc, 'pp_workspace_material_reviews', configuration_id, payload, session.context['actor']))
            return svc._run(session.context['actor'], 'workspace_combined_review', binding, save,
                            permission='review', request_key=request_key)

    def contract_state(self, context, contract_id, item_index):
        result = self.materials.get(context, contract_id, item_index)
        if not result['ok']:
            return result
        data = result['data']
        with self.host.request(context) as session:
            svc = session._service
            def read():
                saved = svc.db.execute("""SELECT * FROM pp_workspace_material_reviews
                    WHERE payload->>'contract_id'=%s AND payload->>'item_index'=%s
                    ORDER BY created_at DESC,id DESC LIMIT 1""", (contract_id,str(item_index))).fetchone()
                data.update(combined_saved=saved, combined_current=False, combined_stock_changed=False)
                if saved:
                    p = saved['payload']
                    if digest(p) != saved['content_hash']:
                        raise DomainError('RFQ_INTEGRITY', '저장된 통합 검토 결과의 무결성을 확인하세요.')
                    data['combined_current'] = (p['link_id']==data['link']['id'] and
                                                p['data_version']==svc._version())
                    cid = p['configuration_id']
                    stock = self.materials.stock.calculate(self.materials.stock.latest(svc, cid), svc,
                                                          (contract_id,item_index))
                    _, basis = self.process_rfq.current(svc, cid, p['quantity'])
                    old = p['bom']['stock']
                    data['combined_stock_changed'] = (basis!=p.get('process_basis_hash') or
                        stock.get('snapshot_id')!=old.get('snapshot_id') or
                        stock.get('reservation_token')!=old.get('reservation_token'))
                return svc._envelope(ok=True, data=data)
            return svc._run(session.context['actor'], 'workspace_contract_material_get',
                            dict(contract_id=contract_id,item_index=item_index), read)

    def contract_review(self, context, contract_id, item_index, link_id, quantity, reason, allowance_percent, request_key):
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise DomainError('INVALID_INPUT', '생산 검토 수량의 산정 근거를 입력하세요.')
        current = self.materials.get(context, contract_id, item_index)
        if not current['ok']:
            return current
        link = current['data']['link']
        if link['id'] != link_id:
            raise DomainError('STALE_REVISION', '제품 연결이 바뀌었습니다. 다시 확인하세요.')
        contract = dict(contract_id=contract_id, item_index=item_index, link_id=link_id,
                        interpretation_id=link['interpretation_id'], review_id=link['review_id'], reason=reason)
        return self.review(context, link['configuration_id'], quantity, allowance_percent, request_key, contract=contract)

    @staticmethod
    def _check_contract(svc, contract, version):
        # Recheck immutable bindings under the same common store lock used for the saved draft.
        cid, item = contract['contract_id'], contract['item_index']
        link = svc.db.execute('''SELECT * FROM pp_contract_configuration_links
            WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC LIMIT 1''', (cid,item)).fetchone()
        interpretation = svc.db.execute('''SELECT id FROM pp_contract_interpretations
            WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1''', (cid,)).fetchone()
        check = svc.db.execute('''SELECT id FROM pp_contract_reading_checks
            WHERE interpretation_id=%s AND decisions IS NOT NULL AND decisions != '{}'::jsonb
            ORDER BY checked_at DESC,id DESC LIMIT 1''', (contract['interpretation_id'],)).fetchone()
        if (not link or link['id'] != contract['link_id'] or link['data_version'] != version
                or not interpretation or interpretation['id'] != contract['interpretation_id']
                or not check or check['id'] != contract['review_id']):
            raise DomainError('STALE_REVISION', '계약 대조·제품 연결이 변경됐습니다. 다시 확인하세요.')
