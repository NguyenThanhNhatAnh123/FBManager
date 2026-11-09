"""Synthetic published-job snapshots only; no live Graph, upload or schedules."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager.graph import GraphError
from page_manager.idea_pipeline import IdeaPipeline
from page_manager.idea_views import IdeaViews, MAX_TARGETS
from page_manager.service import PageManager


PAGE = '111111'
TOKEN = 'PRIVATE-PAGE-TOKEN'


class Box:
    def decrypt(self, value):
        return value


class Graph:
    def __init__(self, fixture):
        self.f = fixture

    async def __aenter__(self):
        self.f.contexts += 1
        return self

    async def __aexit__(self, *_):
        self.f.closed += 1

    async def identity(self, token):
        assert token == TOKEN
        return {'id': self.f.identity_id}

    async def get(self, path, token, params):
        assert token == TOKEN and params == {'fields': 'id,from,views'}
        self.f.calls.append(path)
        if self.f.on_get:
            return await self.f.on_get(path)
        if self.f.error:
            raise self.f.error
        return self.f.responses.get(path, {'id': path, 'from': {'id': PAGE}, 'views': 123})


@pytest.fixture
def f(tmp_path):
    f = SimpleNamespace(clock=datetime(2030, 11, 2, 12, tzinfo=timezone.utc),
                        responses={}, calls=[], contexts=0, closed=0,
                        identity_id=PAGE, error=None, on_get=None)
    f.manager = PageManager(tmp_path / 'manager', secret_box=Box(), graph_factory=lambda: Graph(f))
    f.store = f.manager.store
    f.cid = f.store.save_connection({'label': 'fixture', 'token_type': 'page'}, TOKEN)
    f.store.sync(f.cid, 1, [{'id': PAGE, 'name': 'fixture Page', 'sources': ['fixture']}],
                 True, 'working', None, {'id': PAGE, 'name': 'fixture Page'})
    from conftest import mark_page_information_updated
    mark_page_information_updated(f.store,PAGE,f.cid)
    f.pipeline = IdeaPipeline(f.manager, directory=tmp_path / 'ideas',
                              output_directory=tmp_path / 'out', clock=lambda: f.clock)
    f.collector = IdeaViews(f.manager, f.pipeline, clock=lambda: f.clock)
    return f


def job(f, *, status='completed', variant_status='metadata_registered',
        sha_match=True, reservation_match=True, finished=True, index=0):
    idea_id, variant_id, reservation, asset_id = [str(uuid4()) for _ in range(4)]
    sha = hashlib.sha256(f'video-{variant_id}'.encode()).hexdigest()
    artwork = hashlib.sha256(f'idea-{idea_id}'.encode()).hexdigest()
    stamp = f.clock - timedelta(hours=24)
    with f.store.connect() as db:
        db.execute('INSERT INTO idea_pipeline_ideas(id,artwork_sha256,filename,created_at) VALUES(?,?,?,?)',
                   (idea_id, artwork, 'artwork.png', stamp.isoformat()))
        db.execute('INSERT INTO idea_pipeline_variants(id,idea_id,ordinal,status,video_sha256,reservation_id) VALUES(?,?,?,?,?,?)',
                   (variant_id, idea_id, 1, variant_status, sha, reservation))
    f.store.reel_asset_save({'id': asset_id, 'name': 'video.mp4', 'path': 'private.mp4',
        'size': 24, 'sha256': sha if sha_match else 'a' * 64, 'mime': 'video/mp4'})
    row, _ = f.store.reel_create({'request_id': reservation if reservation_match else str(uuid4()),
        'page_id': PAGE, 'page_name': 'fixture Page', 'connection_id': f.cid,
        'connection_label': 'fixture', 'token_revision': 1, 'asset_id': asset_id,
        'asset_name': 'video.mp4', 'caption': 'PRIVATE CAPTION', 'scheduled_at': stamp.isoformat(),
        'scheduled_local': '2030-11-01T04:00', 'timezone': 'America/Los_Angeles', 'fold': 0})
    video_id = str(900000 + row['id'])
    f.store.reel_update(row['id'], status=status, video_id=video_id,
        finished_at=stamp.isoformat() if finished else None)
    return SimpleNamespace(idea_id=idea_id, variant_id=variant_id,
                           reservation=reservation, job_id=row['id'], video_id=video_id)


def snapshots(f):
    with f.store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM idea_pipeline_views')]


def test_no_targets_never_opens_graph(f):
    result = asyncio.run(f.collector.refresh())
    assert result['targets'] == result['refreshed'] == result['unavailable'] == 0
    assert not f.calls and f.contexts == 0 and not snapshots(f)


def test_exact_views_saved_but_completion_proxy_excluded_from_ranking(f):
    target = job(f)
    result = asyncio.run(f.collector.refresh())
    assert result['targets'] == result['refreshed'] == 1 and result['unavailable'] == 0
    assert f.calls == [target.video_id] and f.contexts == f.closed == 1
    row = snapshots(f)[0]
    assert row['value'] == 123 and row['metric'] == 'views' and row['metric_verified'] == 1
    assert row['published_time_provenance'] == 'job_completion'
    assert not f.pipeline.ranking()['cohorts']
    assert TOKEN not in json.dumps(result) and 'PRIVATE CAPTION' not in json.dumps(result)
    assert f.store.reel_job(target.job_id)['status'] == 'completed'


@pytest.mark.parametrize('value', [0, 1, 10**15, '0', '1', '123', str(10**15)])
def test_real_integer_zero_is_valid_only_when_provider_returns_it(f, value):
    target = job(f)
    f.responses[target.video_id] = {'id': target.video_id, 'from': {'id': PAGE}, 'views': value}
    assert asyncio.run(f.collector.refresh())['refreshed'] == 1
    assert snapshots(f)[0]['value'] == int(value)


@pytest.mark.parametrize('value', [None, False, True, -1, 10**15 + 1, 1.0, {}, [],
    '01', '00', '-1', '+1', ' 1', '1 ', '1.0', '1e3', '1K', 'N/A', '１２', str(10**15 + 1)])
def test_invalid_view_value_is_unknown_never_zero(f, value):
    target = job(f)
    f.responses[target.video_id] = {'id': target.video_id, 'from': {'id': PAGE}, 'views': value}
    result = asyncio.run(f.collector.refresh())
    assert result['unavailable'] == 1 and result['refreshed'] == 0
    row = snapshots(f)[0]
    assert row['availability'] == 'unavailable' and row['value'] is None and row['metric_verified'] == 0


@pytest.mark.parametrize('data', [None, {}, {'views': 400},
    {'id': 'wrong', 'from': {'id': PAGE}, 'views': 400},
    {'id': '900001', 'from': {'id': '22222'}, 'views': 400},
    {'id': '900001', 'from': PAGE, 'views': 400},
    {'id': '900001', 'from': {'id': PAGE}, 'reach': 400, 'impressions': 400}])
def test_exact_video_owner_and_metric_required(f, data):
    target = job(f)
    f.responses[target.video_id] = data
    assert asyncio.run(f.collector.refresh())['unavailable'] == 1
    assert snapshots(f)[0]['value'] is None


@pytest.mark.parametrize('options', [
    {'status': 'scheduled'}, {'status': 'needs_review'}, {'status': 'cancelled'},
    {'variant_status': 'planned'}, {'variant_status': 'needs_review'},
    {'sha_match': False}, {'reservation_match': False},
])
def test_only_exact_completed_pipeline_job_is_a_target(f, options):
    job(f, **options)
    assert asyncio.run(f.collector.refresh())['targets'] == 0
    assert not f.calls and not snapshots(f)


def test_unknown_publication_time_does_not_call_graph(f):
    job(f, finished=False)
    result = asyncio.run(f.collector.refresh())
    assert result['targets'] == result['unavailable'] == 1
    assert not f.calls and not snapshots(f)


def test_wrong_page_token_never_reads_video_metric(f):
    job(f)
    f.identity_id = '222222'
    result = asyncio.run(f.collector.refresh())
    assert result['unavailable'] == 1 and not f.calls
    assert snapshots(f)[0]['value'] is None


@pytest.mark.parametrize('error', [GraphError('permission_limited'), GraphError('rate_limited'),
                                  RuntimeError('PRIVATE ERROR ' + TOKEN)])
def test_provider_errors_are_sanitized_and_missing(f, error):
    job(f)
    f.error = error
    result = asyncio.run(f.collector.refresh())
    assert result['unavailable'] == 1
    assert snapshots(f)[0]['value'] is None
    assert TOKEN not in json.dumps(result) and 'PRIVATE ERROR' not in json.dumps(result)


def test_changed_binding_during_get_is_not_recorded(f):
    target = job(f)
    async def changed(path):
        with f.store.connect() as db:
            db.execute("UPDATE idea_pipeline_variants SET status='needs_review' WHERE id=?", (target.variant_id,))
        return {'id': path, 'from': {'id': PAGE}, 'views': 123}
    f.on_get = changed
    assert asyncio.run(f.collector.refresh())['unavailable'] == 1
    assert not snapshots(f)


def test_source_disconnect_during_get_does_not_record_verified_metric(f):
    target = job(f)
    async def changed(path):
        f.store.disconnect(f.cid)
        return {'id': path, 'from': {'id': PAGE}, 'views': 123}
    f.on_get = changed
    assert asyncio.run(f.collector.refresh())['unavailable'] == 1
    assert snapshots(f)[0]['value'] is None


def test_bound_limit_is_one_hundred(f):
    for index in range(MAX_TARGETS + 3):
        job(f, index=index)
    result = asyncio.run(f.collector.refresh())
    assert result['targets'] == result['refreshed'] == MAX_TARGETS
    assert len(f.calls) == MAX_TARGETS and len(snapshots(f)) == MAX_TARGETS


def test_total_deadline_cancels_only_read_work(f, monkeypatch):
    import page_manager.idea_views as module
    monkeypatch.setattr(module, 'MAX_REFRESH_SECONDS', 0.02)
    target = job(f)
    async def blocked(path):
        await asyncio.sleep(5)
    f.on_get = blocked
    result = asyncio.run(f.collector.refresh())
    assert result['targets'] == result['unavailable'] == 1
    assert f.contexts == f.closed == 1 and not snapshots(f)
    assert f.store.reel_job(target.job_id)['status'] == 'completed'


def test_concurrent_refresh_does_not_launch_second_read(f):
    job(f)
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def blocked(path):
            started.set()
            await release.wait()
            return {'id': path, 'from': {'id': PAGE}, 'views': 123}
        f.on_get = blocked
        first = asyncio.create_task(f.collector.refresh())
        await started.wait()
        second = await f.collector.refresh()
        release.set()
        result = await first
        return second, result
    second, first = asyncio.run(scenario())
    assert second['targets'] == 0 and first['refreshed'] == 1
    assert len(f.calls) == 1 and any('lượt trùng' in item for item in second['limitations'])


def test_latest_returns_only_newest_safe_counts_without_guessed_publication_time(f):
    from datetime import timedelta
    target = job(f)
    asyncio.run(f.collector.refresh())
    f.clock += timedelta(hours=1)
    f.responses[target.video_id] = {'id': target.video_id, 'from': {'id': PAGE}, 'views': '201'}
    asyncio.run(f.collector.refresh())
    value = f.collector.latest()
    assert value['total'] == value['known'] == 1 and value['unavailable'] == 0
    row = value['items'][0]
    assert row['value'] == 201 and row['metric_verified'] is True
    assert row['published_time_provenance'] == 'job_completion'
    assert 'published_at' not in row and 'PRIVATE' not in json.dumps(value)
    assert set(row) == {'idea_id', 'variant_id', 'page_id', 'video_id', 'metric', 'value',
        'metric_verified', 'availability', 'observed_at', 'published_time_provenance'}
    assert value['automatic_interval_seconds'] == 3600
    assert not f.pipeline.ranking()['cohorts']


def test_latest_missing_count_does_not_keep_an_old_number_as_current(f):
    target = job(f)
    asyncio.run(f.collector.refresh())
    f.clock += timedelta(hours=1)
    f.responses[target.video_id] = {'id': target.video_id, 'from': {'id': PAGE}}
    asyncio.run(f.collector.refresh())
    value = f.collector.latest()
    assert value['known'] == 0 and value['unavailable'] == 1
    assert value['items'][0]['value'] is None


def test_hourly_tick_has_no_meta_request_with_no_completed_targets(f):
    async def scenario():
        f.collector.tick()
        first = f.collector.task
        await first
        f.collector.tick()
        assert f.collector.task is first
        await f.collector.shutdown()
        f.collector.next_tick = 0
        f.collector.tick()
        assert f.collector.task is first
    asyncio.run(scenario())
    assert f.contexts == 0 and not f.calls and not snapshots(f)
    assert f.collector.latest()['last_refresh_at'] == f.clock.isoformat()


def test_hourly_tick_reads_only_bound_targets_and_stops_cleanly(f):
    target = job(f)
    async def scenario():
        f.collector.tick()
        await f.collector.task
        first = f.collector.task
        f.collector.tick()
        assert f.collector.task is first
        assert f.calls == [target.video_id]
        f.clock += timedelta(hours=1)
        f.collector.next_tick = 0
        f.collector.tick()
        await f.collector.task
        assert f.calls == [target.video_id, target.video_id]
        await f.collector.shutdown()
    asyncio.run(scenario())
    assert len(snapshots(f)) == 2


def test_shutdown_cancels_owned_background_read_and_closes_graph(f):
    job(f)
    async def scenario():
        started = asyncio.Event()
        async def blocked(path):
            started.set()
            await asyncio.sleep(30)
        f.on_get = blocked
        f.collector.tick()
        await started.wait()
        await f.collector.shutdown()
        assert f.collector.task.done() and f.collector.closing
    asyncio.run(scenario())
    assert f.contexts == f.closed == 1 and not snapshots(f)


def test_tick_without_event_loop_does_not_start_background_worker(f):
    f.collector.tick()
    assert f.collector.task is None and f.collector.next_tick == 0
