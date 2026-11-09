"""Synthetic planner checks: no live DB, filesystem production, worker or network IO."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from page_manager.auto_batch_planner import AutoBatchPlanner, allocate_counts
from page_manager.errors import AppError
from page_manager.idea_pipeline import IdeaPipeline
from page_manager.store import Store


def identities(count):
    return [str(uuid5(NAMESPACE_URL, 'synthetic-auto-idea-' + str(index))) for index in range(count)]


@pytest.mark.parametrize('count,expected', [(1, [14]), (3, [5, 5, 4]), (7, [2] * 7), (14, [1] * 14), (20, [1] * 14)])
def test_pure_allocator_conserves_page_quota(count, expected):
    ids = identities(count)
    plan = allocate_counts(ids, 14)
    assert [row['variant_count'] for row in plan['allocations']] == expected
    assert [row['idea_id'] for row in plan['allocations']] == ids[:14]
    assert [row['position'] for row in plan['allocations']] == list(range(1, min(14, count) + 1))
    assert plan['total_videos'] == sum(expected) == 14
    assert plan['deferred_ids'] == ids[14:]


def test_pure_allocator_supports_smaller_future_pools_and_rotation():
    ids = identities(8)
    plan = allocate_counts(ids, 3, 6)
    assert [row['idea_id'] for row in plan['allocations']] == [ids[6], ids[7], ids[0]]
    assert plan['next_index'] == 1 and plan['total_videos'] == 3
    assert allocate_counts([], 14) == {'allocations': [], 'deferred_ids': [], 'next_index': 0, 'total_videos': 0}


@pytest.mark.parametrize('ids,pages,start', [(['bad'],14,0), (identities(1)*2,14,0),
    (identities(1),0,0), (identities(1),15,0), (identities(1),True,0),
    (identities(1),14,True), (identities(1),14,-1), (None,14,0)])
def test_allocator_rejects_invalid_identity_or_capacity(ids, pages, start):
    with pytest.raises(ValueError):
        allocate_counts(ids, pages, start)


@pytest.fixture
def fixture(tmp_path):
    store = Store(tmp_path / 'manager')
    clock = [datetime(2030, 11, 1, 16, tzinfo=timezone.utc)]
    pipeline = IdeaPipeline(SimpleNamespace(store=store), directory=tmp_path / 'artwork',
                            output_directory=tmp_path / 'output', clock=lambda: clock[0])
    pages = [str(100000 + index) for index in range(14)]
    for pid in pages:
        store.record_chrome_setup(pid, str(uuid4()), {'bio': 'a' * 64})
    source = {'enabled': True, 'page_ids': pages, 'connection_id': 1,
              'daily_time': '06:00', 'timezone': 'America/Los_Angeles',
              'caption_template': 'PRIVATE caption must not enter snapshot'}
    with store.connect() as db:
        db.executescript('''CREATE TABLE video_source_settings(
            id INTEGER PRIMARY KEY,revision INTEGER NOT NULL,configured INTEGER NOT NULL,settings TEXT NOT NULL);
            CREATE TABLE idea_manual_runs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,idea_id TEXT,
                artwork_sha256 TEXT,status TEXT);''')
        db.execute('INSERT INTO video_source_settings VALUES(1,5,1,?)', (json.dumps(source),))
    planner = AutoBatchPlanner(store, clock=lambda: clock[0])
    return SimpleNamespace(store=store, clock=clock, pipeline=pipeline, planner=planner, source=source)


def add(f, count=7, *, start=0):
    result = identities(start + count)[start:]
    with f.store.connect() as db:
        for index, idea in enumerate(result, start=start):
            stamp = (f.clock[0] - timedelta(days=1) + timedelta(seconds=index)).isoformat()
            sha = hashlib.sha256(idea.encode()).hexdigest()
            db.execute('INSERT INTO idea_pipeline_ideas(id,artwork_sha256,filename,created_at) VALUES(?,?,?,?)',
                       (idea, sha, 'PRIVATE-artwork-' + idea + '.png', stamp))
            for ordinal in range(1, 8):
                db.execute('''INSERT INTO idea_pipeline_variants(id,idea_id,ordinal,reservation_id)
                    VALUES(?,?,?,?)''', (str(uuid4()), idea, ordinal, str(uuid4())))
    return result


def enable(f):
    return f.planner.configure(True, f.planner.settings()['revision'])


def claim(f):
    preview = f.planner.preview()
    return f.planner.claim_day(preview['revision'], preview['plan_hash'])


def run(f, item, *, status='queued', **overrides):
    values = {'id': str(uuid4()), 'request_id': item['request_id'], 'idea_id': item['idea_id'],
              'artwork_sha256': item['artwork_sha256'], 'status': status} | overrides
    with f.store.connect() as db:
        db.execute('INSERT INTO idea_manual_runs VALUES(:id,:request_id,:idea_id,:artwork_sha256,:status)', values)
    return values['id']


def finish(f, batch):
    for item in batch['items']:
        rid = run(f, item, status='completed')
        f.planner.mark_item(batch['id'], item['idea_id'], 'completed', rid)


def test_defaults_off_preview_reads_only_and_keeps_canonical_seven(fixture):
    f = fixture; ids = add(f)
    with f.store.connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM idea_pipeline_variants ORDER BY idea_id,ordinal')]
    settings = f.planner.settings()
    assert settings['enabled'] is False and settings['revision'] == 1
    assert settings['max_batches_per_day'] == 1 and settings['timezone'] == 'America/Los_Angeles'
    preview = f.planner.preview()
    assert preview['candidate_count'] == 7 and preview['capacity'] == 14
    assert preview['total_videos'] == 14 and preview['deferred_count'] == 0
    assert preview['blocker_codes'] == ['auto_disabled'] and not preview['ready']
    assert [row['idea_id'] for row in preview['allocations']] == ids
    assert all(row['variant_count'] == 2 for row in preview['allocations'])
    with pytest.raises(AppError) as caught:
        f.planner.claim_day(1, preview['plan_hash'])
    assert caught.value.code == 'auto_disabled'
    with f.store.connect() as db:
        assert before == [tuple(row) for row in db.execute('SELECT * FROM idea_pipeline_variants ORDER BY idea_id,ordinal')]
        assert db.execute('SELECT COUNT(*) FROM idea_auto_batches').fetchone()[0] == 0
    assert not f.pipeline.output_directory.exists()
    assert f.planner.current_batch() is None and f.planner.find_incomplete_batch() is None


def test_claim_is_immutable_idempotent_and_metadata_only(fixture):
    f = fixture; add(f); enable(f)
    preview = f.planner.preview()
    batch = claim(f)
    assert batch['replayed'] is False and batch['status'] == 'pending'
    assert f.planner.claim_day(preview['revision'], preview['plan_hash']) == batch | {'replayed': True}
    assert len(batch['items']) == 7 and len({row['request_id'] for row in batch['items']}) == 7
    assert all(row['variant_count'] == 2 and row['status'] == 'pending' for row in batch['items'])
    assert f.planner.current_batch()['id'] == batch['id']
    assert f.planner.find_incomplete_batch()['id'] == batch['id']
    assert f.planner.list_batches()['items'] == [f.planner.batch(batch['id'])]
    assert f.planner.item(batch['id'], batch['items'][0]['idea_id']) == batch['items'][0]
    f.planner.configure(False, preview['revision'])
    assert f.planner.batch(batch['id'])['snapshot'] == batch['snapshot']
    encoded = json.dumps(batch)
    assert all(word not in encoded for word in ('PRIVATE', 'filename', 'caption_template', 'variant_id', 'reservation_id'))
    with f.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM idea_pipeline_variants').fetchone()[0] == 49
        assert not db.execute('SELECT 1 FROM idea_pipeline_jobs').fetchall()


def test_two_controllers_claim_one_frozen_daily_batch(fixture):
    f = fixture; add(f, 20); enable(f)
    preview = f.planner.preview()
    other = AutoBatchPlanner(f.store, clock=lambda: f.clock[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(planner.claim_day, preview['revision'], preview['plan_hash']) for planner in (f.planner, other)]
        batches = [job.result() for job in jobs]
    assert batches[0]['id'] == batches[1]['id']
    assert sorted(row['replayed'] for row in batches) == [False, True]
    assert f.planner.list_batches()['total'] == 1
    assert len(batches[0]['items']) == 14 and batches[0]['snapshot']['deferred_count'] == 6


@pytest.mark.parametrize('change', ['source', 'candidate', 'revision'])
def test_stale_preview_or_configuration_cannot_claim(fixture, change):
    f = fixture; add(f); enable(f); preview = f.planner.preview()
    if change == 'source':
        with f.store.connect() as db:
            db.execute('UPDATE video_source_settings SET revision=revision+1')
    elif change == 'candidate':
        add(f, 1, start=7)
    else:
        f.planner.configure(False, preview['revision'])
    with pytest.raises(AppError) as caught:
        f.planner.claim_day(preview['revision'], preview['plan_hash'])
    assert caught.value.code == ('auto_settings_changed' if change == 'revision' else 'auto_preview_changed')
    assert f.planner.list_batches()['total'] == 0


@pytest.mark.parametrize('change', ['thirteen', 'unaccepted', 'wrongtime', 'wrongzone', 'disabled', 'duplicate', 'malformed'])
def test_runtime_requires_fourteen_accepted_pages_and_six_am_la(fixture, change):
    f = fixture; add(f); enable(f); source = dict(f.source)
    if change == 'thirteen': source['page_ids'] = source['page_ids'][:-1]
    elif change == 'unaccepted': source['page_ids'] = source['page_ids'][:-1] + ['900001']
    elif change == 'wrongtime': source['daily_time'] = '07:00'
    elif change == 'wrongzone': source['timezone'] = 'UTC'
    elif change == 'disabled': source['enabled'] = False
    elif change == 'duplicate': source['page_ids'] = [source['page_ids'][0]] * 14
    elif change == 'malformed': source = []
    with f.store.connect() as db:
        db.execute('UPDATE video_source_settings SET settings=?', (json.dumps(source),))
    preview = f.planner.preview()
    assert not preview['ready']
    assert ('page_information_required' if change == 'unaccepted' else 'schedule_unavailable') in preview['blocker_codes']
    with pytest.raises(AppError): claim(f)


def test_excludes_all_manual_history_uncertain_campaigns_registered_video_and_claimed_jobs(fixture):
    f = fixture; ids = add(f, 10); enable(f)
    with f.store.connect() as db:
        for index, status in enumerate(('cancelled', 'completed', 'needs_review', 'queued')):
            db.execute('INSERT INTO idea_manual_runs VALUES(?,?,?,?,?)',
                       (str(uuid4()), str(uuid4()), ids[index], hashlib.sha256(ids[index].encode()).hexdigest(), status))
        db.execute("UPDATE idea_pipeline_ideas SET campaign_state='uncertain' WHERE id=?", (ids[4],))
        db.execute("UPDATE idea_pipeline_ideas SET campaign_state='submitting' WHERE id=?", (ids[5],))
        db.execute('UPDATE idea_pipeline_variants SET video_sha256=? WHERE idea_id=? AND ordinal=1', ('b'*64, ids[6]))
        for index, status in ((7, 'leased'), (8, 'submitting')):
            db.execute('''INSERT INTO idea_pipeline_jobs(id,idea_id,stage,job_key,status,created_at)
                VALUES(?,?,'upload',?,?,?)''', (str(uuid4()), ids[index], str(uuid4()), status, f.clock[0].isoformat()))
    preview = f.planner.preview()
    assert preview['candidate_count'] == 1
    assert [row['idea_id'] for row in preview['allocations']] == [ids[9]]
    assert preview['allocations'][0]['variant_count'] == 14


def test_incomplete_and_review_batch_holds_new_day_without_reusing_ideas(fixture):
    f = fixture; ids = add(f, 20); enable(f); batch = claim(f)
    f.clock[0] += timedelta(days=1)
    preview = f.planner.preview()
    assert preview['candidate_count'] == 6
    assert 'previous_batch_incomplete' in preview['blocker_codes']
    with pytest.raises(AppError) as caught: claim(f)
    assert caught.value.code == 'auto_batch_incomplete'
    first = batch['items'][0]
    f.planner.mark_item(batch['id'], first['idea_id'], 'needs_review', error_code='uncertain')
    assert f.planner.find_incomplete_batch()['status'] == 'needs_review'
    with pytest.raises(AppError): claim(f)
    finish(f, batch)
    assert f.planner.find_incomplete_batch() is None
    second = claim(f)
    assert [row['idea_id'] for row in second['items']] == ids[14:]
    assert [row['variant_count'] for row in second['items']] == [3, 3, 2, 2, 2, 2]


def test_fair_cursor_continues_older_deferred_candidates_when_new_ideas_arrive(fixture):
    f = fixture; ids = add(f, 30); enable(f); first = claim(f); finish(f, first)
    add(f, 10, start=30)
    f.clock[0] += timedelta(days=1)
    second = claim(f)
    assert [row['idea_id'] for row in second['items']] == ids[14:28]
    assert not set(row['idea_id'] for row in first['items']) & set(row['idea_id'] for row in second['items'])


def test_authorization_binds_exact_frozen_item_but_off_on_can_resume(fixture):
    f = fixture; add(f); enable(f); batch = claim(f); item = batch['items'][0]
    context = {key: batch[key] for key in ('settings_revision', 'day_key')} | {'batch_id': batch['id']}
    def authorized(**kwargs):
        return f.planner.authorize(item['idea_id'], item['request_id'], item['variant_count'], context, **kwargs)
    assert authorized()
    assert not f.planner.authorize(item['idea_id'], str(uuid4()), 2, context)
    assert not f.planner.authorize(item['idea_id'], item['request_id'], 1, context)
    assert not f.planner.authorize(item['idea_id'], item['request_id'], 2, context | {'extra': 1})
    f.planner.configure(False, f.planner.settings()['revision'])
    assert not authorized() and authorized(running=True)
    f.planner.configure(True, f.planner.settings()['revision'])
    assert authorized() and f.planner.settings()['revision'] != batch['settings_revision']
    f.planner.mark_item(batch['id'], item['idea_id'], 'needs_review', error_code='uncertain')
    assert not authorized() and not authorized(running=True)


def test_authorization_rejects_changed_artwork_or_source(fixture):
    f = fixture; add(f); enable(f); batch = claim(f); item = batch['items'][0]
    context = {'batch_id': batch['id'], 'settings_revision': batch['settings_revision'], 'day_key': batch['day_key']}
    with f.store.connect() as db:
        db.execute('UPDATE idea_pipeline_ideas SET artwork_sha256=? WHERE id=?', ('c'*64, item['idea_id']))
    assert not f.planner.authorize(item['idea_id'], item['request_id'], 2, context, running=True)
    with f.store.connect() as db:
        db.execute('UPDATE idea_pipeline_ideas SET artwork_sha256=? WHERE id=?', (item['artwork_sha256'], item['idea_id']))
        db.execute('UPDATE video_source_settings SET revision=revision+1')
    assert not f.planner.authorize(item['idea_id'], item['request_id'], 2, context)


@pytest.mark.parametrize('status', ['cancelled', 'queued', 'completed'])
def test_manual_action_after_claim_prevents_automatic_dispatch(fixture, status):
    f = fixture; add(f); enable(f); batch = claim(f); item = batch['items'][0]
    context = {'batch_id': batch['id'], 'settings_revision': batch['settings_revision'], 'day_key': batch['day_key']}
    rid = run(f, item, status=status, request_id=str(uuid4()))
    assert not f.planner.authorize(item['idea_id'], item['request_id'], 2, context)
    with pytest.raises(AppError):
        f.planner.mark_item(batch['id'], item['idea_id'], 'needs_review', rid, 'run_conflict')
    f.planner.mark_item(batch['id'], item['idea_id'], 'needs_review', error_code='run_conflict')
    assert f.planner.item(batch['id'], item['idea_id'])['run_id'] is None


def test_invalid_source_values_never_enter_metadata_snapshot(fixture):
    f = fixture; add(f)
    secret = 'PRIVATE malformed data must not be projected'
    with f.store.connect() as db:
        source = f.source | {'connection_id': secret, 'daily_time': secret, 'timezone': secret}
        db.execute('UPDATE video_source_settings SET settings=?', (json.dumps(source),))
    preview = f.planner.preview()
    assert not preview['ready'] and 'schedule_unavailable' in preview['blocker_codes']
    assert secret not in json.dumps(preview)
    assert all(preview['source'][key] is None for key in ('connection_id', 'daily_time', 'timezone'))


def test_progress_requires_canonical_run_proof_and_never_reopens_held_or_completed(fixture):
    f = fixture; add(f); enable(f); batch = claim(f); item = batch['items'][0]
    with pytest.raises(AppError): f.planner.mark_item(batch['id'], item['idea_id'], 'queued')
    wrong = run(f, item, idea_id=str(uuid4()))
    with pytest.raises(AppError): f.planner.mark_item(batch['id'], item['idea_id'], 'queued', wrong)
    with f.store.connect() as db: db.execute('DELETE FROM idea_manual_runs WHERE id=?', (wrong,))
    rid = run(f, item)
    f.planner.mark_item(batch['id'], item['idea_id'], 'queued', rid)
    with pytest.raises(AppError): f.planner.mark_item(batch['id'], item['idea_id'], 'pending')
    with pytest.raises(AppError): f.planner.mark_item(batch['id'], item['idea_id'], 'completed', rid)
    with f.store.connect() as db: db.execute("UPDATE idea_manual_runs SET status='completed' WHERE id=?", (rid,))
    f.planner.configure(False, f.planner.settings()['revision'])
    f.planner.mark_item(batch['id'], item['idea_id'], 'completed', rid)
    assert f.planner.item(batch['id'], item['idea_id'])['status'] == 'completed'
    with pytest.raises(AppError): f.planner.mark_item(batch['id'], item['idea_id'], 'needs_review', error_code='uncertain')


def test_disabling_stops_dispatch_but_keeps_existing_run_and_snapshot(fixture):
    f = fixture; add(f); enable(f); batch = claim(f); item = batch['items'][0]; rid = run(f, item)
    f.planner.configure(False, f.planner.settings()['revision'])
    context = {'batch_id': batch['id'], 'settings_revision': batch['settings_revision'], 'day_key': batch['day_key']}
    assert not f.planner.authorize(item['idea_id'], item['request_id'], item['variant_count'], context)
    f.planner.mark_item(batch['id'], item['idea_id'], 'queued', rid)
    assert f.planner.authorize(item['idea_id'], item['request_id'], item['variant_count'], context, running=True)
    assert f.planner.batch(batch['id'])['snapshot'] == batch['snapshot']
    with f.store.connect() as db: assert db.execute('SELECT status FROM idea_manual_runs WHERE id=?', (rid,)).fetchone()[0] == 'queued'
    with f.store.connect() as db: db.execute("UPDATE idea_manual_runs SET status='completed' WHERE id=?", (rid,))
    f.planner.mark_item(batch['id'], item['idea_id'], 'completed', rid)


def test_restarts_keep_ledgers_and_la_day_across_dst(fixture):
    f = fixture; add(f); enable(f)
    f.clock[0] = datetime(2030, 11, 3, 6, 59, tzinfo=timezone.utc)
    assert f.planner.preview()['day_key'] == '2030-11-02'
    first = claim(f)
    restarted = AutoBatchPlanner(f.store, clock=lambda: f.clock[0])
    assert restarted.current_batch()['id'] == first['id']
    f.clock[0] = datetime(2030, 11, 3, 9, 30, tzinfo=timezone.utc)
    assert restarted.preview()['day_key'] == '2030-11-03'
    assert 'previous_batch_incomplete' in restarted.preview()['blocker_codes']
    assert restarted.find_incomplete_batch()['id'] == first['id']


def test_empty_unknown_config_and_invalid_public_arguments_fail_closed(fixture):
    f = fixture
    assert 'no_eligible_ideas' in f.planner.preview()['blocker_codes']
    for action in (lambda: f.planner.configure(1,1), lambda: f.planner.configure(True,True),
                   lambda: f.planner.claim_day(1,'bad'), lambda: f.planner.preview('2030-1-1'),
                   lambda: f.planner.batch('bad'), lambda: f.planner.list_batches(True),
                   lambda: f.planner.mark_item(str(uuid4()),str(uuid4()),[])):
        with pytest.raises(AppError): action()
    with f.store.connect() as db: db.execute('DELETE FROM video_source_settings')
    assert 'schedule_unavailable' in f.planner.preview()['blocker_codes']
    assert not f.planner.authorize('bad', 'bad', 2, {})


def test_settings_cas_and_noop_do_not_revise_frozen_policy(fixture):
    f = fixture
    assert f.planner.configure(False,1)['revision'] == 1
    assert f.planner.configure(True,1)['revision'] == 2
    with pytest.raises(AppError) as caught: f.planner.configure(False,1)
    assert caught.value.code == 'auto_settings_changed'


def test_legacy_pipeline_only_install_creates_default_off_without_modifying_history(tmp_path):
    store = Store(tmp_path / 'old')
    with store.connect() as db:
        db.execute('CREATE TABLE sentinel(id TEXT PRIMARY KEY, value TEXT)')
        db.execute("INSERT INTO sentinel VALUES('old','seven canonical reservations')")
    planner = AutoBatchPlanner(store)
    assert not planner.settings()['enabled']
    assert 'schedule_unavailable' in planner.preview()['blocker_codes']
    with store.connect() as db:
        assert tuple(db.execute('SELECT * FROM sentinel').fetchone()) == ('old','seven canonical reservations')
