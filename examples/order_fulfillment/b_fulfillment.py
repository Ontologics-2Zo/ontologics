"""B-CQ02: reviewed synthetic facts -> exact versioned six-state snapshot.

No LLM/API key needed. Human reviewer is a scripted development identity, not an
operational approval. Each run uses a fresh PostgreSQL schema.
"""
import argparse
import json
from pathlib import Path
import uuid

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.demo import PRINCIPALS, must
from ontologics.order_fulfillment.fulfillment import AS_OF, ROUND, OTHER_ROUND, load_demo_fulfillment
from ontologics.ingest import build_catalog
from ontologics.model import ROOT, SCHEMA, LIST_REFERENCES, graph_jsonld
from ontologics.order_fulfillment.service import AgentGateway, Service


def run_example(database=None, *, schema=None):
    expected = json.loads((ROOT / 'data/order_fulfillment/b_fulfillment_expected.json').read_text())
    catalog = build_catalog()['nodes']
    base = catalog + load_demo_contract()
    candidates = load_demo_fulfillment()
    service = Service(database, principals=PRINCIPALS,
                      schema=schema or ('b_cq02_' + uuid.uuid4().hex if database is None else None))
    gateway = AgentGateway(service, 'product-agent')
    try:
        before = must(service.bootstrap('seed-admin', base, request_key='cq02-promise-seed'))['data_version']
        arguments = dict(delivery_round_id=ROUND, as_of=AS_OF, version=before)
        missing = must(gateway.call('fulfillment_status', arguments))
        draft = must(gateway.call('prepare_draft', {'operations': [{'op': 'put', 'node': n} for n in candidates],
                                                 'request_key': 'cq02-snapshot-draft'}))['data']
        did, dv = draft['draft_id'], draft['draft_version']
        validation = must(service.validate_draft('product-agent', did, dv))
        if not validation['data']['validation']['valid']:
            raise RuntimeError('B-CQ02 draft validation failed')
        blocked = service.apply('workflow', did, dv, request_key='cq02-before-review')
        agent_blocked = gateway.call('review', {})
        must(service.review('reviewer', did, dv, decision='approve',
            note='Scripted development review of synthetic snapshot, coverage and source; not an operational approval.',
            request_key='cq02-reviewed'))
        version = must(service.apply('workflow', did, dv, request_key='cq02-applied'))['data_version']
        arguments['version'] = version
        query = must(gateway.call('fulfillment_status', arguments))
        data = query['data']
        other = must(gateway.call('fulfillment_status', dict(arguments, delivery_round_id=OTHER_ROUND)))
        utc = must(gateway.call('fulfillment_status', dict(arguments, as_of='2026-10-27T01:00:00Z')))
        unknown_time = must(gateway.call('fulfillment_status', dict(arguments, as_of='2026-10-27T11:00:00+09:00')))
        historical = must(gateway.call('fulfillment_status', dict(arguments, version=before)))
        details = []
        for eid in query['evidence']:
            evidence = must(service.lookup('product-agent', node_id=eid, version=version))['data'][0]
            source = must(service.lookup('product-agent', node_id=evidence['source_id'], version=version))['data'][0]
            details.append(dict(evidence=evidence, source=source))
        checks = {
            'exact_six_state_answer': data['status'] == 'complete' and data['states'] == expected['states'],
            'allocated_remaining_unallocated': all(data[k] == expected[k] for k in
                ('allocated_quantity', 'remaining_quantity', 'unallocated_quantity')),
            'source_stock_not_double_counted': {s['stock_id']: s['remaining_quantity_as_of'] for s in data['stocks']}
                == expected['source_stock_remaining'],
            'other_round_excluded_and_queryable': other['data']['states'] == expected['other_round_states'],
            'same_utc_instant': utc['data']['states'] == expected['states'],
            'no_arbitrary_time_reconstruction': unknown_time['data']['status'] == 'needs_confirmation'
                and unknown_time['data']['states'] is None,
            'missing_is_not_zero': missing['data']['status'] == 'needs_confirmation' and missing['data']['states'] is None,
            'approval_required': not blocked['ok'] and blocked['errors'][0]['code'] == 'APPROVAL_REQUIRED',
            'agent_review_blocked': not agent_blocked['ok'] and agent_blocked['errors'][0]['code'] == 'TOOL_NOT_ALLOWED',
            'historical_snapshot_unchanged': historical['data']['status'] == 'needs_confirmation'
                and historical['data']['states'] is None,
            'same_data_version': query['data_version'] == version,
            'read_did_not_change_version': service.lookup('product-agent')['data_version'] == version,
            'source_provenance': any(d['source']['id'] == expected['source_id']
                and d['source']['sha256'] == expected['source_sha256'] for d in details),
            'no_fulfillment_forecast': data['fulfillment_assessed'] is False,
        }
        if not all(checks.values()):
            raise RuntimeError('B-CQ02 checks failed: ' + json.dumps(checks))
        # Minimal representative dependency closure, preserving the original full
        # installation graph and the earlier B-CQ01 representative file.
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
            evidence_details=details, other_round=other, historical=historical), graph
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', help='Fresh PostgreSQL schema; default unique b_cq02_<id>')
    parser.add_argument('--output', default='artifacts/local/b_cq02_task08/example.json')
    parser.add_argument('--export-graph', action='store_true', help='Write the separate B-CQ02 representative graph')
    args = parser.parse_args()
    report, graph = run_example(schema=args.schema)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.export_graph:
        (ROOT / 'data/order_fulfillment/b_fulfillment_graph.jsonld').write_text(json.dumps(graph, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(checks=report['checks'], versions=report['versions'],
        states=report['query']['data']['states'], output=str(output)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
