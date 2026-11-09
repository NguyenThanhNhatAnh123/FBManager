"""Confirmed app-level Meta denial through fake HTTP and disposable cache only."""
import json

import httpx
import pytest

from conftest import FixtureBox,immediate
from page_manager.app import create_app
from page_manager.graph import GraphClient,GraphError
from page_manager.inputs import ConnectionInput
from page_manager.reel_graph import ReelGraphClient
from test_http_security import request,session


TOKEN='FixtureBlockedSourceToken82'
PAGE_TOKEN='FixtureBlockedPageToken82'
PRIVATE='FixturePrivateResponseMarker82'
BLOCKED='Meta đang chặn quyền truy cập API của ứng dụng. Kiểm tra trạng thái ứng dụng trong Meta for Developers.'
PERMISSION='Token chưa có quyền đọc dữ liệu được yêu cầu.'


def invoke(operation,respond):
    async def run():
        client=ReelGraphClient if operation=='reel_start' else GraphClient
        async with client(transport=httpx.MockTransport(respond)) as graph:
            if operation=='identity':return await graph.identity(TOKEN)
            if operation=='page_update':return await graph.update_page(TOKEN,'555',{'bio':'Fixture proposed bio'})
            return await graph.start_reel(TOKEN,'555')
    return immediate(run())


@pytest.mark.parametrize('operation',['identity','page_update','reel_start'])
@pytest.mark.parametrize('message',['API access blocked.','API access blocked','  API ACCESS BLOCKED.  '])
def test_confirmed_app_block_is_distinct_safe_and_never_retried(operation,message):
    seen=[]
    def respond(req):
        seen.append(req)
        return httpx.Response(400,json={'error':{'code':200,'message':message,'error_data':{'private':PRIVATE,'access_token':TOKEN},'fbtrace_id':PRIVATE}})
    with pytest.raises(GraphError) as caught:invoke(operation,respond)
    error=caught.value
    assert error.status=='api_access_blocked' and error.code=='200' and str(error)==BLOCKED
    assert len(seen)==1 and seen[0].method==('GET' if operation=='identity' else 'POST')
    assert seen[0].url.host=='graph.facebook.com' and TOKEN not in str(seen[0].url)
    assert all(value not in str(error) for value in (TOKEN,PAGE_TOKEN,PRIVATE,'API access blocked'))


@pytest.mark.parametrize('operation',['identity','page_update','reel_start'])
@pytest.mark.parametrize('message',[
    None,{},[],True,200,'','Application does not have permission.',
    'API access blocked!','Not API access blocked.','API access blocked. '+TOKEN,
    PRIVATE+' '+TOKEN,
])
def test_generic_or_malformed_permission_denials_are_not_relabelled(operation,message):
    seen=[]
    def respond(req):
        seen.append(req)
        return httpx.Response(400,json={'error':{'code':200,'message':message}})
    with pytest.raises(GraphError) as caught:invoke(operation,respond)
    assert caught.value.status=='permission_limited' and str(caught.value)==PERMISSION
    assert len(seen)==1 and TOKEN not in str(caught.value) and PRIVATE not in str(caught.value)


@pytest.mark.parametrize('operation',['identity','page_update','reel_start'])
@pytest.mark.parametrize('code,expected',[(10,'permission_limited'),(283,'permission_limited'),(190,'invalid'),(2,'error'),('200','error')])
def test_blocked_phrase_requires_the_confirmed_numeric_error_code(operation,code,expected):
    def respond(_):return httpx.Response(400,json={'error':{'code':code,'message':'API access blocked.'}})
    with pytest.raises(GraphError) as caught:invoke(operation,respond)
    assert caught.value.status==expected and 'API access blocked' not in str(caught.value)


@pytest.mark.parametrize('status',[200,400,403])
def test_error_object_retains_app_block_classification_at_known_http_statuses(status):
    def respond(_):return httpx.Response(status,json={'error':{'code':200,'message':'API access blocked.'}})
    with pytest.raises(GraphError) as caught:invoke('identity',respond)
    assert caught.value.status=='api_access_blocked' and str(caught.value)==BLOCKED


def source_with_page(manager):
    source=manager.save_connection(ConnectionInput(label='Fixture source',token_type='system_user',token=TOKEN))
    row=manager.store.connection(source['id'])
    manager.store.sync(source['id'],row['token_revision'],[{
        'id':'555','name':'Fixture Page','sources':['assigned_pages'],'tasks':None,
        'encrypted_page_token':manager.secrets.encrypt(PAGE_TOKEN),
    }],True,'working',None,{'id':'77','name':'Fixture user'})
    return source['id']


@pytest.mark.parametrize('failure_stage',['page_identity','edit_fields'])
def test_edit_options_returns_fixed_ui_error_without_rederiving_or_echoing_private_response(tmp_path,failure_stage):
    seen=[]
    def respond(req):
        seen.append(req)
        if failure_stage=='edit_fields' and req.url.path.endswith('/me'):
            return httpx.Response(200,json={'id':'555','name':'Fixture Page'})
        return httpx.Response(400,json={'error':{'code':200,'message':'API access blocked.',
            'error_user_msg':PRIVATE+' '+TOKEN+' '+PAGE_TOKEN,'error_data':{'token':PAGE_TOKEN}}})
    app=create_app(tmp_path,secret_box=FixtureBox(),graph_factory=lambda:GraphClient(transport=httpx.MockTransport(respond)))
    manager=app.state.manager;cid=source_with_page(manager);headers=session(app)
    before=dict(manager.store.source('555',cid))
    status,_,body=request(app,'/api/pages/555/edit-options','POST',{'connection_id':cid},headers)
    assert status==400 and body=={'detail':BLOCKED,'code':'meta_api_access_blocked'}
    serialized=json.dumps(body,ensure_ascii=False)
    assert all(value not in serialized for value in (TOKEN,PAGE_TOKEN,PRIVATE,'API access blocked'))
    assert len(seen)==(1 if failure_stage=='page_identity' else 2)
    assert all(req.method=='GET' and req.headers['authorization']=='Bearer '+PAGE_TOKEN for req in seen)
    assert dict(manager.store.source('555',cid))==before


def test_blocked_token_check_is_safe_attention_state_and_preserves_cached_page(tmp_path):
    seen=[]
    def respond(req):
        seen.append(req)
        return httpx.Response(400,json={'error':{'code':200,'message':'API access blocked.','error_data':{'private':PRIVATE,'token':TOKEN}}})
    app=create_app(tmp_path,secret_box=FixtureBox(),graph_factory=lambda:GraphClient(transport=httpx.MockTransport(respond)))
    manager=app.state.manager;cid=source_with_page(manager)
    result=immediate(manager.check(cid))
    assert result['connection']['status']=='api_access_blocked' and result['connection']['error']==BLOCKED
    overview=manager.overview('UTC')
    assert overview['stats']['needs_attention']==1 and overview['stats']['working_connections']==0
    assert overview['stats']['pages']==1 and manager.pages(timezone='UTC')['items'][0]['status']=='needs_attention'
    public=json.dumps({'check':result,'overview':overview,'pages':manager.pages(timezone='UTC')},ensure_ascii=False)
    assert all(value not in public for value in (TOKEN,PAGE_TOKEN,PRIVATE,'API access blocked'))
    assert len(seen)==1 and seen[0].method=='GET'
