"""Password hashes, revocable sessions and metadata-only audit in a separate DB."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
import threading
import time
from contextlib import closing, contextmanager
from .access_tokens import AccessTokens,create_schema as create_access_token_schema


SESSION_SECONDS = 12 * 60 * 60
IDLE_SECONDS = 30 * 60
USERNAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,39}$')
_PASSWORD_SLOTS = threading.BoundedSemaphore(4)


class IdentityError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128 or any(ord(c) < 32 for c in password):
        raise IdentityError('password_policy')
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1,
                            dklen=32, maxmem=64 * 1024 * 1024).hex()
    return 'scrypt-v1$' + salt + '$' + digest


def password_matches(password, encoded):
    try:
        if not isinstance(password, str) or len(password) > 128:
            return False
        kind, salt, expected = encoded.split('$')
        if kind != 'scrypt-v1':
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1,
                                dklen=32, maxmem=64 * 1024 * 1024).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _absolute(path):
    # Keep the lexical path until every existing component has been checked.
    # resolve() would follow a redirect before we could reject it.
    return Path(os.path.abspath(os.fspath(path)))


def _assert_private_path(path, *, directory=False, missing=False):
    path = _absolute(path)
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise IdentityError('unsafe_identity_path', 500) from None
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or getattr(item, 'is_junction', lambda: False)()):
            raise IdentityError('unsafe_identity_path', 500)
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise IdentityError('unsafe_identity_path', 500)
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IdentityError('unsafe_identity_path', 500)
    return path


def _replace_windows_dacl(path, sid):
    """Replace the complete DACL, including explicit grants, and protect inheritance."""
    import ctypes
    from ctypes import wintypes
    inherit = 'OICI' if path.is_dir() else ''
    sddl = 'D:P' + ''.join(f'(A;{inherit};FA;;;{principal})' for principal in (sid, 'SY', 'BA'))
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    get_dacl = advapi.GetSecurityDescriptorDacl
    get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    set_security = advapi.SetNamedSecurityInfoW
    set_security.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_security.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise IdentityError('private_permissions_failed', 500)
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present or not dacl:
            raise IdentityError('private_permissions_failed', 500)
        # SE_FILE_OBJECT; DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION.
        if set_security(str(path), 1, 0x00000004 | 0x80000000, None, None, dacl, None):
            raise IdentityError('private_permissions_failed', 500)
    finally:
        kernel.LocalFree(descriptor)


def protect_private(path):
    """Set a complete private ACL before creating any database, key or password file."""
    path = _absolute(path)
    _assert_private_path(path, directory=path.is_dir())
    if os.name == 'nt':
        current = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                                 text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        import csv
        sid = next(csv.reader([current.stdout.strip()]))[-1]
        if not re.fullmatch(r'S-1-(?:\d+-)*\d+', sid):
            raise IdentityError('private_permissions_failed', 500)
        _replace_windows_dacl(path, sid)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


class IdentityStore(AccessTokens):
    def __init__(self, directory, *, clock=time.time, service_directory=None):
        self.directory = _absolute(directory)
        self.path = self.directory / 'identity.sqlite3'
        self.service_directory = self.directory if service_directory is None else _absolute(service_directory)
        self.service_path = self.service_directory / 'local-service.key'
        self.clock = clock
        self._prepared = False
        self._prepare_lock = threading.Lock()
        self._dummy = None

    def prepare(self):
        if self._prepared:
            return
        with self._prepare_lock:
            if self._prepared:
                return
            _assert_private_path(self.directory, directory=True, missing=True)
            self.directory.mkdir(parents=True, exist_ok=True)
            self._assert_database_paths()
            _assert_private_path(self.service_path, missing=True)
            protect_private(self.directory)
            with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS users (
                      id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                      role TEXT NOT NULL CHECK(role IN ('admin','operator')),
                      password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                      must_change INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
                      created_at INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS sessions (
                      digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                      csrf TEXT NOT NULL, created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL,
                      expires_at INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS login_limits (
                      key TEXT PRIMARY KEY, window_start INTEGER NOT NULL, attempts INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS audit (
                      id INTEGER PRIMARY KEY, at INTEGER NOT NULL, actor_id INTEGER,
                      actor TEXT NOT NULL, action TEXT NOT NULL, route TEXT NOT NULL,
                      status INTEGER NOT NULL, request_id TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS audit_at ON audit(at);
                ''')
                create_access_token_schema(db)
            protect_private(self.path)
            self._prepared = True

    @contextmanager
    def connect(self):
        self.prepare()
        _assert_private_path(self.directory, directory=True)
        self._assert_database_paths()
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _assert_database_paths(self):
        for suffix in ('', '-wal', '-shm', '-journal'):
            _assert_private_path(Path(str(self.path) + suffix), missing=True)

    @staticmethod
    def public(row):
        return {key: row[key] for key in ('id', 'username', 'role', 'revision')} | {
            'active': bool(row['active']), 'must_change_password': bool(row['must_change'])}

    def count(self):
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM users').fetchone()[0]

    def bootstrap(self):
        """One-time operation. Passwords are returned only to the provisioning CLI."""
        accounts = []
        for username, role in [('admin', 'admin'), ('operator1', 'operator'), ('operator2', 'operator')]:
            password = secrets.token_urlsafe(24)
            accounts.append((username, role, password, password_hash(password)))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
                raise IdentityError('users_already_initialized', 409)
            for username, role, _, hashed in accounts:
                db.execute('INSERT INTO users(username,role,password_hash,created_at) VALUES(?,?,?,?)',
                           (username, role, hashed, int(self.clock())))
        return [{'username': username, 'role': role, 'password': password} for username, role, password, _ in accounts]

    def ensure_service_key(self):
        self.prepare()
        if self.service_directory != self.directory:
            _assert_private_path(self.service_directory, directory=True, missing=True)
            self.service_directory.mkdir(parents=True, exist_ok=True)
            protect_private(self.service_directory)
        _assert_private_path(self.service_path, missing=True)
        if not self.service_path.exists():
            key = secrets.token_urlsafe(48)
            try:
                fd = os.open(self.service_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'w', encoding='ascii') as file:
                    file.write(key)
        protect_private(self.service_path)

    def service_actor(self, token):
        if not token or len(token) > 128:
            return None
        _assert_private_path(self.service_path, missing=True)
        if not self.service_path.is_file():
            return None
        with self.service_path.open('rb') as file:
            opened = os.fstat(file.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise IdentityError('unsafe_identity_path', 500)
            raw = file.read(129)
        if len(raw) > 128:
            raise IdentityError('unsafe_identity_key', 500)
        try:
            expected = raw.decode('ascii').strip()
        except UnicodeError:
            raise IdentityError('unsafe_identity_key', 500) from None
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return None
        return {'id': None, 'username': 'local-service', 'role': 'service', 'active': True,
                'must_change_password': False, 'revision': 1}

    def _reserve_login(self, username, peer):
        now = int(self.clock())
        keys = [('peer:' + peer, 30), ('user:' + username.casefold(), 10)]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for raw, limit in keys:
                key = token_hash(raw)
                row = db.execute('SELECT * FROM login_limits WHERE key=?', (key,)).fetchone()
                if row and now - row['window_start'] < 300 and row['attempts'] >= limit:
                    raise IdentityError('login_rate_limited', 429)
            for raw, _ in keys:
                key = token_hash(raw)
                db.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET '
                           'window_start=CASE WHEN ?-window_start>=300 THEN ? ELSE window_start END,'
                           'attempts=CASE WHEN ?-window_start>=300 THEN 1 ELSE attempts+1 END',
                           (key, now, now, now, now))
            db.execute('DELETE FROM login_limits WHERE window_start<?', (now - 86400,))

    def _new_session(self, db, user_id):
        now = int(self.clock())
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires_at<=? OR last_seen<=?', (now, now - IDLE_SECONDS))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                   (token_hash(raw), user_id, csrf, now, now, now + SESSION_SECONDS))
        return raw

    def login(self, username, password, peer, *, workspace_user_id=None):
        if not isinstance(username, str) or not USERNAME.fullmatch(username):
            username = '_invalid_'
        self._reserve_login(username, peer)
        if not _PASSWORD_SLOTS.acquire(blocking=False):
            raise IdentityError('login_rate_limited', 429)
        try:
            with self.connect() as db:
                user = db.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE', (username,)).fetchone()
            if self._dummy is None:
                self._dummy = password_hash(secrets.token_urlsafe(24))
            valid = password_matches(password, user['password_hash'] if user else self._dummy)
            if not user or not valid or not user['active']:
                raise IdentityError('invalid_credentials', 401)
            if workspace_user_id is not None and user['id'] != workspace_user_id:
                raise IdentityError('workspace_mismatch', 403)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
                if not current or not current['active'] or current['password_hash'] != user['password_hash']:
                    raise IdentityError('invalid_credentials', 401)
                raw = self._new_session(db, user['id'])
            return raw
        finally:
            _PASSWORD_SLOTS.release()

    def resolve(self, raw):
        if not raw or len(raw) > 128:
            return None
        now = int(self.clock())
        with self.connect() as db:
            row = db.execute('SELECT users.*,sessions.csrf,sessions.digest,sessions.expires_at,sessions.last_seen '
                             'FROM sessions JOIN users ON users.id=sessions.user_id WHERE digest=?', (token_hash(raw),)).fetchone()
            if not row or not row['active'] or row['expires_at'] <= now or row['last_seen'] <= now - IDLE_SECONDS:
                return None
            if now - row['last_seen'] >= 60:
                db.execute('UPDATE sessions SET last_seen=? WHERE digest=?', (now, row['digest']))
            return self.public(row) | {'csrf_token': row['csrf']}

    def logout(self, raw):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (token_hash(raw),))

    def change_password(self, user_id, old_password, new_password):
        if not _PASSWORD_SLOTS.acquire(blocking=False):
            raise IdentityError('login_rate_limited', 429)
        try:
            return self._change_password(user_id, old_password, new_password)
        finally:
            _PASSWORD_SLOTS.release()

    def _change_password(self, user_id, old_password, new_password):
        with self.connect() as db:
            user = db.execute('SELECT * FROM users WHERE id=? AND active=1', (user_id,)).fetchone()
        if not user or not password_matches(old_password, user['password_hash']):
            raise IdentityError('invalid_credentials', 401)
        if password_matches(new_password, user['password_hash']):
            raise IdentityError('new_password_required')
        hashed = password_hash(new_password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE users SET password_hash=?,must_change=0,revision=revision+1 '
                                 'WHERE id=? AND password_hash=? AND active=1', (hashed, user_id, user['password_hash']))
            if not changed.rowcount:
                raise IdentityError('user_changed', 409)
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            return self._new_session(db, user_id)

    def list_users(self):
        with self.connect() as db:
            return [self.public(row) for row in db.execute('SELECT * FROM users ORDER BY id')]

    def update_user(self, actor_id, user_id, values):
        if set(values) - {'username', 'role', 'active', 'revision', 'reset_password', 'revoke_sessions'}:
            raise IdentityError('invalid_input')
        if type(values.get('revision')) is not int:
            raise IdentityError('revision_required', 428)
        if any(name in values and type(values[name]) is not bool for name in ('reset_password', 'revoke_sessions')):
            raise IdentityError('invalid_input')
        if actor_id == user_id and values.get('reset_password') is True:
            raise IdentityError('self_reset_not_allowed', 409)
        password = secrets.token_urlsafe(24) if values.get('reset_password') is True else None
        hashed = None
        if password:
            if not _PASSWORD_SLOTS.acquire(blocking=False):
                raise IdentityError('login_rate_limited', 429)
            try:
                hashed = password_hash(password)
            finally:
                _PASSWORD_SLOTS.release()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not row:
                raise IdentityError('user_not_found', 404)
            if row['revision'] != values['revision']:
                raise IdentityError('user_changed', 409)
            username, role, active = values.get('username', row['username']), values.get('role', row['role']), values.get('active', bool(row['active']))
            if not isinstance(username, str) or not USERNAME.fullmatch(username) or role not in ('admin', 'operator') or type(active) is not bool:
                raise IdentityError('invalid_input')
            if row['role'] == 'admin' and (role != 'admin' or not active):
                others = db.execute("SELECT COUNT(*) FROM users WHERE id!=? AND role='admin' AND active=1", (user_id,)).fetchone()[0]
                if not others:
                    raise IdentityError('last_admin_required', 409)
            try:
                db.execute('UPDATE users SET username=?,role=?,active=?,password_hash=?,must_change=?,revision=revision+1 WHERE id=?',
                           (username, role, int(active), hashed or row['password_hash'], 1 if password else row['must_change'], user_id))
            except sqlite3.IntegrityError:
                raise IdentityError('username_exists', 409) from None
            # Any security or identity change invalidates every existing session for this user.
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            current = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        return {'user': self.public(current), **({'temporary_password': password} if password else {})}

    def audit(self, actor, action, route, status, request_id):
        with self.connect() as db:
            db.execute('INSERT INTO audit(at,actor_id,actor,action,route,status,request_id) VALUES(?,?,?,?,?,?,?)',
                       (int(self.clock()), actor.get('id'), actor.get('username', 'anonymous'), action[:32], route[:240], status, request_id))
            db.execute('DELETE FROM audit WHERE at<?', (int(self.clock()) - 90 * 86400,))

    def audits(self, limit=50, offset=0):
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
            return {'total': total, 'items': [dict(row) for row in db.execute(
                'SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?', (limit, offset))]}
