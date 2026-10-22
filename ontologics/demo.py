"""Reproducible source import -> human review -> investigation -> supplement flow."""
import argparse
import hashlib
import json
import uuid
from . import __version__
from pathlib import Path
from .ingest import build_catalog
from .model import ROOT, tbox_turtle
from .service import Service, AgentGateway

PRINCIPALS = {
    'seed-admin': {'kind': 'human', 'roles': ['read', 'seed', 'audit']},
    'product-agent': {'kind': 'agent', 'roles': ['read', 'draft']},
    'reviewer': {'kind': 'human', 'roles': ['read', 'review', 'audit']},
    'workflow': {'kind': 'system', 'roles': ['read', 'apply']},
}
CONFIG_L = 'config:esp32-poe:L:WROOM'
CONFIG_L1 = 'config:esp32-poe:L1:WROOM'
LOT = 'lot:demo:lan8710:L07'

def fixture_nodes():
    path = ROOT / 'data/demo_records.json'
    raw = json.loads(path.read_text())
    source = {'id': 'source:demo:operations', 'type': 'SourceDocument', 'origin': 'synthetic', 'evidence': [],
        'url': 'urn:ontologics:demo:operations', 'path': 'data/demo_records.json',
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'version': 'demo-1',
        'license': 'Project demonstration data', 'collected_at': raw['authored_at'], 'kind': 'synthetic_operations'}
    evidence = [{'id': 'evidence:demo:' + part, 'type': 'Evidence', 'origin': 'synthetic', 'evidence': [],
        'source_id': source['id'], 'locator': '/' + part,
        'description': 'Fictional demonstration ' + part + '; not OLIMEX operating records.'} for part in ('initial', 'supplement')]
    initial = raw['initial']; ev = ['evidence:demo:initial']
    def node(kind, nid, **fields):
        return {'id': nid, 'type': kind, 'origin': 'synthetic', 'evidence': ev[:], **fields}
    nodes = [source, *evidence]
    nodes += [node('ProductUnit', 'unit:' + u, serial=u, company=initial['company'], configuration_id=initial['configuration_id']) for u in initial['units']]
    nodes += [node('ComponentLot', LOT, company=initial['company'], supplier=initial['supplier'], lot_code=initial['lot_code'], component_id=initial['component_id'])]
    for entry in initial['installations']:
        fields = dict(entry); nid = fields.pop('id')
        nodes.append(node('Installation', nid, lot_id=LOT, position=initial['position'], **fields))
    for entry in initial['coverage']:
        fields = dict(entry); nid = fields.pop('id')
        nodes.append(node('HistoryCoverage', nid, **fields))
    for entry in initial['inventory']:
        fields = dict(entry); nid = fields.pop('id')
        nodes.append(node('InventoryMovement', nid, lot_id=LOT, **fields))
    updated = dict(next(n for n in nodes if n['id'] == raw['supplement']['installation_id']))
    updated.update(removed_at=raw['supplement']['removed_at'], evidence=ev + ['evidence:demo:supplement'])
    cov = dict(raw['supplement']['coverage']); nid = cov.pop('id')
    coverage = node('HistoryCoverage', nid, **cov); coverage['evidence'] = ['evidence:demo:supplement']
    return nodes, [{'op': 'put', 'node': n} for n in (updated, coverage)]

def example_queries():
    base = {'lot_id': LOT, 'unit_ids': ['unit:U104', 'unit:U105', 'unit:U106', 'unit:U107']}
    return {'A': dict(base, mode='A', at='2026-09-20T10:00:00+09:00'),
            'B': dict(base, mode='B', start='2026-09-01T00:00:00+09:00', end='2026-09-21T00:00:00+09:00')}

def must(response):
    if not response['ok']: raise RuntimeError(json.dumps(response['errors'], ensure_ascii=False))
    return response

