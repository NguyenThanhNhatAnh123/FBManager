import json
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeoutError
from app.core.outlook import OutlookStart,OutlookCode,suggested_reader
from app.core.config import Config
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import Profile,OutlookLogin,Event
from app.services.browsers import BrowserManager
from app.services.resources import Resources
from app.services.state import State
from app.services.outlook import OutlookManager,WebInboxReader,extract_code,public_outlook,is_outlook_landing,outlook_signin_url,OUTLOOK_URL,OUTLOOK_SIGN_IN_URL
from app.automation.manager import TaskManager


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected I/O in a fixture test')


def config(**changes):
    return OutlookStart(**({'profile_id':1,'email':'sample.user@hotmail.com','password':'FixtureOnly!9','recovery_email':'recovery.sample@smvmail.com'}|changes))


@pytest.fixture
def svc(tmp_path):
    cfg=Config(root=tmp_path,data_dir=tmp_path/'data');cfg.prepare()
    db=Database(cfg.data_dir/'manager.sqlite3');state=State(db);state.save_settings(Settings(require_proxy=False))
    browsers=BrowserManager(db,state,cfg,Resources());tasks=TaskManager(db,state,browsers,cfg)
    outlook=OutlookManager(db,state,browsers,tasks)
    with db.session.begin() as session:session.add(Profile(name='One',profile_path=str(cfg.profiles_dir/'One')))
    yield SimpleNamespace(db=db,state=state,browsers=browsers,tasks=tasks,outlook=outlook)
    db.engine.dispose()


def test_credentials_are_secret_and_smvmail_suggestion_encodes_address():
    body=config(recovery_email='recovery.sample+test@smvmail.com')
    assert 'FixtureOnly!9' not in repr(body)
    assert body.inbox_url()=='https://smvmail.com/email/inbox?email=recovery.sample%2Btest%40smvmail.com'
    assert suggested_reader('x@gmail.com')==''


@pytest.mark.parametrize('url,expected',[
    ('https://www.microsoft.com/en-us/microsoft-365/outlook/email-and-calendar-software-microsoft-outlook',True),
    ('https://microsoft.com/vi-vn/microsoft-365/outlook',True),
    ('https://www.microsoft.com/en-us/store/',False),
    ('https://microsoft.com.reader.example/en-us/microsoft-365/outlook',False),
    ('https://reader.example/en-us/microsoft-365/outlook',False),
])
def test_outlook_product_page_detection(url,expected):
    assert is_outlook_landing(url) is expected


@pytest.mark.parametrize('url',[
    'https://reader.example/steal','javascript:alert(1)',
    'https://go.microsoft.com/fwlink/p/?linkid=9999999',
    'https://login.live.com.reader.example/',
    'https://user:secret@login.live.com/login.srf',
    'https://login.live.com:444/login.srf',
])
def test_landing_link_rejects_unexpected_signin_destinations(url):
    assert outlook_signin_url(url)==OUTLOOK_SIGN_IN_URL


@pytest.mark.parametrize('message',[
    'Execution context was destroyed, most likely because of a navigation',
    'Cannot find context with specified id',
    'Navigation is interrupted by another navigation',
    'net::ERR_ABORTED',
])
def test_inspect_waits_for_redirect_then_reads_the_next_document(svc,message):
    class Page:
        calls=0
        async def evaluate(self,*_):
            self.calls+=1
            if self.calls==1:raise BrowserError(message)
            return {'stage':'email','selector':'input[name="loginfmt"]'}
    page=Page()
    assert immediate(svc.outlook.inspect(page,config()))=={'stage':'loading'}
    assert immediate(svc.outlook.inspect(page,config()))['stage']=='email'


def test_closed_page_is_not_misclassified_as_a_redirect(svc):
    class Page:
        async def evaluate(self,*_):raise BrowserError('Target page, context or browser has been closed')
    with pytest.raises(BrowserError):immediate(svc.outlook.inspect(Page(),config()))


