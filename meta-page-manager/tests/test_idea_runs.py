"""Manual producer lifecycle using fake processes and callbacks only."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import threading
import tomllib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager import idea_runs as module
from page_manager.errors import AppError
from page_manager.idea_pipeline import IdeaPipeline
from page_manager.idea_runs import IdeaRuns
from page_manager.store import Store


PNG = b'\x89PNG\r\n\x1a\nfixture-original-design'
SOURCE = {'shop': 'blueox179.com', 'campaign_url': 'https://blueox179.com/campaign/zitat25', 'verified': True}
BLACK = dict(SOURCE, campaign_url='https://blueox179.com/campaign/ge2610')


class FakeProcess:
    def __init__(self, argv, *, status='needs_review', malformed=False, block=None, failure=0):
        self.argv, self.status, self.malformed, self.block, self.failure = argv, status, malformed, block, failure
        self.returncode = None
        self.terminated = self.killed = False
        self.prompt = None

    async def communicate(self, prompt):
        self.prompt = prompt.decode('utf-8')
        if self.block is not None: await self.block.wait()
        output_path = Path(self.argv[self.argv.index('-o') + 1])
        schema = json.loads((output_path.parent / 'worker-output.schema.json').read_text(encoding='utf-8'))
        properties = schema['properties']
        value = {key: item['const'] for key, item in properties.items() if 'const' in item}
        value.update(status=self.status, reason='ready' if self.status == 'ready' else 'qa_pending', campaign=None)
        if self.status == 'ready':
            url = 'https://blueox179.com/campaign/fixture'
            value['campaign'] = {'kind': 'existing', 'url': url, 'source_url': None, 'shirt_color': None,
                                 'evidence': {'observed_at': '2030-11-01T12:00:00+00:00', 'artwork_matches': True,
                                              'quote_matches': True, 'public_url': url}}
        if self.malformed: value['idea_id'] = str(uuid4())
        output_path.write_text(json.dumps(value), encoding='utf-8')
        self.returncode = self.failure
        return None, None

    async def wait(self):
        if self.returncode is None and self.block is not None: await self.block.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True; self.returncode = -15
        if self.block is not None: self.block.set()

    def kill(self):
        self.killed = True; self.returncode = -9
        if self.block is not None: self.block.set()


class FakeLauncher:
    def __init__(self, **options):
        self.options, self.calls, self.processes = options, [], []

    async def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        process = FakeProcess(argv, **self.options); self.processes.append(process)
        return process


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(module,'producer_profile_args',lambda _root:module.codex_role_args('producer'))
    monkeypatch.delenv('PM_CODEX_CLI', raising=False)
    store = Store(tmp_path / 'manager')
    monkeypatch.setattr(store,'posting_eligibility',lambda page_id,db=None:{'page_id':page_id,'can_publish':True,
        'information_status':'updated','reason':None,'revision':1})
    manager = SimpleNamespace(store=store)
    ideas = tmp_path / 'ideas'; ideas.mkdir()
    output = tmp_path / 'ABC'; output.mkdir()
    bridge = output / 'page-manager-bridge'; bridge.mkdir()
    (bridge / 'bridge.py').write_text('# offline fixture only', encoding='utf-8')
    cli = tmp_path / 'fixture-codex.exe'; cli.write_bytes(b'not executable fixture')
    monkeypatch.setattr(module, 'CLI', cli)
    monkeypatch.setattr(module.shutil, 'which', lambda _name: None)
    monkeypatch.setattr(module, 'video_readiness', lambda _, **_kwargs: {'status': 'blocked', 'workflow_ready': True})
    provider = {'installed': True, 'available': True, 'configured_blueox': True,
                'active_domain': 'blueox179.com', 'authenticated': None}
    monkeypatch.setattr(module, 'PodsachBridge', lambda: SimpleNamespace(inspect=lambda: dict(provider)))
    clock = lambda: datetime(2030, 11, 1, 12, tzinfo=timezone.utc)
    pipeline = IdeaPipeline(manager, directory=ideas, output_directory=output, clock=clock, stable_seconds=0)
    pipeline.configure(campaign_source=SOURCE, black_campaign_source=BLACK)
    schedule = {'enabled': True, 'page_ids': ['123', '456']+[str(700+i) for i in range(12)], 'connection_id': 1,
                'daily_time': '06:00', 'timezone': 'America/Los_Angeles', 'caption_template': ''}
    with store.connect() as db:
        db.execute('CREATE TABLE video_source_settings(id INTEGER PRIMARY KEY,revision INTEGER,settings TEXT,configured INTEGER)')
        db.execute('INSERT INTO video_source_settings VALUES(1,2,?,1)', (json.dumps(schedule),))
    launcher = FakeLauncher()
    runs = IdeaRuns(tmp_path / 'manager', manager, pipeline, launcher=launcher, clock=clock, execution_pipeline='legacy')
    return SimpleNamespace(manager=manager, store=store, pipeline=pipeline, runs=runs, launcher=launcher,
                           output=output, ideas=ideas, clock=clock, provider=provider)


def add(f, filename='one.png', payload=PNG):
    (f.ideas / filename).write_bytes(payload)
    f.pipeline.scan(); f.pipeline.scan()
    return next(row for row in f.pipeline.status()['ideas'] if row['artwork_sha256'] == hashlib.sha256(payload).hexdigest())


async def drain(runs):
    runs.tick()
    if runs._task is not None: await runs._task


@pytest.mark.parametrize('cancelled', [False, True])
def test_read_only_authority_has_wal_files_and_fresh_fence_until_worker_stops(fixture, monkeypatch, cancelled):
    f=fixture;idea=add(f);run=f.runs.request(idea['id'],str(uuid4()));phases=[]
    def read_current():
        # A sandboxed reader cannot create these beside its authority database.
        assert Path(str(f.store.path)+'-wal').is_file()
        assert Path(str(f.store.path)+'-shm').is_file()
        with closing(sqlite3.connect(f.store.path.as_uri()+'?mode=ro',uri=True)) as reader:
            reader.execute('PRAGMA query_only=ON')
            return reader.execute('SELECT fence FROM idea_manual_runs WHERE id=?',(run['id'],)).fetchone()[0]
    async def execute(_row):
        assert read_current()==1
        with f.store.connect() as writer:
            writer.execute('UPDATE idea_manual_runs SET fence=fence+1 WHERE id=?',(run['id'],))
        assert read_current()==2, 'The host must not pin an old authority snapshot'
        phases.append('execute')
        if cancelled:raise asyncio.CancelledError()
    async def stop():
        assert read_current()==2
        phases.append('stopped')
    monkeypatch.setattr(f.runs,'_execute',execute)
    monkeypatch.setattr(f.runs,'_stop',stop)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):asyncio.run(drain(f.runs))
    else:asyncio.run(drain(f.runs))
    assert phases==['execute','stopped']


def test_request_is_manual_only_durable_and_replayed_without_launch(fixture):
    f = fixture; idea = add(f); key = str(uuid4())
    run = f.runs.request(idea['id'], key)
    assert run['request_id'] == key and run['status'] == 'queued'
    assert f.runs.request(idea['id'], key) == run
    assert not f.launcher.calls and not (f.output / 'page-manager-bridge' / 'runs').exists()
    assert not f.pipeline.status()['settings']['enabled']
    assert not f.store.reel_jobs() and not f.store.reel_assets()
    with pytest.raises(AppError) as error: f.runs.request(idea['id'], str(uuid4()))
    assert error.value.code == 'idea_run_exists'
    other = add(f, 'two.png', PNG + b'other')
    with pytest.raises(AppError) as error: f.runs.request(other['id'], key)
    assert error.value.code == 'idea_run_conflict'


def test_discovery_never_requests_runs(fixture):
    f = fixture
    add(f)
    asyncio.run(drain(f.runs))
    assert not f.runs.list()['items'] and not f.launcher.calls


def test_tick_without_running_loop_or_queued_manual_work_creates_no_task(fixture, monkeypatch):
    f = fixture; idea = add(f)
    run = f.runs.request(idea['id'], str(uuid4()))
    f.runs.tick()
    assert f.runs._task is None and f.runs.get(run['id'])['status'] == 'queued'
    asyncio.run(f.runs.cancel(run['id']))
    async def empty_tick():
        def forbidden(_): pytest.fail('Empty queue must not create a task')
        monkeypatch.setattr(asyncio, 'create_task', forbidden)
        f.runs.tick()
        assert f.runs._task is None
    asyncio.run(empty_tick())


def test_unverified_sources_or_unavailable_real_workflow_prevent_queue(fixture):
    f = fixture; idea = add(f)
    f.pipeline.configure(campaign_source=dict(SOURCE, verified=False), black_campaign_source=BLACK)
    with pytest.raises(AppError) as error: f.runs.request(idea['id'], str(uuid4()))
    assert error.value.code == 'idea_run_not_ready'
    f.pipeline.configure(campaign_source=SOURCE, black_campaign_source=BLACK)
    f.provider['active_domain'] = 'wrong-shop.test'
    with pytest.raises(AppError): f.runs.request(idea['id'], str(uuid4()))
    assert not f.launcher.calls


def test_unknown_session_does_not_fake_verified_or_permanently_block_manual_preflight(fixture):
    ready = fixture.runs.readiness()
    assert ready['ready'] and not ready['session_verified'] and ready['manual_only']
    assert ready['cli_configured'] and ready['cli_runtime_verified'] is False


def test_one_unedited_page_blocks_production_before_any_worker_launch(fixture,monkeypatch):
    f=fixture;idea=add(f)
    monkeypatch.setattr(f.store,'posting_eligibility',lambda page_id,db=None:{'can_publish':page_id!='123'})
    ready=f.runs.readiness()
    assert ready['schedule_ready'] and not ready['pages_information_ready']
    assert 'page_information_required' in ready['blocker_codes'] and not ready['ready']
    with pytest.raises(AppError) as error:f.runs.request(idea['id'],str(uuid4()))
    assert error.value.code=='idea_run_not_ready' and not f.launcher.calls


@pytest.mark.parametrize('frozen_kind',['local','web'])
def test_unavailable_frozen_uploader_blocks_queued_worker_despite_other_ready_provider(fixture,frozen_kind):
    f=fixture;idea=add(f)
    endpoints={'local':module.LOCAL_ENDPOINT,'web':module.WEB_ENDPOINT}
    def provider(ready):return {'ready':ready,'active_domain':'blueox179.com'}
    f.provider.update(provider=frozen_kind,endpoint=endpoints[frozen_kind],ready=True,
                      providers={kind:provider(kind==frozen_kind) for kind in endpoints})
    run=f.runs.request(idea['id'],str(uuid4()))
    replacement='web' if frozen_kind=='local' else 'local'
    f.provider.update(provider=replacement,endpoint=endpoints[replacement],ready=True,
                      providers={kind:provider(kind==replacement) for kind in endpoints})
    assert f.runs.readiness()['ready'],'A different provider can make new runs available'
    asyncio.run(drain(f.runs))
    saved=f.runs.get(run['id'])
    assert saved['status']=='needs_review' and saved['diagnostics']['code']=='readiness_changed'
    assert not f.launcher.calls and not (f.runs.runs_root/run['id']).exists()
    assert f.runs._internal(f.runs._row(run['id']))['uploader']['kind']==frozen_kind


def test_ready_frozen_provider_is_kept_when_router_prefers_another_ready_provider(fixture):
    f=fixture;idea=add(f)
    providers={kind:{'ready':True,'active_domain':'blueox179.com'} for kind in ('local','web')}
    f.provider.update(provider='web',endpoint=module.WEB_ENDPOINT,ready=True,providers=providers)
    run=f.runs.request(idea['id'],str(uuid4()))
    f.provider.update(provider='local',endpoint=module.LOCAL_ENDPOINT)
    asyncio.run(drain(f.runs))
    assert len(f.launcher.calls)==1
    frozen=json.loads((f.runs.runs_root/run['id']/'run-input.json').read_text(encoding='utf-8'))
    assert frozen['uploader']=={'kind':'web','endpoint':module.WEB_ENDPOINT,'shop':'blueox179.com'}


def test_readiness_fingerprints_actual_worker_output_directory(fixture, monkeypatch):
    from page_manager import idea_workspace
    f = fixture
    other = f.output.parent / 'different-default-workflow'
    for directory in (f.output, other):
        for name in idea_workspace.VIDEO_WORKFLOW_FILES:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(directory.name + '/' + name, encoding='utf-8')
    config = idea_workspace.video_workflow_configuration(workflow_directory=f.output)
    runtime = f.runs.root / '.runtime'; runtime.mkdir()
    claim = {'schema_version': idea_workspace.VIDEO_ATTESTATION_SCHEMA,
             'kind': 'producer_workflow_attestation', 'status': 'ready',
             'producer_workflow_status': 'ready', 'checked_at': datetime.now(timezone.utc).isoformat(),
             'workflow_version': config['workflow_version'], 'workflow_sha256': config['workflow_sha256']}
    (runtime / 'page-video-agent-readiness.json').write_text(json.dumps(claim), encoding='utf-8')
    monkeypatch.setattr(idea_workspace, 'OUTPUT_DIRECTORY', other)
    monkeypatch.setattr(module, 'video_readiness', idea_workspace.video_readiness)
    assert f.runs.readiness()['video_workflow_ready'] is True
    renderer = next(name for name in idea_workspace.VIDEO_WORKFLOW_FILES if name.endswith('/scripts/render_video.py'))
    (f.output / renderer).write_text('changed actual renderer', encoding='utf-8')
    assert f.runs.readiness()['video_workflow_ready'] is False


def test_launch_is_fixed_hidden_ephemeral_sandboxed_and_pending_never_completes(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(drain(f.runs))
    assert len(f.launcher.calls) == 1
    argv, options = f.launcher.calls[0]
    assert argv[0] == str(module.CLI) and argv[1:6] == ['exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write']
    assert argv[argv.index('-C') + 1] == str(f.output)
    overrides = {}
    for index, item in enumerate(argv):
        if item == '-c':
            name, value = argv[index + 1].split('=', 1)
            overrides[name] = tomllib.loads('value=' + value)['value']
    assert overrides['mcp_servers.via_manager.enabled_tools'] == ['via_status']
    assert json.loads(overrides['mcp_servers.via_manager.env.VIA_MCP_ALLOWED_TOOLS']) == ['via_status']
    assert '-m' not in argv and '--model' not in argv and '--dangerously-bypass-approvals-and-sandbox' not in argv
    assert options['stdout'] == options['stderr'] == subprocess.DEVNULL
    assert options['creationflags'] == (subprocess.CREATE_NO_WINDOW if module.os.name == 'nt' else 0)
    process = f.launcher.processes[0]
    assert 'EXACTLY the verified product URL' in process.prompt and 'Audio lacking actual human approval or an exact approved-mix PCM match remains audio_verified=false' in process.prompt
    assert 'Do not call any Meta/Facebook/Page Manager API' in process.prompt
    skill = f.output / '.agents' / 'skills' / 'page-shirt-zoom-8s' / 'SKILL.md'
    renderer = skill.parent / 'scripts' / 'render_video.py'
    assert str(skill) in process.prompt and str(renderer) in process.prompt
    assert 'installed pod-shirt-pipeline' not in process.prompt
    assert 'with this FFmpeg renderer only' in process.prompt
    folder = f.runs.runs_root / run['id']
    assert (folder / 'source.png').read_bytes() == PNG
    data = json.loads((folder / 'run-input.json').read_text(encoding='utf-8'))
    assert data['idea_id'] == idea['id'] and data['artwork_sha256'] == idea['artwork_sha256']
    assert data['source_revision'] == 2 and len(data['variants']) == 7
    assert data['source_snapshot']['black_campaign_source'] == BLACK
    assert f.runs.get(run['id'])['status'] == 'needs_review'
    diagnostic = f.runs.get(run['id'])['diagnostics']
    assert diagnostic['code'] == 'worker_needs_review' and diagnostic['exit_code'] == 0
    assert type(diagnostic['duration_ms']) is int and diagnostic['duration_ms'] >= 0
    assert not list(folder.glob('*.log')) and not f.store.reel_jobs()


@pytest.mark.parametrize('ack', [None, {'accepted': False}, True, {}])
def test_no_callback_ack_means_no_completed_run(fixture, ack):
    f = fixture; idea = add(f); f.launcher.options['status'] = 'ready'
    if ack is not None:
        async def callback(run, folder): return ack
        f.runs.on_result = callback
    run = f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(drain(f.runs))
    assert f.runs.get(run['id'])['status'] == 'needs_review'


def test_exact_callback_snapshot_and_ack_are_required_to_complete(fixture):
    f = fixture; idea = add(f); f.launcher.options['status'] = 'ready'
    received = []
    async def callback(run, folder):
        received.append((run, folder))
        with f.store.connect() as db:
            row = db.execute('SELECT * FROM idea_manual_runs WHERE id=?', (run['id'],)).fetchone()
            assert row['status'] == 'importing' and row['fence'] == 1 and not row['cancel_requested']
        assert run['source_revision'] == 2
        assert hashlib.sha256(module._json(json.loads(run['payload'])).encode()).hexdigest() == run['payload_hash']
        return {'accepted': True, 'status': 'ready_for_schedule', 'approved': 7, 'copied': 7}
    f.runs.on_result = callback
    run = f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(drain(f.runs))
    assert len(received) == 1 and f.runs.get(run['id'])['status'] == 'completed'
    internal, folder = received[0]
    assert internal['run_directory'] == str(folder) and internal['lease_fence'] == 1
    assert set(f.runs.get(run['id'])) <= {'id', 'request_id', 'idea_id', 'artwork_sha256', 'filename', 'status', 'stage',
                                            'message', 'created_at', 'started_at', 'finished_at', 'can_cancel', 'trigger_mode', 'diagnostics',
                                                'variant_count','automatic_batch','pipeline'}
    assert f.runs.get(run['id'])['diagnostics']['code'] == 'completed'


def test_changed_source_hash_or_hardlink_holds_before_launch(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    (f.ideas / 'one.png').write_bytes(PNG + b'changed')
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls and f.runs.get(run['id'])['status'] == 'needs_review'


def test_source_hardlink_is_not_staged_even_when_bytes_match(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    os.link(f.ideas / 'one.png', f.ideas / 'same-bytes.png')
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls and f.runs.get(run['id'])['status'] == 'needs_review'


def test_existing_staging_folder_is_not_overwritten(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    folder = f.runs.runs_root / run['id']; folder.mkdir(parents=True)
    protected = folder / 'source.png'; protected.write_bytes(b'previous-output')
    asyncio.run(drain(f.runs))
    assert protected.read_bytes() == b'previous-output' and not f.launcher.calls
    assert f.runs.get(run['id'])['status'] == 'needs_review'


def test_changed_scheduling_configuration_is_not_silently_adopted(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    with f.store.connect() as db: db.execute('UPDATE video_source_settings SET revision=3 WHERE id=1')
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls and f.runs.get(run['id'])['status'] == 'needs_review'


def test_changed_campaign_template_is_not_silently_adopted(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    f.pipeline.configure(campaign_source=dict(SOURCE, campaign_url='https://blueox179.com/campaign/other'),
                         black_campaign_source=BLACK)
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls and f.runs.get(run['id'])['status'] == 'needs_review'


def test_bad_worker_binding_does_not_reach_consumer(fixture):
    f = fixture; idea = add(f); f.launcher.options.update(status='ready', malformed=True)
    calls = []
    async def callback(*args): calls.append(args); return {'accepted': True}
    f.runs.on_result = callback
    run = f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(drain(f.runs))
    assert not calls and f.runs.get(run['id'])['status'] == 'needs_review'
    assert f.runs.get(run['id'])['diagnostics']['code'] == 'worker_output_invalid'


def test_server_restart_marks_active_review_and_never_relaunches(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    with f.store.connect() as db: db.execute("UPDATE idea_manual_runs SET status='running',stage='producer' WHERE id=?", (run['id'],))
    recovered = IdeaRuns(f.runs.root, f.manager, f.pipeline, launcher=f.launcher, clock=f.clock)
    assert recovered.get(run['id'])['status'] == 'needs_review'
    assert recovered.get(run['id'])['diagnostics']['code'] == 'interrupted'
    asyncio.run(drain(recovered))
    assert not f.launcher.calls
    with pytest.raises(AppError): recovered.request(idea['id'], str(uuid4()))


def test_global_single_worker_while_other_manual_request_waits(fixture):
    async def exercise():
        f = fixture; first = add(f); second = add(f, 'two.png', PNG + b'next')
        event = asyncio.Event(); f.launcher.options['block'] = event
        one = f.runs.request(first['id'], str(uuid4())); two = f.runs.request(second['id'], str(uuid4()))
        f.runs.tick()
        for _ in range(100):
            if f.launcher.calls: break
            await asyncio.sleep(.005)
        assert len(f.launcher.calls) == 1
        f.runs.tick(); assert len(f.launcher.calls) == 1
        assert f.runs.get(two['id'])['status'] == 'queued'
        event.set(); await f.runs._task
        f.runs.tick(); await f.runs._task
        assert len(f.launcher.calls) == 2
        assert f.runs.get(one['id'])['status'] == 'needs_review'
    asyncio.run(exercise())


def test_cancel_and_timeout_stop_only_own_fake_worker_and_keep_outputs(fixture, monkeypatch):
    async def exercise():
        f = fixture; idea = add(f); event = asyncio.Event(); f.launcher.options['block'] = event
        run = f.runs.request(idea['id'], str(uuid4())); f.runs.tick()
        for _ in range(100):
            if f.launcher.calls: break
            await asyncio.sleep(.005)
        stopped = await f.runs.cancel(run['id'])
        assert stopped['status'] == 'needs_review' and f.launcher.processes[0].terminated
        assert stopped['diagnostics']['code'] == 'worker_cancelled'
        assert stopped['diagnostics']['exit_code'] == -15
        assert (f.runs.runs_root / run['id'] / 'run-input.json').exists()
        with pytest.raises(AppError): f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(exercise())


def test_timeout_is_uncertain_and_never_retries(fixture, monkeypatch):
    async def exercise():
        f = fixture; idea = add(f); event = asyncio.Event(); f.launcher.options['block'] = event
        entered=asyncio.Event();deadlines=[];real_timeout=asyncio.timeout
        def controlled_timeout(seconds):
            assert seconds==module.RUN_SECONDS
            deadline=real_timeout(None);deadlines.append(deadline);return deadline
        communicate=FakeProcess.communicate
        async def entered_communicate(process,prompt):
            entered.set();return await communicate(process,prompt)
        monkeypatch.setattr(module.asyncio,'timeout',controlled_timeout)
        monkeypatch.setattr(FakeProcess,'communicate',entered_communicate)
        run = f.runs.request(idea['id'], str(uuid4()))
        f.runs.tick()
        await asyncio.wait_for(entered.wait(),5)
        deadlines[0].reschedule(asyncio.get_running_loop().time())
        await f.runs._task
        assert f.runs.get(run['id'])['stage'] == 'timeout' and f.launcher.processes[0].terminated
        assert f.runs.get(run['id'])['diagnostics']['code'] == 'worker_timeout'
        assert f.runs.get(run['id'])['diagnostics']['exit_code'] == -15
        await drain(f.runs)
        assert len(f.launcher.calls) == 1
    asyncio.run(exercise())


def test_cancelled_unstarted_request_never_launches(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    assert asyncio.run(f.runs.cancel(run['id']))['status'] == 'cancelled'
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls


def test_shutdown_cancels_only_active_owned_worker(fixture):
    async def exercise():
        f = fixture; idea = add(f); f.launcher.options['block'] = asyncio.Event()
        run = f.runs.request(idea['id'], str(uuid4())); f.runs.tick()
        for _ in range(100):
            if f.launcher.calls: break
            await asyncio.sleep(.005)
        await f.runs.shutdown()
        assert f.launcher.processes[0].terminated and f.runs.get(run['id'])['status'] == 'needs_review'
        f.runs.tick(); assert len(f.launcher.calls) == 1
    asyncio.run(exercise())


@pytest.mark.parametrize('stop', ['shutdown', 'cancel', 'timeout'])
def test_consumer_sees_durable_hold_before_cancellation_and_is_awaited(fixture, monkeypatch, stop):
    """Models the real consumer's shielded media thread without decoding files."""
    async def exercise():
        f = fixture; idea = add(f); f.launcher.options['status'] = 'ready'
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        seen = []
        deadlines = []
        if stop == 'timeout':
            real_timeout = asyncio.timeout
            def controlled_timeout(seconds):
                assert seconds == module.RUN_SECONDS
                deadline = real_timeout(None)
                deadlines.append(deadline)
                return deadline
            # This test targets timeout while consuming, not how quickly the
            # unrelated preparation thread finishes on a busy test machine.
            monkeypatch.setattr(module.asyncio, 'timeout', controlled_timeout)
        def consuming(run):
            entered.set()
            assert release.wait(3)
            with f.store.connect() as db:
                row = db.execute('SELECT status,cancel_requested FROM idea_manual_runs WHERE id=?', (run['id'],)).fetchone()
                seen.append((row['status'], row['cancel_requested']))
            finished.set()
            return {'accepted': False}
        async def consumer(run, folder):
            work = asyncio.create_task(asyncio.to_thread(consuming, run))
            try: return await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise
        f.runs.on_result = consumer
        run = f.runs.request(idea['id'], str(uuid4())); f.runs.tick()
        for _ in range(200):
            if entered.is_set(): break
            await asyncio.sleep(.005)
        assert entered.is_set()
        if stop == 'shutdown': stopping = asyncio.create_task(f.runs.shutdown())
        elif stop == 'cancel': stopping = asyncio.create_task(f.runs.cancel(run['id']))
        else:
            assert len(deadlines) == 1
            deadlines[0].reschedule(asyncio.get_running_loop().time())
            stopping = f.runs._task
        for _ in range(200):
            with f.store.connect() as db:
                row = db.execute('SELECT status,cancel_requested FROM idea_manual_runs WHERE id=?', (run['id'],)).fetchone()
            if row['status'] == 'needs_review' and row['cancel_requested']: break
            await asyncio.sleep(.005)
        assert row['status'] == 'needs_review' and row['cancel_requested'] == 1
        assert not stopping.done() and not finished.is_set()
        release.set()
        await stopping
        assert finished.is_set() and seen == [('needs_review', 1)]
        assert f.runs.get(run['id'])['status'] == 'needs_review'
        assert not f.store.reel_jobs()
    asyncio.run(exercise())


