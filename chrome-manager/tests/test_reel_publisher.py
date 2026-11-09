"""Scheduled Reel protocol fixtures. Never opens a saved profile or publishes."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.services import reel_publisher as protocol
from app.services.reel_publisher import ReelPublisher,ReelNeedsUser

TARGET='https://www.facebook.com/profile.php?id=555'


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in offline fixture')


def state(**extra):
    return {'blocked':False,'reel_context':False,'buttons':[],'files':[],'captions':[],'processing':False,
            'upload_ready':False,'actor_matches':False,'published':False,'published_urls':[],'current_reel_url':'',
            'reel_video_visible':False,'reel_video_count':0,**extra}


def button(kind,id=None,enabled=True):return {'id':id or kind,'kind':kind,'label':kind,'enabled':enabled}


def editor(**extra):
    return state(reel_context=True,upload_ready=True,actor_matches=True,buttons=[button('publish')],captions=[{'id':'caption','matches':True}],**extra)


class Page:
    def __init__(self,*states,fail_publish=False):
        self.states=list(states);self.last=self.states[-1] if self.states else state();self.url=TARGET
        self.actions=[];self.fail_publish=fail_publish;self.identity=[];self.read_modes=[];self.discovery_modes=[]
    async def evaluate(self,_source,arg=None):
        if arg is None:return self.identity.pop(0) if self.identity else {'blocked':False,'controls':[]}
        self.read_modes.append(arg.get('verificationOnly') is True)
        self.discovery_modes.append(arg.get('discoverPublishedReel') is True)
        if self.states:self.last=self.states.pop(0)
        return deepcopy(self.last)
    async def goto(self,url,**_):self.url=url;self.actions.append(('goto',url))
    async def wait_for_timeout(self,_):pass
    def locator(self,selector):
        page=self
        class Locator:
            async def click(self,**_):
                page.actions.append(('click',selector))
                if page.fail_publish and selector=='[data-cm-reel="publish"]':raise RuntimeError('fixture-private-token')
            async def fill(self,value,**_):page.actions.append(('fill',value))
            async def set_input_files(self,value,**_):page.actions.append(('upload',value))
        return Locator()


@pytest.fixture
def factory(tmp_path,monkeypatch):
    video=tmp_path/'chosen.mp4';video.write_bytes(b'offline-video-fixture')
    async def switch(page,url,name):page.actions.append(('actor',url,name));return {'kind':'page','url':url,'name':name}
    monkeypatch.setattr(protocol,'switch_to_page_identity',switch)
    def make(page,**kwargs):
        checkpoints=[];diagnostics=[];notices=[];all_checkpoints=[]
        def checkpoint(value):
            all_checkpoints.append(deepcopy(value))
            if set(value)=={'diagnostics'}:
                diagnostics.append(value['diagnostics'])
            else:
                checkpoints.append(value);page.actions.append(('checkpoint',deepcopy(value)))
        publisher=ReelPublisher(page,TARGET,'Fixture Page',video,'Fixture caption',kwargs.pop('notify',lambda stage,message:notices.append(stage)),kwargs.pop('checkpoint',checkpoint),posting_guard=kwargs.pop('posting_guard',lambda:True),**kwargs)
        publisher.PROCESSING_ATTEMPTS=3;publisher.VERIFICATION_ATTEMPTS=3;publisher.UI_ATTEMPTS=3
        publisher.saved=checkpoints;publisher.diagnostics=diagnostics;publisher.all_checkpoints=all_checkpoints;publisher.notices=notices
        return publisher
    return make


def initial():return state(buttons=[button('create')])
def composer():return state(reel_context=True,files=[{'id':'video'}])
def published():return state(published=True,published_urls=['https://m.facebook.com/reel/123456?tracking=removed'],current_reel_url='https://www.facebook.com/reel/123456',reel_video_visible=True,reel_video_count=1)


def test_upload_caption_durable_checkpoint_then_single_publish_and_verified_success(factory):
    unfilled=editor();unfilled['captions'][0]['matches']=False
    page=Page(initial(),composer(),state(reel_context=True,processing=True),unfilled,editor(),editor(),editor(),published())
    publisher=factory(page)
    assert immediate(publisher.run())=={'url':'https://www.facebook.com/reel/123456','evidence':'visible_reel_video','page_url':TARGET}
    assert [action[0] for action in page.actions]==['goto','actor','click','upload','fill','checkpoint','click']
    assert page.actions[-1]==('click','[data-cm-reel="publish"]')
    assert publisher.saved[0]['publish_attempted'] is True and publisher.saved[0]['page_url']==TARGET
    assert len([action for action in page.actions if action==('click','[data-cm-reel="publish"]')])==1
    assert {'opening','uploading','processing','caption','publishing','verifying'}<=set(publisher.notices)
    assert page.read_modes[-1] is True and all(mode is False for mode in page.read_modes[:-1]),'Only post-publish reconciliation requests the light DOM path'


def test_next_step_uses_same_video_and_does_not_reupload(factory):
    next_step=state(reel_context=True,upload_ready=True,buttons=[button('next')])
    page=Page(initial(),composer(),next_step,next_step,editor(),editor(),editor(),published())
    result=immediate(factory(page).run())
    assert result['evidence']=='visible_reel_video'
    assert len([action for action in page.actions if action[0]=='upload'])==1
    assert ('click','[data-cm-reel="next"]') in page.actions


def test_wrong_composer_actor_prevents_checkpoint_and_publish(factory):
    wrong=editor();wrong['actor_matches']=False
    page=Page(initial(),composer(),wrong,wrong)
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='tên Page'):immediate(publisher.run())
    assert publisher.saved==[] and ('click','[data-cm-reel="publish"]') not in page.actions


def test_identity_change_after_checkpoint_prevents_publish_without_resetting_attempt(factory):
    wrong=editor();wrong['actor_matches']=False
    page=Page(initial(),composer(),editor(),editor(),wrong)
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='danh tính'):immediate(publisher.run())
    assert publisher.saved[0]['publish_attempted'] and publisher.publish_attempted
    assert ('click','[data-cm-reel="publish"]') not in page.actions


@pytest.mark.parametrize('reject',[False,RuntimeError('fixture-private-code')])
def test_checkpoint_must_succeed_before_publish(factory,reject):
    page=Page(initial(),composer(),editor(),editor())
    def checkpoint(_):
        if isinstance(reject,Exception):raise reject
        return reject
    publisher=factory(page,checkpoint=checkpoint)
    with pytest.raises(ReelNeedsUser) as error:immediate(publisher.run())
    assert 'fixture-private' not in str(error.value)
    assert ('click','[data-cm-reel="publish"]') not in page.actions and not publisher.publish_attempted


def test_publish_timeout_reconciles_instead_of_clicking_again(factory):
    page=Page(initial(),composer(),editor(),editor(),editor(),published(),fail_publish=True)
    assert immediate(factory(page).run())['url']=='https://www.facebook.com/reel/123456'
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1


def candidate(url='https://www.facebook.com/reel/123456',count=1):
    return state(published_reel_candidate_url=url,published_reel_candidate_count=count)


def test_fresh_publish_notice_opens_observed_reel_in_same_tab_then_checks_video(factory):
    page=Page(initial(),composer(),editor(),editor(),editor(),candidate(),published())
    publisher=factory(page);proof=immediate(publisher.run())
    assert proof=={'url':'https://www.facebook.com/reel/123456','evidence':'visible_reel_video','page_url':TARGET}
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1
    assert [item for item in page.actions if item[0]=='goto']==[('goto',TARGET),('goto','https://www.facebook.com/reel/123456')]
    assert page.read_modes[-2:]==[True,True] and page.discovery_modes[-2:]==[True,False]
    assert publisher.publish_attempted and publisher.saved[0]['publish_attempted'] is True


@pytest.mark.parametrize('late_notice',[False,True])
def test_notice_already_visible_before_publish_cannot_be_discovered(factory,late_notice):
    stale=editor(published=True)
    before=[editor(),editor(),stale] if late_notice else [stale,stale,stale]
    page=Page(initial(),composer(),*before,candidate())
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='Dán link'):immediate(publisher.run())
    assert publisher.success_was_visible is True and publisher.publish_attempted is True
    assert not any(page.discovery_modes)
    assert [item for item in page.actions if item[0]=='goto']==[('goto',TARGET)]
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1


@pytest.mark.parametrize('prior',[
    {'publish_attempted':True,'page_url':TARGET},
    {'publish_attempted':True,'page_url':TARGET,'verification_url':'https://www.facebook.com/reel/777'},
])
def test_resumed_or_supplied_link_verification_never_discovers_another_notice(factory,prior):
    page=Page(candidate());publisher=factory(page,prior_checkpoint=prior)
    with pytest.raises(ReelNeedsUser,match='Dán link'):immediate(publisher.run())
    assert page.actions==[] and not any(page.discovery_modes) and publisher.publish_attempted


@pytest.mark.parametrize('url,count',[
    ('https://www.facebook.com/reel/123456',2),
    ('https://www.facebook.com/reel/123456',True),
    ('https://evil.test/reel/123456',1),
    ('https://www.facebook.com:444/reel/123456',1),
    ('https://user:pass@www.facebook.com/reel/123456',1),
])
def test_ambiguous_or_invalid_notice_candidate_never_causes_navigation_or_republish(factory,url,count):
    page=Page(initial(),composer(),editor(),editor(),editor(),candidate(url,count));publisher=factory(page)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert [item for item in page.actions if item[0]=='goto']==[('goto',TARGET)]
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1 and publisher.publish_attempted


def test_discovered_reel_navigation_happens_once_and_redirect_cannot_verify_another_id(factory):
    wrong=published();wrong['current_reel_url']='https://www.facebook.com/reel/999'
    page=Page(initial(),composer(),editor(),editor(),editor(),candidate(),wrong);publisher=factory(page)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert [item for item in page.actions if item[0]=='goto']==[('goto',TARGET),('goto','https://www.facebook.com/reel/123456')]
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1 and publisher.publish_attempted


def test_discovered_link_is_not_reopened_when_video_does_not_load(factory):
    page=Page(initial(),composer(),editor(),editor(),editor(),candidate(),candidate());publisher=factory(page)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert page.actions.count(('goto','https://www.facebook.com/reel/123456'))==1
    assert page.discovery_modes[-2:]==[False,False]
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1


def test_verification_budget_is_separate_from_long_upload_processing_budget(factory):
    page=Page(state());publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    publisher.PROCESSING_ATTEMPTS=600;publisher.VERIFICATION_ATTEMPTS=2
    with pytest.raises(ReelNeedsUser,match='Dán link'):immediate(publisher.run())
    assert page.read_modes==[True,True] and page.actions==[] and publisher.publish_attempted


def test_no_visible_success_after_publish_is_uncertain_and_never_retried(factory):
    page=Page(initial(),composer(),editor(),editor(),editor(),state())
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='tránh trùng'):immediate(publisher.run())
    assert publisher.publish_attempted and page.actions.count(('click','[data-cm-reel="publish"]'))==1


def test_resume_after_publish_attempt_is_verification_only(factory):
    page=Page(published())
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    assert immediate(publisher.run())['evidence']=='visible_reel_video'
    assert page.actions==[] and publisher.saved==[] and page.read_modes==[True]


def test_resume_unknown_publication_never_uploads_switches_or_publishes(factory):
    page=Page(state())
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert page.actions==[]


def test_resume_checkpoint_cannot_be_used_for_another_page(factory):
    page=Page(published())
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':'https://www.facebook.com/profile.php?id=999'})
    with pytest.raises(ReelNeedsUser,match='không khớp'):immediate(publisher.run())
    assert page.actions==[]


def test_stale_published_toast_before_publish_does_not_count_as_new_success(factory):
    stale=editor();stale['published']=True
    page=Page(initial(),composer(),stale,stale,stale,state(published=True))
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert publisher.saved[0]['success_was_visible'] is True
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1


@pytest.mark.parametrize('matching',[True,False])
def test_post_publish_video_proof_does_not_require_reading_author_or_caption(factory,matching):
    page=Page(state(current_reel_url='https://www.facebook.com/reel/987?ref=test',reel_video_visible=True,reel_video_count=1,actor_matches=matching,published_caption_matches=matching))
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    assert immediate(publisher.run())=={'url':'https://www.facebook.com/reel/987','evidence':'visible_reel_video','page_url':TARGET}
    assert page.actions==[] and page.read_modes==[True]


@pytest.mark.parametrize('value',['https://evil.test/reel/123','https://facebook.com.evil.test/reel/123','http://www.facebook.com/reel/123','https://www.facebook.com/reel/not-id','https://user:pass@www.facebook.com/reel/123'])
def test_nonofficial_reel_links_are_not_publication_proof(factory,value):
    page=Page(state(current_reel_url=value,reel_video_visible=True,reel_video_count=1,actor_matches=True,published_caption_matches=True))
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert page.actions==[]


@pytest.mark.parametrize('value',[
    'https://www.facebook.com:444/reel/123456',
    'https://www.facebook.com:0/reel/123456',
    'https://www.facebook.com:65536/reel/123456',
    'https://www.facebook.com:invalid/reel/123456',
    'https://www.facebook.com:/reel/123456',
    'https://fixture-private-user:fixture-private-password@www.facebook.com/reel/123456',
    'https://@www.facebook.com/reel/123456',
])
def test_verification_refuses_untrusted_current_origin_before_reading_any_dom(factory,value):
    class UntrustedPage(Page):
        async def evaluate(self,*_,**__):raise AssertionError('Untrusted origin must never be evaluated')
    page=UntrustedPage();page.url=value
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    with pytest.raises(ReelNeedsUser,match='chuyển sang trang khác') as error:immediate(publisher.run())
    assert 'fixture-private' not in str(error.value)
    assert publisher.publish_attempted is True and publisher.saved==[] and page.actions==[]


@pytest.mark.parametrize('value',[
    'https://www.facebook.com:444/reel/123456',
    'https://m.facebook.com:80/reel/123456',
    'https://www.facebook.com:65536/reel/123456',
    'https://www.facebook.com:invalid/reel/123456',
    'https://www.facebook.com:/reel/123456',
    'https://@www.facebook.com/reel/123456',
])
def test_nondefault_or_invalid_reel_origin_never_becomes_canonical_publication_proof(factory,value):
    page=Page(state(current_reel_url=value,reel_video_visible=True,reel_video_count=1,actor_matches=True,published_caption_matches=True))
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert publisher.publish_attempted is True and page.actions==[]
    assert publisher._reel_url(value) is None


@pytest.mark.parametrize('value',[
    'https://www.facebook.com/reel/123456',
    'https://www.facebook.com:443/reel/123456?tracking=removed',
    'https://m.facebook.com:443/reels/123456/',
    'https://facebook.com:443/reel/123456',
])
def test_default_https_and_explicit_443_current_origin_keep_verified_permalink_support(factory,value):
    page=Page(state(current_reel_url=value,reel_video_visible=True,reel_video_count=1,actor_matches=True,published_caption_matches=True));page.url=value
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    assert immediate(publisher.run())=={'url':'https://www.facebook.com/reel/123456','evidence':'visible_reel_video','page_url':TARGET}
    assert page.actions==[] and publisher.publish_attempted is True


@pytest.mark.parametrize('visible,count',[(False,1),(True,0),(True,2),(True,True),(True,'1')])
def test_light_verification_needs_exactly_one_visible_video_not_toast_author_or_caption(factory,visible,count):
    page=Page(state(current_reel_url='https://www.facebook.com/reel/123456',reel_video_visible=visible,reel_video_count=count,
                    actor_matches=True,published_caption_matches=True,published=True,published_urls=['https://www.facebook.com/reel/123456']))
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert page.actions==[] and page.read_modes==[True]*publisher.PROCESSING_ATTEMPTS


@pytest.mark.parametrize('supplied,previous,accepted',[
    (None,None,True),
    (None,'https://www.facebook.com/reel/123456',False),
    ('https://www.facebook.com/reel/123456','https://www.facebook.com/reel/123456',True),
    ('https://www.facebook.com/reel/999',None,False),
    ('https://www.facebook.com:444/reel/123456',None,False),
])
def test_light_video_proof_is_bound_to_supplied_url_or_new_unsupplied_reel(factory,supplied,previous,accepted):
    page=Page(published());prior={'publish_attempted':True,'page_url':TARGET,'previous_reel_url':previous}
    if supplied is not None:prior['verification_url']=supplied
    publisher=factory(page,prior_checkpoint=prior);publisher.caption='';publisher.page_name=None
    if accepted:assert immediate(publisher.run())['evidence']=='visible_reel_video'
    else:
        with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert page.actions==[] and all(page.read_modes)


def test_light_video_evidence_cannot_be_used_before_durable_publish_attempt(factory):
    publisher=factory(Page(published()))
    assert publisher.publish_attempted is False and publisher._proof(published()) is None
    immediate(publisher._read())
    assert publisher.page.read_modes==[False],'Ordinary reads keep full pre-publish Page and caption checks'


def test_light_verification_requires_canonical_reel_route_with_ascii_id(factory):
    page=Page(state(current_reel_url='https://www.facebook.com/reel/１２３４５６',reel_video_visible=True,reel_video_count=1))
    publisher=factory(page,prior_checkpoint={'publish_attempted':True,'page_url':TARGET})
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert page.actions==[]


def test_processing_timeout_leaves_video_unpublished(factory):
    page=Page(initial(),composer(),state(reel_context=True,processing=True))
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='10 phút'):immediate(publisher.run())
    assert publisher.saved==[] and ('click','[data-cm-reel="publish"]') not in page.actions


def test_login_checkpoint_pauses_before_any_upload(factory):
    page=Page(state(blocked=True))
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='đăng nhập'):immediate(publisher.run())
    assert not any(action[0] in ('upload','checkpoint') for action in page.actions)


def test_existing_known_page_guide_is_closed_before_create_reel(factory):
    page=Page(initial(),composer(),editor(),editor(),editor(),published())
    page.identity=[{'controls':[{'id':'tip','kind':'dismiss_tip'}]},{'controls':[]}]
    immediate(factory(page).run())
    assert page.actions.index(('click','[data-cm-identity="tip"]'))<page.actions.index(('click','[data-cm-reel="create"]'))


def test_cancellation_during_durable_checkpoint_propagates_without_publish(factory):
    page=Page(initial(),composer(),editor(),editor())
    async def checkpoint(_):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):immediate(factory(page,checkpoint=checkpoint).run())
    assert ('click','[data-cm-reel="publish"]') not in page.actions


def test_caption_cannot_change_between_fill_and_publish(factory):
    changed=editor();changed['captions'][0]['matches']=False
    page=Page(initial(),composer(),editor(),changed)
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='thay đổi'):immediate(publisher.run())
    assert publisher.saved==[]


def test_file_chooser_uploads_only_the_selected_video_once(factory):
    choose=state(reel_context=True,buttons=[button('upload')])
    class ChooserPage(Page):
        def expect_file_chooser(self,**_):
            page=self
            class Chooser:
                async def set_files(self,value,**_):page.actions.append(('upload',value))
            class Pending:
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
                @property
                def value(self):
                    async def result():return Chooser()
                    return result()
            return Pending()
    page=ChooserPage(initial(),choose,editor(),editor(),editor(),published())
    publisher=factory(page)
    immediate(publisher.run())
    assert page.actions.count(('click','[data-cm-reel="upload"]'))==1
    assert [action[1] for action in page.actions if action[0]=='upload']==[str(publisher.video_path.resolve())]


def test_async_notify_and_checkpoint_are_awaited_before_publish(factory):
    page=Page(initial(),composer(),editor(),editor(),editor(),published());notices=[]
    async def notify(stage,message):notices.append(stage)
    async def checkpoint(value):page.actions.append(('durably_saved',value));return True
    immediate(factory(page,notify=notify,checkpoint=checkpoint).run())
    saved=next(index for index,value in enumerate(page.actions) if value[0]=='durably_saved')
    click=page.actions.index(('click','[data-cm-reel="publish"]'))
    assert saved<click and 'verifying' in notices


def test_optional_page_name_uses_name_from_verified_page_identity(factory,monkeypatch):
    class ObservedPage(Page):
        async def evaluate(self,source,arg=None):
            if arg is not None:
                assert arg['pageName']=='Observed Page','Every composer read uses the verified observed name'
            return await super().evaluate(source,arg)
    async def switch(page,url,name):
        assert url==TARGET and name is None
        return {'kind':'page','url':url,'name':'Observed Page'}
    monkeypatch.setattr(protocol,'switch_to_page_identity',switch)
    page=ObservedPage(initial(),composer(),editor(),editor(),editor(),published())
    publisher=factory(page);publisher.page_name=None
    assert immediate(publisher.run())['evidence']=='visible_reel_video'
    assert publisher.page_name=='Observed Page'


def test_typed_identity_error_preserves_safe_message_and_reports_switching(factory,monkeypatch):
    message='Facebook chưa tải đủ giao diện để xác nhận danh tính. Chờ Chrome tải xong rồi tiếp tục.'
    async def failed(*_):raise protocol.FacebookIdentityError(message)
    monkeypatch.setattr(protocol,'switch_to_page_identity',failed)
    page=Page(initial());publisher=factory(page)
    with pytest.raises(ReelNeedsUser) as error:immediate(publisher.run())
    assert str(error.value)==message
    assert publisher.notices==['opening','switching']
    assert publisher.saved==[] and not any(action[0]=='upload' for action in page.actions)


def test_unexpected_identity_exception_is_redacted_but_step_still_switching(factory,monkeypatch):
    async def failed(*_):raise RuntimeError('fixture-private-token https://facebook.com/private?secret=value')
    monkeypatch.setattr(protocol,'switch_to_page_identity',failed)
    page=Page(initial());publisher=factory(page)
    with pytest.raises(ReelNeedsUser) as error:immediate(publisher.run())
    assert 'fixture-private' not in str(error.value) and 'secret=value' not in str(error.value)
    assert 'facebook.com/private' not in str(error.value)
    assert publisher.notices==['opening','switching']
    assert publisher.saved==[] and not any(action[0]=='upload' for action in page.actions)


def test_app_menu_then_create_reel_reaches_composer_upload_and_one_publish(factory):
    menu=state(buttons=[button('app_menu')])
    page=Page(menu,menu,initial(),composer(),editor(),editor(),editor(),published())
    publisher=factory(page)
    result=immediate(publisher.run())
    assert result['evidence']=='visible_reel_video'
    clicks=[action for action in page.actions if action[0]=='click']
    assert clicks==[('click','[data-cm-reel="app_menu"]'),('click','[data-cm-reel="create"]'),('click','[data-cm-reel="publish"]')]
    assert [action[1] for action in page.actions if action[0]=='upload']==[str(publisher.video_path.resolve())]
    assert page.actions.index(('actor',TARGET,'Fixture Page'))<page.actions.index(clicks[0])
    assert next(index for index,action in enumerate(page.actions) if action[0]=='checkpoint')<page.actions.index(clicks[-1])
    assert [action for action in page.actions if action[0]=='goto']==[('goto',TARGET)]


def test_direct_create_reel_never_opens_app_menu_even_when_menu_controls_are_ambiguous(factory):
    direct=state(buttons=[button('create'),button('app_menu','menu-a'),button('app_menu','menu-b')])
    page=Page(direct,composer(),editor(),editor(),editor(),published())
    assert immediate(factory(page).run())['evidence']=='visible_reel_video'
    assert ('click','[data-cm-reel="create"]') in page.actions
    assert not any(action[0]=='click' and 'menu-' in action[1] for action in page.actions)
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1


@pytest.mark.parametrize('buttons',[
    [],[button('app_menu',enabled=False)],
    [button('app_menu','menu-a'),button('app_menu','menu-b')],
    [button('create','create-a'),button('create','create-b'),button('app_menu')],
])
def test_missing_or_ambiguous_reel_entry_controls_never_upload_or_publish(factory,buttons):
    page=Page(state(buttons=buttons));publisher=factory(page)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert publisher.saved==[]
    assert not any(action[0] in ('click','upload','fill','checkpoint') for action in page.actions)
    assert [action for action in page.actions if action[0]=='goto']==[('goto',TARGET)]


@pytest.mark.parametrize('after_menu',[
    state(buttons=[button('app_menu')]),
    state(buttons=[button('create',enabled=False)]),
    state(buttons=[button('create','create-a'),button('create','create-b')]),
])
def test_menu_opens_once_but_missing_or_ambiguous_create_stops_before_upload(factory,after_menu):
    page=Page(state(buttons=[button('app_menu')]),after_menu);publisher=factory(page)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert page.actions.count(('click','[data-cm-reel="app_menu"]'))==1
    assert not any(action[0] in ('upload','fill','checkpoint') for action in page.actions)
    assert [action for action in page.actions if action[0]=='click']==[('click','[data-cm-reel="app_menu"]')]
    assert publisher.saved==[]


def test_disabled_publish_after_caption_waits_for_enable_then_publishes_once(factory):
    disabled=editor();disabled['buttons']=[button('publish',enabled=False)]
    page=Page(initial(),composer(),editor(),disabled,disabled,editor(),editor(),published())
    publisher=factory(page)
    assert immediate(publisher.run())['evidence']=='visible_reel_video'
    assert page.actions.count(('click','[data-cm-reel="publish"]'))==1
    assert len([action for action in page.actions if action[0]=='upload'])==1
    assert publisher.diagnostics==[]


def test_disabled_publish_never_enables_pauses_without_publish_or_reupload(factory):
    disabled=editor();disabled['buttons']=[button('publish',enabled=False)]
    page=Page(initial(),composer(),editor(),disabled)
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='chưa sẵn sàng'):immediate(publisher.run())
    assert ('click','[data-cm-reel="publish"]') not in page.actions
    assert len([action for action in page.actions if action[0]=='upload'])==1
    assert publisher.saved==[]
    assert publisher.diagnostics[0]['failure_category']=='controls_disabled'
    assert publisher.diagnostics[0]['buttons']=={'publish':{'enabled':0,'disabled':1}}


def test_missing_advance_controls_do_not_start_generic_wait(factory):
    absent=editor();absent['buttons']=[]
    page=Page(initial(),composer(),editor(),absent,published())
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser,match='Chưa có nút'):immediate(publisher.run())
    assert len(page.states)==1,'No extra read waits when no recognized Next/Publish exists'
    assert publisher.diagnostics[0]['buttons']=={}


def test_diagnostics_allowlists_bounded_ui_facts_without_secrets_or_latch_changes(factory):
    secret='fixture-private-password-caption-url'
    absent=state(reel_context=True,heading='Paramètres du reel',actor_matches=True,page_context_actor_matches=True,
                 buttons=[{'kind':'invented-'+secret,'enabled':True,'label':secret},
                          {'kind':'publish','enabled':False,'label':secret,'url':'https://evil.test/'+secret}],
                 captions=[{'id':secret,'matches':True,'value':secret}]*20,file_input_count=999999,
                 ui_diagnostics={'dialog_count':999999,'scope_is_dialog':True,'all_publish_count':-7,'outside_publish_count':2,'enabled_publish_count':0,'raw_body':secret},
                 raw_body=secret,current_url='https://evil.test/'+secret)
    page=Page(initial(),composer(),editor(),absent);publisher=factory(page)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    diagnostic=publisher.diagnostics[0];serialized=json.dumps(diagnostic,ensure_ascii=False)
    assert secret not in serialized and 'evil.test' not in serialized and str(publisher.video_path) not in serialized
    assert diagnostic['title']=='Paramètres du reel' and diagnostic['file_count']==10000
    assert diagnostic['caption_matches']==[True]*8 and diagnostic['caption_count']==20
    assert diagnostic['buttons']=={'publish':{'enabled':0,'disabled':1}}
    assert diagnostic['ui']=={'dialog_count':10000,'all_publish_count':0,'outside_publish_count':2,'enabled_publish_count':0,'scope_is_dialog':True}
    assert publisher.all_checkpoints==[{'diagnostics':diagnostic}]
    assert not publisher.publish_attempted and 'publish_attempted' not in publisher.all_checkpoints[0]


def test_unrecognized_title_and_exception_text_never_enter_diagnostics(factory):
    private='fixture-private-heading-and-browser-token'
    class FailedPage(Page):
        def locator(self,_):
            class Locator:
                async def click(self,**_):raise RuntimeError(private)
            return Locator()
    page=FailedPage(state(heading=private,buttons=[button('create')],captions=[{'matches':private}]))
    publisher=factory(page)
    with pytest.raises(ReelNeedsUser) as error:immediate(publisher.run())
    assert private not in str(error.value) and private not in json.dumps(publisher.diagnostics)
    assert publisher.diagnostics[0]['title'] is None
    assert publisher.diagnostics[0]['failure_category']=='unexpected_error'


def test_diagnostic_callback_failure_never_replaces_original_safe_error(factory):
    async def checkpoint(_):raise RuntimeError('fixture-private-diagnostic-failure')
    publisher=factory(Page(state(blocked=True)),checkpoint=checkpoint)
    with pytest.raises(ReelNeedsUser,match='đăng nhập') as error:immediate(publisher.run())
    assert 'fixture-private' not in str(error.value)


def test_diagnostics_after_uncertain_resume_preserve_true_publish_latch(factory):
    saved={'publish_attempted':True,'page_url':TARGET}
    patches=[]
    def checkpoint(value):patches.append(deepcopy(value));saved.update(value)
    publisher=factory(Page(state()),checkpoint=checkpoint,prior_checkpoint=deepcopy(saved))
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert saved['publish_attempted'] is True and saved['page_url']==TARGET
    assert len(patches)==1 and set(patches[0])=={'diagnostics'}
    assert saved['diagnostics']['failure_stage']=='verifying' and saved['diagnostics']['failure_category']=='publication_unverified'


def test_cancelled_run_does_not_write_failure_diagnostics(factory,monkeypatch):
    async def cancelled(*_):raise asyncio.CancelledError()
    monkeypatch.setattr(protocol,'switch_to_page_identity',cancelled)
    publisher=factory(Page(initial()))
    with pytest.raises(asyncio.CancelledError):immediate(publisher.run())
    assert publisher.all_checkpoints==[] and publisher.diagnostics==[]


def test_ui_diagnostics_accepts_only_fixed_fields_and_bounded_prefixed_scalars(factory):
    class IntSubclass(int):pass
    raw={'dialog_count':3,'scope_is_dialog':True,'all_publish_count':2,'outside_publish_count':1,'enabled_publish_count':1,
         'actor_verified':True,'actor_link_count':20001,'menu_depth':-9,'scope_inside':False,
         'cookies':True,'other_count':4,'actor_secret':'fixture-private-token','actor_labels':['fixture-private-token'],
         'actor_object':{'private':'fixture-private-token'},'actor_float':1.5,'actor_none':None,
         'actor_subclass':IntSubclass(7),'actor_':True,'actor_Bad':True,'actor_count2':2,
         'scope_'+'x'*49:True,'menu_'+'x'*48:8}
    publisher=factory(Page(state()));publisher._last_read=state(heading='Paramètres du reel',ui_diagnostics=raw)
    immediate(publisher._save_failure_diagnostics(ReelNeedsUser('Fixed safe message')))
    diagnostic=publisher.diagnostics[0]
    assert diagnostic['title']=='Paramètres du reel'
    assert diagnostic['ui']=={'dialog_count':3,'scope_is_dialog':True,'all_publish_count':2,'outside_publish_count':1,'enabled_publish_count':1,
                              'actor_verified':True,'actor_link_count':10000,'menu_depth':0,'scope_inside':False,'menu_'+'x'*48:8}
    assert 'fixture-private' not in json.dumps(diagnostic)
    assert publisher.all_checkpoints==[{'diagnostics':diagnostic}] and not publisher.publish_attempted


def test_ui_diagnostics_extension_limit_does_not_remove_existing_five_fields(factory):
    raw={'actor_'+chr(97+index//26)+chr(97+index%26):index for index in range(45)}
    raw.update(dialog_count=4,scope_is_dialog=True,all_publish_count=2,outside_publish_count=1,enabled_publish_count=1)
    publisher=factory(Page(state()));publisher._last_read=state(ui_diagnostics=raw)
    immediate(publisher._save_failure_diagnostics(ReelNeedsUser('Fixed safe message')))
    ui=publisher.diagnostics[0]['ui']
    assert len(ui)==37 and len([key for key in ui if key.startswith('actor_')])==32
    assert ui['dialog_count']==4 and ui['scope_is_dialog'] is True
    assert ui['all_publish_count']==2 and ui['outside_publish_count']==1 and ui['enabled_publish_count']==1
    assert 'actor_bf' in ui and 'actor_bg' not in ui


def test_unsafe_ui_values_cannot_overwrite_fixed_scope_boolean_or_enter_extensions(factory):
    publisher=factory(Page(state()));publisher._last_read=state(ui_diagnostics={
        'scope_is_dialog':7,'dialog_count':True,'all_publish_count':'fixture-private-token',
        'actor_trusted':False,'scope_caption':'fixture-private-token','menu_items':('private',),
    })
    immediate(publisher._save_failure_diagnostics(ReelNeedsUser('Fixed safe message')))
    ui=publisher.diagnostics[0]['ui']
    assert ui['scope_is_dialog'] is False and ui['dialog_count']==0 and ui['all_publish_count']==0
    assert ui['actor_trusted'] is False and 'scope_caption' not in ui and 'menu_items' not in ui
