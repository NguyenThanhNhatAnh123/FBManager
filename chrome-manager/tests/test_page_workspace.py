"""Mounted workspace isolation; temporary roots, no server or saved data."""
from pathlib import Path
import sys
from types import ModuleType

from filelock import FileLock
import pytest
from starlette.responses import JSONResponse

from app.services.page_workspace import PageWorkspace


@pytest.fixture
def workspace_modules():
    saved={key:value for key,value in sys.modules.items() if key=='page_manager' or key.startswith('page_manager.')}
    old_path=sys.path[:]
    for key in saved:sys.modules.pop(key,None)
    try:yield
    finally:
        for key in list(sys.modules):
            if key=='page_manager' or key.startswith('page_manager.'):sys.modules.pop(key,None)
        sys.modules.update(saved);sys.path[:]=old_path


def roots(tmp_path,source='def create_app(*args,**kwargs): return object()\n'):
    chrome=tmp_path/'chrome-manager';chrome.mkdir()
    meta=tmp_path/'meta-page-manager';package=meta/'backend'/'page_manager';package.mkdir(parents=True)
    (package/'__init__.py').write_text('',encoding='utf-8')
    (package/'app.py').write_text(source,encoding='utf-8')
    return chrome,meta


def test_publication_facade_is_fail_closed_and_delegates_no_database(tmp_path):
    from types import SimpleNamespace
    workspace=PageWorkspace(tmp_path/'chrome-manager',8000)
    assert not workspace.publication_approval({})['can_publish']
    seen=[]
    def verdict(binding):
        seen.append(binding)
        return {'required':True,'can_publish':False,'state':'pending','reason':'publication_approval_required',
                'request_id':None,'binding_hash':None,'approve_by':None,'publish_window_end':None,'private':'never expose'}
    workspace.application=SimpleNamespace(state=SimpleNamespace(publication_approvals=SimpleNamespace(verdict=verdict,ensure=verdict)))
    assert workspace.publication_approval({'fixture':True})['state']=='pending' and seen==[{'fixture':True}]
    assert 'private' not in workspace.publication_approval({})
    workspace.application.state.publication_approvals.verdict=lambda binding:{'required':True,'can_publish':True,'state':'pending'}
    assert workspace.publication_approval({})['state']=='unavailable'


def test_private_chrome_media_resolver_registered_on_mounted_state(tmp_path):
    from types import SimpleNamespace
    workspace=PageWorkspace(tmp_path/'chrome-manager',8000)
    callback=lambda asset_id:'private fixture path'
    workspace.register_publication_media_resolver(callback)
    captured={'token':lambda asset_id:'token fixture path'}
    workspace.application=SimpleNamespace(state=SimpleNamespace(publication_media_resolvers=captured))
    workspace._register_publication_media()
    assert workspace.application.state.publication_media_resolvers is captured and captured['chrome'] is callback


def test_private_chrome_status_resolver_registers_without_opening_peer_database(tmp_path):
    from types import SimpleNamespace
    workspace=PageWorkspace(tmp_path/'chrome-manager',8000)
    callback=lambda job_id:{'status':'scheduled','job_id':job_id}
    workspace.register_publication_status_resolver(callback)
    captured={'token':lambda job_id:{'status':'fixture'}}
    workspace.application=SimpleNamespace(state=SimpleNamespace(publication_status_resolvers=captured))
    workspace._register_publication_media()
    assert workspace.application.state.publication_status_resolvers is captured and captured['chrome'] is callback
    assert captured['chrome'](9)=={'status':'scheduled','job_id':9}
    assert not workspace.root.exists()


def test_approval_facade_rejects_incomplete_approval_identity(tmp_path):
    from types import SimpleNamespace
    workspace=PageWorkspace(tmp_path/'chrome-manager',8000)
    bad={'required':True,'can_publish':True,'state':'approved','reason':None,
         'request_id':None,'binding_hash':None,'approve_by':None,'publish_window_end':None}
    workspace.application=SimpleNamespace(state=SimpleNamespace(publication_approvals=SimpleNamespace(verdict=lambda _:bad)))
    assert not workspace.publication_approval({})['can_publish']


