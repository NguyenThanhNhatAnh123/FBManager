"""Facebook OTP inbox reader: synthetic messages and a rendered-UI state spy."""
import asyncio
import hashlib
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as BrowserError,TimeoutError as BrowserTimeoutError

from app.services.outlook_inbox import (
    OutlookInboxError,OutlookInboxNeedsLogin,OutlookInboxWrongAccount,OutlookInboxReader,extract_facebook_code,
)


EMAIL='recipient.fixture@hotmail.com'
SINCE=datetime(2026,10,29,10,0,tzinfo=timezone.utc)
NOW=SINCE+timedelta(minutes=1)
CODE='12345678'


def message(**changes):
    return {
        'key':'fixture-message','sender':'Facebook <security@facebookmail.com>',
        'recipients':[EMAIL],'subject':f'{CODE} is your Facebook confirmation code',
        'body':f'Your Facebook security code is {CODE}. Do not share this code.',
        'received_at':(SINCE+timedelta(seconds=5)).isoformat(),
    }|changes


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in a synthetic UI test')


@pytest.mark.parametrize('digits',['123456','1234567','12345678'])
def test_fresh_security_mail_accepts_one_contextual_six_to_eight_digit_code(digits):
    data=message(subject=f'{digits} is your Facebook confirmation code',body=f'Your Facebook security code: {digits}')
    assert extract_facebook_code(data,EMAIL,SINCE,now=NOW)==digits


@pytest.mark.parametrize('changes',[
    {'sender':'Facebook'},
    {'sender':'Facebook <security@facebookmail.com.evil.test>'},
    {'sender':'security@other.test'},
    {'sender':'security@facebookmail.com, other@example.test'},
    {'sender':'notification@facebookmail.com'},
    {'recipients':['other.fixture@hotmail.com']},
    {'recipients':[]},
    {'recipients':None},
    {'received_at':(SINCE-timedelta(seconds=1)).isoformat()},
    {'received_at':(NOW+timedelta(seconds=1)).isoformat()},
    {'received_at':'2026-10-29T10:00:05'},
    {'received_at':'10:00 AM'},
    {'received_at':None},
    {'subject':'New login alert for Facebook'},
    {'subject':'Facebook login-alert security code'},
    {'subject':'Facebook Page invitation: verification code'},
    {'subject':'Your Facebook ads confirmation code'},
    {'subject':'Facebook campaign security code'},
    {'subject':'Facebook notification'},
    {'body':'Open https://www.facebook.com/confirm?code=12345678'},
    {'subject':'Microsoft verification code','body':'Your Microsoft security code is 12345678'},
    {'body':'Your Facebook security codes are 12345678 and 87654321'},
    {'subject':'Facebook confirmation code 12345','body':'Your Facebook security code is 12345'},
    {'subject':'Facebook confirmation code 123456789','body':'Your Facebook security code is 123456789'},
    {'subject':'Facebook confirmation code ١٢٣٤٥٦٧٨','body':'Your Facebook security code is ١٢٣٤٥٦٧٨'},
    {'unverified':True},
])
def test_unrelated_stale_ambiguous_or_unbound_mail_never_produces_a_code(changes):
    assert extract_facebook_code(message(**changes),EMAIL,SINCE,now=NOW) is None


def test_header_addresses_are_case_insensitive_and_urls_do_not_create_extra_candidates():
    data=message(sender='Facebook <SECURITY@FACEBOOKMAIL.COM>',recipients=[EMAIL.upper()],body=f'Facebook security code: {CODE}\nhttps://www.facebook.com/profile.php?id=87654321')
    assert extract_facebook_code(data,EMAIL,SINCE,now=NOW)==CODE


def test_missing_or_invalid_request_time_and_reused_codes_are_rejected():
    assert extract_facebook_code(message(),EMAIL,SINCE.replace(tzinfo=None),now=NOW) is None
    digest=hashlib.sha256(CODE.encode()).hexdigest()
    assert extract_facebook_code(message(),EMAIL,SINCE,now=NOW,used_code_hashes={digest}) is None


