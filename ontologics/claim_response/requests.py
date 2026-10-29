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


# ------------------------------------------------------------ 6-1 test request
# Team meeting (2026-10-29): C does not run tests or decide dispositions. C prepares the
# request to the test team; the test team returns per-board results and dispositions.
# 5A-0 decided to store it in the common graph: the request is also a TestRequest node
# (common type added by PR #7), submitted as a draft for human review like the other requests.

TEST_REQUEST = 'test_request'
TEST_RESULT_LABELS = ('불합격', '통과', '증상 미재현', '미검사')
TEST_DISPOSITION_LABELS = ('고객 교체', '보관 대체', '출고 허용', '보류', '미결')


def test_request_filename(case):
    """Same naming as the containment documents, e.g. test-request_CLM-..._test_request.md"""
    return ('test-request:' + _suffix(case) + ':' + TEST_REQUEST).replace(':', '_') + '.md'


def test_targets(case, hold_result):
    """Boards to test: the claimed boards always, the held boards only when the hold scope is ready."""
    groups = [{'group': '고객 신고 (회수)', 'unit_ids': sorted(case['unit_ids']), 'basis': '고객 클레임과 사람 확인'}]
    skipped = None
    if hold_result.get('state') == 'ready':
        scope = hold_result['node']
        basis = '; '.join(BASIS_LABELS.get(b, b) for b in hold_result.get('basis') or []) or '그래프 근거'
        groups.append({'group': '보관 보류', 'unit_ids': sorted(scope['unit_ids']), 'basis': basis,
                       'as_of': scope['as_of']})
    else:
        skipped = {'group': '보관 보류', 'state': hold_result.get('state'),
                   'reason': hold_result.get('reason', 'no ready hold scope')}
    return {'groups': groups, 'skipped': skipped}


def render_test_request(case, targets, *, prepared_at, external_claim_id, configuration_id, production_line_id,
                        candidate_result=None, review_status=None, document_version=1):
    """Markdown review document for the test team; C asks for results and dispositions, it decides neither."""
    total = sum(len(g['unit_ids']) for g in targets['groups'])
    lines = [
        '# 테스트 의뢰서 검토 초안', '',
        '수신 대상 역할: 테스트팀  ',
        '작성 시점: ' + _kst(prepared_at) + ' (가정)', '',
        '이 문서는 C MVP용 가정 출력이며 실제 클레임 처리·시험 지시가 아니다. '
        'C는 시험을 수행하거나 조치를 판정하지 않는다. 시스템은 이 문서를 송부하지 않는다. '
        '사람이 검토·수정한 뒤 기존 업무 채널로 직접 송부한다.', '',
        '| 상태 | 값 |', '| --- | --- |',
        f'| 문서 버전 | {document_version} |',
        f'| 검토 상태 | {REVIEW_LABELS.get(review_status, review_status)} |',
        '| 송부 상태 | 미송부 |',
        '| 테스트팀 접수 | 미확인 |',
        f'| 고객 클레임 번호 | {external_claim_id} |',
        f"| 사건 ID | {case['id']} |",
        f'| 대상 구성 | {configuration_id} |',
        f'| 생산라인 | {production_line_id} |', '',
        '## 의뢰 배경', '',
        f"- 고객 신고: {serial_ranges(case['unit_ids'])} {len(case['unit_ids'])}대에서 '{case['reported_symptom']}'이라고 신고.",
        '- 신고 증상은 고객 표현이며 확인된 불량이나 원인이 아님.',
        f'- 생산 출처: 신고 보드 모두 보드별 생산 기록에서 {production_line_id} 생산으로 확인.', '',
        '## 시험 대상', '',
        '| 묶음 | 보드 | 수량 | 대상 근거 |', '| --- | --- | --- | --- |',
    ]
    for group in targets['groups']:
        lines.append(f"| {group['group']} | {serial_ranges(group['unit_ids'])} | {len(group['unit_ids'])}대 | {group['basis']} |")
    lines += ['', f'합계 {total}대. 같은 보드는 한 번만 적는다.', '']
    if targets.get('skipped'):
        skipped = targets['skipped']
        lines += [f"- 보관 보류 보드는 포함하지 않음: 보류 범위 미확정({skipped['state']}).", '']
    if candidate_result and candidate_result.get('state') == 'ready':
        units = candidate_result['node']['unit_ids']
        lines += [f'- 범위 밖: 이미 납품된 같은 라인 보드 {serial_ranges(units)} ({len(units)}대)는 영향 확인 후보이며 '
                  '이번 의뢰에 포함하지 않는다.', '']
    lines += [
        '## 돌려받을 내용', '',
        '보드마다 아래 한 줄을 채워 돌려준다. 시험 방법·판정 기준·조치 판정은 테스트팀이 정한다.', '',
        '| 보드 | 시험 결과 | 조치 판정 | 관측 시각 (UTC offset 포함) | 비고 |',
        '| --- | --- | --- | --- | --- |',
        '| 예: C-S001 | 불합격 | 보관 대체 | 2026-10-27T14:00:00+09:00 | |', '',
        '- 시험 결과: ' + ' / '.join(TEST_RESULT_LABELS) + '. 시험하지 않은 보드는 반드시 미검사로 적는다.',
        '- 조치 판정: ' + ' / '.join(TEST_DISPOSITION_LABELS) + '.',
        '- 표본만 시험한 경우 표본 결과를 나머지 보드에 적용하지 않는다.',
        '- 이 결과로 C가 재생산 필요 수량을 계산해 A 전달용 요청서를 만든다.', '',
        '## 사람 검토와 송부 기록', '',
        '| 항목 | 입력 |', '| --- | --- |',
        '| 검토자 | 미지정 |', '| 검토 결과 | ' + REVIEW_LABELS.get(review_status, review_status) + ' |',
        '| 수정 의견 | 미입력 |', '| 송부자·수신 담당 | 미입력 |', '| 송부 시각·방법 | 미송부 |',
        '| 테스트팀 응답 | 미확인 |', '',
        '이 파일을 생성하거나 검토한 것만으로 송부나 시험이 완료되지 않는다.', '',
    ]
    return '\n'.join(lines)


