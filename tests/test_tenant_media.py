"""Company media upload, sanitisation, persistent isolation, and published TV tests."""
import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import tenant_meta
import tenant_media
import tenant_screens

class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,"arena.sqlite3")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE staff(username TEXT PRIMARY KEY,role TEXT)")
        tenant_meta.init(self.path)
        tenant_screens.init(self.path)
        tenant_media.init(self.path)
        self.a=tenant_meta.create_organisation(self.path,"Alpha Venue","admin")["id"]
        self.b=tenant_meta.create_organisation(self.path,"Beta Venue","admin")["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def picture(self,format="PNG",mode="RGBA"):
        img=Image.new(mode,(42,28),(12,45,130,200) if mode=="RGBA" else (12,45,130))
        stream=io.BytesIO();img.save(stream,format=format)
        return stream.getvalue()

    def test_upload_is_webp_and_tenant_scoped(self):
        saved=tenant_media.upload(self.path,self.a,self.picture(),"image/png")
        self.assertRegex(saved["reference"],r"^media:[a-f0-9]{32}$")
        identifier=tenant_media.media_id(saved["reference"])
        self.assertTrue(tenant_media.exists(self.path,self.a,identifier))
        self.assertFalse(tenant_media.exists(self.path,self.b,identifier))
        with open(tenant_media._path(self.path,self.a,identifier),"rb") as f:
            file=f.read()
        with Image.open(io.BytesIO(file)) as img:
            self.assertEqual(img.format,"WEBP")
            self.assertEqual(img.size,(42,28))
            self.assertNotIn("exif",img.info)
        self.assertNotEqual(file,self.picture())

    def test_invalid_files_rejected(self):
        for name,data,mime in [
            ("script",b"<svg><script>alert(1)</script></svg>","image/png"),
            ("html",b"<html>bad</html>","image/jpeg"),
            ("fake",self.picture(),"application/octet-stream"),
            ("too large",b"x"*(tenant_media.MAX_BYTES+1),"image/png")
        ]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    tenant_media.upload(self.path,self.a,data,mime)

    def test_cross_company_draft_reference_rejected(self):
        saved=tenant_media.upload(self.path,self.a,self.picture(),"image/png")
        setting=tenant_screens.get_config(self.path,self.b)["settings"]
        with self.assertRaises(ValueError):
            tenant_screens.save_config(self.path,self.b,{**setting,"logo":saved["reference"]})
        with self.assertRaises(ValueError):
            tenant_screens.save_config(self.path,self.b,{**setting,"slides":[
                {"title":"Private","image":saved["reference"],"description":"","duration":10}]})

    def test_draft_image_not_public_until_published(self):
        saved=tenant_media.upload(self.path,self.a,self.picture(),"image/png")
        identifier=tenant_media.media_id(saved["reference"])
        settings=tenant_screens.get_config(self.path,self.a)["settings"]
        tenant_screens.save_config(self.path,self.a,{**settings,"logo":saved["reference"]})
        self.assertFalse(tenant_media.referenced_in_published(self.path,self.a,identifier))
        tenant_screens.publish(self.path,self.a)
        self.assertTrue(tenant_media.referenced_in_published(self.path,self.a,identifier))
        self.assertFalse(tenant_media.referenced_in_published(self.path,self.b,identifier))

    def test_celebration_art_must_be_16_by_9_company_owned_and_published(self):
        def art(width,height):
            buffer=io.BytesIO()
            Image.new("RGB",(width,height),(24,78,155)).save(buffer,format="PNG")
            return tenant_media.upload(self.path,self.a,buffer.getvalue(),"image/png")
        valid=art(320,180)
        invalid=art(300,180)
        settings=tenant_screens.get_config(self.path,self.a)["settings"]
        goal={"enabled":True,"goal":1300,"message":"Happy hour",
              "animation":"confetti","celebration_image":valid["reference"],
              "display_duration":3600}
        setting={**settings,"targets":{"facebook":goal}}
        ident=tenant_media.media_id(valid["reference"])
        tenant_screens.save_config(self.path,self.a,setting)
        self.assertFalse(tenant_media.referenced_in_published(self.path,self.a,ident))
        tenant_screens.publish(self.path,self.a)
        self.assertTrue(tenant_media.referenced_in_published(self.path,self.a,ident))
        self.assertFalse(tenant_media.referenced_in_published(self.path,self.b,ident))
        target=tenant_screens.get_config(self.path,self.a,True)["settings"]["targets"]["facebook"]
        self.assertEqual(target["display_duration"],3600)
        self.assertEqual(target["celebration_image"],valid["reference"])
        with self.assertRaisesRegex(ValueError,"16:9"):
            tenant_screens.save_config(self.path,self.a,{
                **settings,"targets":{"facebook":{
                    **goal,"celebration_image":invalid["reference"]}}})
        with self.assertRaises(ValueError):
            tenant_screens.save_config(self.path,self.b,{
                **tenant_screens.get_config(self.path,self.b)["settings"],
                "targets":{"facebook":goal}})

    def test_media_route_authorisation_and_public_publishing(self):
        saved=tenant_media.upload(self.path,self.a,self.picture(),"image/png")
        ident=tenant_media.media_id(saved["reference"])
        key_a=tenant_screens.rotate_screen_key(self.path,self.a)
        key_b=tenant_screens.rotate_screen_key(self.path,self.b)
        class Handler:
            def __init__(self,url,user=None,origin=True,payload=b"",content_type="image/png"):
                self.path=url;self.username=user;self.origin=origin;self.data=payload
                self.headers={"Content-Type":content_type}
                self.wfile=io.BytesIO()
                self.status=None
            def current_user(self):return {"username":self.username} if self.username else None
            def same_origin(self):return self.origin
            def json_response(self,status,body):self.status=status
            def send_response(self,status):self.status=status
            def send_header(self,*args):pass
            def end_headers(self):pass
            def post_body(self,limit):return self.data if 0<len(self.data)<=limit else None
        admin_url=f"/api/organisations/{self.a}/media/{ident}"
        anon=Handler(admin_url)
        self.assertTrue(tenant_media.route(anon,self.path,"GET"))
        self.assertEqual(anon.status,401)
        someone_else=Handler(f"/api/organisations/{self.b}/media/{ident}","admin")
        self.assertTrue(tenant_media.route(someone_else,self.path,"GET"))
        self.assertEqual(someone_else.status,404)
        draft=Handler(f"/api/screens/{key_a}/media/{ident}")
        self.assertTrue(tenant_media.route(draft,self.path,"GET"))
        self.assertEqual(draft.status,404)
        settings=tenant_screens.get_config(self.path,self.a)["settings"]
        tenant_screens.save_config(self.path,self.a,{**settings,"logo":saved["reference"]})
        tenant_screens.publish(self.path,self.a)
        public=Handler(f"/api/screens/{key_a}/media/{ident}")
        self.assertTrue(tenant_media.route(public,self.path,"GET"))
        self.assertEqual(public.status,200)
        self.assertTrue(public.wfile.getvalue().startswith(b"RIFF"))
        wrong_key=Handler(f"/api/screens/{key_b}/media/{ident}")
        self.assertTrue(tenant_media.route(wrong_key,self.path,"GET"))
        self.assertEqual(wrong_key.status,404)
        forbidden=Handler(f"/api/organisations/{self.a}/media/upload","admin",
                          origin=False,payload=self.picture())
        self.assertTrue(tenant_media.route(forbidden,self.path,"POST"))
        self.assertEqual(forbidden.status,403)
        anonymous_upload=Handler(f"/api/organisations/{self.a}/media/upload",payload=self.picture())
        self.assertTrue(tenant_media.route(anonymous_upload,self.path,"POST"))
        self.assertEqual(anonymous_upload.status,401)

if __name__=="__main__":
    unittest.main()
