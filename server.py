import json, os, time, threading, urllib.request, urllib.parse, random, hmac, sqlite3, hashlib, secrets, base64, html
from collections import deque
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from http.cookies import SimpleCookie

ROOT=Path(__file__).resolve().parent
CACHE={'instagram':None,'facebook':None,'updated':None,'error':None,'demo':False}
LOCK=threading.Lock()
DB_LOCK=threading.Lock()
ADMIN_PASSWORD=''
DISPLAY_KEY=''
SESSION_SECRET=''
DB_PATH=None
CONFIG_KEYS={'arenaSettings','arenaEventAnimations','arenaAnimationDetails','arenaCardCopy','arenaBrand','arenaLogo','arenaSlidesV10','arenaEventModeV11'}

def db_init():
    global DB_PATH
    DB_PATH=Path(os.getenv('DATABASE_PATH',str(ROOT/'data'/'arena.sqlite3')))
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, version INTEGER NOT NULL)')
        db.execute("INSERT OR IGNORE INTO config(id,data,version) VALUES(1,'{}',0)")

def db_get():
    with DB_LOCK, sqlite3.connect(DB_PATH) as db:
        data,version=db.execute('SELECT data,version FROM config WHERE id=1').fetchone()
    return {'settings':json.loads(data),'version':version}

def db_update(settings):
    with DB_LOCK, sqlite3.connect(DB_PATH) as db:
        version=db.execute('SELECT version FROM config WHERE id=1').fetchone()[0]+1
        db.execute('UPDATE config SET data=?,version=? WHERE id=1',(json.dumps(settings),version))
    return version

def sign_session(expiry):
    msg=str(expiry).encode(); signature=hmac.new(SESSION_SECRET.encode(),msg,hashlib.sha256).hexdigest()
    return str(expiry)+'.'+signature

def is_session(value):
    try:
        expiry,signature=value.split('.',1)
        return int(expiry)>time.time() and hmac.compare_digest(sign_session(int(expiry)),value)
    except (ValueError,AttributeError):return False

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
    while True:
        time.sleep(20)
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
    def admin_ok(self):
        try:
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
            return 'arena_session' in cookie and is_session(cookie['arena_session'].value)
        except Exception:return False
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
        origin=self.headers.get('Origin')
        if not origin:return True
        host=self.headers.get('Host','')
        return origin in ('https://'+host,'http://'+host)
    def do_PUT(self):
        if self.path.split('?')[0]!='/api/config':return self.send_error(404)
        if not self.admin_ok() or not self.same_origin():return self.json_response(403,{'error':'forbidden'})
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
        path=self.path.split('?')[0]
        if path=='/login':
            if not self.same_origin():return self.send_error(403)
            body=self.post_body(4096)
            if body is None:return self.send_error(413)
            fields=urllib.parse.parse_qs(body.decode('utf-8','replace'))
            if not hmac.compare_digest(fields.get('password',[''])[0],ADMIN_PASSWORD):
                return self.no_cache_html('<h1>Incorrect password</h1><p><a href="/login">Try again</a></p>',401)
            token=sign_session(int(time.time()+12*3600))
            cookie='arena_session='+token+'; Path=/; HttpOnly; SameSite=Lax'+('; Secure' if self.headers.get('X-Forwarded-Proto')=='https' else '')
            self.send_response(303);self.send_header('Set-Cookie',cookie);self.send_header('Location','/admin');self.end_headers();return
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
        path=self.path.split('?')[0]
        if path=='/login':
            return self.no_cache_html("""<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Arena Admin</title><style>body{font:16px Arial;background:#0B1F55;color:white;display:grid;place-items:center;min-height:90vh}form{border:1px solid #789;padding:30px;min-width:280px}input,button{display:block;padding:14px;width:100%;box-sizing:border-box;margin-top:16px;border-radius:0}button{background:#fff;color:#0B1F55;font-weight:bold}</style><form action="/login" method="post"><h1>British Arena</h1><p>Cloud dashboard sign in</p><label>Admin password<input name="password" type="password" required autofocus></label><button>Sign in</button></form></html>""")
        if path=='/':
            self.send_response(302);self.send_header('Location','/login');self.end_headers();return
        if path=='/admin':
            if not self.admin_ok():
                self.send_response(302);self.send_header('Location','/login');self.end_headers();return
            return self.no_cache_html((ROOT/'index.html').read_text())
        if path=='/display':
            if not self.display_ok():return self.send_error(403)
            return self.no_cache_html((ROOT/'index.html').read_text())
        if path=='/api/config':
            if not (self.admin_ok() or self.display_ok()):return self.json_response(403,{'error':'forbidden'})
            return self.json_response(200,db_get())
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
