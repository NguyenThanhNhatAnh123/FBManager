"""Offline idea ledger checks; no live DB, agent launch, Graph or publications."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from page_manager.idea_pipeline import IdeaPipeline, SHOP
from page_manager.store import Store


PNG = b'\x89PNG\r\n\x1a\nfixture-artwork'
SOURCE = {'shop': SHOP, 'campaign_url': 'https://blueox179.com/campaign/zitat25', 'verified': True}
BLACK_SOURCE = {'shop': SHOP, 'campaign_url': 'https://blueox179.com/campaign/ge2610', 'verified': True}


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / 'ideas'; root.mkdir()
    store = Store(tmp_path / 'manager')
    clock = [datetime(2030, 11, 1, 12, tzinfo=timezone.utc)]
    pipeline = IdeaPipeline(SimpleNamespace(store=store), directory=root,
                            output_directory=tmp_path / 'output', clock=lambda: clock[0])
    return SimpleNamespace(root=root, store=store, clock=clock, pipeline=pipeline)


def add(f, filename='httpsblueox179.comcampaignguessed.png', payload=PNG):
    path = f.root / filename
    path.write_bytes(payload)
    f.pipeline.scan()
    f.clock[0] += timedelta(seconds=31)
    f.pipeline.scan()
    sha = hashlib.sha256(payload).hexdigest()
    return next(i for i in f.pipeline.status()['ideas'] if i['artwork_sha256'] == sha)


def enabled(f):
    return f.pipeline.configure(enabled=True, upload_ready=True, video_ready=True, campaign_source=SOURCE)


def upload_job(f, idea):
    enabled(f)
    queued = f.pipeline.queue_stage(idea['id'], 'upload', request_id=str(uuid4()))
    return f.pipeline.claim('upload', 'uploader', request_id=str(uuid4()))['job']


def finish(f, job, outcome, result=None):
    return f.pipeline.complete(job['id'], job['worker_id'], job['fence'], job['revision'],
                               outcome, result or {}, request_id=str(uuid4()))


def campaign_result(f, idea):
    url = 'https://blueox179.com/campaign/verified-' + idea['id'][:8]
    return {'idea_id': idea['id'], 'artwork_sha256': idea['artwork_sha256'], 'shop': SHOP,
            'campaign_url': url, 'campaign_source': SOURCE['campaign_url'],
            'evidence': {'observed_at': f.clock[0].isoformat(), 'artwork_matches': True,
                         'quote_matches': True, 'public_url': url}}


def verified_idea(f, filename='art.png', payload=PNG):
    idea = add(f, filename, payload)
    job = upload_job(f, idea)
    job = finish(f, job, 'submitting')
    finish(f, job, 'verified', campaign_result(f, idea))
    return f.pipeline.idea(idea['id'])


def reserve(f, idea, number=0, sha=None, **extra):
    return f.pipeline.register_video(idea['id'], idea['variants'][number]['id'], idea['artwork_sha256'],
            sha or hashlib.sha256(('video:' + idea['id'] + str(number)).encode()).hexdigest(),
            provenance={'skill': 'fixture', 'run_id': 'run-1', 'concept_id': str(number)}, **extra)


def test_scan_is_disabled_safe_and_only_plans(fixture):
    f = fixture
    idea = add(f)
    status = f.pipeline.status()
    assert not status['ready'] and not status['settings']['enabled']
    assert idea['campaign_state'] == 'prepared' and idea['campaign'] is None
    assert len(idea['variants']) == 7
    assert all(v['status'] == 'planned' and v['video_sha256'] is None for v in idea['variants'])
    assert len({v['reservation_id'] for v in idea['variants']}) == 7
    assert not status['jobs'] and not f.store.reel_jobs() and not f.store.reel_assets()
    assert not f.pipeline.output_directory.exists()
    assert set(f.root.iterdir()) == {f.root / idea['filename']}
    assert idea['filename'] not in json.dumps(idea['campaign'])
    assert not status['policy']['allocation_active']


@pytest.mark.parametrize('count',[1,2,5,14])
def test_reserve_dynamic_prefix_preserves_every_existing_uuid_and_unused_row(fixture,count):
    f=fixture;idea=add(f)
    original=idea['variants']
    selected=f.pipeline.reserve_variants(idea['id'],count)
    assert len(selected)==count and [v['ordinal'] for v in selected]==list(range(1,count+1))
    assert selected[:min(count,7)]==original[:min(count,7)]
    all_rows=f.pipeline.idea(idea['id'])['variants']
    assert len(all_rows)==max(count,7) and all_rows[:7]==original
    assert f.pipeline.reserve_variants(idea['id'],count)==selected
    assert not f.store.reel_jobs() and not f.store.reel_assets()


@pytest.mark.parametrize('count',[0,15,-1,True,False,'2',2.0,None])
def test_reserve_rejects_invalid_count_without_changing_legacy_reservations(fixture,count):
    f=fixture;idea=add(f)
    with pytest.raises(AppError) as caught:f.pipeline.reserve_variants(idea['id'],count)
    assert caught.value.code=='invalid_idea_variant_count'
    assert f.pipeline.idea(idea['id'])['variants']==idea['variants']


def test_reservations_grow_in_the_callers_transaction_and_roll_back_together(fixture):
    f=fixture;idea=add(f)
    with pytest.raises(RuntimeError):
        with f.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            assert len(f.pipeline.reserve_variants(idea['id'],14,db=db))==14
            raise RuntimeError('synthetic rollback')
    assert f.pipeline.idea(idea['id'])['variants']==idea['variants']


def test_library_catalog_does_not_hide_ideas_after_first_hundred(fixture):
    f = fixture
    for index in range(105):
        (f.root / f'idea-{index:03}.png').write_bytes(PNG + str(index).encode())
    f.pipeline.scan()
    f.clock[0] += timedelta(seconds=31)
    f.pipeline.scan()
    status = f.pipeline.status()
    assert status['idea_count'] == len(status['ideas']) == 105
    assert {idea['filename'] for idea in status['ideas']} == {f'idea-{index:03}.png' for index in range(105)}
    assert not status['jobs'] and not f.store.reel_jobs() and not f.store.reel_assets()


def test_stable_observations_and_changed_file_restart_wait(fixture):
    f = fixture
    path = f.root / 'art.png'; path.write_bytes(PNG)
    assert f.pipeline.scan()['waiting'] == 1
    f.clock[0] += timedelta(seconds=31)
    path.write_bytes(PNG + b'changed')
    assert f.pipeline.scan()['waiting'] == 1
    assert not f.pipeline.status()['ideas']
    f.clock[0] += timedelta(seconds=31)
    assert f.pipeline.scan()['registered'] == 1


def test_rename_copy_restart_dedup_preserves_ids(fixture):
    f = fixture
    idea = add(f, 'one.png')
    (f.root / 'one.png').rename(f.root / 'two.png')
    (f.root / 'copy.png').write_bytes(PNG)
    f.pipeline.scan(); f.clock[0] += timedelta(seconds=31)
    assert f.pipeline.scan()['registered'] == 0
    other = IdeaPipeline(SimpleNamespace(store=f.store), directory=f.root,
                         output_directory=f.pipeline.output_directory, clock=lambda: f.clock[0])
    assert other.idea(idea['id']) == idea
    assert other.status()['idea_count'] == 1


def test_links_hardlinks_and_fake_image_are_rejected(fixture, tmp_path):
    f = fixture
    outside = tmp_path / 'outside.png'; outside.write_bytes(PNG)
    os.link(outside, f.root / 'hardlink.png')
    (f.root / 'fake.png').write_bytes(b'not-an-image')
    try: (f.root / 'symlink.png').symlink_to(outside)
    except OSError: pass
    f.pipeline.scan(); f.clock[0] += timedelta(seconds=31)
    result = f.pipeline.scan()
    assert result['registered'] == 0 and result['warning']
    assert f.pipeline.status()['idea_count'] == 0


def test_candidate_source_saved_without_enabling_workers(fixture):
    f = fixture
    selected = dict(SOURCE, verified=False)
    status = f.pipeline.configure(campaign_source=selected)
    assert status['settings']['campaign_source'] == selected and not status['ready']
    with pytest.raises(AppError): f.pipeline.configure(enabled=True, upload_ready=True, video_ready=True, campaign_source=selected)
    with pytest.raises(AppError): f.pipeline.configure(upload_ready=True, campaign_source=selected)


def test_black_source_is_separate_and_all_configured_sources_need_verification(fixture):
    f = fixture
    selected, black = dict(SOURCE, verified=False), dict(BLACK_SOURCE, verified=False)
    status = f.pipeline.configure(campaign_source=selected, black_campaign_source=black)
    assert status['settings']['black_campaign_source'] == black and not status['ready']
    with pytest.raises(AppError): f.pipeline.configure(enabled=True, upload_ready=True, video_ready=True,
        campaign_source=SOURCE, black_campaign_source=black)
    with pytest.raises(AppError): f.pipeline.configure(campaign_source=SOURCE, black_campaign_source=SOURCE)
    with pytest.raises(AppError): f.pipeline.configure(campaign_source=SOURCE,
        black_campaign_source=dict(BLACK_SOURCE, campaign_url='https://evil.test/campaign/ge2610'))
    assert f.pipeline.configure(enabled=True, upload_ready=True, video_ready=True,
        campaign_source=SOURCE, black_campaign_source=BLACK_SOURCE)['ready']


def test_old_settings_backfill_black_source_none(fixture):
    f = fixture
    with f.store.connect() as db:
        db.execute('UPDATE idea_pipeline_settings SET settings=? WHERE id=1',
                   (json.dumps({'enabled': False, 'upload_ready': False, 'video_ready': False, 'campaign_source': None}),))
    assert f.pipeline.status()['settings']['black_campaign_source'] is None


def test_black_campaign_completion_binds_color_without_assuming_default_color(fixture):
    f = fixture; idea = add(f)
    f.pipeline.configure(enabled=True, upload_ready=True, video_ready=True,
                         campaign_source=SOURCE, black_campaign_source=BLACK_SOURCE)
    f.pipeline.queue_stage(idea['id'], 'upload', request_id=str(uuid4()))
    job = f.pipeline.claim('upload', 'uploader', request_id=str(uuid4()))['job']
    job = finish(f, job, 'submitting')
    result = dict(campaign_result(f, idea), campaign_source=BLACK_SOURCE['campaign_url'])
    with pytest.raises(AppError): finish(f, job, 'verified', result)
    with pytest.raises(AppError): finish(f, job, 'verified', dict(campaign_result(f, idea), shirt_color='black'))
    finish(f, job, 'verified', dict(result, shirt_color='black'))
    assert f.pipeline.idea(idea['id'])['campaign']['shirt_color'] == 'black'


@pytest.mark.parametrize('source', [
    dict(SOURCE, shop='wildy05.com'), dict(SOURCE, campaign_url='https://evil.test/campaign/zitat25'),
    dict(SOURCE, campaign_url='https://blueox179.com.evil.test/campaign/zitat25'),
    dict(SOURCE, campaign_url='https://blueox179.com/campaign/zitat25?token=private'),
    dict(SOURCE, campaign_url='https://user:pass@blueox179.com/campaign/zitat25'),
    dict(SOURCE, campaign_url='http://blueox179.com/campaign/zitat25'), dict(SOURCE, verified='true'),
])
def test_exact_shop_no_secrets_in_urls(fixture, source):
    with pytest.raises(AppError): fixture.pipeline.configure(campaign_source=source)


def test_revision_and_readiness_gate(fixture):
    f = fixture; idea = add(f)
    with pytest.raises(AppError) as error:
        f.pipeline.queue_stage(idea['id'], 'upload', request_id=str(uuid4()))
    assert error.value.code == 'pipeline_not_ready'
    revision = f.pipeline.status()['settings']['revision']
    f.pipeline.configure(campaign_source=dict(SOURCE, verified=False), revision=revision)
    with pytest.raises(AppError) as error: f.pipeline.configure(revision=revision)
    assert error.value.code == 'pipeline_revision_changed'


def test_exclusive_claim_and_expired_fencing(fixture):
    f = fixture; idea = add(f); enabled(f)
    f.pipeline.queue_stage(idea['id'], 'upload', request_id=str(uuid4()))
    def claim(worker): return f.pipeline.claim('upload', worker, request_id=str(uuid4()), lease_seconds=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ['one', 'two']))
    jobs = [r['job'] for r in results if r['job']]
    assert len(jobs) == 1
    old = jobs[0]
    f.clock[0] += timedelta(seconds=31)
    new = claim('next')['job']
    assert new['id'] == old['id'] and new['fence'] == old['fence'] + 1
    with pytest.raises(AppError) as error: finish(f, old, 'submitting')
    assert error.value.code == 'stale_idea_lease'
    assert finish(f, new, 'submitting')['status'] == 'submitting'


def test_mutation_uuid_replay_and_conflicting_payload(fixture):
    f = fixture; idea = add(f); enabled(f); key = str(uuid4())
    a = f.pipeline.queue_stage(idea['id'], 'upload', request_id=key)
    assert f.pipeline.queue_stage(idea['id'], 'upload', request_id=key) == a
    claim_id = str(uuid4())
    job = f.pipeline.claim('upload', 'one', request_id=claim_id)['job']
    assert f.pipeline.claim('upload', 'one', request_id=claim_id)['job'] == job
    with pytest.raises(AppError) as error: f.pipeline.claim('upload', 'other', request_id=claim_id)
    assert error.value.code == 'idea_operation_conflict'
    operation = str(uuid4())
    done = f.pipeline.complete(job['id'], 'one', job['fence'], job['revision'], 'submitting', {}, request_id=operation)
    assert f.pipeline.complete(job['id'], 'one', job['fence'], job['revision'], 'submitting', {}, request_id=operation) == done
    assert f.pipeline.idea(idea['id'])['campaign_state'] == 'submitting'


def test_submitting_expiry_becomes_uncertain_never_reclaimed(fixture):
    f = fixture; idea = add(f); job = upload_job(f, idea)
    job = finish(f, job, 'submitting')
    f.clock[0] += timedelta(seconds=301)
    assert f.pipeline.claim('upload', 'other', request_id=str(uuid4()))['job'] is None
    current = f.pipeline.status()['jobs'][0]
    assert current['status'] == 'uncertain'
    assert f.pipeline.idea(idea['id'])['campaign_state'] == 'uncertain'
    with pytest.raises(AppError): f.pipeline.queue_stage(idea['id'], 'upload', request_id=str(uuid4()))
    with pytest.raises(AppError): finish(f, current, 'verified', campaign_result(f, idea))
    result = campaign_result(f, idea); result['reconciled'] = True
    assert finish(f, current, 'verified', result)['status'] == 'verified'


@pytest.mark.parametrize('outcome', ['uncertain', 'verified'])
def test_worker_can_report_uncertainty_or_reconcile_after_submit_lease_expires(fixture, outcome):
    f = fixture; idea = add(f); job = upload_job(f, idea)
    job = finish(f, job, 'submitting')
    f.clock[0] += timedelta(seconds=301)
    result = dict(campaign_result(f, idea), reconciled=True) if outcome == 'verified' else {}
    assert finish(f, job, outcome, result)['status'] == outcome
    assert f.pipeline.claim('upload', 'replacement', request_id=str(uuid4()))['job'] is None


def test_campaign_checkpoint_exact_artwork_source_and_evidence(fixture):
    f = fixture; idea = add(f); job = upload_job(f, idea)
    with pytest.raises(AppError): finish(f, job, 'verified', campaign_result(f, idea))
    job = finish(f, job, 'submitting')
    for changes in ({'artwork_sha256': '0' * 64}, {'shop': 'wildy05.com'},
                    {'campaign_source': 'https://blueox179.com/campaign/other'},
                    {'campaign_url': 'https://other.test/campaign/tee'}, {'evidence': {}}):
        result = dict(campaign_result(f, idea), **changes)
        with pytest.raises(AppError): finish(f, job, 'verified', result)
    finish(f, job, 'verified', campaign_result(f, idea))
    result = f.pipeline.idea(idea['id'])
    assert result['campaign_state'] == 'verified' and result['campaign']['shop'] == SHOP
    assert not f.store.reel_jobs()


def test_active_source_cannot_change_but_pause_can(fixture):
    f = fixture; idea = add(f); job = upload_job(f, idea)
    with pytest.raises(AppError): f.pipeline.configure(enabled=True, upload_ready=True, video_ready=True,
        campaign_source=dict(SOURCE, campaign_url='https://blueox179.com/campaign/other'))
    assert not f.pipeline.configure(enabled=False, upload_ready=True, video_ready=True,
                                    campaign_source=SOURCE)['settings']['enabled']
    with pytest.raises(AppError): f.pipeline.claim('upload', 'other', request_id=str(uuid4()))


def test_video_metadata_reservation_is_unique_not_published_or_qa_verified(fixture):
    f = fixture; idea = verified_idea(f)
    first = reserve(f, idea)
    assert first['status'] == 'metadata_registered' and first['verification'] == 'metadata_only'
    assert reserve(f, idea) == first
    with pytest.raises(AppError) as error: reserve(f, idea, 1, first['video_sha256'])
    assert error.value.code == 'duplicate_idea_video'
    with pytest.raises(AppError): reserve(f, idea, 0, 'f' * 64)
    flagged = reserve(f, idea, 1, near_duplicate=True)
    assert flagged['status'] == 'needs_review' and flagged['near_duplicate']
    assert not f.store.reel_assets() and not f.store.reel_jobs()
    assert not f.pipeline.output_directory.exists()


def test_video_stage_metadata_completion_requires_verified_campaign(fixture):
    f = fixture; idea = add(f); enabled(f)
    with pytest.raises(AppError): f.pipeline.queue_stage(idea['id'], 'video', variant_id=idea['variants'][0]['id'], request_id=str(uuid4()))
    job = f.pipeline.queue_stage(idea['id'], 'upload', request_id=str(uuid4()))
    job = f.pipeline.claim('upload', 'uploader', request_id=str(uuid4()))['job']
    job = finish(f, job, 'submitting'); finish(f, job, 'verified', campaign_result(f, idea))
    variant = idea['variants'][0]
    f.pipeline.queue_stage(idea['id'], 'video', variant_id=variant['id'], request_id=str(uuid4()))
    job = f.pipeline.claim('video', 'video-agent', request_id=str(uuid4()))['job']
    job = finish(f, job, 'submitting')
    finish(f, job, 'verified', {'idea_id': idea['id'], 'variant_id': variant['id'], 'artwork_sha256': idea['artwork_sha256'],
        'video_sha256': 'a' * 64, 'near_duplicate': False, 'provenance': {'skill': 'fixture', 'run_id': 'run-2'}})
    assert f.pipeline.idea(idea['id'])['variants'][0]['status'] == 'metadata_registered'


def test_unknown_metrics_not_zero_no_reach_substitution(fixture):
    f = fixture; idea = verified_idea(f); variant = reserve(f, idea)
    published = f.clock[0] - timedelta(hours=24)
    args = (variant['id'], '123', '456', 'views')
    unknown = f.pipeline.record_views(*args, None, f.clock[0], published, availability='unavailable')
    assert unknown['value'] is None and f.pipeline.ranking()['cohorts'] == []
    with pytest.raises(AppError): f.pipeline.record_views(*args, None, f.clock[0], published)
    with pytest.raises(AppError): f.pipeline.record_views(variant['id'], '123', '456', 'reach', 100, f.clock[0], published)
    f.clock[0] += timedelta(minutes=1)
    f.pipeline.record_views(*args, 0, f.clock[0], published, metric_verified=True)
    ranked = f.pipeline.ranking()['cohorts'][0]['items'][0]
    assert ranked['median_views_24h'] == 0 and ranked['sample_count'] == 1
    assert ranked['allocation_group'] == 'exploration'
    assert not f.pipeline.ranking()['proposed_allocation']['active']


def test_snapshot_immutable_binding_and_unverified_excluded(fixture):
    f = fixture; idea = verified_idea(f); variant = reserve(f, idea)
    published = f.clock[0] - timedelta(hours=24)
    record = f.pipeline.record_views(variant['id'], '123', '456', 'views', 100, f.clock[0], published)
    assert f.pipeline.record_views(variant['id'], '123', '456', 'views', 100, f.clock[0], published) == record
    assert not f.pipeline.ranking()['cohorts']
    with pytest.raises(AppError): f.pipeline.record_views(variant['id'], '123', '456', 'views', 200, f.clock[0], published)
    with pytest.raises(AppError): f.pipeline.record_views(variant['id'], '999', '456', 'views', 100, f.clock[0] + timedelta(seconds=1), published)


def test_job_completion_is_not_publication_time_for_same_age_ranking(fixture):
    f = fixture; idea = verified_idea(f); variant = reserve(f, idea)
    finished = f.clock[0] - timedelta(hours=24)
    record = f.pipeline.record_views(variant['id'], '123', '456', 'views', 100, f.clock[0], finished,
                                    metric_verified=True, published_time_provenance='job_completion')
    assert record['value'] == 100 and record['published_time_provenance'] == 'job_completion'
    assert f.pipeline.ranking()['cohorts'] == []
    f.clock[0] += timedelta(seconds=1)
    published = finished - timedelta(minutes=5)
    f.pipeline.record_views(variant['id'], '123', '456', 'views', 110, f.clock[0], published,
                            metric_verified=True, published_time_provenance='provider_published_at')
    assert f.pipeline.ranking()['cohorts'][0]['items'][0]['median_views_24h'] == 110


def test_ranking_same_age_median_not_sum_or_mixed_metrics(fixture):
    f = fixture; idea = verified_idea(f)
    first_observation = f.clock[0]
    published = first_observation - timedelta(hours=24)
    f.clock[0] += timedelta(hours=1)
    for number, count in enumerate([10, 20, 1000]):
        variant = reserve(f, idea, number)
        f.pipeline.record_views(variant['id'], str(number + 1), str(number + 100), 'views', count,
                               first_observation, published, metric_verified=True)
        # Earlier/later samples for same variant must not count as extra videos.
        f.pipeline.record_views(variant['id'], str(number + 1), str(number + 100), 'views', count + 10000,
                               f.clock[0], published, metric_verified=True)
    variant = reserve(f, idea, 3)
    f.pipeline.record_views(variant['id'], '4', '104', 'views', 999999, f.clock[0],
                           f.clock[0] - timedelta(hours=72), metric_verified=True)
    f.pipeline.record_views(variant['id'], '4', '104', 'blue_reels_play_count', 999,
                           f.clock[0], f.clock[0] - timedelta(hours=72), metric_verified=True)
    ranking = f.pipeline.ranking()
    assert len(ranking['cohorts']) == 1
    item = ranking['cohorts'][0]['items'][0]
    assert item['median_views_24h'] == 20 and item['sample_count'] == 3
    assert item['confidence'] == 'observed' and item['median_page_lift'] is None
    assert not f.store.reel_jobs()
