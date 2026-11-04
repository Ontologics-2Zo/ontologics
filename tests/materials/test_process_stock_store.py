"""Explicit process stock observations use the authenticated A journal."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.materials.library_process_materials import LibraryProcessMaterials
from ontologics.web import USERS
from tests.db import database_for_test


CID = 'config:esp32-poe:L:WROOM'
OTHER_CID = 'config:esp32-poe:L1:WROOM'
MID = 'process-material:kester:951'


class ProcessStockStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = database_for_test(self)
        self.context = DevelopmentContext('dev-alice')
        self.host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True), self.settings)
        self.stock = LibraryProcessMaterials(self.host, self.context)
        observed = datetime.now(timezone.utc) - timedelta(minutes=2)
        self.payload = dict(
            origin='synthetic', version=1, company='주식회사 온톨로직스',
            location='성수 공정자재 창고', as_of=observed.isoformat(),
            basis='회사 업무 검증용 가상 재고 관측이며 실제 실사 기록이 아닙니다.',
            lots=[dict(id='ONT-PROC-951-001', material_id=MID, unit='g',
                       on_hand='72.25', reserved='12', held='0.25',
                       expires_at=(observed + timedelta(days=365)).isoformat(),
                       expiry_basis='업무 검증을 위한 가상 사용기한',
                       deductions_disjoint=True)])

    def ok(self, result):
        self.assertTrue(result['ok'], result)
        return result['data']

    def rows(self):
        with self.host.request(self.context) as session:
            return session._service.db.execute(
                'SELECT * FROM pp_process_material_stock ORDER BY created_at,id').fetchall()

    def test_custom_company_and_lot_visible_in_both_configurations(self):
        original = deepcopy(self.payload)
        saved = self.ok(self.stock.store(self.context, self.payload, 'company-stock'))
        self.assertEqual(original, self.payload)
        for cid in (CID, OTHER_CID):
            data = self.ok(self.stock.inspect(self.context, cid, 180))
            self.assertEqual(saved['id'], data['stock_snapshot_id'])
            self.assertEqual('주식회사 온톨로직스', data['stock_scope']['company'])
            self.assertEqual('성수 공정자재 창고', data['stock_scope']['location'])
            flux = next(row for row in data['materials'] if row['id'] == MID)
            self.assertEqual('60', flux['stock']['available'])
            self.assertEqual('ONT-PROC-951-001', flux['stock']['lots'][0]['id'])
            self.assertTrue(all(row['stock']['available'] is None
                                for row in data['materials'] if row['id'] != MID))
        with self.host.request(self.context) as session:
            actor = session.context['actor']
            writes = session._service.db.execute(
                "SELECT actor,status FROM runs WHERE tool='process_material_stock_store'").fetchall()
        self.assertEqual([dict(actor=actor, status='succeeded')], writes)

    def test_append_keeps_previous_observation_and_seed_preserves_latest(self):
        first = self.ok(self.stock.store(self.context, self.payload, 'first'))
        previous = self.rows()[0]
        changed = deepcopy(self.payload)
        changed['as_of'] = (datetime.fromisoformat(changed['as_of']) + timedelta(minutes=1)).isoformat()
        changed['lots'][0]['on_hand'] = '92.25'
        second = self.ok(self.stock.store(self.context, changed, 'second'))
        self.assertNotEqual(first['id'], second['id'])
        rows = self.rows()
        self.assertEqual(2, len(rows))
        self.assertEqual(previous, next(row for row in rows if row['id'] == first['id']))
        current = self.ok(self.stock.inspect(self.context, CID, 180))
        self.assertEqual(second['id'], current['stock_snapshot_id'])
        self.assertEqual('80', next(row for row in current['materials'] if row['id'] == MID)['stock']['available'])
        preserved = self.ok(self.stock.seed_scenario(self.context, 'preserve-explicit-stock'))
        self.assertTrue(preserved['preserved'])
        self.assertEqual(second['id'], preserved['id'])
        self.assertEqual(rows, self.rows())

    def test_invalid_lot_does_not_partially_store_valid_lots(self):
        self.ok(self.stock.store(self.context, self.payload, 'existing'))
        before = self.rows()
        for number, change in enumerate((dict(reserved='73'), dict(on_hand='-1'), dict(unit='mL'))):
            with self.subTest(change=change):
                invalid = deepcopy(self.payload)
                invalid['lots'].append(dict(invalid['lots'][0], id='ONT-PROC-951-002', **change))
                result = self.stock.store(self.context, invalid, 'invalid-' + str(number))
                self.assertFalse(result['ok'], result)
                self.assertEqual('INVALID_INPUT', result['errors'][0]['code'])
                self.assertEqual(before, self.rows())

    def test_malformed_scope_and_future_observations_are_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        cases = [None, [], dict(self.payload, company=[]), dict(self.payload, basis=''),
                 dict(self.payload, as_of=future), dict(self.payload, as_of=None)]
        bad_expiry = deepcopy(self.payload)
        bad_expiry['lots'][0]['expires_at'] = 42
        cases.append(bad_expiry)
        for number, payload in enumerate(cases):
            with self.subTest(number=number):
                result = self.stock.store(self.context, payload, 'malformed-' + str(number))
                self.assertFalse(result['ok'], result)
                self.assertEqual('INVALID_INPUT', result['errors'][0]['code'])
        self.assertEqual([], self.rows())

    def test_reader_cannot_store(self):
        reader = DevelopmentContext('reader')
        host = CommonHost(DevelopmentIdentityProvider(
            [UserIdentity('reader', frozenset({'read'}))], enabled=True), self.settings)
        stock = LibraryProcessMaterials(host, reader)
        denied = stock.store(reader, self.payload, 'reader-stock')
        self.assertFalse(denied['ok'], denied)
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        self.assertEqual([], self.rows())

    def test_content_and_request_key_retries_do_not_duplicate_observations(self):
        first = self.ok(self.stock.store(self.context, self.payload, 'stock-request'))
        before = self.rows()
        replay = self.ok(self.stock.store(self.context, deepcopy(self.payload), 'stock-request'))
        same_content = self.ok(self.stock.store(self.context, deepcopy(self.payload), 'new-request'))
        self.assertEqual(first['id'], replay['id'])
        self.assertEqual(first['id'], same_content['id'])
        changed = deepcopy(self.payload)
        changed['lots'][0]['on_hand'] = '90'
        conflict = self.stock.store(self.context, changed, 'stock-request')
        self.assertFalse(conflict['ok'], conflict)
        self.assertEqual('IDEMPOTENCY_CONFLICT', conflict['errors'][0]['code'])
        self.assertEqual(before, self.rows())
