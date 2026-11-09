import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.core.config import Config
from app.core.schemas import Settings, TaskInput
from app.database.session import Database
from app.models.entities import Profile, Task, TaskRun
from app.services.state import State
from app.services.resources import Resources
from app.services.browsers import BrowserManager
from app.automation.manager import TaskManager, encode_task

@pytest.fixture
def services(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data')
    config.prepare()
    db=Database(config.data_dir/'manager.sqlite3')
    state=State(db)
    state.save_settings(Settings(require_proxy=False))
    resources=Resources()
    resources.current={'cpu_percent':0,'memory_percent':20,'memory_available_mb':8192}
    browsers=BrowserManager(db,state,config,resources)
    tasks=TaskManager(db,state,browsers,config)
    yield SimpleNamespace(db=db,state=state,resources=resources,browsers=browsers,tasks=tasks,config=config)
    db.engine.dispose()

async def test_cancel_before_execution_and_crash_recovery(services):
    s=services
    with s.db.session.begin() as session:
        profile=Profile(name='One',profile_path=str(s.config.profiles_dir/'One'))
        task=Task(name='Test',configuration=encode_task(TaskInput(name='Test',authorized=True,actions=[{'type':'open_url','url':'https://example.com'}])))
        session.add_all([profile,task]);session.flush()
        pid,tid=profile.id,task.id
    run_id=s.tasks.enqueue(tid,[pid])[0]
    # Same loop turn: validates a cancellation before the coroutine ever begins.
    await s.tasks.stop_task(tid)
    with s.db.session() as session:
        assert session.get(TaskRun,run_id).status=='Cancelled'
    assert not s.tasks.jobs and not s.tasks.profile_jobs
    with s.db.session.begin() as session:
        session.add(TaskRun(task_id=tid,profile_id=pid,profile_name='One',status='Running'))
        session.add(TaskRun(task_id=tid,profile_id=pid,profile_name='One',status='Pending'))
        session.get(Task,tid).status='Running'
    s.tasks.recover()
    with s.db.session() as session:
        rows=list(session.scalars(select(TaskRun).order_by(TaskRun.id)))
        assert rows[-2].status=='Failed' and rows[-1].status=='Cancelled'
        assert session.get(Task,tid).status=='Interrupted'

async def test_capacity_and_resource_pause(services,monkeypatch):
    s=services
    settings=Settings(require_proxy=False,max_browsers=1)
    s.state.save_settings(settings)
    class Page:
        url='about:blank'
        async def goto(self, *_args, **_kwargs):return SimpleNamespace(status=200)
        async def bring_to_front(self):pass
    class Context:
        pages=[Page()]
        def on(self,*_):pass
        async def close(self):pass
    class Chromium:
        calls=0
        async def launch_persistent_context(self,*args,**kwargs):
            self.calls+=1
            return Context()
    chromium=Chromium()
    class Manual(Context):
        def __init__(self,*args):pass
        async def start(self,_):
            chromium.calls+=1
            return self
    monkeypatch.setattr('app.services.browsers.ManualBrowser',Manual)
    s.browsers.playwright=SimpleNamespace(chromium=chromium)
    with s.db.session.begin() as session:
        for i in range(3):
            session.add(Profile(name=f'P{i}',profile_path=str(s.config.profiles_dir/f'P{i}')))
    s.resources.current['memory_percent']=95
    for pid in (1,2,3):
        s.browsers.request_start(pid)
    await asyncio.sleep(.05)
    assert chromium.calls==0
    s.resources.current['memory_percent']=20
    await s.browsers.wake()
    await asyncio.sleep(.05)
    assert chromium.calls==1 and len(s.browsers.contexts)==1
    await s.browsers.stop(1)
    await asyncio.sleep(.05)
    assert chromium.calls==2 and len(s.browsers.contexts)==1
    for pid in (1,2,3):
        await s.browsers.stop(pid)
    assert not s.browsers.locks and not s.browsers.contexts and not s.browsers.launches
