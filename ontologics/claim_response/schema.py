"""C claim response business rules and compatibility exports of shared graph metadata.

Definitions live in domain_schema (C_SCHEMA). The common validator calls
validate_claim_nodes() only when a C type is present; generic shape, reference,
list-reference, origin and evidence checks stay in model.validate_graph.
Importing this module never registers a type or changes approval/storage.
"""
from __future__ import annotations

from .. import domain_schema
from ..model import _string_list, _text, instant

SCHEMA = domain_schema.subset(domain_schema.C_SCHEMA, domain_schema.C_TYPES)
OPTIONAL_FIELDS = domain_schema.subset(domain_schema.OPTIONAL_FIELDS, SCHEMA)
OPTIONAL_REFERENCES = domain_schema.subset(domain_schema.OPTIONAL_REFERENCES, SCHEMA)
LIST_REFERENCES = domain_schema.subset(domain_schema.LIST_REFERENCES, SCHEMA)
STRUCTURED_FIELDS = domain_schema.subset(domain_schema.STRUCTURED_FIELDS, SCHEMA)

# reported_symptom is the customer's reported wording only; it never asserts a confirmed fault.
ENUMS = {'ClaimIntake': {'intake_state': ('needs_review', 'confirmed', 'rejected')},
         'QualityScope': {'scope_state': ('hold', 'candidate')},
         'ContainmentRequest': {'request_kind': ('production_stop', 'shipment_hold')}}
TIMES = {'UnitProductionRecord': ('produced_at',), 'ClaimIntake': ('reported_at',), 'QualityCase': ('reported_at',),
         'QualityScope': ('as_of', 'start', 'end'), 'ContainmentRequest': ('prepared_at',)}
# One production record per board in this MVP; reproduction/rework records are not modelled yet.
IDENTITIES = {'ProductionLine': ('company', 'line_code'), 'ClaimIntake': ('customer_name', 'external_claim_id'),
              'UnitProductionRecord': ('unit_id',)}

# 2nd phase (PR #7). The test team decides outcome and disposition; C copies them as returned.
ENUMS.update({'QualityTestResult': {'outcome': ('fail', 'pass', 'not_reproduced'),
                                    'disposition': ('customer_replacement', 'stock_replacement', 'release',
                                                    'hold', 'unresolved')},
              'ReproductionRequirement': {'assessment_state': ('confirmed', 'lower_bound'), 'unit': ('piece',)}})
TIMES.update({'TestRequest': ('prepared_at',), 'QualityTestResult': ('observed_at',)})
# One result per requested board (a board is counted once); one requirement per case and configuration.
IDENTITIES.update({'QualityTestResult': ('test_request_id', 'unit_id'),
                   'ReproductionRequirement': ('case_id', 'configuration_id')})
C2_QUANTITIES = domain_schema.C2_QUANTITIES


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _typed(index, nid, kind):
    node = index.get(nid) if isinstance(nid, str) else None
    return node if node and node.get('type') == kind else None


def _validate_test_result(index, nid, n, error):
    """Results only for requested boards of the same case; replacement kinds match the claimed boards."""
    request = _typed(index, n.get('test_request_id'), 'TestRequest')
    unit = n.get('unit_id') if isinstance(n.get('unit_id'), str) else None
    if request and request.get('case_id') != n.get('case_id'):
        error(nid, 'CASE_MISMATCH', 'case_id must match the case of test_request_id')
    if request and _string_list(request.get('unit_ids')) and unit not in request['unit_ids']:
        error(nid, 'UNIT_NOT_REQUESTED', 'Results are accepted only for boards in the test request')
    case = _typed(index, n.get('case_id'), 'QualityCase')
    claimed = set(case['unit_ids']) if case and _string_list(case.get('unit_ids')) else set()
    # Customer replacements are the claimed boards; stock replacements are never the claimed boards.
    if case and n.get('disposition') == 'customer_replacement' and unit not in claimed:
        error(nid, 'DISPOSITION_TARGET_MISMATCH', 'customer_replacement is only for boards in the case')
    if n.get('disposition') == 'stock_replacement' and unit in claimed:
        error(nid, 'DISPOSITION_TARGET_MISMATCH', 'stock_replacement is not for the claimed boards')


