"""Approve synthetic B requests in a fresh test_ PostgreSQL schema.

Uses development identities and existing common approval/lookup. No model calls,
A processing, Neo4j synchronization or operational approval are claimed.
The explicitly selected test schema remains available for inspection.
"""
from copy import deepcopy
import argparse
import json
import re

from examples.order_fulfillment.b_material_request import load_examples
from ontologics.database import DatabaseSettings
from ontologics.demo import must
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.service import Service


def verify(database):
    if not re.fullmatch(r'test_[a-z0-9_]+', database.schema):
        raise ValueError('Use a fresh test_ schema, never an operational store')
    author = UserIdentity('b-request-author', frozenset({'read', 'draft'}))
    reviewer = UserIdentity('b-request-reviewer', frozenset({'read', 'review'}))
    reader = UserIdentity('b-request-reader', frozenset({'read'}))
    identities = DevelopmentIdentityProvider([author, reviewer, reader], enabled=True)
    host = CommonHost(identities, database)
    demo = load_examples()
    audit = Service(database, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read']}})
    try:
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        base = must(audit.bootstrap('fixture', catalog, request_key='b-request-seed'))['data_version']
        registered = []
        for name, expected_revision in (('new_30', 0), ('revised_40', 1), ('independent_10', 0)):
            original = deepcopy(demo['requests'][name])
            request = {key: value for key, value in original.items()
                       if key not in {'type', 'origin', 'request_revision', 'reference_data_version'}}
            evidence_ids = set(request['evidence'])
            source_ids = {node['source_id'] for node in demo['supporting_nodes']
                          if node['type'] == 'Evidence' and node['id'] in evidence_ids}
            support = [node for node in demo['supporting_nodes'] if node['id'] in evidence_ids | source_ids]
            with host.request(DevelopmentContext(author.subject)) as session:
                draft = must(session.material_request_draft(request, supporting_nodes=support,
                    expected_data_version=base, expected_request_revision=expected_revision,
                    request_key='draft-' + name))['data']
                if not draft['validation']['valid'] or draft['status'] != 'awaiting_review':
                    raise RuntimeError('Request draft is not ready for review')
            with host.request(DevelopmentContext(reviewer.subject)) as session:
                must(session.review(draft['draft_id'], draft['draft_version'], decision='approve',
                    note='Development review stand-in: compare source and structured values', request_key='review-' + name))
            applied = must(host.apply_approved(draft['draft_id'], draft['draft_version'], request_key='apply-' + name))
            base = applied['data_version']
            with host.request(DevelopmentContext(reader.subject)) as session:
                stored = must(session.call('lookup', {'node_id': request['id'], 'version': base}))['data'][0]
                if stored['quantity'] != original['quantity'] or stored['request_revision'] != expected_revision + 1:
                    raise RuntimeError('Stored request differs from the reviewed request')
                for eid in stored['evidence']:
                    evidence = must(session.call('lookup', {'node_id': eid, 'version': base}))['data'][0]
                    must(session.call('lookup', {'node_id': evidence['source_id'], 'version': base}))
            registered.append(dict(case=name, request_id=stored['id'],
                request_revision=stored['request_revision'], quantity=stored['quantity'],
                reference_data_version=stored['reference_data_version'], applied_version=base))
        latest = must(audit.lookup('fixture', kind='AdditionalMaterialReviewRequest', version=base))['data']
        quantities = {node['id']: node['quantity'] for node in latest}
        expected = {demo['requests'][name]['id']: demo['requests'][name]['quantity']
                    for name in ('revised_40', 'independent_10')}
        return dict(passed=quantities == expected, scope='synthetic_b_postgres_registration',
            identity_mode='development', database_schema=database.schema,
            registered=registered, latest_quantities=quantities,
            stored=True, approved=True, neo4j_verified=False, a_processed=False)
    finally:
        audit.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True, help='Fresh test_ schema; results remain for inspection')
    parser.add_argument('--env-file', help='Development DB settings; never committed')
    args = parser.parse_args()
    result = verify(DatabaseSettings.from_environment(schema=args.schema, env_file=args.env_file))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
