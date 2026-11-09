"""ASGI identity/authorization boundary, before either manager's local session."""
import asyncio
from http.cookies import SimpleCookie, CookieError
import ipaddress
import json
import logging
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs,urlencode,urlsplit
from uuid import uuid4

from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from .store import IdentityError, SESSION_SECONDS, _assert_private_path
from .access_tokens import canonical_public_origin
from .rest import legacy_api_path

COOKIE = 'via_identity'
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
STATIC = Path(__file__).resolve().parent / 'static'
PAGE_FRONTEND = Path(__file__).resolve().parents[2] / 'meta-page-manager' / 'frontend'
AUTH_FONT_ASSETS = {
    '/auth/static/fonts.css': (PAGE_FRONTEND / 'fonts.css', 'text/css'),
    **{
        f'/auth/static/fonts/geist-{subset}-wght-normal.woff2': (
            PAGE_FRONTEND / 'fonts' / f'geist-{subset}-wght-normal.woff2', 'font/woff2',
        )
        for subset in ('latin', 'latin-ext', 'vietnamese')
    },
}
MESSAGES = {
    'authentication_required': 'Đăng nhập để tiếp tục.',
    'invalid_credentials': 'Tên đăng nhập hoặc mật khẩu chưa đúng.',
    'password_change_required': 'Đổi mật khẩu ban đầu trước khi dùng workspace.',
    'password_policy': 'Mật khẩu cần từ 12 đến 128 ký tự và không chứa ký tự điều khiển.',
    'new_password_required': 'Chọn mật khẩu khác mật khẩu hiện tại.',
    'forbidden': 'Tài khoản này không có quyền thực hiện thao tác.',
    'csrf_required': 'Phiên bảo vệ yêu cầu đã đổi. Tải lại trang trước khi thử lại.',
    'login_rate_limited': 'Đã có nhiều lượt đăng nhập. Vui lòng thử lại sau 5 phút.',
    'setup_required': 'Chưa khởi tạo tài khoản. Chạy via-auth/setup-users.bat trên máy chủ.',
    'last_admin_required': 'Cần giữ ít nhất một quản trị viên đang hoạt động.',
    'revision_required': 'Cần phiên bản tài khoản trước khi lưu.',
    'user_changed': 'Tài khoản đã thay đổi. Tải lại danh sách để đối chiếu.',
    'username_exists': 'Tên đăng nhập đã được sử dụng.',
    'self_reset_not_allowed': 'Dùng mục Tài khoản để đổi mật khẩu của chính bạn.',
    'invalid_input': 'Dữ liệu nhập không hợp lệ.',
    'token_limit_reached': 'Đã có 10 kết nối Codex đang hiệu lực. Thu hồi kết nối cũ trước.',
    'token_not_found': 'Không có kết nối Codex này trong tài khoản của bạn.',
    'mcp_not_configured': 'Cổng kết nối Codex chưa được cấu hình trên địa chỉ này.',
    'token_scope_required': 'Token này chỉ có quyền đọc.',
    'workspace_mismatch': 'Không gian làm việc này thuộc tài khoản Via khác.',
}


WORKSPACE_OWNER_WRITES = {
    'POST': (
        r'/api/profiles(?:/bulk-create)?',
        r'/api/proxies/(?:import|check)',
        r'/api/bm-profiles/[0-9]+/(?:open|proxy/check)',
        r'/pages/api/connections',
        r'/pages/api/video-source/scan',
    ),
    'PUT': (
        r'/api/bm-profiles/[0-9]+(?:/(?:mobile|proxy))?',
        r'/pages/api/connections/[0-9]+',
        r'/pages/api/video-source/settings',
    ),
    'PATCH': (r'/api/profiles/[0-9]+',),
    'DELETE': (
        r'/api/profiles/[0-9]+',
        r'/api/proxies/[0-9]+',
        r'/api/bm-profiles/[0-9]+(?:/proxy)?',
        r'/pages/api/connections/[0-9]+',
    ),
}


def workspace_host(user_id, port):
    if type(user_id) is not int or not 1 <= user_id <= 2**63 - 1:
        raise ValueError('Invalid workspace user ID')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid workspace port')
    return f'via-user-{user_id}.internal:{port}'


