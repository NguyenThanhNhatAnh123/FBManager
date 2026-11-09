"""Daily name refresh uses only fixture databases and fake jobs/browsers."""
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func,event as sql_event
from app.core.config import Config
from app.core.facebook import FacebookCheck
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import Profile,Proxy,BmProfile,BmProxy,FacebookAccount,FacebookPage,FacebookRefreshSchedule,ReelJob,ReelAsset,Event
from app.services.state import State
from app.services.facebook import FacebookManager,FacebookLoginRequired
from app.services.facebook_refresh import FacebookRefreshScheduler,INTERVAL
from app.services import facebook_refresh as schedule_module
from app.services import facebook as facebook_module


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in fixture')


class Job:
    def __init__(self):self.finished=False;self.was_cancelled=False;self.value=None
    def done(self):return self.finished
    def cancelled(self):return self.was_cancelled
    def result(self):return self.value
    def finish(self,status,at):self.finished=True;self.value={'status':status,'finished_at':at.isoformat()}


class Browsers:
    def __init__(self):
        self.contexts={};self.launches={};self.reserved=set();self.facebook_profiles=set();self.extension_profiles=set();self.outlook_profiles=set();self.recipient_profiles=set();self.reel_profiles=set()
        self.reason=None;self.reject=False;self.resources=SimpleNamespace(blocked_reason=lambda _:self.reason)
    def status(self,pid):return 'Running' if pid in self.contexts else 'Starting' if pid in self.reserved else 'Queued' if pid in self.launches else 'Stopped'
    def validate_automation_start(self,*_,**__):
        if self.reject:raise ValueError('fixture-private-precondition')


class Facebook:
    def __init__(self,db,browsers,clock):self.jobs={};self.db=db;self.browsers=browsers;self.clock=clock;self.accepted=[];self.reject=False;self.stopped=[]
    def enqueue(self,body):
        pid=body.profile_ids[0]
        assert body.names_only is True and len(body.profile_ids)==1
        with self.db.session() as session:
            row=session.get(FacebookRefreshSchedule,pid)
            assert row.result=='running' and row.last_attempt_at==self.clock[0].isoformat()
            assert row.next_due_at==(self.clock[0]+INTERVAL).isoformat(),'Durable reservation precedes enqueue'
        if self.reject:raise ValueError('fixture-private-enqueue-error')
        job=Job();self.jobs[pid]=job;self.browsers.facebook_profiles.add(pid);self.accepted.append((pid,body,job))
        return {'accepted':[pid],'errors':[]}
    def finish(self,pid,status='complete'):
        job=self.jobs.pop(pid);job.finish(status,self.clock[0]);self.browsers.facebook_profiles.discard(pid);return job
    async def stop(self,pid):self.stopped.append(pid);self.finish(pid,'cancelled')


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    clock=[datetime(2026,11,3,12,tzinfo=timezone.utc)];monkeypatch.setattr(schedule_module,'utc_now',lambda:clock[0])
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare();db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    state.save_settings(Settings(require_proxy=False,max_browsers=2));browsers=Browsers();tasks=SimpleNamespace(profile_jobs={})
    with db.session.begin() as session:
        for i in range(1,6):session.add(Proxy(id=i,host='192.0.2.'+str(i),port=10000+i,protocol='http',identity='fixture'+str(i),status='Working'))
        session.flush()
        for pid,proxy in [(1,1),(2,2),(3,None),(4,3),(5,4),(6,5)]:session.add(Profile(id=pid,name='Fixture '+str(pid),profile_path=str(tmp_path/'profiles'/str(pid)),proxy_id=proxy,created_at=(clock[0]-timedelta(days=2) if pid!=6 else clock[0]-timedelta(hours=1)).isoformat()))
        session.flush();session.add(BmProfile(profile_id=4));session.add(BmProxy(proxy_id=4,profile_id=None))
        session.add(FacebookAccount(profile_id=6,checked_at=(clock[0]-timedelta(hours=2)).isoformat()))
    facebook=Facebook(db,browsers,clock);scheduler=FacebookRefreshScheduler(db,state,browsers,tasks,facebook)
    yield SimpleNamespace(db=db,state=state,browsers=browsers,tasks=tasks,facebook=facebook,scheduler=scheduler,clock=clock,config=config)
    db.engine.dispose()


