"""Disposable three-user HTTP gateway tests; never touch a running manager."""
import hashlib
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'via-auth'))
from via_auth import IdentityGate,IdentityStore
from via_mcp.bridge import ManagerClient
from via_mcp.common import Inputs,MutationInput,Tool,ToolError
from via_mcp.dashboard_proxy import forward_http
from via_mcp.remote_app import Gateway,SessionMediaVault,create_app,load_config
from via_mcp.registry import basic_tools

CONFIG={'version':1,'bind_host':'127.0.0.1','port':8040,'manager_port':8000,
        'public_origin':None,'allow_http_loopback':True}
INIT={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}
PNG=b'\x89PNG\r\n\x1a\n'+b'private test bytes'


@pytest.fixture
def site(tmp_path):
    clock=[2_000_000_000];store=IdentityStore(tmp_path/'via-auth'/'data',clock=lambda:clock[0])
    initial=store.bootstrap();tokens={};ids={};passwords={}
    for account in initial:
        user=next(row for row in store.list_users() if row['username']==account['username'])
        password='New-Temporary-'+account['username']+'-Password'
        store.change_password(user['id'],account['password'],password)
        ids[account['username']]=user['id'];passwords[account['username']]=password
        tokens[account['username']]=store.create_access_token(user['id'],name='Test machine',
            scopes=['via:read','via:write'],audience='http://127.0.0.1:8040/mcp')
    calls=[]
    class Client:
        def __init__(self,port,media,*,bearer_token):self.raw=bearer_token
        def close(self):pass
        def request(self,*args,**kwargs):return {'stats':{'total':3}}
    def mutate(ctx,args):
        who=next(name for name,value in tokens.items() if value['token']==ctx.client.raw)
        calls.append(who);return {'actor':who}
    catalog=basic_tools()+[
        Tool('chrome_stop_profile','Test permitted mutation',MutationInput,mutate,read_only=False),
        Tool('chrome_check_proxies','Test admin mutation',MutationInput,mutate,read_only=False)]
    app=create_app(tmp_path,dict(CONFIG),store=store,client_factory=Client,catalog=catalog)
    with TestClient(app,base_url='http://127.0.0.1:8040',client=('127.0.0.1',1234)) as client:
        yield SimpleNamespace(client=client,store=store,tokens=tokens,ids=ids,passwords=passwords,clock=clock,app=app,root=tmp_path,calls=calls)


def connect(site,name='admin',token=None):
    raw=token or site.tokens[name]['token'];auth={'Authorization':'Bearer '+raw}
    response=site.client.post('/mcp',json=INIT,headers=auth)
    assert response.status_code==200
    headers={**auth,'Mcp-Session-Id':response.headers['Mcp-Session-Id'],'MCP-Protocol-Version':'2025-11-25'}
    response=site.client.post('/mcp',json={'jsonrpc':'2.0','method':'notifications/initialized'},headers=headers)
    assert response.status_code==204
    return headers


def rpc(site,headers,method='tools/list',params=None,identifier=2):
    return site.client.post('/mcp',json={'jsonrpc':'2.0','id':identifier,'method':method,'params':params or {}},headers=headers)


def test_three_accounts_have_independent_sessions_roles_and_journals(site):
    operation=str(uuid4());sessions=[]
    for name in ('admin','operator1','operator2'):
        headers=connect(site,name);sessions.append(headers['Mcp-Session-Id'])
        names={row['name'] for row in rpc(site,headers).json()['result']['tools']}
        assert ('chrome_check_proxies' in names)==(name=='admin')
        result=rpc(site,headers,'tools/call',{'name':'chrome_stop_profile','arguments':{'operation_id':operation}},3).json()['result']['structuredContent']
        assert result['ok'] and result['result']['actor']==name and not result['replayed']
        replay=rpc(site,headers,'tools/call',{'name':'chrome_stop_profile','arguments':{'operation_id':operation}},4).json()['result']['structuredContent']
        assert replay['replayed'] and replay['result']['actor']==name
        if name!='admin':
            denied=rpc(site,headers,'tools/call',{'name':'chrome_check_proxies','arguments':{'operation_id':str(uuid4())}},5)
            assert denied.json()['result']['structuredContent']['code']=='tool_not_allowed'
    assert len(set(sessions))==3 and site.calls==['admin','operator1','operator2']
    assert len(list((site.root/'via-mcp'/'data'/'remote-users').glob('*/data/operations.sqlite3')))==3


