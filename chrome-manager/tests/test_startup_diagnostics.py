import asyncio
import errno
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace

from filelock import Timeout
import pytest

from app.core.startup_diagnostics import StartupASGI,StartupDiagnostics,StartupObserver


def read(root,name='startup-error.json'):
    return json.loads((root/'logs'/name).read_text(encoding='utf-8'))


@pytest.mark.parametrize('error,reason',[(ValueError('PRIVATE_PASSWORD_COOKIE'),'startup_exception'),
    (Timeout('PRIVATE_PATH'),'already_running'),(OSError(errno.EADDRINUSE,'PRIVATE_TOKEN'),'port_in_use'),
    (PermissionError('PRIVATE_PASSWORD'),'permission_denied'),(ModuleNotFoundError('PRIVATE_COOKIE'),'missing_dependency')])
def test_failure_records_safe_cause_and_never_exception_text(tmp_path,error,reason):
    diagnostics=StartupDiagnostics(tmp_path);diagnostics.failure('asgi_startup',error,port=8000)
    value=read(tmp_path)
    assert value['reason']==reason and value['exception_type']==type(error).__name__
    assert value['stage']=='asgi_startup' and value['port']==8000 and value['exit_code']==3
    assert value['checked_at'] and value['attempt_id']
    assert 'PRIVATE' not in json.dumps(value)


def test_lifespan_failure_records_actual_exception_without_recording_traceback(tmp_path):
    async def application(scope,receive,send):
        await send({'type':'lifespan.startup.failed','message':'PRIVATE_TRACE_AND_TOKEN'})
        raise Timeout('PRIVATE_PATH')
    sent=[]
    async def send(message):sent.append(message)
    async def receive():return {'type':'lifespan.startup'}
    with pytest.raises(Timeout):asyncio.run(StartupASGI(application,StartupDiagnostics(tmp_path))({'type':'lifespan'},receive,send))
    value=read(tmp_path)
    assert value['reason']=='already_running' and value['exception_type']=='Timeout'
    assert len(sent)==1 and 'PRIVATE' not in json.dumps(value)


def record(message):return logging.LogRecord('uvicorn.error',logging.INFO,'fixture',1,message,(),None)


def test_browser_ready_fires_only_after_socket_bound_and_once(tmp_path):
    calls=[];observer=StartupObserver(StartupDiagnostics(tmp_path),lambda:calls.append('open'))
    observer.emit(record('Application startup complete.'));assert not calls
    observer.emit(record('Uvicorn running on %s://%s:%d (Press CTRL+C to quit)'))
    observer.emit(record('Uvicorn running on %s://%s:%d (Press CTRL+C to quit)'))
    assert calls==['open'] and read(tmp_path,'startup-status.json')['state']=='running'
    observer.emit(record(OSError(errno.EADDRINUSE,'PRIVATE')))
    assert not (tmp_path/'logs'/'startup-error.json').exists()


def test_socket_bind_failure_is_distinguished_from_lifespan_failure(tmp_path):
    observer=StartupObserver(StartupDiagnostics(tmp_path),lambda:pytest.fail('Must not open browser'))
    observer.emit(record(OSError(errno.EADDRINUSE,'PRIVATE')))
    assert read(tmp_path)['stage']=='socket_bind' and read(tmp_path)['reason']=='port_in_use'


