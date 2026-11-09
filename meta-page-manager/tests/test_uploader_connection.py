"""Hosted uploader session tests. Mock HTTP and SecretBox only; no real login."""
import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from page_manager.errors import AppError
from page_manager.uploader_gateway import GatewayError
from page_manager.store import Store
from page_manager.uploader_connection import (
    ENDPOINT, HOST, MAX_COOKIE_BYTES, MAX_RESPONSE_BYTES, UploaderConnection,
    _CookieError, _cookie_values, OVERVIEW_PATH, SHOP_PREFIX, get_tool_overview_path,
)


PASSWORD = 'LOCAL_PASSWORD_NEVER_SAVED'
COOKIE = 'TOOL_SESSION_PRIVATE_VALUE'
FORM = '<form method="post" action="/login"><input type="hidden" name="next" value="/"><input name="user" required><input type="password" name="password" required></form>'


class Box:
    def encrypt(self, value):
        return 'fixture-encrypted:' + base64.b64encode(value.encode()).decode()

    def decrypt(self, value):
        return base64.b64decode(value.removeprefix('fixture-encrypted:')).decode()


def valid_info():
    # Login to the uploader must not silently select blueox or assert POD auth.
    return {'info': {'domain': '247smileworld.com', 'shops': {
        'blueox179.com': {'fallback': 'zitat25'}, '247smileworld.com': {'fallback': 'other'}}},
        'job': {'log': ['PRIVATE LOG NEVER RETURNED']}}


def build(tmp_path, *, form=FORM, post_status=302, location='/', overview_status=200,
          payload=None, cookie=None, handler=None, box=None):
    calls = []

    def respond(request):
        calls.append(request)
        assert str(request.url).startswith(ENDPOINT + '/')
        if handler:
            custom = handler(request)
            if custom is not None:
                return custom
        if request.url.path == '/login' and request.method == 'GET':
            assert 'x-podsach-expected-shop' not in request.headers
            return httpx.Response(200, text=form, headers={'Content-Type': 'text/html; charset=utf-8'})
        if request.url.path == '/login' and request.method == 'POST':
            assert 'x-podsach-expected-shop' not in request.headers
            body = parse_qs(request.content.decode())
            assert body['user'] == ['fixture-user'] and body['password'] == [PASSWORD]
            assert body['next'] == ['/']
            return httpx.Response(post_status, text='PRIVATE LOGIN RESPONSE', headers={
                'Location': location, 'Set-Cookie': cookie or f'pod_session={COOKIE}; Path=/; HttpOnly; Secure; SameSite=Lax'})
        if request.url.path == OVERVIEW_PATH:
            return httpx.Response(overview_status, json=valid_info() if payload is None else payload)
        if request.url.path == '/' and request.method == 'GET':
            return httpx.Response(200, text='Tool dashboard', headers={'Content-Type': 'text/html'})
        return httpx.Response(404, json={'error': 'PRIVATE RESPONSE'})

    store = Store(tmp_path)
    value = UploaderConnection(store, box or Box(), transport=httpx.MockTransport(respond))
    return value, store, calls


def stored(store):
    with store.connect() as db:
        return dict(db.execute('SELECT * FROM uploader_remote_session WHERE id=1').fetchone())


def test_login_stores_only_encrypted_tool_cookie_and_never_selects_shop(tmp_path):
    value, store, calls = build(tmp_path)
    result = value.connect('fixture-user', PASSWORD)
    assert result == {'endpoint': ENDPOINT, 'session_saved': True}
    assert [(row.method, row.url.path) for row in calls] == [
        ('GET', '/login'), ('POST', '/login'), ('GET', OVERVIEW_PATH)]
    assert COOKIE in calls[-1].headers['cookie']
    assert 'x-podsach-expected-shop' not in calls[-1].headers
    row = stored(store)
    assert PASSWORD not in json.dumps(row) and COOKIE not in json.dumps(row)
    assert 'fixture-user' not in json.dumps(row)
    assert row['encrypted_cookies'].startswith('fixture-encrypted:')
    assert 'authenticated' not in result and 'PRIVATE' not in json.dumps(result)


