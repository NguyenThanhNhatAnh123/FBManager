"""Private, one-use operator recovery for a proven zero-submission pilot hold.

Not an HTTP/MCP endpoint. The host must release its workspace lock first. This
does not launch workers or bypass any normal execution/uploader/consumer guard.
"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import UUID

from filelock import FileLock,Timeout

from .errors import AppError
from .idea_runs import _regular,_read_json,_json
from .idea_workspace import video_readiness
from .project_paths import RUNS_DIRECTORY,IDEA_DIRECTORY,PRODUCER_ROOT

REVIEWABLE_PARTIAL_FILES={'source-on-black.png','source-on-white.png','source-review.json',
    'uploader-preflight.json','qa-status.json','job.json','manifest.json','result.json',
    'bridge-validation.json','bridge-export.json'}


def fail():raise AppError('Pilot không đủ bằng chứng để mở lại; giữ nguyên để đối chiếu.','pilot_resume_rejected',409)
def sha(path):
    if not _regular(path,25*1024*1024):fail()
    return hashlib.sha256(path.read_bytes()).hexdigest()
def checked_json(path,expected):
    if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected) or sha(path)!=expected:fail()
    return _read_json(path)
def guard(path,root):
    path=Path(path)
    if not path.is_absolute() or not path.is_relative_to(root) or path.resolve()!=path:fail()
    for part in (path,*path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400):fail()
    return path


def resume_preflight_hold(store,root,run_id,*,expected_output_sha256,expected_checkpoint_sha256,
        expected_input_sha256,expected_payload_sha256,expected_workflow_sha256,
        allow_workflow_rebind=False,checkpoint_name='checkpoint.json',
        reviewed_partial_files=None,
        runs_root=RUNS_DIRECTORY,ideas_root=IDEA_DIRECTORY,producer_root=PRODUCER_ROOT):
    """Archive evidence, fence the old worker, and queue the exact same run once.

    Workflow rebind changes only payload.workflow, after explicit expected hashes
    and a valid current attestation. All other frozen input remains unchanged.
    Optional paths are for isolated unit fixtures; the operator CLI pins defaults.
    """
    try:valid_id=isinstance(run_id,str) and str(UUID(run_id))==run_id and UUID(run_id).int!=0
    except (ValueError,TypeError,AttributeError):valid_id=False
    if (not valid_id or not isinstance(checkpoint_name,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}\.json',checkpoint_name)
            or checkpoint_name in {'run-input.json','worker-output.json','worker-output.schema.json'}
            or type(allow_workflow_rebind) is not bool):fail()
    reviewed_partial_files={} if reviewed_partial_files is None else reviewed_partial_files
    if (not isinstance(reviewed_partial_files,dict) or set(reviewed_partial_files)-REVIEWABLE_PARTIAL_FILES
            or any(not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value) for value in reviewed_partial_files.values())):fail()
    root=Path(root).absolute();runs_root=Path(runs_root).absolute();ideas_root=Path(ideas_root).absolute()
    folder=guard(runs_root/run_id,runs_root);archive=guard(runs_root/'.preflight-holds'/run_id,runs_root)
    lock_path=guard(root/'data'/'page-manager.lock',root)
    if lock_path.exists() and not _regular(lock_path):fail()
    lock=FileLock(str(lock_path))
    try:lock.acquire(timeout=0)
    except Timeout:fail()
    archived=False;claimed=False
    try:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS pilot_preflight_resume_audit(
                run_id TEXT PRIMARY KEY,status TEXT NOT NULL,evidence TEXT NOT NULL,created_at TEXT NOT NULL)''')
            if db.execute('SELECT 1 FROM pilot_preflight_resume_audit WHERE run_id=?',(run_id,)).fetchone():fail()
            row=db.execute('''SELECT r.*,d.code AS diagnostic_code,d.exit_code FROM idea_manual_runs r
                LEFT JOIN idea_manual_run_diagnostics d ON d.run_id=r.id WHERE r.id=?''',(run_id,)).fetchone()
            if row is None:fail()
            row=dict(row)
            if (row['status']!='needs_review' or row['diagnostic_code']!='worker_needs_review'
                    or type(row['exit_code']) is not int or row['exit_code']!=0 or not row['finished_at']
                    or row['cancel_requested'] or type(row['fence']) is not int or not 1<=row['fence']<2**63-1
                    or hashlib.sha256(row['payload'].encode()).hexdigest()!=expected_payload_sha256
                    or row['payload_hash']!=expected_payload_sha256):fail()
            try:finished=datetime.fromisoformat(row['finished_at'])
            except (ValueError,TypeError):fail()
            if finished.tzinfo is None or finished.utcoffset() is None:fail()
            if db.execute("SELECT 1 FROM idea_manual_runs WHERE status IN ('queued','preparing','running','importing') LIMIT 1").fetchone():fail()
            if db.execute("SELECT 1 FROM idea_pipeline_jobs WHERE status NOT IN ('completed','cancelled') LIMIT 1").fetchone():fail()
            if archive.exists() or not folder.is_dir():fail()
            payload=json.loads(row['payload']);data=checked_json(folder/'run-input.json',expected_input_sha256)
            output=checked_json(folder/'worker-output.json',expected_output_sha256)
            checkpoint=checked_json(folder/checkpoint_name,expected_checkpoint_sha256)
            if (checkpoint.get('submission_state')!='not_submitted' or checkpoint.get('outcome_unknown') is not False
                    or any(type(checkpoint.get(key)) is not int or checkpoint[key]!=0 for key in
                        ('external_creates_attempted','videos_rendered','new_images_generated'))):fail()
            if (output.get('status')!='needs_review' or 'campaign' not in output or output['campaign'] is not None
                    or output.get('reason') in (None,'ready')):fail()
            for key in ('run_id','idea_id','artwork_sha256','lease_fence'):
                expected={'run_id':run_id,'idea_id':row['idea_id'],'artwork_sha256':row['artwork_sha256'],'lease_fence':row['fence']}[key]
                if data.get(key)!=expected or output.get(key)!=expected:fail()
                if key in checkpoint and checkpoint[key]!=expected:fail()
            if type(data['lease_fence']) is not int or type(output['lease_fence']) is not int:fail()
            if data.get('original_filename')!=row['filename']:fail()
            for key in ('source_snapshot','source_revision','uploader','template_policy','variants','variant_count',
                    'caption_mode','trigger_mode','automatic_batch','workflow','audio_assignments','audio_mix_policy','approved_audio_mixes'):
                if data.get(key)!=payload.get(key):fail()
            count=payload.get('variant_count')
            if type(count) is not int or not 1<=count<=14 or not isinstance(payload.get('variants'),list) or len(payload['variants'])!=count:fail()
            filename=row['filename']
            if not filename or ':' in filename or Path(filename).is_absolute() or '..' in filename.replace('\\','/').split('/'):fail()
            original=guard(ideas_root/filename,ideas_root);source=guard(folder/('source'+original.suffix.lower()),folder)
            if data.get('source_path')!=str(source) or sha(original)!=row['artwork_sha256'] or sha(source)!=row['artwork_sha256']:fail()
            allowed={folder/'run-input.json',folder/'worker-output.json',folder/'worker-output.schema.json',folder/checkpoint_name,source}
            for name,expected in reviewed_partial_files.items():
                path=guard(folder/name,folder)
                if sha(path)!=expected:fail()
                allowed.add(path)
            seed=payload.get('preflight_seed');expected_seeds=[]
            if seed is not None:
                if not isinstance(seed,dict) or seed.get('source_sha256')!=row['artwork_sha256'] or seed.get('origin')!='codex_native_imagegen':fail()
                expected_seeds=[{'image_path':str(folder/'seed-images'/'01.png'),
                    'image_sha256':seed.get('image_sha256'),'origin':'codex_native_imagegen'}]
            if data.get('preflight_images',[])!=expected_seeds:fail()
            for entry in expected_seeds:
                path=guard(Path(entry['image_path']),folder)
                if path.parent!=folder/'seed-images' or sha(path)!=entry['image_sha256']:fail()
                allowed.add(path)
            for base,dirs,files in os.walk(folder,followlinks=False):
                for name in dirs:guard(Path(base)/name,folder)
                for name in files:
                    path=guard(Path(base)/name,folder)
                    if path.name=='uploader-client-state.json' or path not in allowed or not _regular(path,25*1024*1024):fail()
            idea=db.execute('SELECT * FROM idea_pipeline_ideas WHERE id=?',(row['idea_id'],)).fetchone()
            if not idea or idea['campaign_state']!='prepared' or idea['campaign'] is not None or idea['artwork_sha256']!=row['artwork_sha256'] or idea['filename']!=filename:fail()
            variants=db.execute('SELECT * FROM idea_pipeline_variants WHERE idea_id=? ORDER BY ordinal',(row['idea_id'],)).fetchall()
            if not variants or any(v['status']!='planned' or v['video_sha256'] is not None for v in variants):fail()
            if [(v['id'],v['reservation_id']) for v in variants[:len(payload['variants'])]]!=[(v['variant_id'],v['reservation_id']) for v in payload['variants']]:fail()
            readiness=video_readiness(root,workflow_directory=producer_root);configuration=readiness.get('configuration',{})
            workflow={'version':configuration.get('workflow_version'),'sha256':configuration.get('workflow_sha256')}
            if readiness.get('ready') is not True or configuration.get('ready') is not True or workflow['sha256']!=expected_workflow_sha256:fail()
            if payload.get('workflow')!=workflow:
                if not allow_workflow_rebind:fail()
                payload['workflow']=workflow
            encoded=_json(payload);new_hash=hashlib.sha256(encoded.encode()).hexdigest()
            evidence={'input_sha256':expected_input_sha256,'output_sha256':expected_output_sha256,
                'checkpoint_sha256':expected_checkpoint_sha256,'old_payload_sha256':expected_payload_sha256,
                'new_payload_sha256':new_hash,'workflow_sha256':expected_workflow_sha256,'old_fence':row['fence'],'new_fence':row['fence']+1,
                'reviewed_partial_files':reviewed_partial_files,
                'reviewed_partial_files_sha256':hashlib.sha256(_json(reviewed_partial_files).encode()).hexdigest()}
            db.execute('INSERT INTO pilot_preflight_resume_audit VALUES(?,?,?,?)',(run_id,'prepared',_json(evidence),datetime.now(timezone.utc).isoformat()))
        claimed=True
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT status,payload_hash,fence FROM idea_manual_runs WHERE id=?',(run_id,)).fetchone()
            if tuple(current)!=('needs_review',expected_payload_sha256,row['fence']):fail()
            archive.parent.mkdir(parents=True,exist_ok=True);guard(archive.parent,runs_root)
            guard(folder,runs_root);guard(archive,runs_root);folder.rename(archive);archived=True
            db.execute("UPDATE idea_manual_runs SET status='queued',stage='queued',message='Operator đã kiểm chứng chưa gửi bên ngoài; xếp lại đúng lượt.',payload=?,payload_hash=?,fence=fence+1,cancel_requested=0,started_at=NULL,finished_at=NULL WHERE id=?",(encoded,new_hash,run_id))
            db.execute("UPDATE idea_manual_run_diagnostics SET code=NULL,exit_code=NULL,duration_ms=NULL WHERE run_id=?",(run_id,))
            db.execute("UPDATE pilot_preflight_resume_audit SET status='requeued' WHERE run_id=?",(run_id,))
        return {'run_id':run_id,'status':'queued','fence':row['fence']+1,'payload_sha256':new_hash,'archived':True}
    except Exception:
        restored=True
        if archived:
            try:
                guard(archive,runs_root);guard(folder,runs_root)
                if folder.exists():raise RuntimeError()
                archive.rename(folder)
            except Exception:restored=False
        if claimed:
            with store.connect() as db:
                # A commit can succeed before its caller sees an error. Always
                # hold that row as well; restoring old files must not queue work.
                db.execute("UPDATE idea_manual_runs SET cancel_requested=1,status='needs_review',stage='interrupted',message='Operator recovery interrupted; inspect archived evidence.' WHERE id=? AND status IN ('queued','preparing','running','importing')",(run_id,))
                db.execute('UPDATE pilot_preflight_resume_audit SET status=? WHERE run_id=?',('restored_held' if restored else 'archive_held',run_id))
        raise
    finally:lock.release()
