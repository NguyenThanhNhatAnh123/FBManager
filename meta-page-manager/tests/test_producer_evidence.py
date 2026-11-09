"""Synthetic evidence/DB fixtures only; no real generation, media or approval."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager import producer_evidence as module
from page_manager.producer_evidence import ProducerEvidence
from page_manager.idea_workspace import VIDEO_WORKFLOW_FILES,video_workflow_configuration

PRIVATE='PRIVATE TOKEN OR PATH MUST NOT BE PUBLIC'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def artifact(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return {'path':str(path),'sha256':sha(raw)}


def save(path,value):
    raw=json.dumps(value,ensure_ascii=False).encode('utf-8');path.write_bytes(raw);return sha(raw)


@pytest.fixture
def f(tmp_path):
    project=tmp_path/'page-producer';manager=tmp_path/'manager';media=tmp_path/'media'
    manager.mkdir();project.mkdir();media.mkdir();(manager/'.runtime').mkdir();(project/'.runtime').mkdir()
    for name in VIDEO_WORKFLOW_FILES:
        file=project/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('INERT WORKFLOW FIXTURE '+name)
    config=video_workflow_configuration(workflow_directory=project)
    workflow={'version':config['workflow_version'],'sha256':config['workflow_sha256']}
    clock=[datetime(2030,1,1,tzinfo=timezone.utc)];ticks=[0.0]
    source=artifact(media/'ideas/source.png',b'INERT SOURCE ARTWORK')
    image=artifact(project/'.runtime/capability-probe/image.png',b'INERT NATIVE PHOTO')
    video=artifact(project/'.runtime/capability-probe/video.mp4',b'INERT CAPABILITY SAMPLE VIDEO')
    native={'schema_version':1,'kind':'native_image_generation','provider':'codex','tool':'image_gen',
        'checked_at':clock[0].isoformat(),'workflow_sha256':workflow['sha256'],
        'source_sha256':source['sha256'],'image_sha256':image['sha256']}
    native_path=project/'.runtime/native-generation.json'
    main={'schema_version':1,'kind':'producer_capability_evidence','provider':'codex',
        'checked_at':clock[0].isoformat(),'workflow':workflow,'source':source,'image':image,'video':video,
        'native_generation':{'path':str(native_path),'sha256':save(native_path,native)},'production':None}
    path=manager/'.runtime/producer-capability-evidence.json';save(path,main)
    database=manager/'fixture.sqlite3'
    @contextmanager
    def connect():
        db=sqlite3.connect(database);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        finally:db.close()
    store=SimpleNamespace(connect=connect)
    service=ProducerEvidence(store,manager,workflow_directory=project,clock=lambda:clock[0],monotonic=lambda:ticks[0])
    return SimpleNamespace(project=project,manager=manager,media=media,clock=clock,ticks=ticks,source=source,
        image=image,video=video,native=native,native_path=native_path,main=main,path=path,store=store,service=service)


def record_production(f,*,asset_directory=None):
    run_id,idea_id=str(uuid4()),str(uuid4());pairs=[];hashes=[];rows=[]
    for ordinal in (1,2):
        variant,reservation,asset_id=str(uuid4()),str(uuid4()),str(uuid4())
        photo=artifact(f.project/f'.runtime/run/image-{ordinal}.png',f'INERT RUN PHOTO {ordinal}'.encode())
        raw=f'INERT APPROVED VIDEO {ordinal}'.encode()
        produced=artifact(f.project/f'.runtime/run/video-{ordinal}.mp4',raw)
        exposed=artifact(f.media/f'video/idea-{variant}.mp4',raw)
        asset=artifact((asset_directory or f.manager/'media/reels')/f'{asset_id}.mp4',raw)
        pairs.append({'variant_id':variant,'reservation_id':reservation,'ordinal':ordinal});hashes.append(produced['sha256'])
        rows.append((variant,reservation,asset_id,ordinal,photo,produced,exposed,asset))
    payload={'workflow':f.main['workflow'],'variant_count':2,'variants':pairs,'source_revision':3}
    caption='https://blueox179.com/campaign/synthetic-fixture'
    with f.store.connect() as db:
        db.executescript('''CREATE TABLE idea_manual_runs(id TEXT,idea_id TEXT,artwork_sha256 TEXT,payload TEXT,payload_hash TEXT,status TEXT,cancel_requested INTEGER);
            CREATE TABLE idea_pipeline_ideas(id TEXT,artwork_sha256 TEXT,campaign_state TEXT);
            CREATE TABLE idea_pipeline_variants(id TEXT,idea_id TEXT,reservation_id TEXT,ordinal INTEGER,status TEXT,video_sha256 TEXT);
            CREATE TABLE idea_pipeline_approved_videos(run_id TEXT,idea_id TEXT,variant_id TEXT,reservation_id TEXT,video_sha256 TEXT,image_sha256 TEXT,image_path TEXT,source_path TEXT,caption TEXT,status TEXT,source_revision INTEGER);
            CREATE TABLE video_source_items(id TEXT,sha256 TEXT,path TEXT,asset_id TEXT,job_id INTEGER);
            CREATE TABLE reel_assets(id TEXT,sha256 TEXT,path TEXT,size INTEGER);
            CREATE TABLE reel_jobs(id INTEGER,request_id TEXT,asset_id TEXT,caption TEXT);''')
        db.execute('INSERT INTO idea_manual_runs VALUES(?,?,?,?,?,?,?)',(run_id,idea_id,f.source['sha256'],json.dumps(payload),sha(module._canonical(payload)),'completed',0))
        db.execute('INSERT INTO idea_pipeline_ideas VALUES(?,?,?)',(idea_id,f.source['sha256'],'verified'))
        for variant,reservation,asset_id,ordinal,photo,produced,exposed,asset in rows:
            db.execute('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?,?,?)',(variant,idea_id,reservation,ordinal,'metadata_registered',produced['sha256']))
            db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?,?,?,?,?,?,?,?)',(run_id,idea_id,variant,reservation,produced['sha256'],photo['sha256'],photo['path'],produced['path'],caption,'ready',3))
            db.execute('INSERT INTO video_source_items VALUES(?,?,?,?,?)',(reservation,produced['sha256'],exposed['path'],asset_id,ordinal))
            db.execute('INSERT INTO reel_assets VALUES(?,?,?,?)',(asset_id,produced['sha256'],asset['path'],Path(asset['path']).stat().st_size))
            db.execute('INSERT INTO reel_jobs VALUES(?,?,?,?)',(ordinal,reservation,asset_id,caption))
    f.main['production']={'run_id':run_id,'variant_count':2,'video_sha256s':hashes};save(f.path,f.main)
    return run_id


def test_missing_evidence_never_creates_runtime_directory(f):
    other=f.manager/'not-created';service=ProducerEvidence(f.store,other,workflow_directory=f.project)
    assert service.snapshot()=={'image_verified':False,'production_verified':False,'checked_at':None,'last_run_id':None,'reason':'not_recorded'}
    assert not other.exists()


def test_actual_bound_image_probe_is_separate_from_completed_production(f):
    value=f.service.snapshot()
    assert value=={'image_verified':True,'production_verified':False,'checked_at':f.clock[0].isoformat(),
        'last_run_id':None,'reason':'production_not_recorded'}
    assert str(f.project) not in json.dumps(value)


def test_completed_own_run_assets_and_queue_bindings_verify_even_with_a_different_sample_clip(f):
    run_id=record_production(f)
    assert f.video['sha256'] not in f.main['production']['video_sha256s']
    value=f.service.snapshot()
    assert value['image_verified'] and value['production_verified'] and value['last_run_id']==run_id
    assert value['reason']=='verified'


def test_actual_manager_reel_asset_directory_verifies_and_tampering_busts_cache(f):
    assets=f.manager/'reel-assets'
    run_id=record_production(f,asset_directory=assets)
    value=f.service.snapshot()
    assert value['image_verified'] and value['production_verified'] and value['last_run_id']==run_id
    assert value['reason']=='verified' and str(assets) not in json.dumps(value)
    next(assets.glob('*.mp4')).write_bytes(b'CHANGED MANAGER-OWNED SYNTHETIC ASSET')
    value=f.service.snapshot()
    assert value['image_verified'] and not value['production_verified']
    assert value['reason']=='production_assets_unverified' and value['last_run_id'] is None


def test_worker_selected_lookalike_asset_directory_is_not_trusted(f):
    record_production(f,asset_directory=f.manager/'worker-area/reel-assets')
    value=f.service.snapshot()
    assert value['image_verified'] and not value['production_verified']
    assert value['reason']=='production_assets_unverified'


@pytest.mark.parametrize('change',['extra','schema_bool','provider','missing_video','future','stale','workflow','bad_sha','unknown_production'])
def test_malformed_future_stale_or_workflow_mismatched_evidence_fails_closed(f,change):
    if change=='extra':f.main['private_cookie']=PRIVATE
    elif change=='schema_bool':f.main['schema_version']=True
    elif change=='provider':f.main['provider']='claude'
    elif change=='missing_video':f.main.pop('video')
    elif change=='future':f.main['checked_at']=(f.clock[0]+timedelta(seconds=1)).isoformat()
    elif change=='stale':f.main['checked_at']=(f.clock[0]-timedelta(seconds=module.TTL_SECONDS+1)).isoformat()
    elif change=='workflow':f.main['workflow']['sha256']='0'*64
    elif change=='bad_sha':f.main['source']['sha256']='PRIVATE INVALID HASH'
    else:f.main['production']={'private':PRIVATE}
    save(f.path,f.main);value=f.service.snapshot()
    assert value['production_verified'] is False
    if change!='unknown_production':assert value['image_verified'] is False
    assert PRIVATE not in json.dumps(value)


@pytest.mark.parametrize('change',['extra','tool','image','source','future','outside_runtime'])
def test_native_provenance_must_be_closed_bound_and_inside_project_runtime(f,change):
    if change=='extra':f.native['raw_output']=PRIVATE
    elif change=='tool':f.native['tool']='features_list_flag'
    elif change=='image':f.native['image_sha256']='0'*64
    elif change=='source':f.native['source_sha256']='0'*64
    elif change=='future':f.native['checked_at']=(f.clock[0]+timedelta(seconds=1)).isoformat()
    else:
        f.native_path=f.media/'misplaced-provenance.json';f.main['native_generation']['path']=str(f.native_path)
    f.main['native_generation']['sha256']=save(f.native_path,f.native);save(f.path,f.main)
    assert not f.service.snapshot()['image_verified']


@pytest.mark.parametrize('field',['source','image','video'])
def test_artifact_changes_invalidate_cache_immediately_without_rehashing_unchanged_files(f,field,monkeypatch):
    calls=[];original=f.service._read
    monkeypatch.setattr(f.service,'_read',lambda *args:(calls.append(args[0]) or original(*args)))
    assert f.service.snapshot()['image_verified'];first=len(calls)
    assert f.service.snapshot()['image_verified'] and len(calls)==first
    Path(f.main[field]['path']).write_bytes(b'CHANGED INERT FIXTURE')
    assert not f.service.snapshot()['image_verified'] and len(calls)>first


def test_workflow_dependency_changes_bust_cache(f):
    assert f.service.snapshot()['image_verified']
    (f.project/VIDEO_WORKFLOW_FILES[0]).write_text('CHANGED WORKFLOW FIXTURE')
    assert f.service.snapshot()['reason']=='workflow_changed'


def test_ttl_and_future_clock_are_rechecked_even_during_cache_window(f):
    assert f.service.snapshot()['image_verified']
    f.clock[0]-=timedelta(seconds=1)
    assert not f.service.snapshot()['image_verified']
    f.clock[0]+=timedelta(seconds=module.TTL_SECONDS+2)
    assert not f.service.snapshot()['image_verified']


@pytest.mark.parametrize('change',['status','cancel','payload_hash','workflow','count','source','asset','source_item','reservation','image','approval_status'])
def test_production_requires_completed_current_workflow_and_canonical_registered_assets(f,change):
    record_production(f)
    with f.store.connect() as db:
        if change=='status':db.execute("UPDATE idea_manual_runs SET status='running'")
        elif change=='cancel':db.execute('UPDATE idea_manual_runs SET cancel_requested=1')
        elif change=='payload_hash':db.execute("UPDATE idea_manual_runs SET payload_hash='invalid'")
        elif change=='source':db.execute("UPDATE idea_manual_runs SET artwork_sha256=?",('0'*64,))
        elif change in ('workflow','count'):
            value=json.loads(db.execute('SELECT payload FROM idea_manual_runs').fetchone()[0])
            if change=='workflow':value['workflow']={'version':'old','sha256':'0'*64}
            else:value['variant_count']=1
            db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=?',(json.dumps(value),sha(module._canonical(value))))
        elif change=='asset':db.execute("UPDATE reel_assets SET sha256=?",('0'*64,))
        elif change=='source_item':db.execute("UPDATE video_source_items SET asset_id='missing'")
        elif change=='reservation':db.execute("UPDATE reel_jobs SET request_id=?",(str(uuid4()),))
        elif change=='image':db.execute("UPDATE idea_pipeline_approved_videos SET image_sha256=?",('0'*64,))
        else:db.execute("UPDATE idea_pipeline_approved_videos SET status='needs_review'")
    value=f.service.snapshot()
    assert value['image_verified'] and not value['production_verified'] and value['last_run_id'] is None
    assert value['reason'] in ('production_run_unverified','production_assets_unverified')


def test_database_changes_are_rechecked_after_the_bounded_cache_ttl(f):
    record_production(f);assert f.service.snapshot()['production_verified']
    with f.store.connect() as db:db.execute('UPDATE idea_manual_runs SET cancel_requested=1')
    f.ticks[0]+=module.CACHE_SECONDS+1
    assert not f.service.snapshot()['production_verified']


def test_duplicate_json_keys_and_large_evidence_do_not_escape_as_private_errors(f):
    f.path.write_text('{"schema_version":1,"schema_version":1}')
    assert f.service.snapshot()['reason']=='invalid_evidence'
    f.path.write_text(PRIVATE*module.MAX_JSON)
    assert not f.service.snapshot()['image_verified']
    assert PRIVATE not in json.dumps(f.service.snapshot())
