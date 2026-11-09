"""Opt-in orchestration of user-added ideas. All external writes stay in IdeaRuns."""
import asyncio
from contextlib import suppress
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import random
import re
import stat
import time

from .auto_batch_planner import AutoBatchPlanner
from .errors import AppError
from .idea_workspace import uploader_workspace_blocker

AUDIO_LIBRARY=Path(r'C:\Users\Admin\Documents\ChatGPT\ABC\assets\audio\audio-library.json')
CONNECTION_PROBE_SECONDS=240


def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def _audio_plan(store,batch,library=AUDIO_LIBRARY):
    """Freeze one shuffled-without-replacement plan for the entire daily batch."""
    with store.connect() as db:
        old=db.execute('SELECT assignments,sha256 FROM idea_auto_batch_audio WHERE batch_id=?',(batch['id'],)).fetchone()
    if old:
        if hashlib.sha256(old['assignments'].encode()).hexdigest()!=old['sha256']:raise ValueError('Audio plan changed')
        assignments=json.loads(old['assignments'])
        for entry in assignments:
            source=Path(entry['source_file']);info=source.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size>50*1024*1024 or source.is_symlink():raise ValueError('Audio unavailable')
            if hashlib.sha256(source.read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('Audio changed')
        return assignments
    if library.stat().st_size>128*1024:raise ValueError('Invalid audio library')
    entries=json.loads(library.read_text(encoding='utf-8'))['files'];tracks=[]
    if not isinstance(entries,list) or not 1<=len(entries)<=100:raise ValueError('Invalid audio library')
    for entry in entries:
        source=(library.parent/entry['relative_path']).resolve(strict=True)
        info=source.lstat();duration=entry.get('duration_seconds')
        if (source.parent!=library.parent.resolve() or not stat.S_ISREG(info.st_mode) or info.st_size>50*1024*1024
                or type(duration) not in (int,float) or not 0<duration<86400):raise ValueError('Invalid audio track')
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if digest!=entry['sha256']:raise ValueError('Audio hash changed')
        if digest in {track['source_sha256'] for track in tracks}:continue
        tracks.append({'source_file':str(source),'source_sha256':digest,'source_duration_seconds':duration,
            'start_seconds':0,'padding_silence_seconds':max(0,8-duration),'trimmed_to_seconds':min(8,duration),
            'loop':False,'speed_changed':False})
    total=sum(item['variant_count'] for item in batch['items']);assignments=[]
    rng=random.Random(batch['id'])
    while len(assignments)<total:
        cycle=list(tracks);rng.shuffle(cycle)
        if len(cycle)>1 and assignments and cycle[0]['source_sha256']==assignments[-1]['source_sha256']:
            cycle[0],cycle[1]=cycle[1],cycle[0]
        for track in cycle:
            assignments.append({'index':len(assignments)+1,**track})
            if len(assignments)==total:break
    encoded=_json(assignments);digest=hashlib.sha256(encoded.encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO idea_auto_batch_audio(batch_id,assignments,sha256) VALUES(?,?,?)',(batch['id'],encoded,digest))
        saved=db.execute('SELECT assignments,sha256 FROM idea_auto_batch_audio WHERE batch_id=?',(batch['id'],)).fetchone()
        if hashlib.sha256(saved['assignments'].encode()).hexdigest()!=saved['sha256']:raise ValueError('Invalid audio plan')
    return json.loads(saved['assignments'])


class AutoIdeaController:
    def __init__(self,manager,pipeline,runs,upload_bridge,producer_diagnostics,*,planner=None,
                 clock=None,monotonic=time.monotonic,coalesce_seconds=60,audio_factory=None):
        self.manager,self.pipeline,self.runs=manager,pipeline,runs
        self.upload_bridge,self.diagnostics=upload_bridge,producer_diagnostics
        self.planner=planner or AutoBatchPlanner(manager.store)
        self.clock=clock or (lambda:datetime.now(timezone.utc));self.monotonic=monotonic
        self.coalesce_seconds=coalesce_seconds
        self.audio_factory=audio_factory or (lambda _store,batch:self.runs.audio_approvals.plan_batch(batch))
        self._task=None;self._closing=False;self._next_step=0;self._last_probe=-1e12
        self._seen_hash=None;self._seen_at=0;self.blockers=['preflight_not_checked'];self.state='disabled';self.checked_at=None
        with manager.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS idea_auto_batch_audio(batch_id TEXT PRIMARY KEY,assignments TEXT NOT NULL,sha256 TEXT NOT NULL)')
        runs.automatic_authorizer=self.planner.authorize

    def status(self):
        settings=self.planner.settings();preview=self.planner.preview()
        batch=self.planner.find_incomplete_batch() or self.planner.current_batch()
        return {'settings':settings,'preview':preview,'active_batch':batch,
            'review_policy':getattr(self.runs,'review_policy',None),'ai_qa':getattr(self.runs,'review_policy',None)!='manual_review_v1',
            'blockers':list(self.blockers) if settings['enabled'] else [],
            'state':self.state if settings['enabled'] else 'disabled','checked_at':self.checked_at,
            'coalesce_seconds':self.coalesce_seconds}

    def browser_status(self, idea_ids=()):
        """Cheap policy/allocation read for the Idea board; no plan or provider probes."""
        return {'settings':self.planner.settings(),
                'active_batch':self.planner.visible_allocation(idea_ids) if idea_ids else None}

    def configure(self,enabled,expected_revision):
        self.planner.configure(enabled,expected_revision)
        self._seen_hash=None;self._next_step=0
        self.state='blocked' if enabled else 'disabled';self.blockers=['preflight_not_checked'] if enabled else []
        return self.status()

    def _state(self,state,blockers=()):
        self.state=state;self.blockers=list(blockers);self.checked_at=self.clock().isoformat()

    def _consumer_ready(self):
        callback='on_item' if getattr(self.runs,'review_policy',None)=='manual_review_v1' else 'on_result'
        if callable(getattr(self.runs,callback,None)):return True
        self._state('blocked',['consumer_unavailable']);return False

    async def _refresh_connections(self):
        """Keep bounded session proofs fresh while an enabled Auto run is busy."""
        if self.monotonic()-self._last_probe<CONNECTION_PROBE_SECONDS:return
        self._last_probe=self.monotonic()
        await self.diagnostics.check('codex')
        overview=await asyncio.to_thread(self.upload_bridge.inspect)
        providers=overview.get('providers',{})
        for name in ('local','web'):
            provider=providers.get(name,{})
            if provider.get('available') is True or name=='web' and provider.get('session_saved') is True:
                with suppress(ValueError,AppError):
                    await asyncio.to_thread(self.upload_bridge.check_shop_session,name)

    async def _preflight(self):
        if not self._consumer_ready():return False
        approvals=getattr(self.runs,'audio_approvals',None)
        if approvals is not None and approvals.snapshot().get('ready') is not True:
            self._state('blocked',['audio_mixes_not_approved']);return False
        await self._refresh_connections()
        codex=self.diagnostics.snapshot().get('providers',{}).get('codex',{})
        blockers=[]
        if getattr(self.runs,'review_policy',None)=='manual_review_v1':
            with self.manager.store.connect() as db:
                try:
                    gate=db.execute('SELECT required FROM publication_approval_settings WHERE id=1').fetchone()
                    bot=db.execute('SELECT enabled,revision,checked_revision,checked_ok FROM telegram_approval_settings WHERE id=1').fetchone()
                    review_ready=bool(gate and gate['required']==1 and bot and bot['enabled']==1
                        and bot['revision']==bot['checked_revision'] and bot['checked_ok']==1)
                except Exception:review_ready=False
            if not review_ready:blockers.append('telegram_review_unavailable')
        if codex.get('authenticated') is not True:blockers.append('codex_not_authenticated')
        if codex.get('image_generation',{}).get('configured') is not True:blockers.append('codex_image_generation_unavailable')
        overview=await asyncio.to_thread(self.upload_bridge.inspect)
        if overview.get('authenticated') is not True or overview.get('ready') is not True:blockers.append('uploader_session_unverified')
        workspace_blocker=uploader_workspace_blocker(overview)
        if workspace_blocker:blockers.append(workspace_blocker)
        readiness=await asyncio.to_thread(self.runs.readiness,trigger_mode='automatic_idea_batch')
        if not readiness.get('video_workflow_ready'):blockers.append('producer_workflow_unavailable')
        if not readiness.get('ready'):blockers.append('production_readiness_unavailable')
        with self.manager.store.connect() as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'reel_jobs' in tables and db.execute("SELECT 1 FROM reel_jobs WHERE archived_at IS NULL AND status IN ('needs_user','needs_review','waiting_page') LIMIT 1").fetchone():
                blockers.append('queue_requires_review')
        if blockers:self._state('blocked',blockers)
        return not blockers

    def _reconcile(self,batch):
        for item in batch['items']:
            if item['status']=='completed':continue
            with self.manager.store.connect() as db:
                row=db.execute('SELECT id,status FROM idea_manual_runs WHERE request_id=?',(item['request_id'],)).fetchone()
            if item['status']=='needs_review' and (row is None or row['status']!='completed'):continue
            if row:
                status='completed' if row['status']=='completed' else 'needs_review' if row['status'] in ('needs_review','cancelled') else 'queued'
                self.planner.mark_item(batch['id'],item['idea_id'],status,run_id=row['id'],
                    error_code=('run_cancelled' if row['status']=='cancelled' else 'run_needs_review') if status=='needs_review' else None)
        return self.planner.batch(batch['id'])

    def _hold_changed_source(self,batch):
        """A claimed batch cannot be moved to a newly configured Page pool."""
        with self.manager.store.connect() as db:
            row=db.execute('SELECT revision,configured,settings FROM video_source_settings WHERE id=1').fetchone()
        try:
            values=json.loads(row['settings']) if row else {}
            source={key:values.get(key) for key in ('page_ids','connection_id','daily_time','timezone')}
            source['revision']=row['revision'] if row else None
            unchanged=bool(row and row['configured']==1 and values.get('enabled') is True
                           and source==batch['snapshot']['source'])
        except (ValueError,TypeError,AttributeError,KeyError):unchanged=False
        if unchanged:return False
        for item in batch['items']:
            if item['status']=='pending':
                self.planner.mark_item(batch['id'],item['idea_id'],'needs_review',error_code='source_changed')
        self._state('needs_review',['auto_batch_source_changed'])
        return True

    async def _step(self):
        if not self.planner.settings()['enabled']:return
        batch=self.planner.find_incomplete_batch()
        if batch:
            batch=self._reconcile(batch)
            if any(item['status']=='needs_review' for item in batch['items']):
                self._state('needs_review',['auto_batch_requires_review']);return
            if any(item['status']=='queued' for item in batch['items']):
                # A long production run can outlive the five-minute CLI/shop
                # proofs. Maintenance must not cancel, replay or hold its work.
                with suppress(Exception):await self._refresh_connections()
                self._state('running');return
            if all(item['status']=='completed' for item in batch['items']):
                self._state('completed');return
        else:
            existing=self.planner.current_batch()
            if existing:
                self._state('completed' if existing['status']=='completed' else 'needs_review',['auto_batch_already_created']);return
            preview=self.planner.preview()
            allocations=preview.get('allocations',[])
            if not allocations:
                self._state('idle',['waiting_for_ideas']);return
            if preview.get('blocker_codes'):
                self._state('blocked',preview['blocker_codes']);return
            fingerprint=preview.get('plan_hash')
            if fingerprint!=self._seen_hash:
                self._seen_hash=fingerprint;self._seen_at=self.monotonic()
            if self.monotonic()-self._seen_at<self.coalesce_seconds:
                self._state('coalescing');return
            if not await self._preflight():return
            settings=self.planner.settings()
            batch=self.planner.claim_day(settings['revision'],fingerprint)
        pending=next((item for item in batch['items'] if item['status']=='pending'),None)
        if pending is None:self._state('completed');return
        if self._hold_changed_source(batch):return
        if not await self._preflight():return
        # No new dispatch after a pause that arrived during asynchronous checks.
        if not self.planner.settings()['enabled']:return
        planning=asyncio.create_task(asyncio.to_thread(self.audio_factory,self.manager.store,batch))
        try:assignments=await asyncio.shield(planning)
        except asyncio.CancelledError:
            # The bounded local thread may still save the frozen audio ledger.
            # Join this owned task before shutdown releases the application lock.
            with suppress(BaseException):await planning
            raise
        if not self.planner.settings()['enabled']:return
        if self._hold_changed_source(batch):return
        if not self._consumer_ready():return
        offset=0
        for item in batch['items']:
            if item['idea_id']==pending['idea_id']:break
            offset+=item['variant_count']
        context={key:batch[key] for key in ('settings_revision','day_key')};context['batch_id']=batch['id']
        try:
            run=self.runs.request(pending['idea_id'],pending['request_id'],variant_count=pending['variant_count'],
                trigger_mode='automatic_idea_batch',automatic_batch=context,
                audio_assignments=assignments[offset:offset+pending['variant_count']])
        except AppError as error:
            if not self.planner.settings()['enabled']:return
            if self._hold_changed_source(batch):return
            if error.code not in ('automatic_batch_unavailable','idea_run_exists','idea_run_requires_review','idea_run_conflict'):raise
            self.planner.mark_item(batch['id'],pending['idea_id'],'needs_review',
                error_code='idea_changed' if error.code=='idea_run_requires_review' else 'run_conflict')
            self._state('needs_review',['auto_batch_requires_review']);return
        self.planner.mark_item(batch['id'],pending['idea_id'],'queued',run_id=run['id'])
        self._state('running')

    async def _run_step(self):
        try:await self._step()
        except asyncio.CancelledError:raise
        except AppError as error:
            self._state('blocked',['idea_batch_changed' if error.status_code==409 else 'automatic_check_failed'])
        except Exception:self._state('blocked',['automatic_check_failed'])

    def tick(self):
        if self._closing or self._task is not None and not self._task.done() or self.monotonic()<self._next_step:return
        if not self.planner.settings()['enabled']:return
        self._next_step=self.monotonic()+5
        self._task=asyncio.create_task(self._run_step())

    async def shutdown(self):
        self._closing=True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):await self._task
