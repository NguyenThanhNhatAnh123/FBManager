"""Official Reel wire protocol through MockTransport only."""
import hashlib
import json
from urllib.parse import parse_qs
import httpx
import pytest
from page_manager.graph import GraphError,GraphMutationUncertain
from page_manager.reel_graph import ReelGraphClient
from page_manager import reel_graph as module
from conftest import immediate

TOKEN='FixtureReelPageToken82'


def call(respond,operation):
    async def run():
        async with ReelGraphClient(transport=httpx.MockTransport(respond)) as graph:return await operation(graph)
    return immediate(run())


@pytest.mark.parametrize('upload',['https://rupload.facebook.com/video-upload/v26.0/999','https://rupload.facebook.com/video-upload/999'])
def test_start_is_bound_to_page_and_accepts_only_documented_upload_forms(upload):
    def respond(request):
        assert str(request.url)=='https://graph.facebook.com/v26.0/555/video_reels'
        assert request.method=='POST' and request.headers['authorization']=='Bearer '+TOKEN
        assert parse_qs(request.content.decode())=={'upload_phase':['start']}
        assert TOKEN not in str(request.url) and TOKEN.encode() not in request.content
        return httpx.Response(200,json={'video_id':'999','upload_url':upload})
    assert call(respond,lambda graph:graph.start_reel(TOKEN,'555'))=='999'


def observed_call(respond,operation,observer):
    async def run():
        async with ReelGraphClient(transport=httpx.MockTransport(respond),mutation_observer=observer) as graph:
            return await operation(graph)
    return immediate(run())


@pytest.mark.parametrize('upload',[
    'https://rupload.facebook.com/video-upload/v270.0/999',
    'https://rupload.facebook.com/video-upload/v26.0/999?secret='+TOKEN,
    'https://'+TOKEN+'@evil.test/video-upload/v26.0/999',
])
def test_start_diagnostics_preserve_numeric_bound_id_before_strict_url_rejection(upload):
    events=[];seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'video_id':'999','upload_url':upload,TOKEN:TOKEN})
    with pytest.raises(GraphMutationUncertain):
        observed_call(respond,lambda graph:graph.start_reel(TOKEN,'555'),events.append)
    assert len(seen)==1 and all(r.url.host=='graph.facebook.com' for r in seen)
    parsed=next(event for event in events if event['phase']=='response_parsed')
    assert parsed['http_status']==200 and parsed['video_id']=='999'
    assert parsed['json_fields']=={'video_id':'string','upload_url':'string'} and parsed['other_key_count']==1
    assert events[-1]['phase']=='start_upload_url'
    assert TOKEN not in json.dumps(events) and upload not in json.dumps(events)
    assert all(set(event)=={'schema_version','operation','phase','http_status','exception_class',
        'json_fields','other_key_count','video_id','upload_url_summary','graph_error_code'} for event in events)


def test_numeric_json_id_is_observable_but_current_strict_parser_still_rejects():
    events=[]
    with pytest.raises(GraphMutationUncertain):
        observed_call(lambda _:httpx.Response(200,json={'video_id':999,
            'upload_url':'https://rupload.facebook.com/video-upload/v26.0/999'}),
            lambda graph:graph.start_reel(TOKEN,'555'),events.append)
    assert events[-1]['phase']=='start_video_id' and events[-1]['video_id']=='999'
    assert events[-1]['json_fields']['video_id']=='integer'


def test_mutation_diagnostics_distinguish_local_pre_transport_failure_without_raw_exception():
    events=[]
    class LocalFailure:
        def stream(self,*args,**kwargs):raise TypeError(TOKEN)
    graph=ReelGraphClient(mutation_observer=events.append);graph.client=LocalFailure()
    with pytest.raises(GraphMutationUncertain):immediate(graph.start_reel(TOKEN,'555'))
    assert len(events)==1 and events[0]['phase']=='before_dispatch'
    assert events[0]['exception_class']=='TypeError' and events[0]['http_status'] is None
    assert TOKEN not in json.dumps(events)


def test_mutation_diagnostics_distinguish_json_decode_and_meta_numeric_error():
    events=[]
    with pytest.raises(GraphMutationUncertain):
        observed_call(lambda _:httpx.Response(200,content=TOKEN.encode()),lambda graph:graph.start_reel(TOKEN,'555'),events.append)
    assert any(event['phase']=='parse_response' and event['exception_class']=='JSONDecodeError' for event in events)
    assert TOKEN not in json.dumps(events)
    events.clear()
    with pytest.raises(GraphError) as error:
        observed_call(lambda _:httpx.Response(403,json={'error':{'code':200,'message':TOKEN}}),
            lambda graph:graph.start_reel(TOKEN,'555'),events.append)
    assert error.value.status=='permission_limited' and events[-1]['graph_error_code']==200
    assert events[-1]['http_status']==403 and TOKEN not in json.dumps(events)


