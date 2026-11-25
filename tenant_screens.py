"""Organisation-isolated TV configuration, public screen tokens and social counts.

Never expose a tenant screen with legacy global /api/config or /api/counts.
All company writes require authenticated membership and same-origin protection.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing

import tenant_meta
import meta_connect

ALLOWED={"title","subtitle","accent","background","logo","facebook_url",
         "instagram_url","newsletter_url","slides","animation","rotation_seconds"}
MAX_CONFIG=100000
DEFAULT={"title":"My Social Wall","subtitle":"Follow us on social media",
         "accent":"#f04438","background":"#0B1F55","logo":"",
         "facebook_url":"","instagram_url":"","newsletter_url":"",
         "slides":[],"animation":"celebrate","rotation_seconds":12}
ANIMATIONS={"celebrate","football","confetti","none"}

def _db(path):
    db=sqlite3.connect(str(path),timeout=15)
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init(path):
    with closing(_db(path)) as db:
        with db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS company_screen_config(
              org_id TEXT PRIMARY KEY, draft TEXT NOT NULL, published TEXT NOT NULL,
              draft_version INTEGER NOT NULL DEFAULT 0,
              published_version INTEGER NOT NULL DEFAULT 0,
              updated_at INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(org_id) REFERENCES organisations(id));
            CREATE TABLE IF NOT EXISTS company_screen_keys(
              org_id TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE,
              created_at INTEGER NOT NULL,
              FOREIGN KEY(org_id) REFERENCES organisations(id));
            CREATE TABLE IF NOT EXISTS company_social_counts(
              org_id TEXT PRIMARY KEY,
              facebook_count INTEGER, instagram_count INTEGER,
              last_success_at INTEGER, last_attempt_at INTEGER,
              status TEXT NOT NULL DEFAULT 'not_connected',
              FOREIGN KEY(org_id) REFERENCES organisations(id));
            """)

def _check_config(config):
    if not isinstance(config,dict) or set(config)-ALLOWED:
        raise ValueError("Invalid screen configuration")
    clean={**DEFAULT,**config}
    for field in ("title","subtitle","logo","facebook_url","instagram_url","newsletter_url"):
        if not isinstance(clean[field],str) or len(clean[field])>500:
            raise ValueError("Invalid "+field)
    for field in ("accent","background"):
        color=clean[field]
        if not isinstance(color,str) or len(color)!=7 or not color.startswith("#") or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
            raise ValueError("Invalid colour")
    if clean["animation"] not in ANIMATIONS:
        raise ValueError("Unknown animation")
    if type(clean["rotation_seconds"]) is not int or not 5<=clean["rotation_seconds"]<=120:
        raise ValueError("Invalid rotation")
    if not isinstance(clean["slides"],list) or len(clean["slides"])>20:
        raise ValueError("Too many slides")
    for slide in clean["slides"]:
        if not isinstance(slide,dict) or set(slide)-{"title","description","image","duration"}:
            raise ValueError("Invalid slide")
        for field in ("title","description","image"):
            if not isinstance(slide.get(field,""),str) or len(slide.get(field,""))>1500:
                raise ValueError("Invalid slide text")
        if type(slide.get("duration",10)) is not int or not 5<=slide.get("duration",10)<=120:
            raise ValueError("Invalid slide duration")
    if len(json.dumps(clean))>MAX_CONFIG:
        raise ValueError("Configuration too large")
    return clean

def _ensure(db,org):
    db.execute("""INSERT OR IGNORE INTO company_screen_config
       (org_id,draft,published,draft_version,published_version,updated_at)
       VALUES (?,?,?,?,?,?)""",
       (org,json.dumps(DEFAULT),json.dumps(DEFAULT),0,0,int(time.time())))

def get_config(path,org,published=False):
    with closing(_db(path)) as db:
        with db:
            _ensure(db,org)
            row=db.execute("""SELECT draft,published,draft_version,published_version
                   FROM company_screen_config WHERE org_id=?""",(org,)).fetchone()
    i=1 if published else 0
    return {"settings":json.loads(row[i]),"version":row[3] if published else row[2],
            "published_version":row[3]}

def save_config(path,org,config):
    clean=_check_config(config)
    with closing(_db(path)) as db:
        with db:
            _ensure(db,org)
            db.execute("""UPDATE company_screen_config SET draft=?,
                draft_version=draft_version+1,updated_at=? WHERE org_id=?""",
                (json.dumps(clean,separators=(",",":")),int(time.time()),org))
            version=db.execute("SELECT draft_version FROM company_screen_config WHERE org_id=?",(org,)).fetchone()[0]
    return version

def publish(path,org):
    with closing(_db(path)) as db:
        with db:
            _ensure(db,org)
            db.execute("""UPDATE company_screen_config
              SET published=draft,published_version=published_version+1,
                  updated_at=? WHERE org_id=?""",(int(time.time()),org))
            version=db.execute("SELECT published_version FROM company_screen_config WHERE org_id=?",(org,)).fetchone()[0]
    return version

def rotate_screen_key(path,org):
    raw=secrets.token_urlsafe(40)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    with closing(_db(path)) as db:
        with db:
            db.execute("""INSERT INTO company_screen_keys VALUES (?,?,?)
                  ON CONFLICT(org_id) DO UPDATE SET
                  key_hash=excluded.key_hash,created_at=excluded.created_at""",
                  (org,digest,int(time.time())))
    return raw

def screen_org(path,raw):
    if not isinstance(raw,str) or len(raw)<40 or len(raw)>100:
        return None
    digest=hashlib.sha256(raw.encode()).hexdigest()
    with closing(_db(path)) as db:
        row=db.execute("SELECT org_id FROM company_screen_keys WHERE key_hash=?",(digest,)).fetchone()
    return row[0] if row else None

