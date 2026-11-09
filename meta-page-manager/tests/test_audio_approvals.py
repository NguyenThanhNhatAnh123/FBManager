"""Synthetic fixed-mix registry tests: no listening claim or real media process.

WAV fixtures contain valid test PCM. ffprobe/renderer/ffmpeg responses are fake;
successful assertions below prove registry behavior, never production audio QA.
"""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import uuid4
import wave

import pytest

from page_manager.audio_approvals import AudioApprovals,SOURCE_DIRECTORY
from page_manager.errors import AppError


def digest(value):return hashlib.sha256(value).hexdigest()
def wav_bytes(frames,rate=8000,channels=1):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as output:
        output.setnchannels(channels);output.setsampwidth(2);output.setframerate(rate);output.writeframes(frames)
    return stream.getvalue()
def frames(path):
    with wave.open(str(path),'rb') as value:return value.readframes(value.getnframes())


class Store:
    def __init__(self,path):self.path=path
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()


class Processor:
    """Inert command boundary; only fixture bytes and invented probe metadata."""
    def __init__(self):self.calls=[];self.pcm={};self.metadata={};self.bad_preview=False
    def __call__(self,args,timeout=30):
        args=list(map(str,args));self.calls.append((args,timeout))
        if args[0].endswith('ffprobe.exe'):
            row={'codec_name':'aac','sample_rate':'8000','channels':1,'duration':'8.0'}
            row.update(self.metadata.get(args[-1],{}))
            return json.dumps({'streams':[row]})
        if '--image' in args:
            source=Path(args[args.index('--audio')+1]);video=Path(args[args.index('--output')+1])
            pcm=frames(source);duration=len(pcm)/16000;pcm+=b'\0'*(128000-len(pcm));self.pcm[str(video)]=pcm
            video.write_bytes(b'SYNTHETIC NON-MEDIA VIDEO FIXTURE')
            video.with_suffix('.verification.json').write_text(json.dumps({'audio_assignment':{
                'source_sha256':digest(source.read_bytes()),'source_duration_seconds':duration}}),encoding='utf-8')
            return ''
        if args[0].endswith('ffmpeg.exe'):
            source=Path(args[args.index('-i')+1])
            pcm=self.pcm.get(str(source))
            if pcm is None:pcm=frames(source)
            if '-f' in args:return 'SHA256='+digest(pcm)+'\n'
            assert args[-1].endswith('review.wav') and '-n' in args
            Path(args[-1]).write_bytes(wav_bytes(pcm if not self.bad_preview else b'\x01\0'*64000))
            return ''
        raise AssertionError('No process, provider or real renderer may run')


@pytest.fixture
def registry(tmp_path_factory,monkeypatch):
    # Two 64-character hash directories need a short fixture prefix on Windows.
    tmp_path=tmp_path_factory.mktemp('a')
    root=tmp_path/'m';root.mkdir();source=tmp_path/'FILE ÂM THANH';source.mkdir()
    workflow=tmp_path/'workflow';base=workflow/'.agents'/'skills'/'page-shirt-zoom-8s'
    for name in ('scripts/render_video.py','scripts/audio_tools.py','references/defaults.json'):
        path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('inert fixture '+name,encoding='utf-8')
    paths=[]
    for index in range(6):
        path=source/f'fixture-track-{index}.wav'
        path.write_bytes(wav_bytes(bytes([index+1,0])*8000*(index+2)));paths.append(path)
    store=Store(root/'test.sqlite3');service=AudioApprovals(store,root,source_directory=source,workflow_directory=workflow,ffbin=tmp_path/'fake-ffbin')
    service.directory=root/'mixes'
    processor=Processor();monkeypatch.setattr(service,'_run',processor)
    return SimpleNamespace(root=root,source=source,workflow=workflow,base=base,store=store,service=service,
        processor=processor,paths=paths,tmp_path=tmp_path)


def seed(f,*,approved=False):
    """Seed six valid reference WAVs; rows are unit fixtures, not human approvals."""
    signature=f.service.render_signature();rows=[]
    for source in f.paths:
        source_sha=digest(source.read_bytes());mix_id=f.service._id(signature,source_sha)
        folder=f.service._folder(signature,mix_id);folder.mkdir(parents=True,exist_ok=True)
        pcm=frames(source);duration=len(pcm)/16000;pcm+=b'\0'*(128000-len(pcm))
        preview=folder/'review.wav';preview.write_bytes(wav_bytes(pcm))
        row=(mix_id,source.name,source_sha,signature,digest(pcm),digest(preview.read_bytes()),duration,
             8000,1,8.0,'approved' if approved else 'pending','2030-01-01T00:00:00+00:00',
             '2030-01-01T00:00:01+00:00' if approved else None,'unit_fixture_only' if approved else None)
        with f.store.connect() as db:db.execute('INSERT INTO audio_mix_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',row)
        rows.append(f.service._row(mix_id))
    return rows


