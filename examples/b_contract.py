"""B-CQ01: shared contract source -> reviewed registration -> versioned promise.

No LLM or API key is needed. Reviewer identities are scripted development roles.
Each execution uses a fresh PostgreSQL schema and preserves previous examples.
"""
import argparse
import json
from pathlib import Path
import uuid

from ontologics import __version__
from ontologics.contracts import load_demo_contract
from ontologics.demo import PRINCIPALS, must
from ontologics.ingest import build_catalog
from ontologics.model import ROOT, SCHEMA, graph_jsonld, tbox_turtle
from ontologics.service import AgentGateway, Service


def promise_values(data):
    """Select factual fields for comparison with the independently authored answer."""
    contract, item, delivery = data['contract'], data['contract_item'], data['delivery_round']
    return {'contract_id': contract['id'], 'contract_item_id': item['id'],
            'delivery_round_id': delivery['id'], 'company': contract['company'],
            'customer_name': contract['customer_name'],
            'configuration_id': data['configuration']['id'],
            'configuration_name': data['configuration']['name'],
            'contract_quantity': item['quantity'], 'quantity': delivery['quantity'],
            'unit': delivery['unit'], 'due_at': delivery['due_at'],
            'due_basis': delivery['due_basis'], 'destination': delivery['destination'],
            'fulfillment_assessed': data['fulfillment_assessed']}


def run_example(database=None, *, schema=None):
    expected = json.loads((ROOT / 'data/b_contract_expected.json').read_text())
    catalog = build_catalog(ROOT)['nodes']
    candidates = load_demo_contract()
    service = Service(database, principals=PRINCIPALS,
                      schema=schema or ('b_cq01_' + uuid.uuid4().hex if database is None else None))
    gateway = AgentGateway(service, 'product-agent')
    try:
        seed = must(service.bootstrap('seed-admin', catalog, request_key='b-cq01-design-seed'))
        baseline = seed['data_version']
        draft = must(gateway.call('prepare_draft', {
            'operations': [{'op': 'put', 'node': n} for n in candidates],
            'request_key': 'b-cq01-contract-draft'}))['data']
        did, dv = draft['draft_id'], draft['draft_version']
        validation = must(service.validate_draft('product-agent', did, dv))
        if not validation['data']['validation']['valid']:
            raise RuntimeError('B-CQ01 draft validation failed')
        before = gateway.call('delivery_round', {'delivery_round_id': expected['delivery_round_id'], 'version': baseline})
        blocked = service.apply('workflow', did, dv, request_key='b-cq01-before-approval')
        must(service.review('reviewer', did, dv, decision='approve',
            note='Scripted development review of the synthetic contract source and exact configuration; not an operational approval.',
            request_key='b-cq01-reviewed'))
        applied = must(service.apply('workflow', did, dv, request_key='b-cq01-applied'))
        version = applied['data_version']
        query = must(gateway.call('delivery_round', {'delivery_round_id': expected['delivery_round_id'], 'version': version}))
        historical = gateway.call('delivery_round', {'delivery_round_id': expected['delivery_round_id'], 'version': baseline})
        actual = promise_values(query['data'])
        details = []
        for eid in query['evidence']:
            evidence = must(service.lookup('product-agent', node_id=eid, version=version))['data'][0]
            source = must(service.lookup('product-agent', node_id=evidence['source_id'], version=version))['data'][0]
            details.append({'evidence': evidence, 'source': source})
        checks = {
            'golden_answer': actual == {key: expected[key] for key in actual},
            'unapplied_not_visible': not before['ok'] and before['errors'][0]['code'] == 'NOT_FOUND',
            'approval_required': not blocked['ok'] and blocked['errors'][0]['code'] == 'APPROVAL_REQUIRED',
            'historical_snapshot_unchanged': not historical['ok'] and historical['errors'][0]['code'] == 'NOT_FOUND',
            'same_version': query['data_version'] == version,
            'contract_and_configuration_evidence': set(expected['contract_evidence'] + expected['configuration_evidence']) <= set(query['evidence']),
            'synthetic_source_hash': any(d['source']['id'] == expected['source_id']
                and d['source']['origin'] == 'synthetic' and d['source']['sha256'] == expected['source_sha256'] for d in details),
            'promise_only': query['data']['fulfillment_assessed'] is False
                and any(w['code'] == 'FULFILLMENT_NOT_ASSESSED' for w in query['warnings']),
        }
        if not all(checks.values()):
            raise RuntimeError('B-CQ01 checks failed: ' + json.dumps(checks))
        # Small representative A-box: only new facts and their real design/source
        # dependencies. The original full BOM/installation graph stays intact.
        index = {n['id']: n for n in catalog + candidates}
        selected = set()
        def visit(nid):
            if nid in selected:
                return
            selected.add(nid)
            node = index[nid]
            for key in SCHEMA[node['type']][1]:
                visit(node[key])
            for eid in node['evidence']:
                visit(eid)
        for node in candidates:
            visit(node['id'])
        graph = graph_jsonld([node for node in index.values() if node['id'] in selected])
        return {'contract_version': __version__, 'mode': 'development example; scripted reviewer, no LLM',
                'database_schema': service.schema, 'versions': [baseline, version],
                'checks': checks, 'promise': actual, 'query': query,
                'evidence_details': details}, graph
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', help='Fresh PostgreSQL schema; default unique b_cq01_<id>')
    parser.add_argument('--output', default='artifacts/local/task08/b_cq01.json')
    parser.add_argument('--export-graph', action='store_true', help='Regenerate B representative graph and common T-box')
    args = parser.parse_args()
    report, graph = run_example(schema=args.schema)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.export_graph:
        (ROOT / 'ontology/tbox.ttl').write_text(tbox_turtle())
        (ROOT / 'data/b_contract_graph.jsonld').write_text(json.dumps(graph, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'schema': report['database_schema'], 'versions': report['versions'],
                      'checks': report['checks'], 'promise': report['promise'], 'output': str(output)},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
