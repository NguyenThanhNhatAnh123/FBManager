"""Scheduler protocol/recovery tests: temporary SQLite and fake Graph only."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from page_manager.errors import AppError
from page_manager.graph import GraphError,GraphMutationUncertain
from page_manager.inputs import ConnectionInput,ConnectionUpdate
from page_manager.service import PageManager
from page_manager.reel_scheduler import TokenReelScheduler
from page_manager import reel_scheduler as module
from conftest import FixtureBox,immediate,mark_page_information_updated

TOKEN='FixtureSchedulerPageToken82'
NOW=datetime.now(timezone.utc)


class Assets:
    def __init__(self,row):self.row=row;self.calls=0
    def resolve(self,asset_id):
        self.calls+=1
        assert asset_id==self.row['id']
        if hashlib.sha256(self.row['path'].read_bytes()).hexdigest()!=self.row['sha256']:raise ValueError('Fixture asset changed')
        return dict(self.row)


class Graph:
    def __init__(self):
        self.calls=[];self.on_start=None;self.on_upload=None;self.on_finish=None;self.error=None;self.statuses=[];self.store=None;self.jid=None
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def identity(self,token):self.calls.append(('identity',token));return {'id':'555'}
    async def start_reel(self,token,pid):
        assert self.store.reel_job(self.jid)['checkpoint']['start_attempted'] is True
        self.calls.append(('start',token,pid))
        if self.on_start:self.on_start()
        if self.error=='start':raise GraphMutationUncertain()
        return '999'
    async def upload_reel(self,token,vid,path,size,*,sha256=None,before_send=None):
        assert self.store.reel_job(self.jid)['checkpoint']['upload_attempted'] is True
        assert sha256==hashlib.sha256(path.read_bytes()).hexdigest()
        if before_send:before_send()
        self.calls.append(('upload',token,vid,str(path),size))
        if self.on_upload:self.on_upload()
        if self.error=='upload':raise GraphMutationUncertain()
        return True
    async def finish_reel(self,token,pid,vid,caption):
        assert self.store.reel_job(self.jid)['checkpoint']['finish_attempted'] is True
        self.calls.append(('finish',token,pid,vid,caption))
        if self.on_finish:self.on_finish()
        if self.error=='finish':raise GraphMutationUncertain()
        return True
    async def reel_status(self,token,pid,vid):
        self.calls.append(('status',token,pid,vid))
        if self.statuses:return deepcopy(self.statuses.pop(0))
        attempted=self.store.reel_job(self.jid)['checkpoint'].get('finish_attempted')
        return {'video_id':vid,'owner_matches':True,'owner_conflict':False,'upload_complete':True,'processing_complete':False,
                'published':bool(attempted),'failed':False,'published_url':'https://www.facebook.com/reel/'+vid if attempted else None}


def connect(manager,pid='555',token=TOKEN):
    row=manager.save_connection(ConnectionInput(label='Fixture source '+pid,token_type='page',token=token));cid=row['id']
    manager.store.sync(cid,row.get('token_revision',1),[{'id':pid,'name':'Fixture Page '+pid,'sources':['page'],'tasks':['CREATE_CONTENT']}],True,'working',None,{'id':pid})
    mark_page_information_updated(manager.store,pid,cid,row.get('token_revision',1))
    return cid


def create(manager,asset,cid,pid='555',due=None):
    row,_=manager.store.reel_create({'request_id':str(uuid4()),'page_id':pid,'page_name':'Fixture Page '+pid,'connection_id':cid,
          'connection_label':'Fixture source '+pid,'token_revision':manager.store.connection(cid)['token_revision'],'asset_id':asset['id'],'asset_name':asset['name'],
          'caption':'Exact fixture caption','scheduled_at':(due or NOW-timedelta(minutes=1)).isoformat(),
          'scheduled_local':'2026-10-31T10:00:00','timezone':'UTC','fold':0,'approval_policy':None})
    return row


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    manager=PageManager(tmp_path,secret_box=FixtureBox());cid=connect(manager)
    path=tmp_path/'fixture.mp4';path.write_bytes(b'private video fixture')
    asset={'id':'a'*32,'name':'fixture.mp4','path':path,'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mime':'video/mp4'}
    manager.store.reel_asset_save(asset);assets=Assets(asset);graph=Graph();graph.store=manager.store
    scheduler=TokenReelScheduler(manager,assets,graph_factory=lambda:graph,clock=lambda:NOW);scheduler.POLL_ATTEMPTS=2
    async def thread(fn,*args):return fn(*args)
    async def wait():pass
    monkeypatch.setattr(module.asyncio,'to_thread',thread);monkeypatch.setattr(scheduler,'_wait',wait)
    row=create(manager,asset,cid);graph.jid=row['id']
    return scheduler,manager,assets,graph,row


def mutations(graph):return [call for call in graph.calls if call[0] in ('start','upload','finish')]


def approval_fixture(fixture):
    from page_manager.publication_approvals import PublicationApprovals
    scheduler,manager,assets,graph,old=fixture
    clock=[NOW]
    ledger=PublicationApprovals(manager.store,clock=lambda:clock[0]);ledger.configure(True,1)
    scheduler.approvals=ledger
    row=create(manager,assets.row,old['connection_id'],due=NOW+timedelta(minutes=1));graph.jid=row['id']
    return scheduler,manager,assets,graph,row,ledger,clock


def approve(ledger,row):
    return ledger.decide(row['request_id'],row['binding_hash'],'approve',actor_id=123,chat_id=456,update_id=1,message_id=2)


def immediate_fixture(fixture):
    from page_manager.publication_approvals import PublicationApprovals
    scheduler,manager,assets,graph,old=fixture
    manager.store.reel_update(old['id'],status='cancelled')
    clock=[NOW];ledger=PublicationApprovals(manager.store,clock=lambda:clock[0]);scheduler.approvals=ledger
    values={**old,'request_id':str(uuid4()),'publish_mode':'on_approval',
            'scheduled_at':(NOW-timedelta(minutes=20)).isoformat(),'approve_by':(NOW+timedelta(hours=1)).isoformat()}
    row,_=manager.store.reel_create(values);graph.jid=row['id'];scheduler.clock=lambda:clock[0]
    return scheduler,manager,assets,graph,row,ledger,clock


def test_immediate_waits_for_real_human_then_becomes_due_without_rescheduling(fixture,monkeypatch):
    scheduler,manager,_,graph,row,ledger,clock=immediate_fixture(fixture)
    opened=[];monkeypatch.setattr(scheduler,'_spawn',lambda item,**_:opened.append(item['id']))
    scheduler.tick()
    assert not opened and graph.calls==[] and manager.store.reel_job(row['id'])['step']=='approval'
    clock[0]+=timedelta(minutes=5)
    ticket=ledger.pending()['items'][0];approve(ledger,ticket)
    scheduler.tick()
    assert opened==[row['id']] and manager.store.reel_job(row['id'])['scheduled_at']==row['scheduled_at']
    result=ledger.get(ticket['request_id'])
    assert datetime.fromisoformat(result['publish_window_end'])==clock[0]+timedelta(minutes=30)


def test_immediate_approved_publishes_once_through_existing_fake_graph_protocol(fixture):
    scheduler,manager,_,graph,row,ledger,_=immediate_fixture(fixture)
    approve(ledger,ledger.pending()['items'][0])
    immediate(scheduler._run(row['id']))
    assert manager.store.reel_job(row['id'])['status']=='completed'
    assert [c[0] for c in mutations(graph)]==['start','upload','finish']
    assert manager.store.reel_job(row['id'])['scheduled_at']==row['scheduled_at']


def test_immediate_expired_pending_holds_without_automatic_reapproval(fixture,monkeypatch):
    scheduler,manager,_,graph,row,ledger,clock=immediate_fixture(fixture)
    clock[0]=datetime.fromisoformat(row['approve_by'])
    monkeypatch.setattr(scheduler,'_spawn',lambda *_a,**_k:pytest.fail('expired job must never start'))
    scheduler.tick();current=manager.store.reel_job(row['id'])
    assert current['status']=='needs_user' and current['step']=='approval_expired'
    assert not scheduler.public(current)['can_resume'] and graph.calls==[]
    with pytest.raises(AppError):immediate(scheduler.resume(row['id']))


def test_immediate_cannot_bypass_missing_approval_service(fixture):
    scheduler,manager,_,graph,row,_,_=immediate_fixture(fixture)
    scheduler.approvals=None;manager.store.publication_approvals=None
    assert scheduler.public(row)['publication_approval']['can_publish'] is False
    immediate(scheduler._run(row['id']))
    assert graph.calls==[]


def test_returned_upload_version_checkpoint_is_validated_immutable_and_does_not_reset_flags(fixture):
    _,manager,_,_,row=fixture
    saved=manager.store.reel_checkpoint(row['id'],{'start_attempted':True,'upload_version':'v27.0'},video_id='999')
    assert saved['checkpoint']['upload_version']=='v27.0' and saved['checkpoint']['start_attempted'] is True
    assert manager.store.reel_checkpoint(row['id'],{'upload_version':'v27.0'})['checkpoint']['start_attempted'] is True
    for patch in ({'upload_version':'v28.0'},{'upload_version':None},{'upload_version':'v270.0'},
                  {'upload_version':'https://rupload.facebook.com/video-upload/v27.0/999'},{'start_attempted':False}):
        with pytest.raises(AppError):manager.store.reel_checkpoint(row['id'],patch)
    assert manager.store.reel_job(row['id'])['checkpoint']['upload_version']=='v27.0'


def test_approval_pending_at_enqueue_blocks_tick_and_graph_before_any_upload(fixture,monkeypatch):
    scheduler,manager,_,graph,row,ledger,clock=approval_fixture(fixture)
    assert ledger.pending()['total']==1
    assert scheduler.public(row)['publication_approval']['state']=='pending'
    opened=[];monkeypatch.setattr(scheduler,'_spawn',lambda row,**_:opened.append(row['id']))
    clock[0]=NOW+timedelta(minutes=1,seconds=2);scheduler.clock=lambda:clock[0]
    scheduler.tick()
    assert not opened and manager.store.reel_job(row['id'])['step']=='approval'
    immediate(scheduler._run(row['id']))
    assert graph.calls==[] and manager.store.reel_job(row['id'])['status']=='scheduled'


def test_preapproved_future_job_publishes_once_in_its_window(fixture):
    scheduler,manager,_,graph,row,ledger,clock=approval_fixture(fixture)
    approve(ledger,ledger.pending()['items'][0])
    clock[0]=NOW+timedelta(minutes=1,seconds=2);scheduler.clock=lambda:clock[0]
    immediate(scheduler._run(row['id']))
    assert manager.store.reel_job(row['id'])['status']=='completed'
    assert [call[0] for call in mutations(graph)]==['start','upload','finish']


def test_approval_is_rechecked_after_upload_before_finish(fixture):
    scheduler,manager,_,graph,row,ledger,clock=approval_fixture(fixture)
    approve(ledger,ledger.pending()['items'][0])
    clock[0]=NOW+timedelta(minutes=1,seconds=2);scheduler.clock=lambda:clock[0]
    graph.on_upload=lambda:ledger.withdraw('token',row['id'])
    immediate(scheduler._run(row['id']))
    assert [call[0] for call in mutations(graph)]==['start','upload']
    saved=manager.store.reel_job(row['id'])
    assert saved['status']=='needs_review' and not saved['checkpoint'].get('finish_attempted')


def test_unapproved_already_attempted_job_remains_verify_only(fixture):
    scheduler,manager,_,graph,row,ledger,_=approval_fixture(fixture)
    manager.store.reel_checkpoint(row['id'],{'finish_attempted':True},video_id='999',status='needs_review')
    immediate(scheduler._run(row['id'],verify_only=True))
    assert not mutations(graph) and manager.store.reel_job(row['id'])['status']=='completed'


def test_required_policy_missing_service_fails_closed(fixture):
    scheduler,manager,_,graph,row,_,_=approval_fixture(fixture)
    scheduler.approvals=None;del manager.store.publication_approvals
    immediate(scheduler._run(row['id']))
    assert graph.calls==[] and scheduler.public(row)['publication_approval']['state']=='unavailable'


def test_future_backfill_does_not_grant_old_or_cancelled_jobs(fixture):
    from page_manager.publication_approvals import PublicationApprovals
    scheduler,manager,assets,_,old=fixture
    future=create(manager,assets.row,old['connection_id'],due=NOW+timedelta(hours=1))
    cancelled=create(manager,assets.row,old['connection_id'],due=NOW+timedelta(hours=1))
    manager.store.reel_update(cancelled['id'],status='cancelled')
    ledger=PublicationApprovals(manager.store,clock=lambda:NOW);ledger.configure(True,1);scheduler.approvals=ledger
    scheduler._backfill_approvals()
    assert [row['binding']['job_id'] for row in ledger.pending()['items']]==[future['id']]
    assert manager.store.reel_job(cancelled['id'])['status']=='cancelled'


def test_due_run_uses_saved_page_source_asset_caption_and_durable_flags(fixture):
    scheduler,manager,assets,graph,row=fixture
    immediate(scheduler._run(row['id']))
    saved=manager.store.reel_job(row['id'])
    assert saved['status']=='completed' and saved['published_url']=='https://www.facebook.com/reel/999'
    assert [call[0] for call in mutations(graph)]==['start','upload','finish']
    assert mutations(graph)[0]==('start',TOKEN,'555') and mutations(graph)[-1]==('finish',TOKEN,'555','999','Exact fixture caption')
    assert saved['checkpoint']['asset_sha256']==assets.row['sha256'] and saved['checkpoint']['page_id']=='555'
    assert all(saved['checkpoint'][key] for key in ('start_attempted','start_ack','upload_attempted','upload_ack','finish_attempted','finish_ack'))
    public=scheduler.public(saved)
    assert public['request_id']==row['request_id'] and not public['can_resume'] and not public['can_cancel']
    assert TOKEN not in json.dumps(public) and str(assets.row['path']) not in json.dumps(public) and 'sha256' not in public


def test_processing_not_started_after_upload_does_not_deadlock_first_finish(fixture):
    scheduler,manager,_,graph,row=fixture
    graph.statuses=[{'video_id':'999','owner_matches':False,'owner_conflict':False,'upload_complete':True,'processing_complete':False,'published':False,'failed':False}]
    immediate(scheduler._run(row['id']))
    assert manager.store.reel_job(row['id'])['status']=='completed' and sum(call[0]=='finish' for call in graph.calls)==1


@pytest.mark.parametrize('stage',['start','upload','finish'])
def test_uncertain_post_never_repeats_any_mutation_after_restart_or_verification(fixture,stage):
    scheduler,manager,assets,graph,row=fixture;graph.error=stage
    immediate(scheduler._run(row['id']));first=deepcopy(mutations(graph));saved=manager.store.reel_job(row['id'])
    assert saved['status']=='needs_review' and saved['checkpoint']['uncertain'] is True and not scheduler.public(saved)['can_resume']
    restarted=TokenReelScheduler(manager,assets,graph_factory=lambda:graph,clock=lambda:NOW);restarted.POLL_ATTEMPTS=1
    immediate(restarted._run(row['id'],verify_only=True))
    assert mutations(graph)==first
    if stage=='start':assert manager.store.reel_job(row['id'])['video_id'] is None


def test_known_upload_ack_timeout_can_resume_first_unattempted_finish_without_reupload(fixture):
    scheduler,manager,_,graph,row=fixture
    graph.statuses=[{'video_id':'999','upload_complete':False},{'video_id':'999','upload_complete':False}]
    immediate(scheduler._run(row['id']))
    paused=manager.store.reel_job(row['id']);assert paused['status']=='needs_review' and scheduler.public(paused)['can_resume']
    immediate(scheduler.resume(row['id']));immediate(scheduler._run(row['id']))
    assert [call[0] for call in mutations(graph)]==['start','upload','finish']
    assert manager.store.reel_job(row['id'])['status']=='completed'


def test_finish_ack_is_not_completion_without_publishing_proof(fixture):
    scheduler,manager,_,graph,row=fixture
    graph.statuses=[{'video_id':'999','owner_matches':True,'upload_complete':True}]+[{'video_id':'999','owner_matches':True,'published':False}]*2
    immediate(scheduler._run(row['id']))
    saved=manager.store.reel_job(row['id'])
    assert saved['status']=='needs_review' and saved['checkpoint']['finish_ack'] is True
    assert not scheduler.public(saved)['can_resume'] and scheduler.public(saved)['can_verify']


@pytest.mark.parametrize('bad',[{'video_id':'888','upload_complete':True},{'video_id':'999','owner_conflict':True,'upload_complete':True},{'video_id':'999','failed':True,'upload_complete':True}])
def test_wrong_video_foreign_owner_or_phase_error_blocks_finish(fixture,bad):
    scheduler,manager,_,graph,row=fixture;graph.statuses=[bad]
    immediate(scheduler._run(row['id']))
    assert not any(call[0]=='finish' for call in graph.calls) and manager.store.reel_job(row['id'])['status']=='needs_review'


def test_revision_change_during_start_stops_before_upload_and_never_chooses_another_token(fixture):
    scheduler,manager,_,graph,row=fixture
    graph.on_start=lambda:manager.save_connection(ConnectionUpdate(label='Rotated',token_type='page',token='FixtureReplacementToken82', revision=manager.store.connection(row['connection_id'])["revision"]),row['connection_id'])
    immediate(scheduler._run(row['id']))
    assert [call[0] for call in mutations(graph)]==['start']
    assert manager.store.reel_job(row['id'])['status']=='needs_review'
    with pytest.raises(AppError):immediate(scheduler.resume(row['id']))
    assert manager.store.reel_job(row['id'])['token_revision']==row['token_revision']


def test_file_changes_after_acknowledged_start_are_not_uploaded_or_published(fixture):
    scheduler,manager,assets,graph,row=fixture
    graph.on_start=lambda:assets.row['path'].write_bytes(b'x'*assets.row['size'])
    immediate(scheduler._run(row['id']))
    assert [call[0] for call in mutations(graph)]==['start']
    assert manager.store.reel_job(row['id'])['status']=='needs_review'


def test_rate30_limit_prevents_finish_and_keeps_only_unattempted_finish_resumable(fixture,monkeypatch):
    scheduler,manager,_,graph,row=fixture
    monkeypatch.setattr(manager.store,'reel_publish_count',lambda pid,since:30)
    immediate(scheduler._run(row['id']))
    saved=manager.store.reel_job(row['id'])
    assert saved['status']=='needs_user' and saved['step']=='rate_limit' and not saved['checkpoint'].get('finish_attempted')
    assert scheduler.public(saved)['can_resume'] and not any(call[0]=='finish' for call in graph.calls)


def test_cancel_before_start_is_local_only_and_cannot_be_resumed(fixture):
    scheduler,manager,_,graph,row=fixture
    result=immediate(scheduler.cancel(row['id']))
    assert result['status']=='cancelled' and not graph.calls
    with pytest.raises(AppError):immediate(scheduler.resume(row['id']))
    assert manager.store.reel_job(row['id'])['checkpoint']['cancel_requested'] is True


def test_cancel_after_finish_keeps_read_only_verification_available(fixture):
    scheduler,manager,_,graph,row=fixture
    manager.store.reel_checkpoint(row['id'],{'start_attempted':True,'start_ack':True,'upload_attempted':True,'upload_ack':True,'finish_attempted':True},video_id='999',status='needs_review')
    result=immediate(scheduler.cancel(row['id']))
    assert result['status']=='needs_review' and result['can_verify']
    immediate(scheduler._run(row['id'],verify_only=True))
    assert manager.store.reel_job(row['id'])['status']=='completed' and not mutations(graph)


def test_verification_does_not_require_the_original_asset_to_still_exist(fixture):
    scheduler,manager,assets,graph,row=fixture
    manager.store.reel_checkpoint(row['id'],{'finish_attempted':True,'uncertain':True},video_id='999',status='needs_review')
    assets.row['path'].unlink()
    immediate(scheduler._run(row['id'],verify_only=True))
    assert manager.store.reel_job(row['id'])['status']=='completed' and assets.calls==0 and not mutations(graph)


def test_restart_does_not_automatically_dispatch_any_job_with_a_post_latch(fixture):
    scheduler,manager,assets,graph,row=fixture
    manager.store.reel_checkpoint(row['id'],{'start_attempted':True},status='starting')
    restarted=TokenReelScheduler(manager,assets,graph_factory=lambda:graph,clock=lambda:NOW)
    restarted.tick()
    assert manager.store.reel_job(row['id'])['status']=='needs_review' and not restarted.jobs and not graph.calls


def fake_tasks(monkeypatch):
    class Task:
        def __init__(self):self.callback=None
        def add_done_callback(self,callback):self.callback=callback
        def cancelled(self):return False
        def exception(self):return None
    def spawn(coroutine):coroutine.close();return Task()
    monkeypatch.setattr(module.asyncio,'create_task',spawn)


def test_tick_catches_up_overdue_with_max2_and_one_page_source_owner(fixture,monkeypatch):
    scheduler,manager,assets,_,row=fixture;fake_tasks(monkeypatch)
    create(manager,assets.row,row['connection_id'])
    other=connect(manager,'666','FixtureOtherSource82');second=create(manager,assets.row,other,'666')
    thirdcid=connect(manager,'777','FixtureThirdSource82');third=create(manager,assets.row,thirdcid,'777')
    scheduler.tick()
    assert set(scheduler.jobs)=={row['id'],second['id']} and len(scheduler.page_jobs)==2
    assert manager.store.reel_job(third['id'])['status']=='scheduled'
    assert scheduler.status()['scheduler_running'] and scheduler.status()['scheduler_healthy']
    assert scheduler.status()['active_count']==2


def test_future_job_and_busy_page_do_not_launch(fixture,monkeypatch):
    scheduler,manager,assets,graph,row=fixture;fake_tasks(monkeypatch)
    manager.store.reel_update(row['id'],status='cancelled')
    create(manager,assets.row,row['connection_id'],due=NOW+timedelta(hours=1))
    scheduler.tick();assert not scheduler.jobs
    due=create(manager,assets.row,row['connection_id'])
    lock=manager._page_lock('555');assert immediate(lock.acquire()) is True
    try:scheduler.tick()
    finally:lock.release()
    assert not scheduler.jobs and manager.store.reel_job(due['id'])['step']=='waiting_page' and not graph.calls


def test_unexpected_tick_failure_is_fixed_safe_and_does_not_kill_host_watcher(fixture,monkeypatch):
    scheduler,manager,_,_,_=fixture
    def broken(_):raise RuntimeError(TOKEN)
    monkeypatch.setattr(manager.store,'reel_due',broken)
    scheduler.tick()
    assert scheduler.status()['error'] and TOKEN not in str(scheduler.status())


def test_running_status_expires_when_host_stops_ticking_and_recovers_on_next_tick(fixture,monkeypatch):
    scheduler,manager,_,_,_=fixture
    clock=[0.0];scheduler.monotonic=lambda:clock[0]
    monkeypatch.setattr(manager.store,'reel_due',lambda _:[])
    scheduler.tick()
    assert scheduler.status()['scheduler_running'] and scheduler.status()['scheduler_healthy']
    clock[0]=11
    assert not scheduler.status()['scheduler_running'] and not scheduler.status()['scheduler_healthy']
    scheduler.tick()
    assert scheduler.status()['scheduler_running'] and scheduler.status()['scheduler_healthy']


def test_shutdown_before_any_post_preserves_due_job_for_restart(fixture):
    scheduler,manager,_,graph,row=fixture
    scheduler.closing=True
    immediate(scheduler._run(row['id']))
    assert manager.store.reel_job(row['id'])['status']=='scheduled' and not graph.calls


def test_existing_due_job_without_information_is_paused_before_any_graph_call(fixture,monkeypatch):
    scheduler,manager,_,graph,row=fixture;fake_tasks(monkeypatch)
    with manager.store.connect() as db:db.execute('DELETE FROM page_edits WHERE page_id=?',('555',))
    scheduler.tick()
    saved=manager.store.reel_job(row['id'])
    assert saved['status']=='needs_user' and saved['step']=='page_information'
    assert not scheduler.jobs and not graph.calls and not scheduler.public(saved)['can_resume']
    with pytest.raises(AppError) as error:immediate(scheduler.resume(row['id']))
    assert error.value.code=='page_information_required' and error.value.status_code==422
    assert immediate(scheduler.cancel(row['id']))['status']=='cancelled'


@pytest.mark.parametrize('stage,expected',[('start',['start']),('upload',['start','upload'])])
def test_information_gate_is_rechecked_before_later_meta_mutations(fixture,stage,expected):
    scheduler,manager,_,graph,row=fixture
    def unready():
        with manager.store.connect() as db:db.execute('DELETE FROM page_edits WHERE page_id=?',('555',))
    setattr(graph,'on_'+stage,unready)
    immediate(scheduler._run(row['id']))
    assert [call[0] for call in mutations(graph)]==expected
    saved=manager.store.reel_job(row['id'])
    assert saved['status']=='needs_review' and saved['step']=='page_information'


def test_unready_page_can_still_verify_after_finish_without_mutating(fixture):
    scheduler,manager,_,graph,row=fixture
    manager.store.reel_checkpoint(row['id'],{'finish_attempted':True,'uncertain':True},video_id='999',status='needs_review')
    with manager.store.connect() as db:db.execute('DELETE FROM page_edits WHERE page_id=?',('555',))
    assert scheduler.public(manager.store.reel_job(row['id']))['can_verify']
    immediate(scheduler._run(row['id'],verify_only=True))
    assert manager.store.reel_job(row['id'])['status']=='completed'
    assert not mutations(graph) and any(call[0]=='status' for call in graph.calls)
