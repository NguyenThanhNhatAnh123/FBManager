"""Offline Page setup fixtures: exact fields, saved readback and safe pauses."""
import asyncio
from pathlib import Path

import pytest

from app.services.page_setup import PageSetupController,PageSetupNeedsUser,_digest,_input_digest


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in fixture')


class Field:
    def __init__(self,value='',role=None):self.value=value;self.role=role;self.writes=[]
    async def fill(self,value,**_):self.value=value;self.writes.append(value)
    async def input_value(self):return self.value
    async def get_attribute(self,name):return self.role if name=='role' else None


class Page:
    def __init__(self,*media):self.media=list(media);self.uploads=[];self.first=self
    async def wait_for_timeout(self,_):pass
    async def evaluate(self,*_):return self.media.pop(0)
    def locator(self,_):return self
    async def count(self):return 1
    async def set_input_files(self,value,**_):self.uploads.append(value)


class Controller(PageSetupController):
    def __init__(self,page=None,root=None):
        self.events=[];self.actions=[];self.fields={};self.persisted={};self.saved={}
        super().__init__(page or Page(),'https://www.facebook.com/profile.php?id=555','Fixture Page',root or Path('.'),lambda key,status,message='',**extra:self.events.append((key,status,message,extra)))
    async def _guard(self):self.actions.append('guard')
    async def _open_editor(self,key):
        self.actions.append(('open',key))
        if key in self.saved:
            for name,value in self.saved[key].items():self.fields[name]=Field(value)
    async def _textbox(self,key,required=True):return self.fields.get(key)
    async def _save_reload(self):self.actions.append('save_reload')
    async def _dismiss_readback(self):self.actions.append('dismiss')


def test_blank_setup_never_switches_uploads_or_clears_existing_data():
    controller=Controller()
    assert immediate(controller.apply({'bio':'  ','address':{'street':'  '},'categories':[]}))=={key:'skipped' for key in ('avatar','cover','bio','address','categories')}
    assert controller.actions==[]
    assert all(event[1]=='skipped' for event in controller.events)


def test_unchanged_completed_field_is_not_written_again():
    controller=Controller()
    fields={'bio':{'status':'completed','fingerprint':_digest('Existing bio')}}
    result=immediate(controller.apply({'bio':'Existing bio'},fields))
    assert result['bio']=='completed' and controller.actions==[]
    assert not any(event[0]=='bio' for event in controller.events)


def test_bio_only_completes_after_saved_readback_matches():
    controller=Controller();controller.fields['bio']=Field('Old bio');controller.saved['bio']={'bio':'Requested bio'}
    result=immediate(controller.apply({'bio':'Requested bio'}))
    assert result['bio']=='completed'
    assert controller.actions==['guard',('open','bio'),'guard','save_reload',('open','bio'),'dismiss']
    assert [event[1] for event in controller.events if event[0]=='bio']==['updating','completed']


def test_unsaved_or_rejected_bio_pauses_and_does_not_mark_success():
    controller=Controller();controller.fields['bio']=Field('Old bio');controller.saved['bio']={'bio':'Old bio'}
    with pytest.raises(PageSetupNeedsUser):immediate(controller.apply({'bio':'Requested bio','address':{'street':'123 Fixture Street'}}))
    assert [event[1] for event in controller.events if event[0]=='bio']==['updating','needs_user']
    assert not any(action==('open','address') for action in controller.actions)


def test_address_updates_only_nonblank_requested_parts_and_checks_each_after_reload():
    controller=Controller();controller.fields={'street':Field('Old street'),'city':Field('Existing City')}
    original_city=controller.fields['city']
    controller.saved['address']={'street':'123 Fixture Street','city':'Existing City'}
    result=immediate(controller.apply({'address':{'street':'123 Fixture Street','city':'','postal_code':''}}))
    assert result['address']=='completed' and original_city.writes==[]
    assert controller.actions.count('save_reload')==1


def test_address_readback_must_match_every_provided_part():
    controller=Controller();controller.fields={'street':Field(),'city':Field()}
    controller.saved['address']={'street':'123 Fixture Street','city':'Wrong City'}
    with pytest.raises(PageSetupNeedsUser):immediate(controller.apply({'address':{'street':'123 Fixture Street','city':'Requested City'}}))
    assert not any(event[0]=='address' and event[1]=='completed' for event in controller.events)


def test_address_combobox_requires_an_exact_visible_option():
    class MissingOptionController(Controller):
        async def _find(self,*_,**__):return None
    controller=MissingOptionController();controller.fields={'city':Field(role='combobox')}
    with pytest.raises(PageSetupNeedsUser):immediate(controller.apply({'address':{'city':'Requested City'}}))
    assert 'save_reload' not in controller.actions