def test_read_only_pat_keeps_protocol_reads_and_blocks_writes_uploads(site):
    pat=site.store.create_access_token(site.ids['admin'],name='Read only',scopes=['via:read'],audience='http://127.0.0.1:8040/mcp')
    headers=connect(site,token=pat['token'])
    names={row['name'] for row in rpc(site,headers).json()['result']['tools']}
    assert 'via_status' in names and 'via_register_media' not in names and 'chrome_stop_profile' not in names
    result=rpc(site,headers,'tools/call',{'name':'via_status','arguments':{}},3).json()['result']['structuredContent']
    assert result['ok']
    denied=rpc(site,headers,'tools/call',{'name':'chrome_stop_profile','arguments':{'operation_id':str(uuid4())}},4)
    assert denied.json()['result']['structuredContent']['code']=='tool_not_allowed'
    assert site.client.post('/mcp/media?kind=image',content=PNG,headers={**headers,'X-Filename':'a.png','X-Content-SHA256':hashlib.sha256(PNG).hexdigest()}).status_code==403


def test_token_binding_and_revocation_are_checked_on_every_request(site):
    headers=connect(site,'operator1')
    stolen={**headers,'Authorization':'Bearer '+site.tokens['operator2']['token']}
    assert rpc(site,stolen).status_code==404
    site.store.revoke_access_token(site.ids['operator1'],site.tokens['operator1']['id'])
    assert rpc(site,headers).status_code==401
    header2=connect(site,'operator2')
    site.store.change_password(site.ids['operator2'],site.passwords['operator2'],'Rotated-Temporary-Password')
    assert rpc(site,header2).status_code==401


def test_media_upload_checksum_session_ownership_and_no_server_path(site):
    headers=connect(site,'operator1');other=connect(site,'operator2')
    upload_headers={**headers,'X-Filename':'art.png','X-Content-SHA256':hashlib.sha256(PNG).hexdigest()}
    assert site.client.post('/mcp/media?kind=image',content=PNG,headers={**upload_headers,'X-Content-SHA256':'0'*64}).status_code==400
    response=site.client.post('/mcp/media?kind=image',content=PNG,headers=upload_headers)
    assert response.status_code==201
    upload=response.json()['upload_id'];params={'name':'via_register_media','arguments':{'file_path':'upload:'+upload,'kind':'image'}}
    result=rpc(site,headers,'tools/call',params).json()['result']['structuredContent']
    assert result['ok'] and result['result']['name']=='art.png' and 'path' not in result['result']
    denied=rpc(site,other,'tools/call',params).json()['result']['structuredContent']
    assert denied['code']=='invalid_media'
    params['arguments']['file_path']=str(site.root/'private.png')
    assert rpc(site,headers,'tools/call',params,3).json()['result']['structuredContent']['code']=='invalid_media'
    folder=site.app.state.gateway.sessions[headers['Mcp-Session-Id']].folder
    assert site.client.delete('/mcp',headers=headers).status_code==204 and not folder.exists()
    assert rpc(site,headers,identifier=4).status_code==404


def test_gateway_boundary_host_origin_service_cookie_and_protocol(site):
    assert site.client.get('/health').json()=={'name':'via-remote-mcp','ready':True,'version':'1.4.1'}
    assert site.client.get('/health',headers={'Host':'untrusted.example'}).status_code==403
    site.store.ensure_service_key()
    service=site.store.service_path.read_text().strip()
    assert site.client.post('/mcp',json=INIT,headers={'X-Via-Service-Token':service}).status_code==401
    assert site.client.post('/mcp',json=INIT,headers={'Authorization':'Bearer '+site.tokens['admin']['token'],'Origin':'https://evil.example'}).status_code==403
    headers=connect(site)
    assert rpc(site,{key:value for key,value in headers.items() if key!='MCP-Protocol-Version'}).status_code==400
    assert site.client.get('/auth/api/me',headers=headers).status_code==401
    assert site.client.get('/mcp',headers=headers).status_code==405
    assert site.client.post('/mcp',content='[{}]',headers={**headers,'Content-Type':'application/json'}).status_code==400
    assert site.client.post('/mcp',content='x'*262145,headers={**headers,'Content-Type':'application/json'}).status_code==400


