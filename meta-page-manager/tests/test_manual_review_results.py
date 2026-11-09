"""Actual synthetic decoding and isolated SQLite; no generation or external IO."""
import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_idea_run_results import f,media,sha,save,mix_proof
from page_manager.publication_approvals import PublicationApprovals


def manual(f):
    proof=mix_proof();run=f.run
    run.update(review_policy='manual_review_v1',execution_pipeline='rolling_v1',
        audio_mix_policy='human_approved_exact_pcm_v1',audio_assignments=[{'source_sha256':'a'*64} for _ in range(7)],
        approved_audio_mixes={'a'*64:proof},status='running')
    with f.store.connect() as db:
        payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs').fetchone()[0])
        payload.update({k:run[k] for k in ('review_policy','execution_pipeline','audio_mix_policy','audio_assignments','approved_audio_mixes')})
        db.execute("UPDATE idea_manual_runs SET payload=?,payload_hash=?,status='running'",(json.dumps(payload),sha(__import__('page_manager.idea_run_results',fromlist=['_canonical'])._canonical(payload))))
    for name in ('images','videos','handoff'):(f.directory/name).mkdir()
    original=f.manifest['variants'][0]
    image=f.directory/'images/01.png';video=f.directory/'videos/01.mp4'
    shutil.copyfile(original['image_path'],image);shutil.copyfile(original['video_path'],video)
    campaign=f.worker['campaign'];campaign.update(kind='existing',source_url=None,shirt_color=None)
    doc={'schema_version':'1.0','record_type':'page_manager_manual_review_variant','review_policy':'manual_review_v1',
        'status':'manual_review_pending','ai_qa':False,'visual_review':'pending','distinct_review':'pending',
        'run_id':run['id'],'idea_id':run['idea_id'],'source_sha256':run['artwork_sha256'],'lease_fence':1,
        'variant_count':7,'ordinal':1,**run['variants'][0],'source_path':str(f.directory/'source.png'),
        'image_path':str(image),'image_sha256':sha(image.read_bytes()),'image_origin':'generated_native',
        'video_path':str(video),'video_sha256':sha(video.read_bytes()),'audio_source_sha256':'a'*64,
        'concept_id':'synthetic-manual-first','caption_mode':'product_link_only','caption':campaign['url'],'campaign':campaign,
        'technical':{'width':1080,'height':1920,'fps':30,'frames':240,'duration_seconds':8,
            'video_codec':'h264','audio_codec':'aac','full_decode_passed':True,'all_frames_artwork_visible':True,
            'minimum_margin_fraction':.04,'motion_registration':'not_run'},
        'approved_audio':{'review_method':'approved_mix_hash_match','binding':{'approval_id':proof['approval_id'],
            'actual_pcm_sha256':proof['pcm_sha256'],'approved_pcm_sha256':proof['pcm_sha256'],'audio_source_sha256':'a'*64},
            'checks':{'exact_pcm_match':True}}}
    save(f.directory/'handoff/01.json',doc)
    calls=[]
    def verify(path,source_sha,approval_id=None):
        calls.append((path,source_sha,approval_id));return proof
    f.consumer.audio_approvals=SimpleNamespace(verify_video=verify)
    return doc,calls


def test_first_video_imports_while_other_six_do_not_exist_and_replay_is_idempotent(f):
    doc,calls=manual(f)
    first=asyncio.run(f.consumer.accept_item(f.run,f.directory,1))
    assert first['accepted'],first.get('reason')
    assert first['status']=='manual_review_pending' and first['ai_qa'] is False
    assert f.source.requests==1 and len(calls)==1
    assert len(list(f.destination.glob('*.mp4')))==1
    with f.store.connect() as db:
        rows=db.execute('SELECT status FROM idea_pipeline_approved_videos').fetchall()
        assert len(rows)==1 and rows[0]['status']=='manual_review_pending'
        assert db.execute('SELECT status FROM idea_manual_runs').fetchone()[0]=='running'
    assert asyncio.run(f.consumer.accept_item(f.run,f.directory,1))['accepted']
    assert len(list(f.destination.glob('*.mp4')))==1


@pytest.mark.parametrize('change',['ai_qa','fence','ordinal','file','audio','cancel','policy','decoder'])
def test_manual_receipt_cannot_bypass_identity_bytes_audio_or_cancellation(f,change,monkeypatch):
    doc,calls=manual(f)
    if change=='ai_qa':doc['ai_qa']=True
    elif change=='fence':doc['lease_fence']=2
    elif change=='ordinal':doc['ordinal']=2
    elif change=='file':Path(doc['video_path']).write_bytes(b'changed')
    elif change=='audio':f.consumer.audio_approvals.verify_video=lambda *_a,**_k:None
    elif change=='decoder':monkeypatch.setattr(f.consumer,'_decode',lambda *_:(_ for _ in ()).throw(ValueError()))
    elif change=='policy':f.run['review_policy']=None
    elif change=='cancel':
        with f.store.connect() as db:db.execute('UPDATE idea_manual_runs SET cancel_requested=1')
    save(f.directory/'handoff/01.json',doc)
    assert not asyncio.run(f.consumer.accept_item(f.run,f.directory,1))['accepted']
    assert not list(f.destination.glob('*.mp4'))


def test_manual_job_still_requires_human_approval_when_global_gate_is_disabled(f):
    doc,_=manual(f)
    assert asyncio.run(f.consumer.accept_item(f.run,f.directory,1))['accepted']
    ledger=PublicationApprovals(f.store)
    f.store.reel_asset_save({'id':'a'*32,'name':'test.mp4','path':doc['video_path'],'size':Path(doc['video_path']).stat().st_size,
        'sha256':doc['video_sha256'],'mime':'video/mp4','created_at':'2026-01-01'})
    f.store.reel_create({'request_id':doc['reservation_id'],'page_id':f.source.settings['page_ids'][0],
        'page_name':'fixture','connection_id':f.source.settings['connection_id'],'connection_label':'fixture','token_revision':1,
        'asset_id':'a'*32,'asset_name':'test.mp4','caption':doc['caption'],'scheduled_at':'2030-01-01T14:00:00+00:00',
        'scheduled_local':'2030-01-01T06:00','timezone':'America/Los_Angeles','fold':0})
    binding={'queue':'token','job_id':1,'page_id':f.source.settings['page_ids'][0],'asset_id':'a'*32,
        'asset_sha256':doc['video_sha256'],'asset_size':Path(doc['video_path']).stat().st_size,'caption':doc['caption'],
        'scheduled_at':'2030-01-01T14:00:00+00:00','timezone':'America/Los_Angeles','actor_id':f.source.settings['connection_id'],'actor_revision':1}
    assert ledger.settings()['required'] is False
    assert ledger.ensure(binding)['required'] is True
    assert ledger.verdict(binding)['can_publish'] is False
    assert ledger.pending()['total']==1
