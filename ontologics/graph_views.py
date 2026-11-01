"""Trusted read projections; PostgreSQL remains the approval and snapshot authority.

No raw SPARQL/Cypher endpoint is exposed to users or models. A projection can
serve only the exact committed source version and policy fingerprint requested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .database import environment_values
from .model import SCHEMA_VERSION, digest


@dataclass(frozen=True)
class GraphSettings:
    backend: str = 'postgres'
    uri: str = field(default='', repr=False)
    user: str = field(default='', repr=False)
    password: str = field(default='', repr=False)
    database: str = 'neo4j'
    namespace: str = ''

    def __post_init__(self):
        if self.backend not in ('postgres', 'rdf', 'neo4j'):
            raise ValueError('Graph backend must be postgres, rdf, or neo4j')
        if self.namespace and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', self.namespace):
            raise ValueError('Use a dedicated graph namespace of 1..80 safe characters')
        if self.backend == 'neo4j':
            self.require_neo4j()

    def require_neo4j(self):
        if (not self.uri.startswith(('bolt://', 'bolt+s://', 'bolt+ssc://',
                                    'neo4j://', 'neo4j+s://', 'neo4j+ssc://'))
                or not self.user or not self.password or not self.namespace):
            raise ValueError('Neo4j requires URI, credentials, and a dedicated graph namespace')

    @classmethod
    def from_environment(cls, *, env_file=None, environ=None, backend=None):
        values = environment_values(env_file, environ)
        return cls(backend=backend or values.get('ONTOLOGICS_GRAPH_BACKEND', 'postgres'),
                   uri=values.get('NEO4J_URI', ''), user=values.get('NEO4J_USER', ''),
                   password=values.get('NEO4J_PASSWORD', ''),
                   database=values.get('NEO4J_DATABASE', 'neo4j'),
                   namespace=values.get('ONTOLOGICS_GRAPH_NAMESPACE', ''))


class GraphViews:
    def __init__(self, service, settings):
        self.service, self.settings = service, settings
        self._projection = None
        self._rdf_cache = {}

    @property
    def namespace(self):
        # A test/development schema must never see another schema's projection.
        database = self.service.db.execute('SELECT current_database() AS name').fetchone()['name']
        return self.settings.namespace + ':' + digest({'database': database, 'schema': self.service.schema})[:32]

    def close(self):
        if self._projection is not None:
            self._projection.close()

    def projection(self):
        from .graph_neo4j import Neo4jProjection
        self.settings.require_neo4j()
        if self._projection is None:
            self._projection = Neo4jProjection(self.settings.uri, self.settings.user,
                self.settings.password, namespace=self.namespace, database=self.settings.database)
        return self._projection

    def rdf(self, version, nodes):
        from .graph_rdf import RdfSnapshot
        from .service import DomainError, RULE_VERSION
        key = (version, digest(nodes), SCHEMA_VERSION, RULE_VERSION)
        if key not in self._rdf_cache:
            try:
                snapshot = RdfSnapshot(nodes, version=version,
                    schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)
            except ValueError:
                raise DomainError('GRAPH_VALIDATION_FAILED', 'RDF/SHACL validation failed') from None
            if len(self._rdf_cache) >= 4:
                self._rdf_cache.pop(next(iter(self._rdf_cache)))
            self._rdf_cache[key] = snapshot
        return self._rdf_cache[key]

    def reader(self, version):
        from .graph_neo4j import ProjectionError
        from .service import DomainError, RULE_VERSION
        if self.settings.backend == 'postgres':
            return None
        version = self.service._version() if version is None else version
        nodes = self.service._nodes(version)
        if self.settings.backend == 'rdf':
            return self.rdf(version, nodes)
        content_hash = digest(nodes)
        ready = self.service.db.execute(
            'SELECT * FROM graph_projections WHERE backend=%s AND namespace=%s AND data_version=%s',
            ('neo4j', self.namespace, version)).fetchone()
        if not ready:
            raise DomainError('GRAPH_NOT_READY', 'Requested approved version is not synchronized')
        expected = (content_hash, SCHEMA_VERSION, RULE_VERSION, len(nodes))
        if tuple(ready[k] for k in ('content_hash', 'schema_version', 'rule_version', 'node_count')) != expected:
            raise DomainError('GRAPH_VERSION_CONFLICT', 'Projection does not match the approved version')
        try:
            return self.projection().for_version(version=version, content_hash=content_hash,
                schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)
        except ProjectionError as exc:
            raise DomainError(exc.code, str(exc)) from None

    def query(self, version, method, *args, **kwargs):
        from .graph_neo4j import ProjectionError
        from .service import DomainError
        reader = self.reader(version)
        try:
            return getattr(reader, method)(*args, **kwargs)
        except ProjectionError as exc:
            raise DomainError(exc.code, str(exc)) from None
        except ValueError:
            raise DomainError('NOT_FOUND', 'Unknown graph entity') from None

    def synchronize(self, actor, version, *, request_key):
        """Host-only index operation. Never seed, approve, or apply a graph fact."""
        from .graph_neo4j import ProjectionError
        from .service import DomainError, RULE_VERSION
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise DomainError('INVALID_VERSION', 'Choose an existing committed data version')
            nodes = self.service._nodes(version)
            reason = self.service.db.execute('SELECT reason FROM snapshots WHERE version=%s', (version,)).fetchone()['reason']
            if not (reason.startswith('approved:') or reason == 'development_seed'):
                raise DomainError('APPROVAL_REQUIRED', 'Only approved or explicit development snapshots may be projected')
            # Validation runs before any external write. Dev seed provenance stays explicit.
            rdf = self.rdf(version, nodes)
            if rdf.nodes() != nodes:
                raise DomainError('GRAPH_INTEGRITY', 'RDF projection changed source content or order')
            try:
                self.projection().sync(nodes, version=version,
                    schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION)
            except ProjectionError as exc:
                raise DomainError(exc.code, str(exc)) from None
            from .harness import current_budget
            if current_budget() is not None:
                current_budget().check()
            metadata = {'backend': 'neo4j', 'namespace': self.namespace,
                'content_hash': digest(nodes), 'node_count': len(nodes),
                'source_status': reason, 'status': 'ready'}
            self.service.db.execute('''INSERT INTO graph_projections VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(backend,namespace,data_version) DO UPDATE SET
                content_hash=EXCLUDED.content_hash, schema_version=EXCLUDED.schema_version,
                rule_version=EXCLUDED.rule_version,node_count=EXCLUDED.node_count,ready_at=EXCLUDED.ready_at''',
                ('neo4j', self.namespace, version, metadata['content_hash'], SCHEMA_VERSION,
                 RULE_VERSION, len(nodes), self.service.now()))
            return self.service._envelope(ok=True, data=metadata, version=version)
        return self.service._run(actor, 'synchronize_graph', {'version': version,
            'namespace': self.settings.namespace, 'backend': 'neo4j'}, act,
            permission='index', request_key=request_key)
