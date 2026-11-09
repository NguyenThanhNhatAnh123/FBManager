"""Real connection/gateway/bridge integration with synthetic HTTP and secrets only."""
import base64
import json

import httpx
import pytest

from page_manager.podsach_bridge import PodsachBridge
from page_manager.store import Store
from page_manager.uploader_connection import UploaderConnection, ENDPOINT, OVERVIEW_PATH
from page_manager.uploader_gateway import SHOP_PREFIX, TICKET_PREFIX


FORM = '<form method="post" action="/login"><input name="next" type="hidden" value="/"><input name="user"><input name="password" type="password"></form>'
OV_TICKET = 'OVERVIEW01234567'
CHECK_TICKET = 'SHOPCHECK0123456'


class SyntheticBox:
    def encrypt(self, value):
        return 'fake-encrypted:' + base64.b64encode(value.encode()).decode()

    def decrypt(self, value):
        return base64.b64decode(value.removeprefix('fake-encrypted:')).decode()


class HostedTool:
    """The modern gateway has a web login and a separate per-shop session."""
    def __init__(self):
        self.calls = []
        self.cold = False
        self.gateway_check = False
        self.check_ok = True
        self.check_status = 200
        self.check_override = None
        self.during_check = None
        self.ticket_polls = {}
        self.login_count = 0

    @staticmethod
    def overview():
        # These saved-file fields deliberately do NOT include authenticated.
        return {'info': {'domain': 'blueox179.com', 'server': True,
                         'shops': {'blueox179.com': {'fallback': 'zitat25'},
                                   '247smileworld.com': {'fallback': 'unrelated'}},
                         'fallback_origin': 'zitat25', 'auth_saved': True,
                         'session': {'saved': True, 'matches': True, 'cookies': 8},
                         'input_dir': 'C:\\PRIVATE_INPUT_NO_ECHO'},
                'input': [], 'review': None, 'batches': [],
                'job': {'running': False, 'log': ['PRIVATE_LOG_NO_ECHO']}}

    def result(self):
        return self.check_override if self.check_override is not None else {
            'ok': self.check_ok, 'url': 'https://blueox179.com/admin/campaign',
            'title': 'Synthetic Blueox campaigns', 'renewed': True, 'error': ''}

    def __call__(self, request):
        self.calls.append(request)
        assert request.url.scheme == 'https'
        assert request.url.host == 'ul.eagleglobal2023.com'
        assert request.headers['Host'] == 'ul.eagleglobal2023.com'
        path = request.url.path
        if path == '/login' and request.method == 'GET':
            return httpx.Response(200,text=FORM,headers={'Content-Type':'text/html'})
        if path == '/login' and request.method == 'POST':
            self.login_count += 1
            return httpx.Response(302,headers={'Location':'/s/247smileworld.com/',
                'Set-Cookie':f'podsach_web=FIXTURE_COOKIE_{self.login_count}; Secure; HttpOnly; SameSite=Lax; Path=/'})
        assert path.startswith(SHOP_PREFIX + '/'), 'Every protected request is pinned to Blueox'
        assert 'FIXTURE_COOKIE_' in request.headers.get('Cookie','')
        assert request.method == 'GET', 'This integration fixture never uploads or submits products'
        if path == OVERVIEW_PATH:
            if self.cold:
                if request.extensions['timeout']['read'] < 65:
                    raise httpx.ReadTimeout('Synthetic cold worker has not reached the gateway ticket window',request=request)
                self.cold = False
                return httpx.Response(202,json={'gateway_pending':OV_TICKET})
            return httpx.Response(200,json=self.overview())
        if path == SHOP_PREFIX + '/api/session/check':
            assert request.extensions['timeout']['read'] >= 65
            callback, self.during_check = self.during_check, None
            if callback is not None: callback()
            if self.gateway_check:
                return httpx.Response(202,json={'gateway_pending':CHECK_TICKET})
            return httpx.Response(self.check_status,json=self.result())
        if path.startswith(TICKET_PREFIX):
            ticket = path.removeprefix(TICKET_PREFIX)
            assert ticket in (OV_TICKET,CHECK_TICKET)
            assert request.extensions['timeout']['read'] > 25
            self.ticket_polls[ticket] = self.ticket_polls.get(ticket,0)+1
            if ticket == CHECK_TICKET and self.ticket_polls[ticket] == 1:
                return httpx.Response(202,json={'gateway_pending':ticket})
            return httpx.Response(200,json=self.overview() if ticket == OV_TICKET else self.result())
        raise AssertionError('Unexpected protected fixture route')