@pytest.mark.parametrize('error',[BrowserTimeoutError('Timeout 60000ms exceeded'),BrowserError('net::ERR_ABORTED')])
def test_slow_navigation_continues_observing_without_reloading(svc,error):
    calls=[]
    class Page:
        async def goto(self,url,**kwargs):
            calls.append((url,kwargs))
            raise error
    immediate(svc.outlook.navigate(1,Page(),OUTLOOK_URL))
    assert calls==[(OUTLOOK_URL,{'wait_until':'commit','timeout':60000})]


def test_diagnostics_keep_only_safe_step_information(svc):
    error=BrowserError('net::ERR_PROXY_CONNECTION_FAILED https://example.test/?token=fixture-reader-token FixtureOnly!9 123456')
    page=SimpleNamespace(url='https://login.live.com/login.srf?token=fixture-reader-token')
    assert svc.outlook.diagnose(1,'opening',error,page)=='network'
    data=json.loads((svc.browsers.config.root/'.runtime/outlook-debug/profile_1.json').read_text(encoding='utf-8'))
    assert data['category']=='network' and data['phase']=='opening' and data['host']=='login.live.com'
    with svc.db.session() as session:
        output=json.dumps(data)+' '.join(session.scalars(select(Event.message)))
    assert all(secret not in output for secret in ('FixtureOnly!9','123456','fixture-reader-token'))


@pytest.mark.parametrize('error',[BrowserTimeoutError('Timed out filling'),BrowserError('Execution context was destroyed')])
def test_fill_waits_and_reinspects_before_submitting(svc,error):
    class Page:
        url='https://login.live.com/login.srf'
        calls=0
        def locator(self,_):return self
        async def fill(self,_value,**kwargs):
            assert kwargs=={'timeout':10000}
            self.calls+=1
            if self.calls==1:raise error
        async def wait_for_timeout(self,_):pass
    page=Page()
    assert immediate(svc.outlook.fill_value(page,'input','fixture')) is False
    assert immediate(svc.outlook.fill_value(page,'input','fixture')) is True


def test_submit_navigation_interruption_does_not_cause_a_second_click(svc):
    class Element:
        calls=0
        async def click(self,**_):
            self.calls+=1
            raise BrowserError('Execution context was destroyed, most likely because of a navigation')
    button=Element()
    assert immediate(svc.outlook.click_submit(button)) is True
    assert button.calls==1


@pytest.mark.parametrize('domain',['fviainboxes.com','fviadropinbox.com','fviamail.work','dropinboxes.com'])
def test_all_published_fvia_domains_suggest_the_central_reader(domain):
    body=config(recovery_email='recovery.sample@'+domain.upper())
    assert body.inbox_url()=='https://fviainboxes.com/'
    assert config(recovery_email='recovery.sample@'+domain,recovery_url='https://reader.example/inbox').inbox_url()=='https://reader.example/inbox'


@pytest.mark.parametrize('domain',['notfviainboxes.com','fviainboxes.com.example','sub.dropinboxes.com'])
def test_unrelated_domains_are_not_sent_to_fvia(domain):
    assert suggested_reader('sample@'+domain)==''


@pytest.mark.parametrize('domain',[
    'blondmail.com','chapsmail.com','clowmail.com','dropjar.com','fivermail.com','getairmail.com',
    'getmule.com','getnada.com','gimpmail.com','givmail.com','guysmail.com','inboxbear.com',
    'replyloop.com','robot-mail.com','tafmail.com','temptami.com','tupmail.com','vomoto.com',
])
def test_published_inboxes_domains_and_custom_override(domain):
    assert config(recovery_email='recovery.sample@'+domain.upper()).inbox_url()=='https://inboxes.com/'
    assert config(recovery_email='recovery.sample@'+domain,recovery_url='https://reader.example/').inbox_url()=='https://reader.example/'
    assert suggested_reader('sample@sub.'+domain)==''
    assert suggested_reader('sample@'+domain+'.example')==''


