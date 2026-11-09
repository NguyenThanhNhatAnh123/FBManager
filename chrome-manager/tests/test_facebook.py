import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from pydantic import ValidationError
from app.api.routes import profile_rows, update_profile, delete_profile, update_settings
from app.core.facebook import FacebookCheck, facebook_url, visible_post_date
from app.core.schemas import Settings, ProfileInput
from app.core.config import Config
from app.database.session import Database
from app.models.entities import Profile, FacebookAccount, FacebookPage
from app.services.state import State
from app.services.resources import Resources
from app.services.browsers import BrowserManager
from app.services.facebook import FacebookManager, FacebookLoginRequired, public_facebook
from app.services.extensions import ExtensionManager
from app.automation.manager import TaskManager


def immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in a synchronous fixture test')


def current_day():
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return FacebookCheck(profile_ids=[1], timezone='UTC', check_date=start.date().isoformat(),
                         day_start=start.isoformat(), day_end=(start + timedelta(days=1)).isoformat())


@pytest.fixture
def svc(tmp_path):
    config = Config(root=tmp_path, data_dir=tmp_path/'data')
    config.prepare()
    db = Database(config.data_dir/'manager.sqlite3')
    state, resources = State(db), Resources()
    state.save_settings(Settings(require_proxy=False))
    browsers = BrowserManager(db, state, config, resources)
    tasks = TaskManager(db, state, browsers, config)
    facebook = FacebookManager(db, state, browsers, tasks)
    extensions = ExtensionManager(db, state, config, browsers, tasks)
    with db.session.begin() as session:
        session.add(Profile(name='Test', profile_path=str(config.profiles_dir/'Test')))
        session.add(Profile(name='Other', profile_path=str(config.profiles_dir/'Other')))
    yield SimpleNamespace(db=db, state=state, resources=resources, browsers=browsers, tasks=tasks, facebook=facebook, extensions=extensions)
    db.engine.dispose()


@pytest.mark.parametrize('url,key,clean', [
    ('https://www.facebook.com/My.Page?ref=bookmarks', 'my.page', 'https://www.facebook.com/My.Page'),
    ('https://m.facebook.com/profile.php?id=123&ref=test', '123', 'https://www.facebook.com/profile.php?id=123'),
    ('https://www.facebook.com/people/Some-Page/123/', '123', 'https://www.facebook.com/profile.php?id=123'),
])
def test_page_urls(url,key,clean):
    assert facebook_url(url) == (key,clean)


@pytest.mark.parametrize('url', ['https://facebook.com.evil.test/Page','https://evil.test/Page','https://user:pass@www.facebook.com/Page','http://facebook.com/Page','https://www.facebook.com/login','https://www.facebook.com/pages','https://www.facebook.com/Page/posts/1','https://www.facebook.com/profile.php?id=bad'])
def test_reject_non_page_urls(url):
    with pytest.raises(ValueError): facebook_url(url)


@pytest.mark.parametrize('value,expected', [
    ('Just now','2026-10-28'), ('5m','2026-10-28'), ('5 phút','2026-10-28'), ('1h','2026-10-27'),
    ('2 hours ago','2026-10-27'), ('Hôm nay lúc 00:10','2026-10-28'), ('Yesterday at 8 PM','2026-10-27'),
    ('Wednesday, October 28, 2026 at 1:00 AM','2026-10-28'), ('28 tháng 10, 2026 lúc 20:10','2026-10-28'),
    ('October 28','2026-10-28'), ('28 Oct 2026','2026-10-28'), ('December 31','2025-12-31'),
    ('2026-10-28T06:59:00+00:00','2026-10-27'), ('2026-10-28T07:00:00+00:00','2026-10-28'),
    ('Sponsored',None), ('Like',None), ('28 reactions',None), ('',None),
])
def test_visible_dates_and_midnight(value,expected):
    window=SimpleNamespace(check_date='2026-10-28',day_start='2026-10-28T07:00:00+00:00',day_end='2026-10-29T07:00:00+00:00')
    result=visible_post_date(value,datetime(2026,10,28,7,20,tzinfo=timezone.utc),window)
    assert (result.isoformat() if result else None)==expected


def test_check_input_rejects_expired_or_bad_windows():
    data=current_day().model_dump()
    with pytest.raises(ValidationError): FacebookCheck(**(data|{'day_end':data['day_start']}))
    with pytest.raises(ValidationError): FacebookCheck(**(data|{'day_start':'2020-01-01T00:00:00Z','day_end':'2020-01-02T00:00:00Z'}))
    with pytest.raises(ValidationError): FacebookCheck(**(data|{'day_start':data['day_start'].split('+')[0]}))


