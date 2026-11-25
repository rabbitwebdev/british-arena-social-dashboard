"""Tenant-aware organisations and Meta connections for the stdlib HTTP dashboard.

Phase 1 isolates Meta credentials and organisation membership. Existing display,
config, and staff administration are legacy single-site and are NOT tenant-safe.
"""
import html
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
from contextlib import closing

import meta_connect
from cryptography.fernet import Fernet


def token_cipher():
    raw=os.environ['META_TOKEN_ENCRYPTION_KEY']
    if len(raw)<32:
        raise ValueError('META_TOKEN_ENCRYPTION_KEY must be at least 32 characters')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))

ROLES = ("owner", "manager", "viewer")
CALLBACK = "/api/integrations/meta/callback"


def db_open(path):
    db = sqlite3.connect(str(path), timeout=15)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init(path):
    with closing(db_open(path)) as db:
        with db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS organisations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS organisation_members (
                    organisation_id TEXT NOT NULL, username TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner','manager','viewer')),
                    PRIMARY KEY(organisation_id,username),
                    FOREIGN KEY(organisation_id) REFERENCES organisations(id));
                CREATE TABLE IF NOT EXISTS tenant_oauth_states (
                    nonce TEXT PRIMARY KEY, organisation_id TEXT NOT NULL,
                    username TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS tenant_meta_pending (
                    nonce TEXT PRIMARY KEY, organisation_id TEXT NOT NULL,
                    username TEXT NOT NULL, encrypted_token TEXT NOT NULL,
                    expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS tenant_meta_connections (
                    organisation_id TEXT NOT NULL, page_id TEXT NOT NULL,
                    page_name TEXT NOT NULL, instagram_id TEXT,
                    instagram_username TEXT, encrypted_token TEXT NOT NULL,
                    connected_by TEXT NOT NULL, connected_at INTEGER NOT NULL,
                    PRIMARY KEY(organisation_id,page_id),
                    FOREIGN KEY(organisation_id) REFERENCES organisations(id));
            """)
            db.execute("INSERT OR IGNORE INTO organisations VALUES (?,?,?)",
                       ("british-arena","British Arena",int(time.time())))
            db.execute("INSERT OR IGNORE INTO organisation_members VALUES (?,?,?)",
                       ("british-arena","admin","owner"))
            # Initial legacy staff memberships were seeded in Phase 1.
            # Never grant *new* staff British Arena membership implicitly.
            # Platform admins assign each new account to its intended company.


def memberships(path, username):
    with closing(db_open(path)) as db:
        rows=db.execute("""SELECT o.id,o.name,m.role FROM organisations o
            JOIN organisation_members m ON m.organisation_id=o.id
            WHERE m.username=? ORDER BY o.name""",(username,)).fetchall()
    return [{"id":a,"name":b,"role":c} for a,b,c in rows]


def member_role(path, org, username):
    with closing(db_open(path)) as db:
        row=db.execute("SELECT role FROM organisation_members WHERE organisation_id=? AND username=?",
                       (org,username)).fetchone()
    return row[0] if row else None


def create_organisation(path, name, owner):
    if not isinstance(name,str) or not 2<=len(name.strip())<=100:
        raise ValueError("Invalid organisation name")
    org=secrets.token_hex(12)
    with closing(db_open(path)) as db:
        with db:
            db.execute("INSERT INTO organisations VALUES (?,?,?)",(org,name.strip(),int(time.time())))
            db.execute("INSERT INTO organisation_members VALUES (?,?,?)",(org,owner,"owner"))
    return {"id":org,"name":name.strip()}


def add_member(path, org, username, role):
    if role not in ROLES or not isinstance(username,str) or not username:
        raise ValueError("Invalid membership")
    with closing(db_open(path)) as db:
        with db:
            if username!="admin" and not db.execute("SELECT 1 FROM staff WHERE username=?",(username,)).fetchone():
                raise ValueError("Staff account not found")
            db.execute("""INSERT INTO organisation_members VALUES (?,?,?)
                ON CONFLICT(organisation_id,username) DO UPDATE SET role=excluded.role""",
                (org,username,role))


def redirect_uri():
    return os.environ["PUBLIC_ORIGIN"].rstrip("/")+CALLBACK


def authorize(path, org, username):
    nonce=secrets.token_urlsafe(32)
    now=int(time.time())
    with closing(db_open(path)) as db:
        with db:
            db.execute("DELETE FROM tenant_oauth_states WHERE expires<?",(now,))
            db.execute("INSERT INTO tenant_oauth_states VALUES (?,?,?,?)",
                       (nonce,org,username,now+600))
    params={"client_id":os.environ["META_APP_ID"],"redirect_uri":redirect_uri(),
            "response_type":"code","scope":meta_connect.PERMISSIONS,"state":nonce}
    return "https://www.facebook.com/"+meta_connect.GRAPH_VERSION+"/dialog/oauth?"+urllib.parse.urlencode(params)


def consume_state(path, nonce, username):
    with closing(db_open(path)) as db:
        with db:
            row=db.execute("SELECT organisation_id,username,expires FROM tenant_oauth_states WHERE nonce=?",
                           (nonce,)).fetchone()
            db.execute("DELETE FROM tenant_oauth_states WHERE nonce=?",(nonce,))
    if not row or row[1]!=username or row[2]<time.time():
        raise ValueError("Invalid or expired OAuth state")
    return row[0]


def store_pending(path, org, username, token):
    nonce=secrets.token_urlsafe(24)
    ciphertext=token_cipher().encrypt(token.encode()).decode()
    with closing(db_open(path)) as db:
        with db:
            db.execute("DELETE FROM tenant_meta_pending WHERE expires<?",(int(time.time()),))
            db.execute("INSERT INTO tenant_meta_pending VALUES (?,?,?,?,?)",
                       (nonce,org,username,ciphertext,int(time.time())+600))
    return nonce


def consume_pending(path, nonce, org, username):
    with closing(db_open(path)) as db:
        with db:
            row=db.execute("""SELECT organisation_id,username,encrypted_token,expires
                FROM tenant_meta_pending WHERE nonce=?""",(nonce,)).fetchone()
            db.execute("DELETE FROM tenant_meta_pending WHERE nonce=?",(nonce,))
    if not row or row[0]!=org or row[1]!=username or row[3]<time.time():
        raise ValueError("Invalid or expired account selection")
    return token_cipher().decrypt(row[2].encode()).decode()


def save_page(path, org, username, page):
    if not page.get("access_token") or not page.get("id"):
        raise ValueError("Page has no granted access token")
    cipher=token_cipher().encrypt(page["access_token"].encode()).decode()
    with closing(db_open(path)) as db:
        with db:
            db.execute("""INSERT INTO tenant_meta_connections
                (organisation_id,page_id,page_name,instagram_id,instagram_username,
                 encrypted_token,connected_by,connected_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(organisation_id,page_id) DO UPDATE SET
                page_name=excluded.page_name,instagram_id=excluded.instagram_id,
                instagram_username=excluded.instagram_username,
                encrypted_token=excluded.encrypted_token,connected_by=excluded.connected_by,
                connected_at=excluded.connected_at""",
                (org,str(page["id"]),page["name"],page.get("instagram_id"),
                 page.get("instagram_username"),cipher,username,int(time.time())))


def connections(path, org):
    with closing(db_open(path)) as db:
        rows=db.execute("""SELECT page_id,page_name,instagram_id,instagram_username,connected_at
            FROM tenant_meta_connections WHERE organisation_id=? ORDER BY page_name""",
            (org,)).fetchall()
    return [{"page_id":a,"page_name":b,"instagram_id":c,
             "instagram_username":d,"connected_at":e} for a,b,c,d,e in rows]


def disconnect(path, org, page):
    with closing(db_open(path)) as db:
        with db:
            result=db.execute("DELETE FROM tenant_meta_connections WHERE organisation_id=? AND page_id=?",
                              (org,page))
    return result.rowcount>0


def exchange_code(code):
    """Exchange with the exact SaaS callback URI, without mutating module globals."""
    params={"client_id":os.environ["META_APP_ID"],"client_secret":os.environ["META_APP_SECRET"],
            "redirect_uri":redirect_uri(),"code":code}
    short=meta_connect.graph("oauth/access_token",params)["access_token"]
    try:
        long=meta_connect.graph("oauth/access_token",{
            "grant_type":"fb_exchange_token","client_id":os.environ["META_APP_ID"],
            "client_secret":os.environ["META_APP_SECRET"],"fb_exchange_token":short})
        return long["access_token"]
    except (KeyError,ValueError):
        return short


def route(handler, path, method):
    """Handle tenant routes using the parent Handler's session and CSRF checks."""
    parsed=urllib.parse.urlsplit(handler.path)
    segments=parsed.path.strip("/").split("/")
    if segments==["api","platform","integrations","meta"] and method=="GET":
        if not handler.is_admin():
            handler.json_response(403,{"error":"platform administrator only"});return True
        required=("META_APP_ID","META_APP_SECRET","META_TOKEN_ENCRYPTION_KEY","PUBLIC_ORIGIN")
        missing=[key for key in required if not os.environ.get(key)]
        handler.json_response(200,{
            "configured":not missing,
            "missing":missing,
            "callback_url":redirect_uri() if os.environ.get("PUBLIC_ORIGIN") else None,
            "graph_version":meta_connect.GRAPH_VERSION,
            "credentials_location":"Railway environment variables",
            "customer_setup_required":False
        })
        return True
    if segments==["api","organisations"]:
        user=handler.current_user()
        if not user:
            handler.json_response(401,{"error":"sign in required"});return True
        if method=="GET":
            handler.json_response(200,{"organisations":memberships(path,user["username"])})
        elif method=="POST":
            if not handler.is_admin() or not handler.same_origin():
                handler.json_response(403,{"error":"admin only"});return True
            try:
                data=json.loads(handler.post_body(4096) or b"{}")
                handler.json_response(201,create_organisation(path,data.get("name"),user["username"]))
            except (TypeError,ValueError):
                handler.json_response(400,{"error":"invalid organisation"})
        else:
            handler.json_response(405,{"error":"method not allowed"})
        return True

    if len(segments)>=4 and segments[:2]==["api","organisations"]:
        org=segments[2]
        user=handler.current_user()
        if not user:
            handler.json_response(401,{"error":"sign in required"});return True
        username=user["username"]
        role=member_role(path,org,username)
        if not role:
            handler.json_response(403,{"error":"not an organisation member"});return True
        if segments[3:]==["members"] and method=="POST":
            if role!="owner" or not handler.same_origin():
                handler.json_response(403,{"error":"owner only"});return True
            try:
                data=json.loads(handler.post_body(4096) or b"{}")
                add_member(path,org,data["username"],data["role"])
                handler.json_response(200,{"ok":True})
            except (TypeError,ValueError,KeyError,sqlite3.IntegrityError):
                handler.json_response(400,{"error":"invalid member"})
            return True
        if segments[3:]==["meta","availability"] and method=="GET":
            handler.json_response(200,{
                "available":bool(meta_connect.configured()),
                "message":("Connect your Facebook Page to get started."
                           if meta_connect.configured()
                           else "Facebook connections are being prepared. Please try again later.")
            })
            return True
        if segments[3:]==["meta","connections"] and method=="GET":
            handler.json_response(200,{"connections":connections(path,org)});return True
        if len(segments)==5 and segments[3]=="meta":
            if role not in ("owner","manager"):
                handler.json_response(403,{"error":"manager permission required"});return True
            operation=segments[4]
            if operation=="connect" and method=="GET":
                if not meta_connect.configured():
                    handler.json_response(503,{"error":"Facebook connections are temporarily unavailable. Please try again later."});return True
                handler.send_response(303)
                handler.send_header("Location",authorize(path,org,username))
                handler.send_header("Cache-Control","no-store")
                handler.end_headers();return True
            if operation in ("select","disconnect") and method=="POST":
                if not handler.same_origin():
                    handler.json_response(403,{"error":"invalid origin"});return True
                try:
                    data=json.loads(handler.post_body(8192) or b"{}")
                    if operation=="disconnect":
                        result={"removed":disconnect(path,org,str(data["page_id"]))}
                    else:
                        token=consume_pending(path,data["pending"],org,username)
                        selected=next((page for page in meta_connect.list_pages(token)
                                       if page["id"]==data["page_id"]),None)
                        if not selected: raise ValueError("Page not available")
                        save_page(path,org,username,selected)
                        result={"ok":True}
                    handler.json_response(200,result)
                except (TypeError,ValueError,KeyError):
                    handler.json_response(400,{"error":"selection invalid or expired"})
                return True
    if segments==["api","integrations","meta","callback"] and method=="GET":
        user=handler.current_user()
        if not user:
            handler.json_response(401,{"error":"sign in required"});return True
        params=urllib.parse.parse_qs(parsed.query)
        if "error" in params:
            handler.no_cache_html("<h1>Connection cancelled</h1><a href='/admin'>Back</a>",400);return True
        try:
            org=consume_state(path,params.get("state",[""])[0],user["username"])
            if member_role(path,org,user["username"]) not in ("owner","manager"):
                raise ValueError("No longer permitted")
            code=params.get("code",[""])[0]
            if not code: raise ValueError("Missing authorization code")
            token=exchange_code(code)
            pages=meta_connect.list_pages(token)
            if not pages: raise ValueError("No Pages accessible")
            pending=store_pending(path,org,user["username"],token)
            options="".join("<option value='"+html.escape(str(page["id"]),quote=True)+"'>"
                            +html.escape(page["name"])+"</option>" for page in pages)
            org_js=json.dumps(org)
            pending_js=json.dumps(pending)
            page_html="""<!doctype html><html lang='en'><meta charset='utf-8'>
              <title>Choose Facebook Page</title><body style='background:#0B1F55;color:white;font:16px sans-serif;max-width:650px;margin:60px auto'>
              <h1>Choose Facebook Page</h1><p>Select the Page your organisation manages.</p>
              <select id='page'>OPTIONS</select><button id='connect'>Connect Page</button><p id='result'></p>
              <script>document.getElementById('connect').onclick=async()=>{
                const r=await fetch('/api/organisations/'+ORG+'/meta/select',{
                method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({pending:PENDING,page_id:document.getElementById('page').value})});
                if(r.ok)location.href='/admin';
                else document.getElementById('result').textContent='Connection failed; please retry.';
              };</script></body></html>"""
            handler.no_cache_html(page_html.replace("OPTIONS",options)
                                  .replace("ORG",org_js).replace("PENDING",pending_js))
        except Exception:
            handler.no_cache_html("<h1>Meta connection failed</h1><a href='/admin'>Back</a>",400)
        return True
    return False
