"""Dedicated BM proxies stay outside the ordinary pool, including after detach.

Only synthetic endpoints and credentials are used. Network checks are queued into a
spy so these integration tests do not open browsers or connect to real proxies.
"""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app.api import routes
from app.automation.manager import TaskManager
from app.core.config import Config
from app.core.security import decrypt_credentials
from app.core.schemas import BmProfileInput,BmProxyInput,BulkCreate,ProfileInput,ProxyCheck,ProxyImport,Settings
from app.database.session import Database
from app.models.entities import BmProfile,BmProxy,Profile,Proxy
from app.services.browsers import BrowserManager
from app.services.proxies import ProxyService
from app.services.resources import Resources
from app.services.state import State


PRIVATE_LINE='192.0.2.33:10033:FixtureBmUser:FixtureBmSecret!82'


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in a synchronous fixture test')


@pytest.fixture
def svc(tmp_path,monkeypatch):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    state.save_settings(Settings(require_proxy=False))
    browsers=BrowserManager(db,state,config,Resources())
    proxies=ProxyService(db,state,config)
    tasks=TaskManager(db,state,browsers,config)
    service=SimpleNamespace(config=config,db=db,state=state,browsers=browsers,proxies=proxies,tasks=tasks)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=service)))
    ids=proxies.import_proxies('192.0.2.1:10001\n192.0.2.2:10002',False)['proxy_ids']
    with db.session.begin() as session:
        for pid,name,proxy_id in [(1,'Ordinary One',ids[0]),(2,'BM One',None),(3,'Ordinary Two',ids[1]),(4,'BM Two',None)]:
            directory=config.profiles_dir/f'Fixture_{pid}';directory.mkdir()
            (directory/'session-fixture.txt').write_text('retained')
            session.add(Profile(id=pid,name=name,profile_path=str(directory),proxy_id=proxy_id))
    for pid in (2,4):immediate(routes.assign_bm_profile(pid,BmProfileInput(),request))
    queued=[]
    monkeypatch.setattr(proxies,'schedule_checks',lambda values:queued.append(list(values)) or list(values))
    yield request,service,queued
    db.engine.dispose()


def attach_private(request,content=PRIVATE_LINE,check=False,profile_id=2):
    immediate(routes.save_bm_proxy(profile_id,BmProxyInput(content=content,default_protocol='http',check_after_save=check),request))
    return next(row for row in immediate(routes.list_profiles(request)) if row['id']==profile_id)['proxy_id']


def public_ids(request):
    return {row['id'] for row in immediate(routes.list_proxies(request))}


def test_private_proxy_is_assigned_only_to_bm_and_never_returns_credentials(svc):
    request,service,queued=svc
    proxy_id=attach_private(request,check=True)
    assert queued==[[proxy_id]]
    assert proxy_id not in public_ids(request)
    profiles=immediate(routes.list_profiles(request))
    bm=next(row for row in profiles if row['id']==2)
    assert bm['proxy']['host']=='192.0.2.33' and bm['proxy']['has_credentials'] is True
    with service.db.session() as session:
        assert session.get(BmProxy,proxy_id).profile_id==2
        stored=session.get(Proxy,proxy_id).encrypted_credentials
        assert stored and 'FixtureBmSecret!82' not in stored and 'FixtureBmUser' not in stored
    serialized=json.dumps(profiles)+json.dumps(immediate(routes.list_proxies(request)))
    assert all(value not in serialized for value in ('FixtureBmUser','FixtureBmSecret!82','encrypted_credentials'))


def test_existing_assigned_proxy_moves_into_private_scope_without_changing_route_or_storage(svc):
    request,service,_=svc
    with service.db.session() as session:
        original=session.get(Profile,1)
        proxy_id,path=original.proxy_id,original.profile_path
    immediate(routes.assign_bm_profile(1,BmProfileInput(note='BM source'),request))
    with service.db.session() as session:
        profile=session.get(Profile,1)
        assert profile.proxy_id==proxy_id and profile.profile_path==path
        assert session.get(BmProxy,proxy_id).profile_id==1
    assert proxy_id not in public_ids(request)
    assert (Path(path)/'session-fixture.txt').read_text()=='retained'


def test_normal_check_all_excludes_private_and_explicit_ids_cannot_bypass_scope(svc):
    request,_,queued=svc
    proxy_id=attach_private(request)
    normal=public_ids(request)
    immediate(routes.check_proxies(ProxyCheck(),request))
    assert set(queued[-1])==normal and proxy_id not in queued[-1]
    previous=len(queued)
    with pytest.raises(HTTPException) as error:
        immediate(routes.check_proxies(ProxyCheck(proxy_ids=[proxy_id]),request))
    assert error.value.status_code in (404,409) and len(queued)==previous


