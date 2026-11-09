"""Per-user, revocable MCP credentials. Raw tokens are returned once only."""
import hashlib
import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

TOKEN_PATTERN=re.compile(r'via_pat_[A-Za-z0-9_-]{43}\Z')
TOKEN_SCOPES=frozenset(('via:read','via:write'))


def canonical_public_origin(value):
    if (not isinstance(value,str) or not value or len(value)>2048 or '\\' in value
        or any(c.isspace() or ord(c)<32 for c in value)):raise ValueError('Invalid HTTPS origin')
    url=urlsplit(value);port=url.port
    if (url.scheme!='https' or not url.hostname or url.username is not None or url.password is not None
        or url.path or url.query or url.fragment or port is not None and not 1<=port<=65535):raise ValueError('Invalid HTTPS origin')
    try:host=ipaddress.ip_address(url.hostname).compressed
    except ValueError:host=url.hostname.encode('idna').decode('ascii').lower()
    if ':' in host:host='['+host+']'
    return 'https://'+host+(':'+str(port) if port not in (None,443) else '')


def create_schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS mcp_access_tokens (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
        digest TEXT NOT NULL UNIQUE, name TEXT NOT NULL, scopes TEXT NOT NULL,
        user_revision INTEGER NOT NULL, audience TEXT NOT NULL,
        created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        revoked_at INTEGER, last_used_at INTEGER);
      CREATE INDEX IF NOT EXISTS mcp_tokens_user ON mcp_access_tokens(user_id);
    ''')


def valid_audience(value):
    try:
        if (not isinstance(value,str) or len(value)>2048 or '\\' in value
            or any(c.isspace() or ord(c)<32 for c in value)):return False
        url=urlsplit(value)
        port=url.port
        return bool(url.path=='/mcp' and url.hostname and url.username is None
                    and url.password is None and not url.query and not url.fragment
                    and (url.scheme=='https' or url.scheme=='http' and url.hostname=='127.0.0.1' and port))
    except (ValueError,TypeError):return False


class AccessTokens:
    def create_access_token(self,user_id,*,name,scopes,audience,days=30,expected_revision=None):
        from .store import IdentityError
        if (not isinstance(name,str) or not 1<=len(name.strip())<=64 or any(ord(c)<32 for c in name)
            or not isinstance(scopes,list) or not scopes or any(not isinstance(s,str) for s in scopes)
            or len(set(scopes))!=len(scopes) or not set(scopes)<=TOKEN_SCOPES or 'via:read' not in scopes
            or type(days) is not int or not 1<=days<=30 or not valid_audience(audience)):
            raise IdentityError('invalid_input')
        raw='via_pat_'+secrets.token_urlsafe(32);identifier=str(uuid4());now=int(self.clock())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if not user or not user['active'] or user['must_change']:
                raise IdentityError('password_change_required',403)
            if expected_revision is not None and user['revision']!=expected_revision:
                raise IdentityError('user_changed',409)
            active=db.execute('SELECT COUNT(*) FROM mcp_access_tokens WHERE user_id=? AND revoked_at IS NULL AND expires_at>? AND user_revision=?',
                              (user_id,now,user['revision'])).fetchone()[0]
            if active>=10:raise IdentityError('token_limit_reached',409)
            db.execute('INSERT INTO mcp_access_tokens(id,user_id,digest,name,scopes,user_revision,audience,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (identifier,user_id,hashlib.sha256(raw.encode()).hexdigest(),name.strip(),json.dumps(sorted(scopes)),user['revision'],audience,now,now+days*86400))
        return {'id':identifier,'name':name.strip(),'token':raw,'scopes':sorted(scopes),
                'audience':audience,'expires_at':now+days*86400,'created_at':now}

    def resolve_access_token(self,raw,*,audience=None):
        if not isinstance(raw,str) or TOKEN_PATTERN.fullmatch(raw) is None:return None
        now=int(self.clock())
        with self.connect() as db:
            row=db.execute('SELECT u.*,t.id AS access_token_id,t.scopes AS access_scopes,t.audience AS access_audience,t.expires_at AS access_expires_at,'
                           't.revoked_at,t.user_revision,t.last_used_at FROM mcp_access_tokens t JOIN users u ON u.id=t.user_id WHERE t.digest=?',
                           (hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
            if (not row or not row['active'] or row['must_change'] or row['revoked_at'] is not None
                or row['access_expires_at']<=now or row['revision']!=row['user_revision']
                or audience is not None and row['access_audience']!=audience):return None
            scopes=json.loads(row['access_scopes'])
            if not isinstance(scopes,list) or 'via:read' not in scopes or not set(scopes)<=TOKEN_SCOPES:return None
            if row['last_used_at'] is None or now-row['last_used_at']>=60:
                db.execute('UPDATE mcp_access_tokens SET last_used_at=? WHERE id=?',(now,row['access_token_id']))
            return self.public(row)|{'auth_method':'mcp_token','token_id':row['access_token_id'],
                'scopes':scopes,'audience':row['access_audience'],'expires_at':row['access_expires_at'],'csrf_token':''}

    def list_access_tokens(self,user_id):
        with self.connect() as db:
            rows=db.execute('SELECT t.id,t.name,t.scopes,t.audience,t.created_at,t.expires_at,t.revoked_at,t.last_used_at,t.user_revision,u.revision '
                            'FROM mcp_access_tokens t JOIN users u ON u.id=t.user_id WHERE user_id=? ORDER BY t.created_at DESC,t.id',(user_id,))
            return [{key:row[key] for key in ('id','name','audience','created_at','expires_at','revoked_at','last_used_at')}|
                    {'scopes':json.loads(row['scopes']),'valid_revision':row['user_revision']==row['revision']} for row in rows]

    def revoke_access_token(self,user_id,identifier):
        from .store import IdentityError
        if not isinstance(identifier,str) or len(identifier)!=36:raise IdentityError('invalid_input')
        with self.connect() as db:
            updated=db.execute('UPDATE mcp_access_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE id=? AND user_id=?',
                               (int(self.clock()),identifier,user_id))
            if not updated.rowcount:raise IdentityError('token_not_found',404)
