"""Consume a worker's frozen QA-bound variants into the existing source.

This is a local acceptance/copy boundary, not a second publisher. It never
generates media, uploads campaigns or invokes Meta. Producer QA reports remain
worker attestations; file identity, media decoding and queue bindings are
checked independently here before any approval is committed.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import types
from uuid import UUID

from .idea_images import _thumbnail
from .errors import AppError
from .reel_assets import _validate_header, HEADER_BYTES, MAX_VIDEO_BYTES
from .project_paths import (PRODUCER_ROOT,BRIDGE_PATH,RUNS_DIRECTORY,FFMPEG_PATH,
    LEGACY_BRIDGE_PATH,LEGACY_RUNS_DIRECTORY,select_existing_run_directory)


ABC_ROOT = PRODUCER_ROOT
FFMPEG_PATHS = (
    FFMPEG_PATH,
)
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
SOURCE_URL = 'https://blueox179.com/campaign/zitat25'
BLACK_SOURCE_URL = 'https://blueox179.com/campaign/ge2610'
CAMPAIGN_RE = re.compile(r'https://blueox179\.com/campaign/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
SAFE_MESSAGE = 'Kết quả cần kiểm tra: chưa đủ dữ liệu, QA hoặc liên kết an toàn để xếp lịch.'


class _Hold(Exception):
    pass


class _UnavailableFiles:
    def __init__(self, *_args, **_kwargs):
        raise _Hold()


def _require(value):
    if not value:
        raise _Hold()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _variant_count(value):
    count = value.get('variant_count', 7)
    _require(type(count) is int and 1 <= count <= 14)
    return count


def _uuid(value):
    _require(isinstance(value, str) and str(UUID(value)) == value and UUID(value).int != 0)
    return value


def _safe_dir(path):
    for item in (path, *path.parents):
        metadata = item.lstat()
        _require(stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
                 and not getattr(metadata, 'st_file_attributes', 0) & 0x400
                 and not getattr(item, 'is_junction', lambda: False)())


def _load_bridge(path=None):
    # Compile the trusted adapter once at service construction, before a
    # worker can alter any run. Do not execute worker-provided Python paths.
    path=Path(path) if path is not None else BRIDGE_PATH
    _safe_dir(path.parent)
    metadata = path.lstat()
    _require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
             and not getattr(metadata, 'st_file_attributes', 0) & 0x400
             and 0 < metadata.st_size <= 256 * 1024)
    raw = path.read_bytes()
    _require(len(raw) == metadata.st_size)
    module = types.ModuleType('_via_trusted_producer_bridge')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


class IdeaRunResults:
    def __init__(self, manager, pipeline, video_source, *, runs_directory=RUNS_DIRECTORY,
                 bridge=None, ffmpeg_path=None):
        self.manager, self.pipeline, self.video_source = manager, pipeline, video_source
        self.audio_approvals = None
        self.store = manager.store
        self.runs_directory = Path(runs_directory).absolute()
        self.legacy_runs_directory=LEGACY_RUNS_DIRECTORY if self.runs_directory==RUNS_DIRECTORY else None
        try:
            self.bridge = self._bounded_bridge(bridge or _load_bridge())
        except Exception:
            # Missing optional producer tooling must not stop the dashboard.
            # It cannot be loaded from a worker's output later in the run.
            self.bridge = types.SimpleNamespace(Files=_UnavailableFiles)
        self.legacy_bridge=None
        if bridge is None and self.legacy_runs_directory is not None:
            try:self.legacy_bridge=self._bounded_bridge(_load_bridge(LEGACY_BRIDGE_PATH))
            except Exception:pass
        self.ffmpeg_path = Path(ffmpeg_path) if ffmpeg_path else next(
            (path for path in FFMPEG_PATHS if path.is_file()), None)
        self.lock = asyncio.Lock()
        self.cancel_event = threading.Event()
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS idea_pipeline_approved_videos(
                video_sha256 TEXT PRIMARY KEY,variant_id TEXT NOT NULL UNIQUE,
                reservation_id TEXT NOT NULL UNIQUE,idea_id TEXT NOT NULL,run_id TEXT NOT NULL,
                caption TEXT NOT NULL,source_path TEXT NOT NULL,destination_name TEXT NOT NULL,
                status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                image_path TEXT NOT NULL,image_sha256 TEXT NOT NULL,concept_id TEXT NOT NULL,
                result_sha256 TEXT NOT NULL,source_revision INTEGER NOT NULL)''')

    def _bounded_bridge(self,bridge):
        base_files = bridge.Files
        class BoundedFiles(base_files):
            def hash(inner, value, expected, suffixes):
                bridge.digest(expected, 'expected file SHA256')
                limit = MAX_VIDEO_BYTES if set(suffixes) == {'.mp4'} else MAX_IMAGE_BYTES
                with inner.opened(value, suffixes) as (path, stream):
                    _require(0 < os.fstat(stream.fileno()).st_size <= limit)
                    digest, total = hashlib.sha256(), 0
                    while block := stream.read(1024 * 1024):
                        _require(not self.cancel_event.is_set())
                        total += len(block); _require(total <= limit)
                        digest.update(block)
                    _require(total > 0 and digest.hexdigest() == expected)
                return str(path), expected
        # The loaded module is private to this consumer, so its normal validator
        # now uses bounded hashing as well as its existing handle/path guards.
        bridge.Files = BoundedFiles
        return bridge

    def _run_bridge(self,run,directory):
        if self.legacy_runs_directory is not None:
            selected=select_existing_run_directory(run['id'],primary_runs=self.runs_directory,legacy_runs=self.legacy_runs_directory)
            _require(selected==directory and Path(run['run_directory'])==selected)
            bridge=self.legacy_bridge if directory.parent==self.legacy_runs_directory else self.bridge
            _require(bridge is not None)
            return bridge
        expected=self.runs_directory/run['id']
        _require(directory==expected and Path(run['run_directory'])==expected)
        return self.bridge

    def _run_guard(self, run, db=None):
        _require(not self.cancel_event.is_set())
        def check(conn):
            row = conn.execute('SELECT * FROM idea_manual_runs WHERE id=?', (run['id'],)).fetchone()
            manual=run.get('review_policy')=='manual_review_v1'
            _require(row is not None and row['status'] in (('running','importing') if manual else ('importing',)) and not row['cancel_requested']
                     and row['fence'] == run['fence'] and row['request_id'] == run['request_id']
                     and row['idea_id'] == run['idea_id'] and row['artwork_sha256'] == run['artwork_sha256'])
            payload = json.loads(row['payload'])
            _require(hashlib.sha256(_canonical(payload)).hexdigest() == row['payload_hash'])
            for key in ('source_snapshot', 'variants', 'caption_mode', 'trigger_mode', 'source_revision', 'scheduling_bindings_hash', 'automatic_batch', 'workflow', 'audio_assignments', 'audio_mix_policy', 'approved_audio_mixes', 'execution_pipeline', 'execution_pipeline_sha256', 'preflight_seed','review_policy'):
                if key in payload:
                    _require(run.get(key) == payload[key])
            _require(('production_iteration' in payload) == ('production_iteration' in run)
                     and run.get('production_iteration') == payload.get('production_iteration'))
            _require(_variant_count(payload) == _variant_count(run)
                     and len(payload['variants']) == _variant_count(payload)
                     and payload.get('caption_mode') == 'product_link_only'
                     and payload.get('trigger_mode') in ('manual_web_run_idea', 'automatic_idea_batch'))
            if payload['trigger_mode'] == 'automatic_idea_batch':
                _require('variant_count' in payload and isinstance(payload.get('automatic_batch'), dict))
            _require(payload.get('audio_mix_policy') in (None,'human_approved_exact_pcm_v1'))
            _require(payload.get('execution_pipeline') in (None,'rolling_v1'))
            _require(payload.get('review_policy') in (None,'manual_review_v1')
                     and (payload.get('review_policy')!='manual_review_v1' or manual))
            if 'production_iteration' in payload:
                _require(self._canonical_variants(run, db=conn) == payload['variants'])
        if db is not None:
            check(db)
        else:
            with self.store.connect() as conn:
                check(conn)

    def _canonical_variants(self, run, db=None):
        """Resolve immutable run slots independently of the current catalog view."""
        try:
            if 'production_iteration' in run:
                _require(isinstance(run['production_iteration'], dict))
                _require(isinstance(run.get('variants'), list) and all(
                    isinstance(value, dict) and type(value.get('ordinal')) is int
                    and value['ordinal'] == ordinal for ordinal, value in enumerate(run['variants'], 1)))
            rows = self.pipeline.run_variants(run['idea_id'], _variant_count(run),
                production_iteration=run.get('production_iteration'), request_id=run['request_id'], db=db)
            return [{'variant_id': value['id'], 'reservation_id': value['reservation_id'],
                     'ordinal': value['ordinal']} for value in rows]
        except (AppError, ValueError, TypeError, KeyError):
            raise _Hold() from None

    def _source_guard(self, revision=None, bindings_hash=None):
        _require(not self.cancel_event.is_set())
        status = self.video_source.status()
        settings = status['settings']
        ids = settings['page_ids']
        _require(status['configured'] and settings['enabled'] and len(ids) == len(set(ids)) == 14
                 and settings['daily_time'] == '06:00' and settings['timezone'] == 'America/Los_Angeles'
                 and type(settings['revision']) is int and settings['revision'] > 0
                 and (revision is None or settings['revision'] == revision))
        if bindings_hash is not None:
            # The runner hashes the persisted settings (not status decorations).
            with self.store.connect() as db:
                row = db.execute('SELECT settings FROM video_source_settings WHERE id=1').fetchone()
            _require(row is not None and hashlib.sha256(_canonical(json.loads(row['settings']))).hexdigest() == bindings_hash)
        for page_id in ids:
            self.manager._edit_source(page_id, settings['connection_id'])
        _safe_dir(Path(self.video_source.directory).absolute())
        return settings['revision']

    def _bounded(self, files, value, expected, suffixes, limit, *, image=False, decode=False):
        with files.opened(value, suffixes) as (path, stream):
            size = os.fstat(stream.fileno()).st_size
            _require(0 < size <= limit)
            digest, total, chunks, header = hashlib.sha256(), 0, [], bytearray()
            while block := stream.read(1024 * 1024):
                _require(not self.cancel_event.is_set())
                total += len(block)
                _require(total <= limit)
                digest.update(block)
                if image:
                    chunks.append(block)
                if len(header) < HEADER_BYTES:
                    header.extend(block[:HEADER_BYTES - len(header)])
            _require(total == size and digest.hexdigest() == expected)
            if image:
                _thumbnail(b''.join(chunks))
            if decode:
                _validate_header(header, size)
                self._decode(path)
        return str(path)

    def _decode(self, path):
        _require(self.ffmpeg_path is not None and self.ffmpeg_path.is_file())
        command = [str(self.ffmpeg_path), '-hide_banner', '-nostdin', '-nostats', '-v', 'info',
                   '-xerror', '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-i', str(path),
                   '-map', '0:v:0', '-map', '0:a:0', '-threads', '1', '-progress', 'pipe:1',
                   '-f', 'null', '-']
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs, overflow = [bytearray(), bytearray()], threading.Event()
        def reader(stream, output):
            try:
                while block := stream.read(65536):
                    if len(output) + len(block) > MAX_JSON_BYTES:
                        overflow.set(); process.kill(); break
                    output.extend(block)
            finally:
                stream.close()
        threads = [threading.Thread(target=reader, args=(stream, output), daemon=True)
                   for stream, output in zip((process.stdout, process.stderr), outputs)]
        for thread in threads:
            thread.start()
        try:
            deadline = time.monotonic() + 90
            while True:
                result = process.poll()
                if result is not None:
                    break
                _require(not self.cancel_event.is_set() and time.monotonic() < deadline)
                self.cancel_event.wait(.1)
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
        _require(result == 0 and not overflow.is_set() and all(not thread.is_alive() for thread in threads))
        progress = outputs[0].decode('utf-8', 'replace')
        log = outputs[1].decode('utf-8', 'replace').split('Output #', 1)[0]
        video = next((line for line in log.splitlines() if 'Video:' in line), '')
        audio = next((line for line in log.splitlines() if 'Audio:' in line), '')
        frames = re.findall(r'^frame=([0-9]+)$', progress, re.M)
        times = re.findall(r'^out_time_us=([0-9]+)$', progress, re.M)
        _require(re.search(r'Video: h264\b', video) and re.search(r'\b1080x1920\b', video)
                 and re.search(r'\b30(?:\.0+)? fps\b', video) and re.search(r'Audio: aac\b', audio)
                 and frames and int(frames[-1]) == 240 and times
                 and abs(int(times[-1]) / 1_000_000 - 8) <= .08 and 'progress=end' in progress)

    def _campaign(self, run, value):
        fields = {'kind', 'url', 'source_url', 'shirt_color', 'evidence'}
        _require(isinstance(value, dict) and set(value) == fields
                 and value['kind'] in ('new', 'existing') and isinstance(value['url'], str)
                 and CAMPAIGN_RE.fullmatch(value['url']) and value['shirt_color'] in (None, 'black'))
        snapshot = run['source_snapshot']
        source = snapshot.get('campaign_source')
        black = snapshot.get('black_campaign_source')
        sources = {entry['campaign_url'] for entry in (source, black) if isinstance(entry, dict)
                   and entry.get('shop') == 'blueox179.com' and entry.get('verified') is True
                   and entry.get('campaign_url') in (SOURCE_URL, BLACK_SOURCE_URL)}
        if value['kind'] == 'new':
            _require(value['source_url'] in sources)
        else:
            _require(value['source_url'] is None or value['source_url'] in sources)
        if run.get('review_policy')=='manual_review_v1':
            _require(value['source_url'] is None if value['kind']=='existing' else value['source_url'] in sources)
            if value['kind']=='new':
                _require(value['shirt_color']==('black' if value['source_url']==SOURCE_URL else None))
        elif value['source_url'] == BLACK_SOURCE_URL:
            _require(value['shirt_color'] == 'black')
        elif value['kind'] == 'new':
            _require(value['shirt_color'] is None)
        evidence = value['evidence']
        _require(isinstance(evidence, dict) and set(evidence) ==
                 {'observed_at', 'artwork_matches', 'quote_matches', 'public_url'}
                 and evidence['artwork_matches'] is True and evidence['quote_matches'] is True
                 and evidence['public_url'] == value['url'])
        observed = datetime.fromisoformat(evidence['observed_at'].replace('Z', '+00:00'))
        _require(observed.tzinfo is not None and observed <= datetime.now(timezone.utc) + timedelta(minutes=5))
        return value

    def _validate(self, run, directory):
        _uuid(run['id']); _uuid(run['idea_id']); _uuid(run['request_id'])
        _require(type(run['fence']) is int and run['fence'] > 0)
        bridge=self._run_bridge(run,directory)
        _safe_dir(directory)
        self._run_guard(run)
        idea = self.pipeline.idea(run['idea_id'])
        _require(idea['artwork_sha256'] == run['artwork_sha256'] and idea['filename'] == run['filename'])
        count = _variant_count(run)
        canonical = self._canonical_variants(run)
        _require(len(canonical) == count and canonical == run['variants'])
        files = bridge.Files(directory)
        worker, _, _ = files.json(str(directory / 'worker-output.json'))
        fields = {'schema_version', 'run_id', 'idea_id', 'artwork_sha256', 'lease_fence', 'campaign',
                  'job_path', 'manifest_path', 'result_path', 'status', 'reason'}
        _require(isinstance(worker, dict) and set(worker) == fields and worker['schema_version'] == '1.0'
                 and worker['run_id'] == run['id'] and worker['idea_id'] == run['idea_id']
                 and worker['artwork_sha256'] == run['artwork_sha256']
                 and type(worker['lease_fence']) is int and worker['lease_fence'] == run['fence']
                 and worker['status'] == worker['reason'] == 'ready')
        for key, name in (('job_path', 'job.json'), ('manifest_path', 'manifest.json'), ('result_path', 'result.json')):
            _require(isinstance(worker[key], str) and Path(worker[key]) == directory / name)
        campaign = self._campaign(run, worker['campaign'])
        job, _, _ = files.json(worker['job_path'])
        manifest, _, _ = files.json(worker['manifest_path'])
        pairs = [{k: v[k] for k in ('variant_id', 'reservation_id')} for v in canonical]
        staged = directory / ('source' + Path(run['filename']).suffix.lower())
        _require(job.get('idea_id') == run['idea_id'] and job.get('source_sha256') == run['artwork_sha256']
                 and Path(job.get('source_path', '')) == staged and job.get('variants') == pairs
                 and job.get('campaign_url') == campaign['url'] and job.get('campaign_verified') is True
                 and job.get('lease_fence') == run['fence'] and job.get('trigger_mode') == run['trigger_mode']
                 and _variant_count(job) == count
                 and ('variant_count' not in run or job.get('variant_count') == count))
        self._bounded(files, str(staged), run['artwork_sha256'], {'.png', '.jpg', '.jpeg', '.webp'}, MAX_IMAGE_BYTES, image=True)
        _require(isinstance(manifest.get('variants'), list) and len(manifest['variants']) == count)
        # Bound every role before the producer validator's full hash pass.
        for variant in manifest['variants']:
            _require(isinstance(variant, dict))
            for role, maximum, suffixes in (('video', MAX_VIDEO_BYTES, {'.mp4'}),
                    ('image', MAX_IMAGE_BYTES, {'.png', '.jpg', '.jpeg', '.webp'})):
                with files.opened(variant.get(role + '_path'), suffixes) as (_, stream):
                    _require(0 < os.fstat(stream.fileno()).st_size <= maximum)
        rebuilt = bridge.build_result(worker['job_path'], worker['manifest_path'], root=directory)
        result, result_sha, _ = files.json(worker['result_path'])
        _require(result == rebuilt and result['status'] == 'verified_for_handoff'
                 and all(v['ready_for_handoff'] and v['state'] == 'verified_for_handoff' for v in result['variants']))
        if 'audio_assignments' in run:
            assignments = run['audio_assignments']
            _require(isinstance(assignments,list) and len(assignments)==count)
            for variant,assignment in zip(result['variants'],assignments):
                _require(isinstance(assignment,dict) and isinstance(assignment.get('source_sha256'),str)
                         and re.fullmatch(r'[a-f0-9]{64}',assignment['source_sha256'])
                         and variant.get('audio_source_sha256')==assignment['source_sha256'])
        for variant in result['variants']:
            self._run_guard(run)
            _require(variant['caption'] == campaign['url'] and variant['caption_mode'] == 'product_link_only')
            self._bounded(files, variant['image_path'], variant['image_sha256'],
                          {'.png', '.jpg', '.jpeg', '.webp'}, MAX_IMAGE_BYTES, image=True)
            self._bounded(files, variant['video_path'], variant['video_sha256'], {'.mp4'}, MAX_VIDEO_BYTES, decode=True)
            self._audio_mix_guard(run, variant)
            self._run_guard(run)
        return files, idea, campaign, result, result_sha

    def _audio_mix_guard(self, run, variant):
        """Verify approved PCM independently; worker reports never grant approval."""
        evidence = variant.get('qa_evidence',{}).get('audio',{})
        policy = run.get('audio_mix_policy')
        if policy is None:
            # Historical listening attestations retain their original contract.
            _require(evidence.get('review_method')=='listened_to_final_mix')
            return
        _require(policy=='human_approved_exact_pcm_v1'
                 and evidence.get('review_method')=='approved_mix_hash_match'
                 and evidence.get('status')=='passed')
        source_sha = variant.get('audio_source_sha256')
        snapshots = run.get('approved_audio_mixes')
        _require(isinstance(snapshots,dict) and isinstance(source_sha,str))
        approved = snapshots.get(source_sha)
        _require(isinstance(approved,dict))
        for key in ('approval_id','pcm_sha256','source_sha256','render_signature'):
            _require(isinstance(approved.get(key),str) and re.fullmatch(r'[a-f0-9]{64}',approved[key]))
        _require(approved['source_sha256']==source_sha
                 and evidence.get('approval_id')==approved['approval_id']
                 and evidence.get('actual_pcm_sha256')==approved['pcm_sha256']
                 and evidence.get('approved_pcm_sha256')==approved['pcm_sha256'])
        verifier = getattr(self.audio_approvals,'verify_video',None)
        _require(callable(verifier))
        verified = verifier(variant['video_path'],source_sha,approval_id=approved['approval_id'])
        _require(isinstance(verified,dict))
        for key in ('approval_id','pcm_sha256','source_sha256','render_signature','sample_rate','channels','duration_seconds'):
            _require(key in approved and verified.get(key)==approved[key])

    def _approve(self, run, idea, campaign, result, result_sha, revision):
        stamp = datetime.now(timezone.utc).isoformat()
        manual=run.get('review_policy')=='manual_review_v1'
        review_status='manual_review_pending' if manual else 'approved'
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._run_guard(run, db)
            row = db.execute('SELECT campaign_state,campaign FROM idea_pipeline_ideas WHERE id=?', (idea['id'],)).fetchone()
            if row['campaign_state'] == 'verified':
                _require(json.loads(row['campaign'])['campaign_url'] == campaign['url'])
            else:
                _require(row['campaign_state'] in ('prepared', 'submitting', 'uncertain'))
            for variant in result['variants']:
                histories = db.execute('''SELECT j.request_id,j.caption FROM reel_jobs j
                    JOIN reel_assets a ON a.id=j.asset_id WHERE a.sha256=?''', (variant['video_sha256'],)).fetchall()
                _require(all(j['request_id'] == variant['reservation_id'] and j['caption'] == campaign['url'] for j in histories))
                source_item = db.execute('SELECT id FROM video_source_items WHERE sha256=?', (variant['video_sha256'],)).fetchone()
                _require(source_item is None or source_item['id'] == variant['reservation_id'])
                existing = db.execute('SELECT * FROM idea_pipeline_approved_videos WHERE video_sha256=? OR variant_id=? OR reservation_id=?',
                    (variant['video_sha256'], variant['variant_id'], variant['reservation_id'])).fetchall()
                _require(not existing or len(existing) == 1 and all(existing[0][key] == val for key, val in {
                    'video_sha256': variant['video_sha256'], 'variant_id': variant['variant_id'],
                    'reservation_id': variant['reservation_id'], 'idea_id': idea['id'], 'run_id': run['id'],
                    'caption': campaign['url'], 'source_path': variant['video_path'],
                    'image_path': variant['image_path'], 'image_sha256': variant['image_sha256'],
                    'concept_id': variant['concept_id'], 'result_sha256': result_sha, 'source_revision': revision}.items()))
            if row['campaign_state'] != 'verified':
                binding = {'shop': 'blueox179.com', 'campaign_url': campaign['url'],
                           'campaign_source': campaign['source_url'], 'kind': campaign['kind'],
                           'evidence': campaign['evidence'], 'attested_by': 'manual_worker:' + run['id'],
                           'verification': 'worker_attested'}
                if campaign['shirt_color'] is not None:
                    binding['shirt_color'] = campaign['shirt_color']
                db.execute("UPDATE idea_pipeline_ideas SET campaign_state='verified',campaign=? WHERE id=?",
                           (json.dumps(binding, ensure_ascii=False), idea['id']))
            for index, variant in enumerate(result['variants'], 1):
                self.pipeline._register_video(db, idea['id'], variant['variant_id'], idea['artwork_sha256'],
                    variant['video_sha256'], False, {'skill': 'pod-shirt-pipeline', 'run_id': run['id'], 'concept_id': variant['concept_id']})
                name = 'idea-' + variant['variant_id'] + '.mp4'
                values = (variant['video_sha256'], variant['variant_id'], variant['reservation_id'], idea['id'], run['id'],
                          campaign['url'], variant['video_path'], name, review_status, stamp, stamp,
                          variant['image_path'], variant['image_sha256'], variant['concept_id'], result_sha, revision)
                db.execute('''INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(video_sha256) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at''', values)

    def _has_job(self, variant):
        with self.store.connect() as db:
            return db.execute('''SELECT 1 FROM reel_jobs j JOIN reel_assets a ON a.id=j.asset_id
                WHERE j.request_id=? AND a.sha256=?''', (variant['reservation_id'], variant['video_sha256'])).fetchone() is not None

    @staticmethod
    def _rename_new(temporary, final):
        if os.name == 'nt':
            temporary.rename(final)
        else:
            os.link(temporary, final, follow_symlinks=False)
            temporary.unlink()

    @staticmethod
    def _discard_partial(path, limit):
        _safe_dir(path.parent)
        if not os.path.lexists(path):
            return
        metadata = path.lstat()
        _require(stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
                 and metadata.st_nlink == 1 and not getattr(metadata, 'st_file_attributes', 0) & 0x400
                 and metadata.st_size <= limit)
        path.unlink()

    def _existing(self, path, expected, limit):
        _safe_dir(path.parent)
        if not os.path.lexists(path):
            return False
        metadata = path.lstat()
        _require(stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
                 and metadata.st_nlink == 1 and not getattr(metadata, 'st_file_attributes', 0) & 0x400
                 and 0 < metadata.st_size <= limit)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            start = os.fstat(stream.fileno())
            _require((start.st_dev, start.st_ino, start.st_size, start.st_mtime_ns) ==
                     (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns))
            total = 0
            while block := stream.read(1024 * 1024):
                _require(not self.cancel_event.is_set())
                total += len(block); _require(total <= limit)
                digest.update(block)
            end = os.fstat(stream.fileno())
        current = path.lstat()
        signature = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_nlink)
        _require(signature(metadata) == signature(end) == signature(current) and digest.hexdigest() == expected)
        return True

    def _expose(self, files, run, variant, campaign_url, revision):
        root = Path(self.video_source.directory).absolute()
        final = root / ('idea-' + variant['variant_id'] + '.mp4')
        sidecar = final.with_suffix('.txt')
        caption = campaign_url.encode('utf-8')
        # Detect a name collision before creating a sidecar that might attach
        # this idea's link to an unrelated video already in the watched folder.
        if self._has_job(variant):
            return False
        final_exists = self._existing(final, variant['video_sha256'], MAX_VIDEO_BYTES)
        if not self._existing(sidecar, hashlib.sha256(caption).hexdigest(), 2048):
            temporary = root / ('.caption-' + str(UUID(run['id'])) + '-' + variant['variant_id'] + '.partial')
            self._discard_partial(temporary, 2048)
            try:
                with temporary.open('xb') as output:
                    output.write(caption); output.flush(); os.fsync(output.fileno())
                self._run_guard(run); self._source_guard(revision)
                self._rename_new(temporary, sidecar)
            finally:
                if temporary.exists():
                    temporary.unlink()
        # Never recopy a canonical video already in any queue history.
        if final_exists:
            return False
        temporary = root / ('.video-' + str(UUID(run['id'])) + '-' + variant['variant_id'] + '.partial')
        self._discard_partial(temporary, MAX_VIDEO_BYTES)
        try:
            digest, total = hashlib.sha256(), 0
            with files.opened(variant['video_path'], {'.mp4'}) as (_, source), temporary.open('xb') as output:
                while block := source.read(1024 * 1024):
                    _require(not self.cancel_event.is_set())
                    total += len(block); _require(total <= MAX_VIDEO_BYTES)
                    digest.update(block); output.write(block)
                _require(total > 0 and digest.hexdigest() == variant['video_sha256'])
                output.flush(); os.fsync(output.fileno())
            self._run_guard(run); self._source_guard(revision)
            self._rename_new(temporary, final)
            return True
        finally:
            if temporary.exists():
                temporary.unlink()

    def _accept(self, run, directory):
        approved, copied = 0, 0
        try:
            revision = self._source_guard(run.get('source_revision'), run.get('scheduling_bindings_hash'))
            files, idea, campaign, result, result_sha = self._validate(run, directory)
            self._source_guard(revision)
            for variant in result['variants']:
                final = Path(self.video_source.directory).absolute() / ('idea-' + variant['variant_id'] + '.mp4')
                self._existing(final, variant['video_sha256'], MAX_VIDEO_BYTES)
                self._existing(final.with_suffix('.txt'), hashlib.sha256(campaign['url'].encode('utf-8')).hexdigest(), 2048)
            self._approve(run, idea, campaign, result, result_sha, revision)
            approved = len(result['variants'])
            for variant in result['variants']:
                self._run_guard(run); self._source_guard(revision)
                copied += int(self._expose(files, run, variant, campaign['url'], revision))
                with self.store.connect() as db:
                    db.execute("UPDATE idea_pipeline_approved_videos SET status='ready',updated_at=? WHERE variant_id=? AND run_id=?",
                               (datetime.now(timezone.utc).isoformat(), variant['variant_id'], run['id']))
            return {'accepted': True, 'status': 'ready_for_schedule', 'approved': approved, 'copied': copied,
                    'verification': 'worker_qa_approved',
                    'message': f'Đã kiểm tra {approved} video và bàn giao nguồn lịch hiện có; chưa gọi Meta đăng bài.'}
        except Exception:
            if approved:
                with self.store.connect() as db:
                    db.execute("UPDATE idea_pipeline_approved_videos SET status='needs_review',updated_at=? WHERE run_id=?",
                               (datetime.now(timezone.utc).isoformat(), run['id']))
            return {'accepted': False, 'status': 'needs_review', 'approved': approved, 'copied': copied, 'message': SAFE_MESSAGE}

    async def accept(self, run, run_directory):
        async with self.lock:
            self.cancel_event.clear()
            work = asyncio.create_task(asyncio.to_thread(self._accept, run, Path(run_directory).absolute()))
            try:
                result = await asyncio.shield(work)
            except asyncio.CancelledError:
                self.cancel_event.set()
                # Cancelling an asyncio waiter cannot stop a Python thread.
                # Wait for this exact worker to leave its decode/copy guards
                # before the host can release the application's data lock.
                while not work.done():
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        continue
                # Retrieve any exception without leaking worker/provider data.
                try:
                    work.result()
                except Exception:
                    pass
                raise
            if result['accepted']:
                try:
                    self.video_source.request_scan()
                except Exception:
                    # Durable approvals/files remain ready for the existing
                    # source's next tick. Never create an alternate queue.
                    pass
            return result

    async def accept_item(self,run,run_directory,ordinal):
        from .manual_review_results import accept_item
        async with self.lock:
            self.cancel_event.clear()
            work=asyncio.create_task(asyncio.to_thread(accept_item,self,run,Path(run_directory).absolute(),ordinal))
            try:result=await asyncio.shield(work)
            except asyncio.CancelledError:
                self.cancel_event.set()
                while not work.done():
                    try:await asyncio.shield(work)
                    except asyncio.CancelledError:continue
                with __import__('contextlib').suppress(Exception):work.result()
                raise
            if result['accepted']:
                try:self.video_source.request_scan()
                except Exception:pass
            return result
