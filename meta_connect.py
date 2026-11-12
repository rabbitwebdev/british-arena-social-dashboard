"""Server-only Meta OAuth and page connection. No Meta tokens in published config or browser storage."""
import base64, hashlib, hmac, json, os, secrets, sqlite3, time, urllib.parse, urllib.request, urllib.error
from cryptography.fernet import Fernet, InvalidToken

GRAPH_VERSION=os.getenv('META_API_VERSION','v26.0')
PERMISSIONS='pages_show_list,pages_read_engagement,instagram_basic'  # optional insights are a separate review step

def configured():
    return bool(os.getenv('META_APP_ID') and os.getenv('META_APP_SECRET') and os.getenv('META_TOKEN_ENCRYPTION_KEY') and os.getenv('PUBLIC_ORIGIN'))

def cipher():
    return Fernet(os.environ['META_TOKEN_ENCRYPTION_KEY'].encode())

def init(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS meta_connections (page_id TEXT PRIMARY KEY, page_name TEXT NOT NULL, instagram_id TEXT, instagram_username TEXT, secret TEXT NOT NULL, connected_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
        db.execute('CREATE TABLE IF NOT EXISTS meta_oauth_pending (nonce TEXT PRIMARY KEY, token TEXT NOT NULL, expires INTEGER NOT NULL)')

def graph(path,params,token=None):
    params=dict(params)
    if token: params['access_token']=token
    url='https://graph.facebook.com/'+GRAPH_VERSION+'/'+path.lstrip('/')+'?'+urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'BritishArenaSocialDashboard/15'}),timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        # Don't print access tokens or returned raw details
        raise ValueError('Meta API rejected this request (HTTP '+str(exc.code)+'). Check approved permissions and connected Page access.') from None

def callback_url():
    return os.environ['PUBLIC_ORIGIN'].rstrip('/')+'/meta/callback'

def state_token(user):
    nonce=secrets.token_urlsafe(24)
    issued=int(time.time())
    raw=json.dumps({'n':nonce,'u':user,'t':issued},separators=(',',':')).encode()
    content=base64.urlsafe_b64encode(raw).decode().rstrip('=')
    sig=hmac.new(os.environ['SESSION_SECRET'].encode(),content.encode(),hashlib.sha256).hexdigest()
    return content+'.'+sig

def verify_state(state,user):
    try:
        content,sig=state.split('.',1)
        expected=hmac.new(os.environ['SESSION_SECRET'].encode(),content.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected):return False
        data=json.loads(base64.urlsafe_b64decode(content+'='*(-len(content)%4)))
        return data['u']==user and 0<=time.time()-int(data['t'])<=600
    except (ValueError,KeyError,TypeError):return False

def authorize_url(user):
    return 'https://www.facebook.com/'+GRAPH_VERSION+'/dialog/oauth?'+urllib.parse.urlencode({'client_id':os.environ['META_APP_ID'],'redirect_uri':callback_url(),'state':state_token(user),'scope':PERMISSIONS,'response_type':'code'})

def code_to_token(code):
    result=graph('oauth/access_token',{'client_id':os.environ['META_APP_ID'],'client_secret':os.environ['META_APP_SECRET'],'redirect_uri':callback_url(),'code':code})
    short=result['access_token']
    try:
        long=graph('oauth/access_token',{'grant_type':'fb_exchange_token','client_id':os.environ['META_APP_ID'],'client_secret':os.environ['META_APP_SECRET'],'fb_exchange_token':short})
        return long['access_token']
    except (KeyError,ValueError):return short

def list_pages(token):
    result=graph('me/accounts',{'fields':'id,name,access_token,instagram_business_account{id,username},connected_instagram_account{id,username}','limit':'100'},token)
    pages=result.get('data',[])
    # request first 100 and let staff see clear feedback if not present
    return [{'id':x['id'],'name':x['name'],'instagram_id':(x.get('instagram_business_account') or x.get('connected_instagram_account') or {}).get('id'),'instagram_username':(x.get('instagram_business_account') or x.get('connected_instagram_account') or {}).get('username'),'access_token':x.get('access_token')} for x in pages]

def pending_save(db_path,token):
    nonce=secrets.token_urlsafe(20)
    with sqlite3.connect(db_path) as db:
        db.execute('DELETE FROM meta_oauth_pending WHERE expires<?',(int(time.time()),))
        db.execute('INSERT INTO meta_oauth_pending(nonce,token,expires) VALUES(?,?,?)',(nonce,cipher().encrypt(token.encode()).decode(),int(time.time())+600))
    return nonce

def pending_consume(db_path,nonce):
    with sqlite3.connect(db_path) as db:
        row=db.execute('SELECT token,expires FROM meta_oauth_pending WHERE nonce=?',(nonce,)).fetchone()
        db.execute('DELETE FROM meta_oauth_pending WHERE nonce=?',(nonce,))
    if not row or row[1]<time.time():raise ValueError('Connection session expired. Try again.')
    return cipher().decrypt(row[0].encode()).decode()

def save_page(db_path,page):
    if not page.get('access_token'):raise ValueError('Meta did not grant Page access. Reconnect and select the Page.')
    with sqlite3.connect(db_path) as db:
        db.execute('UPDATE meta_connections SET active=0')
        db.execute('INSERT INTO meta_connections(page_id,page_name,instagram_id,instagram_username,secret,connected_at,active) VALUES(?,?,?,?,?,?,1) ON CONFLICT(page_id) DO UPDATE SET page_name=excluded.page_name,instagram_id=excluded.instagram_id,instagram_username=excluded.instagram_username,secret=excluded.secret,connected_at=excluded.connected_at,active=1',(page['id'],page['name'],page.get('instagram_id'),page.get('instagram_username'),cipher().encrypt(page['access_token'].encode()).decode(),int(time.time())))

def status(db_path):
    with sqlite3.connect(db_path) as db:
        row=db.execute('SELECT page_id,page_name,instagram_id,instagram_username,connected_at FROM meta_connections WHERE active=1 LIMIT 1').fetchone()
    return {'configured':configured(),'connected':bool(row),'page':{'id':row[0],'name':row[1],'instagram_connected':bool(row[2]),'instagram_username':row[3],'connected_at':row[4]} if row else None,'requiresMetaApp':True}

def disconnect(db_path):
    with sqlite3.connect(db_path) as db:db.execute('DELETE FROM meta_connections')

def fetch_counts(db_path):
    with sqlite3.connect(db_path) as db:
        row=db.execute('SELECT page_id,instagram_id,secret FROM meta_connections WHERE active=1 LIMIT 1').fetchone()
    if not row:return None
    page_id,ig_id,encrypted=row
    token=cipher().decrypt(encrypted.encode()).decode()
    result={'facebook':None,'instagram':None}
    page=graph(page_id,{'fields':'followers_count,fan_count'},token)
    result['facebook']=page.get('followers_count',page.get('fan_count'))
    if ig_id:
        instagram=graph(ig_id,{'fields':'followers_count'},token)
        result['instagram']=instagram.get('followers_count')
    return result
