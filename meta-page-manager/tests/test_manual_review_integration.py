"""Manual per-item integration on isolated ledgers; no model, Telegram or Meta IO."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager.idea_runs import _json
from page_manager.publication_approvals import PublicationApprovals
from page_manager.store import Store
from test_idea_runs import fixture,add
from test_video_source import setup as source_fixture,settings,write,ready_scan
from page_manager.idea_pipeline import IdeaPipeline
from conftest import immediate


def running(f,count=2):
    idea=add(f);run=f.runs.request(idea['id'],str(uuid4()),variant_count=count)
    row=f.runs._row(run['id']);payload=json.loads(row['payload']);payload['review_policy']='manual_review_v1'
    encoded=_json(payload)
    with f.store.connect() as db:
        db.execute("UPDATE idea_manual_runs SET payload=?,payload_hash=?,status='running' WHERE id=?",
            (encoded,hashlib.sha256(encoded.encode()).hexdigest(),run['id']))
    row=f.runs._row(run['id']);folder=f.output/'per-item-test';(folder/'handoff').mkdir(parents=True)
    return row,folder


def test_host_consumes_each_ready_item_without_waiting_for_all_and_without_replay(fixture):
    f=fixture;row,folder=running(f);calls=[];accepted=set()
    async def consume(run,directory,ordinal):
        calls.append((run['id'],directory,ordinal));return {'accepted':True,'status':'manual_review_pending','ai_qa':False}
    f.runs.on_item=consume
    async def scenario():
        await f.runs._consume_items(row,folder,accepted)
        assert calls==[] and accepted==set()
        (folder/'handoff/01.json').write_text('{}')
        await f.runs._consume_items(row,folder,accepted)
        assert accepted=={1} and [call[2] for call in calls]==[1]
        assert not (folder/'handoff/02.json').exists()
        await f.runs._consume_items(row,folder,accepted)
        assert [call[2] for call in calls]==[1]
        (folder/'handoff/02.json').write_text('{}')
        await f.runs._consume_items(row,folder,accepted)
        assert accepted=={1,2} and [call[2] for call in calls]==[1,2]
        assert f.runs._row(row['id'])['status']=='running'
    asyncio.run(scenario())
    assert not f.launcher.calls and not f.store.reel_jobs()


def test_host_cancellation_marks_guard_before_owned_consumer_cleanup(fixture):
    f=fixture;row,folder=running(f);(folder/'handoff/01.json').write_text('{}');observed=[]
    async def scenario():
        entered=asyncio.Event()
        async def consume(*_):
            entered.set()
            try:await asyncio.Event().wait()
            finally:
                current=f.runs._row(row['id']);observed.append((current['status'],current['cancel_requested']))
        f.runs.on_item=consume
        task=asyncio.create_task(f.runs._consume_items(row,folder,set()))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(scenario())
    assert observed==[('needs_review',1)]
    assert not f.launcher.calls and not f.store.reel_jobs()


def test_host_cancel_or_changed_source_prevents_next_item_callback(fixture):
    f=fixture;row,folder=running(f);accepted=set();calls=[]
    (folder/'handoff/01.json').write_text('{}');(folder/'handoff/02.json').write_text('{}')
    async def consume(*args):
        calls.append(args[-1])
        with f.store.connect() as db:db.execute('UPDATE idea_manual_runs SET cancel_requested=1 WHERE id=?',(row['id'],))
        return {'accepted':True}
    f.runs.on_item=consume
    with pytest.raises(asyncio.CancelledError):asyncio.run(f.runs._consume_items(row,folder,accepted))
    assert calls==[1] and accepted=={1}
    assert not f.launcher.calls and not f.store.reel_jobs()


@pytest.mark.parametrize('queue',['token','chrome'])
def test_known_manual_video_requires_review_for_reused_asset_in_either_queue(tmp_path,queue):
    store=Store(tmp_path);now=datetime(2030,1,1,12,tzinfo=timezone.utc)
    ledger=PublicationApprovals(store,clock=lambda:now)
    sha='b'*64
    with store.connect() as db:
        db.execute('CREATE TABLE idea_pipeline_approved_videos(video_sha256 TEXT,reservation_id TEXT,status TEXT)')
        db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?)',(sha,str(uuid4()),'manual_review_pending'))
    binding={'queue':queue,'job_id':91,'page_id':'555','asset_id':'a'*32,'asset_sha256':sha,'asset_size':30,
        'caption':'https://blueox179.com/campaign/test','scheduled_at':(now+timedelta(hours=1)).isoformat(),
        'timezone':'America/Los_Angeles','actor_id':1,'actor_revision':1}
    assert ledger.settings()['required'] is False
    assert not ledger.verdict(binding)['can_publish']
    first=ledger.ensure(binding)
    assert first['required'] and first['state']=='pending' and first==ledger.ensure(binding)
    assert ledger.pending()['total']==1
    assert ledger.pending()['items'][0]['binding']==binding
    assert ledger.verdict(binding|{'asset_sha256':'c'*64})['can_publish'] is True


@pytest.mark.parametrize('cancelled',[False,True])
def test_source_imports_one_manual_item_after_two_stable_observations_without_full_set(source_fixture,tmp_path,cancelled):
    f=source_fixture;f.source.configure(settings(f));ledger=PublicationApprovals(f.manager.store,clock=lambda:f.clock[0])
    ideas=tmp_path/'ideas';ideas.mkdir();producer=tmp_path/'producer';producer.mkdir()
    pipeline=IdeaPipeline(f.manager,directory=ideas,output_directory=producer)
    path=write(f,'one-manual-video.mp4');sha=hashlib.sha256(path.read_bytes()).hexdigest()
    iid,rid,vid,reservation=(str(uuid4()) for _ in range(4));caption='https://blueox179.com/campaign/one-manual'
    encoded=_json({'review_policy':'manual_review_v1','variant_count':7})
    revision=f.source.status()['settings']['revision']
    with f.manager.store.connect() as db:
        db.execute('CREATE TABLE idea_manual_runs(id TEXT,idea_id TEXT,artwork_sha256 TEXT,status TEXT,cancel_requested INTEGER,payload TEXT,payload_hash TEXT)')
        db.execute('CREATE TABLE idea_pipeline_approved_videos(video_sha256 TEXT,variant_id TEXT,reservation_id TEXT,idea_id TEXT,run_id TEXT,caption TEXT,status TEXT,source_revision INTEGER)')
        db.execute('INSERT INTO idea_pipeline_ideas VALUES(?,?,?,?,?,?)',(iid,'d'*64,'one.png','verified',json.dumps({'campaign_url':caption}),'2030-11-01T12:00:00Z'))
        db.execute('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?,?,?,?,?)',(vid,iid,1,'metadata_registered',sha,0,'{}',reservation))
        db.execute('INSERT INTO idea_manual_runs VALUES(?,?,?,?,?,?,?)',(rid,iid,'d'*64,'running',int(cancelled),encoded,hashlib.sha256(encoded.encode()).hexdigest()))
        db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?,?,?,?,?)',(sha,vid,reservation,iid,rid,caption,'manual_review_pending',revision))
    immediate(f.source.scan_once())
    assert not f.manager.store.reel_jobs() and not f.manager.store.reel_assets()
    f.clock[0]+=timedelta(seconds=31);immediate(f.source.scan_once())
    jobs=f.manager.store.reel_jobs()
    assert len(jobs)==(0 if cancelled else 1)
    if not cancelled:
        job=jobs[0]
        assert job['request_id']==reservation and job['caption']==caption and job['status']=='scheduled'
        assert f.manager.store.reel_asset(job['asset_id'])['sha256']==sha
        assert ledger.settings()['required'] is False
        assert ledger.pending()['total']==1
        assert ledger.pending()['items'][0]['state']=='pending'
        with f.manager.store.connect() as db:
            assert db.execute('SELECT status FROM idea_manual_runs').fetchone()[0]=='running'
            assert db.execute('SELECT COUNT(*) FROM idea_pipeline_variants').fetchone()[0]==1
        immediate(f.source.scan_once())
        assert len(f.manager.store.reel_jobs())==1 and ledger.pending()['total']==1


def test_owned_host_delivers_first_video_before_worker_exit_without_waiting_for_second(fixture,monkeypatch):
    from test_rolling_idea_runs import rolling
    f=fixture;rolling(f,monkeypatch)
    run=f.runs.request(add(f)['id'],str(uuid4()),variant_count=2)
    seen=[];processes=[]
    async def scenario():
        first=asyncio.Event();release=asyncio.Event()
        class Process:
            returncode=None
            async def communicate(self,prompt):
                assert prompt==b'ROLLING_V1_START\n'
                folder=f.runs.runs_root/run['id'];(folder/'handoff').mkdir()
                (folder/'handoff/01.json').write_text('{}')
                await release.wait()
                (folder/'handoff/02.json').write_text('{}')
                row=f.runs._row(run['id'])
                output={'schema_version':'1.0','run_id':run['id'],'idea_id':run['idea_id'],
                    'artwork_sha256':run['artwork_sha256'],'lease_fence':row['fence'],
                    'review_policy':'manual_review_v1','ai_qa':False,'campaign':{},
                    'status':'manual_review_pending','reason':'ready',
                    'handoff_paths':[str(folder/'handoff'/f'{n:02}.json') for n in (1,2)]}
                (folder/'worker-output.json').write_text(json.dumps(output))
                self.returncode=0
                return None,None
            async def wait(self):return self.returncode
            def terminate(self):self.returncode=-15;release.set()
            def kill(self):self.returncode=-9;release.set()
        async def launch(*_args,**_kwargs):
            process=Process();processes.append(process);return process
        async def consume(_run,folder,ordinal):
            seen.append(ordinal)
            if ordinal==1:
                assert processes[0].returncode is None
                assert not (folder/'handoff/02.json').exists()
                assert f.runs._row(run['id'])['status']=='running'
                first.set()
            return {'accepted':True,'status':'manual_review_pending','ai_qa':False}
        f.runs.launcher=launch;f.runs.on_item=consume
        task=asyncio.create_task(f.runs._run())
        try:
            await asyncio.wait_for(first.wait(),5)
            assert seen==[1]
            release.set();await asyncio.wait_for(task,5)
        finally:
            if not task.done():
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
    asyncio.run(scenario())
    assert seen==[1,2]
    value=f.runs.get(run['id'])
    assert value['status']=='completed' and value['review_policy']=='manual_review_v1' and value['ai_qa'] is False
    assert not f.store.reel_jobs() and not f.store.reel_assets()
