"""Small notebook presentation over Demo; no transport, DB or approval policy.

Construction and overview are inert. Console.run is an explicitly enabled human
interaction. Only the current Demo's reports are displayed; history is not read.
"""
from __future__ import annotations

from html import escape
import json
import re
from pathlib import Path

from .b_notebook import CASE_DETAILS


def _text(value):
    return escape(str(value), quote=True)


def _json(value):
    return _text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _details(label, value, *, opened=False):
    return '<details' + (' open' if opened else '') + '><summary>' + _text(label) + '</summary><pre>' + _json(value) + '</pre></details>'


def _panel(content):
    return ('<div style="font-size:15px;line-height:1.65;max-width:1000px;overflow-wrap:anywhere">'
            + content + '</div>')


def plan_html(demo):
    plan = demo.plan_summary()
    readiness = demo.live_readiness()
    rows = ''.join('<tr><td>' + str(index) + '</td><td>' + _text(row['case'])
        + '</td><td>' + _text(row['question']) + '</td></tr>'
        for index, row in enumerate(plan['representatives'], 1))
    issues = ''.join('<li>' + _text(issue) + '</li>' for issue in readiness['issues'])
    return _panel('<h3>이번 새 실행 계획</h3><p>고정 가상 업무 사례 ' + str(len(demo.cases))
        + '대표 · 대표당 최대 4요청 · 전체 최대 ' + str(plan['total_request_cap'])
        + '요청</p><p>모델 ' + _text(plan['model'])
        + ' · 출력 1,024토큰 · 요청당 30초 · 자동 재시도 0회</p>'
        + '<p>기본 셀은 실행을 시작하지 않습니다. 무료 모의 결과와 실제 LLM 결과를 각각 표시합니다.</p>'
        + '<p>실제 호출 준비: ' + ('통과' if readiness['ready'] else '차단 — 공통 연결 검토 필요')
        + '</p><ul>' + issues + '</ul><table><thead><tr><th>순서</th><th>대표</th>'
        + '<th>질문</th></tr></thead><tbody>' + rows + '</tbody></table>')


def inputs_html(demo, case=None):
    sections = []
    for case in (demo.cases if case is None else (case,)):
        source = demo.input_preview(case=case)
        sections.append('<h4>' + _text(source['label']) + '</h4><p>' + _text(source['question'])
            + '</p><details><summary>입력 자료 · 고정 가상 원문 · 모델 답변 아님</summary><pre>'
            + _text(source['source_text']) + '</pre><p>' + _text(source['source_path'])
            + ' · SHA-256 ' + _text(source['source_sha256']) + '</p></details>')
    return _panel('<h3>고정 사례 확인</h3>' + ''.join(sections))


