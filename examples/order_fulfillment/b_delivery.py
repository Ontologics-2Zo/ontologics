"""B-CQ04 reviewed synthetic inputs -> read-only conditional arrival forecast.

No LLM/API key is used. Two scenarios use separate fresh PostgreSQL schemas.
The scripted development human reviewer does not establish operational approval.
Default generated schemas are removed after the example; an explicit schema or
DatabaseSettings belongs to its caller and is not removed here.
"""
import argparse
import copy
import json
from pathlib import Path
import uuid

import psycopg
from psycopg import sql

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.database import DatabaseSettings
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, WORK, MIXED_WORK, load_demo_delivery
from ontologics.demo import PRINCIPALS, must
from ontologics.ingest import build_catalog
from ontologics.model import ROOT, SCHEMA, LIST_REFERENCES, graph_jsonld
from ontologics.order_fulfillment.service import AgentGateway, Service


def representative_graph(base, candidates):
    """Export exact typed/evidence dependency closure, never merge scenarios."""
    index = {node['id']: node for node in base + candidates}
    selected = set()

    def visit(node_id):
        if node_id in selected:
            return
        selected.add(node_id)
        node = index[node_id]
        for field in SCHEMA[node['type']][1]:
            if field in node:
                visit(node[field])
        for field in LIST_REFERENCES.get(node['type'], {}):
            for ref in node[field]:
                visit(ref)
        for ref in node['evidence']:
            visit(ref)

    for node in candidates:
        visit(node['id'])
    return graph_jsonld([node for node in index.values() if node['id'] in selected])