@pytest.fixture
def integrated(tmp_path):
    tool = HostedTool()
    store = Store(tmp_path / 'manager')
    box = SyntheticBox()
    connection = UploaderConnection(store,box,transport=httpx.MockTransport(tool))
    connection.connect('fixture-user','FIXTURE_PASSWORD_NEVER_PERSISTED')
    tool.calls.clear()
    def unavailable(request):
        raise httpx.ConnectError('Synthetic local uploader is stopped',request=request)
    bridge = PodsachBridge(tool_root=tmp_path/'uninstalled-local-tool',
        transport=httpx.MockTransport(unavailable),remote_connection=connection)
    return tool,store,box,connection,bridge


def no_product_posts(calls):
    assert all(request.method == 'GET' or request.url.path == '/login' for request in calls)
    assert not any(request.url.path.endswith('/api/shop/activate') for request in calls)


def test_passive_modern_overview_never_inferrs_shop_login_or_checks_it(integrated):
    tool,store,box,connection,bridge = integrated
    status = bridge.inspect(force=True)
    assert status['provider']=='web' and status['available'] and status['configured_blueox']
    assert status['active_domain']=='blueox179.com'
    assert status['authenticated'] is None and not status['ready']
    assert connection.shop_verified() is None
    assert [request.url.path for request in tool.calls]==[OVERVIEW_PATH]
    public = json.dumps(status)
    for private in ('FIXTURE_COOKIE_','FIXTURE_PASSWORD','PRIVATE_LOG','PRIVATE_INPUT'):
        assert private not in public
    no_product_posts(tool.calls)


def test_explicit_check_recovers_cold_worker_202_and_persists_attestation(integrated):
    tool,store,box,connection,bridge = integrated
    tool.cold = True
    cold = bridge.inspect(force=True)
    assert not cold['available'] and connection.shop_verified() is None
    tool.gateway_check = True
    status = bridge.check_shop_session('web')
    assert status['available'] and status['ready'] and status['authenticated'] is True
    assert tool.ticket_polls == {OV_TICKET:1,CHECK_TICKET:2}
    check_calls=[r for r in tool.calls if r.url.path.endswith('/api/session/check')]
    assert len(check_calls)==1 and check_calls[0].extensions['timeout']['read']>=65
    overview_calls=[r for r in tool.calls if r.url.path==OVERVIEW_PATH]
    assert any(r.extensions['timeout']['read']>=65 for r in overview_calls)
    assert connection.shop_verified() is True
    fresh = UploaderConnection(store,box,transport=connection.transport)
    assert fresh.shop_verified() is True
    with store.connect() as db:
        row=dict(db.execute('SELECT * FROM uploader_remote_session WHERE id=1').fetchone())
    assert row['checked_ok']==1 and row['checked_revision']==row['revision']
    assert 'FIXTURE_COOKIE_' not in json.dumps(row) and 'FIXTURE_PASSWORD' not in json.dumps(row)
    no_product_posts(tool.calls)


def test_negative_explicit_shop_check_stays_not_ready(integrated):
    tool,store,box,connection,bridge = integrated
    tool.check_ok = False
    status = bridge.check_shop_session('web')
    assert connection.shop_verified() is False
    assert status['authenticated'] is False and not status['ready']
    assert status['available'] and status['session_saved']
    no_product_posts(tool.calls)


@pytest.mark.parametrize('action',['disconnect','reconnect','revision_change'])
def test_check_cannot_attest_session_changed_while_remote_probe_running(integrated,action):
    tool,store,box,connection,bridge = integrated
    before = connection.session_revision()
    def change_session():
        if action in ('disconnect','reconnect'): connection.disconnect()
        if action=='reconnect': connection.connect('fixture-user','NEW_FIXTURE_PASSWORD')
        if action=='revision_change':
            with store.connect() as db: db.execute('UPDATE uploader_remote_session SET revision=revision+1 WHERE id=1')
    tool.during_check = change_session
    with pytest.raises(ValueError): bridge.check_shop_session('web')
    assert connection.session_revision()>before
    assert connection.shop_verified() is None
    with store.connect() as db:
        row=dict(db.execute('SELECT * FROM uploader_remote_session WHERE id=1').fetchone())
    assert row['checked_revision'] is None and row['checked_ok'] is None
    if action=='reconnect': assert connection.status()['session_saved']
    no_product_posts(tool.calls)


@pytest.mark.parametrize('reply',[{}, {'ok':'true'}, {'ok':1}, {'authenticated':'true'}])
def test_malformed_check_result_never_becomes_authentication(integrated,reply):
    tool,store,box,connection,bridge=integrated
    tool.check_override=reply
    with pytest.raises(ValueError):bridge.check_shop_session('web')
    assert connection.shop_verified() is None
    no_product_posts(tool.calls)