def test_mutation_observer_failure_never_changes_protocol_or_retries():
    seen=[]
    def broken(_event):raise RuntimeError(TOKEN)
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'video_id':'999','upload_url':'https://rupload.facebook.com/video-upload/v26.0/999'})
    assert observed_call(respond,lambda graph:graph.start_reel(TOKEN,'555'),broken)=='999'
    assert len(seen)==1


@pytest.mark.parametrize('upload',[
    'http://rupload.facebook.com/video-upload/v26.0/999','https://evil.test/video-upload/v26.0/999',
    'https://rupload.facebook.com.evil.test/video-upload/v26.0/999','https://fixture@rupload.facebook.com/video-upload/v26.0/999',
    'https://rupload.facebook.com:444/video-upload/v26.0/999','https://rupload.facebook.com/video-upload/v270.0/999',
    'https://rupload.facebook.com/video-upload/v26.0/888','https://rupload.facebook.com/video-upload/v26.0/999?token=fixture',
    'https://rupload.facebook.com/video-upload/v26.0/999#fragment','https://rupload.facebook.com/video-upload/v26.0/999/','//rupload.facebook.com/video-upload/999',None,
])
def test_untrusted_start_url_is_uncertain_and_never_followed(upload):
    seen=[]
    def respond(request):seen.append(request);return httpx.Response(200,json={'video_id':'999','upload_url':upload})
    with pytest.raises(GraphMutationUncertain):call(respond,lambda graph:graph.start_reel(TOKEN,'555'))
    assert len(seen)==1 and seen[0].url.host=='graph.facebook.com'


def test_local_upload_uses_fixed_rupload_oauth_offset_size_and_exact_binary(tmp_path,monkeypatch):
    path=tmp_path/'fixture.mp4';raw=b'fixture binary video';path.write_bytes(raw);guards=[]
    async def thread(fn,*args):return fn(*args)
    monkeypatch.setattr(module.asyncio,'to_thread',thread)
    def respond(request):
        assert str(request.url)=='https://rupload.facebook.com/video-upload/v26.0/999'
        assert request.method=='POST' and request.headers['authorization']=='OAuth '+TOKEN
        assert request.headers['offset']=='0' and request.headers['file_size']==str(len(raw))
        assert request.headers['content-length']==str(len(raw)) and request.headers['content-type']=='application/octet-stream'
        assert request.content==raw and TOKEN not in str(request.url)
        return httpx.Response(200,json={'success':True})
    assert call(respond,lambda graph:graph.upload_reel(TOKEN,'999',path,len(raw),sha256=hashlib.sha256(raw).hexdigest(),before_send=lambda:guards.append(True))) is True
    assert guards==[True]


@pytest.mark.parametrize('returned_version',['v25.0','v27.0'])
def test_start_accepts_bound_official_version_and_new_client_upload_uses_persisted_version(tmp_path,monkeypatch,returned_version):
    seen=[];version=[]
    async def thread(fn,*args):return fn(*args)
    monkeypatch.setattr(module.asyncio,'to_thread',thread)
    def start_response(request):
        seen.append(str(request.url))
        assert request.url.path=='/v26.0/555/video_reels'
        return httpx.Response(200,json={'video_id':'999','upload_url':f'https://rupload.facebook.com/video-upload/{returned_version}/999'})
    async def start(graph):
        value=await graph.start_reel(TOKEN,'555')
        version.append(graph.upload_version(value))
        assert graph.upload_target(value)==f'https://rupload.facebook.com/video-upload/{returned_version}/999'
        return value
    assert call(start_response,start)=='999' and version==[returned_version]
    path=tmp_path/'inert.mp4';path.write_bytes(b'SYNTHETIC UPLOAD FIXTURE')
    def upload_response(request):
        seen.append(str(request.url))
        assert str(request.url)==f'https://rupload.facebook.com/video-upload/{returned_version}/999'
        assert request.content==path.read_bytes() and request.headers['authorization']=='OAuth '+TOKEN
        return httpx.Response(200,json={'success':True})
    # call creates a fresh client: no in-memory start map survives this boundary.
    assert call(upload_response,lambda graph:graph.upload_reel(TOKEN,'999',path,path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),upload_version=version[0])) is True
    assert len(seen)==2


