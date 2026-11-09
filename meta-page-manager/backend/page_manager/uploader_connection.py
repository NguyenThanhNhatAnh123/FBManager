"""An isolated, fixed-origin login session for the hosted PODSach tool.

This module never reads a browser profile or the uploader's shop credentials.
Only the tool session cookies are persisted, through the application's SecretBox.
Login to this tool does not attest authentication to any POD shop.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import Cookie
import json
import re
import threading
import time
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

from .errors import AppError
from .uploader_gateway import GatewayClient, GatewayError


ENDPOINT = 'https://ul.eagleglobal2023.com'
HOST = 'ul.eagleglobal2023.com'
COOKIE_PARENT_DOMAIN = 'eagleglobal2023.com'
TARGET_SHOP = 'blueox179.com'
SHOP_PREFIX = '/s/' + TARGET_SHOP
OVERVIEW_PATH = SHOP_PREFIX + '/api/overview'
MAX_RESPONSE_BYTES = 512 * 1024
MAX_COOKIE_BYTES = 64 * 1024
MAX_COOKIES = 32
SHOP_CHECK_TTL_SECONDS = 300
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,256}\Z", re.ASCII)
_DOMAIN = re.compile(r'(?=.{4,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z', re.ASCII)
_HIDDEN = {'csrf_token', '_csrf', 'csrfmiddlewaretoken'}


def get_tool_overview_path():
    """The hosted gateway requires an explicit, immutable shop namespace."""
    return OVERVIEW_PATH


class _CookieError(ValueError):
    def __init__(self, reason):
        self.reason = reason if reason in {'missing', 'unsupported_scope', 'size', 'invalid'} else 'invalid'
        super().__init__('Invalid tool cookie jar')


def _error(detail='Chưa kết nối được phiên đăng nhập tool upload.',
           code='uploader_connection_failed', status=502):
    return AppError(detail, code, status)


def _stage_error(stage, reason='invalid'):
    # Codes identify the failing step without copying remote bodies, URLs,
    # credentials, exception text, cookie names or cookie values into the API.
    labels = {
        'login_form': 'Không đọc được biểu mẫu đăng nhập tool web.',
        'login_submit': 'Tool web chưa hoàn tất bước đăng nhập.',
        'login_landing': 'Tool web chưa hoàn tất chuyển trang sau đăng nhập.',
        'overview': 'Chưa xác minh được phiên tool web qua dữ liệu tổng quan.',
        'cookie': 'Tool web chưa cấp phiên đăng nhập hợp lệ để lưu.',
        'save': 'Không lưu được phiên tool web an toàn trên máy này.',
    }
    detail = labels[stage]
    if reason == 'timeout':
        detail += ' Kết nối vượt thời gian chờ; hãy thử lại khi mạng ổn định.'
    elif reason == 'network':
        detail += ' Chưa liên lạc được với máy chủ upload.'
    return _error(detail, 'uploader_' + stage + '_' + reason, 503 if stage == 'save' else 502)


def _fixed_url(value):
    try:
        url = urlsplit(str(value))
        return (url.scheme == 'https' and url.hostname == HOST
                and url.port in (None, 443) and url.username is None
                and url.password is None and not url.fragment)
    except (ValueError, TypeError):
        return False


def _login_landing_url(value):
    """Known login pages only; never follow redirects into side-effectful APIs."""
    url = urlsplit(urljoin(ENDPOINT + '/login', value))
    return (url.geturl() if _fixed_url(url.geturl()) and
            (url.path in ('/', SHOP_PREFIX + '/') and not url.query or url.path == '/login' and
             (not url.query or parse_qsl(url.query, keep_blank_values=True) == [('next', '/')]))
            else None)


def _bounded_response(response):
    length = response.headers.get('content-length')
    if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
        raise ValueError('Invalid response length')
    raw = bytearray()
    for chunk in response.iter_bytes(chunk_size=16384):
        raw.extend(chunk)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('Response too large')
    return bytes(raw)


class _LoginForm(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'form':
            if self.current is not None:
                raise ValueError('Nested form')
            self.current = {'action': attributes.get('action', ''),
                            'method': attributes.get('method', 'get').lower(), 'inputs': []}
        elif tag == 'input' and self.current is not None:
            self.current['inputs'].append(attributes)

    def handle_endtag(self, tag):
        if tag == 'form' and self.current is not None:
            self.forms.append(self.current)
            self.current = None


def _login_fields(raw):
    parser = _LoginForm()
    parser.feed(raw.decode('utf-8'))
    parser.close()
    matches = []
    for form in parser.forms:
        action = urlsplit(urljoin(ENDPOINT + '/login', form['action']))
        if (form['method'] != 'post' or not _fixed_url(action.geturl())
                or action.path != '/login' or action.query):
            continue
        inputs = form['inputs']
        if (sum(row.get('name') == 'user' for row in inputs) != 1
                or sum(row.get('name') == 'password' for row in inputs) != 1):
            continue
        fields = {'next': '/'}
        for row in inputs:
            name = row.get('name')
            if name not in _HIDDEN or row.get('type', '').lower() != 'hidden':
                continue
            value = row.get('value', '')
            if name in fields or len(value) > 4096 or any(ord(char) < 32 for char in value):
                raise ValueError('Invalid hidden field')
            fields[name] = value
        matches.append(fields)
    if len(matches) != 1:
        raise ValueError('Unsupported login form')
    return matches[0]


def _cookie_values(cookies):
    values = []
    for cookie in cookies.jar:
        # The trusted tool may issue a shared EagleGlobal SSO cookie. Accept
        # only its exact parent, then omit domain from persisted rows so reload
        # always recreates it as a HOST-only UL cookie. Client requests remain
        # fixed to UL even before this normalization; no sibling can receive it.
        if cookie.domain.lstrip('.').lower() not in (HOST, COOKIE_PARENT_DOMAIN):
            raise _CookieError('unsupported_scope')
        if cookie.is_expired(time.time()):
            continue
        name, value, path = cookie.name, cookie.value, cookie.path or '/'
        if (isinstance(name, str) and len(name) > 256
                or isinstance(value, str) and len(value) > 8192
                or isinstance(path, str) and len(path) > 2048):
            raise _CookieError('size')
        if (not _COOKIE_NAME.fullmatch(name) or not isinstance(value, str)
                or len(value) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or not isinstance(path, str) or not path.startswith('/') or len(path) > 2048
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or cookie.expires is not None and (type(cookie.expires) is not int or cookie.expires < 0)):
            raise _CookieError('invalid')
        values.append({'name': name, 'value': value, 'path': path,
                       'secure': bool(cookie.secure), 'expires': cookie.expires})
    if not values:
        raise _CookieError('missing')
    if len(values) > MAX_COOKIES:
        raise _CookieError('size')
    encoded = json.dumps(values, ensure_ascii=True, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > MAX_COOKIE_BYTES:
        raise _CookieError('size')
    return encoded


def _decode_cookies(encoded):
    if not isinstance(encoded, str) or len(encoded.encode('utf-8')) > MAX_COOKIE_BYTES:
        raise ValueError('Invalid saved jar')
    values = json.loads(encoded)
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_COOKIES:
        raise ValueError('Invalid saved cookies')
    cookies = httpx.Cookies()
    for row in values:
        if (not isinstance(row, dict) or set(row) != {'name', 'value', 'path', 'secure', 'expires'}
                or type(row['secure']) is not bool):
            raise ValueError('Invalid saved cookie')
        cookies.jar.set_cookie(Cookie(
            version=0, name=row['name'], value=row['value'], port=None,
            port_specified=False, domain=HOST, domain_specified=False,
            domain_initial_dot=False, path=row['path'], path_specified=True,
            secure=row['secure'], expires=row['expires'], discard=row['expires'] is None,
            comment=None, comment_url=None, rest={'HttpOnly': None}, rfc2109=False))
    # Revalidate every field rather than trusting the encrypted container alone.
    _cookie_values(cookies)
    return cookies


class UploaderConnection:
    def __init__(self, store, secret_box, *, transport=None, timeout_seconds=5,
                 login_timeout_seconds=20,initialize=True):
        if type(initialize) is not bool:raise ValueError('Invalid uploader initialization mode')
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not 0 < timeout_seconds <= 10):
            raise ValueError('Invalid uploader connection timeout')
        if (isinstance(login_timeout_seconds, bool)
                or not isinstance(login_timeout_seconds, (int, float))
                or not 0 < login_timeout_seconds <= 20):
            raise ValueError('Invalid uploader login timeout')
        self.store, self.secrets = store, secret_box
        self.transport, self.timeout_seconds = transport, timeout_seconds
        self.login_timeout_seconds = login_timeout_seconds
        self._diagnostic_lock = threading.Lock()
        self._login_diagnostic = None
        with store.connect() as db:
            if not initialize:
                columns={row[1] for row in db.execute('PRAGMA table_info(uploader_remote_session)')}
                required={'id','encrypted_cookies','revision','updated_at','checked_revision','checked_at','checked_ok'}
                if not required.issubset(columns) or db.execute('SELECT id FROM uploader_remote_session WHERE id=1').fetchone() is None:
                    raise _error('Phiên tool web chưa được ứng dụng khởi tạo.','uploader_session_unavailable',503)
                return
            db.execute('''CREATE TABLE IF NOT EXISTS uploader_remote_session(
                id INTEGER PRIMARY KEY CHECK(id=1), encrypted_cookies TEXT,
                revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT)''')
            db.execute('INSERT OR IGNORE INTO uploader_remote_session(id) VALUES(1)')
            columns = {row[1] for row in db.execute('PRAGMA table_info(uploader_remote_session)')}
            for name, kind in (('checked_revision', 'INTEGER'), ('checked_at', 'TEXT'), ('checked_ok', 'INTEGER')):
                if name not in columns:
                    db.execute('ALTER TABLE uploader_remote_session ADD COLUMN ' + name + ' ' + kind)

    def _row(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM uploader_remote_session WHERE id=1').fetchone())

    def _saved_cookies(self, row=None):
        encrypted = (self._row() if row is None else row)['encrypted_cookies']
        if not encrypted:
            return httpx.Cookies()
        try:
            return _decode_cookies(self.secrets.decrypt(encrypted))
        except Exception:
            raise _error('Không đọc được phiên tool upload đã lưu; hãy kết nối lại.',
                         'uploader_session_unavailable', 503) from None

    def _new_client(self, *, cookies=None, overview_only=False, login=False):
        def restrict(request):
            if not _fixed_url(request.url):
                raise _error('Đích kết nối tool upload không hợp lệ.', 'invalid_uploader_target', 400)
            request.headers['Host'] = HOST
            if client.is_gateway_poll(request):
                if (request.method != 'GET' or request.url.query
                        or not re.fullmatch(re.escape(SHOP_PREFIX) + r'/_gw/ticket/[A-Za-z0-9_-]{16}', request.url.path)):
                    raise _error('Yêu cầu theo dõi tool upload không hợp lệ.', 'invalid_uploader_read', 400)
                if overview_only or login:
                    request.headers.pop('X-PODSach-Expected-Shop', None)
                else:
                    request.headers['X-PODSach-Expected-Shop'] = TARGET_SHOP
                return
            if overview_only:
                if request.method != 'GET' or request.url.path != OVERVIEW_PATH or request.url.query:
                    raise _error('Chỉ được đọc tổng quan tool upload.', 'invalid_uploader_read', 400)
                request.headers.pop('X-PODSach-Expected-Shop', None)
            elif login:
                if not ((request.method == 'GET' and request.url.path in ('/', '/login', SHOP_PREFIX + '/', OVERVIEW_PATH))
                        or (request.method == 'POST' and request.url.path == '/login')):
                    raise _error('Thao tác kết nối tool upload không hợp lệ.', 'invalid_uploader_login', 400)
                request.headers.pop('X-PODSach-Expected-Shop', None)
            else:
                if not (request.url.path.startswith(SHOP_PREFIX + '/api/')
                        or request.url.path.startswith(SHOP_PREFIX + '/img/')):
                    raise _error('Đường dẫn tool upload không thuộc shop đã chọn.', 'invalid_uploader_shop_path', 400)
                if any(part in ('.', '..') for part in request.url.path.split('/')):
                    raise _error('Đường dẫn tool upload không hợp lệ.', 'invalid_uploader_shop_path', 400)
                request.headers['X-PODSach-Expected-Shop'] = TARGET_SHOP
        base = ENDPOINT if login or overview_only else ENDPOINT + SHOP_PREFIX + '/'
        client = GatewayClient(base_url=base, cookies=cookies, transport=self.transport,
                            trust_env=False, follow_redirects=False,
                            timeout=self.login_timeout_seconds if login else self.timeout_seconds,
                            headers={'Accept': 'application/json'}, event_hooks={'request': [restrict]})
        return client

    @contextmanager
    def client(self, *, overview_only=False):
        """The unscoped variant reads only the fixed Blueox overview endpoint."""
        with self._new_client(cookies=self._saved_cookies(), overview_only=overview_only) as client:
            yield client

    def status(self):
        try:
            saved = bool(list(self._saved_cookies().jar))
        except AppError:
            saved = False
        return {'endpoint': ENDPOINT, 'session_saved': saved}

    def session_revision(self):
        """Internal fence for shop-session attestations; never a credential."""
        revision = self._row()['revision']
        if type(revision) is not int or revision < 1:
            raise _error('Không đọc được trạng thái phiên tool web.', 'uploader_session_unavailable', 503)
        return revision

    def record_shop_check(self, ok, *, expected_revision=None):
        """Record a result only for the tool login that started its probe.

        The optional revision keeps the original in-process callers compatible.
        Network probe callers pass the revision captured before opening a client.
        """
        if type(ok) is not bool:
            raise _error('Kết quả kiểm tra phiên shop không hợp lệ.', 'invalid_uploader_shop_check', 422)
        if (expected_revision is not None
                and (type(expected_revision) is not int or expected_revision < 1)):
            raise _error('Phiên kiểm tra shop không hợp lệ.', 'invalid_uploader_shop_check', 422)
        row = self._row()
        if expected_revision is not None and row['revision'] != expected_revision:
            raise _error('Phiên tool web đã thay đổi; kiểm tra lại phiên mới.', 'uploader_connection_changed', 409)
        revision = row['revision'] if expected_revision is None else expected_revision
        if not row['encrypted_cookies'] or not list(self._saved_cookies(row).jar):
            raise _error('Kết nối tool web trước khi kiểm tra phiên shop.', 'uploader_session_unavailable', 409)
        checked_at = datetime.now(timezone.utc).isoformat()
        with self.store.connect() as db:
            result = db.execute('''UPDATE uploader_remote_session SET checked_revision=?,checked_at=?,checked_ok=?
                WHERE id=1 AND revision=? AND encrypted_cookies IS NOT NULL''',
                (revision, checked_at, int(ok), revision))
            if result.rowcount != 1:
                raise _error('Phiên tool web đã thay đổi; kiểm tra lại phiên mới.', 'uploader_connection_changed', 409)
        return ok

    def shop_verified(self):
        """Return only a fresh explicit result, never infer login from saved data."""
        try:
            row = self._row()
            if (not row['encrypted_cookies'] or row['checked_revision'] != row['revision']
                    or row['checked_ok'] not in (0, 1) or not isinstance(row['checked_at'], str)):
                return None
            stamp = datetime.fromisoformat(row['checked_at'])
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                return None
            age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
            if not 0 <= age <= SHOP_CHECK_TTL_SECONDS or not list(self._saved_cookies(row).jar):
                return None
            if self.session_revision() != row['revision']:
                return None
            return row['checked_ok'] == 1
        except Exception:
            return None

    def _diagnostic(self, stage=None, reason=None, http_status=None):
        with self._diagnostic_lock:
            if stage is None:
                self._login_diagnostic = None
                return
            stages = {'input', 'login_form', 'login_submit', 'login_landing', 'overview', 'cookie', 'save'}
            reasons = {'invalid', 'unavailable', 'invalid_redirect', 'rejected', 'timeout',
                       'network', 'failed', 'changed', 'info_missing', 'domain_invalid',
                       'shops_invalid', 'json_invalid', 'content_type', 'missing',
                       'unsupported_scope', 'size', 'redirect_limit', 'pending', 'gateway_timeout',
                       'gateway_ticket_invalid', 'gateway_ticket_changed', 'gateway_response_size'}
            self._login_diagnostic = {
                'stage': stage if stage in stages else 'save',
                'reason': reason if reason in reasons else 'failed',
                'http_status': http_status if type(http_status) is int and 100 <= http_status <= 599 else None,
            }

    def diagnostics(self):
        """Return the last in-process login failure without upstream content."""
        with self._diagnostic_lock:
            return dict(self._login_diagnostic) if self._login_diagnostic is not None else None

    def connect(self, username, password):
        if (not isinstance(username, str) or not 1 <= len(username) <= 200
                or username != username.strip() or any(ord(char) < 32 or ord(char) == 127 for char in username)
                or not isinstance(password, str) or not 1 <= len(password) <= 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in password)):
            self._diagnostic('input', 'invalid')
            raise _error('Nhập tài khoản và mật khẩu tool upload hợp lệ.', 'invalid_uploader_login', 422)
        stage, http_status = 'save', None
        try:
            revision = self._row()['revision']
            stage = 'login_form'
            with self._new_client(login=True) as client:
                with client.stream('GET', '/login?next=/', headers={'Accept': 'text/html'}) as response:
                    http_status = response.status_code
                    if response.status_code != 200:
                        raise _stage_error(stage, 'unavailable')
                    if 'text/html' not in response.headers.get('content-type', '').lower():
                        raise _stage_error(stage, 'invalid')
                    fields = _login_fields(_bounded_response(response))
                fields.update(user=username, password=password)
                stage, http_status = 'login_submit', None
                with client.stream('POST', '/login', data=fields,
                                   headers={'Accept': 'text/html', 'Origin': ENDPOINT,
                                            'Referer': ENDPOINT + '/login?next=/'}) as response:
                    http_status = response.status_code
                    if response.status_code in (401, 403):
                        raise _error('Tool upload chưa chấp nhận thông tin đăng nhập.', 'uploader_login_rejected', 401)
                    if response.status_code not in (200, 302, 303):
                        raise _stage_error(stage, 'unavailable')
                    post_status = response.status_code
                    post_location = response.headers.get('location')
                    _bounded_response(response)

                def probe_session():
                    nonlocal stage, http_status
                    stage, http_status = 'overview', None
                    with client.stream('GET', OVERVIEW_PATH,
                                       timeout=httpx.Timeout(self.login_timeout_seconds, read=65)) as response:
                        http_status = response.status_code
                        if http_status in (301, 302, 303, 307, 308, 401, 403):
                            return None, http_status
                        if http_status != 200:
                            raise _stage_error(stage, 'unavailable')
                        if 'application/json' not in response.headers.get('content-type', '').lower():
                            raise _stage_error(stage, 'content_type')
                        try:
                            value = json.loads(_bounded_response(response).decode('utf-8'))
                        except (UnicodeError, ValueError, RecursionError):
                            raise _stage_error(stage, 'json_invalid') from None
                    info = value.get('info') if isinstance(value, dict) else None
                    if not isinstance(info, dict):
                        raise _stage_error(stage, 'info_missing')
                    if not isinstance(info.get('domain'), str) or not _DOMAIN.fullmatch(info['domain']):
                        raise _stage_error(stage, 'domain_invalid')
                    if not isinstance(info.get('shops'), dict):
                        raise _stage_error(stage, 'shops_invalid')
                    stage, http_status = 'cookie', None
                    return _cookie_values(client.cookies), None

                def pending_failure(status):
                    nonlocal stage, http_status
                    stage, http_status = 'overview', status
                    if status in (401, 403):
                        raise _error('Tool upload chưa chấp nhận thông tin đăng nhập.', 'uploader_login_rejected', 401)
                    raise _stage_error(stage, 'pending')

                # Authentication is proved by the protected API, not by which
                # UI page the tool wants to display. A valid session can return
                # a dashboard redirect that this adapter never needs to follow.
                encoded, pending_status = probe_session()
                if encoded is None:
                    if post_status not in (302, 303):
                        pending_failure(pending_status)
                    redirect = _login_landing_url(post_location) if post_location else None
                    if redirect is None:
                        stage, http_status = 'login_submit', post_status
                        raise _stage_error(stage, 'invalid_redirect')
                    # Only known UI pages may establish/renew a pending login.
                    # After each GET, probe the API before inspecting another
                    # navigation target. Never replay POST or fetch unknown paths.
                    for _ in range(3):
                        stage, http_status = 'login_landing', None
                        with client.stream('GET', redirect, headers={'Accept': 'text/html',
                                           'Referer': ENDPOINT + '/login'}) as response:
                            landing_status = http_status = response.status_code
                            landing_location = response.headers.get('location')
                            _bounded_response(response)
                        encoded, pending_status = probe_session()
                        if encoded is not None:
                            break
                        stage, http_status = 'login_landing', landing_status
                        if landing_status in (301, 302, 303, 307, 308):
                            redirect = _login_landing_url(landing_location) if landing_location else None
                            if redirect is None:
                                raise _stage_error(stage, 'invalid_redirect')
                        elif landing_status == 200:
                            pending_failure(pending_status)
                        else:
                            raise _stage_error(stage, 'unavailable')
                    else:
                        raise _stage_error(stage, 'redirect_limit')
            stage, http_status = 'save', None
            encrypted = self.secrets.encrypt(encoded)
            if not isinstance(encrypted, str) or not encrypted or encrypted == encoded:
                raise _stage_error(stage, 'failed')
            with self.store.connect() as db:
                result = db.execute('''UPDATE uploader_remote_session SET encrypted_cookies=?,
                    revision=revision+1,updated_at=?,checked_revision=NULL,checked_at=NULL,checked_ok=NULL
                    WHERE id=1 AND revision=?''',
                    (encrypted, datetime.now(timezone.utc).isoformat(), revision))
                if result.rowcount != 1:
                    raise _error('Kết nối đã thay đổi trong lúc đăng nhập; hãy kiểm tra lại.',
                                 'uploader_connection_changed', 409)
            self._diagnostic()
            return self.status()
        except _CookieError as error:
            self._diagnostic('cookie', error.reason)
            raise _stage_error('cookie', error.reason) from None
        except AppError as error:
            reason = ('rejected' if error.code == 'uploader_login_rejected' else
                      'changed' if error.code == 'uploader_connection_changed' else
                      error.code.removeprefix('uploader_' + stage + '_'))
            self._diagnostic(stage, reason, http_status)
            raise
        except GatewayError as error:
            reason = {
                'uploader_gateway_timeout': 'gateway_timeout',
                'uploader_gateway_invalid_ticket': 'gateway_ticket_invalid',
                'uploader_gateway_ticket_changed': 'gateway_ticket_changed',
                'uploader_gateway_response_too_large': 'gateway_response_size',
            }.get(error.code, 'network')
            self._diagnostic(stage, reason, http_status)
            raise _stage_error(stage, reason) from None
        except httpx.TimeoutException:
            self._diagnostic(stage, 'timeout', http_status)
            raise _stage_error(stage, 'timeout') from None
        except httpx.HTTPError:
            self._diagnostic(stage, 'network', http_status)
            raise _stage_error(stage, 'network') from None
        except Exception:
            reason = 'failed' if stage == 'save' else 'invalid'
            self._diagnostic(stage, reason, http_status)
            raise _stage_error(stage, reason) from None
        finally:
            # Values are never retained on the connection object or written to logs.
            password = None

    def disconnect(self):
        with self.store.connect() as db:
            db.execute('''UPDATE uploader_remote_session SET encrypted_cookies=NULL,
                revision=revision+1,updated_at=?,checked_revision=NULL,checked_at=NULL,checked_ok=NULL
                WHERE id=1''', (datetime.now(timezone.utc).isoformat(),))
        self._diagnostic()
        return self.status()