def test_new_policy_facade_accepts_no_cutoff_and_exact_late_approval_window(tmp_path):
    from types import SimpleNamespace
    from uuid import uuid4
    workspace=PageWorkspace(tmp_path/'chrome-manager',8000)
    binding={'approval_policy':'remind_until_decision_v1','scheduled_at':'2030-01-01T10:00:00+00:00'}
    verdict={'required':True,'can_publish':False,'state':'pending','reason':'publication_approval_required',
        'request_id':str(uuid4()),'binding_hash':'a'*64,'approve_by':None,'publish_window_end':None,
        'approval_policy':'remind_until_decision_v1','reminder_interval_seconds':300,
        'due_at':binding['scheduled_at'],'approved_at':None}
    workspace.application=SimpleNamespace(state=SimpleNamespace(publication_approvals=SimpleNamespace(verdict=lambda _:verdict)))
    pending=workspace.publication_approval(binding)
    assert pending['state']=='pending' and pending['approve_by'] is None
    verdict.update(state='approved',can_publish=True,reason=None,approved_at='2030-01-16T12:00:00+00:00',publish_window_end='2030-01-16T12:30:00+00:00')
    assert workspace.publication_approval(binding)['can_publish']
    verdict['publish_window_end']='2030-01-16T12:31:00+00:00'
    assert workspace.publication_approval(binding)['state']=='unavailable'


def test_factory_receives_separate_existing_root_and_mounted_boundary(tmp_path):
    chrome,meta=roots(tmp_path);seen=[]
    def factory(root,**values):seen.append((root,values));return JSONResponse({'workspace':'meta'})
    workspace=PageWorkspace(chrome,8000,factory=factory)
    try:
        assert workspace.start() is True
        assert seen==[(meta,{'port':8000,'base_path':'/pages'})]
        assert workspace.root==meta and workspace.lock.is_locked
        assert not (chrome/'data').exists()
        assert (meta/'data'/'page-manager.lock').is_file()
    finally:workspace.close()
    assert workspace.application is None and workspace.lock is None


def test_bound_page_factory_receives_shared_identity_and_secondary_workspace(tmp_path):
    chrome,meta=roots(tmp_path);seen=[];identity=object()
    def factory(root,**values):
        seen.append((root,values))
        return JSONResponse({'workspace':'meta'})
    workspace=PageWorkspace(chrome,8042,factory=factory,identity_store=identity,workspace_user_id=2,secondary_workspace=True)
    try:
        assert workspace.start()
        assert seen==[(meta,{'port':8042,'base_path':'/pages','identity_store':identity,
                             'workspace_user_id':2,'secondary_workspace':True})]
    finally:workspace.close()


def test_bound_page_rejects_hardlinked_sqlite_sidecar_before_opening_workspace(tmp_path):
    chrome,meta=roots(tmp_path);seen=[]
    foreign=tmp_path/'foreign-workspace-wal';foreign.write_bytes(b'fixture data')
    data=meta/'data';data.mkdir()
    (data/'page-manager.sqlite3-wal').hardlink_to(foreign)
    workspace=PageWorkspace(chrome,8042,factory=lambda *_args,**_kwargs:seen.append(True),
                            identity_store=object(),workspace_user_id=2,secondary_workspace=True)
    assert workspace.start() is False
    assert seen==[] and workspace.lock is None and not (data/'page-manager.lock').exists()
    assert foreign.read_bytes()==b'fixture data'


def test_missing_sibling_app_is_not_created_or_replaced(tmp_path):
    chrome=tmp_path/'chrome-manager';chrome.mkdir();seen=[]
    workspace=PageWorkspace(chrome,8000,factory=lambda *_args,**_kwargs:seen.append(True))
    assert workspace.start() is False and seen==[]
    assert not (tmp_path/'meta-page-manager').exists() and workspace.lock is None


def test_same_meta_lock_excludes_standalone_and_second_mounted_owner(tmp_path):
    chrome,meta=roots(tmp_path);data=meta/'data';data.mkdir();seen=[]
    def factory(*_args,**_kwargs):seen.append(True);return JSONResponse({'ok':True})
    workspace=PageWorkspace(chrome,8000,factory=factory)
    with FileLock(str(data/'page-manager.lock')):
        assert workspace.start() is False and seen==[] and workspace.lock is None
    assert workspace.start() is True
    second=PageWorkspace(chrome,8000,factory=factory)
    try:
        assert second.start() is False and len(seen)==1
        workspace.close()
        assert second.start() is True and len(seen)==2
    finally:workspace.close();second.close()


