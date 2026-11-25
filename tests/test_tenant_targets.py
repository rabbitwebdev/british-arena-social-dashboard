"""Test one-time company social milestones, real count crossings and no replays."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import tenant_meta
import tenant_screens
import tenant_targets


class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=self.tmp.name+"/test.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE staff(username TEXT PRIMARY KEY,role TEXT)")
        self.env=patch.dict(os.environ,{"META_TOKEN_ENCRYPTION_KEY":"X"*48,
                                     "PUBLIC_ORIGIN":"https://staging.test"})
        self.env.start()
        tenant_meta.init(self.db)
        tenant_screens.init(self.db)
        self.org=tenant_meta.create_organisation(self.db,"Test Venue","admin")["id"]
        self.other=tenant_meta.create_organisation(self.db,"Other Venue","admin")["id"]

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def update(self,org=None,metric="facebook",goal=1233,message="Target reached, drinks are on us",animation="confetti"):
        org=org or self.org
        s=tenant_screens.get_config(self.db,org)["settings"]
        s["targets"]={metric:{"enabled":True,"goal":goal,"message":message,"animation":animation}}
        tenant_screens.save_config(self.db,org,s)
        tenant_screens.publish(self.db,org)

    def poll(self,org=None,fb=1232,likes=35,ig=120):
        tenant_targets.after_sync(self.db,org or self.org,
             {"facebook":fb,"facebook_likes":likes,"instagram":ig})

    def events(self,org=None):
        org=org or self.org
        return tenant_targets.events(self.db,org,
              tenant_screens.get_config(self.db,org,True)["settings"])

    def test_facebook_goal_crosses_once_and_custom_message_persists(self):
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO company_social_counts(org_id,facebook_count,
                instagram_count,facebook_likes,status) VALUES (?,?,?,?,?)""",
                (self.org,1232,10,11,"connected"))
        self.update()
        self.assertEqual(self.events()["facebook"]["state"],"armed")
        self.poll(fb=1232)
        self.assertIsNone(self.events()["facebook"]["event_id"])
        self.poll(fb=1233)
        first=self.events()["facebook"]
        self.assertEqual(first["state"],"reached")
        self.assertEqual(first["message"],"Target reached, drinks are on us")
        self.assertEqual(first["animation"],"confetti")
        self.assertTrue(first["event_id"])
        self.poll(fb=1234)
        self.poll(fb=1230)
        self.poll(fb=1233)
        self.assertEqual(self.events()["facebook"]["event_id"],first["event_id"])

    def test_new_higher_target_rearms_previous_one(self):
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO company_social_counts(org_id,facebook_count,
                instagram_count,facebook_likes,status) VALUES (?,?,?,?,?)""",
                (self.org,1232,10,11,"connected"))
        self.update()
        self.poll(fb=1233)
        first=self.events()["facebook"]["event_id"]
        self.update(goal=1250,message="1250 followers!")
        self.assertIsNone(self.events()["facebook"]["event_id"])
        self.assertEqual(self.events()["facebook"]["state"],"armed")
        self.poll(fb=1250)
        later=self.events()["facebook"]
        self.assertNotEqual(later["event_id"],first)
        self.assertEqual(later["message"],"1250 followers!")
        self.assertEqual(later["state"],"reached")

    def test_already_reached_when_published_does_not_fabricate_event(self):
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO company_social_counts(org_id,facebook_count,
                instagram_count,facebook_likes,status) VALUES (?,?,?,?,?)""",
                (self.org,1235,10,11,"connected"))
        self.update(goal=1233)
        self.assertEqual(self.events()["facebook"]["state"],"reached")
        self.assertIsNone(self.events()["facebook"]["event_id"])
        self.poll(fb=1238)
        self.assertIsNone(self.events()["facebook"]["event_id"])

    def test_unknown_baseline_does_not_fabricate_event(self):
        self.update(goal=5)
        self.assertEqual(self.events()["facebook"]["state"],"waiting")
        self.poll(fb=10)
        self.assertEqual(self.events()["facebook"]["state"],"reached")
        self.assertIsNone(self.events()["facebook"]["event_id"])

    def test_facebook_instagram_and_likes_independent(self):
        settings=tenant_screens.get_config(self.db,self.org)["settings"]
        settings["targets"]={
            "facebook":{"enabled":True,"goal":1233,"message":"Facebook","animation":"football"},
            "instagram":{"enabled":True,"goal":300,"message":"Instagram","animation":"celebrate"},
            "facebook_likes":{"enabled":True,"goal":100,"message":"Likes","animation":"fireworks"}}
        tenant_screens.save_config(self.db,self.org,settings)
        tenant_screens.publish(self.db,self.org)
        self.poll(fb=1232,ig=299,likes=99)
        self.poll(fb=1233,ig=299,likes=99)
        self.assertIsNotNone(self.events()["facebook"]["event_id"])
        self.assertIsNone(self.events()["instagram"]["event_id"])
        self.poll(fb=1234,ig=300,likes=100)
        events=self.events()
        self.assertEqual({k:bool(v["event_id"]) for k,v in events.items()},
                         {"facebook":True,"instagram":True,"facebook_likes":True})
        self.assertEqual(events["facebook_likes"]["animation"],"fireworks")

    def test_tenant_separation_and_disable_removes_events(self):
        self.update()
        self.poll(fb=1200)
        self.poll(fb=1233)
        self.assertEqual(self.events(self.other),{})
        settings=tenant_screens.get_config(self.db,self.org)["settings"]
        settings["targets"]={}
        tenant_screens.save_config(self.db,self.org,settings)
        tenant_screens.publish(self.db,self.org)
        self.assertEqual(self.events(),{})
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM company_target_states WHERE org_id=?",
                                        (self.org,)).fetchone()[0],0)

    def test_same_goal_republish_does_not_replay(self):
        self.update()
        self.poll(fb=100)
        self.poll(fb=1233)
        first=self.events()["facebook"]["event_id"]
        self.update(message="New wording, same goal")
        self.assertEqual(self.events()["facebook"]["event_id"],first)
        self.poll(fb=1235)
        self.assertEqual(self.events()["facebook"]["event_id"],first)
        self.assertEqual(self.events()["facebook"]["message"],"New wording, same goal")

    def test_invalid_target_rejected_without_mutating_config(self):
        base=tenant_screens.get_config(self.db,self.org)["settings"]
        bad=[
            {"facebook":{"enabled":True,"goal":0,"message":"Hello","animation":"confetti"}},
            {"facebook":{"enabled":True,"goal":True,"message":"Hello","animation":"confetti"}},
            {"facebook":{"enabled":True,"goal":1233,"message":"","animation":"confetti"}},
            {"facebook":{"enabled":True,"goal":1233,"message":"A"*241,"animation":"confetti"}},
            {"facebook":{"enabled":True,"goal":1233,"message":"Hello","animation":"javascript"}},
            {"foreign":{"enabled":True,"goal":1233,"message":"Hello","animation":"confetti"}}
        ]
        for candidate in bad:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    tenant_screens.save_config(self.db,self.org,{**base,"targets":candidate})
        self.assertEqual(tenant_screens.get_config(self.db,self.org)["settings"]["targets"],{})


if __name__=="__main__":
    unittest.main()
