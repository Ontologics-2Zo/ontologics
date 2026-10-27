"""Role-owned definitions for the limited CLI example; not full service evaluation."""
import json
from ...demo import CONFIG_L1, must
from ...ingest import build_catalog
from ...model import canonical
from ...service import Service
from ..sample_support import COMMON, example_principals, _successful_calls, strings, integer

PRODUCT_PROMPT = COMMON + '''
너는 최소 Product 예제다. 이미 파싱·검증된 ODS의 대표 두 행을 제공받는다.
조회 version은 입력 data_version을 그대로 사용한다. 0이나 추정한 버전을 사용하지 않는다.
lookup으로 기존 Configuration과 각 Evidence를 확인하고, 두 행만 BOMItem put operations로 만들어
prepare_draft를 호출한다. 첫 호출은 새 초안이므로 draft_id와 expected_version을 생략한다.
Configuration ID를 draft_id로 사용하지 않는다. 반환받은 실제 draft_id만 후속 조회·검증에 사용한다. 각 입력 행의 population은 공통 파서가 확인한 값이므로 그대로 복사한다. U4는 populated, R50은 unknown이다.
확인된 U4를 unknown으로 낮추거나 R50을 populated로 높이지 않는다.
부품 ID와 원문 값·수량·위치를 바꾸지 않는다. 전체 BOM 완성이나 직접 원본 PDF 추출을 주장하지 않는다.
반환된 draft_id/draft_version으로 validate_draft를 호출하고 검증에 실패하면 실패를 설명하고 종료한다.
최종 JSON: {"draft_id":문자열,"draft_version":정수,"summary":한국어설명,
"evidence":[두 Evidence ID],"unknowns":[{"node_id":ID,"field":"population","value":"unknown","reason":한국어}],
"needs_human_review":true}. 승인·등록 완료라고 설명하지 않는다.'''


def parse_answer(text):
    """Reject malformed role output without crashing the example report."""
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            return None
        required = {'draft_id','draft_version','summary','evidence','unknowns','needs_human_review'}
        valid = (set(value)==required and isinstance(value['draft_id'],str) and integer(value['draft_version'])
            and isinstance(value['summary'],str) and strings(value['evidence'])
            and type(value['needs_human_review']) is bool and isinstance(value['unknowns'],list)
            and all(isinstance(u,dict) and set(u)=={'node_id','field','value','reason'} and all(isinstance(x,str) for x in u.values()) for u in value['unknowns']))
        return value if valid else None
    except (ValueError, TypeError):
        return None


def setup_product(database=None):
    catalog = build_catalog()['nodes']
    selected = [next(n for n in catalog if n['type'] == 'BOMItem' and n['configuration_id'] == CONFIG_L1 and p in n['positions']) for p in ('U4', 'R50')]
    excluded = {n['id'] for n in selected}
    service = Service(database, principals=example_principals())
    must(service.bootstrap('seed-admin', [n for n in catalog if n['id'] not in excluded], request_key='llm-product-seed'))
    return service, selected


def product_task(service, selected, request_key):
    rows = []
    for n in selected:
        comp = must(service.lookup('product-agent', node_id=n['component_id']))['data'][0]
        rows.append({'node_id': n['id'], 'configuration_id': n['configuration_id'], 'component_id': n['component_id'],
            'Quantity': n['quantity'], 'Designation': n['raw_value'], 'Package': comp['package'],
            'Designator': n['positions'], 'population': n['population'], 'evidence_id': n['evidence'][0]})
    return {'task': '기존 L1 구성에 누락된 대표 두 BOM 행의 근거 있는 등록 초안을 준비하세요. 사람 검토에서 멈추세요.',
        'configuration_id': CONFIG_L1, 'data_version': must(service.lookup('product-agent',node_id=CONFIG_L1))['data_version'],
        'request_key': request_key, 'rows': rows,
        'node_contract': {'type': 'BOMItem', 'origin': 'real', 'unit': 'piece',
            'fields': ['id', 'type', 'origin', 'evidence', 'configuration_id', 'component_id', 'quantity', 'unit', 'positions', 'population', 'raw_value']}}


def evaluate_product(service, selected, result, before_version):
    answer = parse_answer(result['text'])
    checks = {'runtime_completed': result['ok'], 'structured_answer': answer is not None,
              'confirmed_version_unchanged': service.lookup('product-agent')['data_version'] == before_version}
    prepares = _successful_calls(result, 'prepare_draft')
    validations = _successful_calls(result, 'validate_draft')
    checks['model_called_prepare_and_validate'] = bool(prepares and validations)
    checks['exact_source_rows_preserved'] = bool(prepares) and all(
        canonical(sorted((op['node'] for op in t['arguments']['operations']), key=lambda n: n['id'])) == canonical(sorted(selected, key=lambda n: n['id'])) for t in prepares)
    if answer:
        stored = service.get_draft('product-agent', answer.get('draft_id'), answer.get('draft_version'))
        d = stored.get('data') if stored['ok'] else None
        checks['answer_links_unapproved_validated_draft'] = bool(d and d['status'] == 'awaiting_review' and not d['reviewer'] and any(
            t['arguments'] == {'draft_id': d['id'], 'version': d['version']} and t['result']['data']['validation']['valid'] for t in validations))
        evidence = {e for n in selected for e in n['evidence']}
        checks['answer_cites_source_rows'] = set(answer.get('evidence', [])) == evidence
        unknown = next(n for n in selected if n['population'] == 'unknown')
        checks['unknown_population_explicit'] = (len(answer['unknowns']) == 1 and
            answer['unknowns'][0]['node_id'] == unknown['id'] and answer['unknowns'][0]['field'] == 'population' and answer['unknowns'][0]['value'] == 'unknown')
        checks['human_review_required'] = answer.get('needs_human_review') is True
    else:
        checks['answer_links_unapproved_validated_draft'] = False
    return checks, answer
