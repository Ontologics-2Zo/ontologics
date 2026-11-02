"""Original eleven A/B fixture types through the host and three read backends.

Public originals and existing synthetic fixtures only; no external model calls.
Requires an empty, explicitly named test_ PostgreSQL schema and a test namespace.
"""
from contextlib import ExitStack
from dataclasses import replace
import argparse
import json
import re

from ontologics.database import DatabaseSettings
from ontologics.demo import example_queries, fixture_nodes, must
from ontologics.domain_schema import SCHEMA as BUSINESS_SCHEMA
from ontologics.graph_views import GraphSettings
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.model import ROOT, SCHEMA_VERSION, digest
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.order_fulfillment.service import Service as BusinessService
from ontologics.service import Service
from ontologics.web import USERS
from examples.graph_cq_check import comparable


def candidates():
    """Reuse unchanged source-backed records, without importing role test code."""
    base = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + fixture_nodes()[0]
    added = (json.loads((ROOT / 'data/production_planning/graph_nodes_v1.json').read_text())['nodes']
             + load_demo_contract() + load_demo_delivery(mixed=True))
    return base, added


def verify(database, settings):
    if not re.fullmatch(r'test_[A-Za-z0-9_]+', database.schema):
        raise ValueError('Use a fresh test_ schema, never an operational store')
    settings.require_neo4j()
    identities = DevelopmentIdentityProvider(USERS, enabled=True)
    host = CommonHost(identities, database, graph_settings=settings)
    base, added = candidates()
    # This historical fixture predates additional material requests. Preserve its
    # coverage without claiming newly registered types have been approved here.
    fixture_types = {node['type'] for node in added} & set(BUSINESS_SCHEMA)
    checks = {}
    with ExitStack() as stack:
        audit = stack.enter_context(_service(database))
        must(audit.bootstrap('fixture', base, request_key='ab-seed'))
        before_hash = digest(audit._nodes(1))
        operations = [{'op': 'put', 'node': n} for n in added]
        with host.request(DevelopmentContext('dev-alice')) as request:
            draft = must(request.call('prepare_draft', {'operations': operations, 'request_key': 'ab-draft'}))['data']
            checks['full_candidate_valid'] = draft['validation']['valid']
            denied = request.review(draft['draft_id'], 1, decision='approve', note='Self test', request_key='ab-self')
            checks['self_approval_blocked'] = not denied['ok'] and denied['errors'][0]['code'] == 'SELF_APPROVAL_FORBIDDEN'
        checks['draft_does_not_change_snapshot'] = audit._version() == 1 and digest(audit._nodes(1)) == before_hash
        denied_apply = host.apply_approved(draft['draft_id'], 1, request_key='ab-before-approval')
        checks['unapproved_apply_blocked'] = not denied_apply['ok'] and denied_apply['errors'][0]['code'] == 'APPROVAL_REQUIRED'
        with host.request(DevelopmentContext('dev-alice')) as request:
            revised = must(request.call('prepare_draft', {'operations': operations, 'draft_id': draft['draft_id'],
                'expected_version': 1, 'request_key': 'ab-revise'}))['data']
        with host.request(DevelopmentContext('dev-bob')) as request:
            stale = request.review(draft['draft_id'], 1, decision='approve', note='Old version', request_key='ab-stale')
            checks['old_draft_version_blocked'] = not stale['ok'] and stale['errors'][0]['code'] == 'SUPERSEDED_DRAFT'
            approved = must(request.review(draft['draft_id'], 2, decision='approve', note='Source-backed synthetic integration records', request_key='ab-review'))
        checks['exact_review_hash'] = approved['data']['content_hash'] == draft['content_hash'] == digest(operations)
        application = must(host.apply_approved(draft['draft_id'], 2, request_key='ab-apply'))
        checks['approved_projection_ready'] = application['data']['graph_projection']['status'] == 'ready'
        checks['approved_version'] = application['data_version'] == 2
        replay = must(host.apply_approved(draft['draft_id'], 2, request_key='ab-apply'))
        checks['idempotent_apply'] = replay['data_version'] == 2 and audit._version() == 2
        readers = {}
        for backend in ('postgres', 'rdf', 'neo4j'):
            readers[backend] = stack.enter_context(_service(database, replace(settings, backend=backend)))
        expected = {}
        for backend, reader in readers.items():
            results = {kind: comparable(must(reader.lookup('fixture', kind=kind, version=2))) for kind in BUSINESS_SCHEMA}
            results['jsonld'] = comparable(must(reader.export_graph('fixture', version=2)))
            results['delivery'] = comparable(must(reader.delivery_round('fixture', ROUND, version=2)))
            # Freeze only audit/query time, keeping facts and business arithmetic unchanged.
            reader.now = lambda: '2026-10-27T10:10:00+09:00'
            results['forecast'] = comparable(must(reader.delivery_forecast('fixture', ROUND, AS_OF, version=2)))
            if backend == 'postgres': expected = results
            else: checks[backend + '_same_nodes_relations_and_business_results'] = results == expected
        checks['all_11_types_present'] = len(fixture_types) == 11 and all(expected[k]['data'] for k in fixture_types)
        checks['old_core_snapshot_unchanged'] = digest(audit._nodes(1)) == before_hash
        # A/B business records must not alter existing temporal A/B semantics.
        with host.request(DevelopmentContext('dev-alice')) as request:
            supplement = must(request.call('prepare_draft', {'operations': fixture_nodes()[1], 'request_key': 'ab-supplement'}))['data']
        with host.request(DevelopmentContext('dev-bob')) as request:
            must(request.review(supplement['draft_id'], 1, decision='approve', note='U104 synthetic removal evidence', request_key='ab-supplement-review'))
        must(host.apply_approved(supplement['draft_id'], 1, request_key='ab-supplement-apply'))
        comparisons = {}
        for backend, reader in readers.items():
            comparisons[backend] = {mode: comparable(must(reader.compare_investigation('fixture', query, 2, 3)))
                                    for mode, query in example_queries().items()}
        checks['temporal_backend_parity'] = comparisons['postgres'] == comparisons['rdf'] == comparisons['neo4j']
        for mode in ('A', 'B'):
            rows = comparisons['postgres'][mode]['data']['after']['results']
            checks['U104_' + mode] = rows[0]['status'] == ('excluded' if mode == 'A' else 'included')
            checks['U106_' + mode] = rows[2]['status'] == 'needs_confirmation'
        return {'passed': all(checks.values()), 'checks': checks, 'schema_version': SCHEMA_VERSION,
                'data_versions': [1, 2, 3], 'business_types': sorted(fixture_types),
                'database_schema': database.schema, 'origin': 'public originals + synthetic fixtures',
                'model_calls': 0}


from contextlib import contextmanager

@contextmanager
def _service(database, settings=None):
    service = BusinessService(database, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read']}}, graph_settings=settings)
    try: yield service
    finally: service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True, help='Fresh test_ schema; results remain for inspection')
    args = parser.parse_args()
    result = verify(DatabaseSettings.from_environment(schema=args.schema), GraphSettings.from_environment(backend='neo4j'))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
