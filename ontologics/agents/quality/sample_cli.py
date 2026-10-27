"""Role-owned definitions for the limited CLI example; not full service evaluation."""
import json
from ...demo import LOT, example_queries, must
from ...tool_specs import QUALITY_TOOLS
from ..sample_support import COMMON, _successful_calls, strings, integer

QUALITY_PROMPT = COMMON + '''
너는 최소 Quality 예제다. 주어진 A/B 조건을 그대로 compare_investigation에 넣어 두 버전을 비교한다.
unit:U104와 해당 ComponentLot을 lookup하고 component_usage로 실제 설계 BOM 근거도 확인한다.
조회 결과에 나온 Evidence를 lookup해 원본 위치를 확인한다. A/B의 before/after 판정을 계산해서 바꾸지 않는다.
보완 후 A 제외는 안전 판정이 아니며 B 포함은 실제 가동을 뜻하지 않는다.
이전 버전은 개발 기준 자료, 이후 보완은 호스트가 개발용 사람 역할로 검토·반영한 시험 데이터다.
최종 JSON: {"unit_id":"unit:U104","lot_id":로트ID,"before_version":정수,"after_version":정수,
"answers":[{"mode":"A","before":status,"after":status,"explanation":한국어,"evidence":[ID]},
{"mode":"B","before":status,"after":status,"explanation":한국어,"evidence":[ID]}],
"real_design_evidence":[BOM Evidence ID],"synthetic_operations":true,
"not_established":["failure_cause","safety","operating_time","cross_revision_component_equivalence"],
"limitations":[한국어확인한계]}.'''


def parse_answer(text):
    """Reject malformed role output without crashing the example report."""
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            return None
        required = {'unit_id','lot_id','before_version','after_version','answers','real_design_evidence','synthetic_operations','not_established','limitations'}
        valid = (set(value)==required and all(isinstance(value[k],str) for k in ('unit_id','lot_id'))
            and all(integer(value[k]) for k in ('before_version','after_version'))
            and type(value['synthetic_operations']) is bool
            and all(strings(value[k]) for k in ('real_design_evidence','not_established','limitations'))
            and isinstance(value['answers'],list) and all(isinstance(a,dict)
                and set(a)=={'mode','before','after','explanation','evidence'} and strings(a['evidence'])
                and all(isinstance(a[k],str) for k in ('mode','before','after','explanation')) for a in value['answers']))
        return value if valid else None
    except (ValueError, TypeError):
        return None


def quality_task(before_version, after_version):
    queries = {mode: dict(q, unit_ids=['unit:U104']) for mode, q in example_queries().items()}
    return {'task': 'U104의 보완 전후 A/B 결과와 서로 다른 이유를 근거로 설명하세요.',
        'lot_id': LOT, 'queries': queries, 'before_version': before_version, 'after_version': after_version}


def evaluate_quality(service, task, result, before_version):
    answer = parse_answer(result['text'])
    checks = {'runtime_completed': result['ok'], 'structured_answer': answer is not None,
              'confirmed_version_unchanged': service.lookup('quality-agent')['data_version'] == before_version,
              'no_mutation_tool': all(t['name'] in QUALITY_TOOLS for t in result.get('tool_trace', []))}
    calls = _successful_calls(result, 'compare_investigation')
    for mode, q in task['queries'].items():
        expected_args = {'query': q, 'before_version': task['before_version'], 'after_version': task['after_version']}
        checks[f'{mode}_exact_conditions_and_versions'] = any(t['arguments'] == expected_args for t in calls)
    checks['all_comparisons_match_requested_conditions'] = bool(calls) and all(t['arguments'] in [
        {'query': q, 'before_version': task['before_version'], 'after_version': task['after_version']} for q in task['queries'].values()] for t in calls)
    if answer:
        checks['identity_and_versions'] = all(answer.get(k) == v for k,v in {'unit_id':'unit:U104','lot_id':LOT,
            'before_version':task['before_version'],'after_version':task['after_version']}.items())
        answers = answer.get('answers', [])
        checks['A_B_before_after_correct'] = isinstance(answers, list) and len(answers) == 2 and {
            (a.get('mode'),a.get('before'),a.get('after')) for a in answers if isinstance(a,dict)} == {
                ('A','needs_confirmation','excluded'),('B','needs_confirmation','included')}
        checks['operations_evidence_cited'] = all(set(a['evidence']) == {'evidence:demo:initial','evidence:demo:supplement'} for a in answers) and len(answers)==2
        unit = must(service.lookup('quality-agent',node_id='unit:U104'))['data'][0]
        lot = must(service.lookup('quality-agent',node_id=LOT))['data'][0]
        usage = must(service.component_usage('quality-agent',lot['component_id']))['data']['items']
        expected_evidence = {e for n in usage if n['configuration_id']==unit['configuration_id'] and 'U4' in n['positions'] for e in n['evidence']}
        checks['real_BOM_evidence_linked'] = bool(_successful_calls(result,'component_usage')) and set(answer.get('real_design_evidence',[])) == expected_evidence
        checks['uncertainty_and_synthetic_boundary'] = answer.get('synthetic_operations') is True and set(answer.get('not_established',[])) >= {'failure_cause','safety','operating_time','cross_revision_component_equivalence'}
    else: checks['A_B_before_after_correct'] = False
    return checks, answer
