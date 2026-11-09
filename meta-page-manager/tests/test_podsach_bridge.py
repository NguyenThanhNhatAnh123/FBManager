import json
from contextlib import contextmanager

import httpx
import pytest

from page_manager.podsach_bridge import (
    MAX_CONFIG_BYTES, MAX_OVERVIEW_BYTES, OVERVIEW_URL, PodsachBridge, ShopSessionCheckError,
)


def tool(tmp_path, config=None):
    for name in ('dashboard.py', 'upload_batch.py', 'config.py'):
        (tmp_path / name).write_text('# synthetic tool marker\n', encoding='utf-8')
    if config is not None:
        (tmp_path / 'shops.json').write_text(json.dumps(config), encoding='utf-8')
    return tmp_path


def overview(**extra):
    info = {'domain': 'blueox179.com',
            'shops': {'blueox179.com': {'fallback': 'ge23108'}},
            'fallback_origin': 'ge23108',
            # These exact old-uploader fields describe a saved cookie file only.
            'auth_saved': True,
            'session': {'saved': True, 'matches': True, 'cookies': 7}}
    info.update(extra)
    return {'info': info, 'job': {'log': ['PRIVATE LOG']},
            'review': {'output_dir': 'C:\\private\\work'}}


def adapter(tmp_path, payload=None, **kwargs):
    def respond(request):
        assert request.method == 'GET'
        assert str(request.url) == OVERVIEW_URL
        return httpx.Response(200, json=payload if payload is not None else overview())
    return PodsachBridge(tool_root=tmp_path, transport=httpx.MockTransport(respond), **kwargs)


def test_saved_cookies_never_count_as_authenticated(tmp_path):
    result = adapter(tool(tmp_path)).inspect()
    assert result['installed'] and result['available'] and result['configured_blueox']
    assert result['active_domain'] == 'blueox179.com'
    assert result['source'] == 'ge23108'
    assert result['authenticated'] is None and result['ready'] is False
    encoded = json.dumps(result)
    assert 'PRIVATE' not in encoded and 'cookies' not in encoded and str(tmp_path) not in encoded
    assert set(result) == {'installed', 'tool_root', 'available', 'configured_blueox',
                           'active_domain', 'authenticated', 'source', 'ready', 'blockers','workspace'}


@pytest.mark.parametrize('where', ['info', 'session'])
@pytest.mark.parametrize('value', [True, False])
def test_only_explicit_boolean_authentication_is_used(tmp_path, where, value):
    extra = {'authenticated': value} if where == 'info' else {'session': {'authenticated': value}}
    result = adapter(tool(tmp_path), overview(**extra)).inspect()
    assert result['authenticated'] is value
    assert result['ready'] is value


@pytest.mark.parametrize('value', ['true', 1, {}, ['authenticated']])
def test_non_boolean_authentication_is_unknown(tmp_path, value):
    assert adapter(tool(tmp_path), overview(authenticated=value)).inspect()['authenticated'] is None


def test_other_active_shop_does_not_inherit_auth_or_origin(tmp_path):
    payload = overview(domain='247smileworld.com', authenticated=True,
                       fallback_origin='wrong-shop-template',
                       session={'authenticated': True})
    result = adapter(tool(tmp_path), payload).inspect()
    assert result['source'] == 'ge23108'
    assert result['authenticated'] is None and not result['ready']
    assert any('chưa chọn shop' in item for item in result['blockers'])


def test_offline_reports_only_safe_local_readiness(tmp_path):
    root = tool(tmp_path, {'active': '247smileworld.com',
                           'shops': {'blueox179.com': {'fallback': 'ge23108', 'secret': 'DO NOT ECHO'}}})
    def unavailable(request):
        raise httpx.ConnectError('PRIVATE EXCEPTION', request=request)
    result = PodsachBridge(tool_root=root, transport=httpx.MockTransport(unavailable)).inspect()
    assert result['installed'] and result['configured_blueox']
    assert not result['available'] and not result['ready']
    assert result['source'] == 'ge23108'
    assert 'PRIVATE' not in json.dumps(result) and 'DO NOT ECHO' not in json.dumps(result)
    assert any('8770' in item for item in result['blockers'])


