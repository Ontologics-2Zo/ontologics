"""Containment request drafts: production stop and shipment hold (C 5th step).

Builds ContainmentRequest candidate nodes and the Markdown documents people review
and send themselves. Nothing here sends mail, controls equipment, approves or applies.
Generated, reviewed, sent, received and applied on site are different states: a new
document is always unreviewed, unsent and unconfirmed on site.
"""
from pathlib import Path

from ..model import ROOT, instant
from .trace import TRACED, trace_lines

PRODUCTION_STOP, SHIPMENT_HOLD = 'production_stop', 'shipment_hold'
REQUEST_DIR = ROOT / 'artifacts/local/claim_response/requests'
RECIPIENT = {PRODUCTION_STOP: '생산관리 담당자', SHIPMENT_HOLD: '창고 및 출고관리 담당자'}
TITLE = {PRODUCTION_STOP: '생산 중단 요청서 검토 초안', SHIPMENT_HOLD: '출고 배송 중단 요청서 검토 초안'}
# Host draft status -> review state shown in the document. Review never implies sending.
REVIEW_LABELS = {None: '미검토', 'awaiting_review': '미검토', 'needs_changes': '수정 필요',
                 'approved': '검토 완료 (그래프 반영 전)', 'applied': '검토·그래프 반영 완료', 'rejected': '반려'}
BASIS_LABELS = {'c_operations_record': 'C 운영 기록 기준 (재고·출하 기록 아님, 공통 재고·출하 타입 반영 전 임시)'}


def _suffix(case):
    return case['id'].split(':', 1)[1]


def line_scope(case, line_id, records, *, as_of=None):
    """Scope for a production stop: the claimed boards, all traced to line_id by production records.

    Uses only production records in the graph, so it does not depend on stock or shipment data.
    """
    traces = trace_lines(case['unit_ids'], records)
    off_line = [t['unit_id'] for t in traces if t['state'] != TRACED or t['production_line_id'] != line_id]
    if off_line:
        return {'state': 'line_not_confirmed', 'node': None, 'unit_ids': off_line,
                'reason': 'claimed boards without a single production record on this line'}
    evidence = sorted({e for t in traces for e in t['evidence']})
    node = {'id': 'quality-scope:' + _suffix(case) + ':line', 'type': 'QualityScope', 'origin': case['origin'],
            'evidence': evidence, 'case_id': case['id'], 'production_line_id': line_id,
            'as_of': as_of or case['reported_at'], 'unit_ids': sorted(case['unit_ids']), 'scope_state': 'candidate'}
    return {'state': 'ready', 'node': node}


def prepare_request(case, scope, kind, *, prepared_at, document_ref, production_line_id=None):
    """ContainmentRequest candidate for one scope of one case."""
    if kind not in (PRODUCTION_STOP, SHIPMENT_HOLD):
        raise ValueError('INVALID_REQUEST_KIND: ' + str(kind))
    if scope.get('case_id') != case['id']:
        raise ValueError('CASE_MISMATCH: the scope belongs to another case')
    if kind == PRODUCTION_STOP and not production_line_id:
        raise ValueError('PRODUCTION_LINE_REQUIRED: a production stop names its line')
    instant(prepared_at)
    node = {'id': 'containment:' + _suffix(case) + ':' + kind, 'type': 'ContainmentRequest',
            'origin': case['origin'], 'evidence': sorted(set(case['evidence']) | set(scope['evidence'])),
            'case_id': case['id'], 'scope_id': scope['id'], 'request_kind': kind,
            'prepared_at': prepared_at, 'document_ref': document_ref}
    if production_line_id:
        node['production_line_id'] = production_line_id
    return node


