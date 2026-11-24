"""Encrypted tenant-scoped SQLite store. Uses a dedicated DB under /data."""
import base64
import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from cryptography.fernet import Fernet

class Store:
    def __init__(self, db_path=None, encryption_key=None):
        self.db_path = db_path or os.environ.get('META_CONNECTION_DB', '/data/meta-connections.sqlite3')
        encryption_key = encryption_key or os.environ['META_TOKEN_ENCRYPTION_KEY']
        if len(encryption_key) < 32:
            raise ValueError('META_TOKEN_ENCRYPTION_KEY must be at least 32 characters')
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(encryption_key.encode()).digest()))
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS meta_oauth_states (
              state_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
              user_id TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta_connections (
              tenant_id TEXT NOT NULL, page_id TEXT NOT NULL,
              page_name TEXT NOT NULL, page_token BLOB NOT NULL,
              instagram_id TEXT, user_token BLOB,
              token_expires_at INTEGER, connected_by TEXT NOT NULL,
              connected_at INTEGER NOT NULL, last_checked_at INTEGER,
              connection_status TEXT NOT NULL DEFAULT 'connected',
              PRIMARY KEY (tenant_id, page_id)
            );
            CREATE INDEX IF NOT EXISTS idx_meta_connections_tenant ON meta_connections(tenant_id);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def encrypt(self, value):
        return self.cipher.encrypt(value.encode('utf-8'))

    def decrypt(self, value):
        return self.cipher.decrypt(value).decode('utf-8')

    def save_state(self, state, tenant_id, user_id):
        digest = hashlib.sha256(state.encode()).hexdigest()
        with self.connection() as db:
            db.execute('DELETE FROM meta_oauth_states WHERE created_at < ?', (int(time.time()) - 600,))
            db.execute('INSERT INTO meta_oauth_states VALUES (?,?,?,?)',
                       (digest, tenant_id, user_id, int(time.time())))

    def consume_state(self, state, tenant_id, user_id):
        digest = hashlib.sha256(state.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute('SELECT * FROM meta_oauth_states WHERE state_hash = ?', (digest,)).fetchone()
            db.execute('DELETE FROM meta_oauth_states WHERE state_hash = ?', (digest,))
        return bool(row and row['tenant_id'] == tenant_id and row['user_id'] == user_id
                    and row['created_at'] >= int(time.time()) - 600)

    def upsert_page(self, tenant_id, user_id, page, user_token, expiry):
        with self.connection() as db:
            db.execute('''INSERT INTO meta_connections
              (tenant_id,page_id,page_name,page_token,instagram_id,user_token,token_expires_at,
              connected_by,connected_at,connection_status)
              VALUES (?,?,?,?,?,?,?,?,?,'connected')
              ON CONFLICT (tenant_id,page_id) DO UPDATE SET
              page_name=excluded.page_name,page_token=excluded.page_token,
              instagram_id=excluded.instagram_id,user_token=excluded.user_token,
              token_expires_at=excluded.token_expires_at,connected_by=excluded.connected_by,
              connected_at=excluded.connected_at,connection_status='connected' ''',
              (tenant_id, str(page['id']), page['name'], self.encrypt(page['access_token']),
               (page.get('instagram_business_account') or {}).get('id'),
               self.encrypt(user_token), expiry, user_id, int(time.time())))

    def public_pages(self, tenant_id):
        with self.connection() as db:
            rows = db.execute('''SELECT page_id,page_name,instagram_id,connected_at,
                               connection_status,last_checked_at FROM meta_connections
                               WHERE tenant_id=? ORDER BY page_name''', (tenant_id,)).fetchall()
            return [dict(row) for row in rows]

    def page_token(self, tenant_id, page_id):
        with self.connection() as db:
            row = db.execute('SELECT page_token FROM meta_connections WHERE tenant_id=? AND page_id=?',
                             (tenant_id, page_id)).fetchone()
            return self.decrypt(row['page_token']) if row else None

    def disconnect(self, tenant_id, page_id):
        with self.connection() as db:
            cur = db.execute('DELETE FROM meta_connections WHERE tenant_id=? AND page_id=?',
                             (tenant_id, page_id))
            return cur.rowcount > 0
