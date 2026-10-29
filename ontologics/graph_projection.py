"""Host-only projection sync/status CLI; no approval or arbitrary query execution."""
from __future__ import annotations

import argparse
from dataclasses import replace

from .database import DatabaseError, DatabaseSettings
from .graph_views import GraphSettings
from .model import canonical
from .service import Service


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('sync', 'status'))
    parser.add_argument('--schema', required=True)
    parser.add_argument('--version', required=True, type=int)
    parser.add_argument('--request-key', help='Required for sync; reuse only for the same operation')
    parser.add_argument('--env-file')
    parser.add_argument('--graph-env-file')
    args = parser.parse_args(argv)
    if args.command == 'sync' and not args.request_key:
        parser.error('sync requires --request-key')
    service = None
    try:
        database = DatabaseSettings.from_environment(schema=args.schema, env_file=args.env_file)
        settings = GraphSettings.from_environment(env_file=args.graph_env_file or args.env_file)
        settings.require_neo4j()
        actor = 'system:graph-projector'
        service = Service(database, principals={actor: {'kind': 'system', 'roles': ['read', 'index']}},
                          graph_settings=replace(settings, backend='neo4j'))
        if args.command == 'sync':
            result = service.graph_views.synchronize(actor, args.version, request_key=args.request_key)
        else:
            # Exercise a real exact-version relation read, not merely a socket ping.
            result = service.lookup(actor, kind='SourceDocument', version=args.version)
            if result['ok']:
                result['data'] = {'status': 'ready', 'source_count': len(result['data']),
                                  'backend': 'neo4j', 'namespace': service.graph_views.namespace}
        print(canonical(result))
        return 0 if result['ok'] else 1
    except (DatabaseError, ValueError):
        print(canonical({'ok': False, 'errors': [{'code': 'GRAPH_CONFIGURATION_ERROR',
            'message': 'Check PostgreSQL and graph settings; credentials are never printed.'}]}))
        return 1
    finally:
        if service is not None:
            service.close()


if __name__ == '__main__':
    raise SystemExit(main())