def row(f,pid=1):
    with f.db.session() as session:return session.get(FacebookRefreshSchedule,pid)


def test_initial_due_is_checked_account_or_created_plus_24h_and_only_ordinary_proxy_profiles(fixture):
    f=fixture;status=f.scheduler.status()
    assert Settings().facebook_auto_refresh is True and status['interval_hours']==24
    assert status['eligible_count']==3 and status['due_count']==2
    assert {item['profile_id'] for item in status['profiles']}=={1,2,6}
    assert row(f,6).next_due_at==(f.clock[0]+timedelta(hours=22)).isoformat()
    f.scheduler.tick();assert [item[0] for item in f.facebook.accepted]==[1]
    f.scheduler.tick();assert len(f.facebook.accepted)==1,'No batch enqueue while one owned job is active'


@pytest.mark.parametrize('blocker',['open_profile','task','extension','outlook','recipient','reel','manual_facebook','resources','slots','failed_proxy'])
def test_busy_or_unavailable_profiles_wait_without_consuming_cadence(fixture,blocker):
    f=fixture
    # Block the second due profile too, so only profile1 readiness is observed.
    f.browsers.outlook_profiles.add(2)
    if blocker=='open_profile':f.browsers.contexts[1]=object()
    elif blocker=='task':f.tasks.profile_jobs[1]=object()
    elif blocker in ('extension','outlook','recipient','reel'):getattr(f.browsers,blocker+'_profiles').add(1)
    elif blocker=='manual_facebook':f.facebook.jobs[99]=Job()
    elif blocker=='resources':f.browsers.reason='fixture resource wait'
    elif blocker=='slots':f.browsers.contexts={98:object(),99:object()}
    else:
        with f.db.session.begin() as session:session.get(Proxy,1).status='Failed'
    before=row(f).next_due_at;f.scheduler.tick()
    assert f.facebook.accepted==[] and row(f).next_due_at==before and row(f).last_attempt_at is None


def test_imminent_reel_has_priority_over_automatic_refresh(fixture):
    f=fixture
    with f.db.session.begin() as session:
        session.add(ReelAsset(id='a'*32,name='Fixture',path='fixture-only.mp4',size=1,sha256='0'*64,mime='video/mp4'));session.flush()
        session.add(ReelJob(profile_id=2,profile_name='Fixture 2',page_url='https://www.facebook.com/Page',asset_id='a'*32,caption='',scheduled_at=(f.clock[0]+timedelta(minutes=9)).isoformat(),timezone='UTC'))
    f.scheduler.tick();assert not f.facebook.accepted and row(f).last_attempt_at is None


def test_reel_reserved_before_its_browser_launch_keeps_the_last_slot(fixture):
    f=fixture;f.state.save_settings(f.state.settings.model_copy(update={'max_browsers':1}))
    # reels.tick reserves the profile before its async runner opens Chrome.
    f.browsers.reel_profiles.add(98)
    f.scheduler.tick()
    assert f.facebook.accepted==[] and row(f).last_attempt_at is None
    f.browsers.reel_profiles.clear();f.scheduler.tick();assert f.facebook.accepted[0][0]==1


@pytest.mark.parametrize('status',['complete','partial','error','login_required','cancelled'])
def test_accepted_completion_moves_next_due_24h_from_finish_and_survives_restart(fixture,status):
    f=fixture;f.scheduler.tick();f.clock[0]+=timedelta(minutes=4);f.facebook.finish(1,status)
    f.state.save_settings(f.state.settings.model_copy(update={'facebook_auto_refresh':False}));f.scheduler.tick()
    assert row(f).result==status and row(f).last_finished_at==f.clock[0].isoformat()
    assert row(f).next_due_at==(f.clock[0]+INTERVAL).isoformat()
    restarted=FacebookRefreshScheduler(f.db,f.state,f.browsers,f.tasks,f.facebook);restarted.tick()
    assert len(f.facebook.accepted)==1


