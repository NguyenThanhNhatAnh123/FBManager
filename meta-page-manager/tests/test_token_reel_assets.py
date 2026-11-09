"""Independent asset integrity/storage tests using synthetic container bytes."""
import asyncio
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from conftest import immediate
from page_manager import reel_assets as module
from page_manager.reel_assets import ReelAssetError, ReelAssetStore, ReelAssetTooLarge, TokenReelAssets
from page_manager.store import Store


def video(brand=b'isom', payload=b'offline-fixture-video-content'):
    return (24).to_bytes(4, 'big') + b'ftyp' + brand + b'\0\0\0\0' + brand + b'mp42' + (8 + len(payload)).to_bytes(4, 'big') + b'mdat' + payload


async def chunks(data, size=7):
    for index in range(0, len(data), size):
        yield data[index:index + size]


@pytest.fixture
def assets(tmp_path):
    return TokenReelAssets(tmp_path, Store(tmp_path), max_bytes=256)


@pytest.mark.parametrize('filename,brand,mime', [
    ('clip.mp4', b'isom', 'video/mp4'), ('clip.M4V', b'M4V ', 'video/x-m4v'),
    ('clip.mov', b'qt  ', 'video/quicktime'),
])
def test_private_assets_round_trip_after_restart_without_exposing_path_hash_or_tokens(assets, tmp_path, filename, brand, mime):
    data = video(brand)
    public = immediate(assets.ingest(chunks(data), filename, content_length=len(data)))
    assert public['mime'] == mime and public['size'] == len(data)
    assert set(public) == {'id', 'name', 'size', 'mime', 'created_at', 'preview_url'}
    assert str(tmp_path) not in json.dumps(public)
    restarted = TokenReelAssets(tmp_path, Store(tmp_path), max_bytes=256)
    assert restarted.list() == [public]
    private = restarted.resolve(public['id'])
    assert private['path'].parent == restarted.files.root and private['path'].read_bytes() == data
    assert private['sha256'] == hashlib.sha256(data).hexdigest()


def test_unfinished_stream_never_exposes_complete_file_or_catalog_row(assets):
    data = video()
    async def stream():
        yield data[:17]
        assert not assets.list() and not list(assets.files.root.glob('*.mp4'))
        yield data[17:]
    public = immediate(assets.ingest(stream(), r'C:\fakepath\..\safe\clip.MP4'))
    assert public['name'] == 'clip.mp4' and not list(assets.files.root.glob('.*.upload'))


@pytest.mark.parametrize('data', [b'', b'not video', video(b'avif'), video(b'heic'), video(b'M4A '), b'\0\0\0\x18ftypisom'])
def test_nonvideo_image_audio_or_truncated_header_leaves_no_asset(assets, data):
    with pytest.raises(ReelAssetError):
        immediate(assets.ingest(chunks(data), 'clip.mp4'))
    assert not assets.list() and not list(assets.files.root.iterdir())


@pytest.mark.parametrize('declared', [0, -1, True, '100', 1, 200])
def test_invalid_or_mismatched_content_length_cannot_publish_partial_asset(assets, declared):
    with pytest.raises(ReelAssetError):
        immediate(assets.ingest(chunks(video()), 'clip.mp4', content_length=declared))
    assert not assets.list() and not list(assets.files.root.iterdir())


def test_size_limit_applies_to_declared_and_actual_stream_before_metadata(assets):
    consumed = []
    async def source():
        consumed.append(True)
        yield video()
    with pytest.raises(ReelAssetTooLarge):
        immediate(assets.ingest(source(), 'clip.mp4', content_length=257))
    assert not consumed
    with pytest.raises(ReelAssetTooLarge):
        immediate(assets.ingest(chunks(video(payload=b'x' * 300)), 'clip.mp4'))
    assert not assets.list() and not list(assets.files.root.iterdir())


