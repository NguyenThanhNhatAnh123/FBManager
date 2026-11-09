"""Real identity middleware around both dashboards; disposable DBs, no browsers."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Config
from app.main import create_app
from app.services.browsers import BrowserManager
from via_auth import IdentityStore, workspace_host
from via_auth.middleware import COOKIE

pytestmark=pytest.mark.identity
ORIGIN='http://127.0.0.1:8000'

class IsolatedSite(SimpleNamespace):
    def __repr__(self):return '<isolated identity fixture>'


class FixtureBox:
    def encrypt(self,value):return 'isolated-fixture:'+value
    def decrypt(self,value):return value.removeprefix('isolated-fixture:')


@pytest.fixture
def identity_site(tmp_path,monkeypatch):
    async def no_browser(self):pass
    monkeypatch.setattr(BrowserManager,'initialize',no_browser)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'meta-page-manager'/'backend'))
    from page_manager.app import create_app as create_pages
    def forbidden_graph(*args,**kwargs):raise AssertionError('Identity tests cannot call Meta')
    root=tmp_path/'workspace';chrome=root/'chrome-manager';pages=root/'meta-page-manager'
    store=IdentityStore(root/'via-auth'/'data')
    accounts={row['username']:row for row in store.bootstrap()}
    app=create_app(Config(root=chrome,data_dir=chrome/'data'),identity_store=store)
    child=create_pages(pages,port=8000,base_path='/pages',secret_box=FixtureBox(),identity_store=store,
        graph_factory=forbidden_graph,reel_graph_factory=forbidden_graph,
        video_source_directory=root/'videos',idea_pipeline_directory=root/'ideas')
    # Keep the real mounted child and both real IdentityGates; only its discovery
    # from an installed project tree is unnecessary in a disposable workspace.
    app.state.page_workspace.application=child
    dist=chrome/'frontend'/'dist';dist.mkdir(parents=True)
    (dist/'index.html').write_text('<!doctype html><title>Isolated Chrome fixture</title>',encoding='utf-8')
    (pages/'frontend').mkdir()
    (pages/'frontend'/'index.html').write_text('<!doctype html><title>Isolated Page fixture</title>',encoding='utf-8')
    with TestClient(app,base_url=ORIGIN,client=('127.0.0.1',51234)) as client:
        yield IsolatedSite(app=app,child=child,store=store,accounts=accounts,client=client)


def sign_in(site,username,*,change=True):
    client=site.client;client.cookies.clear();account=site.accounts[username]
    response=client.post('/auth/api/login',json={'username':username,'password':account['password']})
    assert response.status_code==200
    initial=client.cookies.get(COOKIE)
    me=client.get('/auth/api/me').json()
    if change:
        password='ChangedFixturePassword-'+username
        response=client.post('/auth/api/password',json={'current_password':account['password'],'new_password':password},
            headers={'X-Via-CSRF':me['csrf_token']})
        assert response.status_code==200
        account['password']=password
        assert client.cookies.get(COOKIE)!=initial
        me=client.get('/auth/api/me').json()
        assert me['user']['must_change_password'] is False
        assert client.get('/api/session').status_code==200
        page_session=client.get('/pages/api/session')
        assert page_session.status_code==200
        mpm_csrf=page_session.json()['csrf_token']
    else:mpm_csrf=None
    cookies=dict(client.cookies)
    headers={'Cookie':'; '.join(name+'='+value for name,value in cookies.items()),'X-Via-CSRF':me['csrf_token']}
    if mpm_csrf:headers['X-Mpm-CSRF']=mpm_csrf
    return SimpleNamespace(headers=headers,cookies=cookies,user=me['user'],initial_cookie=initial)


def test_versioned_api_and_direct_links_use_existing_authentication(identity_site):
    from urllib.parse import parse_qs, urlsplit
    site=identity_site;client=site.client
    location=client.get('/chrome/settings?tab=bm',follow_redirects=False).headers['location']
    assert parse_qs(urlsplit(location).query)['next']==['/chrome/settings?tab=bm']
    assert client.get('/api/v1/openapi.json').status_code==401
    assert client.get('/pages/api/v1/openapi.json').status_code==401
    assert client.get('/pages/api/v1/idea-pipeline').status_code==401
    assert client.get('/pages/api/v1/telegram-approval').status_code==401

    admin=sign_in(site,'admin')
    assert client.get('/chrome/settings?tab=bm',headers=admin.headers).status_code==200
    schema=client.get('/api/v1/openapi.json',headers=admin.headers).json()['paths']
    assert 'post' in schema['/api/v1/profiles'] and 'get' in schema['/api/v1/profiles/{profile_id}']
    assert all(path.startswith('/api/v1/') for path in schema)
    assert client.get('/pages/saved',headers=admin.headers).status_code==200
    page_schema=client.get('/pages/api/v1/openapi.json',headers=admin.headers).json()
    assert page_schema['servers']==[{'url':'/pages'}]
    assert 'get' in page_schema['paths']['/api/v1/pages']
    assert 'post' in page_schema['paths']['/api/v1/reels/assets']
    assert 'get' in page_schema['paths']['/api/v1/idea-pipeline']
    assert 'put' in page_schema['paths']['/api/v1/telegram-approval']
    assert client.get('/pages/api/v1/pages',headers=admin.headers).json()['items']==[]
    blocked=client.post('/api/v1/profiles',headers={'Cookie':admin.headers['Cookie']},json={'name':'No CSRF'})
    assert blocked.status_code==403 and blocked.json()['code']=='csrf_required'
    created=client.post('/api/v1/profiles',headers=admin.headers,json={'name':'Versioned fixture'})
    assert created.status_code==201
    profile_id=created.json()['id']
    assert client.get(f'/api/v1/profiles/{profile_id}',headers=admin.headers).json()['name']=='Versioned fixture'
    assert [row['id'] for row in client.get('/api/profiles',headers=admin.headers).json()]==[profile_id]
    missing=client.get('/api/v1/profiles/9999',headers=admin.headers)
    assert missing.status_code==404 and missing.json()['code']=='not_found'
    invalid=client.get('/api/v1/profiles?limit=101',headers=admin.headers)
    assert invalid.status_code==422 and invalid.json()['code']=='invalid_input'

    operator=sign_in(site,'operator1')
    denied=client.post('/api/v1/profiles',headers=operator.headers,json={'name':'Forbidden'})
    assert denied.status_code==403 and denied.json()['code']=='forbidden'
    for method,path in (('PUT','/pages/api/v1/telegram-approval'),
                        ('POST','/pages/api/v1/idea-pipeline/producers/check')):
        response=client.request(method,path,headers=operator.headers,json={})
        assert response.status_code==403 and response.json()['code']=='forbidden'


def test_bound_core_accepts_its_owner_and_local_service_key_only(tmp_path,monkeypatch):
    async def no_browser(self):pass
    monkeypatch.setattr(BrowserManager,'initialize',no_browser)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'meta-page-manager'/'backend'))
    from page_manager.app import create_app as create_pages

    def no_graph(*args,**kwargs):raise AssertionError('Workspace fixture cannot call Meta')

    primary=tmp_path/'primary'
    secondary=tmp_path/'secondary'
    central=IdentityStore(primary/'via-auth'/'data')
    credentials={row['username']:row['password'] for row in central.bootstrap()}
    central.ensure_service_key()
    sessions={}
    tokens={}
    for name in ('admin','operator1'):
        user=next(row for row in central.list_users() if row['username']==name)
        raw=central.change_password(user['id'],credentials[name],'Fixture isolated password '+name)
        sessions[name]=raw
        actor=central.resolve(raw)
        tokens[name]=central.create_access_token(user['id'],name='Fixture workspace token',scopes=['via:read','via:write'],
            audience='https://via.example.test/mcp',expected_revision=actor['revision'])['token']
    owner=central.resolve(sessions['operator1']);port=8042;origin=f'http://127.0.0.1:{port}'
    monkeypatch.setenv('VIA_WORKSPACE_USER_ID',str(owner['id']))
    monkeypatch.setenv('VIA_WORKSPACE_AUTH_DIR',str(central.directory))
    monkeypatch.setenv('VIA_WORKSPACE_PRIMARY','0')
    chrome=secondary/'chrome-manager'
    app=create_app(Config(root=chrome,data_dir=chrome/'data',port=port))
    local_store=app.state.identity_store
    app.state.page_workspace.application=create_pages(secondary/'meta-page-manager',port=port,base_path='/pages',
        secret_box=FixtureBox(),graph_factory=no_graph,reel_graph_factory=no_graph,identity_store=local_store,
        workspace_user_id=owner['id'],secondary_workspace=True)
    host=workspace_host(owner['id'],port)
    target={'Host':host,'Origin':origin}
    owner_cookie=target|{'Cookie':COOKIE+'='+sessions['operator1'],'X-Via-CSRF':owner['csrf_token']}
    foreign_cookie=target|{'Cookie':COOKIE+'='+sessions['admin']}
    with TestClient(app,base_url=origin,client=('127.0.0.1',51234)) as client:
        assert client.get('/_via/workspace',headers=target).json()=={'workspace_user_id':owner['id'],'pid':os.getpid()}
        assert client.get('/_via/workspace',headers={'Host':workspace_host(owner['id']+1,port)}).status_code==403
        primary_key=central.service_path.read_text(encoding='ascii').strip()
        secondary_key=local_store.service_path.read_text(encoding='ascii').strip()
        assert local_store.path==central.path and local_store.service_path.parent==secondary/'via-auth'/'data'
        assert primary_key!=secondary_key
        assert client.get('/api/session',headers=target|{'X-Via-Service-Token':primary_key}).status_code==401
        assert client.get('/api/session',headers=target|{'X-Via-Service-Token':secondary_key}).status_code==200
        assert client.get('/api/health/ready',headers=target|{'X-Via-Service-Token':secondary_key}).json()['workspace_user_id']==owner['id']
        assert client.get('/api/session',headers=foreign_cookie).status_code==403
        assert client.get('/api/session',headers=target|{'Authorization':'Bearer '+tokens['admin']}).status_code==403
        assert client.get('/api/session',headers=target|{'Authorization':'Bearer '+tokens['operator1']}).status_code==200
        assert client.get('/pages/api/session',headers=target|{'Authorization':'Bearer '+tokens['operator1']}).status_code==200
        assert client.get('/pages/api/session',headers=target|{'Authorization':'Bearer '+tokens['admin']}).status_code==403
        assert client.get('/auth/api/users',headers=owner_cookie).status_code==403
        owner_cookie['Cookie']+='; cm_session='+client.cookies['cm_session']
        assert client.put('/api/settings',headers=owner_cookie,json={}).status_code==403
        assert client.post('/api/profiles',headers=owner_cookie,json={'name':'Workspace fixture'}).status_code==201
        assert client.get('/api/profiles',headers=owner_cookie).json()[0]['name']=='Workspace fixture'
        with client.websocket_connect('/ws?mode=invalidate',headers=owner_cookie) as ws:
            assert ws.receive_json()['kind']=='changed'
        foreign_cookie['Cookie']+='; cm_session='+client.cookies['cm_session']
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect('/ws?mode=invalidate',headers=foreign_cookie):pass
        assert error.value.code==1008


def test_initial_password_must_change_before_either_dashboard_session(identity_site):
    site=identity_site;client=site.client
    assert client.get('/',follow_redirects=False).headers['location'].startswith('/auth/login')
    assert client.get('/api/session').status_code==401
    admin=sign_in(site,'admin',change=False)
    assert admin.user['must_change_password'] is True
    for path in ('/api/session','/pages/api/session','/auth/api/users'):
        response=client.get(path,headers=admin.headers)
        assert response.status_code==403 and response.json()['code']=='password_change_required'
    assert client.get('/',headers=admin.headers,follow_redirects=False).headers['location']=='/auth/account'
    body={'current_password':site.accounts['admin']['password'],'new_password':'NewFixturePassword-2026'}
    assert client.post('/auth/api/password',json=body,headers={'Cookie':admin.headers['Cookie']}).status_code==403
    changed=client.post('/auth/api/password',json=body,headers=admin.headers)
    assert changed.status_code==200
    assert client.get('/auth/api/me',headers=admin.headers).status_code==401,'Password change revokes the old session'
    assert client.get('/auth/api/me').json()['user']['must_change_password'] is False
    assert client.get('/api/session').status_code==200
    assert client.get('/pages/api/session').status_code==200
    identity_cookie=next(cookie for cookie in client.cookies.jar if cookie.name==COOKIE)
    assert identity_cookie.has_nonstandard_attr('HttpOnly')
    assert identity_cookie.get_nonstandard_attr('SameSite')=='strict'


def test_three_users_have_distinct_sessions_and_logout_does_not_sign_out_others(identity_site):
    site=identity_site;client=site.client
    sessions=[sign_in(site,name) for name in ('admin','operator1','operator2')]
    assert len({session.cookies[COOKIE] for session in sessions})==3
    assert [session.user['role'] for session in sessions]==['admin','operator','operator']
    for session in sessions:
        assert client.get('/auth/api/me',headers=session.headers).json()['user']['id']==session.user['id']
    assert client.post('/auth/api/logout',headers=sessions[1].headers).status_code==200
    assert client.get('/api/settings',headers=sessions[1].headers).status_code==401
    assert client.get('/api/settings',headers=sessions[0].headers).status_code==200
    assert client.get('/pages/api/connections',headers=sessions[2].headers).status_code==200
    assert len(site.store.list_users())==3


def test_operator_acl_applies_to_chrome_and_real_mounted_page_apis(identity_site):
    site=identity_site;client=site.client;operator=sign_in(site,'operator1')
    for method,path in (('PUT','/api/settings'),('DELETE','/api/profiles/99?confirm=true'),
            ('POST','/api/profiles'),('POST','/pages/api/connections'),
            ('PUT','/pages/api/connections/1'),('DELETE','/pages/api/connections/1?revision=1')):
        response=client.request(method,path,json={},headers=operator.headers)
        assert response.status_code==403 and response.json()['code']=='forbidden'
    assert client.get('/auth/api/users',headers=operator.headers).status_code==403
    assert client.get('/api/settings',headers=operator.headers).status_code==200
    assert client.get('/pages/api/connections',headers=operator.headers).status_code==200
    for path in ('/api/reels','/pages/api/reels'):
        assert client.post(path,json={},headers=operator.headers).status_code==422,'Allowed operations reach business validation'
    no_csrf={key:value for key,value in operator.headers.items() if key!='X-Via-CSRF'}
    response=client.post('/api/reels',json={},headers=no_csrf)
    assert response.status_code==403 and response.json()['code']=='csrf_required'
    assert client.post('/api/reels',json={},headers=operator.headers|{'Origin':'https://untrusted.example'}).status_code==403


def test_old_manager_cookies_and_claim_headers_cannot_bypass_user_gate_or_media(identity_site):
    site=identity_site;client=site.client;admin=sign_in(site,'admin')
    local_cookies={name:value for name,value in admin.cookies.items() if name!=COOKIE}
    assert {'cm_session','mpm_pages_session'}<=set(local_cookies)
    headers={'Cookie':'; '.join(name+'='+value for name,value in local_cookies.items()),
        'X-Via-User':'admin','X-Via-Role':'admin','X-Via-Identity':'admin'}
    for path in ('/api/session','/api/settings','/api/reels/assets/'+'a'*32+'/video',
            '/pages/api/session','/pages/api/connections','/pages/api/reels/assets/'+'a'*32+'/video'):
        response=client.get(path,headers=headers)
        assert response.status_code==401 and response.json()['code']=='authentication_required'
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect('ws://127.0.0.1:8000/ws',headers=headers|{'Origin':ORIGIN}):pass
    assert error.value.code==1008


@pytest.mark.parametrize('change',[{'active':False},{'revoke_sessions':True}])
def test_user_disable_or_revoke_terminates_existing_http_and_websocket_sessions(identity_site,change):
    site=identity_site;client=site.client
    admin=sign_in(site,'admin');operator=sign_in(site,'operator1')
    with client.websocket_connect('ws://127.0.0.1:8000/ws?mode=invalidate',headers=operator.headers|{'Origin':ORIGIN}) as socket:
        assert socket.receive_json()['kind']=='changed'
        response=client.patch('/auth/api/users/'+str(operator.user['id']),
            json={'revision':operator.user['revision']}|change,headers=admin.headers)
        assert response.status_code==200
        client.portal.call(site.app.state.services.state.emit)
        with pytest.raises(WebSocketDisconnect) as error:socket.receive_json()
        assert error.value.code==1008
    assert client.get('/api/settings',headers=operator.headers).status_code==401
    assert client.get('/pages/api/session',headers=operator.headers).status_code==401
    assert client.get('/api/settings',headers=admin.headers).status_code==200
    if change.get('active') is False:
        assert client.post('/auth/api/login',json={'username':'operator1','password':site.accounts['operator1']['password']}).status_code==401


def test_explicit_local_service_key_can_initialize_both_local_sessions(identity_site):
    site=identity_site;client=site.client;client.cookies.clear()
    assert client.get('/api/session').status_code==401
    assert client.get('/api/session',headers={'X-Via-Service-Token':'invalid-fixture-key'}).status_code==401
    site.store.ensure_service_key()
    key=site.store.service_path.read_text(encoding='ascii').strip()
    service={'X-Via-Service-Token':key}
    assert client.get('/api/session',headers=service).status_code==200
    assert client.get('/api/settings',headers=service).status_code==200
    response=client.post('/api/profiles',json={'name':'Isolated service profile'},headers=service)
    assert response.status_code==201,'Service auth intentionally uses its own key, without browser CSRF'
    page_session=client.get('/pages/api/session',headers=service)
    assert page_session.status_code==200
    assert client.post('/pages/api/reels',json={},headers=service|{'X-Mpm-CSRF':page_session.json()['csrf_token']}).status_code==422
    assert client.get('/auth/api/users',headers=service).status_code==401,'Service identity cannot manage human accounts'
    remote=TestClient(site.app,base_url=ORIGIN,client=('192.0.2.5',1234))
    try:
        assert remote.get('/api/session',headers=service).status_code==403
    finally:remote.close()


def test_real_mcp_bearer_bridge_keeps_three_users_roles_through_mounted_managers(identity_site,monkeypatch,tmp_path):
    import httpx
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'via-mcp'))
    from via_mcp.bridge import ManagerClient
    from via_mcp.media import MediaVault
    site=identity_site;observed=[]
    def transport(request):
        response=site.client.request(request.method,str(request.url),headers=request.headers,content=request.read())
        observed.append((request.method,request.url.path,response.status_code))
        return httpx.Response(response.status_code,headers=response.headers,content=response.content)
    for name in ('admin','operator1','operator2'):
        session=sign_in(site,name)
        token=site.store.create_access_token(session.user['id'],name='Temporary Codex',scopes=['via:read','via:write'],
            audience='https://via.example/mcp')['token']
        bridge=ManagerClient(8000,MediaVault([tmp_path]),bearer_token=token,transport=httpx.MockTransport(transport))
        try:
            assert isinstance(bridge.request('chrome','GET','/api/settings',read_only=True),dict)
            assert 'stats' in bridge.request('pages','GET','/api/overview',read_only=True)
            # Allowed writes reach business validation in both namespaces. No
            # valid schedule, remote content, browser or Meta credential exists.
            for workspace,path in (('chrome','/api/reels'),('pages','/api/reels')):
                from via_mcp.common import ToolError
                with pytest.raises(ToolError):bridge.request(workspace,'POST',path,body={})
                assert observed[-1][2]==422
        finally:bridge.close()
    rows=site.store.audits(100,0)['items']
    for route in ('/api/reels','/pages/api/reels'):
        assert {row['actor'] for row in rows if row['route']==route and row['status']==422}=={'admin','operator1','operator2'}


def test_admin_audit_records_user_and_outcome_without_request_content(identity_site):
    site=identity_site;client=site.client
    admin=sign_in(site,'admin');operator=sign_in(site,'operator2')
    sentinel='PRIVATE-BODY-FIXTURE-DO-NOT-AUDIT'
    assert client.post('/api/reels',json={'caption':sentinel},headers=operator.headers).status_code==422
    assert client.delete('/api/profiles/99?confirm=true',headers=operator.headers).status_code==403
    response=client.get('/auth/api/audit',headers=admin.headers)
    assert response.status_code==200 and sentinel not in response.text
    rows=response.json()['items']
    assert any(row['actor_id']==operator.user['id'] and row['route']=='/api/reels' and row['status']==422 for row in rows)
    assert any(row['actor_id']==operator.user['id'] and row['route']=='/api/profiles/99' and row['status']==403 for row in rows)
    assert client.get('/auth/api/audit',headers=operator.headers).status_code==403


def test_standalone_page_manager_maps_operator_permissions_without_parent_gate(identity_site):
    from page_manager.app import create_app as create_pages
    site=identity_site;operator=sign_in(site,'operator1')
    root=site.store.directory.parent.parent/'standalone-pages'
    def forbidden_graph(*args,**kwargs):raise AssertionError('Identity tests cannot call Meta')
    app=create_pages(root,port=8010,secret_box=FixtureBox(),identity_store=site.store,
        graph_factory=forbidden_graph,reel_graph_factory=forbidden_graph,
        video_source_directory=root/'videos',idea_pipeline_directory=root/'ideas')
    with TestClient(app,base_url='http://127.0.0.1:8010',client=('127.0.0.1',51235)) as client:
        client.cookies.set(COOKIE,operator.cookies[COOKIE])
        session=client.get('/api/session')
        assert session.status_code==200
        headers={'X-Via-CSRF':operator.headers['X-Via-CSRF'],'X-Mpm-CSRF':session.json()['csrf_token']}
        assert client.get('/api/connections').status_code==200
        for method,path in (('POST','/api/connections'),('PUT','/api/connections/1'),('PUT','/api/video-source/settings')):
            response=client.request(method,path,json={},headers=headers)
            assert response.status_code==403 and response.json()['code']=='forbidden'
        assert client.post('/api/reels',json={},headers=headers).status_code==422
        assert client.get('/auth/api/me').json()['user']['id']==operator.user['id']


def test_readiness_reports_mounted_tick_failure_and_recovers_without_server_restart(identity_site,monkeypatch):
    site=identity_site;client=site.client;admin=sign_in(site,'admin')
    supervisor=site.app.state.scheduler_supervisor
    client.portal.call(supervisor.cycle)
    assert client.get('/api/health/ready',headers=admin.headers).status_code==200
    original=site.child.state.reels.tick
    def fail_tick():raise RuntimeError('PRIVATE-FIXTURE-ERROR-MUST-NOT-LEAK')
    monkeypatch.setattr(site.child.state.reels,'tick',fail_tick)
    client.portal.call(supervisor.cycle)
    response=client.get('/api/health/ready',headers=admin.headers)
    assert response.status_code==503 and response.json()['host']['running'] is True
    assert response.json()['pages']['services']['reels']['error']=='scheduler_tick_failed'
    assert 'PRIVATE-FIXTURE-ERROR' not in response.text
    assert client.get('/api/settings',headers=admin.headers).status_code==200
    monkeypatch.setattr(site.child.state.reels,'tick',original)
    client.portal.call(supervisor.cycle)
    assert client.get('/api/health/ready',headers=admin.headers).status_code==200


def test_readiness_detects_cancelled_host_supervisor_and_recovery(identity_site):
    site=identity_site;client=site.client;admin=sign_in(site,'admin')
    supervisor=site.app.state.scheduler_supervisor
    client.portal.call(supervisor.cycle)
    assert client.get('/api/health/ready',headers=admin.headers).status_code==200
    async def stop_supervisor():
        supervisor.task.cancel()
        try:await supervisor.task
        except asyncio.CancelledError:pass
    client.portal.call(stop_supervisor)
    response=client.get('/api/health/ready',headers=admin.headers)
    assert response.status_code==503 and response.json()['host']['running'] is False
    assert client.get('/api/settings',headers=admin.headers).status_code==200
    async def restart_supervisor():
        task=asyncio.create_task(supervisor.run())
        for _ in range(20):
            await asyncio.sleep(.01)
            if supervisor.snapshot()['ready']:break
        return task
    client.portal.call(restart_supervisor)
    try:assert client.get('/api/health/ready',headers=admin.headers).status_code==200
    finally:client.portal.call(stop_supervisor)
