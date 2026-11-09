import json
import httpx
from app.services import outlook_diagnostics as diagnostics


def test_network_permission_error_is_not_reported_as_a_bad_proxy():
    low = PermissionError(13, 'fixture network permission')
    error = httpx.ConnectError('fixture')
    error.__cause__ = low
    assert diagnostics.failure_code(error) == 'LOCAL_NETWORK_PERMISSION_DENIED'


def test_proxy_error_never_returns_credentials_or_raw_exception():
    error = httpx.ProxyError('407 http://fixture-user:fixture-password@proxy.example')
    assert diagnostics.failure_code(error) == 'PROXY_AUTH'
    assert diagnostics.failure_code(httpx.ReadTimeout('fixture-secret')) == 'TIMEOUT'


def test_tls_and_reset_diagnostics_remain_bounded_fixed_codes():
    assert diagnostics.failure_code(httpx.ConnectError('[SSL: UNEXPECTED_EOF_WHILE_READING] fixture-secret')) == 'TLS_UNEXPECTED_EOF'
    assert diagnostics.failure_code(httpx.ConnectError('[SSL: CERTIFICATE_VERIFY_FAILED] fixture-secret')) == 'TLS_CERTIFICATE'
    assert diagnostics.failure_code(ConnectionResetError(10054, 'fixture-secret')) == 'CONNECTION_RESET'


def test_connect_probe_only_uses_assigned_proxy_and_returns_no_headers(monkeypatch):
    from types import SimpleNamespace
    calls = []
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ('proxy.invalid', 1234, 10)
        def set_tunnel(self, host, port, headers):
            assert (host, port) == ('login.live.com', 443)
            assert headers['Proxy-Authorization'].startswith('Basic ')
            calls.append('tunnel')
        def connect(self):
            calls.append('connect')
        def close(self):
            calls.append('close')
    monkeypatch.setattr(diagnostics.http.client, 'HTTPConnection', Connection)
    proxy = SimpleNamespace(protocol='http', host='proxy.invalid', port=1234, username='fixture-user', password='fixture-secret')
    result = diagnostics.probe_login_tunnel(proxy)
    assert result == {'target': 'microsoft_login', 'connect_tunnel': True}
    assert calls == ['tunnel', 'connect', 'close']
    assert 'fixture-secret' not in json.dumps(result)


def test_probe_uses_explicit_proxy_no_fallback_and_logs_no_response_secrets(monkeypatch):
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://login.live.com/?sensitive=fixture-secret'}, text='fixture-secret')
    original = httpx.Client
    def client(**kwargs):
        assert kwargs['proxy'] == 'http://fixture-user:fixture-password@proxy.invalid:1234'
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(respond), trust_env=False)
    monkeypatch.setattr(diagnostics.httpx, 'Client', client)
    result = diagnostics.probe_proxy(1, 'http://fixture-user:fixture-password@proxy.invalid:1234')
    assert len(requests) == len(diagnostics.TARGETS) == len(result)
    assert all(row['status'] == 302 and row['redirect_host'] == 'login.live.com' for row in result)
    serialized = json.dumps(result)
    assert 'fixture-secret' not in serialized and 'fixture-password' not in serialized
