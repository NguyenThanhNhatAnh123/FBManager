"""Isolated HTTP and durable scheduling checks. No real token or Meta calls."""
import asyncio
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
import httpx
import pytest
from page_manager.app import create_app
from page_manager.errors import AppError
from conftest import FixtureBox,immediate,mark_page_information_updated


VIDEO=(24).to_bytes(4,'big')+b'ftypisom'+b'\0'*4+b'isommp42'+b'\0\0\0\x10mdat' + b'x'*100


def run(coro):return immediate(coro)


class OfflineClient:
    """Exercise real ASGI middleware/routes without restricted loop sockets."""
    def __init__(self,transport,base_url):self.app=transport.app;self.url=urlsplit(base_url);self.cookie=''
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def get(self,path,**kwargs):return await self.request(path,'GET',**kwargs)
    async def post(self,path,**kwargs):return await self.request(path,'POST',**kwargs)
    async def request(self,path,method,**kwargs):
        target=urlsplit(path)
        headers={'host':self.url.netloc,**({'cookie':self.cookie} if self.cookie else {}),**kwargs.get('headers',{})}
        raw=json.dumps(kwargs['json']).encode() if 'json' in kwargs else kwargs.get('content',b'')
        if 'json' in kwargs:headers['content-type']='application/json'
        iterator=raw.__aiter__() if hasattr(raw,'__aiter__') else None
        if iterator is None:headers['content-length']=str(len(raw))
        sent=False;messages=[]
        async def receive():
            nonlocal sent
            if iterator:
                try:return {'type':'http.request','body':await anext(iterator),'more_body':True}
                except StopAsyncIteration:return {'type':'http.request','body':b'','more_body':False}
            if sent:return {'type':'http.disconnect'}
            sent=True;return {'type':'http.request','body':raw,'more_body':False}
        async def send(message):messages.append(message)
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':method,'scheme':'http',
            'path':target.path,'raw_path':target.path.encode(),'query_string':target.query.encode(),'root_path':'','client':('127.0.0.1',1234),'server':('127.0.0.1',self.url.port),
            'headers':[(key.lower().encode(),value.encode('latin1')) for key,value in headers.items()]}
        await self.app(scope,receive,send)
        start=next(message for message in messages if message['type']=='http.response.start')
        values={key.decode():value.decode() for key,value in start['headers']}
        if 'set-cookie' in values:
            cookies=SimpleCookie();cookies.load(values['set-cookie']);self.cookie='; '.join(key+'='+value.value for key,value in cookies.items())
        return httpx.Response(start['status'],headers=values,content=b''.join(message.get('body',b'') for message in messages if message['type']=='http.response.body'))


@pytest.fixture(autouse=True)
def no_socket_event_loop(monkeypatch):
    import anyio
    async def inline(call,*args,**kwargs):return call(*args,**kwargs)
    async def anyio_inline(call,*args,**kwargs):return call(*args)
    monkeypatch.setattr(asyncio,'to_thread',inline)
    monkeypatch.setattr(anyio.to_thread,'run_sync',anyio_inline)
    monkeypatch.setattr(httpx,'AsyncClient',OfflineClient)


def fixture_app(root,**kwargs):
    def forbidden():raise AssertionError('No external Graph request is authorized by these tests')
    return create_app(root,secret_box=FixtureBox(),graph_factory=forbidden,reel_graph_factory=forbidden,**kwargs)


def linked(app,pid='123',label='Fixture connection',*,information_updated=True):
    store=app.state.manager.store
    cid=store.save_connection({'label':label,'token_type':'page'},FixtureBox().encrypt('fixture-token-do-not-expose'))
    store.sync(cid,1,[{'id':pid,'name':'Fixture Page','sources':['page'],'tasks':['CREATE_CONTENT']}],True,'working',None,{'id':pid})
    if information_updated:mark_page_information_updated(store,pid,cid)
    return cid


async def client_session(client,prefix=''):
    response=await client.get(prefix+'/api/session');assert response.status_code==200
    return {'x-mpm-csrf':response.json()['csrf_token']}


