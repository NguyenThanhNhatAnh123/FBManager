"""Persist per-BM mobile mode without replacing profile data or changing live sessions."""
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.routes import set_bm_mobile
from app.core.config import Config
from app.core.schemas import BmMobileInput,BmProfileInput
from app.database.session import Database
from app.models.entities import BmBrowserSettings,BmProfile,Profile,Proxy
from app.services.bm_profiles import public_bm_profiles,save_bm_profile
from app.services.state import State


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O')


@pytest.fixture
def services(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3')
    folder=config.profiles_dir/'BM_fixture';folder.mkdir();(folder/'retained.txt').write_text('saved session fixture')
    with db.session.begin() as session:
        proxy=Proxy(host='192.0.2.5',port=8080,protocol='http',identity='mobile-fixture')
        session.add(proxy);session.flush()
        session.add_all([Profile(id=1,name='BM fixture',profile_path=str(folder),proxy_id=proxy.id),
                         Profile(id=2,name='Ordinary fixture',profile_path=str(config.profiles_dir/'ordinary'))])
        session.flush();session.add(BmProfile(profile_id=1))
    browsers=SimpleNamespace(status=lambda _:'Stopped',extension_profiles=set(),facebook_profiles=set(),outlook_profiles=set())
    svc=SimpleNamespace(db=db,state=State(db),browsers=browsers,tasks=SimpleNamespace(profile_jobs={}),config=config)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=svc)))
    yield svc,request
    db.engine.dispose()


def test_mobile_setting_survives_reload_and_metadata_edit_without_replacing_profile(services):
    svc,request=services
    with svc.db.session() as session:
        original=session.get(Profile,1);path,proxy_id=original.profile_path,original.proxy_id
        assert public_bm_profiles(session)[1]['mobile_enabled'] is False
    assert immediate(set_bm_mobile(1,BmMobileInput(enabled=True),request))=={'enabled':True}
    with svc.db.session.begin() as session:
        save_bm_profile(session,1,BmProfileInput(note='Updated note'))
    reopened=Database(svc.config.data_dir/'manager.sqlite3')
    with reopened.session() as session:
        assert public_bm_profiles(session)[1]['mobile_enabled'] is True
        profile=session.get(Profile,1)
        assert (profile.profile_path,profile.proxy_id)==(path,proxy_id)
    reopened.engine.dispose()
    assert (Path(path)/'retained.txt').read_text()=='saved session fixture'
    immediate(set_bm_mobile(1,BmMobileInput(enabled=False),request))
    with svc.db.session() as session:assert public_bm_profiles(session)[1]['mobile_enabled'] is False


@pytest.mark.parametrize('busy',['Running','Starting','Queued','extension','facebook','outlook','task'])
def test_mobile_setting_cannot_change_an_active_profile(services,busy):
    svc,request=services
    if busy in ('Running','Starting','Queued'):svc.browsers.status=lambda _:busy
    elif busy=='task':svc.tasks.profile_jobs[1]=1
    else:getattr(svc.browsers,busy+'_profiles').add(1)
    with pytest.raises(HTTPException) as error:immediate(set_bm_mobile(1,BmMobileInput(enabled=True),request))
    assert error.value.status_code==409
    with svc.db.session() as session:assert session.get(BmBrowserSettings,1) is None


@pytest.mark.parametrize('profile_id',[2,999])
def test_only_existing_bm_roles_can_have_mobile_settings(services,profile_id):
    svc,request=services
    with pytest.raises(HTTPException) as error:immediate(set_bm_mobile(profile_id,BmMobileInput(enabled=True),request))
    assert error.value.status_code==404
    with svc.db.session() as session:assert session.get(BmBrowserSettings,profile_id) is None


def test_returning_to_ordinary_profiles_removes_mobile_setting(services):
    svc,request=services
    immediate(set_bm_mobile(1,BmMobileInput(enabled=True),request))
    with svc.db.session.begin() as session:session.delete(session.get(BmProfile,1))
    with svc.db.session() as session:
        assert session.get(BmBrowserSettings,1) is None
        assert session.get(Profile,1) is not None
