"""Isolation and security regression tests for Phase 1 tenant-aware Meta."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

import tenant_meta


class TenantMetaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.temp.name,"app.sqlite")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE staff(username TEXT PRIMARY KEY,role TEXT)")
            db.executemany("INSERT INTO staff VALUES (?,?)",
                           [("marketer","marketing"),("observer","viewer")])
        self.env=patch.dict(os.environ,{
            "META_APP_ID":"test-app-id","META_APP_SECRET":"fake-not-production",
            "META_TOKEN_ENCRYPTION_KEY":"a"*44,
            "PUBLIC_ORIGIN":"https://staging.example.test"
        })
        self.env.start()
        tenant_meta.init(self.path)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_existing_staff_scoped_to_original_organisation(self):
        assert tenant_meta.member_role(self.path,"british-arena","admin")=="owner"
        assert tenant_meta.member_role(self.path,"british-arena","marketer")=="manager"
        assert tenant_meta.member_role(self.path,"british-arena","observer")=="viewer"
        org=tenant_meta.create_organisation(self.path,"Another Company","admin")
        self.assertIsNone(tenant_meta.member_role(self.path,org["id"],"marketer"))
        self.assertEqual(tenant_meta.member_role(self.path,org["id"],"admin"),"owner")

    def test_meta_state_is_one_time_and_user_scoped(self):
        url=tenant_meta.authorize(self.path,"british-arena","admin")
        query=parse_qs(urlsplit(url).query)
        self.assertEqual(query["redirect_uri"],["https://staging.example.test/api/integrations/meta/callback"])
        state=query["state"][0]
        with self.assertRaises(ValueError):
            tenant_meta.consume_state(self.path,state,"another-user")
        with self.assertRaises(ValueError):
            tenant_meta.consume_state(self.path,state,"admin")
        url=tenant_meta.authorize(self.path,"british-arena","admin")
        state=parse_qs(urlsplit(url).query)["state"][0]
        self.assertEqual(tenant_meta.consume_state(self.path,state,"admin"),"british-arena")
        with self.assertRaises(ValueError):
            tenant_meta.consume_state(self.path,state,"admin")

    def test_pending_token_encrypted_and_organisation_scoped(self):
        org=tenant_meta.create_organisation(self.path,"Second Company","admin")["id"]
        pending=tenant_meta.store_pending(self.path,"british-arena","admin","private-user-token")
        with open(self.path,"rb") as stream:
            self.assertNotIn(b"private-user-token",stream.read())
        with self.assertRaises(ValueError):
            tenant_meta.consume_pending(self.path,pending,org,"admin")
        with self.assertRaises(ValueError):
            tenant_meta.consume_pending(self.path,pending,"british-arena","admin")
        pending=tenant_meta.store_pending(self.path,"british-arena","admin","private-user-token")
        self.assertEqual(tenant_meta.consume_pending(self.path,pending,"british-arena","admin"),"private-user-token")

    def test_pages_isolated_and_tokens_not_returned(self):
        org=tenant_meta.create_organisation(self.path,"Second Company","admin")["id"]
        tenant_meta.save_page(self.path,"british-arena","admin",{
            "id":"page1","name":"Arena","access_token":"private-page-token",
            "instagram_id":"ig1","instagram_username":"arena"})
        tenant_meta.save_page(self.path,org,"admin",{
            "id":"page1","name":"Second","access_token":"other-secret"})
        arena=tenant_meta.connections(self.path,"british-arena")
        second=tenant_meta.connections(self.path,org)
        self.assertEqual(arena[0]["page_name"],"Arena")
        self.assertEqual(second[0]["page_name"],"Second")
        self.assertNotIn("token",str(arena))
        with open(self.path,"rb") as stream:
            raw=stream.read()
            self.assertNotIn(b"private-page-token",raw)
            self.assertNotIn(b"other-secret",raw)
        self.assertTrue(tenant_meta.disconnect(self.path,org,"page1"))
        self.assertEqual(tenant_meta.connections(self.path,org),[])
        self.assertEqual(len(tenant_meta.connections(self.path,"british-arena")),1)

    def test_role_validation(self):
        org=tenant_meta.create_organisation(self.path,"Second Company","admin")["id"]
        with self.assertRaises(ValueError):
            tenant_meta.add_member(self.path,org,"missing-user","manager")
        with self.assertRaises(ValueError):
            tenant_meta.add_member(self.path,org,"marketer","superadmin")
        tenant_meta.add_member(self.path,org,"marketer","manager")
        self.assertEqual(tenant_meta.member_role(self.path,org,"marketer"),"manager")


if __name__=="__main__":
    unittest.main()