def test_changed_opened_file_hash_blocks_upload_before_any_network(tmp_path,monkeypatch):
    path=tmp_path/'fixture.mp4';path.write_bytes(b'changed video')
    async def thread(fn,*args):return fn(*args)
    monkeypatch.setattr(module.asyncio,'to_thread',thread)
    def forbidden(_):raise AssertionError('Changed file must never reach transport')
    with pytest.raises(GraphError):call(forbidden,lambda graph:graph.upload_reel(TOKEN,'999',path,path.stat().st_size,sha256='0'*64))


def test_source_guard_runs_after_opened_fd_hash_and_before_upload(tmp_path,monkeypatch):
    path=tmp_path/'fixture.mp4';path.write_bytes(b'fixture')
    async def thread(fn,*args):return fn(*args)
    monkeypatch.setattr(module.asyncio,'to_thread',thread)
    def reject():raise ValueError('Fixture source changed')
    with pytest.raises(ValueError):call(lambda _:pytest.fail('Guard must block transport'),lambda graph:graph.upload_reel(TOKEN,'999',path,7,sha256=hashlib.sha256(b'fixture').hexdigest(),before_send=reject))


def test_finish_is_one_explicit_page_post_with_caption_and_published_state():
    seen=[]
    def respond(request):
        seen.append(request)
        assert str(request.url)=='https://graph.facebook.com/v26.0/555/video_reels'
        assert parse_qs(request.content.decode())=={'upload_phase':['finish'],'video_id':['999'],'video_state':['PUBLISHED'],'description':['Fixture caption']}
        return httpx.Response(200,json={'success':True})
    assert call(respond,lambda graph:graph.finish_reel(TOKEN,'555','999','Fixture caption')) is True
    assert len(seen)==1


@pytest.mark.parametrize('stage',['start','finish','upload'])
def test_post_timeout_is_uncertain_and_not_retried(stage,tmp_path):
    seen=[];path=tmp_path/'fixture.mp4';path.write_bytes(b'fixture')
    def respond(request):seen.append(request);raise httpx.ReadTimeout(TOKEN,request=request)
    def operation(graph):
        if stage=='start':return graph.start_reel(TOKEN,'555')
        if stage=='finish':return graph.finish_reel(TOKEN,'555','999','Fixture')
        return graph.upload_reel(TOKEN,'999',path,7)
    with pytest.raises(GraphMutationUncertain) as caught:call(respond,operation)
    assert len(seen)==1 and TOKEN not in str(caught.value)


@pytest.mark.parametrize('response',[httpx.Response(200,json={}),httpx.Response(302,headers={'location':'https://evil.test'},json={}),httpx.Response(200,content=b'not-json')])
def test_finish_ack_requires_literal_success_and_never_follows_redirect(response):
    seen=[]
    def respond(request):seen.append(request);return response
    with pytest.raises(GraphMutationUncertain):call(respond,lambda graph:graph.finish_reel(TOKEN,'555','999','Fixture'))
    assert len(seen)==1


def test_meta_denial_never_returns_raw_error_or_credentials():
    def respond(_):return httpx.Response(403,json={'error':{'code':200,'message':TOKEN,'error_data':{'secret':TOKEN}}})
    with pytest.raises(GraphError) as caught:call(respond,lambda graph:graph.finish_reel(TOKEN,'555','999','Fixture'))
    assert caught.value.status=='permission_limited' and TOKEN not in str(caught.value)


def status_body(**extra):
    return {'id':'999','from':{'id':'555'},'status':{'video_status':'ready','uploading_phase':{'status':'complete'},'processing_phase':{'status':'complete'},'publishing_phase':{'status':'complete'}},'permalink_url':'https://www.facebook.com/reel/999',**extra}