def seed(svc,status='posted'):
    window=current_day()
    fields={key:getattr(window,key) for key in ('check_date','timezone','day_start','day_end')}
    with svc.db.session.begin() as session:
        session.add(FacebookAccount(profile_id=1,name='Account One',url='https://www.facebook.com/AccountOne',status='complete',**fields))
        session.add_all([FacebookPage(profile_id=1,page_key='a',name='Page A',url='https://www.facebook.com/PageA',status=status,posts=json.dumps([{'url':'https://www.facebook.com/PageA/posts/1','date':window.check_date}]),**fields),
                         FacebookPage(profile_id=1,page_key='b',name='Page B',url='https://www.facebook.com/PageB',status='not_seen',**fields)])
    return window


def test_multiple_pages_are_scoped_persisted_and_expire(svc):
    window=seed(svc)
    with svc.db.session() as session:
        rows=profile_rows(svc,session)
        assert len(rows[0]['facebook']['pages'])==2
        assert rows[1]['facebook'] is None
        assert rows[0]['facebook']['pages'][0]['posts_today']==1
        assert rows[0]['facebook']['pages'][1]['posts_today']==0
        expired=public_facebook(session,datetime.fromisoformat(window.day_end))
        assert not expired[1]['fresh']
        assert all(p['status']=='unchecked' and p['posts_today'] is None and p['posts']==[] for p in expired[1]['pages'])
    separate=Database(svc.db.engine.url.database)
    with separate.session() as session:
        assert len(public_facebook(session)[1]['pages'])==2
    separate.engine.dispose()


def test_profile_delete_cascades_only_its_facebook_records(svc):
    seed(svc)
    with svc.db.session.begin() as session:
        session.add(FacebookAccount(profile_id=2,name='Second'))
        session.delete(session.get(Profile,1))
    with svc.db.session() as session:
        assert session.get(FacebookAccount,1) is None
        assert not list(session.scalars(select(FacebookPage)))
        assert session.get(FacebookAccount,2).name=='Second'


def test_reserved_scan_blocks_manual_launch_tasks_edit_delete_and_routing(svc):
    svc.browsers.facebook_profiles.add(1)
    with pytest.raises(ValueError,match='Facebook'): svc.browsers.request_start(1)
    with pytest.raises(ValueError,match='Facebook'): svc.browsers.validate_automation_start(1)
    with pytest.raises(ValueError,match='Facebook'): svc.extensions.request_setup(1)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=svc)))
    from fastapi import HTTPException
    with pytest.raises(HTTPException): immediate(update_profile(1,ProfileInput(name='Changed'),request))
    with pytest.raises(HTTPException): immediate(delete_profile(1,request,confirm=True))
    with pytest.raises(HTTPException): immediate(update_settings(Settings(proxy_routing='facebook_only'),request))
    svc.browsers.validate_automation_start(1,for_facebook=True)


def test_enqueue_rejects_busy_and_unknown_profiles(svc):
    svc.browsers.contexts[1]=object()
    result=svc.facebook.enqueue(current_day())
    assert result['accepted']==[] and len(result['errors'])==1
    assert not svc.facebook.jobs
    body=current_day().model_copy(update={'profile_ids':[999]})
    assert svc.facebook.enqueue(body)['accepted']==[]


def test_cancel_before_first_coroutine_step_releases_reservation(svc,monkeypatch):
    class Pending:
        def __init__(self,coroutine): self.coroutine=coroutine
        def add_done_callback(self,_): pass
        def cancel(self): self.coroutine.close()
    monkeypatch.setattr('app.services.facebook.asyncio.create_task',Pending)
    async def gather(*_args,**_kwargs): pass
    monkeypatch.setattr('app.services.facebook.asyncio.gather',gather)
    assert svc.facebook.enqueue(current_day())['accepted']==[1]
    assert 1 in svc.browsers.facebook_profiles
    immediate(svc.facebook.stop(1))
    assert not svc.browsers.facebook_profiles and not svc.facebook.jobs
    with svc.db.session() as session:
        assert session.get(FacebookAccount,1).status=='cancelled'


