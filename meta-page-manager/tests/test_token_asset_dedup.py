"""Content dedup is a private-library concern; no Reel jobs or Meta requests."""
import asyncio
import json
from pathlib import Path
import threading

import pytest

from page_manager.reel_assets import TokenReelAssets
from page_manager.store import Store


def video(payload=b'fixture-video-content'):
    return (24).to_bytes(4, 'big') + b'ftypisom' + b'\0\0\0\0isommp42' + (8 + len(payload)).to_bytes(4, 'big') + b'mdat' + payload


async def stream(raw, *, pause=False):
    yield raw[:20]
    if pause: await asyncio.sleep(0)
    yield raw[20:]


@pytest.fixture
def assets(tmp_path):
    return TokenReelAssets(tmp_path / 'manager', Store(tmp_path / 'manager'))


def test_renamed_duplicate_reuses_first_metadata_and_no_private_fields(assets, tmp_path):
    async def exercise():
        original = tmp_path / 'user-original.mp4'; original.write_bytes(video())
        first = await assets.ingest(stream(original.read_bytes()), 'first.mp4')
        again = await assets.ingest(stream(original.read_bytes()), 'renamed.MOV')
        assert again == first and again['name'] == 'first.mp4' and again['mime'] == 'video/mp4'
        assert len(assets.list()) == len(list(assets.files.root.iterdir())) == 1
        assert original.read_bytes() == video() and not assets.store.reel_jobs()
        assert set(again) == {'id', 'name', 'size', 'mime', 'created_at', 'preview_url'}
        assert all(word not in json.dumps(again) for word in ('sha256', 'path', str(tmp_path)))
    asyncio.run(exercise())


def test_restarted_library_finds_existing_valid_asset(assets):
    async def exercise():
        first = await assets.ingest(stream(video()), 'first.mp4')
        restarted = TokenReelAssets(assets.files.app_root, assets.store)
        again = await restarted.ingest(stream(video()), 'retry.mp4')
        assert again == first and len(restarted.list()) == 1
        assert len(list(restarted.files.root.iterdir())) == 1
    asyncio.run(exercise())


def test_concurrent_duplicates_commit_one_canonical_asset(assets):
    async def exercise():
        results = await asyncio.gather(*[assets.ingest(stream(video(), pause=True), f'clip-{i}.mp4') for i in range(5)])
        assert all(item == results[0] for item in results)
        assert len(assets.list()) == len(list(assets.files.root.iterdir())) == 1
        assert not assets.store.reel_jobs()
    asyncio.run(exercise())


def test_same_size_different_bytes_keep_separate_assets(assets):
    async def exercise():
        first = await assets.ingest(stream(video(b'first-content')), 'one.mp4')
        other = await assets.ingest(stream(video(b'other-content')), 'two.mp4')
        assert first['size'] == other['size'] and first['id'] != other['id']
        assert len(assets.list()) == len(list(assets.files.root.iterdir())) == 2
    asyncio.run(exercise())


@pytest.mark.parametrize('damage', ['missing', 'same_size_corruption', 'outside_metadata'])
def test_corrupt_or_missing_asset_not_reused_and_history_not_changed(assets, tmp_path, damage):
    async def exercise():
        first = await assets.ingest(stream(video()), 'first.mp4')
        private = assets.resolve(first['id']); original_path = private['path']
        outside = tmp_path / 'original-outside.mp4'; outside.write_bytes(video())
        if damage == 'missing': original_path.unlink()
        elif damage == 'same_size_corruption': original_path.write_bytes(video()[:-1] + b'X')
        else:
            with assets.store.connect() as db:
                db.execute('UPDATE reel_assets SET path=? WHERE id=?', (str(outside), first['id']))
        history = assets.store.reel_asset(first['id'])
        valid = await assets.ingest(stream(video()), 'valid-new.mp4')
        assert valid['id'] != first['id'] and assets.store.reel_asset(first['id']) == history
        assert assets.resolve(valid['id'])['path'].read_bytes() == video()
        again = await assets.ingest(stream(video()), 'retry.mp4')
        assert again == valid and len(assets.list()) == 2
        assert outside.read_bytes() == video()
    asyncio.run(exercise())


def test_cancel_while_waiting_for_selection_lock_removes_only_new_owned_file(assets, tmp_path):
    async def exercise():
        original = tmp_path / 'user-original.mp4'; original.write_bytes(video())
        await assets._ingest_lock.acquire()
        task = asyncio.create_task(assets.ingest(stream(original.read_bytes()), 'cancelled.mp4'))
        for _ in range(100):
            if list(assets.files.root.glob('*.mp4')): break
            await asyncio.sleep(.001)
        assert len(list(assets.files.root.glob('*.mp4'))) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assets._ingest_lock.release()
        assert not assets.list() and not list(assets.files.root.iterdir())
        assert original.read_bytes() == video()
    asyncio.run(exercise())


def test_cancel_during_existing_asset_hash_check_keeps_canonical_file(assets, monkeypatch):
    async def exercise():
        first = await assets.ingest(stream(video()), 'canonical.mp4')
        canonical = assets.resolve(first['id'])['path']
        entered, release = threading.Event(), threading.Event()
        real_resolve = assets.resolve
        def resolving(asset_id):
            entered.set()
            assert release.wait(3)
            return real_resolve(asset_id)
        monkeypatch.setattr(assets, 'resolve', resolving)
        task = asyncio.create_task(assets.ingest(stream(video()), 'cancelled-copy.mp4'))
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.005)
        assert entered.is_set() and len(list(assets.files.root.glob('*.mp4'))) == 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert assets.list() == [first] and list(assets.files.root.iterdir()) == [canonical]
        assert canonical.read_bytes() == video()
        release.set()
    asyncio.run(exercise())


def test_dedup_does_not_change_preexisting_reel_job_policy_or_records(assets):
    async def exercise():
        first = await assets.ingest(stream(video()), 'canonical.mp4')
        # A private-library retry only touches its newly owned file, irrespective
        # of jobs. Scheduling remains owned by the unchanged Store.reel_create.
        with assets.store.connect() as db:
            before = [tuple(r) for r in db.execute('SELECT * FROM reel_jobs')]
        assert await assets.ingest(stream(video()), 'retry.mp4') == first
        with assets.store.connect() as db:
            assert [tuple(r) for r in db.execute('SELECT * FROM reel_jobs')] == before
    asyncio.run(exercise())