@pytest.mark.parametrize('override', ['', 'codex.exe', 'relative/codex.exe', 'missing absolute executable'])
def test_invalid_cli_override_blocks_without_falling_back(fixture, monkeypatch, override):
    f = fixture; idea = add(f)
    monkeypatch.setenv('PM_CODEX_CLI', override)
    ready = f.runs.readiness()
    assert not ready['ready'] and not ready['cli_configured']
    assert ready['cli_configuration_source'] == 'environment' and 'cli_unavailable' in ready['blocker_codes']
    with pytest.raises(AppError) as error: f.runs.request(idea['id'], str(uuid4()))
    assert error.value.code == 'idea_run_not_ready' and not f.launcher.calls


def test_valid_cli_override_selects_only_the_configured_executable(fixture, monkeypatch, tmp_path):
    f = fixture; idea = add(f)
    override = tmp_path / 'another-codex.exe'; override.write_bytes(b'fake executable')
    monkeypatch.setenv('PM_CODEX_CLI', str(override))
    ready = f.runs.readiness()
    assert ready['ready'] and ready['cli_configured'] and not ready['cli_runtime_verified']
    run = f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(drain(f.runs))
    assert f.launcher.calls[0][0][0] == str(override)
    assert f.runs.get(run['id'])['diagnostics']['code'] == 'worker_needs_review'