def prepare_test_request(case, line_id, hold_result, *, prepared_at, external_claim_id, configuration_id,
                         candidate_result=None, review_status=None):
    """6-1: targets, the TestRequest node and the document. Returns {'targets', 'node', 'documents'}.

    The node is a draft candidate; nothing is stored, approved or sent here.
    """
    instant(prepared_at)
    targets = test_targets(case, hold_result)
    filename = test_request_filename(case)
    text = render_test_request(case, targets, prepared_at=prepared_at, external_claim_id=external_claim_id,
                               configuration_id=configuration_id, production_line_id=line_id,
                               candidate_result=candidate_result, review_status=review_status)
    evidence = set(case['evidence'])
    if hold_result.get('state') == 'ready':
        evidence |= set(hold_result['node']['evidence'])
    node = {'id': 'test-request:' + _suffix(case), 'type': 'TestRequest', 'origin': case['origin'],
            'evidence': sorted(evidence), 'case_id': case['id'],
            'unit_ids': [u for group in targets['groups'] for u in group['unit_ids']],
            'prepared_at': prepared_at, 'document_ref': REQUEST_DIR.relative_to(ROOT).as_posix() + '/' + filename}
    return {'targets': targets, 'node': node, 'documents': {filename: text}}


# ------------------------------------------------------------ 6-5 reproduction request for A
# C turns the reproduction quantity (6-3) into a review document for A. A confirmed quantity
# becomes a request; a lower bound becomes a document marked "추가 확인 필요" that must not be
# read as the final number; an undetermined quantity gets no document and no node.

REPRODUCTION_REQUEST = 'reproduction_request'
STATE_LABELS = {'confirmed': '확정', 'lower_bound': '하한 (추가 확인 필요)'}


def reproduction_request_filename(case):
    """e.g. reproduction_CLM-..._reproduction_request.md"""
    return ('reproduction:' + _suffix(case) + ':' + REPRODUCTION_REQUEST).replace(':', '_') + '.md'


