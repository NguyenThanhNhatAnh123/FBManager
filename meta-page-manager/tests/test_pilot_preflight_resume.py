"""Operator recovery fixtures only; no live ledger, CLI, uploader or scheduling."""
import hashlib
import json
from pathlib import Path

from filelock import FileLock
import pytest

from page_manager import pilot_preflight_resume as module
from page_manager.errors import AppError
from page_manager.idea_runs import _json
from test_idea_runs import fixture,add


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def held(fixture,monkeypatch):
    f=fixture;idea=add(f);run=f.runs.request(idea['id'],'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',variant_count=2)
    with f.store.connect() as db:db.execute("UPDATE idea_manual_runs SET status='preparing' WHERE id=?",(run['id'],))
    folder=f.runs._prepare(f.runs._row(run['id']))
    row=f.runs._row(run['id']);payload=json.loads(row['payload']);payload['workflow']={'version':'fixture-v3','sha256':'a'*64}
    encoded=_json(payload);payload_sha=hashlib.sha256(encoded.encode()).hexdigest()
    data=json.loads((folder/'run-input.json').read_text());data['workflow']=payload['workflow']
    (folder/'run-input.json').write_text(json.dumps(data),encoding='utf-8')
    output={'run_id':run['id'],'idea_id':idea['id'],'artwork_sha256':idea['artwork_sha256'],'lease_fence':1,
        'status':'needs_review','campaign':None,'reason':'source_unverified'}
    (folder/'worker-output.json').write_text(json.dumps(output),encoding='utf-8')
    checkpoint={'submission_state':'not_submitted','outcome_unknown':False,'external_creates_attempted':0,'videos_rendered':0,'new_images_generated':0}
    (folder/'checkpoint.json').write_text(json.dumps(checkpoint),encoding='utf-8')
    with f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET status='needs_review',stage='review',payload=?,payload_hash=?,finished_at='2030-11-01T12:01:00+00:00' WHERE id=?",(encoded,payload_sha,run['id']))
        db.execute("INSERT OR REPLACE INTO idea_manual_run_diagnostics VALUES(?, 'worker_needs_review',0,1000,'2030-11-01T12:01:00+00:00')",(run['id'],))
    monkeypatch.setattr(module,'video_readiness',lambda *_args,**_kwargs:{'ready':True,'configuration':{'ready':True,'workflow_version':'fixture-v3','workflow_sha256':'a'*64}})
    f.run_id=run['id'];f.folder=folder;f.payload=payload
    f.arguments={'expected_output_sha256':sha(folder/'worker-output.json'),'expected_checkpoint_sha256':sha(folder/'checkpoint.json'),
        'expected_input_sha256':sha(folder/'run-input.json'),'expected_payload_sha256':payload_sha,'expected_workflow_sha256':'a'*64,
        'runs_root':f.runs.runs_root,'ideas_root':f.ideas,'producer_root':f.output}
    return f


def resume(f,**extra):return module.resume_preflight_hold(f.store,f.runs.root,f.run_id,**(f.arguments|extra))


def test_exact_zero_submission_hold_archives_once_queues_same_uuid_and_fences_old_input(held):
    f=held;result=resume(f)
    assert result['status']=='queued' and result['run_id']==f.run_id and result['fence']==2
    assert not f.folder.exists() and (f.runs.runs_root/'.preflight-holds'/f.run_id/'run-input.json').is_file()
    row=f.runs._row(f.run_id);assert row['payload_hash']==f.arguments['expected_payload_sha256'] and row['request_id']=='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    with pytest.raises(AppError):resume(f)
    assert not f.launcher.calls and not f.store.reel_jobs()


@pytest.mark.parametrize('change',['bad_hash','unknown_outcome','client_state','video','campaign','active_worker','variant'])
def test_any_ambiguous_or_completed_external_state_keeps_original_hold(held,change):
    f=held
    if change=='bad_hash':f.arguments['expected_output_sha256']='b'*64
    elif change=='unknown_outcome':
        path=f.folder/'checkpoint.json';data=json.loads(path.read_text());data['outcome_unknown']=True;path.write_text(json.dumps(data));f.arguments['expected_checkpoint_sha256']=sha(path)
    elif change in ('client_state','video'):(f.folder/('uploader-client-state.json' if change=='client_state' else 'output.mp4')).write_bytes(b'fixture')
    else:
        with f.store.connect() as db:
            if change=='campaign':db.execute("UPDATE idea_pipeline_ideas SET campaign_state='verified',campaign='{}' WHERE id=?",(f.payload['variants'][0]['idea_id'] if 'idea_id' in f.payload['variants'][0] else f.runs._row(f.run_id)['idea_id'],))
            elif change=='active_worker':db.execute("UPDATE idea_manual_runs SET status='running' WHERE id=?",(f.run_id,))
            else:db.execute("UPDATE idea_pipeline_variants SET status='metadata_registered',video_sha256=? WHERE id=?",('c'*64,f.payload['variants'][0]['variant_id']))
    with pytest.raises(AppError):resume(f)
    assert f.folder.is_dir() and not (f.runs.runs_root/'.preflight-holds'/f.run_id).exists()