@pytest.mark.parametrize('failure',[RuntimeError('fixture-password-token'),PageSetupNeedsUser('raw-private-text')])
def test_layout_and_browser_errors_are_sanitized_before_field_status(failure):
    class FailedController(Controller):
        async def _bio(self,value):raise failure
    controller=FailedController()
    with pytest.raises(PageSetupNeedsUser) as error:immediate(controller.apply({'bio':'Fixture bio'}))
    assert 'fixture-password' not in str(error.value) and 'raw-private' not in str(error.value)
    assert all('fixture-password' not in event[2] and 'raw-private' not in event[2] for event in controller.events)


def test_cancellation_does_not_become_a_failed_field():
    class CancelledController(Controller):
        async def _bio(self,value):raise asyncio.CancelledError()
    controller=CancelledController()
    with pytest.raises(asyncio.CancelledError):immediate(controller.apply({'bio':'Fixture bio'}))
    assert not any(event[1] in ('completed','needs_user') for event in controller.events)


def test_assets_outside_validated_root_are_rejected_before_upload(tmp_path):
    assets=tmp_path/'page-assets';assets.mkdir();outside=tmp_path/'outside.png';outside.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    controller=Controller(root=assets)
    with pytest.raises(PageSetupNeedsUser):immediate(controller.apply({'avatar_path':str(outside)}))
    assert controller.page.uploads==[] and 'save_reload' not in controller.actions


def test_completed_photo_fingerprint_includes_validated_file_contents(tmp_path):
    import hashlib
    assets=tmp_path/'page-assets';assets.mkdir();picture=assets/'fixture.png'
    content=b'\x89PNG\r\n\x1a\nfirst-fixture';picture.write_bytes(content)
    first=_input_digest('avatar',str(picture),assets)
    assert first==_digest({'path':str(picture.resolve()),'sha256':hashlib.sha256(content).hexdigest()})
    controller=Controller(root=assets)
    result=immediate(controller.apply({'avatar_path':str(picture)},{'avatar':{'status':'completed','fingerprint':first}}))
    assert result['avatar']=='completed' and controller.actions==[]
    picture.write_bytes(b'\x89PNG\r\n\x1a\nsecond-fixture')
    assert _input_digest('avatar',str(picture),assets)!=first


@pytest.mark.parametrize('before,after,success',[
    ('https://cdn.example/old.jpg?token=one','https://cdn.example/new.jpg?token=two',True),
    ('https://cdn.example/same.jpg?token=one','https://cdn.example/same.jpg?token=two',False),
    ('https://cdn.example/old.jpg','',False),
])
def test_photo_completion_requires_loaded_new_header_media_after_saved_reload(tmp_path,before,after,success):
    assets=tmp_path/'page-assets';assets.mkdir();picture=assets/'fixture.png';picture.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    class PhotoController(Controller):
        async def _find(self,*_,**__):return object()
        async def _click(self,_):await self._guard();self.actions.append('open_photo')
        async def _scope(self):return self.page
    controller=PhotoController(Page(before,after),assets)
    if success:
        assert immediate(controller.apply({'avatar_path':str(picture)}))['avatar']=='completed'
    else:
        with pytest.raises(PageSetupNeedsUser):immediate(controller.apply({'avatar_path':str(picture)}))
        assert not any(event[0]=='avatar' and event[1]=='completed' for event in controller.events)
    assert controller.page.uploads==[str(picture.resolve())]
    assert controller.actions.count('guard')==3


def test_categories_preserve_existing_and_only_add_missing_exact_suggestions():
    class CategoriesController(Controller):
        def __init__(self):super().__init__();self.reads=iter([{'existing category'},{'existing category','requested category'},{'existing category','requested category'}]);self.fields['categories']=Field()
        async def _category_values(self):return next(self.reads)
        async def _fill(self,field,value,*,suggestion=False):
            assert suggestion is True
            await super()._fill(field,value,suggestion=False)
    controller=CategoriesController()
    result=immediate(controller.apply({'categories':['Existing Category','Requested Category']}))
    assert result['categories']=='completed' and controller.fields['categories'].writes==['Requested Category']


def test_categories_without_persisted_readback_pause():
    class CategoriesController(Controller):
        def __init__(self):super().__init__();self.reads=iter([{'requested category'},set()]);self.fields['categories']=Field()
        async def _category_values(self):return next(self.reads)
    controller=CategoriesController()
    with pytest.raises(PageSetupNeedsUser):immediate(controller.apply({'categories':['Requested Category']}))
    assert not any(event[0]=='categories' and event[1]=='completed' for event in controller.events)