def test_rejected_enqueue_has_60s_backoff_without_spending_daily_due_or_logging_raw_error(fixture):
    f=fixture;f.facebook.reject=True;before=row(f).next_due_at;f.scheduler.tick()
    assert row(f).next_due_at==before and row(f).last_attempt_at is None
    assert row(f).retry_after_at==(f.clock[0]+timedelta(seconds=60)).isoformat()
    # Also block the other profile to observe this profile's persisted retry.
    f.browsers.outlook_profiles.add(2);f.facebook.reject=False;f.scheduler.tick();assert not f.facebook.accepted
    f.clock[0]+=timedelta(seconds=60);f.scheduler.tick();assert f.facebook.accepted[0][0]==1
    with f.db.session() as session:assert 'fixture-private' not in ' '.join(event.message for event in session.scalars(select(Event)))


def test_restart_of_reserved_attempt_does_not_duplicate_and_disable_never_launches(fixture):
    f=fixture;f.scheduler.tick();f.facebook.jobs.clear();f.browsers.facebook_profiles.clear()
    restarted=FacebookRefreshScheduler(f.db,f.state,f.browsers,f.tasks,f.facebook)
    assert row(f).result=='interrupted' and row(f).next_due_at>f.clock[0].isoformat()
    f.state.save_settings(f.state.settings.model_copy(update={'facebook_auto_refresh':False}));restarted.tick()
    assert len(f.facebook.accepted)==1 and restarted.status()['enabled'] is False


def test_shutdown_only_stops_exact_auto_owned_job_not_new_manual_job_on_same_profile(fixture):
    f=fixture;f.scheduler.tick();f.facebook.finish(1);manual=Job();f.facebook.jobs[1]=manual
    immediate(f.scheduler.shutdown())
    assert not f.facebook.stopped and f.facebook.jobs[1] is manual and row(f).result=='complete'


def test_shutdown_stops_owned_job_and_preserves_daily_backoff(fixture):
    f=fixture;f.scheduler.tick();immediate(f.scheduler.shutdown())
    assert f.facebook.stopped==[1] and row(f).result=='cancelled'
    assert row(f).next_due_at==(f.clock[0]+INTERVAL).isoformat()


def test_status_is_pure_even_for_new_eligible_profile_and_tick_errors_cannot_escape(fixture,monkeypatch):
    f=fixture
    with f.db.session.begin() as session:
        session.add(Proxy(id=9,host='192.0.2.9',port=10009,protocol='http',identity='newfixture',status='Working'));session.flush()
        session.add(Profile(id=9,name='New fixture',profile_path='fixture9',proxy_id=9,created_at=f.clock[0].isoformat()))
    with f.db.session() as session:before=session.scalar(select(func.count()).select_from(FacebookRefreshSchedule))
    assert f.scheduler.status()['eligible_count']==4
    with f.db.session() as session:assert session.scalar(select(func.count()).select_from(FacebookRefreshSchedule))==before
    def failure():raise RuntimeError('fixture-private-storage-error')
    monkeypatch.setattr(f.scheduler,'_tick',failure);f.scheduler.tick();f.scheduler.tick()
    with f.db.session() as session:
        messages=[event.message for event in session.scalars(select(Event))]
        assert len(messages)==1 and 'fixture-private' not in messages[0]


def name_window():
    start=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    return FacebookCheck(profile_ids=[1],names_only=True,timezone='UTC',check_date=start.date().isoformat(),day_start=start.isoformat(),day_end=(start+timedelta(days=1)).isoformat())


