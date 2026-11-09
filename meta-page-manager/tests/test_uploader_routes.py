"""Uploader connection routes against synthetic local dependencies only."""
import asyncio
from http.cookies import SimpleCookie
import json
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from conftest import FixtureBox
from page_manager.app import create_app
from page_manager.errors import AppError
from page_manager.podsach_bridge import ShopSessionCheckError


ROOT = '/api/idea-pipeline/upload'
PASSWORD = 'PRIVATE TOOL LOGIN PASSWORD'


class Connection:
    def __init__(self):
        self.calls = []
        self.saved = False
        self.failure = None

    def connect(self, username, password):
        self.calls.append(('connect', username, password))
        if self.failure:
            raise self.failure
        self.saved = True
        # The HTTP route must not expose a dependency's raw login result.
        return {'private_cookie': 'PRIVATE TOOL COOKIE', 'password': password}

    def disconnect(self):
        self.calls.append(('disconnect',))
        self.saved = False
        return {'private_ciphertext': 'PRIVATE ENCRYPTED SESSION'}

    def status(self):
        return {'endpoint': 'https://ul.eagleglobal2023.com', 'session_saved': self.saved}


class Bridge:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []
        self.failure = None

    def inspect(self, *, force=False):
        self.calls.append(('inspect', force))
        return {'installed': True, 'tool_root': 'PODSach web', 'provider': 'web',
                'endpoint': 'https://ul.eagleglobal2023.com', 'policy': 'local_first',
                'available': self.connection.saved, 'session_saved': self.connection.saved,
                'configured_blueox': True, 'active_domain': 'blueox179.com',
                'authenticated': None, 'source': 'zitat25', 'ready': False,
                'blockers': ['Chưa kiểm tra phiên shop.']}

    def invalidate(self):
        self.calls.append(('invalidate',))

    def check_shop_session(self, provider):
        self.calls.append(('check', provider))
        if self.failure:
            raise self.failure
        return self.inspect(force=True)


def request(fixture, path, method='GET', body=None, headers=None):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app, client=('127.0.0.1', 1234)),
                                     base_url=fixture.origin) as client:
            response = await client.request(method, fixture.prefix + path, json=body, headers=headers)
            return response.status_code, dict(response.headers), response.json()
    return asyncio.run(send())


@pytest.fixture(params=[False, True], ids=['standalone', 'mounted'])
def fixture(tmp_path, request):
    connection = Connection()
    bridge = Bridge(connection)
    injected = []
    ideas = tmp_path / 'ideas'
    ideas.mkdir()
    def make_connection(manager):
        injected.append(manager)
        return connection
    child = create_app(tmp_path / 'manager', port=8000 if request.param else 8010,
        base_path='/pages' if request.param else '', secret_box=FixtureBox(),
        idea_pipeline_directory=ideas, podsach_bridge_factory=lambda: bridge,
        uploader_connection_factory=make_connection)
    child.state.manager.graph_factory = lambda: (_ for _ in ()).throw(AssertionError('No Graph'))
    app = child
    if request.param:
        app = FastAPI()
        app.mount('/pages', child)
    result = SimpleNamespace(app=app, child=child, prefix='/pages' if request.param else '',
        origin='http://127.0.0.1:' + ('8000' if request.param else '8010'), connection=connection,
        bridge=bridge, injected=injected)
    code, headers, body = globals()['request'](result, '/api/session')
    assert code == 200
    cookie = SimpleCookie()
    cookie.load(headers['set-cookie'])
    cookie_name = 'mpm_pages_session' if request.param else 'mpm_session'
    result.headers = {'cookie': cookie_name + '=' + cookie[cookie_name].value, 'x-mpm-csrf': body['csrf_token']}
    return result


def test_uploader_routes_require_own_page_session(fixture):
    for path, method, body in [(ROOT + '/status', 'GET', None),
        (ROOT + '/connect', 'POST', {'username': 'tool-user', 'password': PASSWORD}),
        (ROOT + '/connection', 'DELETE', None), (ROOT + '/session-check', 'POST', {'provider': 'web'})]:
        assert request(fixture, path, method, body)[0] == 401
        assert request(fixture, path, method, body, {'cookie': 'cm_session=WRONG'})[0] == 401
    assert fixture.connection.calls == [] and fixture.bridge.calls == []