def test_bm_check_uses_only_its_private_proxy_even_while_chrome_is_running(svc,monkeypatch):
    request,service,queued=svc
    proxy_id=attach_private(request)
    monkeypatch.setattr(service.browsers,'status',lambda _:'Running')
    immediate(routes.check_bm_proxy(2,request))
    assert queued==[[proxy_id]]
    with pytest.raises(HTTPException) as error:immediate(routes.check_bm_proxy(4,request))
    assert error.value.status_code in (404,409)


def test_detached_proxy_remains_private_and_cannot_be_imported_or_deleted_from_normal_manager(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    immediate(routes.remove_bm_proxy(2,request))
    with service.db.session() as session:
        assert session.get(Profile,2).proxy_id is None
        assert session.get(BmProxy,proxy_id).profile_id is None
        assert session.get(Proxy,proxy_id) is not None
    assert proxy_id not in public_ids(request)
    result=immediate(routes.import_proxies(ProxyImport(content=PRIVATE_LINE,assign=False,check_after_import=True),request))
    assert result['created']==0 and result['errors'] and result['proxy_ids']==[]
    with pytest.raises(HTTPException) as error:immediate(routes.delete_proxy(proxy_id,request))
    assert error.value.status_code in (404,409)


def test_normal_import_and_bulk_assignment_cannot_consume_private_or_bm_profiles(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    immediate(routes.remove_bm_proxy(2,request))
    immediate(routes.bulk_create(BulkCreate(count=2),request))
    with service.db.session() as session:
        regular=[row for row in session.scalars(select(Profile)) if row.name.startswith('Chrome_')]
        assert len(regular)==2 and all(row.proxy_id is None for row in regular)
    result=immediate(routes.import_proxies(ProxyImport(content='192.0.2.99:10099',assign=True),request))
    assert result['assigned']==1
    with service.db.session() as session:
        assert session.get(Profile,2).proxy_id is None and session.get(Profile,4).proxy_id is None
        assert not list(session.scalars(select(Profile).where(Profile.proxy_id==proxy_id)))


def test_private_scope_cannot_be_attached_by_ordinary_profile_create_or_edit(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    immediate(routes.remove_bm_proxy(2,request))
    with pytest.raises(HTTPException) as error:
        immediate(routes.create_profile(ProfileInput(name='Cannot consume BM proxy',proxy_id=proxy_id),request))
    assert error.value.status_code in (404,409)
    with service.db.session() as session:revision=session.get(Profile,3).revision
    with pytest.raises(HTTPException) as error:
        immediate(routes.update_profile(3,ProfileInput(name='Ordinary Two',proxy_id=proxy_id,revision=revision),request))
    assert error.value.status_code in (404,409)
    with service.db.session() as session:
        assert session.get(Profile,3).proxy_id!=proxy_id
        assert len(list(session.scalars(select(Profile))))==4


def test_bm_profile_cannot_select_a_public_pool_proxy_through_generic_profile_api(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    public=service.proxies.import_proxies('192.0.2.88:10088',False)['proxy_ids'][0]
    with service.db.session() as session:revision=session.get(Profile,2).revision
    with pytest.raises(HTTPException) as error:
        immediate(routes.update_profile(2,ProfileInput(name='BM One',proxy_id=public,revision=revision),request))
    assert error.value.status_code in (404,409)
    with pytest.raises(HTTPException) as error:
        immediate(routes.create_profile(ProfileInput(name='New BM',proxy_id=public,bm=BmProfileInput()),request))
    assert error.value.status_code in (404,409)
    with service.db.session() as session:assert session.get(Profile,2).proxy_id==proxy_id


def test_private_proxy_cannot_be_taken_from_another_bm_profile(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    with pytest.raises(HTTPException) as error:attach_private(request,profile_id=4)
    assert error.value.status_code==409
    with service.db.session() as session:
        assert session.get(Profile,2).proxy_id==proxy_id and session.get(Profile,4).proxy_id is None


def test_detached_private_proxy_can_be_reused_by_another_bm_without_creating_a_duplicate(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    immediate(routes.remove_bm_proxy(2,request))
    assert attach_private(request,profile_id=4)==proxy_id
    with service.db.session() as session:
        assert session.get(Profile,2).proxy_id is None
        assert session.get(Profile,4).proxy_id==proxy_id
        assert session.get(BmProxy,proxy_id).profile_id==4
        assert len(list(session.scalars(select(Proxy))))==3
    assert proxy_id not in public_ids(request)


def test_replacing_private_proxy_keeps_both_endpoints_outside_the_normal_pool(svc):
    request,service,_=svc
    original=attach_private(request)
    replacement=attach_private(request,content='192.0.2.44:10044')
    assert replacement!=original
    with service.db.session() as session:
        assert session.get(Profile,2).proxy_id==replacement
        assert session.get(BmProxy,original).profile_id is None
        assert session.get(BmProxy,replacement).profile_id==2
        assert session.get(Proxy,original) is not None
    assert {original,replacement}.isdisjoint(public_ids(request))


@pytest.mark.parametrize('changed',[False,True])
def test_saving_existing_identity_refreshes_credentials_and_only_resets_checks_when_changed(svc,changed):
    request,service,_=svc
    proxy_id=attach_private(request)
    with service.db.session.begin() as session:
        proxy=session.get(Proxy,proxy_id)
        proxy.status,proxy.ip_address,proxy.latency_ms='Working','203.0.113.9',123
        proxy.error_message,proxy.last_checked='Synthetic previous result','2026-10-29T00:00:00+00:00'
    line=PRIVATE_LINE.replace('FixtureBmSecret!82','FixtureBmReplacement!83') if changed else PRIVATE_LINE
    assert attach_private(request,content=line)==proxy_id
    with service.db.session() as session:
        proxy=session.get(Proxy,proxy_id)
        expected='FixtureBmReplacement!83' if changed else 'FixtureBmSecret!82'
        assert decrypt_credentials(proxy.encrypted_credentials)==('FixtureBmUser',expected)
        assert session.get(BmProxy,proxy_id).profile_id==2
        if changed:
            assert proxy.status=='Unchecked'
            assert all(value is None for value in (proxy.ip_address,proxy.latency_ms,proxy.error_message,proxy.last_checked))
        else:
            assert proxy.status=='Working' and proxy.ip_address=='203.0.113.9' and proxy.latency_ms==123
            assert proxy.error_message=='Synthetic previous result' and proxy.last_checked=='2026-10-29T00:00:00+00:00'
    assert proxy_id not in public_ids(request)


@pytest.mark.parametrize('busy',['Running','Starting','Queued','outlook','facebook','extension','task','checking'])
def test_private_proxy_edits_and_detach_require_an_idle_profile(svc,monkeypatch,busy):
    request,service,_=svc
    proxy_id=attach_private(request)
    if busy in ('Running','Starting','Queued'):monkeypatch.setattr(service.browsers,'status',lambda _:busy)
    elif busy=='outlook':service.browsers.outlook_profiles.add(2)
    elif busy=='facebook':service.browsers.facebook_profiles.add(2)
    elif busy=='extension':service.browsers.extension_profiles.add(2)
    elif busy=='task':service.tasks.profile_jobs[2]=object()
    else:service.proxies.checking.add(proxy_id)
    with pytest.raises(HTTPException) as error:attach_private(request,content='192.0.2.44:10044')
    assert error.value.status_code==409
    with pytest.raises(HTTPException) as error:immediate(routes.remove_bm_proxy(2,request))
    assert error.value.status_code==409
    with service.db.session() as session:assert session.get(Profile,2).proxy_id==proxy_id


def test_remove_bm_role_requires_detaching_its_private_proxy_first_and_keeps_storage(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    with service.db.session() as session:path=session.get(Profile,2).profile_path
    with pytest.raises(HTTPException) as error:immediate(routes.unassign_bm_profile(2,request))
    assert error.value.status_code==409
    immediate(routes.remove_bm_proxy(2,request))
    immediate(routes.unassign_bm_profile(2,request))
    with service.db.session() as session:
        assert session.get(BmProfile,2) is None and session.get(Profile,2).proxy_id is None
        assert session.get(BmProxy,proxy_id) is not None
    assert (Path(path)/'session-fixture.txt').read_text()=='retained'
    assert proxy_id not in public_ids(request)


def test_deleted_profile_does_not_expose_its_private_proxy_to_the_normal_pool(svc):
    request,service,_=svc
    proxy_id=attach_private(request)
    with service.db.session.begin() as session:session.delete(session.get(Profile,2))
    with service.db.session() as session:
        assert session.get(BmProxy,proxy_id).profile_id is None
    assert proxy_id not in public_ids(request)


def test_private_proxy_input_must_contain_exactly_one_valid_proxy_without_partial_save(svc):
    request,service,_=svc
    with service.db.session() as session:before=len(list(session.scalars(select(Proxy))))
    with pytest.raises((HTTPException,ValueError,ValidationError)):
        attach_private(request,content='192.0.2.50:10050\n192.0.2.51:10051')
    with service.db.session() as session:
        assert session.get(Profile,2).proxy_id is None
        assert len(list(session.scalars(select(Proxy))))==before
