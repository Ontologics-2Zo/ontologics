"""Immutable, version-pinned Neo4j read projections of PostgreSQL snapshots.

This module neither approves drafts nor makes a projection authoritative. The trusted
host supplies an approved snapshot, validates RDF/SHACL, and records synchronization
attempts. One Neo4j transaction installs the complete projection and READY marker.
All queries bind values; callers cannot provide Cypher or choose another version.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from neo4j import GraphDatabase, Query, unit_of_work
from neo4j.exceptions import Neo4jError, DriverError

from .model import SCHEMA, canonical, digest, validate_graph


PROJECTION_VERSION = 'neo4j-projection-1'
TEMPORAL_TYPES = ['ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage']


class ProjectionError(ValueError):
    """Safe error contract; driver text and connection secrets are never exposed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectionError('INVALID_VERSION', 'A nonnegative integer data version is required')


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError('INVALID_INPUT', name + ' must be nonempty text')


def _links(nodes):
    """Exact semantic edges, including source/evidence and ordered evidence IDs."""
    result = []
    for node in nodes:
        for field in SCHEMA[node['type']][1]:
            result.append({'source': node['id'], 'target': node[field], 'field': field, 'ordinal': 0})
        for ordinal, target in enumerate(node['evidence']):
            result.append({'source': node['id'], 'target': target, 'field': 'evidence', 'ordinal': ordinal})
    return result


def _edge_key(edge):
    return edge['source'], edge['field'], edge['ordinal'], edge['target']


def _timeout():
    # Lazy import avoids the Service -> graph adapter -> Harness -> Service cycle.
    from .harness import current_budget
    budget = current_budget()
    return min(30.0, budget.remaining_seconds()) if budget is not None else 30.0


class _BoundedTransaction:
    """Do not dispatch a second query after the shared request has expired."""
    def __init__(self, tx):
        self.tx = tx

    def run(self, *args, **kwargs):
        _timeout()
        result = self.tx.run(*args, **kwargs)
        _timeout()
        return result


