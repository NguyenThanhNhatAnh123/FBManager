"""One private, scoped product continuation for the existing two-video pilot.

No worker, uploader, network, consumer, scheduling or publishing code runs here.
The operator holds the host lock and an RW/query-only WAL anchor for the entire
context. Existing media never moves or changes; historical JSON is copied first.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from filelock import FileLock, Timeout

from .errors import AppError
from .idea_recovery import producer_idle
from .idea_workspace import video_readiness
from .project_paths import RUNS_DIRECTORY, IDEA_DIRECTORY, PRODUCER_ROOT

PILOT_RUN_ID = '52a95426-120a-4b35-bdd3-3bf19bd9df2d'
MEDIA_FILES = ('source.png', 'images/01.png', 'images/02.png', 'videos/01.mp4', 'videos/02.mp4')
QA_KINDS = ('visual', 'audio', 'technical', 'distinct_variant', 'caption')
SNAPSHOT_FILES = ('run-input.json', 'worker-output.json', 'worker-output.schema.json',
    'checkpoint.json', 'checkpoint-final.json', 'job.json', 'manifest.json', 'result.json', 'audio-plan.json',
    'videos/01.verification.json', 'videos/02.verification.json',
    *(f'qa/{number:02}-{kind}.json' for number in (1, 2) for kind in QA_KINDS))
INVENTORY_FILES = (*MEDIA_FILES, *SNAPSHOT_FILES)
BOUND_FIELDS = ('source_snapshot', 'source_revision', 'uploader', 'template_policy', 'variants',
    'variant_count', 'caption_mode', 'trigger_mode', 'automatic_batch', 'workflow',
    'audio_assignments', 'audio_mix_policy', 'approved_audio_mixes', 'execution_pipeline', 'execution_pipeline_sha256')
ACTIVE = ('queued', 'preparing', 'running', 'importing')
_RETAINED_LOCKS = []  # Fail closed if durable authority revocation is unavailable.


def _fail():
    raise AppError('Pilot vẫn được giữ; bằng chứng hoặc quyền tiếp tục chưa hợp lệ.', 'pilot_continuation_rejected', 409)


def _require(value):
    if not value: _fail()


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _safe(path, root, *, directory=False):
    path, root = Path(path), Path(root)
    _require(path.is_absolute() and path.is_relative_to(root) and path.resolve() == path)
    for item in (path, *path.parents):
        info = item.lstat()
        _require(not item.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400)
    info = path.stat()
    _require(stat.S_ISDIR(info.st_mode) if directory else
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= 1024 * 1024 * 1024)
    return path


def _bytes(path, root):
    path = _safe(path, root)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno()); raw = stream.read(before.st_size + 1); after = os.fstat(stream.fileno())
    signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    _require(len(raw) == before.st_size and len(raw) <= 1024 * 1024 * 1024 and signature(before) == signature(after))
    _safe(path, root)
    return raw


def _sha(path, root): return hashlib.sha256(_bytes(path, root)).hexdigest()


def _read(path, root):
    raw = _bytes(path, root); _require(len(raw) <= 2 * 1024 * 1024)
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result); result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
        parse_constant=lambda _: _fail())


def _replace(path, raw, root):
    _safe(path, root)
    temporary = path.with_name(path.name + '.continuation-tmp')
    with temporary.open('xb') as output:
        output.write(raw); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)


def _now(): return datetime.now(timezone.utc).isoformat()


def _row(store, run_id, db=None):
    if db is None:
        with store.connect() as connection: return _row(store, run_id, connection)
    value = db.execute('SELECT * FROM idea_manual_runs WHERE id=?', (run_id,)).fetchone()
    _require(value is not None)
    return dict(value)


def _database(db, row, payload):
    _require(not db.execute("SELECT 1 FROM idea_manual_runs WHERE id<>? AND status IN ('queued','preparing','running','importing') LIMIT 1", (row['id'],)).fetchone())
    settings = db.execute('SELECT enabled FROM idea_auto_batch_settings WHERE id=1').fetchone()
    _require(settings is not None and settings['enabled'] == 0)
    _require(not db.execute("SELECT 1 FROM idea_pipeline_jobs WHERE status NOT IN ('completed','cancelled') LIMIT 1").fetchone())
    idea = db.execute('SELECT * FROM idea_pipeline_ideas WHERE id=?', (row['idea_id'],)).fetchone()
    _require(idea is not None and idea['artwork_sha256'] == row['artwork_sha256'] and idea['filename'] == row['filename']
        and idea['campaign_state'] == 'prepared' and idea['campaign'] is None)
    variants = [dict(v) for v in db.execute('SELECT id AS variant_id,reservation_id,ordinal,status,video_sha256 FROM idea_pipeline_variants WHERE idea_id=? ORDER BY ordinal', (row['idea_id'],))]
    _require(len(variants) >= 2 and all(v['status'] == 'planned' and v['video_sha256'] is None for v in variants))
    _require([{key: value[key] for key in ('variant_id', 'reservation_id', 'ordinal')} for value in variants[:2]] == payload['variants'])
    source = db.execute('SELECT settings,revision,configured FROM video_source_settings WHERE id=1').fetchone()
    _require(source is not None and source['configured'] == 1 and source['revision'] == payload['source_revision']
        and hashlib.sha256(_canonical(json.loads(source['settings'])).encode()).hexdigest() == payload['scheduling_bindings_hash'])
    pipeline = db.execute('SELECT settings FROM idea_pipeline_settings WHERE id=1').fetchone()
    current = json.loads(pipeline['settings']) if pipeline else {}
    _require(all(current.get(key) == payload['source_snapshot'].get(key) for key in ('campaign_source', 'black_campaign_source')))


def _prepared_evidence(folder, root, payload, proof):
    from PIL import Image
    previous, state_sha, review_path, review_sha = proof
    _require(_sha(folder / 'uploader-client-state.json', folder) == state_sha)
    review_path = _safe(review_path, root.parent / 'integration-diagnostics')
    _require(_sha(review_path, root.parent / 'integration-diagnostics') == review_sha)
    review = _read(review_path, root.parent / 'integration-diagnostics')
    _require(isinstance(review, dict) and set(review) == {'item_id', 'source', 'slug', 'origin', 'shirt', 'side',
        'approved', 'design_input', 'extract_ok', 'prepared_sha256', 'source_sha256', 'pixels_equal',
        'actual_candidate_public_status', 'prepared_path'})
    _require(isinstance(review['item_id'], str) and re.fullmatch(r'[a-f0-9]{12}', review['item_id'])
        and review['source'] == 'source.png' and isinstance(review['slug'], str)
        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', review['slug'])
        and review['origin'] == 'ge2610' and review['shirt'] == 'black' and review['side'] == 'front'
        and all(review[key] is True for key in ('approved', 'design_input', 'extract_ok', 'pixels_equal'))
        and type(review['actual_candidate_public_status']) is int and review['actual_candidate_public_status'] == 404
        and _digest(review['prepared_sha256']) and _digest(review['source_sha256']))
    state = _read(folder / 'uploader-client-state.json', folder)
    _require(isinstance(state, dict) and set(state) == {'run_id', 'artwork_sha256', 'uploader', 'input_name', 'operations'}
        and state['run_id'] == PILOT_RUN_ID and state['artwork_sha256'] == review['source_sha256']
        and state['uploader'] == payload['uploader'] and state['input_name'] == 'source.png'
        and isinstance(state['operations'], list) and len(state['operations']) == 3)
    paths = ('/api/input/upload', '/api/prep/start', '/api/item/' + review['item_id'])
    for operation, path in zip(state['operations'], paths):
        _require(isinstance(operation, dict) and set(operation) == {'path', 'digest', 'lease_fence', 'outcome', 'result'}
            and operation['path'] == path and _digest(operation['digest'])
            and type(operation['lease_fence']) is int and operation['lease_fence'] == 4
            and operation['outcome'] == 'accepted' and isinstance(operation['result'], dict))
    _require(len({operation['digest'] for operation in state['operations']}) == 3
        and state['operations'][0]['result'] == {'saved': ['source.png'], 'skipped': []}
        and state['operations'][1]['result'] == {'started': True}
        and state['operations'][1]['result'].get('started') is True)
    prepared = _safe(Path(review['prepared_path']), root.parent / 'integration-diagnostics')
    _require(_sha(prepared, root.parent / 'integration-diagnostics') == review['prepared_sha256']
        and _sha(folder / 'source.png', folder) == review['source_sha256'])
    with Image.open(prepared) as image, Image.open(folder / 'source.png') as source:
        _require(image.format == 'PNG' and image.size == source.size
            and image.convert('RGBA').tobytes() == source.convert('RGBA').tobytes())
    return state, review, review_path


def _prepared_state_guard(folder, original, new_fence):
    """Accepted preparation is immutable; only export then upload may append."""
    current = _read(folder / 'uploader-client-state.json', folder)
    _require(isinstance(current, dict) and set(current) in (set(original), set(original) | {'exported_batch'})
        and all(current[key] == original[key] for key in ('run_id', 'artwork_sha256', 'uploader', 'input_name'))
        and isinstance(current['operations'], list) and 3 <= len(current['operations']) <= 5
        and current['operations'][:3] == original['operations'])
    if len(current['operations']) == 3: _require('exported_batch' not in current)
    for index, operation in enumerate(current['operations'][3:]):
        _require(isinstance(operation, dict))
        expected = {'path', 'digest', 'lease_fence', 'outcome'}
        if operation.get('outcome') == 'accepted': expected.add('result')
        _require(set(operation) == expected)
        _require(operation['path'] == ('/api/review/export', '/api/upload/start')[index]
            and _digest(operation['digest']) and type(operation['lease_fence']) is int
            and operation['lease_fence'] == new_fence and operation['outcome'] in ('submitting', 'accepted'))
        if index == 1: _require(current['operations'][3]['outcome'] == 'accepted')
    if len(current['operations']) >= 4 and current['operations'][3]['outcome'] == 'accepted':
        result = current['operations'][3]['result']
        _require(isinstance(result, dict) and type(result.get('count')) is int and result['count'] == 1
            and isinstance(result.get('batch'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', result['batch'])
            and current.get('exported_batch') == result['batch'])


@dataclass(frozen=True)
class ProductContinuation:
    operation_id: str
    folder: Path
    data: dict
    guard: object


@contextmanager
def _product_scope(store, root, *, expected_payload_sha256, expected_input_sha256,
        expected_output_sha256, expected_checkpoint_sha256, inventory, expected_inventory_sha256,
        run_id=PILOT_RUN_ID, runs_root=RUNS_DIRECTORY, ideas_root=IDEA_DIRECTORY,
        producer_root=PRODUCER_ROOT, process_check=producer_idle, _retry_proof=None, _prepared_proof=None):
    """Yield one active, fenced product-only authority, then always hold it.

    Optional roots/process_check support isolated fixtures. There is no endpoint,
    bypass flag, requeue, producer launch or automatic retry. The caller must keep
    all its own product children inside an owned job and stop them before exit.
    """
    _require(run_id == PILOT_RUN_ID and isinstance(inventory, dict) and set(inventory) == set(INVENTORY_FILES)
        and all(_digest(value) for value in inventory.values())
        and all(_digest(value) for value in (expected_payload_sha256, expected_input_sha256,
            expected_output_sha256, expected_checkpoint_sha256, expected_inventory_sha256))
        and hashlib.sha256(_canonical(inventory).encode()).hexdigest() == expected_inventory_sha256)
    _require(inventory['run-input.json'] == expected_input_sha256
        and inventory['worker-output.json'] == expected_output_sha256
        and inventory['checkpoint-final.json'] == expected_checkpoint_sha256)
    inventory = dict(inventory)
    retry, prepared = _retry_proof is not None, _prepared_proof is not None
    _require(not (retry and prepared))
    old_fence, new_fence = (4, 5) if prepared else (3, 4) if retry else (2, 3)
    audit_table = 'pilot_product_prepared_audit' if prepared else 'pilot_product_retry_audit' if retry else 'pilot_product_continuations'
    previous_table = 'pilot_product_retry_audit' if prepared else 'pilot_product_continuations'
    root, runs_root, ideas_root, producer_root = map(lambda path: Path(path).absolute(), (root, runs_root, ideas_root, producer_root))
    folder = _safe(runs_root / run_id, runs_root, directory=True)
    lock_path = root / 'data/page-manager.lock'; _safe(lock_path.parent, root, directory=True)
    if os.path.lexists(lock_path):
        info = lock_path.lstat()
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not lock_path.is_symlink()
            and not getattr(info, 'st_file_attributes', 0) & 0x400)
    lock = FileLock(str(lock_path))
    try: lock.acquire(timeout=0)
    except Timeout: _fail()
    operation_id = str(uuid4()); originals = {}; row = None; archive = None; claimed = False
    release_allowed = True
    new_input_sha = new_schema_sha = None
    def hold_once():
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            audit = db.execute(f'SELECT * FROM {audit_table} WHERE run_id=?', (run_id,)).fetchone()
            if audit is not None and audit['operation_id'] == operation_id and audit['status'] == 'active':
                db.execute("UPDATE idea_manual_runs SET status='needs_review',stage='operator_product_held',message=?,finished_at=? WHERE id=? AND fence=? AND status IN ('queued','preparing','running','importing')",
                    ('Tiếp tục sản phẩm đã dừng; giữ nguyên media và chờ kiểm tra bàn giao, chưa đăng lên Meta.', _now(), run_id, audit['new_fence']))
                db.execute(f"UPDATE {audit_table} SET status='held',finished_at=? WHERE run_id=? AND operation_id=?", (_now(), run_id, operation_id))
    def hold():
        nonlocal release_allowed
        release_allowed = False
        for _ in range(3):
            try:
                hold_once()
                current = _row(store, run_id)
                _require(current['fence'] != new_fence or current['status'] not in ACTIVE)
                release_allowed = True
                return
            except Exception:
                pass
        raise AppError('Chưa xác nhận thu hồi quyền; khóa host còn giữ trong tiến trình operator. Kiểm tra DB trước khi đóng tiến trình.',
            'pilot_continuation_hold_unavailable', 503)
    try:
        _require(process_check(folder) is True)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(f'''CREATE TABLE IF NOT EXISTS {audit_table}(
                run_id TEXT PRIMARY KEY,operation_id TEXT UNIQUE NOT NULL,status TEXT NOT NULL,
                old_fence INTEGER NOT NULL,new_fence INTEGER NOT NULL,payload_hash TEXT NOT NULL,
                inventory TEXT NOT NULL,inventory_sha256 TEXT NOT NULL,archive_path TEXT NOT NULL,
                created_at TEXT NOT NULL,finished_at TEXT)''')
            _require(not db.execute(f'SELECT 1 FROM {audit_table} WHERE run_id=?', (run_id,)).fetchone())
            row = _row(store, run_id, db)
            _require(row['status'] == 'needs_review' and not row['cancel_requested'] and type(row['fence']) is int and row['fence'] == old_fence
                and row['payload_hash'] == expected_payload_sha256
                and hashlib.sha256(row['payload'].encode()).hexdigest() == expected_payload_sha256)
            payload = json.loads(row['payload'])
            _require(payload.get('variant_count') == 2 and type(payload.get('variant_count')) is int
                and payload.get('trigger_mode') == 'manual_web_run_idea'
                and 'execution_pipeline' not in payload and 'execution_pipeline_sha256' not in payload)
            _database(db, row, payload)
            if not prepared: _require(not os.path.lexists(folder / 'uploader-client-state.json'))
            receipt = receipt_raw = None
            prepared_state = prepared_review = prepared_review_path = None
            if retry or prepared:
                previous_operation = (_prepared_proof if prepared else _retry_proof)[0]
                _require(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (previous_table,)).fetchone())
                previous = db.execute(f'SELECT * FROM {previous_table} WHERE run_id=?', (run_id,)).fetchone()
                _require(previous is not None and previous['operation_id'] == previous_operation
                    and previous['status'] == 'held' and previous['new_fence'] == old_fence
                    and previous['payload_hash'] == expected_payload_sha256)
                previous_inventory = json.loads(previous['inventory'])
                _require(hashlib.sha256(_canonical(previous_inventory).encode()).hexdigest() == previous['inventory_sha256']
                    and set(previous_inventory) == set(INVENTORY_FILES)
                    and all(previous_inventory[name] == inventory[name] for name in INVENTORY_FILES
                        if name not in ('run-input.json', 'worker-output.schema.json')))
            if retry:
                receipt_sha = _retry_proof[1]
                receipt = folder / ('operator-product-' + previous_operation) / 'stage-output.json'
                _require(not os.path.lexists(receipt.parent / 'product-checkpoint.json'))
                receipt_raw = _bytes(receipt, folder)
                _require(hashlib.sha256(receipt_raw).hexdigest() == receipt_sha)
                _require(_read(receipt, folder) == {'status': 'needs_review', 'reason': 'upload_unavailable', 'campaign': None})
            if prepared:
                _require(not os.path.lexists(folder / ('operator-product-' + previous_operation) / 'product-checkpoint.json'))
                prepared_state, prepared_review, prepared_review_path = _prepared_evidence(folder, root, payload, _prepared_proof)
            for name, expected in inventory.items(): _require(_sha(folder / name, folder) == expected)
            data = _read(folder / 'run-input.json', folder)
            output = _read(folder / 'worker-output.json', folder)
            checkpoint = _read(folder / 'checkpoint-final.json', folder)
            schema = _read(folder / 'worker-output.schema.json', folder)
            for key, expected in {'run_id': run_id, 'idea_id': row['idea_id'], 'artwork_sha256': row['artwork_sha256'], 'lease_fence': 2}.items():
                _require(data.get(key) == (old_fence if key == 'lease_fence' else expected) and output.get(key) == expected
                    and (key not in checkpoint or checkpoint[key] == expected))
            _require(type(data['lease_fence']) is int and type(output['lease_fence']) is int
                and output.get('status') == 'needs_review' and output.get('campaign', 'missing') is None
                and checkpoint.get('external_create_attempted') is False
                and checkpoint.get('campaign_verified') is False
                and data.get('source_path') == str(folder / 'source.png')
                and inventory['source.png'] == row['artwork_sha256']
                and all(data.get(key) == payload.get(key) and (key in data) == (key in payload) for key in BOUND_FIELDS))
            _require(schema['properties']['lease_fence'] == {'type': 'integer', 'const': old_fence})
            original_source = ideas_root / row['filename']
            _require(_sha(original_source, ideas_root) == row['artwork_sha256'])
            configuration = video_readiness(root, workflow_directory=producer_root).get('configuration', {})
            workflow = {'version': configuration.get('workflow_version'), 'sha256': configuration.get('workflow_sha256')}
            _require(configuration.get('ready') is True and payload.get('workflow') == workflow)
            parent = folder / '.operator-continuation'
            parent.mkdir(exist_ok=True); _safe(parent, folder, directory=True)
            archive = parent / operation_id; archive.mkdir()
            for name in SNAPSHOT_FILES:
                source = folder / name; destination = archive / name
                destination.parent.mkdir(parents=True, exist_ok=True); _safe(destination.parent, folder, directory=True)
                raw = _bytes(source, folder)
                _require(hashlib.sha256(raw).hexdigest() == inventory[name])
                with destination.open('xb') as saved: saved.write(raw); saved.flush(); os.fsync(saved.fileno())
                _require(_sha(destination, folder) == inventory[name])
                if name in ('run-input.json', 'worker-output.schema.json'): originals[name] = raw
            if retry:
                with (archive / 'previous-product-receipt.json').open('xb') as saved:
                    saved.write(receipt_raw); saved.flush(); os.fsync(saved.fileno())
                _require(_sha(archive / 'previous-product-receipt.json', folder) == _retry_proof[1])
            if prepared:
                for name, raw, expected in (
                    ('previous-client-state.json', _bytes(folder / 'uploader-client-state.json', folder), _prepared_proof[1]),
                    ('prepared-owned-review.json', _bytes(prepared_review_path, root.parent / 'integration-diagnostics'), _prepared_proof[3])):
                    _require(hashlib.sha256(raw).hexdigest() == expected)
                    with (archive / name).open('xb') as saved:
                        saved.write(raw); saved.flush(); os.fsync(saved.fileno())
            data = {**data, 'lease_fence': new_fence}
            schema['properties']['lease_fence'] = {'type': 'integer', 'const': new_fence}
            input_raw, schema_raw = (_canonical(value).encode() + b'\n' for value in (data, schema))
            release_allowed = False
            _replace(folder / 'run-input.json', input_raw, folder)
            _replace(folder / 'worker-output.schema.json', schema_raw, folder)
            new_input_sha, new_schema_sha = (hashlib.sha256(raw).hexdigest() for raw in (input_raw, schema_raw))
            db.execute(f'INSERT INTO {audit_table} VALUES(?,?,?,?,?,?,?,?,?,?,NULL)',
                (run_id, operation_id, 'active', old_fence, new_fence, expected_payload_sha256, _canonical(inventory), expected_inventory_sha256, str(archive), _now()))
            if retry:
                db.execute('''CREATE TABLE IF NOT EXISTS pilot_product_retry_proof(
                    run_id TEXT PRIMARY KEY,previous_operation_id TEXT NOT NULL,receipt_path TEXT NOT NULL,receipt_sha256 TEXT NOT NULL)''')
                db.execute('INSERT INTO pilot_product_retry_proof VALUES(?,?,?,?)', (run_id, _retry_proof[0], str(receipt), _retry_proof[1]))
            if prepared:
                db.execute('''CREATE TABLE IF NOT EXISTS pilot_product_prepared_proof(
                    run_id TEXT PRIMARY KEY,previous_operation_id TEXT NOT NULL,state_sha256 TEXT NOT NULL,
                    review_path TEXT NOT NULL,review_sha256 TEXT NOT NULL)''')
                db.execute('INSERT INTO pilot_product_prepared_proof VALUES(?,?,?,?,?)',
                    (run_id, _prepared_proof[0], _prepared_proof[1], str(prepared_review_path), _prepared_proof[3]))
            changed = db.execute("UPDATE idea_manual_runs SET status='running',stage='operator_product_continuation',message=?,fence=?,finished_at=NULL WHERE id=? AND status='needs_review' AND fence=? AND payload_hash=? AND cancel_requested=0",
                ('Operator tiếp tục đúng sản phẩm và media đã có; không tạo lại ảnh/video hoặc đăng Page.', new_fence, run_id, old_fence, expected_payload_sha256)).rowcount
            _require(changed == 1)
        claimed = True
        def guard():
            current = _row(store, run_id)
            _require(current['status'] == 'running' and not current['cancel_requested'] and current['fence'] == new_fence
                and current['payload'] == row['payload'] and current['payload_hash'] == expected_payload_sha256)
            with store.connect() as db:
                audit = db.execute(f'SELECT operation_id,status,new_fence FROM {audit_table} WHERE run_id=?', (run_id,)).fetchone()
                _require(audit is not None and tuple(audit) == (operation_id, 'active', new_fence))
                if retry:
                    proof = db.execute('SELECT previous_operation_id,receipt_path,receipt_sha256 FROM pilot_product_retry_proof WHERE run_id=?', (run_id,)).fetchone()
                    previous = db.execute('SELECT operation_id,status,new_fence,payload_hash FROM pilot_product_continuations WHERE run_id=?', (run_id,)).fetchone()
                    _require(proof is not None and tuple(proof) == (_retry_proof[0], str(receipt), _retry_proof[1])
                        and previous is not None and tuple(previous) == (_retry_proof[0], 'held', 3, expected_payload_sha256)
                        and _sha(receipt, folder) == _retry_proof[1])
                if prepared:
                    proof = db.execute('SELECT previous_operation_id,state_sha256,review_path,review_sha256 FROM pilot_product_prepared_proof WHERE run_id=?', (run_id,)).fetchone()
                    previous = db.execute('SELECT operation_id,status,new_fence,payload_hash FROM pilot_product_retry_audit WHERE run_id=?', (run_id,)).fetchone()
                    _require(proof is not None and tuple(proof) == (_prepared_proof[0], _prepared_proof[1], str(prepared_review_path), _prepared_proof[3])
                        and previous is not None and tuple(previous) == (_prepared_proof[0], 'held', 4, expected_payload_sha256))
                _database(db, current, payload)
            _require(_sha(folder / 'run-input.json', folder) == new_input_sha
                and _sha(folder / 'worker-output.schema.json', folder) == new_schema_sha)
            for name in MEDIA_FILES: _require(_sha(folder / name, folder) == inventory[name])
            _require(_sha(original_source, ideas_root) == row['artwork_sha256'])
            if prepared:
                _prepared_state_guard(folder, prepared_state, new_fence)
                _require(_sha(prepared_review_path, root.parent / 'integration-diagnostics') == _prepared_proof[3]
                    and _sha(Path(prepared_review['prepared_path']), root.parent / 'integration-diagnostics') == prepared_review['prepared_sha256'])
            current_config = video_readiness(root, workflow_directory=producer_root).get('configuration', {})
            _require(current_config.get('ready') is True and workflow == {
                'version': current_config.get('workflow_version'), 'sha256': current_config.get('workflow_sha256')})
        # Keep WAL/SHM initialized with an RW-opened, query-only, cursor-free
        # connection. Do not BEGIN a read transaction or retain a stale snapshot.
        with store.connect() as anchor:
            anchor.execute('PRAGMA query_only=ON')
            anchor.execute('SELECT id FROM idea_manual_runs WHERE id=?', (run_id,)).fetchall()
            guard()
            try: yield ProductContinuation(operation_id, folder, data, guard)
            finally: hold()
    except BaseException:
        if claimed:
            hold()
        elif row is not None:
            # A commit may succeed before its caller sees an IO exception. Never
            # restore old authority over a committed claim; hold fence three.
            current = _row(store, run_id)
            if current['fence'] == new_fence:
                hold()
            elif current['fence'] == old_fence and current['status'] == 'needs_review':
                for name, raw in originals.items(): _replace(folder / name, raw, folder)
                release_allowed = True
        raise
    finally:
        if release_allowed: lock.release()
        else: _RETAINED_LOCKS.append(lock)


def product_continuation(store, root, **arguments):
    """The original one-use fence-two to fence-three product context."""
    _require(not {'_retry_proof', '_prepared_proof'} & set(arguments))
    return _product_scope(store, root, **arguments)


def retry_unsubmitted_product(store, root, *, previous_operation_id,
        expected_product_receipt_sha256, **arguments):
    """One separately audited fence-three to fence-four zero-submission retry."""
    from uuid import UUID
    try: valid = isinstance(previous_operation_id, str) and str(UUID(previous_operation_id)) == previous_operation_id and UUID(previous_operation_id).int != 0
    except (ValueError, TypeError, AttributeError): valid = False
    _require(valid and _digest(expected_product_receipt_sha256) and not {'_retry_proof', '_prepared_proof'} & set(arguments))
    return _product_scope(store, root, _retry_proof=(previous_operation_id, expected_product_receipt_sha256), **arguments)


def resume_prepared_product(store, root, *, previous_operation_id, expected_state_sha256,
        prepared_review_path, expected_prepared_review_sha256, **arguments):
    """Once-only reconciliation of three accepted preparation steps, fence 4->5."""
    from uuid import UUID
    try: valid = isinstance(previous_operation_id, str) and str(UUID(previous_operation_id)) == previous_operation_id and UUID(previous_operation_id).int != 0
    except (ValueError, TypeError, AttributeError): valid = False
    _require(valid and _digest(expected_state_sha256) and _digest(expected_prepared_review_sha256)
        and not {'_retry_proof', '_prepared_proof'} & set(arguments))
    return _product_scope(store, root, _prepared_proof=(previous_operation_id, expected_state_sha256,
        Path(prepared_review_path), expected_prepared_review_sha256), **arguments)


def revoke_aborted_continuation(store, root, operation_id, *, expected_fence=3,
        runs_root=RUNS_DIRECTORY, ideas_root=IDEA_DIRECTORY, process_check=producer_idle):
    """Hold only this owned claim after the controller stopped its whole job.

    The host lock and stopped-producer proof are mandatory. This neither accepts
    QA nor retries product work. A held operation replays without another write.
    """
    from uuid import UUID
    try: valid = isinstance(operation_id, str) and str(UUID(operation_id)) == operation_id and UUID(operation_id).int != 0
    except (ValueError, TypeError, AttributeError): valid = False
    _require(valid and type(expected_fence) is int and expected_fence in (3, 4, 5))
    audit_table = {3: 'pilot_product_continuations', 4: 'pilot_product_retry_audit', 5: 'pilot_product_prepared_audit'}[expected_fence]
    root, runs_root, ideas_root = (Path(path).absolute() for path in (root, runs_root, ideas_root))
    folder = _safe(runs_root / PILOT_RUN_ID, runs_root, directory=True)
    lock_path = root / 'data/page-manager.lock'; _safe(lock_path.parent, root, directory=True)
    if os.path.lexists(lock_path):
        info = lock_path.lstat()
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not lock_path.is_symlink()
            and not getattr(info, 'st_file_attributes', 0) & 0x400)
    lock = FileLock(str(lock_path))
    try: lock.acquire(timeout=0)
    except Timeout: _fail()
    try:
        _require(process_check(folder) is True)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _require(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (audit_table,)).fetchone())
            audit = db.execute(f'SELECT * FROM {audit_table} WHERE run_id=?', (PILOT_RUN_ID,)).fetchone()
            current = _row(store, PILOT_RUN_ID, db)
            _require(audit is not None and audit['operation_id'] == operation_id
                and audit['new_fence'] == expected_fence and current['fence'] == expected_fence
                and audit['payload_hash'] == current['payload_hash']
                and hashlib.sha256(current['payload'].encode()).hexdigest() == current['payload_hash'])
            inventory = json.loads(audit['inventory'])
            _require(isinstance(inventory, dict) and set(inventory) == set(INVENTORY_FILES)
                and hashlib.sha256(_canonical(inventory).encode()).hexdigest() == audit['inventory_sha256'])
            for name in MEDIA_FILES: _require(_sha(folder / name, folder) == inventory[name])
            _require(_sha(ideas_root / current['filename'], ideas_root) == current['artwork_sha256'])
            if audit['status'] == 'held' and current['status'] not in ACTIVE:
                return {'run_id': PILOT_RUN_ID, 'operation_id': operation_id,
                    'status': 'held', 'fence': expected_fence, 'replayed': True}
            _require(audit['status'] == 'active' and current['status'] in ('preparing', 'running', 'importing'))
            _database(db, current, json.loads(current['payload']))
            changed = db.execute("UPDATE idea_manual_runs SET status='needs_review',stage='operator_product_aborted',message=?,finished_at=? WHERE id=? AND fence=? AND payload_hash=? AND status IN ('preparing','running','importing')",
                ('Driver sản phẩm đã dừng; quyền tiếp tục đã thu hồi, giữ media để kiểm tra, không tự gửi lại.',
                 _now(), PILOT_RUN_ID, expected_fence, audit['payload_hash'])).rowcount
            _require(changed == 1)
            db.execute(f"UPDATE {audit_table} SET status='held',finished_at=? WHERE run_id=? AND operation_id=? AND status='active'",
                (_now(), PILOT_RUN_ID, operation_id))
        return {'run_id': PILOT_RUN_ID, 'operation_id': operation_id,
            'status': 'held', 'fence': expected_fence, 'replayed': False}
    finally:
        lock.release()
