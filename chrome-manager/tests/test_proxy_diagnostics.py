import httpx
import pytest
from app.core.config import Config
from app.core.schemas import Settings
from app.services.proxies import proxy_failure_message

@pytest.mark.parametrize('error,code',[
    (httpx.ProxyError('407 Proxy Authentication Required: private-test-secret'),'[PROXY_AUTH]'),
    (httpx.ProxyError('Tunnel failed for private-test-secret'),'[PROXY_TUNNEL]'),
    (httpx.ConnectError('CERTIFICATE_VERIFY_FAILED private-test-secret'),'[TLS_CERTIFICATE]'),
    (httpx.ConnectError('Connection refused private-test-secret'),'[PROXY_CONNECT]'),
    (httpx.RemoteProtocolError('Invalid response private-test-secret'),'[PROXY_PROTOCOL]'),
    (ValueError('private-test-secret'),'[IP_RESPONSE]'),
])
def test_proxy_failure_reasons_are_useful_and_redact_secrets(error,code):
    message=proxy_failure_message(error)
    assert code in message and 'private-test-secret' not in message

def test_ip_version_chooses_correct_check_endpoint():
    config=Config()
    assert 'api64.ipify.org' in config.ip_check_url_for('auto')
    assert 'api.ipify.org' in config.ip_check_url_for('ipv4')
    assert 'api6.ipify.org' in config.ip_check_url_for('ipv6')
    assert Settings().proxy_ip_version=='auto'