def test_client_restores_cookie_scopes_shop_and_overwrites_host_override(tmp_path):
    value, store, calls = build(tmp_path)
    value.connect('fixture-user', PASSWORD)
    fresh = UploaderConnection(store, Box(), transport=value.transport)
    with fresh.client() as client:
        response = client.get(ENDPOINT + OVERVIEW_PATH, headers={
            'X-PODSach-Expected-Shop': 'wrong.example', 'Host': 'wrong.example'})
    assert response.status_code == 200
    assert calls[-1].headers['X-PODSach-Expected-Shop'] == 'blueox179.com'
    assert calls[-1].headers['Host'] == HOST
    assert calls[-1].headers['Cookie'] == 'pod_session=' + COOKIE
    assert calls[-1].url.host == HOST


@pytest.mark.parametrize('url', ['https://other.example/api/overview', 'http://ul.eagleglobal2023.com/api/overview',
    'https://user:password@ul.eagleglobal2023.com/api/overview', 'https://ul.eagleglobal2023.com:8443/api/overview',
    'https://ul.eagleglobal2023.com.evil.example/api/overview', 'https://ul.eagleglobal2023.com/api/overview#fragment'])
def test_client_rejects_other_origins_before_transport(tmp_path, url):
    value, _, calls = build(tmp_path)
    with value.client() as client, pytest.raises((AppError, GatewayError)):
        client.get(url)
    assert calls == []


@pytest.mark.parametrize('method,path', [('POST', OVERVIEW_PATH), ('GET', OVERVIEW_PATH + '?shop=other'),
    ('GET', '/api/shops'), ('POST', '/login'), ('GET', '/')])
def test_unscoped_overview_client_is_narrow_read_only(tmp_path, method, path):
    value, _, calls = build(tmp_path)
    with value.client(overview_only=True) as client, pytest.raises(AppError):
        client.request(method, path)
    assert calls == []


def test_unscoped_overview_strips_expected_shop_without_broadening_client(tmp_path):
    value, _, calls = build(tmp_path)
    with value.client(overview_only=True) as client:
        assert client.get(ENDPOINT + OVERVIEW_PATH, headers={'X-PODSach-Expected-Shop': 'blueox179.com'}).status_code == 200
    assert len(calls) == 1 and 'x-podsach-expected-shop' not in calls[0].headers


def test_client_does_not_follow_redirects_or_send_saved_cookie_to_other_host(tmp_path):
    def redirect(request):
        if request.url.path == SHOP_PREFIX + '/api/external':
            return httpx.Response(302, headers={'Location': 'https://other.example/private'})
    value, _, calls = build(tmp_path, handler=redirect)
    value.connect('fixture-user', PASSWORD)
    before = len(calls)
    with value.client() as client:
        assert client.get('/api/external').status_code == 302
    assert len(calls) == before + 1


@pytest.mark.parametrize('where,status', [('post', 401), ('post', 403), ('overview', 401), ('overview', 403)])
def test_rejected_login_has_safe_error_and_leaves_previous_session_untouched(tmp_path, where, status):
    value, store, calls = build(tmp_path)
    value.connect('fixture-user', PASSWORD)
    before = stored(store)
    rejected, _, _ = build(tmp_path, post_status=status if where == 'post' else 302,
                           overview_status=status if where == 'overview' else 200)
    with pytest.raises(AppError) as caught:
        rejected.connect('fixture-user', PASSWORD)
    assert caught.value.code == 'uploader_login_rejected'
    assert stored(store) == before
    assert all(secret not in str(caught.value) for secret in (PASSWORD, COOKIE, 'PRIVATE'))


@pytest.mark.parametrize('username,password', [('', PASSWORD), (' user', PASSWORD), ('user\n', PASSWORD),
    ('a' * 201, PASSWORD), (True, PASSWORD), ('fixture-user', ''), ('fixture-user', 'a' * 4097),
    ('fixture-user', ['PRIVATE']), ('fixture-user', 'pass\x00word')])
