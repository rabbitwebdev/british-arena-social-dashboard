import json, os, time, threading, urllib.request, urllib.parse, random, hmac, sqlite3, hashlib, secrets, base64, html
from collections import deque
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from http.cookies import SimpleCookie
import meta_connect
import tenant_meta

ROOT=Path(__file__).resolve().parent
CACHE={'instagram':None,'facebook':None,'updated':None,'error':None,'demo':False}
LOCK=threading.Lock()
DB_LOCK=threading.Lock()
ADMIN_PASSWORD=''
DISPLAY_KEY=''
SESSION_SECRET=''
DB_PATH=None
CONFIG_KEYS={'arenaSettings','arenaEventAnimations','arenaAnimationDetails','arenaCardCopy','arenaBrand','arenaLogo','arenaSlidesV10','arenaEventModeV11','arenaDisplayStyle'}

def db_init():
    global DB_PATH
    DB_PATH=Path(os.getenv('DATABASE_PATH',str(ROOT/'data'/'arena.sqlite3')))
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, version INTEGER NOT NULL)')
        db.execute("INSERT OR IGNORE INTO config(id,data,version) VALUES(1,'{}',0)")
        db.execute('CREATE TABLE IF NOT EXISTS staff (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS publications (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL, version INTEGER NOT NULL, published_at INTEGER NOT NULL, published_by TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS published (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, version INTEGER NOT NULL)')
        db.execute("INSERT OR IGNORE INTO published(id,data,version) VALUES(1,'{}',0)")

    meta_connect.init(DB_PATH)
    tenant_meta.init(DB_PATH)

def db_get():
    with DB_LOCK, sqlite3.connect(DB_PATH) as db:
        data,version=db.execute('SELECT data,version FROM config WHERE id=1').fetchone()
    return {'settings':json.loads(data),'version':version}

def db_update(settings):
    with DB_LOCK, sqlite3.connect(DB_PATH) as db:
        version=db.execute('SELECT version FROM config WHERE id=1').fetchone()[0]+1
        db.execute('UPDATE config SET data=?,version=? WHERE id=1',(json.dumps(settings),version))
    return version

def hash_password(password,salt=None):
    salt=salt or secrets.token_hex(16)
    key=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),260000)
    return salt+':'+key.hex()

def check_password(password,stored):
    try:
        salt,expected=stored.split(':',1)
        return hmac.compare_digest(hash_password(password,salt),stored)
    except (ValueError,TypeError):return False

def session_sign(payload):
    raw=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
    return raw+'.'+hmac.new(SESSION_SECRET.encode(),raw.encode(),hashlib.sha256).hexdigest()

def session_read(token):
    try:
        raw,sig=token.split('.',1)
        expected=hmac.new(SESSION_SECRET.encode(),raw.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected):return None
        p=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
        if int(p.get('exp',0))<=time.time():return None
        return p
    except (ValueError,AttributeError,TypeError):return None

def published_get():
    with DB_LOCK,sqlite3.connect(DB_PATH) as db:
        data,version=db.execute('SELECT data,version FROM published WHERE id=1').fetchone()
    return {'settings':json.loads(data),'version':version}

def publish(username,undo=False):
    with DB_LOCK,sqlite3.connect(DB_PATH) as db:
        if undo:
            rows=db.execute('SELECT data FROM publications ORDER BY id DESC LIMIT 2').fetchall()
            if len(rows)<2:return None
            data=rows[1][0]
        else:data=db.execute('SELECT data FROM config WHERE id=1').fetchone()[0]
        version=db.execute('SELECT version FROM published WHERE id=1').fetchone()[0]+1
        db.execute('UPDATE published SET data=?,version=? WHERE id=1',(data,version))
        db.execute('INSERT INTO publications(data,version,published_at,published_by) VALUES(?,?,?,?)',(data,version,int(time.time()),username))
        return version

NEWSLETTER_EVENTS=deque(maxlen=100)
NEWSLETTER_IDS=deque(maxlen=500)
NEWSLETTER_SEQ=0

def read_env():
    p=ROOT/'.env'
    if p.exists():
        for line in p.read_text().splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k,v=line.split('=',1)
                os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))

def fetch_count(identifier, token, version):
    url=f'https://graph.facebook.com/{version}/{urllib.parse.quote(identifier,safe="")}?'+urllib.parse.urlencode({'fields':'followers_count','access_token':token})
    with urllib.request.urlopen(url,timeout=12) as resp:
        data=json.load(resp)
    return int(data['followers_count'])