@pytest.mark.parametrize('link',['https://www.facebook.com/reel/999','https://m.facebook.com/reels/999?ref=fixture','https://www.facebook.com/FixturePage/videos/999/','https://www.facebook.com/555/videos/999/'])
def test_publication_proof_binds_exact_video_owner_and_official_permalink(link):
    def respond(request):
        assert request.method=='GET' and request.url.path=='/v26.0/999'
        assert set(request.url.params['fields'].split(','))=={'id','from','status','published','permalink_url'}
        return httpx.Response(200,json=status_body(permalink_url=link))
    result=call(respond,lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published'] is True and result['owner_matches'] is True and result['published_url']=='https://www.facebook.com/reel/999'


@pytest.mark.parametrize('extra',[
    {'from':{'id':'888'}},{'from':None},
    {'status':{'video_status':'ready','publishing_phase':{'status':'not_started'}}},
    {'status':{'video_status':'error','publishing_phase':{'status':'complete'}}},
    {'status':{'video_status':'processing','processing_phase':{'status':'not_started','error':{'message':TOKEN}},'publishing_phase':{'status':'complete'}}},
    {'status':{'publishing_phase':{'status':'complete','errors':[{'message':TOKEN}]}}},
])
def test_ready_upload_ack_or_phase_error_cannot_prove_publication(extra):
    result=call(lambda _:httpx.Response(200,json=status_body(**extra)),lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published'] is False and TOKEN not in str(result)


@pytest.mark.parametrize('link',['https://evil.test/reel/999','https://www.facebook.com:444/reel/999','https://fixture@www.facebook.com/reel/999','https://www.facebook.com/reel/888','https://www.facebook.com/FixturePage/videos/888/'])
def test_foreign_permalink_never_becomes_a_completion_url(link):
    result=call(lambda _:httpx.Response(200,json=status_body(permalink_url=link)),lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published_url'] is None


def test_status_response_must_have_the_requested_video_id():
    with pytest.raises(GraphError):call(lambda _:httpx.Response(200,json=status_body(id='888')),lambda graph:graph.reel_status(TOKEN,'555','999'))


@pytest.mark.parametrize('link',['missing',None,''])
def test_explicit_publication_can_supply_canonical_url_when_optional_permalink_absent(link):
    body=status_body(published=True,permalink_url=link)
    if link=='missing':body.pop('permalink_url')
    seen=[]
    def respond(request):
        seen.append(request)
        assert request.method=='GET' and request.url.path=='/v26.0/999'
        return httpx.Response(200,json=body)
    result=call(respond,lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published'] is True and result['published_url']=='https://www.facebook.com/reel/999'
    assert result['owner_matches'] is True and not result['failed'] and len(seen)==1


@pytest.mark.parametrize('extra',[
    {'published':False},{'published':'true'},{'published':1},{'published':None},
    {'from':{'id':'888'}},{'from':None},
    {'status':{'video_status':'ready','uploading_phase':{'status':'complete'},'publishing_phase':{'status':'not_started'}}},
    {'status':{'video_status':'processing','uploading_phase':{'status':'complete'},'processing_phase':{'status':'not_started'},'publishing_phase':{'status':'complete'}}},
    {'status':{'video_status':'ready','uploading_phase':{'status':'not_started'},'publishing_phase':{'status':'complete'}}},
    {'status':{'video_status':'error','uploading_phase':{'status':'complete'},'processing_phase':{'status':'complete'},'publishing_phase':{'status':'complete'}}},
    {'status':{'video_status':'ready','uploading_phase':{'status':'complete'},'processing_phase':{'status':'complete','error':{'message':TOKEN}},'publishing_phase':{'status':'complete'}}},
    {'status':{'video_status':'ready','uploading_phase':{'status':'complete'},'processing_phase':{'status':'complete'},'publishing_phase':{'status':'complete','errors':[{'message':TOKEN}]}}},
])
def test_missing_permalink_fallback_requires_all_explicit_proof(extra):
    body=status_body(published=True,permalink_url=None);body.update(extra)
    result=call(lambda _:httpx.Response(200,json=body),lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published_url'] is None and TOKEN not in str(result)


@pytest.mark.parametrize('link',[
    'https://evil.test/reel/999','https://www.facebook.com/reel/888',
    'https://www.facebook.com/OtherPage/videos/888/','http://www.facebook.com/reel/999',
    'https://www.facebook.com:444/reel/999','https://fixture@www.facebook.com/reel/999',
    'not a URL',' ',False,0,
])
def test_explicit_published_true_does_not_repair_nonempty_untrusted_permalink(link):
    result=call(lambda _:httpx.Response(200,json=status_body(published=True,permalink_url=link)),
                lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published_url'] is None


@pytest.mark.parametrize('link',[
    '/reel/999','/reel/999/','/reels/999','/reels/999/',
    '/reel/999/?ref=fixture','/reels/999?ref=fixture&source=page',
])
def test_exact_root_relative_meta_permalink_is_canonicalized(link):
    result=call(lambda _:httpx.Response(200,json=status_body(published=True,permalink_url=link)),
                lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published'] is True and result['owner_matches'] is True
    assert result['published_url']=='https://www.facebook.com/reel/999'


@pytest.mark.parametrize('link',[
    '//www.facebook.com/reel/999','//evil.test/reel/999','///reel/999',
    '/reel/888/','/reels/888?ref=fixture','/other/999','/555/videos/999',
    '/reel/%39%39%39','/reel/999/../888','/reel/999#fragment',
    ' /reel/999','/reel/9\n99','/reel/999?ref=has space',
    '\\reel\\999','https:////reel/999',
])
def test_relative_permalink_cannot_hide_network_path_wrong_id_or_encoded_path(link):
    result=call(lambda _:httpx.Response(200,json=status_body(published=True,permalink_url=link)),
                lambda graph:graph.reel_status(TOKEN,'555','999'))
    assert result['published_url'] is None
