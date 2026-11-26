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

    def test_countdown_settings_persist_and_do_not_change_goal_state(self):
        settings=tenant_screens.get_config(self.db,self.org)["settings"]
        settings["targets"]={"facebook":{
            "enabled":True,"goal":1300,
            "message":"Drinks on us!","animation":"confetti",
            "countdown_enabled":True,
            "countdown_message":"Only {remaining} followers and you get happy hour!"}}
        tenant_screens.save_config(self.db,self.org,settings)
        tenant_screens.publish(self.db,self.org)
        self.poll(fb=1278)
        target=self.events()["facebook"]
        self.assertEqual(target["goal"],1300)
        self.assertTrue(target["countdown_enabled"])
        self.assertEqual(target["countdown_message"],"Only {remaining} followers and you get happy hour!")
        self.assertEqual(target["countdown_message"].replace("{remaining}",str(target["goal"]-1278)),
                         "Only 22 followers and you get happy hour!")
        self.assertIsNone(target["event_id"])
        self.poll(fb=1300)
        reached=self.events()["facebook"]
        self.assertEqual(reached["state"],"reached")
        self.assertIsNotNone(reached["event_id"])
        self.poll(fb=1302)
        self.assertEqual(self.events()["facebook"]["event_id"],reached["event_id"])

    def test_countdowns_are_independent_and_optional(self):
        settings=tenant_screens.get_config(self.db,self.org)["settings"]
        settings["targets"]={
            "facebook":{"enabled":True,"goal":1300,"message":"FB","animation":"confetti",
                        "countdown_enabled":True,"countdown_message":"{remaining} to go!"},
            "instagram":{"enabled":True,"goal":650,"message":"IG","animation":"football",
                         "countdown_enabled":False,"countdown_message":"{remaining} more follows"},
            "facebook_likes":{"enabled":True,"goal":1000,"message":"Likes","animation":"fireworks",
                             "countdown_enabled":True,"countdown_message":"Just {remaining} likes to go!"}
        }
        tenant_screens.save_config(self.db,self.org,settings)
        tenant_screens.publish(self.db,self.org)
        self.poll(fb=1280,ig=600,likes=999)
        goals=self.events()
        self.assertTrue(goals["facebook"]["countdown_enabled"])
        self.assertFalse(goals["instagram"]["countdown_enabled"])
        self.assertTrue(goals["facebook_likes"]["countdown_enabled"])
        self.assertEqual(tenant_targets.events(self.db,self.other,
                          tenant_screens.get_config(self.db,self.other,True)["settings"]),{})

    def test_countdown_template_validation(self):
        base={"enabled":True,"goal":1300,"message":"Reached","animation":"confetti",
              "countdown_enabled":True}
        bad=[
            {**base,"countdown_message":"Only 22 followers"},
            {**base,"countdown_message":"{remaining} and {remaining} again"},
            {**base,"countdown_message":""},
            {**base,"countdown_message":"x"*241},
            {**base,"countdown_enabled":"yes","countdown_message":"{remaining}"},
            {**base,"enabled":False,"countdown_message":"{remaining}"}
        ]
        for target in bad:
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    tenant_targets.validate({"facebook":target})
        compatible=tenant_targets.validate({"facebook":{"enabled":True,"goal":1300,
                 "message":"Reached","animation":"confetti"}})
        self.assertFalse(compatible["facebook"]["countdown_enabled"])

    def test_celebration_duration_bounds_and_old_defaults(self):
        base={"enabled":True,"goal":1300,"message":"Happy hour",
              "animation":"confetti"}
        compatible=tenant_targets.validate({"facebook":base})["facebook"]
        self.assertEqual(compatible["display_duration"],8)
        self.assertEqual(compatible["celebration_image"],"")
        for seconds in (5,8,60,3599,3600):
            with self.subTest(seconds=seconds):
                chosen=tenant_targets.validate({"instagram":{
                    **base,"display_duration":seconds}})["instagram"]
                self.assertEqual(chosen["display_duration"],seconds)
        for invalid in (0,4,3601,True,1.5,"3600",None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    tenant_targets.validate({"facebook":{**base,"display_duration":invalid}})
        for invalid in (None,"https://example.test/image.jpg","media:fake",123):
            with self.subTest(image=invalid):
                with self.assertRaises(ValueError):
                    tenant_targets.validate({"facebook":{**base,"celebration_image":invalid}})

    def test_image_and_duration_are_published_with_target_without_rearming(self):
        base=tenant_screens.get_config(self.db,self.org)["settings"]
        base["targets"]={"facebook":{"enabled":True,"goal":1500,
                     "message":"Party time","animation":"confetti",
                     "display_duration":3600}}
        tenant_screens.save_config(self.db,self.org,base)
        tenant_screens.publish(self.db,self.org)
        self.poll(fb=1499)
        self.poll(fb=1500)
        current=self.events()["facebook"]
        self.assertEqual(current["display_duration"],3600)
        self.assertEqual(current["celebration_image"],"")
        self.assertTrue(current["event_id"])
        base["targets"]["facebook"]["display_duration"]=5
        tenant_screens.save_config(self.db,self.org,base)
        tenant_screens.publish(self.db,self.org)
        updated=self.events()["facebook"]
        self.assertEqual(updated["display_duration"],5)
        self.assertEqual(updated["event_id"],current["event_id"])

    def test_image_only_requires_artwork_when_enabled(self):
        sample={"enabled":True,"goal":1300,"message":"Stored but hidden",
                "animation":"image_only","display_duration":3600}
        with self.assertRaisesRegex(ValueError,"uploaded 16:9 image"):
            tenant_targets.validate({"facebook":sample})
        image="media:"+"a"*32
        accepted=tenant_targets.validate({"facebook":{
            **sample,"celebration_image":image}})["facebook"]
        self.assertEqual(accepted["animation"],"image_only")
        self.assertEqual(accepted["display_duration"],3600)
        self.assertEqual(accepted["celebration_image"],image)
        # Unselected target settings remain editable without uploaded art.
        disabled=tenant_targets.validate({"instagram":{
            **sample,"enabled":False}})["instagram"]
        self.assertFalse(disabled["enabled"])

    def test_target_sequence_waits_for_prior_celebration_duration(self):
        from unittest.mock import patch
        root=tenant_screens.get_config(self.db,self.org)["settings"]
        root["targets"]={"facebook":{"enabled":True,"goal":1300,
             "message":"Drinks","animation":"confetti","display_duration":5,
             "next_targets":[
                {"enabled":True,"goal":1350,"message":"Happy hour","animation":"football",
                 "display_duration":60,"countdown_enabled":True,
                 "countdown_message":"Only {remaining} more followers!"},
                {"enabled":True,"goal":1400,"message":"Prize","animation":"fireworks",
                 "display_duration":10}]}}
        with patch.object(tenant_targets.time,"time",return_value=1000):
            tenant_screens.save_config(self.db,self.org,root)
            tenant_screens.publish(self.db,self.org)
            self.poll(fb=1299)
            self.poll(fb=1300)
            first=self.events()["facebook"]
            self.assertEqual((first["goal"],first["active_index"],first["total_targets"]),
                             (1300,0,3))
            self.assertTrue(first["event_id"])
        with patch.object(tenant_targets.time,"time",return_value=1004):
            self.poll(fb=1350)
            held=self.events()["facebook"]
            self.assertEqual(held["goal"],1300)
            self.assertEqual(held["event_id"],first["event_id"])
        with patch.object(tenant_targets.time,"time",return_value=1005):
            self.poll(fb=1340)
            second=self.events()["facebook"]
            self.assertEqual((second["goal"],second["active_index"]),(1350,1))
            self.assertEqual(second["state"],"armed")
            self.assertIsNone(second["event_id"])
            self.assertEqual(second["countdown_message"],"Only {remaining} more followers!")
        with patch.object(tenant_targets.time,"time",return_value=1006):
            self.poll(fb=1350)
            second=self.events()["facebook"]
            self.assertTrue(second["event_id"])
            self.assertNotEqual(second["event_id"],first["event_id"])
        with patch.object(tenant_targets.time,"time",return_value=1065):
            self.poll(fb=1400)
            self.assertEqual(self.events()["facebook"]["goal"],1350)
        with patch.object(tenant_targets.time,"time",return_value=1066):
            self.poll(fb=1399)
            third=self.events()["facebook"]
            self.assertEqual((third["goal"],third["active_index"]),(1400,2))
            self.assertIsNone(third["event_id"])
        with patch.object(tenant_targets.time,"time",return_value=1067):
            self.poll(fb=1400)
            third=self.events()["facebook"]
            self.assertTrue(third["event_id"])
        with patch.object(tenant_targets.time,"time",return_value=1077):
            complete=self.events()["facebook"]
            self.assertEqual(complete["state"],"complete")
            self.assertIsNone(complete["event_id"])
            self.assertEqual(complete["total_targets"],3)

    def test_sequence_validation_and_tenant_scoping(self):
        base={"enabled":True,"goal":100,"message":"First","animation":"confetti"}
        later={"enabled":True,"goal":200,"message":"Second","animation":"football"}
        valid=tenant_targets.validate({"facebook":{**base,"next_targets":[later]}})
        self.assertEqual([x["goal"] for x in tenant_targets.stages(valid["facebook"])],[100,200])
        self.assertNotIn("next_targets",tenant_targets.validate({"facebook":base})["facebook"])
        for extra in ([{**later,"goal":100}],[{**later,"enabled":False}],
                      [{**later,"next_targets":[]}],[later]*20):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    tenant_targets.validate({"facebook":{**base,"next_targets":extra}})
        first=tenant_screens.get_config(self.db,self.org)["settings"]
        first["targets"]=valid
        tenant_screens.save_config(self.db,self.org,first)
        tenant_screens.publish(self.db,self.org)
        self.assertEqual(self.events(self.other),{})

    def test_existing_followers_skip_previously_met_first_stage(self):
        config=tenant_screens.get_config(self.db,self.org)["settings"]
        config["targets"]={"facebook":{"enabled":True,"goal":1200,
          "message":"Past goal","animation":"confetti","display_duration":5,
          "next_targets":[{"enabled":True,"goal":1300,
             "message":"Future goal","animation":"image_only",
             "celebration_image":"media:"+"a"*32,"display_duration":60}]}}
        # Test state engine directly: real media ownership is separately checked.
        with patch.object(tenant_targets.time,"time",return_value=2000):
            with sqlite3.connect(self.db) as db:
                db.execute("""INSERT INTO company_social_counts(org_id,facebook_count,
                     instagram_count,facebook_likes,status) VALUES (?,?,?,?,?)""",
                     (self.org,1250,10,10,"connected"))
                db.execute("UPDATE company_screen_config SET published=? WHERE org_id=?",
                           (__import__("json").dumps(config),self.org))
            result=self.events()["facebook"]
            self.assertEqual((result["goal"],result["active_index"]),(1300,1))
            self.assertEqual(result["state"],"armed")
            self.assertIsNone(result["event_id"])
        with patch.object(tenant_targets.time,"time",return_value=2001):
            self.poll(fb=1300)
            fired=self.events()["facebook"]
            self.assertEqual(fired["animation"],"image_only")
            self.assertEqual(fired["display_duration"],60)
            self.assertIsNotNone(fired["event_id"])

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
