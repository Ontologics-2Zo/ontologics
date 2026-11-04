"""Explicit C intake and human-selected case proposals for the common workspace.

No symptom diagnosis, test result invention, stock membership inference, or
production-work linking occurs here. Approval is always the common host's job.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import email
from email import policy
from pathlib import Path
import tempfile

from ..model import ROOT, digest
from ..service import DomainError
from .fixtures import load_operations
from .intake import COLUMNS, PARSERS, convert_claim
from .reproduction import c_operations_replacement, read_test_results, reproduction_requirement
from .requests import (prepare_containment, prepare_test_request, prepare_reproduction_request,
                       render_reproduction_request)
from .trace import (MATCHED, c_operations_shipments, c_operations_stock, candidate_scope, line_summary,
                    link_serials, prepare_quality_case, stored_scope, trace_lines)

# Synthetic claim files waiting for intake. Demonstration data only; the screen never reads a real
# customer mailbox, and these are the same files the C scenarios use.
INBOX = ROOT / 'examples/claim_response/scenario_inputs'
RECORD_LIMIT = 100  # the common draft builder takes at most 100 records at a time


CLAIM_REQUIRED = {'external_claim_id', 'customer_name', 'reported_at',
                  'reported_symptom', 'claimed_quantity', 'reported_serials'}
CLAIM_OPTIONAL = {'reported_conditions', 'requested_action', 'delivery_reference'}


def _fields(payload, required, optional=()):
    if not isinstance(payload, dict) or not required <= set(payload) or set(payload) - required - set(optional):
        raise DomainError('INVALID_CLAIM_INPUT', '클레임의 필수 입력과 허용 항목을 확인하세요.')


def _demo(value):
    return isinstance(value, str) and any(word in value.casefold() for word in ('demo', '가상', '가정', '시연'))


def _bare(record):
    return {key: value for key, value in record.items() if key not in {'origin', 'evidence'}}


def _inbox(kind):
    if not INBOX.is_dir():
        return []
    claims = kind == 'claim'
    return sorted(p for p in INBOX.iterdir()
                  if p.suffix.lower() in (('.eml', '.xlsx', '.csv') if claims else ('.csv',))
                  and ('테스트팀_결과' not in p.name) == claims)


def _inbox_file(kind, name):
    for path in _inbox(kind):
        if path.name == name:
            return path
    raise DomainError('NOT_FOUND', '대기 목록에 없는 파일입니다.')


def _of_type(index, nid, kind, message):
    node = index.get(nid) if isinstance(nid, str) else None
    if node is None or node['type'] != kind:
        raise DomainError('NOT_FOUND', message)
    return node


def _case_context(nodes, index, case_id):
    """The approved records one case needs: its boards, production records and the C operations basis."""
    case = _of_type(index, case_id, 'QualityCase', '승인된 품질 사건을 선택하세요.')
    intake = _of_type(index, case['intake_id'], 'ClaimIntake', '사건의 클레임 접수 기록이 없습니다.')
    records = [n for n in nodes if n['type'] == 'UnitProductionRecord']
    traces = trace_lines(case['unit_ids'], records, [n for n in nodes if n['type'] == 'ProductionLine'])
    line_id = line_summary(traces)['single_line']
    if line_id is None:
        raise DomainError('CLAIM_LINE_UNRESOLVED', '신고 보드가 한 생산라인으로 모이지 않습니다. 사람이 확인하세요.')
    unit = index.get(case['unit_ids'][0], {})
    return {'case': case, 'intake': intake, 'records': records, 'line_id': line_id,
            'configuration_id': unit.get('configuration_id'), 'operations': load_operations()}


def _hold(context):
    return stored_scope(context['case'], context['line_id'], context['records'],
                        c_operations_stock(context['operations']),
                        c_operations_shipments(context['operations']))


def build_records(nodes, action, payload):
    index = {node['id']: node for node in nodes}
    if action == 'intake':
        _fields(payload, CLAIM_REQUIRED, CLAIM_OPTIONAL)
        # The current public policy permits demonstration records only, not
        # uploading real customer information to the synthetic graph.
        if (not _demo(payload.get('external_claim_id')) or not _demo(payload.get('customer_name'))
                or ('delivery_reference' in payload and not _demo(payload['delivery_reference']))):
            raise DomainError('DEMO_CLAIM_REQUIRED', '시연용 가상 고객·클레임·납품 번호에 DEMO 또는 가상 표시를 포함하세요.')
        identity = (payload['customer_name'], payload['external_claim_id'])
        if any(node['type'] == 'ClaimIntake' and
                (node['customer_name'], node['external_claim_id']) == identity for node in nodes):
            raise DomainError('CLAIM_ALREADY_REGISTERED', '같은 고객·클레임 번호가 이미 승인됐습니다. 기존 접수 기록을 확인하세요.')
        return [{'id': 'claim-intake:submitted:' + digest(identity), 'type': 'ClaimIntake',
                 **payload, 'intake_state': 'needs_review'}]
    if action == 'confirm_case':
        _fields(payload, {'intake_id', 'unit_ids', 'serials_confirmed'})
        intake = index.get(payload['intake_id']) if isinstance(payload['intake_id'], str) else None
        if not intake or intake['type'] != 'ClaimIntake':
            raise DomainError('NOT_FOUND', '승인된 클레임 접수 기록을 선택하세요.')
        if intake['intake_state'] == 'rejected':
            raise DomainError('INVALID_STATE', '반려된 접수로 품질 사건을 만들 수 없습니다.')
        units = payload['unit_ids']
        if (payload['serials_confirmed'] is not True or not isinstance(units, list)
                or not units or any(not isinstance(nid, str) for nid in units)
                or len(set(units)) != len(units)):
            raise DomainError('CLAIM_UNITS_REQUIRED', '신고 일련번호와 직접 대조한 개별 보드를 선택하고 확인하세요.')
        for nid in units:
            unit = index.get(nid)
            if not unit or unit['type'] != 'ProductUnit' or unit['serial'] not in intake['reported_serials']:
                raise DomainError('CLAIM_SERIAL_MISMATCH', '선택한 보드가 신고 일련번호에 없습니다. 근거를 보완하고 다시 확인하세요.')
        if any(node['type'] == 'QualityCase' and node['intake_id'] == intake['id'] for node in nodes):
            raise DomainError('CLAIM_CASE_EXISTS', '이 접수의 품질 사건이 이미 등록됐습니다. 기존 사건을 확인하세요.')
        claim = dict(_bare(intake), intake_state='confirmed')
        case = {'id': 'quality-case:submitted:' + digest(intake['id']), 'type': 'QualityCase',
                'intake_id': intake['id'], 'unit_ids': units,
                **{key: intake[key] for key in ('customer_name', 'reported_at', 'reported_symptom')}}
        return [claim, case]
    if action == 'intake_file':
        # One waiting claim file -> a ClaimIntake proposal. Conversion only: no board link,
        # no cause, no defect confirmation. Values missing from the file stay missing.
        _fields(payload, {'file', 'collected_at'})
        path = _inbox_file('claim', payload['file'])
        try:
            result = convert_claim(path, collected_at=payload['collected_at'])
        except ValueError as exc:
            raise DomainError('CLAIM_FILE_UNREADABLE', '클레임 파일을 읽지 못했습니다: ' + str(exc)) from None
        report = result['report']
        if report['state'] != 'needs_review':
            raise DomainError('CLAIM_CONVERSION_BLOCKED', '필수 항목이 없거나 값이 맞지 않습니다: '
                              + '; '.join(i['code'] for i in report['issues']))
        intake = next(n for n in result['nodes'] if n['type'] == 'ClaimIntake')
        if any(node['type'] == 'ClaimIntake' and node['external_claim_id'] == intake['external_claim_id']
               and node['customer_name'] == intake['customer_name'] for node in nodes):
            raise DomainError('CLAIM_ALREADY_REGISTERED', '같은 고객·클레임 번호가 이미 승인됐습니다.')
        return [_bare(intake)]
    if action == 'trace_case':
        # Serial -> board by the recorded serial only. Any serial that does not match stops here
        # for a person; the screen never guesses a near match.
        _fields(payload, {'intake_id'})
        intake = _of_type(index, payload['intake_id'], 'ClaimIntake', '승인된 클레임 접수를 선택하세요.')
        if intake['intake_state'] == 'rejected':
            raise DomainError('INVALID_STATE', '반려된 접수로 품질 사건을 만들 수 없습니다.')
        if any(node['type'] == 'QualityCase' and node['intake_id'] == intake['id'] for node in nodes):
            raise DomainError('CLAIM_CASE_EXISTS', '이 접수의 품질 사건이 이미 등록됐습니다.')
        links = link_serials(intake, [n for n in nodes if n['type'] == 'ProductUnit'])
        unresolved = [l['serial'] + ' (' + l['match_state'] + ')' for l in links if l['match_state'] != MATCHED]
        if unresolved:
            raise DomainError('CLAIM_SERIAL_UNRESOLVED',
                              '사람이 확인해야 하는 일련번호가 있습니다: ' + ', '.join(unresolved))
        case = prepare_quality_case(dict(intake, intake_state='confirmed'), links)
        if case['node'] is None:
            raise DomainError('CLAIM_CASE_NOT_READY', case['reason'])
        return [dict(_bare(intake), intake_state='confirmed'), _bare(case['node'])]
    if action == 'containment':
        # Investigation scope and the two request drafts. Generating a request is not sending it
        # and not a defect confirmation; the scope lists boards to hold, not faulty boards.
        _fields(payload, {'case_id', 'prepared_at'})
        context = _case_context(nodes, index, payload['case_id'])
        hold = _hold(context)
        prepared = prepare_containment(context['case'], context['line_id'], context['records'], hold,
                                       prepared_at=payload['prepared_at'],
                                       external_claim_id=context['intake']['external_claim_id'],
                                       configuration_id=context['configuration_id'])
        if not prepared['nodes']:
            raise DomainError('CLAIM_SCOPE_NOT_READY', '; '.join(s['reason'] for s in prepared['skipped']))
        candidate = candidate_scope(context['case'], context['line_id'], context['records'],
                                    c_operations_shipments(context['operations']))
        records = prepared['nodes'] + ([candidate['node']] if candidate['node'] else [])
        return [_bare(node) for node in records]
    if action == 'test_request':
        _fields(payload, {'case_id', 'prepared_at'})
        context = _case_context(nodes, index, payload['case_id'])
        if any(n['type'] == 'TestRequest' and n['case_id'] == payload['case_id'] for n in nodes):
            raise DomainError('TEST_REQUEST_EXISTS', '이 사건의 시험 의뢰가 이미 등록됐습니다.')
        candidate = candidate_scope(context['case'], context['line_id'], context['records'],
                                    c_operations_shipments(context['operations']))
        test = prepare_test_request(context['case'], context['line_id'], _hold(context),
                                    prepared_at=payload['prepared_at'],
                                    external_claim_id=context['intake']['external_claim_id'],
                                    configuration_id=context['configuration_id'], candidate_result=candidate)
        return [_bare(test['node'])]
    if action == 'test_results':
        # The test team's file, read as written. C never edits an outcome or a disposition.
        # Sent in parts because the common draft takes at most RECORD_LIMIT records at a time.
        _fields(payload, {'case_id', 'file', 'collected_at'}, {'part'})
        context = _case_context(nodes, index, payload['case_id'])
        request = next((n for n in nodes if n['type'] == 'TestRequest' and n['case_id'] == payload['case_id']), None)
        if request is None:
            raise DomainError('NOT_FOUND', '먼저 시험 의뢰를 승인하세요.')
        path = _inbox_file('result', payload['file'])
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / path.name
            copy.write_bytes(path.read_bytes())
            read = read_test_results(copy, request, context['case'], collected_at=payload['collected_at'])
        if read['state'] != 'needs_review':
            raise DomainError('TEST_RESULT_BLOCKED', '결과 파일에 고칠 행이 있습니다: '
                              + '; '.join(f"{i.get('locator') or '파일'} {i['code']}" for i in read['issues']))
        stored = {n['id'] for n in nodes if n['type'] == 'QualityTestResult'}
        pending = [n for n in read['nodes'] if n['type'] == 'QualityTestResult' and n['id'] not in stored]
        if not pending:
            raise DomainError('TEST_RESULTS_COMPLETE', '이 파일의 결과는 모두 등록됐습니다.')
        return [_bare(node) for node in pending[:RECORD_LIMIT]]
    if action == 'reproduction':
        # Counts the test team's dispositions. An undetermined quantity is not stored as 0.
        _fields(payload, {'case_id', 'collected_at'})
        context = _case_context(nodes, index, payload['case_id'])
        request = next((n for n in nodes if n['type'] == 'TestRequest' and n['case_id'] == payload['case_id']), None)
        if request is None:
            raise DomainError('NOT_FOUND', '먼저 시험 의뢰를 승인하세요.')
        results = [n for n in nodes if n['type'] == 'QualityTestResult' and n['case_id'] == payload['case_id']]
        if not results:
            raise DomainError('NOT_FOUND', '먼저 테스트팀 결과를 승인하세요.')
        reproduction = reproduction_requirement(context['case'], request, results,
                                                c_operations_replacement(context['operations']),
                                                configuration_id=context['configuration_id'])
        if reproduction['node'] is None:
            raise DomainError('REPRODUCTION_UNDETERMINED',
                              '재생산 수량이 미정입니다. 0으로 저장하지 않습니다: ' + str(reproduction['reason']))
        return [_bare(reproduction['node'])]
    raise DomainError('INVALID_CLAIM_ACTION', '지원하지 않는 클레임 작업입니다.')


# ---------------------------------------------------------------- read-only views
# The common screen's C reads, wired like B's /api/fulfillment/query. Each view reads one explicit
# approved graph version through the shared lookup (audited like any read) and returns what a person
# needs to see before or after a step: the claim file as written, the serial match, and the request
# documents rendered from the stored records. Nothing here saves, approves or sends anything.
QUERY_ARGUMENTS = {
    'claim_file': {'file', 'version'},
    'serial_match': {'intake_id', 'version'},
    'containment_documents': {'case_id', 'version'},
    'test_request_document': {'case_id', 'version'},
    'result_file': {'case_id', 'file', 'version'},
    'reproduction_document': {'case_id', 'version'},
}
KST = timezone(timedelta(hours=9))


def _now():
    return datetime.now(KST).isoformat(timespec='seconds')


def _plain(value):
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _claim_original(path):
    """The claim file as the customer sent it: mail headers and text body, or the sheet rows."""
    if path.suffix.lower() == '.eml':
        message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        body = message.get_body(preferencelist=('plain',))
        return {'kind': 'mail', 'headers': [[name, str(message.get(name, ''))] for name in ('From', 'To', 'Date', 'Subject')],
                'text': body.get_content() if body is not None else ''}
    rows = PARSERS[path.suffix.lower()](path)['rows']
    columns = [key for key in rows[0] if not key.startswith('_')]
    header = {field: title for title, field in COLUMNS.items()}  # the sheet's own column titles
    return {'kind': 'table', 'columns': [header.get(key, key) for key in columns],
            'rows': [[_plain(row.get(key)) for key in columns] for row in rows]}


def _claim_report(path):
    try:
        return convert_claim(path, collected_at=_now())['report']
    except ValueError as exc:
        raise DomainError('CLAIM_FILE_UNREADABLE', '클레임 파일을 읽지 못했습니다: ' + str(exc)) from None


def _source_report(intake):
    """The waiting file an approved intake came from, matched by customer and claim number."""
    for path in _inbox('claim'):
        try:
            claim = convert_claim(path, collected_at=_now())['report']['claim']
        except ValueError:
            continue
        if (claim.get('customer_name'), claim.get('external_claim_id')) == (intake['customer_name'], intake['external_claim_id']):
            return claim
    return None


def _view_claim_file(nodes, args):
    path = _inbox_file('claim', args['file'])
    report = _claim_report(path)
    claim = report['claim']
    registered = any(n['type'] == 'ClaimIntake' and (n['customer_name'], n['external_claim_id'])
                     == (claim.get('customer_name'), claim.get('external_claim_id')) for n in nodes)
    return {'file': path.name, 'format': report['format'], 'original': _claim_original(path),
            'state': report['state'], 'claim': {key: _plain(value) for key, value in claim.items()},
            'claimed_quantity': report['claimed_quantity'],
            'serials': [unit['serial'] for unit in report['unit_candidates']],
            'issues': report['issues'], 'registered': registered}


def _view_serial_match(nodes, args):
    index = {node['id']: node for node in nodes}
    intake = _of_type(index, args['intake_id'], 'ClaimIntake', '승인된 클레임 접수를 선택하세요.')
    produced = {n['unit_id']: n for n in nodes if n['type'] == 'UnitProductionRecord'}
    rows = []
    for link in link_serials(intake, [n for n in nodes if n['type'] == 'ProductUnit']):
        record = produced.get(link['unit_id'], {})
        line = index.get(record.get('production_line_id'), {})
        rows.append({'serial': link['serial'], 'match_state': link['match_state'], 'unit_id': link['unit_id'],
                     'production_line': line.get('name'), 'produced_at': record.get('produced_at')})
    return {'intake': _bare(intake), 'links': rows,
            'ready': bool(rows) and all(row['match_state'] == MATCHED for row in rows)}


def _stored(nodes, kind, case_id):
    return [n for n in nodes if n['type'] == kind and n.get('case_id') == case_id]


def _view_containment(nodes, args):
    """The production stop and shipment hold documents. Before the step is saved this is the draft
    the person is about to submit; after, it is rendered again from the same approved records."""
    index = {node['id']: node for node in nodes}
    context = _case_context(nodes, index, args['case_id'])
    stored = _stored(nodes, 'ContainmentRequest', args['case_id'])
    prepared_at = stored[0]['prepared_at'] if stored else _now()
    prepared = prepare_containment(context['case'], context['line_id'], context['records'], _hold(context),
                                   prepared_at=prepared_at, external_claim_id=context['intake']['external_claim_id'],
                                   configuration_id=context['configuration_id'])
    return {'saved': bool(stored), 'prepared_at': prepared_at,
            'documents': [{'filename': name, 'markdown': text} for name, text in prepared['documents'].items()],
            'skipped': prepared['skipped']}


def _view_test_request(nodes, args):
    index = {node['id']: node for node in nodes}
    context = _case_context(nodes, index, args['case_id'])
    stored = _stored(nodes, 'TestRequest', args['case_id'])
    prepared_at = stored[0]['prepared_at'] if stored else _now()
    candidate = candidate_scope(context['case'], context['line_id'], context['records'],
                                c_operations_shipments(context['operations']))
    test = prepare_test_request(context['case'], context['line_id'], _hold(context), prepared_at=prepared_at,
                                external_claim_id=context['intake']['external_claim_id'],
                                configuration_id=context['configuration_id'], candidate_result=candidate)
    return {'saved': bool(stored), 'prepared_at': prepared_at, 'unit_count': len(test['node']['unit_ids']),
            'documents': [{'filename': name, 'markdown': text} for name, text in test['documents'].items()]}


def _view_result_file(nodes, args):
    """The test team's file as written, and what C would read from it. C edits no outcome."""
    index = {node['id']: node for node in nodes}
    context = _case_context(nodes, index, args['case_id'])
    request = next(iter(_stored(nodes, 'TestRequest', args['case_id'])), None)
    if request is None:
        raise DomainError('NOT_FOUND', '먼저 시험 의뢰를 승인하세요.')
    path = _inbox_file('result', args['file'])
    table = list(csv.reader(path.read_text(encoding='utf-8').splitlines()))
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / path.name
        copy.write_bytes(path.read_bytes())
        read = read_test_results(copy, request, context['case'], collected_at=_now())
    counts = Counter((n['outcome'], n['disposition']) for n in read['nodes'] if n['type'] == 'QualityTestResult')
    return {'file': path.name, 'header': table[0] if table else [], 'rows': table[1:], 'state': read['state'],
            'issues': read['issues'], 'stored': len(_stored(nodes, 'QualityTestResult', args['case_id'])),
            'counts': [{'outcome': o, 'disposition': d, 'count': n} for (o, d), n in sorted(counts.items())]}


