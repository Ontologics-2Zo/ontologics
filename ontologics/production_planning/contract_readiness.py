"""Read-only readiness for order-based estimates, never human approval."""
from copy import deepcopy
import re
from .contract_policy import effective_fields, classify_policy

IDENTITY = ('manufacturer', 'product_model', 'revision', 'variant')
USABLE = {'supported', 'accepted', 'corrected'}
LABELS = dict(manufacturer='제조사', product_model='제품명', revision='설계 개정',
              variant='제품 변형', ordered_quantity='주문 수량', unit='완제품 단위')


def preview_fields(row):
    checks = [c for c in row.get('checks', []) if c.get('decisions')]
    check = checks[-1] if checks else None
    items = []
    for i, item in enumerate(row['result']['items']):
        fields = {}
        for name, original in effective_fields(item).items():
            decision = check['decisions'].get(f'{i}:{name}') if check else None
            state = decision['state'] if decision else original['state']
            value = decision['value'] if decision else original.get('value')
            fields[name] = dict(value=value if state in USABLE else None, state=state,
                source='human_correction' if state == 'corrected' else
                       'reviewed_extraction' if state == 'accepted' else
                       'source_validated' if state == 'supported' else 'unconfirmed',
                original_value=original.get('value'), reason=decision.get('reason', '') if decision else '')
            if name == 'constraints':
                fields[name]['policy'] = classify_policy([value]) if state in USABLE and value else classify_policy([])
        items.append(dict(fields=fields))
    return items, check


def evaluate_contract_readiness(fields: dict, candidates: list, *, basis: dict) -> dict:
    questions = []
    def ask(field, message):
        questions.append(dict(field=field, question=message))
    def value(name):
        f = fields.get(name, {})
        return f.get('value') if f.get('state') in USABLE else None
    for name in IDENTITY:
        if not value(name): ask(name, f'{LABELS[name]} 정보를 확인해 주세요.')
    if fields.get('configuration_id', {}).get('state') in ('conflict', 'invalid', 'unsupported'):
        ask('configuration_id', '원문의 구성 ID가 충돌하거나 근거가 부족합니다. 계약 내용을 확인해 주세요.')
    matches = [c for c in candidates if c.get('exact')]
    if not any(q['field'] in IDENTITY for q in questions) and len(matches) != 1:
        ask('configuration_id', '일치하는 제품 구성을 선택해 주세요.' if matches else '일치하는 등록 제품 구성이 없습니다. 제품 조건과 등록 자료를 확인해 주세요.')
    quantity = value('ordered_quantity')
    if isinstance(quantity, str) and re.fullmatch(r'[0-9]+', quantity) and len(quantity) <= 8:
        quantity = int(quantity)
    if type(quantity) is not int or not 1 <= quantity <= 10000000:
        quantity = None
        ask('ordered_quantity', '주문 수량을 1~10,000,000 사이의 정수로 확인해 주세요.')
    if value('unit') not in ('piece', '대'):
        ask('unit', '완제품 수량의 단위를 확인해 주세요. 자동 계산은 piece 또는 대를 지원합니다.')
    return dict(action='order_material_preview', ready=not questions, quantity=quantity,
                configuration_id=matches[0]['configuration_id'] if len(matches) == 1 else None,
                blocking_questions=questions,
                other_unknowns=[k for k,f in fields.items() if f.get('state') not in USABLE and k not in LABELS and k not in {q['field'] for q in questions}],
                basis=dict(deepcopy(basis), rule_version='contract-readiness-1'),
                unit='piece' if value('unit') in ('piece','대') else None,
                original_unit=value('unit'), confirmation_verified=False)