def test_invalid_credentials_do_not_make_requests(tmp_path, username, password):
    value, store, calls = build(tmp_path)
    with pytest.raises(AppError) as caught:
        value.connect(username, password)
    assert caught.value.status_code == 422 and calls == []
    assert stored(store)['encrypted_cookies'] is None


def test_supported_csrf_hidden_field_is_forwarded_and_next_stays_fixed(tmp_path):
    form = FORM.replace('<input name="user"', '<input type="hidden" name="csrf_token" value="known-token"><input name="user"').replace('value="/"', 'value="https://other.example/private"')
    value, _, calls = build(tmp_path, form=form)
    value.connect('fixture-user', PASSWORD)
    assert parse_qs(calls[1].content.decode())['csrf_token'] == ['known-token']
    assert parse_qs(calls[1].content.decode())['next'] == ['/']


@pytest.mark.parametrize('form', [FORM.replace('action="/login"', 'action="https://other.example/login"'),
    FORM.replace('method="post"', 'method="get"'), FORM.replace('name="user"', 'name="other"'),
    FORM + FORM, '<html>PRIVATE unsupported login</html>'])
def test_unsupported_or_foreign_login_form_never_posts_credentials(tmp_path, form):
    value, store, calls = build(tmp_path, form=form)
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert [(row.method, row.url.path) for row in calls] == [('GET', '/login')]
    assert stored(store)['encrypted_cookies'] is None


def test_external_login_redirect_is_not_followed_or_persisted(tmp_path):
    value, store, calls = build(tmp_path, location='https://other.example/private', overview_status=401)
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert len(calls) == 3 and stored(store)['encrypted_cookies'] is None


@pytest.mark.parametrize('payload', [[], {}, {'info': []}, {'info': {'domain': 'https://PRIVATE/', 'shops': {}}},
    {'info': {'domain': 'blueox179.com', 'shops': []}}])
def test_login_requires_protected_overview_shape(tmp_path, payload):
    value, store, _ = build(tmp_path, payload=payload)
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert stored(store)['encrypted_cookies'] is None


@pytest.mark.parametrize('declared', [True, False])
def test_remote_login_responses_are_bounded(tmp_path, declared):
    def oversized(request):
        if request.url.path == '/login' and request.method == 'GET':
            headers = {'Content-Type': 'text/html'}
            if declared:
                headers['Content-Length'] = str(MAX_RESPONSE_BYTES + 1)
            return httpx.Response(200, headers=headers, content=b' ' * (MAX_RESPONSE_BYTES + 1))
    value, store, _ = build(tmp_path, handler=oversized)
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert stored(store)['encrypted_cookies'] is None


@pytest.mark.parametrize('cookie', [f'session={COOKIE}; Domain=other.example; Path=/; Secure',
    f'session={COOKIE}; Domain=.com; Path=/; Secure',
    f'session={COOKIE}; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT'])
def test_only_exact_host_live_cookies_are_persisted(tmp_path, cookie):
    value, store, _ = build(tmp_path, cookie=cookie)
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert stored(store)['encrypted_cookies'] is None


def test_disconnect_clears_only_tool_session_and_never_calls_network(tmp_path):
    value, store, calls = build(tmp_path)
    value.connect('fixture-user', PASSWORD)
    with store.connect() as db:
        db.execute("INSERT INTO events(action,status,message,created_at) VALUES('untouched','success','Keep','2030-01-01')")
    count = len(calls)
    assert value.disconnect() == {'endpoint': ENDPOINT, 'session_saved': False}
    assert len(calls) == count
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1
    with value.client() as client:
        client.get(ENDPOINT + OVERVIEW_PATH)
    assert 'cookie' not in calls[-1].headers


def test_concurrent_disconnect_fences_in_flight_login(tmp_path):
    second = None
    def disconnect(request):
        if request.url.path == OVERVIEW_PATH:
            second.disconnect()
    value, store, _ = build(tmp_path, handler=disconnect)
    second = UploaderConnection(store, Box(), transport=value.transport)
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert caught.value.code == 'uploader_connection_changed'
    assert value.status()['session_saved'] is False


