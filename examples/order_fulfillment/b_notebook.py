"""Notebook controls for fresh fixed-case CQ01–06 runs through the existing protected CLI.

Importing or constructing a Demo does not read credentials, start processes, or
read historical results. Only an exact interactive run-bound confirmation can
open the live path. The notebook never implements its own HTTP or DB transport.
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from .b_run_isolation import ROOT, SAFE_INPUTS, checked_path, clean_environment, sha, source_digest
from .b_run_transport import CASES, ENDPOINT, MODEL, validate_authorization

CASE = 'cq04_normal'
QUESTION = '이 회차는 기한까지 몇 대 도착하고 몇 대 부족한가요? 전량 도착 예상과 근거·조건도 설명해줘.'


# Labels and source previews explain the fixed inputs; the protected CLI owns tasks.
CASE_DETAILS = {
    'cq01_intake': ('CQ01 접수', '계약 원문을 읽고 미승인 등록 초안을 준비하세요.', 'contract'),
    'cq01_promise': ('CQ01 약속', '승인된 정확 버전의 가상 계약 약속만 조회하세요.', 'contract'),
    'cq02_normal': ('CQ02 정상', 'B-01 1회차의 현황 기준 시점 물량과 잔여 약속을 근거와 함께 설명해줘.', 'fulfillment'),
    'cq03_normal': ('CQ03 정상', '이 작업은 언제 생산 완료할 것으로 예상되나요? 근거와 조건도 설명해줘.', 'production'),
    'cq04_normal': ('CQ04 기본', QUESTION, 'delivery'),
    'cq04_mixed': ('CQ04 혼합', QUESTION, 'delivery_mixed'),
    'cq05_normal': ('CQ05 기본', '이 회차의 고객 회신과 새 출고·운송 검토 초안, 확인 사항을 근거와 함께 알려줘.', 'delivery'),
    'cq05_mixed': ('CQ05 혼합', '이 회차의 고객 회신과 새 출고·운송 검토 초안, 확인 사항을 근거와 함께 알려줘.', 'delivery_mixed'),
    'cq06_defects11': ('CQ06 불량 증가', '두 승인 버전의 수량·시각·입력 변경과 검토 초안 비교', 'recalculation'),
    'cq06_transport2h': ('CQ06 운송 변경', '두 승인 버전의 수량·시각·입력 변경과 검토 초안 비교', 'recalculation'),
}


def _read(path: Path):
    path = checked_path(path)
    if not path.is_file() or path.stat().st_size > 8_000_000:
        raise ValueError('bounded owned JSON required')
    return json.loads(path.read_text(encoding='utf-8'))


def _write_new(path: Path, value):
    """Publish complete JSON without ever replacing an existing submission."""
    checked_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)  # atomic visibility, exclusive destination
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class Demo:
    """One-shot ordered session; every new paid run needs a fresh free gate."""

    def __init__(self, cases=None):
        selected = (CASE,) if cases is None else tuple(cases)
        if (not selected or any(case not in CASES for case in selected)
                or len(set(selected)) != len(selected)
                or selected != tuple(case for case in CASES if case in selected)):
            raise ValueError('nonempty canonical ordered unique cases required')
        self._cases = selected
        token = uuid.uuid4().hex[:16]
        base = ROOT / 'artifacts/local/order_fulfillment'
        self.control = base / ('notebook_' + token + '_control')
        self.outputs = {mode: base / ('notebook_' + token + '_' + mode)
                        for mode in ('free', 'live')}
        self.processes = {}
        self.started = set()
        self._draft = None
        self._controls_created = False

    def _controls(self):
        checked_path(self.control)
        if not self._controls_created:
            self.control.mkdir(mode=0o700)
            self._controls_created = True
            (self.control / 'home').mkdir(mode=0o700)
            (self.control / 'temp').mkdir(mode=0o700)

    def _mode(self, mode):
        if mode not in self.outputs:
            raise ValueError('explicit free/live mode required')
        return self.outputs[mode]

    def running(self):
        return any(process.poll() is None for process in self.processes.values())

    @property
    def cases(self):
        return self._cases

    def _case(self, case):
        if case is None and len(self.cases) == 1:
            return self.cases[0]
        if case not in self.cases:
            raise ValueError('explicit case from this session required')
        return case

    def plan_summary(self):
        return dict(cases=list(self.cases), representatives=[
            dict(case=case, label=CASE_DETAILS[case][0], question=CASE_DETAILS[case][1], max_requests=4)
            for case in self.cases], total_request_cap=4 * len(self.cases),
            cq01_request_cap=4 * sum(case.startswith('cq01_') for case in self.cases),
            model=MODEL, max_output_tokens=1024, timeout_seconds=30, automatic_retries=0)

    def input_preview(self, case=None):
        case = self._case(case)
        label, question, source = CASE_DETAILS[case]
        relative = 'data/order_fulfillment/sources/b_demo_' + source + '_01.md'
        path = checked_path(ROOT / relative)
        if sha(path) != SAFE_INPUTS[relative] or path.stat().st_size > 64_000:
            raise ValueError('fixed reviewed synthetic input changed')
        return dict(case=case, label=label, question=question, origin='synthetic',
                    source_path=relative, source_sha256=SAFE_INPUTS[relative],
                    source_text=path.read_text(encoding='utf-8'))

    def _start(self, mode, extra=()):
        output = checked_path(self._mode(mode))
        if mode in self.started or output.exists() or self.running():
            raise RuntimeError('duplicate/restart/concurrent execution refused')
        executable = ROOT / '.venv/bin/python'
        if not executable.is_file():
            raise RuntimeError('existing project Python environment required')
        self._controls()
        self.started.add(mode)  # a failed launch is not silently retried
        args = [str(executable), '-B', '-m', 'examples.order_fulfillment.b_run',
                mode, *[part for case in self.cases for part in ('--case', case)],
                '--output', str(output), *extra]
        try:
            with (self.control / (mode + '.stdout.txt')).open('xb') as stdout, \
                    (self.control / (mode + '.stderr.txt')).open('xb') as stderr:
                self.processes[mode] = subprocess.Popen(
                    args, cwd=ROOT, env=clean_environment(self.control),
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    start_new_session=True)
                atexit.register(self._stop_at_exit, mode)
            _write_new(self.control / (mode + '.owner.json'),
                       dict(pid=self.processes[mode].pid, mode=mode, output=str(output)))
        except BaseException:
            self.stop(mode)
            raise
        return self.status(mode)

    def start_free(self):
        return self._start('free')

    def wait(self, mode, seconds=30):
        self._mode(mode)
        if not 0 <= seconds <= 60:
            raise ValueError('wait must be bounded to 0..60 seconds')
        process = self.processes.get(mode)
        if process is None:
            return self.status(mode)
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            pass  # waiting never retries or starts another request
        except KeyboardInterrupt:
            self.stop(mode)
            raise
        return self.status(mode)

    def status(self, mode):
        output = self._mode(mode)
        process = self.processes.get(mode)
        result = dict(mode=mode, output=str(output), started=mode in self.started,
                      running=process is not None and process.poll() is None,
                      exit_code=None if process is None else process.poll())
        for name in ('shutdown.json', 'free_gate.json' if mode == 'free' else 'completion.json',
                     'execution_failure.json', 'campaign_failure.json'):
            path = output / name
            if path.exists():
                try:
                    result[name] = _read(path)
                except json.JSONDecodeError:
                    result[name] = {'being_written': True}
        ledger = output / 'campaign/http_budget.jsonl'
        if ledger.exists():
            checked_path(ledger)
            if ledger.stat().st_size > 1_000_000:
                raise ValueError('ledger size exceeded')
            rows = []
            for line in ledger.read_text(encoding='utf-8').splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    break  # only display completely written rows while running
            result['requests_reserved'] = sum(r['event'] == 'reserved' for r in rows)
            result['events'] = [{k: row[k] for k in ('event', 'at', 'case', 'request', 'status', 'usage')
                                 if k in row} for row in rows]
        result['overall_passed'] = (
            result.get('completion.json' if mode == 'live' else 'free_gate.json', {}).get('passed') is True
            and result.get('shutdown.json', {}).get('server_stopped') is True
            and result['exit_code'] == 0)
        return result

    def result(self, mode, case=None):
        case = self._case(case)
        path = self._mode(mode) / 'campaign/reports' / (case + '.json')
        if not path.exists():
            return {'available': False, 'mode': mode, 'case': case, 'message': '이번 실행의 새 결과가 아직 없습니다.'}
        try:
            report = _read(path)
        except json.JSONDecodeError:
            process = self.processes.get(mode)
            if process is not None and process.poll() is None:
                return {'available': False, 'mode': mode, 'case': case, 'message': '이번 보고서를 기록 중입니다.'}
            raise
        expected = 'actual_paid' if mode == 'live' else 'offline_http_double'
        if report.get('execution_mode') != expected or report.get('case') != case:
            raise ValueError('current run execution mode/case mismatch')
        return dict(available=True, case=case, execution_mode=report['execution_mode'], task=report['task'],
                    actual_api_attempts=report['actual_api_attempts'],
                    http_requests=report['http_requests'], usage=report['usage'],
                    automatic_passed=report['passed'], checks=report['checks'],
                    model_text=report['runtime']['text'], tool_trace=report['runtime'].get('tool_trace', []),
                    answer=report['answer'], service_result=report['service_query_raw'],
                    report_sha256=sha(path), overall=self.status(mode))

    def results(self, mode):
        return {case: self.result(mode, case=case) for case in self.cases}

    def _free_gate(self):
        status = self.status('free')
        if not status['overall_passed']:
            raise RuntimeError('completed stopped free precheck required')
        output = self.outputs['free']
        gate = _read(output / 'free_gate.json')
        if (gate.get('mode') != 'free' or gate.get('cases') != list(self.cases)
                or gate.get('source_sha256') != source_digest()
                or gate.get('actual_api_attempts') != 0
                or gate.get('summary_sha256') != sha(output / 'campaign/summary.json')
                or gate.get('shutdown_sha256') != sha(output / 'shutdown.json')):
            raise ValueError('free precheck binding changed')
        return gate

    def approval_draft(self):
        """An unapproved plan, never a reusable authorization from a previous run."""
        gate = self._free_gate()
        draft = dict(approved=False, source='explicit_user_confirmation_with_mandatory_free_gate',
                     run_id=str(self.outputs['live']), cases=list(self.cases), per_case_request_cap=4,
                     total_request_cap=4 * len(self.cases),
                     cq01_request_cap=4 * sum(case.startswith('cq01_') for case in self.cases), model=MODEL, endpoint=ENDPOINT,
                     max_output_tokens=1024, timeout_seconds=30, automatic_retries=0,
                     input_and_money_caps_unfixed_acknowledged=False,
                     free_gate_sha256=sha(self.outputs['free'] / 'free_gate.json'),
                     source_sha256=gate['source_sha256'])
        if self._draft is not None and self._draft != draft:
            raise ValueError('approval plan changed; create a new session')
        if self._draft is None:
            self._controls()
            _write_new(self.control / 'authorization.unapproved.json', draft)
            self._draft = draft
        return dict(draft)

    def approval_phrase(self):
        scope = CASE_DETAILS[self.cases[0]][0] if len(self.cases) == 1 else ','.join(self.cases)
        return (scope + ' ' + str(len(self.cases)) + '대표 최대' + str(4 * len(self.cases))
                + '요청 유료 실행 승인 / ' + MODEL
                + ' / 출력1024·30초·재시도0 / 입력·금액 별도상한 없음 / 실행ID: '
                + str(self.outputs['live']))

    def start_live(self, confirmation):
        # This string must be entered by the human presenter. The notebook never
        # supplies it programmatically and has no live call in its default cells.
        if confirmation != self.approval_phrase():
            raise PermissionError('fresh exact human run-bound confirmation required')
        if 'live' in self.started or self.running():
            raise RuntimeError('live execution already started or another run active')
        auth = self.approval_draft()
        auth.update(approved=True, input_and_money_caps_unfixed_acknowledged=True)
        validate_authorization(auth, run_id=str(self.outputs['live']), cases=list(self.cases),
                               free_gate_sha256=auth['free_gate_sha256'],
                               source_sha256=auth['source_sha256'])
        path = self.control / 'authorization.json'
        _write_new(path, auth)
        return self._start('live', ('--authorization', str(path), '--free-gate',
                                   str(self.outputs['free'] / 'free_gate.json')))

    def review_request(self, case=None):
        output = self.outputs['live']
        candidates = self.cases if case is None else (self._case(case),)
        for selected in candidates:
            stem = selected + '-separate_korean_semantic_review'
            path = output / 'submissions' / (stem + '.requested.json')
            if not path.exists() or (output / 'submissions' / (stem + '.json')).exists():
                continue
            report = output / 'campaign/reports' / (selected + '.json')
            try:
                request = _read(path)
                value = _read(report)
            except json.JSONDecodeError:
                process = self.processes.get('live')
                if process is not None and process.poll() is None:
                    return None
                raise
            if (request.get('case') != selected or request.get('report') != str(report)
                    or request.get('kind') != 'separate_korean_semantic_review'
                    or request.get('report_sha256') != sha(report)
                    or request.get('bound_output_hash') != value['runtime']['output_hash']
                    or value.get('execution_mode') != 'actual_paid'
                    or value.get('case') != selected or value.get('passed') is not True
                    or not value.get('checks')
                    or any(flag is not True for flag in value['checks'].values())):
                raise ValueError('review is not bound to this current successful report')
            return request
        return None

    def submit_review(self, notes, *, passed=False, case=None):
        """Human review only: no default pass, generated notes, or Agent approval."""
        self._active_review_worker()
        request = self.review_request(case=case)
        if request is None:
            raise RuntimeError('no pending exact-report review')
        self._human_notes(notes)
        if type(passed) is not bool:
            raise ValueError('explicit boolean review decision required')
        review = dict(request, passed=passed, notes=notes)
        path = self.outputs['live'] / 'submissions' / (request['case'] + '-separate_korean_semantic_review.json')
        _write_new(path, review)
        return dict(submitted=True, case=request['case'], passed=passed, report_sha256=request['report_sha256'])

    def _active_review_worker(self):
        process = self.processes.get('live')
        if process is None or process.poll() is not None:
            raise RuntimeError('no active live review worker')

    @staticmethod
    def _human_notes(notes):
        if not isinstance(notes, str) or not re.search('[가-힣]', notes):
            raise ValueError('Korean human review notes required')

    def draft_review_request(self):
        """Display exact CQ01 draft material; a development identity, not login."""
        if not {'cq01_intake', 'cq01_promise'} <= set(self.cases):
            return None
        output = self.outputs['live']
        stem = 'cq01_intake-cq01_draft_review'
        path = output / 'submissions' / (stem + '.requested.json')
        if not path.exists() or (output / 'submissions' / (stem + '.json')).exists():
            return None
        report = output / 'campaign/reports/cq01_intake.json'
        semantic_path = output / 'submissions/cq01_intake-separate_korean_semantic_review.json'
        try:
            request, value, semantic = _read(path), _read(report), _read(semantic_path)
        except json.JSONDecodeError:
            process = self.processes.get('live')
            if process is not None and process.poll() is None:
                return None
            raise
        answer = value.get('answer') or {}
        drafts = value.get('after_business_rows', {}).get('drafts', [])
        matching = [row for row in drafts if row.get('id') == request.get('draft_id')
                    and row.get('version') == request.get('version')]
        draft = matching[0] if len(matching) == 1 else {}
        if (request.get('kind') != 'cq01_draft_review' or request.get('case') != 'cq01_intake'
                or request.get('development_fixture') is not True
                or value.get('case') != 'cq01_intake' or value.get('execution_mode') != 'actual_paid'
                or value.get('passed') is not True or not value.get('checks')
                or any(flag is not True for flag in value['checks'].values())
                or semantic.get('passed') is not True
                or semantic.get('kind') != 'separate_korean_semantic_review'
                or semantic.get('case') != 'cq01_intake' or semantic.get('report') != str(report)
                or semantic.get('report_sha256') != sha(report)
                or semantic.get('bound_output_hash') != value['runtime']['output_hash']
                or request.get('draft_id') != answer.get('draft_id')
                or type(request.get('version')) is not int
                or request['version'] != answer.get('draft_version')
                or not re.fullmatch('[a-f0-9]{64}', str(request.get('content_hash', '')))
                or request.get('content_hash') != draft.get('content_hash')
                or request.get('author') != draft.get('author')
                or request.get('author') != 'user:task11-author'
                or request.get('reviewer') != 'user:task11-reviewer'
                or request['reviewer'] == request['author']
                or draft.get('status') != 'awaiting_review' or not draft.get('operations')):
            raise ValueError('draft review is not bound to the current exact intake draft')
        return dict(request, draft_material=draft, report_sha256=sha(report))

    def submit_draft_review(self, notes, *, decision='reject'):
        """Human author/reviewer separation; default rejects and never applies here."""
        self._active_review_worker()
        self._human_notes(notes)
        if decision not in ('approve', 'reject'):
            raise ValueError('explicit approve/reject draft decision required')
        request = self.draft_review_request()
        if request is None:
            raise RuntimeError('no pending exact-draft review')
        review = {key: value for key, value in request.items()
                  if key not in ('draft_material', 'report_sha256')}
        review.update(decision=decision, notes=notes)
        path = self.outputs['live'] / 'submissions/cq01_intake-cq01_draft_review.json'
        _write_new(path, review)
        return dict(submitted=True, decision=decision, draft_id=request['draft_id'],
                    version=request['version'], content_hash=request['content_hash'])

    def stop(self, mode):
        output = self._mode(mode)
        process = self.processes.get(mode)
        marker_error = None
        if process is not None and process.poll() is None:
            submissions = output / 'submissions'
            try:
                if submissions.is_dir() and not (submissions / 'stop.json').exists():
                    _write_new(submissions / 'stop.json', {'stop': True, 'reason': 'notebook operator stopped'})
            except (OSError, ValueError) as error:
                marker_error = type(error).__name__
            try:
                process.terminate()  # CLI forwards stop and owns PostgreSQL cleanup
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                return dict(self.status(mode), cleanup_pending=True, stop_marker_error=marker_error)
        return dict(self.status(mode), stop_marker_error=marker_error)

    def _stop_at_exit(self, mode):
        try:
            self.stop(mode)
        except (OSError, ValueError, RuntimeError):
            pass  # explicit shutdown.json check is still required by the presenter