async def upload(client,headers,prefix=''):
    result=await client.post(prefix+'/api/reels/assets',content=VIDEO,
        headers={**headers,'content-type':'application/octet-stream','x-filename':'fixture.mp4'})
    assert result.status_code==201,result.text
    return result.json()['asset']


def payload(cid,asset_id):
    return {'request_id':str(uuid4()),'page_id':'123','connection_id':cid,'asset_id':asset_id,'caption':'Fixture caption',
            'scheduled_local':(datetime.now(timezone.utc)+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S'),'timezone':'UTC'}


def test_page_rest_v1_bounds_lists_and_preserves_media_upload_and_spa_entry(tmp_path):
    frontend=tmp_path/'frontend';frontend.mkdir()
    (frontend/'index.html').write_text('<title>Fixture Page workspace</title>',encoding='utf-8')
    app=fixture_app(tmp_path);cid=linked(app);store=app.state.manager.store
    store.sync(cid,1,[{'id':str(pid),'name':f'Fixture Page {pid}','sources':['page'],'tasks':['CREATE_CONTENT']} for pid in range(123,153)],
               True,'working',None,{'id':'123'})
    disconnected=linked(app,label='Fixture disconnected connection');store.disconnect(disconnected)
    mark_page_information_updated(store,'124',cid)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            assert (await client.get('/api/v1/pages')).status_code==401
            headers=await client_session(client)
            assert (await client.get('/api/session')).json()['app']['rest_api_version']==1
            legacy=(await client.get('/api/pages')).json()
            assert len(legacy['items'])==30 and 'limit' not in legacy
            versioned=(await client.get('/api/v1/pages')).json()
            assert len(versioned['items'])==versioned['limit']==25 and versioned['total']==30
            for path in ('connections','reels','reels/assets'):
                assert (await client.get(f'/api/v1/{path}')).json()['limit']==25
            uploaded=await client.post('/api/v1/reels/assets',content=VIDEO+b'x'*130_000,
                headers={**headers,'content-type':'application/octet-stream','x-filename':'fixture-large.mp4'})
            assert uploaded.status_code==201
            asset_id=uploaded.json()['asset']['id']
            assert (await client.get('/api/v1/reels/assets')).json()['assets'][0]['id']==asset_id
            outside=(await client.get('/api/v1/reels/assets?limit=1&offset=7')).json()
            assert outside['offset']==7 and outside['total']==1 and outside['assets']==[],{key:outside[key] for key in ('offset','limit','total')}
            old_window=(await client.get('/api/reels/assets?limit=1&offset=7')).json()
            assert old_window['offset']==0 and old_window['assets'][0]['id']==asset_id
            connections=(await client.get('/api/v1/connections?connected_only=true&search=Fixture')).json()
            assert connections['total']==1 and [row['id'] for row in connections['items']]==[cid]
            for page_id in ('123','124'):
                created=await client.post('/api/v1/reels',json=payload(cid,asset_id)|{'page_id':page_id},headers=headers)
                assert created.status_code==201,created.text
            jobs=(await client.get(f'/api/v1/reels?page_id=123&connection_id={cid}&limit=1')).json()
            assert jobs['total']==1 and [job['page_id'] for job in jobs['jobs']]==['123']
            wrong_source=(await client.get(f'/api/v1/reels?page_id=123&connection_id={disconnected}')).json()
            assert wrong_source['total']==0 and wrong_source['jobs']==[]
            schema=(await client.get('/api/v1/openapi.json')).json()
            assert {'get','post'}<=set(schema['paths']['/api/v1/reels'])
            assert '/api/v1/session' not in schema['paths']
            assert '/api/v1/idea-pipeline' in schema['paths'] and '/api/v1/telegram-approval' in schema['paths']
            for path in ('/','/schedule','/saved','/settings'):
                response=await client.get(path)
                assert response.status_code==200 and 'Fixture Page workspace' in response.text
    run(scenario())


def test_notification_status_verifies_actual_file_and_expired_window_read_only(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app);ledger=app.state.publication_approvals;ledger.configure(True,1)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client);asset=await upload(client,headers)
            response=await client.post('/api/reels',json=payload(cid,asset['id']),headers=headers)
            assert response.status_code==201
            job=response.json()['job'];resolve=app.state.publication_status_resolvers['token']
            assert resolve(job['id'])['ready'] is False
            ticket=ledger.pending()['items'][0]
            ledger.decide(ticket['request_id'],ticket['binding_hash'],'approve',actor_id=99,chat_id=100,update_id=1,message_id=2)
            assert resolve(job['id'])['ready'] is True
            stored=app.state.manager.store.reel_asset(asset['id'])
            from pathlib import Path
            path=Path(stored['path']);path.write_bytes(b'tampered fixture')
            assert resolve(job['id'])['ready'] is False
            path.write_bytes(VIDEO)
            ledger.clock=lambda:datetime.fromisoformat(job['scheduled_at'])+timedelta(minutes=31)
            result=resolve(job['id'])
            assert result['status']=='needs_user' and result['step']=='approval_expired'
            assert app.state.manager.store.reel_job(job['id'])['status']=='scheduled'
    run(scenario())


def test_unedited_page_is_readable_and_uploadable_but_cannot_create_schedule(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app,information_updated=False)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client)
            assert (await client.get('/api/pages')).status_code==200
            asset=await upload(client,headers)
            response=await client.post('/api/reels',json=payload(cid,asset['id']),headers=headers)
            assert response.status_code==422 and response.json()['code']=='page_information_required'
    run(scenario())
    assert not app.state.manager.store.reel_jobs() and len(app.state.manager.store.reel_assets())==1