def connection_health(path,org):
    with closing(_db(path)) as db:
        row=db.execute("""SELECT facebook_count,instagram_count,last_success_at,
                 last_attempt_at,status FROM company_social_counts WHERE org_id=?""",(org,)).fetchone()
    connected=tenant_meta.connections(path,org)
    # A revoked/unlinked company must never display cached follower counts.
    if not connected:
        return {"accounts":[],"facebook":None,"instagram":None,
                "last_success_at":None,"last_attempt_at":None,"status":"not_connected"}
    return {"accounts":connected,"facebook":row[0] if row else None,
            "instagram":row[1] if row else None,"last_success_at":row[2] if row else None,
            "last_attempt_at":row[3] if row else None,
            "status":row[4] if row else "pending"}

def sync_org(path,org):
    """Poll a specific organisation. Do not log tokens, response payloads or URLs."""
    with closing(_db(path)) as db:
        row=db.execute("""SELECT page_id,instagram_id,encrypted_token
                FROM tenant_meta_connections WHERE organisation_id=?
                ORDER BY connected_at DESC LIMIT 1""",(org,)).fetchone()
    if not row:
        with closing(_db(path)) as db:
            with db:
                db.execute("""INSERT INTO company_social_counts(org_id,status,last_attempt_at)
                    VALUES (?,?,?) ON CONFLICT(org_id) DO UPDATE
                    SET status='not_connected',facebook_count=NULL,instagram_count=NULL,
                    last_attempt_at=excluded.last_attempt_at""",(org,"not_connected",int(time.time())))
        return
    page_id,ig_id,ciphertext=row
    now=int(time.time())
    facebook=instagram=None
    status="connected"
    try:
        token=tenant_meta.token_cipher().decrypt(ciphertext.encode()).decode()
        data=meta_connect.graph(str(page_id),{"fields":"followers_count,fan_count"},token)
        fb=data.get("followers_count",data.get("fan_count"))
        if fb is not None: facebook=int(fb)
        if ig_id:
            ig=meta_connect.graph(str(ig_id),{"fields":"followers_count"},token)
            if ig.get("followers_count") is not None:instagram=int(ig["followers_count"])
        if facebook is None and instagram is None:status="unavailable"
    except Exception:
        status="needs_attention"
    with closing(_db(path)) as db:
        with db:
            db.execute("""INSERT INTO company_social_counts
             (org_id,facebook_count,instagram_count,last_success_at,last_attempt_at,status)
             VALUES (?,?,?,?,?,?)
             ON CONFLICT(org_id) DO UPDATE SET
               facebook_count=CASE WHEN excluded.status='connected' THEN excluded.facebook_count ELSE company_social_counts.facebook_count END,
               instagram_count=CASE WHEN excluded.status='connected' THEN excluded.instagram_count ELSE company_social_counts.instagram_count END,
               last_success_at=CASE WHEN excluded.status='connected' THEN excluded.last_success_at ELSE company_social_counts.last_success_at END,
               last_attempt_at=excluded.last_attempt_at,status=excluded.status""",
               (org,facebook,instagram,now if status=="connected" else None,now,status))

def poll_all(path):
    with closing(_db(path)) as db:
        orgs=[row[0] for row in db.execute("SELECT DISTINCT organisation_id FROM tenant_meta_connections").fetchall()]
    for org in orgs:
        sync_org(path,org)

def handler_route(handler,path,method):
    parts=handler.path.split("?",1)[0].strip("/").split("/")
    # Public display accesses ONLY opaque screen-key endpoints.
    if len(parts)==3 and parts[:2]==["api","screens"] and method=="GET":
        org=screen_org(path,parts[2])
        if not org:
            handler.json_response(404,{"error":"screen not found"});return True
        state=get_config(path,org,True)
        state["social"]=connection_health(path,org)
        # Public screen response must not list Page IDs or names beyond display config.
        state["social"].pop("accounts",None)
        handler.json_response(200,state)
        return True
    if len(parts)<4 or parts[:2]!=["api","organisations"] or parts[3]!="screen":
        return False
    org=parts[2]
    user=handler.current_user()
    if not user:
        handler.json_response(401,{"error":"sign in required"});return True
    role=tenant_meta.member_role(path,org,user["username"])
    if not role:
        handler.json_response(403,{"error":"not a member"});return True
    action=parts[4] if len(parts)==5 else ""
    if method=="GET" and action=="config":
        handler.json_response(200,get_config(path,org));return True
    if method=="GET" and action=="social":
        handler.json_response(200,connection_health(path,org));return True
    if method=="GET" and action=="access":
        with closing(_db(path)) as db:
            exists=bool(db.execute("SELECT 1 FROM company_screen_keys WHERE org_id=?",(org,)).fetchone())
        handler.json_response(200,{"configured":exists,"can_rotate":role=="owner"});return True
    if method=="POST" and action in ("config","publish","rotate-key"):
        if role not in ("owner","manager") or (action=="rotate-key" and role!="owner") or not handler.same_origin():
            handler.json_response(403,{"error":"insufficient permissions"});return True
        try:
            if action=="config":
                body=handler.post_body(110000)
                if body is None:raise ValueError("Missing config")
                cfg=json.loads(body).get("settings")
                handler.json_response(200,{"version":save_config(path,org,cfg)})
            elif action=="publish":
                handler.json_response(200,{"version":publish(path,org)})
            else:
                raw=rotate_screen_key(path,org)
                # Show the generated token only once to the owner.
                handler.json_response(200,{"url":os.environ["PUBLIC_ORIGIN"].rstrip("/")+"/screen/"+raw})
        except (ValueError,TypeError,AttributeError):
            handler.json_response(400,{"error":"invalid configuration"})
        return True
    return False
