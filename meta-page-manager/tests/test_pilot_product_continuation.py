"""Offline private continuation fixtures; never opens the real pilot or uploader."""
from contextlib import contextmanager
import asyncio
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

from filelock import FileLock, Timeout
from PIL import Image
import pytest

from page_manager import pilot_product_continuation as module
from page_manager.errors import AppError


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def held(tmp_path, monkeypatch):
    root = tmp_path / 'manager'; (root / 'data').mkdir(parents=True)
    ideas = tmp_path / 'ideas'; ideas.mkdir()
    producer = tmp_path / 'p'; runs = producer / 'r'
    folder = runs / module.PILOT_RUN_ID; folder.mkdir(parents=True)
    database = root / 'data/page-manager.sqlite3'
    class Store:
        @contextmanager
        def connect(self):
            db = sqlite3.connect(database); db.row_factory = sqlite3.Row
            try:
                with db: yield db
            finally: db.close()
    store = Store(); idea = str(uuid4()); request = str(uuid4())
    Image.new('RGBA', (12, 16), (44, 55, 66, 255)).save(folder / 'source.png')
    source = (folder / 'source.png').read_bytes()
    (ideas / 'pilot.png').write_bytes(source)
    source_hash = sha(folder / 'source.png')
    snapshot = {'campaign_source': {'shop': 'blueox179.com', 'campaign_url': 'https://blueox179.com/campaign/zitat25', 'verified': True},
        'black_campaign_source': {'shop': 'blueox179.com', 'campaign_url': 'https://blueox179.com/campaign/ge2610', 'verified': True}}
    settings = {'enabled': True, 'page_ids': ['123'], 'connection_id': 1}
    variants = [{'variant_id': str(uuid4()), 'reservation_id': str(uuid4()), 'ordinal': n} for n in (1, 2)]
    payload = {'source_snapshot': snapshot, 'source_revision': 2,
        'scheduling_bindings_hash': hashlib.sha256(module._canonical(settings).encode()).hexdigest(),
        'uploader': {'kind': 'local', 'endpoint': 'http://127.0.0.1:8770', 'shop': 'blueox179.com'},
        'variants': variants, 'variant_count': 2, 'caption_mode': 'product_link_only', 'trigger_mode': 'manual_web_run_idea',
        'workflow': {'version': 'fixture-v3', 'sha256': 'a' * 64}}
    encoded = module._canonical(payload); payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    data = {k: v for k, v in payload.items() if k != 'scheduling_bindings_hash'} | {
        'schema_version': '1.0', 'run_id': module.PILOT_RUN_ID, 'idea_id': idea,
        'artwork_sha256': source_hash, 'original_filename': 'pilot.png', 'lease_fence': 2,
        'source_path': str(folder / 'source.png')}
    output = {'run_id': module.PILOT_RUN_ID, 'idea_id': idea, 'artwork_sha256': source_hash,
        'lease_fence': 2, 'status': 'needs_review', 'campaign': None}
    checkpoint = {'run_id': module.PILOT_RUN_ID, 'idea_id': idea, 'artwork_sha256': source_hash,
        'lease_fence': 2, 'external_create_attempted': False, 'campaign_verified': False}
    schema = {'type': 'object', 'properties': {'lease_fence': {'type': 'integer', 'const': 2},
        'unrelated': {'type': 'string', 'const': 'preserved'}}}
    for name in module.INVENTORY_FILES:
        path = folder / name; path.parent.mkdir(exist_ok=True, parents=True)
        if path.exists(): continue
        path.write_bytes((name + ': INERT TEST DATA').encode() if name in module.MEDIA_FILES else b'{"synthetic":true}')
    for name, value in {'run-input.json': data, 'worker-output.json': output,
        'worker-output.schema.json': schema, 'checkpoint-final.json': checkpoint}.items():
        (folder / name).write_text(json.dumps(value))
    with store.connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''CREATE TABLE idea_manual_runs(id TEXT PRIMARY KEY,request_id TEXT,idea_id TEXT,artwork_sha256 TEXT,
            filename TEXT,payload TEXT,payload_hash TEXT,status TEXT,stage TEXT,message TEXT,cancel_requested INTEGER,
            started_at TEXT,finished_at TEXT,fence INTEGER);
            CREATE TABLE idea_auto_batch_settings(id INTEGER PRIMARY KEY,enabled INTEGER);
            CREATE TABLE idea_pipeline_jobs(status TEXT);
            CREATE TABLE idea_pipeline_ideas(id TEXT PRIMARY KEY,artwork_sha256 TEXT,filename TEXT,campaign_state TEXT,campaign TEXT);
            CREATE TABLE idea_pipeline_variants(id TEXT,reservation_id TEXT,ordinal INTEGER,status TEXT,video_sha256 TEXT,idea_id TEXT);
            CREATE TABLE video_source_settings(id INTEGER PRIMARY KEY,settings TEXT,revision INTEGER,configured INTEGER);
            CREATE TABLE idea_pipeline_settings(id INTEGER PRIMARY KEY,settings TEXT);''')
        db.execute('INSERT INTO idea_manual_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (module.PILOT_RUN_ID, request, idea, source_hash, 'pilot.png', encoded, payload_hash,
             'needs_review', 'review', 'fixture', 0, '2026-11-03T00:00:00Z', '2026-11-03T01:00:00Z', 2))
        db.execute('INSERT INTO idea_auto_batch_settings VALUES(1,0)')
        db.execute('INSERT INTO idea_pipeline_ideas VALUES(?,?,?,?,NULL)', (idea, source_hash, 'pilot.png', 'prepared'))
        for variant in variants:
            db.execute('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?,NULL,?)',
                (variant['variant_id'], variant['reservation_id'], variant['ordinal'], 'planned', idea))
        db.execute('INSERT INTO video_source_settings VALUES(1,?,2,1)', (json.dumps(settings),))
        db.execute('INSERT INTO idea_pipeline_settings VALUES(1,?)', (json.dumps(snapshot),))
    monkeypatch.setattr(module, 'video_readiness', lambda *args, **kwargs: {'configuration': {
        'ready': True, 'workflow_version': 'fixture-v3', 'workflow_sha256': 'a' * 64}})
    inventory = {name: sha(folder / name) for name in module.INVENTORY_FILES}
    arguments = {'expected_payload_sha256': payload_hash, 'expected_input_sha256': inventory['run-input.json'],
        'expected_output_sha256': inventory['worker-output.json'], 'expected_checkpoint_sha256': inventory['checkpoint-final.json'],
        'inventory': inventory, 'expected_inventory_sha256': hashlib.sha256(module._canonical(inventory).encode()).hexdigest(),
        'runs_root': runs, 'ideas_root': ideas, 'producer_root': producer, 'process_check': lambda _: True}
    return SimpleNamespace(root=root, store=store, folder=folder, data=data, payload=payload,
        arguments=arguments, database=database)


def scope(f, **changes): return module.product_continuation(f.store, f.root, **(f.arguments | changes))
def row(f):
    with f.store.connect() as db: return dict(db.execute('SELECT * FROM idea_manual_runs WHERE id=?', (module.PILOT_RUN_ID,)).fetchone())


def test_same_uuid_scope_fences_preserves_media_and_legacy_fields_always_holds(held):
    f = held; originals = {name: (f.folder / name).read_bytes() for name in module.SNAPSHOT_FILES}
    with scope(f) as claim:
        assert row(f)['status'] == 'running' and row(f)['fence'] == 3
        claim.guard()
        assert claim.data == f.data | {'lease_fence': 3}
        assert 'audio_assignments' not in claim.data and 'execution_pipeline' not in claim.data
        current = json.loads((f.folder / 'worker-output.schema.json').read_text())
        assert current['properties']['lease_fence']['const'] == 3
        assert current['properties']['unrelated']['const'] == 'preserved'
        for name in module.MEDIA_FILES: assert sha(f.folder / name) == f.arguments['inventory'][name]
        archive = f.folder / '.operator-continuation' / claim.operation_id
        assert all((archive / name).read_bytes() == raw for name, raw in originals.items())
        assert not (archive / 'images').exists() and not (archive / 'review').exists()
        other = FileLock(str(f.root / 'data/page-manager.lock'))
        with pytest.raises(Timeout): other.acquire(timeout=0)
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 3
    with pytest.raises(AppError): claim.guard()
    with pytest.raises(AppError):
        with scope(f): pytest.fail('Once-only scope must not yield again')


def test_exception_and_cancellation_clear_active_authority_before_unlock(held):
    f = held
    with pytest.raises(RuntimeError, match='operator stopped'):
        with scope(f) as claim:
            claim.guard(); raise RuntimeError('operator stopped')
    assert row(f)['status'] == 'needs_review'
    with FileLock(str(f.root / 'data/page-manager.lock'), timeout=0): pass


def test_cancelled_error_also_holds_same_fence(held):
    f = held
    with pytest.raises(asyncio.CancelledError):
        with scope(f): raise asyncio.CancelledError()
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 3


def test_commit_ack_failure_holds_new_fence_instead_of_restoring_old_authority(held):
    f = held; original = f.store.connect; fired = []
    @contextmanager
    def failing_commit():
        with original() as db:
            yield db
            current = db.execute('SELECT status FROM idea_manual_runs WHERE id=?', (module.PILOT_RUN_ID,)).fetchone()
            active = current['status'] == 'running'
        if active and not fired:
            fired.append(True); raise OSError('synthetic lost commit acknowledgment')
    f.store.connect = failing_commit
    with pytest.raises(OSError):
        with scope(f): pytest.fail('Uncertain claim must never yield')
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 3
    assert json.loads((f.folder / 'run-input.json').read_text())['lease_fence'] == 3


def test_persistent_hold_failure_retains_host_lock_until_operator_can_revoke(held):
    f = held; original = f.store.connect; previous = len(module._RETAINED_LOCKS)
    try:
        with pytest.raises(AppError, match='khóa host'):
            with scope(f):
                @contextmanager
                def unavailable():
                    raise sqlite3.OperationalError('synthetic DB unavailable')
                    yield
                f.store.connect = unavailable
        other = FileLock(str(f.root / 'data/page-manager.lock'))
        with pytest.raises(Timeout): other.acquire(timeout=0)
        assert len(module._RETAINED_LOCKS) == previous + 1
    finally:
        f.store.connect = original
        with original() as db: db.execute("UPDATE idea_manual_runs SET status='needs_review'")
        for lock in module._RETAINED_LOCKS[previous:]: lock.release()
        del module._RETAINED_LOCKS[previous:]


@pytest.mark.parametrize('case', ['wrong_run', 'bad_inventory', 'media_change', 'auto_on', 'other_active',
    'client_state', 'external_create', 'producer_busy', 'wrong_workflow', 'changed_variants'])
def test_unsafe_preflight_never_yields_or_changes_fence(held, monkeypatch, case):
    f = held; changes = {}
    if case == 'wrong_run': changes['run_id'] = str(uuid4())
    elif case == 'bad_inventory': changes['expected_inventory_sha256'] = 'e' * 64
    elif case == 'media_change': (f.folder / 'videos/02.mp4').write_bytes(b'changed test fixture')
    elif case == 'client_state': (f.folder / 'uploader-client-state.json').write_text('{}')
    elif case == 'producer_busy': changes['process_check'] = lambda _: False
    elif case == 'wrong_workflow': monkeypatch.setattr(module, 'video_readiness', lambda *a, **k: {'configuration': {'ready': False}})
    elif case == 'external_create':
        path = f.folder / 'checkpoint-final.json'; value = json.loads(path.read_text()); value['external_create_attempted'] = True
        path.write_text(json.dumps(value)); f.arguments['inventory'][path.name] = sha(path)
        f.arguments['expected_checkpoint_sha256'] = sha(path)
        f.arguments['expected_inventory_sha256'] = hashlib.sha256(module._canonical(f.arguments['inventory']).encode()).hexdigest()
    else:
        with f.store.connect() as db:
            if case == 'auto_on': db.execute('UPDATE idea_auto_batch_settings SET enabled=1')
            elif case == 'changed_variants': db.execute("UPDATE idea_pipeline_variants SET status='metadata_registered'")
            else: db.execute("INSERT INTO idea_manual_runs(id,status) VALUES(?,'queued')", (str(uuid4()),))
    with pytest.raises(AppError):
        with scope(f, **changes): pytest.fail('Unsafe preflight must not yield')
    assert row(f)['fence'] == 2 and row(f)['status'] == 'needs_review'
    assert json.loads((f.folder / 'run-input.json').read_text()) == f.data


def test_guard_rechecks_media_and_workflow_while_scope_active(held, monkeypatch):
    f = held
    with scope(f) as claim:
        monkeypatch.setattr(module, 'video_readiness', lambda *a, **k: {'configuration': {'ready': False}})
        with pytest.raises(AppError): claim.guard()
    assert row(f)['status'] == 'needs_review'


def test_claim_file_failure_restores_original_input_and_schema(held, monkeypatch):
    f = held; original = module._replace; calls = []
    def fail_once(path, raw, root):
        calls.append(path.name)
        if len(calls) == 2: raise OSError('synthetic schema write failure')
        return original(path, raw, root)
    monkeypatch.setattr(module, '_replace', fail_once)
    with pytest.raises(OSError):
        with scope(f): pytest.fail('Failed claim must not yield')
    assert row(f)['fence'] == 2 and row(f)['status'] == 'needs_review'
    assert sha(f.folder / 'run-input.json') == f.arguments['expected_input_sha256']
    assert sha(f.folder / 'worker-output.schema.json') == f.arguments['inventory']['worker-output.schema.json']


def test_existing_live_host_lock_blocks_before_authority_write(held):
    f = held
    with FileLock(str(f.root / 'data/page-manager.lock')):
        with pytest.raises(AppError):
            with scope(f): pytest.fail('Host lock must prevent claim')
    assert row(f)['fence'] == 2


def test_ordinary_uploader_binding_works_only_inside_scoped_authority(held):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'scripts/uploader_client.py'
    spec = importlib.util.spec_from_file_location('fixture_continuation_uploader', path)
    client = importlib.util.module_from_spec(spec); spec.loader.exec_module(client)
    f = held
    with pytest.raises(client.ClientError): client.load_binding(f.folder / 'run-input.json', f.store, runs_root=f.folder.parent)
    class ReadOnlyStore:
        @contextmanager
        def connect(self):
            db = sqlite3.connect(f.database.as_uri() + '?mode=ro', uri=True); db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA query_only=ON'); yield db
            finally: db.close()
    with scope(f) as claim:
        assert f.database.with_name(f.database.name + '-wal').exists()
        assert f.database.with_name(f.database.name + '-shm').exists()
        binding = client.load_binding(f.folder / 'run-input.json', ReadOnlyStore(), runs_root=f.folder.parent)
        assert binding['data']['lease_fence'] == 3
        claim.guard()
    with pytest.raises(client.ClientError): client.load_binding(f.folder / 'run-input.json', f.store, runs_root=f.folder.parent)


def revoke(f, operation_id, **changes):
    return module.revoke_aborted_continuation(f.store, f.root, operation_id,
        runs_root=f.arguments['runs_root'], ideas_root=f.arguments['ideas_root'],
        **({'process_check': lambda _: True} | changes))


def test_abort_revocation_holds_only_owned_claim_and_replays_without_retry(held):
    f = held
    with scope(f) as claim:
        operation_id = claim.operation_id
    # Synthetic abrupt-driver aftermath: operation was durable, but no finally.
    with f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET status='running',finished_at=NULL")
        db.execute("UPDATE pilot_product_continuations SET status='active',finished_at=NULL")
    assert revoke(f, operation_id)['replayed'] is False
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 3
    assert revoke(f, operation_id)['replayed'] is True
    assert row(f)['stage'] == 'operator_product_aborted'


@pytest.mark.parametrize('case', ['wrong_operation', 'producer_busy', 'host_locked', 'source_changed', 'other_owner'])
def test_abort_revocation_refuses_wrong_or_live_authority(held, case):
    f = held
    with scope(f) as claim: operation_id = claim.operation_id
    with f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET status='running'")
        db.execute("UPDATE pilot_product_continuations SET status='active'")
        if case == 'other_owner': db.execute("INSERT INTO idea_manual_runs(id,status) VALUES(?,'queued')", (str(uuid4()),))
    if case == 'source_changed': (f.folder / 'images/01.png').write_bytes(b'changed inert fixture')
    changes = {'process_check': lambda _: False} if case == 'producer_busy' else {}
    requested = str(uuid4()) if case == 'wrong_operation' else operation_id
    if case == 'host_locked':
        with FileLock(str(f.root / 'data/page-manager.lock')):
            with pytest.raises(AppError): revoke(f, requested, **changes)
    else:
        with pytest.raises(AppError): revoke(f, requested, **changes)
    assert row(f)['status'] == 'running'


@pytest.fixture
def unsubmitted(held):
    f = held
    with scope(f) as claim: previous_operation_id = claim.operation_id
    workspace = f.folder / ('operator-product-' + previous_operation_id); workspace.mkdir()
    receipt = workspace / 'stage-output.json'
    receipt.write_text(json.dumps({'status': 'needs_review', 'reason': 'upload_unavailable', 'campaign': None}))
    f.previous_operation_id, f.receipt = previous_operation_id, receipt
    f.arguments['inventory'] = {name: sha(f.folder / name) for name in module.INVENTORY_FILES}
    f.arguments['expected_input_sha256'] = f.arguments['inventory']['run-input.json']
    f.arguments['expected_inventory_sha256'] = hashlib.sha256(module._canonical(f.arguments['inventory']).encode()).hexdigest()
    return f


def retry(f, **changes):
    arguments = f.arguments | {'previous_operation_id': f.previous_operation_id,
        'expected_product_receipt_sha256': sha(f.receipt)} | changes
    return module.retry_unsubmitted_product(f.store, f.root, **arguments)


def test_zero_submission_retry_is_separately_once_audited_same_media_fence_four(unsubmitted):
    f = unsubmitted
    with retry(f) as claim:
        claim.guard()
        assert claim.data == f.data | {'lease_fence': 4}
        assert row(f)['fence'] == 4 and row(f)['status'] == 'running'
        archive = f.folder / '.operator-continuation' / claim.operation_id
        assert sha(archive / 'previous-product-receipt.json') == sha(f.receipt)
        assert json.loads((archive / 'run-input.json').read_text())['lease_fence'] == 3
        assert json.loads((f.folder / 'worker-output.json').read_text())['lease_fence'] == 2
        assert all(sha(f.folder / name) == f.arguments['inventory'][name] for name in module.MEDIA_FILES)
        with f.store.connect() as db:
            assert db.execute('SELECT status FROM pilot_product_continuations').fetchone()[0] == 'held'
            assert db.execute('SELECT status FROM pilot_product_retry_audit').fetchone()[0] == 'active'
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 4
    with pytest.raises(AppError):
        with retry(f): pytest.fail('Retry may claim only once')
    with pytest.raises(AppError):
        with scope(f): pytest.fail('Initial one-use protection must remain')


@pytest.mark.parametrize('case', ['wrong_previous', 'bad_receipt_hash', 'receipt_campaign', 'receipt_other_reason',
    'external_checkpoint', 'client_state', 'changed_media_reviewed_again', 'prior_not_held'])
def test_retry_requires_exact_prior_zero_submission_and_unchanged_inventory(unsubmitted, case):
    f = unsubmitted; changes = {}
    if case == 'wrong_previous': changes['previous_operation_id'] = str(uuid4())
    elif case == 'bad_receipt_hash': changes['expected_product_receipt_sha256'] = 'f' * 64
    elif case in ('receipt_campaign', 'receipt_other_reason'):
        value = json.loads(f.receipt.read_text())
        if case == 'receipt_campaign': value['campaign'] = {'url': 'https://blueox179.com/campaign/fixture'}
        else: value['reason'] = 'campaign_uncertain'
        f.receipt.write_text(json.dumps(value))
    elif case == 'external_checkpoint': (f.receipt.parent / 'product-checkpoint.json').write_text('{"state":"submitting"}')
    elif case == 'client_state': (f.folder / 'uploader-client-state.json').write_text('{}')
    elif case == 'prior_not_held':
        with f.store.connect() as db: db.execute("UPDATE pilot_product_continuations SET status='active'")
    else:
        (f.folder / 'videos/02.mp4').write_bytes(b'changed and newly-reviewed inert fixture')
        f.arguments['inventory']['videos/02.mp4'] = sha(f.folder / 'videos/02.mp4')
        f.arguments['expected_inventory_sha256'] = hashlib.sha256(module._canonical(f.arguments['inventory']).encode()).hexdigest()
    with pytest.raises(AppError):
        with retry(f, **changes): pytest.fail('Unsafe retry must never yield')
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 3


def test_retry_exception_holds_fence_four_and_abort_revoke_supports_owned_retry(unsubmitted):
    f = unsubmitted
    with pytest.raises(RuntimeError):
        with retry(f) as claim:
            operation = claim.operation_id
            raise RuntimeError('synthetic operator exit')
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 4
    with f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET status='running'")
        db.execute("UPDATE pilot_product_retry_audit SET status='active'")
    result = module.revoke_aborted_continuation(f.store, f.root, operation, expected_fence=4,
        runs_root=f.arguments['runs_root'], ideas_root=f.arguments['ideas_root'], process_check=lambda _: True)
    assert result['status'] == 'held' and row(f)['status'] == 'needs_review'


@pytest.fixture
def prepared(unsubmitted):
    f = unsubmitted
    with retry(f) as claim: previous = claim.operation_id
    f.prepared_previous = previous
    state = {'run_id': module.PILOT_RUN_ID, 'artwork_sha256': sha(f.folder / 'source.png'),
        'uploader': f.payload['uploader'], 'input_name': 'source.png', 'operations': [
            {'path': '/api/input/upload', 'digest': '1' * 64, 'lease_fence': 4, 'outcome': 'accepted', 'result': {'saved': ['source.png'], 'skipped': []}},
            {'path': '/api/prep/start', 'digest': '2' * 64, 'lease_fence': 4, 'outcome': 'accepted', 'result': {'started': True}},
            {'path': '/api/item/5b5fba3253d9', 'digest': '3' * 64, 'lease_fence': 4, 'outcome': 'accepted', 'result': {}}]}
    (f.folder / 'uploader-client-state.json').write_text(json.dumps(state))
    diagnostics = f.root.parent / 'integration-diagnostics'; diagnostics.mkdir()
    image = diagnostics / 'prepared.png'; image.write_bytes((f.folder / 'source.png').read_bytes())
    review = {'item_id': '5b5fba3253d9', 'source': 'source.png', 'slug': 'sk7828a', 'origin': 'ge2610',
        'shirt': 'black', 'side': 'front', 'approved': True, 'design_input': True, 'extract_ok': True,
        'prepared_sha256': sha(image), 'source_sha256': sha(f.folder / 'source.png'), 'pixels_equal': True,
        'actual_candidate_public_status': 404, 'prepared_path': str(image)}
    f.prepared_review = diagnostics / 'prepared-review.json'; f.prepared_review.write_text(json.dumps(review))
    f.arguments['inventory'] = {name: sha(f.folder / name) for name in module.INVENTORY_FILES}
    f.arguments['expected_input_sha256'] = f.arguments['inventory']['run-input.json']
    f.arguments['expected_inventory_sha256'] = hashlib.sha256(module._canonical(f.arguments['inventory']).encode()).hexdigest()
    return f


def reconcile(f, **changes):
    return module.resume_prepared_product(f.store, f.root, **(f.arguments | {
        'previous_operation_id': f.prepared_previous,
        'expected_state_sha256': sha(f.folder / 'uploader-client-state.json'),
        'prepared_review_path': f.prepared_review,
        'expected_prepared_review_sha256': sha(f.prepared_review)} | changes))


def test_prepared_reconciliation_once_preserves_assigned_slug_and_advances_only_export_upload(prepared):
    f = prepared; state_path = f.folder / 'uploader-client-state.json'; old_state = state_path.read_bytes()
    with reconcile(f) as claim:
        claim.guard()
        assert claim.data['lease_fence'] == 5 and row(f)['fence'] == 5
        archive = f.folder / '.operator-continuation' / claim.operation_id
        assert (archive / 'previous-client-state.json').read_bytes() == old_state
        assert (archive / 'prepared-owned-review.json').read_bytes() == f.prepared_review.read_bytes()
        state = json.loads(old_state)
        state['exported_batch'] = 'owned-batch'
        state['operations'].append({'path': '/api/review/export', 'digest': '4' * 64, 'lease_fence': 5,
            'outcome': 'accepted', 'result': {'batch': 'owned-batch', 'count': 1}})
        state_path.write_text(json.dumps(state)); claim.guard()
        state['operations'].append({'path': '/api/upload/start', 'digest': '5' * 64, 'lease_fence': 5, 'outcome': 'submitting'})
        state_path.write_text(json.dumps(state)); claim.guard()
        state['operations'][-1].update(outcome='accepted', result={'started': True})
        state_path.write_text(json.dumps(state)); claim.guard()
        assert json.loads(f.prepared_review.read_text())['slug'] == 'sk7828a'
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 5
    with pytest.raises(AppError):
        with reconcile(f): pytest.fail('Prepared reconciliation is once only')


@pytest.mark.parametrize('case', ['uncertain', 'exported', 'missing_operation', 'wrong_item', 'wrong_source',
    'wrong_origin', 'prepared_pixel_lie', 'wrong_previous', 'wrong_state_hash', 'external_checkpoint'])
def test_prepared_reconciliation_refuses_unowned_or_already_submitted_state(prepared, case):
    f = prepared; state_path = f.folder / 'uploader-client-state.json'; state = json.loads(state_path.read_text()); changes = {}
    if case == 'uncertain': state['operations'][-1]['outcome'] = 'uncertain'
    elif case == 'exported': state['operations'].append({'path': '/api/review/export', 'digest': 'e' * 64, 'lease_fence': 4, 'outcome': 'accepted', 'result': {}})
    elif case == 'missing_operation': state['operations'].pop()
    elif case == 'wrong_item': state['operations'][-1]['path'] = '/api/item/aaaaaaaaaaaa'
    elif case == 'wrong_source': state['input_name'] = 'foreign.png'
    elif case == 'wrong_previous': changes['previous_operation_id'] = str(uuid4())
    elif case == 'wrong_state_hash': changes['expected_state_sha256'] = 'f' * 64
    elif case == 'external_checkpoint':
        path = f.folder / ('operator-product-' + f.prepared_previous); path.mkdir()
        (path / 'product-checkpoint.json').write_text('{"state":"submitting"}')
    else:
        review = json.loads(f.prepared_review.read_text())
        if case == 'wrong_origin': review['origin'] = 'zitat25'
        else:
            image = __import__('pathlib').Path(review['prepared_path'])
            Image.new('RGBA', (12, 16), (200, 0, 0, 255)).save(image)
            review['prepared_sha256'] = sha(image)
        f.prepared_review.write_text(json.dumps(review))
    state_path.write_text(json.dumps(state))
    with pytest.raises(AppError):
        with reconcile(f, **changes): pytest.fail('Unsafe prepared state must not yield')
    assert row(f)['status'] == 'needs_review' and row(f)['fence'] == 4


def test_prepared_guard_refuses_old_operation_rewrite_or_extra_prep(prepared):
    f = prepared; path = f.folder / 'uploader-client-state.json'
    with reconcile(f) as claim:
        state = json.loads(path.read_text())
        state['operations'].append({'path': '/api/prep/start', 'digest': 'f' * 64, 'lease_fence': 5, 'outcome': 'submitting'})
        path.write_text(json.dumps(state))
        with pytest.raises(AppError): claim.guard()
    assert row(f)['status'] == 'needs_review'


def test_abort_revocation_supports_only_owned_prepared_fence_five(prepared):
    f = prepared
    with reconcile(f) as claim: operation = claim.operation_id
    with f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET status='running'")
        db.execute("UPDATE pilot_product_prepared_audit SET status='active'")
    result = module.revoke_aborted_continuation(f.store, f.root, operation, expected_fence=5,
        runs_root=f.arguments['runs_root'], ideas_root=f.arguments['ideas_root'], process_check=lambda _: True)
    assert result['fence'] == 5 and result['status'] == 'held'
    assert row(f)['status'] == 'needs_review'
