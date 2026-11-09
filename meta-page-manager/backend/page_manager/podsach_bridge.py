"""Read-only, fixed-target readiness for local and authenticated web uploaders.

This adapter never imports the uploader, opens its auth files, changes its shop,
starts a job, or submits a campaign. Saved cookies are not proof of login.
"""
from pathlib import Path
import json
import os
import re
import stat
import threading
from datetime import datetime, timezone
import time

import httpx


DEFAULT_TOOL_ROOT = Path.home() / 'Downloads/PODSach (2)/podsach-bulk-prep'
OVERVIEW_URL = 'http://127.0.0.1:8770/api/overview'
LOCAL_ENDPOINT = 'http://127.0.0.1:8770'
WEB_ENDPOINT = 'https://ul.eagleglobal2023.com/s/blueox179.com'
TOOL_LABEL = 'PODSach bulk prep'
TARGET_DOMAIN = 'blueox179.com'

SHOP_CHECK_ERRORS = {
    'invalid_provider': ('Chọn kiểm tra phiên cục bộ hoặc web.', 422),
    'web_not_connected': ('Chưa kết nối tool web. Mở Kết nối web dự phòng để đăng nhập tool.', 409),
    'tool_unavailable': ('Chưa đọc được tool đã chọn. Mở tool rồi kiểm tra phiên lại.', 503),
    'wrong_shop': ('Tool đã chọn đang dùng shop khác. Chọn blueox179.com trong tool rồi kiểm tra lại.', 409),
    'tool_session_expired': ('Phiên đăng nhập tool không còn hợp lệ. Mở tool hoặc kết nối web lại rồi kiểm tra.', 409),
    'busy_or_shop_changed': ('Tool đang bận hoặc shop vừa thay đổi. Đọc lại trạng thái rồi kiểm tra lại.', 409),
    'timeout': ('Kiểm tra phiên chưa trả kết quả trong thời gian chờ. Mở tool để đối chiếu rồi kiểm tra lại.', 504),
    'invalid_response': ('Tool chưa trả kết quả kiểm tra phiên hợp lệ. Mở tool để đối chiếu.', 502),
    'session_changed': ('Phiên tool vừa thay đổi trong lúc kiểm tra. Kiểm tra lại phiên hiện tại.', 409),
    'unavailable': ('Chưa kiểm tra được phiên shop. Mở tool để kiểm tra đăng nhập rồi thử lại.', 502),
}


class ShopSessionCheckError(ValueError):
    """Only fixed reason codes cross the uploader session-check boundary."""
    def __init__(self,reason):
        if reason not in SHOP_CHECK_ERRORS:reason='unavailable'
        self.reason=reason
        self.detail,self.status_code=SHOP_CHECK_ERRORS[reason]
        super().__init__(self.detail)
MAX_CONFIG_BYTES = 128 * 1024
MAX_OVERVIEW_BYTES = 512 * 1024
DOMAIN_RE = re.compile(r'(?=.{4,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z', re.ASCII)
SLUG_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z', re.ASCII)


def _domain(value):
    if not isinstance(value, str):
        return None
    value = value.lower()
    return value if DOMAIN_RE.fullmatch(value) else None


def _source(entry):
    # A named preset or a shirt-colour origin is not an operator-selected
    # common source. Only report the explicit fallback; never invent a choice.
    value = entry.get('fallback') if isinstance(entry, dict) else None
    return value if isinstance(value, str) and SLUG_RE.fullmatch(value) else None