def seed_names(f):
    with f.db.session.begin() as session:
        session.add(FacebookAccount(profile_id=1,name='Old personal name',url='https://www.facebook.com/profile.php?id=100',status='complete',checked_at='2026-10-29T12:00:00+00:00',check_date='2026-10-29',timezone='UTC',day_start='2026-10-29T00:00:00+00:00',day_end='2026-10-30T00:00:00+00:00'))
        for key,status in [('555','posted'),('777','not_seen')]:session.add(FacebookPage(profile_id=1,page_key=key,name='Old Page '+key,url='https://www.facebook.com/profile.php?id='+key,status=status,checked_at='2026-10-29T12:00:00+00:00',check_date='2026-10-29',timezone='UTC',day_start='2026-10-29T00:00:00+00:00',day_end='2026-10-30T00:00:00+00:00',posts='[{"url":"https://www.facebook.com/reel/9","date":"2026-10-29"}]',error_message='Retained post evidence'))


def snapshots(f):
    with f.db.session() as session:
        account=session.get(FacebookAccount,1)
        return {column.name:getattr(account,column.name) for column in account.__table__.columns},[
            {column.name:getattr(page,column.name) for column in page.__table__.columns} for page in session.scalars(select(FacebookPage).where(FacebookPage.profile_id==1).order_by(FacebookPage.id))]


class NoTimeout:
    async def __aenter__(self):pass
    async def __aexit__(self,*_):pass


def names_manager(f,monkeypatch,mode='complete'):
    manager=FacebookManager(f.db,f.state,f.browsers,f.tasks);opened=[];stopped=[];owner=object()
    manager.jobs[1]=owner;manager.names_only_jobs[1]=owner;f.browsers.facebook_profiles.add(1)
    f.browsers.validate_automation_start=lambda *_,**__:None
    class Page:
        def on(self,*_):pass
    class Context:
        async def new_page(self):return Page()
    async def ensure(*args,**kwargs):opened.append(args);return Context()
    async def stop(pid):stopped.append(pid)
    async def visit(*_):pass
    async def personal(*_):return {'kind':'personal','url':'https://www.facebook.com/profile.php?id=100'}
    async def account(*_):
        if mode=='login':raise FacebookLoginRequired('Needs login')
        return {'name':'New personal name','url':'https://www.facebook.com/profile.php?id='+('999' if mode=='changed_identity' else '100')}
    async def discover(*_):return ([],None) if mode=='empty' else ([{'page_key':'555','name':'Renamed Page','url':'https://www.facebook.com/profile.php?id=555'},{'page_key':'888','name':'New Page','url':'https://www.facebook.com/profile.php?id=888'}],'Partial list' if mode=='partial' else None)
    async def no_posts(*_):raise AssertionError('Names-only must never read post timelines')
    async def diagnostics(*_):pass
    monkeypatch.setattr(f.browsers,'ensure_open',ensure,raising=False);monkeypatch.setattr(f.browsers,'stop',stop,raising=False)
    monkeypatch.setattr(manager,'visit',visit);monkeypatch.setattr(manager,'read_account',account);monkeypatch.setattr(manager,'discover',discover);monkeypatch.setattr(manager,'check_page',no_posts);monkeypatch.setattr(manager,'capture_identity_diagnostics',diagnostics)
    monkeypatch.setattr(facebook_module,'ensure_personal_identity',personal);monkeypatch.setattr(facebook_module.asyncio,'timeout',lambda _:NoTimeout())
    return manager,opened,stopped,owner


@pytest.mark.parametrize('mode',['complete','partial','empty','login','changed_identity'])
def test_names_only_preserves_manual_account_and_all_page_post_snapshots(fixture,monkeypatch,mode):
    f=fixture;seed_names(f);before_account,before_pages=snapshots(f);manager,opened,stopped,_=names_manager(f,monkeypatch,mode)
    result=immediate(manager._run(1,name_window()));after_account,after_pages=snapshots(f)
    assert opened and stopped==[1] and not manager.jobs and not f.browsers.facebook_profiles
    for key,value in before_account.items():
        if key not in ('name','url'):assert after_account[key]==value
    for before in before_pages:
        after=next(page for page in after_pages if page['id']==before['id'])
        for key,value in before.items():
            if key not in ('name','url'):assert after[key]==value
    assert next(page for page in after_pages if page['page_key']=='777')['name']=='Old Page 777'
    if mode in ('complete','partial'):
        assert after_account['name']=='New personal name' and len(after_pages)==3
        assert next(page for page in after_pages if page['page_key']=='888')['status']=='unchecked'
    if mode in ('login','changed_identity'):assert after_account==before_account and after_pages==before_pages
    assert result['status']==('partial' if mode=='partial' else 'login_required' if mode=='login' else 'error' if mode=='changed_identity' else 'complete')


