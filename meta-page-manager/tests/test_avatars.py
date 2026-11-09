"""Page avatars use fake Graph/CDN reads and disposable local state only."""
from io import BytesIO
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI
import httpx
from PIL import Image, PngImagePlugin
import pytest

from page_manager import avatars as avatar_module
from page_manager.avatars import AvatarUnavailable, PageAvatars
from page_manager.app import create_app
from page_manager.graph import GraphError
from page_manager.inputs import ConnectionInput, ConnectionUpdate
from page_manager.service import PageManager
from conftest import FixtureBox, immediate
from test_http_security import session, mounted_session


PID = '555'
TOKEN = 'FixturePrivateConnectionToken82'
PAGE_TOKEN = 'FixturePrivatePageToken82'
CDN_URL = 'https://scontent.xx.fbcdn.net/avatar.png?hash=private-signed-value'


def picture_bytes(color='red', *, metadata=False, size=(20, 20), format='PNG'):
    image = Image.new('RGB', size, color)
    output = BytesIO()
    options = {}
    if metadata:
        info = PngImagePlugin.PngInfo()
        info.add_text('private', TOKEN + PAGE_TOKEN)
        options['pnginfo'] = info
    image.save(output, format=format, **options)
    return output.getvalue()


PNG = picture_bytes(metadata=True)


class GraphFixture:
    def __init__(self):
        self.calls = []
        self.ids = {PAGE_TOKEN: PID}
        self.url = CDN_URL
        self.silhouette = False
        self.return_id = None
        self.error = None
        self.on_identity = self.on_picture = None
        self.deferred = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def identity(self, token):
        self.calls.append(('identity', token))
        if self.on_identity:
            self.on_identity()
        return {'id': self.ids.get(token)}

    async def page_token(self, token, pid):
        self.calls.append(('page_token', token, pid))
        return PAGE_TOKEN

    async def get(self, pid, token, params):
        self.calls.append(('picture', pid, token, params))
        assert params == {'fields': 'id,picture.type(large)'}
        if self.on_picture:
            self.on_picture()
        if self.error:
            raise self.error
        if self.deferred:
            await Deferred(('picture', pid))
        return {'id': self.return_id or pid, 'picture': {'data': {'url': self.url, 'is_silhouette': self.silhouette}}}


def link(manager, graph, *, pid=PID, cid=None, token=PAGE_TOKEN):
    if cid is None:
        cid = manager.save_connection(ConnectionInput(label='Avatar fixture', token_type='system_user', token=TOKEN))['id']
    row = manager.store.connection(cid)
    manager.store.sync(cid, row['token_revision'], [{'id': pid, 'name': 'Fixture Page ' + pid,
        'sources': ['assigned_pages'], 'encrypted_page_token': manager.secrets.encrypt(token)}], False, 'working', None, {})
    graph.ids[token] = pid
    return cid


@pytest.fixture
def setup(tmp_path):
    graph = GraphFixture()
    manager = PageManager(tmp_path, graph_factory=lambda: graph, secret_box=FixtureBox())
    cid = link(manager, graph)
    calls = []
    response = {'raw': PNG, 'mime': 'image/png', 'status': 200, 'headers': {}}

    def respond(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url.scheme == 'https' and request.url.host == 'scontent.xx.fbcdn.net'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert TOKEN not in str(request.url) and PAGE_TOKEN not in str(request.url)
        if callable(response.get('callback')):
            response['callback']()
        return httpx.Response(response['status'], content=response['raw'],
            headers={'content-type': response['mime'], **response['headers']})

    clock = [0.0]
    avatars = PageAvatars(manager, transport=httpx.MockTransport(respond), clock=lambda: clock[0])
    return manager, graph, cid, avatars, calls, response, clock


def http_request(app, path, *, headers=None, host='127.0.0.1:8010', method='GET', raw=b''):
    target = urlsplit(path)
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
        'scheme': 'http', 'path': target.path, 'raw_path': target.path.encode(), 'query_string': target.query.encode(),
        'root_path': '', 'client': ('127.0.0.1', 1234), 'server': ('127.0.0.1', int(host.rsplit(':', 1)[1])),
        'headers': [(key.lower().encode(), value.encode()) for key, value in {'host': host, **(headers or {})}.items()]}
    messages = []
    supplied = False

    async def receive():
        nonlocal supplied
        if supplied:
            return {'type': 'http.disconnect'}
        supplied = True
        return {'type': 'http.request', 'body': raw, 'more_body': False}

    async def send(message):
        messages.append(message)

    immediate(app(scope, receive, send))
    start = next(message for message in messages if message['type'] == 'http.response.start')
    response_headers = {key.decode(): value.decode() for key, value in start['headers']}
    body = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
    return start['status'], response_headers, body