@pytest.mark.parametrize('failure', ['cancelled', 'error', 'invalid_chunk'])
def test_interrupted_stream_cleans_owned_temp_and_redacts_exception(assets, failure):
    async def source():
        yield video()[:17]
        if failure == 'cancelled': raise asyncio.CancelledError()
        if failure == 'error': raise RuntimeError('private-fixture-token-or-path')
        yield 'invalid text chunk'
    with pytest.raises(asyncio.CancelledError if failure == 'cancelled' else ReelAssetError) as error:
        immediate(assets.ingest(source(), 'clip.mp4'))
    assert 'private-fixture' not in str(error.value)
    assert not assets.list() and not list(assets.files.root.iterdir())


def test_recording_failure_removes_only_uncommitted_file(assets, monkeypatch):
    def failure(_): raise RuntimeError('fixture database unavailable')
    monkeypatch.setattr(assets.store, 'reel_asset_save', failure)
    with pytest.raises(RuntimeError):
        immediate(assets.ingest(chunks(video()), 'clip.mp4'))
    assert not assets.list() and not list(assets.files.root.iterdir())


def test_postcommit_projection_failure_cannot_delete_a_persisted_asset(assets, monkeypatch):
    original = assets.store.reel_asset
    def failure(_): raise RuntimeError('fixture read failure after commit')
    monkeypatch.setattr(assets.store, 'reel_asset', failure)
    try:
        immediate(assets.ingest(chunks(video()), 'clip.mp4'))
    except RuntimeError:
        pass
    rows = assets.store.reel_assets()
    assert len(rows) == 1 and Path(rows[0]['path']).is_file(), 'Committed catalog must never reference a file deleted by response/projection cleanup'
    monkeypatch.setattr(assets.store, 'reel_asset', original)
    assert assets.resolve(rows[0]['id'])['path'].is_file()


@pytest.mark.parametrize('tamper', ['same_size', 'size', 'hash', 'outside_path'])
def test_tampered_bytes_or_metadata_cannot_resolve_for_upload(assets, tmp_path, tamper):
    public = immediate(assets.ingest(chunks(video()), 'clip.mp4'))
    private = assets.resolve(public['id'])
    path = private['path']
    if tamper == 'same_size': path.write_bytes(path.read_bytes()[:-1] + b'X')
    elif tamper == 'size': path.write_bytes(path.read_bytes() + b'X')
    else:
        if tamper == 'outside_path':
            outside = tmp_path / 'private.mp4'
            outside.write_bytes(video())
            field, value = 'path', str(outside)
        else:
            field, value = 'sha256', '0' * 64
        with assets.store.connect() as db:
            db.execute('UPDATE reel_assets SET ' + field + '=? WHERE id=?', (value, public['id']))
    with pytest.raises(ReelAssetError): assets.resolve(public['id'])


def test_uuid_collision_never_overwrites_an_existing_file(assets, monkeypatch):
    fixed = uuid.UUID('11111111-1111-4111-8111-111111111111')
    monkeypatch.setattr(module.uuid, 'uuid4', lambda: fixed)
    existing = assets.files.root / (fixed.hex + '.mp4')
    existing.write_bytes(b'previous fixture')
    with pytest.raises(ReelAssetError):
        immediate(assets.ingest(chunks(video()), 'clip.mp4'))
    assert existing.read_bytes() == b'previous fixture' and list(assets.files.root.iterdir()) == [existing]
    assert not assets.list()


def test_linked_file_and_replaced_root_are_rejected_before_any_read(assets, monkeypatch):
    public = immediate(assets.ingest(chunks(video()), 'clip.mp4'))
    source = assets.resolve(public['id'])['path']
    original_link, original_open = Path.is_symlink, Path.open
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == source or original_link(path))
    def safe_open(path, *args, **kwargs):
        if path == source: raise AssertionError('Linked file must not be opened')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', safe_open)
    with pytest.raises(ReelAssetError): assets.resolve(public['id'])
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == assets.files.root or original_link(path))
    with pytest.raises(ReelAssetError): ReelAssetStore(assets.files.app_root)
