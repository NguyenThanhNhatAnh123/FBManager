"""Daily orchestration tests use fake CLI/session checks and a disposable ledger."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from test_idea_runs import fixture,add
from page_manager import idea_runs as runs_module
from page_manager.auto_batch_planner import AutoBatchPlanner
from page_manager.idea_automation import AutoIdeaController,_audio_plan


@pytest.fixture
def automatic(fixture,monkeypatch):
    f=fixture
    monkeypatch.setattr(runs_module,'video_readiness',lambda *_args,**_kwargs:{'workflow_ready':True,
        'configuration':{'ready':True,'workflow_version':'fixture-v2','workflow_sha256':'a'*64}})
    events=[];ready={'authenticated':True,'ready':True}
    class Bridge:
        def inspect(self):return {**ready,'provider':'local','endpoint':runs_module.LOCAL_ENDPOINT,
            'providers':{'local':{**ready,'available':True,'active_domain':'blueox179.com'}}}
        def check_shop_session(self,kind):events.append(('session',kind));return self.inspect()
    class Diagnostics:
        authenticated=True
        async def check(self,kind):events.append(('cli',kind));return self.snapshot()
        def snapshot(self):return {'providers':{'codex':{'authenticated':self.authenticated,'image_generation':{'configured':True}}}}
    bridge=Bridge();diagnostics=Diagnostics();f.runs.upload_bridge=bridge
    async def consume(_run,_folder):return {'accepted':True}
    f.runs.on_result=consume
    planner=AutoBatchPlanner(f.store,clock=f.clock);elapsed=[0]
    def audio(_store,batch):return [{'index':i+1,'source_sha256':format(i+1,'064x')} for i in range(sum(v['variant_count'] for v in batch['items']))]
    controller=AutoIdeaController(f.manager,f.pipeline,f.runs,bridge,diagnostics,planner=planner,
        clock=f.clock,monotonic=lambda:elapsed[0],coalesce_seconds=0,audio_factory=audio)
    return SimpleNamespace(f=f,planner=planner,controller=controller,events=events,ready=ready,diagnostics=diagnostics,elapsed=elapsed)


def add_ideas(a,count):
    return [add(a.f,f'idea-{i}.png',b'\x89PNG\r\n\x1a\nfixture'+bytes([i])) for i in range(count)]


def test_disabled_has_preview_but_does_not_probe_or_dispatch(automatic):
    a=automatic;add_ideas(a,7)
    assert [v['variant_count'] for v in a.controller.status()['preview']['allocations']]==[2]*7
    asyncio.run(a.controller._step())
    assert not a.events and not a.f.runs.list()['items']


def test_missing_consumer_blocks_before_probes_claim_or_dispatch(automatic):
    a=automatic;add_ideas(a,3);a.controller.configure(True,1);a.f.runs.on_result=None
    asyncio.run(a.controller._run_step())
    assert a.controller.state=='blocked' and a.controller.blockers==['consumer_unavailable']
    assert not a.events and not a.planner.current_batch()
    assert not a.f.runs.list()['items'] and not a.f.launcher.calls


def test_auto_preflight_requests_explicit_automatic_readiness(automatic,monkeypatch):
    a=automatic;seen=[]
    def readiness(*,trigger_mode='manual_web_run_idea'):
        seen.append(trigger_mode)
        return {'ready':trigger_mode=='automatic_idea_batch','video_workflow_ready':trigger_mode=='automatic_idea_batch'}
    monkeypatch.setattr(a.f.runs,'readiness',readiness)
    assert asyncio.run(a.controller._preflight())
    assert seen==['automatic_idea_batch'] and not a.f.runs.list()['items']


def test_auto_continuity_does_not_bypass_current_audio_approval(automatic,monkeypatch):
    a=automatic
    a.f.runs.audio_approvals=SimpleNamespace(snapshot=lambda:{'ready':False})
    monkeypatch.setattr(a.f.runs,'readiness',lambda **_kwargs:(_ for _ in ()).throw(AssertionError('Audio must gate before workflow continuity')))
    assert not asyncio.run(a.controller._preflight())
    assert a.controller.blockers==['audio_mixes_not_approved'] and not a.events


@pytest.mark.parametrize('workspace,code',[
    ({'state':'busy','input_count':1,'review_pending':False,'pending_batch_count':0},'uploader_workspace_busy'),
    ({'state':'unknown','input_count':None,'review_pending':None,'pending_batch_count':None},'uploader_workspace_unknown')])
def test_auto_workspace_gate_prevents_claim_and_dispatch_before_images(automatic,workspace,code):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1);a.ready['workspace']=workspace
    asyncio.run(a.controller._run_step())
    assert a.controller.state=='blocked' and code in a.controller.blockers
    assert not a.planner.current_batch() and not a.f.runs.list()['items'] and not a.f.launcher.calls


def test_seven_ideas_freeze_two_each_and_reconcile_restart_without_duplicate_dispatch(automatic):
    a=automatic;add_ideas(a,7);a.controller.configure(True,1)
    asyncio.run(a.controller._step())
    batch=a.planner.find_incomplete_batch();assert [v['variant_count'] for v in batch['items']]==[2]*7
    assert len(a.f.runs.list()['items'])==1
    first=a.f.runs.list()['items'][0];assert first['variant_count']==2
    asyncio.run(a.controller._step());assert len(a.f.runs.list()['items'])==1
    with a.f.store.connect() as db:db.execute("UPDATE idea_manual_runs SET status='completed' WHERE id=?",(first['id'],))
    # A new image after claim belongs to a later batch, preserving this plan.
    add(a.f,'later.png',b'\x89PNG\r\n\x1a\nlate')
    asyncio.run(a.controller._step())
    runs=a.f.runs.list()['items'];assert len(runs)==2 and all(v['variant_count']==2 for v in runs)
    assert len(a.planner.batch(batch['id'])['items'])==7
    assert a.planner.batch(batch['id'])['items'][0]['status']=='completed'


def test_long_queued_auto_run_refreshes_session_proofs_before_expiry_without_redispatch(automatic,monkeypatch):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    checked={'cli':0,'shop':0}
    async def check_cli(kind):
        a.events.append(('cli',kind));checked['cli']=a.elapsed[0]
    def cli_snapshot():
        return {'providers':{'codex':{'authenticated':a.elapsed[0]-checked['cli']<300,
            'image_generation':{'configured':True}}}}
    original=a.controller.upload_bridge.inspect
    def overview():
        fresh=a.elapsed[0]-checked['shop']<300
        a.ready.update(authenticated=True if fresh else None,ready=fresh)
        return original()
    def check_shop(kind):
        a.events.append(('session',kind));checked['shop']=a.elapsed[0];return overview()
    monkeypatch.setattr(a.diagnostics,'check',check_cli)
    monkeypatch.setattr(a.diagnostics,'snapshot',cli_snapshot)
    monkeypatch.setattr(a.controller.upload_bridge,'inspect',overview)
    monkeypatch.setattr(a.controller.upload_bridge,'check_shop_session',check_shop)
    asyncio.run(a.controller._step());run=a.f.runs.list()['items'][0];before=list(a.events)
    a.elapsed[0]=239;asyncio.run(a.controller._step())
    assert a.events==before
    a.elapsed[0]=240;asyncio.run(a.controller._step())
    assert checked=={'cli':240,'shop':240}
    assert a.events==before+[('cli','codex'),('session','local')]
    a.elapsed[0]=301;asyncio.run(a.controller._step())
    assert cli_snapshot()['providers']['codex']['authenticated'] is True and overview()['authenticated'] is True
    assert a.controller.state=='running' and a.controller.blockers==[]
    assert [item['id'] for item in a.f.runs.list()['items']]==[run['id']]
    assert a.planner.find_incomplete_batch()['items'][0]['status']=='queued'
    assert not a.f.launcher.calls


@pytest.mark.parametrize('failure',['cli','shop'])
def test_queued_connection_maintenance_failure_does_not_hold_cancel_or_retry_run(automatic,monkeypatch,failure):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1);asyncio.run(a.controller._step())
    run=a.f.runs.list()['items'][0];a.elapsed[0]=240
    def fail(*_args):raise RuntimeError('PRIVATE SYNTHETIC UPSTREAM ERROR')
    if failure=='cli':
        async def failed_check(*_args):fail()
        monkeypatch.setattr(a.diagnostics,'check',failed_check)
    else:monkeypatch.setattr(a.controller.upload_bridge,'check_shop_session',fail)
    asyncio.run(a.controller._run_step())
    assert a.controller.state=='running' and a.controller.blockers==[]
    rows=a.f.runs.list()['items'];assert len(rows)==1 and rows[0]['id']==run['id'] and rows[0]['status']=='queued'
    assert 'PRIVATE' not in json.dumps(a.controller.status()) and not a.f.launcher.calls
    assert a.planner.find_incomplete_batch()['items'][0]['status']=='queued'


def test_paused_auto_never_renews_sessions_for_its_existing_queued_run(automatic):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1);asyncio.run(a.controller._step())
    run=a.f.runs.list()['items'][0];a.controller.configure(False,a.planner.settings()['revision'])
    events=list(a.events);a.elapsed[0]=400;asyncio.run(a.controller._step())
    assert a.events==events and a.controller.status()['state']=='disabled'
    assert [item['id'] for item in a.f.runs.list()['items']]==[run['id']]


@pytest.mark.parametrize('broken',['cli','session','workflow'])
def test_preflight_blockers_never_claim_or_launch(automatic,monkeypatch,broken):
    a=automatic;add_ideas(a,3);a.controller.configure(True,1)
    if broken=='cli':a.diagnostics.authenticated=False
    elif broken=='session':a.ready.update(authenticated=None,ready=False)
    else:monkeypatch.setattr(a.f.runs,'readiness',lambda **_kwargs:{'ready':False,'video_workflow_ready':False})
    asyncio.run(a.controller._step())
    assert a.controller.state=='blocked' and not a.planner.current_batch()
    assert not a.f.runs.list()['items'] and not a.f.launcher.calls


@pytest.mark.parametrize('status',['needs_review','cancelled'])
def test_review_or_user_cancel_holds_batch_without_auto_retry(automatic,status):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    asyncio.run(a.controller._step());run=a.f.runs.list()['items'][0]
    with a.f.store.connect() as db:db.execute('UPDATE idea_manual_runs SET status=? WHERE id=?',(status,run['id']))
    asyncio.run(a.controller._step());asyncio.run(a.controller._step())
    assert a.controller.state=='needs_review' and len(a.f.runs.list()['items'])==1
    assert a.planner.find_incomplete_batch()['status']=='needs_review'


@pytest.mark.parametrize('another_hold',[False,True])
def test_review_resolves_only_from_canonical_completed_proof(automatic,another_hold):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    asyncio.run(a.controller._run_step());run=a.f.runs.list()['items'][0]
    with a.f.store.connect() as db:db.execute("UPDATE idea_manual_runs SET status='needs_review' WHERE id=?",(run['id'],))
    asyncio.run(a.controller._run_step());batch=a.planner.find_incomplete_batch()
    assert batch['items'][0]['status']=='needs_review'
    if another_hold:
        a.planner.mark_item(batch['id'],batch['items'][1]['idea_id'],'needs_review',error_code='uncertain')
    asyncio.run(a.controller._run_step())
    assert len(a.f.runs.list()['items'])==1 and a.controller.state=='needs_review'
    # Simulate the trusted evidence recovery tool, never a blind producer retry.
    with a.f.store.connect() as db:db.execute("UPDATE idea_manual_runs SET status='completed' WHERE id=?",(run['id'],))
    asyncio.run(a.controller._run_step())
    resolved=a.planner.batch(batch['id'])
    assert resolved['items'][0]['status']=='completed'
    assert resolved['snapshot']==batch['snapshot']
    assert len(a.f.runs.list()['items'])==(1 if another_hold else 2)
    assert a.controller.state==('needs_review' if another_hold else 'running')


def test_audio_is_shared_across_all_ideas_and_replay_checks_original_hashes(automatic,tmp_path):
    import hashlib
    from uuid import uuid4
    a=automatic;entries=[]
    for index in range(3):
        source=tmp_path/f'track-{index}.wav';source.write_bytes(bytes([index])*100)
        entries.append({'relative_path':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'duration_seconds':index+2})
    library=tmp_path/'library.json';library.write_text(json.dumps({'files':entries}),encoding='utf-8')
    batch={'id':str(uuid4()),'items':[{'variant_count':2} for _ in range(7)]}
    assignments=_audio_plan(a.f.store,batch,library)
    assert len(assignments)==14
    for start in range(0,12,3):assert len({row['source_sha256'] for row in assignments[start:start+3]})==3
    assert all(left['source_sha256']!=right['source_sha256'] for left,right in zip(assignments,assignments[1:]))
    assert _audio_plan(a.f.store,batch,library)==assignments
    (tmp_path/'track-0.wav').write_bytes(b'changed')
    with pytest.raises(ValueError):_audio_plan(a.f.store,batch,library)


def test_source_change_after_claim_holds_pending_batch_without_dispatch_retry(automatic,monkeypatch):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    preview=a.planner.preview();batch=a.planner.claim_day(preview['revision'],preview['plan_hash'])
    with a.f.store.connect() as db:db.execute('UPDATE video_source_settings SET revision=revision+1')
    calls=[];original=a.f.runs.request
    def request(*args,**kwargs):
        calls.append((args,kwargs));return original(*args,**kwargs)
    monkeypatch.setattr(a.f.runs,'request',request)
    asyncio.run(a.controller._run_step());asyncio.run(a.controller._run_step())
    assert a.controller.state=='needs_review'
    held=a.planner.batch(batch['id'])
    assert held['status']=='needs_review'
    assert all(item['status']=='needs_review' and item['error_code']=='source_changed' for item in held['items'])
    assert not calls and not a.f.runs.list()['items']


def test_source_changed_during_audio_allocation_holds_before_request(automatic):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1);original=a.controller.audio_factory
    def audio(store,batch):
        with store.connect() as db:db.execute('UPDATE video_source_settings SET revision=revision+1')
        return original(store,batch)
    a.controller.audio_factory=audio
    asyncio.run(a.controller._run_step())
    assert a.controller.state=='needs_review' and not a.f.runs.list()['items']
    assert all(item['error_code']=='source_changed' for item in a.planner.find_incomplete_batch()['items'])


def test_user_manual_cancel_after_claim_holds_automatic_item_instead_of_retrying(automatic,monkeypatch):
    from uuid import uuid4
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    preview=a.planner.preview();batch=a.planner.claim_day(preview['revision'],preview['plan_hash'])
    item=batch['items'][0]
    manual=a.f.runs.request(item['idea_id'],str(uuid4()))
    asyncio.run(a.f.runs.cancel(manual['id']))
    calls=[];original=a.f.runs.request
    def request(*args,**kwargs):
        calls.append((args,kwargs));return original(*args,**kwargs)
    monkeypatch.setattr(a.f.runs,'request',request)
    asyncio.run(a.controller._run_step());asyncio.run(a.controller._run_step())
    assert a.controller.state=='needs_review'
    assert a.planner.item(batch['id'],item['idea_id'])['error_code']=='run_conflict'
    assert len(calls)==1 and len(a.f.runs.list()['items'])==1


def test_request_ledger_crash_reconciles_existing_run_exactly_once(automatic,monkeypatch):
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    original=a.planner.mark_item;crash=[True]
    def mark(*args,**kwargs):
        if crash[0] and args[2]=='queued':
            crash[0]=False;raise RuntimeError('synthetic crash after canonical request')
        return original(*args,**kwargs)
    monkeypatch.setattr(a.planner,'mark_item',mark)
    asyncio.run(a.controller._run_step())
    assert len(a.f.runs.list()['items'])==1
    assert a.planner.find_incomplete_batch()['items'][0]['status']=='pending'
    asyncio.run(a.controller._run_step())
    assert len(a.f.runs.list()['items'])==1
    assert a.planner.find_incomplete_batch()['items'][0]['status']=='queued'


def test_pause_during_audio_allocation_prevents_dispatch_and_reuses_frozen_batch(automatic):
    import threading
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    entered=threading.Event();release=threading.Event();original=a.controller.audio_factory
    def audio(store,batch):
        entered.set()
        if not release.wait(3):raise RuntimeError('synthetic wait expired')
        return original(store,batch)
    a.controller.audio_factory=audio
    async def scenario():
        task=asyncio.create_task(a.controller._run_step())
        assert await asyncio.to_thread(entered.wait,3)
        frozen=a.planner.find_incomplete_batch()
        a.controller.configure(False,a.planner.settings()['revision']);release.set();await task
        assert not a.f.runs.list()['items']
        assert a.planner.find_incomplete_batch()['snapshot']==frozen['snapshot']
        a.controller.configure(True,a.planner.settings()['revision'])
        await a.controller._run_step()
        assert a.planner.find_incomplete_batch()['id']==frozen['id']
        assert len(a.f.runs.list()['items'])==1
    asyncio.run(scenario())


def test_shutdown_waits_for_owned_audio_thread_and_never_dispatches(automatic):
    import threading
    a=automatic;add_ideas(a,2);a.controller.configure(True,1)
    entered=threading.Event();release=threading.Event();finished=threading.Event();original=a.controller.audio_factory
    def audio(store,batch):
        entered.set()
        if not release.wait(3):raise RuntimeError('synthetic wait expired')
        result=original(store,batch);finished.set();return result
    a.controller.audio_factory=audio
    async def scenario():
        a.controller.tick()
        assert await asyncio.to_thread(entered.wait,3)
        stopping=asyncio.create_task(a.controller.shutdown())
        try:
            await asyncio.sleep(.02)
            assert not stopping.done(), 'Shutdown released ownership while audio thread could still write'
            assert not finished.is_set()
        finally:
            release.set();await stopping
        assert finished.is_set() and not a.f.runs.list()['items']
        assert all(item['status']=='pending' for item in a.planner.find_incomplete_batch()['items'])
    asyncio.run(scenario())


def test_all_seven_items_receive_one_contiguous_shared_fourteen_track_plan(automatic):
    a=automatic;add_ideas(a,7);a.controller.configure(True,1)
    observed=[]
    for _ in range(7):
        asyncio.run(a.controller._run_step())
        with a.f.store.connect() as db:
            row=db.execute("SELECT id,payload FROM idea_manual_runs WHERE status='queued'").fetchone()
            assert row is not None
            payload=json.loads(row['payload'])
            assert payload['variant_count']==len(payload['variants'])==len(payload['audio_assignments'])==2
            observed.extend(item['index'] for item in payload['audio_assignments'])
            db.execute("UPDATE idea_manual_runs SET status='completed' WHERE id=?",(row['id'],))
    asyncio.run(a.controller._run_step());asyncio.run(a.controller._run_step())
    assert observed==list(range(1,15))
    assert a.planner.current_batch()['status']=='completed'
    assert len(a.f.runs.list()['items'])==7 and not a.planner.find_incomplete_batch()
