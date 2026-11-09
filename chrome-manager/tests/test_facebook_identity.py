"""Rendered identity switching: exact targets, ambiguity stops and confirmation."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services.facebook_identity import (
    FacebookIdentityError,ensure_personal_identity,switch_to_page_identity,
)
from app.services import facebook_identity as identity_module


@pytest.fixture(autouse=True)
def diagnostic_folder(tmp_path,monkeypatch):
    monkeypatch.setattr(identity_module,'DIAGNOSTICS_ROOT',tmp_path/'facebook-identity')


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O')


def state(**values):
    return {'blocked':False,'ready':True,'acting_page':False,'current_name':'','current_url':'','controls':[],'candidates':[],**values}


class Page:
    def __init__(self,*states,url='https://www.facebook.com/me'):
        self.states=list(states);self.current=self.states[-1];self.url=url;self.actions=[];self.keyboard=self
    async def evaluate(self,*_):
        if self.states:self.current=self.states.pop(0)
        return deepcopy(self.current)
    async def wait_for_timeout(self,_):pass
    def locator(self,selector):
        page=self
        class Locator:
            async def click(self,**_):page.actions.append(('click',selector))
        return Locator()
    async def press(self,key):self.actions.append(('key',key))
    async def goto(self,url,**_):self.actions.append(('goto',url));self.url=url


def entry(name='Account One',url='https://www.facebook.com/profile.php?id=111',**values):
    return {'id':'10','name':name,'url':url,'kind':'unknown','selected':False,**values}


def test_bound_personal_identity_is_not_changed():
    page=Page(state(current_name='Account One',current_url='https://www.facebook.com/profile.php?id=111'))
    result=immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert result['kind']=='personal' and result['switched'] is False and page.actions==[]


def test_loading_skeleton_is_never_returned_as_personal_identity():
    page=Page(state(ready=False),state(ready=False,current_url='https://www.facebook.com/profile.php?id=111'),
              state(current_name='Account One',current_url='https://www.facebook.com/profile.php?id=111'))
    result=immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert result['name']=='Account One' and page.actions==[] and not page.states


def test_permanent_loading_skeleton_pauses_without_opening_any_picker():
    page=Page(state(ready=False))
    with pytest.raises(FacebookIdentityError,match='chưa tải đủ'):immediate(ensure_personal_identity(page))
    assert page.actions==[]


@pytest.mark.parametrize('bound',[False,True])
def test_ready_home_shell_without_current_identity_is_not_personal_confirmation(bound):
    page=Page(state())
    with pytest.raises(FacebookIdentityError):
        immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111' if bound else None))
    assert page.actions==[]


def test_page_identity_switches_through_account_and_profile_picker():
    page=Page(state(acting_page=True,controls=[{'id':'0','kind':'menu','label':'Your profile'}]),
              state(acting_page=True,controls=[{'id':'1','kind':'picker','label':'See all profiles'}]),
              state(acting_page=True,candidates=[entry(),entry(name='Different account',url='https://www.facebook.com/profile.php?id=222',id='11')]),
              state(ready=False),state(current_name='Account One',current_url='https://www.facebook.com/profile.php?id=111'))
    result=immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert result['switched'] and result['url']=='https://www.facebook.com/profile.php?id=111'
    assert [item[1] for item in page.actions]==['[data-cm-identity="0"]','[data-cm-identity="1"]','[data-cm-identity="10"]','https://www.facebook.com/me']


def test_explicit_personal_row_is_usable_when_no_prior_personal_identity_is_known():
    page=Page(state(acting_page=True,candidates=[entry(name='Your profile',url='',kind='personal')]),
              state(current_name='Actual personal name',current_url='https://www.facebook.com/profile.php?id=111'))
    result=immediate(ensure_personal_identity(page))
    assert result['switched'] and result['name']=='Actual personal name'


def test_picker_can_finish_loading_without_reclicking_the_menu():
    page=Page(state(acting_page=True,controls=[{'id':'0','kind':'menu','label':'Facebook Menu'}]),
              state(ready=False),state(acting_page=True,candidates=[entry()]),state(current_name='Account One',current_url='https://www.facebook.com/profile.php?id=111'))
    result=immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert result['switched'] and len(page.actions)==3


def test_known_page_coachmark_is_closed_before_any_identity_candidate_or_menu():
    tip={'id':'99','kind':'dismiss_tip','label':'Close'}
    menu={'id':'0','kind':'menu','label':'Your profile'}
    page=Page(state(acting_page=True,controls=[menu,tip],candidates=[entry()]),
              state(acting_page=True,controls=[menu]),state(acting_page=True,candidates=[entry()]),
              state(current_name='Account One',current_url='https://www.facebook.com/profile.php?id=111'))
    result=immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert result['kind']=='personal'
    assert page.actions[:3]==[('click','[data-cm-identity="99"]'),('click','[data-cm-identity="0"]'),('click','[data-cm-identity="10"]')]


def test_page_coachmark_dismissal_is_bounded_and_never_force_clicks_menu():
    page=Page(state(acting_page=True,controls=[{'id':'99','kind':'dismiss_tip','label':'Close'},{'id':'0','kind':'menu','label':'Your profile'}]))
    with pytest.raises(FacebookIdentityError,match='Hướng dẫn'):immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert page.actions==[('click','[data-cm-identity="99"]')]*3


def test_viewing_bound_personal_profile_while_actor_remains_page_is_not_a_switch():
    page=Page(state(acting_page=True,candidates=[entry()]),
              state(current_name='Account One',current_url='https://www.facebook.com/profile.php?id=111'),
              state(acting_page=True,current_name='Active Page',current_url='https://www.facebook.com/profile.php?id=222'))
    with pytest.raises(FacebookIdentityError):immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert page.actions[-1]==('goto','https://www.facebook.com/me')


def test_switch_does_not_return_cached_or_picker_identity_when_me_stays_blank():
    page=Page(state(acting_page=True,candidates=[entry()]),state())
    with pytest.raises(FacebookIdentityError):immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))


def test_profile_with_same_name_and_conflicting_url_is_not_selected():
    page=Page(state(acting_page=True,candidates=[entry(url='https://www.facebook.com/profile.php?id=999')]))
    with pytest.raises(FacebookIdentityError):immediate(ensure_personal_identity(page,'https://www.facebook.com/profile.php?id=111','Account One'))
    assert page.actions==[]


@pytest.mark.parametrize('bound',[False,True])
def test_ambiguous_profiles_stop_before_any_identity_click(bound):
    page=Page(state(acting_page=True,candidates=[entry(kind='personal'),entry(url='https://www.facebook.com/profile.php?id=222',kind='personal',id='11')]))
    with pytest.raises(FacebookIdentityError,match='nhiều'):
        immediate(ensure_personal_identity(page,expected_name='Account One' if bound else None))
    assert page.actions==[]


def test_login_checkpoint_stops_before_interacting():
    page=Page({'blocked':True})
    with pytest.raises(FacebookIdentityError,match='đăng nhập'):immediate(ensure_personal_identity(page))
    assert page.actions==[]


def test_current_correct_page_needs_no_second_switch():
    page=Page(state(acting_page=True,current_name='Target Page',current_url='https://www.facebook.com/profile.php?id=555'))
    result=immediate(switch_to_page_identity(page,'https://m.facebook.com/profile.php?id=555&ref=one','Target Page'))
    assert result['switched'] is False and page.actions==[]


def test_page_proof_is_rechecked_after_navigation_before_duplicate_candidates():
    target='https://www.facebook.com/profile.php?id=555'
    page=Page(state(acting_page=True,current_name='Another Page',current_url='https://www.facebook.com/profile.php?id=999'),
              state(acting_page=True,current_name='Target Page',current_url=target,candidates=[entry(name='Target Page',url=target),entry(name='Target Page link',url=target,id='11')]),url='https://www.facebook.com/profile.php?id=999')
    result=immediate(switch_to_page_identity(page,target,'Target Page'))
    assert result['switched'] is False and page.actions==[('goto',target)]


def test_page_loading_shell_waits_for_acting_page_instead_of_opening_account_menu():
    target='https://www.facebook.com/profile.php?id=555'
    page=Page(state(controls=[{'id':'0','kind':'menu','label':'Votre profil'}]),
              state(acting_page=True,current_name='Target Page',current_url=target),url=target)
    result=immediate(switch_to_page_identity(page,target,'Target Page'))
    assert result['switched'] is False and page.actions==[]


def test_picker_refresh_stops_when_correct_page_finishes_switching():
    target='https://www.facebook.com/profile.php?id=555'
    shell=state(controls=[{'id':'0','kind':'menu','label':'Votre profil'}])
    actual=state(acting_page=True,current_name='Target Page',current_url=target,candidates=[entry(name='Target Page',url=target),entry(name='Target Page',url=target,id='11')])
    page=Page(*([shell]*20),actual,url=target)
    result=immediate(switch_to_page_identity(page,target,'Target Page'))
    assert result['switched'] is False and page.actions==[('click','[data-cm-identity="0"]')]


def test_page_switch_now_is_only_reported_after_acting_page_and_target_are_visible():
    target='https://www.facebook.com/profile.php?id=555'
    page=Page(state(),state(controls=[{'id':'4','kind':'switch_page','label':'Switch now'}]),
              state(ready=False),state(acting_page=True,current_name='Target Page',current_url=target))
    result=immediate(switch_to_page_identity(page,target,'Target Page'))
    assert result['kind']=='page' and result['switched'] is True
    assert page.actions==[('goto',target),('click','[data-cm-identity="4"]')]


def test_switch_button_without_target_confirmation_does_not_report_success():
    target='https://www.facebook.com/profile.php?id=555'
    page=Page(state(controls=[{'id':'4','kind':'switch_page','label':'Switch now'}]),
              state(acting_page=True,current_name='Wrong Page',current_url='https://www.facebook.com/profile.php?id=999'),url=target)
    with pytest.raises(FacebookIdentityError,match='Chưa xác nhận'):immediate(switch_to_page_identity(page,target,'Target Page'))
    assert page.actions==[('click','[data-cm-identity="4"]')]


@pytest.mark.parametrize('target',['https://facebook.com.evil.test/Page','http://facebook.com/Page','https://www.facebook.com/login'])
def test_non_page_destinations_are_rejected(target):
    page=Page(state())
    with pytest.raises(FacebookIdentityError,match='URL'):immediate(switch_to_page_identity(page,target))
    assert page.actions==[]


def test_raw_click_error_never_reaches_status_message_or_diagnostic_files():
    class FailedPage(Page):
        def locator(self,selector):
            class Locator:
                mask_selector=selector
                async def click(self,**_):raise RuntimeError('fixture-password-and-token')
            return Locator()
        async def screenshot(self,**kwargs):
            self.capture=kwargs
            Path(kwargs['path']).write_bytes(b'masked-offline-fixture')
    page=FailedPage(state(acting_page=True,candidates=[entry()]))
    with pytest.raises(FacebookIdentityError) as error:immediate(ensure_personal_identity(page,expected_name='Account One'))
    assert 'fixture-password' not in str(error.value) and 'token' not in str(error.value)
    diagnostic=identity_module.DIAGNOSTICS_ROOT/'click_failed.json'
    assert json.loads(diagnostic.read_text())=={'kind':'unknown','error_category':'other'}
    assert not any(value in diagnostic.read_text() for value in ('fixture-password','token','Account One','facebook.com'))
    assert page.capture['mask'][0].mask_selector=='input,textarea,[contenteditable]'
    assert page.capture['full_page'] is False and page.capture['timeout']==5000
    assert Path(page.capture['path']).name=='click_failed.png'


@pytest.mark.parametrize('message,category',[
    ('overlay intercepts pointer events','pointer_intercepted'),
    ('Element is not attached to the DOM','detached_or_missing'),
    ('Target page, context or browser has been closed','closed'),
    ('Timeout 5000ms exceeded','timeout'),
])
def test_click_diagnostic_stores_only_fixed_error_categories(message,category):
    assert identity_module._click_error_category(RuntimeError(message+' fixture-private-token'))==category


@pytest.mark.parametrize('url',['https://outlook.live.com/mail/','https://www.facebook.com.evil.test/Page','http://www.facebook.com/Page','about:blank'])
def test_identity_failure_never_captures_other_sites(url):
    class FailedPage(Page):
        def locator(self,_):
            class Locator:
                async def click(self,**_):raise RuntimeError('fixture-private-token')
            return Locator()
        async def screenshot(self,**_):raise AssertionError('No other site may be captured')
    page=FailedPage(state(acting_page=True,candidates=[entry()]),url=url)
    with pytest.raises(FacebookIdentityError):immediate(ensure_personal_identity(page,expected_name='Account One'))
    assert not identity_module.DIAGNOSTICS_ROOT.exists()


def test_failed_capture_preserves_safe_error_and_removes_stale_screenshot():
    folder=identity_module.DIAGNOSTICS_ROOT;folder.mkdir()
    (folder/'click_failed.png').write_bytes(b'old-fixture')
    class FailedPage(Page):
        def locator(self,_):
            class Locator:
                async def click(self,**_):raise RuntimeError('overlay intercepts pointer events fixture-private-token')
            return Locator()
        async def screenshot(self,**_):raise RuntimeError('fixture-private-capture-error')
    page=FailedPage(state(acting_page=True,candidates=[entry()]))
    with pytest.raises(FacebookIdentityError) as error:immediate(ensure_personal_identity(page,expected_name='Account One'))
    assert 'fixture-private' not in str(error.value)
    assert not (folder/'click_failed.png').exists()
    assert json.loads((folder/'click_failed.json').read_text())=={'kind':'unknown','error_category':'pointer_intercepted'}


def test_cancellation_propagates_instead_of_becoming_identity_error():
    class CancelledPage(Page):
        def locator(self,_):
            class Locator:
                async def click(self,**_):raise asyncio.CancelledError()
            return Locator()
    page=CancelledPage(state(acting_page=True,candidates=[entry()]))
    with pytest.raises(asyncio.CancelledError):immediate(ensure_personal_identity(page,expected_name='Account One'))
