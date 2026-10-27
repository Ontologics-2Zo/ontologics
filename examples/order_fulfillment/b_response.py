"""B-CQ05 reviewed synthetic facts -> unexecuted Korean response drafts.

No LLM/API key is used. Each default run uses a fresh PostgreSQL schema and
removes only that generated schema. Explicit schemas belong to their caller.
The scripted development reviewer does not establish operational approval.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import uuid

import psycopg
from psycopg import sql

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.database import DatabaseSettings
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.demo import PRINCIPALS, must
from ontologics.ingest import build_catalog
from ontologics.model import ROOT
from ontologics.order_fulfillment.service import AgentGateway, Service


def _business_record_counts(service):
    """Trusted example-side observation; never exposed as an Agent tool."""
    return {table: service.db.execute(
        sql.SQL('SELECT COUNT(*) AS count FROM {}').format(sql.Identifier(table))
    ).fetchone()['count'] for table in ('snapshots', 'drafts', 'review_events')}


def run_example(database=None, *, schema=None, mixed=False):
    golden = json.loads((ROOT / 'data/order_fulfillment/b_response_expected.json').read_text())
    expected = golden['cases']['mixed15' if mixed else 'normal']
    selected = golden['selected_evidence']['mixed' if mixed else 'normal']
    base = build_catalog()['nodes'] + load_demo_contract()
    candidates = load_demo_delivery(mixed=mixed)
    generated = schema is None and not isinstance(database, DatabaseSettings)
    chosen_schema = schema or ('b_cq05_' + uuid.uuid4().hex if generated else None)
    settings = (database if isinstance(database, DatabaseSettings) else
                DatabaseSettings(database, chosen_schema) if database is not None else
                DatabaseSettings.from_environment(schema=chosen_schema))
    principals = copy.deepcopy(PRINCIPALS)
    principals['response-reader'] = {'kind': 'human', 'roles': ['read']}
    service = Service(settings, principals=principals)
    reader = AgentGateway(service, 'response-reader', allowed_tools={'response_plan', 'lookup'})
    try:
        before = must(service.bootstrap('seed-admin', base, request_key='cq05-promise-seed'))['data_version']
        arguments = dict(delivery_round_id=ROUND, as_of=AS_OF, version=before)
        missing = must(reader.call('response_plan', arguments))
        draft = must(service.prepare_draft('product-agent',
            [{'op': 'put', 'node': node} for node in candidates],
            request_key='cq05-delivery-draft'))['data']
        did, dv = draft['draft_id'], draft['draft_version']
        validation = must(service.validate_draft('product-agent', did, dv))
        if not validation['data']['validation']['valid']:
            raise RuntimeError('B-CQ05 input validation failed: ' + json.dumps(validation))
        pending = must(reader.call('response_plan', arguments))
        blocked = service.apply('workflow', did, dv, request_key='cq05-before-review')
        denied_tools = {tool: reader.call(tool, {}) for tool in ('prepare_draft', 'review', 'apply')}
        must(service.review('reviewer', did, dv, decision='approve',
            note='Scripted development review of unchanged synthetic CQ04 inputs; not operational approval.',
            request_key='cq05-reviewed'))
        version = must(service.apply('workflow', did, dv, request_key='cq05-applied'))['data_version']
        arguments['version'] = version
        before_read = must(service.lookup('response-reader', version=version))['data']
        counts_before = _business_record_counts(service)
        query = must(reader.call('response_plan', arguments))
        data = query['data']
        utc = must(reader.call('response_plan', dict(arguments, as_of='2026-10-27T01:00:00Z')))
        unknown_time = must(reader.call('response_plan', dict(arguments, as_of='2026-10-27T11:00:00+09:00')))
        historical_version = must(reader.call('response_plan', dict(arguments, version=before)))
        details = []
        for evidence_id in query['evidence']:
            evidence = must(reader.call('lookup', {'node_id': evidence_id, 'version': version}))['data'][0]
            source = must(reader.call('lookup', {'node_id': evidence['source_id'], 'version': version}))['data'][0]
            details.append(dict(evidence=evidence, source=source))
        current_graph = must(service.lookup('response-reader', version=version))['data']
        independent = (data['response_state'] == expected['response_state'] and
            all(data[section][key] == value for section in ('forecast', 'dispatch_request')
                for key, value in expected[section].items()))
        utc_matches = (utc['data']['response_state'] == expected['response_state'] and
            all(utc['data'][section][key] == value for section in ('forecast', 'dispatch_request')
                for key, value in expected[section].items()))
        request = data['dispatch_request']
        checks = {
            'independent_expected_quantities': independent,
            'same_utc_instant': utc_matches,
            'unapproved_inputs_invisible': all(result['data']['response_state'] == 'needs_confirmation'
                and result['data']['dispatch_request']['new_dispatch_quantity'] is None
                for result in (missing, pending)),
            'approval_required': not blocked['ok'] and blocked['errors'][0]['code'] == 'APPROVAL_REQUIRED',
            'read_gateway_has_no_write_review_apply': all(not result['ok'] and
                result['errors'][0]['code'] == 'TOOL_NOT_ALLOWED' for result in denied_tools.values()),
            'exact_confirmed_version': query['data_version'] == version,
            'old_version_not_replaced': historical_version['data_version'] == before and
                historical_version['data']['response_state'] == 'needs_confirmation',
            'no_arbitrary_time_reconstruction': unknown_time['data']['response_state'] == 'needs_confirmation'
                and unknown_time['data']['dispatch_request']['new_dispatch_quantity'] is None,
            'confirmed_graph_unchanged': before_read == current_graph,
            'no_business_records_created_by_read': counts_before == _business_record_counts(service),
            'reads_did_not_change_version': must(service.lookup('response-reader'))['data_version'] == version,
            'draft_only_no_execution': data['draft_only'] and data['review_required'] and
                not data['execution_performed'],
            'missing_logistics_not_invented': all(request[key] is None for key in
                ('carrier', 'vehicle', 'detailed_address', 'contact', 'request_number')),
            'selected_evidence_exact': data['evidence'] == selected,
            'integrity_evidence_preserved': set(selected).issubset(query['evidence']) and
                len(query['evidence']) == (17 if mixed else 12),
            'source_hashes_and_locators': all(detail['evidence']['locator'] and
                detail['source']['sha256'] == hashlib.sha256((ROOT / detail['source']['path']).read_bytes()).hexdigest()
                for detail in details),
            'new_dispatch_excludes_delivered_and_transit': all(item['source_state'] not in
                ('delivered', 'in_transit') for item in request['items']),
        }
        if not all(checks.values()):
            raise RuntimeError('B-CQ05 example checks failed: ' + json.dumps(checks))
        return dict(contract_version=__version__, mode='deterministic development example; scripted reviewer, no LLM',
            mixed=mixed, database_schema=service.schema, generated_schema_removed_on_exit=generated,
            versions=[before, version], checks=checks, query=query, evidence_details=details,
            historical_version=historical_version,
            limitations=['No CQ05 Agent/provider or live LLM validation',
                         'No sending, registration, operational approval or dispatch execution'])
    finally:
        service.close()
        if generated:
            # Only this function's freshly generated schema is disposable.
            with psycopg.connect(settings.url, autocommit=True) as connection:
                connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(settings.schema)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', help='Caller-owned PostgreSQL schema; default disposable b_cq05_<id>')
    parser.add_argument('--mixed', action='store_true', help='Use separately sourced delivered/transit/warehouse/production facts')
    parser.add_argument('--output', default='artifacts/local/b_cq05_task08/example.json')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise ValueError('Use a new output path to preserve previous execution evidence')
    report = run_example(schema=args.schema, mixed=args.mixed)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    data = report['query']['data']
    print(json.dumps(dict(checks=report['checks'], versions=report['versions'], mixed=args.mixed,
        response_state=data['response_state'], forecast=data['forecast']['on_time_quantity'],
        new_dispatch_quantity=data['dispatch_request']['new_dispatch_quantity'],
        selected_evidence_count=len(data['evidence']), output=str(output)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