def test_live_state_overrides_stale_local_shop_configuration(tmp_path):
    root = tool(tmp_path, {'active': 'blueox179.com',
                           'shops': {'blueox179.com': {'fallback': 'stale'}}})
    result = adapter(root, overview(domain='wildy05.com', shops={})).inspect()
    assert result['active_domain'] == 'wildy05.com'
    assert not result['configured_blueox'] and result['source'] is None


def test_never_selects_arbitrary_preset_or_colour_origin(tmp_path):
    payload = overview(shops={'blueox179.com': {'black': 'black-template',
                                               'white': 'white-template',
                                               'presets': {'animal': 'other'}}},
                       fallback_origin='', authenticated=True)
    result = adapter(tool(tmp_path), payload).inspect()
    assert result['source'] is None and not result['ready']
    assert any('campaign mẫu' in item for item in result['blockers'])


def test_missing_install_is_blocker_even_if_service_responds(tmp_path):
    result = adapter(tmp_path, overview(authenticated=True)).inspect()
    assert result['available'] and not result['installed'] and not result['ready']


@pytest.mark.parametrize('payload', [[], {}, {'info': []},
    {'info': {'domain': 'blueox179.com', 'shops': []}},
    {'info': {'domain': 'http://127.0.0.1/private?token=SECRET', 'shops': {}}}])
def test_bad_api_shapes_do_not_escape(tmp_path, payload):
    result = adapter(tool(tmp_path), payload).inspect()
    assert not result['available'] and not result['ready']
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('status', [301, 302, 401, 403, 500])
def test_no_redirect_or_alternative_network_target(tmp_path, status):
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={'Location': 'https://untrusted.example/private'},
                              text='PRIVATE RESPONSE')
    result = PodsachBridge(tool_root=tool(tmp_path), transport=httpx.MockTransport(response)).inspect()
    assert calls == [OVERVIEW_URL] and not result['available']
    assert 'PRIVATE' not in json.dumps(result)


def test_large_api_and_config_are_bounded(tmp_path):
    root = tool(tmp_path)
    (root / 'shops.json').write_bytes(b' ' * (MAX_CONFIG_BYTES + 1))
    def response(request):
        return httpx.Response(200, content=b' ' * (MAX_OVERVIEW_BYTES + 1))
    result = PodsachBridge(tool_root=root, transport=httpx.MockTransport(response)).inspect()
    assert not result['available'] and not result['configured_blueox']


@pytest.mark.parametrize('fallback', ['https://example.com/token=SECRET', '../SECRET', 'a' * 101, ['SECRET']])
def test_source_is_a_bounded_campaign_slug_only(tmp_path, fallback):
    result = adapter(tool(tmp_path), overview(
        shops={'blueox179.com': {'fallback': fallback}}, fallback_origin=fallback)).inspect()
    assert result['source'] is None and 'SECRET' not in json.dumps(result)


def test_invalid_json_is_fixed_offline_message(tmp_path):
    def response(request):
        return httpx.Response(200, text='not-json SECRET')
    result = PodsachBridge(tool_root=tool(tmp_path), transport=httpx.MockTransport(response)).inspect()
    assert not result['available'] and 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('timeout', [0, -1, 5.1, True, '2'])
def test_timeout_is_bounded(timeout):
    with pytest.raises(ValueError):
        PodsachBridge(timeout_seconds=timeout)


class RemoteConnection:
    """Synthetic hosted-tool session; never authenticates or changes shop."""
    def __init__(self, payload=None, *, saved=True, status=200, failure=False):
        self.payload = overview(authenticated=True) if payload is None else payload
        self.saved, self.response_status, self.failure = saved, status, failure
        self.calls = []

    def status(self):
        return {'endpoint': 'https://ul.eagleglobal2023.com', 'session_saved': self.saved}

    @contextmanager
    def client(self, *, overview_only=False):
        assert overview_only is True, 'Unscoped remote access is limited to readiness overview'
        def respond(request):
            self.calls.append(request)
            assert request.method == 'GET'
            assert str(request.url) == 'https://ul.eagleglobal2023.com/s/blueox179.com/api/overview'
            if self.failure:
                raise httpx.ConnectError('PRIVATE REMOTE COOKIE ERROR', request=request)
            return httpx.Response(self.response_status, json=self.payload,
                                  headers={'Location': 'https://untrusted.example/private'})
        with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False,
                          trust_env=False) as client:
            yield client