class Neo4jProjection:
    """Trusted host adapter. Never register this object as an Agent tool.

    ``namespace`` isolates one PostgreSQL store; different stores may have the same
    numeric versions. Connection/transaction bounds are safety limits, not service
    budget replacements. The host still applies the shared execution deadline.
    """

    def __init__(self, url, user, password, *, namespace, database='neo4j'):
        if not isinstance(url, str) or not url.startswith(('bolt://', 'bolt+s://', 'bolt+ssc://',
                                                          'neo4j://', 'neo4j+s://', 'neo4j+ssc://')):
            raise ProjectionError('INVALID_INPUT', 'Configure a Neo4j Bolt URI')
        if not isinstance(namespace, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}', namespace):
            raise ProjectionError('INVALID_INPUT', 'Use a dedicated projection namespace')
        for value, name in ((user, 'user'), (password, 'password'), (database, 'database')):
            _text(value, name)
        self.namespace, self.database = namespace, database
        self._constraints_ready = False
        try:
            self._driver = GraphDatabase.driver(url, auth=(user, password), connection_timeout=5,
                connection_acquisition_timeout=10, max_transaction_retry_time=0)
        except (ValueError, Neo4jError, DriverError):
            raise ProjectionError('GRAPH_UNAVAILABLE', 'Neo4j configuration could not be initialized') from None

    def close(self):
        self._driver.close()

    def _execute(self, action: Callable, *, write=False):
        timeout = _timeout()
        @unit_of_work(timeout=timeout)
        def bounded(tx):
            _timeout()
            result = action(_BoundedTransaction(tx))
            _timeout()
            return result
        try:
            with self._driver.session(database=self.database) as session:
                _timeout()
                result = (session.execute_write if write else session.execute_read)(bounded)
                _timeout()
                return result
        except ProjectionError:
            raise
        except (Neo4jError, DriverError, OSError):
            raise ProjectionError('GRAPH_UNAVAILABLE', 'Neo4j operation failed; no alternative version was used') from None

    def _ensure_constraints(self):
        if self._constraints_ready:
            return
        _timeout()
        try:
            with self._driver.session(database=self.database) as session:
                for statement in (
                    'CREATE CONSTRAINT ontologics_snapshot_key IF NOT EXISTS '
                    'FOR (s:OntologicsSnapshot) REQUIRE (s.namespace, s.version) IS UNIQUE',
                    'CREATE CONSTRAINT ontologics_entity_key IF NOT EXISTS '
                    'FOR (n:OntologicsEntity) REQUIRE (n.namespace, n.version, n.id) IS UNIQUE',
                ):
                    session.run(Query(statement, timeout=_timeout())).consume()
                    _timeout()
            self._constraints_ready = True
        except (Neo4jError, DriverError, OSError):
            raise ProjectionError('GRAPH_UNAVAILABLE', 'Neo4j projection constraints could not be initialized') from None

    def sync(self, nodes, *, version, schema_version, rule_version):
        """Publish a complete immutable projection or roll back every graph write.

        Retry of identical content is idempotent. A version cannot be overwritten
        with another snapshot, mapping, schema, or time-rule version. PostgreSQL
        approval success does not depend on this separate transaction succeeding.
        """
        _timeout()
        _version(version)
        _text(schema_version, 'schema_version'); _text(rule_version, 'rule_version')
        report = validate_graph(nodes)
        if not report['valid']:
            raise ProjectionError('GRAPH_VALIDATION_FAILED', 'Invalid source snapshot cannot be projected')
        # Detach from the caller before building the hash and transaction parameters.
        nodes = json.loads(canonical(nodes))
        edges = _links(nodes)
        metadata = {'namespace': self.namespace, 'version': version, 'content_hash': digest(nodes),
                    'schema_version': schema_version, 'rule_version': rule_version,
                    'projection_version': PROJECTION_VERSION, 'node_count': len(nodes),
                    'relationship_count': len(edges)}
        rows = [{'id': n['id'], 'kind': n['type'], 'origin': n['origin'],
                 'payload': canonical(n), 'ordinal': i} for i, n in enumerate(nodes)]
        self._ensure_constraints()

        def write(tx):
            # MERGE + a transient write acquires the version lock even for an
            # existing READY snapshot, serializing same-version concurrent retries.
            row = tx.run('MERGE (s:OntologicsSnapshot {namespace:$namespace, version:$version}) '
                'ON CREATE SET s.state="loading", s += $metadata '
                'SET s._sync_lock=true REMOVE s._sync_lock RETURN properties(s) AS metadata',
                **metadata, metadata=metadata).single()
            current = row['metadata']
            if any(current.get(k) != v for k, v in metadata.items()):
                raise ProjectionError('GRAPH_VERSION_CONFLICT', 'Data version already has different projection content or rules')
            if current['state'] == 'ready':
                self._verify(tx, metadata)
                return dict(metadata, state='ready', replayed=True)
            if current['state'] != 'loading':
                raise ProjectionError('GRAPH_NOT_READY', 'Projection state is not publishable')
            count = tx.run('MATCH (n:OntologicsEntity {namespace:$namespace, version:$version}) '
                           'RETURN count(n) AS count', **metadata).single()['count']
            if count:
                # Atomic writes cannot leave staged nodes. Do not silently reuse
                # or delete an externally created partial projection.
                raise ProjectionError('GRAPH_INTEGRITY', 'Unexpected partial projection requires operator inspection')
            self._write_nodes(tx, metadata, rows)
            self._write_edges(tx, metadata, edges)
            self._verify(tx, metadata, require_ready=False)
            tx.run('MATCH (s:OntologicsSnapshot {namespace:$namespace, version:$version}) '
                   'SET s.state="ready"', **metadata).consume()
            return dict(metadata, state='ready', replayed=False)
        return self._execute(write, write=True)

    def _write_nodes(self, tx, metadata, rows):
        tx.run('UNWIND $rows AS row '
               'CREATE (n:OntologicsEntity {namespace:$namespace, version:$version, id:row.id, '
               'kind:row.kind, origin:row.origin, payload:row.payload, ordinal:row.ordinal})',
               **metadata, rows=rows).consume()

    def _write_edges(self, tx, metadata, edges):
        tx.run('UNWIND $edges AS edge '
               'MATCH (a:OntologicsEntity {namespace:$namespace, version:$version, id:edge.source}) '
               'MATCH (b:OntologicsEntity {namespace:$namespace, version:$version, id:edge.target}) '
               'CREATE (a)-[:OntologicsLink {field:edge.field, ordinal:edge.ordinal}]->(b)',
               **metadata, edges=edges).consume()

    def _verify(self, tx, expected, *, require_ready=True):
        row = tx.run('MATCH (s:OntologicsSnapshot {namespace:$namespace, version:$version}) '
                     'RETURN properties(s) AS metadata', **expected).single()
        if row is None or (require_ready and row['metadata'].get('state') != 'ready'):
            raise ProjectionError('GRAPH_NOT_READY', 'Requested data version has no completed Neo4j projection')
        meta = row['metadata']
        for field in ('namespace', 'version', 'content_hash', 'schema_version', 'rule_version', 'projection_version'):
            if meta.get(field) != expected.get(field):
                raise ProjectionError('GRAPH_VERSION_CONFLICT', 'Projection does not match the requested snapshot or rules')
        rows = list(tx.run('MATCH (n:OntologicsEntity {namespace:$namespace, version:$version}) '
                           'RETURN properties(n) AS node ORDER BY n.ordinal', **expected))
        nodes = []
        try:
            for ordinal, record in enumerate(rows):
                props = record['node']; node = json.loads(props['payload'])
                if (props['ordinal'] != ordinal or props['id'] != node['id'] or props['kind'] != node['type']
                        or props['origin'] != node['origin'] or node['type'] not in SCHEMA):
                    raise ValueError()
                nodes.append(node)
            if len(nodes) != meta['node_count'] or digest(nodes) != expected['content_hash']:
                raise ValueError()
            required = sorted(_links(nodes), key=_edge_key)
            # Include every outbound/inbound edge touching this version, not just
            # nominal OntologicsLink edges. Cross-version and foreign links fail.
            actual_rows = tx.run('MATCH (a)-[r]->(b) '
                'WHERE (a:OntologicsEntity AND a.namespace=$namespace AND a.version=$version) '
                'OR (b:OntologicsEntity AND b.namespace=$namespace AND b.version=$version) '
                'RETURN a.namespace AS ans, a.version AS av, a.id AS source, '
                'b.namespace AS bns, b.version AS bv, b.id AS target, '
                'type(r) AS rel_type, r.field AS field, r.ordinal AS ordinal', **expected)
            actual = []
            for rel in actual_rows:
                if (rel['ans'] != expected['namespace'] or rel['bns'] != expected['namespace']
                        or rel['av'] != expected['version'] or rel['bv'] != expected['version']
                        or rel['rel_type'] != 'OntologicsLink'):
                    raise ValueError()
                actual.append({k: rel[k] for k in ('source', 'target', 'field', 'ordinal')})
            if (len(actual) != meta['relationship_count'] or
                    sorted(actual, key=_edge_key) != required):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ProjectionError('GRAPH_INTEGRITY', 'Projection content or relationships do not match its snapshot') from None
        return nodes

    def for_version(self, *, version, content_hash, schema_version, rule_version):
        _version(version)
        if not isinstance(content_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', content_hash):
            raise ProjectionError('INVALID_INPUT', 'A source snapshot SHA-256 is required')
        _text(schema_version, 'schema_version'); _text(rule_version, 'rule_version')
        snapshot = Neo4jSnapshot(self, version, content_hash, schema_version, rule_version)
        # Fail before callers obtain a usable snapshot when synchronization is absent.
        snapshot._read(lambda tx: None)
        return snapshot


@dataclass(frozen=True)
class Neo4jSnapshot:
    projection: Neo4jProjection
    version: int
    content_hash: str
    schema_version: str
    rule_version: str

    def _expected(self):
        return {'namespace': self.projection.namespace, 'version': self.version,
                'content_hash': self.content_hash, 'schema_version': self.schema_version,
                'rule_version': self.rule_version, 'projection_version': PROJECTION_VERSION}

    def _read(self, action, expected=lambda nodes: None):
        def read(tx):
            nodes = self.projection._verify(tx, self._expected())
            wanted = expected(nodes)
            try:
                result = action(tx)
            except ProjectionError as exc:
                if exc.code == 'NOT_FOUND':
                    raise ProjectionError('GRAPH_INTEGRITY', 'A verified relation disappeared during the query') from None
                raise
            except (KeyError, TypeError, ValueError):
                raise ProjectionError('GRAPH_INTEGRITY', 'Query returned malformed projection content') from None
            # Neo4j read-committed transactions are not immutable read snapshots.
            # Bind the actual traversal result to the already verified content.
            if result != wanted:
                raise ProjectionError('GRAPH_INTEGRITY', 'Query result changed after snapshot verification')
            return result
        return self.projection._execute(read)

    @staticmethod
    def _decode(records, key='node'):
        return [json.loads(row[key]['payload']) for row in records]

    def lookup(self, node_id=None, kind=None):
        def expected(nodes):
            rows = [n for n in nodes if (node_id is None or n['id'] == node_id)
                    and (kind is None or n['type'] == kind)]
            if node_id and not rows:
                raise ProjectionError('NOT_FOUND', 'Unknown node ID')
            return rows
        def read(tx):
            rows = list(tx.run('MATCH (n:OntologicsEntity {namespace:$namespace, version:$version}) '
                'WHERE ($node_id IS NULL OR n.id=$node_id) AND ($kind IS NULL OR n.kind=$kind) '
                'RETURN properties(n) AS node ORDER BY n.ordinal', **self._expected(), node_id=node_id, kind=kind))
            if node_id and not rows:
                raise ProjectionError('NOT_FOUND', 'Unknown node ID')
            return self._decode(rows)
        return self._read(read, expected)

    def nodes(self):
        return self.lookup()

    def temporal_nodes(self):
        def read(tx):
            rows = tx.run('MATCH (n:OntologicsEntity {namespace:$namespace, version:$version}) '
                'WHERE n.kind IN $kinds RETURN properties(n) AS node ORDER BY n.ordinal',
                **self._expected(), kinds=TEMPORAL_TYPES)
            return self._decode(rows)
        return self._read(read, lambda nodes: [n for n in nodes if n['type'] in TEMPORAL_TYPES])

    def configuration(self, configuration_id):
        def expected(nodes):
            index = {n['id']: n for n in nodes}
            config = index.get(configuration_id)
            if config is None or config['type'] != 'Configuration':
                raise ProjectionError('NOT_FOUND', 'Unknown configuration')
            revision = index[config['revision_id']]
            items = [n for n in nodes if n['type'] == 'BOMItem' and n['configuration_id'] == configuration_id]
            return {'configuration': config, 'revision': revision, 'product': index[revision['model_id']],
                    'items': items, 'components': [index[c] for c in sorted({n['component_id'] for n in items})]}
        def read(tx):
            params = dict(self._expected(), cid=configuration_id)
            row = tx.run('MATCH (c:OntologicsEntity {namespace:$namespace, version:$version, id:$cid, kind:"Configuration"}) '
                '-[:OntologicsLink {field:"revision_id"}]->'
                '(r:OntologicsEntity {namespace:$namespace, version:$version, kind:"ProductRevision"}) '
                '-[:OntologicsLink {field:"model_id"}]->'
                '(p:OntologicsEntity {namespace:$namespace, version:$version, kind:"ProductModel"}) '
                'RETURN properties(c) AS configuration, properties(r) AS revision, properties(p) AS product', **params).single()
            if row is None:
                raise ProjectionError('NOT_FOUND', 'Unknown configuration')
            items = self._decode(tx.run('MATCH (b:OntologicsEntity {namespace:$namespace, version:$version, kind:"BOMItem"}) '
                '-[:OntologicsLink {field:"configuration_id"}]->'
                '(c:OntologicsEntity {namespace:$namespace, version:$version, id:$cid}) '
                'RETURN properties(b) AS node ORDER BY b.ordinal', **params))
            components = self._decode(tx.run('MATCH (c:OntologicsEntity {namespace:$namespace, version:$version, id:$cid}) '
                '<-[:OntologicsLink {field:"configuration_id"}]-'
                '(b:OntologicsEntity {namespace:$namespace, version:$version, kind:"BOMItem"}) '
                '-[:OntologicsLink {field:"component_id"}]->'
                '(part:OntologicsEntity {namespace:$namespace, version:$version, kind:"ComponentModel"}) '
                'WITH DISTINCT part RETURN properties(part) AS node ORDER BY part.id', **params))
            return {**{k: json.loads(row[k]['payload']) for k in ('configuration', 'revision', 'product')},
                    'items': items, 'components': components}
        return self._read(read, expected)

    def component_usage(self, component_id):
        def expected(nodes):
            if not any(n['type'] == 'ComponentModel' and n['id'] == component_id for n in nodes):
                raise ProjectionError('NOT_FOUND', 'Unknown source-scoped component')
            return {'component_id': component_id,
                    'items': [n for n in nodes if n['type'] == 'BOMItem' and n['component_id'] == component_id],
                    'scope': 'exact component identity; unverified cross-revision equivalence is not merged'}
        def read(tx):
            params = dict(self._expected(), cid=component_id)
            row = tx.run('MATCH (c:OntologicsEntity {namespace:$namespace, version:$version, id:$cid, kind:"ComponentModel"}) '
                         'RETURN c.id AS id', **params).single()
            if row is None:
                raise ProjectionError('NOT_FOUND', 'Unknown source-scoped component')
            items = self._decode(tx.run('MATCH (b:OntologicsEntity {namespace:$namespace, version:$version, kind:"BOMItem"}) '
                '-[:OntologicsLink {field:"component_id"}]->'
                '(c:OntologicsEntity {namespace:$namespace, version:$version, id:$cid}) '
                'RETURN properties(b) AS node ORDER BY b.ordinal', **params))
            return {'component_id': component_id, 'items': items,
                    'scope': 'exact component identity; unverified cross-revision equivalence is not merged'}
        return self._read(read, expected)