@pytest.mark.parametrize('form_open',[False,True])
@pytest.mark.parametrize('with_ad',[False,True])
def test_inboxes_selects_exact_domain_in_the_add_inbox_form(form_open,with_ad):
    actions=[]
    opened=form_open
    ad_closed=False
    class Element:
        def __init__(self,name):self.name=name
        async def is_visible(self):return opened if self.name=='username' else True
        async def wait_for(self,**_):pass
        async def fill(self,value):actions.append(('fill',self.name,value))
        async def click(self,**_):
            nonlocal opened,ad_closed
            actions.append(('click',self.name))
            if self.name=='open-form':opened=True
            if self.name=='submit-form' and with_ad and not ad_closed:page.url='https://inboxes.com/#google_vignette'
            if self.name=='dismiss-ad':
                ad_closed=True
                page.url='https://inboxes.com/'
        async def select_option(self,value,**_):actions.append(('select',value))
        async def all(self):return [Element('open-form')]
        def locator(self,selector):
            if self.name=='adframe':
                assert selector=='#dismiss-button'
                return Element('dismiss-ad')
            assert 'Cancel' in selector
            return Element('form')
        def get_by_role(self,role,**kwargs):
            assert self.name=='form' and role=='button' and kwargs=={'name':'Add Inbox','exact':True}
            return Element('submit-form')
    class Page:
        url='https://inboxes.com/'
        def get_by_placeholder(self,name,**kwargs):
            assert name=='Enter username' and kwargs=={'exact':True}
            return Element('username')
        def get_by_role(self,*_,**__):return Element('opener')
        def locator(self,selector):
            assert selector=='select:visible'
            return Element('domain')
        def frame_locator(self,selector):
            assert selector=='iframe[id*="GoogleInterstitial"]'
            return Element('adframe')
        async def wait_for_timeout(self,_):pass
    page=Page()
    reader=WebInboxReader(page,config(recovery_email='recovery.sample@getnada.com'))
    assert immediate(reader.open_inboxes()) is True
    expected=[('fill','username','recovery.sample'),('select','getnada.com'),('click','submit-form')]
    if not form_open:expected.insert(0,('click','open-form'))
    if with_ad:expected.extend([('click','dismiss-ad'),('click','submit-form')])
    assert actions==expected
    reader.page.url='https://unrelated.example/'
    assert immediate(reader.open_inboxes()) is False


def test_fvia_reader_selects_the_exact_domain_before_opening_inbox():
    actions=[]
    class Element:
        def __init__(self,name):self.name=name
        async def is_visible(self):return True
        async def fill(self,value):actions.append(('fill',self.name,value))
        async def click(self,**_):actions.append(('click',self.name))
    class Page:
        url='https://fviainboxes.com/'
        def locator(self,selector):return Element(selector)
        def get_by_role(self,role,**kwargs):return Element((role,kwargs['name'],kwargs['exact']))
        async def wait_for_timeout(self,_):pass
    reader=WebInboxReader(Page(),config(recovery_email='recovery.sample@fviamail.work'))
    assert immediate(reader.open_fvia()) is True
    assert actions==[
        ('fill','input#username[name="username"]','recovery.sample'),
        ('click','button[aria-haspopup="listbox"]'),
        ('click',('option','fviamail.work',True)),
        ('click',('button','Get Email',True)),
    ]
    assert reader.form_address=='recovery.sample@fviamail.work'
    reader.page.url='https://reader.example/'
    assert immediate(reader.open_fvia()) is False


@pytest.mark.parametrize('url',['http://reader.example/inbox','https://localhost/inbox','https://127.0.0.1/','https://[::1]/','https://router.local/','https://user:pass@reader.example/','https://reader.example:8000/','javascript:alert(1)'])
def test_reader_rejects_non_https_local_and_credential_urls(url):
    with pytest.raises(ValidationError):config(recovery_url=url)