def test_corrupt_or_oversized_saved_session_is_never_returned_or_used(tmp_path):
    value, store, calls = build(tmp_path)
    for raw in ('PRIVATE INVALID CIPHER', Box().encrypt('x' * (MAX_COOKIE_BYTES + 1)),
                Box().encrypt(json.dumps([{'name': 'cookie', 'value': COOKIE, 'path': '/', 'secure': True, 'expires': None, 'domain': 'evil.example'}]))):
        with store.connect() as db:
            db.execute('UPDATE uploader_remote_session SET encrypted_cookies=? WHERE id=1', (raw,))
        assert value.status() == {'endpoint': ENDPOINT, 'session_saved': False}
        with pytest.raises(AppError) as caught, value.client():
            pass
        assert COOKIE not in str(caught.value) and 'PRIVATE' not in str(caught.value)
    assert calls == []


def test_secret_storage_failure_is_safe(tmp_path):
    class FailedBox(Box):
        def encrypt(self, value):
            raise RuntimeError('PRIVATE SECRET STORAGE ERROR')
    value, store, _ = build(tmp_path, box=FailedBox())
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert 'PRIVATE' not in str(caught.value) and COOKIE not in str(caught.value)
    assert stored(store)['encrypted_cookies'] is None


@pytest.mark.parametrize('timeout', [0, -1, 10.1, True, '5'])
def test_timeout_is_bounded(tmp_path, timeout):
    with pytest.raises(ValueError):
        UploaderConnection(Store(tmp_path), Box(), timeout_seconds=timeout)


@pytest.mark.parametrize('domain', ['eagleglobal2023.com', '.eagleglobal2023.com'])
def test_trusted_parent_cookie_is_narrowed_to_ul_host_only_on_save(tmp_path, domain):
    value, store, calls = build(tmp_path, cookie=f'session={COOKIE}; Domain={domain}; Path=/; Secure; HttpOnly')
    assert value.connect('fixture-user', PASSWORD)['session_saved'] is True
    assert calls[-1].url.host == HOST and COOKIE in calls[-1].headers['cookie']
    encoded = Box().decrypt(stored(store)['encrypted_cookies'])
    assert 'domain' not in encoded and COOKIE in encoded
    fresh = UploaderConnection(store, Box(), transport=value.transport)
    with fresh.client() as client:
        cookies = list(client.cookies.jar)
        assert len(cookies) == 1
        assert cookies[0].domain == HOST and cookies[0].domain_specified is False
        client.get(ENDPOINT + OVERVIEW_PATH)
        before = len(calls)
        for url in ('https://qa.eagleglobal2023.com/api/overview',
                    'https://eagleglobal2023.com/api/overview'):
            with pytest.raises((AppError, GatewayError)):
                client.get(url)
        assert len(calls) == before


@pytest.mark.parametrize('stage,method,path', [
    ('login_form', 'GET', '/login'), ('login_submit', 'POST', '/login'),
    ('login_landing', 'GET', '/'),
    ('overview', 'GET', OVERVIEW_PATH)])
@pytest.mark.parametrize('failure,reason', [(httpx.ReadTimeout, 'timeout'),
                                          (httpx.ConnectError, 'network')])
def test_login_network_failures_identify_only_safe_stage(tmp_path, stage, method, path, failure, reason):
    def fail(request):
        if stage == 'login_landing' and request.url.path == OVERVIEW_PATH:
            return httpx.Response(401, json={'error': 'Login pending'})
        if request.method == method and request.url.path == path:
            raise failure('PRIVATE COOKIE PASSWORD ' + PASSWORD, request=request)
    value, store, _ = build(tmp_path, handler=fail)
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert caught.value.code == 'uploader_' + stage + '_' + reason
    assert caught.value.status_code == 502
    assert value.diagnostics() == {'stage': stage, 'reason': reason, 'http_status': None}
    assert 'PRIVATE' not in str(caught.value) and PASSWORD not in str(caught.value)
    assert stored(store)['encrypted_cookies'] is None


