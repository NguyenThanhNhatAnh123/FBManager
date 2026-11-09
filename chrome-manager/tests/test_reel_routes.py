"""Direct API tests with synthetic uploads; no production schedule or network."""
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from app.api import routes
from app.core.config import Config
from app.core.reel_inputs import VerifyReelLink
from app.main import create_app
from app.services.reel_assets import ReelAssetStore


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O')


def video():return (24).to_bytes(4,'big')+b'ftypisom\x00\x00\x00\x00isommp42'+(12).to_bytes(4,'big')+b'mdatdata'


def request(svc,content=b'',headers=None):
    async def stream():yield content
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=svc)),headers=headers or {},stream=stream)


def test_upload_stream_returns_metadata_only_and_uses_decoded_filename(tmp_path):
    store=ReelAssetStore(tmp_path);saved=[]
    def add(meta):saved.append(meta);return {key:meta[key] for key in ('id','name','size','mime')}
    result=immediate(routes.upload_reel_asset(request(SimpleNamespace(reels=SimpleNamespace(assets=store,add_asset=add)),video(),{'x-filename':quote('Video thật.mp4'),'content-length':str(len(video()))})))
    assert result['name']=='Video thật.mp4' and result['size']==len(video())
    assert 'path' not in result and 'sha256' not in result and Path(saved[0]['path']).is_file()


def test_upload_limits_are_enforced_while_streaming_without_content_length(tmp_path):
    store=ReelAssetStore(tmp_path,max_bytes=8)
    with pytest.raises(HTTPException) as error:
        immediate(routes.upload_reel_asset(request(SimpleNamespace(reels=SimpleNamespace(assets=store)),video(),{'x-filename':'clip.mp4'})))
    assert error.value.status_code==413 and list(store.root.iterdir())==[]


def test_failed_asset_commit_cleans_only_new_owned_upload(tmp_path):
    store=ReelAssetStore(tmp_path)
    def fail(_):raise RuntimeError('fixture database failure')
    with pytest.raises(RuntimeError):immediate(routes.upload_reel_asset(request(SimpleNamespace(reels=SimpleNamespace(assets=store,add_asset=fail)),video())))
    assert list(store.root.iterdir())==[]


def test_preview_is_inline_range_capable_and_integrity_work_is_off_thread(tmp_path,monkeypatch):
    path=tmp_path/'clip.mp4';path.write_bytes(video());calls=[]
    asset=SimpleNamespace(id='a'*32,path=str(path),sha256='hash',size=len(video()),mime='video/mp4',name='clip.mp4')
    def resolve(*args):calls.append(args);return path
    async def threaded(function,*args):return function(*args)
    monkeypatch.setattr(routes.asyncio,'to_thread',threaded)
    response=immediate(routes.reel_video(asset.id,request(SimpleNamespace(reels=SimpleNamespace(asset=lambda _:asset,assets=SimpleNamespace(resolve=resolve))))))
    assert calls and response.headers['accept-ranges']=='bytes' and response.headers['content-disposition'].startswith('inline;')


def test_reel_owner_blocks_profile_edit_before_touching_database():
    svc=SimpleNamespace(browsers=SimpleNamespace(reel_profiles={3},recipient_profiles=set()))
    with pytest.raises(HTTPException) as error:routes.assert_recipient_available(svc,3)
    assert error.value.status_code==409


def test_large_body_exception_applies_only_to_raw_reel_upload(tmp_path):
    app=create_app(Config(root=tmp_path,data_dir=tmp_path/'data'))
    session_route=next(route for route in app.routes if getattr(route,'path','')=='/api/session')
    cookie=SimpleCookie();cookie.load(immediate(session_route.endpoint()).headers['set-cookie'])
    token=cookie['cm_session'].value
    middleware=next(item.kwargs['dispatch'] for item in app.user_middleware if 'dispatch' in item.kwargs)
    async def next_response(_):return JSONResponse({'ok':True})
    def run(path):
        req=SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'),url=SimpleNamespace(path=path),method='POST',headers={'content-length':'3000000'},cookies={'cm_session':token})
        return immediate(middleware(req,next_response))
    assert run('/api/reels/assets').status_code==200
    assert run('/api/reels').status_code==413


def test_reel_list_defaults_to_visible_and_explicit_archive_history_is_forwarded():
    calls=[]
    def listing(*,include_archived=False):calls.append(include_archived);return [{'history':include_archived}]
    req=request(SimpleNamespace(reels=SimpleNamespace(list=listing)))
    assert immediate(routes.reel_jobs(req))==[{'history':False}]
    assert immediate(routes.reel_jobs(req,include_archived=True))==[{'history':True}]
    assert calls==[False,True]


def test_page_selector_filter_is_explicit_and_legacy_array_contract_is_unchanged():
    calls=[]
    def options(profile_id,*,posting_eligible_only=False):
        calls.append((profile_id,posting_eligible_only));return [{'page_url':'fixture','can_publish':posting_eligible_only}]
    req=request(SimpleNamespace(reels=SimpleNamespace(page_options=options)))
    assert immediate(routes.reel_pages(3,req))==[{'page_url':'fixture','can_publish':False}]
    assert immediate(routes.reel_pages(3,req,posting_eligible_only=True))==[{'page_url':'fixture','can_publish':True}]
    assert calls==[(3,False),(3,True)]


def test_page_selector_http_filter_rejects_invalid_boolean():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI();app.state.services=SimpleNamespace(reels=SimpleNamespace(page_options=lambda *a,**k:[]));app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.get('/api/reels/pages?profile_id=3&posting_eligible_only=invalid').status_code==422


def test_archive_restore_routes_are_metadata_only_and_registered_as_post(tmp_path):
    calls=[]
    def archive(job_id):calls.append(('archive',job_id));return {'ok':True,'archived_at':'fixture-stamp'}
    def restore(job_id):calls.append(('restore',job_id));return {'ok':True,'archived_at':None}
    req=request(SimpleNamespace(reels=SimpleNamespace(archive=archive,restore=restore)))
    assert immediate(routes.archive_reel(7,req))=={'ok':True,'archived_at':'fixture-stamp'}
    assert immediate(routes.restore_reel(7,req))=={'ok':True,'archived_at':None}
    assert calls==[('archive',7),('restore',7)]
    for path in ('/api/reels/{job_id}/archive','/api/reels/{job_id}/restore'):
        route=next(route for route in routes.router.routes if getattr(route,'path','')==path)
        assert route.methods=={'POST'}


def test_verify_link_route_only_forwards_validated_permalink_to_existing_job():
    calls=[]
    def verify(job_id,url):calls.append((job_id,url));return {'ok':True,'verification_url':url}
    req=request(SimpleNamespace(reels=SimpleNamespace(verify_link=verify)))
    body=VerifyReelLink(reel_url='https://m.facebook.com/reels/123456/?ref=fixture')
    assert immediate(routes.verify_reel_link(7,body,req))=={'ok':True,'verification_url':'https://www.facebook.com/reel/123456'}
    assert calls==[(7,'https://www.facebook.com/reel/123456')]
    route=next(route for route in routes.router.routes if getattr(route,'path','')=='/api/reels/{job_id}/verify-link')
    assert route.methods=={'POST'} and route.status_code==202