def batch():return {'id':str(uuid4()),'items':[{'variant_count':2} for _ in range(7)]}


def test_snapshot_never_renders_decodes_or_approves_and_uses_only_pinned_folder(registry):
    f=registry;outside=f.tmp_path/'outside';outside.mkdir();(outside/'not-allowed.wav').write_bytes(wav_bytes(b'\0\0'*8000))
    state=f.service.snapshot()
    assert state['source_directory']==str(f.source) and state['source_count']==6
    assert state['approved_count']==0 and state['ready'] is False
    assert {row['status'] for row in state['items']}=={'not_prepared'}
    assert all(row['preview_url'] is None for row in state['items'])
    assert not f.processor.calls
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM audio_mix_approvals').fetchone()[0]==0
    from page_manager.project_paths import AUDIO_DIRECTORY
    assert SOURCE_DIRECTORY==AUDIO_DIRECTORY


def test_explicit_prepare_makes_six_pending_reviews_without_approval_and_does_not_rerender(registry):
    f=registry;state=f.service.prepare()
    assert state['source_count']==6 and state['approved_count']==0 and not state['ready']
    assert {row['status'] for row in state['items']}=={'pending'}
    assert all(row['prepared'] and row['preview_url'] for row in state['items'])
    assert sum('--image' in args for args,_ in f.processor.calls)==6
    with f.store.connect() as db:
        assert all(row['approved_at'] is None and row['reviewer'] is None for row in db.execute('SELECT * FROM audio_mix_approvals'))
    count=len(f.processor.calls);assert f.service.prepare()==state
    assert len(f.processor.calls)==count,'Preparing existing immutable mixes never replays rendering'


def test_partial_or_mismatched_preview_is_held_without_approval(registry):
    f=registry;f.processor.bad_preview=True
    with pytest.raises(AppError) as error:f.service.prepare()
    assert error.value.code=='audio_preview_mismatch'
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM audio_mix_approvals').fetchone()[0]==0
    with pytest.raises(AppError) as error:f.service.prepare()
    assert error.value.code=='audio_mix_partial_requires_review'


def test_only_explicit_approval_unlocks_all_six_and_partial_approval_cannot_plan(registry):
    f=registry;rows=seed(f);signature=f.service.render_signature();ids=[row['id'] for row in rows]
    assert not f.service.snapshot()['ready'] and not f.processor.calls
    with pytest.raises(AppError):f.service.approved_mixes()
    state=f.service.approve(ids[:5],signature)
    assert state['approved_count']==5 and not state['ready']
    with pytest.raises(AppError):f.service.plan_batch(batch())
    state=f.service.approve(ids[5:],signature)
    assert state['ready'] and state['approved_count']==6
    with f.store.connect() as db:
        assert {row[0] for row in db.execute('SELECT reviewer FROM audio_mix_approvals')}=={'user_listened_to_final_mix'}
    assert len(f.service.approved_mixes())==6


def test_bulk_approval_is_atomic_when_one_of_six_reviews_changed(registry):
    f=registry;rows=seed(f);preview=f.service._preview(rows[-1])
    preview.write_bytes(wav_bytes(b'\x77\0'*64000))
    with pytest.raises(AppError):f.service.approve([row['id'] for row in rows],f.service.render_signature())
    with f.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audio_mix_approvals WHERE status='approved'").fetchone()[0]==0


@pytest.mark.parametrize('ids',[None,[],['x'],[True],[{}],['a'*64,'a'*64]])
def test_invalid_approval_ids_fail_with_bounded_error_without_changes(registry,ids):
    f=registry;seed(f)
    with pytest.raises(AppError) as error:f.service.approve(ids,f.service.render_signature())
    assert error.value.code=='invalid_audio_mix' and error.value.status_code==422
    assert f.service.snapshot()['approved_count']==0


