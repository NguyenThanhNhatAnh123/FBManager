"""Private operator recovery of an existing, held producer handoff.

No HTTP route, producer launch, campaign submission, Graph call or alternate
publisher is exposed here. A digest-bound claim only re-enters the existing
independent consumer with the original run/fence/reservations unchanged.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import types
from uuid import UUID, uuid4

from .errors import AppError
from .idea_runs import CLI, MAX_RESULT_BYTES, _parents_safe, _regular
from .idea_run_results import RUNS_DIRECTORY, BRIDGE_PATH, _canonical
from .video_source import DEFAULT_DIRECTORY
from .project_paths import LEGACY_RUNS_DIRECTORY,select_existing_run_directory


MESSAGES = {
    'invalid_recovery': 'Mã lượt chạy hoặc SHA256 chưa hợp lệ.',
    'recovery_output_invalid': 'Đầu ra chưa khớp tệp và danh tính đã đóng băng.',
    'recovery_binding_changed': 'Nguồn hoặc nội dung lượt chạy đã thay đổi.',
    'recovery_producer_busy': 'Chưa xác nhận producer của lượt này đã dừng.',
    'recovery_run_busy': 'Đang có lượt chạy chờ hoặc xử lý; chưa nhận kết quả phục hồi.',
    'recovery_not_held': 'Lượt này không ở trạng thái chờ kiểm tra hợp lệ.',
    'recovery_conflict': 'Lượt này đã có lần phục hồi khác; cần đối chiếu thủ công.',
    'recovery_consumer_rejected': 'Bộ nhận chưa xác nhận đủ số video đã cấp và QA.',
    'recovery_interrupted': 'Đã dừng phục hồi và giữ kết quả để kiểm tra.',
    'recovery_validator_changed': 'Bộ kiểm tra bàn giao không còn khớp bản đã xác minh trước sản xuất.',
}
FROZEN = {'source_snapshot', 'variants', 'source_revision',
          'scheduling_bindings_hash', 'caption_mode', 'trigger_mode'}
FROZEN_OPTIONAL = {'uploader','variant_count','automatic_batch','workflow','audio_assignments','template_policy',
                   'audio_mix_policy','approved_audio_mixes','execution_pipeline','execution_pipeline_sha256','preflight_seed','production_iteration'}
# Captured from the reviewed adapter before this production run. Never read a
# worker's later readiness/manifest to choose the executable validator digest.
TRUSTED_BRIDGE_SHA256 = 'd157b4fb0fef430473bd616b36e41a7b0d85432876ffc8252324bfc614508394'
TRUSTED_BRIDGE_SHA256S = frozenset({TRUSTED_BRIDGE_SHA256,
    '42f21c3c6b6564eb25806d945eb274fcb0e4264264f800b7e76fcaf0e2c496fd',
    '45ffb5cd40348c85522699208da8a6e0ddd80916341a7592ccd4389aed81a4f7',
    'b8f9fff9b5196393d8b8ce7a1175bf0f38ca7b662643be5c7efdfb6fd32119dc',
    # Reviewed 2026-11-06: only adds the separate pending-human receipt validator;
    # the full-set handoff and recovery execution paths remain unchanged.
    '2dacc53474f1da7da93306cdd0626432f399ba8a3e3e702c797c4404bbb55200',
    # Reviewed portability-only change: ABC_ROOT derives from this file's location.
    'c3bd0c08716da0af9ccaf704b102ae047b34d5a58cd46d71612d18ccef349295'})


def trusted_bridge(path=BRIDGE_PATH):
    """Compile once, from exactly the bounded bytes whose pinned hash passed."""
    path = Path(path)
    try:
        if not _regular(path, 256 * 1024):
            raise ValueError()
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read(256 * 1024 + 1)
            after = os.fstat(stream.fileno())
        same = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if (not _regular(path, 256 * 1024) or same(before) != same(after)
                or same(path.stat()) != same(after) or len(raw) != after.st_size
                or len(raw) > 256 * 1024 or hashlib.sha256(raw).hexdigest() not in TRUSTED_BRIDGE_SHA256S):
            raise ValueError()
        module = types.ModuleType('_via_private_recovery_trusted_bridge')
        module.__file__ = str(path)
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        return module
    except (OSError, ValueError, TypeError, SyntaxError):
        fail('recovery_validator_changed')


def fail(code):
    raise AppError(MESSAGES[code], code, 409)


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        fail('invalid_recovery')
    return value


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        fail('invalid_recovery')
    return value


def _parse(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def producer_idle(directory):
    """Fail closed; output contains only one enum, never process arguments."""
    if os.name != 'nt':
        return False
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = f'''$ErrorActionPreference='Stop'
try {{
  $exe={quote(CLI)}; $run={quote(directory.name)}; $folder={quote(directory)}
  $coordinator={quote(Path(__file__).resolve().parents[2]/'scripts'/'run_streaming_producer.py')}
  $items=@(Get-CimInstance Win32_Process -Filter "Name = 'codex.exe'")
  foreach($item in $items) {{
    if(-not $item.ExecutablePath) {{ Write-Output 'UNKNOWN'; exit 0 }}
    if($item.ExecutablePath -ieq $exe) {{
      if(-not $item.CommandLine) {{ Write-Output 'UNKNOWN'; exit 0 }}
      if($item.CommandLine.IndexOf($run,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or
         $item.CommandLine.IndexOf($folder,[StringComparison]::OrdinalIgnoreCase) -ge 0) {{
        Write-Output 'BUSY'; exit 0
      }}
    }}
  }}
  $pythonItems=@(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'")
  foreach($item in $pythonItems) {{
    if(-not $item.CommandLine) {{ Write-Output 'UNKNOWN'; exit 0 }}
    if($item.CommandLine.IndexOf($coordinator,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and
       ($item.CommandLine.IndexOf($run,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $item.CommandLine.IndexOf($folder,[StringComparison]::OrdinalIgnoreCase) -ge 0)) {{
      Write-Output 'BUSY'; exit 0
    }}
  }}
  Write-Output 'IDLE'
}} catch {{ Write-Output 'UNKNOWN'; exit 0 }}'''
    try:
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-EncodedCommand', base64.b64encode(command.encode('utf-16le')).decode('ascii')],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode == 0 and result.stdout.strip() == b'IDLE'
    except (OSError, subprocess.SubprocessError):
        return False


class StoredVideoSource:
    """Read-only view: unlike FolderVideoSource, construction recovers no rows."""
    def __init__(self, manager):
        self.store = manager.store
        self.directory = Path(DEFAULT_DIRECTORY).absolute()

    def status(self):
        with self.store.connect() as db:
            row = db.execute('SELECT settings,configured,revision FROM video_source_settings WHERE id=1').fetchone()
        if row is None:
            fail('recovery_binding_changed')
        return {'configured': row['configured'] == 1,
                'settings': {**_parse(row['settings']), 'revision': row['revision']}}

    def request_scan(self):
        # The live host's normal source timer imports only after completed.
        # An operator process must not create a competing scanner/queue.
        return None


class IdeaRunRecovery:
    def __init__(self, manager, pipeline, consumer, *, runs_directory=RUNS_DIRECTORY,
                 process_check=producer_idle, clock=None):
        self.manager, self.pipeline, self.consumer = manager, pipeline, consumer
        self.store = manager.store
        self.runs_directory = Path(runs_directory).absolute()
        self.legacy_runs_directory=LEGACY_RUNS_DIRECTORY if self.runs_directory==RUNS_DIRECTORY else None
        self.process_check = process_check
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            fail('invalid_recovery')
        return value.astimezone(timezone.utc).isoformat()

    def _producer_idle(self, directory):
        try:
            return self.process_check(directory) is True
        except Exception:
            return False

    def _row(self, run_id, db=None):
        if db is None:
            with self.store.connect() as conn:
                return self._row(run_id, conn)
        row = db.execute('SELECT * FROM idea_manual_runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            fail('recovery_not_held')
        return dict(row)

    def _output(self, directory, digest):
        path = directory / 'worker-output.json'
        try:
            if not _regular(path, MAX_RESULT_BYTES):
                raise ValueError()
            _parents_safe(directory)
            with path.open('rb') as stream:
                before = os.fstat(stream.fileno())
                raw = stream.read(MAX_RESULT_BYTES + 1)
                after = os.fstat(stream.fileno())
            _parents_safe(directory)
            same = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
            if (not _regular(path, MAX_RESULT_BYTES) or same(before) != same(after)
                    or same(path.stat()) != same(after) or len(raw) > MAX_RESULT_BYTES
                    or hashlib.sha256(raw).hexdigest() != digest):
                raise ValueError()
            value = _parse(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError, UnicodeError):
            fail('recovery_output_invalid')

    def _bindings(self, row, *, db=None):
        try:
            payload = _parse(row['payload'])
            count=payload.get('variant_count',7)
            if (not FROZEN.issubset(payload) or set(payload)-FROZEN-FROZEN_OPTIONAL
                    or type(count) is not int or not 1<=count<=14
                    or hashlib.sha256(_canonical(payload)).hexdigest() != row['payload_hash']
                    or payload['caption_mode'] != 'product_link_only'
                    or payload['trigger_mode'] not in ('manual_web_run_idea','automatic_idea_batch')
                    or payload.get('audio_mix_policy') not in (None,'human_approved_exact_pcm_v1')
                    or payload['trigger_mode']=='automatic_idea_batch' and ('variant_count' not in payload or not isinstance(payload.get('automatic_batch'),dict))
                    or type(row['fence']) is not int or row['fence'] < 1):
                raise ValueError()
            if 'preflight_seed' in payload:
                # Server-frozen provenance only. Recovery never opens this path
                # or uses it to select media; the consumer hashes the actual
                # run-local images, videos and independent QA as before.
                seed=payload['preflight_seed']
                if (not isinstance(seed,dict) or set(seed)!= {'source_sha256','image_path','image_sha256','origin'}
                        or seed['source_sha256']!=row['artwork_sha256'] or seed['origin']!='codex_native_imagegen'
                        or not isinstance(seed['image_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',seed['image_sha256'])
                        or not isinstance(seed['image_path'],str) or not 0<len(seed['image_path'])<=1024
                        or not Path(seed['image_path']).is_absolute() or Path(seed['image_path']).suffix.lower()!='.png'
                        or '..' in Path(seed['image_path']).parts):
                    raise ValueError()
            idea = self.pipeline.idea(row['idea_id'])
            iteration=payload.get('production_iteration')
            if 'production_iteration' in payload:
                if (not isinstance(iteration,dict) or set(iteration)!= {'id','number'}
                        or not isinstance(iteration['id'],str) or str(UUID(iteration['id']))!=iteration['id'] or UUID(iteration['id']).int==0
                        or type(iteration['number']) is not int or not 2<=iteration['number']<=2**63-1
                        or payload['trigger_mode']!='manual_web_run_idea'):
                    raise ValueError()
                try:
                    selected=self.pipeline.run_variants(row['idea_id'],count,production_iteration=iteration,request_id=row['request_id'],db=db)
                except AppError:raise ValueError() from None
            else:selected=[v for v in idea['variants'] if v['ordinal']<=count]
            canonical = [{'variant_id': v['id'], 'reservation_id': v['reservation_id'], 'ordinal': v['ordinal']}
                         for v in selected]
            if (idea['artwork_sha256'] != row['artwork_sha256'] or idea['filename'] != row['filename']
                    or len(canonical) != count or canonical != payload['variants']):
                raise ValueError()
            settings = self.pipeline.status()['settings']
            if any(settings.get(key) != payload['source_snapshot'][key]
                   for key in ('campaign_source', 'black_campaign_source')):
                raise ValueError()
            def schedule(conn):
                value = conn.execute('SELECT settings,revision,configured FROM video_source_settings WHERE id=1').fetchone()
                if (value is None or value['configured'] != 1 or value['revision'] != payload['source_revision']
                        or hashlib.sha256(_canonical(_parse(value['settings']))).hexdigest() != payload['scheduling_bindings_hash']):
                    raise ValueError()
            if db is None:
                with self.store.connect() as conn:
                    schedule(conn)
            else:
                schedule(db)
            return payload
        except (KeyError, TypeError, ValueError):
            fail('recovery_binding_changed')

    def preflight(self, run_id, output_sha):
        run_id, output_sha = _uuid(run_id), _sha(output_sha)
        row = self._row(run_id)
        if row['status'] != 'needs_review' or row['cancel_requested']:
            fail('recovery_not_held')
        payload = self._bindings(row)
        directory=(select_existing_run_directory(run_id,primary_runs=self.runs_directory,legacy_runs=self.legacy_runs_directory)
                   if self.legacy_runs_directory is not None else self.runs_directory/run_id)
        if directory is None:fail('recovery_output_invalid')
        value = self._output(directory, output_sha)
        expected = {'schema_version': '1.0', 'run_id': run_id, 'idea_id': row['idea_id'],
                    'artwork_sha256': row['artwork_sha256'], 'lease_fence': row['fence'],
                    'status': 'ready', 'reason': 'ready',
                    **{key + '_path': str(directory / (key + '.json')) for key in ('job', 'manifest', 'result')}}
        if (set(value) != set(expected) | {'campaign'} or any(value.get(k) != v for k, v in expected.items())
                or type(value['lease_fence']) is not int or not isinstance(value['campaign'], dict)):
            fail('recovery_output_invalid')
        if not self._producer_idle(directory):
            fail('recovery_producer_busy')
        return row, payload, directory

    @staticmethod
    def _audit_schema(db):
        db.execute('''CREATE TABLE IF NOT EXISTS idea_operator_recoveries(
            run_id TEXT PRIMARY KEY,output_sha256 TEXT NOT NULL,operation_id TEXT UNIQUE NOT NULL,
            payload_hash TEXT NOT NULL,fence INTEGER NOT NULL,status TEXT NOT NULL,code TEXT,
            started_at TEXT NOT NULL,finished_at TEXT)''')

    def _replay(self, run_id, output_sha):
        with self.store.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_operator_recoveries'").fetchone()
            row = db.execute('SELECT * FROM idea_operator_recoveries WHERE run_id=?', (run_id,)).fetchone() if exists else None
            if row is None:
                return None
            if row['output_sha256'] != output_sha:
                fail('recovery_conflict')
            current = self._row(run_id, db)
            if row['fence'] != current['fence'] or row['payload_hash'] != current['payload_hash']:
                fail('recovery_conflict')
            return {'run_id': run_id, 'operation_id': row['operation_id'],
                    'accepted': row['status'] == 'completed' and current['status'] == 'completed',
                    'status': row['status'], 'code': row['code'], 'replayed': True}

    def _claim(self, row, output_sha):
        operation_id = str(uuid4())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._audit_schema(db)
            if db.execute('SELECT 1 FROM idea_operator_recoveries WHERE run_id=?', (row['id'],)).fetchone():
                fail('recovery_conflict')
            current = self._row(row['id'], db)
            if (current['status'] != 'needs_review' or current['cancel_requested']
                    or any(current[k] != row[k] for k in ('request_id', 'fence', 'payload_hash', 'payload', 'idea_id', 'artwork_sha256', 'filename'))):
                fail('recovery_not_held')
            if db.execute("SELECT 1 FROM idea_manual_runs WHERE status IN ('queued','preparing','running','importing') LIMIT 1").fetchone():
                fail('recovery_run_busy')
            self._bindings(current, db=db)
            changed = db.execute('''UPDATE idea_manual_runs SET status='importing',stage='operator_recovery',
                message=?,finished_at=NULL WHERE id=? AND status='needs_review' AND cancel_requested=0
                AND fence=? AND payload_hash=?''',
                ('Đang kiểm tra lại bàn giao đã có; không chạy producer hoặc gửi lại sản phẩm.',
                 row['id'], row['fence'], row['payload_hash'])).rowcount
            if changed != 1:
                fail('recovery_not_held')
            db.execute('INSERT INTO idea_operator_recoveries VALUES(?,?,?,?,?,?,?,?,?)',
                       (row['id'], output_sha, operation_id, row['payload_hash'], row['fence'], 'importing', None, self._now(), None))
        return operation_id

    def _finish(self, row, operation_id, accepted, code):
        stamp = self._now()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._row(row['id'], db)
            audit = db.execute('SELECT * FROM idea_operator_recoveries WHERE run_id=?', (row['id'],)).fetchone()
            own = bool(audit and audit['operation_id'] == operation_id and audit['status'] == 'importing'
                       and audit['fence'] == row['fence'] and audit['payload_hash'] == row['payload_hash'])
            valid = bool(own and current['status'] == 'importing' and not current['cancel_requested']
                         and current['fence'] == row['fence'] and current['payload_hash'] == row['payload_hash'])
            if accepted and valid:
                self._bindings(current, db=db)
            accepted = bool(accepted and valid)
            state = 'completed' if accepted else 'needs_review'
            if own:
                db.execute('UPDATE idea_operator_recoveries SET status=?,code=?,finished_at=? WHERE run_id=? AND operation_id=?',
                           (state, None if accepted else code, stamp, row['id'], operation_id))
                if current['status'] == 'importing' and current['fence'] == row['fence'] and current['payload_hash'] == row['payload_hash']:
                    db.execute('UPDATE idea_manual_runs SET status=?,stage=?,message=?,finished_at=? WHERE id=?',
                               (state, 'operator_recovered' if accepted else 'operator_review',
                                f"Bộ nhận đã kiểm chứng {_parse(row['payload']).get('variant_count',7)} video từ bàn giao gốc; chưa đăng lên Meta." if accepted else MESSAGES[code],
                                stamp, row['id']))
                if not accepted:
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_pipeline_approved_videos'").fetchone()
                    if exists:
                        db.execute("UPDATE idea_pipeline_approved_videos SET status='needs_review',updated_at=? WHERE run_id=?", (stamp, row['id']))
        return {'run_id': row['id'], 'operation_id': operation_id, 'accepted': accepted,
                'status': state, 'code': None if accepted else code, 'replayed': False}

    async def recover(self, run_id, output_sha):
        run_id, output_sha = _uuid(run_id), _sha(output_sha)
        replay = await asyncio.to_thread(self._replay, run_id, output_sha)
        if replay is not None:
            return replay
        row, payload, directory = await asyncio.to_thread(self.preflight, run_id, output_sha)
        claiming = asyncio.create_task(asyncio.to_thread(self._claim, row, output_sha))
        try:
            operation_id = await asyncio.shield(claiming)
        except asyncio.CancelledError:
            # A SQLite writer thread cannot be cancelled. Join this exact
            # claim before recording a terminal hold; never leave a late claim.
            while not claiming.done():
                try:
                    await asyncio.shield(claiming)
                except asyncio.CancelledError:
                    continue
            operation_id = claiming.result()
            self._finish(row, operation_id, False, 'recovery_interrupted')
            raise
        try:
            if not await asyncio.to_thread(self._producer_idle, directory):
                fail('recovery_producer_busy')
            await asyncio.to_thread(self._output, directory, output_sha)
            internal = {**row, **payload, 'status': 'importing', 'lease_fence': row['fence'],
                        'run_directory': str(directory)}
            outcome = await self.consumer.accept(internal, directory)
            accepted = (isinstance(outcome, dict) and outcome.get('accepted') is True
                        and type(outcome.get('approved')) is int and outcome['approved'] == payload.get('variant_count',7))
            await asyncio.to_thread(self._output, directory, output_sha)
            finishing = asyncio.create_task(asyncio.to_thread(self._finish, row, operation_id, accepted, 'recovery_consumer_rejected'))
            try:
                return await asyncio.shield(finishing)
            except asyncio.CancelledError:
                # If cancellation races the final transaction, settle it before
                # exiting. Its durable audit is authoritative on a later read.
                while not finishing.done():
                    try:
                        await asyncio.shield(finishing)
                    except asyncio.CancelledError:
                        continue
                finishing.result()
                raise
        except asyncio.CancelledError:
            # Existing consumer joins its own threads before propagating this.
            self._finish(row, operation_id, False, 'recovery_interrupted')
            raise
        except BaseException as error:
            code = error.code if isinstance(error, AppError) and error.code in MESSAGES else 'recovery_consumer_rejected'
            # This terminal SQLite write is bounded and synchronous in the
            # private operator process, so another cancellation cannot detach
            # an unjoined last writer thread.
            return self._finish(row, operation_id, False, code)