def run_example(database=None, *, schema=None, mixed=False):
    expected_path = 'data/order_fulfillment/b_delivery_mixed_expected.json' if mixed else 'data/order_fulfillment/b_delivery_expected.json'
    expected = json.loads((ROOT / expected_path).read_text())
    base = build_catalog()['nodes'] + load_demo_contract()
    candidates = load_demo_delivery(mixed=mixed)
    generated = schema is None and not isinstance(database, DatabaseSettings)
    chosen_schema = schema or ('b_cq04_' + uuid.uuid4().hex if generated else None)
    settings = (database if isinstance(database, DatabaseSettings) else
                DatabaseSettings(database, chosen_schema) if database is not None else
                DatabaseSettings.from_environment(schema=chosen_schema))
    principals = copy.deepcopy(PRINCIPALS)
    principals['delivery-reader'] = {'kind': 'human', 'roles': ['read']}
    service = Service(settings, principals=principals)
    reader = AgentGateway(service, 'delivery-reader', allowed_tools={'delivery_forecast', 'lookup'})
    drafter = AgentGateway(service, 'product-agent', allowed_tools={'prepare_draft', 'validate_draft'})
    try:
        before = must(service.bootstrap('seed-admin', base, request_key='cq04-promise-seed'))['data_version']
        arguments = dict(delivery_round_id=ROUND, as_of=AS_OF, version=before)
        missing = reader.call('delivery_forecast', arguments)
        draft = must(drafter.call('prepare_draft', {
            'operations': [{'op': 'put', 'node': node} for node in candidates],
            'request_key': 'cq04-delivery-draft'}))['data']
        did, dv = draft['draft_id'], draft['draft_version']
        validation = must(service.validate_draft('product-agent', did, dv))
        if not validation['data']['validation']['valid']:
            raise RuntimeError('B-CQ04 draft validation failed: ' + json.dumps(validation))
        blocked = service.apply('workflow', did, dv, request_key='cq04-before-review')
        agent_blocked = reader.call('review', {})
        must(service.review('reviewer', did, dv, decision='approve',
            note='Scripted development review of synthetic quantities, events, dispatch conditions and assumptions; not operational approval.',
            request_key='cq04-reviewed'))
        version = must(service.apply('workflow', did, dv, request_key='cq04-applied'))['data_version']
        arguments['version'] = version
        before_read = must(service.lookup('delivery-reader', version=version))['data']
        query = must(reader.call('delivery_forecast', arguments))
        data = query['data']
        utc = must(reader.call('delivery_forecast', dict(arguments, as_of='2026-10-27T01:00:00Z')))
        unknown_time = must(reader.call('delivery_forecast', dict(arguments, as_of='2026-10-27T11:00:00+09:00')))
        historical = reader.call('delivery_forecast', dict(arguments, version=before))
        details = []
        for evidence_id in query['evidence']:
            evidence = must(service.lookup('delivery-reader', node_id=evidence_id, version=version))['data'][0]
            source = must(service.lookup('delivery-reader', node_id=evidence['source_id'], version=version))['data'][0]
            details.append(dict(evidence=evidence, source=source))
        current_work = must(service.lookup('delivery-reader', node_id=MIXED_WORK if mixed else WORK, version=version))['data'][0]
        keys = ('delivery_round_id', 'snapshot_id', 'configuration_id', 'unit',
                'deadline_status', 'on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at')
        checks = {
            'independent_expected_forecast': all(data[key] == expected[key] for key in keys),
            'same_utc_instant': all(utc['data'][key] == expected[key] for key in keys),
            'no_arbitrary_time_reconstruction': unknown_time['data']['deadline_status'] == 'needs_confirmation'
                and unknown_time['data']['on_time_quantity'] is None,
            'unapproved_inputs_invisible': not missing['ok'] or (
                missing['data']['deadline_status'] == 'needs_confirmation' and missing['data']['on_time_quantity'] is None),
            'approval_required': not blocked['ok'] and blocked['errors'][0]['code'] == 'APPROVAL_REQUIRED',
            'agent_review_blocked': not agent_blocked['ok'] and agent_blocked['errors'][0]['code'] == 'TOOL_NOT_ALLOWED',
            'historical_version_unchanged': not historical['ok'] or (
                historical['data']['deadline_status'] == 'needs_confirmation' and historical['data']['on_time_quantity'] is None),
            'same_data_version': query['data_version'] == version,
            'reads_did_not_change_version': service.lookup('delivery-reader')['data_version'] == version,
            'confirmed_graph_unchanged': before_read == must(service.lookup('delivery-reader', version=version))['data'],
            'work_state_not_completed_by_forecast': current_work['completed_quantity'] == (200 if mixed else 600)
                and current_work['production_state'] == 'in_progress',
            'source_provenance': any(detail['source']['id'] == expected['source_id']
                and detail['source']['sha256'] == expected['source_sha256'] for detail in details),
            'assumptions_provenance': set(expected['assumptions_evidence']).issubset(query['evidence']),
        }
        if not all(checks.values()):
            raise RuntimeError('B-CQ04 checks failed: ' + json.dumps(checks))
        return dict(contract_version=__version__, mode='development example; scripted reviewer, no LLM',
            mixed=mixed, database_schema=service.schema, generated_schema_removed_on_exit=generated,
            versions=[before, version], checks=checks, query=query, evidence_details=details,
            historical=historical), representative_graph(base, candidates)
    finally:
        service.close()
        if generated:
            # This identifier was just generated above; never drop a caller schema.
            with psycopg.connect(settings.url, autocommit=True) as connection:
                connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(settings.schema)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', help='Dedicated caller-owned PostgreSQL schema; default disposable b_cq04_<id>')
    parser.add_argument('--mixed', action='store_true', help='Use separate delivered/transit/warehouse/production scenario')
    parser.add_argument('--output', default='artifacts/local/b_cq04_task08/example.json')
    parser.add_argument('--export-graph', action='store_true', help='Write the separate scenario representative graph')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise ValueError('Use a new output path to preserve previous execution evidence')
    report, graph = run_example(schema=args.schema, mixed=args.mixed)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.export_graph:
        path = ROOT / ('data/order_fulfillment/b_delivery_mixed_graph.jsonld' if args.mixed else 'data/order_fulfillment/b_delivery_graph.jsonld')
        path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(checks=report['checks'], versions=report['versions'], mixed=args.mixed,
        on_time_quantity=report['query']['data']['on_time_quantity'],
        shortfall_quantity=report['query']['data']['shortfall_quantity'],
        estimated_full_arrival_at=report['query']['data']['estimated_full_arrival_at'],output=str(output)),
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
