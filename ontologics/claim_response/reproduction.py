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
from .fixtures import batch_serials
from .intake import _clean, _issue, _read_bounded, _relative_path
from .trace import C_OPERATIONS_BASIS

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


# ------------------------------------------------------------ 6-3 reproduction quantity
# Replacement stock (C-R001~007, line C-02) is read from the temporary C operations record.
# MVP assumption: it is outside the claim scope, usable and not allocated to other orders.
# When B's shared allocation data is linked (3-3), replace only this adapter.

CONFIRMED, LOWER_BOUND, UNDETERMINED = 'confirmed', 'lower_bound', 'undetermined'


def c_operations_replacement(raw):
    """Usable replacement boards from the C operations record (temporary basis)."""
    batch = next(b for b in raw['batches'] if b['name'] == 'replacement')
    return {'unit_ids': ['unit:' + s for s in batch_serials(batch)], 'basis': [C_OPERATIONS_BASIS],
            'evidence': ['evidence:claim_response:batch:replacement'], 'configuration_id': raw['configuration_id']}


def reproduction_requirement(case, test_request, results, replacement, *, configuration_id):
    """6-3: count the good boards A must make from the test team's dispositions.

    customer = boards with customer_replacement, stock = boards with stock_replacement; each board
    has one result, so it is counted once. unresolved = requested boards without a result plus
    results marked unresolved. required = max(0, customer + stock - replacement).
    - no unresolved boards          -> confirmed
    - unresolved boards, some demand -> lower_bound (at least this many; the final number may grow)
    - unresolved boards, no demand   -> undetermined: no node, because 0 would read as "nothing to make"
    """
    results = [r for r in results if r.get('type') == 'QualityTestResult'
               and r.get('test_request_id') == test_request['id']]
    by_unit = {r['unit_id']: r for r in results}
    customer = [r for r in results if r['disposition'] == 'customer_replacement']
    stock = [r for r in results if r['disposition'] == 'stock_replacement']
    untested = [u for u in test_request['unit_ids'] if u not in by_unit]
    unresolved = len(untested) + sum(r['disposition'] == 'unresolved' for r in results)
    available = [u for u in replacement['unit_ids'] if u not in test_request['unit_ids']]
    demand = len(customer) + len(stock)
    required = max(0, demand - len(available))
    summary = {'customer_replacement_quantity': len(customer), 'stock_replacement_quantity': len(stock),
               'available_replacement_quantity': len(available), 'required_good_quantity': required,
               'unresolved_quantity': unresolved, 'untested_quantity': len(untested),
               'basis': replacement['basis']}
    if unresolved and not demand:
        return dict(summary, state=UNDETERMINED, node=None, required_good_quantity=None,
                    reason='no confirmed replacement demand yet and some boards are unresolved or untested')
    state = LOWER_BOUND if unresolved else CONFIRMED
    evidence = set(case['evidence']) | set(replacement['evidence'])
    for r in results:
        evidence |= set(r['evidence'])
    node = {'id': 'reproduction:' + case['id'].split(':', 1)[1], 'type': 'ReproductionRequirement',
            'origin': case['origin'], 'evidence': sorted(evidence), 'case_id': case['id'],
            'configuration_id': configuration_id, 'unit': 'piece', 'assessment_state': state,
            'test_result_ids': sorted(r['id'] for r in results),
            **{k: summary[k] for k in ('customer_replacement_quantity', 'stock_replacement_quantity',
                                       'available_replacement_quantity', 'required_good_quantity',
                                       'unresolved_quantity')}}
    if available:
        node['replacement_unit_ids'] = available
    return dict(summary, state=state, node=node, reason=None)