def render_reproduction_request(requirement, case, *, prepared_at, external_claim_id, review_status=None,
                                document_version=1):
    """Markdown review document for A. The numbers are copied from the ReproductionRequirement node."""
    r = requirement
    state = r['assessment_state']
    demand = r['customer_replacement_quantity'] + r['stock_replacement_quantity']
    title = '재생산 요청서 검토 초안' if state == 'confirmed' else '재생산 요청서 검토 초안 (하한, 추가 확인 필요)'
    lines = [
        '# ' + title, '',
        '수신 대상 역할: A 담당 (재생산 계획)  ',
        '작성 시점: ' + _kst(prepared_at) + ' (가정)', '',
        '이 문서는 C MVP용 가정 출력이며 실제 생산 지시가 아니다. 시험 결과와 조치 판정은 테스트팀이 정했고 '
        'C는 그 판정을 세어 수량만 계산했다. 시스템은 이 문서를 송부하지 않는다. '
        '사람이 검토·수정한 뒤 기존 업무 채널로 직접 송부한다.', '',
        '| 상태 | 값 |', '| --- | --- |',
        f'| 문서 버전 | {document_version} |',
        f'| 검토 상태 | {REVIEW_LABELS.get(review_status, review_status)} |',
        '| 송부 상태 | 미송부 |',
        '| A 접수 | 미확인 |',
        f'| 고객 클레임 번호 | {external_claim_id} |',
        f"| 사건 ID | {case['id']} |",
        f"| 재생산 요건 ID | {r['id']} |",
        f"| 대상 구성 | {r['configuration_id']} |",
        f'| 수량 상태 | {STATE_LABELS[state]} |', '',
        '## 요청 수량', '',
        '| 항목 | 수량 |', '| --- | --- |',
        f"| 고객 교체 (테스트팀 판정) | {r['customer_replacement_quantity']}대 |",
        f"| 보관 대체 (테스트팀 판정) | {r['stock_replacement_quantity']}대 |",
        f'| 교체 필요 합계 | {demand}대 |',
        f"| 사용할 대체 재고 | {r['available_replacement_quantity']}대 |",
        f"| **재생산 필요 정상 수량** | **{r['required_good_quantity']}대** |",
        f"| 미결 (판정 없음·미검사) | {r['unresolved_quantity']}대 |", '',
        f"계산: max(0, {demand} − {r['available_replacement_quantity']}) = {r['required_good_quantity']}대.", '',
    ]
    if r.get('replacement_unit_ids'):
        lines += [f"- 대체 재고: {serial_ranges(r['replacement_unit_ids'])}. "
                  'C 운영 기록 기준이며 다른 주문 배정 여부는 사람이 확인한다.', '']
    if state == 'lower_bound':
        lines += [
            f"- 이 수량은 **최소 수량**이다. 미결 {r['unresolved_quantity']}대의 판정에 따라 늘어날 수 있다.",
            '- 미결 보드의 결과를 받기 전에는 이 수량을 최종 재생산 수량으로 쓰지 않는다.',
            '- 최종 수량이 정해지면 새 버전의 요청서를 만든다.', '',
        ]
    lines += [
        '## 사람 검토와 송부 기록', '',
        '| 항목 | 입력 |', '| --- | --- |',
        '| 검토자 | 미지정 |', '| 검토 결과 | ' + REVIEW_LABELS.get(review_status, review_status) + ' |',
        '| 수정 의견 | 미입력 |', '| 송부자·수신 담당 | 미입력 |', '| 송부 시각·방법 | 미송부 |',
        '| A 응답 | 미확인 |', '',
        '이 파일을 생성하거나 검토한 것만으로 송부나 생산 계획 반영이 완료되지 않는다.', '',
    ]
    return '\n'.join(lines)


def prepare_reproduction_request(case, reproduction, *, prepared_at, external_claim_id, review_status=None):
    """6-5: reproduction_requirement() result -> {'state', 'node', 'documents', 'skipped'}.

    undetermined -> no node and no document, only the reason. Otherwise the node gets document_ref.
    """
    instant(prepared_at)
    if reproduction['node'] is None:
        return {'state': reproduction['state'], 'node': None, 'documents': {},
                'skipped': {'request_kind': REPRODUCTION_REQUEST, 'state': reproduction['state'],
                            'reason': reproduction['reason']}}
    filename = reproduction_request_filename(case)
    node = dict(reproduction['node'], document_ref=REQUEST_DIR.relative_to(ROOT).as_posix() + '/' + filename)
    text = render_reproduction_request(node, case, prepared_at=prepared_at, external_claim_id=external_claim_id,
                                       review_status=review_status)
    return {'state': reproduction['state'], 'node': node, 'documents': {filename: text}, 'skipped': None}