def test_renderer_change_invalidates_approval_and_old_signature_cannot_be_replayed(registry):
    f=registry;rows=seed(f,approved=True);signature=f.service.render_signature()
    (f.base/'scripts'/'audio_tools.py').write_text('new inert fixture',encoding='utf-8')
    state=f.service.snapshot()
    assert state['render_signature']!=signature and state['approved_count']==0 and not state['ready']
    with pytest.raises(AppError):f.service.approve([row['id'] for row in rows],signature)
    with pytest.raises(AppError):f.service.preview_path(rows[0]['id'])
    assert not f.processor.calls


def test_source_hash_change_or_preview_tamper_invalidates_registry(registry):
    f=registry;rows=seed(f,approved=True)
    f.paths[0].write_bytes(wav_bytes(b'\x25\0'*16000))
    assert f.service.snapshot()['approved_count']==5
    with pytest.raises(AppError):f.service.preview_path(rows[0]['id'])
    preview=f.service._folder(rows[1]['render_signature'],rows[1]['id'])/'review.wav';preview.write_bytes(wav_bytes(b'\x33\0'*64000))
    assert f.service.snapshot()['approved_count']==4
    with pytest.raises(AppError):f.service.approve([rows[1]['id']],f.service.render_signature())


def test_hardlink_source_is_rejected_even_if_its_bytes_match(registry):
    f=registry;link=f.source/'duplicate-link.wav'
    link.hardlink_to(f.paths[0])
    state=f.service.snapshot()
    assert state['ready'] is False and state['blockers']==['audio_mix_unavailable']


def test_symlink_source_cannot_escape_pinned_folder(registry):
    f=registry;outside=f.tmp_path/'outside.wav';outside.write_bytes(wav_bytes(b'\x10\0'*8000))
    try:(f.source/'escape.wav').symlink_to(outside)
    except OSError:pytest.skip('Windows symlink privilege unavailable for disposable fixture')
    assert f.service.snapshot()['blockers']==['audio_mix_unavailable']


def test_exact_decoded_pcm_and_metadata_return_only_hash_bound_approval_proof(registry):
    f=registry;row=seed(f,approved=True)[0];video=f.root/'fixture.mp4';video.write_bytes(b'NOT REAL MEDIA')
    f.processor.pcm[str(video)]=frames(f.service._preview(row))
    proof=f.service.verify_video(video,row['source_sha256'],row['id'])
    assert proof==f.service._proof(row) and proof['pcm_sha256']==digest(f.processor.pcm[str(video)])
    f.processor.pcm[str(video)]=b'\x11\0'*64000
    with pytest.raises(AppError) as error:f.service.verify_video(video,row['source_sha256'],row['id'])
    assert error.value.code=='audio_mix_not_approved'


@pytest.mark.parametrize('change',[
    {'codec_name':'mp3'},{'sample_rate':'16000'},{'channels':2},{'duration':'9.0'},{'duration':'NaN'},
])
def test_mismatched_or_nonfinite_final_metadata_is_never_approved(registry,change):
    f=registry;row=seed(f,approved=True)[0];video=f.root/'fixture.mp4';video.write_bytes(b'NOT REAL MEDIA')
    f.processor.pcm[str(video)]=frames(f.service._preview(row));f.processor.metadata[str(video)]=change
    with pytest.raises(AppError):f.service.verify_video(video,row['source_sha256'],row['id'])


@pytest.mark.parametrize('change',['wrong_id','wrong_pcm','null_proof'])
def test_frozen_approval_snapshot_must_match_exact_current_registry(registry,change):
    f=registry;seed(f,approved=True);proofs=f.service.approved_mixes()
    assert f.service.validate_snapshot(proofs)
    source=next(iter(proofs))
    if change=='null_proof':proofs[source]=None
    elif change=='wrong_id':proofs[source]['approval_id']='f'*64
    else:proofs[source]['pcm_sha256']='e'*64
    with pytest.raises(AppError):f.service.validate_snapshot(proofs)


def test_manual_plan_is_repeatable_approved_and_does_not_create_an_auto_batch(registry):
    f=registry;seed(f,approved=True);request_id=str(uuid4())
    assignments=f.service.plan_manual(request_id,14)
    assert assignments==f.service.plan_manual(request_id,14)
    assert len(assignments)==14 and len({v['source_sha256'] for v in assignments[:6]})==6
    assert len({v['source_sha256'] for v in assignments[6:12]})==6
    assert all(v['approved_mix']['source_sha256']==v['source_sha256'] for v in assignments)
    with f.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM idea_auto_batch_audio').fetchone()[0]==0