@pytest.mark.parametrize('key,value',[('bind_host','0.0.0.0'),('version',True),('port',8000),('public_origin','http://example.com'),('public_origin','https://a.example/path'),('public_origin','https://user:pass@a.example')])
def test_strict_server_configuration(tmp_path,key,value):
    config={**CONFIG,key:value};path=tmp_path/'config.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError):load_config(path)


def test_public_origin_matches_browser_normalized_host(tmp_path):
    path=tmp_path/'config.json'
    path.write_text(json.dumps({**CONFIG,'public_origin':'https://Via.Example:443'}))
    config=load_config(path)
    assert config['public_origin']=='https://via.example'
    app=create_app(tmp_path,config)
    with TestClient(app,base_url='https://via.example',client=('127.0.0.1',1234)) as client:
        assert client.get('/health',headers={'Origin':'https://via.example'}).status_code==200
        assert app.state.gateway.resource=='https://via.example/mcp'


def test_dashboard_relay_has_fixed_upstream_real_cookie_identity_and_secure_cookies(tmp_path):
    store=IdentityStore(tmp_path/'identity');accounts=store.bootstrap();user=store.list_users()[0]
    password='Temporary-Proxy-Password';cookie=store.change_password(user['id'],accounts[0]['password'],password)
    seen=[]
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):yield b'{"ok":true}'
    def upstream(request):
        seen.append(request)
        if request.url.path=='/pages':
            return httpx.Response(307,stream=Stream(),headers={'Location':'http://127.0.0.1:8000/pages/'})
        return httpx.Response(200,stream=Stream(),headers=[('set-cookie','cm_session=temporary; Path=/; HttpOnly')])
    config={**CONFIG,'public_origin':'https://via.example'}
    async def relay(request):return await forward_http(request,config,transport=httpx.MockTransport(upstream))
    app=IdentityGate(Starlette(routes=[Route('/api/session',relay),Route('/pages',relay)]),store,port=8040,public_origin=config['public_origin'],allow_service=False)
    with TestClient(app,base_url='https://via.example',client=('127.0.0.1',1234)) as client:
        response=client.get('/api/session',headers={'Cookie':'via_identity='+cookie,'X-Via-Service-Token':'attacker','Authorization':''})
        assert response.status_code==200 and 'Secure' in response.headers['set-cookie']
        assert str(seen[0].url)=='http://127.0.0.1:8000/api/session'
        assert seen[0].headers['origin']=='http://127.0.0.1:8000'
        assert 'x-via-service-token' not in seen[0].headers and 'authorization' not in seen[0].headers
        assert client.get('/api/session',headers={'Cookie':'via_identity='+cookie,'Origin':'https://evil.example'}).status_code==403
        redirect=client.get('/pages',headers={'Cookie':'via_identity='+cookie},follow_redirects=False)
        assert redirect.status_code==307 and redirect.headers['Location']=='/pages/'


