"""Phase 3 company TV publishing, account isolation and live follower tests."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import tenant_meta
import tenant_screens


class CompanyScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=self.tmp.name+"/screens.sqlite"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE staff(username TEXT PRIMARY KEY,role TEXT)")
            db.execute("INSERT INTO staff VALUES ('bob','marketing')")
        self.env=patch.dict(os.environ,{"META_TOKEN_ENCRYPTION_KEY":"X"*48,
            "PUBLIC_ORIGIN":"https://staging.example.test"})
        self.env.start()
        tenant_meta.init(self.db)
        tenant_screens.init(self.db)
        self.second=tenant_meta.create_organisation(self.db,"Second Venue","admin")["id"]

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_distinct_company_settings_and_publishing(self):
        arena=tenant_screens.get_config(self.db,"british-arena")
        second=tenant_screens.get_config(self.db,self.second)
        self.assertEqual(arena["version"],0)
        self.assertEqual(second["settings"]["title"],arena["settings"]["title"])
        a={**arena["settings"],"title":"First Venue","accent":"#012345",
           "slides":[{"title":"Sponsor","description":"Test","image":"","duration":10}]}
        tenant_screens.save_config(self.db,"british-arena",a)
        self.assertEqual(tenant_screens.get_config(self.db,"british-arena",True)["settings"]["title"],
                         "My Social Wall")
        tenant_screens.publish(self.db,"british-arena")
        self.assertEqual(tenant_screens.get_config(self.db,"british-arena",True)["settings"]["title"],
                         "First Venue")
        self.assertEqual(tenant_screens.get_config(self.db,self.second,True)["settings"]["title"],
                         "My Social Wall")

    def test_invalid_settings_not_saved(self):
        original=tenant_screens.get_config(self.db,self.second)
        for config in ({"title":"Oops","injected":"leak"}, {"slides":[{}]*21},
                       {"accent":"expression(alert(1))"},
                       {"rotation_seconds":True}):
            with self.assertRaises(ValueError):
                tenant_screens.save_config(self.db,self.second,config)
        self.assertEqual(tenant_screens.get_config(self.db,self.second)["version"],original["version"])

    def test_private_screen_key_rotation(self):
        a=tenant_screens.rotate_screen_key(self.db,"british-arena")
        b=tenant_screens.rotate_screen_key(self.db,self.second)
        self.assertEqual(tenant_screens.screen_org(self.db,a),"british-arena")
        self.assertEqual(tenant_screens.screen_org(self.db,b),self.second)
        self.assertIsNone(tenant_screens.screen_org(self.db,"bad"))
        self.assertNotIn(a,open(self.db,"rb").read().decode("latin-1"))
        newer=tenant_screens.rotate_screen_key(self.db,"british-arena")
        self.assertIsNone(tenant_screens.screen_org(self.db,a))
        self.assertEqual(tenant_screens.screen_org(self.db,newer),"british-arena")
        self.assertEqual(tenant_screens.screen_org(self.db,b),self.second)

    def test_follower_sync_scoped_by_organisation(self):
        tenant_meta.save_page(self.db,"british-arena","admin",{
            "id":"a-page","name":"First","access_token":"token-a","instagram_id":"ig-a"})
        tenant_meta.save_page(self.db,self.second,"admin",{
            "id":"b-page","name":"Second","access_token":"token-b"})
        seen=[]
        def fake_graph(path,fields,token):
            seen.append((path,token))
            return {"followers_count":{"a-page":120,"b-page":230,"ig-a":70}[path]}
        with patch.object(tenant_screens.meta_connect,"graph",side_effect=fake_graph):
            tenant_screens.poll_all(self.db)
        first=tenant_screens.connection_health(self.db,"british-arena")
        second=tenant_screens.connection_health(self.db,self.second)
        self.assertEqual(first["facebook"],120)
        self.assertEqual(first["instagram"],70)
        self.assertEqual(second["facebook"],230)
        self.assertIsNone(second["instagram"])
        self.assertIn(("a-page","token-a"),seen)
        self.assertIn(("b-page","token-b"),seen)
        self.assertNotIn("token-a",str(first))
        tenant_meta.disconnect(self.db,"british-arena","a-page")
        self.assertEqual(tenant_screens.connection_health(self.db,"british-arena")["status"],"not_connected")
        self.assertIsNone(tenant_screens.connection_health(self.db,"british-arena")["facebook"])

    def test_failed_sync_preserves_last_count_but_flags_reconnect(self):
        tenant_meta.save_page(self.db,self.second,"admin",{
            "id":"b-page","name":"Second","access_token":"token-b"})
        with patch.object(tenant_screens.meta_connect,"graph",return_value={"followers_count":44}):
            tenant_screens.sync_org(self.db,self.second)
        with patch.object(tenant_screens.meta_connect,"graph",side_effect=ValueError("bad token")):
            tenant_screens.sync_org(self.db,self.second)
        health=tenant_screens.connection_health(self.db,self.second)
        self.assertEqual(health["facebook"],44)
        self.assertEqual(health["status"],"needs_attention")

    def test_api_membership_and_screen_response(self):
        class Handler:
            def __init__(self,path,user=None,origin=True,body=None):
                self.path=path
                self.user=user
                self.origin=origin
                self.body=body
                self.result=None
            def current_user(self):return {"username":self.user} if self.user else None
            def same_origin(self):return self.origin
            def post_body(self,limit=0):return self.body
            def json_response(self,code,payload):self.result=(code,payload)
        route=tenant_screens.handler_route
        url="/api/organisations/"+self.second+"/screen/config"
        no_user=Handler(url)
        self.assertTrue(route(no_user,self.db,"GET"))
        self.assertEqual(no_user.result[0],401)
        unauthorized=Handler(url,"bob")
        self.assertTrue(route(unauthorized,self.db,"GET"))
        self.assertEqual(unauthorized.result[0],403)
        accepted=Handler(url,"admin")
        self.assertTrue(route(accepted,self.db,"GET"))
        self.assertEqual(accepted.result[0],200)
        post=Handler(url,"admin",origin=False,body=b'{"settings":{"title":"Evil"}}')
        self.assertTrue(route(post,self.db,"POST"))
        self.assertEqual(post.result[0],403)
        token=tenant_screens.rotate_screen_key(self.db,self.second)
        public=Handler("/api/screens/"+token)
        self.assertTrue(route(public,self.db,"GET"))
        self.assertEqual(public.result[0],200)
        self.assertNotIn("accounts",public.result[1]["social"])
        self.assertNotIn(self.second,str(public.result[1]))


if __name__=="__main__":
    unittest.main()
