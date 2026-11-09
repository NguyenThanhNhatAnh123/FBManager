"""Synthetic local handoff fixtures; no campaigns, generation service or Meta."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from page_manager.idea_pipeline import IdeaPipeline
import page_manager.idea_run_results as module
from page_manager.idea_run_results import IdeaRunResults, SOURCE_URL, BLACK_SOURCE_URL
from page_manager.service import PageManager


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return sha(path.read_bytes())


def png(color, size=(50, 80)):
    output = BytesIO()
    Image.new('RGB', size, color).save(output, 'PNG')
    return output.getvalue()


@pytest.fixture(scope='session')
def media(tmp_path_factory):
    ffmpeg = next((path for path in module.FFMPEG_PATHS if path.is_file()), None)
    if ffmpeg is None:
        pytest.skip('Known bundled FFmpeg absent; no system installation performed')
    root = tmp_path_factory.mktemp('synthetic-ready-videos')
    videos = []
    for index, color in enumerate(['red', 'green', 'blue', 'orange', 'purple', 'cyan', 'yellow']):
        target = root / f'fixture-{index}.mp4'
        command = [str(ffmpeg), '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
            f'color=c={color}:s=1080x1920:r=30', '-f', 'lavfi', '-i',
            f'sine=frequency={440 + index * 40}:sample_rate=48000', '-t', '8',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '64k', '-movflags', '+faststart', str(target)]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=90,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert result.returncode == 0, 'Synthetic local FFmpeg fixture failed'
        videos.append(target)
    return SimpleNamespace(videos=videos, ffmpeg=ffmpeg)


class Source:
    def __init__(self, root, pages, cid):
        self.directory, self.requests = root, 0
        self.settings = {'enabled': True, 'page_ids': pages, 'connection_id': cid,
                         'daily_time': '06:00', 'timezone': 'America/Los_Angeles', 'revision': 2}

    def status(self):
        return {'configured': True, 'settings': dict(self.settings)}

    def request_scan(self):
        self.requests += 1


@pytest.fixture
def f(tmp_path, media):
    abc = tmp_path / 'ABC'; abc.mkdir()
    inbox = tmp_path / 'Idea'; inbox.mkdir()
    destination = tmp_path / 'Video'; destination.mkdir()
    (inbox / 'art.png').write_bytes(png('grey'))
    now = [datetime.now(timezone.utc)]
    manager = PageManager(tmp_path / 'manager')
    cid = manager.store.save_connection({'label': 'fixture', 'token_type': 'page'}, 'PRIVATE FIXTURE TOKEN')
    ids = [str(10000 + index) for index in range(14)]
    manager.store.sync(cid, 1, [{'id': pid, 'name': 'Page ' + pid, 'sources': ['fixture']} for pid in ids],
                       True, 'working', None, {'id': ids[0], 'name': 'fixture'})
    from conftest import mark_page_information_updated
    for pid in ids:mark_page_information_updated(manager.store,pid,cid)
    pipeline = IdeaPipeline(manager, directory=inbox, output_directory=abc,
                            stable_seconds=0, clock=lambda: now[0])
    pipeline.scan(); pipeline.scan()
    idea = pipeline.status()['ideas'][0]
    run_id, request_id = str(uuid4()), str(uuid4())
    root = abc / 'page-manager-bridge' / 'runs'; directory = root / run_id
    directory.mkdir(parents=True)
    bridge = module._load_bridge()
    bridge.ABC_ROOT = abc
    variants = [{'variant_id': v['id'], 'reservation_id': v['reservation_id'], 'ordinal': v['ordinal']}
                for v in idea['variants']]
    sources = {'campaign_source': {'shop': 'blueox179.com', 'campaign_url': SOURCE_URL, 'verified': True},
               'black_campaign_source': {'shop': 'blueox179.com', 'campaign_url': BLACK_SOURCE_URL, 'verified': True}}
    payload = {'source_snapshot': sources, 'variants': variants, 'caption_mode': 'product_link_only',
               'trigger_mode': 'manual_web_run_idea', 'source_revision': 2}
    run = {'id': run_id, 'request_id': request_id, 'idea_id': idea['id'],
           'artwork_sha256': idea['artwork_sha256'], 'filename': idea['filename'],
           'status': 'importing', 'stage': 'validate_result', 'fence': 1,
           'run_directory': str(directory), **payload}
    source = Source(destination, ids, cid)
    with manager.store.connect() as db:
        db.executescript('''CREATE TABLE idea_manual_runs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,
            idea_id TEXT,artwork_sha256 TEXT,filename TEXT,payload TEXT,payload_hash TEXT,status TEXT,
            stage TEXT,message TEXT,cancel_requested INTEGER DEFAULT 0,created_at TEXT,started_at TEXT,
            finished_at TEXT,fence INTEGER DEFAULT 1);
            CREATE TABLE video_source_items(id TEXT PRIMARY KEY,sha256 TEXT);''')
        db.execute('INSERT INTO idea_manual_runs(id,request_id,idea_id,artwork_sha256,filename,payload,payload_hash,status,stage,fence) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (run_id, request_id, idea['id'], idea['artwork_sha256'], idea['filename'], json.dumps(payload),
             sha(module._canonical(payload)), 'importing', 'validate_result', 1))
    consumer = IdeaRunResults(manager, pipeline, source, runs_directory=root, bridge=bridge, ffmpeg_path=media.ffmpeg)
    result = SimpleNamespace(root=root, directory=directory, manager=manager, store=manager.store,
        pipeline=pipeline, idea=idea, run=run, source=source, consumer=consumer, bridge=bridge,
        destination=destination, media=media, inbox=inbox)
    make_output(result)
    return result


def test_existing_legacy_run_uses_its_bound_bridge_after_project_root_changes(f,tmp_path):
    f.consumer.runs_directory=tmp_path/'new-project/page-manager-bridge/runs'
    f.consumer.legacy_runs_directory=f.root
    f.consumer.legacy_bridge=f.bridge
    result=asyncio.run(f.consumer.accept(f.run,f.directory))
    assert result['accepted'] and result['approved']==7
    assert not f.manager.store.reel_jobs()


def test_same_run_uuid_in_project_and_legacy_roots_is_held_without_approval(f,tmp_path):
    f.consumer.runs_directory=tmp_path/'new-project/page-manager-bridge/runs'
    f.consumer.legacy_runs_directory=f.root;f.consumer.legacy_bridge=f.bridge
    (f.consumer.runs_directory/f.run['id']).mkdir(parents=True)
    result=asyncio.run(f.consumer.accept(f.run,f.directory))
    assert not result['accepted'] and result['approved']==0
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM idea_pipeline_approved_videos').fetchone()[0]==0


def make_output(f, *, existing=False, black=False):
    directory, run = f.directory, f.run
    staged = directory / 'source.png'; staged.write_bytes((f.inbox / 'art.png').read_bytes())
    campaign_url = 'https://blueox179.com/campaign/synthetic-' + run['id'][:8]
    job = {'schema_version': '1.0', 'idea_id': run['idea_id'], 'source_path': str(staged),
        'source_sha256': run['artwork_sha256'], 'campaign_url': campaign_url,
        'campaign_verified': True, 'caption_mode': 'product_link_only',
        'trigger_mode': run['trigger_mode'], 'lease_fence': 1,
        'variants': [{k: v[k] for k in ('variant_id', 'reservation_id')} for v in run['variants']]}
    manifest = {k: job[k] for k in ('schema_version', 'idea_id', 'source_sha256', 'campaign_url', 'caption_mode', 'lease_fence')}
    manifest['variants'] = []
    if 'variant_count' in run:
        job['variant_count']=manifest['variant_count']=run['variant_count']
    for index, reserved in enumerate(job['variants']):
        video = directory / f'video-{index}.mp4'; shutil.copyfile(f.media.videos[index], video)
        image = directory / f'image-{index}.png'; image.write_bytes(png((index * 30, 40, 150)))
        v = {**reserved, 'video_path': str(video), 'video_sha256': sha(video.read_bytes()),
             'image_path': str(image), 'image_sha256': sha(image.read_bytes()),
             'caption': campaign_url, 'concept_id': 'synthetic-scene-' + str(index), 'qa': {}}
        binding = {'idea_id': run['idea_id'], **reserved, 'source_sha256': run['artwork_sha256'],
                   'video_sha256': v['video_sha256'], 'image_sha256': v['image_sha256'],
                   'campaign_url': campaign_url, 'lease_fence': 1}
        if 'audio_assignments' in run:
            v['audio_source_sha256']=run['audio_assignments'][index]['source_sha256']
        checks = {
            'visual': {'original_artwork_compared': True, 'complete_print_visible': True, 'frames_reviewed': [0, 60, 120, 180, 239]},
            'audio': {'listened_to_final_mix': True, 'cuts_checked': True, 'loudness_checked': True, 'clipping_checked': True},
            'technical': {'width': 1080, 'height': 1920, 'fps': 30, 'frames': 240, 'duration_seconds': 8,
                'video_codec': 'h264', 'audio_codec': 'aac', 'full_decode_passed': True, 'all_frames_artwork_visible': True,
                'subpixel_zoom_verified': True, 'minimum_margin_fraction': .04, 'motion_rms_pixels': .05},
            'distinct_variant': {'independent_source_photo': True, 'new_scene_reviewed': True, 'concept_id': v['concept_id']},
            'caption': {'exact_product_link': True, 'caption_sha256': sha(campaign_url.encode()),
                        'campaign_url': campaign_url, 'idea_id': run['idea_id']},
        }
        for kind in f.bridge.QA_NAMES:
            approved_mix=run.get('approved_audio_mixes',{}).get(v.get('audio_source_sha256')) if kind=='audio' else None
            approved_method=kind=='audio' and run.get('audio_mix_policy')=='human_approved_exact_pcm_v1'
            proof = {'schema_version': '1.0', 'kind': kind, 'status': 'passed',
                'review_method': 'approved_mix_hash_match' if approved_method else f.bridge.METHODS[kind],
                'reviewer': 'SIMULATED TEST ATTESTATION ONLY',
                'reviewed_at': '2026-01-01T00:00:00Z',
                'binding': {**binding,'audio_source_sha256':v['audio_source_sha256']} if kind=='audio' and 'audio_source_sha256' in v else binding,
                'checks': checks[kind]}
            if approved_method:
                proof['binding'].update(approval_id=approved_mix['approval_id'],actual_pcm_sha256=approved_mix['pcm_sha256'],
                    approved_pcm_sha256=approved_mix['pcm_sha256'])
                proof['checks']={'exact_pcm_match':True}
            path = directory / f'qa-{index}-{kind}.json'
            v['qa'][kind] = {'path': str(path), 'sha256': save(path, proof)}
            v[kind + '_verified'] = True
        manifest['variants'].append(v)
    f.job, f.manifest = job, manifest
    save(directory / 'job.json', job); save(directory / 'manifest.json', manifest)
    f.bridge.export(str(directory / 'job.json'), str(directory / 'manifest.json'),
                    str(directory / 'result.json'), root=directory)
    worker = {'schema_version': '1.0', 'run_id': run['id'], 'idea_id': run['idea_id'],
        'artwork_sha256': run['artwork_sha256'], 'lease_fence': 1, 'status': 'ready', 'reason': 'ready',
        'job_path': str(directory / 'job.json'), 'manifest_path': str(directory / 'manifest.json'),
        'result_path': str(directory / 'result.json'),
        'campaign': {'kind': 'existing' if existing else 'new', 'url': campaign_url,
            'source_url': None if existing else BLACK_SOURCE_URL if black else SOURCE_URL,
            'shirt_color': 'black' if black else None,
            'evidence': {'observed_at': '2026-01-01T00:00:00Z', 'artwork_matches': True,
                         'quote_matches': True, 'public_url': campaign_url}}}
    save(directory / 'worker-output.json', worker)
    f.worker = worker


def accept(f):
    return asyncio.run(f.consumer.accept(f.run, f.directory))


def approved(f):
    with f.store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM idea_pipeline_approved_videos')]


def held(f):
    response = accept(f)
    assert not response['accepted'] and response['status'] == 'needs_review'
    assert 'PRIVATE' not in json.dumps(response)
    assert not list(f.destination.iterdir()) and not approved(f)
    assert f.pipeline.idea(f.idea['id'])['campaign_state'] == 'prepared'
    return response


def test_ready_actual_media_is_approved_and_exposed_without_new_publisher(f):
    before = [path.read_bytes() for path in f.media.videos]
    response = accept(f)
    assert response['accepted'] and response['approved'] == response['copied'] == 7
    assert response['verification'] == 'worker_qa_approved' and f.source.requests == 1
    rows = approved(f)
    assert len(rows) == 7 and all(row['status'] == 'ready' and row['source_revision'] == 2 for row in rows)
    for row in rows:
        final = f.destination / row['destination_name']
        assert sha(final.read_bytes()) == row['video_sha256']
        assert final.with_suffix('.txt').read_text() == f.job['campaign_url']
    assert not f.store.reel_jobs()  # Existing source owns queue creation later.
    assert [path.read_bytes() for path in f.media.videos] == before
    assert all(v['status'] == 'metadata_registered' for v in f.pipeline.idea(f.idea['id'])['variants'])


def set_dynamic_count(f,count=2,trigger='automatic_idea_batch'):
    f.run.update(variant_count=count,variants=f.run['variants'][:count],trigger_mode=trigger)
    if trigger=='automatic_idea_batch':
        f.run['automatic_batch']={'batch_id':str(uuid4()),'settings_revision':1,'day_key':'2030-01-01'}
    with f.store.connect() as db:
        row=db.execute('SELECT payload FROM idea_manual_runs WHERE id=?',(f.run['id'],)).fetchone()
        payload=json.loads(row['payload'])
        for key in ('variant_count','variants','trigger_mode','automatic_batch','audio_assignments','workflow','audio_mix_policy','approved_audio_mixes'):
            if key in f.run:payload[key]=f.run[key]
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=? WHERE id=?',
                   (json.dumps(payload),sha(module._canonical(payload)),f.run['id']))
    (f.directory/'result.json').unlink()
    make_output(f)


@pytest.mark.parametrize('count',[1,2,5])
def test_dynamic_count_handoff_approves_only_the_frozen_prefix_and_replays(f,count):
    set_dynamic_count(f,count)
    response=accept(f)
    assert response['accepted'] and response['approved']==response['copied']==count
    assert len(approved(f))==count and len(list(f.destination.glob('*.mp4')))==count
    with f.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM idea_pipeline_variants').fetchone()[0]==7
    public=f.pipeline.idea(f.idea['id'])
    assert public['allocated_variant_count']==count and len(public['variants'])==count
    replay=accept(f)
    assert replay['accepted'] and replay['approved']==count and replay['copied']==0


def iteration_output(f):
    """Synthetic fresh iteration keeps the seven historical variant slots."""
    iteration={'id':str(uuid4()),'number':2}
    variants=[{'variant_id':str(uuid4()),'reservation_id':str(uuid4()),'ordinal':n} for n in (1,2)]
    f.run.update(production_iteration=iteration,variant_count=2,variants=variants)
    with f.store.connect() as db:
        db.execute('INSERT INTO idea_production_iterations VALUES(?,?,?,?,?,?,?)',
            (iteration['id'],f.run['idea_id'],f.run['request_id'],f.run['artwork_sha256'],2,2,'2030-01-01'))
        for value in variants:
            db.execute('INSERT INTO idea_pipeline_variants(id,idea_id,ordinal,reservation_id) VALUES(?,?,?,?)',
                (value['variant_id'],f.run['idea_id'],20+value['ordinal'],value['reservation_id']))
            db.execute('INSERT INTO idea_production_iteration_variants VALUES(?,?,?)',
                (iteration['id'],value['ordinal'],value['variant_id']))
        payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs WHERE id=?',(f.run['id'],)).fetchone()[0])
        payload.update(production_iteration=iteration,variant_count=2,variants=variants)
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=? WHERE id=?',
            (json.dumps(payload),sha(module._canonical(payload)),f.run['id']))
    (f.directory/'result.json').unlink();make_output(f)
    return iteration


def test_iteration_handoff_uses_fresh_exact_mapping_and_replay_preserves_historical_slots(f):
    old_ids=[value['id'] for value in f.idea['variants']]
    iteration_output(f)
    response=accept(f)
    assert response['accepted'] and response['approved']==response['copied']==2
    assert {value['variant_id'] for value in approved(f)}=={value['variant_id'] for value in f.run['variants']}
    with f.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM idea_pipeline_variants').fetchone()[0]==9
        assert all(db.execute('SELECT status FROM idea_pipeline_variants WHERE id=?',(value,)).fetchone()[0]=='planned' for value in old_ids)
    replay=accept(f)
    assert replay['accepted'] and replay['approved']==2 and replay['copied']==0


@pytest.mark.parametrize('change',['idea','request','source','count','number','missing_mapping','order','reservation',
                                 'foreign_variant','metadata_missing','metadata_null','legacy_prefix'])
def test_iteration_consumer_rejects_changed_authority_before_approval_or_exposure(f,change):
    iteration=iteration_output(f)
    with f.store.connect() as db:
        foreign_idea=str(uuid4())
        if change in ('idea','foreign_variant'):
            db.execute('INSERT INTO idea_pipeline_ideas(id,artwork_sha256,filename,created_at) VALUES(?,?,?,?)',
                (foreign_idea,'e'*64,'other-synthetic.png','2030-01-01'))
        if change in ('idea','request','source','count','number'):
            key={'idea':'idea_id','request':'request_id','source':'artwork_sha256','count':'variant_count','number':'number'}[change]
            value='f'*64 if change=='source' else 1 if change=='count' else 3 if change=='number' else foreign_idea if change=='idea' else str(uuid4())
            db.execute('UPDATE idea_production_iterations SET '+key+'=?',(value,))
        elif change=='missing_mapping':db.execute('DELETE FROM idea_production_iteration_variants WHERE ordinal=2')
        elif change=='order':db.execute('UPDATE idea_production_iteration_variants SET ordinal=4 WHERE ordinal=2')
        elif change=='reservation':db.execute('UPDATE idea_pipeline_variants SET reservation_id=? WHERE id=?',(str(uuid4()),f.run['variants'][0]['variant_id']))
        elif change=='foreign_variant':db.execute('UPDATE idea_pipeline_variants SET idea_id=? WHERE id=?',(foreign_idea,f.run['variants'][0]['variant_id']))
    if change=='metadata_missing':f.run.pop('production_iteration')
    elif change=='metadata_null':
        f.run['production_iteration']=None
        with f.store.connect() as db:
            payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs').fetchone()[0]);payload['production_iteration']=None
            db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=?',(json.dumps(payload),sha(module._canonical(payload))))
    elif change=='legacy_prefix':f.run['variants']=[{'variant_id':v['id'],'reservation_id':v['reservation_id'],'ordinal':v['ordinal']} for v in f.idea['variants'][:2]]
    held(f)


def test_iteration_membership_is_rechecked_at_commit_boundary(f):
    iteration=iteration_output(f)
    f.consumer._run_guard(f.run)
    with f.store.connect() as db:
        db.execute('DELETE FROM idea_production_iteration_variants WHERE iteration_id=? AND ordinal=2',(iteration['id'],))
        with pytest.raises(module._Hold):f.consumer._run_guard(f.run,db=db)
    assert not approved(f) and not list(f.destination.iterdir())


@pytest.mark.parametrize('ordinal',[True,1.0,'1'])
def test_iteration_consumer_rejects_noninteger_local_ordinal_even_if_payload_hash_matches(f,ordinal):
    iteration_output(f)
    f.run['variants'][0]['ordinal']=ordinal
    with f.store.connect() as db:
        payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs').fetchone()[0])
        payload['variants']=f.run['variants']
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=?',(json.dumps(payload),sha(module._canonical(payload))))
    held(f)


def test_legacy_consumer_slots_remain_resolvable_after_new_iteration_is_appended(f):
    legacy=dict(f.run);expected=list(legacy['variants'])
    iteration_output(f)
    assert f.consumer._canonical_variants(legacy)==expected
    assert f.consumer._canonical_variants(f.run)==f.run['variants']
    assert not {v['variant_id'] for v in expected}&{v['variant_id'] for v in f.run['variants']}


@pytest.mark.parametrize('change',['count','trigger','automatic_batch','job_count','manifest_count'])
def test_dynamic_consumer_rejects_worker_changes_to_frozen_allocation(f,change):
    set_dynamic_count(f,2)
    if change=='count':f.run['variant_count']=1
    elif change=='trigger':f.run['trigger_mode']='manual_web_run_idea'
    elif change=='automatic_batch':f.run['automatic_batch']={**f.run['automatic_batch'],'settings_revision':2}
    elif change=='job_count':f.job['variant_count']=3;save(f.directory/'job.json',f.job)
    else:f.manifest['variant_count']=3;save(f.directory/'manifest.json',f.manifest)
    held(f)


def test_frozen_audio_assignment_is_verified_by_ordinal_without_implying_listening(f):
    f.run['audio_assignments']=[{'source_sha256':'a'*64},{'source_sha256':'b'*64}]
    set_dynamic_count(f,2)
    assert accept(f)['accepted'] and len(approved(f))==2


@pytest.mark.parametrize('change',['missing','different'])
def test_consumer_rejects_unassigned_audio_even_with_otherwise_valid_worker_evidence(f,change):
    f.run['audio_assignments']=[{'source_sha256':'a'*64},{'source_sha256':'b'*64}]
    set_dynamic_count(f,2)
    variant=f.manifest['variants'][0]
    proof_path=Path(variant['qa']['audio']['path'])
    proof=json.loads(proof_path.read_text())
    if change=='missing':
        variant.pop('audio_source_sha256');proof['binding'].pop('audio_source_sha256')
    else:
        variant['audio_source_sha256']=proof['binding']['audio_source_sha256']='c'*64
    variant['qa']['audio']['sha256']=save(proof_path,proof)
    save(f.directory/'manifest.json',f.manifest)
    (f.directory/'result.json').unlink()
    f.bridge.export(str(f.directory/'job.json'),str(f.directory/'manifest.json'),str(f.directory/'result.json'),root=f.directory)
    held(f)


def mix_proof(source='a'*64):
    return {'approval_id':'b'*64,'pcm_sha256':'c'*64,'source_sha256':source,
        'render_signature':'d'*64,'sample_rate':44100,'channels':2,'duration_seconds':8.0}


def audio_variant():
    return {'video_path':'SYNTHETIC_FIXTURE.mp4','audio_source_sha256':'a'*64,
        'qa_evidence':{'audio':{'review_method':'approved_mix_hash_match','status':'passed',
            'approval_id':'b'*64,'actual_pcm_sha256':'c'*64,'approved_pcm_sha256':'c'*64}}}


def test_consumer_rechecks_registry_and_final_file_independently():
    consumer=object.__new__(IdeaRunResults)
    proof=mix_proof();calls=[]
    def verify(video,source_sha,approval_id=None):
        calls.append((video,source_sha,approval_id));return proof
    consumer.audio_approvals=SimpleNamespace(verify_video=verify)
    run={'audio_mix_policy':'human_approved_exact_pcm_v1','approved_audio_mixes':{'a'*64:proof}}
    consumer._audio_mix_guard(run,audio_variant())
    assert calls==[('SYNTHETIC_FIXTURE.mp4','a'*64,'b'*64)]


@pytest.mark.parametrize('change',['registry_missing','revoked','pcm','approval_id','profile','format','snapshot_missing','listening'])
def test_new_audio_policy_fails_closed_on_unapproved_or_changed_mix(change):
    consumer=object.__new__(IdeaRunResults)
    proof=mix_proof();variant=audio_variant()
    run={'audio_mix_policy':'human_approved_exact_pcm_v1','approved_audio_mixes':{'a'*64:dict(proof)}}
    def verify(*_args,**_kwargs):
        if change=='revoked':raise ValueError('SIMULATED REVOKED APPROVAL')
        return proof
    consumer.audio_approvals=SimpleNamespace(verify_video=verify)
    if change=='registry_missing':consumer.audio_approvals=None
    elif change=='pcm':variant['qa_evidence']['audio']['actual_pcm_sha256']='e'*64
    elif change=='approval_id':variant['qa_evidence']['audio']['approval_id']='e'*64
    elif change=='profile':proof['render_signature']='e'*64
    elif change=='format':proof['channels']=1
    elif change=='snapshot_missing':run['approved_audio_mixes']={}
    elif change=='listening':variant['qa_evidence']['audio']['review_method']='listened_to_final_mix'
    with pytest.raises((module._Hold,ValueError)):consumer._audio_mix_guard(run,variant)


def test_complete_handoff_uses_simulated_registry_approval_without_listening_claim(f):
    f.run['audio_mix_policy']='human_approved_exact_pcm_v1'
    f.run['audio_assignments']=[{'source_sha256':'a'*64},{'source_sha256':'e'*64}]
    first=mix_proof();second={**mix_proof('e'*64),'approval_id':'f'*64,'pcm_sha256':'1'*64}
    snapshots=f.run['approved_audio_mixes']={'a'*64:first,'e'*64:second}
    set_dynamic_count(f,2)
    calls=[]
    def verify(video,source_sha,approval_id=None):
        # This mock represents the separately tested trusted decoder/registry.
        # It never approves real media or writes a production approval row.
        assert Path(video).parent==f.directory
        assert approval_id==snapshots[source_sha]['approval_id']
        calls.append((video,source_sha,approval_id));return snapshots[source_sha]
    f.consumer.audio_approvals=SimpleNamespace(verify_video=verify)
    assert accept(f)['accepted'] and len(approved(f))==2 and len(calls)==2
    for variant in f.manifest['variants']:
        document=json.loads(Path(variant['qa']['audio']['path']).read_text())
        assert document['review_method']=='approved_mix_hash_match'
        assert document['checks']=={'exact_pcm_match':True}


def test_idempotent_callback_does_not_copy_or_register_again(f):
    assert accept(f)['accepted']
    stats = {path.name: path.stat().st_mtime_ns for path in f.destination.iterdir()}
    second = accept(f)
    assert second['accepted'] and second['copied'] == 0
    assert len(approved(f)) == 7
    assert stats == {path.name: path.stat().st_mtime_ns for path in f.destination.iterdir()}


def test_verified_existing_campaign_keeps_original_source_unknown(f):
    f.worker['campaign']['kind'] = 'existing'; f.worker['campaign']['source_url'] = None
    save(f.directory / 'worker-output.json', f.worker)
    assert accept(f)['accepted']
    campaign = f.pipeline.idea(f.idea['id'])['campaign']
    assert campaign['kind'] == 'existing' and campaign['campaign_source'] is None
    assert campaign['verification'] == 'worker_attested'


@pytest.mark.parametrize('change', ['fence', 'cancel', 'status', 'payload_hash'])
def test_stale_or_cancelled_claim_never_accepts(f, change):
    with f.store.connect() as db:
        updates = {'fence': ('fence', 2), 'cancel': ('cancel_requested', 1),
                   'status': ('status', 'cancelled'), 'payload_hash': ('payload_hash', 'a' * 64)}
        field, value = updates[change]
        db.execute('UPDATE idea_manual_runs SET ' + field + '=? WHERE id=?', (value, f.run['id']))
    held(f)


@pytest.mark.parametrize('change', ['revision', 'paused', 'timezone', 'page_count'])
def test_source_changes_hold_all_work(f, change):
    if change == 'revision': f.source.settings['revision'] = 3
    if change == 'paused': f.source.settings['enabled'] = False
    if change == 'timezone': f.source.settings['timezone'] = 'Asia/Ho_Chi_Minh'
    if change == 'page_count': f.source.settings['page_ids'] = f.source.settings['page_ids'][:-1]
    held(f)


@pytest.mark.parametrize('field,value', [('run_id', str(uuid4())), ('idea_id', str(uuid4())),
    ('artwork_sha256', 'b' * 64), ('lease_fence', 2), ('extra', 'PRIVATE TASK'),
    ('status', 'needs_review'), ('reason', 'qa_pending')])
def test_worker_closed_schema_and_bindings(f, field, value):
    f.worker[field] = value
    save(f.directory / 'worker-output.json', f.worker)
    held(f)


def test_other_run_or_secret_path_is_not_consumed(f):
    f.worker['job_path'] = str(f.root / 'auth' / 'private.json')
    save(f.directory / 'worker-output.json', f.worker)
    held(f)


@pytest.mark.parametrize('change', ['new_no_source', 'other_domain', 'black_without_color', 'not_verified'])
def test_campaign_provenance_is_explicit(f, change):
    campaign = f.worker['campaign']
    if change == 'new_no_source': campaign['source_url'] = None
    if change == 'other_domain': campaign['url'] = 'https://wildy05.com/campaign/example'
    if change == 'black_without_color': campaign['source_url'] = BLACK_SOURCE_URL
    if change == 'not_verified': campaign['evidence']['artwork_matches'] = False
    save(f.directory / 'worker-output.json', f.worker)
    held(f)


def test_forged_export_cannot_bypass_rebuild(f):
    result = json.loads((f.directory / 'result.json').read_text())
    result['variants'][0]['video_sha256'] = 'a' * 64
    save(f.directory / 'result.json', result)
    held(f)


def test_changed_actual_media_is_not_accepted(f):
    Path(f.manifest['variants'][0]['video_path']).write_bytes(b'PRIVATE invalid movie')
    held(f)


def test_missing_audio_evidence_holds_all_seven(f):
    f.manifest['variants'][6]['audio_verified'] = False
    save(f.directory / 'manifest.json', f.manifest)
    # Export now reflects pending state; callback may not blindly trust exit0.
    f.bridge.export(str(f.directory / 'job.json'), str(f.directory / 'manifest.json'),
        str(f.directory / 'result.json'), root=f.directory)
    held(f)


def test_decoder_failure_is_not_replaced_by_worker_qa_claim(f, monkeypatch):
    monkeypatch.setattr(f.consumer, '_decode', lambda path: (_ for _ in ()).throw(ValueError('PRIVATE decoder failure')))
    held(f)


def test_duplicate_token_history_blocks_before_campaign_or_copy(f, monkeypatch):
    # Skip only repeated independent decode for this ledger-specific test.
    monkeypatch.setattr(f.consumer, '_decode', lambda path: None)
    v = f.manifest['variants'][0]
    asset_id = uuid4().hex
    f.store.reel_asset_save({'id': asset_id, 'name': 'old.mp4', 'path': 'private', 'size': 20,
        'sha256': v['video_sha256'], 'mime': 'video/mp4'})
    f.store.reel_create({'request_id': str(uuid4()), 'page_id': f.source.settings['page_ids'][0],
        'page_name': 'fixture', 'connection_id': f.source.settings['connection_id'], 'connection_label': 'fixture',
        'token_revision': 1, 'asset_id': asset_id, 'asset_name': 'old.mp4', 'caption': 'old',
        'scheduled_at': datetime.now(timezone.utc).isoformat(), 'scheduled_local': '2026-11-02T06:00',
        'timezone': 'America/Los_Angeles', 'fold': 0})
    held(f)


def test_interrupted_copy_marks_approvals_review_then_exact_resume(f, monkeypatch):
    monkeypatch.setattr(f.consumer, '_decode', lambda path: None)
    original = f.consumer._expose
    calls = [0]
    def fail_after_one(*args):
        calls[0] += 1
        if calls[0] == 2: raise OSError('PRIVATE simulated interruption')
        return original(*args)
    monkeypatch.setattr(f.consumer, '_expose', fail_after_one)
    first = accept(f)
    assert not first['accepted'] and first['approved'] == 7 and first['copied'] == 1
    assert all(row['status'] == 'needs_review' for row in approved(f))
    monkeypatch.setattr(f.consumer, '_expose', original)
    second = accept(f)
    assert second['accepted'] and second['copied'] == 6 and len(list(f.destination.glob('*.mp4'))) == 7


def test_preexisting_destination_is_not_overwritten(f, monkeypatch):
    monkeypatch.setattr(f.consumer, '_decode', lambda path: None)
    path = f.destination / ('idea-' + f.run['variants'][0]['variant_id'] + '.mp4')
    path.write_bytes(b'UNRELATED PRIVATE FILE')
    response = accept(f)
    assert not response['accepted'] and path.read_bytes() == b'UNRELATED PRIVATE FILE'
    assert not approved(f) and not path.with_suffix('.txt').exists()
    assert f.pipeline.idea(f.idea['id'])['campaign_state'] == 'prepared'


def test_missing_optional_bridge_keeps_dashboard_alive_and_holds_result(f, monkeypatch):
    monkeypatch.setattr(module, '_load_bridge', lambda: (_ for _ in ()).throw(FileNotFoundError('PRIVATE PATH')))
    f.consumer = IdeaRunResults(f.manager, f.pipeline, f.source,
        runs_directory=f.root, ffmpeg_path=f.media.ffmpeg)
    held(f)


def test_cancellation_waits_for_thread_guard_and_prevents_orphan_writes(f, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    reached_write = []
    def blocked_validation(run, directory):
        started.set()
        try:
            assert release.wait(timeout=5)
            f.consumer._run_guard(run)
            reached_write.append(True)
            raise AssertionError('Cancelled worker must not reach writes')
        finally:
            finished.set()
    monkeypatch.setattr(f.consumer, '_validate', blocked_validation)
    async def scenario():
        task = asyncio.create_task(f.consumer.accept(f.run, f.directory))
        assert await asyncio.to_thread(started.wait, 5)
        # The runner must persist its stop before cancelling the callback.
        with f.store.connect() as db:
            db.execute("UPDATE idea_manual_runs SET status='needs_review',cancel_requested=1 WHERE id=?", (f.run['id'],))
        task.cancel()
        for _ in range(10):
            if f.consumer.cancel_event.is_set():
                break
            await asyncio.sleep(.001)
        assert f.consumer.cancel_event.is_set()
        assert not task.done() and not finished.is_set()
        # A repeated shutdown cancellation must not abandon the thread either.
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    asyncio.run(scenario())
    assert not reached_write and not approved(f) and not list(f.destination.iterdir())
    assert f.source.requests == 0


def test_cancellation_stops_only_the_owned_decoder_process(tmp_path, monkeypatch):
    decoder = tmp_path / 'synthetic-ffmpeg.exe'; decoder.touch()
    consumer = object.__new__(IdeaRunResults)
    consumer.ffmpeg_path, consumer.cancel_event = decoder, threading.Event()
    consumer.cancel_event.set()
    class Process:
        def __init__(self):
            self.stdout, self.stderr = BytesIO(), BytesIO()
            self.killed, self.waited = False, False
        def poll(self):
            return -9 if self.killed else None
        def kill(self):
            self.killed = True
        def wait(self, timeout):
            self.waited = True
            return -9
    owned = Process()
    calls = []
    def launch(command, **kwargs):
        calls.append(command)
        assert command[0] == str(decoder) and kwargs['shell'] is False
        return owned
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    with pytest.raises(module._Hold):
        consumer._decode(tmp_path / 'synthetic.mp4')
    assert len(calls) == 1 and owned.killed and owned.waited
