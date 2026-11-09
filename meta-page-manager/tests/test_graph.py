import json
import httpx
import pytest
from page_manager.graph import GraphClient,GraphError,graph_version
from conftest import immediate

TOKEN='FixtureAccessToken82'


def run_with(respond,operation):
    async def run():
        async with GraphClient(transport=httpx.MockTransport(respond)) as graph:return await operation(graph)
    return immediate(run())


def test_cursor_pagination_rebuilds_only_official_get_endpoint_and_never_puts_token_in_url():
    seen=[]
    def respond(request):
        seen.append(request)
        assert request.method=='GET' and request.url.host=='graph.facebook.com'
        assert request.headers['authorization']=='Bearer '+TOKEN and TOKEN not in str(request.url)
        if len(seen)==1:return httpx.Response(200,json={'data':[{'id':'1','name':'One'}],'paging':{'next':'https://evil.test/?access_token='+TOKEN,'cursors':{'after':'opaque + / cursor'}}})
        assert request.url.params['after']=='opaque + / cursor'
        return httpx.Response(200,json={'data':[{'id':'2','name':'Two'}]})
    values,complete,error=run_with(respond,lambda graph:graph.collect('me/accounts',TOKEN,'id,name'))
    assert [item['id'] for item in values]==['1','2'] and complete is True and error is None
    assert all(request.url.path=='/v26.0/me/accounts' for request in seen)


@pytest.mark.parametrize('status,code,expected',[(401,190,'invalid'),(403,200,'permission_limited'),(429,4,'rate_limited'),(500,2,'error')])
def test_meta_error_messages_and_payloads_never_escape(status,code,expected):
    def respond(_):return httpx.Response(status,json={'error':{'code':code,'message':'private '+TOKEN,'error_data':{'token':TOKEN}}})
    with pytest.raises(GraphError) as caught:run_with(respond,lambda graph:graph.identity(TOKEN))
    assert caught.value.status==expected and TOKEN not in str(caught.value)


def test_redirect_is_not_followed_or_parsed_as_success():
    seen=[]
    def respond(request):seen.append(str(request.url));return httpx.Response(302,headers={'location':'https://evil.test/'+TOKEN},json={})
    with pytest.raises(GraphError):run_with(respond,lambda graph:graph.identity(TOKEN))
    assert len(seen)==1


def test_partial_collection_preserves_seen_items_and_bounds_repeated_cursors():
    def respond(_):return httpx.Response(200,json={'data':[{'id':'1','name':'One'}],'paging':{'next':'ignored','cursors':{'after':'same'}}})
    values,complete,error=run_with(respond,lambda graph:graph.collect('me/accounts',TOKEN,'id,name'))
    assert len(values)==2 and complete is False and isinstance(error,GraphError)


def test_business_sync_reports_partial_owned_and_client_scope_without_erasing_success():
    seen=[]
    def respond(request):
        seen.append(request.url.path)
        if request.url.path.endswith('owned_pages'):return httpx.Response(200,json={'data':[{'id':'1','name':'Owned'}]})
        return httpx.Response(403,json={'error':{'code':200,'message':TOKEN}})
    pages,complete,error=run_with(respond,lambda graph:graph.page_catalog(TOKEN,'system_user','77','88'))
    assert [page['id'] for page in pages]==['1'] and pages[0]['_source']=='owned_pages'
    assert complete is False and error.status=='permission_limited' and len(seen)==2


def test_user_catalog_intersects_exact_business_members_with_user_pages_and_retains_tasks():
    seen=[]
    accounts=[{'id':str(pid),'name':'Same Page name','access_token':f'FixturePageToken{pid}','tasks':['CREATE_CONTENT']}
              for pid in (1,2,3,4)]
    def respond(request):
        path=request.url.path.removeprefix('/v26.0/')
        seen.append(path)
        assert request.headers['authorization']=='Bearer '+TOKEN and TOKEN not in str(request.url)
        if path=='me/accounts':
            return httpx.Response(200,json={'data':accounts})
        assert request.url.params['fields']=='id'
        if path=='88/owned_pages':
            return httpx.Response(200,json={'data':[{'id':'1'},{'id':'5'}]})
        if path=='88/client_pages':
            if 'after' not in request.url.params:
                return httpx.Response(200,json={'data':[], 'paging':{'next':'ignored','cursors':{'after':'fixture-cursor'}}})
            assert request.url.params['after']=='fixture-cursor'
            return httpx.Response(200,json={'data':[{'id':'2'}]})
        if path=='99/owned_pages':
            return httpx.Response(200,json={'data':[{'id':'3'}]})
        assert path=='99/client_pages'
        return httpx.Response(200,json={'data':[]})
    for business,expected in (('88',accounts[:2]),('99',accounts[2:3])):
        pages,complete,error=run_with(respond,lambda graph:graph.page_catalog(TOKEN,'user','77',business))
        assert pages==[dict(page,_source='accounts') for page in expected]
        assert complete is True and error is None
    assert seen==['88/owned_pages','88/client_pages','88/client_pages','me/accounts',
                  '99/owned_pages','99/client_pages','me/accounts']


