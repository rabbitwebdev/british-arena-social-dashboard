"""Verify milestone previews require company membership and never update event state."""
import os,sqlite3,tempfile,unittest
from unittest.mock import patch
import server,tenant_meta,tenant_screens,tenant_targets

class MilestonePreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,"preview.sqlite")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE staff(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at INTEGER NOT NULL)")
        tenant_meta.init(self.path)
        tenant_screens.init(self.path)
        self.org=tenant_meta.create_organisation(self.path,"First Company","admin")["id"]
        self.other=tenant_meta.create_organisation(self.path,"Other Company","admin")["id"]
        self.dbpatch=patch.object(server,"DB_PATH",self.path);self.dbpatch.start()
    def tearDown(self):
        self.dbpatch.stop()
        self.tmp.cleanup()
    def handler(self,org,user):
        h=object.__new__(server.Handler)
        h.path="/company/"+org+"/preview"
        h.current_user=lambda:{"username":user} if user else None
        h.status=None
        h.send_error=lambda status:setattr(h,"status",status)
        h.no_cache_html=lambda html,**kwargs:setattr(h,"status",200)
        return h
    def test_anonymous_cannot_preview(self):
        h=self.handler(self.org,None)
        server.Handler.do_GET(h)
        self.assertEqual(h.status,403)
    def test_other_company_cannot_preview(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO staff VALUES ('client@example.com','hash','marketing',0)")
        tenant_meta.add_member(self.path,self.org,"client@example.com","owner")
        h=self.handler(self.other,"client@example.com")
        server.Handler.do_GET(h)
        self.assertEqual(h.status,403)
    def test_member_preview_does_not_mutate_milestone_state(self):
        config=tenant_screens.get_config(self.path,self.org)["settings"]
        config["targets"]={"facebook":{"enabled":True,"goal":1233,
                  "message":"Drinks on us","animation":"confetti"}}
        tenant_screens.save_config(self.path,self.org,config)
        tenant_screens.publish(self.path,self.org)
        before=tenant_targets.events(self.path,self.org,config)
        h=self.handler(self.org,"admin")
        server.Handler.do_GET(h)
        self.assertEqual(h.status,200)
        after=tenant_targets.events(self.path,self.org,config)
        self.assertEqual(before,after)
        self.assertIsNone(after["facebook"]["event_id"])
    def test_countdown_preview_is_client_side_and_requires_private_company_page(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        editor=(root/"company-dashboard.html").read_text()
        screen=(root/"company-screen.html").read_text()
        self.assertIn("Preview countdown",editor)
        self.assertIn("social-countdown-preview",editor)
        self.assertIn("social-countdown-preview",screen)
        self.assertIn("target-countdowns",screen)
        self.assertIn('target.countdown_enabled!==true',screen)
        self.assertIn('target.state==="reached"',screen)
        self.assertIn('social.status!=="connected"',screen)
        self.assertIn("Math.max(0,target.goal-count)",screen)
        self.assertIn('previewFrame.src="/company/"+encodeURIComponent(org)+"/preview"',editor)

    def test_celebration_preview_uses_uploaded_image_and_duration_without_mutation(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        editor=(root/"company-dashboard.html").read_text()
        screen=(root/"company-screen.html").read_text()
        self.assertIn("Upload 16:9 celebration image for",editor)
        self.assertIn("Target reached display duration",editor)
        self.assertIn("celebration_image:imageRef",editor)
        self.assertIn("display_duration:Number(duration.value)",editor)
        self.assertIn("imageSource(item.celebration_image",screen)
        self.assertIn('id="milestone-image"',screen)
        self.assertIn("seconds*1000",screen)
        self.assertIn("with-image",screen)
        self.assertIn('previewFrame.src="/company/"+encodeURIComponent(org)+"/preview"',editor)

    def test_editor_contains_preview_and_replay_ui(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        editor=(root/"company-dashboard.html").read_text()
        screen=(root/"company-screen.html").read_text()
        self.assertIn("Preview celebration",editor)
        self.assertIn('id="replay-preview"',editor)
        self.assertIn("social-milestone-preview-ready",screen)
        self.assertIn("if(!previewMode)checkMilestones",screen)
        self.assertIn("if(!previewMode&&initialized",screen)

if __name__=="__main__":
    unittest.main()