def test_form_overview_cookie_and_save_failures_have_different_safe_codes(tmp_path):
    cases = [
        ({'form': '<html>PRIVATE unsupported form</html>'}, 'uploader_login_form_invalid'),
        ({'payload': {'info': {'domain': 'PRIVATE INVALID', 'shops': {}}}}, 'uploader_overview_domain_invalid'),
        ({'cookie': f'session={COOKIE}; Domain=other.example; Path=/'}, 'uploader_cookie_missing'),
    ]
    for index, (options, code) in enumerate(cases):
        value, _, _ = build(tmp_path / str(index), **options)
        with pytest.raises(AppError) as caught:
            value.connect('fixture-user', PASSWORD)
        assert caught.value.code == code
        assert 'PRIVATE' not in str(caught.value) and COOKIE not in str(caught.value)
    class FailedBox(Box):
        def encrypt(self, value):
            raise RuntimeError('PRIVATE STORAGE CONTENT')
    value, _, _ = build(tmp_path / 'save', box=FailedBox())
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert caught.value.code == 'uploader_save_failed' and caught.value.status_code == 503
    assert 'PRIVATE' not in str(caught.value)


def test_login_has_longer_timeout_without_slowing_normal_readiness_client(tmp_path):
    value, _, calls = build(tmp_path)
    value.connect('fixture-user', PASSWORD)
    for request in calls:
        assert request.extensions['timeout']['read'] == (65 if request.url.path == OVERVIEW_PATH else 20)
        assert request.extensions['timeout']['connect'] == 20
    with value.client() as client:
        client.get(ENDPOINT + OVERVIEW_PATH)
    assert set(calls[-1].extensions['timeout'].values()) == {5}


@pytest.mark.parametrize('timeout', [0, -1, 20.1, True, '20'])
def test_login_timeout_is_bounded_separately(tmp_path, timeout):
    with pytest.raises(ValueError):
        UploaderConnection(Store(tmp_path), Box(), login_timeout_seconds=timeout)


@pytest.mark.parametrize('options,expected', [
    ({'form': '<html>PRIVATE unsupported form</html>'},
     {'stage': 'login_form', 'reason': 'invalid', 'http_status': 200}),
    ({'post_status': 503},
     {'stage': 'login_submit', 'reason': 'unavailable', 'http_status': 503}),
    ({'location': 'https://PRIVATE.example/login', 'overview_status': 401},
     {'stage': 'login_submit', 'reason': 'invalid_redirect', 'http_status': 302}),
    ({'post_status': 403},
     {'stage': 'login_submit', 'reason': 'rejected', 'http_status': 403}),
    ({'overview_status': 401},
     {'stage': 'overview', 'reason': 'rejected', 'http_status': 401}),
    ({'payload': {'info': {'domain': 'PRIVATE INVALID', 'shops': {}}}},
     {'stage': 'overview', 'reason': 'domain_invalid', 'http_status': 200}),
    ({'cookie': f'session={COOKIE}; Domain=other.example; Path=/'},
     {'stage': 'cookie', 'reason': 'missing', 'http_status': None}),
])
def test_last_login_diagnostic_is_fixed_stage_reason_and_status_only(tmp_path, options, expected):
    value, store, calls = build(tmp_path, **options)
    assert value.diagnostics() is None
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert value.diagnostics() == expected
    assert set(value.diagnostics()) == {'stage', 'reason', 'http_status'}
    assert all(secret not in json.dumps(value.diagnostics()) for secret in ('PRIVATE', COOKIE, PASSWORD))
    public = value.diagnostics()
    public['stage'] = 'PRIVATE MUTATION'
    assert value.diagnostics() == expected, 'Callers cannot mutate the stored diagnostic'
    assert value.status() == {'endpoint': ENDPOINT, 'session_saved': False}
    fresh = UploaderConnection(store, Box(), transport=value.transport)
    assert fresh.diagnostics() is None, 'Diagnostics stay in memory, not in shared credential storage'