def dual_adapter(tmp_path, *, local=None, remote=None, local_offline=False):
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == 'GET' and str(request.url) == OVERVIEW_URL
        if local_offline:
            raise httpx.ConnectError('PRIVATE LOCAL ERROR', request=request)
        return httpx.Response(200, json=overview(authenticated=True) if local is None else local)
    root = tool(tmp_path)
    remote = remote or RemoteConnection()
    bridge = PodsachBridge(tool_root=root, transport=httpx.MockTransport(respond),
                           remote_connection=remote)
    return bridge, calls, remote


def workspace_overview(*,running=False,inputs=None,review=None,batches=None):
    return {**overview(authenticated=True),'job':{'running':running,'log':['PRIVATE JOB LOG']},
        'input':[] if inputs is None else inputs,'review':review,'batches':[] if batches is None else batches}


@pytest.mark.parametrize('provider',['local','web'])
def test_idle_workspace_is_derived_from_same_overview_once_without_raw_metadata(tmp_path,provider):
    payload=workspace_overview(batches=[{'rows':[{'status':'done','source_mockup':'PRIVATE FILE','token':'PRIVATE TOKEN'}],
        'error':'','name':'PRIVATE BATCH'}])
    bridge,calls,remote=dual_adapter(tmp_path,local=payload if provider=='local' else overview(authenticated=None),
        remote=RemoteConnection(payload))
    value=bridge.inspect()
    assert value['provider']==provider
    assert value['workspace']=={'state':'idle','input_count':0,'review_pending':False,'pending_batch_count':0}
    assert value['providers'][provider]['workspace']==value['workspace']
    assert len(calls)==len(remote.calls)==1
    assert all(row.method=='GET' and row.url.path.endswith('/api/overview') for row in [*calls,*remote.calls])
    assert 'PRIVATE' not in json.dumps(value) and value['ready'] and value['authenticated']


@pytest.mark.parametrize('change,expected',[
    ({'running':True},{'input_count':0,'review_pending':False,'pending_batch_count':0}),
    ({'inputs':[{'name':'PRIVATE FOREIGN INPUT','path':'PRIVATE PATH'}]},
        {'input_count':1,'review_pending':False,'pending_batch_count':0}),
    ({'review':{'output_dir':'PRIVATE DIRECTORY','items':['PRIVATE REVIEW']}},
        {'input_count':0,'review_pending':True,'pending_batch_count':0}),
    ({'batches':[{'rows':[{'status':'pending'},{'status':'error: PRIVATE MESSAGE'}]},
                 {'rows':[{'status':'done'}]},{'rows':[{'status':'pending'}]}]},
        {'input_count':0,'review_pending':False,'pending_batch_count':2}),
])
def test_workspace_busy_counts_only_and_never_changes_existing_auth_or_route(tmp_path,change,expected):
    bridge,calls,remote=dual_adapter(tmp_path,local=workspace_overview(**change),
        remote=RemoteConnection(workspace_overview()))
    value=bridge.inspect()
    assert value['workspace']=={'state':'busy',**expected}
    assert value['providers']['local']['workspace']==value['workspace']
    assert value['providers']['web']['workspace']['state']=='idle'
    assert value['provider']=='local' and value['ready'] and value['authenticated']
    assert len(calls)==len(remote.calls)==1 and 'PRIVATE' not in json.dumps(value)


