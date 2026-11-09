"""Direct synchronous route/lifecycle checks; no event loop, Chrome, accounts or network."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.api import routes
from app.core.outlook import OutlookCode
from app.core.page_recipient import PageRecipientStart
from app.core.schemas import BmMobileInput,BmProfileInput,BmProxyInput,Ids,ProfileInput,Settings


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O')


def request_for(services):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=services)))


def test_mail_probe_route_returns_only_probe_result_without_code(monkeypatch):
    from app.services import outlook_probe
    recipient=object();calls=[]
    result={'found':True,'message':'Verified','diagnostics':{'sender_verified':True}}
    async def probe(manager,pid):calls.append((manager,pid));return result
    monkeypatch.setattr(outlook_probe,'probe_outlook',probe)
    assert immediate(routes.check_page_recipient_mail(3,request_for(SimpleNamespace(recipient=recipient))))==result
    assert calls==[(recipient,3)] and 'code' not in result


def fixture_services(marker=True):
    browsers=SimpleNamespace(status=lambda pid:'Stopped',mode=lambda pid:None,extension_profiles=set(),facebook_profiles=set(),outlook_profiles=set(),contexts={},launches={},reserved=set())
    if marker:browsers.recipient_profiles={3}
    return SimpleNamespace(browsers=browsers,tasks=SimpleNamespace(profile_jobs={},jobs={}),state=SimpleNamespace(settings=Settings()))


@pytest.mark.parametrize('operation',[
    lambda request:routes.update_profile(3,ProfileInput(name='Fixture'),request),
    lambda request:routes.assign_bm_profile(3,BmProfileInput(),request),
    lambda request:routes.unassign_bm_profile(3,request),
    lambda request:routes.set_bm_mobile(3,BmMobileInput(enabled=True),request),
    lambda request:routes.save_bm_proxy(3,BmProxyInput(content='192.0.2.1:8080'),request),
    lambda request:routes.remove_bm_proxy(3,request),
    lambda request:routes.open_bm_profile(3,request),
    lambda request:routes.delete_profile(3,request,confirm=True),
    lambda request:routes.open_outlook(3,request),
])
def test_owned_recipient_profile_is_guarded_before_other_state_is_touched(operation):
    services=fixture_services()
    # No database or browser-opening method exists: the guard must run first.
    with pytest.raises(HTTPException) as error:immediate(operation(request_for(services)))
    assert error.value.status_code==409


def test_routing_change_is_blocked_by_recipient_marker_alone():
    services=fixture_services()
    with pytest.raises(HTTPException) as error:
        immediate(routes.update_settings(Settings(proxy_routing='facebook_only'),request_for(services)))
    assert error.value.status_code==409


def test_optional_marker_keeps_existing_service_mocks_compatible():
    services=fixture_services(marker=False)
    assert routes.assert_recipient_available(services,3) is None
    saved=[]
    services.state.save_settings=lambda value,**kwargs:saved.append(value)
    async def wake():pass
    services.browsers.wake=wake
    body=Settings(proxy_routing='facebook_only',revision=1)
    services.state.public_settings=lambda:body.model_dump()|{'revision':2}
    assert immediate(routes.update_settings(body,request_for(services)))==body.model_dump()|{'revision':2}
    assert saved==[body]


@pytest.mark.parametrize('marker,job,row,expected',[(True,False,False,True),(False,True,False,True),(False,False,True,True),(False,False,False,False)])
def test_profile_stop_only_calls_recipient_when_its_own_state_exists(marker,job,row,expected):
    stopped=[]
    async def stop(pid):stopped.append(pid)
    recipient=SimpleNamespace(jobs={3:object()} if job else {},row=lambda pid:object() if row else None,stop=stop)
    services=SimpleNamespace(browsers=SimpleNamespace(recipient_profiles={3} if marker else set()),recipient=recipient)
    immediate(routes.stop_recipient_if_present(services,3))
    assert stopped==([3] if expected else [])
    # Profiles with no recipient manager continue to use their ordinary stop flow.
    immediate(routes.stop_recipient_if_present(SimpleNamespace(browsers=SimpleNamespace()),4))


def test_bulk_stop_cancels_the_recipient_before_other_services_and_skips_unrelated_profiles():
    events=[]
    class Session:
        def get(self,model,pid):return SimpleNamespace(id=pid)
    @contextmanager
    def session():yield Session()
    def stopper(name):
        async def stop(pid):events.append((name,pid))
        return stop
    services=SimpleNamespace(db=SimpleNamespace(session=session),browsers=SimpleNamespace(recipient_profiles={3},stop=stopper('browser')),recipient=SimpleNamespace(jobs={},row=lambda pid:object() if pid==3 else None,stop=stopper('recipient')),facebook=SimpleNamespace(stop=stopper('facebook')),outlook=SimpleNamespace(stop=stopper('outlook')),extensions=SimpleNamespace(stop=stopper('extensions')),tasks=SimpleNamespace(stop_profile=stopper('tasks')))
    result=immediate(routes.stop_selected(Ids(profile_ids=[3,4]),request_for(services)))
    assert result=={'accepted':[3,4],'errors':[]}
    assert events==[('recipient',3),('facebook',3),('outlook',3),('extensions',3),('tasks',3),('browser',3),('facebook',4),('outlook',4),('extensions',4),('tasks',4),('browser',4)]


def test_recipient_endpoints_delegate_validated_inputs_without_exposing_code(tmp_path):
    received=[]
    class Session:
        def get(self,model,pid):return SimpleNamespace(id=pid)
    @contextmanager
    def session():yield Session()
    class Recipient:
        asset_root=tmp_path/'page-assets'
        def list(self):return [{'profile_id':3,'status':'needs_confirmation'}]
        def start(self,body):received.append(('start',body));return {'accepted':[body.profile_id]}
        def resume(self,pid):received.append(('resume',pid));return {'accepted':[pid]}
        def submit_code(self,pid,code):received.append(('code',pid,code));return {'ok':True}
        async def stop(self,pid):received.append(('stop',pid))
    request=request_for(SimpleNamespace(recipient=Recipient(),db=SimpleNamespace(session=session)))
    assert immediate(routes.page_recipient_config(request))=={'asset_root':str(tmp_path/'page-assets')}
    assert immediate(routes.page_recipient_jobs(request))==[{'profile_id':3,'status':'needs_confirmation'}]
    body=PageRecipientStart(profile_id=3,outlook_email='inbox@example.test')
    assert immediate(routes.start_page_recipient(body,request))=={'accepted':[3]}
    assert immediate(routes.resume_page_recipient(3,request))=={'accepted':[3]}
    code=OutlookCode(code='123456')
    assert '123456' not in repr(code)
    assert immediate(routes.page_recipient_code(3,code,request))=={'ok':True}
    assert immediate(routes.stop_page_recipient(3,request))=={'ok':True}
    assert received==[('start',body),('resume',3),('code',3,'123456'),('stop',3)]
    endpoints={route.path:route for route in routes.router.routes if route.path.startswith('/api/page-recipient/')}
    assert len(endpoints)==7
    assert endpoints['/api/page-recipient/{profile_id}/check-mail'].methods=={'POST'}
    assert endpoints['/api/page-recipient/start'].status_code==202
    assert endpoints['/api/page-recipient/{profile_id}/code'].status_code==202


def test_lifecycle_registers_recipient_proxy_guard_and_shuts_it_down_first(tmp_path,monkeypatch):
    from app import main
    events=[]
    class Manager:
        def __init__(self,*args,**kwargs):self.posting_policy=kwargs.get('posting_policy')
        def tick(self):pass
        async def shutdown(self):events.append(self.name)
    def manager(name):
        return type(name,(Manager,),{'name':name})
    class Browsers(Manager):
        name='browser'
        def __init__(self,*args):self.extension_profiles=set();self.facebook_profiles=set();self.outlook_profiles=set();self.recipient_profiles={3}
        def status(self,pid):return 'Stopped'
        async def initialize(self):pass
        async def wake(self):pass
    class Proxies(Manager):
        name='proxy'
        def schedule_pending_checks(self):pass
    class Tasks(Manager):
        name='task'
        def __init__(self,*args):self.profile_jobs={}
        def recover(self):pass
    class State:
        def __init__(self,*args):pass
        def log(self,*args):pass
        def emit(self):pass
    class Monitor:
        def cancel(self):pass
        def __await__(self):
            async def finished():pass
            return finished().__await__()
    def create_task(coroutine):coroutine.close();return Monitor()
    monkeypatch.setattr(main,'Database',lambda path:SimpleNamespace(engine=SimpleNamespace(dispose=lambda:None)))
    monkeypatch.setattr(main,'adopt_existing_bm_proxies',lambda db:None)
    monkeypatch.setattr(main,'State',State);monkeypatch.setattr(main,'Resources',lambda:SimpleNamespace(sample=lambda:None))
    monkeypatch.setattr(main,'BrowserManager',Browsers);monkeypatch.setattr(main,'ProxyService',Proxies);monkeypatch.setattr(main,'TaskManager',Tasks)
    for attribute,name in [('ExtensionManager','extension'),('FacebookManager','facebook'),('OutlookManager','outlook'),('PageRecipientManager','recipient'),('ReelScheduler','reels'),('FacebookRefreshScheduler','facebook_refresh')]:monkeypatch.setattr(main,attribute,manager(name))
    monkeypatch.setattr(main.asyncio,'create_task',create_task)
    data=tmp_path/'data'
    config=SimpleNamespace(root=tmp_path,data_dir=data,port=8000,prepare=lambda:data.mkdir())
    app=main.create_app(config)
    lifespan=app.router.lifespan_context(app)
    immediate(lifespan.__aenter__())
    try:
        assert app.state.services.recipient.name=='recipient'
        assert app.state.services.reels.name=='reels'
        assert isinstance(app.state.services.recipient.posting_policy,main.PageWorkspace)
        assert app.state.services.recipient.posting_policy is app.state.services.reels.posting_policy
        assert app.state.services.facebook_refresh.name=='facebook_refresh'
        assert app.state.services.proxies.is_busy(3)
        assert not app.state.services.proxies.is_busy(4)
    finally:immediate(lifespan.__aexit__(None,None,None))
    assert events==['facebook_refresh','reels','recipient','outlook','facebook','extension','task','proxy','browser']