def test_storage_diagnostic_has_no_raw_encryption_error(tmp_path):
    class FailedBox(Box):
        def encrypt(self, value):
            raise RuntimeError('PRIVATE KEY ERROR')
    value, _, _ = build(tmp_path, box=FailedBox())
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert value.diagnostics() == {'stage': 'save', 'reason': 'failed', 'http_status': None}


def test_success_and_explicit_disconnect_clear_prior_diagnostic(tmp_path):
    value, _, calls = build(tmp_path)
    with pytest.raises(AppError):
        value.connect('', PASSWORD)
    assert value.diagnostics() == {'stage': 'input', 'reason': 'invalid', 'http_status': None}
    assert calls == []
    assert value.connect('fixture-user', PASSWORD)['session_saved'] is True
    assert value.diagnostics() is None
    with pytest.raises(AppError):
        value.connect('', PASSWORD)
    assert value.diagnostics() is not None and value.status()['session_saved'] is True
    value.disconnect()
    assert value.diagnostics() is None and value.status()['session_saved'] is False


@pytest.mark.parametrize('payload,reason', [({}, 'info_missing'), ([], 'info_missing'),
    ({'info': []}, 'info_missing'), ({'info': {'domain': None, 'shops': {}}}, 'domain_invalid'),
    ({'info': {'domain': 'blueox179.com', 'shops': []}}, 'shops_invalid')])
def test_overview_schema_failures_are_distinguishable_without_body_content(tmp_path, payload, reason):
    value, _, _ = build(tmp_path, payload=payload)
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert caught.value.code == 'uploader_overview_' + reason
    assert value.diagnostics() == {'stage': 'overview', 'reason': reason, 'http_status': 200}


@pytest.mark.parametrize('content,content_type,reason', [
    ('PRIVATE HTML BLOCK PAGE', 'text/html', 'content_type'),
    ('PRIVATE INVALID JSON', 'application/json', 'json_invalid')])
def test_overview_content_type_and_json_parse_diagnostics_are_safe(tmp_path, content, content_type, reason):
    def respond(request):
        if request.url.path == OVERVIEW_PATH:
            return httpx.Response(200, content=content, headers={'Content-Type': content_type})
    value, _, _ = build(tmp_path, handler=respond)
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert value.diagnostics() == {'stage': 'overview', 'reason': reason, 'http_status': 200}
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('kind,reason', [('empty', 'missing'), ('other_scope', 'unsupported_scope'),
                                      ('large_value', 'size'), ('large_count', 'size')])
def test_cookie_scope_missing_and_size_reasons_remain_enums(kind, reason):
    cookies = httpx.Cookies()
    if kind == 'other_scope':
        cookies.set('session', COOKIE, domain='other.example', path='/')
    elif kind == 'large_value':
        cookies.set('session', 'X' * 8193, domain=HOST, path='/')
    elif kind == 'large_count':
        for index in range(33):
            cookies.set('session' + str(index), COOKIE, domain=HOST, path='/')
    with pytest.raises(_CookieError) as caught:
        _cookie_values(cookies)
    assert caught.value.reason == reason and COOKIE not in str(caught.value)


def test_cookie_oversize_is_reported_as_cookie_stage_with_no_value(tmp_path):
    value, store, _ = build(tmp_path, cookie='session=' + 'PRIVATE' * 1500 + '; Path=/; Secure')
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert caught.value.code == 'uploader_cookie_size'
    assert value.diagnostics() == {'stage': 'cookie', 'reason': 'size', 'http_status': None}
    assert 'PRIVATE' not in str(caught.value) and stored(store)['encrypted_cookies'] is None


def test_cookie_issued_by_known_landing_get_is_verified_and_saved(tmp_path):
    def respond(request):
        if request.url.path == '/login' and request.method == 'POST':
            return httpx.Response(303, headers={'Location': '/'})
        if request.url.path == '/':
            assert request.method == 'GET'
            return httpx.Response(200, text='Dashboard', headers={
                'Set-Cookie': f'session={COOKIE}; Domain=.eagleglobal2023.com; Path=/; Secure; HttpOnly'})
        if request.url.path == OVERVIEW_PATH:
            if 'cookie' not in request.headers:
                return httpx.Response(401, json={'error': 'Login pending'})
            assert request.headers['cookie'] == 'session=' + COOKIE
    value, store, calls = build(tmp_path, handler=respond)
    assert value.connect('fixture-user', PASSWORD)['session_saved'] is True
    assert sum(row.method == 'POST' for row in calls) == 1
    assert value.diagnostics() is None and stored(store)['encrypted_cookies']