def test_upload_schedule_cache_preview_and_replay_remain_local(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client)
            assert (await client.get('/api/reels/config')).json()['scheduler_running'] is False
            asset=await upload(client,headers)
            assert 'path' not in asset and 'sha256' not in asset
            video=await client.get(asset['preview_url'],headers={'range':'bytes=0-15'})
            assert video.status_code==206 and video.content==VIDEO[:16]
            assert "media-src 'self' blob:" in video.headers['content-security-policy']
            values=payload(cid,asset['id'])
            first=await client.post('/api/reels',json=values,headers=headers)
            assert first.status_code==201,first.text
            row=first.json()['job'];assert row['page_id']=='123' and row['connection_id']==cid and row['status']=='scheduled'
            assert row['caption']=='Fixture caption' and row['request_id']==values['request_id']
            again=await client.post('/api/reels',json=values,headers=headers)
            assert again.status_code==201 and again.json()['replayed'] and again.json()['job']['id']==row['id']
            conflict=await client.post('/api/reels',json={**values,'caption':'Changed'},headers=headers)
            assert conflict.status_code==409
            jobs=(await client.get('/api/reels')).json()['jobs'];assert len(jobs)==1
            assert 'fixture-token' not in json.dumps(jobs) and 'path' not in json.dumps(jobs)
            assert app.state.manager.store.reel_job(row['id'])['checkpoint']=={}
    run(scenario())
    assert not (tmp_path/'manager.sqlite3').exists()