def test_nonzero_exit_has_durable_diagnostics_in_all_read_views(fixture):
    f = fixture; idea = add(f); f.launcher.options['failure'] = 17
    run = f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(drain(f.runs))
    result = f.runs.get(run['id'])
    diagnostic = result['diagnostics']
    assert result['status'] == 'needs_review'
    assert diagnostic['code'] == 'worker_exit_nonzero' and diagnostic['exit_code'] == 17
    assert type(diagnostic['duration_ms']) is int and diagnostic['duration_ms'] >= 0
    assert f.runs.list()['items'][0]['diagnostics'] == diagnostic
    assert f.runs.for_ideas([idea['id']])['items'][0]['diagnostics'] == diagnostic
    assert f.runs.request(idea['id'], run['request_id']) == result
    recovered = IdeaRuns(f.runs.root, f.manager, f.pipeline, launcher=f.launcher, clock=f.clock)
    assert recovered.get(run['id'])['diagnostics'] == diagnostic
    assert len(f.launcher.calls) == 1


def test_launch_error_retains_only_safe_metadata_and_never_exception_text(fixture):
    f = fixture; idea = add(f)
    secret = 'fixture-token-password-cookie-do-not-retain'
    async def broken_launcher(argv, **options): raise OSError(secret)
    f.runs.launcher = broken_launcher
    run = f.runs.request(idea['id'], str(uuid4()))
    asyncio.run(drain(f.runs))
    result = f.runs.get(run['id'])
    assert result['diagnostics']['code'] == 'worker_launch_failed'
    assert result['diagnostics']['exit_code'] is None
    assert secret not in json.dumps(result)
    with f.store.connect() as db:
        recorded = db.execute('SELECT * FROM idea_manual_run_diagnostics WHERE run_id=?', (run['id'],)).fetchone()
        assert set(recorded.keys()) == {'run_id', 'code', 'exit_code', 'duration_ms', 'updated_at'}
        assert secret not in json.dumps(dict(recorded))
    assert not list((f.runs.runs_root / run['id']).glob('*.log'))