def test_failed_factory_releases_only_its_own_meta_lock_and_redacts_error(tmp_path):
    chrome,meta=roots(tmp_path)
    def failing(*_args,**_kwargs):raise RuntimeError('fixture-private-token')
    workspace=PageWorkspace(chrome,8000,factory=failing)
    assert workspace.start() is False and workspace.lock is None
    assert 'fixture-private' not in workspace.reason
    with FileLock(str(meta/'data'/'page-manager.lock'),timeout=0):pass
    assert not (chrome/'data').exists()


def test_preloaded_foreign_app_module_is_rejected_before_factory(tmp_path,workspace_modules):
    chrome,_=roots(tmp_path);seen=[]
    foreign=ModuleType('page_manager.app');foreign.__file__=str(tmp_path/'foreign'/'app.py')
    foreign.create_app=lambda *_args,**_kwargs:seen.append(True)
    sys.modules['page_manager.app']=foreign
    workspace=PageWorkspace(chrome,8000)
    try:assert workspace.start() is False and seen==[] and workspace.lock is None
    finally:workspace.close()


def test_preloaded_foreign_relative_module_cannot_enter_local_factory(tmp_path,workspace_modules):
    chrome,_=roots(tmp_path,'from .service import foreign_factory as create_app\n');seen=[]
    foreign=ModuleType('page_manager.service');foreign.__file__=str(tmp_path/'foreign'/'service.py')
    foreign.foreign_factory=lambda *_args,**_kwargs:seen.append(True) or JSONResponse({'workspace':'foreign'})
    sys.modules['page_manager.service']=foreign
    workspace=PageWorkspace(chrome,8000)
    try:assert workspace.start() is False and seen==[] and workspace.lock is None
    finally:workspace.close()


def test_foreign_package_search_path_is_rejected_before_import(tmp_path,workspace_modules):
    chrome,meta=roots(tmp_path);package=ModuleType('page_manager')
    package.__file__=str(meta/'backend'/'page_manager'/'__init__.py')
    package.__path__=[str(tmp_path/'foreign-package')]
    sys.modules['page_manager']=package
    workspace=PageWorkspace(chrome,8000)
    try:assert workspace.start() is False and workspace.lock is None
    finally:workspace.close()


def test_parent_http_boundary_preserves_mounted_child_strict_csp(tmp_path):
    from app.core.config import Config
    from app.main import create_app
    from starlette.requests import Request
    chrome,_=roots(tmp_path)
    app=create_app(Config(root=chrome,data_dir=chrome/'data',port=8000))
    dispatch=next(item.kwargs['dispatch'] for item in app.user_middleware if 'dispatch' in item.kwargs)
    request=Request({'type':'http','http_version':'1.1','method':'GET','scheme':'http','path':'/pages/api/session','root_path':'','query_string':b'',
                     'client':('127.0.0.1',1234),'server':('127.0.0.1',8000),'headers':[(b'host',b'127.0.0.1:8000')]})
    strict="default-src 'self'; script-src 'self'; form-action 'self'; base-uri 'none'"
    async def next_response(_):return JSONResponse({'workspace':'meta'},headers={'Content-Security-Policy':strict})
    coroutine=dispatch(request,next_response)
    try:
        try:coroutine.send(None)
        except StopIteration as result:response=result.value
        else:raise AssertionError('Unexpected asynchronous I/O')
    finally:coroutine.close()
    assert response.status_code==200 and response.headers['Content-Security-Policy']==strict


def test_meta_cookie_cannot_authorize_root_chrome_api(tmp_path):
    from app.core.config import Config
    from app.main import create_app
    from starlette.requests import Request
    chrome,_=roots(tmp_path)
    app=create_app(Config(root=chrome,data_dir=chrome/'data',port=8000))
    dispatch=next(item.kwargs['dispatch'] for item in app.user_middleware if 'dispatch' in item.kwargs)
    request=Request({'type':'http','http_version':'1.1','method':'GET','scheme':'http','path':'/api/profiles','root_path':'','query_string':b'',
                     'client':('127.0.0.1',1234),'server':('127.0.0.1',8000),'headers':[(b'host',b'127.0.0.1:8000'),(b'cookie',b'mpm_pages_session=fixture-meta-session')]})
    async def next_response(_):raise AssertionError('Meta session cannot enter Chrome API')
    coroutine=dispatch(request,next_response)
    try:
        try:coroutine.send(None)
        except StopIteration as result:response=result.value
        else:raise AssertionError('Unexpected asynchronous I/O')
    finally:coroutine.close()
    assert response.status_code==401


