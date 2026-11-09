"""Synchronous service integration checks; no event loop or browser is substituted."""
from types import SimpleNamespace
import json
import pytest
from sqlalchemy import select
from app.api import routes
from app.automation.manager import TaskManager, decode_task
from app.core.config import Config
from app.core.schemas import BulkCreate, ProfileInput, ProxyImport, Settings, TaskInput
from app.database.session import Database
from app.models.entities import Profile, Proxy, Task
from app.services.browsers import BrowserManager
from app.services.proxies import ProxyService
from app.services.resources import Resources
from app.services.state import State
from app.services.extensions import ExtensionManager

def immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()
    raise AssertionError('This service check must not wait on asynchronous I/O.')

@pytest.fixture
def request_and_services(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data')
    config.prepare()
    db=Database(config.data_dir/'manager.sqlite3')
    state=State(db)
    resources=Resources()
    browsers=BrowserManager(db,state,config,resources)
    proxies=ProxyService(db,state,config)
    tasks=TaskManager(db,state,browsers,config)
    extensions=ExtensionManager(db,state,config,browsers,tasks)
    svc=SimpleNamespace(config=config,db=db,state=state,resources=resources,browsers=browsers,proxies=proxies,tasks=tasks,extensions=extensions)
    proxies.is_busy=lambda pid: pid in tasks.profile_jobs or browsers.status(pid) in ('Running','Starting','Queued')
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=svc)))
    yield request,svc
    db.engine.dispose()

def test_bulk_creation_mapping_and_database_reopen(request_and_services):
    request,svc=request_and_services
    result=immediate(routes.bulk_create(BulkCreate(count=100),request))
    assert result['created']==100
    assert immediate(routes.bulk_create(BulkCreate(count=100),request))['created']==0
    content='\r\n'.join(f'192.0.2.10:{20000+i}:account_{i}:test_secret_{i}' for i in range(100))
    imported=immediate(routes.import_proxies(ProxyImport(content=content),request))
    assert imported['created']==100 and imported['assigned']==100 and imported['errors']==[]
    profiles=immediate(routes.list_profiles(request))
    assert len(profiles)==100 and len({p['profile_path'] for p in profiles})==100
    assert len({p['proxy_id'] for p in profiles})==100
    for i,profile in enumerate(profiles):
        assert profile['name']==f'Chrome_{i+1:03d}'
        assert profile['proxy']['port']==20000+i
    assert immediate(routes.import_proxies(ProxyImport(content=content),request))['duplicates']==100
    reopened=Database(svc.config.data_dir/'manager.sqlite3')
    with reopened.session() as session:
        assert len(list(session.scalars(select(Profile))))==100
        assert len(list(session.scalars(select(Proxy))))==100
        assert 'test_secret_0' not in session.get(Proxy,1).encrypted_credentials
    reopened.engine.dispose()


def test_import_can_queue_only_its_valid_distinct_proxy_ids_after_commit(request_and_services,monkeypatch):
    request,svc=request_and_services
    existing=svc.proxies.import_proxies('192.0.2.1:12000\n192.0.2.2:12001',False)['proxy_ids']
    calls=[]
    def schedule(ids):
        with svc.db.session() as session:
            assert set(ids)<=set(session.scalars(select(Proxy.id)))
        calls.append(ids)
        return ids
    monkeypatch.setattr(svc.proxies,'schedule_checks',schedule)
    result=immediate(routes.import_proxies(ProxyImport(content='192.0.2.1:12000\n192.0.2.3:12002\n192.0.2.3:12002\nnot valid proxy',assign=False,check_after_import=True),request))
    assert result['created']==1 and result['duplicates']==2 and len(result['errors'])==1
    assert result['checking']==result['proxy_ids']==calls[0]
    assert existing[0] in result['checking'] and existing[1] not in result['checking']
    assert len(result['checking'])==2