def test_existing_run_database_gains_diagnostics_without_changing_queued_work(fixture):
    f = fixture; idea = add(f); run = f.runs.request(idea['id'], str(uuid4()))
    with f.store.connect() as db: db.execute('DROP TABLE idea_manual_run_diagnostics')
    recovered = IdeaRuns(f.runs.root, f.manager, f.pipeline, launcher=f.launcher, clock=f.clock)
    assert recovered.get(run['id']) == run and not f.launcher.calls


@pytest.mark.parametrize('count',[1,2,5,14])
def test_allocated_count_is_frozen_in_worker_input_and_extra_legacy_slots_are_unused(fixture,count):
    f=fixture;idea=add(f);key=str(uuid4())
    run=f.runs.request(idea['id'],key,variant_count=count)
    assert run['variant_count']==count
    if count<14:f.pipeline.reserve_variants(idea['id'],14)
    asyncio.run(drain(f.runs))
    data=json.loads((f.runs.runs_root/run['id']/'run-input.json').read_text(encoding='utf-8'))
    assert data['variant_count']==count and len(data['variants'])==count
    assert f.runs.get(run['id'])['diagnostics']['code']=='worker_needs_review'
    assert f'Create exactly {count} genuinely distinct' in f.launcher.processes[0].prompt
    assert 'all seven' not in f.launcher.processes[0].prompt
    with pytest.raises(AppError) as error:f.runs.request(idea['id'],key,variant_count=14 if count!=14 else 2)
    assert error.value.code=='idea_run_conflict'


