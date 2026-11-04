import unittest
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentIdentityProvider
from ontologics.web import USERS


class WorkspaceSourceTests(unittest.TestCase):
    def test_source_settings_keep_default_host_and_reject_partial_configuration(self):
        from ontologics.materials.workspace_source import configured_request_host
        host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True))
        self.assertIs(configured_request_host(host, environ={}), host)
        with self.assertRaises(ValueError):
            configured_request_host(host, environ={'MAT_REQUEST_GRAPH_NAMESPACE': 'other'})

    def test_source_uses_same_identity_provider_and_no_independent_cost_policy(self):
        from ontologics.materials.workspace_source import configured_request_host
        from tempfile import TemporaryDirectory
        from pathlib import Path
        host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True))
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'db.env'
            path.write_text('DATABASE_URL=postgresql://test:test@127.0.0.1:5432/source\n')
            source = configured_request_host(host, environ={
                'MAT_REQUEST_SCHEMA': 'source_requests', 'MAT_REQUEST_DATABASE_ENV': str(path)})
        self.assertIs(source._identities, host._identities)
        self.assertIsNone(source._cost_policy)
        self.assertEqual(source._database.schema, 'source_requests')
        self.assertIn('/source', source._database.url)

    def test_namespace_requires_neo4j_and_explicit_source_settings(self):
        from ontologics.materials.workspace_source import configured_request_host
        host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True))
        with self.assertRaises(ValueError):
            configured_request_host(host, environ={'MAT_REQUEST_SCHEMA':'requests',
                                                  'MAT_REQUEST_GRAPH_NAMESPACE':'requests'})