# Which test-team dispositions make a board a replacement demand. hold/release are decisions
# without demand; 'unresolved', a missing result or conflicting results leave the board unresolved.
DEMAND = ('customer_replacement', 'stock_replacement')


def board_configuration(index, board):
    """The configuration of a board, from the existing ProductUnit. None if it cannot be read."""
    unit = _typed(index, board, 'ProductUnit')
    return unit.get('configuration_id') if unit else None


def calculation_basis(index, case_id, configuration_id=None):
    """Boards and results a ReproductionRequirement of case_id must be computed from.

    Target boards: union of unit_ids of every TestRequest of the case in the same graph
    (a board in two requests is one board). With configuration_id, only the boards whose
    ProductUnit.configuration_id is that configuration: one case can cover several
    configurations (L and L1), and each is produced separately, so replacement demand and
    unresolved boards are never shared between them. Results: every QualityTestResult of
    the case for a target board, from any of those requests. Per board:
      - no result                          -> untested (unresolved)
      - one or several identical results   -> that disposition, counted once
      - results that disagree              -> conflict (unresolved); no result is picked
    Returns {'targets', 'effective', 'results', 'conflicts'}; effective maps board -> disposition or None.
    """
    targets = set()
    for node in index.values():
        if node.get('type') == 'TestRequest' and node.get('case_id') == case_id and _string_list(node.get('unit_ids')):
            targets.update(node['unit_ids'])
    if configuration_id is not None:
        targets = {board for board in targets if board_configuration(index, board) == configuration_id}
    by_board = {}
    for rid, node in index.items():
        if (node.get('type') == 'QualityTestResult' and node.get('case_id') == case_id
                and isinstance(node.get('unit_id'), str) and node['unit_id'] in targets):
            by_board.setdefault(node['unit_id'], []).append(rid)
    effective, conflicts = {}, set()
    for board in targets:
        # Malformed values are compared by text so that validation never raises on them.
        decisions = {tuple(v if isinstance(v, str) else repr(v)
                           for v in (index[rid].get('outcome'), index[rid].get('disposition')))
                     for rid in by_board.get(board, [])}
        if len(decisions) == 1:
            effective[board] = next(iter(decisions))[1]
        else:
            effective[board] = None
            if len(decisions) > 1:
                conflicts.add(board)
    return {'targets': targets, 'effective': effective, 'results': by_board, 'conflicts': conflicts}


