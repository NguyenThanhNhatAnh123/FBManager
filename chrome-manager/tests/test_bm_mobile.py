"""BM mobile launches use real emulation options without opening any browser."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from filelock import FileLock
from playwright.async_api import Error as PlaywrightError
from sqlalchemy import select

from app.api import routes
from app.core.config import Config
from app.core.first_open import pending_first_open,schedule_first_open,STORE_URL
from app.core.mobile_emulation import BM_MOBILE_DEVICE,bm_mobile_options
from app.core.schemas import Settings
from app.core.security import encrypt_credentials
from app.database.session import Database
from app.models.entities import BmBrowserSettings,BmProfile,BmProxy,Event,Profile,Proxy
from app.services.browsers import BrowserManager
from app.services.proxies import ProxyService
from app.services.state import State


PIXEL_7={
    'user_agent':'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.8078.4 Mobile Safari/537.36',
    'viewport':{'width':412,'height':839},'device_scale_factor':2.625,
    'is_mobile':True,'has_touch':True,'default_browser_type':'chromium',
}


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
    browser=BrowserManager(db,state,config,SimpleNamespace(blocked_reason=lambda _:None))
    proxies=ProxyService(db,state,config)
    ids=proxies.import_proxies('192.0.2.1:10001\n192.0.2.2:10002',False)['proxy_ids']
    with db.session.begin() as session:
        for pid,proxy_id in [(1,ids[0]),(2,ids[1])]:
            session.add(Profile(id=pid,name=f'Fixture {pid}',profile_path=str(config.profiles_dir/f'Fixture_{pid}'),proxy_id=proxy_id))
        session.flush()
        session.add(BmProfile(profile_id=1))
        session.add(BmProxy(proxy_id=ids[0],profile_id=1))
        session.flush()
        session.add(BmBrowserSettings(profile_id=1,mobile_enabled=True))
    yield browser
    for lock in browser.locks.values():lock.release()
    db.engine.dispose()


def fake_browser(manager,monkeypatch,navigation_error=False,empty=False):
    calls={'launches':[],'manual':[],'navigations':[],'bridges':[],'new_pages':0}
    class Page:
        async def goto(self,url,**options):
            calls['navigations'].append((url,options))
            if navigation_error:raise PlaywrightError('net::ERR_PROXY_CONNECTION_FAILED synthetic-private-value')
        async def bring_to_front(self):calls['front']=True
    class Context:
        def __init__(self):self.pages=[] if empty else [Page()]
        def on(self,*_):pass
        async def new_page(self):
            calls['new_pages']+=1
            page=Page();self.pages.append(page);return page
        async def close(self):calls['closed']=True
    async def launch(path,**options):
        calls['launches'].append((path,options))
        return Context()
    class Manual(Context):
        def __init__(self,config,driver,path,options,args,log):
            super().__init__();calls['manual'].append((str(path),options,args))
        async def start(self,open_start_page):calls['manual_start']=open_start_page;return self
    class Bridge:
        def __init__(self,upstream,path,on_error,*,routing):calls['bridges'].append(routing)
        async def start(self):return {'server':'http://127.0.0.1:4567'}
        async def close(self):pass
    async def wait(awaitable,timeout):return await awaitable
    monkeypatch.setattr('app.services.browsers.ManualBrowser',Manual)
    monkeypatch.setattr('app.services.browsers.BrowserProxyBridge',Bridge)
    monkeypatch.setattr('app.services.browsers.BrowserNetworkDiagnostics',lambda *_:None)
    monkeypatch.setattr('app.services.browsers.asyncio.wait_for',wait)
    manager.playwright=SimpleNamespace(devices={BM_MOBILE_DEVICE:deepcopy(PIXEL_7)},chromium=SimpleNamespace(launch_persistent_context=launch))
    return calls


def test_installed_device_descriptor_only_passes_documented_launch_options_and_is_not_mutated():
    descriptor=deepcopy(PIXEL_7)
    driver=SimpleNamespace(devices={BM_MOBILE_DEVICE:descriptor})
    options=bm_mobile_options(driver)
    assert options=={key:value for key,value in descriptor.items() if key!='default_browser_type'}
    options['viewport']['width']=1
    assert descriptor==PIXEL_7


def test_legacy_bm_without_a_saved_mobile_setting_keeps_manual_browsing(manager,monkeypatch):
    with manager.db.session.begin() as session:session.delete(session.get(BmBrowserSettings,1))
    calls=fake_browser(manager,monkeypatch)
    immediate(manager._launch(1,open_start_page=False))
    assert calls['manual'] and not calls['launches'] and manager.mobile_enabled(1) is False


@pytest.mark.parametrize('profile_kind',['mobile_bm','desktop_bm','regular'])
@pytest.mark.parametrize('automation',[False,True])
def test_mobile_flag_controls_persistent_emulation_while_default_launches_are_unchanged(manager,monkeypatch,profile_kind,automation):
    pid=2 if profile_kind=='regular' else 1
    if profile_kind=='desktop_bm':
        with manager.db.session.begin() as session:session.get(BmBrowserSettings,1).mobile_enabled=False
    calls=fake_browser(manager,monkeypatch)
    immediate(manager._launch(pid,open_start_page=False,for_automation=automation))
    mobile=profile_kind=='mobile_bm'
    assert bool(calls['launches'])==(automation or mobile)
    assert bool(calls['manual'])==(not(automation or mobile))
    if automation or mobile:
        path,options=calls['launches'][0]
        assert path==str(manager.config.profiles_dir/f'Fixture_{pid}')
        assert options['ignore_default_args']==['--disable-extensions']
        assert options['service_workers']==('block' if automation else 'allow')
        if mobile:
            assert all(options[key]==value for key,value in PIXEL_7.items() if key!='default_browser_type')
            assert options['proxy']=={'server':'http://192.0.2.1:10001'}
        else:
            assert options['viewport'] is None
            assert not any(key in options for key in ('is_mobile','has_touch','user_agent','device_scale_factor'))
        assert 'default_browser_type' not in options
    assert calls['navigations']==[]


@pytest.mark.parametrize('profile_kind,automation,expected',[
    ('mobile_bm',False,'mobile'),('mobile_bm',True,'automation'),
    ('desktop_bm',False,'manual'),('desktop_bm',True,'automation'),
    ('regular',False,'manual'),('regular',True,'automation'),
])
def test_request_start_tracks_mobile_browsing_separately_from_real_tasks(manager,monkeypatch,profile_kind,automation,expected):
    pid=2 if profile_kind=='regular' else 1
    if profile_kind=='desktop_bm':
        with manager.db.session.begin() as session:session.get(BmBrowserSettings,1).mobile_enabled=False
    class Job:
        def add_done_callback(self,_):pass
    def task(coroutine):coroutine.close();return Job()
    monkeypatch.setattr('app.services.browsers.asyncio.create_task',task)
    assert manager.request_start(pid,for_automation=automation)=='Queued'
    assert manager.mode(pid)==expected


@pytest.mark.parametrize('empty',[False,True])
def test_mobile_normal_open_navigates_facebook_without_f12(manager,monkeypatch,empty):
    calls=fake_browser(manager,monkeypatch,empty=empty)
    immediate(manager._launch(1,open_start_page=True))
    assert calls['navigations']==[('https://www.facebook.com/',{'wait_until':'commit','timeout':45000})]
    assert calls['front'] and calls['new_pages']==int(empty)


def test_mobile_start_page_failure_keeps_chrome_open_and_never_logs_raw_error(manager,monkeypatch):
    calls=fake_browser(manager,monkeypatch,navigation_error=True)
    context=immediate(manager._launch(1,open_start_page=True))
    assert manager.contexts[1] is context and manager.status(1)=='Running'
    assert not calls.get('closed')
    with manager.db.session() as session:
        logs=[row.message for row in session.scalars(select(Event).where(Event.profile_id==1))]
    assert any('start page could not finish loading' in message for message in logs)
    assert all('synthetic-private-value' not in message for message in logs)


@pytest.mark.parametrize('navigation_error',[False,True])
def test_mobile_honors_pending_first_open_and_only_consumes_success(manager,monkeypatch,navigation_error):
    directory=manager.config.profiles_dir/'Fixture_1'
    schedule_first_open(directory,manager.config.profiles_dir,1)
    calls=fake_browser(manager,monkeypatch,navigation_error=navigation_error)
    immediate(manager._launch(1,open_start_page=True))
    assert calls['navigations'][0][0]==STORE_URL
    assert pending_first_open(directory)==(STORE_URL if navigation_error else None)


def test_mobile_automation_does_not_open_a_browsing_start_page(manager,monkeypatch):
    calls=fake_browser(manager,monkeypatch)
    immediate(manager._launch(1,open_start_page=True,for_automation=True))
    assert calls['navigations']==[]


def test_bm_open_uses_mobile_context_new_page_and_keeps_manual_bm_navigation_compatible(manager,monkeypatch):
    calls=fake_browser(manager,monkeypatch)
    immediate(manager._launch(1,open_start_page=False))
    manager.modes[1]='mobile'
    services=SimpleNamespace(db=manager.db,browsers=manager,tasks=SimpleNamespace(profile_jobs={}))
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))
    assert immediate(routes.open_bm_profile(1,request))['status']=='opened'
    assert manager.mode(1)=='mobile' and calls['new_pages']==1
    assert calls['navigations'][0][0]=='https://business.facebook.com/settings/'


def test_mobile_launch_preserves_extension_arguments_private_proxy_and_profile_lock(manager,monkeypatch):
    manager.config.authenticator_deployment={'bundle_path':str(manager.config.root/'extension-fixture')}
    with manager.db.session.begin() as session:
        profile=session.get(Profile,1)
        proxy_id,path=profile.proxy_id,profile.profile_path
        session.get(Proxy,proxy_id).encrypted_credentials=encrypt_credentials('FixtureUser','FixturePassword')
    calls=fake_browser(manager,monkeypatch)
    immediate(manager._launch(1,open_start_page=False))
    options=calls['launches'][0][1]
    assert options['channel']=='chromium' and options['service_workers']=='allow'
    assert calls['bridges']==['all'] and options['proxy']=={'server':'http://127.0.0.1:4567'}
    assert f'--load-extension={manager.config.root / "extension-fixture"}' in options['args']
    assert manager.locks[1].is_locked
    with manager.db.session() as session:
        profile=session.get(Profile,1)
        assert profile.proxy_id==proxy_id and profile.profile_path==path


def test_mobile_browser_cannot_be_reused_by_a_real_task_until_stopped(manager,monkeypatch):
    fake_browser(manager,monkeypatch)
    immediate(manager._launch(1,open_start_page=False))
    manager.modes[1]='mobile'
    with pytest.raises(ValueError,match='Stop this profile'):manager.validate_automation_start(1)


def test_mobile_launch_keeps_existing_profile_lock_guard(manager,monkeypatch):
    directory=manager.config.profiles_dir/'Fixture_1';directory.mkdir()
    calls=fake_browser(manager,monkeypatch)
    with FileLock(str(directory/'.manager.lock')):
        with pytest.raises(RuntimeError,match='locked'):immediate(manager._launch(1,open_start_page=False))
    assert not calls['launches'] and not calls['manual'] and not manager.locks