def can_access(actor, method, path, *, workspace_owner=False):
    path=legacy_api_path(path)
    if actor['role'] in ('admin', 'service'):
        return True
    if method in SAFE_METHODS:
        return True
    if workspace_owner and any(re.fullmatch(pattern, path) for pattern in WORKSPACE_OWNER_WRITES.get(method, ())):
        return True
    if method != 'POST':
        return False
    chrome = (
        r'/api/profiles/(?:start-selected|stop-selected)',
        r'/api/profiles/[0-9]+/(?:start|stop)',
        r'/api/facebook/(?:check|[0-9]+/stop)',
        r'/api/tasks/[0-9]+/(?:run|stop)',
        r'/api/reels(?:/assets|/[0-9]+/(?:cancel|resume|verify-link|archive|restore))?',
    )
    pages = (
        r'/pages/api/connections/[0-9]+/(?:check|sync)',
        r'/pages/api/pages/[0-9]+/(?:refresh|edit-options|edit|image)',
        r'/pages/api/reels(?:/assets|/[0-9]+/(?:cancel|resume|verify))?',
        r'/pages/api/idea-pipeline/(?:images|scan|views/refresh|production-batches)',
        r'/pages/api/idea-pipeline/ideas/[a-f0-9-]+/(?:run|iterations)',
        r'/pages/api/idea-pipeline/runs/[a-f0-9-]+/cancel',
    )
    return any(re.fullmatch(pattern, path) for pattern in (*chrome, *pages))


CHROME_UI_PATHS=frozenset(('/', '/chrome/pages', '/chrome/reels/new', '/chrome/reels/saved', '/chrome/settings'))
PAGE_UI_PATHS=frozenset(('/pages/', '/pages/schedule', '/pages/saved', '/pages/settings'))
PAGE_STANDALONE_UI_PATHS=frozenset(('/', '/schedule', '/saved', '/settings'))


def login_destination(path, query, *, standalone_pages=False):
    screens=PAGE_STANDALONE_UI_PATHS if standalone_pages else CHROME_UI_PATHS|PAGE_UI_PATHS
    if path not in screens:
        return '/pages/' if path.startswith('/pages') else '/'
    tabs={
        '/chrome/settings': ('app','bm','proxies','automation','history','recipient','data'),
        '/pages/settings': ('connections','activity','videosource','ideapipeline'),
        '/settings': ('connections','activity','videosource','ideapipeline'),
    }.get(path, ())
    if tabs:
        tab=parse_qs(query.decode('ascii','ignore'),max_num_fields=32).get('tab',[''])[0]
        if tab in tabs:return path+'?tab='+tab
    return path


def raw_cookie(scope):
    try:
        headers = dict(scope.get('headers', []))
        cookie = SimpleCookie()
        cookie.load(headers.get(b'cookie', b'').decode('latin1'))
        return cookie[COOKIE].value if COOKIE in cookie else ''
    except (CookieError, UnicodeError):
        return ''