@pytest.mark.parametrize('loading,matching',[(True,True),(False,False)])
def test_reader_never_accepts_a_stale_reading_pane_during_selection_or_loading(loading,matching):
    reader=OutlookInboxReader(SimpleNamespace(),EMAIL)
    assert reader._extract({'loading':loading,'detail':message(selection_matches=matching)},SINCE) is None


class Clock:
    def __init__(self):self.now=0;self.sleeps=[]
    def monotonic(self):return self.now
    async def sleep(self,seconds):self.sleeps.append(seconds);self.now+=seconds


@pytest.fixture
def clock(monkeypatch):
    value=Clock()
    monkeypatch.setattr('app.services.outlook_inbox.time.monotonic',value.monotonic)
    monkeypatch.setattr('app.services.outlook_inbox.asyncio.sleep',value.sleep)
    return value


class Page:
    def __init__(self,clock,*,account=EMAIL,show_account=True,arrival=0,location=('inbox','focused'),detail=None):
        self.clock=clock;self.account=account;self.show_account=show_account;self.arrival=arrival
        self.location=location;self.message=detail or message();self.folder='inbox';self.tab='focused'
        self.opened=False;self.account_open=False;self.actions=[];self.reads=0;self.slow_reads=0
        self.url='https://outlook.live.com/mail/0/inbox'
        self.keyboard=SimpleNamespace(press=self.press)
    async def press(self,key):self.actions.append(('key',key));self.account_open=False
    async def goto(self,url,**_):self.actions.append(('goto',url));self.url=url
    async def wait_for_timeout(self,milliseconds):self.clock.now+=milliseconds/1000
    async def evaluate(self,source,args):
        assert 'cookies' in source and args=={'email':EMAIL}
        self.reads+=1
        if self.reads<=self.slow_reads:raise BrowserTimeoutError('sensitive-fixture-value 87654321')
        present=self.clock.now>=self.arrival and (self.folder,self.tab if self.folder=='inbox' else None)==self.location
        items=[{'key':self.message.get('key'),'subject':self.message['subject'],'hint':'Facebook '+self.message['subject'],'received_at':self.message.get('received_at'),'selector':'[data-cm-outlook-message="0"]'}] if present else []
        return {
            'state':'mailbox','account_email':self.account if self.show_account or self.account_open else '',
            'account_selector':'[data-cm-outlook-account="0"]',
            'folder':self.folder,
            'folders':[{'kind':'inbox','selected':self.folder=='inbox','selector':'[data-cm-outlook-folder="0"]'},
                       {'kind':'junk','selected':self.folder=='junk','selector':'[data-cm-outlook-folder="1"]'}],
            'tabs':[{'kind':'focused','selected':self.tab=='focused','selector':'[data-cm-outlook-tab="0"]'},
                    {'kind':'other','selected':self.tab=='other','selector':'[data-cm-outlook-tab="1"]'}] if self.folder=='inbox' else [],
            'items':items,'detail':deepcopy(self.message) if self.opened and present else None,
        }
    def locator(self,selector):
        async def click(**_):
            self.actions.append(('click',selector))
            if 'account' in selector:self.account_open=True
            elif 'folder' in selector:self.folder='inbox' if '"0"' in selector else 'junk';self.opened=False
            elif 'tab' in selector:self.tab='focused' if '"0"' in selector else 'other';self.opened=False
            elif 'message' in selector:self.opened=True
        return SimpleNamespace(click=click)


def test_prepare_opens_only_the_account_ui_when_identity_is_collapsed(clock):
    page=Page(clock,show_account=False)
    reader=OutlookInboxReader(page,EMAIL)
    immediate(reader.prepare())
    assert reader.prepared and ('click','[data-cm-outlook-account="0"]') in page.actions
    assert page.actions[-1]==('key','Escape')


@pytest.mark.parametrize('url',['https://login.live.com/login.srf','https://login.microsoftonline.com/common/oauth2/authorize','https://www.microsoft.com/en-us/microsoft-365/outlook/email-and-calendar-software-microsoft-outlook'])
def test_prepare_requires_existing_outlook_login_without_filling_credentials(clock,url):
    page=Page(clock);page.url=url
    with pytest.raises(OutlookInboxNeedsLogin):immediate(OutlookInboxReader(page,EMAIL).prepare())
    assert page.actions==[]