def entry(tmp_path,monkeypatch,state):
    spec=importlib.util.spec_from_file_location('via_run_entry',Path(__file__).resolve().parents[1]/'backend'/'run.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    diagnostics=StartupDiagnostics(tmp_path)
    monkeypatch.setattr(module,'StartupDiagnostics',lambda _root:diagnostics)
    import app.core.config
    monkeypatch.setattr(app.core.config,'Config',lambda:SimpleNamespace(root=tmp_path,port=8000))
    startup=ModuleType('app.core.startup');startup.inspect_existing_instance=lambda _config:state
    monkeypatch.setitem(sys.modules,'app.core.startup',startup)
    return module,diagnostics


@pytest.mark.parametrize('open_browser',['true','false'])
def test_run_entry_reuses_verified_host_without_initializing_application(tmp_path,monkeypatch,open_browser):
    module,_=entry(tmp_path,monkeypatch,{'state':'running','port':8000,'pid':123,'reason':'existing_instance'})
    calls=[];monkeypatch.setenv('CM_OPEN_DASHBOARD',open_browser);monkeypatch.setenv('CM_OPEN_DASHBOARD_PATH','/pages/')
    monkeypatch.setattr(module.webbrowser,'open',lambda value:calls.append(value))
    import uvicorn
    monkeypatch.setattr(uvicorn,'Server',lambda *_args,**_kwargs:pytest.fail('Second server must not start'))
    assert module.main()==0
    assert calls==(['http://127.0.0.1:8000/pages/'] if open_browser=='true' else [])
    assert read(tmp_path,'startup-status.json')['reason']=='existing_instance'


def test_run_entry_refuses_unrelated_listener_and_preserves_failure_reason(tmp_path,monkeypatch):
    module,_=entry(tmp_path,monkeypatch,{'state':'unavailable','port':8000,'reason':'port_in_use'})
    monkeypatch.setenv('CM_OPEN_DASHBOARD','true')
    monkeypatch.setattr(module.webbrowser,'open',lambda *_args:pytest.fail('Must not open unrelated server'))
    assert module.main()==3
    assert read(tmp_path)['reason']=='port_in_use' and read(tmp_path)['stage']=='preflight'


@pytest.mark.parametrize('fail',[False,True])
def test_real_uvicorn_lifespan_is_observed_on_an_isolated_ephemeral_listener(tmp_path,monkeypatch,fail):
    module,_=entry(tmp_path,monkeypatch,{'state':'stopped','port':0,'reason':'no_instance'})
    import app.core.config
    monkeypatch.setattr(app.core.config,'Config',lambda:SimpleNamespace(root=tmp_path,port=0))
    monkeypatch.setenv('CM_OPEN_DASHBOARD','false')
    async def application(scope,receive,send):
        assert scope['type']=='lifespan'
        await receive()
        if fail:
            await send({'type':'lifespan.startup.failed','message':'PRIVATE_FAILURE'})
            raise ValueError('PRIVATE_FAILURE')
        await send({'type':'lifespan.startup.complete'})
        await receive();await send({'type':'lifespan.shutdown.complete'})
    fake=ModuleType('app.main');fake.app=application;monkeypatch.setitem(sys.modules,'app.main',fake)
    import uvicorn
    original=uvicorn.Server
    class OnePassServer(original):
        async def startup(self,sockets=None):
            assert self.config.timeout_graceful_shutdown==30
            await super().startup(sockets=sockets)
            self.should_exit=True
    monkeypatch.setattr(uvicorn,'Server',OnePassServer)
    assert module.main()==(3 if fail else 0)
    if fail:
        value=read(tmp_path)
        assert value['stage']=='asgi_startup' and value['exception_type']=='ValueError'
        assert 'PRIVATE' not in json.dumps(value)
    else:
        assert read(tmp_path,'startup-status.json')['state']=='stopped'
        assert not (tmp_path/'logs'/'startup-error.json').exists()


def test_run_entry_bounds_wait_for_request_tasks_and_still_runs_lifespan_cleanup(tmp_path,monkeypatch):
    module,_=entry(tmp_path,monkeypatch,{'state':'stopped','port':0,'reason':'no_instance'})
    import app.core.config
    monkeypatch.setattr(app.core.config,'Config',lambda:SimpleNamespace(root=tmp_path,port=0))
    monkeypatch.setenv('CM_OPEN_DASHBOARD','false')
    events=[]
    async def application(scope,receive,send):
        assert scope['type']=='lifespan'
        await receive()
        await send({'type':'lifespan.startup.complete'})
        message=await receive()
        assert message['type']=='lifespan.shutdown'
        events.append('lifespan_cleanup')
        await send({'type':'lifespan.shutdown.complete'})
    async def stalled_request():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append('request_cancelled')
            raise
    fake=ModuleType('app.main');fake.app=application;monkeypatch.setitem(sys.modules,'app.main',fake)
    import uvicorn
    original=uvicorn.Server
    class StalledRequestServer(original):
        async def startup(self,sockets=None):
            assert self.config.timeout_graceful_shutdown==30
            # Exercise the real timeout branch promptly; the entry-point value
            # above remains the configured production allowance.
            self.config.timeout_graceful_shutdown=.01
            await super().startup(sockets=sockets)
            task=asyncio.create_task(stalled_request())
            self.server_state.tasks.add(task)
            task.add_done_callback(self.server_state.tasks.discard)
            await asyncio.sleep(0)
            self.should_exit=True
    monkeypatch.setattr(uvicorn,'Server',StalledRequestServer)
    assert module.main()==0
    assert events==['request_cancelled','lifespan_cleanup']
    assert read(tmp_path,'startup-status.json')['state']=='stopped'
