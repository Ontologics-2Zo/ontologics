"""C claim response as a person would use it: upload the claim, review, send, upload the test-team results.

    python -m examples.claim_response.scenario_app        # then open http://127.0.0.1:8765

Input files for 5 fictional scenarios are in examples/claim_response/scenario_inputs/ (see README.md there).
The page asks for one file or one decision at a time. Behind each click the real C functions and the common
draft -> review -> apply path run on a fresh PostgreSQL schema. The person's approvals use the development
reviewer identity; nothing is sent.

Model: the claim trace (step 3) runs on the project's fixed OpenAI model with the key and cost policy from
.env.local (paid, about a cent per claim). The model's answer is accepted only when it equals the trace the
functions compute from the model's own reads; the next steps use the function result. Without the settings the
server does not start; --scripted runs the trace on the scripted stand-in instead (no cost). Uploaded files are kept in
data/claim_response/uploads/ (ignored by Git) because graph sources must be files inside data/.
Local only: the server binds to 127.0.0.1.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import uuid

from decimal import Decimal

from examples.claim_response.run_demo import (
    PRINCIPALS, existing_nodes, live_agent_settings, live_report, must, run_agent_trace)
from ontologics.claim_response.fixtures import claim_response_nodes, load_operations
from ontologics.claim_response.intake import convert_claim
from ontologics.claim_response.reproduction import (
    c_operations_replacement, read_test_results, reproduction_requirement)
from ontologics.claim_response.requests import (
    REVIEW_LABELS, prepare_containment, prepare_reproduction_request, prepare_test_request, serial_ranges,
    write_request)
from ontologics.claim_response.trace import (
    MATCHED, c_operations_shipments, c_operations_stock, candidate_scope, stored_scope, trace_claim)
from ontologics.model import ROOT
from ontologics.service import DomainError, Service

INPUTS = Path(__file__).resolve().parent / 'scenario_inputs'
UPLOADS = ROOT / 'data/claim_response/uploads'
DOCS = ROOT / 'artifacts/local/claim_response/app'
MAX_UPLOAD = 2 * 1024 * 1024
KST = timezone(timedelta(hours=9))
DISPOSITIONS = {'customer_replacement': '고객 교체', 'stock_replacement': '보관 대체', 'release': '출고 허용',
                'hold': '보류', 'unresolved': '미결'}

# What the page asks next, by state. 'upload_*' shows a file input, the rest a decision button.
NEXT = {
    'start': ('upload_claim', '클레임 파일 올리기', '고객이 보낸 메일(.eml), 엑셀(.xlsx), CSV 중 하나를 올리세요.'),
    'claim_converted': ('approve_claim', '변환 내용 확인 후 등록', '원본과 변환 결과가 같은지 보고 등록합니다. (검토자로서 승인)'),
    'claim_registered': ('confirm_claim', '고객 클레임으로 확인', '조사 대상 클레임으로 확인합니다. 확인 후 보드·라인 추적이 시작됩니다.'),
    'containment_ready': ('approve_containment', '중단 요청서 검토 승인', '두 요청서를 읽고 승인합니다. 송부는 승인과 별개로 사람이 직접 합니다.'),
    'test_request_ready': ('send_test_request', '테스트팀에 송부했음으로 기록', '의뢰서를 테스트팀에 직접 보낸 뒤 누르세요. 시스템은 보내지 않습니다.'),
    'awaiting_results': ('upload_results', '테스트팀 결과 파일 올리기', '테스트팀이 돌려준 결과 CSV(보드, 시험 결과, 조치 판정, 관측 시각, 비고)를 올리세요.'),
    'results_blocked': ('upload_results', '고친 결과 파일 다시 올리기', '표시된 행을 테스트팀과 확인해 고친 파일을 올리세요. 아무것도 저장되지 않았습니다.'),
    'reproduction_ready': ('approve_reproduction', 'A 요청서 검토 승인', '재생산 수량과 근거를 확인하고 승인합니다. 송부는 사람이 직접 합니다.'),
}


class ActionError(Exception):
    pass


class ClaimCase:
    """One claim from upload to the A request, on its own fresh schema. Each public method is one user action."""

    def __init__(self, database=None, *, docs_dir=DOCS, uploads_dir=UPLOADS, live=None, clock=None):
        self.database, self.docs_dir, self.uploads_dir = database, Path(docs_dir), Path(uploads_dir)
        # The time a person presses the button (KST). Tests may pass a fixed clock.
        self.clock = clock or (lambda: datetime.now(KST).isoformat(timespec='seconds'))
        self.live = live  # live_agent_settings() result for the real model, None for the scripted stand-in
        self.state, self.events, self.s, self.svc = 'start', [], {}, None

    # ------------------------------------------------------------------ helpers
    def close(self):
        if self.svc is not None:
            self.svc.close()
            self.svc = None

    def _event(self, title, rows, *, tone='ok', documents=None, note=None):
        docs = {}
        for name, text in (documents or {}).items():
            write_request(text, name, base_dir=self.docs_dir)
            docs[name] = text
        self.events.append({'title': title, 'rows': rows, 'tone': tone, 'documents': docs, 'note': note})

    def _refresh(self, documents):
        """Rewrite documents after a review (same file names); earlier cards show the new text too."""
        for name, text in documents.items():
            write_request(text, name, base_dir=self.docs_dir)
            for event in self.events:
                if name in event['documents']:
                    event['documents'][name] = text
        return len(documents)

    def _save_upload(self, filename, content, allowed):
        name = Path(filename or '').name
        suffix = Path(name).suffix.lower()
        if suffix not in allowed:
            raise ActionError('지원하지 않는 파일 형식입니다: ' + (suffix or '확장자 없음') + ' (가능: ' + ', '.join(allowed) + ')')
        if not content or len(content) > MAX_UPLOAD:
            raise ActionError('파일이 비어 있거나 2MB를 넘습니다.')
        safe = re.sub(r'[^0-9A-Za-z가-힣._-]', '_', Path(name).stem)[:60]
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        path = self.uploads_dir / (hashlib.sha256(content).hexdigest()[:12] + '_' + safe + suffix)
        path.write_bytes(content)
        return path

    def _require(self, state):
        if self.state != state:
            raise ActionError('지금 단계에서 할 수 없는 동작입니다.')

    def _approve(self, operations, key, note):
        draft = must(self.svc.prepare_draft('claim-agent', operations, request_key=key + '-draft'))['data']
        must(self.svc.review('reviewer', draft['draft_id'], draft['draft_version'], decision='approve', note=note,
                             request_key=key + '-review'))
        return must(self.svc.apply('workflow', draft['draft_id'], draft['draft_version'], request_key=key + '-apply'))

    def _status(self, draft):
        return must(self.svc.get_draft('claim-agent', draft['draft_id'], draft['draft_version']))['data']['status']

    # ------------------------------------------------------------------ actions
    def upload_claim(self, filename, content):
        if self.state not in ('start', 'claim_converted'):
            raise ActionError('클레임은 처음 단계에서만 올릴 수 있습니다. "새 클레임"으로 다시 시작하세요.')
        path = self._save_upload(filename, content, ('.eml', '.xlsx', '.csv'))
        try:
            conversion = convert_claim(path, collected_at=self.clock())
        except ValueError as exc:
            self._event('클레임 파일을 읽지 못함', [('파일', Path(filename).name), ('이유', str(exc))], tone='bad',
                        note='파일 형식이나 양식을 확인하세요.')
            return
        report = conversion['report']
        intake = next((n for n in conversion['nodes'] if n['type'] == 'ClaimIntake'), None)
        claim = report['claim']
        rows = [('파일', f"{Path(filename).name} ({report['format']})"), ('고객 클레임 번호', claim.get('external_claim_id')),
                ('고객사', claim.get('customer_name')), ('신고 시각', claim.get('reported_at')),
                ('신고 보드', ', '.join(c['serial'] for c in report['unit_candidates'])),
                ('수량', f"{report['claimed_quantity']}대" if report['claimed_quantity'] is not None else None),
                ('증상 (고객 표현)', claim.get('reported_symptom')), ('발생 조건', claim.get('reported_conditions')),
                ('납품 문서', claim.get('delivery_reference')), ('원본 위치', report['locator'])]
        problems = [f"{i['code']} {i.get('message') or ''}".strip() for i in report['issues']]
        warnings = [f"{w['code']} {w.get('message') or ''}".strip() for w in report['warnings']]
        if problems:
            rows.append(('문제', '; '.join(problems)))
        if warnings:
            rows.append(('사람 확인', '; '.join(warnings)))
        if report['state'] != 'needs_review' or intake is None:
            self._event('클레임 변환 막힘', rows, tone='bad', note='필수 항목이 없거나 값이 맞지 않습니다. 고친 파일을 다시 올리세요.')
            return
        configuration = report.get('configuration_candidate_id')
        if configuration is None:
            rows.append(('제품 구성', '정할 수 없음 (제조사·모델·설계 개정·변형 확인 필요)'))
            self._event('클레임 변환 — 제품 구성 확인 필요', rows, tone='bad',
                        note='구성을 추측하지 않습니다. 고객에게 제품 정보를 확인해 고친 파일을 올리세요.')
            return
        rows.append(('제품 구성', configuration))
        self.s.update(conversion=conversion, intake=intake, configuration=configuration)
        self._event('클레임 변환', rows, tone='warn' if warnings else 'ok',
                    note='고객 표현을 그대로 옮겼습니다. 보드 연결과 원인 판단은 아직 하지 않았습니다.')
        self.state = 'claim_converted'

    def approve_claim(self):
        self._require('claim_converted')
        self.close()
        self.svc = Service(self.database, schema=('claim_app_' + uuid.uuid4().hex) if self.database is None else None,
                           principals=PRINCIPALS)
        must(self.svc.bootstrap('seed-admin', existing_nodes() + claim_response_nodes(), request_key='app-seed'))
        registered = self._approve([{'op': 'put', 'node': n} for n in self.s['conversion']['nodes']], 'app-intake',
                                   'Reviewed in the claim app: converted fields match the source file.')
        self._event('클레임 등록', [('그래프 버전', registered['data_version']), ('상태', '검토 필요 (needs_review)')],
                    note='등록했지만 아직 조사 대상으로 확인하지 않았습니다.')
        self.state = 'claim_registered'

    def confirm_claim(self):
        self._require('claim_registered')
        intake = self.s['intake']
        confirmed = self._approve([{'op': 'put', 'node': dict(intake, intake_state='confirmed')}], 'app-confirm',
                                  'Confirmed in the claim app as a customer claim to investigate.')
        version = confirmed['data_version']
        self._event('클레임 확인', [('그래프 버전', version), ('상태', '확인됨 (confirmed)')])
        trace = trace_claim(self.svc, 'claim-agent', intake['id'], version=version,
                            configuration_id=self.s['conversion']['report']['configuration_candidate_id'])
        matched = [l for l in trace['links'] if l['match_state'] == MATCHED]
        open_links = [l for l in trace['links'] if l['match_state'] != MATCHED]
        case = trace['quality_case']
        rows = [('연결된 보드', f"{len(matched)}대 · {serial_ranges([l['unit_id'] for l in matched]) or '-'}"),
                ('못 찾은 번호', ', '.join(f"{l['serial']} ({l['match_state']})" for l in open_links) or '없음'),
                ('생산라인', trace['lines']['single_line'] or '하나로 정해지지 않음')]
        self._agent_trace(intake['id'], version, matched, trace['lines']['single_line'])
        mismatched = [l['serial'] for l in matched if any(w['code'] == 'CONFIGURATION_MISMATCH' for w in l.get('warnings', []))]
        if mismatched:
            rows.append(('구성 불일치', f"클레임은 {self.s['configuration']}, 보드 기록은 다른 구성: {', '.join(mismatched)}"))
            case = dict(case, node=None, reason='claimed configuration differs from the board records')
        if case['node'] is None or trace['lines']['single_line'] is None:
            rows.append(('멈춘 이유', case.get('reason') or ', '.join(trace['lines'].get('unresolved_unit_ids') or [])))
            self._event('보드·생산라인 추적 — 사람 확인 필요', rows, tone='bad',
                        note='비슷한 번호로 추측하지 않습니다. 고객에게 일련번호를 확인한 뒤 고친 클레임으로 새로 시작하세요.')
            self.state = 'trace_blocked'
            return
        self._event('보드·생산라인 추적', rows)
        self.s.update(case=case['node'], line=trace['lines']['single_line'], version=version)
        self._prepare_containment()

    def _agent_trace(self, intake_id, version, matched, line):
        """The same trace by the C Agent through run_task: the real model (default) or the scripted stand-in."""
        if self.live is None:
            agent = run_agent_trace(self.database, self.svc.schema, intake_id, version)
            self._event('C Agent 추적 (대역, 모델 호출 없음)', [
                ('결과', ('함수 계산과 일치' if agent['runtime']['ok'] else '불일치') + f" · 근거 {len(agent['evidence_refs'])}개")],
                tone='ok' if agent['runtime']['ok'] else 'bad', note='--scripted 실행: 실제 모델 대신 정해진 순서로 도구를 부르는 대역입니다.')
            return
        try:
            agent = run_agent_trace(self.database, self.svc.schema, intake_id, version, live=self.live)
        except DomainError as exc:
            self._event('C Agent 추적 (실제 모델) — 실행 안 됨', [('이유', f'{exc.code}: {exc.message}')], tone='bad',
                        note='모델 없이도 다음 단계는 함수 결과로 진행합니다.')
            return
        expected = {'matched_unit_ids': [l['unit_id'] for l in matched], 'production_line_id': line}
        report = live_report(agent, expected)
        answer, checks = report['answer'], report['automatic_checks']
        calls = report['provider_calls']
        cost = sum(Decimal(c['cost']['reported_cost_usd']) for c in calls
                   if (c.get('cost') or {}).get('reported_cost_usd') is not None)
        rows = [('모델', report['runtime']['model']),
                ('모델이 고른 도구', ' → '.join(f"{t['name']}({', '.join(f'{k}={v}' for k, v in t['arguments'].items() if k != 'version')})"
                                         for t in report['tool_calls']) or '없음'),
                ('모델 답: 보드', serial_ranges(answer['matched_unit_ids']) if answer else '-'),
                ('모델 답: 생산라인', (answer.get('production_line_id') or '없음 (하나로 안 정해짐)') if answer else '-'),
                ('모델 답: 근거', f"{len(answer['evidence_ids'])}개" if answer else '-'),
                ('함수 계산과 일치', '예' if checks['answer_equals_recomputed_trace'] else '아니요 (거부됨)'),
                ('자동 검사', f"{sum(checks.values())}/4 통과"),
                ('호출', f"모델 {len(calls)}회 · 약 ${cost:.4f}")]
        if answer is None:
            rows += [('거부 이유', (report['runtime']['error'] or {}).get('code')),
                     ('모델이 쓴 답', report['final_text'] or '(없음)')]
        self._event('C Agent 추적 (실제 모델)', rows, tone='ok' if all(checks.values()) else 'bad',
                    note='모델 답은 함수가 모델이 읽은 데이터로 다시 계산한 값과 같을 때만 인정합니다. 다음 단계는 함수 결과로 진행합니다.')

    def _prepare_containment(self):
        s = self.s
        records = must(self.svc.lookup('claim-agent', kind='UnitProductionRecord', version=s['version']))['data']
        operations = load_operations()
        shipments = c_operations_shipments(operations)
        hold = stored_scope(s['case'], s['line'], records, c_operations_stock(operations), shipments)
        candidate = candidate_scope(s['case'], s['line'], records, shipments)
        s.update(records=records, hold=hold, candidate=candidate)

        def show(result):
            units = (result.get('node') or {}).get('unit_ids')
            return f"{len(units)}대 · {serial_ranges(units)}" if units else result['state']
        self._event('보관 보류·납품 후보 범위', [('보관 보류 (같은 라인, 창고)', show(hold)),
                                           ('납품 후보 (이미 고객에게, 범위 밖)', show(candidate)),
                                           ('근거', 'C 운영 기록 (임시, B 공통 재고·출하 타입 전)')],
                    tone='ok' if hold['state'] == 'ready' else 'warn',
                    note='보류 대상은 불량 확정이 아닙니다.')
        s['t_containment'] = self.clock()
        containment = prepare_containment(s['case'], s['line'], records, hold, prepared_at=s['t_containment'],
                                          external_claim_id=s['intake']['external_claim_id'], configuration_id=s['configuration'])
        extra = [candidate['node']] if candidate['node'] else []
        draft = must(self.svc.prepare_draft('claim-agent', [{'op': 'put', 'node': n} for n in [s['case'], *containment['nodes'], *extra]],
                                            request_key='app-containment'))['data']
        status = self._status(draft)
        again = prepare_containment(s['case'], s['line'], records, hold, prepared_at=s['t_containment'],
                                    external_claim_id=s['intake']['external_claim_id'], configuration_id=s['configuration'],
                                    review_status=status)
        s['containment_draft'] = draft
        kinds = {'production_stop': '생산 중단', 'shipment_hold': '출고·배송 중단'}
        made = [kinds[n['request_kind']] for n in containment['nodes'] if n['type'] == 'ContainmentRequest']
        rows = [('요청서', ', '.join(made)), ('검토 상태', REVIEW_LABELS.get(status, status)), ('송부 상태', '미송부')]
        for skipped in containment['skipped']:
            rows.append(('만들지 않음', f"{kinds[skipped['request_kind']]}: {skipped['state']}"))
        self._event('중단 요청서 초안', rows, documents=again['documents'], note='검토할 초안입니다. 시스템은 보내지 않습니다.')
        self.state = 'containment_ready'

    def approve_containment(self):
        self._require('containment_ready')
        s, draft = self.s, self.s['containment_draft']
        must(self.svc.review('reviewer', draft['draft_id'], draft['draft_version'], decision='approve',
                             note='Request drafts reviewed in the claim app; sending is recorded by people.',
                             request_key='app-containment-review'))
        applied = must(self.svc.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='app-containment-apply'))
        refreshed = prepare_containment(s['case'], s['line'], s['records'], s['hold'], prepared_at=s['t_containment'],
                                        external_claim_id=s['intake']['external_claim_id'], configuration_id=s['configuration'],
                                        review_status='applied')
        count = self._refresh(refreshed['documents'])
        self._event('중단 요청서 승인', [('그래프 버전', applied['data_version']),
                                    ('문서 검토 상태', f"{REVIEW_LABELS['applied']} ({count}개 문서 갱신)"),
                                    ('송부 상태', '미송부 (사람이 직접 송부)')])
        s['t_test'] = self.clock()
        test = prepare_test_request(s['case'], s['line'], s['hold'], prepared_at=s['t_test'],
                                    external_claim_id=s['intake']['external_claim_id'], configuration_id=s['configuration'],
                                    candidate_result=s['candidate'])
        s['test'] = test
        groups = ' + '.join(f"{g['group']} {len(g['unit_ids'])}대" for g in test['targets']['groups'])
        self._event('테스트 의뢰서', [('의뢰 대상', f"{len(test['node']['unit_ids'])}대 ({groups})"),
                                  ('돌려받을 내용', '보드별 시험 결과 + 조치 판정 (테스트팀이 정함)')],
                    documents=test['documents'], note='의뢰서를 내려받아 테스트팀에 보내세요.')
        self.state = 'test_request_ready'

    def send_test_request(self):
        self._require('test_request_ready')
        s = self.s
        sent = self._approve([{'op': 'put', 'node': s['test']['node']}], 'app-test-request',
                             'Test request reviewed and sent to the test team by a person (recorded in the claim app).')
        refreshed = prepare_test_request(s['case'], s['line'], s['hold'], prepared_at=s['t_test'],
                                         external_claim_id=s['intake']['external_claim_id'], configuration_id=s['configuration'],
                                         candidate_result=s['candidate'], review_status='applied')
        self._refresh(refreshed['documents'])
        self._event('테스트 의뢰 송부 기록', [('그래프 버전', sent['data_version']),
                                       ('문서 검토 상태', REVIEW_LABELS['applied']),
                                       ('송부', '사람이 보냈다고 기록함 (시스템은 보내지 않음)'), ('다음', '테스트팀 결과 기다림')])
        self.state = 'awaiting_results'

    def upload_results(self, filename, content):
        if self.state not in ('awaiting_results', 'results_blocked'):
            raise ActionError('지금은 결과 파일을 올릴 단계가 아닙니다.')
        s = self.s
        path = self._save_upload(filename, content, ('.csv',))
        try:
            results = read_test_results(path, s['test']['node'], s['case'], collected_at=self.clock())
        except ValueError as exc:
            self._event('결과 파일을 읽지 못함', [('파일', Path(filename).name), ('이유', str(exc))], tone='bad',
                        note='머리글은 보드, 시험 결과, 조치 판정, 관측 시각, 비고 순서여야 합니다.')
            self.state = 'results_blocked'
            return
        if results['state'] != 'needs_review':
            # Counts of a blocked file are not results: nothing is stored, so only the rows to fix are shown.
            rows = [('파일', Path(filename).name), ('저장', '안 함 (0건)'), ('오류', f"{len(results['issues'])}건"),
                    ('고칠 행', '; '.join(f"{i.get('locator') or '파일'}: {i['code']} ({i.get('message') or ''})"
                                         for i in results['issues']))]
            self._event('테스트팀 결과 — 오류로 차단', rows, tone='bad',
                        note='오류가 하나라도 있으면 결과를 하나도 저장하지 않습니다. C는 판정을 고치지 않습니다.')
            self.state = 'results_blocked'
            return
        counts = results['counts']
        rows = [('파일', Path(filename).name), ('시험 / 미검사', f"{counts['tested']}대 / {counts['untested']}대"),
                ('조치 판정 (테스트팀)', ' · '.join(f"{DISPOSITIONS[k]} {counts[k]}" for k in DISPOSITIONS if k in counts) or '없음')]
        self._event('테스트팀 결과 받음', rows, note='판정은 파일에 적힌 그대로 옮겼습니다.')
        s['results'] = results
        replacement = c_operations_replacement(load_operations())
        reproduction = reproduction_requirement(s['case'], s['test']['node'], results['nodes'],
                                                replacement, configuration_id=s['configuration'])
        s['t_reproduction'] = self.clock()
        request = prepare_reproduction_request(s['case'], reproduction, prepared_at=s['t_reproduction'],
                                               external_claim_id=s['intake']['external_claim_id'])
        nodes = results['nodes'] + ([request['node']] if request['node'] else [])
        draft = must(self.svc.prepare_draft('claim-agent', [{'op': 'put', 'node': n} for n in nodes],
                                            request_key='app-reproduction'))['data']
        status = self._status(draft)
        s['reproduction_draft'] = draft
        r, required = reproduction, reproduction['required_good_quantity']
        # The boards actually counted as replacement stock (same rule as reproduction_requirement).
        available = (r['node'] or {}).get('replacement_unit_ids') or [
            u for u in replacement['unit_ids'] if u not in s['test']['node']['unit_ids']]
        state_label = {'confirmed': '확정', 'lower_bound': '하한 (최소 수량, 추가 확인 필요)', 'undetermined': '미정'}[r['state']]
        rows = [('고객 교체 + 보관 대체', f"{r['customer_replacement_quantity']} + {r['stock_replacement_quantity']}"),
                ('사용할 대체 재고', f"{r['available_replacement_quantity']}대 ({serial_ranges(available) or '없음'})"),
                ('계산', '계산하지 않음 (확정된 교체 수요 없음)' if required is None else
                 f"max(0, {r['customer_replacement_quantity'] + r['stock_replacement_quantity']} − {r['available_replacement_quantity']}) = {required}대"),
                ('재생산 필요', '미정' if required is None else f'{required}대'), ('상태', state_label), ('미결', f"{r['unresolved_quantity']}대")]
        if request['node'] is None:
            rows.append(('A 요청서', '만들지 않음 (미정은 0으로 저장하지 않음)'))
            self._event('재생산 수량', rows, tone='warn', note='미결 보드의 판정이 나오면 새 결과로 다시 계산합니다.')
        else:
            request = prepare_reproduction_request(s['case'], reproduction, prepared_at=s['t_reproduction'],
                                                   external_claim_id=s['intake']['external_claim_id'], review_status=status)
            rows.append(('A 요청서', REVIEW_LABELS.get(status, status) + ' · 미송부'))
            self._event('재생산 수량·A 요청서', rows, tone='ok' if r['state'] == 'confirmed' else 'warn',
                        documents=request['documents'])
        self.s['reproduction'] = reproduction
        self.state = 'reproduction_ready'

    def approve_reproduction(self):
        self._require('reproduction_ready')
        draft = self.s['reproduction_draft']
        must(self.svc.review('reviewer', draft['draft_id'], draft['draft_version'], decision='approve',
                             note='Results and reproduction quantity reviewed in the claim app.',
                             request_key='app-reproduction-review'))
        applied = must(self.svc.apply('workflow', draft['draft_id'], draft['draft_version'], request_key='app-reproduction-apply'))
        s = self.s
        required = s['reproduction']['required_good_quantity']
        if required is not None:
            refreshed = prepare_reproduction_request(s['case'], s['reproduction'], prepared_at=s['t_reproduction'],
                                                     external_claim_id=s['intake']['external_claim_id'], review_status='applied')
            self._refresh(refreshed['documents'])
        self._event('검토 완료', [('그래프 버전', applied['data_version']),
                                  ('문서 검토 상태', REVIEW_LABELS['applied'] if required is not None else '해당 문서 없음'),
                                  ('최종 답', '재생산 미정 · A 요청서 없음' if required is None else f'재생산 {required}대 · A 요청서 송부 대기 (사람)')])
        self.state = 'done'
        self.close()

    def view(self):
        action = NEXT.get(self.state)
        return {'state': self.state, 'events': self.events,
                'next': None if action is None else {'action': action[0], 'label': action[1], 'help': action[2]},
                'finished': self.state in ('done', 'trace_blocked')}


# ---------------------------------------------------------------------- local web server
CURRENT = {'case': ClaimCase(), 'live': None}
ACTIONS = {'approve_claim', 'confirm_claim', 'approve_containment', 'send_test_request', 'approve_reproduction'}


def input_files():
    return sorted(p.name for p in INPUTS.iterdir() if p.suffix.lower() in ('.eml', '.xlsx', '.csv')) if INPUTS.is_dir() else []


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, kind='application/json; charset=utf-8', extra=None):
        data = body if isinstance(body, bytes) else (body.encode('utf-8') if isinstance(body, str)
                                                     else json.dumps(body, ensure_ascii=False).encode('utf-8'))
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            return self._send(200, PAGE, 'text/html; charset=utf-8')
        if self.path == '/api/case':
            return self._send(200, dict(CURRENT['case'].view(), inputs=input_files()))
        match = re.fullmatch(r'/docs/([0-9A-Za-z._-]+\.md)', self.path)
        if match and (DOCS / match.group(1)).is_file():
            return self._send(200, (DOCS / match.group(1)).read_bytes(), 'text/markdown; charset=utf-8',
                              {'Content-Disposition': f'attachment; filename="{match.group(1)}"'})
        return self._send(404, {'error': 'NOT_FOUND'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_UPLOAD * 2:
            self.rfile.read(length)
            return self._send(413, {'error': '파일이 너무 큽니다.'})
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return self._send(400, {'error': '요청 형식 오류'})
        case = CURRENT['case']
        try:
            if self.path == '/api/new':
                case.close()
                CURRENT['case'] = ClaimCase(live=CURRENT['live'])
            elif self.path in ('/api/upload_claim', '/api/upload_results'):
                content = base64.b64decode(body.get('content') or '', validate=True)
                getattr(case, self.path.rsplit('/', 1)[1])(body.get('filename'), content)
            elif self.path.startswith('/api/') and self.path[5:] in ACTIONS:
                getattr(case, self.path[5:])()
            else:
                return self._send(404, {'error': 'NOT_FOUND'})
        except ActionError as exc:
            return self._send(409, dict(CURRENT['case'].view(), error=str(exc), inputs=input_files()))
        except Exception as exc:  # show it on the page instead of a blank screen
            return self._send(500, dict(CURRENT['case'].view(), error=f'{type(exc).__name__}: {exc}', inputs=input_files()))
        return self._send(200, dict(CURRENT['case'].view(), inputs=input_files()))


PAGE = r'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>C 클레임 대응</title><style>
:root{--bg:#f6f6f3;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e1db;--ok:#1f7a4d;--okbg:#e6f3ec;--bad:#b3261e;--badbg:#fbe7e5;--warn:#9a6200;--warnbg:#fbf1dc;--accent:#2b59c3}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--mute:#a09f98;--line:#34342f;--ok:#5fc48f;--okbg:#17301f;--bad:#f19a90;--badbg:#3a1a17;--warn:#e2b45c;--warnbg:#33290f;--accent:#86a8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,"Malgun Gothic",sans-serif}
.wrap{display:grid;grid-template-columns:260px 1fr;min-height:100vh}aside{border-right:1px solid var(--line);padding:18px;background:var(--card)}
aside h1{font-size:17px;margin:0 0 4px}aside p{color:var(--mute);font-size:13px;margin:0 0 14px}
ol.track{list-style:none;padding:0;margin:0 0 18px}ol.track li{padding:6px 0 6px 26px;position:relative;color:var(--mute)}
ol.track li:before{content:"";position:absolute;left:4px;top:11px;width:10px;height:10px;border-radius:50%;border:2px solid var(--line)}
ol.track li.done{color:var(--ink)}ol.track li.done:before{background:var(--ok);border-color:var(--ok)}
ol.track li.now{color:var(--accent);font-weight:600}ol.track li.now:before{border-color:var(--accent)}
ol.track li.stop{color:var(--bad)}ol.track li.stop:before{background:var(--bad);border-color:var(--bad)}
.files{font-size:12px;color:var(--mute)}.files code{display:block;margin:2px 0;color:var(--ink);overflow-wrap:anywhere}
button{font:inherit;cursor:pointer}.newbtn{width:100%;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;padding:8px}
main{padding:22px 26px 80px;max-width:960px}
.action{background:var(--card);border:2px solid var(--accent);border-radius:12px;padding:16px;margin-bottom:18px}
.action h2{margin:0 0 4px;font-size:17px}.action p{margin:0 0 12px;color:var(--mute)}
.primary{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:9px 18px;font-weight:600}
.primary:disabled{opacity:.5;cursor:default}.drop{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.err{background:var(--badbg);color:var(--bad);border-radius:8px;padding:8px 12px;margin-bottom:12px}
.ev{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--ok);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.ev.warn{border-left-color:var(--warn)}.ev.bad{border-left-color:var(--bad)}.ev h3{margin:0 0 8px;font-size:15px}
.ev .no{color:var(--mute);font-weight:400;margin-right:6px}
dl{display:grid;grid-template-columns:minmax(150px,max-content) 1fr;gap:3px 14px;margin:0}dt{color:var(--mute)}dd{margin:0;overflow-wrap:anywhere}
.note{color:var(--mute);font-size:13px;margin:8px 0 0}details{margin-top:8px;border:1px solid var(--line);border-radius:8px}
summary{cursor:pointer;padding:6px 10px;font-size:13px;overflow-wrap:anywhere}summary a{margin-left:8px;font-size:12px;color:var(--accent)}
pre{margin:0;padding:10px;white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;max-height:440px;overflow:auto;border-top:1px solid var(--line)}
.end{border-radius:10px;padding:14px;font-weight:600;margin-bottom:18px}.end.ok{background:var(--okbg);color:var(--ok)}.end.bad{background:var(--badbg);color:var(--bad)}
@media (max-width:760px){.wrap{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line)}main{padding:16px}dl{grid-template-columns:1fr}}
</style></head><body><div class="wrap"><aside><h1>C 클레임 대응</h1>
<p>사용자처럼 파일을 올리고 검토·확인 버튼을 누르며 진행합니다. 가상 데이터이며 아무것도 송부하지 않습니다. 클레임 추적은 실제 모델이 한 번 더 수행하고 함수 계산과 대조합니다.</p>
<ol class="track" id="track"></ol><button class="newbtn" onclick="act('new')">새 클레임 시작</button>
<div class="files" id="files"></div></aside><main id="main"></main></div>
<script>
const STEPS=[['upload_claim','클레임 접수'],['approve_claim','변환 확인·등록'],['confirm_claim','클레임 확인·추적'],['approve_containment','중단 요청서 검토'],
['send_test_request','테스트 의뢰 송부'],['upload_results','테스트팀 결과'],['approve_reproduction','재생산·A 요청서 검토']];
let view=null,busy=false;const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){view=await (await fetch('/api/case')).json();render();}
async function act(name,payload){if(busy)return;busy=true;render();
 const r=await fetch('/api/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload||{})});
 view=await r.json();busy=false;render();window.scrollTo({top:0,behavior:'smooth'});}
function upload(name){const f=document.getElementById('file').files[0];if(!f)return;const rd=new FileReader();
 rd.onload=()=>act(name,{filename:f.name,content:rd.result.split(',')[1]});rd.readAsDataURL(f);}
function render(){if(!view)return;const cur=view.next?view.next.action:null,idx=STEPS.findIndex(s=>s[0]===cur);
 const stopped=view.state==='trace_blocked';
 document.getElementById('track').innerHTML=STEPS.map((s,i)=>{let c='';if(view.state==='done'||(idx>i&&idx>=0))c='done';
  if(i===idx)c='now';if(stopped&&i===2)c='stop';if(stopped&&i<2)c='done';return `<li class="${c}">${i+1}. ${s[1]}</li>`;}).join('');
 document.getElementById('files').innerHTML='<p>시나리오 입력 파일<br>examples/claim_response/scenario_inputs/</p>'+(view.inputs||[]).map(f=>`<code>${esc(f)}</code>`).join('');
 let action='';if(view.error)action+=`<div class="err">${esc(view.error)}</div>`;
 if(view.next){const n=view.next,isUp=n.action.startsWith('upload');
  action+=`<div class="action"><h2>${esc(n.label)}</h2><p>${esc(n.help)}</p>${isUp?`<div class="drop"><input type="file" id="file" accept="${n.action==='upload_claim'?'.eml,.xlsx,.csv':'.csv'}">
  <button class="primary" ${busy?'disabled':''} onclick="upload('${n.action}')">${busy?'처리 중…':'올리기'}</button></div>`:
  `<button class="primary" ${busy?'disabled':''} onclick="act('${n.action}')">${busy?'처리 중…':esc(n.label)}</button>`}</div>`;}
 if(view.state==='done'){const last=view.events[view.events.length-1];const fin=(last.rows.find(r=>r[0]==='최종 답')||[])[1];action+=`<div class="end ok">완료 · ${esc(fin)}</div>`;}
 if(stopped)action+=`<div class="end bad">사람 확인이 필요해 여기서 멈췄습니다. "새 클레임 시작"으로 고친 클레임을 올리세요.</div>`;
 const evs=view.events.map((e,i)=>`<div class="ev ${e.tone}"><h3><span class="no">#${i+1}</span>${esc(e.title)}</h3>
  <dl>${e.rows.filter(r=>r[1]!==null&&r[1]!==undefined&&r[1]!=='').map(r=>`<dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd>`).join('')}</dl>
  ${e.note?`<p class="note">${esc(e.note)}</p>`:''}${Object.entries(e.documents||{}).map(([n,t])=>`<details><summary>📄 ${esc(n)}<a href="/docs/${encodeURIComponent(n)}" download>내려받기</a></summary><pre>${esc(t)}</pre></details>`).join('')}</div>`).reverse().join('');
 document.getElementById('main').innerHTML=action+(evs?'<h3 style="margin:6px 0 10px;font-size:14px;color:var(--mute)">진행 기록 (최근 순)</h3>'+evs:'');}
load();
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--scripted', action='store_true', help='Run the claim trace on the scripted stand-in (no model, no cost)')
    parser.add_argument('--env-file', default=None, help='Default .env then .env.local; process environment wins')
    args = parser.parse_args()
    if not args.scripted:
        try:
            CURRENT['live'] = live_agent_settings(args.env_file)
        except DomainError as exc:
            print(f'실제 모델 설정이 없어 시작하지 않습니다: {exc.code} ({exc.message})')
            print('.env.local의 OPENAI_API_KEY, OPENAI_MODEL, 비용 상한·단가를 확인하거나 --scripted로 실행하세요.')
            return 1
    CURRENT['case'] = ClaimCase(live=CURRENT['live'])
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    print(f'C 클레임 대응 화면: http://127.0.0.1:{args.port}  (끝내려면 Ctrl+C)')
    print(f'시나리오 입력 파일: {INPUTS}')
    print('클레임 추적 모델: ' + ('실제 모델 ' + CURRENT['live']['settings'].model + ' (유료)' if CURRENT['live'] else '대역 (--scripted)'))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        CURRENT['case'].close()
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
