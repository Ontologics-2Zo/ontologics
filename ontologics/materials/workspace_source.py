"""Trusted A-only request-store configuration for the shared web adapter.

No browser input selects a database or identity. Default to the same store;
separate development stores retain the common identity and request budget.
"""
import os
from dataclasses import replace
from ..database import DatabaseSettings
from ..graph_views import GraphSettings
from ..host import CommonHost


def configured_request_host(host, *, environ=None):
    values = os.environ if environ is None else environ
    schema = values.get('MAT_REQUEST_SCHEMA')
    db_file = values.get('MAT_REQUEST_DATABASE_ENV')
    graph_file = values.get('MAT_REQUEST_GRAPH_ENV')
    namespace = values.get('MAT_REQUEST_GRAPH_NAMESPACE')
    backend = values.get('MAT_REQUEST_GRAPH_BACKEND')
    if not schema:
        if db_file or graph_file or namespace or backend:
            raise ValueError('별도 요청 저장소는 MAT_REQUEST_SCHEMA가 필요합니다.')
        return host
    database = (DatabaseSettings.from_environment(schema=schema, env_file=db_file, environ={})
                if db_file else replace(host._database, schema=schema))
    graph = (GraphSettings.from_environment(env_file=graph_file, environ={}, backend=backend)
             if graph_file or backend else host._graph_settings)
    if namespace:
        if graph is None or graph.backend != 'neo4j':
            raise ValueError('요청 그래프 namespace는 명시적인 Neo4j 설정이 필요합니다.')
        graph = replace(graph, namespace=namespace)
    return CommonHost(host._identities, database, graph_settings=graph)
