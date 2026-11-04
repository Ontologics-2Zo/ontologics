"""Seed an empty development schema with the common fixture plus the C claim-response basis.

The common screen needs C's boards, production lines and production records to be in the graph
before the claim steps can trace a claim. Those are common types, so the claims draft path refuses
them: registering reference data is not a business proposal. This mirrors
``python -m ontologics.web seed`` and adds ``claim_response_nodes()`` to the same one-time
development seed, so a demonstration starts from a graph that holds both.

No approval is bypassed. Nothing a person proposes on the screen is seeded here; every claim record
still goes through a draft and a separate reviewer. Development schemas only, never production.

    python -m examples.claim_response.seed_common --schema <new schema> --env-file .env.local
"""
from __future__ import annotations

import argparse
import json
import sys

from ontologics.claim_response.fixtures import claim_response_nodes
from ontologics.database import DatabaseError, DatabaseSettings
from ontologics.demo import fixture_nodes
from ontologics.model import ROOT, validate_graph
from ontologics.service import Service

SEED_ACTOR = 'claim-response-seed'
# Same filter as ontologics.web.seed_development: L1 is registered through the S2 approval path.
RESERVED_L1 = {'config:esp32-poe:L1:WROOM', 'revision:esp32-poe:L1'}


def seed_nodes(*, reserve_l1=False):
    catalog = json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']
    if reserve_l1:
        catalog = [n for n in catalog if n['id'] not in RESERVED_L1
                   and n.get('configuration_id') != 'config:esp32-poe:L1:WROOM'
                   and not n['id'].startswith('component:olimex-L1-wroom:')]
    fictional, _ = fixture_nodes()
    return catalog + fictional + claim_response_nodes()


def main():
    parser = argparse.ArgumentParser(description='Seed a development schema with the common fixture and the C basis.')
    parser.add_argument('--schema', required=True, help='Dedicated, empty development PostgreSQL schema')
    parser.add_argument('--env-file', help='Database settings file; never commit secrets')
    parser.add_argument('--reserve-l1', action='store_true', help='Seed only L configuration; register L1 through S2')
    args = parser.parse_args()

    nodes = seed_nodes(reserve_l1=args.reserve_l1)
    report = validate_graph(nodes)
    if not report['valid']:
        print(json.dumps({'ok': False, 'errors': report['errors'][:20]}, ensure_ascii=False, indent=2))
        return 1
    try:
        database = DatabaseSettings.from_environment(schema=args.schema, env_file=args.env_file)
        service = Service(database, principals={SEED_ACTOR: {'kind': 'human', 'roles': ['seed']}})
        try:
            result = service.bootstrap(SEED_ACTOR, nodes, request_key='claim-response-seed-v1')
        finally:
            service.close()
    except DatabaseError as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    if not result['ok']:
        print(json.dumps({'ok': False, 'errors': result['errors']}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({'ok': True, 'schema': args.schema, 'nodes': len(nodes),
                      'claim_response_nodes': len(claim_response_nodes())}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
