"""Read-only SQL workspace windows using temporary SQLite and no browser/network."""
import asyncio
import json
import tracemalloc
from datetime import datetime,timedelta,timezone
from http.cookies import SimpleCookie
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from starlette.websockets import WebSocketDisconnect

from app.api import routes,workspace
from app.core.config import Config
from app.database.session import Database
from app.main import install_rest_v1
from app.models.entities import (Profile,Proxy,BmProfile,BmProxy,BmBrowserSettings,FacebookAccount,
    FacebookPage,OutlookLogin,Task,TaskRun,Event,PageRecipientJob,ReelJob,ReelAsset)
from app.services.state import State
from app.services.page_recipient import PageRecipientManager
from app.services.reel_scheduler import ReelScheduler


@pytest.fixture(scope='module')
def data(tmp_path_factory):
    root=tmp_path_factory.mktemp('workspace-sql');config=Config(root=root,data_dir=root/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    contexts={1:object()};launches={2:object()};reserved={3};errors={4:'Synthetic browser error'}
    browser=SimpleNamespace(contexts=contexts,launches=launches,reserved=reserved,errors=errors,
        status=lambda pid:'Running' if pid in contexts else 'Queued' if pid in launches else 'Starting' if pid in reserved else 'Error' if pid in errors else 'Stopped',
        mode=lambda pid:'manual' if pid in contexts else None,extension_profiles=set(),facebook_profiles=set(),outlook_profiles=set())
    tasks=SimpleNamespace(profile_jobs={},jobs={})
    recipient=PageRecipientManager(db,state,browser,tasks,config)
    reels=ReelScheduler(db,state,browser,tasks,config)
    import app.automation.manager as task_module
    patch=pytest.MonkeyPatch();patch.setattr(task_module,'decrypt_credentials',lambda value:('task',value))
    service=SimpleNamespace(db=db,state=state,browsers=browser,recipient=recipient,reels=reels,tasks=tasks,
        proxies=SimpleNamespace(checking={1}),outlook=SimpleNamespace(jobs={}),config=config,
        resources=SimpleNamespace(current={'memory_available_mb':8192},blocked_reason=lambda _:None),
        extensions=SimpleNamespace(rows=lambda ids:[{'id':pid,'name':f'Profile {pid}'} for pid in ids]))
    stamp=datetime.now(timezone.utc);start=(stamp-timedelta(hours=12)).isoformat();end=(stamp+timedelta(hours=12)).isoformat();day=stamp.date().isoformat()
    page_records=[]
    with db.session.begin() as session:
        session.add_all([Proxy(id=i,host=f'192.0.2.{i%250+1}',port=10000+i,protocol='http',identity=f'proxy-{i}',encrypted_credentials='fixture-private-proxy',status='Working' if i%2 else 'Failed') for i in range(1,132)]);session.flush()
        session.add_all([Profile(id=i,name='Áo Funny %_ literal' if i==1 else f'Profile {i:03}',profile_path=str(root/'profiles'/str(i)),proxy_id=i if i<128 else None) for i in range(1,129)]);session.flush()
        session.add(BmProfile(profile_id=127,portfolios='[{"bm_id":"555","name":"Private BM"}]',note='BM private note'));session.flush()
        session.add_all([BmProxy(proxy_id=127,profile_id=127),BmProxy(proxy_id=131,profile_id=None),BmBrowserSettings(profile_id=127,mobile_enabled=True)])
        session.add_all([FacebookAccount(profile_id=i,name=f'Account {i}',status='checked',check_date=day,day_start=start,day_end=end,timezone='UTC') for i in range(1,129)])
        session.add_all([OutlookLogin(profile_id=i,email=f'fixture-{i}@example.test',status='success') for i in range(1,129)])
        for pid in range(1,129):
            for index in range(4):
                rowid=pid*10+index;url=f'https://www.facebook.com/profile.php?id={pid*1000+index}'
                if index==0 and pid in (1,2):url='https://m.facebook.com/profile.php?id=555&ref=fixture' if pid==1 else 'https://www.facebook.com/pages/Shared/555'
                posts=json.dumps([{'id':f'{pid}_{index}_{p}','date':day,'url':f'https://www.facebook.com/{pid}/posts/{p}','message':'TEST_POST_CONTENT '+'p'*256} for p in range(8)])
                row=FacebookPage(id=rowid,profile_id=pid,page_key=f'fixture-{rowid}',name='Áo Page %_' if pid==1 and index==0 else f'Page {pid:03}-{index}',url=url,status='posted' if index%2 else 'not_seen',checked_at=stamp.isoformat(),check_date=day,day_start=start,day_end=end,timezone='UTC',posts=posts)
                session.add(row);page_records.append((rowid,pid,url))
        for index in range(130):
            rowid=10000+index;url=f'https://www.facebook.com/profile.php?id={900000+index}'
            session.add(FacebookPage(id=rowid,profile_id=1,page_key=f'fixture-{rowid}',name=f'Extra Page {index:03}',url=url,status='posted',checked_at=stamp.isoformat(),check_date=day,day_start=start,day_end=end,timezone='UTC',posts=json.dumps([{'date':day,'message':'TEST_POST_CONTENT '+'x'*256}])));page_records.append((rowid,1,url))
        session.add_all([Task(id=i,name='Áo Task %_' if i==1 else f'Task {i:03}',configuration='{"name":"Fixture only","authorized":true,"actions":[{"type":"open_url","url":"https://example.test"}]}') for i in range(1,128)]);session.flush()
        session.add_all([TaskRun(task_id=tid,profile_id=tid,profile_name=f'Profile {tid:03}',status='Running' if run==1 else 'Pending' if run==2 else 'Completed',result='["UNSELECTED_RUN_PAYLOAD '+('r'*2048)+'"]',progress=run*10) for tid in range(1,128) for run in range(5)])
        session.add_all([Event(profile_id=1,level='warning' if i%2 else 'info',message='Áo log %_' if i==1 else f'Event {i:03}') for i in range(1,128)])
        session.add_all([PageRecipientJob(profile_id=i,status='completed' if i%2 else 'needs_user',configuration='{}',checkpoint='{"candidates":[]}',fields='{}',page_id=str(700000+i),page_name='Áo recipient %_' if i==1 else f'Recipient Page {i:03}') for i in range(1,128)])
        session.add_all([ReelAsset(id=f'{i:032x}',name='Áo clip %_.mp4' if i==1 else f'Clip {i:03}.mp4',path=str(root/'private-assets'/f'{i}.mp4'),size=1024,sha256=f'{i:064x}',mime='video/mp4') for i in range(1,128)]);session.flush()
        session.add_all([ReelJob(id=i,profile_id=i,profile_name=f'Profile {i:03}',page_url=f'https://www.facebook.com/profile.php?id={700000+i}',page_name=f'Reel Page {i:03}',asset_id=f'{i:032x}',caption='Áo caption %_' if i==1 else 'Caption '+('c'*2048),scheduled_at=stamp.isoformat(),timezone='UTC',status='completed' if i%2 else 'scheduled',archived_at=stamp.isoformat() if i%17==0 else None,checkpoint='{}') for i in range(1,128)])
    app=FastAPI();app.state.services=service;app.include_router(workspace.router);app.include_router(routes.router)
    install_rest_v1(app,('profiles','reels'),source_routes=routes.router.routes,read_routes=workspace.router.routes,
                    read_aliases={'reels/assets':'reel-assets'})
    client=TestClient(app,raise_server_exceptions=True)
    yield SimpleNamespace(service=service,client=client,config=config,pages=page_records,root=root)
    client.close();patch.undo();db.engine.dispose()


def get(data,path):
    response=data.client.get('/api/workspace'+path)
    assert response.status_code==200,response.text
    return response.json()


def test_versioned_catalog_reads_only_requested_rows_and_selectors(data):
    client=data.client
    page=client.get('/api/v1/profiles',params={'scope':'all','limit':10})
    old_selectors=client.get('/api/workspace/options')
    assert page.status_code==old_selectors.status_code==200
    assert len(page.json()['items'])==10 and page.json()['total']==128
    assert len(page.content)<len(old_selectors.content)/5
    assert all(not item['facebook']['pages'] for item in page.json()['items'])
    empty=client.get('/api/v1/profiles',params={'scope':'all','limit':10,'offset':500}).json()
    assert empty['items']==[] and empty['offset']==500
    one=client.get('/api/v1/profiles/1',params={'preview':2}).json()
    assert one['id']==1 and len(one['facebook']['pages'])==2
    chosen=client.get('/api/v1/profile-ids',params={'scope':'regular','ids':'1,127,128'}).json()
    assert chosen['ids']==[1,128]
    assert set(client.get('/api/v1/options',params={'include':'tasks'}).json())=={'tasks'}
    assert len(client.get('/api/v1/reels/assets',params={'limit':2}).json()['items'])==2


def test_versioned_selectors_are_bounded_searchable_and_keep_activity_guards(data):
    profiles=data.client.get('/api/v1/profile-options',params={'limit':25}).json()
    assert profiles['total']==127 and len(profiles['items'])==25
    assert all(row['bm'] is None and 'profile_path' not in row and 'proxy' not in row for row in profiles['items'])
    first=profiles['items'][0]
    assert first['browser_mode']=='manual' and first['task']['status'] in ('Running','Pending')
    assert set(first['facebook'])=={'name','status'} and set(first['outlook'])=={'email','status','active'}
    last=data.client.get('/api/v1/profile-options',params={'q':'Profile 128'}).json()
    assert [row['id'] for row in last['items']]==[128]
    assert data.client.get('/api/v1/profile-options',params={'scope':'bm'}).status_code==422
    proxies=data.client.get('/api/v1/proxy-options').json()
    assert {row['id'] for row in proxies['items']}=={128,129,130}
    assigned=data.client.get('/api/v1/proxy-options',params={'profile_id':1}).json()
    assert {row['id'] for row in assigned['items']}=={1,128,129,130}
    assert all(set(row)=={'id','host','port','protocol'} for row in assigned['items'])
    tasks=data.client.get('/api/v1/task-options',params={'limit':25,'offset':25}).json()
    assert tasks['total']==127 and len(tasks['items'])==25 and all(set(row)=={'id','name'} for row in tasks['items'])
    assert not any(marker in json.dumps([profiles,assigned,tasks]) for marker in ('fixture-private-proxy','UNSELECTED_RUN_PAYLOAD','TEST_POST_CONTENT'))
    for route in ('profile-options','proxy-options','task-options','task-history'):
        assert data.client.get('/api/v1/'+route,params={'limit':101}).status_code==422


def test_versioned_history_loads_results_only_on_selected_detail(data):
    history=data.client.get('/api/v1/task-history',params={'limit':10}).json()
    assert history['total']==635 and len(history['items'])==10
    assert all(row['result']==[] for row in history['items']) and 'UNSELECTED_RUN_PAYLOAD' not in json.dumps(history)
    chosen=history['items'][0]
    detail=data.client.get('/api/v1/task-runs/'+str(chosen['id'])).json()
    assert detail['id']==chosen['id'] and 'UNSELECTED_RUN_PAYLOAD' in detail['result'][0]
    assert data.client.get('/api/v1/task-runs/999999').status_code==404
    assert data.client.get('/api/v1/task-history',params={'offset':99999}).json()['items']==[]


def test_picker_and_history_queries_do_not_select_unused_large_or_private_columns(data):
    from sqlalchemy import event
    statements=[]
    def capture(_connection,_cursor,statement,_parameters,_context,_many):statements.append(statement)
    event.listen(data.service.db.engine,'before_cursor_execute',capture)
    try:
        for endpoint in ('profile-options','proxy-options','task-options','task-history'):
            assert data.client.get('/api/v1/'+endpoint).status_code==200
    finally:event.remove(data.service.db.engine,'before_cursor_execute',capture)
    sql='\n'.join(statements).lower()
    assert 'encrypted_credentials' not in sql and 'configuration' not in sql and 'task_runs.result' not in sql and 'facebook_pages.posts' not in sql


def test_reel_page_selector_checks_policy_only_for_visible_canonical_targets(data,monkeypatch):
    consulted=[]
    def policy(url):
        consulted.append(url)
        return {'page_id':'555','can_publish':True,'information_status':'updated','posting_block_reason':None}
    monkeypatch.setattr(data.service.reels,'posting_eligibility',policy)
    value=data.client.get('/api/v1/reel-page-options',params={'profile_id':1,'limit':25}).json()
    assert value['total']==134 and len(value['items'])==len(consulted)==25
    assert all(row['source']=='facebook_scan' for row in value['items'])
    consulted.clear()
    specific=data.client.get('/api/v1/reel-page-options',params={'profile_id':1,'q':'Extra Page 129'}).json()
    assert specific['total']==1 and len(consulted)==1
    assert data.client.get('/api/v1/reel-page-options',params={'profile_id':127}).status_code==400
    assert data.client.get('/api/v1/reel-page-options',params={'profile_id':999999}).status_code==404
    assert data.client.get('/api/v1/reel-page-options',params={'profile_id':1,'limit':101}).status_code==422
    with data.service.db.session.begin() as session:
        job=session.scalar(select(PageRecipientJob).where(PageRecipientJob.profile_id==1))
        original=(job.checkpoint,job.page_name)
        job.checkpoint=json.dumps({'accepted':True,'switched':True,'page_url':'https://m.facebook.com/profile.php?id=555&tracking=fixture'})
        job.page_name='Receipt override'
    try:
        consulted.clear()
        received=data.client.get('/api/v1/reel-page-options',params={'profile_id':1,'q':'Receipt override'}).json()
        assert received['total']==len(consulted)==1 and received['items'][0]['source']=='received'
        assert received['items'][0]['page_url']=='https://www.facebook.com/profile.php?id=555'
        all_targets=data.client.get('/api/v1/reel-page-options',params={'profile_id':1}).json()
        assert all_targets['total']==134,'A received target overrides the same canonical cached Page instead of duplicating it'
    finally:
        with data.service.db.session.begin() as session:
            job=session.scalar(select(PageRecipientJob).where(PageRecipientJob.profile_id==1));job.checkpoint,job.page_name=original


def test_all_windows_bound_count_and_reach_records_beyond_100(data):
    paths=['/profiles?scope=all','/pages?zone=UTC','/facebook-pages?profile_id=1&zone=UTC','/proxies','/tasks','/recipient-jobs','/reels?include_archived=true','/reel-assets','/logs?profile_id=1','/authenticator']
    for path in paths:
        join='&' if '?' in path else '?'
        first=get(data,path+join+'limit=10&offset=0');last=get(data,path+join+'limit=10&offset=100')
        assert first['total']>100 and last['total']==first['total'],path
        assert len(first['items'])==len(last['items'])==10 and last['offset']==100,path
        assert first['items']!=last['items'],path
    assert get(data,'/profiles?scope=all&limit=10&offset=9999')['offset']==120,'Out-of-range offsets clamp to the last real SQL window'


def test_canonical_page_aliases_group_before_limits_and_private_proxy_scope(data):
    expected=len({workspace.canonical_key(url,pid,rowid) for rowid,pid,url in data.pages})
    catalog=get(data,'/pages?limit=10&zone=UTC&q=555')
    assert catalog['total']==1 and catalog['items'][0]['key']=='id:555'
    assert catalog['items'][0]['source_count']==2 and len(catalog['items'][0]['sources'])==2
    assert get(data,'/pages?limit=10&zone=UTC')['total']==expected
    assert get(data,'/profiles?scope=bm')['total']==1
    assert get(data,'/profiles?scope=proxy')['total']==126
    assert get(data,'/profiles?scope=regular')['total']==127
    proxy_ids={row['id'] for row in get(data,'/proxies?limit=100&offset=100')['items']}
    assert 127 not in proxy_ids and 131 not in proxy_ids,'BM proxies are never offered by regular Proxy Manager'
    bm=get(data,'/profiles?scope=bm')['items'][0]
    assert bm['bm']['mobile_enabled'] is True and bm['proxy']['id']==127


@pytest.mark.parametrize('path',['/profiles?scope=all','/pages?zone=UTC','/proxies','/tasks','/recipient-jobs','/reels?include_archived=true','/reel-assets','/logs?profile_id=1'])
def test_unicode_casefold_and_literal_wildcards(data,path):
    join='&' if '?' in path else '?'
    if path=='/proxies':
        assert get(data,path+join+'q='+quote('%_'))['total']==1
    else:
        assert get(data,path+join+'q='+quote('áo'))['total']>=1
        literal=get(data,path+join+'q='+quote('%_'))
        assert literal['total']==(134 if path.startswith('/pages?') else 1),'Percent/underscore must remain literal text, not SQL wildcards'


def test_profile_and_catalog_previews_never_parse_post_or_run_json_outside_detail(data,monkeypatch):
    original=workspace.json.loads
    def guarded(raw,*args,**kwargs):
        assert 'TEST_POST_CONTENT' not in str(raw),'List projections cannot parse post JSON'
        assert 'UNSELECTED_RUN_PAYLOAD' not in str(raw),'List projections cannot parse historical run results'
        return original(raw,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(workspace.json,'loads',guarded)
        profiles=get(data,'/profiles?scope=all&limit=10')
        catalog=get(data,'/pages?limit=10&zone=UTC')
        options=get(data,'/options')
        assert all(len(row['facebook']['pages'])<=3 and all(not page['posts'] for page in row['facebook']['pages']) for row in profiles['items'])
        assert all(not row['display']['posts'] for row in catalog['items'])
        assert all(not(row.get('facebook')or{}).get('pages') for row in options['profiles'])
        assert all(not(row.get('bm')or{}).get('portfolios') for row in options['profiles'])
        assert all(not row['actions'] for row in options['tasks'])
        assert all(row['id'] not in (127,131) for row in options['proxies'])
    detail=get(data,'/facebook-pages?profile_id=1&zone=UTC&status=posted&limit=10')
    assert len(detail['items'])==10 and all(row['posts'] for row in detail['items'])


def test_system_aggregate_and_invalidation_ws_do_not_construct_snapshots(data,monkeypatch):
    monkeypatch.setattr(routes,'profile_rows',lambda *_:pytest.fail('System aggregation cannot construct profile snapshots'))
    aggregate=routes.system_snapshot(data.service)
    assert aggregate['total_profiles']==128 and aggregate['total_proxies']==129 and aggregate['running_tasks']==127
    assert 'profiles' not in aggregate
    from app.main import create_app
    import app.main as main_module
    app=create_app(data.config);app.state.services=data.service
    session=next(route.endpoint for route in app.routes if getattr(route,'path','')=='/api/session')
    response=asyncio.run(session());cookies=SimpleCookie();cookies.load(response.headers['set-cookie']);session_token=cookies['cm_session'].value
    sent=[]
    class Socket:
        scope={}
        client=SimpleNamespace(host='127.0.0.1')
        headers={'origin':'http://127.0.0.1:8000'}
        cookies={'cm_session':session_token}
        query_params={'mode':'invalidate'}
        async def accept(self):pass
        async def close(self,code):pytest.fail(f'Authorized fixture WebSocket rejected: {code}')
        async def send_json(self,value):sent.append(value);raise WebSocketDisconnect()
    monkeypatch.setattr(main_module,'snapshot',lambda *_:pytest.fail('Invalidate WebSocket cannot build legacy snapshot'))
    websocket=next(route.endpoint for route in app.routes if getattr(route,'path','')=='/ws')
    asyncio.run(websocket(Socket()))
    assert len(sent)==1 and sent[0]['kind']=='changed' and 'profiles' not in sent[0]
    assert not data.service.state.subscribers


def test_payload_and_python_heap_are_bounded_while_legacy_array_contract_survives(data):
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=data.service)))
    tracemalloc.start();bounded=get(data,'/profiles?scope=all&limit=10');_,small_peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    tracemalloc.start()
    with data.service.db.session() as session:legacy=routes.profile_rows(data.service,session)
    _,legacy_peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    small_bytes=len(json.dumps(bounded).encode());legacy_bytes=len(json.dumps(legacy).encode())
    assert isinstance(legacy,list) and len(legacy)==128
    assert small_bytes<legacy_bytes/10 and small_peak<legacy_peak/3
    assert isinstance(asyncio.run(routes.list_profiles(request)),list),'Legacy /api/profiles remains an array'
    assert 'fixture-private-proxy' not in json.dumps(bounded)
    print({'chrome_profile_total':128,'window_rows':10,'window_json_bytes':small_bytes,'legacy_json_bytes':legacy_bytes,'window_python_peak_bytes':small_peak,'legacy_python_peak_bytes':legacy_peak})


