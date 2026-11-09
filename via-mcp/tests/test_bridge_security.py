"""Independent security checks using temporary media and offline HTTP only."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from via_mcp.bridge import ManagerClient
from via_mcp.common import Inputs, MutationInput, Tool, ToolError
from via_mcp.journal import Journal
from via_mcp.media import MediaVault
from via_mcp.registry import Registry


CM_COOKIE = 'fixture-cm-cookie-private'
PAGE_COOKIE = 'fixture-page-cookie-private'
CSRF = 'fixture-csrf-private'
TOKEN = 'EAA' + 'X' * 40
SECRETS = [CM_COOKIE, PAGE_COOKIE, CSRF, TOKEN, 'fixture-pass-private',
           'fixture@example.invalid', 'fixture-bearer-private', 'fixture-oauth-private']
VIDEO = b'\x00\x00\x00\x20ftypisom' + b'\x00' * 64
PNG = b'\x89PNG\r\n\x1a\n' + b'fixture-image-bytes'


def assert_public(value, extra=()):
    wire = json.dumps(value, ensure_ascii=False, default=str)
    assert all(secret not in wire for secret in [*SECRETS, *extra])


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def build(handler=None):
        seen = []

        def respond(request):
            seen.append(request)
            assert request.url.scheme == 'http'
            assert request.url.host == '127.0.0.1'
            assert request.url.port == 8000
            assert request.headers['origin'] == 'http://127.0.0.1:8000'
            if request.url.path in ('/api/session', '/pages/api/session'):
                pages = request.url.path.startswith('/pages/')
                cookie = f'mpm_pages_session={PAGE_COOKIE}; Path=/pages/' if pages else f'cm_session={CM_COOKIE}; Path=/'
                return httpx.Response(200, json={'csrf_token': CSRF} if pages else {'ok': True},
                                      headers={'set-cookie': cookie})
            return handler(request) if handler else httpx.Response(200, json={'ok': True})

        vault = MediaVault([tmp_path / 'media'])
        client = ManagerClient(8000, vault, transport=httpx.MockTransport(respond))
        clients.append(client)
        return client, seen, vault

    yield build
    for client in clients:
        client.close()


def media_file(tmp_path, name='clip.mp4', content=VIDEO):
    root = tmp_path / 'media'
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(content)
    return root, path


@pytest.mark.parametrize('port', [True, False, '8000', 0, 1023, 65536, -8000, None])
def test_only_explicit_valid_local_port(port):
    with pytest.raises(ValueError):
        ManagerClient(port, None)


@pytest.mark.parametrize(('workspace', 'path'), [
    ('other', '/api/status'), ('pages', 'https://evil.invalid/api/status'),
    ('chrome', '//evil.invalid/api/status'), ('chrome', '/api/status?token=x'),
    ('chrome', '/api/status#fragment'), ('pages', '/api/../session'),
    ('pages', '/api/%2e%2e/session'), ('pages', '/api//session'),
    ('pages', '/pages/api/session'), ('chrome', '/api/status\r\nX-Token:x'),
    ('chrome', '/api/status\\more'), ('chrome', None),
])
def test_route_escape_rejected_before_any_request(make_client, workspace, path):
    client, seen, _ = make_client()
    with pytest.raises(ToolError) as issue:
        client.request(workspace, 'GET', path, read_only=True)
    assert issue.value.code == 'invalid_route'
    assert seen == []


def test_session_secrets_and_cookie_jars_are_isolated(make_client):
    observed = []

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={'ok': True})

    client, _, _ = make_client(handler)
    assert_public(client.request('chrome', 'GET', '/api/profiles', read_only=True))
    assert_public(client.request('pages', 'POST', '/api/pages/123/edit', body={'about': 'Fixture'}))
    assert_public(client.request('chrome', 'POST', '/api/profiles/1/open'))
    chrome_read, page_write, chrome_write = observed
    assert CM_COOKIE in chrome_read.headers['cookie']
    assert PAGE_COOKIE not in chrome_read.headers['cookie']
    assert PAGE_COOKIE in page_write.headers['cookie']
    assert CM_COOKIE not in page_write.headers['cookie']
    assert page_write.headers['x-mpm-csrf'] == CSRF
    assert 'x-mpm-csrf' not in chrome_write.headers
    assert PAGE_COOKIE not in chrome_write.headers['cookie']
    assert all(not c.trust_env and not c.follow_redirects for c in client.clients.values())


@pytest.mark.parametrize('failure', ['wrong_cookie', 'missing_csrf', 'oversize_csrf'])
def test_bad_session_never_reaches_selected_endpoint_or_returns_secret(tmp_path, failure):
    seen = []

    def respond(request):
        seen.append(request)
        cookie = f'cm_session={CM_COOKIE}; Path=/' if failure == 'wrong_cookie' else f'mpm_pages_session={PAGE_COOKIE}; Path=/pages/'
        csrf = '' if failure == 'missing_csrf' else 'X' * 201 if failure == 'oversize_csrf' else CSRF
        return httpx.Response(200, json={'csrf_token': csrf, 'token': TOKEN}, headers={'set-cookie': cookie})

    client = ManagerClient(8000, MediaVault([tmp_path]), transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ToolError) as issue:
            client.request('pages', 'GET', '/api/pages', read_only=True)
        assert issue.value.code == 'session_failed'
        assert [request.url.path for request in seen] == ['/pages/api/session']
        assert_public(issue.value.message)
    finally:
        client.close()


def test_result_defence_removes_nested_credentials_paths_and_known_secret_text(make_client, tmp_path):
    hidden_path = str(tmp_path / 'private-profile')
    raw = {'name': 'Allowed name', 'access_token': TOKEN, 'password': 'fixture-pass-private',
           'path': hidden_path, 'csrf_token': CSRF, 'cookies': CM_COOKIE,
           'nested': [{'encrypted_token': TOKEN, 'proxy_password': 'fixture-pass-private', 'safe': 7}],
           'message': f'Bearer fixture-bearer-private OAuth fixture-oauth-private {TOKEN} fixture@example.invalid '
                      'http://fixture-user:fixture-pass-private@127.0.0.1/'}
    client, _, _ = make_client(lambda _: httpx.Response(200, json=raw))
    result = client.request('chrome', 'GET', '/api/profiles', read_only=True)
    assert result['name'] == 'Allowed name'
    assert result['nested'] == [{'safe': 7}]
    assert_public(result, [hidden_path])


def test_redirect_is_never_followed_or_echoed(make_client):
    client, seen, _ = make_client(lambda _: httpx.Response(302, json={'token': TOKEN},
        headers={'location': 'https://evil.invalid/steal'}))
    with pytest.raises(ToolError) as issue:
        client.request('pages', 'GET', '/api/pages', read_only=True)
    assert issue.value.code == 'upstream_rejected'
    assert [r.url.path for r in seen] == ['/pages/api/session', '/pages/api/pages']
    assert_public(issue.value.message)


def test_read_session_refresh_has_one_retry_only(make_client):
    count = 0

    def handler(_):
        nonlocal count
        count += 1
        return httpx.Response(401 if count == 1 else 200, json={'safe': True})

    client, seen, _ = make_client(handler)
    assert client.request('pages', 'GET', '/api/pages', read_only=True) == {'safe': True}
    assert [r.method for r in seen] == ['GET'] * 4
    assert [r.url.path for r in seen].count('/pages/api/session') == 2


@pytest.mark.parametrize('status', [401, 403])
@pytest.mark.parametrize('method', ['POST', 'PATCH', 'PUT', 'DELETE'])
def test_mutation_is_not_replayed_after_auth_failure(make_client, status, method):
    client, seen, _ = make_client(lambda _: httpx.Response(status, json={'token': TOKEN}))
    with pytest.raises(ToolError) as issue:
        client.request('pages', method, '/api/pages/123/edit', body={'about': 'One write'})
    assert issue.value.code == 'session_changed'
    assert len([r for r in seen if r.method == method]) == 1
    assert len([r for r in seen if r.url.path == '/pages/api/session']) == 2
    assert_public(issue.value.message)


@pytest.mark.parametrize('method', ['POST', 'PATCH', 'PUT', 'DELETE'])
def test_read_only_flag_cannot_replay_a_mutation(make_client, method):
    client, seen, _ = make_client(lambda _: httpx.Response(401, json={}))
    with pytest.raises(ToolError) as issue:
        client.request('pages', method, '/api/pages/123/edit', read_only=True)
    assert issue.value.code == 'invalid_read_mode'
    assert seen == []


def test_only_known_semantic_read_post_can_refresh(make_client):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(401 if len(attempts) == 1 else 200, json={'allowed_fields': ['about']})

    client, seen, _ = make_client(handler)
    assert client.request('pages', 'POST', '/api/pages/123/edit-options', read_only=True)['allowed_fields'] == ['about']
    assert len(attempts) == 2
    assert all(r.url.path == '/pages/api/pages/123/edit-options' for r in attempts)
    assert [r.method for r in seen] == ['GET', 'POST', 'GET', 'POST']


def test_repeated_denial_does_not_loop(make_client):
    client, seen, _ = make_client(lambda _: httpx.Response(403, json={}))
    with pytest.raises(ToolError) as issue:
        client.request('chrome', 'GET', '/api/profiles', read_only=True)
    assert issue.value.code == 'upstream_rejected'
    assert len(seen) == 4


@pytest.mark.parametrize(('method', 'read_only', 'uncertain'), [('GET', True, False), ('POST', False, True)])
@pytest.mark.parametrize('failure', ['json', 'huge', 'timeout'])
def test_upstream_failures_are_bounded_and_do_not_echo_response(make_client, method, read_only, uncertain, failure):
    def handler(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout(f'Bearer fixture-bearer-private {TOKEN}', request=request)
        if failure == 'huge':
            return httpx.Response(200, content=b'x' * (4 * 1024 * 1024 + 1))
        return httpx.Response(200, content=f'not-json {TOKEN}'.encode())

    client, seen, _ = make_client(handler)
    with pytest.raises(ToolError) as issue:
        client.request('pages', method, '/api/pages', read_only=read_only)
    assert issue.value.uncertain is uncertain
    assert_public(issue.value.message)
    assert len([r for r in seen if r.url.path == '/pages/api/pages']) == 1


def test_media_handles_are_opaque_session_local_and_typed(tmp_path):
    root, path = media_file(tmp_path)
    vault = MediaVault([root])
    public = vault.register(str(path), 'video')
    assert len(public['file_id']) == 32
    assert int(public['file_id'], 16) >= 0
    assert public['name'] == 'clip.mp4'
    assert 'sha256' not in public and 'path' not in public
    assert str(root) not in json.dumps(public)
    with vault.open(public['file_id'], 'video') as (stream, row):
        assert stream.read() == VIDEO
        assert row['sha256'] == hashlib.sha256(VIDEO).hexdigest()
    for other, kind in [(vault, 'image'), (MediaVault([root]), 'video')]:
        with pytest.raises(ToolError) as issue:
            with other.open(public['file_id'], kind):
                pytest.fail('Invalid handle opened')
        assert issue.value.code == 'invalid_file_id'


@pytest.mark.parametrize('bad', ['relative.mp4', 'C:\\fixture\x00.mp4', 'https://example.invalid/video.mp4'])
def test_media_rejects_non_local_absolute_path(tmp_path, bad):
    with pytest.raises(ToolError):
        MediaVault([tmp_path]).register(bad, 'video')


@pytest.mark.parametrize('blocked', ['.git', '.codex', '.aws', 'data', 'profiles', 'logs', 'node_modules', 'browser-data'])
def test_media_rejects_sensitive_directory_even_beneath_allowed_root(tmp_path, blocked):
    folder = tmp_path / blocked
    folder.mkdir()
    path = folder / 'clip.mp4'
    path.write_bytes(VIDEO)
    with pytest.raises(ToolError) as issue:
        MediaVault([tmp_path]).register(str(path), 'video')
    assert issue.value.code == 'media_root_required'


def test_media_rejects_outside_root_and_wrong_extension(tmp_path):
    root, path = media_file(tmp_path)
    with pytest.raises(ToolError):
        MediaVault([root / 'narrower']).register(str(path), 'video')
    text = root / 'private.txt'
    text.write_bytes(VIDEO)
    with pytest.raises(ToolError):
        MediaVault([root]).register(str(text), 'video')


@pytest.mark.parametrize(('name', 'content', 'kind'), [
    ('empty.mp4', b'', 'video'), ('fake.mp4', b'not a video', 'video'),
    ('fake.jpg', b'not an image', 'image'), ('fake.png', VIDEO, 'image'),
])
def test_media_rejects_empty_or_wrong_signature(tmp_path, name, content, kind):
    root, path = media_file(tmp_path, name, content)
    with pytest.raises(ToolError) as issue:
        MediaVault([root]).register(str(path), kind)
    assert issue.value.code == 'invalid_media'
    assert str(path) not in issue.value.message


@pytest.mark.parametrize('change', ['same_size', 'longer', 'removed'])
def test_registered_file_is_rehashed_before_reuse(tmp_path, change):
    root, path = media_file(tmp_path)
    vault = MediaVault([root])
    file_id = vault.register(str(path), 'video')['file_id']
    if change == 'removed':
        path.unlink()
    else:
        path.write_bytes(VIDEO[:-1] + b'Z' if change == 'same_size' else VIDEO + b'Z')
    with pytest.raises(ToolError) as issue:
        with vault.open(file_id, 'video'):
            pytest.fail('Changed media opened')
    assert issue.value.code == 'invalid_media'


@pytest.mark.parametrize('link_method', ['is_symlink', 'is_junction'])
def test_media_rejects_link_at_parent_boundary_without_following(tmp_path, monkeypatch, link_method):
    root, path = media_file(tmp_path)
    original = getattr(Path, link_method)
    monkeypatch.setattr(Path, link_method, lambda self: self == root or original(self))
    with pytest.raises(ToolError) as issue:
        MediaVault([tmp_path]).register(str(path), 'video')
    assert issue.value.code == 'invalid_media'


@pytest.mark.parametrize(('field', 'value', 'kind'), [('st_nlink', 2, 'video'), ('st_size', 1073741825, 'video'), ('st_size', 8388609, 'image')])
def test_media_rejects_hardlinked_and_oversize_file_without_reading(tmp_path, monkeypatch, field, value, kind):
    root, path = media_file(tmp_path, 'clip.mp4' if kind == 'video' else 'portrait.png', VIDEO if kind == 'video' else PNG)
    original = Path.lstat

    def fake_lstat(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self == path:
            data = {key: getattr(result, key) for key in ['st_mode', 'st_nlink', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns']}
            data[field] = value
            return SimpleNamespace(**data)
        return result

    monkeypatch.setattr(Path, 'lstat', fake_lstat)
    with pytest.raises(ToolError) as issue:
        MediaVault([root]).register(str(path), kind)
    assert issue.value.code == 'invalid_media'


def test_media_session_handle_count_is_bounded(tmp_path):
    root, path = media_file(tmp_path)
    vault = MediaVault([root])
    for _ in range(128):
        vault.register(str(path), 'video')
    with pytest.raises(ToolError) as issue:
        vault.register(str(path), 'video')
    assert issue.value.code == 'media_limit'


@pytest.mark.parametrize(('workspace', 'kind', 'name', 'content', 'params', 'route', 'mime'), [
    ('chrome', 'video', 'clip.mp4', VIDEO, {}, '/api/reels/assets', 'application/octet-stream'),
    ('pages', 'video', 'clip.mov', VIDEO, {}, '/pages/api/reels/assets', 'application/octet-stream'),
    ('pages', 'avatar', 'portrait.png', PNG, {'page_id': '123', 'connection_id': 7, 'request_id': 'fixture-request'}, '/pages/api/pages/123/image', 'image/png'),
])
def test_upload_reads_only_registered_bytes_and_does_not_return_private_metadata(make_client, tmp_path, workspace, kind, name, content, params, route, mime):
    captured = []

    def handler(request):
        captured.append((request, request.read()))
        return httpx.Response(200, json={'id': 'public-id', 'path': 'private-path', 'token': TOKEN})

    client, _, vault = make_client(handler)
    _, path = media_file(tmp_path, name, content)
    file_id = vault.register(str(path), 'video' if kind == 'video' else 'image')['file_id']
    assert client.upload(workspace, file_id, kind=kind, params=params) == {'id': 'public-id'}
    request, body = captured[0]
    assert request.method == 'POST' and request.url.path == route
    assert body == content
    assert request.headers['content-type'] == mime
    assert int(request.headers['content-length']) == len(content)
    assert request.headers['x-filename'] == name
    assert file_id not in str(request.url)


def test_changed_media_never_reaches_upload_post(make_client, tmp_path):
    client, seen, vault = make_client()
    _, path = media_file(tmp_path)
    file_id = vault.register(str(path), 'video')['file_id']
    path.write_bytes(VIDEO[:-1] + b'Z')
    with pytest.raises(ToolError):
        client.upload('chrome', file_id)
    assert not any(request.method == 'POST' for request in seen)


def test_upload_auth_failure_has_no_automatic_replay(make_client, tmp_path):
    client, seen, vault = make_client(lambda request: httpx.Response(401, json={}))
    _, path = media_file(tmp_path)
    file_id = vault.register(str(path), 'video')['file_id']
    with pytest.raises(ToolError) as issue:
        client.upload('chrome', file_id)
    assert issue.value.code == 'upload_failed'
    assert len([request for request in seen if request.method == 'POST']) == 1


def test_change_after_preflight_is_uncertain_and_never_automatically_uploaded_again(make_client, tmp_path, monkeypatch):
    def handler(request):
        request.read()
        return httpx.Response(200, json={'id': 'must-not-be-accepted'})

    client, seen, vault = make_client(handler)
    _, path = media_file(tmp_path)
    file_id = vault.register(str(path), 'video')['file_id']
    real_open = vault.open

    @contextmanager
    def changed_after_preflight(handle_id, kind):
        with real_open(handle_id, kind) as (handle, row):
            # Simulate a local writer changing the already verified file before
            # the HTTP body is consumed. This touches this temporary file only.
            path.write_bytes(VIDEO[:-1] + b'Z')
            yield handle, row

    monkeypatch.setattr(vault, 'open', changed_after_preflight)
    with pytest.raises(ToolError) as issue:
        client.upload('chrome', file_id)
    assert issue.value.code == 'media_changed' and issue.value.uncertain is True
    # MockTransport eagerly consumes a streaming request before its responder,
    # so it may reject the body before the responder observes even one POST.
    assert len([request for request in seen if request.method == 'POST']) <= 1


@pytest.mark.parametrize('kind', ['avatar', 'cover'])
def test_image_change_after_preflight_blocks_page_mutation_before_post(make_client, tmp_path, monkeypatch, kind):
    client, seen, vault = make_client()
    _, path = media_file(tmp_path, 'portrait.png', PNG)
    file_id = vault.register(str(path), 'image')['file_id']
    real_open = vault.open

    @contextmanager
    def changed_after_preflight(handle_id, media_kind):
        with real_open(handle_id, media_kind) as (handle, row):
            path.write_bytes(PNG[:-1] + b'Z')
            yield handle, row

    monkeypatch.setattr(vault, 'open', changed_after_preflight)
    with pytest.raises(ToolError) as issue:
        client.upload('pages', file_id, kind=kind, params={'page_id': '123', 'connection_id': 7, 'request_id': 'fixture-request'})
    assert issue.value.code == 'media_changed' and issue.value.uncertain is False
    assert not any(request.method == 'POST' for request in seen)


@pytest.mark.parametrize('kind', ['avatar', 'cover'])
def test_image_bytes_are_frozen_before_the_http_mutation(make_client, tmp_path, kind):
    captured = []
    _, path = media_file(tmp_path, 'portrait.png', PNG)

    def handler(request):
        path.write_bytes(PNG[:-1] + b'Z')
        captured.append(request.read())
        return httpx.Response(200, json={'ok': True, 'outcome': 'accepted'})

    client, seen, vault = make_client(handler)
    file_id = vault.register(str(path), 'image')['file_id']
    result = client.upload('pages', file_id, kind=kind, params={'page_id': '123', 'connection_id': 7, 'request_id': 'fixture-request'})
    assert result['outcome'] == 'accepted' and captured == [PNG]
    assert len([request for request in seen if request.method == 'POST']) == 1


def test_error_results_and_durable_journal_are_sanitized(tmp_path):
    class Change(MutationInput):
        value: str

    message = f'Bearer fixture-bearer-private OAuth fixture-oauth-private {TOKEN} fixture@example.invalid'

    def fail(*_):
        raise ToolError(message, 'fixture_failure', uncertain=True)

    journal = Journal(tmp_path)
    registry = Registry(None, None, journal, catalog=[Tool('fixture_write', 'Offline fixture', Change, fail, read_only=False)])
    operation_id = str(uuid4())
    result = registry.call('fixture_write', {'operation_id': operation_id, 'value': 'authorized'})
    assert result['ok'] is False and result['uncertain'] is True
    assert_public(result)
    assert_public(journal.get(operation_id))
    assert TOKEN.encode() not in journal.path.read_bytes()


def test_registry_output_bound_remains_an_execution_error(tmp_path):
    # Each string is below protect()'s text cap; the aggregate exceeds 1 MiB.
    registry = Registry(None, None, Journal(tmp_path), catalog=[Tool('large', 'Offline fixture', Inputs,
        lambda *_: {'rows': ['x' * 20000 for _ in range(60)]})])
    result = registry.call('large', {})
    assert result['ok'] is False and result['code'] == 'result_too_large'

