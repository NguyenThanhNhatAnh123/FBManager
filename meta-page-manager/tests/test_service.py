from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import pytest
from page_manager.errors import AppError
from page_manager.graph import GraphError
from page_manager.inputs import ConnectionInput,ConnectionUpdate,PageRefresh
from page_manager.service import PageManager
from page_manager import service as service_module
from page_manager.timezones import date_keys
from conftest import FixtureBox,immediate

TOKEN1='FixtureTokenOne82';TOKEN2='FixtureTokenTwo82';PAGE_TOKEN='FixtureDerivedToken82'


class FakeGraph:
    def __init__(self):self.catalogs={};self.complete=True;self.failure=None;self.post_values=[];self.has_more=False;self.on_catalog=None;self.details_error=None
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def identity(self,token):return {'id':'77','name':'Fixture identity'}
    async def permissions(self,_):return None
    async def page_catalog(self,token,*_):
        if self.on_catalog:self.on_catalog()
        return deepcopy(self.catalogs.get(token,[])),self.complete,self.failure
    async def details(self,token,pid):
        if self.details_error:raise self.details_error
        return {'id':pid,'name':'Page '+pid,'about':'Fixture about','unknown_secret':token},None
    async def posts(self,*_):return deepcopy(self.post_values),self.has_more


@pytest.fixture
def fixture(tmp_path):
    graph=FakeGraph();manager=PageManager(tmp_path,graph_factory=lambda:graph,secret_box=FixtureBox())
    return manager,graph


def connection(manager,token=TOKEN1):return manager.save_connection(ConnectionInput(label='Fixture connection',token_type='user',token=token))
def catalog(pid='555',**extra):return {'id':pid,'name':'Page '+pid,'_source':'accounts',**extra}


def test_same_page_multiple_tokens_and_duplicate_edges_are_one_canonical_card_and_count(fixture):
    manager,graph=fixture;one=connection(manager);two=connection(manager,TOKEN2)
    graph.catalogs={TOKEN1:[catalog(access_token=PAGE_TOKEN),catalog(_source='owned_pages')],TOKEN2:[catalog()]}
    immediate(manager.sync(one['id']));immediate(manager.sync(two['id']))
    pages=manager.pages(timezone='UTC')['items']
    assert len(pages)==1 and len(pages[0]['connections'])==2
    assert pages[0]['connections'][0]['sources']==['accounts','owned_pages']
    assert manager.overview('UTC')['stats']['pages']==1
    assert manager.connections()['items'][0]['page_count']==1
    serialized=json.dumps(manager.connections())+json.dumps(pages)+json.dumps(manager.store.events())
    assert all(secret not in serialized for secret in (TOKEN1,TOKEN2,PAGE_TOKEN,'encrypted_token','encrypted_page_token'))
    with manager.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM pages').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM connection_pages').fetchone()[0]==2
        assert TOKEN1 not in db.execute('SELECT encrypted_token FROM connections LIMIT 1').fetchone()[0]


def test_partial_sync_adds_seen_pages_without_deactivating_previous_links(fixture):
    manager,graph=fixture;cid=connection(manager)['id'];graph.catalogs[TOKEN1]=[catalog('1'),catalog('2')];immediate(manager.sync(cid))
    graph.catalogs[TOKEN1]=[catalog('3')];graph.complete=False;graph.failure=GraphError('permission_limited',200)
    result=immediate(manager.sync(cid))
    assert result['complete'] is False and result['pages_seen']==1
    assert manager.connection_public(manager.store.connection(cid))['page_count']==3
    graph.complete=True;graph.failure=None;immediate(manager.sync(cid))
    assert manager.connection_public(manager.store.connection(cid))['page_count']==1
    assert len(manager.pages(timezone='UTC')['items'])==3,'Canonical history stays cached'


def test_complete_scope_snapshot_replaces_old_edge_while_partial_snapshot_keeps_known_sources(fixture):
    manager,graph=fixture;cid=connection(manager)['id'];graph.catalogs[TOKEN1]=[catalog(_source='owned_pages')];immediate(manager.sync(cid))
    graph.catalogs[TOKEN1]=[catalog(_source='client_pages')];graph.complete=False;immediate(manager.sync(cid))
    assert manager.pages(timezone='UTC')['items'][0]['connections'][0]['sources']==['client_pages','owned_pages']
    graph.complete=True;immediate(manager.sync(cid))
    assert manager.pages(timezone='UTC')['items'][0]['connections'][0]['sources']==['client_pages']


