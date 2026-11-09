"""Recipient protocol and persistence, without launching a saved profile."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.core.config import Config
from app.core.schemas import Settings
from app.core.page_recipient import PageRecipientStart,PageSetup,asset_path
from app.database.session import Database
from app.models.entities import Profile,BmProfile,PageRecipientJob
from app.services.browsers import BrowserManager
from app.services.resources import Resources
from app.services.state import State
from app.services.page_recipient import PageRecipientManager,RecipientPause
from app.services import page_recipient as protocol


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in offline fixture')


@pytest.fixture
def manager(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    state.save_settings(Settings(require_proxy=False))
    browsers=BrowserManager(db,state,config,Resources())
    tasks=SimpleNamespace(profile_jobs={})
    result=PageRecipientManager(db,state,browsers,tasks,config)
    with db.session.begin() as session:
        session.add(Profile(id=3,name='Recipient fixture',profile_path=str(tmp_path/'profiles'/'Test')))
    def spawn(pid):
        browsers.recipient_profiles.add(pid);result.codes.setdefault(pid,asyncio.Queue(maxsize=1))
    result._spawn=spawn
    yield result
    db.engine.dispose()


def body(**extra):
    return PageRecipientStart(profile_id=3,outlook_email='recipient@example.test',**extra)


def start(manager,**extra):
    value=body(**extra);manager.start(value);return value


def test_blank_setup_means_receive_switch_only_and_never_invents_data():
    value=body(setup={'bio':'   ','categories':[' ',''],'address':{'city':' '}})
    assert value.accept_only and value.setup.requested()=={}
    assert PageSetup(categories=['Shop','Shop',' Art ']).categories==['Shop','Art']


@pytest.mark.parametrize('extra',[
    {'code_wait_seconds':301},{'code_wait_seconds':0},
    {'expected_page_url':'https://facebook.com.evil.test/Page'},
    {'expected_page_url':'https://www.facebook.com/login'},
    {'setup':{'password':'secret'}},{'password':'secret'},
])
def test_request_rejects_unsafe_or_secret_inputs(extra):
    with pytest.raises(ValidationError):body(**extra)


def test_asset_path_restricts_root_size_and_real_image_signature(manager,tmp_path):
    root=manager.asset_root
    good=root/'avatar.png';good.write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*12)
    assert asset_path(str(good),root)==good
    outside=tmp_path/'outside.png';outside.write_bytes(good.read_bytes())
    fake=root/'fake.jpg';fake.write_text('not image')
    empty=root/'empty.png';empty.touch()
    for path in (outside,fake,empty,root):
        with pytest.raises(ValueError):asset_path(str(path),root)


def test_start_stores_only_config_progress_and_skips_all_setup(manager):
    manager.start(body(expected_page_url='https://m.facebook.com/profile.php?id=555&ref=tracking'))
    row=manager.row(3);configuration=json.loads(row.configuration)
    assert configuration['expected_page_url']=='https://www.facebook.com/profile.php?id=555'
    assert all(field['status']=='skipped' for field in json.loads(row.fields).values())
    assert row.status=='queued' and 3 in manager.browsers.recipient_profiles
    assert not {'password','code','mail_body'}&configuration.keys()


def test_image_fingerprint_includes_file_contents(manager):
    path=manager.asset_root/'avatar.png';path.write_bytes(b'\x89PNG\r\n\x1a\nfixture1')
    first=manager.input_fingerprint('avatar',str(path))
    path.write_bytes(b'\x89PNG\r\n\x1a\nfixture2')
    assert first!=manager.input_fingerprint('avatar',str(path))


@pytest.mark.parametrize('owner',['recipient_profiles','outlook_profiles','facebook_profiles','extension_profiles'])
def test_start_rejects_profile_owned_by_any_helper(manager,owner):
    getattr(manager.browsers,owner).add(3)
    with pytest.raises(ValueError):manager.start(body())
    assert manager.row(3) is None


def test_start_rejects_bm_role(manager):
    with manager.db.session.begin() as session:session.add(BmProfile(profile_id=3))
    with pytest.raises(ValueError,match='BM'):manager.start(body())


def test_marker_blocks_other_browser_operations_even_when_no_window(manager):
    manager.browsers.recipient_profiles.add(3)
    with pytest.raises(ValueError,match='nhận Page'):manager.browsers.request_start(3)
    with pytest.raises(ValueError,match='nhận Page'):manager.browsers.validate_automation_start(3)
    manager.browsers.validate_automation_start(3,for_recipient=True)


def test_resume_confirmation_is_bound_to_reviewed_page_and_control(manager):
    start(manager);review={'url':'https://www.facebook.com/profile.php?id=555','control':'partial'}
    manager.checkpoint(3,review_target=review,page_url=review['url'])
    manager.update(3,status='needs_confirmation')
    manager.resume(3)
    assert json.loads(manager.row(3).checkpoint)['confirmed_target']==review
    assert manager.row(3).status=='queued'


def test_manual_code_only_in_memory_and_queue_keeps_latest(manager):
    start(manager)
    with pytest.raises(ValueError):manager.submit_code(3,'12345678')
    manager.update(3,status='code_timeout')
    manager.submit_code(3,'12345678');manager.submit_code(3,'87654321')
    assert manager.codes[3].get_nowait()=='87654321'
    row=manager.row(3)
    assert '87654321' not in repr(vars(row))
    assert '12345678' not in json.dumps(manager.list())


def test_recover_marks_old_jobs_interrupted_and_preserves_checkpoint(manager):
    start(manager);manager.checkpoint(3,accepted=True,page_url='https://www.facebook.com/PageFixture')
    manager.update(3,status='waiting_code',code_deadline='2099-01-01T00:00:00+00:00')
    recovered=PageRecipientManager(manager.db,manager.state,manager.browsers,manager.tasks,manager.config)
    assert recovered.row(3).status=='interrupted'
    assert recovered.row(3).code_deadline is None
    assert json.loads(recovered.row(3).checkpoint)['accepted']


class Page:
    def __init__(self):self.waits=0
    async def wait_for_timeout(self,_):self.waits+=1


class Reader:
    def __init__(self,code='12345678',timeout=False):self.code=code;self.timeout=timeout;self.baselines=0;self.requests=[]
    async def capture_baseline(self):self.baselines+=1
    async def wait_for_code(self,since,**options):
        self.requests.append((since,options))
        if self.timeout:raise TimeoutError()
        return self.code


def stages(manager,values):
    sequence=iter(values)
    async def read(*_):return next(sequence)
    manager.read=read


def test_review_accept_pauses_before_access_without_confirmation(manager,monkeypatch):
    value=start(manager);manager.checkpoint(3,page_url='https://www.facebook.com/PageFixture')
    stages(manager,[{'review':True},{'next':True},{'accept':True,'control':'partial'}]);actions=[]
    async def click(_,pattern):actions.append(pattern)
    monkeypatch.setattr(protocol,'click_named',click)
    with pytest.raises(RecipientPause) as error:immediate(manager.receive(Page(),3,value,Reader()))
    assert error.value.status=='needs_confirmation'
    assert len(actions)==2 and all('Accept|' not in action for action in actions)
    checkpoint=json.loads(manager.row(3).checkpoint)
    assert checkpoint['review_target']['control']=='partial' and not checkpoint.get('accepted')


def test_rendered_review_invitation_label_is_used_without_relabeling_ui(manager,monkeypatch):
    value=start(manager);manager.checkpoint(3,page_url='https://www.facebook.com/PageFixture')
    stages(manager,[{'review':True,'actions':{'review':'Review Invitation'}},{'next':True,'actions':{'next':'Next'}},{'accept':True,'control':'partial'}]);actions=[]
    async def click(_,pattern):actions.append(pattern)
    monkeypatch.setattr(protocol,'click_named',click)
    with pytest.raises(RecipientPause) as error:immediate(manager.receive(Page(),3,value,Reader()))
    assert error.value.status=='needs_confirmation'
    assert actions==['^Review\\ Invitation$','^Next$']


def test_visible_action_names_are_escaped_as_literal_labels():
    assert protocol.action_pattern({'actions':{'next':'Next (continue)'}},'next','fallback')==r'^Next\ \(continue\)$'
    assert protocol.action_pattern({},'next','fallback')=='fallback'


def test_confirmed_accept_requests_fresh_mail_then_code_and_success(manager,monkeypatch):
    value=start(manager);target={'url':'https://www.facebook.com/PageFixture','control':'partial'}
    manager.checkpoint(3,page_url=target['url'],confirmed_target=target)
    stages(manager,[{'accept':True,'control':'partial'},{'code':True},{'accepted':True}]);actions=[];filled=[]
    async def click(_,pattern):actions.append(pattern)
    class Input:
        async def fill(self,value,**_):filled.append(value)
    async def named(*_):return Input()
    monkeypatch.setattr(protocol,'click_named',click);monkeypatch.setattr(protocol,'visible_named',named)
    reader=Reader();immediate(manager.receive(Page(),3,value,reader))
    assert reader.baselines==1 and reader.requests[0][1]['timeout_seconds']==300
    assert filled==[reader.code] and len(actions)==1
    checkpoint=json.loads(manager.row(3).checkpoint)
    assert checkpoint['accepted'] and 'code_requested_at' in checkpoint
    assert reader.code not in repr(vars(manager.row(3)))


def test_code_timeout_is_paused_without_retrying_accept_or_resend(manager):
    value=start(manager);stages(manager,[{'code':True}]);manager.codes[3]=asyncio.Queue()
    with pytest.raises(RecipientPause) as error:immediate(manager.receive(Page(),3,value,Reader(timeout=True)))
    assert error.value.status=='code_timeout'
    assert not json.loads(manager.row(3).checkpoint).get('accepted')


def test_changed_control_does_not_reuse_confirmation(manager):
    value=start(manager)
    manager.checkpoint(3,page_url='https://www.facebook.com/PageFixture',confirmed_target={'url':'https://www.facebook.com/PageFixture','control':'partial'})
    stages(manager,[{'accept':True,'control':'full'}])
    with pytest.raises(RecipientPause) as error:immediate(manager.receive(Page(),3,value,Reader()))
    assert error.value.status=='needs_confirmation'


def test_stop_releases_owner_and_transient_codes_preserves_accepted(manager):
    start(manager);manager.checkpoint(3,accepted=True)
    manager.codes[3]=asyncio.Queue();manager.codes[3].put_nowait('12345678')
    async def stop(_):pass
    manager.browsers.stop=stop
    immediate(manager.stop(3))
    assert 3 not in manager.browsers.recipient_profiles and 3 not in manager.codes
    assert manager.row(3).status=='cancelled' and json.loads(manager.row(3).checkpoint)['accepted']


def test_closing_chrome_does_not_cancel_a_completed_receipt(manager):
    start(manager);manager.checkpoint(3,accepted=True,switched=True)
    manager.update(3,status='completed',step='ready_setup',message='Verified Page receipt')
    async def stop(_):pass
    manager.browsers.stop=stop
    immediate(manager.stop(3))
    assert manager.row(3).status=='completed' and manager.row(3).step=='ready_setup'
    assert 3 not in manager.browsers.recipient_profiles


def test_resume_keeps_verification_redirect_and_switches_only_once(manager,monkeypatch):
    start(manager);target='https://www.facebook.com/profile.php?id=555'
    manager.checkpoint(3,page_url=target,personal_url='https://www.facebook.com/profile.php?id=111')
    class NoWait:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
    class LivePage:
        url='https://accountscenter.facebook.com/confirmation'
        def is_closed(self):return False
    class LiveReader:
        async def prepare(self):pass
    page=LivePage();context=object();calls=[]
    manager.semaphore=NoWait();monkeypatch.setattr(protocol.asyncio,'timeout',lambda _:NoWait())
    async def opened(*_,**__):return context
    async def visit(*_):calls.append('navigation')
    async def receive(*_):calls.append('receive')
    async def screenshot(*_):pass
    async def switched(*_):calls.append('switch');return {'name':'Page Fixture','kind':'page','url':target}
    async def invitation(*_):return {'url':target,'name':'Page Fixture'}
    manager.browsers.ensure_open=opened;manager.visit=visit;manager.receive=receive;manager.screenshot=screenshot;manager.find_invite=invitation
    monkeypatch.setattr(protocol,'switch_to_page_identity',switched)
    manager.sessions[3]={'context':context,'page':page,'outlook':LivePage(),'reader':LiveReader()}
    stages(manager,[{'code':True},{'heading':'Page Fixture'}])
    immediate(manager._run(3))
    assert calls==['receive','switch']
    assert manager.row(3).status=='completed' and manager.row(3).step=='ready_setup'


def test_accepted_checkpoint_switches_exact_page_without_reading_stale_invitation(manager,monkeypatch):
    value=start(manager);target='https://www.facebook.com/profile.php?id=61594160723315'
    manager.checkpoint(3,accepted=True,page_url=target);manager.update(3,page_name='Familien Spaß')
    calls=[];reader=Reader()
    async def switch(page,url,name):
        calls.append((url,name));return {'kind':'page','url':url,'name':name}
    async def forbidden(*_args,**_kwargs):raise AssertionError('Accepted checkpoints cannot reopen or accept invitation UI')
    monkeypatch.setattr(protocol,'switch_to_page_identity',switch)
    monkeypatch.setattr(protocol,'click_named',forbidden);monkeypatch.setattr(manager,'read',forbidden)
    immediate(manager.receive(Page(),3,value,reader))
    assert calls==[(target,'Familien Spaß')] and reader.baselines==0 and reader.requests==[]


@pytest.mark.parametrize('failure',[protocol.FacebookIdentityError('Wrong Page'),RuntimeError('fixture-private-code')])
def test_accepted_checkpoint_failed_switch_pauses_without_retrying_accept(manager,monkeypatch,failure):
    value=start(manager);manager.checkpoint(3,accepted=True,page_url='https://www.facebook.com/profile.php?id=61594160723315')
    calls=[]
    async def switch(*_):calls.append('switch');raise failure
    async def forbidden(*_args,**_kwargs):raise AssertionError('Never repeat Accept after accepted checkpoint')
    monkeypatch.setattr(protocol,'switch_to_page_identity',switch)
    monkeypatch.setattr(protocol,'click_named',forbidden);monkeypatch.setattr(manager,'read',forbidden)
    with pytest.raises(RecipientPause) as error:immediate(manager.receive(Page(),3,value,Reader()))
    assert error.value.status=='needs_user' and 'fixture-private-code' not in error.value.message
    assert calls==['switch'] and json.loads(manager.row(3).checkpoint)['accepted']


def test_accepted_checkpoint_preserves_cancellation_during_switch(manager,monkeypatch):
    value=start(manager);manager.checkpoint(3,accepted=True,page_url='https://www.facebook.com/profile.php?id=61594160723315')
    async def switch(*_):raise asyncio.CancelledError()
    monkeypatch.setattr(protocol,'switch_to_page_identity',switch)
    with pytest.raises(asyncio.CancelledError):immediate(manager.receive(Page(),3,value,Reader()))


def test_autosubmitted_code_dialog_disappears_without_clicking_continue_or_accept(manager,monkeypatch):
    value=start(manager);manager.checkpoint(3,page_url='https://www.facebook.com/PageFixture')
    stages(manager,[{'code':True},{'code':False,'accept':True,'control':'partial'},
                    {'accept':True,'control':'partial'},{'review':True},{'switch_now':True}])
    filled=[];reader=Reader()
    class Input:
        async def fill(self,value,**_):filled.append(value)
    async def named(_page,role,_pattern):
        assert role=='textbox','No Continue lookup after the code dialog disappears'
        return Input()
    async def forbidden(*_args,**_kwargs):raise AssertionError('No Continue, Review or Accept after auto-submit')
    monkeypatch.setattr(protocol,'visible_named',named);monkeypatch.setattr(protocol,'click_named',forbidden)
    immediate(manager.receive(Page(),3,value,reader))
    assert filled==[reader.code] and reader.baselines==0 and len(reader.requests)==1
    assert json.loads(manager.row(3).checkpoint)['accepted']
    assert reader.code not in repr(vars(manager.row(3)))


def test_code_waits_for_enabled_continue_and_clicks_it_once(manager,monkeypatch):
    value=start(manager);manager.checkpoint(3,page_url='https://www.facebook.com/PageFixture')
    stages(manager,[{'code':True},{'code':True,'continue':True},{'code':True,'continue':True},
                    {'code':True,'continue':True,'actions':{'continue':'Continue'}},
                    {'accept':True,'control':'partial'},{'accepted':True}])
    calls=[];lookups=[]
    class Input:
        async def fill(self,value,**_):calls.append('fill')
    class Button:
        async def click(self,**_):calls.append('continue')
    async def named(_page,role,_pattern):
        if role=='textbox':return Input()
        lookups.append(role);return Button() if len(lookups)==3 else None
    async def forbidden(*_args,**_kwargs):raise AssertionError('Accept cannot run while a submitted code settles')
    monkeypatch.setattr(protocol,'visible_named',named);monkeypatch.setattr(protocol,'click_named',forbidden)
    page=Page();immediate(manager.receive(page,3,value,Reader()))
    assert calls==['fill','continue'] and lookups==['button']*3 and page.waits>=2
    assert json.loads(manager.row(3).checkpoint)['accepted']


def test_continue_detaches_during_auto_submit_and_success_is_rechecked(manager,monkeypatch):
    value=start(manager);manager.checkpoint(3,page_url='https://www.facebook.com/PageFixture')
    stages(manager,[{'code':True},{'code':True,'continue':True},{'accepted':True}]);calls=[]
    class Input:
        async def fill(self,value,**_):pass
    class Button:
        async def click(self,**_):calls.append('continue');raise protocol.BrowserError('fixture-private-code')
    async def named(_page,role,_pattern):return Input() if role=='textbox' else Button()
    monkeypatch.setattr(protocol,'visible_named',named)
    immediate(manager.receive(Page(),3,value,Reader()))
    assert calls==['continue'] and json.loads(manager.row(3).checkpoint)['accepted']


def test_code_with_no_enabled_continue_pauses_after_bounded_wait_without_accept(manager,monkeypatch):
    value=start(manager);manager.checkpoint(3,page_url='https://www.facebook.com/PageFixture');reads=[]
    async def read(*_):reads.append('stage');return {'code':True,'accept':True,'control':'partial'}
    class Input:
        async def fill(self,value,**_):pass
    async def named(_page,role,_pattern):return Input() if role=='textbox' else None
    monkeypatch.setattr(manager,'read',read);monkeypatch.setattr(protocol,'visible_named',named)
    with pytest.raises(RecipientPause) as error:immediate(manager.receive(Page(),3,value,Reader()))
    assert error.value.status=='needs_user' and len(reads)==21
    assert not json.loads(manager.row(3).checkpoint).get('accepted')


def test_notifications_and_review_happen_before_any_outlook_access(manager,monkeypatch):
    start(manager)
    calls=[];target='https://www.facebook.com/profile.php?id=555'
    class NoWait:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
    class LivePage:
        url=target
        def is_closed(self):return False
        async def wait_for_timeout(self,_):pass
    class Context:
        async def new_page(self):calls.append('facebook tab');return page
    page=LivePage();context=Context()
    manager.semaphore=NoWait();monkeypatch.setattr(protocol.asyncio,'timeout',lambda _:NoWait())
    async def opened(*_,**__):return context
    async def screenshot(*_):pass
    async def verify(*_):calls.append('verify Facebook')
    async def invitation(*_):calls.append('notifications');return {'url':target,'name':'Page Fixture'}
    async def visit(*_):calls.append('invitation page')
    async def click(_,pattern):calls.append(pattern)
    def forbidden(*_,**__):raise AssertionError('Outlook must not open before Facebook review confirmation')
    manager.browsers.ensure_open=opened;manager.screenshot=screenshot;manager.verify_personal=verify;manager.find_invite=invitation;manager.visit=visit
    monkeypatch.setattr(protocol,'OutlookInboxReader',forbidden);monkeypatch.setattr(protocol,'click_named',click)
    stages(manager,[{'heading':'Page Fixture'},{'review':True,'actions':{'review':'Review Invitation'}},{'next':True},{'accept':True,'control':'partial'}])
    immediate(manager._run(3))
    assert manager.row(3).status=='needs_confirmation'
    assert calls[:4]==['facebook tab','verify Facebook','notifications','invitation page']
    assert calls[4]=='^Review\\ Invitation$' and len(calls)==6
    assert 3 in manager.browsers.recipient_profiles


def test_optional_outlook_baseline_does_not_block_accept_on_login(monkeypatch):
    calls=[]
    class NoWait:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
    class Page:
        def is_closed(self):return False
        async def goto(self,*_,**__):calls.append('mail navigation')
        async def bring_to_front(self):calls.append('return Facebook')
    class Context:
        async def new_page(self):return Page()
    class Reader:
        def __init__(self,*_,require_account=True,**__):assert require_account is False
        async def try_capture_baseline(self,**_):raise protocol.OutlookInboxNeedsLogin('Sign in')
    monkeypatch.setattr(protocol.asyncio,'timeout',lambda _:NoWait());monkeypatch.setattr(protocol,'OutlookInboxReader',Reader)
    mailbox=protocol.RecipientMailbox(Context(),'recipient@example.test',Page())
    assert calls==[]
    assert immediate(mailbox.capture_baseline()) is None
    assert calls==['mail navigation','return Facebook']
