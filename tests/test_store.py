import os
import tempfile
import unittest
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('meta_saas_store', Path(__file__).resolve().parents[1] / 'meta_saas' / 'store.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Store = module.Store

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name + '/meta.sqlite', 'a' * 48)
    def tearDown(self):
        self.temp.cleanup()
    def test_state_tenant_scoping_and_one_time_use(self):
        self.store.save_state('test-state', 'org1', 'user1')
        self.assertFalse(self.store.consume_state('test-state', 'org2', 'user1'))
        self.assertFalse(self.store.consume_state('test-state', 'org1', 'user1'))
    def test_encryption_and_tenant_scoping(self):
        self.store.upsert_page('org1','user1',{'id':'42','name':'Arena','access_token':'private-page-token'},'private-user-token',None)
        self.assertIsNone(self.store.page_token('org2','42'))
        self.assertEqual(self.store.page_token('org1','42'),'private-page-token')
        self.assertEqual(self.store.public_pages('org1')[0]['page_name'],'Arena')
        self.assertEqual(self.store.public_pages('org2'),[])
        with open(self.store.db_path,'rb') as f:
            self.assertNotIn(b'private-page-token', f.read())
        self.assertTrue(self.store.disconnect('org1','42'))
        self.assertIsNone(self.store.page_token('org1','42'))

if __name__ == '__main__': unittest.main()
