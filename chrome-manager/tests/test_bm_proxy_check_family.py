"""Private BM proxy checks support either IP family independently of the pool.

The HTTP client is fully mocked; only synthetic endpoints and temporary SQLite
data are used. No Chrome or real proxy connections are involved.
"""
import ipaddress

import httpx
import pytest

from app.core.config import Config
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import BmProfile,BmProxy,Profile,Proxy
from app.services.proxies import ProxyService
from app.services.state import State


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in a synchronous fixture test')


@pytest.fixture
def service(tmp_path,monkeypatch):
    monkeypatch.setenv('CM_IPV4_CHECK_URL','https://ipv4-check.example.test/ip')
    monkeypatch.setenv('CM_IPV6_CHECK_URL','https://ipv6-check.example.test/ip')
    config=Config(root=tmp_path,data_dir=tmp_path/'data',ip_check_url='https://auto-check.example.test/ip');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    proxies=ProxyService(db,state,config)
    proxy_id=proxies.import_proxies('192.0.2.1:10001',False)['proxy_ids'][0]
    yield proxies,proxy_id
    db.engine.dispose()


def mock_response(monkeypatch,service,address,change_family=None):
    calls=[]
    class Client:
        def __init__(self,**options):
            assert options['proxy']=='http://192.0.2.1:10001'
            assert options['trust_env'] is False and options['follow_redirects'] is False
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def get(self,url):
            calls.append(url)
            if change_family:service.state.save_settings(Settings(proxy_ip_version=change_family))
            return httpx.Response(200,json={'ip':address},request=httpx.Request('GET',url))
    monkeypatch.setattr('app.services.proxies.httpx.AsyncClient',Client)
    return calls


@pytest.mark.parametrize('scope',['public','private_attached','private_detached'])
@pytest.mark.parametrize('global_family',['ipv4','ipv6'])
@pytest.mark.parametrize('address',['203.0.113.10','2001:db8::10'])
def test_private_scope_uses_auto_target_and_accepts_either_family_while_public_checks_keep_selected_family(service,monkeypatch,scope,global_family,address):
    proxies,proxy_id=service
    proxies.state.save_settings(Settings(proxy_ip_version=global_family))
    if scope!='public':
        with proxies.db.session.begin() as session:
            if scope=='private_attached':
                session.add(Profile(id=1,name='Synthetic BM',profile_path=str(proxies.config.profiles_dir/'Fixture'),proxy_id=proxy_id))
                session.flush()
                session.add(BmProfile(profile_id=1))
            session.add(BmProxy(proxy_id=proxy_id,profile_id=1 if scope=='private_attached' else None))
    calls=mock_response(monkeypatch,proxies,address)
    proxies.checking.add(proxy_id)
    immediate(proxies._check(proxy_id))
    effective='auto' if scope!='public' else global_family
    assert calls==[proxies.config.ip_check_url_for(effective)]
    expected_working=effective=='auto' or ipaddress.ip_address(address).version==int(global_family[-1])
    with proxies.db.session() as session:
        proxy=session.get(Proxy,proxy_id)
        assert proxy.status==('Working' if expected_working else 'Failed')
        assert proxy.ip_address==(address if expected_working else None)
        assert proxy.error_message is None if expected_working else '[IP_RESPONSE]' in proxy.error_message
        assert proxy.last_checked is not None
    assert proxy_id not in proxies.checking
    assert proxies.state.settings.proxy_ip_version==global_family


def test_public_check_keeps_its_initial_family_when_settings_change_during_request(service,monkeypatch):
    proxies,proxy_id=service
    proxies.state.save_settings(Settings(proxy_ip_version='ipv6'))
    calls=mock_response(monkeypatch,proxies,'2001:db8::10',change_family='ipv4')
    immediate(proxies._check(proxy_id))
    assert calls==[proxies.config.ip_check_url_for('ipv6')]
    with proxies.db.session() as session:
        proxy=session.get(Proxy,proxy_id)
        assert proxy.status=='Working' and proxy.ip_address=='2001:db8::10'
    assert proxies.state.settings.proxy_ip_version=='ipv4'
