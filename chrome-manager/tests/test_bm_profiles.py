"""BM profile classification and manual navigation; no Meta grants or real accounts."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app.api import routes
from app.core.config import Config
from app.core.schemas import ProfileInput,BmProfileInput,BmOpenInput,Settings
from app.database.session import Database
from app.models.entities import Profile,Proxy,BmProfile,BmProxy
from app.services.state import State
from app.services.resources import Resources
from app.services.browsers import BrowserManager
from app.services.proxies import ProxyService
from app.automation.manager import TaskManager
from app.services.manual_browser import ManualBrowser


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O')


@pytest.fixture
def svc(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db);state.save_settings(Settings(require_proxy=False))
    browsers=BrowserManager(db,state,config,Resources())
    services=SimpleNamespace(db=db,state=state,config=config,browsers=browsers,proxies=ProxyService(db,state,config),tasks=TaskManager(db,state,browsers,config))
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))
    path=config.profiles_dir/'Existing';path.mkdir();(path/'session-fixture.txt').write_text('retained')
    with db.session.begin() as session:
        proxy=Proxy(host='192.0.2.1',port=8080,protocol='http',identity='fixture',encrypted_credentials=None)
        session.add(proxy);session.flush()
        session.add(Profile(name='Existing',profile_path=str(path),proxy_id=proxy.id))
    yield request,services
    db.engine.dispose()


def metadata(**changes):
    return BmProfileInput(**({'portfolios':[{'bm_id':'123456789012345','name':'Business fixture'}],'note':'Profile used for BM'}|changes))


def test_classification_update_and_return_preserve_the_existing_profile(svc):
    request,services=svc
    with services.db.session() as session:
        original=session.get(Profile,1)
        path,proxy=original.profile_path,original.proxy_id
    immediate(routes.assign_bm_profile(1,metadata(),request))
    row=immediate(routes.list_profiles(request))[0]
    assert row['bm']['portfolios']==[{'bm_id':'123456789012345','name':'Business fixture'}]
    assert row['bm']['note']=='Profile used for BM'
    assert row['proxy_id']==proxy
    immediate(routes.update_profile(1,ProfileInput(name='Renamed BM',proxy_id=proxy,revision=row['revision']),request))
    assert immediate(routes.list_profiles(request))[0]['bm']['portfolios'][0]['bm_id']=='123456789012345'
    immediate(routes.assign_bm_profile(1,metadata(note='Updated'),request))
    reopened=Database(services.config.data_dir/'manager.sqlite3')
    with reopened.session() as session:assert session.get(BmProfile,1).note=='Updated'
    reopened.engine.dispose()
    with pytest.raises(HTTPException):immediate(routes.unassign_bm_profile(1,request))
    immediate(routes.remove_bm_proxy(1,request))
    immediate(routes.unassign_bm_profile(1,request))
    row=immediate(routes.list_profiles(request))[0]
    assert row['bm'] is None and row['name']=='Renamed BM'
    assert row['profile_path']==path and row['proxy_id'] is None
    with services.db.session() as session:assert session.get(BmProxy,proxy).profile_id is None
    assert (Path(path)/'session-fixture.txt').read_text()=='retained'


def test_create_bm_profile_in_one_transaction_and_metadata_cascades(svc):
    request,services=svc
    result=immediate(routes.create_profile(ProfileInput(name='New BM profile',bm=metadata()),request))
    with services.db.session.begin() as session:
        row=session.get(Profile,result['id'])
        assert Path(row.profile_path).is_dir() and session.get(BmProfile,row.id) is not None
        session.delete(row)
    with services.db.session() as session:assert session.get(BmProfile,result['id']) is None
    assert immediate(routes.list_profiles(request))[0]['bm'] is None


@pytest.mark.parametrize('value',['0','-1','123x','https://business.facebook.com/','1'*31])
def test_bm_ids_are_bounded_positive_numeric_ids(value):
    with pytest.raises(ValidationError):metadata(portfolios=[{'bm_id':value}])


def test_duplicate_ids_are_rejected_and_empty_metadata_is_allowed():
    assert metadata(portfolios=[]).portfolios==[]
    with pytest.raises(ValidationError):metadata(portfolios=[{'bm_id':'123'},{'bm_id':'123'}])


@pytest.mark.parametrize('busy',['Running','Starting','Queued','task','recipient','reel'])
def test_bm_role_transitions_require_an_idle_profile(svc,monkeypatch,busy):
    request,services=svc

    def mark_busy(patch):
        if busy=='task':
            patch.setitem(services.tasks.profile_jobs,1,123)
        elif busy in ('recipient','reel'):
            patch.setattr(services.browsers,busy+'_profiles',{1})
        else:
            patch.setattr(services.browsers,'status',lambda _:busy)

    with monkeypatch.context() as patch:
        mark_busy(patch)
        with pytest.raises(HTTPException) as error:
            immediate(routes.assign_bm_profile(1,metadata(),request))
        assert error.value.status_code==409
    with services.db.session() as session:
        assert session.get(BmProfile,1) is None
        profile=session.get(Profile,1)
        assert profile.proxy_id is not None and session.get(BmProxy,profile.proxy_id) is None

    immediate(routes.assign_bm_profile(1,metadata(),request))
    immediate(routes.remove_bm_proxy(1,request))
    with monkeypatch.context() as patch:
        mark_busy(patch)
        if busy not in ('recipient','reel'):
            assert immediate(routes.assign_bm_profile(1,metadata(note='Updated while open'),request))['ok']
        with pytest.raises(HTTPException) as error:
            immediate(routes.unassign_bm_profile(1,request))
        assert error.value.status_code==409
    with services.db.session() as session:
        assert session.get(BmProfile,1) is not None


def test_open_bm_uses_the_selected_profile_and_registered_business(svc,monkeypatch):
    request,services=svc;opened=[];ensured=[]
    immediate(routes.assign_bm_profile(1,metadata(),request))
    class Manual:
        is_manual=True
        async def open_url(self,url):opened.append(url)
    async def ensure(pid):ensured.append(pid);return Manual()
    monkeypatch.setattr(services.browsers,'ensure_open',ensure)
    immediate(routes.open_bm_profile(1,request,BmOpenInput(bm_id='123456789012345')))
    immediate(routes.open_bm_profile(1,request))
    assert ensured==[1,1]
    target='https://business.facebook.com/settings/?business_id=123456789012345'
    assert opened==[target,target]
    with pytest.raises(HTTPException) as error:immediate(routes.open_bm_profile(1,request,BmOpenInput(bm_id='999')))
    assert error.value.status_code==404 and len(opened)==2
    immediate(routes.assign_bm_profile(1,metadata(portfolios=[{'bm_id':'123456789012345'},{'bm_id':'123456789012346'}]),request))
    with pytest.raises(HTTPException) as error:immediate(routes.open_bm_profile(1,request))
    assert error.value.status_code==422 and ensured==[1,1]
    immediate(routes.assign_bm_profile(1,metadata(portfolios=[]),request))
    immediate(routes.open_bm_profile(1,request))
    assert opened[-1]=='https://business.facebook.com/settings/'


def test_open_bm_rechecks_selection_after_waiting_for_browser(svc,monkeypatch):
    request,services=svc
    immediate(routes.assign_bm_profile(1,metadata(),request))

    async def ensure(pid):
        await routes.assign_bm_profile(pid,metadata(portfolios=[{'bm_id':'999'}]),request)
        return SimpleNamespace(is_manual=True)

    monkeypatch.setattr(services.browsers,'ensure_open',ensure)
    with pytest.raises(HTTPException) as error:
        immediate(routes.open_bm_profile(1,request))
    assert error.value.status_code==409


@pytest.mark.parametrize('busy',['outlook','facebook','extension','task','automation'])
def test_open_bm_rejects_profiles_owned_by_an_active_automation(svc,monkeypatch,busy):
    request,services=svc
    immediate(routes.assign_bm_profile(1,metadata(),request))
    if busy=='outlook':services.browsers.outlook_profiles.add(1)
    elif busy=='facebook':services.browsers.facebook_profiles.add(1)
    elif busy=='extension':services.browsers.extension_profiles.add(1)
    elif busy=='task':services.tasks.profile_jobs[1]=1
    else:monkeypatch.setattr(services.browsers,'mode',lambda _:'automation')
    with pytest.raises(HTTPException) as error:immediate(routes.open_bm_profile(1,request))
    assert error.value.status_code==409


@pytest.mark.parametrize('url',[
    'http://business.facebook.com/settings/',
    'https://business.facebook.com.evil.example/settings/',
    'https://user:secret@business.facebook.com/settings/',
    'https://business.facebook.com:444/settings/',
    'https://business.facebook.com/settings/?business_id=0',
    'https://business.facebook.com/settings/?business_id=123&next=https://example.com/',
    'https://business.facebook.com/settings/?business_id=123#other',
])
def test_manual_bm_destinations_cannot_be_used_as_arbitrary_navigation(url):
    manual=ManualBrowser(SimpleNamespace(authenticator_deployment={},channel=''),
                         SimpleNamespace(chromium=SimpleNamespace(executable_path='fixture-chrome.exe')),
                         Path('fixture-profile'),None,[],lambda _:None)
    with pytest.raises(ValueError,match='Unsupported'):immediate(manual.open_url(url))


@pytest.mark.parametrize('url',['https://business.facebook.com/settings/','https://business.facebook.com/settings/?business_id=123456789012345'])
def test_manual_bm_destinations_forward_to_the_exact_saved_profile(tmp_path,monkeypatch,url):
    directory=tmp_path/'profile';calls=[]
    manual=ManualBrowser(SimpleNamespace(authenticator_deployment={},channel=''),
                         SimpleNamespace(chromium=SimpleNamespace(executable_path='fixture-chrome.exe')),directory,None,[],lambda _:None)
    class Process:
        async def wait(self):return 0
    async def start(*args,**_):calls.append(args);return Process()
    async def wait(operation,timeout):return await operation
    monkeypatch.setattr('app.services.manual_browser.asyncio.create_subprocess_exec',start)
    monkeypatch.setattr('app.services.manual_browser.asyncio.wait_for',wait)
    immediate(manual.open_url(url))
    assert calls==[('fixture-chrome.exe',f'--user-data-dir={directory}','--new-tab',url)]