def test_immediate_api_stable_replay_explicit_cutoff_and_immutable_review_group(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app);store=app.state.manager.store;ledger=app.state.publication_approvals
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client);asset=await upload(client,headers)
            group={'id':str(uuid4()),'expected_count':2}
            values={k:v for k,v in payload(cid,asset['id']).items() if k!='scheduled_local'}
            values.update(publish_mode='on_approval',approve_by=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),review_batch=group,approval_policy=None)
            before=datetime.now(timezone.utc)
            first=await client.post('/api/reels',json=values,headers=headers)
            assert first.status_code==201,first.text
            job=first.json()['job'];assert job['publish_mode']=='on_approval' and job['review_batch']==group
            assert before<=datetime.fromisoformat(job['scheduled_at'])<=datetime.now(timezone.utc)
            assert job['publication_approval']['required'] and not job['publication_approval']['can_publish']
            assert ledger.settings()['required'] is False
            replay=await client.post('/api/reels',json=values,headers=headers)
            assert replay.status_code==201 and replay.json()['replayed'] and replay.json()['job']['scheduled_at']==job['scheduled_at']
            changed=await client.post('/api/reels',json=values|{'review_batch':{'id':str(uuid4()),'expected_count':2}},headers=headers)
            assert changed.status_code==409
            changed=await client.post('/api/reels',json=values|{'approve_by':(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()},headers=headers)
            assert changed.status_code==409
            second=await client.post('/api/reels',json=values|{'request_id':str(uuid4())},headers=headers)
            assert second.status_code==201
            extra=await client.post('/api/reels',json=values|{'request_id':str(uuid4())},headers=headers)
            assert extra.status_code==409 and extra.json()['code']=='publication_review_batch_conflict'
            assert len(store.reel_jobs())==2 and ledger.pending()['total']==2
    run(scenario())


def test_immediate_expired_deadline_and_fake_scheduled_override_are_rejected(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client);asset=await upload(client,headers)
            values={k:v for k,v in payload(cid,asset['id']).items() if k!='scheduled_local'}
            values.update(publish_mode='on_approval',approve_by='2020-01-01T00:00:00Z',approval_policy=None)
            response=await client.post('/api/reels',json=values,headers=headers)
            assert response.status_code==422 and response.json()['code']=='approval_deadline_in_past'
            response=await client.post('/api/reels',json=values|{'scheduled_local':'2030-01-01T10:00'},headers=headers)
            assert response.status_code==422
    run(scenario())
    assert not app.state.manager.store.reel_jobs()


def test_new_review_defaults_have_no_cutoff_and_legacy_uuid_replays_keep_frozen_policy(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client);asset=await upload(client,headers)
            values={k:v for k,v in payload(cid,asset['id']).items() if k!='scheduled_local'}
            values.update(publish_mode='on_approval')
            response=await client.post('/api/reels',json=values,headers=headers)
            assert response.status_code==201,response.text
            job=response.json()['job'];approval=job['publication_approval']
            assert job['approval_policy']=='remind_until_decision_v1'
            assert approval['required'] and approval['state']=='pending'
            assert approval['approve_by'] is None and approval['publish_window_end'] is None
            cutoff=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
            contradictory=values|{'request_id':str(uuid4()),'approve_by':cutoff}
            assert (await client.post('/api/reels',json=contradictory,headers=headers)).status_code==422
            legacy=contradictory|{'approval_policy':None}
            created=await client.post('/api/reels',json=legacy,headers=headers)
            assert created.status_code==201,created.text
            replay=await client.post('/api/reels',json={k:v for k,v in legacy.items() if k!='approval_policy'},headers=headers)
            assert replay.status_code==201,replay.text
            assert replay.json()['replayed'] and replay.json()['job']['id']==created.json()['job']['id']
            assert replay.json()['job']['publication_approval']['approve_by']==cutoff
    run(scenario())


def test_page_source_binding_past_time_and_immutable_review_input(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app);other=linked(app,'999','Other source')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client);asset=await upload(client,headers);values=payload(cid,asset['id'])
            assert (await client.post('/api/reels',json={**values,'connection_id':other},headers=headers)).status_code==403
            assert (await client.post('/api/reels',json={**values,'scheduled_local':'2020-01-01T00:00','timezone':'UTC'},headers=headers)).status_code==422
            assert (await client.post('/api/reels',json={**values,'scheduled_local':'2027-03-14T02:30','timezone':'America/Los_Angeles'},headers=headers)).json()['code']=='nonexistent_time'
            assert (await client.post('/api/reels',json={**values,'scheduled_local':'2027-11-07T01:30','timezone':'America/Los_Angeles'},headers=headers)).json()['code']=='ambiguous_time'
            for patch in ({'token':'fixture-secret'},{'path':'C:/private.mp4'},{'fold':True},{'caption':'\ud800'}):
                assert (await client.post('/api/reels',json={**values,**patch},headers=headers)).status_code==422
            assert app.state.manager.store.reel_jobs()==[]
    run(scenario())


