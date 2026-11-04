"""Real approved S1/S2 snapshots queried through PostgreSQL, SPARQL and Neo4j.

Uses public OLIMEX data and fictional operations only. No model API is called.
The report is a common CQ regression result, not a personal quality evaluation.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from ontologics.database import DatabaseSettings, environment_values
from ontologics.demo import CONFIG_L, CONFIG_L1, PRINCIPALS, example_queries, must, run_demo
from ontologics.graph_views import GraphSettings
from ontologics.model import ROOT
from ontologics.service import Service


COMPARABLE_FIELDS = ('ok', 'data', 'evidence', 'data_version', 'schema_version',
                     'rule_version', 'contract_version', 'warnings', 'conflicts', 'errors')
GRAPH_PRINCIPALS = {**PRINCIPALS, 'graph-indexer': {'kind': 'system', 'roles': ['read', 'index']}}


def comparable(result):
    """Only per-call audit IDs differ across equivalent backend executions."""
    return {name: result[name] for name in COMPARABLE_FIELDS}


def collect_cqs(service):
    calls = {}
    for version in (2, 3):
        configs = []
        for cid in (CONFIG_L, CONFIG_L1):
            result = must(service.configuration('product-agent', cid, version=version))
            calls[f'CQ01_02:{version}:{cid}'] = comparable(result)
            configs.append(result['data'])
        calls[f'CQ03:{version}'] = comparable(must(service.compare_configurations(
            'product-agent', CONFIG_L, CONFIG_L1, version=version)))
        for mode, query in example_queries().items():
            calls[f'CQ05_06_07:{version}:{mode}'] = comparable(must(service.investigate(
                'product-agent', query, version=version)))
        for config in configs:
            component = next(n for n in config['components'] if n['name'].startswith('LAN8710A-EZC'))
            calls[f'CQ10:{version}:{component["id"]}'] = comparable(must(service.component_usage(
                'product-agent', component['id'], version=version)))
    for mode, query in example_queries().items():
        calls[f'CQ08:{mode}'] = comparable(must(service.compare_investigation('product-agent', query, 2, 3)))
    for kind in ('SourceDocument', 'Evidence', 'ProductModel'):
        calls[f'CQ01_sources:{kind}'] = comparable(must(service.lookup('product-agent', kind=kind, version=3)))
    # All nodes, including nested specification lists, preserve the source snapshot.
    calls['graph_roundtrip'] = comparable(must(service.export_graph('product-agent', version=3)))
    return calls


def verify(database=None, *, schema=None, graph_settings=None):
    settings = graph_settings or GraphSettings.from_environment(backend='neo4j')
    settings.require_neo4j()
    summary, _ = run_demo(database, schema=schema)
    if database is None:
        database = DatabaseSettings.from_environment(schema=summary['database_schema'])
    services = {}
    try:
        for backend in ('postgres', 'rdf', 'neo4j'):
            services[backend] = Service(database, principals=GRAPH_PRINCIPALS,
                graph_settings=replace(settings, backend=backend))
        sync = services['neo4j']
        synchronized = [must(sync.graph_views.synchronize('graph-indexer', v, request_key='cq-sync-' + str(v)))
                        for v in (2, 3)]
        baseline = collect_cqs(services['postgres'])
        comparisons = {backend: collect_cqs(services[backend]) for backend in ('rdf', 'neo4j')}
        checks = {backend + ':' + key: value == baseline[key]
                  for backend, calls in comparisons.items() for key, value in calls.items()}
        checks['CQ04_unapproved_apply_blocked'] = summary['approval_before_apply'][0]['code'] == 'APPROVAL_REQUIRED'
        checks['CQ04_duplicate_registration_blocked'] = summary['duplicate_registration'][0]['code'] == 'NO_CHANGES'
        checks['CQ04_approval_application_replay'] = any(w['code'] == 'IDEMPOTENT_REPLAY' for w in summary['replay'])
        expected = {'A': ['excluded', 'included', 'needs_confirmation', 'excluded'],
                    'B': ['included', 'included', 'needs_confirmation', 'excluded']}
        for mode in ('A', 'B'):
            before = baseline[f'CQ05_06_07:2:{mode}']['data']
            after = baseline[f'CQ05_06_07:3:{mode}']['data']
            checks[f'{mode}:same_conditions'] = before['query'] == after['query']
            checks[f'{mode}:supported_unit_results'] = [r['status'] for r in after['results']] == expected[mode]
            checks[f'{mode}:U104_was_unconfirmed'] = before['results'][0]['status'] == 'needs_confirmation'
            checks[f'{mode}:U106_no_fabricated_evidence'] = after['results'][2]['evidence'] == []
            checks[f'{mode}:U104_supplement_evidence'] = 'evidence:demo:supplement' in after['results'][0]['evidence']
        for cid, unknown in ((CONFIG_L, 11), (CONFIG_L1, 12)):
            cfg = baseline[f'CQ01_02:3:{cid}']['data']
            checks[f'CQ02:{cid}:source_rows_positions'] = len(cfg['items']) == 74 and sum(len(i['positions']) for i in cfg['items']) == 128
            checks[f'CQ02:{cid}:unconfirmed_population'] = sum(i['population'] == 'unknown' for i in cfg['items']) == unknown
        checks['CQ03:expected_source_differences'] = [c['position'] for c in baseline['CQ03:3']['data']['changes']] == ['R50', 'R51', 'R6', 'U2']
        sources = {n['id']: n for n in baseline['CQ01_sources:SourceDocument']['data']}
        evidence = baseline['CQ01_sources:Evidence']['data']
        checks['source_bytes_and_hashes'] = all(hashlib.sha256((ROOT / n['path']).read_bytes()).hexdigest() == n['sha256'] for n in sources.values())
        checks['source_origin_and_locator'] = all(e['origin'] == sources[e['source_id']]['origin'] and bool(e['locator']) for e in evidence)
        checks['real_and_synthetic_separated'] = {n['origin'] for n in sources.values()} == {'real', 'synthetic'}
        checks['graph_sync_preserves_approved_version'] = all(s._version() == 3 for s in services.values())
        return {'created_at': datetime.now(timezone.utc).isoformat(), 'database_schema': database.schema,
                'projection_namespace': sync.graph_views.namespace, 'versions': [1, 2, 3],
                'execution_mode': 'real PostgreSQL, pySHACL/SPARQL, Neo4j; scripted human approval identities; no model API',
                'passed': all(checks.values()), 'checks': checks,
                'synchronized_versions': [r['data_version'] for r in synchronized],
                'CQ09': {'status': 'proposed_not_implemented'},
                'CQ10_scope': 'exact source-scoped component IDs; no cross-revision identity inference',
                'remaining': ['real authentication and human UI acceptance', 'arbitrary PDF/OCR and unknown population policy',
                              'cross-revision component identity and full PDF comparison', 'AWS deployment and operational recovery'],
                'answers': {mode: {'before': baseline[f'CQ05_06_07:2:{mode}']['data'],
                                   'after': baseline[f'CQ05_06_07:3:{mode}']['data']} for mode in ('A', 'B')}}
    finally:
        for service in services.values():
            service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env.local')
    parser.add_argument('--graph-env-file', default=None)
    parser.add_argument('--schema', default=None, help='Fresh PostgreSQL schema; default creates demo_<uuid>')
    parser.add_argument('--output', default='artifacts/local/cq_graph_backends.json')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    permitted = (ROOT / 'artifacts/local').resolve()
    if not output.is_relative_to(permitted):
        parser.error('Store generated execution reports under artifacts/local/')
    values = environment_values(args.env_file)
    if args.graph_env_file:
        graph_values = environment_values(args.graph_env_file, environ={})
        values.update(graph_values)
        values.update(os.environ)
    os.environ.update(values)
    settings = GraphSettings.from_environment(environ=values, backend='neo4j')
    report = verify(schema=args.schema, graph_settings=settings)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'schema': report['database_schema'],
                      'passed': report['passed'], 'checks': report['checks'],
                      'CQ09': report['CQ09'], 'CQ10_scope': report['CQ10_scope']}, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
