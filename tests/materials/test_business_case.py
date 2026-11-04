"""Business fixture keeps the registered product and preserves user work."""
import unittest

from ontologics.materials.business_case import install
from ontologics.database import DatabaseSettings
from ontologics.graph_views import GraphSettings
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.materials.workspace import MaterialsWorkspace
from ontologics.web import USERS
from tests.db import database_for_test


class BusinessCaseTests(unittest.TestCase):
    def ok(self, response):
        self.assertTrue(response['ok'], response)
        return response['data']

    def test_install_read_calculate_and_repeat_preserve_records(self):
        database = database_for_test(self)
        # A test schema is explicitly admitted only by this internal test seam.
        report = install(database, GraphSettings(), schema_prefix='test_')
        host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), database)
        ctx = DevelopmentContext('dev-alice')
        app = MaterialsWorkspace(host, ctx, request_host=host)
        contract = self.ok(app.library.get(ctx, report['original_id']))
        self.assertEqual('한빛시설관리 주식회사', contract['counterparty'])
        self.assertIn('180', contract['text'])
        self.assertEqual('not_started', contract['interpretation_status'])
        inbox = self.ok(app.requests.inbox(ctx))
        self.assertEqual(1, len(inbox['requests']))
        request = inbox['requests'][0]
        self.assertEqual(60, request['request']['quantity'])
        self.assertEqual([], request['issues'])
        baseline = self.ok(app.combined.review(ctx, 'config:esp32-poe:L:WROOM', 180, '10', 'baseline'))
        extra = self.ok(app.requests.review(ctx, request['evidence_id'], inbox['data_version'], '10', 'extra'))
        self.assertEqual(180, baseline['payload']['quantity'])
        self.assertEqual(60, extra['payload']['bom']['planned_input_quantity'])
        self.assertTrue(baseline['payload']['rfq']['excluded'])
        self.assertEqual({'bom', 'process'}, {line['kind'] for line in baseline['payload']['rfq']['lines']})
        again = install(database, GraphSettings(), schema_prefix='test_')
        self.assertEqual(report['original_id'], again['original_id'])
        self.assertEqual(report['data_version'], again['data_version'])
        repeated = self.ok(app.requests.inbox(ctx))
        self.assertEqual(extra['id'], repeated['requests'][0]['last_review']['id'])
        with host.request(ctx) as session:
            svc = session._service
            self.assertEqual(2, svc.db.execute('SELECT count(*) AS n FROM pp_company_stock_snapshots').fetchone()['n'])
            self.assertEqual(1, svc.db.execute('SELECT count(*) AS n FROM pp_process_material_stock').fetchone()['n'])
        changed = app.process_materials.latest
        with host.request(ctx) as session:
            payload = changed(session._service)['payload']
        payload['lots'][0]['on_hand'] = '600'
        self.ok(app.process_materials.store(ctx, payload, 'user-stock-change'))
        with self.assertRaisesRegex(ValueError, '재고'):
            install(database, GraphSettings(), schema_prefix='test_')

    def test_existing_workspace_schema_is_rejected_before_connection(self):
        database = DatabaseSettings('postgresql://unused@localhost/unused', 'a_contract_library')
        with self.assertRaisesRegex(ValueError, '전용'):
            install(database, GraphSettings())
