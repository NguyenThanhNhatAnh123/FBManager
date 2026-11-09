"""Page edits use disposable SQLite, fake Graph and in-memory images only."""
from copy import deepcopy
import json
import struct
import zlib
from urllib.parse import parse_qs
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from page_manager.app import create_app
from page_manager.errors import AppError
from page_manager.graph import GraphClient,GraphError,GraphMutationUncertain
from page_manager.inputs import ConnectionInput,ConnectionUpdate,PageEdit,PageEditSource
from page_manager.service import PageManager
from conftest import FixtureBox,immediate
from test_http_security import request,session

TOKEN='FixtureSystemToken82'
DERIVED='FixturePageToken82'
OTHER='FixtureOtherToken82'
def png_chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
PNG=b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',3,3,8,2,0,0,0))+png_chunk(b'IDAT',zlib.compress((b'\0'+b'\xff\0\0'*3)*3))+png_chunk(b'IEND',b'')


class EditGraph:
    def __init__(self):
        self.calls=[];self.ids={TOKEN:'77',DERIVED:'555',OTHER:'888'}
        self.on_identity=None;self.on_update=None;self.update_error=None
        self.current={'id':'555','bio':'Current bio','location':{'street':'Original street'},'category_list':[{'id':'123','name':'Fixture category'}]}
        self.unavailable=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def page_token(self,token,pid):self.calls.append(('page_token',token,pid));return DERIVED
    async def identity(self,token):
        self.calls.append(('identity',token))
        if self.on_identity:self.on_identity()
        return {'id':self.ids.get(token),'name':'Fixture identity'}
    async def edit_details(self,token,pid):self.calls.append(('read',token,pid));return deepcopy(self.current),list(self.unavailable)
    async def update_page(self,token,pid,fields):
        self.calls.append(('update',token,pid,deepcopy(fields)))
        if self.on_update:self.on_update()
        if self.update_error:raise self.update_error
        return True
    async def upload_page_photo(self,token,pid,raw,mime):self.calls.append(('upload',token,pid,raw,mime));return '999'
    async def set_page_avatar(self,token,pid,photo):self.calls.append(('avatar',token,pid,photo));return True
    async def categories(self,token):self.calls.append(('categories',token));return [{'id':'123','name':'Cửa hàng cà phê'},{'id':'456','name':'Restaurant'}],True,None


def link(manager,cid,pid='555',derived=None):
    row=manager.store.connection(cid)
    page={'id':pid,'name':'Fixture Page '+pid,'category':'Original category','link':'https://www.facebook.com/'+pid,'sources':['assigned_pages'],'tasks':None}
    if derived:page['encrypted_page_token']=manager.secrets.encrypt(derived)
    manager.store.sync(cid,row['token_revision'],[page],True,'working',None,{'id':'77','name':'Fixture user'})


@pytest.fixture
def fixture(tmp_path):
    graph=EditGraph();manager=PageManager(tmp_path,graph_factory=lambda:graph,secret_box=FixtureBox())
    connection=manager.save_connection(ConnectionInput(label='Fixture source',token_type='system_user',token=TOKEN))
    link(manager,connection['id'])
    return manager,graph,connection['id']


def edit(cid,**values):return PageEdit(connection_id=cid,request_id=uuid4(),**values)


@pytest.mark.parametrize('values',[
    {},{'bio':None},{'bio':'x'*102},{'category_ids':[]},{'category_ids':['1','1']},
    {'category_ids':['1','2','3','4']},{'category_ids':['../123']},{'category_ids':['１２３']},
    {'location':{}},{'location':{'street':None}},{'location':{'url':'https://example.test'}},
    {'bio':'Fixture','token':TOKEN},{'about':'Unsupported field'},
])
def test_edit_input_is_an_explicit_bounded_field_allowlist(values):
    with pytest.raises(ValueError):edit(1,**values)


def test_edit_body_does_not_add_unspecified_location_fields():
    body=edit(1,bio='',location={'street':' New street '},category_ids=['123'])
    assert body.graph_fields()=={'bio':'','location':{'street':'New street'},'category_list':['123']}