def poll_demo():
    ig=int(os.getenv('DEMO_IG_START','2450'))
    fb=int(os.getenv('DEMO_FB_START','1820'))
    rng=random.Random()
    with LOCK:
        CACHE.update(instagram=ig, facebook=fb, updated=int(time.time()), error=None, demo=True)
    last_meta_check=0
    while True:
        time.sleep(20)
        now=time.time()
        if now-last_meta_check>=60:
            last_meta_check=now
            try:
                connected=meta_connect.fetch_counts(DB_PATH)
                if connected:
                    with LOCK:
                        CACHE.update({k:int(v) for k,v in connected.items() if v is not None})
                        CACHE.update(updated=int(now),demo=False,error=None)
                    continue
            except Exception as exc:
                with LOCK:CACHE['error']='Meta: '+exc.__class__.__name__
        with LOCK:
            if not CACHE['demo']:
                # Do not invent follower events when a live connection is temporarily unavailable.
                continue
        ig += rng.choice([0,0,0,1,1,2])
        fb += rng.choice([0,0,0,0,1])
        with LOCK:
            CACHE.update(instagram=ig, facebook=fb, updated=int(time.time()))

def poll_live():
    version=os.getenv('META_API_VERSION','v26.0')
    token=os.getenv('META_ACCESS_TOKEN','')
    ids={'instagram':os.getenv('IG_ACCOUNT_ID',''),'facebook':os.getenv('FB_PAGE_ID','')}
    while True:
        errors=[]
        try:
            connected=meta_connect.fetch_counts(DB_PATH)
            if connected:
                with LOCK:
                    for channel,count in connected.items():
                        if count is not None:CACHE[channel]=int(count)
                    CACHE['updated']=int(time.time())
                    CACHE['error']=None
                time.sleep(60)
                continue
        except Exception as exc:
            errors.append('Meta connection: '+exc.__class__.__name__)
        for channel,identifier in ids.items():
            if token and identifier:
                try:
                    count=fetch_count(identifier,token,version)
                    with LOCK: CACHE[channel]=count
                except Exception as exc: errors.append(f'{channel}: {exc.__class__.__name__}')
        with LOCK:
            if token and any(ids.values()) and not errors:
                CACHE['updated']=int(time.time())
            CACHE['error']='; '.join(errors) if errors else None
        time.sleep(60)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def json_response(self, code, obj):
        body=json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def no_cache_html(self,body,status=200,headers=None):
        encoded=body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Length',str(len(encoded)))
        for key,value in (headers or {}).items():self.send_header(key,value)
        self.end_headers();self.wfile.write(encoded)
    def current_user(self):
        try:
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
            token=cookie['arena_session'].value
            user=session_read(token)
            if user and user.get('role') in ('admin','marketing','viewer'):
                if user.get('username')=='admin':return user
                with DB_LOCK,sqlite3.connect(DB_PATH) as db:
                    row=db.execute('SELECT role FROM staff WHERE username=?',(user['username'],)).fetchone()
                if row and row[0]==user['role']:return user
        except Exception:pass
        return None
    def admin_ok(self):return self.current_user() is not None
    def can_edit(self):return bool(self.current_user() and self.current_user()['role'] in ('admin','marketing'))
    def is_admin(self):return bool(self.current_user() and self.current_user()['role']=='admin')
    def display_ok(self):
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        token=query.get('token',[''])[0]
        return bool(token and hmac.compare_digest(token,DISPLAY_KEY))
    def post_body(self,limit=2_000_000):
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:return None
        if length<1 or length>limit:return None
        return self.rfile.read(length)
    def same_origin(self):
        # Modern browsers supply Fetch Metadata headers for form submissions.
        # A same-origin navigation is safe even when Railway terminates TLS at
        # its proxy and forwards a different Host/scheme to this process.
        fetch_site=self.headers.get('Sec-Fetch-Site','').strip().lower()
        if fetch_site in ('cross-site','same-site'):
            return False
        if fetch_site=='same-origin':
            return True
        origin=self.headers.get('Origin','').strip().rstrip('/')
        trusted=os.environ.get('PUBLIC_ORIGIN','').strip().rstrip('/')
        if origin:
            if trusted and hmac.compare_digest(origin,trusted):return True
            # Railway also supplies the public hostname via X-Forwarded-Host.
            host=self.headers.get('X-Forwarded-Host') or self.headers.get('Host','')
            return origin in ('https://'+host,'http://'+host)
        # HTML forms may omit Origin in some browsers. Allow a same-origin
        # Referer, but never allow a cross-site one.
        referer=self.headers.get('Referer','')
        if referer:
            parsed=urllib.parse.urlsplit(referer)
            ref_origin=parsed.scheme+'://'+parsed.netloc
            if trusted and hmac.compare_digest(ref_origin,trusted):return True
            host=self.headers.get('X-Forwarded-Host') or self.headers.get('Host','')
            return ref_origin in ('https://'+host,'http://'+host)
        return False
    def do_PUT(self):
        if self.path.split('?')[0]!='/api/config':return self.send_error(404)
        if not self.can_edit() or not self.same_origin():return self.json_response(403,{'error':'forbidden'})
        body=self.post_body(3_000_000)
        if body is None:return self.json_response(413,{'error':'payload too large'})
        try:
            payload=json.loads(body)
            settings=payload['settings']
            if not isinstance(settings,dict) or set(settings)-CONFIG_KEYS or not all(isinstance(v,str) for v in settings.values()):raise ValueError()
            version=db_update(settings)
            return self.json_response(200,{'ok':True,'version':version})
        except (ValueError,KeyError,TypeError):return self.json_response(400,{'error':'invalid settings'})
    def do_POST(self):
        if tenant_meta.route(self,DB_PATH,'POST'):return
        path=self.path.split('?')[0]
        if path=='/api/meta/select':
            if not self.is_admin() or not self.same_origin():return self.json_response(403,{'error':'admin only'})
            try:
                data=json.loads(self.post_body(8192) or b'{}')
                token=meta_connect.pending_consume(DB_PATH,data['pending'])
                pages=meta_connect.list_pages(token)
                selected=next((x for x in pages if x['id']==data['page_id']),None)
                if not selected:return self.json_response(400,{'error':'Page not available'})
                meta_connect.save_page(DB_PATH,selected)
                return self.json_response(200,{'ok':True})
            except Exception:
                return self.json_response(400,{'error':'Meta connection failed. Please reconnect and check Page permissions.'})
        if path=='/api/meta/disconnect':
            if not self.is_admin() or not self.same_origin():return self.json_response(403,{'error':'admin only'})
            meta_connect.disconnect(DB_PATH)
            with LOCK:CACHE.update(demo=True,error=None)
            return self.json_response(200,{'ok':True})
        if path=='/login':
            if not self.same_origin():return self.send_error(403)
            body=self.post_body(4096)
            if body is None:return self.send_error(413)
            fields=urllib.parse.parse_qs(body.decode('utf-8','replace'))
            username=fields.get('username',['admin'])[0].strip().lower()
            password=fields.get('password',[''])[0]
            role=None
            if username=='admin' and hmac.compare_digest(password,ADMIN_PASSWORD):role='admin'
            elif username!='admin':
                with DB_LOCK,sqlite3.connect(DB_PATH) as db:
                    row=db.execute('SELECT password_hash,role FROM staff WHERE username=?',(username,)).fetchone()
                if row and check_password(password,row[0]):role=row[1]
            if role is None:return self.no_cache_html('<h1>Incorrect credentials</h1><p><a href="/login">Try again</a></p>',401)
            token=session_sign({'username':username,'role':role,'exp':int(time.time()+12*3600)})
            cookie='arena_session='+token+'; Path=/; HttpOnly; SameSite=Lax'+('; Secure' if self.headers.get('X-Forwarded-Proto')=='https' else '')
            self.send_response(303);self.send_header('Set-Cookie',cookie);self.send_header('Location','/admin');self.end_headers();return
        if path in ('/api/publish','/api/undo','/api/staff'):
            if not self.same_origin():return self.json_response(403,{'error':'forbidden'})
            if path=='/api/staff':
                if not self.is_admin():return self.json_response(403,{'error':'admin only'})
                body=self.post_body(8192)
                try:
                    payload=json.loads(body or b'{}'); username=payload['username'].strip().lower();password=payload['password'];role=payload['role']
                    if not (3<=len(username)<=48 and all(c.isalnum() or c in '._-' for c in username) and username!='admin' and len(password)>=12 and role in ('marketing','viewer')):raise ValueError()
                    with DB_LOCK,sqlite3.connect(DB_PATH) as db:
                        db.execute('INSERT INTO staff(username,password_hash,role,created_at) VALUES(?,?,?,?)',(username,hash_password(password),role,int(time.time())))
                    tenant_meta.init(DB_PATH)
                    return self.json_response(201,{'ok':True,'username':username,'role':role})
                except sqlite3.IntegrityError:return self.json_response(409,{'error':'username exists'})
                except (ValueError,TypeError,KeyError):return self.json_response(400,{'error':'invalid user'})
            if not self.can_edit():return self.json_response(403,{'error':'edit permission required'})
            version=publish(self.current_user()['username'],undo=path=='/api/undo')
            if version is None:return self.json_response(409,{'error':'no earlier publication to restore'})
            return self.json_response(200,{'ok':True,'version':version})
        if path=='/logout':
            self.send_response(303);self.send_header('Set-Cookie','arena_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax');self.send_header('Location','/login');self.end_headers();return

        global NEWSLETTER_SEQ
        if self.path.split('?')[0]!='/api/newsletter-event':
            return self.send_error(404)
        secret=os.getenv('NEWSLETTER_WEBHOOK_TOKEN','')
        authorization=self.headers.get('Authorization','')
        if not secret or not hmac.compare_digest(authorization, 'Bearer '+secret):
            return self.json_response(401,{'error':'unauthorized'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<2 or length>4096: return self.json_response(413,{'error':'invalid payload size'})
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict) or payload.get('event')!='newsletter_signup':
                return self.json_response(400,{'error':'expected newsletter_signup event'})
            event_id=payload.get('id')
            if not isinstance(event_id,str) or not 1<=len(event_id)<=160:
                return self.json_response(400,{'error':'unique event id required'})
            with LOCK:
                if event_id in NEWSLETTER_IDS: return self.json_response(200,{'ok':True,'duplicate':True})
                NEWSLETTER_IDS.append(event_id)
                NEWSLETTER_SEQ+=1
                NEWSLETTER_EVENTS.append({'seq':NEWSLETTER_SEQ,'time':int(time.time())})
            return self.json_response(200,{'ok':True})
        except (ValueError,UnicodeDecodeError,TypeError):
            return self.json_response(400,{'error':'invalid json'})
    def do_GET(self):
        if tenant_meta.route(self,DB_PATH,'GET'):return
        path=self.path.split('?')[0]
        if path=='/api/meta/status':
            if not self.admin_ok():return self.json_response(403,{'error':'sign in required'})
            return self.json_response(200,meta_connect.status(DB_PATH))
        if path=='/api/meta/connect':
            if not self.is_admin():return self.send_error(403)
            if not meta_connect.configured():return self.no_cache_html('<h1>Meta setup required</h1><p>Set META_APP_ID, META_APP_SECRET, META_TOKEN_ENCRYPTION_KEY and PUBLIC_ORIGIN in Railway.</p><a href="/admin">Return to dashboard</a>',503)
            self.send_response(303);self.send_header('Location',meta_connect.authorize_url('admin'));self.send_header('Cache-Control','no-store');self.end_headers();return
        if path=='/meta/callback':
            if not self.is_admin():return self.send_error(403)
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            state=params.get('state',[''])[0]
            code=params.get('code',[''])[0]
            if not code or not meta_connect.verify_state(state,'admin'):
                return self.no_cache_html('<h1>Meta authorisation cancelled or expired</h1><p><a href="/admin">Return to dashboard</a></p>',400)
            try:
                token=meta_connect.code_to_token(code)
                pages=meta_connect.list_pages(token)
                if not pages:return self.no_cache_html('<h1>No Facebook Pages available</h1><p>Use an account with access to the British Arena Page and allow the requested permissions.</p><a href="/admin">Back</a>',400)
                pending=meta_connect.pending_save(DB_PATH,token)
                options=''.join('<option value="'+html.escape(x['id'],quote=True)+'">'+html.escape(x['name'])+(' (Instagram: '+html.escape(x['instagram_username'] or 'linked')+')' if x['instagram_id'] else ' (no linked Instagram)')+'</option>' for x in pages)
                script="""<script>async function connectPage(){const r=await fetch('/api/meta/select',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({pending:NONCE_VALUE,page_id:document.getElementById('page').value})});if(r.ok)location.href='/admin';else document.getElementById('result').textContent=(await r.json()).error}</script>""".replace('NONCE_VALUE',json.dumps(pending))
                page='<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Choose Facebook Page</title><style>body{background:#0B1F55;color:#fff;font:16px Arial;padding:35px;max-width:600px;margin:auto}select,button{padding:13px;width:100%;margin-top:15px;border-radius:0}</style><h1>Connect British Arena</h1><p>Choose the Facebook Page to connect to this dashboard.</p><select id="page">'+options+'</select><button onclick="connectPage()">Connect selected Page</button><p id="result"></p>'+script+'</html>'
                return self.no_cache_html(page)
            except Exception:
                return self.no_cache_html('<h1>Could not connect Meta</h1><p>Check the Meta app permissions and callback URL.</p><a href="/admin">Back</a>',502)
        if path=='/login':
            return self.no_cache_html("""<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Arena Admin</title><style>body{font:16px Arial;background:#0B1F55;color:white;display:grid;place-items:center;min-height:90vh}form{border:1px solid #789;padding:30px;min-width:280px}input,button{display:block;padding:14px;width:100%;box-sizing:border-box;margin-top:16px;border-radius:0}button{background:#fff;color:#0B1F55;font-weight:bold}</style><form action="/login" method="post"><h1>British Arena</h1><p>Cloud dashboard sign in</p><label>Username<input name="username" value="admin" required></label><label>Password<input name="password" type="password" required autofocus></label><button>Sign in</button></form></html>""")
        if path=='/':
            self.send_response(302);self.send_header('Location','/login');self.end_headers();return
        if path=='/admin':
            if not self.admin_ok():
                self.send_response(302);self.send_header('Location','/login');self.end_headers();return
            return self.no_cache_html((ROOT/'index.html').read_text())
        if path=='/display':
            if not self.display_ok():return self.send_error(403)
            return self.no_cache_html((ROOT/'index.html').read_text())
        if path in ('/api/config','/api/player/sync'):
            if self.admin_ok() and path=='/api/config':return self.json_response(200,db_get())
            if not self.display_ok():return self.json_response(403,{'error':'forbidden'})
            return self.json_response(200,published_get())
        if path=='/api/me':
            return self.json_response(200,{'user':self.current_user()})
        if path=='/api/publications':
            if not self.admin_ok():return self.json_response(403,{'error':'forbidden'})
            with DB_LOCK,sqlite3.connect(DB_PATH) as db:
                rows=db.execute('SELECT id,version,published_at,published_by FROM publications ORDER BY id DESC LIMIT 30').fetchall()
            return self.json_response(200,{'history':[{'id':r[0],'version':r[1],'time':r[2],'by':r[3]} for r in rows]})
        if path=='/api/staff':
            if not self.is_admin():return self.json_response(403,{'error':'admin only'})
            with DB_LOCK,sqlite3.connect(DB_PATH) as db:
                rows=db.execute('SELECT username,role,created_at FROM staff ORDER BY username').fetchall()
            return self.json_response(200,{'staff':[{'username':r[0],'role':r[1],'created':r[2]} for r in rows]})
        if path=='/api/counts':
            with LOCK: payload=dict(CACHE)
            return self.json_response(200,payload)
        if path=='/api/newsletter-events':
            try: after=max(0,int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('after',['0'])[0]))
            except ValueError: after=0
            with LOCK:
                latest=NEWSLETTER_SEQ
                events=[e for e in NEWSLETTER_EVENTS if e['seq']>after]
            return self.json_response(200,{'latest':latest,'events':events})
        if path not in ():
            return self.send_error(404)
        return super().do_GET()

if __name__=='__main__':
    read_env()
    ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','')
    DISPLAY_KEY=os.getenv('DISPLAY_KEY','')
    SESSION_SECRET=os.getenv('SESSION_SECRET','')
    if not all((ADMIN_PASSWORD,DISPLAY_KEY,SESSION_SECRET)):
        raise SystemExit('Configure ADMIN_PASSWORD, DISPLAY_KEY and SESSION_SECRET in .env')
    db_init()
    demo=os.getenv('DEMO_MODE','true').strip().lower() in ('1','true','yes','on')
    threading.Thread(target=poll_demo if demo else poll_live,daemon=True).start()
    print(f'Arena dashboard: http://localhost:8765 ({"DEMO" if demo else "LIVE"} mode)',flush=True)
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8765'))),Handler).serve_forever()