def test_automatic_run_requires_owned_batch_and_preserves_its_trigger(fixture,monkeypatch):
    f=fixture;idea=add(f);key=str(uuid4())
    context={'batch_id':str(uuid4()),'settings_revision':2,'day_key':'2030-11-01'}
    with pytest.raises(AppError) as error:
        f.runs.request(idea['id'],key,variant_count=2,trigger_mode='automatic_idea_batch',automatic_batch=context)
    assert error.value.code=='automatic_batch_unavailable'
    calls=[]
    def authorize(*args,**kwargs):calls.append((args,kwargs));return True
    f.runs.automatic_authorizer=authorize
    monkeypatch.setattr(module,'video_readiness',lambda *_args,**_kwargs:{'workflow_ready':True,
        'configuration':{'ready':True,'workflow_version':'fixture-v2','workflow_sha256':'a'*64}})
    assignments=[{'source_sha256':'b'*64},{'source_sha256':'c'*64}]
    run=f.runs.request(idea['id'],key,variant_count=2,trigger_mode='automatic_idea_batch',automatic_batch=context,audio_assignments=assignments)
    asyncio.run(drain(f.runs))
    data=json.loads((f.runs.runs_root/run['id']/'run-input.json').read_text(encoding='utf-8'))
    assert data['trigger_mode']=='automatic_idea_batch' and data['automatic_batch']==context
    assert data['audio_assignments']==assignments and data['workflow']['sha256']=='a'*64
    assert 'The user clicked' not in f.launcher.processes[0].prompt
    assert any(kwargs.get('running') is True for _,kwargs in calls)