@pytest.mark.parametrize('request_id,count', [('not-a-uuid',2),(str(uuid4()),True),(str(uuid4()),0),(str(uuid4()),15)])
def test_manual_audio_plan_rejects_invalid_identity_or_count(registry,request_id,count):
    with pytest.raises(AppError):registry.service.plan_manual(request_id,count)


def test_manual_plan_cannot_grant_missing_human_approval(registry):
    f=registry;seed(f,approved=False)
    with pytest.raises(AppError):f.service.plan_manual(str(uuid4()),2)


def test_fourteen_video_plan_uses_all_six_before_reuse_and_replays_durably(registry,monkeypatch):
    f=registry;seed(f,approved=True);request=batch();assignments=f.service.plan_batch(request)
    assert len(assignments)==14 and [row['index'] for row in assignments]==list(range(1,15))
    assert len({row['source_sha256'] for row in assignments[:6]})==6
    assert len({row['source_sha256'] for row in assignments[6:12]})==6
    assert all(a['source_sha256']!=b['source_sha256'] for a,b in zip(assignments,assignments[1:]))
    assert all(Path(row['source_file']).parent==f.source and row['loop'] is False and row['speed_changed'] is False for row in assignments)
    assert all(row['approved_mix']['source_sha256']==row['source_sha256'] for row in assignments)
    again=AudioApprovals(f.store,f.root,source_directory=f.source,workflow_directory=f.workflow,ffbin=f.tmp_path/'fake-ffbin')
    again.directory=f.service.directory
    monkeypatch.setattr(again,'_run',lambda *_args,**_kwargs:(_ for _ in ()).throw(AssertionError('Replay never renders or decodes')))
    assert again.plan_batch(request)==assignments
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM idea_auto_batch_audio').fetchone()[0]==1


@pytest.mark.parametrize('change',['checksum','outside_source','source_hash','count','approval'])
def test_saved_audio_plan_cannot_move_source_or_drop_approval_on_replay(registry,change):
    f=registry;seed(f,approved=True);request=batch();assignments=f.service.plan_batch(request)
    if change=='outside_source':
        path=f.tmp_path/'outside.wav';path.write_bytes(Path(assignments[0]['source_file']).read_bytes());assignments[0]['source_file']=str(path)
    elif change=='source_hash':assignments[0]['source_sha256']='d'*64
    elif change=='count':assignments.pop()
    elif change=='approval':assignments[0]['approved_mix']['pcm_sha256']='c'*64
    encoded=json.dumps(assignments,sort_keys=True,separators=(',',':'))
    with f.store.connect() as db:db.execute('UPDATE idea_auto_batch_audio SET assignments=?,sha256=? WHERE batch_id=?',
        (encoded,'a'*64 if change=='checksum' else digest(encoded.encode()),request['id']))
    with pytest.raises(AppError):f.service.plan_batch(request)


def test_identical_pcm_approval_survives_project_storage_migration_with_audit(registry):
    f=registry;old=seed(f,approved=True)
    (f.base/'scripts/audio_tools.py').write_text('same DSP; new project source path',encoding='utf-8')
    pending=f.service.prepare();assert not pending['ready']
    state=f.service.adopt_approved_pcm([v['id'] for v in old],pending['render_signature'])
    assert state['ready'] and state['approved_count']==6
    assert all(v['approved_at']=='2030-01-01T00:00:01+00:00' for v in state['items'])
    with f.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM audio_mix_approval_transfers').fetchone()[0]==6


def test_migration_cannot_approve_changed_pcm_or_partially_approve_batch(registry):
    f=registry;old=seed(f,approved=True)
    (f.base/'scripts/audio_tools.py').write_text('new project source path',encoding='utf-8')
    pending=f.service.prepare()
    with f.store.connect() as db:db.execute('UPDATE audio_mix_approvals SET pcm_sha256=? WHERE id=?',('d'*64,pending['items'][-1]['id']))
    with pytest.raises(AppError):f.service.adopt_approved_pcm([v['id'] for v in old],pending['render_signature'])
    assert f.service.snapshot()['approved_count']==0


def test_unapproved_second_audio_stream_is_rejected(registry,monkeypatch):
    f=registry;rows=seed(f,approved=True)
    def extra_stream(args,timeout=30):
        row={'codec_name':'aac','sample_rate':'8000','channels':1,'duration':'8'}
        return json.dumps({'streams':[row,row]})
    monkeypatch.setattr(f.service,'_run',extra_stream)
    with pytest.raises(AppError):f.service.verify_video(f.tmp_path/'inert.mp4',rows[0]['source_sha256'])
