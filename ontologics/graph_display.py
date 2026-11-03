"""Derived Neo4j Browser captions, never canonical facts or query inputs.

Kind labels reuse the executable SCHEMA. Colors live in the Browser's local
style preset (ontology/neo4j-browser.grass), not in the approved graph payload.
"""
from __future__ import annotations


DISPLAY_NAMES = {
    'ProductModel': '제품', 'ProductRevision': '개정', 'Configuration': 'BOM 구성',
    'ComponentModel': '부품', 'BOMItem': 'BOM 항목', 'ProductUnit': '보드',
    'ComponentLot': '로트', 'Installation': '장착 기록', 'HistoryCoverage': '이력 범위',
    'InventoryMovement': '입출고', 'Evidence': '근거', 'SourceDocument': '원본',
    'Contract': '계약', 'ContractItem': '계약 품목', 'DeliveryRound': '납품 회차',
    'ComponentStockSnapshot': '부품 재고 관측', 'FulfillmentSnapshot': '이행 기준 시점',
    'FinishedGoodsStock': '완제품 재고 관측', 'ProductionWork': '생산 기록',
    'ProductionObservation': '생산 수량 관측', 'Allocation': '납품 배정',
    'Shipment': '출하 기록', 'DeliveryCondition': '납품 조건',
    'ProductionLine': '생산라인', 'UnitProductionRecord': '보드 생산 기록',
    'ClaimIntake': '클레임 접수', 'QualityCase': '클레임 사건',
    'QualityScope': '영향 범위', 'ContainmentRequest': '중단 요청서',
    'TestRequest': '테스트 의뢰', 'QualityTestResult': '테스트팀 결과',
    'ReproductionRequirement': '재생산 요건',
}


def browser_caption(node, index):
    """Format only validated snapshot values; IDs/full payload stay available.

    A caption is not an identifier and never establishes component equivalence,
    installation certainty, approval, operation time, defects or safety.
    """
    kind = node['type']
    if kind in ('ProductModel', 'ComponentModel'):
        detail = node['name']
    elif kind == 'ProductRevision':
        detail = node['revision']
    elif kind == 'Configuration':
        detail = index[node['revision_id']]['revision'] + ' ' + node['variant']
    elif kind == 'BOMItem':
        revision = index[index[node['configuration_id']]['revision_id']]['revision']
        detail = revision + ' ' + ','.join(node['positions'])
    elif kind == 'ProductUnit':
        detail = node['serial']
    elif kind == 'ComponentLot':
        detail = node['lot_code']
    elif kind == 'Installation':
        detail = index[node['unit_id']]['serial'] + '/' + node['position']
    elif kind == 'HistoryCoverage':
        detail = index[node['unit_id']]['serial'] + ' ' + node['start'][:10]
    elif kind == 'InventoryMovement':
        movement = {'receipt': '입고', 'issue': '출고'}[node['movement']]
        detail = index[node['lot_id']]['lot_code'] + ' ' + movement
    elif kind == 'Contract':
        detail = node['company'] + ' / ' + node['customer_name']
    elif kind == 'ComponentStockSnapshot':
        detail = index[node['lot_id']]['lot_code'] + ' ' + node['as_of'][:10]
        if node['quantity_state'] == 'unknown':
            detail += ' 수량 미확인'
    elif kind == 'FulfillmentSnapshot':
        detail = node['company'] + ' ' + node['as_of'][:10]
    elif kind == 'DeliveryRound':
        detail = node['id'] + ' ' + node['due_at'][:10]
    elif kind == 'ProductionLine':
        detail = node['line_code'] + ' ' + node['name']
    elif kind == 'UnitProductionRecord':
        detail = index[node['unit_id']]['serial'] + ' / ' + index[node['production_line_id']]['line_code']
    elif kind == 'ClaimIntake':
        detail = node['external_claim_id']
    elif kind == 'QualityCase':
        detail = index[node['intake_id']]['external_claim_id']
    elif kind == 'QualityScope':
        detail = index[node['production_line_id']]['line_code'] + ' ' + node['as_of'][:10]
    elif kind == 'ContainmentRequest':
        detail = {'production_stop': '생산 중단 요청', 'shipment_hold': '출고 보류 요청'}[node['request_kind']]
    elif kind == 'TestRequest':
        detail = node['case_id'] + ' ' + node['prepared_at'][:10]
    elif kind == 'QualityTestResult':
        detail = index[node['unit_id']]['serial'] + ' ' + node['outcome']
    elif kind == 'ReproductionRequirement':
        state = {'confirmed': '수량 확정', 'lower_bound': '하한'}[node['assessment_state']]
        detail = node['configuration_id'] + ' ' + str(node['required_good_quantity']) + '대 ' + state
    else:
        detail = node['id']
    return DISPLAY_NAMES[kind] + (' [가상]' if node['origin'] == 'synthetic' else '') + ' ' + detail