def test_mounted_timer_dispatch_and_shutdown_hold_private_lock_until_done(tmp_path):
    from types import SimpleNamespace
    chrome,meta=roots(tmp_path);seen=[]
    workspace=None
    class Scheduler:
        def tick(self):seen.append('tick')
        async def shutdown(self):
            assert workspace.lock.is_locked
            seen.append('shutdown')
    def factory(*args,**kwargs):return SimpleNamespace(state=SimpleNamespace(reels=Scheduler()))
    workspace=PageWorkspace(chrome,8000,factory=factory)
    assert workspace.start()
    workspace.tick()
    coroutine=workspace.shutdown()
    try:
        try:coroutine.send(None)
        except StopIteration:pass
        else:raise AssertionError('Unexpected asynchronous I/O')
    finally:coroutine.close()
    assert seen==['tick','shutdown']
    assert workspace.lock.is_locked
    workspace.close();assert workspace.lock is None


def test_transient_page_tick_failure_does_not_stop_later_subsystems_and_can_recover(tmp_path):
    from types import SimpleNamespace
    import sqlite3
    chrome,_=roots(tmp_path);seen=[];broken=[True]
    class Telegram:
        def tick(self):
            seen.append('telegram')
            if broken[0]:raise sqlite3.OperationalError('private-fixture-error')
    class Reels:
        def tick(self):seen.append('reels')
    workspace=PageWorkspace(chrome,8000,factory=lambda *_args,**_kwargs:SimpleNamespace(state=SimpleNamespace(telegram_approval=Telegram(),reels=Reels())))
    assert workspace.start()
    try:
        workspace.tick()
        assert seen==['telegram','reels']
        assert not workspace.scheduler_health()['ready']
        assert 'private-fixture-error' not in repr(workspace.scheduler_health())
        broken[0]=False;workspace.tick()
        assert seen==['telegram','reels','telegram','reels']
        assert workspace.scheduler_health()['ready']
    finally:workspace.close()


def test_page_cleanup_failure_does_not_skip_other_owned_services_or_release_lock_early(tmp_path):
    from types import SimpleNamespace
    import asyncio
    chrome,_=roots(tmp_path);seen=[]
    class Broken:
        async def shutdown(self):
            assert workspace.lock.is_locked
            seen.append('telegram');raise RuntimeError('private-fixture-error')
    class Reels:
        async def shutdown(self):
            assert workspace.lock.is_locked
            seen.append('reels')
    workspace=PageWorkspace(chrome,8000,factory=lambda *_args,**_kwargs:SimpleNamespace(state=SimpleNamespace(telegram_approval=Broken(),reels=Reels())))
    assert workspace.start()
    try:
        asyncio.run(workspace.shutdown())
        assert seen==['telegram','reels']
        assert workspace.shutdown_failures=={'telegram_approval':'shutdown_failed'}
        assert workspace.lock.is_locked and not workspace.scheduler_health()['ready']
    finally:workspace.close()


def test_folder_source_uses_host_timer_and_stops_before_reel_worker(tmp_path):
    from types import SimpleNamespace
    chrome,_=roots(tmp_path);seen=[]
    class Source:
        def tick(self):seen.append('source_tick')
        async def shutdown(self):
            assert workspace.lock.is_locked
            seen.append('source_stop')
    class Scheduler:
        def tick(self):seen.append('reel_tick')
        async def shutdown(self):
            assert workspace.lock.is_locked
            seen.append('reel_stop')
    workspace=PageWorkspace(chrome,8000,factory=lambda *_args,**_kwargs:SimpleNamespace(state=SimpleNamespace(video_source=Source(),reels=Scheduler())))
    assert workspace.start()
    try:
        workspace.tick()
        coroutine=workspace.shutdown()
        try:
            try:coroutine.send(None)
            except StopIteration:pass
            else:raise AssertionError('Unexpected asynchronous I/O')
        finally:coroutine.close()
        assert seen==['source_tick','reel_tick','source_stop','reel_stop']
    finally:workspace.close()