@pytest.mark.parametrize('suffix',['limit=0','limit=101','offset=-1','limit=1.5'])
def test_workspace_rejects_unbounded_or_invalid_windows(data,suffix):
    for path in ('/profiles','/pages','/proxies','/tasks','/recipient-jobs','/reels','/reel-assets'):
        assert data.client.get('/api/workspace'+path+'?'+suffix).status_code==422


def test_profile_bulk_ids_stay_scalar_and_respect_proxy_bm_runtime_scope(data):
    value=get(data,'/profile-ids?scope=proxy&can_run=true')
    assert value['total']==125 and all(isinstance(pid,int) for pid in value['ids']) and 1 not in value['ids'] and 127 not in value['ids']


def test_canonical_post_counts_union_sources_deduplicate_urls_and_ignore_other_dates(data):
    originals={}
    try:
        with data.service.db.session.begin() as session:
            first=session.get(FacebookPage,10);second=session.get(FacebookPage,20)
            originals={row.id:(row.posts,row.status) for row in (first,second)}
            for row,url in ((first,'https://www.facebook.com/555/posts/111'),(second,'https://www.facebook.com/555/posts/222')):
                row.status='posted';row.posts=json.dumps([{'url':url,'date':row.check_date},{'url':'https://www.facebook.com/555/posts/999','date':'2001-01-01'}])
        canonical=get(data,'/pages?zone=UTC&q=555')['items'][0]
        assert canonical['display']['posts_today']==2,'Canonical Pages union distinct current-day post URLs across profiles'
        preview=get(data,'/profiles?scope=all&q='+quote('Áo Funny'))['items'][0]
        assert next(row for row in preview['facebook']['pages'] if row['id']==10)['posts_today']==1,'Fresh profile previews exclude old-dated post entries'
        detail=get(data,'/facebook-pages?profile_id=1&zone=UTC&q='+quote('Áo Page'))['items'][0]
        assert detail['posts_today']==1 and len(detail['posts'])==1
        with data.service.db.session.begin() as session:
            second=session.get(FacebookPage,20);second.posts=json.dumps([{'url':'https://www.facebook.com/555/posts/111','date':second.check_date}])
        assert get(data,'/pages?zone=UTC&q=555')['items'][0]['display']['posts_today']==1,'The same post from two profile sources counts only once'
        with data.service.db.session.begin() as session:
            for rowid in (10,20):session.get(FacebookPage,rowid).posts='[]'
        assert get(data,'/pages?zone=UTC&q=555')['items'][0]['display']['posts_today']==1,'Posted canonical status retains the established minimum-one count'
    finally:
        with data.service.db.session.begin() as session:
            for rowid,(posts,status) in originals.items():row=session.get(FacebookPage,rowid);row.posts=posts;row.status=status