@pytest.mark.parametrize('part,replacement',[
    ('job',None),('job',{}),('job',{'running':0}),('input',None),('input',{}),('input',[None]),
    ('input',[{}]),('review',False),('review','PRIVATE'),('batches',None),('batches',[None]),
    ('batches',[{}]),('batches',[{'rows':None}]),('batches',[{'rows':[None]}]),
    ('batches',[{'rows':[{}]}]),('batches',[{'rows':[{'status':None}]}]),
    ('batches',[{'rows':[],'error':'PRIVATE MANIFEST PARSE ERROR'}]),
])
def test_missing_or_malformed_workspace_is_unknown_without_affecting_provider_readiness(tmp_path,part,replacement):
    payload=workspace_overview();payload[part]=replacement
    value=adapter(tool(tmp_path),payload).inspect()
    assert value['workspace']=={'state':'unknown','input_count':None,'review_pending':None,'pending_batch_count':None}
    assert value['ready'] and value['authenticated'] and value['available']
    assert 'PRIVATE' not in json.dumps(value)


@pytest.mark.parametrize('missing',['job','input','review','batches'])
def test_missing_workspace_field_is_never_assumed_idle(tmp_path,missing):
    payload=workspace_overview();payload.pop(missing)
    value=adapter(tool(tmp_path),payload).inspect()
    assert value['workspace']['state']=='unknown' and value['ready']


def test_offline_or_saved_only_provider_workspace_is_unknown(tmp_path):
    bridge,_,remote=dual_adapter(tmp_path,local_offline=True,remote=RemoteConnection(saved=False))
    value=bridge.inspect()
    assert all(provider['workspace']['state']=='unknown' for provider in value['providers'].values())
    assert value['workspace']['state']=='unknown' and not value['ready'] and not remote.calls


def test_dual_provider_prefers_ready_local_without_combining_web_fields(tmp_path):
    remote = RemoteConnection(overview(authenticated=True, fallback_origin='remote-template'))
    bridge, _, _ = dual_adapter(tmp_path, remote=remote)
    value = bridge.inspect()
    assert value['provider'] == 'local' and value['policy'] == 'local_first'
    assert value['endpoint'] == 'http://127.0.0.1:8770' and value['source'] == 'ge23108'
    assert value['ready'] is True
    assert set(value['providers']) == {'local', 'web'}
    assert value['providers']['web']['source'] == 'remote-template'
    assert 'PRIVATE LOG' not in json.dumps(value)


def test_ready_web_is_used_when_local_is_offline(tmp_path):
    bridge, local_calls, remote = dual_adapter(tmp_path, local_offline=True)
    value = bridge.inspect()
    assert value['provider'] == 'web' and value['endpoint'] == 'https://ul.eagleglobal2023.com/s/blueox179.com'
    assert value['ready'] is True and value['authenticated'] is True
    assert value['providers']['local']['available'] is False
    assert len(local_calls) == len(remote.calls) == 1
    assert 'PRIVATE' not in json.dumps(value)


def test_ready_web_is_used_when_correct_local_shop_has_no_verified_session(tmp_path):
    bridge, _, _ = dual_adapter(tmp_path, local=overview(authenticated=None))
    value = bridge.inspect()
    assert value['provider'] == 'web' and value['ready'] is True
    assert value['providers']['local']['active_domain'] == 'blueox179.com'
    assert value['providers']['local']['authenticated'] is None


def test_correct_available_local_is_selected_when_neither_provider_is_ready(tmp_path):
    bridge, _, _ = dual_adapter(tmp_path, local=overview(authenticated=None),
                               remote=RemoteConnection(saved=False))
    value = bridge.inspect()
    assert value['provider'] == 'local' and value['ready'] is False
    assert value['providers']['web']['session_saved'] is False


def test_web_status_cannot_inherit_local_template_or_authentication(tmp_path):
    bridge, _, _ = dual_adapter(tmp_path, local=overview(domain='wildy05.com', authenticated=True),
        remote=RemoteConnection(overview(domain='247smileworld.com', authenticated=True,
                                         fallback_origin='wrong-shop-origin')))
    value = bridge.inspect()
    assert value['provider'] == 'web' and value['active_domain'] == '247smileworld.com'
    assert value['authenticated'] is None and value['ready'] is False
    assert value['source'] == 'ge23108'
    assert any('shop khác' in row for row in value['blockers'])