def test_legacy_business_scope_needs_resync_and_never_rebinds_saved_reels(tmp_path):
    from page_manager.graph import GraphError

    app=fixture_app(tmp_path);store=app.state.manager.store
    scoped=store.save_connection({'label':'Fixture BM 88','token_type':'user','business_id':'88'},
                                 FixtureBox().encrypt('fixture-user-token'))
    other=linked(app,'123','Other source with same Page')
    cached=[{'id':pid,'name':'Same Page name','sources':['accounts'],'tasks':['CREATE_CONTENT'],
             'encrypted_page_token':FixtureBox().encrypt('fixture-previous-page-token')} for pid in ('123','124')]
    store.sync(scoped,1,cached,True,'working',None,{'id':'77'},verified_business_id='88')
    mark_page_information_updated(store,'124',scoped)

    async def save_initial():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client);asset=await upload(client,headers)
            response=await client.post('/api/reels',json=payload(scoped,asset['id']),headers=headers)
            assert response.status_code==201,response.text
            return asset['id'],response.json()['job']['id']
    asset_id,job_id=run(save_initial());saved=store.reel_job(job_id)

    # Old releases recorded me/accounts membership without proof of the BM.
    with store.connect() as db:db.execute('ALTER TABLE connections DROP COLUMN business_scope_revision')
    app=fixture_app(tmp_path);manager=app.state.manager;store=manager.store

    class SyncFixture:
        failure=None
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def identity(self,token):return {'id':'77','name':'Fixture User'}
        async def page_catalog(self,token,kind,identity,business):
            assert (kind,identity,business)==('user','77','88')
            return [{'id':'124','name':'Same Page name','_source':'accounts'}],self.failure is None,self.failure

    graph=SyncFixture();manager.graph_factory=lambda:graph

    async def check_restored():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            headers=await client_session(client)
            blocked=await client.post('/api/reels',json=payload(scoped,asset_id),headers=headers)
            assert blocked.status_code==409 and blocked.json()['code']=='business_scope_unverified'
            assert manager.pages(connection_id=scoped)['items']==[]
            assert store.source('123',other)['id']==other and store.reel_job(job_id)==saved
            sync=await client.post(f'/api/connections/{scoped}/sync',headers=headers)
            assert sync.json()['connection']['business_scope_verified'] is True
            assert [row['id'] for row in manager.pages(connection_id=scoped)['items']]==['124']
            assert store.source('124',scoped)['encrypted_page_token'] is None
            assert manager.pages(connection_id=scoped)['items'][0]['connections'][0]['tasks'] is None
            outside=await client.post('/api/reels',json=payload(scoped,asset_id),headers=headers)
            assert outside.status_code==403 and outside.json()['code']=='page_access_required'
            inside=await client.post('/api/reels',json={**payload(scoped,asset_id),'page_id':'124'},headers=headers)
            assert inside.status_code==201 and inside.json()['job']['connection_id']==scoped
            graph.failure=GraphError('permission_limited',200)
            failed=await client.post(f'/api/connections/{scoped}/sync',headers=headers)
            assert failed.json()['complete'] is False and failed.json()['connection']['page_count']==0
            blocked=await client.post('/api/reels',json={**payload(scoped,asset_id),'page_id':'124'},headers=headers)
            assert blocked.status_code==409 and blocked.json()['code']=='business_scope_unverified'
            graph.failure=None
            assert (await client.post(f'/api/connections/{scoped}/sync',headers=headers)).json()['complete'] is True
            changed=await client.request(f'/api/connections/{scoped}','PUT',headers=headers,json={
                'label':'Fixture BM 99','token_type':'user','business_id':'99','revision':store.connection(scoped)['revision']})
            assert changed.status_code==200 and changed.json()['business_scope_verified'] is False
            assert store.connection(scoped)['token_revision']==2 and manager.pages(connection_id=scoped)['items']==[]
            assert store.source('123',other)['id']==other and store.reel_job(job_id)==saved
    run(check_restored())


