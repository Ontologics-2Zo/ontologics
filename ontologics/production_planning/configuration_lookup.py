"""Read-only public-session adapter. No store, validator, host or fallback DB."""
from __future__ import annotations

from copy import deepcopy

from .contract_intake import _configuration_valid


def lookup_contract_configurations(session, *, data_version: int) -> dict:
    """Preserve all public envelopes; a failed/inconsistent query has no bundle.

    Read the exact full snapshot and compare each configuration detail against
    it, including model/revision and BOM/component joins. Success identifies a
    catalog snapshot, not contract registration, source interpretation or human
    confirmation. The session is trusted host state, never model arguments.
    """
    result = dict(lookup_response=None, configuration_responses=[], configurations=None, errors=[])

    def error(code, message):
        result['errors'].append(dict(path='configurations', code=code, message=message))

    if type(data_version) is not int or data_version < 0:
        error('INVALID_VERSION', 'Explicit nonnegative context.data_version required'); return result

    def call(tool, args):
        # Preserve failure envelopes. A thrown transport exception is distinct
        # from an empty catalog; no exception contents (possibly secrets) leak.
        try: response = session.call(tool, args)
        except Exception:
            error('PUBLIC_QUERY_EXCEPTION', 'Public query raised; inspect the host audit path'); return None
        if not isinstance(response, dict) or type(response.get('ok')) is not bool:
            error('INVALID_PUBLIC_RESPONSE', 'Expected a public response envelope'); return response
        if response['ok'] and (type(response.get('data_version')) is not int or response['data_version'] != data_version):
            error('REFERENCE_VERSION_MISMATCH', 'Public response must identify the exact requested version')
        return response

    response = call('lookup', {'version': data_version})
    result['lookup_response'] = response
    if result['errors'] or response is None or not response['ok']: return result
    nodes = response.get('data')
    if (not isinstance(nodes, list) or any(not isinstance(n, dict) or not isinstance(n.get('id'), str) for n in nodes)
            or len({n['id'] for n in nodes}) != len(nodes)):
        error('INVALID_PUBLIC_RESPONSE', 'Expected complete nodes with distinct IDs'); return result
    idx = {n['id']: n for n in nodes}
    candidates = []
    for node in nodes:
        if node.get('type') != 'Configuration': continue
        detail_response = call('configuration', {'configuration_id': node['id'], 'version': data_version})
        result['configuration_responses'].append(detail_response)
        if result['errors'] or detail_response is None or not detail_response['ok']: return result
        detail = detail_response.get('data')
        if not _configuration_valid(detail):
            error('CONFIGURATION_RELATION_MISMATCH', 'Public configuration has inconsistent model/revision/BOM joins'); return result
        if (detail['configuration'] != node or
                any(idx.get(detail[k]['id']) != detail[k] for k in ('revision', 'product'))):
            error('CONFIGURATION_RELATION_MISMATCH', 'Detail nodes must match the same full snapshot'); return result
        expected_items = [n for n in nodes if n.get('type') == 'BOMItem' and n.get('configuration_id') == node['id']]
        expected_components = [idx.get(cid) for cid in sorted({n['component_id'] for n in expected_items})]
        if (sorted(detail['items'], key=lambda n:n['id']) != sorted(expected_items, key=lambda n:n['id'])
                or sorted(detail['components'], key=lambda n:n['id']) != expected_components):
            error('CONFIGURATION_RELATION_MISMATCH', 'BOM and components must be complete and equal to the exact snapshot'); return result
        candidates.append(deepcopy(detail))
    result['configurations'] = dict(data_version=data_version, candidates=candidates, provenance='public_request_session')
    return result