def test_saved_proxy_check_request_is_consumed_once_and_filters_deleted_ids(request_and_services,monkeypatch):
    _,svc=request_and_services
    ids=svc.proxies.import_proxies('192.0.2.1:12000',False)['proxy_ids']
    path=svc.config.data_dir/'pending-proxy-checks.json'
    path.write_text(json.dumps({'version':1,'proxy_ids':ids+ids+[999999]}))
    calls=[]
    monkeypatch.setattr(svc.proxies,'schedule_checks',lambda values:calls.append(values) or values)
    assert svc.proxies.schedule_pending_checks()==ids
    assert not path.exists() and calls==[ids]
    assert svc.proxies.schedule_pending_checks()==[]


def test_invalid_saved_proxy_check_request_never_schedules(request_and_services,monkeypatch):
    _,svc=request_and_services
    path=svc.config.data_dir/'pending-proxy-checks.json'
    path.write_text('{"version":1,"proxy_ids":[true,"1",-1]}')
    monkeypatch.setattr(svc.proxies,'schedule_checks',lambda _:pytest.fail('Invalid request must not schedule checks'))
    assert svc.proxies.schedule_pending_checks()==[] and path.exists()

def test_task_storage_and_settings_reopen(request_and_services):
    request,svc=request_and_services
    config=TaskInput(name='Owned website test',authorized=True,actions=[
        {'type':'open_url','url':'https://example.com'},
        {'type':'fill','selector':'#test','value':'test_private_value'},
        {'type':'screenshot'},
    ])
    task_id=immediate(routes.create_task(config,request))['id']
    with svc.db.session() as session:
        stored=session.get(Task,task_id).configuration
        assert 'test_private_value' not in stored
        assert decode_task(stored).actions[1].value=='test_private_value'
    svc.state.save_settings(Settings(max_browsers=10,allowed_origins=['https://example.com']))
    assert State(svc.db).settings.max_browsers==10

def test_profile_rename_and_delete_keep_boundary(request_and_services):
    request,svc=request_and_services
    profile_id=immediate(routes.create_profile(ProfileInput(name='Initial'),request))['id']
    initial=immediate(routes.list_profiles(request))[0]
    assert immediate(routes.update_profile(profile_id,ProfileInput(name='Renamed',revision=initial['revision']),request))['ok']
    updated=immediate(routes.list_profiles(request))[0]
    assert updated['name']=='Renamed' and updated['profile_path']==initial['profile_path']
    assert immediate(routes.delete_profile(profile_id,request,confirm=True))['ok']
    assert immediate(routes.list_profiles(request))==[]


def test_initial_dashboard_navigation_and_api_csrf_guard():
    from app.main import create_app
    from starlette.requests import Request
    from starlette.responses import Response
    app=create_app()
    dispatch=next(m.kwargs['dispatch'] for m in app.user_middleware if 'dispatch' in m.kwargs)
    async def page_handler(request):
        return Response('<!doctype html><title>Dashboard shell</title>',media_type='text/html')
    def request(path, method='GET'):
        return Request({
            'type':'http','method':method,'path':path,'raw_path':path.encode(),
            'query_string':b'','headers':[(b'host',b'127.0.0.1:8000'),(b'sec-fetch-site',b'cross-site')],
            'client':('127.0.0.1',50000),'server':('127.0.0.1',8000),'scheme':'http',
        })
    assert immediate(dispatch(request('/'),page_handler)).status_code==200
    assert immediate(dispatch(request('/api/session'),page_handler)).status_code==403
    assert immediate(dispatch(request('/api/profiles'),page_handler)).status_code==403
    assert immediate(dispatch(request('/api/profiles','POST'),page_handler)).status_code==403