def _korean_fields(value, path=''):
    """Show model-authored Korean fields without promoting echoed source text."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'source_text', 'source', 'input', 'input_text'}:
                continue
            yield from _korean_fields(child, path + ('.' if path else '') + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _korean_fields(child, path + '[' + str(index) + ']')
    elif isinstance(value, str) and re.search('[가-힣]', value):
        yield path, value


def _model_facts(value, path=''):
    """Expose exact model numbers, booleans and ISO times, never echoed input."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'source_text', 'source', 'input', 'input_text'}:
                continue
            yield from _model_facts(child, path + ('.' if path else '') + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _model_facts(child, path + '[' + str(index) + ']')
    elif type(value) in (int, float, bool) or (
            isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', value)):
        yield path, value


def _model_facts_html(value):
    facts = list(_model_facts(value))
    if not facts:
        return ''
    rows = ''.join('<tr><td>' + _text(path) + '</td><td>'
                   + _json(fact) + '</td></tr>' for path, fact in facts)
    return ('<h4>모델 구조화 답 · 수량·시각·조건</h4><table><thead><tr>'
            '<th>모델의 원래 필드</th><th>모델의 원래 값</th></tr></thead><tbody>'
            + rows + '</tbody></table>')


def result_html(result):
    if not result.get('available'):
        return '<p>이번 실행의 새 결과가 아직 없습니다.</p>'
    actual = result.get('execution_mode') == 'actual_paid'
    label = '이번 실행의 실제 LLM 응답' if actual else '이번 무료 HTTP 모의 응답 · 실제 LLM 성공 아님'
    model_text = result.get('model_text') or ''
    try:
        model_value = json.loads(model_text)
    except (ValueError, TypeError):
        model_value = model_text
    paragraphs = ''.join('<p><small>' + _text(path) + '</small><br><strong>'
        + _text(text) + '</strong></p>' for path, text in _korean_fields(model_value))
    if not paragraphs:
        paragraphs = '<p>한국어 설명 필드가 없습니다. 모델 원문을 확인하고 의미 검토에서 판단하세요.</p>'
    answer = result.get('answer') or {}
    evidence = answer.get('evidence', answer.get('evidence_ids', [])) if isinstance(answer, dict) else []
    tools = [dict(name=row.get('name'), ok=(row.get('result') or {}).get('ok'),
                  data_version=(row.get('result') or {}).get('data_version'))
             for row in result.get('tool_trace', [])]
    return _panel('<h3>' + _text(CASE_DETAILS.get(result.get('case'), (result.get('case'),))[0])
        + '</h3><p>' + label + '</p>' + paragraphs
        + (_model_facts_html(model_value) if not str(result.get('case', '')).startswith('cq01_') else '')
        + '<p>자동 검사: ' + ('통과' if result.get('automatic_passed') else '실패')
        + ' · HTTP 요청 ' + str(result.get('http_requests', 0))
        + '/4 · 실제 API 요청 ' + str(result.get('actual_api_attempts', 0)) + '</p>'
        + '<p>핵심 근거: ' + _text(', '.join(map(str, evidence)) if isinstance(evidence, list) else evidence)
        + '</p><p>도구 실행: ' + _text(' → '.join(str(row['name']) for row in tools)) + '</p>'
        + _details('모델 원문 JSON / 원문 응답', model_value)
        + _details('Python 해석 결과 · 모델 원문과 구분', answer)
        + _details('도구 기록·서비스 근거·자동 검사·사용량', dict(
            tool_trace=result.get('tool_trace', []), service_result=result.get('service_result'),
            checks=result.get('checks'), usage=result.get('usage'), report_sha256=result.get('report_sha256'))))


def status_html(demo, mode, status=None):
    status = demo.status(mode) if status is None else status
    events = status.get('events', [])
    starts = [row for row in events if row.get('event') == 'started']
    current = starts[-1]['case'] if starts else demo.cases[0]
    own_requests = sum(row.get('event') == 'reserved' and row.get('case') == current for row in events)
    accepted = sum(row.get('event') == 'accepted' for row in events)
    failed = 'campaign_failure.json' in status or 'execution_failure.json' in status or any(
        row.get('event') == 'stopped' for row in events)
    shutdown = status.get('shutdown.json', {})
    if status.get('overall_passed'):
        state, action = '완료 · 서버 종료 확인', '전체 결과를 확인하세요.'
    elif failed:
        state, action = '실패 / 중단', '후속 유료 호출을 진행하지 않습니다. 종료 증거를 확인하세요.'
    elif status.get('running'):
        state, action = '진행 / 사람 검토 대기', '화면이 자동 갱신되며 검토할 때 입력창이 나타납니다.'
    elif status.get('started'):
        state, action = '실행 종료 · 완료 여부 확인 필요', '상세 종료 상태를 확인하세요. 이 실행은 재시작하지 않습니다.'
    else:
        state, action = '시작 전', '고정 사례 확인 후 무료 사전검사를 선택하세요.'
    return _panel('<h3>' + ('무료 모의 사전검사' if mode == 'free' else '새 실제 실행')
        + ' — ' + state + '</h3><p>현재 대표 ' + _text(current) + ' · 요청 '
        + str(own_requests) + '/4 · 전체 예약 ' + str(status.get('requests_reserved', 0))
        + '/' + str(4 * len(demo.cases)) + ' · 수락 ' + str(accepted) + '/' + str(len(demo.cases))
        + '</p><p>다음 행동: ' + action + '</p><p>서버 종료 확인: '
        + ('완료' if shutdown.get('server_stopped') is True else '아직 확인되지 않음') + '</p>'
        + _details('요청·종료·오류 상세', status))


class Console:
    """One driver cell; human input is never synthesized or preselected."""

    def __init__(self, demo, *, read=input, show=None):
        self.demo, self.read = demo, read
        self.show = show or self._display
        self._display_handle = None

    def _display(self, html):
        from IPython.display import HTML, display
        if self._display_handle is None:
            self._display_handle = display(HTML(html), display_id=True)
        else:
            self._display_handle.update(HTML(html))

    def start_live(self):
        if self.demo.status('live').get('started'):
            self.show(status_html(self.demo, 'live'))
            return
        readiness = self.demo.live_readiness()
        if not readiness['ready']:
            self.show(plan_html(self.demo))
            return
        if not self.demo.status('free').get('overall_passed'):
            self.show(plan_html(self.demo) + '<p>먼저 무료 사전검사를 완료하고 서버 종료를 확인하세요.</p>')
            return
        token = self.demo.approval_token()
        self.show(plan_html(self.demo) + '<p>아래 입력은 이 실행에만 적용됩니다. 승인할 때만 직접 입력하세요: <strong>'
                  + _text(token) + '</strong></p>')
        confirmation = self.read('이번 유료 실행 승인 (빈 입력은 취소): ')
        if confirmation != token:
            return
        self.demo.start_live_with_token(confirmation)
        self.monitor('live')

    def semantic_review(self, request):
        self.show(result_html(self.demo.result('live', case=request['case']))
            + inputs_html(self.demo, case=request['case'])
            + '<h3>별도 한국어 의미 검토</h3><p>원문·질문·답변·근거·가정·미확인을 직접 대조하세요. '
            '이 판단은 CQ01 초안의 업무 승인을 대신하지 않습니다.</p>' + _details('검토할 정확 보고서 결합', request))
        decision = self.read('의미 검토: 통과 / 거절 / 빈 입력=중단: ')
        if decision not in ('통과', '거절'):
            self.demo.stop('live'); return False
        notes = self.read('직접 작성한 한국어 검토 내용: ')
        self.demo.submit_review(notes, passed=decision == '통과', case=request['case'])
        return True

    def draft_review(self, request):
        self.show(result_html(self.demo.result('live', case='cq01_intake'))
            + '<h3>CQ01 정확 초안의 별도 업무 승인</h3><p>작성자 자신은 승인할 수 없습니다. '
            '아래 개발 검토자 표시는 실제 인증 계정이 아닙니다.</p><p>초안 '
            + _text(request['draft_id']) + ' · 버전 ' + _text(request['version']) + '<br>content_hash '
            + _text(request['content_hash']) + '<br>작성자 ' + _text(request['author'])
            + ' · 검토자 ' + _text(request['reviewer']) + '</p>'
            + _details('정확 초안 내용·변경 operations', request['draft_material'], opened=True))
        decision = self.read('정확 초안 업무 검토: 승인 / 거절 / 빈 입력=중단: ')
        if decision not in ('승인', '거절'):
            self.demo.stop('live'); return False
        notes = self.read('직접 작성한 한국어 업무 검토 내용: ')
        self.demo.submit_draft_review(notes, decision='approve' if decision == '승인' else 'reject')
        return True

    def monitor(self, mode):
        try:
            while True:
                status = self.demo.status(mode)
                if not status.get('running'):
                    self.show_results(mode)
                    return status
                if mode == 'live':
                    review = self.demo.review_request()
                    if review is not None:
                        if not self.semantic_review(review):
                            self.show_results(mode); return self.demo.status(mode)
                    else:
                        draft = self.demo.draft_review_request()
                        if draft is not None:
                            if not self.draft_review(draft):
                                self.show_results(mode); return self.demo.status(mode)
                        else:
                            self.show(status_html(self.demo, mode, status))
                else:
                    self.show(status_html(self.demo, mode, status))
                self.demo.wait(mode, seconds=1)
        except BaseException:
            self.demo.stop(mode)
            raise

    def show_results(self, mode):
        self.show(status_html(self.demo, mode) + ''.join(
            result_html(self.demo.result(mode, case=case)) for case in self.demo.cases))

    def run(self):
        """Explicit interactive menu. Repeated calls reuse this one-shot Demo."""
        self.show(plan_html(self.demo))
        while True:
            action = self.read('1 사례 / 2 무료검사 / 3 실제실행 / 4 전체결과 / 종료: ')
            if action == '1':
                self.show(inputs_html(self.demo))
            elif action == '2':
                status = self.demo.status('free')
                if not status.get('started'):
                    self.demo.start_free()
                self.monitor('free')
            elif action == '3':
                self.start_live()
            elif action == '4':
                mode = 'live' if self.demo.status('live').get('started') else 'free'
                self.show_results(mode)
            else:
                for mode in ('free', 'live'):
                    if self.demo.status(mode).get('running'):
                        self.demo.stop(mode)
                return


def notebook_document():
    """Return a fresh reproducible notebook document; never read or write files.

    The local ignored notebook can be regenerated without overwriting another
    notebook or adding private outputs to Git. Creation is an explicit caller
    action, separate from this pure template function.
    """
    sources = (
        ('markdown', '# CQ01~06 새 실제 실행 · 간소화 화면\n\n고정 가상 사례 10대표를 확인하고 **무료 사전검사 → 이번 실행 승인 → 한국어 답변·근거·사람 검토 → 전체 결과·종료** 순서로 진행합니다.\n\n기존 세 시연 노트북과 저장 출력은 보존했습니다. 이 파일은 과거 결과를 재생하지 않습니다. `무료 HTTP 모의 응답`과 `이번 실행의 실제 LLM 응답`을 별도로 표시합니다.\n\n**현재 유료 실행은 공통 AgentSpec 도메인 연결·비용 정책 검토 때문에 차단됩니다.** 무료 검사는 실행할 수 있으며, 제한을 완화하거나 차단을 제거하는 셀은 없습니다. 무료 통과가 새로운 실제 LLM 성공을 뜻하지 않습니다.\n\n대표당 최대 4요청 / 전체 최대 40요청, 고정 `gpt-5.4-mini-2026-03-17`, 출력 최대 1,024토큰, 요청당 30초, 자동 재시도 0회입니다.\n'),
        ('code', "# 저장소의 .venv Python 커널을 선택하세요. 아래 경로는 이 로컬 저장소입니다.\nimport sys\nfrom pathlib import Path\nROOT = Path(__REPOSITORY_ROOT__)\nif str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))\nfrom examples.order_fulfillment.b_notebook import Demo, CASES\nfrom examples.order_fulfillment.b_notebook_ui import Console, plan_html\nfrom IPython.display import HTML, display\n\n# 셀을 다시 실행해도 기존 세션을 새로 만들지 않습니다.\nif 'demo' not in globals():\n    demo = Demo(cases=CASES)\nif 'console' not in globals():\n    console = Console(demo)\ndisplay(HTML(plan_html(demo)))\n"),
        ('markdown', '## 한 셀에서 진행\n\n아래 `ENABLE_CONTROLS`을 직접 `True`로 바꾸고 셀을 실행하면 메뉴가 열립니다. 기본 `Run All`은 API·DB·서버·입력창을 시작하지 않습니다.\n\n1. `1`로 고정 원문과 질문을 확인합니다. 원문은 **입력 자료**로 표시됩니다.\n2. `2`로 새 격리 DB의 무료 사전검사를 시작합니다. 상태가 자동 갱신됩니다.\n3. `3`은 연결 준비가 통과된 뒤에만 이 실행에 결합한 짧은 승인 입력을 받습니다. 지금은 차단 이유를 보여줍니다.\n4. 실제 실행 시 각 한국어 답변·핵심 근거 바로 아래에서 의미 검토 판단과 한국어 검토 내용을 직접 입력합니다. 통과 내용은 자동 작성되지 않습니다.\n5. CQ01 접수는 의미 검토와 별도로 정확 초안 ID·버전·content_hash·작성자·검토자를 확인하고 업무 승인/거절을 입력합니다. 작성자 자신은 승인할 수 없으며 개발 신원은 실제 인증을 뜻하지 않습니다.\n6. 마지막에 `4`로 전체 결과와 서버 종료 여부를 확인하고 `종료`를 입력합니다. 실행 중 인터럽트도 소유 서버 종료를 요청합니다. 상세 JSON·도구 기록은 접어서 제공합니다.\n\n같은 세션은 한 번만 시작할 수 있습니다. 실패한 실행을 재개하지 않습니다. 새 시연은 이 커널의 실행 종료를 확인한 뒤 **커널 재시작 → 새 무료 검사 → 새 승인**이 필요합니다.\n'),
        ('code', "ENABLE_CONTROLS = False\nif ENABLE_CONTROLS:\n    console.run()\nelse:\n    print('대기: 기본 실행은 시작하지 않았습니다. 진행하려면 ENABLE_CONTROLS=True로 이 셀을 실행하세요.')\n"),
        ('markdown', '## 종료 확인\n\n메뉴의 전체결과와 아래 상태에서 `shutdown.json.server_stopped=true`, 프로세스 종료 코드, 완료 여부를 함께 확인합니다. `overall_passed`는 모의/실제 모드별 결과이며, 무료 모의 성공을 실제 LLM 성공으로 계산하지 않습니다.\n\n보존·연결 검토 근거는 `docs/order_fulfillment/B_MAIN_COMPAT_PLAN.md` 및 이번 최종 보고를 따릅니다. 공통 검토가 끝나기 전 기존 노트북/CLI를 통해 유료 차단을 우회하지 마세요.\n'),
        ('code', "# 읽기 전용: 이 세션의 결과 위치와 종료 상태만 확인합니다.\nfor mode in ('free', 'live'):\n    state = demo.status(mode)\n    print(mode, '시작:', state['started'], '실행 중:', state['running'],\n          '종료 코드:', state['exit_code'], '완료:', state['overall_passed'],\n          '서버 종료:', state.get('shutdown.json', {}).get('server_stopped'),\n          '\\n결과:', state['output'])\n"),
    )
    cells = []
    repository = str(Path(__file__).absolute().parents[2])
    for kind, source in sources:
        source = source.replace('__REPOSITORY_ROOT__', repr(repository))
        cell = dict(cell_type=kind, metadata={}, source=source.splitlines(True))
        if kind == 'code':
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    return dict(nbformat=4, nbformat_minor=5, metadata=dict(
        kernelspec=dict(display_name='Python 3 (ontologics .venv)', language='python', name='python3'),
        language_info=dict(name='python', version='3.14')), cells=cells)