def run_demo(database=None, *, schema=None):
    catalog = build_catalog(ROOT)
    nodes = catalog['nodes']
    fictional, supplement = fixture_nodes()
    # L is the existing baseline; L1 must go through the actual S2 approval path.
    new = [n for n in nodes if n.get('id') in (CONFIG_L1, 'revision:esp32-poe:L1') or
           n.get('configuration_id') == CONFIG_L1 or n['id'].startswith('component:olimex-L1-wroom:')]
    new_ids = {n['id'] for n in new}
    seed = [n for n in nodes if n['id'] not in new_ids] + fictional
    svc = Service(database, schema=schema or ('demo_' + uuid.uuid4().hex if database is None else None), principals=PRINCIPALS)
    gateway = AgentGateway(svc, 'product-agent')
    try:
        if svc._version() != 0:
            raise ValueError('Demo requires a fresh schema; choose a new --schema')
        seeded = must(svc.bootstrap('seed-admin', seed, request_key='seed-v1'))
        registration = must(gateway.call('prepare_draft', {'operations': [{'op': 'put', 'node': n} for n in new], 'request_key': 'register-L1'}))
        d = registration['data']; did, dv = d['draft_id'], d['draft_version']
        registration_diff = must(gateway.call('compare_configurations', {'left_id': CONFIG_L, 'right_id': CONFIG_L1,
            'draft_id': did, 'draft_version': dv}))
        assert registration_diff['data']['is_preview']
        before_approval = svc.apply('workflow', did, dv, request_key='blocked-apply-L1')
        assert before_approval['errors'][0]['code'] == 'APPROVAL_REQUIRED'
        reviewed = must(svc.review('reviewer', did, dv, decision='approve', note='Development test review: verified L1 source mapping; unresolved population labels remain explicit.', request_key='review-L1'))
        applied = must(svc.apply('workflow', did, dv, request_key='apply-L1'))
        duplicate = gateway.call('prepare_draft', {'operations': [{'op': 'put', 'node': n} for n in new], 'request_key': 'duplicate-L1'})
        assert duplicate['errors'][0]['code'] == 'NO_CHANGES'
        queries = example_queries()
        before = {mode: must(gateway.call('investigate', {'query': q})) for mode, q in queries.items()}
        proposal = must(gateway.call('prepare_draft', {'operations': supplement, 'request_key': 'supplement-U104'}))
        d2 = proposal['data']; id2, v2 = d2['draft_id'], d2['draft_version']
        # Demonstrates review request, edited immutable version, fresh approval.
        must(svc.review('reviewer', id2, v2, decision='request_changes', note='Confirm initial state and absence of reinstallation across the complete range.', request_key='supplement-review-request'))
        revised = must(gateway.call('prepare_draft', {'operations': supplement, 'draft_id': id2, 'expected_version': v2, 'request_key': 'supplement-revised'}))
        v2 = revised['data']['draft_version']
        must(svc.review('reviewer', id2, v2, decision='approve', note='Development fixture confirms removal and complete history including initial state; approve exact version.', request_key='supplement-approved'))
        applied2 = must(svc.apply('workflow', id2, v2, request_key='supplement-applied'))
        replay = must(svc.apply('workflow', id2, v2, request_key='supplement-applied'))
        after = {mode: must(gateway.call('investigate', {'query': q})) for mode, q in queries.items()}
        comparisons = {mode: must(svc.compare_investigation('product-agent', q, applied['data_version'], applied2['data_version'])) for mode, q in queries.items()}
        diffs = must(gateway.call('compare_configurations', {'left_id': CONFIG_L, 'right_id': CONFIG_L1}))
        graph = must(svc.export_graph('product-agent'))
        for mode in ('A', 'B'):
            assert before[mode]['data']['results'][0]['status'] == 'needs_confirmation'
        assert after['A']['data']['results'][0]['status'] == 'excluded'
        assert after['B']['data']['results'][0]['status'] == 'included'
        summary = {
            'contract_version': __version__, 'database_schema': svc.schema, 'storage': 'postgresql+pgvector', 'mode': 'development demonstration; scripted human identities, not real operational approval',
            'source_verification': catalog['verification'], 'seed_version': seeded['data_version'],
            'registered_version': applied['data_version'], 'supplemented_version': applied2['data_version'],
            'registration': {'draft_id': did, 'version': dv, 'status': reviewed['data']['status'], 'node_count': len(new)},
            'registration_review_comparison': registration_diff,
            'approval_before_apply': before_approval['errors'], 'duplicate_registration': duplicate['errors'],
            'replay': replay['warnings'], 'before': before, 'after': after, 'same_condition_comparisons': comparisons,
            'configuration_differences': diffs['data'], 'audit': must(svc.run_history('seed-admin'))['data']}
        return summary, graph['data']
    finally:
        svc.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', default=None, help='Fresh PostgreSQL schema; default unique demo_<id>')
    parser.add_argument('--output', default='artifacts/local/demo_result.json')
    parser.add_argument('--export-graph', action='store_true', help='Regenerate checked-in T-box/graph explicitly')
    args = parser.parse_args()
    summary, graph = run_demo(schema=args.schema)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    if args.export_graph:
        (ROOT / 'ontology/tbox.ttl').write_text(tbox_turtle())
        (ROOT / 'data/initial_graph.jsonld').write_text(json.dumps(graph, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'schema': summary['database_schema'], 'output': str(output), 'versions': [summary[k] for k in ('seed_version', 'registered_version', 'supplemented_version')],
        'U104': {phase: {m: summary[phase][m]['data']['results'][0]['status'] for m in ('A', 'B')} for phase in ('before', 'after')},
        'changed_positions': [c['position'] for c in summary['configuration_differences']['changes']]}, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