@pytest.mark.parametrize('failure',[False,True,'discovery','same_name','me_url','identity','switched','page_identity_collision','uid_cached_page','uid_identity_failure','uid_wrong_read','uid_me_read'])
def test_scan_writes_individual_results_and_cleans_up(svc,monkeypatch,failure):
    window=seed(svc)
    uid_bound=failure in ('uid_cached_page','uid_identity_failure','uid_wrong_read','uid_me_read')
    uid_url='https://www.facebook.com/profile.php?id=61590214223772'
    old_name='Stale cached Page' if uid_bound else 'Account One'
    old_url='https://www.facebook.com/profile.php?id=555' if uid_bound else 'https://www.facebook.com/AccountOne'
    if uid_bound:
        with svc.db.session.begin() as session:
            session.get(Profile,1).name='61590214223772'
            account=session.get(FacebookAccount,1);account.name=old_name;account.url=old_url
    svc.browsers.facebook_profiles.add(1)
    class Timeout:
        async def __aenter__(self):pass
        async def __aexit__(self,*_):pass
    monkeypatch.setattr('app.services.facebook.asyncio.timeout',lambda _:Timeout())
    class Locator:
        @property
        def first(self):return self
        async def wait_for(self,**_):pass
    class Page:
        def on(self,*_):pass
        def locator(self,*_):return Locator()
    class Context:
        async def new_page(self):return Page()
    async def ensure_open(*args,**kwargs):
        assert kwargs=={'for_automation':True,'for_facebook':True,'timezone_id':'UTC'}
        return Context()
    closed=[]
    async def stop(pid):closed.append(pid)
    visits=[]
    async def visit(_page,url):visits.append(url)
    async def personal(_page,expected_url=None,expected_name=None):
        assert expected_url==(uid_url if uid_bound else old_url)
        assert expected_name==(None if uid_bound else old_name)
        if failure in ('identity','uid_identity_failure'):
            from app.services.facebook_identity import FacebookIdentityError
            raise FacebookIdentityError('Chưa xác định được danh tính cá nhân.')
        return {'kind':'personal','switched':failure in ('switched','uid_cached_page'),'url':uid_url if uid_bound else old_url}
    async def read(*_args,**_kwargs):
        assert failure not in ('identity','uid_identity_failure'),'Unconfirmed identity must stop before reading account/Page data'
        if failure is True:raise FacebookLoginRequired('Needs login')
        if failure=='page_identity_collision':return {'name':'Active Page','url':'https://www.facebook.com/profile.php?id=555'}
        if uid_bound:return {'name':'Stale cached Page' if failure=='uid_wrong_read' else 'Actual FB','url':old_url if failure=='uid_wrong_read' else 'https://www.facebook.com/me' if failure=='uid_me_read' else uid_url}
        return {'name':'Account One','url':'https://www.facebook.com/me' if failure=='me_url' else 'https://www.facebook.com/AccountOne'}
    async def discover(*_):
        if failure=='discovery':
            from app.services.facebook import FacebookReadError
            raise FacebookReadError('Page list unavailable')
        if failure=='page_identity_collision':return [{'page_key':'555','name':'Active Page','url':'https://www.facebook.com/profile.php?id=555'}],None
        return [{'page_key':'a','name':'Account One' if failure=='same_name' else 'Page A','url':'https://www.facebook.com/PageA'},{'page_key':'b','name':'Page B','url':'https://www.facebook.com/PageB'}],None
    async def check(_page,row,_window):return {'status':'posted' if row.page_key=='a' else 'unknown','posts':'[]','error_message':None}
    monkeypatch.setattr(svc.browsers,'ensure_open',ensure_open)
    monkeypatch.setattr(svc.browsers,'stop',stop)
    monkeypatch.setattr(svc.facebook,'visit',visit)
    monkeypatch.setattr('app.services.facebook.ensure_personal_identity',personal)
    monkeypatch.setattr(svc.facebook,'read',read)
    monkeypatch.setattr(svc.facebook,'discover',discover)
    monkeypatch.setattr(svc.facebook,'check_page',check)
    immediate(svc.facebook._run(1,window))
    assert closed==[1] and not svc.facebook.jobs and not svc.browsers.facebook_profiles
    with svc.db.session() as session:
        account=session.get(FacebookAccount,1)
        assert account.status==('login_required' if failure is True else 'error' if failure in ('discovery','identity','page_identity_collision','uid_identity_failure','uid_wrong_read') else 'partial')
        if failure is not True:
            assert account.name==('Actual FB' if failure in ('uid_cached_page','uid_me_read') else old_name)
        if failure in (False,'same_name','me_url'):
            assert [p.status for p in session.scalars(select(FacebookPage).order_by(FacebookPage.id))]==['posted','unknown']
        if failure=='me_url':assert account.url is None
        if failure in ('identity','page_identity_collision'):
            assert account.url=='https://www.facebook.com/AccountOne'
            assert [(p.page_key,p.status) for p in session.scalars(select(FacebookPage).order_by(FacebookPage.id))]==[('a','posted'),('b','not_seen')]
        if failure in ('uid_identity_failure','uid_wrong_read'):
            assert account.url==old_url
            assert [(p.page_key,p.status) for p in session.scalars(select(FacebookPage).order_by(FacebookPage.id))]==[('a','posted'),('b','not_seen')]
        if failure in ('uid_cached_page','uid_me_read'):assert account.url==uid_url
    assert visits==['https://www.facebook.com/me']*(2 if failure in ('switched','uid_cached_page') else 1)