def reviewed_workflow(f,monkeypatch,*,stale=True,change=None):
    from page_manager import idea_workspace
    for name in idea_workspace.VIDEO_WORKFLOW_FILES:
        path=f.output/name;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_text('INERT REVIEWED WORKFLOW '+name)
    config=idea_workspace.video_workflow_configuration(workflow_directory=f.output)
    reviewed=f.clock()-timedelta(days=2) if stale else f.clock()-timedelta(minutes=1)
    claim={'schema_version':1,'kind':'producer_workflow_attestation','status':'ready','producer_workflow_status':'ready',
        'checked_at':reviewed.isoformat(),'workflow_version':config['workflow_version'],'workflow_sha256':config['workflow_sha256']}
    if change=='old_hash':claim['workflow_sha256']='0'*64
    elif change=='future':claim['checked_at']=(f.clock()+timedelta(seconds=1)).isoformat()
    elif change=='blocked':claim['status']='blocked'
    elif change=='legacy':claim.pop('schema_version')
    elif change=='missing_file':(f.output/idea_workspace.VIDEO_WORKFLOW_FILES[-1]).unlink()
    path=f.runs.root/'.runtime/page-video-agent-readiness.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(claim))
    monkeypatch.setattr(module,'video_readiness',lambda root,**kwargs:idea_workspace.video_readiness(root,now=f.clock(),**kwargs))
    return path,claim