def test_rotate_and_disconnect_invalidate_only_own_links_and_derived_tokens(fixture):
    manager,graph=fixture;one=connection(manager);two=connection(manager,TOKEN2)
    graph.catalogs={TOKEN1:[catalog(access_token=PAGE_TOKEN)],TOKEN2:[catalog()]}
    immediate(manager.sync(one['id']));immediate(manager.sync(two['id']))
    manager.save_connection(ConnectionUpdate(label='Rotated',token_type='user',token='FixtureReplacement82', revision=manager.store.connection(one['id'])["revision"]),one['id'])
    with pytest.raises(AppError):manager.store.source('555',one['id'])
    assert manager.store.source('555',two['id'])['id']==two['id']
    manager.disconnect(one['id'])
    assert manager.connections()['items'][0]['has_token'] is False
    assert len(manager.pages(timezone='UTC')['items'])==1
    with manager.store.connect() as db:
        assert db.execute('SELECT encrypted_page_token FROM connection_pages WHERE connection_id=?',(one['id'],)).fetchone()[0] is None
        assert db.execute('SELECT encrypted_token FROM connections WHERE id=?',(two['id'],)).fetchone()[0]


@pytest.mark.parametrize('mutation',['rotate','disconnect'])
def test_stale_inflight_sync_cannot_restore_rotated_or_disconnected_access(fixture,mutation):
    manager,graph=fixture;cid=connection(manager)['id'];graph.catalogs[TOKEN1]=[catalog()]
    graph.on_catalog=lambda:manager.disconnect(cid) if mutation=='disconnect' else manager.save_connection(ConnectionUpdate(label='Rotated',token_type='user',token='FixtureReplacement82', revision=manager.store.connection(cid)["revision"]),cid)
    with pytest.raises(AppError,match='thay đổi'):immediate(manager.sync(cid))
    assert manager.pages(timezone='UTC')['items']==[]


def test_explicit_connection_must_link_to_requested_page_and_cache_never_mixes_pages(fixture):
    manager,graph=fixture;one=connection(manager);two=connection(manager,TOKEN2)
    graph.catalogs={TOKEN1:[catalog('1')],TOKEN2:[catalog('2')]};immediate(manager.sync(one['id']));immediate(manager.sync(two['id']))
    with pytest.raises(AppError):immediate(manager.refresh('1',PageRefresh(connection_id=two['id']),timezone='UTC'))
    graph.post_values=[{'id':'2_9','message':'wrong Page','created_time':datetime.now(timezone.utc).isoformat()}]
    result=immediate(manager.refresh('1',PageRefresh(connection_id=one['id']),timezone='UTC'))
    assert result['posts']==[] and result['page']['posts_today_complete'] is False


def test_posts_are_deduplicated_and_recent_snapshot_does_not_count_deleted_old_cache(fixture):
    manager,graph=fixture;cid=connection(manager)['id'];graph.catalogs[TOKEN1]=[catalog()];immediate(manager.sync(cid))
    post={'id':'555_9','message':'Fixture '+TOKEN1,'created_time':datetime.now(timezone.utc).isoformat(),'permalink_url':'https://www.facebook.com/555/posts/9?access_token='+TOKEN1}
    graph.post_values=[post,post]
    value=immediate(manager.refresh('555',PageRefresh(),timezone='UTC'))
    assert len(value['posts'])==1 and value['page']['posts_today']==1 and value['page']['posts_today_complete'] is True
    assert TOKEN1 not in json.dumps(value)
    graph.post_values=[];value=immediate(manager.refresh('555',PageRefresh(),timezone='UTC'))
    assert value['posts']==[] and value['page']['posts_today']==0
    with manager.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM posts').fetchone()[0]==1


def test_post_coverage_unknown_before_fetch_and_incomplete_if_recent_results_truncated(fixture):
    manager,graph=fixture;cid=connection(manager)['id'];graph.catalogs[TOKEN1]=[catalog()];immediate(manager.sync(cid))
    assert manager.pages(timezone='UTC')['items'][0]['posts_today'] is None
    graph.post_values=[{'id':'555_1','created_time':datetime.now(timezone.utc).isoformat()}];graph.has_more=True
    value=immediate(manager.refresh('555',PageRefresh(),timezone='UTC'))
    assert value['page']['posts_today']==1 and value['page']['posts_today_complete'] is False
    assert manager.overview('UTC')['stats']['today_count_known'] is False
    graph.post_values.append({'id':'555_2','created_time':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()})
    value=immediate(manager.refresh('555',PageRefresh(),timezone='UTC'))
    assert value['page']['posts_today_complete'] is True