@pytest.mark.parametrize('change',['bm','private_proxy','busy_helper','lost_owner'])
def test_names_only_rechecks_eligibility_after_semaphore_wait_without_touching_cache(fixture,monkeypatch,change):
    f=fixture;seed_names(f);before=snapshots(f);manager,opened,stopped,owner=names_manager(f,monkeypatch)
    class Pause:
        def __await__(self):yield 'semaphore'
    class Semaphore:
        async def acquire(self):await Pause()
        def release(self):pass
    manager.semaphore=Semaphore();operation=manager._run(1,name_window());foreign=object()
    try:
        assert operation.send(None)=='semaphore'
        if change=='bm':
            with f.db.session.begin() as session:session.add(BmProfile(profile_id=1))
        elif change=='private_proxy':
            with f.db.session.begin() as session:session.add(BmProxy(proxy_id=1,profile_id=None))
        elif change=='busy_helper':f.browsers.outlook_profiles.add(1)
        else:manager.jobs[1]=foreign;f.browsers.launches[1]=foreign
        with pytest.raises(StopIteration) as result:operation.send(None)
        assert result.value.value['status']=='deferred' and opened==[] and stopped==[] and snapshots(f)==before
        if change=='lost_owner':assert manager.jobs[1] is foreign and 1 in f.browsers.facebook_profiles
    finally:operation.close()


def test_names_only_early_stop_does_not_mark_old_post_scan_cancelled(fixture,monkeypatch):
    f=fixture;seed_names(f);before=snapshots(f);manager=FacebookManager(f.db,f.state,f.browsers,f.tasks)
    class NeverStarted:
        def __init__(self,coroutine):self.coroutine=coroutine
        def add_done_callback(self,_):pass
        def cancel(self):self.coroutine.close()
    monkeypatch.setattr(facebook_module.asyncio,'create_task',lambda coroutine:NeverStarted(coroutine))
    async def gathered(*_,**__):return []
    monkeypatch.setattr(facebook_module.asyncio,'gather',gathered)
    assert manager.enqueue(name_window())['accepted']==[1]
    assert snapshots(f)==before
    immediate(manager.stop(1));assert snapshots(f)==before and not manager.jobs and 1 not in f.browsers.facebook_profiles


def test_status_summary_matches_details_in_one_read_with_missing_schedule(fixture):
    f=fixture
    with f.db.session.begin() as session:
        session.delete(session.get(FacebookRefreshSchedule,2))
    full=f.scheduler.status()
    queries=[]
    def record(_connection,_cursor,statement,_parameters,_context,_executemany):
        queries.append(statement)
    sql_event.listen(f.db.engine,'before_cursor_execute',record)
    try:summary=f.scheduler.status(include_profiles=False)
    finally:sql_event.remove(f.db.engine,'before_cursor_execute',record)
    assert summary==full|{'profiles':[]}
    assert len(queries)==1 and queries[0].lstrip().startswith('SELECT')
    assert len(full['profiles'])==3 and full['due_count']==2


def test_versioned_status_defaults_to_summary_and_legacy_keeps_details(fixture,monkeypatch):
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import router
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'via-auth'))
    from via_auth.rest import install_rest_v1
    app=FastAPI();app.state.services=SimpleNamespace(facebook_refresh=fixture.scheduler)
    app.include_router(router)
    install_rest_v1(app,('facebook',),source_routes=router.routes)
    with TestClient(app) as client:
        legacy=client.get('/api/facebook/refresh-status').json()
        summary=client.get('/api/v1/facebook/refresh-status').json()
        detailed=client.get('/api/v1/facebook/refresh-status?include_profiles=true').json()
    assert summary==legacy|{'profiles':[]} and detailed==legacy