def test_options_is_read_only_bound_to_exact_page_and_sanitizes_metadata(fixture):
    manager,graph,cid=fixture
    graph.current['bio']=TOKEN
    result=immediate(manager.edit_options('555',PageEditSource(connection_id=cid)))
    assert result['page_id']=='555' and result['connection_id']==cid
    assert result['current']['category_ids']==['123'] and result['current']['category_list']==[{'id':'123','name':'Fixture category'}]
    assert not any(call[0] in ('update','upload','avatar') for call in graph.calls)
    assert TOKEN not in json.dumps(result) and DERIVED not in json.dumps(result)
    graph.unavailable=['bio','category_list']
    result=immediate(manager.edit_options('555',PageEditSource(connection_id=cid)))
    assert 'bio' not in result['supported_fields'] and 'category_ids' not in result['supported_fields']


def test_category_name_picker_is_source_bound_accent_insensitive_and_revision_cached(fixture):
    manager,graph,cid=fixture
    value=immediate(manager.page_categories('555',cid,'CA PHE'))
    assert value=={'items':[{'id':'123','name':'Cửa hàng cà phê'}],'complete':True}
    calls=deepcopy(graph.calls)
    assert len(immediate(manager.page_categories('555',cid,'')).get('items'))==2 and graph.calls==calls
    with pytest.raises(AppError):immediate(manager.page_categories('777',cid,''))
    assert graph.calls==calls
    manager.save_connection(ConnectionUpdate(label='Rotated',token_type='system_user',token=OTHER, revision=manager.store.connection(cid)["revision"]),cid);link(manager,cid)
    immediate(manager.page_categories('555',cid,'restaurant'))
    assert sum(call[0]=='categories' for call in graph.calls)==2


def test_metadata_sends_only_explicit_fields_with_correct_page_token_and_keeps_cache(fixture):
    manager,graph,cid=fixture;before=manager.store.page('555')
    result=immediate(manager.edit_page('555',edit(cid,bio='New bio',location={'city':'Fixture city'},category_ids=['123'])))
    assert result['ok'] is True and result['outcome']=='accepted' and result['requires_refresh'] is True
    assert graph.calls==[('page_token',TOKEN,'555'),('identity',DERIVED),('read',DERIVED,'555'),('update',DERIVED,'555',{'bio':'New bio','location':{'city':'Fixture city'},'category_list':['123']})]
    assert manager.store.page('555')==before,'API acknowledgement must not invent refreshed Page metadata'
    assert all(secret not in json.dumps(result)+json.dumps(manager.store.events()) for secret in (TOKEN,DERIVED))


def test_derived_token_is_used_but_never_borrowed_from_another_connection(fixture):
    manager,graph,cid=fixture
    link(manager,cid,derived=DERIVED)
    two=manager.save_connection(ConnectionInput(label='Other source',token_type='system_user',token=OTHER));link(manager,two['id'],'777')
    immediate(manager.edit_page('555',edit(cid,bio='Chosen Page')))
    assert ('identity',DERIVED) in graph.calls and not any(call[0]=='page_token' for call in graph.calls)
    count=len(graph.calls)
    with pytest.raises(AppError):immediate(manager.edit_page('555',edit(two['id'],bio='Wrong source')))
    assert len(graph.calls)==count


def test_wrong_page_token_is_rejected_without_any_mutation(fixture):
    manager,graph,cid=fixture;graph.ids[DERIVED]='777'
    result=immediate(manager.edit_page('555',edit(cid,bio='No write')))
    assert result['outcome']=='failed' and not any(call[0]=='update' for call in graph.calls)


@pytest.mark.parametrize('when',['preflight','mutation'])
def test_revision_change_does_not_reuse_credentials_or_claim_accepted(fixture,when):
    manager,graph,cid=fixture
    def rotate():manager.save_connection(ConnectionUpdate(label='Rotated',token_type='system_user',token=OTHER, revision=manager.store.connection(cid)["revision"]),cid)
    if when=='preflight':graph.on_identity=rotate
    else:graph.on_update=rotate
    result=immediate(manager.edit_page('555',edit(cid,bio='New bio')))
    assert result['outcome']==('failed' if when=='preflight' else 'unknown')
    assert sum(call[0]=='update' for call in graph.calls)==(when=='mutation')