@pytest.mark.parametrize('location', ['/api/session/check', '/api/shop/activate', '/?logout=1',
    '/login?next=https%3A%2F%2FPRIVATE.example', 'https://qa.eagleglobal2023.com/'])
def test_login_redirect_never_follows_api_actions_unknown_queries_or_sibling_host(tmp_path, location):
    value, store, calls = build(tmp_path, location=location, overview_status=401)
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert len(calls) == 3 and sum(row.method == 'POST' for row in calls) == 1
    assert value.diagnostics() == {'stage': 'login_submit', 'reason': 'invalid_redirect', 'http_status': 302}
    assert 'PRIVATE' not in str(caught.value) and stored(store)['encrypted_cookies'] is None


def test_landing_redirect_limit_never_replays_login_or_checks_shop_session(tmp_path):
    def respond(request):
        if request.url.path == '/':
            return httpx.Response(302, headers={'Location': '/'})
        if request.url.path == OVERVIEW_PATH:
            return httpx.Response(401, json={'error': 'Login pending'})
    value, store, calls = build(tmp_path, handler=respond)
    with pytest.raises(AppError):
        value.connect('fixture-user', PASSWORD)
    assert value.diagnostics() == {'stage': 'login_landing', 'reason': 'redirect_limit', 'http_status': 302}
    assert sum(row.method == 'POST' for row in calls) == 1
    assert sum(row.url.path == '/' for row in calls) == 3
    assert sum(row.url.path == OVERVIEW_PATH for row in calls) == 4
    assert all(row.url.path != '/api/session/check' for row in calls)
    assert stored(store)['encrypted_cookies'] is None


def test_landing_redirect_rejects_external_location_without_following(tmp_path):
    def respond(request):
        if request.url.path == '/':
            return httpx.Response(302, headers={'Location': 'https://PRIVATE.example/'})
        if request.url.path == OVERVIEW_PATH:
            return httpx.Response(401, json={'error': 'Login pending'})
    value, store, calls = build(tmp_path, handler=respond)
    with pytest.raises(AppError) as caught:
        value.connect('fixture-user', PASSWORD)
    assert len(calls) == 5
    assert value.diagnostics() == {'stage': 'login_landing', 'reason': 'invalid_redirect', 'http_status': 302}
    assert 'PRIVATE' not in str(caught.value) and stored(store)['encrypted_cookies'] is None


def test_protected_api_proof_after_post_avoids_unneeded_dashboard_navigation(tmp_path):
    value, store, calls = build(tmp_path, location='/dashboard')
    assert value.connect('fixture-user', PASSWORD)['session_saved'] is True
    assert [(row.method, row.url.path) for row in calls] == [
        ('GET', '/login'), ('POST', '/login'), ('GET', OVERVIEW_PATH)]
    assert value.diagnostics() is None and stored(store)['encrypted_cookies']


