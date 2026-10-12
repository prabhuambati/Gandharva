"""Local-only account storage. Never writes collection.db or media files."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from . import core

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS personal_playlists(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,tracks TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS favorites(user_id TEXT NOT NULL,track_id TEXT NOT NULL,PRIMARY KEY(user_id,track_id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS creator_bindings(creator_id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,instagram_id TEXT NOT NULL DEFAULT '',user_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,user_id TEXT NOT NULL,creator_id TEXT NOT NULL,expires REAL NOT NULL,used REAL,revoked REAL,issued_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS creator_requests(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,creator_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(bucket TEXT NOT NULL,created REAL NOT NULL);
'''

class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise AuthError('Use a password between 12 and 256 characters')
    salt = salt or secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return salt + ':' + result.hex()


def password_matches(password, stored):
    try:
        salt, expected = stored.split(':')
        return hmac.compare_digest(password_hash(password, salt).split(':')[1], expected)
    except (ValueError, AuthError, TypeError):
        return False


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('REELROOM_AUTH_DB', core.DATA_DIR / 'accounts.db')).resolve()
        if self.path == core.DB_PATH.resolve():
            raise ValueError('Accounts database must be separate from the collection')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript(SCHEMA)
        os.chmod(self.path, 0o600)

    def db(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def ready(self):
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone())

    def setup_code(self):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                raise AuthError('Admin setup is already complete', 409)
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('setup_hash', digest(token)))
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('setup_expires', str(time.time() + 900)))
        return token

    def create_user(self, username, password, role='listener', setup_code=None):
        if not isinstance(username, str):
            raise AuthError('Username required')
        username = username.strip().lower()
        if not re.fullmatch(r'[a-z0-9_]{3,32}', username):
            raise AuthError('Username: 3–32 lowercase letters, numbers or underscores')
        hashed = password_hash(password)
        uid = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if role == 'admin':
                settings = dict(db.execute('SELECT key,value FROM settings').fetchall())
                if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                    raise AuthError('Admin setup is already complete', 409)
                if not setup_code or float(settings.get('setup_expires', '0')) < time.time() or not hmac.compare_digest(digest(setup_code), settings.get('setup_hash', '')):
                    raise AuthError('Invalid or expired setup code', 403)
                db.execute("DELETE FROM settings WHERE key IN ('setup_hash','setup_expires')")
            elif role != 'listener':
                raise AuthError('Public signup grants listener access only', 403)
            elif not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                raise AuthError('The owner must complete admin setup first', 409)
            try:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (uid, username, hashed, role, time.time()))
            except sqlite3.IntegrityError:
                raise AuthError('Username unavailable', 409)
        return {'id': uid, 'username': username, 'role': role}

    def limit(self, bucket, maximum=12, period=900):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE created<?', (time.time() - period,))
            count = db.execute('SELECT count(*) FROM attempts WHERE bucket=?', (bucket,)).fetchone()[0]
            if count >= maximum:
                raise AuthError('Too many attempts; try again later', 429)
            db.execute('INSERT INTO attempts VALUES(?,?)', (bucket, time.time()))

    def login(self, username, password):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (str(username).lower().strip(),)).fetchone()
        # Same expensive password operation for nonexistent users.
        fake = '0'*32 + ':' + '0'*64
        if not password_matches(password, row['password'] if row else fake) or not row:
            raise AuthError('Invalid username or password', 401)
        return self.new_session(row['id'])

    def new_session(self, uid):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), uid, csrf, time.time()+43200))
        return token

    def session(self, token):
        if not token:
            return None
        with self.db() as db:
            r = db.execute('SELECT u.id,u.username,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?', (digest(token), time.time())).fetchone()
            return dict(r) if r else None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def settings(self):
        with self.db() as db:
            return {'official_instagram': dict(db.execute('SELECT key,value FROM settings')).get('official_instagram', '')}

    def configure(self, user, handle):
        self.admin(user)
        if not re.fullmatch(r'[a-zA-Z0-9_.]{1,30}', str(handle)):
            raise AuthError('Enter the exact official Instagram username')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('official_instagram', handle))
            self.audit(db, user['id'], 'configure_contact', handle)
        return self.settings()

    @staticmethod
    def admin(user):
        if not user or user['role'] != 'admin':
            raise AuthError('Admin permission required', 403)

    @staticmethod
    def audit(db, actor, action, target):
        db.execute('INSERT INTO audit VALUES(?,?,?,?,?)', (uuid.uuid4().hex, actor, action, target, time.time()))

    def personal_playlists(self, user, payload=None, valid_ids=()):
        uid = user['id']
        with self.db() as db:
            if payload is not None:
                pid = payload.get('id')
                if pid and not db.execute('SELECT 1 FROM personal_playlists WHERE id=? AND user_id=?', (pid, uid)).fetchone():
                    raise AuthError('Playlist not found', 404)
                if payload.get('delete'):
                    if not pid:
                        raise AuthError('Playlist ID required')
                    db.execute('DELETE FROM personal_playlists WHERE id=? AND user_id=?', (pid, uid))
                else:
                    name = str(payload.get('name', '')).strip()
                    if not 1 <= len(name) <= 100:
                        raise AuthError('Playlist name: 1–100 characters')
                    tracks = payload.get('tracks', [])
                    if not isinstance(tracks, list) or len(tracks) > 5000 or any(not isinstance(t, str) for t in tracks):
                        raise AuthError('Invalid playlist tracks')
                    if any(t not in valid_ids for t in tracks):
                        raise AuthError('Only available accepted tracks may be added')
                    tracks = list(dict.fromkeys(tracks))
                    pid = pid or uuid.uuid4().hex
                    db.execute('INSERT OR REPLACE INTO personal_playlists VALUES(?,?,?,?)', (pid, uid, name, json.dumps(tracks)))
            return [dict(id=r['id'], name=r['name'], tracks=json.loads(r['tracks'])) for r in db.execute('SELECT * FROM personal_playlists WHERE user_id=? ORDER BY name', (uid,))]

    def favorites(self, user, payload=None, valid_ids=()):
        with self.db() as db:
            if payload is not None:
                tid = payload.get('track_id')
                if tid not in valid_ids or not isinstance(payload.get('liked'), bool):
                    raise AuthError('Invalid favorite')
                if payload['liked']:
                    db.execute('INSERT OR IGNORE INTO favorites VALUES(?,?)', (user['id'], tid))
                else:
                    db.execute('DELETE FROM favorites WHERE user_id=? AND track_id=?', (user['id'], tid))
            return [r[0] for r in db.execute('SELECT track_id FROM favorites WHERE user_id=?', (user['id'],))]

    def issue_invitation(self, user, payload, creators):
        self.admin(user)
        if not self.settings()['official_instagram']:
            raise AuthError('Configure the official Instagram contact before verifying claims', 409)
        if payload.get('manually_verified') is not True or not str(payload.get('evidence_reference', '')).strip():
            raise AuthError('Explicit manual DM verification and an evidence reference are required')
        creator = next((c for c in creators if c['username'] == payload.get('creator_username')), None)
        if not creator:
            raise AuthError('Creator not found', 404)
        token, iid = secrets.token_urlsafe(32), uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            target = db.execute('SELECT id FROM users WHERE username=?', (payload.get('account_username'),)).fetchone()
            if not target:
                raise AuthError('Invitee must first have a local listener account', 404)
            binding = db.execute('SELECT * FROM creator_bindings WHERE username=?', (creator['username'],)).fetchone()
            if binding and binding['instagram_id'] and creator.get('instagram_id') and binding['instagram_id'] != creator['instagram_id']:
                raise AuthError('Recorded Instagram identity changed; manual dispute review required', 409)
            if binding and binding['user_id']:
                raise AuthError('Creator ownership is disputed; no automatic transfer', 409)
            creator_id = binding['creator_id'] if binding else uuid.uuid4().hex
            if not binding:
                db.execute('INSERT INTO creator_bindings VALUES(?,?,?,?)', (creator_id, creator['username'], creator.get('instagram_id') or '', ''))
            db.execute('UPDATE invitations SET revoked=? WHERE creator_id=? AND used IS NULL', (time.time(), creator_id))
            db.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,?)', (iid, digest(token), target['id'], creator_id, time.time()+86400, None, None, user['id']))
            # Store minimal verification reference privately, never the raw invitation token.
            self.audit(db, user['id'], 'manual_verification_and_invite', json.dumps({'creator_id':creator_id,'user_id':target['id'],'evidence_reference':str(payload['evidence_reference'])[:300]}))
        return {'id': iid, 'token': token, 'expires_in': 86400, 'delivery': 'Manual delivery in the verified Instagram DM; not sent automatically'}

    def redeem(self, user, token):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            invite = db.execute('SELECT * FROM invitations WHERE token_hash=?', (digest(str(token)),)).fetchone()
            if not invite or invite['user_id'] != user['id'] or invite['used'] or invite['revoked'] or invite['expires'] < time.time():
                raise AuthError('Invitation is invalid, expired, revoked or belongs to another account', 403)
            db.execute('UPDATE invitations SET used=? WHERE id=?', (time.time(), invite['id']))
            db.execute('UPDATE creator_bindings SET user_id=? WHERE creator_id=?', (user['id'], invite['creator_id']))
            self.audit(db, user['id'], 'redeem_invitation', invite['id'])
        return {'ok': True}

    def bindings(self, user):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT creator_id,username FROM creator_bindings WHERE user_id=?', (user['id'],))]

    def creator_request(self, user, payload):
        if payload.get('kind') not in ('profile_correction','removal_request','attribution_correction'):
            raise AuthError('Unsupported request type')
        body = str(payload.get('body', '')).strip()
        if not 1 <= len(body) <= 2000:
            raise AuthError('Request: 1–2000 characters')
        with self.db() as db:
            cid = payload.get('creator_id')
            if not db.execute('SELECT 1 FROM creator_bindings WHERE creator_id=? AND user_id=?', (cid, user['id'])).fetchone():
                raise AuthError('Creator permission required', 403)
            db.execute('INSERT INTO creator_requests VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex, user['id'], cid, payload['kind'], body, time.time()))
        return {'ok': True, 'status': 'Pending manual admin review; no collection changes made'}

    def revoke(self, user, invitation_id):
        self.admin(user)
        with self.db() as db:
            row = db.execute('SELECT * FROM invitations WHERE id=?', (invitation_id,)).fetchone()
            if not row:
                raise AuthError('Invitation not found', 404)
            db.execute('UPDATE invitations SET revoked=? WHERE id=?', (time.time(), invitation_id))
            if row['used']:
                db.execute("UPDATE creator_bindings SET user_id='' WHERE creator_id=? AND user_id=?", (row['creator_id'],row['user_id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
            self.audit(db, user['id'], 'revoke_invitation_and_access', invitation_id)
        return {'ok':True}

    def admin_overview(self, user):
        self.admin(user)
        with self.db() as db:
            return {'users':[dict(r) for r in db.execute('SELECT id,username,role FROM users')], 'invitations':[dict(r) for r in db.execute('SELECT id,user_id,creator_id,expires,used,revoked FROM invitations')], 'requests':[dict(r) for r in db.execute('SELECT * FROM creator_requests ORDER BY created DESC')]}