@pytest.mark.parametrize('bad_path',['88/client_pages','me/accounts'])
def test_scoped_user_catalog_fails_closed_on_partial_business_or_accounts_results(bad_path):
    def respond(request):
        path=request.url.path.removeprefix('/v26.0/')
        if path==bad_path and 'after' in request.url.params:
            return httpx.Response(403,json={'error':{'code':200,'message':'private '+TOKEN}})
        if path==bad_path:
            return httpx.Response(200,json={'data':[{'id':'1'}], 'paging':{'next':'ignored','cursors':{'after':'fixture-cursor'}}})
        return httpx.Response(200,json={'data':[{'id':'1'}]})
    pages,complete,error=run_with(respond,lambda graph:graph.page_catalog(TOKEN,'user','77','88'))
    assert pages==[] and complete is False and error.status=='permission_limited' and TOKEN not in str(error)


def test_scoped_user_catalog_rejects_malformed_business_membership_before_accounts_read():
    seen=[]
    def respond(request):
        seen.append(request.url.path)
        return httpx.Response(200,json={'data':[{'id':'1'},{'id':2}]})
    pages,complete,error=run_with(respond,lambda graph:graph.page_catalog(TOKEN,'user','77','88'))
    assert pages==[] and complete is False and isinstance(error,GraphError)
    assert seen==['/v26.0/88/owned_pages']


@pytest.mark.parametrize('paging',[[],None,{'next':True},{'next':'more','cursors':[]}])
def test_scoped_user_catalog_rejects_malformed_paging_in_business_edge(paging):
    def respond(request):
        assert request.url.path=='/v26.0/88/owned_pages'
        return httpx.Response(200,json={'data':[{'id':'1'}],'paging':paging})
    pages,complete,error=run_with(respond,lambda graph:graph.page_catalog(TOKEN,'user','77','88'))
    assert pages==[] and complete is False and isinstance(error,GraphError)


@pytest.mark.parametrize('value',['https://evil.test','v26.0/../../private','v0.0','26.0'])
def test_graph_version_cannot_change_origin_or_path(value):
    with pytest.raises(ValueError):graph_version(value)


def test_page_details_optional_field_failure_falls_back_without_exposing_error():
    def respond(request):
        return httpx.Response(400,json={'error':{'code':100,'message':TOKEN}}) if 'about' in request.url.params.get('fields','') else httpx.Response(200,json={'id':'1','name':'Page'})
    details,warning=run_with(respond,lambda graph:graph.details(TOKEN,'1'))
    assert details=={'id':'1','name':'Page'} and warning.code=='100' and TOKEN not in str(warning)


def test_oversize_response_is_rejected_before_json_enters_cache():
    def respond(_):return httpx.Response(200,content=b'"'+b'x'*2_000_001+b'"')
    with pytest.raises(GraphError):run_with(respond,lambda graph:graph.identity(TOKEN))


def test_permissions_metadata_is_bounded_to_safe_names_and_known_statuses():
    def respond(_):return httpx.Response(200,json={'data':[
        {'permission':'pages_read_engagement','status':'granted'},
        {'permission':TOKEN,'status':'granted'},
        {'permission':'pages_show_list','status':TOKEN},
        {'permission':'x'*81,'status':'granted'},
        {'permission':'raw '+TOKEN,'status':'declined'},
    ]})
    value=run_with(respond,lambda graph:graph.permissions(TOKEN))
    assert value==[{'permission':'pages_read_engagement','status':'granted'}] and TOKEN not in json.dumps(value)


@pytest.mark.parametrize('kind,business,expected',[
    ('user',None,'/v26.0/me/accounts'),('system_user',None,'/v26.0/77/assigned_pages'),('page',None,'/v26.0/me'),
])
def test_declared_token_type_selects_only_its_approved_catalog_edge(kind,business,expected):
    seen=[]
    def respond(request):
        seen.append(request.url.path)
        return httpx.Response(200,json={'id':'555','name':'Fixture Page'} if kind=='page' else {'data':[{'id':'555','name':'Fixture Page'}]})
    pages,complete,error=run_with(respond,lambda graph:graph.page_catalog(TOKEN,kind,'77',business))
    assert len(pages)==1 and complete is True and error is None and seen==[expected]


def test_timeout_error_never_exposes_request_headers_url_or_token():
    def respond(request):raise httpx.ReadTimeout('private '+TOKEN,request=request)
    with pytest.raises(GraphError) as error:run_with(respond,lambda graph:graph.identity(TOKEN))
    assert TOKEN not in str(error.value) and error.value.status=='error'


@pytest.mark.parametrize('limit',[0,26,True,'25'])
def test_post_query_limit_is_bounded_before_network(limit):
    def respond(_):raise AssertionError('Invalid bound cannot reach network')
    with pytest.raises(GraphError):run_with(respond,lambda graph:graph.posts(TOKEN,'555',limit))
