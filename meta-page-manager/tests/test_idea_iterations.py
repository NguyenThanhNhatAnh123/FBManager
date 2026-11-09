"""Explicit repeat production with synthetic history; no real worker or media."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from test_idea_runs import fixture, add, drain
from test_idea_workspace_api import manual_run_fixture, request


def history(f):
    idea = add(f)
    run = f.runs.request(idea['id'], str(uuid4()))
    mark_history(f.store, idea, run['id'])
    return f.pipeline.idea(idea['id']), run


def mark_history(store, idea, run_id=None):
    """Only a local fixture ledger, never an approval or playable media claim."""
    with store.connect() as db:
        if run_id:
            db.execute("UPDATE idea_manual_runs SET status='completed',stage='completed' WHERE id=?", (run_id,))
        db.execute("UPDATE idea_pipeline_ideas SET campaign_state='verified',campaign=? WHERE id=?",
            (json.dumps({'url': 'https://blueox179.com/campaign/fixture-old-product'}), idea['id']))
        for variant in idea['variants']:
            db.execute("UPDATE idea_pipeline_variants SET status='metadata_registered',video_sha256=?,provenance=? WHERE id=?",
                (hashlib.sha256(('old synthetic video:' + variant['id']).encode()).hexdigest(),
                 json.dumps({'skill': 'fixture', 'run_id': run_id or 'legacy-fixture', 'concept_id': str(variant['ordinal'])}), variant['id']))


def repeat(f, idea, key=None, count=4, **kwargs):
    return f.runs.request_iteration(idea['id'], key or str(uuid4()),
        expected_source_sha256=kwargs.pop('expected_source_sha256', idea['artwork_sha256']), variant_count=count, **kwargs)


def rows(f, table):
    with f.store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]


def payload(f, run):
    return json.loads(f.runs._row(run['id'])['payload'])


def test_repeat_appends_fresh_ids_and_preserves_entire_old_history(fixture):
    f = fixture; idea, old = history(f)
    original_ideas = rows(f, 'idea_pipeline_ideas')
    original_variants = rows(f, 'idea_pipeline_variants')
    original_run = f.runs._row(old['id'])
    run = repeat(f, idea)
    frozen = payload(f, run)
    assert run['production_iteration']['number'] == 2 and run['variant_count'] == 4
    assert run['idea_id'] == old['idea_id'] and run['artwork_sha256'] == old['artwork_sha256']
    assert run['trigger_mode'] == 'manual_web_run_idea'
    assert rows(f, 'idea_pipeline_ideas') == original_ideas
    current = rows(f, 'idea_pipeline_variants')
    assert current[:7] == original_variants and [row['ordinal'] for row in current[7:]] == [8, 9, 10, 11]
    assert not {v['id'] for v in original_variants} & {v['variant_id'] for v in frozen['variants']}
    assert not {v['reservation_id'] for v in original_variants} & {v['reservation_id'] for v in frozen['variants']}
    assert [v['ordinal'] for v in frozen['variants']] == [1, 2, 3, 4]
    assert f.runs._row(old['id']) == original_run
    assert not f.launcher.calls and not f.store.reel_jobs() and not f.store.reel_assets()


def test_repeat_same_uuid_replays_without_new_metadata_and_cannot_change_source_count_or_mode(fixture):
    f = fixture; idea, _ = history(f); key = str(uuid4())
    run = repeat(f, idea, key)
    assert repeat(f, idea, key) == run
    for call in (lambda: repeat(f, idea, key, count=5),
                 lambda: repeat(f, idea, key, expected_source_sha256='c' * 64),
                 lambda: f.runs.request(idea['id'], key, variant_count=4)):
        with pytest.raises(AppError) as error: call()
        assert error.value.code == 'idea_run_conflict'
    assert len(rows(f, 'idea_production_iterations')) == 1 and len(rows(f, 'idea_pipeline_variants')) == 11


def test_old_manual_replay_and_original_prefix_remain_stable_after_repeat(fixture):
    f = fixture; idea, old = history(f)
    original = f.pipeline.run_variants(idea['id'], 7)
    repeat(f, idea)
    assert f.pipeline.run_variants(idea['id'], 7) == original
    assert f.runs.request(idea['id'], old['request_id'])['id'] == old['id']
    with pytest.raises(AppError): f.runs.request(idea['id'], str(uuid4()), variant_count=4)


def test_legacy_completed_variants_need_no_fabricated_old_run_to_repeat(fixture):
    f = fixture; idea = add(f); mark_history(f.store, idea)
    run = repeat(f, idea)
    assert run['production_iteration']['number'] == 2 and len(rows(f, 'idea_manual_runs')) == 1


def test_unfinished_or_changed_source_creates_no_iteration(fixture):
    f = fixture; idea = add(f)
    for target in (idea,):
        with pytest.raises(AppError): repeat(f, target)
    mark_history(f.store, idea)
    with pytest.raises(AppError) as error: repeat(f, idea, expected_source_sha256='c' * 64)
    assert error.value.code == 'idea_iteration_source_changed'
    assert rows(f, 'idea_production_iterations') == [] and len(rows(f, 'idea_pipeline_variants')) == 7


@pytest.mark.parametrize('status', ['queued', 'preparing', 'running', 'importing', 'needs_review', 'cancelled'])
def test_repeat_does_not_bypass_an_unresolved_prior_run(fixture, status):
    f = fixture; idea, old = history(f)
    with f.store.connect() as db: db.execute('UPDATE idea_manual_runs SET status=? WHERE id=?', (status, old['id']))
    with pytest.raises(AppError) as error: repeat(f, idea)
    assert error.value.code == 'idea_iteration_requires_review'
    assert rows(f, 'idea_production_iterations') == [] and len(rows(f, 'idea_pipeline_variants')) == 7


@pytest.mark.parametrize('state', ['queued', 'leased', 'submitting', 'needs_review', 'uncertain'])
def test_repeat_does_not_bypass_an_unresolved_legacy_job(fixture, state):
    f = fixture; idea, _ = history(f)
    with f.store.connect() as db:
        db.execute('INSERT INTO idea_pipeline_jobs(id,idea_id,stage,job_key,status,created_at) VALUES(?,?,?,?,?,?)',
            (str(uuid4()), idea['id'], 'upload', 'fixture-legacy-upload', state, f.runs._now()))
    with pytest.raises(AppError): repeat(f, idea)
    assert rows(f, 'idea_production_iterations') == []


def test_manual_readiness_stays_strict_and_failure_reserves_nothing(fixture, monkeypatch):
    f = fixture; idea, _ = history(f)
    monkeypatch.setattr(f.runs, 'readiness', lambda: {'ready': False})
    with pytest.raises(AppError) as error: repeat(f, idea)
    assert error.value.code == 'idea_run_not_ready'
    assert rows(f, 'idea_production_iterations') == [] and len(rows(f, 'idea_pipeline_variants')) == 7


def test_run_insert_failure_rolls_back_iteration_mapping_and_appended_variants(fixture):
    f = fixture; idea, _ = history(f)
    with f.store.connect() as db:
        db.execute("""CREATE TRIGGER reject_fixture_repeat BEFORE INSERT ON idea_manual_runs
            WHEN instr(NEW.payload,'production_iteration')>0 BEGIN SELECT RAISE(ABORT,'fixture insert failure'); END""")
    with pytest.raises(sqlite3.IntegrityError): repeat(f, idea)
    assert rows(f, 'idea_production_iterations') == rows(f, 'idea_production_iteration_variants') == []
    assert len(rows(f, 'idea_pipeline_variants')) == 7 and len(rows(f, 'idea_manual_runs')) == 1


def test_competing_explicit_requests_cannot_append_two_live_iterations(fixture):
    f = fixture; idea, _ = history(f)
    def run(_):
        try: return repeat(f, idea)['status']
        except AppError as error: return error.code
    with ThreadPoolExecutor(max_workers=2) as pool: result = list(pool.map(run, range(2)))
    assert result.count('queued') == result.count('idea_iteration_requires_review') == 1
    assert len(rows(f, 'idea_production_iterations')) == 1 and len(rows(f, 'idea_pipeline_variants')) == 11


def test_prepare_freezes_local_mapping_and_fake_worker_runs_through_normal_owned_lifecycle(fixture):
    f = fixture; idea, _ = history(f); run = repeat(f, idea)
    asyncio.run(drain(f.runs))
    assert len(f.launcher.calls) == 1 and f.runs.get(run['id'])['status'] == 'needs_review'
    data = json.loads((f.runs.runs_root / run['id'] / 'run-input.json').read_text(encoding='utf-8'))
    assert data['production_iteration'] == run['production_iteration'] and data['run_id'] == run['id'] and data['lease_fence'] == 1
    assert data['variants'] == payload(f, run)['variants'] and [v['ordinal'] for v in data['variants']] == [1, 2, 3, 4]
    assert not f.store.reel_jobs()


@pytest.mark.parametrize('change', ['request', 'number', 'count', 'source', 'mapping', 'orphan', 'null'])
def test_exact_iteration_mapping_guard_rejects_drift(fixture, change):
    f = fixture; idea, _ = history(f); run = repeat(f, idea); frozen = payload(f, run)
    with f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET status='preparing' WHERE id=?", (run['id'],))
    active = f.runs._row(run['id']); assert f.runs._guard(active)['id'] == run['id']
    with f.store.connect() as db:
        if change == 'mapping':
            db.execute('UPDATE idea_production_iteration_variants SET variant_id=? WHERE iteration_id=? AND ordinal=1',
                       (idea['variants'][0]['id'], frozen['production_iteration']['id']))
        elif change == 'orphan':
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('INSERT INTO idea_production_iteration_variants VALUES(?,?,?)', (frozen['production_iteration']['id'], 5, str(uuid4())))
        elif change == 'null':
            frozen['production_iteration'] = None
            encoded = json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=? WHERE id=?',
                       (encoded, hashlib.sha256(encoded.encode()).hexdigest(), run['id']))
        else:
            column, value = {'request': ('request_id', str(uuid4())), 'number': ('number', 3),
                             'count': ('variant_count', 5), 'source': ('artwork_sha256', 'c' * 64)}[change]
            db.execute('UPDATE idea_production_iterations SET ' + column + '=? WHERE id=?', (value, frozen['production_iteration']['id']))
    with pytest.raises((AppError, ValueError, asyncio.CancelledError)):
        f.runs._guard(f.runs._row(run['id']))


def test_controller_can_freeze_one_audio_slice_for_repeat_without_public_override(fixture):
    f = fixture; idea, _ = history(f)
    assignments = [{'source_sha256': chr(97 + index) * 64} for index in range(4)]
    run = repeat(f, idea, audio_assignments=assignments)
    assert payload(f, run)['audio_assignments'] == assignments


def test_repeat_api_is_explicit_authenticated_strict_and_idempotent(tmp_path):
    app, headers, path, idea, ordinary = manual_run_fixture(tmp_path)
    mark_history(app.state.manager.store, idea)
    endpoint = ordinary.rsplit('/', 1)[0] + '/iterations'
    body = {'request_id': str(uuid4()), 'expected_source_sha256': idea['artwork_sha256'], 'variant_count': 4}
    assert request(app, endpoint, 'POST', body)[0] == 401
    assert request(app, endpoint, 'POST', body, {'cookie': headers['cookie']})[0] == 403
    for patch in ({'variant_count': True}, {'variant_count': 15}, {'variant_count': 0},
                  {'expected_source_sha256': 'Z' * 64}, {'production_iteration': {'id': str(uuid4()), 'number': 2}},
                  {'audio_assignments': []}):
        assert request(app, endpoint, 'POST', body | patch, headers)[0] == 422
    code, _, value = request(app, endpoint, 'POST', body, headers)
    assert code == 200 and value['accepted'] and value['run']['variant_count'] == 4
    assert value['run']['production_iteration']['number'] == 2
    assert request(app, ordinary, 'POST', {'request_id': str(uuid4()), 'variant_count': 4}, headers)[0] == 409
    path.unlink()
    code, _, replay = request(app, endpoint, 'POST', body, headers)
    assert code == 200 and replay == value
    assert len(app.state.idea_runs.list()['items']) == 1 and not app.state.manager.store.reel_jobs()


def test_repeat_api_checks_original_source_before_any_metadata_or_run(tmp_path):
    app, headers, path, idea, ordinary = manual_run_fixture(tmp_path)
    mark_history(app.state.manager.store, idea)
    endpoint = ordinary.rsplit('/', 1)[0] + '/iterations'
    path.write_bytes(b'changed fixture source')
    code, _, _ = request(app, endpoint, 'POST', {'request_id': str(uuid4()),
        'expected_source_sha256': idea['artwork_sha256'], 'variant_count': 4}, headers)
    assert code == 404 and app.state.idea_runs.list()['items'] == []
    with app.state.manager.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM idea_production_iterations').fetchone()[0] == 0


def test_catalog_projects_latest_iteration_membership_not_historical_prefix(fixture):
    f=fixture;idea,old=history(f)
    historical=rows(f,'idea_pipeline_variants')
    run=repeat(f,idea);frozen=payload(f,run)
    with f.store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS idea_pipeline_approved_videos(variant_id TEXT,video_sha256 TEXT,reservation_id TEXT,status TEXT)')
        old_variant=historical[0]
        db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?)',
            (old_variant['id'],old_variant['video_sha256'],old_variant['reservation_id'],'approved'))
        current=frozen['variants'][0];sha='a'*64
        db.execute("UPDATE idea_pipeline_variants SET status='metadata_registered',video_sha256=? WHERE id=?",(sha,current['variant_id']))
        db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?)',
            (current['variant_id'],sha,current['reservation_id'],'manual_review_pending'))
    view=f.pipeline.idea(idea['id'])
    assert view['allocated_variant_count']==4
    assert [v['id'] for v in view['variants']]==[v['variant_id'] for v in frozen['variants']]
    assert [v['ordinal'] for v in view['variants']]==[1,2,3,4]
    assert [v['verification'] for v in view['variants']]==['manual_visual_review_pending','metadata_only','metadata_only','metadata_only']
    assert f.pipeline.catalog()['items'][0]['variants']==view['variants']
    assert rows(f,'idea_pipeline_variants')[:7]==historical
    assert [v['id'] for v in f.pipeline.run_variants(idea['id'],7)]==[v['id'] for v in historical]