def test_restart_recovers_interrupted_checks(svc):
    seed(svc,status='checking')
    with svc.db.session.begin() as session:session.get(FacebookAccount,1).status='queued'
    FacebookManager(svc.db,svc.state,svc.browsers,svc.tasks)
    with svc.db.session() as session:
        assert session.get(FacebookAccount,1).status=='cancelled'
        assert session.scalar(select(FacebookPage).where(FacebookPage.page_key=='a')).status=='unknown'


def test_post_links_strip_tracking_preserve_identity():
    assert FacebookManager.post_url('https://www.facebook.com/Page/posts/123/?ref=feed')=='https://www.facebook.com/Page/posts/123'
    assert FacebookManager.post_url('https://www.facebook.com/photo.php?fbid=123&id=456&ref=feed')=='https://www.facebook.com/photo.php?id=456&fbid=123'
    assert FacebookManager.post_url('https://www.facebook.com/story.php?ref=feed') is None
    assert FacebookManager.post_url('https://evil.test/Page/posts/123') is None


@pytest.mark.parametrize('timestamp,pinned,author,empty,expected',[
    ('Just now',False,True,False,'posted'),
    ('Sponsored',False,True,False,'unknown'),
    ('January 1, 2020',True,True,False,'unknown'),
    ('January 1, 2020',False,True,False,'not_seen'),
    ('Just now',False,False,False,'unknown'),
    ('',False,False,True,'not_seen'),
])
def test_page_observations_do_not_guess_missing_dates_or_count_visitor_posts(svc,monkeypatch,timestamp,pinned,author,empty,expected):
    class Page:
        def locator(self,*_):raise ValueError('No tooltip available')
        @property
        def mouse(self):return self
        async def wheel(self,*_):pass
        async def wait_for_timeout(self,*_):pass
    async def visit(*_):pass
    async def read(*_args,**_kwargs):return {'empty':empty,'posts':[
        {'url':'https://www.facebook.com/PageA/posts/123?ref=one','times':[timestamp],'hover':0,'pinned':pinned,'author_matches':author},
        {'url':'https://www.facebook.com/PageA/posts/123?ref=two','times':[timestamp],'hover':1,'pinned':pinned,'author_matches':author},
    ]}
    monkeypatch.setattr(svc.facebook,'visit',visit)
    monkeypatch.setattr(svc.facebook,'read',read)
    row=SimpleNamespace(url='https://www.facebook.com/PageA',name='Page A')
    result=immediate(svc.facebook.check_page(Page(),row,current_day()))
    assert result['status']==expected
    assert len(json.loads(result['posts']))==(1 if expected=='posted' else 0)


@pytest.mark.parametrize('status',['checking','unknown','not_seen'])
def test_old_post_links_are_hidden_while_checking_or_when_no_post_was_confirmed(svc,status):
    seed(svc,status=status)
    with svc.db.session() as session:
        page=public_facebook(session)[1]['pages'][0]
        assert page['posts']==[]
        assert page['posts_today']==(0 if status=='not_seen' else None)


@pytest.mark.parametrize('menu_result',[True,False])
def test_account_reader_waits_and_uses_account_menu_without_switching(svc,monkeypatch,menu_result):
    from app.services.facebook import FacebookReadError
    actions=[]
    class Button:
        async def is_visible(self):return True
        async def click(self,**_):actions.append('open_menu')
    class Buttons:
        async def all(self):return [Button()]
    class Keyboard:
        async def press(self,key):actions.append(key)
    class Page:
        keyboard=Keyboard()
        async def wait_for_timeout(self,_):pass
        def get_by_role(self,*_args,**_kwargs):return Buttons()
    async def read(*_args,**kwargs):
        if kwargs.get('accountMenu') and menu_result:return {'name':'Signed In Name','url':'https://www.facebook.com/SignedIn'}
        return {'name':'','diagnostics':{'main_count':2,'heading_count':0,'profile_url':False}}
    monkeypatch.setattr(svc.facebook,'read',read)
    if menu_result:
        assert immediate(svc.facebook.read_account(Page(),1))['name']=='Signed In Name'
    else:
        with pytest.raises(FacebookReadError):immediate(svc.facebook.read_account(Page(),1))
    assert actions==['open_menu','Escape']