def test_optional_detail_failure_and_disconnect_preserve_readable_history_but_refresh_requires_access(fixture):
    manager,graph=fixture;cid=connection(manager)['id'];graph.catalogs[TOKEN1]=[catalog()];immediate(manager.sync(cid))
    immediate(manager.refresh('555',PageRefresh(include_posts=False),timezone='UTC'))
    graph.details_error=GraphError('permission_limited',200)
    value=immediate(manager.refresh('555',PageRefresh(include_posts=False),timezone='UTC'))
    assert value['page']['details']['about']=='Fixture about' and value['page']['error']
    manager.disconnect(cid)
    assert manager.page('555','UTC')['page']['details']['about']=='Fixture about'
    assert manager.page('555','UTC')['page']['status']=='disconnected'
    with pytest.raises(AppError):immediate(manager.refresh('555',PageRefresh(),timezone='UTC'))


def test_unknown_grants_expiry_remain_null_and_user_app_tokens_are_not_supported(fixture):
    manager,_=fixture;cid=connection(manager)['id'];value=immediate(manager.check(cid))['connection']
    assert value['status']=='working' and value['permissions'] is None and value['expires_at'] is None
    with pytest.raises(ValueError):ConnectionInput(label='App token',token_type='user',token='123|app-secret')


def test_timezone_dates_handle_dst_without_fixed_offset_guessing():
    assert date_keys(['2026-11-01T06:59:00+00:00','2026-11-01T07:01:00+00:00'],'America/Los_Angeles')==['2026-10-31','2026-11-01']
    with pytest.raises(AppError):date_keys(['2026-10-30T12:00:00Z'],'NotAZone/Unknown')


def test_long_input_and_derived_token_cannot_leak_via_truncated_names_details_or_url_paths(fixture):
    manager,graph=fixture;long_token='LongFixtureSecret'+('Z'*400);derived='DerivedFixtureSecret'+('Q'*400)
    cid=connection(manager,long_token)['id']
    graph.catalogs[long_token]=[catalog(name=long_token,category=derived,link='https://www.facebook.com/'+derived,access_token=derived)]
    immediate(manager.sync(cid))
    class MetadataGraph(FakeGraph):
        async def details(self,token,pid):return {'id':pid,'name':long_token,'about':long_token+derived,'website':'https://example.test/'+derived,'link':'https://www.facebook.com/'+long_token},None
    graph.__class__=MetadataGraph
    value=immediate(manager.refresh('555',PageRefresh(include_posts=False),timezone='UTC'))
    serialized=json.dumps(value)+json.dumps(manager.connections())+json.dumps(manager.store.events())
    assert long_token[:150] not in serialized and derived[:150] not in serialized
    assert 'website' not in value['page']['details'] and value['page']['details']['about']=='[đã ẩn][đã ẩn]'


def test_inflight_refresh_cannot_store_after_token_rotation(fixture):
    manager,graph=fixture;cid=connection(manager)['id'];graph.catalogs[TOKEN1]=[catalog()];immediate(manager.sync(cid))
    class RotatingGraph(FakeGraph):
        async def details(self,token,pid):
            manager.save_connection(ConnectionUpdate(label='Rotated',token_type='user',token='FixtureReplacement82', revision=manager.store.connection(cid)["revision"]),cid)
            return {'id':pid,'name':'stale metadata','about':'Must not persist'},None
    graph.__class__=RotatingGraph
    with pytest.raises(AppError,match='thay đổi'):immediate(manager.refresh('555',PageRefresh(),timezone='UTC'))
    assert manager.page('555','UTC')['page']['details']=={}


class Deferred:
    def __init__(self,marker):self.marker=marker
    def __await__(self):yield self.marker


class OfflineFuture:
    def __init__(self):self.ready=False
    def __await__(self):
        if not self.ready:yield 'waiting-page-lock'
    def done(self):return self.ready
    def cancelled(self):return False
    def set_result(self,_):self.ready=True