def test_history_filters_apply_before_pagination(request_and_services):
    from app.models.entities import TaskRun
    request,svc=request_and_services
    task_id=immediate(routes.create_task(TaskInput(name='History fixture',authorized=True,actions=[{'type':'open_url','url':'https://example.com'}]),request))['id']
    with svc.db.session.begin() as session:
        for index in range(70):
            session.add(TaskRun(task_id=task_id,profile_id=None,profile_name=f'Profile_{index:03d}',status='Completed' if index%2==0 else 'Failed'))
    first=immediate(routes.task_history(request,limit=10,offset=0,status='Completed',q='Profile_0'))
    second=immediate(routes.task_history(request,limit=10,offset=10,status='Completed',q='Profile_0'))
    assert first['total']==35 and second['total']==35
    assert len(first['items'])==len(second['items'])==10
    assert all(row['status']=='Completed' for row in first['items']+second['items'])
    assert not ({r['id'] for r in first['items']}&{r['id'] for r in second['items']})
    assert immediate(routes.task_history(request,q='no match'))['total']==0


def test_authenticator_scope_inventory_and_missing_proxy_rejection(request_and_services):
    request,svc=request_and_services
    immediate(routes.bulk_create(BulkCreate(count=100),request))
    report=immediate(routes.authenticator_status(request))
    assert report['scope']=='manager_profiles_only' and report['require_proxy'] is True
    assert len(report['profiles'])==100
    assert all(p['status']=='Not installed' for p in report['profiles'])
    from app.core.schemas import Ids
    result=immediate(routes.setup_authenticator(Ids(profile_ids=list(range(1,101))),request))
    assert result['accepted']==[] and len(result['errors'])==100
    assert not svc.extensions.jobs and not svc.browsers.extension_profiles


def test_automation_refuses_profile_while_extension_setup_is_reserved(request_and_services):
    request,svc=request_and_services
    pid=immediate(routes.create_profile(ProfileInput(name='Profile'),request))['id']
    tid=immediate(routes.create_task(TaskInput(name='Task',authorized=True,actions=[{'type':'open_url','url':'https://example.com'}]),request))['id']
    svc.browsers.extension_profiles.add(pid)
    with pytest.raises(ValueError,match='installation tab'):
        svc.tasks.enqueue(tid,[pid])
    assert not svc.tasks.jobs


def test_automation_rejects_manual_browser_before_creating_runs(request_and_services):
    from app.models.entities import TaskRun
    request,svc=request_and_services
    svc.state.save_settings(Settings(require_proxy=False))
    pid=immediate(routes.create_profile(ProfileInput(name='Manual browser'),request))['id']
    tid=immediate(routes.create_task(TaskInput(name='Owned task',authorized=True,actions=[{'type':'open_url','url':'https://example.com'}]),request))['id']
    svc.browsers.contexts[pid]=object()
    svc.browsers.modes[pid]='manual'
    with pytest.raises(ValueError,match='Stop this profile'):
        svc.tasks.enqueue(tid,[pid])
    with svc.db.session() as session:
        assert not list(session.scalars(select(TaskRun)))
    assert not svc.tasks.jobs


def test_automation_requires_controlled_mode_for_queued_or_existing_context(request_and_services):
    request,svc=request_and_services
    svc.state.save_settings(Settings(require_proxy=False))
    pid=immediate(routes.create_profile(ProfileInput(name='Mode boundary'),request))['id']
    svc.browsers.launches[pid]=object()
    svc.browsers.modes[pid]='manual'
    with pytest.raises(ValueError,match='Stop this profile'):
        svc.browsers.validate_automation_start(pid)
    svc.browsers.modes.pop(pid)
    with pytest.raises(ValueError,match='Stop this profile'):
        svc.browsers.validate_automation_start(pid)
    svc.browsers.modes[pid]='automation'
    svc.browsers.validate_automation_start(pid)
    svc.browsers.launches.clear()
    svc.browsers.contexts[pid]=object()
    svc.browsers.modes.pop(pid)
    with pytest.raises(ValueError,match='Stop this profile'):
        svc.browsers.validate_automation_start(pid)