def test_same_request_id_replays_the_result_without_graph_or_duplicate_post(fixture):
    manager,graph,cid=fixture;body=edit(cid,bio='Only once')
    first=immediate(manager.edit_page('555',body));calls=deepcopy(graph.calls)
    assert immediate(manager.edit_page('555',body))==first and graph.calls==calls
    changed=PageEdit(connection_id=cid,request_id=body.request_id,bio='Different value')
    with pytest.raises(AppError):immediate(manager.edit_page('555',changed))
    assert graph.calls==calls


@pytest.mark.parametrize('error,expected',[(GraphError('permission_limited',200),'failed'),(GraphMutationUncertain(),'unknown')])
def test_denied_or_uncertain_mutation_is_durable_and_never_replayed(fixture,error,expected):
    manager,graph,cid=fixture;graph.update_error=error;body=edit(cid,bio='No automatic retry')
    value=immediate(manager.edit_page('555',body));calls=deepcopy(graph.calls)
    assert value['outcome']==expected
    assert immediate(manager.edit_page('555',body))==value and graph.calls==calls


def test_restart_recovers_started_request_as_unknown_instead_of_republishing(fixture,tmp_path):
    manager,graph,cid=fixture;body=edit(cid,bio='Started before restart')
    import hashlib
    digest=hashlib.sha256(json.dumps(body.graph_fields(),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    revision=manager.store.connection(cid)['token_revision']
    manager.store.edit_begin(cid,revision,'555',str(body.request_id),'metadata',digest)
    restarted=PageManager(tmp_path,graph_factory=lambda:graph,secret_box=FixtureBox())
    assert immediate(restarted.edit_page('555',body))['outcome']=='unknown'
    assert not graph.calls


@pytest.mark.parametrize('kind',['avatar','cover'])
def test_local_image_is_uploaded_unpublished_before_apply_and_request_is_idempotent(fixture,kind):
    manager,graph,cid=fixture;rid=uuid4()
    result=immediate(manager.edit_image('555',cid,rid,kind,PNG,'image/png'))
    assert result['outcome']=='accepted'
    upload=next(call for call in graph.calls if call[0]=='upload')
    assert upload[1:3]==(DERIVED,'555') and upload[4]=='image/png'
    assert graph.calls[-1]==(('avatar',DERIVED,'555','999') if kind=='avatar' else ('update',DERIVED,'555',{'cover':'999','no_feed_story':True}))
    count=len(graph.calls)
    assert immediate(manager.edit_image('555',cid,rid,kind,PNG,'image/png'))==result and len(graph.calls)==count


def test_cover_apply_denial_retains_uploaded_checkpoint_and_never_uploads_again(fixture):
    manager,graph,cid=fixture;graph.update_error=GraphError('permission_limited',200);rid=uuid4()
    result=immediate(manager.edit_image('555',cid,rid,'cover',PNG,'image/png'))
    assert result['outcome']=='partial' and result['requires_refresh'] is True
    assert immediate(manager.edit_image('555',cid,rid,'cover',PNG,'image/png'))==result
    assert sum(call[0]=='upload' for call in graph.calls)==1


def test_token_rotation_after_image_upload_blocks_apply_and_retains_durable_photo_checkpoint(fixture):
    manager,graph,cid=fixture;rid=uuid4()
    async def upload(token,pid,raw,mime):
        graph.calls.append(('upload',token,pid,raw,mime))
        manager.save_connection(ConnectionUpdate(label='Rotated',token_type='system_user',token=OTHER, revision=manager.store.connection(cid)["revision"]),cid)
        return '999'
    graph.upload_page_photo=upload
    result=immediate(manager.edit_image('555',cid,rid,'avatar',PNG,'image/png'))
    assert result['outcome']=='unknown'
    assert not any(call[0] in ('avatar','update') for call in graph.calls)
    with manager.store.connect() as db:
        checkpoint=db.execute('SELECT checkpoint FROM page_edits WHERE request_id=?',(str(rid),)).fetchone()[0]
    assert json.loads(checkpoint)=={'uploaded_photo_id':'999'}


def test_uncertain_photo_upload_is_not_applied_or_retried(fixture):
    manager,graph,cid=fixture;rid=uuid4()
    async def upload(token,pid,raw,mime):graph.calls.append(('upload',token,pid));raise GraphMutationUncertain()
    graph.upload_page_photo=upload
    result=immediate(manager.edit_image('555',cid,rid,'avatar',PNG,'image/png'))
    assert result['outcome']=='unknown'
    assert immediate(manager.edit_image('555',cid,rid,'avatar',PNG,'image/png'))==result
    assert sum(call[0]=='upload' for call in graph.calls)==1 and not any(call[0]=='avatar' for call in graph.calls)


@pytest.mark.parametrize('raw,mime',[(b'https://example.test/file.png','image/png'),(PNG,'text/html'),(b'<html>not an image</html>','image/jpeg')])
def test_image_payloads_cannot_request_remote_url_or_arbitrary_file_read(fixture,raw,mime):
    manager,graph,cid=fixture
    with pytest.raises((AppError,ValueError)):immediate(manager.edit_image('555',cid,uuid4(),'avatar',raw,mime))
    assert not graph.calls


def graph_call(responder,operation):
    async def run():
        async with GraphClient(transport=httpx.MockTransport(responder)) as graph:return await operation(graph)
    return immediate(run())


def test_graph_metadata_write_is_fixed_origin_bearer_form_and_whitelisted():
    seen=[]
    def respond(request):
        seen.append(request)
        assert request.method=='POST' and str(request.url)=='https://graph.facebook.com/v26.0/555'
        assert request.headers['authorization']=='Bearer '+DERIVED
        assert parse_qs(request.content.decode())=={'bio':['Fixture bio'],'location':['{"city":"Fixture city"}'],'category_list':['["123"]']}
        assert DERIVED not in str(request.url) and DERIVED.encode() not in request.content
        return httpx.Response(200,json={'success':True})
    assert graph_call(respond,lambda graph:graph.update_page(DERIVED,'555',{'bio':'Fixture bio','location':{'city':'Fixture city'},'category_list':['123']})) is True
    with pytest.raises(GraphError):graph_call(respond,lambda graph:graph.update_page(DERIVED,'555',{'access_token':TOKEN}))
    assert len(seen)==1


def test_graph_photo_uses_bounded_local_multipart_and_published_false():
    def respond(request):
        assert request.method=='POST' and request.url.path=='/v26.0/555/photos'
        assert request.headers['authorization']=='Bearer '+DERIVED
        assert b'name="source"' in request.content and PNG in request.content
        assert b'name="published"' in request.content and b'false' in request.content
        assert DERIVED not in str(request.url) and DERIVED.encode() not in request.content
        return httpx.Response(200,json={'id':'999'})
    assert graph_call(respond,lambda graph:graph.upload_page_photo(DERIVED,'555',PNG,'image/png'))=='999'


@pytest.mark.parametrize('response',[httpx.Response(200,json={}),httpx.Response(302,json={},headers={'location':'https://evil.test'}),httpx.Response(200,content=b'not-json')])
def test_ambiguous_write_responses_never_become_success_or_redirect(response):
    calls=[]
    def respond(request):calls.append(request);return response
    with pytest.raises(GraphMutationUncertain):graph_call(respond,lambda graph:graph.update_page(DERIVED,'555',{'bio':'Fixture'}))
    assert len(calls)==1


def test_known_meta_denial_never_echoes_response_secrets_or_request_details():
    def respond(_):return httpx.Response(403,json={'error':{'code':200,'message':TOKEN+DERIVED,'error_data':{'secret':DERIVED}}})
    with pytest.raises(GraphError) as result:graph_call(respond,lambda graph:graph.update_page(DERIVED,'555',{'bio':'Fixture'}))
    assert result.value.status=='permission_limited' and TOKEN not in str(result.value) and DERIVED not in str(result.value)


def test_page_token_resolution_cannot_accept_another_page_or_malformed_secret():
    for value in ({'id':'777','access_token':DERIVED},{'id':'555','access_token':'123|secret'},{'id':'555'}):
        with pytest.raises(GraphError):graph_call(lambda _:httpx.Response(200,json=value),lambda graph:graph.page_token(TOKEN,'555'))


def test_edit_optional_field_failure_is_explicit_and_does_not_clear_other_values():
    seen=[]
    def respond(request):
        fields=request.url.params['fields'];seen.append(fields)
        if fields=='id,location':return httpx.Response(200,json={'id':'555','location':{'city':'Fixture city'}})
        if fields=='id,category_list':return httpx.Response(200,json={'id':'555','category_list':[{'id':'123','name':'Fixture category'}]})
        return httpx.Response(400,json={'error':{'code':100,'message':TOKEN}})
    raw,unsupported=graph_call(respond,lambda graph:graph.edit_details(DERIVED,'555'))
    assert unsupported==['bio','website','is_always_open','about'] and 'bio' not in raw and raw['_bio_field'] is None
    assert raw['location']=={'city':'Fixture city'} and raw['category_list'][0]['id']=='123'
    assert len(seen)==7


def test_about_is_chosen_only_after_bio_read_is_rejected_and_preserves_other_fields():
    seen=[]
    def respond(request):
        fields=request.url.params['fields'];seen.append(fields)
        if fields=='id,about':return httpx.Response(200,json={'id':'555','about':'Documented introduction'})
        if fields=='id,location':return httpx.Response(200,json={'id':'555','location':{'city':'Fixture city'}})
        if fields=='id,category_list':return httpx.Response(200,json={'id':'555','category_list':[{'id':'123','name':'Fixture category'}]})
        return httpx.Response(400,json={'error':{'code':100}})
    raw,unsupported=graph_call(respond,lambda graph:graph.edit_details(DERIVED,'555'))
    assert raw['_bio_field']=='about' and raw['about']=='Documented introduction' and unsupported==['bio','website','is_always_open']
    assert raw['location']=={'city':'Fixture city'} and raw['category_list'][0]['id']=='123'
    assert seen[-1]=='id,about' and len(seen)==7


@pytest.mark.parametrize('rejected',[False,True])
def test_about_mapping_is_resolved_before_one_write_and_never_retries_a_denial(fixture,rejected):
    manager,graph,cid=fixture
    graph.current={'id':'555','about':'Current intro','_bio_field':'about','location':{'city':'Fixture city'},'category_list':[{'id':'123','name':'Fixture category'}]}
    graph.unavailable=['bio']
    options=immediate(manager.edit_options('555',PageEditSource(connection_id=cid)))
    assert options['current']['bio']=='Current intro' and options['current']['bio_field']=='about'
    assert 'bio' in options['supported_fields'] and options['current']['category_ids']==['123']
    if rejected:graph.update_error=GraphError('permission_limited',200)
    graph.calls=[]
    result=immediate(manager.edit_page('555',edit(cid,bio='New intro',location={'city':'New city'})))
    updates=[value for value in graph.calls if value[0]=='update']
    assert updates==[('update',DERIVED,'555',{'about':'New intro','location':{'city':'New city'}})]
    assert result['outcome']==('failed' if rejected else 'accepted')


def test_both_bio_fields_unavailable_refuses_before_post(fixture):
    manager,graph,cid=fixture;graph.current={'id':'555','_bio_field':None};graph.unavailable=['bio','about']
    result=immediate(manager.edit_page('555',edit(cid,bio='Do not send')))
    assert result['outcome']=='failed' and not any(value[0]=='update' for value in graph.calls)


def test_official_category_taxonomy_flattens_bounded_children_without_following_links():
    seen=[]
    def respond(request):
        seen.append(request)
        assert request.url.path=='/v26.0/fb_page_categories' and request.method=='GET'
        assert request.url.params['fields'].count('fb_page_categories')==3
        return httpx.Response(200,json={'data':[{'id':'1','name':'Business','fb_page_categories':{'data':[{'id':'2','name':'Cafe','fb_page_categories':{'data':[{'id':'3','name':'Coffee shop'}],'paging':{'next':'https://evil.test/'+TOKEN}}}]}}]})
    values,complete,error=graph_call(respond,lambda graph:graph.categories(DERIVED))
    assert values==[{'id':'1','name':'Business'},{'id':'2','name':'Cafe'},{'id':'3','name':'Coffee shop'}]
    assert complete is False and error is None and len(seen)==1


def test_category_taxonomy_cannot_silently_claim_complete_beyond_depth_bound():
    node={'id':'5','name':'Unfetched depth'}
    for number in range(4,0,-1):node={'id':str(number),'name':'Level '+str(number),'fb_page_categories':[node]}
    values,complete,_=graph_call(lambda _:httpx.Response(200,json={'data':[node]}),lambda graph:graph.categories(DERIVED))
    assert [item['id'] for item in values]==['1','2','3','4'] and complete is False


def test_edit_routes_require_their_own_session_and_csrf_before_graph(tmp_path):
    def forbidden():raise AssertionError('No Graph before request authorization')
    app=create_app(tmp_path,secret_box=FixtureBox(),graph_factory=forbidden)
    payload={'connection_id':1,'request_id':str(uuid4()),'bio':'Fixture'}
    assert request(app,'/api/pages/555/edit','POST',payload)[0]==401
    headers=session(app)
    assert request(app,'/api/pages/555/edit','POST',payload,{'cookie':headers['cookie']})[0]==403
    assert request(app,'/api/pages/555/edit-options','POST',{'connection_id':1},{'cookie':headers['cookie']})[0]==403
    assert 'blob:' in request(app,'/api/session')[1]['content-security-policy']


def raw_request(app,target,raw,headers):
    parsed=urlsplit(target)
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http',
           'path':parsed.path,'raw_path':parsed.path.encode(),'query_string':parsed.query.encode(),'root_path':'','client':('127.0.0.1',1234),'server':('127.0.0.1',8010),
           'headers':[(key.lower().encode(),value.encode('latin1')) for key,value in {'host':'127.0.0.1:8010','content-type':'image/png',**headers}.items()]}
    received=False;messages=[]
    async def receive():
        nonlocal received
        if received:return {'type':'http.disconnect'}
        received=True;return {'type':'http.request','body':raw,'more_body':False}
    async def send(value):messages.append(value)
    immediate(app(scope,receive,send))
    start=next(item for item in messages if item['type']=='http.response.start')
    body=b''.join(item.get('body',b'') for item in messages if item['type']=='http.response.body')
    return start['status'],json.loads(body),received


def test_raw_image_route_authorizes_first_and_has_only_its_own_8mib_allowance(tmp_path,monkeypatch):
    app=create_app(tmp_path,secret_box=FixtureBox());headers=session(app);received=[]
    async def image(pid,cid,rid,kind,raw,mime):
        received.append((pid,cid,rid,kind,len(raw),mime))
        return {'ok':True,'outcome':'accepted','message':'Fixture accepted','requires_refresh':True}
    monkeypatch.setattr(app.state.manager,'edit_image',image)
    rid=uuid4();target='/api/pages/555/image?connection_id=1&kind=avatar&request_id='+str(rid)
    assert raw_request(app,target,PNG,{})[0::2]==(401,False)
    assert raw_request(app,target,PNG,{'cookie':headers['cookie']})[0::2]==(403,False)
    # The route cap exceeds the ordinary token/JSON body cap, without raising
    # the allowance on any other API. The service is a fake, so no upload runs.
    large=b'x'*128_001
    assert raw_request(app,target,large,headers)[0]==200
    assert received==[('555',1,rid,'avatar',len(large),'image/png')]
    assert raw_request(app,target,b'',{**headers,'content-length':str(8*1024*1024+1)})[0]==413
    assert raw_request(app,'/api/pages/555/edit',large,headers)[0]==413
    assert raw_request(app,target.replace(str(rid),'not-a-uuid'),PNG,headers)[0]==422
    assert len(received)==1
