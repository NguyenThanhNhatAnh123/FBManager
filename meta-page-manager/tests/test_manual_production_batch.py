"""Synthetic manual-cohort contracts; no worker, uploader, audio or Meta IO."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_idea_runs import fixture, add
from page_manager.auto_batch_planner import AutoBatchPlanner
from page_manager.errors import AppError
from page_manager.manual_production_batch import ManualProductionBatch


class Audio:
    def __init__(self):
        self.calls = []
        self.valid = True

    def plan_manual(self, key, count):
        self.calls.append((key, count))
        return [{'index': i, 'source_sha256': format((i - 1) % 6 + 1, '064x'),
                 'approved_mix': {'approval_id': format((i - 1) % 6 + 1, '064x')}} for i in range(1, count + 1)]

    def validate_snapshot(self, proofs):
        assert len(proofs) == 6
        return self.valid


@pytest.fixture
def batch(fixture):
    f = fixture
    f.runs.on_result = lambda *_: None
    audio = Audio()
    controller = ManualProductionBatch(f.store, f.pipeline, f.runs, audio, clock=f.clock)
    ideas = [add(f, f'source-{i}.png', b'\x89PNG\r\n\x1a\nsource' + bytes([i])) for i in range(3)]
    items = [{'idea_id': v['id'], 'expected_source_sha256': v['artwork_sha256'], 'repeat': False} for v in ideas]
    return SimpleNamespace(f=f, controller=controller, audio=audio, ideas=ideas, items=items, key=str(uuid4()))


def payloads(b):
    with b.f.store.connect() as db:
        return [json.loads(v[0]) for v in db.execute('SELECT payload FROM idea_manual_runs ORDER BY rowid')]


def test_three_ideas_share_one_fourteen_audio_plan_and_counts_without_launch(batch):
    b = batch
    result = b.controller.request(b.key, items=b.items)
    assert result['status'] == 'running' and result['total_videos'] == 14
    assert [v['variant_count'] for v in result['items']] == [5, 5, 4]
    assert [v['idea_id'] for v in result['items']] == [v['idea_id'] for v in b.items]
    assert b.audio.calls == [(result['id'], 14)]
    frozen = payloads(b)
    assert [[v['index'] for v in p['audio_assignments']] for p in frozen] == [list(range(1, 6)), list(range(6, 11)), list(range(11, 15))]
    assert [p['trigger_mode'] for p in frozen] == ['manual_web_run_idea'] * 3
    assert all('automatic_batch' not in p and 'production_iteration' not in p for p in frozen)
    assert not b.f.launcher.calls
    assert 'audio_assignments' not in json.dumps(result) and 'source_file' not in json.dumps(result)
    before = b.f.runs.list()
    assert b.controller.request(b.key, items=b.items) == result
    assert b.f.runs.list() == before and len(b.audio.calls) == 1


@pytest.mark.parametrize('count,expected', [(1, [14]), (2, [7, 7]), (3, [5, 5, 4]), (14, [1] * 14)])
def test_server_allocates_all_fourteen_for_bounded_item_count(batch, count, expected):
    b = batch
    items = list(b.items[:count])
    for i in range(len(items), count):
        idea = add(b.f, f'extra-{i}.png', b'\x89PNG\r\n\x1a\nextra' + bytes([i]))
        items.append({'idea_id': idea['id'], 'expected_source_sha256': idea['artwork_sha256'], 'repeat': False})
    result = b.controller.request(b.key, items=items)
    assert [v['variant_count'] for v in result['items']] == expected
    assert sum(v['variant_count'] for v in result['items']) == 14


@pytest.mark.parametrize('mutation', ['order', 'hash', 'repeat', 'subset'])
def test_uuid_replay_binds_exact_order_sources_and_repeat(batch, mutation):
    b = batch
    b.controller.request(b.key, items=b.items)
    items = [dict(v) for v in b.items]
    if mutation == 'order': items.reverse()
    if mutation == 'hash': items[0]['expected_source_sha256'] = 'f' * 64
    if mutation == 'repeat': items[0]['repeat'] = True
    if mutation == 'subset': items.pop()
    with pytest.raises(AppError) as error:
        b.controller.request(b.key, items=items)
    assert error.value.code == 'manual_batch_conflict'
    assert len(payloads(b)) == 3 and len(b.audio.calls) == 1


@pytest.mark.parametrize('kind', ['empty', 'duplicate', 'extra', 'string_repeat', 'bad_sha', 'bad_uuid', 'too_many', 'bool_count', 'not_fourteen'])
def test_input_is_closed_strict_and_rejected_before_audio_or_enqueue(batch, kind):
    b = batch
    items = [dict(v) for v in b.items]
    count, key = 14, b.key
    if kind == 'empty': items = []
    if kind == 'duplicate': items.append(dict(items[0]))
    if kind == 'extra': items[0]['audio_assignments'] = []
    if kind == 'string_repeat': items[0]['repeat'] = 'false'
    if kind == 'bad_sha': items[0]['expected_source_sha256'] = 'A' * 64
    if kind == 'bad_uuid': key = 'not-a-request'
    if kind == 'too_many': items *= 5
    if kind == 'bool_count': count = True
    if kind == 'not_fourteen': count = 13
    with pytest.raises(AppError) as error:
        b.controller.request(key, items=items, page_count=count)
    assert error.value.status_code == 422
    assert not b.audio.calls and not payloads(b)


@pytest.mark.parametrize('gate', ['auto', 'queued', 'active', 'consumer', 'readiness', 'pool', 'page'])
def test_initial_gates_prevent_any_child_enqueue(batch, monkeypatch, gate):
    b = batch
    if gate == 'auto': AutoBatchPlanner(b.f.store, clock=b.f.clock).configure(True, 1)
    if gate in ('queued', 'active'):
        idea = add(b.f, 'unrelated.png', b'\x89PNG\r\n\x1a\nother')
        run = b.f.runs.request(idea['id'], str(uuid4()))
        if gate == 'active':
            with b.f.store.connect() as db: db.execute("UPDATE idea_manual_runs SET status='running' WHERE id=?", (run['id'],))
    if gate == 'consumer': b.f.runs.on_result = None
    if gate == 'readiness': monkeypatch.setattr(b.f.runs, 'readiness', lambda: {'ready': False})
    if gate == 'pool':
        with b.f.store.connect() as db:
            row = db.execute('SELECT settings FROM video_source_settings WHERE id=1').fetchone()
            values = json.loads(row[0]); values['page_ids'].pop()
            db.execute('UPDATE video_source_settings SET settings=?', (json.dumps(values),))
    if gate == 'page': monkeypatch.setattr(b.f.store, 'posting_eligibility', lambda *_a, **_k: {'can_publish': False})
    before = len(payloads(b))
    with pytest.raises(AppError): b.controller.request(b.key, items=b.items)
    assert len(payloads(b)) == before and not b.f.launcher.calls


@pytest.mark.parametrize('change', ['metadata', 'bytes', 'missing', 'linked'])
def test_all_source_files_verified_before_any_enqueue(batch, change, tmp_path):
    b = batch
    path = b.f.ideas / b.ideas[-1]['filename']
    if change == 'metadata': b.items[-1]['expected_source_sha256'] = 'b' * 64
    if change == 'bytes': path.write_bytes(b'\x89PNG\r\n\x1a\nchanged')
    if change == 'missing': path.unlink()
    if change == 'linked':
        other = tmp_path / 'outside.png'; other.write_bytes(path.read_bytes())
        path.unlink()
        try: path.hardlink_to(other)
        except OSError: pytest.skip('Hard links unavailable')
    with pytest.raises(AppError) as error: b.controller.request(b.key, items=b.items)
    assert error.value.code == 'manual_batch_source_changed'
    assert not payloads(b) and not b.audio.calls


@pytest.mark.parametrize('after_commit', [False, True])
def test_crash_between_enqueues_or_after_child_commit_replays_same_request_ids(batch, monkeypatch, after_commit):
    b = batch
    original = b.f.runs.request
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 2 and not after_commit: raise RuntimeError('simulated process stop')
        result = original(*args, **kwargs)
        if len(calls) == 1 and after_commit: raise RuntimeError('simulated receipt gap')
        return result
    monkeypatch.setattr(b.f.runs, 'request', interrupted)
    with pytest.raises(RuntimeError): b.controller.request(b.key, items=b.items)
    assert len(payloads(b)) == 1
    first = b.f.runs.list()['items'][0]
    monkeypatch.setattr(b.f.runs, 'request', original)
    restarted = ManualProductionBatch(b.f.store, b.f.pipeline, b.f.runs, b.audio, clock=b.f.clock)
    result = restarted.request(b.key, items=b.items)
    assert result['status'] == 'running' and len(payloads(b)) == 3
    assert result['items'][0]['run_id'] == first['id']
    assert len(b.audio.calls) == 1 and not b.f.launcher.calls


def test_changed_source_configuration_after_partial_enqueue_holds_without_replanning(batch, monkeypatch):
    b = batch
    original = b.f.runs.request
    def interrupted(*args, **kwargs):
        if len(payloads(b)) == 1: raise RuntimeError('stop')
        return original(*args, **kwargs)
    monkeypatch.setattr(b.f.runs, 'request', interrupted)
    with pytest.raises(RuntimeError): b.controller.request(b.key, items=b.items)
    with b.f.store.connect() as db: db.execute('UPDATE video_source_settings SET revision=revision+1 WHERE id=1')
    monkeypatch.setattr(b.f.runs, 'request', original)
    with pytest.raises(AppError) as error: b.controller.request(b.key, items=b.items)
    assert error.value.code == 'manual_batch_source_changed'
    assert b.controller.by_request(b.key)['status'] == 'needs_review'
    assert len(payloads(b)) == 1 and len(b.audio.calls) == 1


def test_concurrent_replay_has_one_dispatch_owner(batch, monkeypatch):
    b = batch
    entered, release = threading.Event(), threading.Event()
    original = b.f.runs.request
    calls = []
    def slow(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(b.f.runs, 'request', slow)
    other = ManualProductionBatch(b.f.store, b.f.pipeline, b.f.runs, b.audio, clock=b.f.clock)
    with ThreadPoolExecutor(2) as executor:
        first = executor.submit(b.controller.request, b.key, items=b.items)
        assert entered.wait(5)
        replay = executor.submit(other.request, b.key, items=b.items)
        assert replay.result(timeout=5)['status'] == 'prepared'
        release.set()
        assert first.result(timeout=5)['status'] == 'running'
    assert len(calls) == 3 and len(set(calls)) == 3 and len(payloads(b)) == 3


def completed_previous(b, idea):
    run = b.f.runs.request(idea['id'], str(uuid4()), variant_count=1)
    with b.f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET status='completed' WHERE id=?", (run['id'],))
        db.execute("UPDATE idea_pipeline_ideas SET campaign_state='verified' WHERE id=?", (idea['id'],))
        db.execute("UPDATE idea_pipeline_variants SET status='registered',video_sha256=? WHERE idea_id=? AND ordinal=1", ('e' * 64, idea['id']))
    return run


def test_nonrepeat_completed_idea_is_blocked_before_any_child_request(batch):
    b = batch
    completed_previous(b, b.ideas[-1])
    before = payloads(b)
    with pytest.raises(AppError) as error: b.controller.request(b.key, items=b.items)
    assert error.value.code == 'idea_run_requires_review' and payloads(b) == before


def test_explicit_repeat_uses_fresh_canonical_iteration_and_shared_audio_slice(batch):
    b = batch
    old = completed_previous(b, b.ideas[-1])
    b.items[-1]['repeat'] = True
    result = b.controller.request(b.key, items=b.items)
    repeated = b.f.runs.get(result['items'][-1]['run_id'])
    assert repeated['production_iteration']['number'] == 2 and repeated['id'] != old['id']
    frozen = payloads(b)
    assert [v['index'] for v in frozen[-1]['audio_assignments']] == [11, 12, 13, 14]
    assert len(frozen[-1]['variants']) == 4
    assert set(v['variant_id'] for v in frozen[0]['variants']).isdisjoint(v['variant_id'] for v in frozen[-1]['variants'])
    assert b.controller.request(b.key, items=b.items) == result and len(payloads(b)) == 4


def test_repeat_must_be_finished_and_uncertain_old_work_is_not_bypassed(batch):
    b = batch
    old = completed_previous(b, b.ideas[-1])
    with b.f.store.connect() as db: db.execute("UPDATE idea_manual_runs SET status='needs_review' WHERE id=?", (old['id'],))
    b.items[-1]['repeat'] = True
    with pytest.raises(AppError) as error: b.controller.request(b.key, items=b.items)
    assert error.value.code == 'idea_iteration_requires_review' and len(payloads(b)) == 1


def test_receipt_reconciles_completion_and_never_requeues_cancelled_work(batch):
    b = batch
    result = b.controller.request(b.key, items=b.items)
    with b.f.store.connect() as db: db.execute("UPDATE idea_manual_runs SET status='completed'")
    assert b.controller.get(result['id'])['status'] == 'complete'
    assert b.controller.request(b.key, items=b.items)['status'] == 'complete'
    assert len(payloads(b)) == 3
    with b.f.store.connect() as db: db.execute("UPDATE idea_manual_runs SET status='cancelled' WHERE id=?", (result['items'][0]['run_id'],))
    assert b.controller.request(b.key, items=b.items)['status'] == 'needs_review'
    assert len(payloads(b)) == 3


def test_frozen_audio_is_not_replanned_after_partial_enqueue(batch, monkeypatch):
    b = batch
    original = b.f.runs.request
    def interrupted(*args, **kwargs):
        if len(payloads(b)) == 1: raise RuntimeError('stop')
        return original(*args, **kwargs)
    monkeypatch.setattr(b.f.runs, 'request', interrupted)
    with pytest.raises(RuntimeError): b.controller.request(b.key, items=b.items)
    b.audio.valid = False
    monkeypatch.setattr(b.f.runs, 'request', original)
    with pytest.raises(AppError) as error: b.controller.request(b.key, items=b.items)
    assert error.value.code == 'audio_mix_changed'
    assert b.controller.by_request(b.key)['status'] == 'needs_review'
    assert len(payloads(b)) == 1 and len(b.audio.calls) == 1


@pytest.mark.parametrize('canonical', ['needs_review', 'cancelled', 'corrupt_payload', 'wrong_audio', 'missing'])
def test_partial_batch_holds_remaining_items_when_existing_child_is_not_safe(batch, monkeypatch, canonical):
    b = batch
    original = b.f.runs.request
    def interrupted(*args, **kwargs):
        if len(payloads(b)) == 1: raise RuntimeError('stop')
        return original(*args, **kwargs)
    monkeypatch.setattr(b.f.runs, 'request', interrupted)
    with pytest.raises(RuntimeError): b.controller.request(b.key, items=b.items)
    # Reconcile the receipt once so disappearance is detectable as a broken
    # canonical reference rather than an uncommitted dispatch.
    first = b.controller.by_request(b.key)['items'][0]
    with b.f.store.connect() as db:
        if canonical == 'missing': db.execute('DELETE FROM idea_manual_runs WHERE id=?', (first['run_id'],))
        elif canonical == 'corrupt_payload': db.execute("UPDATE idea_manual_runs SET payload='broken' WHERE id=?", (first['run_id'],))
        elif canonical == 'wrong_audio':
            payload = json.loads(db.execute('SELECT payload FROM idea_manual_runs WHERE id=?', (first['run_id'],)).fetchone()[0])
            payload['audio_assignments'].reverse()
            db.execute('UPDATE idea_manual_runs SET payload=? WHERE id=?', (json.dumps(payload), first['run_id']))
        else: db.execute('UPDATE idea_manual_runs SET status=? WHERE id=?', (canonical, first['run_id']))
    monkeypatch.setattr(b.f.runs, 'request', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('Held batch must not enqueue')))
    result = b.controller.request(b.key, items=b.items)
    assert result['status'] == 'needs_review'
    assert result['items'][1]['status'] == result['items'][2]['status'] == 'pending'
    assert len(b.audio.calls) == 1
