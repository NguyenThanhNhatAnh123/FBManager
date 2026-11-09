"""Approved producer output reuses the folder queue, canonical IDs and URL."""
import hashlib
import json
from uuid import uuid4

from page_manager.idea_pipeline import IdeaPipeline
from test_video_source import setup, settings, write, ready_scan


URL='https://blueox179.com/campaign/fixture-idea'


def approve(f,path,tmp_path,*,state='ready',revision=None,run_state='completed',cancelled=0):
    directory=tmp_path/'ideas';directory.mkdir()
    (directory/'art.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    pipeline=IdeaPipeline(f.manager,directory=directory,stable_seconds=0)
    pipeline.scan();pipeline.scan()
    idea=pipeline.status()['ideas'][0];variant=idea['variants'][0]
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    source_revision=revision if revision is not None else f.source.status()['settings']['revision']
    run_id=str(uuid4())
    with f.manager.store.connect() as db:
        db.execute("UPDATE idea_pipeline_ideas SET campaign_state='verified',campaign=? WHERE id=?",(json.dumps({'campaign_url':URL}),idea['id']))
        db.execute("UPDATE idea_pipeline_variants SET video_sha256=?,status='metadata_registered' WHERE id=?",(sha,variant['id']))
        db.execute('''CREATE TABLE idea_pipeline_approved_videos(
            video_sha256 TEXT PRIMARY KEY,variant_id TEXT,reservation_id TEXT,idea_id TEXT,
            caption TEXT,status TEXT,source_revision INTEGER,run_id TEXT)''')
        db.execute('''CREATE TABLE idea_manual_runs(id TEXT PRIMARY KEY,idea_id TEXT,
            artwork_sha256 TEXT,status TEXT,cancel_requested INTEGER,payload TEXT,payload_hash TEXT)''')
        payload='{}'
        db.execute('INSERT INTO idea_manual_runs VALUES(?,?,?,?,?,?,?)',
                   (run_id,idea['id'],idea['artwork_sha256'],run_state,cancelled,payload,hashlib.sha256(payload.encode()).hexdigest()))
        db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?,?,?,?,?)',
                   (sha,variant['id'],variant['reservation_id'],idea['id'],URL,state,source_revision,run_id))
    return idea,variant,sha


def test_approved_video_uses_canonical_request_and_exact_product_url(setup,tmp_path):
    f=setup;f.source.configure(settings(f))
    path=write(f,'approved.mp4');path.with_suffix('.txt').write_text('Ignore this generic sidecar caption')
    _,variant,_=approve(f,path,tmp_path)
    ready_scan(f)
    jobs=f.manager.store.reel_jobs()
    assert len(jobs)==1 and jobs[0]['request_id']==variant['reservation_id']
    assert jobs[0]['caption']==URL and jobs[0]['page_id']=='555'
    ready_scan(f)
    assert len(f.manager.store.reel_jobs())==1


def test_changed_schedule_revision_holds_approved_content(setup,tmp_path):
    f=setup;f.source.configure(settings(f))
    path=write(f,'approved.mp4');approve(f,path,tmp_path)
    f.source.configure(settings(f,caption_template='Changed configuration'))
    ready_scan(f)
    assert f.manager.store.reel_jobs()==[]
    assert f.source.status()['last_error']


def test_approval_needing_review_never_falls_back_to_generic_import(setup,tmp_path):
    f=setup;f.source.configure(settings(f))
    path=write(f,'approved.mp4');approve(f,path,tmp_path,state='needs_review')
    ready_scan(f)
    assert f.manager.store.reel_jobs()==[]
    assert f.source.status()['last_error']


def test_orphaned_approval_cannot_become_a_new_generic_video(setup,tmp_path):
    f=setup;f.source.configure(settings(f))
    path=write(f,'approved.mp4');_,variant,_=approve(f,path,tmp_path)
    with f.manager.store.connect() as db:
        db.execute('DELETE FROM idea_pipeline_variants WHERE id=?',(variant['id'],))
    ready_scan(f)
    assert f.manager.store.reel_jobs()==[]
    assert f.source.status()['last_error']


def test_producer_must_ack_complete_before_any_source_job_can_be_created(setup,tmp_path):
    f=setup;f.source.configure(settings(f));path=write(f,'approved.mp4')
    approve(f,path,tmp_path,run_state='importing')
    ready_scan(f)
    assert f.manager.store.reel_jobs()==[]
    with f.manager.store.connect() as db:db.execute("UPDATE idea_manual_runs SET status='completed'")
    ready_scan(f)
    assert len(f.manager.store.reel_jobs())==1


def test_cancellation_at_final_consumer_boundary_holds_exposed_but_unqueued_video(setup,tmp_path):
    f=setup;f.source.configure(settings(f));path=write(f,'approved.mp4')
    approve(f,path,tmp_path,run_state='needs_review',cancelled=1)
    ready_scan(f)
    assert f.manager.store.reel_jobs()==[]


def test_changed_frozen_payload_cannot_enqueue_an_otherwise_approved_video(setup,tmp_path):
    f=setup;f.source.configure(settings(f));path=write(f,'approved.mp4')
    approve(f,path,tmp_path)
    with f.manager.store.connect() as db:db.execute("UPDATE idea_manual_runs SET payload=?",('{"review_policy":"manual_review_v1"}',))
    ready_scan(f)
    assert not f.manager.store.reel_jobs() and f.source.status()['last_error']