def test_missing_remote_session_does_not_probe_web_or_mark_authentication(tmp_path):
    remote = RemoteConnection(saved=False)
    bridge, _, _ = dual_adapter(tmp_path, local_offline=True, remote=remote)
    value = bridge.inspect()
    assert remote.calls == [] and value['provider'] == 'web'
    assert not value['available'] and not value['ready'] and value['authenticated'] is None
    assert any('Đăng nhập tool web' in row for row in value['blockers'])


@pytest.mark.parametrize('status', [301, 302, 401, 403, 500])
def test_remote_expiry_or_redirect_never_follows_or_leaks(tmp_path, status):
    remote = RemoteConnection(status=status)
    bridge, _, _ = dual_adapter(tmp_path, local_offline=True, remote=remote)
    value = bridge.inspect()
    assert value['provider'] == 'web' and value['session_saved'] is True
    assert not value['available'] and not value['ready']
    assert len(remote.calls) == 1
    assert 'untrusted' not in json.dumps(value) and 'PRIVATE' not in json.dumps(value)


@pytest.mark.parametrize('payload', [[], {}, {'info': []}, {'info': {'domain': 'PRIVATE URL', 'shops': {}}},
                                   {'info': {'domain': 'blueox179.com', 'shops': []}}])
def test_remote_malformed_shape_is_unavailable_without_local_state_fallback(tmp_path, payload):
    bridge, _, _ = dual_adapter(tmp_path, local_offline=True, remote=RemoteConnection(payload))
    value = bridge.inspect()
    assert value['active_domain'] is None and not value['configured_blueox']
    assert value['source'] is None and not value['ready']
    assert 'PRIVATE' not in json.dumps(value)


def test_remote_network_error_and_oversized_body_are_safe(tmp_path):
    bridge, _, remote = dual_adapter(tmp_path, local_offline=True,
                                     remote=RemoteConnection(failure=True))
    assert not bridge.inspect()['available']
    assert 'PRIVATE' not in json.dumps(bridge.inspect())
    remote.failure = False
    remote.payload = overview(authenticated=True, private='X' * MAX_OVERVIEW_BYTES)
    assert not bridge.inspect(force=True)['available']


def test_dual_readiness_cache_force_and_invalidate_do_not_check_shop_session(tmp_path):
    bridge, local_calls, remote = dual_adapter(tmp_path)
    for _ in range(4):
        assert bridge.inspect()['provider'] == 'local'
    assert len(local_calls) == len(remote.calls) == 1
    bridge.inspect(force=True)
    assert len(local_calls) == len(remote.calls) == 2
    bridge.invalidate()
    bridge.inspect()
    assert len(local_calls) == len(remote.calls) == 3
    assert all('/api/session/check' not in str(row.url) for row in [*local_calls, *remote.calls])


class SessionRemote(RemoteConnection):
    @contextmanager
    def client(self, *, overview_only=False):
        def respond(request):
            self.calls.append(request)
            assert request.method == 'GET'
            assert request.url.host == 'ul.eagleglobal2023.com'
            if request.url.path == '/s/blueox179.com/api/overview':
                assert overview_only is True
                return httpx.Response(200, json=self.payload)
            assert request.url.path == '/s/blueox179.com/api/session/check' and overview_only is False
            assert request.headers['X-PODSach-Expected-Shop'] == 'blueox179.com'
            if self.failure:
                raise httpx.ConnectError('PRIVATE SESSION ERROR', request=request)
            self.payload['info']['authenticated'] = True
            return httpx.Response(200, json={'authenticated': True})
        with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False,
                          trust_env=False) as client:
            yield client


def test_explicit_web_shop_check_uses_only_guarded_get_then_reinspects(tmp_path):
    remote = SessionRemote(overview(authenticated=None))
    bridge, _, _ = dual_adapter(tmp_path, local_offline=True, remote=remote)
    assert bridge.inspect()['authenticated'] is None
    assert all(row.url.path == '/s/blueox179.com/api/overview' for row in remote.calls)
    result = bridge.check_shop_session('web')
    assert result['provider'] == 'web' and result['ready'] is True
    checks = [row for row in remote.calls if row.url.path == '/s/blueox179.com/api/session/check']
    assert len(checks) == 1 and checks[0].method == 'GET'
    assert remote.calls[-1].url.path == '/s/blueox179.com/api/overview'