def test_upload_boundary_authenticates_before_reading_and_limits_stream(tmp_path,monkeypatch):
    import page_manager.app as module
    app=fixture_app(tmp_path)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            touched=[]
            async def forbidden_stream():
                touched.append(True)
                yield VIDEO
            response=await client.post('/api/reels/assets',content=forbidden_stream(),headers={'content-type':'application/octet-stream','x-filename':'fixture.mp4'})
            assert response.status_code==401 and not touched
            headers=await client_session(client)
            assert (await client.post('/api/reels/assets',content=VIDEO,headers={'content-type':'application/octet-stream','x-filename':'fixture.mp4'})).status_code==403
            monkeypatch.setattr(module,'MAX_VIDEO_BYTES',64)
            async def chunks():
                yield VIDEO[:40]
                yield VIDEO[40:80]
            response=await client.post('/api/reels/assets',content=chunks(),headers={**headers,'content-type':'application/octet-stream','x-filename':'fixture.mp4'})
            assert response.status_code==413,response.text
            assert app.state.manager.store.reel_assets()==[] and list((tmp_path/'reel-assets').iterdir())==[]
            assert (await client.post('/api/reels/assets',content=b'bad',headers={**headers,'content-type':'text/plain'})).status_code==415
    run(scenario())


def test_checkpoint_latches_video_binding_and_restart_recovery(tmp_path):
    app=fixture_app(tmp_path);cid=linked(app);store=app.state.manager.store
    async def stream():yield VIDEO
    asset=run(app.state.reel_assets.ingest(stream(),'fixture.mp4'))
    values=payload(cid,asset['id']);values.update({'page_name':'Fixture Page','connection_label':'Fixture connection','asset_name':'fixture.mp4','token_revision':1,
        'scheduled_at':'2027-01-01T00:00:00+00:00','fold':0})
    job,_=store.reel_create(values)
    store.reel_checkpoint(job['id'],{'start_attempted':True},status='starting')
    store.reel_checkpoint(job['id'],{'start_ack':True},video_id='456')
    with pytest.raises(AppError):store.reel_checkpoint(job['id'],{'start_attempted':False})
    with pytest.raises(AppError):store.reel_update(job['id'],video_id='999')
    with pytest.raises(AppError):store.reel_update(job['id'],page_id='999')
    store.reel_recover();row=store.reel_job(job['id'])
    assert row['status']=='needs_review' and row['video_id']=='456' and row['checkpoint']['start_attempted']
    store.reel_update(job['id'],status='completed',published_url='https://www.facebook.com/reel/456')
    with pytest.raises(AppError):store.reel_update(job['id'],status='scheduled')


def test_mounted_token_routes_use_own_csrf_and_data(tmp_path):
    from fastapi import FastAPI
    child=fixture_app(tmp_path/'meta',port=8000,base_path='/pages');parent=FastAPI();parent.mount('/pages',child);cid=linked(child)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=parent),base_url='http://127.0.0.1:8000') as client:
            headers=await client_session(client,'/pages')
            assert (await client.get('/pages/api/session')).json()['app']['token_reels']
            asset=await upload(client,headers,'/pages')
            result=await client.post('/pages/api/reels',json=payload(cid,asset['id']),headers=headers)
            assert result.status_code==201
            assert (await client.post('/pages/api/reels',json=payload(cid,asset['id']),headers={'x-mpm-csrf':'chrome-session'})).status_code==403
            assert (await client.get('/api/reels')).status_code==404
            assert len(child.state.manager.store.reel_jobs())==1
    run(scenario())


