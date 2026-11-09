"""Bounded local video uploads and tamper-proof asset resolution, no browser."""
import asyncio
import hashlib
import os
from pathlib import Path
import uuid

import pytest

from app.services import reel_assets
from app.services.reel_assets import MAX_VIDEO_BYTES,ReelAssetError,ReelAssetStore,ReelAssetTooLarge,clean_video_name


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in synthetic stream fixture')


def video(brand=b'isom',payload=b'synthetic-video-content'):
    return (24).to_bytes(4,'big')+b'ftyp'+brand+b'\x00\x00\x00\x00'+brand+b'mp42'+(8+len(payload)).to_bytes(4,'big')+b'mdat'+payload


async def chunks(data,size=7):
    for index in range(0,len(data),size):yield data[index:index+size]


@pytest.fixture
def store(tmp_path):return ReelAssetStore(tmp_path)


@pytest.mark.parametrize('filename,brand,mime',[
    ('clip.mp4',b'isom','video/mp4'),('clip.M4V',b'M4V ','video/x-m4v'),('clip.mov',b'qt  ','video/quicktime'),
])
def test_stream_stores_complete_owned_uuid_file_with_hash_and_mime(store,filename,brand,mime):
    data=video(brand)
    asset=immediate(store.ingest(chunks(data),filename,content_length=len(data)))
    assert uuid.UUID(hex=asset['id']).hex==asset['id'] and len(asset['id'])==32
    assert asset['size']==len(data) and asset['mime']==mime and asset['sha256']==hashlib.sha256(data).hexdigest()
    path=Path(asset['path'])
    assert path.parent==store.root and path.stem==asset['id'] and path.read_bytes()==data
    assert list(store.root.iterdir())==[path]
    assert store.resolve(asset['id'],asset['path'],asset['sha256'],asset['size'])==path


def test_no_complete_asset_is_visible_until_stream_finishes(store):
    data=video()
    async def streaming():
        yield data[:17]
        assert len(list(store.root.glob('.*.upload')))==1
        assert not list(store.root.glob('*.mp4'))
        yield data[17:]
    result=immediate(store.ingest(streaming(),'clip.mp4'))
    assert Path(result['path']).is_file() and not list(store.root.glob('.*.upload'))


@pytest.mark.parametrize('name,expected',[
    ('../../private/clip.mp4','clip.mp4'),('C:\\fakepath\\Reel.MP4','Reel.mp4'),
    ('reel\u202e.mp4','reel.mp4'),('line\r\nclip.mp4','lineclip.mp4'),
    ('nhạc Việt.mp4','nhạc Việt.mp4'),
])
def test_names_are_display_only_sanitized_basenames(name,expected):
    assert clean_video_name(name)==expected


@pytest.mark.parametrize('name',['clip.webm','clip.exe','file.mp4:secret','file',None,''])
def test_unsupported_extensions_never_create_an_asset(store,name):
    with pytest.raises(ReelAssetError):immediate(store.ingest(chunks(video()),name))
    assert list(store.root.iterdir())==[]


@pytest.mark.parametrize('data',[b'',b'not a movie',video(b'avif'),video(b'heic'),video(b'M4A '),(24).to_bytes(4,'big')+b'ftypisom'])
def test_signature_rejects_empty_truncated_nonvideo_or_image_containers_and_cleans_temp(store,data):
    with pytest.raises(ReelAssetError):immediate(store.ingest(chunks(data),'clip.mp4'))
    assert list(store.root.iterdir())==[]


def test_stream_limit_is_enforced_even_without_content_length(tmp_path):
    store=ReelAssetStore(tmp_path,max_bytes=40)
    with pytest.raises(ReelAssetTooLarge):immediate(store.ingest(chunks(video()),'clip.mp4'))
    assert list(store.root.iterdir())==[]


def test_declared_oversize_is_rejected_before_consuming_any_bytes(store):
    consumed=[]
    async def stream():consumed.append(True);yield video()
    with pytest.raises(ReelAssetTooLarge):immediate(store.ingest(stream(),'clip.mp4',content_length=MAX_VIDEO_BYTES+1))
    assert consumed==[] and list(store.root.iterdir())==[]


@pytest.mark.parametrize('declared',[1,999,-1,0,True,'55'])
def test_invalid_or_mismatched_declared_length_does_not_leave_a_partial_asset(store,declared):
    with pytest.raises(ReelAssetError):immediate(store.ingest(chunks(video()),'clip.mp4',content_length=declared))
    assert list(store.root.iterdir())==[]


def test_cancelled_stream_cleans_temp_and_propagates_cancellation(store):
    async def stream():
        yield video()[:16]
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):immediate(store.ingest(stream(),'clip.mp4'))
    assert list(store.root.iterdir())==[]