def test_prepare_rejects_a_different_mailbox_even_if_message_recipient_matches(clock):
    page=Page(clock,account='different.fixture@hotmail.com')
    with pytest.raises(OutlookInboxNeedsLogin):immediate(OutlookInboxReader(page,EMAIL).prepare())
    assert page.actions==[] and clock.now==0


def test_prepare_waits_through_a_transient_auth_redirect_to_the_saved_mailbox(clock,monkeypatch):
    page=Page(clock);page.url='https://login.live.com/oauth20_authorize.srf'
    async def sso_transition(seconds):
        await clock.sleep(seconds)
        if clock.now>=3:page.url='https://outlook.live.com/mail/0/inbox'
    monkeypatch.setattr('app.services.outlook_inbox.asyncio.sleep',sso_transition)
    reader=OutlookInboxReader(page,EMAIL)
    immediate(reader.prepare())
    assert reader.prepared and clock.now==3 and page.reads==1
    assert page.actions==[]


def test_prepare_permanently_signed_out_auth_page_uses_bounded_sixty_second_budget(clock):
    page=Page(clock);page.url='https://login.live.com/login.srf'
    with pytest.raises(OutlookInboxNeedsLogin):immediate(OutlookInboxReader(page,EMAIL).prepare())
    assert clock.now==60 and page.reads==0 and page.actions==[]


def test_prepare_waits_for_slow_render_and_only_navigates_blank_tabs_to_fixed_outlook(clock):
    page=Page(clock);page.url='about:blank';page.slow_reads=4
    reader=OutlookInboxReader(page,EMAIL)
    immediate(reader.prepare())
    assert reader.prepared and clock.now>=4
    assert page.actions==[('goto','https://outlook.live.com/mail/')]


def test_unrecognized_mailbox_does_not_fail_immediately_or_guess_identity(clock):
    page=Page(clock)
    async def loading(*_):return {'state':'loading'}
    page.evaluate=loading
    with pytest.raises(OutlookInboxNeedsLogin):immediate(OutlookInboxReader(page,EMAIL).prepare())
    assert clock.now==60


@pytest.mark.parametrize('location',[('inbox','focused'),('inbox','other'),('junk',None)])
def test_wait_reads_focused_other_and_junk_from_ui_only(clock,location):
    page=Page(clock,location=location)
    reader=OutlookInboxReader(page,EMAIL)
    assert immediate(reader.wait_for_code(SINCE))==CODE
    assert ('click','[data-cm-outlook-message="0"]') in page.actions
    assert not any(action[0]=='goto' for action in page.actions)


def test_wait_retries_slow_render_and_arrival_with_safe_countdown_messages(clock):
    page=Page(clock,arrival=25,location=('inbox','other'));page.slow_reads=2
    notices=[]
    async def notify(text,remaining):notices.append((text,remaining))
    reader=OutlookInboxReader(page,EMAIL,notify)
    assert immediate(reader.wait_for_code(SINCE))==CODE
    assert clock.now>=25 and 12 in clock.sleeps
    assert notices and all(isinstance(seconds,int) and 0<=seconds<=300 for _,seconds in notices)
    assert all(CODE not in text and EMAIL not in text and 'sensitive-fixture-value' not in text for text,_ in notices)


def test_wait_expires_with_fixed_message_without_resending_or_leaking_raw_errors(clock):
    page=Page(clock,arrival=9999)
    reader=OutlookInboxReader(page,EMAIL)
    with pytest.raises(TimeoutError,match='Không tự yêu cầu gửi lại mã') as error:
        immediate(reader.wait_for_code(SINCE,timeout_seconds=35))
    assert clock.now>=35 and CODE not in str(error.value)
    assert not any('resend' in str(action).lower() for action in page.actions)