def test_real_page_token_and_exact_metadata_binding_yield_metadata_free_png_without_state_writes(setup):
    manager, graph, cid, avatars, calls, _, _ = setup
    before = manager.store.page(PID), manager.store.connection(cid), manager.store.catalog()
    raw = immediate(avatars.get(PID))
    assert raw.startswith(b'\x89PNG\r\n\x1a\n')
    with Image.open(BytesIO(raw)) as image:
        assert image.size == (20, 20) and 'private' not in image.info
    assert TOKEN.encode() not in raw and PAGE_TOKEN.encode() not in raw
    assert graph.calls == [('identity', PAGE_TOKEN), ('picture', PID, PAGE_TOKEN, {'fields': 'id,picture.type(large)'})]
    assert len(calls) == 1
    assert before == (manager.store.page(PID), manager.store.connection(cid), manager.store.catalog())


def test_cache_reuses_bytes_without_new_graph_or_cdn_calls_and_expires(setup):
    _, graph, _, avatars, calls, _, clock = setup
    first = immediate(avatars.get(PID))
    assert immediate(avatars.get(PID)) == first
    assert len(graph.calls) == 2 and len(calls) == 1
    clock[0] = avatar_module.TTL_SECONDS + 1
    assert immediate(avatars.get(PID)) == first
    assert len(graph.calls) == 4 and len(calls) == 2


def test_negative_cache_has_fixed_expiry_and_does_not_slide_on_repeated_render(setup):
    _, graph, _, avatars, calls, response, clock = setup
    response['status'] = 404
    for second in (0, 30, 59):
        clock[0] = second
        with pytest.raises(AvatarUnavailable):
            immediate(avatars.get(PID))
    assert len(calls) == 1 and len(graph.calls) == 2
    clock[0] = 61
    response['status'] = 200
    assert immediate(avatars.get(PID)).startswith(b'\x89PNG')
    assert len(calls) == 2


@pytest.mark.parametrize('mutation', ['rotate', 'disconnect'])
def test_current_binding_is_required_even_for_cached_avatar(setup, mutation):
    manager, graph, cid, avatars, calls, _, _ = setup
    immediate(avatars.get(PID))
    if mutation == 'rotate':
        manager.save_connection(ConnectionUpdate(label='New revision', token_type='system_user', token='FixtureReplacementToken82', revision=manager.store.connection(cid)["revision"]), cid)
    else:
        manager.disconnect(cid)
    with pytest.raises(AvatarUnavailable):
        immediate(avatars.get(PID))
    assert len(graph.calls) == 2 and len(calls) == 1


def test_explicit_source_cannot_borrow_another_connection_or_page(setup):
    manager, graph, _, avatars, calls, _, _ = setup
    other = link(manager, graph, pid='777', token='FixtureOtherPageToken82')
    with pytest.raises(AvatarUnavailable):
        immediate(avatars.get(PID, other))
    assert not graph.calls and not calls


@pytest.mark.parametrize('when', ['identity', 'picture', 'cdn'])
def test_revision_change_during_any_external_read_cannot_cache_or_return_avatar(setup, when):
    manager, graph, cid, avatars, calls, response, _ = setup

    def rotate():
        manager.save_connection(ConnectionUpdate(label='Rotated', token_type='system_user', token='FixtureReplacementToken82', revision=manager.store.connection(cid)["revision"]), cid)

    if when == 'identity':
        graph.on_identity = rotate
    elif when == 'picture':
        graph.on_picture = rotate
    else:
        response['callback'] = rotate
    with pytest.raises(AvatarUnavailable):
        immediate(avatars.get(PID))
    assert not any(raw for _, raw in avatars.cache.values())
    assert len(calls) == (1 if when == 'cdn' else 0)


@pytest.mark.parametrize('problem', ['wrong_identity', 'wrong_picture_page', 'silhouette', 'blocked'])
def test_missing_or_foreign_proof_never_reaches_cdn(setup, problem):
    manager, graph, cid, avatars, calls, _, _ = setup
    if problem == 'wrong_identity':
        graph.ids[PAGE_TOKEN] = '777'
    elif problem == 'wrong_picture_page':
        graph.return_id = '777'
    elif problem == 'silhouette':
        graph.silhouette = True
    else:
        source = manager.store.connection(cid)
        manager.store.health(cid, source['token_revision'], status='api_access_blocked')
    with pytest.raises(AvatarUnavailable) as issue:
        immediate(avatars.get(PID))
    assert not calls and str(issue.value) == ''
    if problem == 'blocked':
        assert not graph.calls


