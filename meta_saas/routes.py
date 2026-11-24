"""Customer-facing Meta OAuth blueprint. No customer API keys required."""
import os
import secrets
import time
from urllib.parse import urlencode
import requests
from flask import Blueprint, abort, jsonify, redirect, request
from .store import Store

SCOPES = ('pages_show_list', 'pages_read_engagement', 'instagram_basic')

def register_meta_routes(app, current_identity, *, store=None, client=None):
    """Register routes. current_identity() MUST return authenticated tenant/user IDs.

    Use the dashboard's actual session/auth middleware. Return None if not logged in.
    This callback is intentionally required, rather than guessing existing auth.
    """
    meta_app_id = os.environ['META_APP_ID']
    meta_app_secret = os.environ['META_APP_SECRET']
    origin = os.environ['PUBLIC_ORIGIN'].rstrip('/')
    if not origin.startswith('https://'):
        raise ValueError('PUBLIC_ORIGIN must use HTTPS')
    version = os.environ.get('META_GRAPH_VERSION', 'v26.0')
    store = store or Store()
    client = client or requests.Session()
    callback_uri = origin + '/api/integrations/meta/callback'
    bp = Blueprint('meta_saas', __name__, url_prefix='/api/integrations/meta')

    def identity():
        value = current_identity()
        if not value or not isinstance(value, (tuple, list)) or len(value) != 2 or not all(value):
            abort(401)
        return str(value[0]), str(value[1])

    def graph_get(path, token, **params):
        params['access_token'] = token
        r = client.get(f'https://graph.facebook.com/{version}/{path}', params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    @bp.get('/connect')
    def connect():
        tenant_id, user_id = identity()
        state = secrets.token_urlsafe(40)
        store.save_state(state, tenant_id, user_id)
        query = urlencode({'client_id':meta_app_id, 'redirect_uri':callback_uri,
                           'scope':','.join(SCOPES), 'response_type':'code', 'state':state})
        return redirect('https://www.facebook.com/' + version + '/dialog/oauth?' + query, code=302)

    @bp.get('/callback')
    def callback():
        tenant_id, user_id = identity()
        state = request.args.get('state', '')
        if not state or not store.consume_state(state, tenant_id, user_id):
            abort(403, 'OAuth state expired or does not belong to this account')
        if request.args.get('error'):
            return jsonify({'error':'authorization_declined'}), 400
        code = request.args.get('code', '')
        if not code:
            return jsonify({'error':'missing_code'}), 400
        try:
            r = client.get(f'https://graph.facebook.com/{version}/oauth/access_token',
                params={'client_id':meta_app_id,'client_secret':meta_app_secret,
                        'redirect_uri':callback_uri,'code':code}, timeout=15)
            r.raise_for_status()
            short_token = r.json()['access_token']
            r = client.get(f'https://graph.facebook.com/{version}/oauth/access_token',
                params={'grant_type':'fb_exchange_token','client_id':meta_app_id,
                        'client_secret':meta_app_secret,'fb_exchange_token':short_token}, timeout=15)
            r.raise_for_status()
            payload = r.json()
            user_token = payload['access_token']
            expires_at = int(time.time()) + int(payload['expires_in']) if payload.get('expires_in') else None
            # Obtain the Pages this person actually granted. No tenant can access another tenant's tokens.
            page_list = graph_get('me/accounts', user_token,
                fields='id,name,access_token,instagram_business_account', limit=100)
            pages = page_list.get('data', [])
            # Pagination may be needed for accounts managing >100 Pages; never trust a paging URL with token in it.
            if page_list.get('paging', {}).get('next'):
                return jsonify({'error':'too_many_pages','message':'More than 100 Pages; update pagination first'}), 422
            for page in pages:
                if page.get('access_token'):
                    store.upsert_page(tenant_id, user_id, page, user_token, expires_at)
            return redirect(origin + '/admin?meta_connected=1', code=302)
        except (requests.RequestException, KeyError, ValueError):
            app.logger.exception('Meta OAuth failed (no tokens logged)')
            return jsonify({'error':'meta_connection_failed'}), 502

    @bp.get('/connections')
    def connections():
        tenant_id, _ = identity()
        return jsonify({'connections':store.public_pages(tenant_id)})

    @bp.delete('/connections/<page_id>')
    def disconnect(page_id):
        tenant_id, _ = identity()
        # MUST enable the parent app's same-origin CSRF protection on this mutation.
        if request.headers.get('Origin') != origin:
            abort(403)
        return jsonify({'disconnected':store.disconnect(tenant_id, page_id)})

    app.register_blueprint(bp)
    return bp
