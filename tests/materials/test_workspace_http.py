"""Shared main HTTP entry exercises A-only adapter without model calls."""
from tests.test_web import WebCase


class MaterialsWorkspaceHTTPTests(WebCase):
    def test_product_review_and_request_read_permissions_through_main_routes(self):
        path = '/api/planning/'
        inventory = self.ok(self.http(self.alice, path+'product-inventory', {'quantity':7}))['data']
        cid = inventory['products'][0]['configuration_id']
        preview = self.ok(self.http(self.alice, path+'process-requirement-preview', {
            'configuration_id':cid,'quantity':7,'allowance_percent':'10'}))['data']
        self.assertTrue(preview['lines'])
        body = {'configuration_id':cid,'quantity':7,'allowance_percent':'10','request_key':'combined-http'}
        self.failed(self.http(self.reader, path+'combined-review', body),403,'FORBIDDEN')
        saved = self.ok(self.http(self.alice, path+'combined-review', body))['data']
        self.assertEqual(7,saved['payload']['quantity'])
        self.assertFalse(saved['payload']['rfq']['sent'])
        self.assertTrue(saved['payload']['rfq']['excluded'])
        self.assertEqual(saved['id'],self.ok(self.http(self.alice,path+'combined-review',body))['data']['id'])
        inbox = self.ok(self.http(self.alice,path+'request-inbox',{}))['data']
        self.assertEqual([],inbox['requests'])
        self.failed(self.http(self.alice,path+'combined-review',dict(body,quantity=-1,request_key='negative')),400,'INVALID_INPUT')
        self.assertEqual(1,self.ok(self.http(self.alice,'/api/context'))['data_version'])