def _validate_reproduction(index, nid, n, error, warnings):
    """Check the submitted quantities against the graph's own requests and results.

    required_good_quantity = max(0, customer + stock replacement - available replacement stock),
    and customer/stock/unresolved must equal what calculation_basis() counts per board.
    """
    values = {key: n.get(key) for key in C2_QUANTITIES}
    bad = [key for key, value in values.items() if not _nonnegative_int(value)]
    for key in bad:
        error(nid, 'INVALID_QUANTITY', key + ' must be a nonnegative integer piece count')
    if not bad:
        expected = max(0, values['customer_replacement_quantity'] + values['stock_replacement_quantity']
                       - values['available_replacement_quantity'])
        if values['required_good_quantity'] != expected:
            error(nid, 'QUANTITY_MISMATCH', f'required_good_quantity must be {expected}')
        if (n.get('assessment_state') == 'confirmed') != (values['unresolved_quantity'] == 0):
            error(nid, 'ASSESSMENT_STATE_MISMATCH', 'confirmed needs 0 unresolved; lower_bound needs some')

    basis = calculation_basis(index, n.get('case_id'), n.get('configuration_id'))
    if not basis['targets']:
        error(nid, 'NO_TEST_REQUEST', 'A reproduction requirement needs a test request of the same case '
              'with boards of its configuration')
    for board in sorted(basis['conflicts']):
        warnings.append({'node_id': nid, 'code': 'RESULT_CONFLICT', 'detail': board})
    counted = {kind: sum(1 for d in basis['effective'].values() if d == kind) for kind in DEMAND}
    unresolved = sum(1 for d in basis['effective'].values() if d is None or d == 'unresolved')
    if not bad and basis['targets']:
        for kind, key in (('customer_replacement', 'customer_replacement_quantity'),
                          ('stock_replacement', 'stock_replacement_quantity')):
            if values[key] != counted[kind]:
                error(nid, 'QUANTITY_MISMATCH', f'{key} must be {counted[kind]} from the test results')
        if values['unresolved_quantity'] != unresolved:
            # Untested boards, 'unresolved' results and conflicting results are never a confirmed 0.
            error(nid, 'UNRESOLVED_MISMATCH', f'unresolved_quantity must be {unresolved} '
                  '(untested, unresolved or conflicting boards)')
        if counted['customer_replacement'] + counted['stock_replacement'] == 0 and unresolved:
            error(nid, 'UNDETERMINED_NOT_STORED', 'No confirmed demand with unresolved boards is undetermined; '
                  'it is not stored as a requirement')

    ids = n['test_result_ids'] if _string_list(n.get('test_result_ids')) else []
    seen_boards = {}
    for rid in ids:
        result = _typed(index, rid, 'QualityTestResult')
        if result is None:
            continue
        if result.get('case_id') != n.get('case_id'):
            error(nid, 'CASE_MISMATCH', rid + ' belongs to another case')
        board = result.get('unit_id') if isinstance(result.get('unit_id'), str) else None
        if board in seen_boards:
            # Retests or a second request for the same board: one board is counted once.
            error(nid, 'DUPLICATE_BOARD_RESULT', f'{rid} and {seen_boards[board]} are both for {board}')
        seen_boards[board] = rid
        if board in basis['conflicts']:
            error(nid, 'RESULT_CONFLICT', rid + ' is one of conflicting results for ' + board)
        unit = _typed(index, board, 'ProductUnit')
        if unit and unit.get('configuration_id') != n.get('configuration_id'):
            error(nid, 'CONFIGURATION_MISMATCH', rid + ' is for another configuration')
    # Counted per configuration: a result for another configuration is reported above, not counted here.
    listed = {kind: sum(1 for rid in ids if _typed(index, rid, 'QualityTestResult')
                        and index[rid].get('disposition') == kind
                        and board_configuration(index, index[rid].get('unit_id')) == n.get('configuration_id'))
              for kind in DEMAND}
    if not bad and ids and (listed['customer_replacement'] != values['customer_replacement_quantity']
                            or listed['stock_replacement'] != values['stock_replacement_quantity']):
        error(nid, 'QUANTITY_MISMATCH', 'replacement quantities must equal the listed test results')

    if 'replacement_unit_ids' in n:
        # Optional text list (see domain_schema); each entry must be a ProductUnit of the same configuration.
        refs = n['replacement_unit_ids']
        if not _string_list(refs):
            error(nid, 'INVALID_REFERENCE', 'replacement_unit_ids must be a nonempty list of unique IDs')
            return
        # A known replacement demand remains unavailable even when another case recorded it.
        # This does not infer stock ownership/allocation or reject every untested board.
        replacement_targets = {row['unit_id'] for row in index.values()
                               if row.get('type') == 'QualityTestResult'
                               and row.get('disposition') in DEMAND
                               and isinstance(row.get('unit_id'), str)}
        for ref in refs:
            unit = _typed(index, ref, 'ProductUnit')
            if unit is None:
                error(nid, 'INVALID_REFERENCE', 'replacement_unit_ids -> ProductUnit: ' + ref)
            elif unit.get('configuration_id') != n.get('configuration_id'):
                error(nid, 'CONFIGURATION_MISMATCH', ref + ' is for another configuration')
            if ref in basis['targets']:
                # A board sent to the test team (to be replaced, held or still open) cannot offset itself.
                error(nid, 'REPLACEMENT_TARGET_CONFLICT', ref + ' is a test target of this case, not replacement stock')
            elif ref in replacement_targets:
                error(nid, 'REPLACEMENT_TARGET_CONFLICT', ref + ' already requires replacement in another case')
        if not bad and len(refs) != values['available_replacement_quantity']:
            error(nid, 'QUANTITY_MISMATCH', 'replacement_unit_ids must list available_replacement_quantity boards')


