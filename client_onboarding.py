"""Invite-only company onboarding. No public registration or shared passwords.

Invites are bound to an organisation and an email. Tokens are stored only as
SHA-256 digests, expire after 48 hours, and are atomically consumed once.
"""
import hashlib, hmac, json, os, re, secrets, smtplib, sqlite3, ssl, time
from contextlib import closing
from email.message import EmailMessage
from urllib.parse import quote

import tenant_meta

EMAIL_RE=re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
VALID_ROLES=("owner","manager","viewer")
LIFETIME=48*3600

def db(path):
    conn=sqlite3.connect(str(path),timeout=20)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init(path):
    with closing(db(path)) as conn:
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS client_invitations(
                    token_hash TEXT PRIMARY KEY, organisation_id TEXT NOT NULL,
                    email TEXT NOT NULL, role TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL, used_at INTEGER,
                    FOREIGN KEY(organisation_id) REFERENCES organisations(id));
                CREATE INDEX IF NOT EXISTS invitations_org_idx
                    ON client_invitations(organisation_id,created_at);
                CREATE TABLE IF NOT EXISTS client_login_attempts(
                    username TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
                    reset_at INTEGER NOT NULL);
            """)

def normal_email(value):
    if not isinstance(value,str):raise ValueError("Invalid email address")
    address=value.strip().lower()
    if len(address)>254 or len(address)<6 or not EMAIL_RE.fullmatch(address) or ".." in address:
        raise ValueError("Invalid email address")
    return address

def make_invite(path,org,email,role,inviter):
    address=normal_email(email)
    if role not in VALID_ROLES:raise ValueError("Invalid company role")
    if not tenant_meta.member_role(path,org,inviter)=="owner":
        raise ValueError("Only company owners can invite users")
    now=int(time.time())
    raw=secrets.token_urlsafe(40)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    with closing(db(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Do not allow sign-up links to reset or take over existing users.
        if conn.execute("SELECT 1 FROM staff WHERE username=?",(address,)).fetchone():
            conn.rollback();raise ValueError("This email already has a dashboard login. Ask the platform administrator to assign membership.")
        recent=conn.execute("SELECT COUNT(*) FROM client_invitations WHERE organisation_id=? AND created_at>?",
                            (org,now-3600)).fetchone()[0]
        if recent>=10:
            conn.rollback();raise ValueError("Invitation limit reached. Try again later.")
        conn.execute("UPDATE client_invitations SET used_at=? WHERE organisation_id=? AND email=? AND used_at IS NULL",
                     (now,org,address))
        conn.execute("""INSERT INTO client_invitations
          (token_hash,organisation_id,email,role,created_by,created_at,expires_at)
          VALUES (?,?,?,?,?,?,?)""",(digest,org,address,role,inviter,now,now+LIFETIME))
        conn.commit()
    return raw

def lookup(path,raw):
    if not isinstance(raw,str) or not 40<=len(raw)<=100:
        return None
    digest=hashlib.sha256(raw.encode()).hexdigest()
    with closing(db(path)) as conn:
        row=conn.execute("""SELECT i.organisation_id,o.name,i.email,i.role,i.expires_at,i.used_at
                    FROM client_invitations i JOIN organisations o ON o.id=i.organisation_id
                    WHERE i.token_hash=?""",(digest,)).fetchone()
    if not row or row[4]<time.time() or row[5] is not None:return None
    return {"organisation_id":row[0],"organisation":row[1],"email":row[2],"role":row[3],"expires_at":row[4]}

def accept(path,raw,password,hash_password):
    if not isinstance(password,str) or not 12<=len(password)<=256 or password.isspace():
        raise ValueError("Choose a password with at least 12 characters")
    invite=lookup(path,raw)
    if not invite:raise ValueError("This invitation is invalid, expired, or already used")
    hashed=hash_password(password)
    now=int(time.time())
    with closing(db(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        digest=hashlib.sha256(raw.encode()).hexdigest()
        row=conn.execute("""SELECT organisation_id,email,role FROM client_invitations
                      WHERE token_hash=? AND used_at IS NULL AND expires_at>=?""",
                      (digest,now)).fetchone()
        if not row:
            conn.rollback();raise ValueError("Invitation already used or expired")
        org,email,role=row
        try:
            conn.execute("INSERT INTO staff(username,password_hash,role,created_at) VALUES (?,?,?,?)",
                         (email,hashed,"viewer" if role=="viewer" else "marketing",now))
            conn.execute("INSERT INTO organisation_members(organisation_id,username,role) VALUES (?,?,?)",
                         (org,email,role))
            conn.execute("UPDATE client_invitations SET used_at=? WHERE token_hash=?",(now,digest))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback();raise ValueError("This email already has a dashboard account")
    return {"username":email,"organisation_id":org,"role":role}

def invitation_url(token):
    origin=os.environ["PUBLIC_ORIGIN"].rstrip("/")
    return origin+"/accept-invite#token="+quote(token,safe="")

def deliver(email,url,org_name):
    """SMTP is optional; never pretend that an unconfigured message was sent."""
    host=os.environ.get("INVITE_SMTP_HOST","").strip()
    sender=os.environ.get("INVITE_FROM_EMAIL","").strip()
    if not host or not sender:return False
    port=int(os.environ.get("INVITE_SMTP_PORT","587"))
    if port not in (465,587):raise ValueError("Unsupported SMTP port")
    msg=EmailMessage()
    msg["From"]=sender
    msg["To"]=email
    msg["Subject"]="Your Social Dashboard invitation"
    msg.set_content("You've been invited to manage "+org_name+" on Social Dashboard.\n\n"
                    "Create your secure login using this link (expires in 48 hours):\n"
                    +url+"\n\nIf you weren't expecting this invitation, ignore this email.")
    password=os.environ.get("INVITE_SMTP_PASSWORD","")
    username=os.environ.get("INVITE_SMTP_USERNAME","")
    if port==465:
        with smtplib.SMTP_SSL(host,port,timeout=10,context=ssl.create_default_context()) as smtp:
            if username and password:smtp.login(username,password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host,port,timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if username and password:smtp.login(username,password)
            smtp.send_message(msg)
    return True

def can_login(path,username):
    with closing(db(path)) as conn:
        row=conn.execute("SELECT attempts,reset_at FROM client_login_attempts WHERE username=?",
                         (username,)).fetchone()
    return not row or row[1]<=time.time() or row[0]<7

def login_result(path,username,success):
    with closing(db(path)) as conn:
        with conn:
            if success:
                conn.execute("DELETE FROM client_login_attempts WHERE username=?",(username,))
                return
            now=int(time.time())
            conn.execute("""INSERT INTO client_login_attempts VALUES (?,?,?)
                ON CONFLICT(username) DO UPDATE SET
                attempts=CASE WHEN reset_at<=? THEN 1 ELSE attempts+1 END,
                reset_at=CASE WHEN reset_at<=? THEN excluded.reset_at ELSE reset_at END""",
                (username,1,now+900,now,now))

def route(handler,path,method,hash_password=None,sign_session=None):
    parsed=handler.path.split("?",1)[0]
    if parsed=="/api/invitations/inspect" and method=="POST":
        if not handler.same_origin():return handler.json_response(403,{"error":"invalid origin"}) or True
        try:
            raw=json.loads(handler.post_body(4096) or b"{}").get("token")
            invite=lookup(path,raw)
            if not invite:handler.json_response(410,{"error":"Invitation invalid, expired or already used"})
            else:handler.json_response(200,invite)
        except (ValueError,TypeError):handler.json_response(400,{"error":"invalid request"})
        return True
    if parsed=="/api/invitations/accept" and method=="POST":
        if not handler.same_origin():
            handler.json_response(403,{"error":"invalid origin"});return True
        try:
            payload=json.loads(handler.post_body(4096) or b"{}")
            result=accept(path,payload.get("token"),payload.get("password"),hash_password)
            token=sign_session(result["username"],"viewer" if result["role"]=="viewer" else "marketing")
            handler.send_response(303)
            handler.send_header("Set-Cookie",token)
            handler.send_header("Location","/onboarding/"+result["organisation_id"])
            handler.send_header("Cache-Control","no-store")
            handler.end_headers()
        except (ValueError,TypeError,sqlite3.IntegrityError):
            handler.json_response(400,{"error":"Invalid or expired invitation, or password too short"})
        return True
    parts=parsed.strip("/").split("/")
    if len(parts)==4 and parts[:2]==["api","organisations"] and parts[3]=="invitations":
        user=handler.current_user()
        if not user:
            handler.json_response(401,{"error":"sign in required"});return True
        if tenant_meta.member_role(path,parts[2],user["username"])!="owner":
            handler.json_response(403,{"error":"company owner only"});return True
        if method=="POST":
            if not handler.same_origin():
                handler.json_response(403,{"error":"invalid origin"});return True
            try:
                payload=json.loads(handler.post_body(4096) or b"{}")
                email=normal_email(payload.get("email"))
                raw=make_invite(path,parts[2],email,payload.get("role","owner"),user["username"])
                url=invitation_url(raw)
                with closing(db(path)) as conn:
                    org=conn.execute("SELECT name FROM organisations WHERE id=?",(parts[2],)).fetchone()[0]
                try:sent=deliver(email,url,org)
                except (OSError,ValueError,smtplib.SMTPException):
                    sent=False
                handler.json_response(201,{"ok":True,"sent":sent,"email":email,
                    "invite_url":None if sent else url,"expires_in_hours":48})
            except (ValueError,TypeError,KeyError,sqlite3.IntegrityError):
                handler.json_response(400,{"error":"Unable to create invitation. Check email, company and rate limits."})
            return True
        handler.json_response(405,{"error":"method not allowed"});return True
    return False