def test_gateway_forwards_each_real_pat_to_manager_identity_and_audit(site):
    # A real ManagerClient crosses HTTP and the real identity middleware. The
    # tiny manager handler is deliberate: no Meta call, scheduler or browser.
    async def session(request):
        response=JSONResponse({'csrf_token':'temporary-inner-csrf'})
        response.set_cookie('cm_session','temporary-session')
        return response
    async def action(request):
        return JSONResponse({'actor':request.scope['via.identity']['username']})
    backend=IdentityGate(Starlette(routes=[Route('/api/session',session),
        Route('/api/profiles/1/stop',action,methods=['POST']),
        Route('/api/proxies/check',action,methods=['POST'])]),site.store,port=8000)
    with TestClient(backend,base_url='http://127.0.0.1:8000',client=('127.0.0.1',1234)) as manager:
        def transport(request):
            response=manager.request(request.method,str(request.url),headers=request.headers,content=request.read())
            return httpx.Response(response.status_code,headers=response.headers,content=response.content)
        def client_factory(port,media,*,bearer_token):
            return ManagerClient(port,media,bearer_token=bearer_token,transport=httpx.MockTransport(transport))
        def allowed(ctx,args):return ctx.client.request('chrome','POST','/api/profiles/1/stop',body={},read_only=False)
        def admin(ctx,args):return ctx.client.request('chrome','POST','/api/proxies/check',body={},read_only=False)
        catalog=basic_tools()+[Tool('chrome_stop_profile','Test',MutationInput,allowed,read_only=False),
            Tool('chrome_check_proxies','Test',MutationInput,admin,read_only=False)]
        app=create_app(site.root/'bridge',dict(CONFIG),store=site.store,client_factory=client_factory,catalog=catalog)
        with TestClient(app,base_url='http://127.0.0.1:8040',client=('127.0.0.1',1234)) as remote:
            bound=SimpleNamespace(client=remote,tokens=site.tokens)
            for name in ('admin','operator1','operator2'):
                headers=connect(bound,name)
                result=rpc(bound,headers,'tools/call',{'name':'chrome_stop_profile','arguments':{'operation_id':str(uuid4())}}).json()['result']['structuredContent']
                assert result['ok'] and result['result']['actor']==name
            # A backend write rejects operator PAT even if another client
            # attempts it outside the gateway's filtered tool catalog.
            denied=manager.post('/api/proxies/check',headers={'Authorization':'Bearer '+site.tokens['operator1']['token']},json={})
            assert denied.status_code==403
    rows=site.store.audits(100,0)['items']
    assert {row['actor'] for row in rows if row['route']=='/api/profiles/1/stop' and row['status']==200}=={'admin','operator1','operator2'}


@pytest.mark.asyncio
async def test_cancelled_request_retains_session_lock_until_write_finishes(tmp_path):
    import threading
    started=threading.Event();finish=threading.Event();completed=[];lock=asyncio.Lock()
    gateway=Gateway(tmp_path,dict(CONFIG),None)
    def write():
        started.set();finish.wait(10);completed.append('journal-completed')
    async def owner():
        async with lock:await gateway.owned_work(write)
    task=asyncio.create_task(owner())
    try:
        assert await asyncio.to_thread(started.wait,5)
        task.cancel();await asyncio.sleep(.02)
        assert lock.locked() and not task.done() and not completed
    finally:finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert completed==['journal-completed'] and not lock.locked()


def test_gateway_media_works_under_data_named_install_root_without_opening_other_files(tmp_path):
    folder=tmp_path/'Data'/'Via Server'/'spool';folder.mkdir(parents=True)
    path=folder/(str(uuid4())+'.png');path.write_bytes(PNG)
    vault=SessionMediaVault([folder]);assert vault.register(str(path),'image')['size']==len(PNG)
    outside=folder.parent/(str(uuid4())+'.png');outside.write_bytes(PNG)
    with pytest.raises(ToolError):vault.register(str(outside),'image')
    ordinary=folder/'secret.png';ordinary.write_bytes(PNG)
    with pytest.raises(ToolError):vault.register(str(ordinary),'image')


