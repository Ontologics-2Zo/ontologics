"""Every integration test owns a disposable schema, never an existing database."""
from contextlib import contextmanager
import re
import uuid
import psycopg
from psycopg import sql
from ontologics.database import DatabaseSettings, environment_values

@contextmanager
def isolated_database():
    values = environment_values()
    settings = DatabaseSettings(values.get('TEST_DATABASE_URL') or values.get('DATABASE_URL', ''),
                                'test_' + uuid.uuid4().hex)
    try:
        yield settings
    finally:
        assert re.fullmatch(r'test_[a-f0-9]{32}', settings.schema)
        with psycopg.connect(settings.url, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(settings.schema)))

def database_for_test(test):
    scope = isolated_database()
    settings = scope.__enter__()
    test.addCleanup(scope.__exit__, None, None, None)
    return settings
