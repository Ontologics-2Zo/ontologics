"""Disposable business drafts over Task03–07, not persisted documents or authority.

Pure dictionaries carry reported evidence, never authenticated identity. The saved
entrypoint refreshes demand and public queries through the existing host. Markdown
exports are review artifacts; no document repository, review or dispatch API exists.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import html
import json
import os
from pathlib import Path
import re

from .scheduling import build_saved_schedule_review, estimate_material_ready, estimate_production_schedule
from .substitution import build_substitution_review, load_revision_evidence, query_substitution, LEFT, RIGHT

RULE_VERSION = 'documents-0.1'
KINDS = ('production_plan', 'substitution_review', 'purchase_request')
TEXT_FIELDS = frozenset({'note', 'request_message'})
INPUT_FIELDS = frozenset({'initial_quantity', 'resource', 'schedules', 'schedule_context',
    'offers', 'cost_context', 'initial_conditions', 'remaining_setup_minutes'})
STATE = dict(review='unreviewed', dispatch='unsent', purchasing_received='unknown',
             ordered='unknown', received='unknown')


def _serial(value):
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, dict): return {k: _serial(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [_serial(v) for v in value]
    return deepcopy(value)


def _json(value):
    return json.dumps(_serial(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def _same(a, b):
    return _json(a) == _json(b)


def _without(value, *keys):
    return {k: deepcopy(v) for k, v in value.items() if k not in keys}


def _provenance(value):
    """Keep every envelope/error/run/version, omit duplicate public graph data."""
    if isinstance(value, list): return [_provenance(x) for x in value]
    if not isinstance(value, dict): return deepcopy(value)
    if 'ok' in value and 'run_id' in value:
        return _without(value, 'data')
    return {k: _provenance(v) for k, v in value.items() if k != 'configurations'}


def _check_query(value, version):
    if isinstance(value, list):
        for child in value: _check_query(child, version)
    elif isinstance(value, dict):
        if value.get('is_preview') or value.get('errors') or value.get('conflicts'):
            raise ValueError('Successful non-preview public query required')
        if 'ok' in value and (value['ok'] is not True or type(value.get('data_version')) is not int or value['data_version'] != version):
            raise ValueError('Exact successful public version required')
        for child in value.values(): _check_query(child, version)


def _notes(edits):
    if not isinstance(edits, dict) or set(edits) - TEXT_FIELDS:
        raise ValueError('Only note/request_message are editable free text')
    if any(not isinstance(v, str) or len(v) > 10000 or any(ord(c) < 32 and c not in '\n\r\t' for c in v) for v in edits.values()):
        raise ValueError('Bounded plain text required')
    return deepcopy(edits)


def _escape(value):
    text = '미정' if value is None else str(value)
    # No links, headings, HTML, table rows or formatting supplied by inputs.
    # One pass: never escape '#' inside an entity introduced by this function.
    text = ''.join('&#' + str(ord(char)) + ';' if char in '\\`*_{}[]()#+-.!|~'
                   else html.escape(char, quote=True) for char in text)
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')


def _render(document):
    p = document['payload']; b = p['binding']; q = p['quantities']; s = p['schedule']; scope = p['scope']
    lines = ['# ' + document['title'], '',
        '**임시 초안 · 미검토 · 미송부 · 실행 아님. 영속 저장/확정 문서 버전은 없습니다.**', '',
        '임시 초안 식별자: ' + document['draft_id'],
        '정확 내용 SHA-256: ' + document['content_sha256'] + ' (영속 버전·서명 아님)', '',
        '작성 목적: ' + _escape(p['purpose']), '',
        '공개 구성: ' + _escape(b['configuration_id']) + ' / 데이터 버전: ' + _escape(b['data_version']),
        '선택 시나리오: ' + _escape(b['scenario']) + ' — 원래와 가상 잔여는 독립 비교이며 합산하지 않습니다.', '',
        '| 주문(대) | 신규 검토 N(대) | 선행 I(대) | 잔여 R(대) |', '| --- | --- | --- | --- |',
        '| ' + ' | '.join(_escape(q[k]) for k in ('target_quantity', 'planned_input_quantity', 'initial_quantity', 'remaining_quantity')) + ' |', '',
        f"전체 입력 BOM {scope['bom_rows']}행 / 실장 미확인 {scope['unknown_population_rows']}행 / 재고 미확인 {scope['unknown_stock_components']}부품.",
        '범위 내 조건부 상한: ' + _escape(scope['conditional_upper_bound']) + ' / 전체 자재 한계: ' + _escape(scope['whole_bom_material_limit']), '',
        '**별도 가정**: 아래 선행 시간은 초기 전체 자재 준비를 따로 가정한 계산입니다. 위 미확인 범위를 해결하지 않습니다.', '',
        '| 구간 | 보드 수 | 작업 시작 | 가공 시작 | 완료 |', '| --- | --- | --- | --- | --- |']
    for label, key in (('선행 별도 가정', 'initial'), ('잔여 조건부 예상', 'remaining')):
        job = s[key]
        lines.append('| ' + label + ' | ' + ' | '.join(_escape(job[k]) for k in ('quantity', 'work_start', 'processing_start', 'completion')) + ' |')
    lines += ['', '전체 자재 사용 가능: ' + _escape(p['material_ready']['ready_at']),
        '잔여 재준비(분): ' + _escape(s['remaining_setup_minutes']),
        '고객 납기 원문: ' + _escape(p['demand'].get('contract_item_candidate', {}).get('delivery_due')),
        '고객 도착 준수: 미정. 포장·출하·운송·배송지·도착 시각을 생산 완료에서 추정하지 않습니다.', '',
        '## 원 자재와 가상 잔여', '',
        'q=보드당 부품 수, D=소요, A=가용, S=부족. 부품 단위는 piece. positions를 다시 곱하지 않습니다.', '',
        '| 정확 부품 ID / BOM 행·위치·원문 | q | 원 D | 원 A | 원 S | 가상 사용 | 잔여 A | 잔여 D | 잔여 S |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in p['components']:
        identity = row['component_id'] + '\n' + '\n'.join(_json(bom) for bom in row['bom_rows'])
        lines.append('| ' + _escape(identity) + ' | ' + ' | '.join(_escape(row[k]) for k in
            ('q', 'D', 'A', 'S', 'virtual_used_quantity', 'remaining_available_quantity', 'remaining_required_quantity', 'remaining_shortage')) + ' |')
    sub = p['substitution']; proc = p['procurement']
    lines += ['', '## 대체 검토 범위', '', _escape(sub['status']), '', _escape(sub['reviewed_scope']), '',
        '후보 발견·문서 검토는 기술 적합성, 계약 변경, 고객 합의, 구성 확인 또는 발주 승인이 아닙니다.', '',
        '## 정확 원 부품 구매 검토', '',
        '양의 부족 목록 밖의 항목은 부족 0이나 준비 완료가 아닙니다. 구매량 0도 자재 투입 준비 완료가 아닙니다.', '',
        '| 정확 부품 ID | 부족(개) | 구매량(개) | 통화 | 소계 | 상태 |', '| --- | --- | --- | --- | --- | --- |']
    positive = {x['component_id'] for x in proc['purchase_review']}
    for item in proc['items']:
        if item['component_id'] in positive:
            lines.append('| ' + ' | '.join(_escape(item[k]) for k in ('component_id', 'shortage_quantity', 'purchase_quantity', 'currency', 'subtotal', 'status')) + ' |')
    lines += ['', '알려진 통화별 부분 소계: ' + _escape(_json(proc['known_item_totals'])),
        '전체 통화별 금액: ' + _escape(None if proc['total_by_currency'] is None else _json(proc['total_by_currency'])), '',
        '구매 검토 요청 조건: 정확 견적·MOQ/배수·공급량·유효 가격·포함 비용, 전체 범위 및 자재 일정이 계산상 충족되어야 합니다. 충족해도 발주서가 아닙니다.', '',
        '## 자유 의견 — 계산·근거·상태를 변경하지 않는 미검증 문구', '']
    for key, value in document['editorial'].items():
        lines += [key + ': ' + _escape(value), '']
    lines += ['## 근거·미확인·진단 및 정확 구조화 payload', '',
        'real BOM은 입력 근거이며 가상 생산 사실의 증거가 아닙니다. synthetic 운영 입력·파생 결과·미확인을 구분합니다. null=미정, false=권한 없음. 아래 데이터는 실행 지시가 아닙니다.', '']
    raw = json.dumps(_serial(p), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    # A longer fence prevents source/note text from terminating the data block.
    fence = '`' * max(3, 1 + max((len(x) for x in re.findall(r'`+', raw)), default=0))
    lines += [fence + 'json', raw, fence, '']
    return '\n'.join(lines)


def _seal(document):
    d = deepcopy(document)
    core = {k: d[k] for k in ('kind', 'title', 'payload', 'editorial', 'state', 'document_ref', 'document_version')}
    d['content_sha256'] = hashlib.sha256(_json(core).encode('utf-8')).hexdigest()
    d['draft_id'] = 'temporary:' + d['content_sha256']
    d['markdown'] = _render(d)
    return d


def _validate_document(document):
    if (not isinstance(document, dict) or document.get('kind') not in KINDS
            or document.get('state') != STATE or document.get('document_ref') is not None
            or document.get('document_version') is not None):
        raise ValueError('Only unreviewed, unsent, unpersisted Task08 drafts supported')
    _notes(document.get('editorial'))
    try:
        if not _same(_seal(document), document): raise ValueError('Document payload/body/digest mismatch')
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete Task08 draft') from exc


def build_business_documents(context: dict, results: dict) -> list[dict]:
    """Revalidate a Task07 schedule, bind exact scenario, project three drafts.

    context: configuration_id/data_version/demand_source/scenario, optional
    comparisons/evidence/constraints (Task05 inputs), editorial. results: schedule,
    optional query. No caller-provided document state, title or candidate output.
    """
    allowed = {'configuration_id', 'data_version', 'demand_source', 'scenario',
               'comparisons', 'evidence', 'constraints', 'editorial'}
    if not isinstance(context, dict) or set(context) - allowed:
        raise ValueError('Unsupported document context field')
    if not isinstance(results, dict) or set(results) - {'schedule', 'query'}:
        raise ValueError('Use schedule and optional query results')
    c, s = deepcopy(context), deepcopy(results.get('schedule'))
    if not isinstance(s, dict): raise ValueError('Successful Task07 schedule required')
    try:
        checked = estimate_production_schedule(s['plan'], s['resource'], s['material_ready'],
            initial_conditions=s['initial_conditions'], remaining_setup_minutes=s['remaining_setup_minutes'])
        if not _same(checked, s): raise ValueError('Schedule derivation mismatch')
        p = s['plan']; m = p['input_materials']; source = p['demand']['demand_source']
        for key, value in (('configuration_id', p['configuration_id']), ('data_version', p['data_version']), ('demand_source', source)):
            if not _same(c.get(key), value): raise ValueError('Document binding mismatch: ' + key)
        if c.get('scenario') not in ('original', 'virtual_remaining'): raise ValueError('Exact single scenario required')
        for key in ('comparisons', 'evidence', 'constraints'):
            if not isinstance(c.get(key, []), list): raise ValueError(key + ' must be a list')
        ready = s['material_ready']
        if c['scenario'] == 'original':
            args = deepcopy(ready['inputs']); args['materials_or_plan'] = m; args['scenario'] = 'original'
            args['context']['scenario'] = 'original'
            if args['cost_context'] is not None: args['cost_context']['scenario'] = 'original'
            # Remaining delivery evidence is never relabelled as original evidence.
            ready = estimate_material_ready(**args)
        selected = ready['procurement']['materials']
        sub = build_substitution_review(selected, c.get('comparisons', []), c.get('evidence', []), c.get('constraints', []))
        if not c.get('comparisons'):
            sub['reviewed_scope'] = 'No public substitution comparison supplied; only exact shortage and supplied evidence reviewed'
        proc = ready['procurement']
        positive = [i for i in proc['items'] if i['shortage_quantity'] is not None and i['shortage_quantity'] > 0]
        eligible = bool(positive) and proc['total_by_currency'] is not None and ready['ready_at'] is not None
        eligible = eligible and not ready['diagnostics'] and not proc['diagnostics'] and all(
            i['status'] == 'estimated' and i['supply_state'] == 'reported_sufficient' and not i['issues'] for i in positive)
        rows = {row['id']: row for comp in m['components'] for row in comp['bom_rows']}
        rows.update({x['row']['id']: x['row'] for x in m['excluded_bom']})
        queries = results.get('query') or {}
        _check_query(queries, p['data_version'])
        nodes = (queries.get('lookup_response') or {}).get('data') or []
        basis_ids = {e for row in rows.values() for e in row.get('evidence', [])}
        basis = [n for n in nodes if n.get('id') in basis_ids or n.get('type') in ('SourceDocument', 'Configuration')]
        evidence_sources = {n.get('source_id') for n in nodes if n.get('id') in basis_ids}
        basis += [n for n in nodes if n.get('id') in evidence_sources and n not in basis]
        payload = dict(purpose='Planning, substitution and original-component purchasing review; no execution',
            binding={k: deepcopy(c[k]) for k in ('configuration_id', 'data_version', 'scenario', 'demand_source')},
            origin=dict(document='derived_review', bom='input_basis_not_production_evidence',
                stock='reported_observations; inspect source/state', initial='separate_assumed_scenario',
                unknown='null remains unknown', authentication='pure caller evidence; host entrypoint required for current demand'),
            quantities={k: deepcopy(p[k]) for k in ('target_quantity', 'target_quantity_basis', 'planned_input_quantity', 'initial_quantity', 'remaining_quantity', 'unit')},
            scope=dict(bom_rows=len(rows), bom_complete=m['bom_complete'],
                unknown_population_rows=sum(row.get('population') == 'unknown' for row in rows.values()),
                unknown_stock_components=sum(row['A'] is None for row in m['components']),
                conditional_upper_bound=p['conditional_upper_bound'], whole_bom_material_limit=p['whole_bom_material_limit'],
                evaluated_component_ids=p['evaluated_component_ids'], excluded_bom=m['excluded_bom']),
            demand=deepcopy(p['demand']), components=deepcopy(p['components']), stock=deepcopy(m['stock']),
            procurement=_without(proc, 'materials', 'substitution_review'),
            material_ready=_without(ready, 'procurement', 'inputs'),
            schedule=_without(s, 'plan', 'material_ready'),
            substitution=_without(sub, 'original_materials', 'comparisons'),
            substitution_query_provenance=_provenance(sub['comparisons']),
            comparison_results=[deepcopy(q.get('comparison_response')) for q in sub['comparisons']],
            query_provenance=_provenance(queries), public_basis=basis,
            calculation_inputs=_without(ready['inputs'], 'materials_or_plan'),
            diagnostics={k: deepcopy(m[k]) for k in ('errors', 'conflicts', 'unresolved', 'questions', 'limitations')},
            rule_versions=dict(documents=RULE_VERSION, materials=m['calculation']['rule_version'],
                production=p['calculation']['rule_version'], procurement=proc['calculation']['rule_version'], scheduling=s['calculation']['rule_version']),
            authority=dict(production_authorized=False, purchase_authorized=False, resource_reserved=False,
                customer_arrival_compliance=None, alternative_ranking=None),
            purchase_request_eligible=bool(eligible), persistence='unsupported; export is not storage')
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete Task03–07 input') from exc
    editorial = _notes(c.get('editorial', {}))
    purchase_title = '구매 검토 요청' if eligible else '견적·납기 확인 요청'
    if not positive:
        purchase_title = ('구매 신청 불필요' if proc['items'] and not selected['unresolved']
            and all(i['status'] == 'not_required' for i in proc['items']) else '구매 대상 미정')
    titles = ('생산 계획 문서', '대체 검토안', purchase_title)
    return [_seal(dict(kind=kind, title=title, payload=_serial(payload), editorial=editorial,
        state=deepcopy(STATE), document_ref=None, document_version=None)) for kind, title in zip(KINDS, titles)]


def edit_document_text(document, edits):
    """New disposable draft; free notes cannot overwrite locked data or state."""
    _validate_document(document)
    edited = deepcopy(document); edited['editorial'].update(_notes(edits))
    return _seal(edited)


def build_saved_business_documents(workflow, host, context, *, scenario='virtual_remaining', editorial=None,
                                   **calculation_arguments):
    """Refresh existing demand/public APIs. No document persistence or SQL."""
    result = build_saved_schedule_review(workflow, host, context, **calculation_arguments)
    result['documents'] = None
    if result['schedule'] is None: return result
    p = result['plan']; selected = p['input_materials'] if scenario == 'original' else p['remaining_materials']
    comparisons = []
    evidence = []
    if p['configuration_id'] == LEFT:
        evidence = [load_revision_evidence()]
        with host.request(context) as session:
            for row in selected['components']:
                if row['S'] is not None and row['S'] > 0:
                    comparisons.append(query_substitution(session, configuration_id=LEFT, component_id=row['component_id'],
                        candidate_configuration_id=RIGHT, data_version=p['data_version']))
    c = dict(configuration_id=p['configuration_id'], data_version=p['data_version'], scenario=scenario,
        demand_source=p['demand']['demand_source'], comparisons=comparisons, evidence=evidence,
        constraints=[p['demand']['demand_source'].get('permissions', {})], editorial=editorial or {})
    result['documents'] = build_business_documents(c, {'schedule': result['schedule'], 'query': result['query']})
    return result


def recalculate_business_documents(previous, workflow, host, context, *, calculation_arguments, edits):
    """Fresh Task02–07 call for allowed input edits; never patch result numbers.

    Original demand/configuration/version/source must stay fixed. A changed demand
    requires a separate new document operation, not an edit to its old draft.
    Failure returns documents=None; the caller's original objects stay untouched.
    """
    if not isinstance(previous, list) or [d.get('kind') for d in previous] != list(KINDS):
        raise ValueError('Exact three previous drafts required')
    for d in previous: _validate_document(d)
    binding = previous[0]['payload']['binding']
    if any(not _same(d['payload'], previous[0]['payload']) for d in previous):
        raise ValueError('Previous document bundle mismatch')
    if not isinstance(edits, dict) or set(edits) - INPUT_FIELDS:
        raise ValueError('Edit calculation inputs only; demand/configuration changes require a new demand')
    source = binding['demand_source']; args = deepcopy(calculation_arguments)
    for key, value in (('demand_id', source.get('demand_id')), ('item_id', source.get('item_id')),
        ('data_version', binding['data_version']), ('expected_revision', source.get('revision')),
        ('expected_target_sha256', source.get('target_sha256'))):
        if value is None or key in args and not _same(args[key], value):
            raise ValueError('Previous current-demand binding required: ' + key)
        args[key] = value
    args.update(deepcopy(edits))
    try:
        result = build_saved_business_documents(workflow, host, context, scenario=binding['scenario'], **args)
        if result['documents'] is not None:
            if not _same(result['documents'][0]['payload']['binding'], binding):
                result['documents'] = None
                result['errors'].append(dict(code='DOCUMENT_BINDING_CHANGED', message='Reopen changed demand as a new draft'))
            else:
                result['documents'] = [edit_document_text(new, old['editorial']) for new, old in zip(result['documents'], previous)]
    except ValueError as exc:
        result = dict(documents=None, errors=[dict(code='DOCUMENT_RECALCULATION_FAILED', message=str(exc))])
    result['original_preserved'] = True
    return result


def export_document_markdown(document, output_directory, filename, *, expected_sha256):
    """Export this exact temporary content, exclusively within a trusted directory.

    Filename is one .md leaf; directory must already exist and is supplied by the
    host, never extracted from source text. Exclusive creation prevents overwrite,
    including symlink targets. content_sha256 is not a persisted version/signature.
    """
    _validate_document(document)
    if expected_sha256 != document['content_sha256']: raise ValueError('Exact content digest required')
    if (not isinstance(filename, str) or not re.fullmatch(r'[^/\\\x00-\x1f]+\.md', filename)
            or filename in ('.md', '..md')):
        raise ValueError('One safe Markdown filename required')
    root = Path(output_directory).resolve(strict=True)
    if not root.is_dir(): raise ValueError('Existing trusted output directory required')
    # Open relative to an already-open directory to avoid directory substitution.
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        out = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(document['markdown'])
    finally:
        os.close(fd)
    return str(root / filename)