@pytest.mark.parametrize('pending_status', [401, 403, 302])
def test_api_probe_after_root_redirect_proves_session_before_rejecting_dashboard(tmp_path, pending_status):
    landed = False
    def respond(request):
        nonlocal landed
        if request.url.path == '/login' and request.method == 'POST':
            assert parse_qs(request.content.decode())['password'] == [PASSWORD]
            return httpx.Response(302, headers={'Location': '/'})
        if request.url.path == '/':
            landed = True
            return httpx.Response(302, headers={'Location': '/dashboard',
                'Set-Cookie': f'session={COOKIE}; Domain=.eagleglobal2023.com; Path=/; Secure; HttpOnly'})
        if request.url.path == OVERVIEW_PATH:
            if not landed:
                return httpx.Response(pending_status, json={'error': 'Pending'},
                                      headers={'Location': '/login?next=/'})
            assert request.headers['cookie'] == 'session=' + COOKIE
    value, store, calls = build(tmp_path, handler=respond)
    assert value.connect('fixture-user', PASSWORD)['session_saved'] is True
    assert [(row.method, row.url.path) for row in calls] == [
        ('GET', '/login'), ('POST', '/login'), ('GET', OVERVIEW_PATH),
        ('GET', '/'), ('GET', OVERVIEW_PATH)]
    assert all(row.url.path not in ('/dashboard', '/api/session/check') for row in calls)
    assert value.diagnostics() is None and stored(store)['encrypted_cookies']


def test_hosted_overview_path_is_the_fixed_blueox_shop_namespace():
    assert get_tool_overview_path() == '/s/blueox179.com/api/overview'
    assert OVERVIEW_PATH == get_tool_overview_path()


@pytest.mark.parametrize('path', [ENDPOINT + '/api/overview', ENDPOINT + '/api/upload', '/login', '/',
    '/s/247smileworld.com/api/overview', '/s/247smileworld.com/img/image.png',
    '/s/blueox179.com.other/api/overview', '/s/blueox179.com/api/%2e%2e/secret'])
def test_default_web_client_rejects_unscoped_or_other_shop_paths_before_network(tmp_path, path):
    value, _, calls = build(tmp_path)
    with value.client() as client, pytest.raises(AppError):
        client.get(path)
    assert calls == []


def test_session_revision_is_an_internal_monotonic_fence_not_a_public_secret(tmp_path):
    value, store, _ = build(tmp_path)
    initial = value.session_revision()
    assert type(initial) is int and initial >= 1
    value.connect('fixture-user', PASSWORD)
    assert value.session_revision() == initial + 1
    with pytest.raises(AppError):
        value.connect('', PASSWORD)
    assert value.session_revision() == initial + 1
    value.disconnect()
    assert value.session_revision() == initial + 2
    assert UploaderConnection(store, Box(), transport=value.transport).session_revision() == initial + 2
    assert set(value.status()) == {'endpoint', 'session_saved'}


def test_normal_client_scopes_relative_api_and_image_requests_to_blueox(tmp_path):
    value, _, calls = build(tmp_path)
    with value.client() as client:
        client.get('/api/overview')
        client.get('/img/fixture.png')
    assert [row.url.path for row in calls] == [OVERVIEW_PATH, SHOP_PREFIX + '/img/fixture.png']
    assert all(row.headers['X-PODSach-Expected-Shop'] == 'blueox179.com' for row in calls)


@pytest.mark.parametrize('overview_only', [False, True])
def test_direct_gateway_ticket_cannot_broaden_public_client_scope(tmp_path, overview_only):
    value, _, calls = build(tmp_path)
    ticket_url = ENDPOINT + SHOP_PREFIX + '/_gw/ticket/' + 'a' * 16
    with value.client(overview_only=overview_only) as client, pytest.raises((AppError, GatewayError)):
        client.get(ticket_url)
    assert calls == []


def test_login_resolves_only_its_scoped_gateway_ticket_without_reposting(tmp_path):
    ticket = 'aB0123456789_-cd'
    def respond(request):
        if request.url.path == OVERVIEW_PATH:
            return httpx.Response(202, json={'gateway_pending': ticket})
        if request.url.path == SHOP_PREFIX + '/_gw/ticket/' + ticket:
            assert request.method == 'GET' and 'x-podsach-expected-shop' not in request.headers
            return httpx.Response(200, json=valid_info())
    value, _, calls = build(tmp_path, handler=respond)
    assert value.connect('fixture-user', PASSWORD)['session_saved'] is True
    assert [row.url.path for row in calls] == ['/login', '/login', OVERVIEW_PATH,
        SHOP_PREFIX + '/_gw/ticket/' + ticket]
    assert sum(row.method == 'POST' for row in calls) == 1
    assert value.diagnostics() is None