def serial_ranges(unit_ids):
    """['unit:C-S001', ..., 'unit:C-S120'] -> 'C-S001 ~ C-S120'; gaps become separate ranges."""
    serials = sorted(u.split(':', 1)[1] for u in unit_ids)
    groups, start, prev = [], None, None
    for serial in serials:
        prefix, number = serial[:-3], serial[-3:]
        if prev and prefix == prev[0] and number.isdigit() and int(number) == int(prev[1]) + 1:
            prev = (prefix, number)
            continue
        if start:
            groups.append(start if start == prev[0] + prev[1] else start + ' ~ ' + prev[0] + prev[1])
        start, prev = serial, (prefix, number)
    if start:
        groups.append(start if start == prev[0] + prev[1] else start + ' ~ ' + prev[0] + prev[1])
    return ', '.join(groups)


def _kst(value):
    moment = instant(value)
    return moment.strftime('%Y년 %m월 %d일 %H시 %M분') + ' (UTC' + moment.strftime('%z')[:3] + ')'


def render_request(request, case, scope, *, external_claim_id, configuration_id, basis=None,
                   review_status=None, document_version=1):
    """Markdown review document in the shape of the shared 05/06 examples."""
    kind = request['request_kind']
    units = serial_ranges(scope['unit_ids'])
    claimed = serial_ranges(case['unit_ids'])
    lines = [
        '# ' + TITLE[kind], '',
        '수신 대상 역할: ' + RECIPIENT[kind] + '  ',
        '작성 시점: ' + _kst(request['prepared_at']) + ' (가정)', '',
        '이 문서는 C MVP용 가정 출력이며 실제 클레임 처리·현장 조치가 아니다. '
        '시스템은 이 문서를 송부하지 않는다. 사람이 검토·수정한 뒤 기존 업무 채널로 직접 송부한다.', '',
        '| 상태 | 값 |', '| --- | --- |',
        f'| 문서 버전 | {document_version} |',
        f'| 검토 상태 | {REVIEW_LABELS.get(review_status, review_status)} |',
        '| 송부 상태 | 미송부 |',
        '| 현장 반영 상태 | 미확인 |',
        f'| 고객 클레임 번호 | {external_claim_id} |',
        f"| 사건 ID | {case['id']} |",
        f"| 요청서 ID | {request['id']} |",
        f'| 대상 구성 | {configuration_id} |', '',
    ]
    if kind == PRODUCTION_STOP:
        lines += [
            '## 요청 내용과 근거', '',
            f"- 요청 대상: {request['production_line_id']}.",
            '- 요청 내용: 관련 생산라인의 가동 중단을 검토·조치해 달라는 요청.',
            f"- 고객 관측: {claimed} {len(case['unit_ids'])}대에서 '{case['reported_symptom']}'이라고 신고.",
            f"- 생산 출처: 신고 보드 {len(scope['unit_ids'])}대 모두 보드별 생산 기록에서 위 라인 생산으로 확인 "
            f"(근거: {', '.join(scope['evidence'])}).",
            '- 초기 판단: 불량 원인은 미확정이며 고객 신고만으로 라인의 모든 생산품이 불량이라고 판정한 것은 아님.',
            '- 재개 기준: 조사 결과와 담당자 판단으로 후속 결정. 이 문서는 재개를 승인하지 않음.', '',
        ]
    else:
        lines += [
            '## 요청 내용과 대상 범위', '',
            f"- 생산 출처: {scope['production_line_id']}.",
            f"- 요청 대상: 해당 라인에서 생산돼 {_kst(scope['as_of'])} 기준 보관 중인 {units} ({len(scope['unit_ids'])}대).",
            '- 요청 내용: 위 보관 물량의 출고·배송을 중단 또는 보류하도록 검토·조치 요청.',
            f"- 사유: 같은 라인 생산 보드 {len(case['unit_ids'])}대에 고객 신고('{case['reported_symptom']}')가 있어 "
            '관련 범위 확인과 테스트가 필요함.',
            f"- 구분: {len(scope['unit_ids'])}대는 초기 보류 요청 수량이며 불량 확정·폐기·재생산 수량이 아님.",
            '- 범위 밖: 이미 출하한 보드에 대한 자동 회수나 운송 중 회송 요청은 이 문서에 포함하지 않음.',
            '- 해제 기준: 조사·테스트와 담당자 판정으로 후속 결정. 이 초안은 해제를 승인하지 않음.', '',
            f'고객 신고 보드({claimed})와 보류 대상({units})은 서로 다른 집합이다. '
            '실제 송부할 대상 목록과 기준 시점은 사람이 확인한다.', '',
        ]
        if basis:
            lines += ['대상 목록 근거: ' + '; '.join(BASIS_LABELS.get(b, b) for b in basis) + '.', '']
    lines += [
        '## 사람 검토와 송부 기록', '',
        '| 항목 | 입력 |', '| --- | --- |',
        '| 검토자 | 미지정 |', '| 검토 결과 | ' + REVIEW_LABELS.get(review_status, review_status) + ' |',
        '| 수정 의견 | 미입력 |', '| 확인한 문서 버전 | 미확인 |', '| 송부자·수신 담당 | 미입력 |',
        '| 송부 시각·방법 | 미송부 |', '| 수신자 응답 | 미확인 |', '| 실제 중단·보류 반영 근거 | 미확인 |', '',
        '이 파일을 생성하거나 검토한 것만으로 송부·현장 조치가 완료되지 않는다. '
        '송부·응답·반영 사실은 사람이 확인한 뒤 위 표에 직접 기록한다.', '',
    ]
    return '\n'.join(lines)