@pytest.mark.parametrize('automation,policy',[(False,'allow'),(True,'block')])
def test_browser_launch_applies_mode_service_worker_policy(request_and_services,monkeypatch,automation,policy):
    request,svc=request_and_services
    svc.state.save_settings(Settings(require_proxy=False))
    svc.resources.current={'cpu_percent':0,'memory_percent':20,'memory_available_mb':8192}
    pid=immediate(routes.create_profile(ProfileInput(name='Policy test'),request))['id']
    options={}
    class Context:
        def on(self,*_):pass
    async def launch(*args,**kwargs):
        options.update(kwargs)
        return Context()
    class Manual(Context):
        def __init__(self,*args):options['manual_process']=True
        async def start(self,_):return self
    monkeypatch.setattr('app.services.browsers.ManualBrowser',Manual)
    async def immediate_wait(awaitable,timeout):return await awaitable
    monkeypatch.setattr('app.services.browsers.asyncio.wait_for',immediate_wait)
    svc.browsers.playwright=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    immediate(svc.browsers._launch(pid,open_start_page=False,for_automation=automation))
    if automation:
        assert options['service_workers']=='block' and options['proxy'] is None
    else:
        assert options=={'manual_process':True}
    assert 'user_agent' not in options
    lock=svc.browsers.locks.pop(pid)
    lock.release()


def test_routing_change_requires_stopped_browsers_and_persists(request_and_services):
    from fastapi import HTTPException
    request,svc=request_and_services
    svc.browsers.contexts[1]=object()
    with pytest.raises(HTTPException) as error:
        immediate(routes.update_settings(Settings(proxy_routing='facebook_only'),request))
    assert error.value.status_code==409
    assert svc.state.settings.proxy_routing=='all'
    svc.browsers.contexts.clear()
    immediate(routes.update_settings(Settings(proxy_routing='facebook_only',revision=svc.state.settings_revision),request))
    assert State(svc.db).settings.proxy_routing=='facebook_only'


def test_facebook_routing_also_uses_adapter_for_anonymous_proxies(request_and_services,monkeypatch):
    request,svc=request_and_services
    svc.state.save_settings(Settings(proxy_routing='facebook_only'))
    svc.resources.current={'cpu_percent':0,'memory_percent':20,'memory_available_mb':8192}
    proxy=svc.proxies.import_proxies('127.0.0.1:1234',False)['created']
    assert proxy==1
    with svc.db.session() as session:
        proxy_id=session.scalar(select(Proxy.id))
    pid=immediate(routes.create_profile(ProfileInput(name='Domain routing',proxy_id=proxy_id),request))['id']
    recorded={}
    class Bridge:
        def __init__(self,upstream,path,on_error,*,routing):recorded['routing']=routing
        async def start(self):return {'server':'http://127.0.0.1:4567'}
    class Manual:
        def __init__(self,config,driver,path,options,args,log):recorded['proxy']=options
        async def start(self,_):return self
        def on(self,*_):pass
    async def wait(awaitable,timeout):return await awaitable
    monkeypatch.setattr('app.services.browsers.BrowserProxyBridge',Bridge)
    monkeypatch.setattr('app.services.browsers.ManualBrowser',Manual)
    monkeypatch.setattr('app.services.browsers.asyncio.wait_for',wait)
    immediate(svc.browsers._launch(pid,open_start_page=False))
    assert recorded=={'routing':'facebook_only','proxy':{'server':'http://127.0.0.1:4567'}}
    svc.browsers.locks.pop(pid).release()


def test_failed_proxy_does_not_block_opening_direct_sites_in_facebook_only_mode(request_and_services):
    request,svc=request_and_services
    svc.proxies.import_proxies('127.0.0.1:1234',False)
    with svc.db.session.begin() as session:
        proxy=session.scalar(select(Proxy))
        proxy.status='Failed'
        proxy_id=proxy.id
    pid=immediate(routes.create_profile(ProfileInput(name='Direct sites remain available',proxy_id=proxy_id),request))['id']
    with pytest.raises(ValueError,match='failed its last check'):
        svc.browsers.validate_start(pid)
    svc.state.save_settings(Settings(proxy_routing='facebook_only'))
    svc.browsers.validate_start(pid)
