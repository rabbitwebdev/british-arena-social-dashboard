"""Irreversible company deletion: ensure all tenant data is removed, not another's."""
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from PIL import Image

import tenant_delete
import tenant_meta
import tenant_screens
import tenant_media
import client_onboarding


class CompanyDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,"arena.sqlite3")
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE staff(username TEXT PRIMARY KEY,
                 password_hash TEXT NOT NULL,role TEXT NOT NULL,created_at INTEGER NOT NULL)""")
            db.execute("INSERT INTO staff VALUES ('client@example.com','hashed','marketing',0)")
        tenant_meta.init(self.path)
        tenant_screens.init(self.path)
        tenant_media.init(self.path)
        client_onboarding.init(self.path)
        self.a=tenant_meta.create_organisation(self.path,"British Arena","admin")["id"]
        self.b=tenant_meta.create_organisation(self.path,"British Arena","admin")["id"]
        self.screen_a=tenant_screens.rotate_screen_key(self.path,self.a)
        self.screen_b=tenant_screens.rotate_screen_key(self.path,self.b)
        for org in (self.a,self.b):
            tenant_meta.add_member(self.path,org,"client@example.com","viewer")
            invite=client_onboarding.make_invite(self.path,org,
                      ("first" if org==self.a else "second")+"@example.com",
                      "manager","admin")
            with sqlite3.connect(self.path) as db:
                db.execute("""INSERT INTO tenant_meta_connections
                  (organisation_id,page_id,page_name,encrypted_token,connected_by,connected_at)
                  VALUES (?,?,?,?,?,?)""",
                  (org,"page-"+org,"Demo Page","encrypted-placeholder","admin",1))
            raw=io.BytesIO()
            Image.new("RGB",(30,30),(15,45,90)).save(raw,format="PNG")
            saved=tenant_media.upload(self.path,org,raw.getvalue(),"image/png")
            self.image=saved["reference"] if org==self.a else None
            settings=tenant_screens.get_config(self.path,org)["settings"]
            tenant_screens.save_config(self.path,org,{**settings,"logo":saved["reference"]})
            tenant_screens.publish(self.path,org)

    def tearDown(self):self.tmp.cleanup()

    def confirm(self,org,name="British Arena"):
        return {"id":org,"name":name,"phrase":"DELETE "+name}

    def test_deletes_selected_duplicate_only(self):
        result=tenant_delete.delete_organisation(self.path,self.a,self.confirm(self.a))
        self.assertTrue(result["deleted"])
        self.assertTrue(result["media_cleaned"])
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT id FROM organisations WHERE id=?",
                                         (self.a,)).fetchone())
            for table,key in tenant_delete.CHILD_TABLES:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM "+table+" WHERE "+key+"=?",
                                           (self.a,)).fetchone()[0],0,table)
            self.assertIsNotNone(db.execute("SELECT id FROM organisations WHERE id=?",
                                              (self.b,)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM "+table+" WHERE "+key+"=?",
                                        (self.b,)).fetchone()[0],1)
            self.assertIsNotNone(db.execute("SELECT username FROM staff WHERE username=?",
                                              ("client@example.com",)).fetchone())
        self.assertFalse((tenant_media.media_root(self.path)/self.a).exists())
        self.assertTrue((tenant_media.media_root(self.path)/self.b).is_dir())
        self.assertIsNone(tenant_screens.screen_org(self.path,self.screen_a))
        self.assertEqual(tenant_screens.screen_org(self.path,self.screen_b),self.b)
        self.assertIsNone(tenant_meta.member_role(self.path,self.a,"client@example.com"))
        self.assertEqual(tenant_meta.member_role(self.path,self.b,"client@example.com"),"viewer")

    def test_immutable_original_company(self):
        with self.assertRaises(PermissionError):
            tenant_delete.delete_organisation(self.path,"british-arena",
                   self.confirm("british-arena"))
        with sqlite3.connect(self.path) as db:
            self.assertIsNotNone(db.execute("SELECT id FROM organisations WHERE id='british-arena'").fetchone())

    def test_rejects_wrong_name_id_or_phrase(self):
        invalid=(
            {"id":self.a,"name":"British Arena","phrase":"DELETE Wrong"},
            {"id":self.b,"name":"British Arena","phrase":"DELETE British Arena"},
            {"id":self.a,"name":"Wrong","phrase":"DELETE Wrong"},
            {"id":self.a,"name":"British Arena","phrase":"delete British Arena"},
        )
        for attempt in invalid:
            with self.subTest(attempt=attempt):
                with self.assertRaises(ValueError):
                    tenant_delete.delete_organisation(self.path,self.a,attempt)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM organisations WHERE id=?",
                                        (self.a,)).fetchone()[0],1)

    def test_route_requires_platform_admin_and_origin(self):
        class Handler:
            def __init__(self,org,user=None,is_admin=False,origin=True,body=None):
                import json
                self.path="/api/organisations/"+org+"/delete"
                self.user=user;self.admin=is_admin;self.origin=origin
                self.body=json.dumps(body if body is not None else {}).encode()
                self.status=None;self.response=None
            def current_user(self):return {"username":self.user} if self.user else None
            def is_admin(self):return self.admin
            def same_origin(self):return self.origin
            def post_body(self,limit):return self.body if len(self.body)<=limit else None
            def json_response(self,code,response):
                self.status=code;self.response=response
        for user,admin,origin,expected in [
            (None,False,True,401),
            ("client@example.com",False,True,403),
            ("admin",True,False,403),
        ]:
            with self.subTest(user=user,admin=admin,origin=origin):
                h=Handler(self.a,user,admin,origin,self.confirm(self.a))
                self.assertTrue(tenant_delete.route(h,self.path,"POST"))
                self.assertEqual(h.status,expected)
        protected=Handler("british-arena","admin",True,True,self.confirm("british-arena"))
        self.assertTrue(tenant_delete.route(protected,self.path,"POST"))
        self.assertEqual(protected.status,403)
        good=Handler(self.a,"admin",True,True,self.confirm(self.a))
        self.assertTrue(tenant_delete.route(good,self.path,"POST"))
        self.assertEqual(good.status,200)

if __name__=="__main__":
    unittest.main()
