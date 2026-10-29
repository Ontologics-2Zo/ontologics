"""Test-team results and reproduction quantities (C 2nd phase, steps 6-2 to 6-5).

Team meeting (2026-10-29): the test team runs the tests and decides each board's
disposition. C only reads what came back, keeps untested boards as untested and
later counts the reproduction quantity for A. Nothing here approves, applies or
changes a result; returned nodes are draft candidates for a human review.
"""
import csv
import hashlib
import io
from pathlib import Path

from ..model import instant
from .intake import _clean, _issue, _read_bounded, _relative_path

RESULT_HEADER = ('보드', '시험 결과', '조치 판정', '관측 시각', '비고')
OUTCOMES = {'불합격': 'fail', '통과': 'pass', '증상 미재현': 'not_reproduced'}
UNTESTED = '미검사'
DISPOSITIONS = {'고객 교체': 'customer_replacement', '보관 대체': 'stock_replacement', '출고 허용': 'release',
                '보류': 'hold', '미결': 'unresolved'}
NEEDS_REVIEW, BLOCKED = 'needs_review', 'blocked'


def _suffix(test_request):
    return test_request['id'].split(':', 1)[1]


def parse_result_csv(path):
    """Rows of the test-team CSV with their row locators; the header must match the request document."""
    text = _read_bounded(path).decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(cell.strip() for cell in rows[0]) != RESULT_HEADER:
        raise ValueError('INVALID_RESULT_HEADER: expected ' + ', '.join(RESULT_HEADER))
    parsed = []
    for number, cells in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in cells):
            continue
        cells = (cells + [''] * len(RESULT_HEADER))[:len(RESULT_HEADER)]
        parsed.append(dict(zip(('serial', 'outcome', 'disposition', 'observed_at', 'note'),
                               (_clean(c) for c in cells)), locator=f'row {number}'))
    return parsed


def read_test_results(path, test_request, case, *, collected_at):
    """6-2: test-team CSV -> QualityTestResult candidates for the boards of one TestRequest.

    Returns {'state', 'nodes', 'untested_unit_ids', 'issues', 'counts'}. A board without a row or
    marked 미검사 is untested and gets no node. Any issue blocks all result nodes for a person to fix.
    """
    instant(collected_at)
    rows = parse_result_csv(path)
    requested = list(test_request['unit_ids'])
    issues, seen, results = [], {}, []
    for row in rows:
        where = row['locator']
        unit_id = 'unit:' + row['serial'] if row['serial'] else None
        if unit_id is None:
            _issue(issues, 'MISSING_FIELD', '보드', where); continue
        if unit_id not in requested:
            _issue(issues, 'UNIT_NOT_REQUESTED', row['serial'] + ' is not in the test request', where); continue
        if unit_id in seen:
            _issue(issues, 'DUPLICATE_RESULT', row['serial'] + ' also appears in ' + seen[unit_id], where); continue
        seen[unit_id] = where
        if row['outcome'] == UNTESTED:
            if row['disposition']:
                _issue(issues, 'DISPOSITION_WITHOUT_TEST', row['serial'] + ' is untested but has a disposition', where)
            continue
        if row['outcome'] not in OUTCOMES:
            _issue(issues, 'INVALID_OUTCOME', str(row['outcome']), where); continue
        if row['disposition'] not in DISPOSITIONS:
            _issue(issues, 'INVALID_DISPOSITION', str(row['disposition']), where); continue
        try:
            instant(row['observed_at'])
        except (ValueError, TypeError):
            _issue(issues, 'TIMEZONE_REQUIRED', 'observed time needs date, time and UTC offset', where); continue
        results.append((unit_id, row))

    raw = Path(path).read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    key = checksum[:16]
    source = {'id': 'source:claim_response:test-results:' + key, 'type': 'SourceDocument', 'origin': 'synthetic',
              'evidence': [], 'url': 'urn:ontologics:claim_response:' + Path(path).name, 'path': _relative_path(path),
              'sha256': checksum, 'version': 'test-results-demo-1', 'license': 'Project demonstration data',
              'collected_at': collected_at, 'kind': 'synthetic_test_results'}
    evidence = {'id': 'evidence:claim_response:test-results:' + key, 'type': 'Evidence', 'origin': 'synthetic',
                'evidence': [], 'source_id': source['id'], 'locator': f'CSV header row 1, result rows 2-{len(rows) + 1}',
                'description': 'Fictional test-team results and dispositions; observations, not confirmed causes.'}
    nodes = [source, evidence]
    if not issues:
        for unit_id, row in results:
            node = {'id': 'test-result:' + _suffix(test_request) + ':' + unit_id.split(':', 1)[1],
                    'type': 'QualityTestResult', 'origin': 'synthetic', 'evidence': [evidence['id']],
                    'case_id': case['id'], 'test_request_id': test_request['id'], 'unit_id': unit_id,
                    'outcome': OUTCOMES[row['outcome']], 'disposition': DISPOSITIONS[row['disposition']],
                    'observed_at': row['observed_at']}
            if row['note']:
                node['note'] = row['note']
            nodes.append(node)
    tested = {unit_id for unit_id, _ in results}
    untested = [u for u in requested if u not in tested]
    counts = {'requested': len(requested), 'tested': len(tested), 'untested': len(untested)}
    for _, row in results:
        key = DISPOSITIONS[row['disposition']]
        counts[key] = counts.get(key, 0) + 1
    return {'state': BLOCKED if issues else NEEDS_REVIEW, 'nodes': nodes, 'untested_unit_ids': untested,
            'issues': issues, 'counts': counts}