def test_wrong_web_shop_is_not_changed_or_checked(tmp_path):
    remote = SessionRemote(overview(domain='247smileworld.com', authenticated=True))
    bridge, _, _ = dual_adapter(tmp_path, local_offline=True, remote=remote)
    with pytest.raises(ValueError):
        bridge.check_shop_session('web')
    assert len(remote.calls) == 1 and remote.calls[0].url.path == '/s/blueox179.com/api/overview'


def test_web_shop_check_failure_has_fixed_message_and_no_implicit_retry(tmp_path):
    remote = SessionRemote(overview(authenticated=None), failure=True)
    bridge, _, _ = dual_adapter(tmp_path, local_offline=True, remote=remote)
    with pytest.raises(ValueError) as caught:
        bridge.check_shop_session('web')
    assert 'PRIVATE' not in str(caught.value)
    assert sum(row.url.path == '/s/blueox179.com/api/session/check' for row in remote.calls) == 1


@pytest.mark.parametrize('provider', ['remote', 'https://PRIVATE.example', None])
def test_shop_check_rejects_unknown_provider_without_requests(tmp_path, provider):
    bridge, local_calls, remote = dual_adapter(tmp_path)
    with pytest.raises(ValueError):
        bridge.check_shop_session(provider)
    assert local_calls == [] and remote.calls == []


class ModernSessionRemote(RemoteConnection):
    def __init__(self, *, check_ok=True):
        super().__init__(overview())
        self.verified, self.check_ok = None, check_ok

    def shop_verified(self):
        return self.verified

    def record_shop_check(self, ok):
        self.verified = ok

    @contextmanager
    def client(self, *, overview_only=False):
        def respond(request):
            self.calls.append(request)
            if request.url.path == '/s/blueox179.com/api/overview':
                return httpx.Response(200, json=self.payload)
            assert request.url.path == '/s/blueox179.com/api/session/check'
            assert not overview_only and request.headers['X-PODSach-Expected-Shop'] == 'blueox179.com'
            assert request.extensions['timeout']['read'] >= 65
            return httpx.Response(200, json={'ok': self.check_ok, 'url': 'https://blueox179.com/admin'})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            yield client


@pytest.mark.parametrize('ok', [True, False])
def test_modern_web_uses_explicit_shop_check_not_saved_cookie_hints(tmp_path, ok):
    remote = ModernSessionRemote(check_ok=ok)
    bridge, _, _ = dual_adapter(tmp_path, local_offline=True, remote=remote)
    assert bridge.inspect()['authenticated'] is None
    assert bridge.inspect()['ready'] is False
    value = bridge.check_shop_session('web')
    assert remote.verified is ok
    assert value['authenticated'] is ok and value['ready'] is ok
    assert sum(row.url.path.endswith('/api/session/check') for row in remote.calls) == 1