def test_two_connections_refreshing_same_page_are_serialized_before_graph_fetch(fixture,monkeypatch):
    manager,graph=fixture;one=connection(manager);two=connection(manager,TOKEN2)
    graph.catalogs={TOKEN1:[catalog()],TOKEN2:[catalog()]};immediate(manager.sync(one['id']));immediate(manager.sync(two['id']))
    requested=[]
    class DeferredGraph(FakeGraph):
        async def details(self,token,pid):
            requested.append(token);await Deferred(('graph',token))
            return {'id':pid,'name':'Older snapshot' if token==TOKEN1 else 'Newer snapshot'},None
        async def posts(self,token,pid,limit):return [{'id':pid+('_1' if token==TOKEN1 else '_2'),'created_time':datetime.now(timezone.utc).isoformat()}],False
    graph.__class__=DeferredGraph
    fake_loop=type('Loop',(),{'create_future':lambda _:OfflineFuture()})()
    monkeypatch.setattr(service_module.asyncio.events,'_get_running_loop',lambda:fake_loop)
    old=manager.refresh('555',PageRefresh(connection_id=one['id']),timezone='UTC')
    newer=manager.refresh('555',PageRefresh(connection_id=two['id']),timezone='UTC')
    try:
        assert old.send(None)==('graph',TOKEN1)
        assert newer.send(None)=='waiting-page-lock' and requested==[TOKEN1]
        assert immediate(old)['page']['name']=='Older snapshot'
        assert newer.send(None)==('graph',TOKEN2) and requested==[TOKEN1,TOKEN2]
        result=immediate(newer)
        assert result['page']['name']=='Newer snapshot' and [post['id'] for post in result['posts']]==['555_2']
        assert len(manager.pages(timezone='UTC')['items'])==1
    finally:old.close();newer.close()


def test_different_pages_on_different_connections_remain_independent(fixture):
    manager,graph=fixture;one=connection(manager);two=connection(manager,TOKEN2)
    graph.catalogs={TOKEN1:[catalog('555')],TOKEN2:[catalog('777')]};immediate(manager.sync(one['id']));immediate(manager.sync(two['id']))
    requested=[]
    class DeferredGraph(FakeGraph):
        async def details(self,token,pid):requested.append(pid);await Deferred(pid);return {'id':pid,'name':'Page '+pid},None
    graph.__class__=DeferredGraph
    first=manager.refresh('555',PageRefresh(connection_id=one['id']),timezone='UTC')
    second=manager.refresh('777',PageRefresh(connection_id=two['id']),timezone='UTC')
    try:
        assert first.send(None)=='555' and second.send(None)=='777' and requested==['555','777']
        assert immediate(second)['page']['id']=='777' and immediate(first)['page']['id']=='555'
    finally:first.close();second.close()


@pytest.mark.parametrize('old_kind,new_kind',[('sync','refresh'),('refresh','sync'),('sync','sync')])
def test_canonical_metadata_uses_operation_start_freshness_across_all_sources(fixture,monkeypatch,old_kind,new_kind):
    manager,graph=fixture;one=connection(manager);two=connection(manager,TOKEN2)
    graph.catalogs={TOKEN1:[catalog()],TOKEN2:[catalog()]};immediate(manager.sync(one['id']));immediate(manager.sync(two['id']))
    frozen=datetime.now(timezone.utc)
    class FrozenClock:
        @staticmethod
        def now(_):return frozen
    monkeypatch.setattr(service_module,'datetime',FrozenClock)
    class OutOfOrderGraph(FakeGraph):
        async def page_catalog(self,token,*_):
            if token==TOKEN1:await Deferred('old-sync-response')
            newer=token==TOKEN2
            return [catalog(name='Newer Page' if newer else 'Older Page',category='New category' if newer else 'Old category',link='https://www.facebook.com/NewerPage' if newer else 'https://www.facebook.com/OlderPage')],True,None
        async def details(self,token,pid):
            if token==TOKEN1:await Deferred('old-detail-response')
            newer=token==TOKEN2
            return {'id':pid,'name':'Newer Page' if newer else 'Older Page','category':'New category' if newer else 'Old category',
                    'link':'https://www.facebook.com/NewerPage' if newer else 'https://www.facebook.com/OlderPage','about':'New detail' if newer else 'Old detail'},None
    graph.__class__=OutOfOrderGraph
    old=manager.sync(one['id']) if old_kind=='sync' else manager.refresh('555',PageRefresh(connection_id=one['id'],include_posts=False),timezone='UTC')
    try:
        assert old.send(None)==('old-sync-response' if old_kind=='sync' else 'old-detail-response')
        if new_kind=='sync':immediate(manager.sync(two['id']))
        else:immediate(manager.refresh('555',PageRefresh(connection_id=two['id'],include_posts=False),timezone='UTC'))
        before,_=manager.store.page('555')
        assert (before['name'],before['category'],before['link'])==('Newer Page','New category','https://www.facebook.com/NewerPage')
        immediate(old)
        after,_=manager.store.page('555')
        assert (after['name'],after['category'],after['link'])==('Newer Page','New category','https://www.facebook.com/NewerPage')
        assert after['last_metadata_started_at']==before['last_metadata_started_at']
        assert after['checked_at']==before['checked_at'] and after['details']==before['details'],'A stale response cannot relabel old details as newly checked'
        page=manager.pages(timezone='UTC')['items'][0]
        assert len(page['connections'])==2 and len(manager.pages(timezone='UTC')['items'])==1
    finally:old.close()
