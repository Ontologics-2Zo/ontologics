"""Conservative, evidence-preserving contract policy classification, not permission.

Unknown phrasing and mixed scopes are deliberately not turned into authorization.
Only validated source fields enter resolution; original records remain immutable.
"""
from copy import deepcopy
import re
from .contract_dates import resolve_deadline

VERSION = 'contract-policy-3'
LABELS = {'approval_required':'구매자 승인 필요', 'prohibited':'대체 불가',
          'conditional':'조건 내 허용', 'unknown':'미확인'}

# Full clauses, not keyword counts: e.g. '승인 없이 허용하지 않는다' is
# approval-required, never an unconditional prohibition or permission.
PATTERNS = {
    'approval_required': [
        r'(?:설계 개정 변경 및 대체 부품 사용:\s*)?구매자의 사전 서면 승인 없이는 허용하지 않는다',
        r'(?:부품 변경|대체 부품 사용) 시 구매자의 사전 서면 승인을 받아야 한다',
        r'승인 (?:회신|통지)을 받기 전에는 기존에 지정된 부품을 사용한다',
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


# Exclude only complete, narrowly defined logistics/administrative statements.
# Keep them in the audit projection. Unknown exceptions are never discarded.
NON_POLICY = [
    r'(?:납기 변경:\s*)?(?:공급자는 지연 예상 사유와 변경 일정을 구매자에게 제시하고, )?구매자의 사전 서면 동의를 받은 경우에만 변경 일정을 적용한다',
    r'수량·가격·납기·제품 구성의 변경은 계약번호와 변경 항목을 명시한 변경 합의서로 관리한다',
    r'공급자는 생산 착수 전에 적용 BOM 및 도면의 개정과 부품 장착 사양을 확인하고, 불명확한 사항은 구매자와 서면으로 합의한다',
    r'공급자는 보유 완제품 및 생산 계획을 고려하여 공급 물량을 확보하되, 본 계약의 품질과 납품 조건을 준수하여야 한다',
    r'분할 납품:\s*허용하지 않음',
]


def non_policy(text):
    return any(re.fullmatch(p, text.strip().rstrip('.。')) for p in NON_POLICY)


def classify_claims(claims):
    """Combine grounded clause interpretations; never infer an approval record.

    Deterministic known clauses take precedence over model labels. Unknown labels and
    mixed scopes/states remain unknown. Text/evidence are kept unchanged.
    """
    excluded = [dict(text=c['value'], reason='logistics_or_administration') for c in claims if non_policy(c['value'])]
    claims = [c for c in claims if not non_policy(c['value'])]
    result = classify_policy([c['value'] for c in claims])
    result['excluded_clauses'] = excluded
    result['method'] = 'literal_rules'
    if not any('policy_status' in c for c in claims): return result
    states = set()
    mismatch = False
    for claim in claims:
        literal_result = classify_policy([claim['value']])
        literal = literal_result['status']
        if literal_result['reason'] == 'mixed_or_conflicting_conditions': mismatch = True
        hint = claim.get('policy_status', literal)
        if hint not in LABELS: mismatch = True
        # Known full clauses are interpreted by deterministic rules; the model
        # supplies semantics only for other wording. Conflicting clauses still
        # produce different states and therefore remain unknown.
        states.add(literal if literal != 'unknown' else hint if hint in LABELS else 'unknown')
    status = next(iter(states)) if len(states)==1 and not mismatch else 'unknown'
    result.update(status=status, label=LABELS[status], method='model_interpretation',
                  reason='model_literal_disagreement' if mismatch else
                         'mixed_or_unconfirmed_conditions' if status=='unknown' else 'grounded_model_interpretation')
    return result


def resolve_constraint(field):
    result = deepcopy(field)
    texts = [c['value'] for c in field.get('claims', []) if isinstance(c.get('value'),str)]
    eligible = field.get('state') in ('supported','conflict') and bool(texts) and all(
        c.get('evidence') and all(e.get('quote') == c.get('value') and
            type(e.get('start')) is int and type(e.get('end')) is int and
            e['end']-e['start'] == len(c['value']) for e in c['evidence'])
        for c in field.get('claims', []))
    policy = classify_claims(field['claims']) if eligible else classify_policy([])
    result['policy'] = policy
    if eligible and policy.get('excluded_clauses') and not policy['conditions']:
        result.update(state='missing', value=None)
    elif eligible and policy['status'] != 'unknown':
        result.update(state='supported', value='\n'.join(dict.fromkeys(texts)))
    return result


def effective_fields(item):
    return {name:resolve_constraint(f) if name=='constraints' else resolve_deadline(f) if name=='delivery_due' else f
            for name,f in item['fields'].items()}
