"""Folder automation tests: temporary folders/SQLite, no Graph or publishing."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager import video_source as module
from page_manager.errors import AppError
from page_manager.inputs import ConnectionInput, ConnectionUpdate
from page_manager.reel_assets import TokenReelAssets
from page_manager.service import PageManager
from page_manager.video_source import FolderVideoSource, VideoSourceSettings
from conftest import FixtureBox, immediate, mark_page_information_updated


TOKEN = 'FixturePrivateSourceToken82'


def video(payload=b'fixture-video'):
    return (24).to_bytes(4, 'big') + b'ftypisom' + b'\0\0\0\0isommp42' + (8 + len(payload)).to_bytes(4, 'big') + b'mdat' + payload


async def stream(raw):
    yield raw


@pytest.fixture
def setup(tmp_path, monkeypatch):
    async def thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(module.asyncio, 'to_thread', thread)

    def forbidden_graph():
        pytest.fail('Folder import must never call Graph or publish')

    manager = PageManager(tmp_path / 'manager', graph_factory=forbidden_graph, secret_box=FixtureBox())
    cid = manager.save_connection(ConnectionInput(label='Offline source', token_type='system_user', token=TOKEN))['id']
    manager.store.sync(cid, 1, [{'id': pid, 'name': 'Fixture Page ' + pid, 'sources': ['assigned_pages']}
                              for pid in ('555', '777')], True, 'working', None, {})
    for pid in ('555','777'):mark_page_information_updated(manager.store,pid,cid)
    assets = TokenReelAssets(tmp_path / 'manager', manager.store)
    folder = tmp_path / 'videos'
    folder.mkdir()
    clock = [datetime(2030, 11, 1, 12, 0, tzinfo=timezone.utc)]
    source = FolderVideoSource(manager, assets, directory=folder, clock=lambda: clock[0])
    return SimpleNamespace(manager=manager, assets=assets, folder=folder, clock=clock, source=source, cid=cid)


def settings(f, **extra):
    extra.setdefault('revision', f.source._settings()['revision'])
    return VideoSourceSettings(enabled=True, page_ids=['555', '777'], connection_id=f.cid,
                               **extra)


def write(f, name, payload=b'fixture-video'):
    path = f.folder / name
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_bytes(video(payload))
    return path


def ready_scan(f):
    immediate(f.source.scan_once())
    f.clock[0] += timedelta(seconds=31)
    return immediate(f.source.scan_once())


def rows(f):
    with f.manager.store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM video_source_items ORDER BY rowid')]


def test_project_copy_of_historical_cancelled_video_does_not_recreate_job_or_rewrite_ledger(setup,tmp_path):
    f=setup;f.source.configure(settings(f));original=write(f,'historical.mp4');ready_scan(f)
    job=f.manager.store.reel_jobs()[0]
    f.manager.store.reel_update(job['id'],status='cancelled')
    frozen=rows(f)
    destination=tmp_path/'project/media/videos';destination.mkdir(parents=True)
    (destination/original.name).write_bytes(original.read_bytes())
    f.source=FolderVideoSource(f.manager,f.assets,directory=destination,clock=lambda:f.clock[0])
    ready_scan(f);ready_scan(f)
    assert rows(f)==frozen
    assert len(f.manager.store.reel_jobs())==1 and f.manager.store.reel_jobs()[0]['status']=='cancelled'
    assert f.manager.store.reel_jobs()[0]['request_id']==job['request_id']


def test_blocked_imported_backlog_does_not_consume_quota_for_new_ready_video(setup):
    f = setup; f.source.configure(settings(f))
    revision = f.source.status()['settings']['revision']
    blocked = []
    for index in range(f.source.max_imports_per_scan):
        with f.manager.store.connect() as db: db.execute('UPDATE video_source_settings SET cursor=0')
        path = write(f, 'blocked-' + str(index) + '.mp4', ('old-' + str(index)).encode())
        candidate = {'path': str(path), 'filename': path.name, 'signature': module._checked(path, f.folder)}
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        item = f.source._reserve(candidate, sha, None, revision)
        asset = immediate(f.source._import(item, candidate, revision))
        f.source._update(item['id'], status='ready', asset_id=asset['id'], message='Synthetic imported pending item')
        blocked.append((item['id'], item['payload']))
    with f.manager.store.connect() as db: db.execute('DELETE FROM page_edits WHERE page_id=?', ('555',))
    fresh = write(f, 'new-ready.mp4', b'distinct new ready video')
    fresh_sha = hashlib.sha256(fresh.read_bytes()).hexdigest()
    ready_scan(f)
    jobs = f.manager.store.reel_jobs()
    assert len(jobs) == 1 and jobs[0]['page_id'] == '777'
    assert f.manager.store.reel_asset(jobs[0]['asset_id'])['sha256'] == fresh_sha
    for item_id, payload in blocked:
        item = f.source._item(item_id=item_id)
        assert item['id'] == item_id and item['payload'] == payload and item['job_id'] is None
    ready_scan(f)
    assert len(f.manager.store.reel_jobs()) == 1


def test_pending_retry_window_advances_past_blocked_rows_across_restart(setup, monkeypatch):
    f = setup; f.source.configure(settings(f))
    # Exercise the same bounded-window algorithm with a small deterministic window.
    monkeypatch.setattr(module, 'PENDING_RETRY_BATCH', 2)
    revision = f.source.status()['settings']['revision']
    frozen = []
    for index in range(3):
        with f.manager.store.connect() as db:
            db.execute('UPDATE video_source_settings SET cursor=?', (0 if index < 2 else 1,))
        path = write(f, 'pending-' + str(index) + '.mp4', ('pending-' + str(index)).encode())
        candidate = {'path': str(path), 'filename': path.name, 'signature': module._checked(path, f.folder)}
        item = f.source._reserve(candidate, hashlib.sha256(path.read_bytes()).hexdigest(), None, revision)
        asset = immediate(f.source._import(item, candidate, revision))
        f.source._update(item['id'], status='ready', asset_id=asset['id'], message='Synthetic imported pending item')
        frozen.append((item['id'], item['payload']))
        path.unlink()  # Imported pending jobs no longer need the original watched file.
    with f.manager.store.connect() as db:
        db.execute('DELETE FROM page_edits WHERE page_id=?', ('555',))
        original = dict(db.execute('SELECT settings,revision,cursor FROM video_source_settings').fetchone())
    immediate(f.source.scan_once())
    assert not f.manager.store.reel_jobs()
    with f.manager.store.connect() as db:
        assert db.execute('SELECT pending_cursor FROM video_source_settings').fetchone()[0] == 2
    f.source = FolderVideoSource(f.manager, f.assets, directory=f.folder, clock=lambda: f.clock[0])
    immediate(f.source.scan_once())
    jobs = f.manager.store.reel_jobs()
    assert len(jobs) == 1 and jobs[0]['page_id'] == '777' and jobs[0]['request_id'] == frozen[2][0]
    with f.manager.store.connect() as db:
        assert dict(db.execute('SELECT settings,revision,cursor FROM video_source_settings').fetchone()) == original
    for item_id, payload in frozen[:2]:
        item = f.source._item(item_id=item_id)
        assert item['payload'] == payload and item['job_id'] is None


def test_legacy_source_gains_retry_cursor_without_changing_configuration_or_round_robin(setup):
    f = setup; f.source.configure(settings(f))
    with f.manager.store.connect() as db:
        db.execute('UPDATE video_source_settings SET cursor=7')
        original = dict(db.execute('SELECT settings,revision,cursor FROM video_source_settings').fetchone())
        db.execute('ALTER TABLE video_source_settings DROP COLUMN pending_cursor')
    reopened = FolderVideoSource(f.manager, f.assets, directory=f.folder, clock=lambda: f.clock[0])
    with f.manager.store.connect() as db:
        assert dict(db.execute('SELECT settings,revision,cursor FROM video_source_settings').fetchone()) == original
        assert db.execute('SELECT pending_cursor FROM video_source_settings').fetchone()[0] == 0
    assert reopened.status()['settings']['page_ids'] == ['555', '777']


def test_enabling_source_rejects_unedited_pages_without_changing_saved_state(setup):
    f=setup
    with f.manager.store.connect() as db:db.execute('DELETE FROM page_edits WHERE page_id=?',('777',))
    with pytest.raises(AppError) as error:f.source.configure(settings(f))
    assert error.value.code=='page_information_required' and error.value.status_code==422
    assert not f.source.status()['settings']['enabled'] and not rows(f)


def test_existing_source_can_be_paused_with_saved_pool_after_all_pages_and_source_are_unavailable(setup):
    f=setup;f.source.configure(settings(f))
    with f.manager.store.connect() as db:
        db.execute('DELETE FROM page_edits')
        db.execute('DELETE FROM connection_pages')
        db.execute('DELETE FROM pages')
        db.execute('DELETE FROM connections')
    value=f.source.configure(settings(f).model_copy(update={'enabled':False}))
    assert value['settings']['enabled'] is False and value['settings']['page_ids']==['555','777']
    assert value['settings']['connection_id']==f.cid and not f.manager.store.reel_jobs()


def test_existing_rotation_skips_unedited_page_without_starving_ready_pages(setup):
    f=setup;f.source.configure(settings(f))
    with f.manager.store.connect() as db:db.execute('DELETE FROM page_edits WHERE page_id=?',('555',))
    write(f,'one.mp4',b'one');write(f,'two.mp4',b'two')
    value=f.source.status()
    assert value['next_page_id']=='777'
    assert value['posting_eligible_page_ids']==['777'] and value['posting_blocked_page_ids']==['555']
    ready_scan(f)
    jobs=f.manager.store.reel_jobs()
    assert len(jobs)==2 and {job['page_id'] for job in jobs}=={'777'}
    assert len({job['scheduled_local'][:10] for job in jobs})==2
    with f.manager.store.connect() as db:assert db.execute('SELECT cursor FROM video_source_settings').fetchone()[0]==4
    mark_page_information_updated(f.manager.store,'555',f.cid)
    assert f.source.status()['next_page_id']=='555'


def test_all_unedited_pages_leave_cursor_and_video_unconsumed(setup):
    f=setup;f.source.configure(settings(f))
    with f.manager.store.connect() as db:db.execute('DELETE FROM page_edits')
    write(f,'one.mp4')
    value=ready_scan(f)
    assert value['next_page_id'] is None and 'đổi thông tin' in value['last_error']
    assert not f.manager.store.reel_jobs() and not f.assets.list() and not rows(f)
    with f.manager.store.connect() as db:assert db.execute('SELECT cursor FROM video_source_settings').fetchone()[0]==0


def test_page_becomes_unready_during_import_pauses_same_reservation_until_edited(setup,monkeypatch):
    f=setup;f.source.configure(settings(f));write(f,'one.mp4')
    original=f.source._import
    async def importing(*args,**kwargs):
        asset=await original(*args,**kwargs)
        with f.manager.store.connect() as db:db.execute('DELETE FROM page_edits WHERE page_id=?',('555',))
        return asset
    monkeypatch.setattr(f.source,'_import',importing)
    ready_scan(f)
    item=rows(f)[0]
    assert item['status']=='ready' and item['asset_id'] and item['job_id'] is None
    assert not f.manager.store.reel_jobs()
    mark_page_information_updated(f.manager.store,'555',f.cid)
    immediate(f.source.scan_once())
    jobs=f.manager.store.reel_jobs()
    assert len(jobs)==1 and jobs[0]['request_id']==item['id'] and jobs[0]['page_id']=='555'


def test_initial_disabled_empty_state_and_saving_rules_never_scans_or_creates_jobs(setup):
    f = setup
    write(f, 'clip.mp4')
    value = f.source.status()
    assert not value['configured'] and not value['settings']['enabled'] and value['settings']['page_ids'] == []
    assert value['items'] == [] and value['next_page_id'] is None
    assert set(value['stats']) == {'files_seen', 'waiting', 'imported', 'queued', 'skipped', 'errors'}
    assert immediate(f.source.scan_once())['stats']['files_seen'] == 0
    assert not f.manager.store.reel_jobs() and not f.assets.list()
    saved = f.source.configure(settings(f))
    assert saved['configured'] and saved['next_page_id'] == '555'
    assert not f.manager.store.reel_jobs() and not f.assets.list()
    assert TOKEN not in json.dumps(saved)


@pytest.mark.parametrize('extra', [
    {'enabled': 1}, {'enabled': 'true'}, {'page_ids': []}, {'page_ids': ['555', '555']},
    {'page_ids': ['１２３']}, {'page_ids': ['../555']}, {'connection_id': True}, {'connection_id': 0},
    {'daily_time': '6:00'}, {'daily_time': '24:00'}, {'timezone': 'NotAReal/Zone'},
    {'caption_template': 'x' * 2201}, {'caption_template': '\x00private'},
    {'directory': r'C:\private'}, {'page_ids': [555]},
])
def test_rules_are_strict_and_directory_cannot_come_from_user_http_input(setup, extra):
    values = {'enabled': True, 'page_ids': ['555'], 'connection_id': setup.cid, **extra}
    with pytest.raises(ValueError):
        VideoSourceSettings.model_validate(values)


def test_config_uses_active_exact_bindings_and_compare_revision_but_pause_stays_available(setup):
    f = setup
    f.source.configure(settings(f))
    revision = f.source.status()['settings']['revision']
    with pytest.raises(AppError):
        f.source.configure(VideoSourceSettings(enabled=True, page_ids=['999'], connection_id=f.cid, revision=revision))
    f.source.configure(settings(f, revision=revision))
    with pytest.raises(AppError) as issue:
        f.source.configure(settings(f, revision=revision))
    assert issue.value.code == 'source_revision_changed'
    f.manager.disconnect(f.cid)
    assert not f.source.configure(settings(f).model_copy(update={'enabled': False}))['settings']['enabled']
    with pytest.raises(AppError):
        f.source.configure(settings(f))


def test_two_observations_are_required_even_with_old_mtime_and_changed_files_restart_wait(setup):
    f = setup
    path = write(f, 'a.mp4')
    import os
    os.utime(path, (1, 1))
    f.source.configure(settings(f))
    assert immediate(f.source.scan_once())['stats']['waiting'] == 1
    assert not f.assets.list()
    f.clock[0] += timedelta(seconds=31)
    path.write_bytes(video(b'changed-copy'))
    assert immediate(f.source.scan_once())['stats']['waiting'] == 1
    assert not f.manager.store.reel_jobs()
    f.clock[0] += timedelta(seconds=31)
    assert immediate(f.source.scan_once())['stats']['queued'] == 1


def test_round_robin_persisted_daily_slots_limits_and_originals_are_preserved(setup):
    f = setup
    originals = [write(f, name, name.encode()) for name in ('a.mp4', 'b.mov', 'c.m4v', 'nested/d.mp4')]
    original_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in originals]
    f.source.configure(settings(f))
    result = ready_scan(f)
    assert result['stats']['queued'] == 2 and len(f.assets.list()) == 2
    jobs = f.manager.store.reel_jobs()
    assert {job['page_id'] for job in jobs} == {'555', '777'}
    assert {job['scheduled_local'] for job in jobs} == {'2030-11-01T06:00:00'}
    immediate(f.source.scan_once())
    jobs = sorted(f.manager.store.reel_jobs(), key=lambda item: item['id'])
    assert [job['page_id'] for job in jobs] == ['555', '777', '555', '777']
    assert [job['scheduled_local'] for job in jobs] == ['2030-11-01T06:00:00'] * 2 + ['2030-11-02T06:00:00'] * 2
    assert all(datetime.fromisoformat(job['scheduled_at']) > f.clock[0] for job in jobs)
    assert f.source.status()['next_page_id'] == '555'
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in originals] == original_hashes
    assert all(job['status'] == 'scheduled' and not job['checkpoint'] for job in jobs)
    assert not list(f.assets.files.root.glob('*.upload'))


def test_hash_dedup_survives_rename_subfolder_and_restart(setup):
    f = setup
    path = write(f, 'a.mp4')
    f.source.configure(settings(f))
    ready_scan(f)
    first = f.manager.store.reel_jobs()[0]
    path.rename(f.folder / 'renamed.mov')
    write(f, 'sub/duplicate.m4v')
    f.source = FolderVideoSource(f.manager, f.assets, directory=f.folder, clock=lambda: f.clock[0])
    ready_scan(f)
    assert len(f.manager.store.reel_jobs()) == len(f.assets.list()) == len(rows(f)) == 1
    assert f.manager.store.reel_jobs()[0]['request_id'] == first['request_id']
    assert f.source.status()['next_page_id'] == '777'


def test_preexisting_valid_asset_is_reused_and_any_historical_job_hash_is_skipped(setup):
    f = setup
    raw = video()
    public = immediate(f.assets.ingest(stream(raw), 'existing.mp4', content_length=len(raw)))
    write(f, 'a.mp4')
    f.source.configure(settings(f))
    ready_scan(f)
    assert len(f.assets.list()) == 1 and f.manager.store.reel_jobs()[0]['asset_id'] == public['id']
    # A fresh source journal still consults historical queue assets, including
    # cancelled jobs. It cannot turn a new local UUID into duplicate work.
    job = f.manager.store.reel_jobs()[0]
    f.manager.store.reel_update(job['id'], status='cancelled')
    with f.manager.store.connect() as db:
        db.execute('DELETE FROM video_source_items')
        db.execute('UPDATE video_source_settings SET cursor=0')
    immediate(f.source.scan_once())
    assert len(f.manager.store.reel_jobs()) == 1
    assert rows(f)[0]['status'] == 'skipped' and f.source.status()['next_page_id'] == '555'


@pytest.mark.parametrize('known_status', ['completed', 'cancelled', 'needs_review'])
def test_known_queue_outcomes_are_never_replanned_or_submitted_again(setup, known_status):
    f = setup
    write(f, 'a.mp4')
    f.source.configure(settings(f))
    ready_scan(f)
    job = f.manager.store.reel_jobs()[0]
    f.manager.store.reel_update(job['id'], status=known_status)
    original_uuid = rows(f)[0]['id']
    f.clock[0] += timedelta(days=10)
    f.source = FolderVideoSource(f.manager, f.assets, directory=f.folder, clock=lambda: f.clock[0])
    immediate(f.source.scan_once())
    assert len(f.manager.store.reel_jobs()) == 1 and rows(f)[0]['id'] == original_uuid
    assert f.manager.store.reel_job(job['id'])['status'] == known_status


def test_sidecar_override_literal_template_and_invalid_text_are_safe(setup):
    f = setup
    write(f, 'a.mp4', b'a')
    write(f, 'b.mp4', b'b')
    (f.folder / 'a.txt').write_text('Sidecar quote\nhttps://blueox179.com', encoding='utf-8')
    f.source.configure(settings(f, caption_template='Hi {page_name} {not_code}'))
    ready_scan(f)
    jobs = sorted(f.manager.store.reel_jobs(), key=lambda item: item['id'])
    assert jobs[0]['caption'] == 'Sidecar quote\nhttps://blueox179.com'
    assert jobs[1]['caption'] == 'Hi Fixture Page 777 {not_code}'
    write(f, 'c.mp4', b'c')
    (f.folder / 'c.txt').write_bytes(b'\xffprivate')
    ready_scan(f)
    assert len(f.manager.store.reel_jobs()) == 2 and f.source.status()['last_error']
    assert TOKEN not in json.dumps(f.source.status())


@pytest.mark.parametrize('bad', ['x' * 2201, 'bad\x00control', 'bad\u202ereversed'])
def test_oversize_or_hidden_control_sidecar_does_not_queue(setup, bad):
    f = setup
    write(f, 'a.mp4')
    (f.folder / 'a.txt').write_text(bad, encoding='utf-8')
    f.source.configure(settings(f))
    ready_scan(f)
    assert not f.manager.store.reel_jobs() and not f.assets.list()


def test_pause_during_copy_keeps_same_uuid_resumable_and_cleans_partial_asset(setup, monkeypatch):
    f = setup
    write(f, 'a.mp4')
    f.source.configure(settings(f))
    immediate(f.source.scan_once())
    f.clock[0] += timedelta(seconds=31)
    stopped = []

    async def pause_thread(fn, *args, **kwargs):
        result = fn(*args, **kwargs)
        if getattr(fn, '__name__', '') == 'read' and not stopped:
            stopped.append(True)
            f.source.configure(settings(f).model_copy(update={'enabled': False}))
        return result

    monkeypatch.setattr(module.asyncio, 'to_thread', pause_thread)
    immediate(f.source.scan_once())
    old = rows(f)[0]
    assert old['status'] == 'ready' and not f.manager.store.reel_jobs() and not f.assets.list()
    assert not list(f.assets.files.root.iterdir())
    f.source.configure(settings(f))
    immediate(f.source.scan_once())
    assert len(f.manager.store.reel_jobs()) == 1
    assert f.manager.store.reel_jobs()[0]['request_id'] == old['id']


class Crash(BaseException):
    pass


@pytest.mark.parametrize('after_create', [False, True])
def test_crash_around_enqueue_reconciles_uuid_without_duplicate_or_overdue_new_job(setup, monkeypatch, after_create):
    f = setup
    write(f, 'a.mp4')
    f.source.configure(settings(f))
    immediate(f.source.scan_once())
    f.clock[0] += timedelta(seconds=31)
    original = f.manager.store.reel_create

    def crash(values):
        if after_create:
            original(values)
        raise Crash()

    monkeypatch.setattr(f.manager.store, 'reel_create', crash)
    with pytest.raises(Crash):
        immediate(f.source.scan_once())
    old = rows(f)[0]
    assert old['asset_id'] and old['status'] == 'enqueueing'
    monkeypatch.setattr(f.manager.store, 'reel_create', original)
    (f.folder / 'a.mp4').unlink()
    f.clock[0] += timedelta(days=2)
    f.source = FolderVideoSource(f.manager, f.assets, directory=f.folder, clock=lambda: f.clock[0])
    immediate(f.source.scan_once())
    assert len(f.manager.store.reel_jobs()) == 1
    job = f.manager.store.reel_jobs()[0]
    assert job['request_id'] == old['id']
    if not after_create:
        assert datetime.fromisoformat(job['scheduled_at']) > f.clock[0]
    else:
        assert job['scheduled_at'] == json.loads(old['payload'])['scheduled_at']


def test_copy_crossing_daily_time_gets_next_future_slot_with_same_item_uuid(setup, monkeypatch):
    f = setup
    f.clock[0] = datetime(2030, 11, 1, 12, 58, 50, tzinfo=timezone.utc)
    write(f, 'a.mp4')
    f.source.configure(settings(f))
    immediate(f.source.scan_once())
    f.clock[0] += timedelta(seconds=31)  # reserve at 05:59:21 local
    original = f.source._import

    async def slow_import(*args):
        result = await original(*args)
        assert json.loads(rows(f)[0]['payload'])['scheduled_local'] == '2030-11-01T06:00:00'
        f.clock[0] += timedelta(minutes=2)
        return result

    monkeypatch.setattr(f.source, '_import', slow_import)
    immediate(f.source.scan_once())
    job = f.manager.store.reel_jobs()[0]
    assert job['scheduled_local'] == '2030-11-02T06:00:00'
    assert datetime.fromisoformat(job['scheduled_at']) > f.clock[0]
    assert job['request_id'] == rows(f)[0]['id']


def test_existing_manual_queue_day_is_reserved_and_dst_keeps_six_am(setup):
    f = setup
    raw = video(b'manual')
    asset = immediate(f.assets.ingest(stream(raw), 'manual.mp4', content_length=len(raw)))
    f.manager.store.reel_create({'request_id': str(uuid4()), 'page_id': '555', 'page_name': 'Fixture Page 555',
        'connection_id': f.cid, 'connection_label': 'Offline source', 'token_revision': 1,
        'asset_id': asset['id'], 'asset_name': asset['name'], 'caption': '', 'scheduled_at': '2030-11-01T13:00:00+00:00',
        'scheduled_local': '2030-11-01T06:00:00', 'timezone': 'America/Los_Angeles', 'fold': 0})
    write(f, 'a.mp4')
    f.source.configure(settings(f))
    ready_scan(f)
    new = max(f.manager.store.reel_jobs(), key=lambda item: item['id'])
    assert new['scheduled_local'] == '2030-11-02T06:00:00'
    f.clock[0] = datetime(2030, 11, 3, 12, 0, tzinfo=timezone.utc)
    write(f, 'b.mp4', b'b')
    ready_scan(f)
    latest = max(f.manager.store.reel_jobs(), key=lambda item: item['id'])
    assert latest['scheduled_local'] == '2030-11-03T06:00:00'
    assert latest['scheduled_at'] == '2030-11-03T14:00:00+00:00'


def test_rotation_before_enqueue_blocks_source_item_without_touching_other_queue(setup, monkeypatch):
    f = setup
    write(f, 'a.mp4')
    f.source.configure(settings(f))
    original = f.source._import

    async def rotated(*args):
        result = await original(*args)
        f.manager.save_connection(ConnectionUpdate(label='Rotated', token_type='system_user', token='FixtureReplacementToken82', revision=f.manager.store.connection(f.cid)["revision"]), f.cid)
        return result

    monkeypatch.setattr(f.source, '_import', rotated)
    ready_scan(f)
    assert not f.manager.store.reel_jobs() and rows(f)[0]['status'] == 'needs_review'


@pytest.mark.parametrize('blocked_kind', ['file', 'parent', 'caption'])
def test_links_are_never_followed_in_video_or_caption_paths(setup, monkeypatch, blocked_kind):
    f = setup
    path = write(f, 'nested/a.mp4')
    caption = path.with_suffix('.txt')
    caption.write_text('Never read', encoding='utf-8')
    original = module._linked
    target = path if blocked_kind == 'file' else path.parent if blocked_kind == 'parent' else caption
    monkeypatch.setattr(module, '_linked', lambda value: Path(value) == target or original(value))
    f.source.configure(settings(f))
    ready_scan(f)
    assert not f.manager.store.reel_jobs() and not f.assets.list()


def test_file_mutation_during_copy_cleans_temp_and_cannot_enqueue(setup, monkeypatch):
    f = setup
    path = write(f, 'a.mp4')
    f.source.configure(settings(f))
    changed = []

    async def changing(fn, *args, **kwargs):
        result = fn(*args, **kwargs)
        if getattr(fn, '__name__', '') == 'read' and not changed:
            changed.append(True)
            path.write_bytes(video(b'different-and-longer'))
        return result

    monkeypatch.setattr(module.asyncio, 'to_thread', changing)
    ready_scan(f)
    assert not f.manager.store.reel_jobs() and not f.assets.list()
    assert not list(f.assets.files.root.iterdir())


def test_metadata_touch_during_copy_remains_resumable_with_same_hash_and_uuid(setup, monkeypatch):
    f = setup
    path = write(f, 'a.mp4')
    f.source.configure(settings(f))
    touched = []

    async def touch(fn, *args, **kwargs):
        result = fn(*args, **kwargs)
        if getattr(fn, '__name__', '') == 'read' and not touched:
            import os
            touched.append(True)
            value = path.stat()
            os.utime(path, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000_000))
        return result

    monkeypatch.setattr(module.asyncio, 'to_thread', touch)
    ready_scan(f)
    item = rows(f)[0]
    assert item['status'] == 'ready' and not f.manager.store.reel_jobs() and not f.assets.list()
    immediate(f.source.scan_once())  # new metadata observation starts the clock
    f.clock[0] += timedelta(seconds=31)
    immediate(f.source.scan_once())
    assert len(f.manager.store.reel_jobs()) == 1 and len(rows(f)) == 1
    assert f.manager.store.reel_jobs()[0]['request_id'] == item['id']


@pytest.mark.parametrize('problem', ['empty', 'oversize'])
def test_excluded_video_files_have_fixed_warning_without_private_path(setup, monkeypatch, problem):
    f = setup
    path = write(f, 'a.mp4')
    if problem == 'empty':
        path.write_bytes(b'')
    else:
        checked = module._checked
        def oversize(value, root, **kwargs):
            if Path(value) == path:
                raise module._FileChanged()
            return checked(value, root, **kwargs)
        monkeypatch.setattr(module, '_checked', oversize)
    f.source.configure(settings(f))
    result = immediate(f.source.scan_once())
    assert result['last_error'] and 'bỏ qua' in result['last_error']
    assert str(path) not in result['last_error'] and not f.manager.store.reel_jobs()


def test_unexpected_error_is_safe_and_does_not_leak_details(setup, monkeypatch):
    f = setup
    f.source.configure(settings(f))
    monkeypatch.setattr(module, '_walk', lambda _: (_ for _ in ()).throw(RuntimeError(TOKEN + r'C:\private\token')))
    value = immediate(f.source.scan_once())
    assert value['last_error'] and TOKEN not in json.dumps(value) and r'C:\private' not in json.dumps(value)
    assert not f.manager.store.reel_jobs()


def test_tick_throttles_own_background_task_and_shutdown_never_touches_queue(setup, monkeypatch):
    f = setup
    tasks = []
    class Task:
        def __init__(self, coroutine):
            self.coroutine = coroutine
            self.finished = False
            self.was_cancelled = False
        def done(self): return self.finished
        def cancel(self): self.was_cancelled = True
        def cancelled(self): return self.was_cancelled
        def add_done_callback(self, callback): pass
        def __await__(self):
            self.coroutine.close()
            self.finished = True
            if False: yield
    monkeypatch.setattr(module.asyncio, 'create_task', lambda coroutine: tasks.append(Task(coroutine)) or tasks[-1])
    f.source.tick()
    assert not tasks
    f.source.configure(settings(f))
    f.source.tick(); f.source.tick(); f.source.request_scan()
    assert len(tasks) == 1
    immediate(f.source.shutdown())
    assert tasks[0].was_cancelled and not f.manager.store.reel_jobs()
    f.source.tick()
    assert len(tasks) == 1
