"""Evidence-selection validation against the exact common-tool results."""
import json
from ...model import canonical
from ...service import DomainError

def verified_knowledge_text(text, result):
    """Check model-selected evidence and render immutable source facts separately."""
    candidates = result['data']['candidates']
    allowed = {e: row for row in candidates for e in row['evidence']}
    try:
        answer = json.loads(text)
        if (not isinstance(answer, dict) or set(answer) != {'data_version', 'answerable', 'selected_evidence_ids'} or
                type(answer['data_version']) is not int or answer['data_version'] != result['data_version'] or
                type(answer['answerable']) is not bool):
            raise ValueError()
        ids = answer['selected_evidence_ids']
        if (not isinstance(ids, list) or any(not isinstance(e, str) for e in ids) or
                len(ids) != len(set(ids)) or not set(ids) <= set(allowed) or
                answer['answerable'] != bool(ids)):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise DomainError('AI_EVIDENCE_MISMATCH', '모델이 선택한 근거 또는 데이터 버전이 검색 결과와 다릅니다.') from None
    coverage = result['data']['index_coverage']
    lines = ['승인된 데이터 버전: v' + str(result['data_version']) +
             ' · 색인 범위: ' + str(coverage['indexed']) + '/' + str(coverage['total']),
             '아래는 모델이 선택한 근거 후보를 공통 조회와 대조한 결과입니다. 설명은 원본 필드로 서버가 구성합니다.']
    if not ids:
        lines.append('반환된 후보에서 이 질문에 직접 답할 근거를 선택하지 못했습니다. 자료가 없거나 해당 부품이 미사용·안전하다는 판정은 아닙니다. 질문이나 근거 범위를 보완해 주세요.')
    population = {'populated': '원본 BOM 표기상 실장', 'not_populated': '원본 BOM 표기상 미실장', 'unknown': '원본 BOM 실장 여부 미확인'}
    for eid in ids:
        row = allowed[eid]; source = row['source']
        lines += ['', eid + ' · ' + ('실제 공개 기술 자료' if row['origin'] == 'real' else '시연용 가상 운영 기록'),
                  '원본: ' + source['id'] + ' · 위치: ' + str(row['locator']),
                  '원본 버전: ' + str(source.get('version', '미기재')) + ' · SHA-256: ' + source['sha256']]
        if source.get('url'):
            lines.append('원본 URL: ' + source['url'])
        for fact in row['facts']:
            if fact['type'] == 'BOMItem':
                lines.append('BOM ' + fact['id'] + ' · ' + ', '.join(fact['positions']) + ' · ' + fact['raw_value'] +
                             ' · 수량 ' + str(fact['quantity']) + ' · ' + population.get(fact['population'], fact['population']))
            elif fact['type'] in {'Installation', 'HistoryCoverage', 'ProductUnit', 'ComponentLot', 'InventoryMovement'}:
                lines.append(fact['type'] + ' ' + fact['id'] + ' · ' + canonical({k:v for k,v in fact.items() if k not in {'evidence','origin','id','type'}}))
    lines.append('BOM 표기와 개별 보드의 로트 장착 이력은 다릅니다. 부품 동일성·호환성·고장 원인·안전·가동 여부는 이 검색으로 확정하지 않습니다. 장착 판정은 동일 A/B 조건의 공통 조회를 사용하세요.')
    return '\n'.join(lines), answer
