from http.cookies import SimpleCookie
import json
import pytest
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from page_manager.app import create_app,BODY_LIMIT
from conftest import FixtureBox,immediate


def request(app,path,method='GET',body=None,headers=None,client='127.0.0.1',host='127.0.0.1:8010',root_path=''):
    raw=json.dumps(body).encode() if body is not None else b''
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,'scheme':'http',
           'path':path,'raw_path':path.encode(),'query_string':b'','root_path':root_path,'client':(client,1234),'server':('127.0.0.1',int(host.rsplit(':',1)[-1])),
           'headers':[(key.lower().encode(),value.encode('latin1')) for key,value in {'host':host,'content-type':'application/json',**(headers or {})}.items()]}
    received=False;messages=[]
    async def receive():
        nonlocal received
        if received:return {'type':'http.disconnect'}
        received=True;return {'type':'http.request','body':raw,'more_body':False}
    async def send(value):messages.append(value)
    immediate(app(scope,receive,send))
    start=next(message for message in messages if message['type']=='http.response.start')
    content=b''.join(message.get('body',b'') for message in messages if message['type']=='http.response.body')
    return start['status'],{key.decode():value.decode() for key,value in start['headers']},json.loads(content) if content else None


def session(app):
    status,headers,body=request(app,'/api/session');assert status==200
    cookie=SimpleCookie();cookie.load(headers['set-cookie'])
    assert set(cookie)=={'mpm_session'}
    return {'cookie':'mpm_session='+cookie['mpm_session'].value,'x-mpm-csrf':body['csrf_token']}


def test_independent_session_cookie_csrf_port_origin_and_no_token_readback(tmp_path):
    app=create_app(tmp_path,secret_box=FixtureBox());headers=session(app)
    payload={'label':'Fixture','token_type':'user','token':'FixtureSecretToken82'}
    status,response_headers,body=request(app,'/api/connections','POST',payload,headers)
    assert status==201 and body['has_token'] is True and 'FixtureSecretToken82' not in json.dumps(body)
    assert response_headers['cache-control']=='no-store' and response_headers['x-frame-options']=='DENY'
    assert request(app,'/api/connections',headers={'cookie':'cm_session=old-manager-cookie'})[0]==401
    assert request(app,'/api/connections','POST',payload,{'cookie':headers['cookie']})[0]==403
    assert request(app,'/api/connections','POST',payload,{**headers,'origin':'http://127.0.0.1:8000'})[0]==403
    assert request(app,'/api/session',host='127.0.0.1:8000')[0]==403
    assert request(app,'/api/session',client='192.0.2.1')[0]==403
    assert request(app,'/api/connections',headers={**headers,'sec-fetch-site':'cross-site'})[0]==403


def test_validation_errors_never_echo_secret_inputs_and_body_stream_is_bounded(tmp_path):
    app=create_app(tmp_path,secret_box=FixtureBox());headers=session(app)
    secret='FixtureSecretToken82'
    status,_,body=request(app,'/api/connections','POST',{'label':'Fixture','token_type':'app','token':secret,'unknown':secret},headers)
    assert status==422 and secret not in json.dumps(body)
    status,_,_=request(app,'/api/connections','POST',{'token':'x'*(BODY_LIMIT+1)},headers)
    assert status==413


def test_repeated_gets_do_not_call_graph_without_explicit_token_operation(tmp_path):
    def forbidden_graph():raise AssertionError('No network on cached list')
    app=create_app(tmp_path,secret_box=FixtureBox(),graph_factory=forbidden_graph);headers=session(app)
    for path in ('/api/connections','/api/pages','/api/overview','/api/events'):
        assert request(app,path,headers=headers)[0]==200


def test_app_data_path_is_fixed_under_own_root_and_does_not_use_chrome_manager(tmp_path):
    app=create_app(tmp_path,secret_box=FixtureBox())
    assert app.state.manager.store.path==tmp_path.resolve()/'data'/'page-manager.sqlite3'
    assert not (tmp_path/'manager.sqlite3').exists()


def test_chrome_manager_port_cannot_be_used_for_independent_app(tmp_path):
    with pytest.raises(ValueError):create_app(tmp_path,port=8000,secret_box=FixtureBox())


@pytest.fixture
def mounted(tmp_path):
    child=create_app(tmp_path/'page-manager',port=8000,base_path='/pages',secret_box=FixtureBox())
    parent=FastAPI()
    @parent.get('/api/session')
    async def chrome_session():
        response=JSONResponse({'csrf_token':'FixtureChromeCsrf','app':'Chrome Manager'})
        response.set_cookie('cm_session','FixtureChromeSession',httponly=True,path='/')
        return response
    @parent.get('/api/connections')
    async def chrome_only(req:Request):
        return JSONResponse({'app':'Chrome Manager'},200 if req.cookies.get('cm_session')=='FixtureChromeSession' else 401)
    parent.mount('/pages',child)
    return parent,child


def mounted_session(parent):
    status,headers,body=request(parent,'/pages/api/session',host='127.0.0.1:8000')
    assert status==200
    cookies=SimpleCookie();cookies.load(headers['set-cookie'])
    return cookies,body,{'cookie':'mpm_pages_session='+cookies['mpm_pages_session'].value,'x-mpm-csrf':body['csrf_token']}


