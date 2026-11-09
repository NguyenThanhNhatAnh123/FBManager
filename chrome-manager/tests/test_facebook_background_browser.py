"""Facebook headless routing with fake Playwright and disposable SQLite only."""
from types import SimpleNamespace
import pytest
from app.core.config import Config
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import Profile,Proxy
from app.services.browsers import BrowserManager
from app.services.state import State
from app.services import browsers as module


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in an offline fixture')


@pytest.fixture
def manager(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data',headless=False);config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    state.save_settings(Settings(require_proxy=True,proxy_routing='all'))
    manager=BrowserManager(db,state,config,SimpleNamespace(blocked_reason=lambda _:None))
    directory=config.profiles_dir/'Fixture101';directory.mkdir()
    (directory/'retained-session-fixture.txt').write_text('fixture session retained',encoding='utf-8')
    with db.session.begin() as session:
        session.add(Proxy(id=21,host='192.0.2.21',port=12021,protocol='http',identity='fixture-background-proxy',status='Working'))
        session.flush();session.add(Profile(id=101,name='Fixture101',profile_path=str(directory),proxy_id=21))
    yield manager
    for lock in manager.locks.values():
        if lock.is_locked:lock.release()
    db.engine.dispose()


def fake_launch(manager,monkeypatch):
    calls={'persistent':[],'manual':[]}
    class Context:
        def on(self,*_):pass
        async def close(self):calls['closed']=True
    async def persistent(path,**options):
        calls['persistent'].append((path,options));return Context()
    class Manual(Context):
        def __init__(self,config,driver,path,proxy,args,log):
            calls['manual'].append({'path':str(path),'proxy':proxy,'args':args,'configured_headless':config.headless})
        async def start(self,open_start_page):calls['manual_start']=open_start_page;return self
    async def wait(awaitable,timeout):return await awaitable
    manager.playwright=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=persistent))
    monkeypatch.setattr(module,'ManualBrowser',Manual)
    monkeypatch.setattr(module,'BrowserNetworkDiagnostics',lambda *_:None)
    monkeypatch.setattr(module.asyncio,'wait_for',wait)
    return calls


@pytest.mark.parametrize('purpose',['facebook','outlook','reel','automation','manual'])
def test_request_start_forwards_facebook_purpose_without_changing_other_modes(manager,monkeypatch,purpose):
    calls=[]
    def launch(pid,**options):
        calls.append((pid,options))
        async def pending():return None
        return pending()
    class Job:
        def add_done_callback(self,_):pass
    def task(coroutine):coroutine.close();return Job()
    monkeypatch.setattr(manager,'_launch',launch);monkeypatch.setattr(module.asyncio,'create_task',task)
    options={'for_automation':purpose!='manual','open_start_page':purpose=='manual'}
    if purpose in ('facebook','outlook','reel'):options['for_'+purpose]=True
    assert manager.request_start(101,**options)=='Queued'
    pid,forwarded=calls[0]
    assert pid==101 and forwarded.get('for_facebook',False)==(purpose=='facebook')
    assert forwarded['for_automation']==(purpose!='manual')
    assert forwarded['open_start_page']==(purpose=='manual')
    assert manager.mode(101)==('manual' if purpose=='manual' else 'automation')


def test_ensure_open_preserves_facebook_flag_timezone_and_saved_context(manager,monkeypatch):
    calls=[];context=object();manager.contexts[101]=context
    monkeypatch.setattr(manager,'request_start',lambda pid,**options:calls.append((pid,options)))
    assert immediate(manager.ensure_open(101,for_automation=True,for_facebook=True,timezone_id='UTC')) is context
    assert calls==[(101,{'open_start_page':False,'for_automation':True,'for_facebook':True,'for_outlook':False,'for_recipient':False,'for_reel':False,'timezone_id':'UTC'})]


@pytest.mark.parametrize('configured_headless',[False,True])
def test_facebook_controlled_launch_is_hidden_and_keeps_existing_proxy_storage_and_lock(manager,monkeypatch,configured_headless):
    manager.config.headless=configured_headless;calls=fake_launch(manager,monkeypatch)
    context=immediate(manager._launch(101,open_start_page=False,for_automation=True,for_facebook=True,timezone_id='UTC'))
    assert not calls['manual'] and len(calls['persistent'])==1
    path,options=calls['persistent'][0]
    assert options['headless'] is True
    assert options['proxy']=={'server':'http://192.0.2.21:12021'} and options['timezone_id']=='UTC'
    assert options['service_workers']=='block' and options['ignore_default_args']==['--disable-extensions']
    assert '--proxy-bypass-list=<-loopback>' in options['args']
    assert path==str(manager.config.profiles_dir/'Fixture101')
    assert manager.contexts[101] is context and manager.locks[101].is_locked
    assert (manager.config.profiles_dir/'Fixture101'/'retained-session-fixture.txt').read_text(encoding='utf-8')=='fixture session retained'
    with manager.db.session() as session:
        profile=session.get(Profile,101)
        assert profile.profile_path==path and profile.proxy_id==21


@pytest.mark.parametrize('configured_headless',[False,True])
def test_nonfacebook_automation_uses_global_headless_config(manager,monkeypatch,configured_headless):
    manager.config.headless=configured_headless;calls=fake_launch(manager,monkeypatch)
    immediate(manager._launch(101,open_start_page=False,for_automation=True))
    assert len(calls['persistent'])==1 and not calls['manual']
    assert calls['persistent'][0][1]['headless'] is configured_headless


@pytest.mark.parametrize('configured_headless',[False,True])
def test_normal_open_keeps_the_existing_manual_browser_path(manager,monkeypatch,configured_headless):
    manager.config.headless=configured_headless;calls=fake_launch(manager,monkeypatch)
    immediate(manager._launch(101,open_start_page=True))
    assert not calls['persistent'] and len(calls['manual'])==1
    assert calls['manual'][0]['configured_headless'] is configured_headless
    assert calls['manual'][0]['proxy']=={'server':'http://192.0.2.21:12021'}
    assert calls['manual_start'] is True
