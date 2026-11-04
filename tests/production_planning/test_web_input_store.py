import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Explicit isolated PostgreSQL required')
class WebInputStoreTests(unittest.TestCase):
    def setUp(self):
        from tests.db import isolated_database
        from examples.production_planning_ui import make_application, SCENARIOS
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        settings={name:self.stack.enter_context(isolated_database()) for name in SCENARIOS}
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.app=make_application(settings,Path(folder),direct_fixture=True)
        self.scenario=self.app.scenarios['standard']

    def test_defaults_are_read_again_from_postgresql(self):
        self.assertNotIn('defaults',self.scenario)
        self.assertNotIn('request',self.scenario)
        first=self.app.planning('defaults',{'scenario':'standard'})
        self.assertTrue(first['ok'],first)
        self.assertEqual(first['data']['storage'],'postgresql')
        with self.scenario['host'].request(self.app.context) as session:
            session._service.db.execute("UPDATE pp_web_inputs SET payload=jsonb_set(payload,'{defaults,notice}',to_jsonb(%s::text)) WHERE id=1",('fresh database value',))
        second=self.app.planning('defaults',{'scenario':'standard'})
        self.assertEqual(second['data']['notice'],'fresh database value')

    def test_missing_database_row_does_not_fall_back_to_files(self):
        with self.scenario['host'].request(self.app.context) as session:
            session._service.db.execute('DELETE FROM pp_web_inputs WHERE id=1')
        response=self.scenario['inputs'].get(self.app.context)
        self.assertFalse(response['ok'])
        self.assertEqual(response['errors'][0]['code'],'INPUTS_NOT_FOUND')

    def test_explicit_rdf_backend_reaches_each_temporary_ui_host(self):
        from ontologics.graph_views import GraphSettings
        from tests.db import isolated_database
        from examples.production_planning_ui import make_application, SCENARIOS
        settings={name:self.stack.enter_context(isolated_database()) for name in SCENARIOS}
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        selected=GraphSettings(backend='rdf')
        app=make_application(settings,Path(folder),graph_settings=selected)
        for scenario in app.scenarios.values():
            self.assertIs(selected,scenario['host']._graph_settings)
            with scenario['host'].request(app.context) as session:
                result=session.call('lookup',{'kind':'ContractItem','version':1})
                self.assertTrue(result['ok'],result)
                self.assertEqual(1,len(result['data']))
