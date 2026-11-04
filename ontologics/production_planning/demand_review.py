"""Presentation of host-recomputed contract demand, never an approval verifier.

The trusted host supplies read_contract_demand's result (and optional previous
result). This function does not authenticate those dicts, verify provenance,
recheck source bytes or issue confirmation rights. Previous data is untrusted
display history. Keep raw reports/envelopes so rejected claims are not lost.
"""
from __future__ import annotations

from copy import deepcopy
import json


def _changes(before, after, path='', *, before_present=True, after_present=True):
    """Index-based display comparison; presence and JSON scalar types matter."""
    if before_present and after_present and type(before) is type(after):
        if isinstance(before, dict):
            changes = []
            for key in sorted(before.keys() | after.keys()):
                changes.extend(_changes(before.get(key), after.get(key),
                    f'{path}.{key}' if path else key,
                    before_present=key in before, after_present=key in after))
            return changes
        if isinstance(before, list):
            changes = []
            for index in range(max(len(before), len(after))):
                changes.extend(_changes(before[index] if index < len(before) else None,
                    after[index] if index < len(after) else None, f'{path}[{index}]',
                    before_present=index < len(before), after_present=index < len(after)))
            return changes
        # Invalid nonfinite quantity claims still deserve display history.
        if json.dumps(before, allow_nan=True) == json.dumps(after, allow_nan=True):
            return []
    return [dict(path=path, before_present=before_present, after_present=after_present,
                 before=deepcopy(before), after=deepcopy(after))]


def build_demand_review(result: dict, *, previous: dict | None = None) -> dict:
    """Build review scopes and value/evidence changes from host-owned results.

    Consume the complete read_contract_demand result, not just its report.
    Query failures have status=query_failed and no review items. Otherwise
    source/original/interpretation/candidates/diagnostics remain in the copied
    revision.after report; revision.before holds the complete previous report.
    Changes use positional item/claim indices, not inferred item identities.

    candidate_for_review means material is available to inspect. It is neither
    business confirmation nor eligibility for downstream calculation. Even a
    dict claiming verified=true is display input only: this output always has
    confirmation.verified=false. No persistence or independent validation occurs.
    """
    query, report = deepcopy(result['query']), deepcopy(result['report'])
    before = deepcopy(previous['report']) if previous is not None else None
    changes = _changes(before, report) if previous is not None else []
    envelopes = [query.get('lookup_response'), *query.get('configuration_responses', [])]
    failed = (report is None or query.get('configurations') is None or bool(query.get('errors'))
              or any(e is not None and e.get('ok') is not True for e in envelopes))
    view = dict(
        purpose='host_recomputed_demand_presentation_only',
        status='query_failed' if failed else 'invalid' if report['errors'] or not report['valid'] else 'needs_confirmation',
        data_version=report['binding']['data_version'] if report is not None else None,
        query=query, items=[],
        errors=deepcopy(report['errors']) if report is not None else [],
        unresolved=deepcopy(report['unresolved']) if report is not None else [],
        questions=deepcopy(report['questions']) if report is not None else [],
        confirmation=dict(verified=False, state='unavailable'),
        revision=dict(history_verified=False, comparison='positional_display_only',
                      changed=bool(changes), before=before, after=report, changes=changes))
    if failed:
        return view

    for index, original_item in enumerate(report['items']):
        item = deepcopy(original_item)
        prefix = f'items[{index}]'
        fields, candidate = item['fields'], item['contract_item_candidate']
        scopes = []

        def scope(name, label, names, *, extra_paths=(), available=None):
            paths = [prefix+'.fields.'+field for field in names] + [prefix+'.'+p for p in extra_paths]
            # Intake diagnostics use items[i].field, whereas report values use
            # items[i].fields.field. Preserve both contracts without rewriting.
            issue_paths = [path.replace(prefix+'.fields.', prefix+'.', 1) for path in paths]
            blocked = any(
                not error['path'].startswith('items[') or error['path'] == prefix or
                any(error['path'] == path or error['path'].startswith(path+'.') for path in issue_paths)
                for error in report['errors'])
            supplied = all(fields[field]['state'] == 'supported' for field in names)
            availability = ('blocked_by_report_errors' if blocked else
                            available if available is not None else
                            'candidate_for_review' if supplied else 'needs_material')
            scopes.append(dict(scope=name, label=label, paths=paths,
                               availability=availability, confirmation='not_recorded'))

        scope('configuration', '제품·개정·변형과 기존 구성 선택',
              ['product_model', 'revision', 'variant'],
              extra_paths=('fields.manufacturer', 'fields.configuration_id', 'proposed_configuration_id'),
              available='candidate_for_review' if candidate['configuration_id'] is not None and
              all(fields[field]['state'] == 'supported' for field in ('product_model', 'revision', 'variant'))
              else 'needs_material')
        for name, label in [('ordered_quantity', '주문량'), ('fulfilled_quantity', '기존 이행량'),
                            ('allocated_quantity', '기존 배정량'), ('planned_input_quantity', '신규 생산 검토량')]:
            scope(name, label, [name, 'unit'])
        scope('delivery_value', '원문 납기 값과 정밀도', ['delivery_due'])
        # There is no destination input in the current intake contract, even
        # when delivery_due already has datetime precision. Never invent one.
        scope('delivery_arrival', '정확 도착 시각·offset·배송지 자료', ['delivery_due'], available='needs_material')
        scope('constraints', '개정 변경·대체 제약의 의미와 허용 범위', ['constraints'],
              available='needs_business_decision')
        item['review_scopes'] = scopes
        view['items'].append(item)
    return view