@pytest.mark.parametrize('url', [
    'http://scontent.xx.fbcdn.net/a.png', 'https://scontent.xx.fbcdn.net:444/a.png',
    'https://scontent.xx.fbcdn.net.evil.invalid/a.png', 'https://fbcdn.net.evil.invalid/a.png',
    'https://evil.invalid/a.png', 'https://127.0.0.1/a.png', 'https://[::1]/a.png',
    'https://name:password@scontent.xx.fbcdn.net/a.png', 'https://scontent.xx.fbcdn.net/a.png#fragment',
    'https://scontent.xx.fbcdn.net/a.png?access_token=secret',
    'https://scontent.xx.fbcdn.net/a.png?Access_Token=secret',
    'https://scontent.xx.fbcdn.net/a.png?token=secret',
    'https://scontent.xx.fbcdn.net/a.png?hash=' + PAGE_TOKEN,
    'https://scontent.xx.fbcdn.net/a.png\n', None,
])
def test_untrusted_cdn_url_is_rejected_before_any_download(setup, url):
    _, graph, _, avatars, calls, _, _ = setup
    graph.url = url
    with pytest.raises(AvatarUnavailable):
        immediate(avatars.get(PID))
    assert not calls


@pytest.mark.parametrize('url', ['https://scontent.xx.fbcdn.net/a.png?stp=normal',
                               'https://scontent-abc.fbcdn.net:443/a.png',
                               'https://platform-lookaside.fbsbx.com/platform/profilepic/?hash=normal'])
def test_only_explicit_meta_cdn_hosts_with_default_https_port_are_accepted(url):
    assert avatar_module._cdn_url(url, (TOKEN, PAGE_TOKEN)) == url


@pytest.mark.parametrize('failure', ['redirect', 'huge_header', 'huge_body', 'svg', 'mismatch', 'broken', 'too_many_pixels'])
def test_redirect_and_unsafe_raster_are_rejected_without_raw_provider_leak(setup, failure):
    _, _, _, avatars, calls, response, _ = setup
    if failure == 'redirect':
        response['status'] = 302
        response['headers'] = {'location': 'https://evil.invalid/' + TOKEN}
    elif failure == 'huge_header':
        response['headers'] = {'content-length': str(avatar_module.MAX_DOWNLOAD_BYTES + 1)}
    elif failure == 'huge_body':
        response['raw'] = b'x' * (avatar_module.MAX_DOWNLOAD_BYTES + 1)
    elif failure == 'svg':
        response.update(raw=b'<svg onload="private"/>', mime='image/svg+xml')
    elif failure == 'mismatch':
        response['mime'] = 'image/jpeg'
    elif failure == 'broken':
        response['raw'] = PNG[:30]
    else:
        response['raw'] = picture_bytes(size=(2001, 2000))
    with pytest.raises(AvatarUnavailable) as issue:
        immediate(avatars.get(PID))
    assert str(issue.value) == '' and len(calls) == 1


@pytest.mark.parametrize('format,mime', [('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')])
def test_supported_still_rasters_are_reencoded_and_dimension_bounded(setup, format, mime):
    _, _, _, avatars, _, response, _ = setup
    response.update(raw=picture_bytes(size=(900, 600), format=format), mime=mime)
    raw = immediate(avatars.get(PID))
    with Image.open(BytesIO(raw)) as image:
        assert image.format == 'PNG' and max(image.size) <= 512


def test_cache_invalidation_fetches_new_pixels_without_mutating_meta(setup):
    _, graph, _, avatars, calls, response, _ = setup
    first = immediate(avatars.get(PID))
    response['raw'] = picture_bytes('blue')
    assert immediate(avatars.get(PID)) == first
    avatars.invalidate(PID)
    assert immediate(avatars.get(PID)) != first
    assert len(calls) == 2 and all(call[0] in ('identity', 'picture') for call in graph.calls)


def test_memory_cache_enforces_entry_and_byte_budgets_after_real_fetches(setup, monkeypatch):
    manager, graph, cid, avatars, _, _, _ = setup
    monkeypatch.setattr(avatar_module, 'MAX_ENTRIES', 2)
    for pid in ('555', '777', '888'):
        link(manager, graph, pid=pid, cid=cid, token='FixturePageToken' + pid)
        immediate(avatars.get(pid))
    assert len(avatars.cache) == 2 and all(key[0] != '555' for key in avatars.cache)
    budget = len(next(iter(avatars.cache.values()))[1]) + 1
    monkeypatch.setattr(avatar_module, 'CACHE_BYTE_BUDGET', budget)
    avatars.invalidate('777')
    immediate(avatars.get('777'))
    assert avatars.cache_bytes <= budget and len(avatars.cache) == 1


class Deferred:
    def __init__(self, marker):
        self.marker = marker

    def __await__(self):
        yield self.marker


class OfflineFuture:
    def __init__(self):
        self.ready = False

    def __await__(self):
        if not self.ready:
            yield 'waiting-lock'

    def done(self):
        return self.ready

    def cancelled(self):
        return False

    def set_result(self, _):
        self.ready = True