def _workspace(value):
    """Summarize one overview without retaining filenames, rows or job logs."""
    unknown={'state':'unknown','input_count':None,'review_pending':None,'pending_batch_count':None}
    if not isinstance(value,dict):return unknown
    job=value.get('job');inputs=value.get('input');batches=value.get('batches')
    if (not isinstance(job,dict) or type(job.get('running')) is not bool
            or not isinstance(inputs,list) or not isinstance(batches,list) or 'review' not in value):return unknown
    if any(not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'] for item in inputs):return unknown
    review=value['review']
    if review is not None and not isinstance(review,dict):return unknown
    pending=0
    for batch in batches:
        if not isinstance(batch,dict) or not isinstance(batch.get('rows'),list):return unknown
        # A manifest read error can produce an empty rows list. It is not proof
        # that a previously pending batch is complete.
        if 'error' in batch and (not isinstance(batch['error'],str) or batch['error']):return unknown
        rows=batch['rows']
        if any(not isinstance(row,dict) or not isinstance(row.get('status'),str) for row in rows):return unknown
        pending+=int(any(row['status']!='done' for row in rows))
    busy=job['running'] or bool(inputs) or review is not None or pending>0
    return {'state':'busy' if busy else 'idle','input_count':len(inputs),
            'review_pending':review is not None,'pending_batch_count':pending}


def _regular(path):
    try:
        metadata = path.lstat()
        return (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
                and not getattr(metadata, 'st_file_attributes', 0) & 0x400)
    except OSError:
        return False


def _local_config(root):
    path = root / 'shops.json'
    if not _regular(path):
        return {}
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            return {}
        value = json.loads(raw.decode('utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {}


def _tool_root(value=None, *, check_exists=True):
    """Server-only installation path, never a client-supplied or relative path."""
    try:
        configured = value if value is not None else os.environ.get('PM_PODSACH_TOOL_ROOT', DEFAULT_TOOL_ROOT)
        root = Path(configured)
        if not root.is_absolute() or '..' in root.parts:
            return None
        if not check_exists:
            return root
        for part in (root, *root.parents):
            info = part.lstat()
            if (not stat.S_ISDIR(info.st_mode) or part.is_symlink()
                    or getattr(part, 'is_junction', lambda: False)()
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                return None
        return root
    except (OSError, ValueError, TypeError):
        return None


class _LocalPodsachBridge:
    """Constructor injection is for offline tests, not request parameters."""

    def __init__(self, *, transport=None, tool_root=None, timeout_seconds=2.0):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not 0 < timeout_seconds <= 5):
            raise ValueError('Invalid readiness timeout')
        self.tool_root = _tool_root(tool_root, check_exists=False)
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def _overview(self):
        try:
            with httpx.Client(transport=self.transport, trust_env=False,
                              follow_redirects=False, timeout=self.timeout_seconds) as client:
                with client.stream('GET', OVERVIEW_URL, headers={'Accept': 'application/json'}) as response:
                    if response.status_code != 200:
                        return None
                    length = response.headers.get('content-length')
                    if length is not None and (not length.isdecimal() or int(length) > MAX_OVERVIEW_BYTES):
                        return None
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        raw.extend(chunk)
                        if len(raw) > MAX_OVERVIEW_BYTES:
                            return None
            value = json.loads(raw.decode('utf-8'))
            info = value.get('info') if isinstance(value, dict) else None
            if not isinstance(info, dict) or _domain(info.get('domain')) is None:
                return None
            if not isinstance(info.get('shops'), dict):
                return None
            return info,_workspace(value)
        except (httpx.HTTPError, OSError, UnicodeError, ValueError, RecursionError):
            return None

    def inspect(self):
        root = _tool_root(self.tool_root) if self.tool_root is not None else None
        installed = root is not None and all(_regular(root / name)
                        for name in ('dashboard.py', 'upload_batch.py', 'config.py'))
        saved = _local_config(root) if root is not None else {}
        observed = self._overview()
        info,workspace = observed if observed is not None else (None,_workspace(None))
        available = info is not None
        # A running tool's active state wins over its on-disk settings.
        shops = info['shops'] if available else saved.get('shops', {})
        shops = shops if isinstance(shops, dict) else {}
        configured = isinstance(shops.get(TARGET_DOMAIN), dict)
        active = _domain(info.get('domain') if available else saved.get('active'))
        source = _source(shops.get(TARGET_DOMAIN))
        if available and active == TARGET_DOMAIN:
            # info.fallback_origin is the actual active tool's selected fallback.
            source = _source({'fallback': info.get('fallback_origin')}) or source
        authenticated = None
        if available and active == TARGET_DOMAIN:
            explicit = info.get('authenticated')
            session = info.get('session')
            if type(explicit) is bool:
                authenticated = explicit
            elif isinstance(session, dict) and type(session.get('authenticated')) is bool:
                authenticated = session['authenticated']

        blockers = []
        if not installed:
            blockers.append('Chưa tìm thấy bộ cài PODSach bulk prep tại vị trí đã cấu hình.')
        if not available:
            blockers.append('Tool PODSach chưa sẵn sàng ở cổng 8770; mở run_dashboard.bat của tool.')
        if not configured:
            blockers.append('Chưa cấu hình shop blueox179.com trong tool PODSach.')
        if configured and active != TARGET_DOMAIN:
            blockers.append('Tool PODSach chưa chọn shop blueox179.com.')
        if not source:
            blockers.append('Chưa chọn campaign mẫu chung cho blueox179.com trong tool PODSach.')
        if authenticated is not True:
            blockers.append('Chưa có xác nhận đăng nhập blueox179.com còn hiệu lực; kiểm tra session trong tool PODSach.')
        return {
            'installed': installed,
            'tool_root': TOOL_LABEL,
            'available': available,
            'configured_blueox': configured,
            'active_domain': active,
            'authenticated': authenticated,
            'source': source,
            'ready': not blockers,
            'blockers': blockers,
            'workspace':workspace,
        }


class PodsachBridge:
    """Prefer a ready local tool; choose web before a run, never retry across tools."""

    def __init__(self, *, transport=None, tool_root=None, timeout_seconds=2.0,
                 remote_connection=None):
        self.local = _LocalPodsachBridge(transport=transport, tool_root=tool_root,
                                        timeout_seconds=timeout_seconds)
        self.remote_connection = remote_connection
        self._lock = threading.Lock()
        self._cached = None
        self._checked = 0.0

    def invalidate(self):
        with self._lock:
            self._cached = None
            self._checked = 0.0

    def _remote(self, *, timeout_seconds=None):
        saved = self.remote_connection.status().get('session_saved') is True
        info = None
        workspace=_workspace(None)
        if saved:
            try:
                with self.remote_connection.client(overview_only=True) as client:
                    with client.stream('GET', WEB_ENDPOINT + '/api/overview',
                                       headers={'Accept': 'application/json'},
                                       **({'timeout': httpx.Timeout(timeout_seconds, connect=5)}
                                          if timeout_seconds is not None else {})) as response:
                        if response.status_code == 200:
                            length = response.headers.get('content-length')
                            if length is None or length.isdecimal() and int(length) <= MAX_OVERVIEW_BYTES:
                                raw = bytearray()
                                for chunk in response.iter_bytes(chunk_size=16384):
                                    raw.extend(chunk)
                                    if len(raw) > MAX_OVERVIEW_BYTES:
                                        break
                                if len(raw) <= MAX_OVERVIEW_BYTES:
                                    value = json.loads(raw.decode('utf-8'))
                                    candidate = value.get('info') if isinstance(value, dict) else None
                                    if (isinstance(candidate, dict) and _domain(candidate.get('domain'))
                                            and isinstance(candidate.get('shops'), dict)):
                                        info = candidate
                                        workspace=_workspace(value)
            except Exception:
                # Credentials, response bodies and upstream exceptions never enter the public status.
                info = None
        available = info is not None
        shops = info['shops'] if available else {}
        configured = isinstance(shops.get(TARGET_DOMAIN), dict)
        active = _domain(info.get('domain')) if available else None
        source = _source(shops.get(TARGET_DOMAIN))
        authenticated = None
        if available and active == TARGET_DOMAIN:
            source = _source({'fallback': info.get('fallback_origin')}) or source
            explicit, session = info.get('authenticated'), info.get('session')
            if type(explicit) is bool:
                authenticated = explicit
            elif isinstance(session, dict) and type(session.get('authenticated')) is bool:
                authenticated = session['authenticated']
            elif callable(getattr(self.remote_connection, 'shop_verified', None)):
                authenticated = self.remote_connection.shop_verified()
        blockers = []
        if not saved:
            blockers.append('Đăng nhập tool web ul.eagleglobal2023.com để dùng làm nguồn dự phòng.')
        elif not available:
            blockers.append('Chưa đọc được tool web. Kết nối lại nếu phiên đăng nhập đã hết hạn.')
        if available and not configured:
            blockers.append('Chưa cấu hình shop blueox179.com trong tool web.')
        elif available and active != TARGET_DOMAIN:
            blockers.append('Tool web đang chọn shop khác; chọn blueox179.com trong tool trước khi upload.')
        if available and not source:
            blockers.append('Chưa chọn campaign mẫu chung cho blueox179.com trong tool web.')
        if available and authenticated is not True:
            blockers.append('Phiên shop blueox179 chưa được xác nhận. Bấm Kiểm tra phiên shop để kiểm tra trực tiếp.')
        result = {'installed': True, 'tool_root': 'PODSach web', 'available': available,
                'configured_blueox': configured, 'active_domain': active,
                'authenticated': authenticated, 'source': source,
                'ready': not blockers, 'blockers': blockers, 'session_saved': saved,'workspace':workspace}
        diagnostic = getattr(self.remote_connection, 'diagnostics', lambda: None)()
        if diagnostic is not None:
            result['login_diagnostic'] = diagnostic
        return result

    def inspect(self, *, force=False):
        if self.remote_connection is None:
            return self.local.inspect()
        with self._lock:
            if not force and self._cached is not None and time.monotonic() - self._checked < 5:
                return self._cached
            local = self.local.inspect()
            web = self._remote()
            provider = ('local' if local['ready'] else 'web' if web['ready'] else
                        'local' if local['available'] and local['active_domain'] == TARGET_DOMAIN else 'web')
            chosen = local if provider == 'local' else web
            result = {**chosen, 'provider': provider,
                      'endpoint': LOCAL_ENDPOINT if provider == 'local' else WEB_ENDPOINT,
                      'policy': 'local_first',
                      'providers': {'local': {**local, 'endpoint': LOCAL_ENDPOINT},
                                    'web': {**web, 'endpoint': WEB_ENDPOINT}}}
            self._cached, self._checked = result, time.monotonic()
            return result

    def check_shop_session(self, provider):
        """Explicit operator action; may renew a shop session inside the upload tool."""
        if provider not in ('local', 'web'):
            raise ShopSessionCheckError('invalid_provider')
        endpoint = LOCAL_ENDPOINT if provider == 'local' else WEB_ENDPOINT
        # Explicit checks can wait for a sleeping web worker. Passive status
        # polling remains short and never renews a shop login.
        revision = None
        if provider == 'web':
            if self.remote_connection is None or self.remote_connection.status().get('session_saved') is not True:
                raise ShopSessionCheckError('web_not_connected')
            revision_reader = getattr(self.remote_connection, 'session_revision', None)
            if callable(revision_reader):
                revision = revision_reader()
            state = self._remote(timeout_seconds=70)
        else:
            state = self.local.inspect()
        if not state['available']:raise ShopSessionCheckError('tool_unavailable')
        if state['active_domain'] != TARGET_DOMAIN:raise ShopSessionCheckError('wrong_shop')
        factory = (lambda: httpx.Client(transport=self.local.transport,trust_env=False,
                                      follow_redirects=False,timeout=45)) if provider == 'local' else self.remote_connection.client
        try:
            with factory() as client:
                with client.stream('GET', endpoint + '/api/session/check',
                                   headers={'Accept': 'application/json', 'X-PODSach-Expected-Shop': TARGET_DOMAIN},
                                   timeout=httpx.Timeout(70, connect=5) if provider == 'web' else 45) as response:
                    if response.status_code in (401,403):raise ShopSessionCheckError('tool_session_expired')
                    if response.status_code == 409:raise ShopSessionCheckError('busy_or_shop_changed')
                    if response.status_code != 200:raise ShopSessionCheckError('unavailable')
                    if response.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
                        raise ShopSessionCheckError('invalid_response')
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        raw.extend(chunk)
                        if len(raw) > MAX_OVERVIEW_BYTES:
                            raise ShopSessionCheckError('invalid_response')
                    result = json.loads(raw.decode('utf-8'))
                    checked = result.get('ok') if isinstance(result, dict) else None
                    if type(checked) is not bool:
                        checked = result.get('authenticated') if isinstance(result, dict) else None
                    if type(checked) is not bool:
                        raise ShopSessionCheckError('invalid_response')
                    if provider == 'web' and callable(getattr(self.remote_connection, 'record_shop_check', None)):
                        self.remote_connection.record_shop_check(checked,
                            **({'expected_revision': revision} if revision is not None else {}))
        except ShopSessionCheckError:raise
        except httpx.TimeoutException:raise ShopSessionCheckError('timeout') from None
        except (ValueError,UnicodeError,RecursionError):raise ShopSessionCheckError('invalid_response') from None
        except Exception as error:
            raise ShopSessionCheckError('session_changed' if getattr(error,'code',None)=='uploader_connection_changed' else 'unavailable') from None
        self.invalidate()
        current=dict(self.inspect(force=True))
        selected=current.get('providers',{}).get(provider,current if provider=='local' else {})
        ready=checked is True and selected.get('ready') is True and selected.get('authenticated') is True
        current['session_check']={'provider':provider,'authenticated':checked,'ready':ready,
            'status':'authenticated' if checked else 'authentication_required',
            'checked_at':datetime.now(timezone.utc).isoformat(),
            'reason':None if ready else 'authentication_required' if not checked else
                     'proof_not_available' if selected.get('authenticated') is not True else 'configuration_required'}
        return current