def test_custom_reader_and_same_mailbox_validation():
    assert config(recovery_email='recover@other.example',recovery_url='https://reader.example/mail?address={email}').inbox_url()=='https://reader.example/mail?address=recover%40other.example'
    with pytest.raises(ValidationError):config(recovery_email='sample.user@hotmail.com')
    with pytest.raises(ValidationError):config(email='not email')
    with pytest.raises(ValidationError):config(password='')


@pytest.mark.parametrize('text,expected',[
    ('Microsoft account security code: 123456','123456'),
    ('Microsoft account verification code: 123 456','123456'),
    ('Microsoft account single-use code: 12345678','12345678'),
    ('Microsoft — mã bảo mật của bạn: 1234567','1234567'),
    ('Équipe Microsoft : code de sécurité 123456','123456'),
    ('Acme Cloud verification code 123456',None),
    ('Microsoft invoice 123456',None),
    ('Microsoft security code 123456 and 654321',None),
    ('Microsoft security code 123456789012345',None),
])
def test_only_unambiguous_microsoft_codes(text,expected):assert extract_code(text)==expected


@pytest.mark.parametrize('value',['12345','123456789','12x456',''])
def test_manual_code_validation(value):
    with pytest.raises(ValidationError):OutlookCode(code=value)


def seed(svc,status='queued'):
    with svc.db.session.begin() as session:session.add(OutlookLogin(profile_id=1,email='sample.user@hotmail.com',status=status))


def test_start_and_cancel_before_first_step_do_not_store_password_or_reader_url(svc,monkeypatch):
    class Pending:
        def __init__(self,coroutine):self.coroutine=coroutine
        def add_done_callback(self,_):pass
        def cancel(self):self.coroutine.close()
    monkeypatch.setattr('app.services.outlook.asyncio.create_task',Pending)
    async def gather(*_args,**_kwargs):pass
    monkeypatch.setattr('app.services.outlook.asyncio.gather',gather)
    body=config(recovery_url='https://reader.example/inbox?key=fixture-reader-token')
    assert svc.outlook.start(body)['status']=='queued'
    with svc.db.session() as session:
        serialized=json.dumps(public_outlook(session))
        assert 'FixtureOnly!9' not in serialized and 'fixture-reader-token' not in serialized
        assert set(OutlookLogin.__table__.columns.keys())=={'profile_id','email','status','message','updated_at'}
    immediate(svc.outlook.stop(1))
    assert not svc.outlook.jobs and not svc.outlook.codes and not svc.browsers.outlook_profiles
    with svc.db.session() as session:assert session.get(OutlookLogin,1).status=='cancelled'


def test_outlook_lock_blocks_other_profile_uses_and_facebook_scan_blocks_outlook(svc):
    svc.browsers.outlook_profiles.add(1)
    with pytest.raises(ValueError,match='Outlook'):svc.browsers.request_start(1)
    with pytest.raises(ValueError,match='Outlook'):svc.browsers.validate_automation_start(1)
    svc.browsers.validate_automation_start(1,for_outlook=True)
    svc.browsers.outlook_profiles.clear();svc.browsers.facebook_profiles.add(1)
    with pytest.raises(ValueError,match='Facebook'):svc.outlook.start(config())
    assert not svc.outlook.jobs


def test_codes_are_only_accepted_for_the_current_waiting_job(svc):
    seed(svc)
    with pytest.raises(ValueError):svc.outlook.provide_code(1,'123456')
    import asyncio
    svc.outlook.codes[1]=asyncio.Queue(maxsize=1)
    svc.outlook.update(1,'waiting_code','Waiting')
    svc.outlook.provide_code(1,'123456')
    with pytest.raises(ValueError):svc.outlook.provide_code(1,'654321')
    with svc.db.session() as session:assert '123456' not in json.dumps(public_outlook(session))


def test_restart_marks_pending_login_interrupted_and_delete_cascades(svc):
    seed(svc,'waiting_code')
    OutlookManager(svc.db,svc.state,svc.browsers,svc.tasks)
    with svc.db.session.begin() as session:
        assert session.get(OutlookLogin,1).status=='interrupted'
        session.delete(session.get(Profile,1))
    with svc.db.session() as session:assert not list(session.scalars(select(OutlookLogin)))


