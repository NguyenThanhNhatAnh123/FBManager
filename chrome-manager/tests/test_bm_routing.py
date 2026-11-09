"""BM browsers require their private proxy and route every website through it.

All launches and adapters are spies; these tests never start Chrome or a network
connection, and credentials/endpoints are synthetic fixtures.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Config
from app.core.schemas import Settings
from app.core.security import encrypt_credentials
from app.database.session import Database
from app.models.entities import BmProfile,BmProxy,Event,Profile,Proxy
from app.services.browsers import BrowserManager
from app.services.proxies import ProxyService
from app.services.state import State


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in a synchronous fixture test')


@pytest.fixture
def manager(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    state.save_settings(Settings(require_proxy=False,proxy_routing='facebook_only'))
    resources=SimpleNamespace(blocked_reason=lambda _:None)
    browser=BrowserManager(db,state,config,resources)
    proxies=ProxyService(db,state,config)
    ids=proxies.import_proxies('192.0.2.1:10001\n192.0.2.2:10002',False)['proxy_ids']
    with db.session.begin() as session:
        for pid,proxy_id in [(1,ids[0]),(2,ids[1]),(3,None)]:
            session.add(Profile(id=pid,name=f'Fixture {pid}',profile_path=str(config.profiles_dir/f'Fixture_{pid}'),proxy_id=proxy_id))
        session.flush()
        session.add(BmProfile(profile_id=1))
        session.add(BmProxy(proxy_id=ids[0],profile_id=1))
    yield browser
    for lock in browser.locks.values():lock.release()
    db.engine.dispose()


@pytest.mark.parametrize('invalid',['missing','public','wrong_owner'])
def test_bm_requires_an_owned_private_proxy_even_when_direct_connections_are_globally_allowed(manager,invalid):
    with manager.db.session.begin() as session:
        profile=session.get(Profile,1)
        scope=session.get(BmProxy,profile.proxy_id)
        if invalid=='missing':
            profile.proxy_id=None
            scope.profile_id=None
        elif invalid=='public':session.delete(scope)
        else:scope.profile_id=3
    with pytest.raises(ValueError,match='proxy'):
        manager.validate_start(1)
    manager.validate_start(3)


@pytest.mark.parametrize('status',['Failed','Timeout'])
def test_bm_failed_proxy_blocks_start_in_facebook_only_mode_while_normal_profile_is_unchanged(manager,status):
    with manager.db.session.begin() as session:
        for proxy in session.scalars(select(Proxy)):proxy.status=status
    with pytest.raises(ValueError,match='failed its last check'):manager.validate_start(1)
    manager.validate_start(2)
    manager.state.save_settings(Settings(require_proxy=False,proxy_routing='all'))
    with pytest.raises(ValueError,match='failed its last check'):manager.validate_start(2)


@pytest.mark.parametrize('bm',[False,True])
@pytest.mark.parametrize('automation',[False,True])
@pytest.mark.parametrize('authenticated',[False,True])
@pytest.mark.parametrize('global_routing',['all','facebook_only'])
def test_launch_uses_all_routing_for_bm_and_preserves_normal_profile_policy(manager,monkeypatch,bm,automation,authenticated,global_routing):
    manager.state.save_settings(Settings(require_proxy=False,proxy_routing=global_routing))
    pid=1 if bm else 2
    with manager.db.session.begin() as session:
        profile=session.get(Profile,pid)
        proxy=session.get(Proxy,profile.proxy_id)
        if authenticated:proxy.encrypted_credentials=encrypt_credentials('FixtureUser','FixturePassword')
        expected_path=profile.profile_path
        expected_upstream=f'{proxy.protocol}://{proxy.host}:{proxy.port}'
    recorded={}
    class Context:
        def on(self,*_):pass
    class Manual(Context):
        def __init__(self,config,driver,path,options,args,log):
            recorded.update(path=str(path),proxy=options,arguments=args,manual=True)
        async def start(self,_):return self
    class Bridge:
        def __init__(self,upstream,path,on_error,*,routing):recorded['routing']=routing
        async def start(self):return {'server':'http://127.0.0.1:4567'}
        async def close(self):pass
    async def launch(path,**options):
        recorded.update(path=path,proxy=options['proxy'],arguments=options['args'],manual=False)
        return Context()
    async def wait(awaitable,timeout):return await awaitable
    monkeypatch.setattr('app.services.browsers.ManualBrowser',Manual)
    monkeypatch.setattr('app.services.browsers.BrowserProxyBridge',Bridge)
    monkeypatch.setattr('app.services.browsers.BrowserNetworkDiagnostics',lambda *_:None)
    monkeypatch.setattr('app.services.browsers.asyncio.wait_for',wait)
    manager.playwright=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    immediate(manager._launch(pid,open_start_page=False,for_automation=automation))
    expected_routing='all' if bm else global_routing
    needs_bridge=authenticated or expected_routing=='facebook_only'
    assert recorded['path']==expected_path and recorded['manual'] is not automation
    assert recorded['proxy']=={'server':'http://127.0.0.1:4567' if needs_bridge else expected_upstream}
    assert recorded.get('routing')==(expected_routing if needs_bridge else None)
    assert '--proxy-bypass-list=<-loopback>' in recorded['arguments']
    assert manager.state.settings.proxy_routing==global_routing
    with manager.db.session() as session:
        logs=[row.message for row in session.scalars(select(Event).where(Event.profile_id==pid))]
    assert any('other domains use the direct connection' in message for message in logs)==(expected_routing=='facebook_only')