def test_auto_request_and_execution_use_unchanged_review_while_manual_stays_expired(fixture,monkeypatch):
    f=fixture;idea=add(f);path,claim=reviewed_workflow(f,monkeypatch);before=path.read_bytes()
    assert not f.runs.readiness()['ready']
    automatic=f.runs.readiness(trigger_mode='automatic_idea_batch')
    assert automatic['ready'] and automatic['workflow_readiness']=={'basis':'reviewed_hash_unchanged',
        'reviewed_at':claim['checked_at'],'validated_at':f.clock().isoformat()}
    with pytest.raises(AppError) as manual:f.runs.request(idea['id'],str(uuid4()),variant_count=2)
    assert manual.value.code=='idea_run_not_ready'
    key=str(uuid4());context={'batch_id':str(uuid4()),'settings_revision':1,'day_key':'2030-11-01'}
    with pytest.raises(AppError) as unauthorized:
        f.runs.request(idea['id'],key,variant_count=2,trigger_mode='automatic_idea_batch',automatic_batch=context)
    assert unauthorized.value.code=='automatic_batch_unavailable'
    calls=[]
    def authorize(iid,request_id,count,batch,*,running=False):
        calls.append(running);return iid==idea['id'] and request_id==key and count==2 and batch==context
    f.runs.automatic_authorizer=authorize;f.launcher.options['status']='ready'
    async def consume(_run,_folder):return {'accepted':True}
    f.runs.on_result=consume
    run=f.runs.request(idea['id'],key,variant_count=2,trigger_mode='automatic_idea_batch',automatic_batch=context)
    asyncio.run(drain(f.runs))
    assert len(f.launcher.calls)==1 and f.runs.get(run['id'])['status']=='completed'
    assert False in calls and True in calls and path.read_bytes()==before
    assert not f.runs.readiness()['ready']


@pytest.mark.parametrize('change',['old_hash','missing_file','future','blocked','legacy'])
def test_auto_request_cannot_use_unreviewed_missing_future_or_blocked_workflow(fixture,monkeypatch,change):
    f=fixture;idea=add(f);reviewed_workflow(f,monkeypatch,change=change)
    f.runs.automatic_authorizer=lambda *_args,**_kwargs:True
    with pytest.raises(AppError) as error:
        f.runs.request(idea['id'],str(uuid4()),variant_count=2,trigger_mode='automatic_idea_batch',automatic_batch={})
    assert error.value.code=='idea_run_not_ready' and not f.launcher.calls and not f.runs.list()['items']


@pytest.mark.parametrize('change',['blocked','future','missing_file'])
def test_auto_execution_rechecks_review_continuity_after_its_request(fixture,monkeypatch,change):
    f=fixture;idea=add(f);reviewed_workflow(f,monkeypatch)
    f.runs.automatic_authorizer=lambda *_args,**_kwargs:True
    run=f.runs.request(idea['id'],str(uuid4()),variant_count=2,trigger_mode='automatic_idea_batch',automatic_batch={})
    reviewed_workflow(f,monkeypatch,change=change)
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls and f.runs.get(run['id'])['status']=='needs_review'


def test_manual_execution_cannot_borrow_auto_continuity_after_claim_expires(fixture,monkeypatch):
    f=fixture;idea=add(f);reviewed_workflow(f,monkeypatch,stale=False)
    run=f.runs.request(idea['id'],str(uuid4()),variant_count=2)
    reviewed_workflow(f,monkeypatch,stale=True)
    assert f.runs.readiness(trigger_mode='automatic_idea_batch')['ready']
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls and f.runs.get(run['id'])['status']=='needs_review'


@pytest.mark.parametrize('gate',['uploader','pages','source'])
def test_auto_continuity_never_bypasses_other_readiness_gates(fixture,monkeypatch,gate):
    f=fixture;add(f);reviewed_workflow(f,monkeypatch)
    assert f.runs.readiness(trigger_mode='automatic_idea_batch')['ready']
    if gate=='uploader':f.provider['available']=False
    elif gate=='pages':monkeypatch.setattr(f.store,'posting_eligibility',lambda *_args,**_kwargs:{'can_publish':False})
    else:
        settings=f.pipeline.settings();settings['campaign_source']={**settings['campaign_source'],'verified':False}
        monkeypatch.setattr(f.pipeline,'settings',lambda:settings)
    ready=f.runs.readiness(trigger_mode='automatic_idea_batch')
    assert ready['video_workflow_ready'] and not ready['ready'] and ready['blocker_codes']


