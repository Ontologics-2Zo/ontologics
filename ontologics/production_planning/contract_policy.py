"""Conservative, evidence-preserving contract policy classification, not permission.

Unknown phrasing and mixed scopes are deliberately not turned into authorization.
Only validated source fields enter resolution; original records remain immutable.
"""
from copy import deepcopy
import re

VERSION = 'contract-policy-1'
LABELS = {'approval_required':'구매자 승인 필요', 'prohibited':'대체 불가',
          'conditional':'조건 내 허용', 'unknown':'미확인'}

# Full clauses, not keyword counts: e.g. '승인 없이 허용하지 않는다' is
# approval-required, never an unconditional prohibition or permission.
PATTERNS = {
    'approval_required': [
        r'(?:설계 개정 변경 및 대체 부품 사용:\s*)?구매자의 사전 서면 승인 없이는 허용하지 않는다',
        r'(?:부품 변경|대체 부품 사용) 시 구매자의 사전 서면 승인을 받아야 한다',
        r'변경안 제출만으로 변경이 승인된 것으로 보지 않으며, 구매자의 서면 승인 전까지 본 계약의 제품 구성을 유지한다',
    ],
    'prohibited': [
        r'지정된 제조사 및 부품번호 외 (?:대체 부품 )?사용을 금지한다',
        r'(?:본 계약에서는 |본 계약의 )?대체 부품 사용을 (?:금지한다|허용하지 않는다)',
    ],
    'conditional': [
        r'합의된 대체품 목록에 등재된 부품은 별도 승인 없이 사용할 수 있다',
        r'합의된 전기적·기계적 규격과 품질 기준을 충족하는 동등품을 사용할 수 있다',
        r'사전 승인된 대체품 목록에 포함된 부품에 한하여 별도 승인 없이 대체할 수 있다',
    ],
}


def classify_policy(texts):
    states = set()
    unknown = False
    for text in dict.fromkeys(texts):
        # Multiple sentences can be extracted as one literal quote.
        clauses = [x.strip() for x in re.split(r'[.。\n]+', text) if x.strip()]
        if not clauses: unknown = True
        for clause in clauses:
            matches = {state for state, patterns in PATTERNS.items()
                       if any(re.fullmatch(p, clause) for p in patterns)}
            if len(matches) != 1: unknown = True
            states.update(matches)
    status = next(iter(states)) if len(states) == 1 and not unknown else 'unknown'
    return dict(status=status, label=LABELS[status], rule_version=VERSION,
                reason='mixed_or_conflicting_conditions' if len(states)>1 else
                       'unrecognized_or_missing' if status=='unknown' else 'explicit_source_condition',
                conditions=list(dict.fromkeys(texts)), approval_verified=False)


def resolve_constraint(field):
    result = deepcopy(field)
    texts = [c['value'] for c in field.get('claims', []) if isinstance(c.get('value'),str)]
    eligible = field.get('state') in ('supported','conflict') and bool(texts) and all(
        c.get('evidence') and all(e.get('quote') == c.get('value') and
            type(e.get('start')) is int and type(e.get('end')) is int and
            e['end']-e['start'] == len(c['value']) for e in c['evidence'])
        for c in field.get('claims', []))
    policy = classify_policy(texts) if eligible else classify_policy([])
    result['policy'] = policy
    if eligible and policy['status'] != 'unknown':
        result.update(state='supported', value='\n'.join(dict.fromkeys(texts)))
    return result


def effective_fields(item):
    return {name:resolve_constraint(f) if name=='constraints' else f
            for name,f in item['fields'].items()}