def test_host_stops_only_owned_producer_diagnostics_before_workspace_lock_release(tmp_path):
    from types import SimpleNamespace
    chrome,_=roots(tmp_path);events=[]
    class Diagnostics:
        async def shutdown(self):
            assert workspace.lock.is_locked
            events.append('producer_diagnostics_stopped')
    workspace=PageWorkspace(chrome,8000,factory=lambda *_args,**_kwargs:SimpleNamespace(state=SimpleNamespace(producer_diagnostics=Diagnostics())))
    assert workspace.start()
    try:
        coroutine=workspace.shutdown()
        try:
            try:coroutine.send(None)
            except StopIteration:pass
            else:raise AssertionError('Unexpected process or network I/O')
        finally:coroutine.close()
        assert events==['producer_diagnostics_stopped'] and workspace.lock.is_locked
    finally:workspace.close()


def test_idea_registry_uses_host_timer_and_finishes_before_data_lock_release(tmp_path):
    from types import SimpleNamespace
    chrome,_=roots(tmp_path);seen=[]
    class Ideas:
        def tick(self):seen.append('idea_tick')
        async def shutdown(self):
            assert workspace.lock.is_locked
            seen.append('idea_stop')
    workspace=PageWorkspace(chrome,8000,factory=lambda *_args,**_kwargs:SimpleNamespace(state=SimpleNamespace(idea_workspace=Ideas())))
    assert workspace.start()
    try:
        workspace.tick()
        coroutine=workspace.shutdown()
        try:
            try:coroutine.send(None)
            except StopIteration:pass
            else:raise AssertionError('Unexpected asynchronous I/O')
        finally:coroutine.close()
        assert seen==['idea_tick','idea_stop']
    finally:workspace.close()


def test_automation_uses_host_timer_and_stops_before_producer_diagnostics(tmp_path):
    from types import SimpleNamespace
    chrome,_=roots(tmp_path);events=[]
    class Automation:
        def tick(self):events.append('automation_tick')
        async def shutdown(self):
            assert workspace.lock.is_locked
            events.append('automation_stopped')
    class Diagnostics:
        async def shutdown(self):events.append('diagnostics_stopped')
    workspace=PageWorkspace(chrome,8000,factory=lambda *_args,**_kwargs:SimpleNamespace(
        state=SimpleNamespace(idea_automation=Automation(),producer_diagnostics=Diagnostics())))
    assert workspace.start()
    try:
        workspace.tick();coroutine=workspace.shutdown()
        try:
            try:coroutine.send(None)
            except StopIteration:pass
            else:raise AssertionError('Unexpected process or network IO')
        finally:coroutine.close()
        assert events==['automation_tick','automation_stopped','diagnostics_stopped']
    finally:workspace.close()


@pytest.mark.parametrize('path,size,expected',[
    ('/pages/api/reels/assets',4_000_000,200),
    ('/pages/api/reels/assets',1_073_741_825,413),
    ('/pages/api/idea-pipeline/images',4_000_000,200),
    ('/pages/api/idea-pipeline/images',26_214_401,413),
    ('/pages/api/idea-pipeline/scan',4_000_000,413),
    ('/pages/api/pages/123/image',4_000_000,200),
    ('/pages/api/pages/123/image',8_388_609,413),
    ('/pages/api/reels',4_000_000,413),
])
def test_parent_preserves_child_scoped_upload_allowances(tmp_path,path,size,expected):
    from app.core.config import Config
    from app.main import create_app
    from starlette.requests import Request
    chrome,_=roots(tmp_path)
    app=create_app(Config(root=chrome,data_dir=chrome/'data',port=8000))
    dispatch=next(item.kwargs['dispatch'] for item in app.user_middleware if 'dispatch' in item.kwargs)
    request=Request({'type':'http','http_version':'1.1','method':'POST','scheme':'http','path':path,'root_path':'','query_string':b'',
        'client':('127.0.0.1',1234),'server':('127.0.0.1',8000),'headers':[(b'host',b'127.0.0.1:8000'),(b'content-length',str(size).encode())]})
    async def next_response(_):return JSONResponse({'child_boundary_still_required':True})
    coro=dispatch(request,next_response)
    try:
        try:coro.send(None)
        except StopIteration as result:response=result.value
        else:raise AssertionError('Unexpected asynchronous I/O')
    finally:coro.close()
    assert response.status_code==expected