def test_real_parent_mount_uses_prefixed_cookie_urls_and_own_auth_for_original_scope_path(mounted):
    parent,child=mounted;cookies,body,headers=mounted_session(parent)
    assert set(cookies)=={'mpm_pages_session'} and cookies['mpm_pages_session']['path']=='/pages/'
    assert cookies['mpm_pages_session']['httponly'] and cookies['mpm_pages_session']['samesite']=='strict'
    assert body['app']['base_path']=='/pages'
    assert body['app']['page_manager_url']=='http://127.0.0.1:8000/pages/'
    assert body['app']['chrome_manager_url']=='http://127.0.0.1:8000/'
    assert request(parent,'/pages/api/connections',host='127.0.0.1:8000')[0]==401
    assert request(parent,'/pages/api/connections',headers={'cookie':'cm_session=FixtureChromeSession'},host='127.0.0.1:8000')[0]==401
    assert request(parent,'/pages/api/connections',headers=headers,host='127.0.0.1:8000')[0]==200
    assert request(parent,'/api/connections',headers=headers,host='127.0.0.1:8000')[0]==401,'Page cookie cannot replace the Chrome session'
    assert request(parent,'/api/connections',headers={'cookie':'cm_session=FixtureChromeSession'},host='127.0.0.1:8000')[2]['app']=='Chrome Manager'


def test_mounted_api_write_requires_own_csrf_and_persists_only_own_database(mounted,tmp_path):
    parent,child=mounted;_,_,headers=mounted_session(parent)
    payload={'label':'Mounted Fixture','token_type':'user','token':'FixtureMountedToken82'}
    wrong={**headers,'x-mpm-csrf':'FixtureChromeCsrf','cookie':headers['cookie']+'; cm_session=FixtureChromeSession'}
    assert request(parent,'/pages/api/connections','POST',payload,wrong,host='127.0.0.1:8000')[0]==403
    assert request(parent,'/pages/api/connections','POST',payload,{'cookie':headers['cookie']},host='127.0.0.1:8000')[0]==403
    code,_,value=request(parent,'/pages/api/connections','POST',payload,{**headers,'origin':'http://127.0.0.1:8000'},host='127.0.0.1:8000')
    assert code==201 and value['label']=='Mounted Fixture' and 'FixtureMountedToken82' not in json.dumps(value)
    assert child.state.manager.store.path==tmp_path/'page-manager'/'data'/'page-manager.sqlite3'
    assert not (tmp_path/'data'/'manager.sqlite3').exists()


def test_mounted_origin_host_and_root_path_boundaries_cannot_bypass_api_auth(mounted):
    parent,child=mounted;_,_,headers=mounted_session(parent)
    assert request(parent,'/pages/api/connections',headers={**headers,'origin':'http://127.0.0.1:8010'},host='127.0.0.1:8000')[0]==403
    assert request(parent,'/pages/api/connections',headers={**headers,'sec-fetch-site':'cross-site'},host='127.0.0.1:8000')[0]==403
    assert request(parent,'/pages/api/connections',headers=headers,host='127.0.0.1:8010')[0]==403
    assert request(child,'/pages/api/connections',host='127.0.0.1:8000',root_path='/pages')[0]==401
    assert request(child,'/pages/api/connections',headers=headers,host='127.0.0.1:8000',root_path='')[0]==403
    assert request(child,'/pages/api/connections',headers=headers,host='127.0.0.1:8000',root_path='/wrong')[0]==403
    assert request(parent,'/pages-extra/api/connections',headers=headers,host='127.0.0.1:8000')[0]==404


@pytest.mark.parametrize('prefix',['pages','/pages/','/other','/pages/api','//pages',None])
def test_only_the_explicit_pages_mount_prefix_is_supported(tmp_path,prefix):
    with pytest.raises(ValueError):create_app(tmp_path,base_path=prefix,secret_box=FixtureBox())


def test_standalone_session_cookie_and_urls_remain_unchanged(tmp_path):
    app=create_app(tmp_path,secret_box=FixtureBox())
    code,headers,body=request(app,'/api/session');cookies=SimpleCookie();cookies.load(headers['set-cookie'])
    assert code==200 and cookies['mpm_session']['path']=='/' and body['app']['base_path']==''
    assert body['app']['page_manager_url']=='http://127.0.0.1:8010/'
    assert body['app']['chrome_manager_url']=='http://127.0.0.1:8000/'


@pytest.mark.parametrize('old_first',[False,True])
def test_mounted_cookie_is_unambiguous_with_old_standalone_and_chrome_cookies_in_either_order(mounted,old_first):
    parent,_=mounted;_,_,headers=mounted_session(parent)
    old='mpm_session=FixtureOldStandaloneSession; cm_session=FixtureChromeSession'
    combined=old+'; '+headers['cookie'] if old_first else headers['cookie']+'; '+old
    mixed={**headers,'cookie':combined}
    assert request(parent,'/pages/api/connections',headers=mixed,host='127.0.0.1:8000')[0]==200
    body={'label':'Mixed cookie fixture','token_type':'user','token':'FixtureMixedToken82'}
    assert request(parent,'/pages/api/connections','POST',body,mixed,host='127.0.0.1:8000')[0]==201
    assert request(parent,'/pages/api/connections',headers={**headers,'cookie':old},host='127.0.0.1:8000')[0]==401


def test_mounted_cookie_does_not_authenticate_optional_standalone_application(mounted,tmp_path):
    parent,_=mounted;_,_,mounted_headers=mounted_session(parent)
    standalone=create_app(tmp_path/'standalone',secret_box=FixtureBox())
    assert request(standalone,'/api/connections',headers=mounted_headers)[0]==401