def idle_workspace():
    return {'state':'idle','input_count':0,'review_pending':False,'pending_batch_count':0}


@pytest.mark.parametrize('workspace,code',[
    ({'state':'busy','input_count':1,'review_pending':False,'pending_batch_count':0},'uploader_workspace_busy'),
    ({'state':'unknown','input_count':None,'review_pending':None,'pending_batch_count':None},'uploader_workspace_unknown'),
    ({'state':'idle'},'uploader_workspace_unknown'),
    ({'state':'idle','input_count':1,'review_pending':False,'pending_batch_count':0},'uploader_workspace_unknown'),
])
def test_workspace_gate_blocks_before_request_or_paid_worker_launch(fixture,workspace,code):
    f=fixture;idea=add(f);f.provider['workspace']=workspace
    for trigger in ('manual_web_run_idea','automatic_idea_batch'):
        ready=f.runs.readiness(trigger_mode=trigger)
        assert not ready['ready'] and code in ready['blocker_codes']
    with pytest.raises(AppError):f.runs.request(idea['id'],str(uuid4()),variant_count=2)
    assert not f.runs.list()['items'] and not f.launcher.calls


def test_frozen_provider_workspace_is_rechecked_before_start_without_switching_uploaders(fixture):
    f=fixture;idea=add(f)
    leaf={'ready':True,'available':True,'authenticated':True,'active_domain':'blueox179.com','workspace':idle_workspace()}
    providers={'local':{**leaf,'endpoint':module.LOCAL_ENDPOINT},'web':{**leaf,'endpoint':module.WEB_ENDPOINT}}
    f.provider.update(ready=True,authenticated=True,provider='web',endpoint=module.WEB_ENDPOINT,
        workspace=idle_workspace(),providers=providers)
    run=f.runs.request(idea['id'],str(uuid4()),variant_count=2)
    f.provider.update(provider='local',endpoint=module.LOCAL_ENDPOINT)
    providers['web']['workspace']={'state':'busy','input_count':1,'review_pending':False,'pending_batch_count':0}
    assert f.runs.readiness()['ready']
    asyncio.run(drain(f.runs))
    assert not f.launcher.calls and f.runs.get(run['id'])['diagnostics']['code']=='readiness_changed'


def test_workspace_becoming_busy_does_not_cancel_or_recheck_a_running_worker(fixture):
    f=fixture;idea=add(f);f.provider['workspace']=idle_workspace()
    f.launcher.options['status']='ready'
    async def consume(_run,_folder):return {'accepted':True}
    f.runs.on_result=consume
    async def exercise():
        gate=asyncio.Event();f.launcher.options['block']=gate
        run=f.runs.request(idea['id'],str(uuid4()),variant_count=2)
        work=asyncio.create_task(drain(f.runs))
        async def launched():
            while not f.launcher.processes:await asyncio.sleep(0)
        await asyncio.wait_for(launched(),2)
        f.provider['workspace']={'state':'busy','input_count':1,'review_pending':False,'pending_batch_count':0}
        gate.set();await work
        return run
    run=asyncio.run(exercise())
    assert f.runs.get(run['id'])['status']=='completed' and len(f.launcher.calls)==1
    assert not f.launcher.processes[0].terminated and not f.launcher.processes[0].killed


def test_refused_second_run_cannot_grow_canonical_reservations(fixture):
    f=fixture;idea=add(f)
    f.runs.request(idea['id'],str(uuid4()),variant_count=2)
    with f.store.connect() as db:
        before=[tuple(row) for row in db.execute('SELECT id,reservation_id FROM idea_pipeline_variants WHERE idea_id=? ORDER BY ordinal',(idea['id'],))]
    with pytest.raises(AppError) as error:f.runs.request(idea['id'],str(uuid4()),variant_count=14)
    assert error.value.code=='idea_run_exists'
    with f.store.connect() as db:
        after=[tuple(row) for row in db.execute('SELECT id,reservation_id FROM idea_pipeline_variants WHERE idea_id=? ORDER BY ordinal',(idea['id'],))]
    assert after==before


def test_native_capability_image_is_reused_once_inside_allocated_count(fixture):
    f=fixture;idea=add(f)
    image=f.runs.root.parent/'integration-diagnostics/.runtime/producer-pilot/image-probe/fixture.png'
    image.parent.mkdir(parents=True);image.write_bytes(PNG+b'fresh native probe image fixture')
    f.runs.root.joinpath('.runtime').mkdir(parents=True,exist_ok=True)
    receipt={'source_sha256':idea['artwork_sha256'],'image_path':str(image),'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'origin':'codex_native_imagegen'}
    (f.runs.root/'.runtime/producer-image-capability.json').write_text(json.dumps(receipt),encoding='utf-8')
    run=f.runs.request(idea['id'],str(uuid4()),variant_count=2);asyncio.run(drain(f.runs))
    data=json.loads((f.runs.runs_root/run['id']/'run-input.json').read_text(encoding='utf-8'))
    assert len(data['variants'])==2 and len(data['preflight_images'])==1
    assert Path(data['preflight_images'][0]['image_path']).read_bytes()==image.read_bytes()
    assert 'Generate only the remaining distinct images' in f.launcher.processes[0].prompt