def test_failed_stream_has_fixed_error_and_no_orphan_asset(store):
    async def stream():
        yield video()[:16]
        raise RuntimeError('synthetic-secret-path-or-token')
    with pytest.raises(ReelAssetError) as error:immediate(store.ingest(stream(),'clip.mp4'))
    assert 'synthetic-secret' not in str(error.value) and list(store.root.iterdir())==[]


def test_uuid_collision_never_overwrites_existing_asset_or_temp(store,monkeypatch):
    value=uuid.UUID('11111111-1111-4111-8111-111111111111')
    monkeypatch.setattr(reel_assets.uuid,'uuid4',lambda:value)
    existing=store.root/(value.hex+'.mp4');existing.write_bytes(b'existing')
    with pytest.raises(ReelAssetError):immediate(store.ingest(chunks(video()),'clip.mp4'))
    assert existing.read_bytes()==b'existing' and list(store.root.iterdir())==[existing]
    temp=store.root/f'.{value.hex}.upload';temp.write_bytes(b'other upload')
    with pytest.raises(ReelAssetError):immediate(store.ingest(chunks(video()),'clip.mp4'))
    assert temp.read_bytes()==b'other upload'


def test_resolve_rejects_hash_or_size_changes_before_posting(store):
    asset=immediate(store.ingest(chunks(video()),'clip.mp4'))
    source=Path(asset['path']);data=source.read_bytes()
    source.write_bytes(data[:-1]+b'X')
    with pytest.raises(ReelAssetError,match='Nội dung'):store.resolve(asset['id'],asset['path'],asset['sha256'],asset['size'])
    source.write_bytes(data+b'extra')
    with pytest.raises(ReelAssetError,match='Kích thước'):store.resolve(asset['id'],asset['path'],asset['sha256'],asset['size'])


def test_resolve_never_reads_arbitrary_file_outside_owned_root(store,tmp_path):
    source=tmp_path/'private.mp4';source.write_bytes(video())
    asset_id=uuid.uuid4().hex
    with pytest.raises(ReelAssetError):store.resolve(asset_id,str(source),hashlib.sha256(video()).hexdigest())
    for asset in ('../../private','not-a-uuid','A'*32):
        with pytest.raises(ReelAssetError):store.resolve(asset,str(source),'a'*64)
    with pytest.raises(ReelAssetError):store.resolve(asset_id,None,'a'*64)


def test_resolve_rejects_hardlinks_and_symlinks(store,tmp_path):
    asset=immediate(store.ingest(chunks(video()),'clip.mp4'))
    source=Path(asset['path']);alias=tmp_path/'hardlink.mp4'
    try:alias.hardlink_to(source)
    except OSError:pytest.skip('Creating hardlinks is unavailable in this managed Windows account')
    with pytest.raises(ReelAssetError):store.resolve(asset['id'],str(source),asset['sha256'])
    alias.unlink();source.unlink()
    outside=tmp_path/'outside.mp4';outside.write_bytes(video())
    try:source.symlink_to(outside)
    except OSError:pytest.skip('Creating symlinks is unavailable in this Windows account')
    with pytest.raises(ReelAssetError):store.resolve(asset['id'],str(source),asset['sha256'])


def test_asset_root_cannot_be_replaced_by_a_symlink(tmp_path):
    outside=tmp_path/'outside';outside.mkdir()
    try:(tmp_path/'reel-assets').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('Creating symlinks is unavailable in this Windows account')
    with pytest.raises(ReelAssetError):ReelAssetStore(tmp_path)


@pytest.mark.parametrize('link_kind',['hardlink','symlink'])
def test_link_guard_rejects_metadata_before_opening_video_without_link_privileges(store,monkeypatch,link_kind):
    asset=immediate(store.ingest(chunks(video()),'clip.mp4'));source=Path(asset['path'])
    original_stat=Path.lstat;original_link=Path.is_symlink;original_open=Path.open
    if link_kind=='hardlink':
        data=list(source.lstat());data[3]=2;linked_stat=os.stat_result(data)
        monkeypatch.setattr(Path,'lstat',lambda path,*args,**kwargs:linked_stat if path==source else original_stat(path,*args,**kwargs))
    else:
        monkeypatch.setattr(Path,'is_symlink',lambda path:True if path==source else original_link(path))
    def guarded_open(path,*args,**kwargs):
        if path==source:raise AssertionError('Unsafe linked file was opened')
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded_open)
    with pytest.raises(ReelAssetError):store.resolve(asset['id'],str(source),asset['sha256'])


def test_bytes_like_streams_are_counted_by_actual_byte_size(store):
    data=video(payload=b'12345678')
    async def stream():yield memoryview(bytearray(data)).cast('I')
    asset=immediate(store.ingest(stream(),'clip.mp4',content_length=len(data)))
    assert asset['size']==len(data) and Path(asset['path']).read_bytes()==data