def test_simultaneous_lazy_requests_for_same_page_coalesce_under_page_lock(setup, monkeypatch):
    _, graph, _, avatars, calls, _, _ = setup
    graph.deferred = True
    loop = type('Loop', (), {'create_future': lambda _: OfflineFuture()})()
    import asyncio
    monkeypatch.setattr(asyncio.events, '_get_running_loop', lambda: loop)
    first, second = avatars.get(PID), avatars.get(PID)
    try:
        assert first.send(None) == ('picture', PID)
        assert second.send(None) == 'waiting-lock'
        assert immediate(first) == immediate(second)
        assert len(graph.calls) == 2 and len(calls) == 1
    finally:
        first.close()
        second.close()


def test_only_two_distinct_page_fetches_can_use_manager_capacity(setup, monkeypatch):
    manager, graph, _, avatars, calls, _, _ = setup
    link(manager, graph, pid='777', token='FixturePrivatePageToken777')
    link(manager, graph, pid='888', token='FixturePrivatePageToken888')
    graph.deferred = True
    loop = type('Loop', (), {'create_future': lambda _: OfflineFuture()})()
    import asyncio
    monkeypatch.setattr(asyncio.events, '_get_running_loop', lambda: loop)
    first, second, third = (avatars.get(pid) for pid in ('555', '777', '888'))
    try:
        assert first.send(None) == ('picture', '555')
        assert second.send(None) == ('picture', '777')
        assert third.send(None) == 'waiting-lock'
        assert len([call for call in graph.calls if call[0] == 'picture']) == 2
        immediate(first)
        assert third.send(None) == ('picture', '888')
        immediate(second)
        immediate(third)
        assert len(calls) == 3 and manager.capacity._value == 2
    finally:
        first.close()
        second.close()
        third.close()


def test_avatar_http_auth_mount_and_security_headers(tmp_path):
    graph = GraphFixture()
    app = create_app(tmp_path, graph_factory=lambda: graph, secret_box=FixtureBox())
    link(app.state.manager, graph)
    app.state.page_avatars.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=PNG, headers={'content-type': 'image/png'}))
    assert http_request(app, '/api/pages/555/avatar')[0] == 401
    headers = session(app)
    assert http_request(app, '/api/pages/555/avatar', headers={**headers, 'origin': 'https://evil.invalid'})[0] == 403
    code, response_headers, body = http_request(app, '/api/pages/555/avatar', headers=headers)
    assert code == 200 and response_headers['content-type'] == 'image/png'
    assert response_headers['cache-control'] == 'no-store' and response_headers['x-content-type-options'] == 'nosniff'
    assert body.startswith(b'\x89PNG') and TOKEN.encode() not in body
    code, _, body = http_request(app, '/api/pages/777/avatar', headers=headers)
    assert code == 404 and body == b''


def test_avatar_http_works_only_in_mounted_page_namespace(tmp_path):
    graph = GraphFixture()
    child = create_app(tmp_path, graph_factory=lambda: graph, secret_box=FixtureBox(), port=8000, base_path='/pages')
    link(child.state.manager, graph)
    child.state.page_avatars.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=PNG, headers={'content-type': 'image/png'}))
    parent = FastAPI()
    parent.mount('/pages', child)
    assert http_request(parent, '/pages/api/pages/555/avatar', host='127.0.0.1:8000')[0] == 401
    _, _, headers = mounted_session(parent)
    assert http_request(parent, '/pages/api/pages/555/avatar', host='127.0.0.1:8000', headers=headers)[0] == 200
    assert http_request(parent, '/api/pages/555/avatar', host='127.0.0.1:8000', headers=headers)[0] == 404


@pytest.mark.parametrize('kind,outcome,requires_refresh,invalidated', [
    ('avatar', 'accepted', False, True), ('avatar', 'unknown', True, True),
    ('avatar', 'failed', False, False), ('cover', 'accepted', True, False),
])
def test_avatar_edit_route_invalidates_only_when_result_warrants_refresh(tmp_path, monkeypatch, kind, outcome, requires_refresh, invalidated):
    app = create_app(tmp_path, secret_box=FixtureBox())
    headers = session(app)
    seen = []

    async def fake_edit(*args):
        return {'outcome': outcome, 'requires_refresh': requires_refresh}

    monkeypatch.setattr(app.state.manager, 'edit_image', fake_edit)
    monkeypatch.setattr(app.state.page_avatars, 'invalidate', lambda pid: seen.append(pid))
    path = f'/api/pages/555/image?connection_id=1&request_id={uuid4()}&kind={kind}'
    status, _, _ = http_request(app, path, headers={**headers, 'content-type': 'image/png'}, method='POST', raw=PNG)
    assert status == 200 and seen == (['555'] if invalidated else [])