@pytest.mark.parametrize('status',['signed_in','waiting_code'])
def test_open_waits_for_successful_cleanup_without_cancelling_active_signin(svc,monkeypatch,status):
    seed(svc,status)
    job=object();svc.outlook.jobs[1]=job;waited=[]
    monkeypatch.setattr('app.services.outlook.asyncio.shield',lambda value:value)
    async def wait(value,timeout):
        assert value is job and timeout==30
        waited.append(1)
    monkeypatch.setattr('app.services.outlook.asyncio.wait_for',wait)
    immediate(svc.outlook.wait_for_saved_session(1))
    assert waited==([1] if status=='signed_in' else [])
    assert svc.outlook.jobs[1] is job


def test_reader_never_reuses_old_or_before_request_messages(monkeypatch):
    body=config();clicked=[]
    class Page:
        def locator(self,selector):
            class Element:
                async def click(self,**_):clicked.append(selector)
            return Element()
        async def wait_for_timeout(self,_):pass
    reader=WebInboxReader(Page(),body);reader.ready=True;reader.requested()
    old={'key':'old','text':'Microsoft security code 111111','selector':'#old','received_at':(reader.started_at-timedelta(hours=1)).isoformat()}
    new={'key':'new','text':'Microsoft security code 222222','selector':'#new','received_at':datetime.now(timezone.utc).isoformat()}
    async def inspect():return {'inbox':True,'items':[old,new]}
    monkeypatch.setattr(reader,'inspect',inspect)
    assert immediate(reader.poll())=='222222'
    assert immediate(reader.poll()) is None
    assert clicked==[]