def request_filename(case, kind):
    """Same name as the request ID with ':' replaced, e.g. containment_CLM-..._production_stop.md"""
    return ('containment:' + _suffix(case) + ':' + kind).replace(':', '_') + '.md'


def write_request(markdown, filename, *, base_dir=REQUEST_DIR):
    """Write one document under base_dir only (default artifacts/local, ignored by Git)."""
    base = Path(base_dir).resolve()
    path = (base / filename).resolve()
    if path.parent != base or path.suffix != '.md':
        raise ValueError('INVALID_DOCUMENT_PATH: write a .md file directly inside the request folder')
    base.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding='utf-8', newline='\n')
    return path


def prepare_containment(case, line_id, records, hold_result, *, prepared_at, external_claim_id,
                        configuration_id, review_status=None):
    """5-1 and 5-2 together: scope + request nodes and their review documents.

    hold_result is the stored_scope() result from step 4-3. Without a ready hold scope the
    shipment hold request is skipped with its reason; the production stop does not depend on it.
    """
    nodes, documents, skipped = [], {}, []
    line = line_scope(case, line_id, records)
    if line['state'] == 'ready':
        filename = request_filename(case, PRODUCTION_STOP)
        stop = prepare_request(case, line['node'], PRODUCTION_STOP, prepared_at=prepared_at,
                               document_ref=REQUEST_DIR.relative_to(ROOT).as_posix() + '/' + filename,
                               production_line_id=line_id)
        nodes += [line['node'], stop]
        documents[filename] = render_request(stop, case, line['node'], external_claim_id=external_claim_id,
                                             configuration_id=configuration_id, review_status=review_status)
    else:
        skipped.append({'request_kind': PRODUCTION_STOP, 'state': line['state'], 'reason': line['reason']})
    if hold_result.get('state') == 'ready':
        scope = hold_result['node']
        filename = request_filename(case, SHIPMENT_HOLD)
        hold = prepare_request(case, scope, SHIPMENT_HOLD, prepared_at=prepared_at,
                               document_ref=REQUEST_DIR.relative_to(ROOT).as_posix() + '/' + filename)
        nodes += [scope, hold]
        documents[filename] = render_request(hold, case, scope, external_claim_id=external_claim_id,
                                             configuration_id=configuration_id, basis=hold_result.get('basis'),
                                             review_status=review_status)
    else:
        skipped.append({'request_kind': SHIPMENT_HOLD, 'state': hold_result.get('state'),
                        'reason': hold_result.get('reason', 'no ready hold scope')})
    return {'nodes': nodes, 'documents': documents, 'skipped': skipped}