class IdentityGate:
    def __init__(self, app, store, port=8000, base_path='', workspace='chrome',public_origin=None,
                 allow_service=True,mcp_resource=None,mcp_paths=(),portal_only=False,public_paths=(),
                 workspace_user_id=None,workspace_owner=False):
        if workspace not in ('chrome', 'pages'):
            raise ValueError('Invalid identity workspace')
        self.app, self.store, self.port, self.base_path = app, store, port, base_path
        self.workspace = workspace
        self.allow_service,self.mcp_resource=allow_service,mcp_resource
        self.mcp_paths,self.portal_only,self.public_paths=frozenset(mcp_paths),portal_only,frozenset(public_paths)
        self.workspace_user_id,self.workspace_owner=workspace_user_id,workspace_owner
        self.hosts = {f'{host}:{number}' for host in ('127.0.0.1', 'localhost') for number in (port, 5173)}
        self.origins = {'http://' + host for host in self.hosts}
        if workspace_user_id is not None:
            self.hosts.add(workspace_host(workspace_user_id,port))
        self.public_host=None
        if public_origin is not None:
            public_origin=canonical_public_origin(public_origin)
            parsed=urlsplit(public_origin)
            if (parsed.scheme!='https' or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment):raise ValueError('Invalid public origin')
            self.public_host=parsed.netloc
            self.hosts.add(parsed.netloc);self.origins.add(public_origin)

    def _resolve_actor(self, scope):
        headers = dict(scope.get('headers', []))
        authorization=headers.get(b'authorization',b'').decode('latin1')
        if authorization:
            if not authorization.startswith('Bearer '):return None
            return self.store.resolve_access_token(authorization[7:],audience=self.mcp_resource)
        token = headers.get(b'x-via-service-token', b'').decode('latin1')
        return (self.store.service_actor(token) if self.allow_service else None) or self.store.resolve(raw_cookie(scope))

    def _actor_in_workspace(self, actor):
        if actor['role']=='service':return self.allow_service
        return self.workspace_user_id is None or actor['id']==self.workspace_user_id

    def live_actor(self, scope):
        actor=self._resolve_actor(scope)
        return actor if actor and self._actor_in_workspace(actor) else None

    @staticmethod
    def _api(path):
        return path.startswith(('/api/', '/pages/api/', '/auth/api/','/mcp'))

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {k.decode('latin1').lower(): v.decode('latin1') for k, v in scope.get('headers', [])}
        try:
            local = ipaddress.ip_address(scope['client'][0]).is_loopback
        except (ValueError, TypeError, KeyError):
            local = False
        path = scope.get('path', '/')
        relative = path[len(self.base_path):] if self.base_path and path.startswith(self.base_path + '/') else path
        method = scope.get('method', 'GET')
        response_started = False
        original_send = send
        async def tracked_send(message):
            nonlocal response_started
            if message['type'] in ('http.response.start', 'websocket.accept'):
                response_started = True
            await original_send(message)
        send = tracked_send
        async def response(value):
            value.headers['Cache-Control'] = 'no-store'
            value.headers['X-Content-Type-Options'] = 'nosniff'
            value.headers['X-Frame-Options'] = 'DENY'
            value.headers['Referrer-Policy'] = 'no-referrer'
            value.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            return await value(scope, receive, send)
        async def reject(code, status):
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await response(JSONResponse({'detail': MESSAGES.get(code, 'Chưa hoàn tất thao tác.'), 'code': code}, status))
        if not local or headers.get('host') not in self.hosts:
            return await reject('local_only', 403)
        if (headers.get('origin') and headers['origin'] not in self.origins
                or headers.get('sec-fetch-site') == 'cross-site' and (self._api(path) or relative in AUTH_FONT_ASSETS)):
            return await reject('untrusted_origin', 403)
        if (self.public_host and headers.get('origin','').startswith('https://')
            and headers.get('host')!=self.public_host):return await reject('untrusted_origin',403)
        # A principal is passed by the parent ASGI application, never an HTTP header.
        # The child still applies its own cookie/CSRF boundary and request size limit.
        inherited = scope.get('via.identity')
        if inherited and scope.get('via.identity_store') == str(self.store.path):
            if (not self._actor_in_workspace(inherited) or inherited['role']=='service'
                    and scope.get('via.identity_service_path')!=str(self.store.service_path)):
                return await reject('workspace_mismatch',403)
            return await self.app(scope, receive, send)
        try:
            if scope['type']=='http' and relative in self.public_paths and method=='GET':
                return await self.app(scope,receive,send)
            if scope['type']=='http' and method in ('GET','HEAD') and relative in AUTH_FONT_ASSETS:
                asset,media_type=AUTH_FONT_ASSETS[relative]
                _assert_private_path(asset)
                return await response(FileResponse(asset,media_type=media_type))
            actor = self._resolve_actor(scope)
            if actor and not self._actor_in_workspace(actor):
                if self._api(path) and not relative.startswith('/auth/api/'):
                    return await reject('workspace_mismatch',403)
                actor=None
            if scope['type'] == 'http' and relative in ('/auth/login', '/auth/account', '/auth/users'):
                return await response(FileResponse(STATIC / 'index.html'))
            if scope['type'] == 'http' and relative in ('/auth/static/app.js', '/auth/static/style.css'):
                return await response(FileResponse(STATIC / relative.rsplit('/', 1)[1]))
            if scope['type'] == 'http' and relative.startswith('/auth/api/'):
                return await response(await self.auth_request(scope, receive, actor, relative, headers))
            if not actor:
                if scope['type'] == 'http' and not self._api(path) and method in ('GET', 'HEAD'):
                    try:target=login_destination(path,scope.get('query_string',b''),
                                                 standalone_pages=self.workspace=='pages' and not self.base_path)
                    except ValueError:target='/pages/' if path.startswith('/pages') else '/'
                    return await response(RedirectResponse('/auth/login?' + urlencode({'next':target},safe='/'), 303))
                return await reject('authentication_required', 401)
            if actor['must_change_password']:
                if scope['type'] == 'http' and not self._api(path) and method in ('GET', 'HEAD'):
                    return await response(RedirectResponse('/auth/account', 303))
                return await reject('password_change_required', 403)
            permission_path = '/pages' + relative if self.workspace == 'pages' else path
            is_mcp=relative in self.mcp_paths
            semantic_read=method=='POST' and re.fullmatch(r'/pages/api/pages/[0-9]+/edit-options',legacy_api_path(permission_path))
            if actor.get('auth_method')=='mcp_token' and not is_mcp and method not in SAFE_METHODS and not semantic_read and 'via:write' not in actor['scopes']:
                return await reject('token_scope_required',403)
            if not is_mcp and not can_access(actor, method, permission_path,workspace_owner=self.workspace_owner):
                self.store.audit(actor, method, permission_path, 403, str(uuid4()))
                return await reject('forbidden', 403)
            if method not in SAFE_METHODS and actor['role'] != 'service' and actor.get('auth_method')!='mcp_token':
                if not secrets.compare_digest(headers.get('x-via-csrf', '').encode(), actor['csrf_token'].encode()):
                    return await reject('csrf_required', 403)
            scope['via.identity'], scope['via.identity_store'] = actor, str(self.store.path)
            if actor['role']=='service':scope['via.identity_service_path']=str(self.store.service_path)
            scope['via.identity_gate'] = self
            request_id = str(uuid4())
            audited = scope['type'] == 'http' and method not in SAFE_METHODS
            if audited:
                self.store.audit(actor, method, permission_path, 102, request_id)
            async def audit_send(message):
                if audited and message['type'] == 'http.response.start':
                    try:
                        self.store.audit(actor, method, permission_path, message['status'], request_id)
                    except Exception:
                        logging.getLogger('via.identity').error('audit_completion_failed')
                await send(message)
            return await self.app(scope, receive, audit_send)
        except IdentityError as error:
            if response_started:
                raise
            return await reject(error.code, error.status)
        except Exception:
            logging.getLogger('via.identity').error('identity_boundary_failed')
            if response_started:
                raise
            return await reject('identity_unavailable', 503)

    async def _body(self, scope, receive):
        headers = dict(scope.get('headers', []))
        if headers.get(b'content-type', b'').split(b';', 1)[0] != b'application/json':
            raise IdentityError('invalid_input', 415)
        try:
            length = int(headers.get(b'content-length', b'0'))
        except ValueError:
            raise IdentityError('invalid_input') from None
        if length < 0:
            raise IdentityError('invalid_input')
        if length > 16_384:
            raise IdentityError('body_too_large', 413)
        body = bytearray()
        while True:
            try:
                message = await asyncio.wait_for(receive(), timeout=15)
            except asyncio.TimeoutError:
                raise IdentityError('request_timeout', 408) from None
            if message['type'] != 'http.request':
                raise IdentityError('invalid_input')
            body.extend(message.get('body', b''))
            if len(body) > 16_384:
                raise IdentityError('body_too_large', 413)
            if not message.get('more_body'):
                break
        try:
            values = json.loads(body)
        except (ValueError, UnicodeError):
            raise IdentityError('invalid_input') from None
        if not isinstance(values, dict):
            raise IdentityError('invalid_input')
        return values

    def _cookie(self,response, raw, scope):
        host=dict(scope.get('headers',[])).get(b'host',b'').decode('latin1')
        response.set_cookie(COOKIE, raw, max_age=SESSION_SECONDS, path='/', httponly=True,
                            secure=scope.get('scheme') == 'https' or bool(self.public_host and host==self.public_host), samesite='strict')
        return response

    async def auth_request(self, scope, receive, actor, path, headers):
        method = scope['method']
        if path == '/auth/api/login' and method == 'POST':
            values = await self._body(scope, receive)
            if set(values) != {'username', 'password'}:
                raise IdentityError('invalid_input')
            if not self.store.count():
                raise IdentityError('setup_required', 503)
            try:
                raw = await asyncio.to_thread(self.store.login, values['username'], values['password'], scope['client'][0],
                                              workspace_user_id=self.workspace_user_id)
            except IdentityError as error:
                self.store.audit({'username': 'anonymous'}, 'login', path, error.status, str(uuid4()))
                raise
            user = self.store.resolve(raw)
            self.store.audit(user, 'login', path, 200, str(uuid4()))
            return self._cookie(JSONResponse({'user': self._public(user)}), raw, scope)
        if not actor or actor['role'] == 'service' or actor.get('auth_method')=='mcp_token':
            raise IdentityError('authentication_required', 401)
        if path == '/auth/api/me' and method == 'GET':
            return JSONResponse({'user': self._public(actor), 'csrf_token': actor['csrf_token'],
                                 'app':{'portal_only':self.portal_only,'mcp_url':self.mcp_resource}})
        if method not in SAFE_METHODS and not secrets.compare_digest(headers.get('x-via-csrf', '').encode(), actor['csrf_token'].encode()):
            raise IdentityError('csrf_required', 403)
        if path == '/auth/api/logout' and method == 'POST':
            self.store.logout(raw_cookie(scope))
            self.store.audit(actor, 'logout', path, 200, str(uuid4()))
            result = JSONResponse({'ok': True})
            for name, cookie_path in ((COOKIE, '/'), ('cm_session', '/'), ('mpm_session', '/'), ('mpm_pages_session', '/pages/')):
                result.delete_cookie(name, path=cookie_path)
            return result
        if path == '/auth/api/password' and method == 'POST':
            values = await self._body(scope, receive)
            if set(values) != {'current_password', 'new_password'}:
                raise IdentityError('invalid_input')
            # Password verification has the same bounded attempt counter as login.
            await asyncio.to_thread(self.store._reserve_login, actor['username'], scope['client'][0])
            raw = await asyncio.to_thread(self.store.change_password, actor['id'], values['current_password'], values['new_password'])
            self.store.audit(actor, 'password_changed', path, 200, str(uuid4()))
            return self._cookie(JSONResponse({'ok': True}), raw, scope)
        if actor['must_change_password']:
            raise IdentityError('password_change_required', 403)
        if path=='/auth/api/mcp-tokens' and method=='GET':
            return JSONResponse({'tokens':self.store.list_access_tokens(actor['id']),'mcp_url':self.mcp_resource})
        if path=='/auth/api/mcp-tokens' and method=='POST':
            if not self.mcp_resource:raise IdentityError('mcp_not_configured',503)
            values=await self._body(scope,receive)
            if set(values)-{'name','scopes','days'} or not {'name','scopes'}<=set(values):raise IdentityError('invalid_input')
            result=self.store.create_access_token(actor['id'],name=values['name'],scopes=values['scopes'],
                audience=self.mcp_resource,days=values.get('days',30),expected_revision=actor['revision'])
            self.store.audit(actor,'mcp_token_created',path,201,str(uuid4()))
            return JSONResponse(result,201)
        token_match=re.fullmatch(r'/auth/api/mcp-tokens/([a-f0-9-]{36})',path)
        if token_match and method=='DELETE':
            self.store.revoke_access_token(actor['id'],token_match[1])
            self.store.audit(actor,'mcp_token_revoked',path,200,str(uuid4()))
            return JSONResponse({'ok':True})
        if actor['role'] != 'admin':
            raise IdentityError('forbidden', 403)
        if path == '/auth/api/users' and method == 'GET':
            return JSONResponse({'users': self.store.list_users()})
        match = re.fullmatch(r'/auth/api/users/([0-9]+)', path)
        if match and method == 'PATCH':
            values = await self._body(scope, receive)
            result = await asyncio.to_thread(self.store.update_user, actor['id'], int(match[1]), values)
            self.store.audit(actor, 'user_updated', path, 200, str(uuid4()))
            return JSONResponse(result)
        if path == '/auth/api/audit' and method == 'GET':
            query = parse_qs(scope.get('query_string', b'').decode('ascii', 'ignore'))
            try:
                limit, offset = int(query.get('limit', ['50'])[0]), int(query.get('offset', ['0'])[0])
            except ValueError:
                raise IdentityError('invalid_input') from None
            if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
                raise IdentityError('invalid_input')
            return JSONResponse(self.store.audits(limit, offset))
        raise IdentityError('not_found', 404)

    @staticmethod
    def _public(actor):
        return {key: value for key, value in actor.items() if key not in ('csrf_token',)}
