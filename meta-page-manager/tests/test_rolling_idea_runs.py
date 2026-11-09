"""New dispatch mode with inert child processes; no paid model or uploader."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager import idea_runs as module
from page_manager.errors import AppError
from page_manager.producer_orchestration import MODE, orchestration_configuration
from test_idea_runs import fixture, add, drain, FakeProcess


def rolling(f, monkeypatch):
    f.runs.execution_pipeline = MODE
    async def accept_item(_run,_folder,_ordinal):return {'accepted':True}
    f.runs.on_item=accept_item
    configuration = {'mode':MODE, 'ready':True, 'sha256':'b' * 64}
    monkeypatch.setattr(module, 'orchestration_configuration', lambda *_:dict(configuration))
    f.runs.audio_approvals = SimpleNamespace(
        plan_manual=lambda _request,count:[{'source_sha256':'a'*64,'source_file':'fixture-track'} for _ in range(count)],
        approved_mixes=lambda:{'a'*64:{'approval_id':'c'*64}}, validate_snapshot=lambda _:True)
    calls = []
    processes = []

    async def launch(argv, **options):
        calls.append((argv, options))
        folder = Path(argv[argv.index('--run-input') + 1]).parent
        process = FakeProcess([*argv, '-o', str(folder / 'worker-output.json')])
        processes.append(process)
        return process

    f.runs.launcher = launch
    f.rolling_processes = processes
    return configuration, calls


def test_new_mode_is_frozen_and_uses_owned_coordinator_not_one_batch_model(fixture, monkeypatch):
    f = fixture
    _, calls = rolling(f, monkeypatch)
    idea = add(f)
    run = f.runs.request(idea['id'], str(uuid4()), variant_count=2)
    payload = json.loads(f.runs._row(run['id'])['payload'])
    assert payload['execution_pipeline'] == MODE
    assert payload['execution_pipeline_sha256'] == 'b' * 64
    asyncio.run(drain(f.runs))
    argv, options = calls[0]
    folder = f.runs.runs_root / run['id']
    assert argv == [sys.executable, '-X', 'utf8', '-B',
        str(f.runs.root / 'scripts' / 'run_streaming_producer.py'),
        '--run-input', str(folder / 'run-input.json')]
    data = json.loads((folder / 'run-input.json').read_text(encoding='utf-8'))
    assert data['execution_pipeline'] == MODE and data['execution_pipeline_sha256'] == 'b' * 64
    assert len(data['variants']) == 2
    assert options['cwd'] == str(f.output)
    assert f.rolling_processes[0].prompt == 'ROLLING_V1_START\n'
    assert f.runs.get(run['id'])['status'] == 'needs_review'
    assert f.runs.get(run['id'])['pipeline'] is None
    assert not f.store.reel_jobs() and not f.store.reel_assets()


def test_incomplete_coordinator_blocks_before_reservation_or_child(fixture, monkeypatch):
    f = fixture
    configuration, calls = rolling(f, monkeypatch)
    configuration.update(ready=False, sha256=None)
    idea = add(f)
    assert 'production_pipeline_unavailable' in f.runs.readiness()['blocker_codes']
    with pytest.raises(AppError) as error:
        f.runs.request(idea['id'], str(uuid4()), variant_count=2)
    assert error.value.code == 'idea_run_not_ready'
    assert not calls and not f.runs.list()['items']


def test_manual_replay_reuses_frozen_audio_plan_without_planning_again(fixture, monkeypatch):
    f = fixture
    rolling(f, monkeypatch)
    request_id = str(uuid4())
    idea = add(f)
    first = f.runs.request(idea['id'], request_id, variant_count=2)
    def forbidden(*_):
        pytest.fail('Replaying an existing request must not make a new audio plan')
    f.runs.audio_approvals.plan_manual = forbidden
    assert f.runs.request(idea['id'], request_id, variant_count=2) == first


def test_changed_coordinator_never_launches_a_previously_queued_run(fixture, monkeypatch):
    f = fixture
    configuration, calls = rolling(f, monkeypatch)
    run = f.runs.request(add(f)['id'], str(uuid4()), variant_count=2)
    configuration['sha256'] = 'c' * 64
    asyncio.run(drain(f.runs))
    assert not calls
    assert f.runs.get(run['id'])['status'] == 'needs_review'
    assert not f.store.reel_jobs()


def test_stale_fence_progress_is_not_displayed_as_current(fixture, monkeypatch):
    f = fixture
    rolling(f, monkeypatch)
    run = f.runs.request(add(f)['id'], str(uuid4()), variant_count=2)
    row = f.runs._row(run['id'])
    folder = f.runs.runs_root / run['id']
    folder.mkdir(parents=True)
    value = {'schema_version':1,'mode':MODE,'run_id':run['id'],'lease_fence':row['fence'],
        'variant_count':2,'product':'passed','items':[
            {'ordinal':1,'image':'passed','video':'passed','qa':'passed'},
            {'ordinal':2,'image':'running','video':'pending','qa':'pending'}]}
    path = folder / 'pipeline-progress.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    assert f.runs.get(run['id'])['pipeline']['items'][0]['qa'] == 'passed'
    with f.store.connect() as db:
        db.execute('UPDATE idea_manual_runs SET fence=fence+1 WHERE id=?',(run['id'],))
    assert f.runs.get(run['id'])['pipeline'] is None
    assert not f.store.reel_assets()


def test_configuration_tracks_fixed_code_and_reference_without_a_worker(tmp_path):
    from page_manager.producer_orchestration import MANAGER_FILES, PRODUCER_FILES, VIA_FILES
    manager, producer = tmp_path / 'manager', tmp_path / 'producer'
    for root, names in ((manager,MANAGER_FILES),(producer,PRODUCER_FILES),(tmp_path,VIA_FILES)):
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('inert configuration fixture',encoding='utf-8')
    before = orchestration_configuration(manager,producer)
    assert before['ready'] and before['mode'] == MODE
    target = manager / MANAGER_FILES[1]
    target.write_text('changed fixture',encoding='utf-8')
    assert orchestration_configuration(manager,producer)['sha256'] != before['sha256']
    target.unlink()
    assert orchestration_configuration(manager,producer) == {'mode':MODE,'ready':False,'sha256':None}