def validate_claim_nodes(index, error, warnings):
    """Append C-only diagnostics for one validation run (values, times, counts, paths, identities)."""
    identities = {}
    for nid, n in index.items():
        kind = n.get('type')
        if not isinstance(kind, str) or kind not in SCHEMA:
            continue
        for key, allowed in ENUMS.get(kind, {}).items():
            if n.get(key) not in allowed:
                error(nid, 'INVALID_ENUM', key + ' must be one of ' + ', '.join(allowed))
        times = {}
        for key in TIMES.get(kind, ()):
            if key in n:
                try: times[key] = instant(n[key])
                except (ValueError, TypeError) as exc: error(nid, 'INVALID_TIME', f'{key}: {exc}')
        if 'start' in times and 'end' in times and times['start'] >= times['end']:
            error(nid, 'INVALID_INTERVAL', 'start must precede end')
        if kind == 'UnitProductionRecord' and 'production_work_id' in n:
            # Unsupported link: the shared ProductionWork type exists, but which work a C board
            # belongs to is not agreed with B (I02). Any value is rejected until that contract exists.
            error(nid, 'PRODUCTION_WORK_UNSUPPORTED', 'production_work_id is not supported yet; leave it out')
        if kind == 'ClaimIntake':
            serials = n.get('reported_serials')
            if not _string_list(serials):
                error(nid, 'INVALID_FIELD_TYPE', 'reported_serials must be unique nonempty text values'); serials = None
            qty = n.get('claimed_quantity')
            if not _positive_int(qty):
                error(nid, 'INVALID_QUANTITY', 'claimed_quantity must be a positive integer piece count')
            elif serials is not None and qty != len(serials):
                # Customer counts and serial lists can disagree; a person resolves it, the graph keeps both.
                warnings.append({'node_id': nid, 'code': 'CLAIM_QUANTITY_SERIAL_MISMATCH'})
        if kind == 'QualityCase':
            intake = index.get(n.get('intake_id')) if isinstance(n.get('intake_id'), str) else None
            if intake and intake.get('type') == 'ClaimIntake' and intake.get('intake_state') != 'confirmed':
                error(nid, 'INTAKE_NOT_CONFIRMED', 'Quality cases start only from a human-confirmed claim intake')
        if kind == 'TestRequest' and 'draft_version' in n and not _positive_int(n['draft_version']):
            error(nid, 'INVALID_FIELD_TYPE', 'draft_version must be a positive integer')
        if kind == 'QualityTestResult':
            _validate_test_result(index, nid, n, error)
        if kind == 'ReproductionRequirement':
            _validate_reproduction(index, nid, n, error, warnings)
        if kind == 'ContainmentRequest':
            if 'draft_version' in n and not _positive_int(n['draft_version']):
                error(nid, 'INVALID_FIELD_TYPE', 'draft_version must be a positive integer')
            if n.get('request_kind') == 'production_stop' and 'production_line_id' not in n:
                error(nid, 'PRODUCTION_LINE_REQUIRED', 'Production stop requests name the production line')
            scope = index.get(n.get('scope_id')) if isinstance(n.get('scope_id'), str) else None
            if scope and scope.get('type') == 'QualityScope' and scope.get('case_id') != n.get('case_id'):
                error(nid, 'CASE_MISMATCH', 'case_id must match the case of scope_id')
            # A request names the line of the scope it points to. A request for another line is not
            # inferred from the scope; it needs its own meaning and evidence contract first.
            if (scope and scope.get('type') == 'QualityScope' and 'production_line_id' in n
                    and n['production_line_id'] != scope.get('production_line_id')):
                error(nid, 'LINE_MISMATCH', 'production_line_id must be the production line of scope_id')
        keys = IDENTITIES.get(kind)
        if keys and all(_text(n.get(key)) for key in keys):
            identity = (kind, *(n[key] for key in keys))
            if identity in identities: error(nid, 'DUPLICATE_IDENTITY', identities[identity])
            identities[identity] = nid