def test_same_reader_never_reuses_a_delivered_code_or_message(clock):
    page=Page(clock);reader=OutlookInboxReader(page,EMAIL)
    assert immediate(reader.wait_for_code(SINCE))==CODE
    with pytest.raises(TimeoutError):immediate(reader.wait_for_code(SINCE,timeout_seconds=15))
    assert all(CODE not in repr(value) for value in reader._used_code_hashes)


def test_manual_queue_is_supported_and_never_persisted_or_reused(clock):
    page=Page(clock,arrival=9999);reader=OutlookInboxReader(page,EMAIL)
    queue=asyncio.Queue();queue.put_nowait('87654321')
    assert immediate(reader.wait_for_code(SINCE,manual_queue=queue))=='87654321'
    queue.put_nowait('87654321')
    with pytest.raises(TimeoutError):immediate(reader.wait_for_code(SINCE,timeout_seconds=1,manual_queue=queue))


def test_cancellation_propagates_normally_and_invalid_dom_selectors_are_never_clicked(clock):
    page=Page(clock);reader=OutlookInboxReader(page,EMAIL);reader.prepared=True
    assert immediate(reader._click('a[href="https://example.test/?code=12345678"]')) is False
    assert page.actions==[]
    async def cancelled(*_):raise asyncio.CancelledError()
    page.evaluate=cancelled
    with pytest.raises(asyncio.CancelledError):immediate(reader.wait_for_code(SINCE))


def test_raw_browser_errors_are_replaced_with_fixed_messages(clock):
    page=Page(clock)
    async def closed(*_):raise BrowserError('Target page closed token=sensitive-fixture-value 12345678')
    page.evaluate=closed
    with pytest.raises(OutlookInboxError) as error:immediate(OutlookInboxReader(page,EMAIL).prepare())
    assert 'sensitive-fixture-value' not in str(error.value) and CODE not in str(error.value)


def test_dom_source_is_reread_for_each_snapshot_without_restarting_reader(clock,monkeypatch,tmp_path):
    path=tmp_path/'reader.js';path.write_text('// cookies excluded\n() => ({state:"mailbox",version:1})',encoding='utf-8')
    monkeypatch.setattr('app.services.outlook_inbox.DOM_PATH',path)
    page=Page(clock);sources=[]
    async def inspect(source,_):sources.append(source);return {'state':'mailbox','account_email':EMAIL}
    page.evaluate=inspect;reader=OutlookInboxReader(page,EMAIL)
    immediate(reader._snapshot())
    path.write_text('// cookies excluded\n() => ({state:"mailbox",version:2})',encoding='utf-8')
    immediate(reader._snapshot())
    assert len(sources)==2 and 'version:1' in sources[0] and 'version:2' in sources[1]


def test_recipient_mode_prepares_rendered_mailbox_without_clicking_avatar(clock):
    page=Page(clock,show_account=False)
    reader=OutlookInboxReader(page,EMAIL,require_account=False)
    immediate(reader.prepare())
    assert reader.prepared and page.actions==[] and clock.now==0


def test_prepare_mode_override_is_remembered_without_changing_default_strict_mode(clock):
    page=Page(clock,show_account=False);reader=OutlookInboxReader(page,EMAIL)
    assert reader.require_account is True
    immediate(reader.prepare(require_account=False))
    immediate(reader.prepare())
    assert reader.require_account is False and page.actions==[]


def test_recipient_mode_still_rejects_explicitly_visible_wrong_account_immediately(clock):
    page=Page(clock,account='wrong.fixture@hotmail.com')
    reader=OutlookInboxReader(page,EMAIL,require_account=False)
    with pytest.raises(OutlookInboxWrongAccount):immediate(reader.prepare())
    assert clock.now==0 and page.actions==[]


@pytest.mark.parametrize('recipient,expected',[(EMAIL,CODE),('wrong.fixture@hotmail.com',None)])
def test_recipient_mode_requires_matching_message_to_even_without_account_metadata(clock,recipient,expected):
    page=Page(clock,show_account=False,detail=message(recipients=[recipient]))
    reader=OutlookInboxReader(page,EMAIL,require_account=False)
    if expected:assert immediate(reader.wait_for_code(SINCE,timeout_seconds=15))==expected
    else:
        with pytest.raises(TimeoutError):immediate(reader.wait_for_code(SINCE,timeout_seconds=15))
    assert not any('account' in str(action) for action in page.actions)


