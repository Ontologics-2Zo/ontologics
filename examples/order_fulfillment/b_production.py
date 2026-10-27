"""B-CQ03: reviewed synthetic production counts -> versioned completion forecast.

No LLM/API key is used. The scripted development reviewer is not operational
approval. Each default run uses a fresh PostgreSQL schema, separate from CQ02.
"""
import argparse
import json
from pathlib import Path
import uuid

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.demo import PRINCIPALS, must
from ontologics.ingest import build_catalog
from ontologics.model import ROOT, SCHEMA, LIST_REFERENCES, graph_jsonld
from ontologics.order_fulfillment.production import AS_OF, ROUND, WORK, load_demo_production
from ontologics.order_fulfillment.service import AgentGateway, Service


def run_example(database=None, *, schema=None):
    expected = json.loads((ROOT / 'data/order_fulfillment/b_production_expected.json').read_text())
    base = build_catalog()['nodes'] + load_demo_contract()
    candidates = load_demo_production()
    service = Service(database, principals=PRINCIPALS,
        schema=schema or ('b_cq03_' + uuid.uuid4().hex if database is None else None))
    gateway = AgentGateway(service, 'product-agent')
    try:
        before = must(service.bootstrap('seed-admin', base, request_key='cq03-promise-seed'))['data_version']
        arguments = dict(production_work_id=WORK, delivery_round_id=ROUND, as_of=AS_OF, version=before)
        missing = gateway.call('production_completion', arguments)
        draft = must(gateway.call('prepare_draft', {
            'operations': [{'op': 'put', 'node': n} for n in candidates],
            'request_key': 'cq03-observations-draft'}))['data']
        did, dv = draft['draft_id'], draft['draft_version']
        validation = must(service.validate_draft('product-agent', did, dv))
        if not validation['data']['validation']['valid']:
            raise RuntimeError('B-CQ03 draft validation failed: ' + json.dumps(validation))
        blocked = service.apply('workflow', did, dv, request_key='cq03-before-review')
        agent_blocked = gateway.call('review', {})
        must(service.review('reviewer', did, dv, decision='approve',
            note='Scripted development review of synthetic counts, allocation and assumptions; not an operational approval.',
            request_key='cq03-reviewed'))
        version = must(service.apply('workflow', did, dv, request_key='cq03-applied'))['data_version']
        arguments['version'] = version
        query = must(gateway.call('production_completion', arguments))
        data = query['data']
        utc = must(gateway.call('production_completion', dict(arguments, as_of='2026-10-27T01:00:00Z')))
        unknown_time = must(gateway.call('production_completion',
            dict(arguments, as_of='2026-10-27T11:00:00+09:00')))
        historical = gateway.call('production_completion', dict(arguments, version=before))
        cq02 = must(gateway.call('fulfillment_status',
            dict(delivery_round_id=ROUND, as_of=AS_OF, version=version)))
        details = []
        for eid in query['evidence']:
            evidence = must(service.lookup('product-agent', node_id=eid, version=version))['data'][0]
            source = must(service.lookup('product-agent', node_id=evidence['source_id'], version=version))['data'][0]
            details.append(dict(evidence=evidence, source=source))
        unchanged_work = must(service.lookup('product-agent', node_id=WORK, version=version))['data'][0]
        checks = {
            'exact_completion_forecast': all(data[key] == expected[key] for key in (
                'status', 'production_work_id', 'delivery_round_id', 'snapshot_id', 'configuration_id',
                'unit', 'target_quantity', 'latest_good_quantity', 'selected_observation_ids',
                'good_rate_per_hour', 'produced_rate_per_hour', 'recent_defect_ratio',
                'remaining_quantity', 'remaining_seconds', 'estimated_completion_at')),
            'same_utc_instant': utc['data']['estimated_completion_at'] == expected['estimated_completion_at'],
            'no_arbitrary_time_reconstruction': unknown_time['data']['status'] == 'needs_confirmation'
                and unknown_time['data']['estimated_completion_at'] is None,
            'unapproved_work_invisible': not missing['ok'] and missing['errors'][0]['code'] == 'NOT_FOUND',
            'approval_required': not blocked['ok'] and blocked['errors'][0]['code'] == 'APPROVAL_REQUIRED',
            'agent_review_blocked': not agent_blocked['ok'] and agent_blocked['errors'][0]['code'] == 'TOOL_NOT_ALLOWED',
            'historical_version_unchanged': not historical['ok'] and historical['errors'][0]['code'] == 'NOT_FOUND',
            'same_data_version': query['data_version'] == version,
            'reads_did_not_change_version': service.lookup('product-agent')['data_version'] == version,
            'work_state_not_completed_by_forecast': unchanged_work['completed_quantity'] == 600
                and unchanged_work['production_state'] == 'in_progress',
            'cq02_partial_production_limit_preserved': cq02['data']['status'] == 'needs_confirmation'
                and any(u['code'] == 'PARTIAL_PRODUCTION_UNSUPPORTED' for u in cq02['data']['unknowns']),
            'source_provenance': any(d['source']['id'] == expected['source_id']
                and d['source']['sha256'] == expected['source_sha256'] for d in details),
            'assumptions_provenance': set(expected['assumptions_evidence']).issubset(query['evidence']),
            'no_arrival_assessment': data['fulfillment_assessed'] is False,
        }
        if not all(checks.values()):
            raise RuntimeError('B-CQ03 checks failed: ' + json.dumps(checks))
        index = {n['id']: n for n in base + candidates}
        selected = set()

        def visit(nid):
            if nid in selected:
                return
            selected.add(nid)
            node = index[nid]
            for key in SCHEMA[node['type']][1]:
                if key in node:
                    visit(node[key])
            for key in LIST_REFERENCES.get(node['type'], {}):
                for ref in node[key]:
                    visit(ref)
            for eid in node['evidence']:
                visit(eid)

        for node in candidates:
            visit(node['id'])
        graph = graph_jsonld([n for n in index.values() if n['id'] in selected])
        return dict(contract_version=__version__, mode='development example; scripted reviewer, no LLM',
            database_schema=service.schema, versions=[before, version], checks=checks, query=query,
            evidence_details=details, historical=historical, cq02=cq02), graph
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', help='Fresh PostgreSQL schema; default unique b_cq03_<id>')
    parser.add_argument('--output', default='artifacts/local/b_cq03_task08/example.json')
    parser.add_argument('--export-graph', action='store_true', help='Write the separate B-CQ03 representative graph')
    args = parser.parse_args()
    report, graph = run_example(schema=args.schema)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.export_graph:
        (ROOT / 'data/order_fulfillment/b_production_graph.jsonld').write_text(json.dumps(graph, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(checks=report['checks'], versions=report['versions'],
        estimated_completion_at=report['query']['data']['estimated_completion_at'], output=str(output)),
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