@pytest.mark.identity
def test_secondary_page_workspace_uses_private_sources_and_has_no_producer_routes(tmp_path):
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from via_auth import IdentityGate,IdentityStore,workspace_host
    from via_auth.middleware import COOKIE

    identity=IdentityStore(tmp_path/'central-identity')
    accounts={account['username']:account for account in identity.bootstrap()}
    operator_session=identity.change_password(2,accounts['operator1']['password'],'Fixture operator password 82')
    admin_session=identity.change_password(1,accounts['admin']['password'],'Fixture administrator password 82')
    operator_cookie=COOKIE+'='+operator_session
    operator_csrf=identity.resolve(operator_session)['csrf_token']
    primary_root=tmp_path/'primary'/'meta-page-manager'
    secondary_root=tmp_path/'secondary'/'meta-page-manager'
    primary=fixture_app(primary_root,base_path='/pages',identity_store=identity,workspace_user_id=1)
    linked(primary,label='Primary only')

    def forbidden(*_args,**_kwargs):raise AssertionError('Secondary workspace must not initialize a producer or shared uploader')
    secondary=fixture_app(secondary_root,base_path='/pages',port=8042,identity_store=identity,workspace_user_id=2,
                          secondary_workspace=True,podsach_bridge_factory=forbidden,uploader_connection_factory=forbidden,
                          idea_runs_factory=forbidden,producer_diagnostics_factory=forbidden,idea_automation_factory=forbidden)
    assert secondary.state.identity_store is identity
    assert secondary.state.video_source.directory==secondary_root.parent/'media'/'videos'
    assert not hasattr(secondary.state,'idea_pipeline') and not hasattr(secondary.state,'producer_diagnostics')
    assert hasattr(primary.state,'idea_pipeline')
    assert not any(getattr(route,'path','').startswith('/api/idea-pipeline') for route in secondary.routes)

    async def scenario():
        mounted=Starlette(routes=[Mount('/pages',app=secondary)])
        gateway=IdentityGate(mounted,identity,port=8042,workspace_user_id=2,workspace_owner=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url='http://'+workspace_host(2,8042)) as client:
            session=await client.get('/pages/api/session',headers={'origin':'http://127.0.0.1:8042','cookie':operator_cookie})
            assert session.status_code==200 and session.json()['app']['idea_pipeline'] is False
            headers={'x-mpm-csrf':session.json()['csrf_token'],'x-via-csrf':operator_csrf,
                     'origin':'http://127.0.0.1:8042','cookie':operator_cookie+'; '+client.cookie}
            assert (await client.get('/pages/api/connections',headers=headers)).json()['items']==[]
            created=await client.post('/pages/api/connections',headers=headers,json={
                'label':'Secondary only','token_type':'page','token':'fixture-secondary-token'})
            assert created.status_code==201
            assert (await client.get('/pages/api/reels',headers=headers)).status_code==200
            assert (await client.get('/pages/api/video-source',headers=headers)).status_code==200
            assert (await client.get('/pages/api/idea-pipeline/upload/status',headers=headers)).status_code==404
            assert (await client.post('/pages/api/idea-pipeline/scan',headers=headers,json={})).status_code==404
            assert (await client.get('/pages/api/session',headers={**headers,'host':workspace_host(1,8042)})).status_code==403
            rejected=await client.get('/pages/api/connections',headers={**headers,'cookie':COOKIE+'='+admin_session+'; '+client.cookie})
            assert rejected.status_code==403 and rejected.json()['code']=='workspace_mismatch'
    run(scenario())
    assert [row['label'] for row in primary.state.manager.store.connections()]==['Primary only']
    assert [row['label'] for row in secondary.state.manager.store.connections()]==['Secondary only']


def test_standalone_lifespan_runs_and_stops_own_timer(tmp_path,monkeypatch):
    app=fixture_app(tmp_path)
    # The real supervisor owns an asyncio Task. Only external/background
    # producers are inert; manually advancing this coroutine cannot model it.
    for name in ('video_source','idea_workspace','idea_automation','idea_runs','idea_views','telegram_approval'):
        monkeypatch.setattr(getattr(app.state,name),'tick',lambda:None)
    async def scenario():
        ticked=asyncio.Event();original=app.state.reels.tick
        def tick():original();ticked.set()
        monkeypatch.setattr(app.state.reels,'tick',tick)
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(ticked.wait(),1)
            assert app.state.reels.status()['scheduler_running']
            assert app.state.scheduler_supervisor.snapshot()['ready']
            monitor=app.state.scheduler_supervisor.task
            assert monitor is not asyncio.current_task() and not monitor.done()
        assert not app.state.reels.status()['scheduler_running']
        assert monitor.done() and app.state.shutdown_failures=={}
    asyncio.run(scenario())