def test_mutations_require_csrf_and_same_origin(fixture):
    operations = [(ROOT + '/connect', 'POST', {'username': 'tool-user', 'password': PASSWORD}),
                  (ROOT + '/connection', 'DELETE', None),
                  (ROOT + '/session-check', 'POST', {'provider': 'local'})]
    for path, method, body in operations:
        assert request(fixture, path, method, body, {'cookie': fixture.headers['cookie']})[0] == 403
        assert request(fixture, path, method, body, {**fixture.headers, 'origin': 'https://other.example'})[0] == 403
        assert request(fixture, path, method, body, {**fixture.headers, 'sec-fetch-site': 'cross-site'})[0] == 403
    assert fixture.connection.calls == [] and fixture.bridge.calls == []


def test_status_is_read_only_and_uses_shared_provider_without_credentials(fixture):
    assert fixture.injected == [fixture.child.state.manager]
    assert fixture.child.state.idea_workspace.bridge is fixture.bridge
    assert fixture.child.state.idea_runs.upload_bridge is fixture.bridge
    for _ in range(2):
        code, headers, value = request(fixture, ROOT + '/status', headers={'cookie': fixture.headers['cookie']})
        assert code == 200 and headers['cache-control'] == 'no-store'
        assert value['policy'] == 'local_first' and value['provider'] == 'web'
    assert fixture.connection.calls == []
    assert fixture.bridge.calls == [('inspect', False), ('inspect', False)]


def test_connect_passes_credentials_only_to_session_service_and_returns_safe_status(fixture):
    before = fixture.child.state.manager.store.reel_jobs()
    code, _, body = request(fixture, ROOT + '/connect', 'POST',
                            {'username': 'tool-user', 'password': PASSWORD}, fixture.headers)
    assert code == 200 and body['upload']['session_saved'] is True
    assert fixture.connection.calls == [('connect', 'tool-user', PASSWORD)]
    assert fixture.bridge.calls == [('invalidate',), ('inspect', False)]
    assert not any(value in json.dumps(body) for value in (PASSWORD, 'PRIVATE TOOL COOKIE', 'private_cookie'))
    assert fixture.child.state.manager.store.reel_jobs() == before


def test_disconnect_only_clears_tool_connection_and_returns_safe_status(fixture):
    fixture.connection.saved = True
    code, _, body = request(fixture, ROOT + '/connection', 'DELETE', headers=fixture.headers)
    assert code == 200 and body['upload']['session_saved'] is False
    assert fixture.connection.calls == [('disconnect',)]
    assert fixture.bridge.calls == [('invalidate',), ('inspect', False)]
    assert 'PRIVATE' not in json.dumps(body) and fixture.child.state.manager.store.reel_jobs() == []


@pytest.mark.parametrize('provider', ['local', 'web'])
def test_shop_session_is_checked_only_after_explicit_operator_call(fixture, provider):
    request(fixture, ROOT + '/status', headers=fixture.headers)
    assert not any(row[0] == 'check' for row in fixture.bridge.calls)
    code, _, body = request(fixture, ROOT + '/session-check', 'POST', {'provider': provider}, fixture.headers)
    assert code == 200 and 'upload' in body
    assert fixture.bridge.calls[-2:] == [('check', provider), ('inspect', True)]
    assert fixture.connection.calls == []


@pytest.mark.parametrize('body', [None, {}, {'username': 'user'}, {'password': PASSWORD},
    {'username': 'user', 'password': PASSWORD, 'endpoint': 'https://PRIVATE.example'},
    {'username': True, 'password': PASSWORD}, {'username': 'user', 'password': 1},
    {'username': 'user', 'password': 'PRIVATE' * 1000}])
def test_connect_validation_never_echoes_private_inputs(fixture, body):
    code, _, value = request(fixture, ROOT + '/connect', 'POST', body, fixture.headers)
    assert code == 422 and 'PRIVATE' not in json.dumps(value)
    assert fixture.connection.calls == [] and fixture.bridge.calls == []


@pytest.mark.parametrize('body', [{}, {'provider': 'remote'}, {'provider': 'https://PRIVATE.example'},
    {'provider': 'web', 'secret': PASSWORD}, {'provider': True}])