def test_recipient_best_effort_baseline_does_not_block_on_auth_or_click_account(clock):
    page=Page(clock);page.url='https://login.live.com/login.srf'
    reader=OutlookInboxReader(page,EMAIL)
    assert immediate(reader.try_capture_baseline()) is None
    assert clock.now==10 and page.actions==[] and reader.prepared is False
    assert reader.require_account is False and reader._baseline_complete is False


def test_best_effort_baseline_bounds_loading_and_folder_scan_with_one_shared_budget(clock):
    page=Page(clock,show_account=False);reader=OutlookInboxReader(page,EMAIL)
    assert immediate(reader.try_capture_baseline(timeout_seconds=1)) is None
    assert clock.now<=1 and not any('account' in str(action) for action in page.actions)
    assert reader._baseline_complete is False


def test_best_effort_baseline_still_reports_a_visible_wrong_account(clock):
    page=Page(clock,account='wrong.fixture@hotmail.com');reader=OutlookInboxReader(page,EMAIL)
    with pytest.raises(OutlookInboxWrongAccount):immediate(reader.try_capture_baseline())
    assert clock.now==0 and page.actions==[]


def test_wait_for_code_includes_preparation_in_its_deadline(clock):
    page=Page(clock);page.url='https://login.live.com/login.srf'
    reader=OutlookInboxReader(page,EMAIL,require_account=False)
    with pytest.raises(OutlookInboxNeedsLogin):immediate(reader.wait_for_code(SINCE,timeout_seconds=3))
    assert clock.now==3 and page.actions==[]


def test_queued_manual_code_works_before_prepare_even_when_outlook_is_logged_out(clock):
    page=Page(clock);page.url='https://login.live.com/login.srf'
    queue=asyncio.Queue();queue.put_nowait('87654321')
    reader=OutlookInboxReader(page,EMAIL,require_account=False)
    assert immediate(reader.wait_for_code(SINCE,manual_queue=queue))=='87654321'
    assert clock.now==0 and page.actions==[] and reader.prepared is False


def test_reader_diagnostics_expose_only_boolean_flags_without_mail_or_code():
    reader=OutlookInboxReader(SimpleNamespace(),EMAIL)
    assert reader._extract({'detail':message()},SINCE)==CODE
    flags=reader.diagnostics
    assert flags and all(type(value) is bool for value in flags.values()) and all(flags.values())
    assert all(secret not in str(flags) for secret in (CODE,EMAIL,'security@facebookmail.com','https://'))
    flags['sender_verified']=False
    assert reader.diagnostics['sender_verified'] is True


def test_reader_diagnostics_distinguish_parsed_future_time_without_guessing_timezone():
    reader=OutlookInboxReader(SimpleNamespace(),EMAIL)
    future=datetime.now(timezone.utc)+timedelta(hours=3)
    assert reader._extract({'detail':message(received_at=future.isoformat())},SINCE) is None
    flags=reader.diagnostics
    assert flags['time_parsed'] is True and flags['time_fresh'] is False
    assert flags['sender_verified'] and flags['recipient_verified'] and flags['body_present']


def test_full_local_ui_timestamp_uses_the_rendered_browser_timezone_offset():
    requested=datetime(2026,10,30,7,12,30,tzinfo=timezone.utc)
    current=requested+timedelta(seconds=10)
    data=message(received_at='Fri 2026-10-30 0:12',received_timezone_offset_minutes=420,received_precision='minute')
    assert extract_facebook_code(data,EMAIL,requested,now=current) is None
    assert extract_facebook_code(data,EMAIL,requested,now=current,allow_minute_rounding=True,first_seen_at=current)==CODE
    del data['received_timezone_offset_minutes']
    assert extract_facebook_code(data,EMAIL,requested,now=current,allow_minute_rounding=True,first_seen_at=current) is None


