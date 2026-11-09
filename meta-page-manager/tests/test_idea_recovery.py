"""Private operator recovery fixtures; no live worker, upload, Meta or DB."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from page_manager.idea_recovery import IdeaRunRecovery, StoredVideoSource
import page_manager.idea_recovery as module
from page_manager.idea_run_results import _canonical
from test_idea_run_results import f, media, approved, save, sha, set_dynamic_count  # Synthetic media/QA only.
from test_idea_run_results import iteration_output


@pytest.fixture
def recovery(f):
    f.pipeline.configure(**json.loads(json.dumps(f.run['source_snapshot'])))
    stored = {key: value for key, value in f.source.settings.items() if key != 'revision'}
    payload = {key: f.run[key] for key in ('source_snapshot', 'variants', 'caption_mode', 'trigger_mode', 'source_revision')}
    payload['scheduling_bindings_hash'] = sha(_canonical(stored))
    f.run.update(payload)
    with f.store.connect() as db:
        db.execute('CREATE TABLE video_source_settings(id INTEGER PRIMARY KEY,settings TEXT,revision INTEGER,configured INTEGER)')
        db.execute('INSERT INTO video_source_settings VALUES(1,?,2,1)', (json.dumps(stored),))
        db.execute("UPDATE idea_manual_runs SET status='needs_review',payload=?,payload_hash=? WHERE id=?",
                   (json.dumps(payload), sha(_canonical(payload)), f.run['id']))
    f.recovery = IdeaRunRecovery(f.manager, f.pipeline, f.consumer, runs_directory=f.root,
                                process_check=lambda directory: True)
    f.output_sha = sha((f.directory / 'worker-output.json').read_bytes())
    return f


def recover(f, digest=None):
    return asyncio.run(f.recovery.recover(f.run['id'], digest or f.output_sha))


def state(f):
    with f.store.connect() as db:
        return dict(db.execute('SELECT * FROM idea_manual_runs WHERE id=?', (f.run['id'],)).fetchone())


def audits(f):
    with f.store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='idea_operator_recoveries'").fetchone():
            return []
        return [dict(row) for row in db.execute('SELECT * FROM idea_operator_recoveries')]


def test_legacy_exact_folder_remains_readable_after_new_default_root(recovery,tmp_path):
    f=recovery;before=state(f)
    f.recovery.runs_directory=tmp_path/'new-project/page-manager-bridge/runs'
    f.recovery.legacy_runs_directory=f.root
    row,payload,directory=f.recovery.preflight(f.run['id'],f.output_sha)
    assert directory==f.directory and row['id']==f.run['id'] and payload['variants']==f.run['variants']
    assert state(f)==before and not audits(f)


def test_preflight_reads_frozen_output_but_does_not_claim_or_approve(recovery):
    f = recovery; before = state(f)
    row, payload, directory = f.recovery.preflight(f.run['id'], f.output_sha)
    assert row['id'] == f.run['id'] and directory == f.directory
    assert payload['variants'] == f.run['variants']
    assert state(f) == before and audits(f) == [] and approved(f) == []
    assert not list(f.destination.iterdir())


def test_iteration_recovery_preflight_uses_exact_fresh_mapping_and_preserves_legacy(recovery):
    f=recovery;legacy_ids=[v['id'] for v in f.idea['variants']]
    iteration=iteration_output(f);f.output_sha=sha((f.directory/'worker-output.json').read_bytes())
    row,payload,_=f.recovery.preflight(f.run['id'],f.output_sha)
    assert payload['production_iteration']==iteration and payload['variant_count']==2
    assert not set(legacy_ids)&{v['variant_id'] for v in payload['variants']}
    assert row['status']=='needs_review' and not audits(f) and not approved(f)


@pytest.mark.parametrize('change',['null','extra','number','missing_mapping','request','legacy_prefix'])
def test_iteration_recovery_rejects_changed_authority_without_claim(recovery,change):
    f=recovery;iteration=iteration_output(f);f.output_sha=sha((f.directory/'worker-output.json').read_bytes())
    with f.store.connect() as db:
        payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs WHERE id=?',(f.run['id'],)).fetchone()[0])
        if change=='null':payload['production_iteration']=None
        elif change=='extra':payload['production_iteration']['command']='invalid'
        elif change=='number':payload['production_iteration']['number']=3
        elif change=='missing_mapping':db.execute('DELETE FROM idea_production_iteration_variants WHERE iteration_id=? AND ordinal=2',(iteration['id'],))
        elif change=='request':db.execute('UPDATE idea_production_iterations SET request_id=? WHERE id=?',(str(uuid4()),iteration['id']))
        else:payload['variants']=[{'variant_id':v['id'],'reservation_id':v['reservation_id'],'ordinal':v['ordinal']} for v in f.idea['variants'][:2]]
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=? WHERE id=?',(json.dumps(payload),sha(_canonical(payload)),f.run['id']))
    with pytest.raises(AppError) as caught:f.recovery.preflight(f.run['id'],f.output_sha)
    assert caught.value.code=='recovery_binding_changed'
    assert state(f)['status']=='needs_review' and not audits(f) and not approved(f)


@pytest.mark.parametrize('change',[None,'source','origin','hash','relative','extra'])
def test_recovery_preserves_frozen_seed_provenance_without_opening_seed(recovery,change):
    f=recovery
    path=f.directory.parent/'not-opened-native-probe.png'
    assert not path.exists()
    seed={'source_sha256':f.run['artwork_sha256'],'image_path':str(path),
          'image_sha256':'a'*64,'origin':'codex_native_imagegen'}
    if change=='source':seed['source_sha256']='f'*64
    elif change=='origin':seed['origin']='shell_command'
    elif change=='hash':seed['image_sha256']='not-a-hash'
    elif change=='relative':seed['image_path']='relative.png'
    elif change=='extra':seed['command']='must never execute'
    with f.store.connect() as db:
        payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs WHERE id=?',(f.run['id'],)).fetchone()[0])
        payload['preflight_seed']=seed
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=? WHERE id=?',
            (json.dumps(payload),sha(_canonical(payload)),f.run['id']))
    if change:
        with pytest.raises(AppError) as caught:f.recovery.preflight(f.run['id'],f.output_sha)
        assert caught.value.code=='recovery_binding_changed'
    else:
        row,frozen,_=f.recovery.preflight(f.run['id'],f.output_sha)
        assert frozen['preflight_seed']==seed and row['status']=='needs_review'
        assert not audits(f) and not approved(f) and not path.exists()


def test_actual_consumer_seven_media_recovery_preserves_identity_and_is_idempotent(recovery):
    f = recovery; original = state(f)
    response = recover(f)
    assert response['accepted'] and response['status'] == 'completed' and not response['replayed']
    row = state(f)
    assert row['status'] == 'completed' and row['stage'] == 'operator_recovered'
    for key in ('id', 'request_id', 'idea_id', 'artwork_sha256', 'fence', 'payload', 'payload_hash'):
        assert row[key] == original[key]
    assert len(approved(f)) == 7 and all(v['status'] == 'ready' for v in approved(f))
    assert len(list(f.destination.glob('*.mp4'))) == 7
    assert all(p.read_text() == f.job['campaign_url'] for p in f.destination.glob('*.txt'))
    assert f.store.reel_jobs() == []  # The existing host owns future scheduling.
    times = {p.name: p.stat().st_mtime_ns for p in f.destination.iterdir()}
    f.recovery.process_check = lambda _: (_ for _ in ()).throw(AssertionError('No producer retry'))
    replay = recover(f)
    assert replay['accepted'] and replay['replayed'] and replay['operation_id'] == response['operation_id']
    assert times == {p.name: p.stat().st_mtime_ns for p in f.destination.iterdir()}
    assert len(audits(f)) == 1


def test_dynamic_automatic_recovery_keeps_frozen_count_and_replays_without_generation(recovery):
    f=recovery
    set_dynamic_count(f,2)
    f.output_sha=sha((f.directory/'worker-output.json').read_bytes())
    row,payload,_=f.recovery.preflight(f.run['id'],f.output_sha)
    assert payload['variant_count']==2 and payload['trigger_mode']=='automatic_idea_batch'
    assert recover(f)['accepted'] and len(approved(f))==2
    replay=recover(f)
    assert replay['accepted'] and replay['replayed'] and len(approved(f))==2


@pytest.mark.parametrize('change',[{'variant_count':True},{'variant_count':15},{'unknown_worker_field':True},
    {'variant_count':2,'trigger_mode':'unsupported'}])
def test_recovery_rejects_unknown_or_invalid_dynamic_frozen_contract(recovery,change):
    f=recovery
    with f.store.connect() as db:
        row=db.execute('SELECT payload FROM idea_manual_runs WHERE id=?',(f.run['id'],)).fetchone()
        payload={**json.loads(row['payload']),**change}
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=? WHERE id=?',
                   (json.dumps(payload),sha(_canonical(payload)),f.run['id']))
    with pytest.raises(AppError) as caught:f.recovery.preflight(f.run['id'],f.output_sha)
    assert caught.value.code=='recovery_binding_changed'


@pytest.mark.parametrize('change', ['digest', 'status', 'fence', 'path', 'run_id', 'duplicate_key'])
def test_invalid_output_preflight_keeps_original_hold(recovery, change):
    f = recovery; before = state(f)
    digest = f.output_sha
    if change == 'digest':
        digest = 'a' * 64
    elif change == 'duplicate_key':
        path = f.directory / 'worker-output.json'
        path.write_bytes(b'{"status":"ready","status":"ready"}')
        digest = sha(path.read_bytes())
    else:
        value = json.loads(json.dumps(f.worker))
        if change == 'status': value['status'] = 'needs_review'
        if change == 'fence': value['lease_fence'] += 1
        if change == 'path': value['result_path'] = str(f.directory.parent / 'other.json')
        if change == 'run_id': value['run_id'] = str(uuid4())
        digest = save(f.directory / 'worker-output.json', value)
    with pytest.raises(AppError, match='Đầu ra'):
        recover(f, digest)
    assert state(f) == before and not audits(f) and not approved(f)


@pytest.mark.parametrize('change', ['cancel', 'queued', 'source_revision', 'source_template', 'artwork', 'payload_hash'])
def test_changed_or_cancelled_frozen_run_cannot_be_recovered(recovery, change):
    f = recovery
    with f.store.connect() as db:
        if change == 'cancel': db.execute('UPDATE idea_manual_runs SET cancel_requested=1')
        if change == 'queued': db.execute("UPDATE idea_manual_runs SET status='queued'")
        if change == 'source_revision': db.execute('UPDATE video_source_settings SET revision=3')
        if change == 'artwork': db.execute("UPDATE idea_manual_runs SET artwork_sha256=?", ('b' * 64,))
        if change == 'payload_hash': db.execute("UPDATE idea_manual_runs SET payload_hash=?", ('c' * 64,))
    if change == 'source_template':
        f.pipeline.configure(campaign_source={'shop': 'blueox179.com', 'campaign_url': 'https://blueox179.com/campaign/changed', 'verified': True},
                             black_campaign_source=f.run['source_snapshot']['black_campaign_source'])
    before = state(f)
    with pytest.raises(AppError): recover(f)
    assert state(f) == before and not audits(f) and not approved(f)


@pytest.mark.parametrize('other_status', ['queued', 'preparing', 'running', 'importing'])
def test_other_waiting_or_active_run_blocks_claim(recovery, other_status):
    f = recovery
    with f.store.connect() as db:
        db.execute('INSERT INTO idea_manual_runs(id,request_id,status) VALUES(?,?,?)', (str(uuid4()), str(uuid4()), other_status))
    with pytest.raises(AppError) as error: recover(f)
    assert error.value.code == 'recovery_run_busy'
    assert state(f)['status'] == 'needs_review' and not audits(f) and not approved(f)


@pytest.mark.parametrize('when', ['preflight', 'after_claim'])
def test_producer_inventory_failure_never_imports(recovery, when):
    f = recovery; calls = []
    def idle(_):
        calls.append(1)
        return len(calls) < 2 if when == 'after_claim' else False
    f.recovery.process_check = idle
    if when == 'preflight':
        with pytest.raises(AppError) as error: recover(f)
        assert error.value.code == 'recovery_producer_busy' and not audits(f)
    else:
        result = recover(f)
        assert not result['accepted'] and result['code'] == 'recovery_producer_busy'
        assert audits(f)[0]['status'] == 'needs_review'
    assert state(f)['status'] == 'needs_review' and not approved(f) and not list(f.destination.iterdir())


def test_rejected_real_qa_handoff_stays_held_and_same_digest_never_retries(recovery):
    f = recovery
    value = json.loads((f.directory / 'manifest.json').read_text())
    value['variants'][0]['audio_verified'] = False
    save(f.directory / 'manifest.json', value)
    result = recover(f)
    assert not result['accepted'] and state(f)['status'] == 'needs_review'
    assert not approved(f) and not list(f.destination.iterdir())
    f.consumer.accept = lambda *_: (_ for _ in ()).throw(AssertionError('No auto retry'))
    assert recover(f)['replayed']
    with pytest.raises(AppError) as error: recover(f, 'd' * 64)
    assert error.value.code == 'recovery_conflict'


def test_inventory_exception_is_a_fixed_safe_hold(recovery):
    f = recovery
    f.recovery.process_check = lambda _: (_ for _ in ()).throw(OSError('PRIVATE'))
    with pytest.raises(AppError) as error: recover(f)
    assert error.value.code == 'recovery_producer_busy' and 'PRIVATE' not in str(error.value)
    assert not audits(f) and state(f)['status'] == 'needs_review'


def test_replayed_audit_does_not_bless_changed_fence(recovery):
    f = recovery
    async def rejected(*_): return {'accepted': False}
    f.consumer.accept = rejected
    assert not recover(f)['accepted']
    with f.store.connect() as db: db.execute('UPDATE idea_manual_runs SET fence=fence+1')
    with pytest.raises(AppError) as error: recover(f)
    assert error.value.code == 'recovery_conflict'


def test_changed_output_after_consumer_ack_cannot_complete(recovery):
    f = recovery
    async def changed(*_):
        (f.directory / 'worker-output.json').write_text('{}')
        return {'accepted': True, 'approved': 7}
    f.consumer.accept = changed
    result = recover(f)
    assert not result['accepted'] and result['code'] == 'recovery_output_invalid'
    assert state(f)['status'] == audits(f)[0]['status'] == 'needs_review'


def test_source_change_after_consumer_ack_cannot_complete(recovery):
    f = recovery
    async def changed(*_):
        with f.store.connect() as db: db.execute('UPDATE video_source_settings SET revision=3')
        return {'accepted': True, 'approved': 7}
    f.consumer.accept = changed
    result = recover(f)
    assert not result['accepted'] and result['code'] == 'recovery_binding_changed'
    assert state(f)['status'] == audits(f)[0]['status'] == 'needs_review'


def test_concurrent_same_digest_recovery_calls_consumer_once(recovery):
    f = recovery
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event(); calls = []
        original = f.consumer.accept
        async def waiting(*args):
            calls.append(1); entered.set(); await release.wait()
            return await original(*args)
        f.consumer.accept = waiting
        first = asyncio.create_task(f.recovery.recover(f.run['id'], f.output_sha))
        await asyncio.wait_for(entered.wait(), 3)
        second = await f.recovery.recover(f.run['id'], f.output_sha)
        assert second['replayed'] and second['status'] == 'importing' and not second['accepted']
        release.set()
        assert (await first)['accepted'] and calls == [1]
    asyncio.run(exercise())


def test_cancellation_during_sqlite_claim_joins_then_holds(recovery, monkeypatch):
    f = recovery; entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = f.recovery._claim
    def claim(*args):
        entered.set(); assert release.wait(3)
        result = original(*args); finished.set(); return result
    monkeypatch.setattr(f.recovery, '_claim', claim)
    async def exercise():
        task = asyncio.create_task(f.recovery.recover(f.run['id'], f.output_sha))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel(); await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert finished.is_set() and state(f)['status'] == 'needs_review'
        assert audits(f)[0]['code'] == 'recovery_interrupted' and not approved(f)
    asyncio.run(exercise())


def test_cancellation_during_consumer_does_not_complete(recovery):
    f = recovery
    async def exercise():
        entered = asyncio.Event()
        async def waiting(*_): entered.set(); await asyncio.Future()
        f.consumer.accept = waiting
        task = asyncio.create_task(f.recovery.recover(f.run['id'], f.output_sha))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert state(f)['status'] == audits(f)[0]['status'] == 'needs_review'
    asyncio.run(exercise())


def test_stored_source_view_does_not_reset_live_imports_or_create_scanner(recovery):
    f = recovery
    with f.store.connect() as db:
        db.execute('ALTER TABLE video_source_items ADD COLUMN status TEXT')
        db.execute('ALTER TABLE video_source_items ADD COLUMN job_id INTEGER')
        db.execute("INSERT INTO video_source_items VALUES('unchanged','unchanged','importing',NULL)")
    source = StoredVideoSource(f.manager)
    assert source.status()['settings']['revision'] == 2
    assert source.request_scan() is None
    with f.store.connect() as db:
        row = db.execute('SELECT id,status FROM video_source_items').fetchone()
        assert tuple(row) == ('unchanged', 'importing')
    assert not hasattr(source, 'tick') and not hasattr(source, '_task')


@pytest.mark.parametrize('output,returncode,expected', [(b'IDLE\r\n', 0, True), (b'BUSY\r\n', 0, False), (b'UNKNOWN\r\n', 0, False), (b'IDLE', 1, False), (b'secret IDLE', 0, False)])
def test_windows_inventory_reports_only_exact_idle(monkeypatch, output, returncode, expected):
    monkeypatch.setattr(module.os, 'name', 'nt')
    calls = []
    def run(argv, **options):
        calls.append((argv, options)); return SimpleNamespace(stdout=output, returncode=returncode)
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.producer_idle(Path('safe') / str(uuid4())) is expected
    assert calls[0][1]['stderr'] == module.subprocess.DEVNULL
    assert '-EncodedCommand' in calls[0][0]


def test_windows_inventory_exception_fails_closed(monkeypatch):
    monkeypatch.setattr(module.os, 'name', 'nt')
    monkeypatch.setattr(module.subprocess, 'run', lambda *_a, **_kw: (_ for _ in ()).throw(OSError('PRIVATE')))
    assert module.producer_idle(Path('safe') / str(uuid4())) is False


def test_trusted_validator_exact_preproduction_bytes_load_from_safe_copy(tmp_path):
    raw = module.BRIDGE_PATH.read_bytes()
    assert sha(raw) in module.TRUSTED_BRIDGE_SHA256S
    path = tmp_path / 'reviewed-bridge.py'; path.write_bytes(raw)
    validator = module.trusted_bridge(path)
    assert callable(validator.build_result) and validator.__file__ == str(path)


@pytest.mark.parametrize('change', ['tamper', 'oversized', 'hardlink'])
def test_untrusted_validator_is_never_executed(tmp_path, change):
    path = tmp_path / 'changed-bridge.py'
    if change == 'tamper':
        path.write_bytes(module.BRIDGE_PATH.read_bytes() + b'\nraise AssertionError("UNTRUSTED EXECUTED")\n')
    elif change == 'oversized':
        path.write_bytes(b'x' * (256 * 1024 + 1))
    else:
        original = tmp_path / 'original.py'; original.write_bytes(module.BRIDGE_PATH.read_bytes())
        path.hardlink_to(original)
    with pytest.raises(AppError) as error: module.trusted_bridge(path)
    assert error.value.code == 'recovery_validator_changed'
