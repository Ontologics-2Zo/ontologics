"""PostgreSQL-only connection and schema setup; no credentials in errors/logs."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import re
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus


class DatabaseError(RuntimeError):
    pass


def environment_values(env_file=None, environ=None):
    """Shared non-executing KEY=value loader. Process environment wins."""
    values = {}
    paths = [Path(env_file)] if env_file is not None else [Path('.env'), Path('.env.local')]
    for path in paths:
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding='utf-8')
            if len(content) > 16384:
                raise ValueError()
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                name, sep, value = line.partition('=')
                name, value = name.strip(), value.strip()
                if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
                    raise ValueError()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                values[name] = value
        except (OSError, UnicodeError, ValueError):
            raise DatabaseError('Invalid environment file; use KEY=value assignments') from None
    values.update(os.environ if environ is None else environ)
    return values


@dataclass(frozen=True)
class DatabaseSettings:
    url: str = field(repr=False)
    schema: str = 'ontologics'

    def __post_init__(self):
        if not isinstance(self.url, str) or not self.url.startswith(('postgresql://', 'postgres://')):
            raise DatabaseError('Set DATABASE_URL to a PostgreSQL URI; file and in-memory databases are unsupported')
        if not isinstance(self.schema, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', self.schema) or self.schema in ('public', 'information_schema') or self.schema.startswith('pg_'):
            raise DatabaseError('Use a dedicated lowercase PostgreSQL schema')

    @classmethod
    def from_environment(cls, *, schema=None, env_file=None, environ=None):
        values = environment_values(env_file, environ)
        return cls(values.get('DATABASE_URL', ''), schema or values.get('ONTOLOGICS_DB_SCHEMA', 'ontologics'))


def in_transaction(db):
    return db.info.transaction_status != TransactionStatus.IDLE


def lock_store(db, schema):
    # Preserve the original single-writer semantics across processes/connections,
    # including read response/version consistency and successful request replay.
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_database() || ':' || %s, 0))", (schema,))


DDL = '''
CREATE TABLE IF NOT EXISTS snapshots(
 version INTEGER PRIMARY KEY, nodes JSONB NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts(
 id TEXT NOT NULL, version INTEGER NOT NULL, author TEXT NOT NULL, base_version INTEGER NOT NULL REFERENCES snapshots(version),
 operations JSONB NOT NULL, content_hash TEXT NOT NULL, status TEXT NOT NULL, reviewer TEXT, review_note TEXT,
 applied_version INTEGER REFERENCES snapshots(version), created_at TEXT NOT NULL, PRIMARY KEY(id,version));
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, tool TEXT NOT NULL, request_hash TEXT NOT NULL,
 status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, data_version INTEGER, error_code TEXT,
 sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE);
CREATE TABLE IF NOT EXISTS requests(
 actor TEXT NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 response JSONB NOT NULL, PRIMARY KEY(actor,request_key));
CREATE TABLE IF NOT EXISTS review_events(
 id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, draft_version INTEGER NOT NULL,
 content_hash TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
 sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE, FOREIGN KEY(draft_id,draft_version) REFERENCES drafts(id,version));
CREATE TABLE IF NOT EXISTS runtime_runs(
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, model TEXT NOT NULL,
 config TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, prompt_hash TEXT, output_hash TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS runtime_steps(
 id TEXT PRIMARY KEY, runtime_id TEXT NOT NULL REFERENCES runtime_runs(id),
 step INTEGER NOT NULL, kind TEXT NOT NULL, tool_name TEXT,
 attempt INTEGER NOT NULL, status TEXT NOT NULL, input_hash TEXT NOT NULL,
 output_hash TEXT, tool_run_ids TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, error TEXT, sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE);
CREATE TABLE IF NOT EXISTS evidence_vectors(
 data_version INTEGER NOT NULL REFERENCES snapshots(version), evidence_id TEXT NOT NULL,
 model TEXT NOT NULL, dimensions INTEGER NOT NULL CHECK(dimensions BETWEEN 1 AND 2000),
 content_hash TEXT NOT NULL, content TEXT NOT NULL, embedding public.vector NOT NULL,
 PRIMARY KEY(data_version,evidence_id,model), CHECK(public.vector_dims(embedding)=dimensions));
'''


def connect(settings):
    db = None
    try:
        db = psycopg.connect(settings.url, autocommit=True, row_factory=dict_row, connect_timeout=10)
        db.execute("SET statement_timeout = '30s'")
        db.execute("SET lock_timeout = '10s'")
        with db.transaction():
            # Serialize extension initialization independently of application schema.
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_database() || ':ontologics:setup', 0))")
            db.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
            db.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(settings.schema)))
            db.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(settings.schema)))
            lock_store(db, settings.schema)
            db.execute(DDL)
            db.execute("INSERT INTO snapshots VALUES(0, '[]', %s, 'empty') ON CONFLICT(version) DO NOTHING", (datetime.now(timezone.utc).isoformat(),))
        return db
    except psycopg.Error:
        if db is not None:
            db.close()
        raise DatabaseError('PostgreSQL setup failed; check DATABASE_URL, access, and pgvector installation') from None