def test_live_workspace_lock_blocks_recovery(held):
    f=held
    with FileLock(str(f.runs.root/'data'/'page-manager.lock')):
        with pytest.raises(AppError):resume(f)
    assert f.folder.is_dir()


def test_workflow_rebind_requires_explicit_hash_and_changes_only_workflow(held,monkeypatch):
    f=held;monkeypatch.setattr(module,'video_readiness',lambda *_args,**_kwargs:{'ready':True,'configuration':{'ready':True,'workflow_version':'fixture-v3','workflow_sha256':'d'*64}})
    with pytest.raises(AppError):resume(f,expected_workflow_sha256='d'*64)
    result=resume(f,expected_workflow_sha256='d'*64,allow_workflow_rebind=True)
    row=f.runs._row(f.run_id);payload=json.loads(row['payload']);assert payload.pop('workflow')=={'version':'fixture-v3','sha256':'d'*64}
    old=dict(f.payload);old.pop('workflow');assert payload==old and result['payload_sha256']!=f.arguments['expected_payload_sha256']


def test_database_failure_after_archive_restores_folder_and_records_hold(held,monkeypatch):
    f=held;real=f.store.connect
    from contextlib import contextmanager
    class FailUpdate:
        def __init__(self,db):self.db=db
        def execute(self,query,args=()):
            if query.startswith('UPDATE idea_manual_runs SET status='):raise RuntimeError('synthetic transaction failure')
            return self.db.execute(query,args)
    @contextmanager
    def connection():
        with real() as db:yield FailUpdate(db)
    monkeypatch.setattr(f.store,'connect',connection)
    with pytest.raises(RuntimeError):resume(f)
    assert f.folder.is_dir()
    with real() as db:
        assert db.execute('SELECT status FROM pilot_preflight_resume_audit WHERE run_id=?',(f.run_id,)).fetchone()[0]=='restored_held'
        assert db.execute('SELECT status,fence FROM idea_manual_runs WHERE id=?',(f.run_id,)).fetchone()[:]==('needs_review',1)
    with pytest.raises(AppError):resume(f)


def test_explicit_reviewed_drafts_are_archived_but_cannot_authorize_extra_products_or_video(held):
    f=held;reviewed={}
    for name in module.REVIEWABLE_PARTIAL_FILES:
        path=f.folder/name;path.write_bytes(b'unit reviewed partial fixture');reviewed[name]=sha(path)
    with pytest.raises(AppError):resume(f)
    result=resume(f,reviewed_partial_files=reviewed)
    assert result['status']=='queued'
    with f.store.connect() as db:
        evidence=json.loads(db.execute('SELECT evidence FROM pilot_preflight_resume_audit').fetchone()[0])
        assert evidence['reviewed_partial_files']==reviewed


@pytest.mark.parametrize('committed',[False,True])
def test_final_commit_error_restores_and_keeps_or_forces_hold(held,monkeypatch,committed):
    f=held;real=f.store.connect;raise_after_commit=[True]
    from contextlib import contextmanager
    @contextmanager
    def connection():
        updated=False
        class Proxy:
            def __init__(self,db):self.db=db
            def execute(self,query,args=()):
                nonlocal updated
                if query.startswith('UPDATE idea_manual_runs SET status='):updated=True
                return self.db.execute(query,args)
        with real() as db:
            yield Proxy(db)
            if updated and raise_after_commit[0] and not committed:
                raise_after_commit[0]=False;raise RuntimeError('synthetic commit failed')
        if updated and raise_after_commit[0]:raise_after_commit[0]=False;raise RuntimeError('synthetic commit acknowledgement lost')
    monkeypatch.setattr(f.store,'connect',connection)
    with pytest.raises(RuntimeError):resume(f)
    assert f.folder.is_dir()
    with real() as db:
        row=db.execute('SELECT status,fence,cancel_requested FROM idea_manual_runs WHERE id=?',(f.run_id,)).fetchone()
        assert tuple(row)==('needs_review',2 if committed else 1,1 if committed else 0)
        assert db.execute('SELECT status FROM pilot_preflight_resume_audit').fetchone()[0]=='restored_held'


def test_rewritten_input_cannot_invent_an_unfrozen_seed_image(held):
    f=held;directory=f.folder/'seed-images';directory.mkdir();image=directory/'01.png';image.write_bytes(b'not a trusted frozen seed')
    path=f.folder/'run-input.json';data=json.loads(path.read_text());data['preflight_images']=[{
        'image_path':str(image),'image_sha256':sha(image),'origin':'codex_native_imagegen'}]
    path.write_text(json.dumps(data),encoding='utf-8');f.arguments['expected_input_sha256']=sha(path)
    with pytest.raises(AppError):resume(f)
    assert f.folder.is_dir()