@pytest.mark.parametrize('ok', [True, False])
def test_explicit_local_check_uses_injected_transport_and_flat_status(tmp_path, ok):
    calls, payload = [], overview()
    def respond(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.host == '127.0.0.1' and request.url.port == 8770
        if request.url.path == '/api/overview':
            return httpx.Response(200, json=payload)
        assert request.url.path == '/api/session/check' and not request.url.query
        assert request.headers['X-PODSach-Expected-Shop'] == 'blueox179.com'
        payload['info']['authenticated'] = ok
        return httpx.Response(200, json={'ok': ok, 'private_cookie': 'PRIVATE COOKIE'})
    bridge = PodsachBridge(tool_root=tool(tmp_path), transport=httpx.MockTransport(respond))
    assert bridge.inspect()['authenticated'] is None
    assert [row.url.path for row in calls] == ['/api/overview']
    value = bridge.check_shop_session('local')
    assert value['authenticated'] is ok and value['ready'] is ok
    check = value['session_check']
    assert check['provider'] == 'local' and check['authenticated'] is ok and check['ready'] is ok
    assert check['status'] == ('authenticated' if ok else 'authentication_required')
    assert check['reason'] is None if ok else check['reason'] == 'authentication_required'
    assert check['checked_at'].endswith('+00:00')
    assert [row.url.path for row in calls] == ['/api/overview', '/api/overview', '/api/session/check', '/api/overview']
    assert 'PRIVATE' not in json.dumps(value)


def test_legacy_local_probe_does_not_fabricate_durable_runtime_proof(tmp_path):
    def respond(request):
        return httpx.Response(200, json=overview() if request.url.path == '/api/overview' else {'ok': True})
    bridge = PodsachBridge(tool_root=tool(tmp_path), transport=httpx.MockTransport(respond))
    value = bridge.check_shop_session('local')
    assert value['authenticated'] is None and value['ready'] is False
    assert value['session_check']['authenticated'] is True
    assert value['session_check']['ready'] is False and value['session_check']['reason'] == 'proof_not_available'


@pytest.mark.parametrize('status,reason', [(401,'tool_session_expired'), (403,'tool_session_expired'),
    (409,'busy_or_shop_changed'), (500,'unavailable')])
def test_local_check_http_failure_is_fixed_safe_and_never_retried(tmp_path, status, reason):
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200,json=overview()) if request.url.path == '/api/overview' else httpx.Response(status,text='PRIVATE COOKIE')
    bridge = PodsachBridge(tool_root=tool(tmp_path), transport=httpx.MockTransport(respond))
    with pytest.raises(ShopSessionCheckError) as caught:
        bridge.check_shop_session('local')
    assert caught.value.reason == reason and 'PRIVATE' not in str(caught.value)
    assert calls == ['/api/overview','/api/session/check']


@pytest.mark.parametrize('payload', [[], {}, {'ok':'true'}, {'authenticated':1}, {'ok':None}, {'ok':True,'private':'X'*MAX_OVERVIEW_BYTES}])
def test_local_check_rejects_nonboolean_or_oversized_result(tmp_path, payload):
    def respond(request):
        return httpx.Response(200,json=overview() if request.url.path == '/api/overview' else payload)
    bridge = PodsachBridge(tool_root=tool(tmp_path), transport=httpx.MockTransport(respond))
    with pytest.raises(ShopSessionCheckError) as caught:
        bridge.check_shop_session('local')
    assert caught.value.reason == 'invalid_response'


@pytest.mark.parametrize('mode,reason', [('offline','tool_unavailable'), ('wrong_shop','wrong_shop'),
    ('timeout','timeout'), ('html','invalid_response')])
def test_local_check_prerequisites_and_timeout_have_actionable_reasons(tmp_path, mode, reason):
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == '/api/overview':
            if mode == 'offline':raise httpx.ConnectError('PRIVATE ERROR',request=request)
            return httpx.Response(200,json=overview(domain='wildy05.com') if mode == 'wrong_shop' else overview())
        if mode == 'timeout':raise httpx.ReadTimeout('PRIVATE COOKIE',request=request)
        return httpx.Response(200,text='PRIVATE HTML LOGIN')
    bridge = PodsachBridge(tool_root=tool(tmp_path), transport=httpx.MockTransport(respond))
    with pytest.raises(ShopSessionCheckError) as caught:
        bridge.check_shop_session('local')
    assert caught.value.reason == reason and 'PRIVATE' not in str(caught.value)
    assert calls == (['/api/overview'] if mode in ('offline','wrong_shop') else ['/api/overview','/api/session/check'])


def test_explicit_web_check_without_saved_session_never_contacts_either_provider(tmp_path):
    bridge, local_calls, remote = dual_adapter(tmp_path, remote=RemoteConnection(saved=False))
    with pytest.raises(ShopSessionCheckError) as caught:
        bridge.check_shop_session('web')
    assert caught.value.reason == 'web_not_connected'
    assert local_calls == [] and remote.calls == []