@pytest.mark.parametrize('outcome',['success','landing_success','navigation_timeout','email_fill_redirect','otp_fill_redirect','password_preferred','password_with_recovery','password_without_code','password_choice_after_password','password_choice_wrong_host','network_failure','closed_page','rejected','wrong_host','wrong_account'])
def test_login_state_machine_and_cleanup_without_persisting_secrets(svc,monkeypatch,outcome):
    seed(svc)
    svc.browsers.outlook_profiles.add(1)
    fills=[];closed=[];navigations=[];wait_calls=[];submit_calls=[]
    choice_clicks=[]
    successful={'success','landing_success','navigation_timeout','email_fill_redirect','otp_fill_redirect','password_preferred','password_with_recovery','password_without_code','password_choice_after_password'}
    fill_redirected=False
    class Timeout:
        async def __aenter__(self):pass
        async def __aexit__(self,*_):pass
    monkeypatch.setattr('app.services.outlook.asyncio.timeout',lambda _:Timeout())
    class Element:
        async def click(self,**_):choice_clicks.append(1)
        async def fill(self,value,**_):
            nonlocal fill_redirected
            fills.append(value)
            if not fill_redirected and ((outcome=='email_fill_redirect' and value=='sample.user@hotmail.com') or (outcome=='otp_fill_redirect' and value=='123456')):
                fill_redirected=True
                raise BrowserError('Execution context was destroyed, most likely because of a navigation')
    class Page:
        url='https://login.live.com/login.srf'
        async def goto(self,url,**_kwargs):
            navigations.append(url)
            assert _kwargs['wait_until']=='commit'
            if outcome=='landing_success':
                self.url='https://www.microsoft.com/en-us/microsoft-365/outlook/email-and-calendar-software-microsoft-outlook' if url==OUTLOOK_URL else 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize'
            if outcome in ('wrong_host','password_choice_wrong_host'):self.url='https://reader.example/login'
            if outcome=='navigation_timeout':raise BrowserTimeoutError('Timeout 60000ms exceeded')
            if outcome=='network_failure':raise BrowserError('net::ERR_CONNECTION_RESET with FixtureOnly!9 and 123456')
            if outcome=='closed_page':raise BrowserError('Target page, context or browser has been closed')
        async def bring_to_front(self):pass
        async def wait_for_timeout(self,_):pass
        def locator(self,_):return Element()
    class Context:
        async def new_page(self):return Page()
    async def ensure(*_args,**kwargs):
        assert kwargs=={'for_automation':True,'for_outlook':True}
        return Context()
    async def stop(pid):closed.append(pid)
    class Reader:
        def __init__(self,*_):pass
        async def prepare(self):pass
        def requested(self):pass
    monkeypatch.setattr('app.services.outlook.WebInboxReader',Reader)
    monkeypatch.setattr(svc.browsers,'ensure_open',ensure)
    monkeypatch.setattr(svc.browsers,'stop',stop)
    sequence=['email','password','code','stay_signed_in','signed_in']
    if outcome=='email_fill_redirect':sequence.insert(0,'email')
    if outcome=='otp_fill_redirect':sequence.insert(2,'code')
    if outcome=='password_preferred':sequence=['email','use_password','use_password','password','code','stay_signed_in','signed_in']
    if outcome=='password_with_recovery':sequence=['email','use_password','password','recovery','code','stay_signed_in','signed_in']
    if outcome=='password_without_code':sequence=['email','use_password','password','stay_signed_in','signed_in']
    if outcome=='password_choice_after_password':sequence=['email','password','use_password','code','stay_signed_in','signed_in']
    if outcome=='password_choice_wrong_host':sequence=['use_password']
    steps=iter((['outlook_landing'] if outcome=='landing_success' else [])+sequence)
    async def inspect(_page,_body,**_kwargs):
        if outcome=='rejected':return {'stage':'rejected'}
        if outcome=='wrong_account':return {'stage':'signed_in','account_email':'another@hotmail.com'}
        stage=next(steps)
        if stage in ('code','recovery','stay_signed_in','signed_in'):assert _kwargs['password_entered'] is True
        return {'stage':stage,'selector':'input','account_email':_body.email,'signin_url':OUTLOOK_SIGN_IN_URL}
    async def submit(*_args,**_kwargs):submit_calls.append(1);return True
    async def wait_code(*_):wait_calls.append(1);return '123456'
    monkeypatch.setattr(svc.outlook,'inspect',inspect)
    monkeypatch.setattr(svc.outlook,'submit',submit)
    monkeypatch.setattr(svc.outlook,'wait_code',wait_code)
    immediate(svc.outlook.run(config()))
    assert closed==([] if outcome=='network_failure' else [1])
    assert not svc.browsers.outlook_profiles and not svc.outlook.codes
    with svc.db.session() as session:
        row=session.get(OutlookLogin,1)
        assert row.status==('signed_in' if outcome in successful else 'failed')
        assert 'FixtureOnly!9' not in (row.message or '')
    if outcome in ('success','landing_success','navigation_timeout'):assert fills==['sample.user@hotmail.com','FixtureOnly!9','123456']
    if outcome in ('email_fill_redirect','otp_fill_redirect'):
        assert len(submit_calls)==4 and len(wait_calls)==1
        assert fills==(['sample.user@hotmail.com','sample.user@hotmail.com','FixtureOnly!9','123456'] if outcome=='email_fill_redirect' else ['sample.user@hotmail.com','FixtureOnly!9','123456','123456'])
    if outcome=='landing_success':assert navigations==[OUTLOOK_URL,OUTLOOK_SIGN_IN_URL]
    if outcome in ('password_preferred','password_with_recovery','password_without_code'):
        assert choice_clicks==[1], 'select Use your password once, even while the old screen remains visible'
        expected=['sample.user@hotmail.com','FixtureOnly!9']
        if outcome=='password_with_recovery':expected.append('recovery.sample@smvmail.com')
        if outcome!='password_without_code':expected.append('123456')
        assert fills==expected
        assert len(wait_calls)==(0 if outcome=='password_without_code' else 1)
    if outcome=='password_choice_after_password':assert choice_clicks==[]
    if outcome in ('wrong_host','password_choice_wrong_host'):assert fills==[] and choice_clicks==[]