def _view_reproduction(nodes, args):
    """A's request in A's form, copied from the approved ReproductionRequirement. A reads the record
    itself from the graph; this document is the human-readable copy. The requested due date is not in
    the graph (kept out by the team decision of 2026-11-04), so it is read again from the claim file."""
    index = {node['id']: node for node in nodes}
    context = _case_context(nodes, index, args['case_id'])
    requirement = next(iter(_stored(nodes, 'ReproductionRequirement', args['case_id'])), None)
    if requirement is None:
        raise DomainError('NOT_FOUND', '먼저 재생산 요건을 승인하세요.')
    claim = _source_report(context['intake'])
    due = claim.get('requested_due_date') if claim else None
    if not due:
        raise DomainError('MISSING_DUE_DATE', '원래 클레임 파일에서 희망 기한을 찾지 못했습니다. 요청서를 만들 수 없습니다.')
    text = render_reproduction_request(_bare(requirement), context['case'], prepared_at=_now(),
                                       external_claim_id=context['intake']['external_claim_id'],
                                       requested_due_date=due,
                                       delivery_reference=context['intake'].get('delivery_reference'))
    return {'requested_due_date': due, 'requirement': _bare(requirement), 'markdown': text}


VIEWS = {'claim_file': _view_claim_file, 'serial_match': _view_serial_match,
         'containment_documents': _view_containment, 'test_request_document': _view_test_request,
         'result_file': _view_result_file, 'reproduction_document': _view_reproduction}


def query(service, actor, action, arguments):
    """Run one read-only C view on an explicit approved version through the shared lookup."""
    if (not isinstance(action, str) or action not in QUERY_ARGUMENTS
            or not isinstance(arguments, dict) or set(arguments) != QUERY_ARGUMENTS[action]):
        raise DomainError('INVALID_INPUT', '지원하는 클레임 조회와 정확한 입력을 선택하세요.')
    version = arguments['version']
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise DomainError('INVALID_VERSION', '클레임 조회에는 명시적인 데이터 버전이 필요합니다.')
    graph = service.lookup(actor, version=version)
    if not graph['ok']:
        return graph
    view = VIEWS[action](graph['data'], {key: value for key, value in arguments.items() if key != 'version'})
    return {**graph, 'data': view, 'evidence': [], 'conflicts': []}