def test_local_ui_time_with_seconds_is_precise_without_rounding_tolerance():
    requested=datetime(2026,10,30,7,12,30,tzinfo=timezone.utc)
    data=message(received_at='Fri 2026-10-30 0:12:45',received_timezone_offset_minutes=420,received_precision='second')
    assert extract_facebook_code(data,EMAIL,requested,now=requested+timedelta(seconds=20))==CODE


@pytest.mark.parametrize('age,precision,observed_after,expected',[
    (30,'minute',1,CODE),(60,'minute',1,CODE),(61,'minute',1,None),
    (30,'second',1,None),(30,None,1,None),(30,'minute',-1,None),
])
def test_minute_rounding_is_bounded_and_requires_first_observation_after_request(age,precision,observed_after,expected):
    data=message(received_at=(SINCE-timedelta(seconds=age)).isoformat(),received_precision=precision)
    result=extract_facebook_code(data,EMAIL,SINCE,now=NOW,allow_minute_rounding=True,first_seen_at=SINCE+timedelta(seconds=observed_after))
    assert result==expected


def prepare_baseline_reader(clock,monkeypatch):
    monkeypatch.setattr('app.services.outlook_inbox._utc_now',lambda:SINCE+timedelta(seconds=clock.now))
    page=Page(clock,detail=message(received_at=SINCE.isoformat(),received_precision='minute'))
    reader=OutlookInboxReader(page,EMAIL)
    return page,reader


def replace_with_new_code(page):
    page.message=message(subject='87654321 is your Facebook confirmation code',body='Your Facebook security code is 87654321.',received_at=SINCE.isoformat(),received_precision='minute')


def test_capture_baseline_allows_only_new_message_subject_in_same_outlook_conversation(clock,monkeypatch):
    page,reader=prepare_baseline_reader(clock,monkeypatch)
    captured=immediate(reader.capture_baseline())
    requested=SINCE+timedelta(seconds=30)
    assert captured and captured<requested and reader._baseline_complete
    assert any('folder="1"' in str(action) for action in page.actions)
    assert any('tab="1"' in str(action) for action in page.actions)
    replace_with_new_code(page);clock.now=35
    assert reader._extract({'detail':page.message},requested)=='87654321'
    assert reader._extract({'detail':page.message},requested) is None
    assert all(CODE not in value and '87654321' not in value for value in reader._baseline_messages)


def test_message_already_in_pre_request_baseline_remains_old_even_within_rounded_minute(clock,monkeypatch):
    page,reader=prepare_baseline_reader(clock,monkeypatch)
    immediate(reader.capture_baseline());clock.now=35
    assert reader._extract({'detail':page.message},SINCE+timedelta(seconds=30)) is None


def test_baseline_captured_after_request_never_authorizes_rounded_old_mail(clock,monkeypatch):
    page,reader=prepare_baseline_reader(clock,monkeypatch);clock.now=35
    immediate(reader.capture_baseline());replace_with_new_code(page);clock.now=40
    assert reader._extract({'detail':page.message},SINCE+timedelta(seconds=30)) is None


def test_incomplete_baseline_never_authorizes_minute_rounding(clock,monkeypatch):
    page,reader=prepare_baseline_reader(clock,monkeypatch)
    original=page.evaluate
    async def missing_junk(*args):
        data=await original(*args);data['folders']=data['folders'][:1];return data
    page.evaluate=missing_junk
    assert immediate(reader.capture_baseline()) is None
    replace_with_new_code(page);clock.now=35
    assert reader._extract({'detail':page.message},SINCE+timedelta(seconds=30)) is None


def test_baseline_with_unknown_subject_fails_closed_for_that_conversation(clock,monkeypatch):
    page,reader=prepare_baseline_reader(clock,monkeypatch)
    original=page.evaluate
    async def no_subject(*args):
        data=await original(*args)
        for item in data['items']:item['subject']=''
        return data
    page.evaluate=no_subject
    immediate(reader.capture_baseline());replace_with_new_code(page);clock.now=35
    assert reader._extract({'detail':page.message},SINCE+timedelta(seconds=30)) is None