def test_session_check_accepts_only_known_provider_without_private_input_echo(fixture, body):
    code, _, value = request(fixture, ROOT + '/session-check', 'POST', body, fixture.headers)
    assert code == 422 and PASSWORD not in json.dumps(value) and 'PRIVATE' not in json.dumps(value)
    assert fixture.bridge.calls == [] and fixture.connection.calls == []


def test_failed_login_invalidates_diagnostics_without_probe_or_password_echo(fixture):
    fixture.connection.failure = AppError('Tool chưa chấp nhận thông tin đăng nhập.', 'uploader_login_rejected', 401)
    code, _, body = request(fixture, ROOT + '/connect', 'POST',
                           {'username': 'tool-user', 'password': PASSWORD}, fixture.headers)
    assert code == 401 and body['code'] == 'uploader_login_rejected'
    assert PASSWORD not in json.dumps(body) and fixture.bridge.calls == [('invalidate',)]


def test_unexpected_login_failure_never_echoes_remote_cookie_or_password(fixture):
    fixture.connection.failure = RuntimeError('PRIVATE COOKIE ' + PASSWORD)
    code, _, body = request(fixture, ROOT + '/connect', 'POST',
                           {'username': 'tool-user', 'password': PASSWORD}, fixture.headers)
    assert code == 500 and 'PRIVATE' not in json.dumps(body) and PASSWORD not in json.dumps(body)


def test_session_check_error_is_safe_and_does_not_repeat(fixture):
    fixture.bridge.failure = ValueError('PRIVATE COOKIE UPSTREAM ERROR')
    code, _, body = request(fixture, ROOT + '/session-check', 'POST', {'provider': 'web'}, fixture.headers)
    assert code == 409 and body['code'] == 'uploader_session_unavailable'
    assert 'PRIVATE' not in json.dumps(body)
    assert fixture.bridge.calls == [('check', 'web')]
    assert fixture.connection.calls == []


@pytest.mark.parametrize('reason,status', [('web_not_connected',409),('tool_unavailable',503),
    ('wrong_shop',409),('tool_session_expired',409),('busy_or_shop_changed',409),
    ('timeout',504),('invalid_response',502),('session_changed',409),('unavailable',502)])
def test_shop_check_exposes_only_bounded_reason_and_status(fixture,reason,status):
    fixture.bridge.failure=ShopSessionCheckError(reason)
    code,_,body=request(fixture,ROOT+'/session-check','POST',{'provider':'local'},fixture.headers)
    assert code==status and body['code']=='uploader_check_'+reason
    assert 'PRIVATE' not in json.dumps(body) and fixture.bridge.calls==[('check','local')]


def test_shop_check_summary_is_projected_without_raw_upstream_fields(fixture):
    def check(provider):
        return {**fixture.bridge.inspect(),'session_check':{'provider':provider,'authenticated':True,
            'ready':False,'status':'authenticated','reason':'proof_not_available',
            'checked_at':'2030-01-01T00:00:00+00:00','cookie':'PRIVATE COOKIE','raw_error':'PRIVATE ERROR'}}
    fixture.bridge.check_shop_session=check
    code,_,body=request(fixture,ROOT+'/session-check','POST',{'provider':'local'},fixture.headers)
    assert code==200 and body['check']=={'provider':'local','authenticated':True,'ready':False,
        'status':'authenticated','reason':'proof_not_available','checked_at':'2030-01-01T00:00:00+00:00'}
    assert 'session_check' not in body['upload'] and 'PRIVATE' not in json.dumps(body)


@pytest.mark.parametrize('change',[{'provider':'web'},{'authenticated':1},{'ready':'true'},
    {'status':'PRIVATE STATUS'},{'reason':'PRIVATE REASON'},{'checked_at':'PRIVATE DATE'},
    {'checked_at':'2030-01-01T00:00:00'}])
def test_malformed_shop_check_summary_is_not_forwarded(fixture,change):
    def check(provider):
        summary={'provider':provider,'authenticated':True,'ready':False,'status':'authenticated',
            'reason':None,'checked_at':'2030-01-01T00:00:00+00:00',**change}
        return {**fixture.bridge.inspect(),'session_check':summary}
    fixture.bridge.check_shop_session=check
    code,_,body=request(fixture,ROOT+'/session-check','POST',{'provider':'local'},fixture.headers)
    assert code==200 and 'check' not in body and 'PRIVATE' not in json.dumps(body)