def test_browser_and_mcp_use_same_authenticated_workspace_even_when_record_ids_overlap(site):
    names=('admin','operator1','operator2')
    ports=(8000,8051,8052)
    workspaces={site.ids[name]:{'manager_port':port,'root':'.' if index==0 else f'workspaces/user-{site.ids[name]}'}
                for index,(name,port) in enumerate(zip(names,ports))}
    rows={port:{'id':1,'owner':name,'opened':False} for name,port in zip(names,ports)}
    catalog=basic_tools()+[
        Tool('chrome_open_bm_profile','Fixture BM action',MutationInput,
             lambda ctx,args:ctx.client.request('chrome','POST','/api/v1/profiles/1/start',body={}),read_only=False),
        Tool('pages_idea_status','Primary producer fixture',Inputs,lambda ctx,args:{'available':True})]

    async def profiles(request):
        return JSONResponse(rows[request.url.port])

    async def start(request):
        row=rows[request.url.port];row['opened']=True
        return JSONResponse(row)

    async def session(request):
        response=JSONResponse({'ok':True})
        response.set_cookie('cm_session','local-fixture')
        return response

    with ExitStack() as stack:
        cores={}
        for user_id,entry in workspaces.items():
            port=entry['manager_port']
            core=IdentityGate(Starlette(routes=[Route('/api/profiles',profiles),Route('/api/v1/profiles/1',profiles),
                Route('/api/session',session),Route('/api/v1/profiles/1/start',start,methods=['POST'])]),site.store,port=port,
                workspace_user_id=user_id,workspace_owner=True)
            cores[port]=stack.enter_context(TestClient(core,base_url=f'http://127.0.0.1:{port}',client=('127.0.0.1',1234)))

        def dispatch(request):
            return cores[request.url.port].request(request.method,str(request.url),headers=request.headers,content=request.content)

        def manager_transport(request):
            response=dispatch(request)
            return httpx.Response(response.status_code,headers=response.headers,content=response.content)

        class Body(httpx.AsyncByteStream):
            def __init__(self,data):self.data=data
            async def __aiter__(self):yield self.data

        async def browser_transport(request):
            response=dispatch(request)
            return httpx.Response(response.status_code,headers=response.headers,stream=Body(response.content))

        def manager_client(port,media,**kwargs):
            return ManagerClient(port,media,transport=httpx.MockTransport(manager_transport),**kwargs)

        app=create_app(site.root/'workspaces-fixture',dict(CONFIG),store=site.store,client_factory=manager_client,
                       catalog=catalog,workspaces=workspaces,dashboard_transport=httpx.MockTransport(browser_transport))
        browser=stack.enter_context(TestClient(app,base_url='http://127.0.0.1:8040',client=('127.0.0.1',1234)))
        remote=SimpleNamespace(client=browser,tokens=site.tokens)
        for name,port in zip(names,ports):
            cookie=site.store.login(name,site.passwords[name],'127.0.0.1')
            raw={'Cookie':'via_identity='+cookie,'X-Via-Workspace-User-Id':str(site.ids['admin'])}
            response=browser.get('/api/profiles?owner_id='+str(site.ids['admin']),headers=raw)
            assert response.status_code==200 and response.json()=={'id':1,'owner':name,'opened':False}
            assert browser.get('/api/v1/profiles/1',headers=raw).json()==response.json()
            assert browser.get('/auth/api/users',headers=raw).status_code==(200 if name=='admin' else 403)
            headers=connect(remote,name)
            tools={tool['name'] for tool in rpc(remote,headers).json()['result']['tools']}
            assert 'chrome_open_bm_profile' in tools
            assert ('pages_idea_status' in tools)==(name=='admin')
            action=rpc(remote,headers,'tools/call',{'name':'chrome_open_bm_profile',
                'arguments':{'operation_id':str(uuid4())}},3).json()['result']['structuredContent']
            assert action['result']=={'id':1,'owner':name,'opened':True}
            assert sum(row['opened'] for row in rows.values())==names.index(name)+1

        # A principal or synthetic Host for another workspace cannot be used
        # by going directly to the wrong core instead of through the gateway.
        token=site.tokens['operator1']['token']
        rejected=cores[8000].get('/api/profiles',headers={'Authorization':'Bearer '+token})
        assert rejected.status_code==403
        wrong_host=cores[8000].get('/api/profiles',headers={'Authorization':'Bearer '+site.tokens['admin']['token'],
            'Host':f"via-user-{site.ids['operator1']}.internal:8000"})
        assert wrong_host.status_code==403
