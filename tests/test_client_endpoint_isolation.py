"""Authenticated clients cannot reach the original Arena live APIs."""
import os,sqlite3,tempfile,unittest
from unittest.mock import patch
import server,tenant_meta,tenant_screens,client_onboarding

class LegacyAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,"test.sqlite")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE staff(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL,created_at INTEGER NOT NULL)")
            db.execute("INSERT INTO staff VALUES ('customer@example.com','hash','marketing',0)")
        tenant_meta.init(self.path)
        tenant_screens.init(self.path)
        client_onboarding.init(self.path)
        self.org=tenant_meta.create_organisation(self.path,"External Company","admin")["id"]
        tenant_meta.add_member(self.path,self.org,"customer@example.com","owner")
        self.patch=patch.object(server,"DB_PATH",self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def fake(self,path):
        h=object.__new__(server.Handler)
        h.path=path
        h.current_user=lambda:{"username":"customer@example.com","role":"marketing"}
        h.admin_ok=lambda:False
        h.display_ok=lambda:False
        h.is_admin=lambda:False
        h.same_origin=lambda:True
        h.last=None
        h.json_response=lambda code,payload:setattr(h,"last",(code,payload))
        h.send_error=lambda status:setattr(h,"last",(status,None))
        h.send_response=lambda status:setattr(h,"last",(status,None))
        h.send_header=lambda *args:None
        h.end_headers=lambda:None
        return h

    def test_other_company_cannot_read_arena_counts(self):
        for endpoint in ("/api/counts","/api/newsletter-events?after=0","/api/config"):
            with self.subTest(endpoint=endpoint):
                h=self.fake(endpoint)
                server.Handler.do_GET(h)
                self.assertEqual(h.last[0],403)

    def test_cannot_open_arena_editor(self):
        h=self.fake("/admin")
        server.Handler.do_GET(h)
        self.assertEqual(h.last[0],302)

    def test_cannot_open_another_company_editor(self):
        h=self.fake("/company/british-arena")
        server.Handler.do_GET(h)
        self.assertEqual(h.last[0],403)

if __name__=="__main__":
    unittest.main()
