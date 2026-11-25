"""Invitation security, tenancy, SMTP setup and login throttling."""
import os, sqlite3, tempfile, unittest
from unittest.mock import patch

import client_onboarding, tenant_meta

def hash_pw(password):
    import hashlib, secrets
    salt=secrets.token_hex(16)
    return salt+":"+hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),260000).hex()

class InvitationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.temp.name,"test.sqlite")
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE staff(username TEXT PRIMARY KEY,
                     password_hash TEXT NOT NULL,role TEXT NOT NULL,created_at INTEGER NOT NULL)""")
        self.environ=patch.dict(os.environ,{"PUBLIC_ORIGIN":"https://staging.example.test",
                             "INVITE_SMTP_HOST":"","INVITE_FROM_EMAIL":""})
        self.environ.start()
        tenant_meta.init(self.path)
        client_onboarding.init(self.path)
        self.a=tenant_meta.create_organisation(self.path,"First Venue","admin")["id"]
        self.b=tenant_meta.create_organisation(self.path,"Other Venue","admin")["id"]

    def tearDown(self):
        self.environ.stop()
        self.temp.cleanup()

    def test_token_hash_and_one_time_consumption(self):
        token=client_onboarding.make_invite(self.path,self.a,"Client@Example.com","owner","admin")
        self.assertTrue(token in client_onboarding.invitation_url(token))
        self.assertEqual(client_onboarding.lookup(self.path,token)["email"],"client@example.com")
        with open(self.path,"rb") as stream:
            self.assertNotIn(token.encode(),stream.read())
        result=client_onboarding.accept(self.path,token,"a-very-long-unique-password",hash_pw)
        self.assertEqual(result["organisation_id"],self.a)
        self.assertIsNone(client_onboarding.lookup(self.path,token))
        with self.assertRaises(ValueError):
            client_onboarding.accept(self.path,token,"a-very-long-unique-password",hash_pw)
        self.assertEqual(tenant_meta.member_role(self.path,self.a,"client@example.com"),"owner")
        self.assertIsNone(tenant_meta.member_role(self.path,self.b,"client@example.com"))
        self.assertIsNone(tenant_meta.member_role(self.path,"british-arena","client@example.com"))
        with sqlite3.connect(self.path) as conn:
            row=conn.execute("SELECT password_hash,role FROM staff WHERE username=?",
                             ("client@example.com",)).fetchone()
        self.assertNotIn("a-very-long-unique-password",row[0])
        self.assertEqual(row[1],"marketing")

    def test_expired_and_revoked_tokens(self):
        token=client_onboarding.make_invite(self.path,self.a,"client@example.com","manager","admin")
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE client_invitations SET expires_at=0")
        self.assertIsNone(client_onboarding.lookup(self.path,token))
        with self.assertRaises(ValueError):
            client_onboarding.accept(self.path,token,"secure-passphrase-123",hash_pw)
        replacement=client_onboarding.make_invite(self.path,self.a,"client@example.com","manager","admin")
        self.assertIsNone(client_onboarding.lookup(self.path,token))
        self.assertIsNotNone(client_onboarding.lookup(self.path,replacement))
        later=client_onboarding.make_invite(self.path,self.a,"client@example.com","viewer","admin")
        self.assertIsNone(client_onboarding.lookup(self.path,replacement))
        self.assertEqual(client_onboarding.lookup(self.path,later)["role"],"viewer")

    def test_cannot_invite_into_another_company(self):
        tenant_meta.add_member(self.path,self.a,"admin","owner")
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO staff VALUES (?,?,?,?)",
                         ("accountant","hashed","marketing",0))
        tenant_meta.add_member(self.path,self.a,"accountant","viewer")
        with self.assertRaises(ValueError):
            client_onboarding.make_invite(self.path,self.a,"client@example.com","owner","accountant")
        with self.assertRaises(ValueError):
            client_onboarding.make_invite(self.path,self.a,"client@example.com","superadmin","admin")
        with self.assertRaises(ValueError):
            client_onboarding.make_invite(self.path,self.a,"not-email","owner","admin")

    def test_password_minimum_and_existing_account(self):
        token=client_onboarding.make_invite(self.path,self.b,"client@example.com","viewer","admin")
        with self.assertRaises(ValueError):
            client_onboarding.accept(self.path,token,"short",hash_pw)
        self.assertIsNotNone(client_onboarding.lookup(self.path,token))
        result=client_onboarding.accept(self.path,token,"long-and-distinct-passphrase",hash_pw)
        self.assertEqual(result["role"],"viewer")
        with self.assertRaises(ValueError):
            client_onboarding.make_invite(self.path,self.a,"client@example.com","owner","admin")

    def test_rate_limit_for_invitations_and_login(self):
        for i in range(10):
            client_onboarding.make_invite(self.path,self.a,f"person{i}@example.com","viewer","admin")
        with self.assertRaises(ValueError):
            client_onboarding.make_invite(self.path,self.a,"eleventh@example.com","viewer","admin")
        for _ in range(7):
            client_onboarding.login_result(self.path,"person0@example.com",False)
        self.assertFalse(client_onboarding.can_login(self.path,"person0@example.com"))
        self.assertTrue(client_onboarding.can_login(self.path,"person1@example.com"))
        client_onboarding.login_result(self.path,"person0@example.com",True)
        self.assertTrue(client_onboarding.can_login(self.path,"person0@example.com"))

    def test_delivery_unconfigured_not_falsely_reported_sent(self):
        token=client_onboarding.make_invite(self.path,self.a,"client@example.com","owner","admin")
        url=client_onboarding.invitation_url(token)
        self.assertIn("/accept-invite#token=",url)
        self.assertFalse(client_onboarding.deliver("client@example.com",url,"First Venue"))

    def test_client_routes_block_cross_company_invitation(self):
        class Handler:
            def __init__(self,org,user,origin=True):
                self.path=f"/api/organisations/{org}/invitations"
                self.user=user
                self.origin=origin
                self.reply=None
            def current_user(self):return {"username":self.user} if self.user else None
            def same_origin(self):return self.origin
            def json_response(self,status,payload):self.reply=(status,payload)
        other=Handler(self.a,None)
        self.assertTrue(client_onboarding.route(other,self.path,"POST"))
        self.assertEqual(other.reply[0],401)
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO staff VALUES (?,?,?,?)",("viewer","hashed","viewer",0))
        tenant_meta.add_member(self.path,self.a,"viewer","viewer")
        unauthorized=Handler(self.a,"viewer")
        self.assertTrue(client_onboarding.route(unauthorized,self.path,"POST"))
        self.assertEqual(unauthorized.reply[0],403)
        bad_origin=Handler(self.a,"admin",origin=False)
        self.assertTrue(client_onboarding.route(bad_origin,self.path,"POST"))
        self.assertEqual(bad_origin.reply[0],403)

if __name__=="__main__":
    unittest.main()
