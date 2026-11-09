"""Durable scheduling and publication boundaries, without Chrome/network."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.core.config import Config
from app.core.reel_inputs import StartReel
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import Profile,BmProfile,PageRecipientJob,ReelJob,ReelAsset,FacebookPage
from app.services.browsers import BrowserManager
from app.services.state import State
from app.services.reel_scheduler import ReelScheduler
from app.services import reel_scheduler as module


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O')


@pytest.fixture
def scheduler(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db);state.save_settings(Settings(require_proxy=False,max_browsers=2))
    browsers=BrowserManager(db,state,config,SimpleNamespace(blocked_reason=lambda _:None))
    policy=SimpleNamespace(posting_eligibility=lambda url:{'page_id':'555','can_publish':True,'information_status':'updated','reason':None,'revision':1})
    manager=ReelScheduler(db,state,browsers,SimpleNamespace(profile_jobs={}),config,posting_policy=policy)
    with db.session.begin() as session:
        for pid in (3,4):session.add(Profile(id=pid,name=f'Fixture Chrome {pid}',profile_path=str(tmp_path/'profiles'/str(pid))))
    asset_id='a'*32;path=manager.assets.root/(asset_id+'.mp4');path.write_bytes(b'fixture video')
    manager.add_asset({'id':asset_id,'name':'chosen.mp4','path':str(path),'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mime':'video/mp4'})
    yield manager
    db.engine.dispose()


def create(scheduler,pid=3,*,approval_policy=None):
    return scheduler.create(StartReel(profile_id=pid,page_url='https://www.facebook.com/profile.php?id=555',page_name='Page Fixture',asset_id='a'*32,caption='Caption đã chọn\n#test',scheduled_at=datetime.now(timezone.utc)+timedelta(hours=2),timezone='UTC',approval_policy=approval_policy))


def approvals(scheduler,tmp_path):
    import sys
    backend=Path(__file__).resolve().parents[2]/'meta-page-manager'/'backend'
    if str(backend) not in sys.path:sys.path.insert(0,str(backend))
    from page_manager.publication_approvals import PublicationApprovals
    from page_manager.store import Store
    clock=[datetime.now(timezone.utc)]
    ledger=PublicationApprovals(Store(tmp_path/'approval-owner'),clock=lambda:clock[0]);ledger.configure(True,1)
    scheduler.posting_policy.publication_approval=lambda binding,create=False:(ledger.ensure if create else ledger.verdict)(binding)
    scheduler.posting_policy.withdraw_publication=lambda jid:ledger.withdraw('chrome',jid)
    return ledger,clock


def approve_ticket(ledger,ticket):
    return ledger.decide(ticket['request_id'],ticket['binding_hash'],'approve',actor_id=123,chat_id=456,update_id=1,message_id=2)


def test_pending_publication_approval_is_created_on_enqueue_and_blocks_browser(scheduler,tmp_path,monkeypatch):
    ledger,clock=approvals(scheduler,tmp_path);job=create(scheduler)
    assert ledger.pending()['total']==1 and job['publication_approval']['state']=='pending'
    clock[0]=datetime.fromisoformat(job['scheduled_at'])+timedelta(seconds=2)
    monkeypatch.setattr(module,'now',lambda:clock[0].isoformat())
    opened=[];scheduler._spawn=lambda row:opened.append(row.id)
    scheduler.tick()
    assert not opened and scheduler.row(job['id']).status=='scheduled' and scheduler.row(job['id']).step=='approval'
    class NeverPublisher:
        def __init__(self,*_,**__):raise AssertionError('Pending approval cannot open producer')
    assert run_fixture(scheduler,job,monkeypatch,NeverPublisher)==[]
    assert not scheduler.row(job['id']).publish_attempted


def test_new_chrome_policy_stays_pending_days_late_and_late_approval_unlocks_exact_binding(scheduler,tmp_path,monkeypatch):
    ledger,clock=approvals(scheduler,tmp_path)
    job=create(scheduler,approval_policy='remind_until_decision_v1')
    ticket=ledger.pending()['items'][0];digest=ticket['binding_hash']
    assert job['approval_policy']=='remind_until_decision_v1'
    assert job['publication_approval']['approve_by'] is None
    clock[0]=datetime.fromisoformat(job['scheduled_at'])+timedelta(days=15)
    monkeypatch.setattr(module,'now',lambda:clock[0].isoformat())
    opened=[];scheduler._spawn=lambda row:opened.append(row.id)
    scheduler.tick()
    pending=scheduler.resolve_publication_status(job['id'])
    assert not opened and pending['status']=='scheduled' and pending['ready'] is False
    assert pending['binding']==ticket['binding'] and ledger.pending()['total']==1
    accepted=approve_ticket(ledger,ticket)
    assert datetime.fromisoformat(accepted['publish_window_end'])==clock[0]+timedelta(minutes=30)
    assert ledger.get(ticket['request_id'])['binding_hash']==digest
    assert scheduler.resolve_publication_status(job['id'])['ready'] is True
    scheduler.tick()
    assert opened==[job['id']]


def test_new_chrome_policy_fails_closed_without_approval_service(scheduler):
    job=create(scheduler,approval_policy='remind_until_decision_v1')
    assert job['publication_approval']['required'] and not job['publication_approval']['can_publish']
    assert job['publication_approval']['state']=='unavailable'


def test_notification_status_resolver_reads_approval_ready_and_published_proof_without_browser(scheduler,tmp_path,monkeypatch):
    ledger,_=approvals(scheduler,tmp_path);job=create(scheduler)
    monkeypatch.setattr(module,'ReelPublisher',lambda *args,**kwargs:pytest.fail('Status reads cannot launch publication'))
    pending=scheduler.resolve_publication_status(job['id'])
    assert pending['binding']['queue']=='chrome' and pending['binding']['page_id']=='555'
    assert pending['binding']['asset_id']==job['asset_id'] and pending['ready'] is False
    approve_ticket(ledger,ledger.pending()['items'][0])
    ready=scheduler.resolve_publication_status(job['id'])
    assert ready['ready'] is True and ready['status']=='scheduled'
    Path(scheduler.asset(job['asset_id']).path).write_bytes(b'tampered fixture video')
    assert scheduler.resolve_publication_status(job['id'])['ready'] is False
    with scheduler.db.session.begin() as session:
        row=session.get(ReelJob,job['id']);row.status='completed';row.published_url='https://www.facebook.com/reel/987654321'
    completed=scheduler.resolve_publication_status(job['id'])
    assert completed['binding']==ready['binding'] and completed['status']=='completed'
    assert completed['ready'] is False and completed['published_url']=='https://www.facebook.com/reel/987654321'


def test_notification_status_reports_expired_publication_window_without_modifying_job(scheduler,tmp_path):
    ledger,clock=approvals(scheduler,tmp_path);job=create(scheduler);approve_ticket(ledger,ledger.pending()['items'][0])
    clock[0]=datetime.fromisoformat(job['scheduled_at'])+timedelta(minutes=31)
    result=scheduler.resolve_publication_status(job['id'])
    assert result['status']=='needs_user' and result['step']=='approval_expired' and result['ready'] is False
    assert scheduler.row(job['id']).status=='scheduled' and not scheduler.row(job['id']).publish_attempted


def test_preapproved_chrome_job_runs_at_due_without_reapproval(scheduler,tmp_path,monkeypatch):
    ledger,clock=approvals(scheduler,tmp_path);job=create(scheduler);approve_ticket(ledger,ledger.pending()['items'][0])
    clock[0]=datetime.fromisoformat(job['scheduled_at'])+timedelta(seconds=2)
    monkeypatch.setattr(module,'now',lambda:clock[0].isoformat())
    class Publisher:
        def __init__(self,*args,**kwargs):self.checkpoint=args[6]
        async def run(self):
            await self.checkpoint({'publish_attempted':True,'page_url':job['page_url']})
            return {'url':'https://www.facebook.com/reel/999','evidence':'published_reel_permalink'}
    assert run_fixture(scheduler,job,monkeypatch,Publisher)
    assert scheduler.row(job['id']).status=='completed'


def test_revoked_approval_final_checkpoint_prevents_publish(scheduler,tmp_path,monkeypatch):
    ledger,_=approvals(scheduler,tmp_path);job=create(scheduler);approve_ticket(ledger,ledger.pending()['items'][0])
    class Publisher:
        def __init__(self,*args,**kwargs):self.checkpoint=args[6]
        async def run(self):
            ledger.withdraw('chrome',job['id'])
            await self.checkpoint({'publish_attempted':True,'page_url':job['page_url']})
            raise AssertionError('Revoked request must stop before publish checkpoint')
    assert run_fixture(scheduler,job,monkeypatch,Publisher)
    assert not scheduler.row(job['id']).publish_attempted and scheduler.row(job['id']).status=='needs_user'


def test_unapproved_attempted_chrome_job_remains_verification_only(scheduler,tmp_path,monkeypatch):
    approvals(scheduler,tmp_path);job=create(scheduler)
    scheduler.update(job['id'],publish_attempted=True,status='needs_review',checkpoint=json.dumps({'publish_attempted':True}))
    class Verification:
        def __init__(self,*args,**kwargs):assert kwargs['prior_checkpoint']['publish_attempted']
        async def run(self):return {'url':'https://www.facebook.com/reel/999','evidence':'published_reel_permalink'}
    assert run_fixture(scheduler,job,monkeypatch,Verification)
    assert scheduler.row(job['id']).status=='completed'


def test_chrome_cancel_withdraws_ticket_without_renewing_it(scheduler,tmp_path):
    ledger,_=approvals(scheduler,tmp_path);job=create(scheduler);ticket=ledger.pending()['items'][0]
    immediate(scheduler.cancel(job['id']));scheduler._backfill_approvals()
    assert ledger.get(ticket['request_id'])['state']=='cancelled' and ledger.pending()['total']==0


def past(scheduler,job):scheduler.update(job['id'],scheduled_at=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat())


def test_schedule_is_durable_and_keeps_chosen_profile_page_video_and_caption(scheduler):
    job=create(scheduler)
    assert job['status']=='scheduled' and job['profile_id']==3 and job['page_url'].endswith('id=555')
    assert job['asset_name']=='chosen.mp4' and job['caption']=='Caption đã chọn\n#test'
    restarted=ReelScheduler(scheduler.db,scheduler.state,scheduler.browsers,scheduler.tasks,scheduler.config)
    assert restarted.list()[0]['id']==job['id'] and restarted.list()[0]['status']=='scheduled'


def test_only_due_job_automatically_opens_corresponding_profile(scheduler):
    due=create(scheduler,3);create(scheduler,4);past(scheduler,due);opened=[]
    scheduler._spawn=lambda row:opened.append((row.id,row.profile_id))
    scheduler.tick()
    assert opened==[(due['id'],3)]


def test_busy_chrome_waits_then_runs_without_forcing_manual_browser_closed(scheduler):
    job=create(scheduler);past(scheduler,job);scheduler.browsers.contexts[3]=object();opened=[]
    scheduler._spawn=lambda row:opened.append(row.profile_id)
    scheduler.tick();assert scheduler.row(job['id']).status=='waiting_profile' and not opened
    scheduler.browsers.contexts.clear();scheduler.tick();assert opened==[3]


def test_cancelled_schedule_never_dispatches(scheduler):
    job=create(scheduler);past(scheduler,job)
    immediate(scheduler.cancel(job['id']));opened=[];scheduler._spawn=lambda row:opened.append(row.id)
    scheduler.tick();assert opened==[] and scheduler.row(job['id']).status=='cancelled'


def test_recover_before_publish_requeues_but_unknown_publish_requires_review(scheduler):
    first=create(scheduler);second=create(scheduler,4)
    scheduler.update(first['id'],status='uploading')
    scheduler.update(second['id'],status='publishing',publish_attempted=True,checkpoint=json.dumps({'publish_attempted':True}))
    recovered=ReelScheduler(scheduler.db,scheduler.state,scheduler.browsers,scheduler.tasks,scheduler.config)
    assert recovered.row(first['id']).status=='scheduled'
    assert recovered.row(second['id']).status=='needs_review'
    assert recovered.list()[0]['publish_attempted'] is True


def test_received_page_options_require_verified_acceptance(scheduler):
    with scheduler.db.session.begin() as session:
        session.add(PageRecipientJob(profile_id=3,page_name='Candidate only',configuration='{}',checkpoint=json.dumps({'page_url':'https://www.facebook.com/profile.php?id=555'})))
    assert scheduler.page_options(3)==[]
    with scheduler.db.session.begin() as session:
        row=session.query(PageRecipientJob).filter_by(profile_id=3).one()
        row.checkpoint=json.dumps({'page_url':'https://www.facebook.com/profile.php?id=555','accepted':True,'switched':True})
    option=scheduler.page_options(3)[0]
    assert option['page_url']=='https://www.facebook.com/profile.php?id=555' and option['page_name']=='Candidate only' and option['source']=='received'
    assert option['can_publish'] is True and option['page_id']=='555'


def test_eligible_only_page_options_resolve_full_profile_before_filter(scheduler):
    ready = {'700150', '700199', '700200'}
    with scheduler.db.session.begin() as session:
        session.add_all([FacebookPage(profile_id=3,page_key='id:'+str(700000+i),name=f'Fixture Page {i:04}',
            url='https://www.facebook.com/profile.php?id='+str(700000+i)) for i in range(201)])
    checked = []
    def eligibility(url):
        pid = url.rsplit('=',1)[-1]; checked.append(pid)
        return {'page_id':pid,'can_publish':pid in ready,'information_status':'updated' if pid in ready else 'not_updated',
            'reason':None if pid in ready else 'page_information_required','revision':1 if pid in ready else 0}
    scheduler.posting_policy.posting_eligibility = eligibility
    assert len(scheduler.page_options(3)) == 201
    checked.clear()
    options = scheduler.page_options(3,posting_eligible_only=True)
    assert len(checked) == 201 and {row['page_id'] for row in options} == ready
    assert all(row['can_publish'] is True for row in options)
    scheduler.posting_policy = None
    assert scheduler.page_options(3,posting_eligible_only=True) == []
    assert len(scheduler.page_options(3)) == 201


def test_bm_profile_cannot_be_scheduled(scheduler):
    with scheduler.db.session.begin() as session:session.add(BmProfile(profile_id=3))
    with pytest.raises(ValueError,match='BM'):create(scheduler)


def test_profile_changed_to_bm_after_scheduling_is_not_posted(scheduler):
    job=create(scheduler);past(scheduler,job)
    with scheduler.db.session.begin() as session:session.add(BmProfile(profile_id=3))
    opened=[];scheduler._spawn=lambda row:opened.append(row.id)
    scheduler.tick();assert not opened and scheduler.row(job['id']).status=='needs_user'


def test_cancel_before_task_first_step_stays_cancelled_after_restart(scheduler,monkeypatch):
    job=create(scheduler);scheduler.update(job['id'],status='opening')
    class NeverStarted:
        def cancel(self):pass
    task=NeverStarted();scheduler.jobs[job['id']]=task
    async def gathered(*_,**__):return []
    monkeypatch.setattr(module.asyncio,'gather',gathered)
    immediate(scheduler.cancel(job['id']))
    restarted=ReelScheduler(scheduler.db,scheduler.state,scheduler.browsers,scheduler.tasks,scheduler.config)
    assert restarted.row(job['id']).status=='cancelled'


def test_reel_lock_rejects_other_browser_work(scheduler):
    scheduler.browsers.reel_profiles.add(3)
    with pytest.raises(ValueError,match='Reel'):scheduler.browsers.request_start(3)
    with pytest.raises(ValueError,match='Reel'):scheduler.browsers.validate_automation_start(3)
    scheduler.browsers.validate_automation_start(3,for_reel=True)


class NoWait:
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass


def run_fixture(scheduler,job,monkeypatch,publisher):
    opened=[]
    class Page:
        def is_closed(self):return False
        async def goto(self,*_,**__):pass
    class Context:
        async def new_page(self):return Page()
    async def ensure(pid,**options):opened.append((pid,options));return Context()
    async def stop(_):pass
    async def thread(function,*args):return function(*args)
    scheduler.browsers.ensure_open=ensure;scheduler.browsers.stop=stop
    scheduler.browsers.reel_profiles.add(job['profile_id']);scheduler.profile_jobs[job['profile_id']]=job['id']
    monkeypatch.setattr(module,'ReelPublisher',publisher);monkeypatch.setattr(module.asyncio,'timeout',lambda _:NoWait());monkeypatch.setattr(module.asyncio,'to_thread',thread)
    immediate(scheduler._run(job['id']))
    return opened


def test_due_execution_uses_exact_saved_profile_and_durable_publish_checkpoint(scheduler,monkeypatch):
    job=create(scheduler)
    class Publisher:
        def __init__(self,page,url,name,path,caption,notify,checkpoint,**_):
            assert url==job['page_url'] and name=='Page Fixture' and caption==job['caption']
            assert Path(path).name=='a'*32+'.mp4'
            self.checkpoint=checkpoint
        async def run(self):
            await self.checkpoint({'publish_attempted':True,'stage':'publishing','page_url':job['page_url']})
            assert scheduler.row(job['id']).publish_attempted
            return {'url':'https://www.facebook.com/reel/999','evidence':'published_reel_permalink'}
    opened=run_fixture(scheduler,job,monkeypatch,Publisher)
    assert opened==[(3,{'for_automation':True,'for_reel':True})]
    assert scheduler.row(job['id']).status=='completed' and scheduler.row(job['id']).published_url.endswith('/999')
    assert 3 not in scheduler.browsers.reel_profiles


def test_exception_after_publish_never_becomes_automatic_retry(scheduler,monkeypatch):
    job=create(scheduler)
    class Publisher:
        def __init__(self,*args,**kwargs):self.checkpoint=args[6]
        async def run(self):
            await self.checkpoint({'publish_attempted':True,'page_url':job['page_url']})
            raise RuntimeError('fixture-private-raw-error')
    run_fixture(scheduler,job,monkeypatch,Publisher)
    row=scheduler.row(job['id']);assert row.status=='needs_review' and row.publish_attempted
    assert 'fixture-private-raw-error' not in row.message
    scheduler.tick();assert row.id not in scheduler.jobs


def test_modified_video_stops_before_opening_any_browser(scheduler,monkeypatch):
    job=create(scheduler);Path(scheduler.asset('a'*32).path).write_bytes(b'changed')
    class NeverPublisher:
        def __init__(self,*_,**__):raise AssertionError('Must not publish a changed video')
    opened=run_fixture(scheduler,job,monkeypatch,NeverPublisher)
    assert opened==[] and scheduler.row(job['id']).status=='needs_user'


def test_unknown_publication_reconciles_even_when_video_is_gone_and_checkpoint_sparse(scheduler,monkeypatch):
    job=create(scheduler);Path(scheduler.asset('a'*32).path).unlink()
    scheduler.update(job['id'],status='needs_review',publish_attempted=True,checkpoint='{}')
    class VerifyOnly:
        def __init__(self,*args,prior_checkpoint,**_):
            assert prior_checkpoint['publish_attempted'] is True
            assert prior_checkpoint['page_url']==job['page_url']
        async def run(self):return {'url':'https://www.facebook.com/reel/999','evidence':'published_reel_permalink'}
    opened=run_fixture(scheduler,job,monkeypatch,VerifyOnly)
    assert opened and scheduler.row(job['id']).status=='completed'


def test_deleted_profile_has_no_resume_capability(scheduler):
    job=create(scheduler);scheduler.update(job['id'],status='cancelled',profile_id=None)
    assert scheduler.list()[0]['can_resume'] is False


def test_cancelled_unknown_publication_does_not_keep_offering_cancel(scheduler):
    job=create(scheduler);scheduler.update(job['id'],status='needs_review',publish_attempted=True)
    immediate(scheduler.cancel(job['id']))
    assert scheduler.list()[0]['can_cancel'] is False and scheduler.list()[0]['can_resume'] is True


def test_owned_paused_resume_still_obeys_reel_concurrency_limit(scheduler):
    job=create(scheduler);past(scheduler,job);scheduler.update(job['id'],status='needs_user')
    scheduler.profile_jobs[3]=job['id'];scheduler.browsers.reel_profiles.add(3)
    scheduler.jobs={88:object(),89:object()};opened=[];scheduler._spawn=lambda row:opened.append(row.id)
    scheduler.resume(job['id']);scheduler.tick()
    assert not opened and scheduler.row(job['id']).status=='waiting_profile'
    scheduler.jobs.clear();scheduler.tick();assert opened==[job['id']]


def test_cancel_blocks_resume_duplicate_cancel_and_dispatch_until_owned_chrome_closes(scheduler):
    job=create(scheduler);past(scheduler,job);scheduler.update(job['id'],status='needs_user')
    scheduler.profile_jobs[3]=job['id'];scheduler.browsers.reel_profiles.add(3);scheduler.pages[job['id']]=object()
    opened=[];scheduler._spawn=lambda row:opened.append(row.id)
    class Closing:
        def __await__(self):yield 'chrome-closing'
    async def stop(pid):assert pid==3;await Closing()
    scheduler.browsers.stop=stop
    operation=scheduler.cancel(job['id'])
    try:
        assert operation.send(None)=='chrome-closing'
        assert scheduler.row(job['id']).status=='cancelled'
        assert job['id'] in scheduler.cancelling and 3 in scheduler.cancelling_profiles
        assert scheduler.profile_jobs[3]==job['id'] and 3 in scheduler.browsers.reel_profiles
        capabilities=scheduler.list()[0]
        assert capabilities['can_cancel'] is False and capabilities['can_resume'] is False
        with pytest.raises(ValueError,match='đang dừng'):scheduler.resume(job['id'])
        with pytest.raises(ValueError,match='đang dừng'):immediate(scheduler.cancel(job['id']))
        # Even a pending row cannot re-enter dispatch during the close await.
        scheduler.update(job['id'],status='scheduled');scheduler.tick();assert opened==[]
        scheduler.update(job['id'],status='cancelled')
        with pytest.raises(StopIteration) as result:operation.send(None)
        assert result.value.value=={'ok':True}
    finally:operation.close()
    assert not scheduler.cancelling and not scheduler.cancelling_profiles and not scheduler.profile_jobs
    assert 3 not in scheduler.browsers.reel_profiles and job['id'] not in scheduler.pages
    assert scheduler.list()[0]['can_resume'] is True


def test_cancel_stop_failure_releases_transition_guard_but_retains_owned_profile_for_retry(scheduler):
    job=create(scheduler);scheduler.update(job['id'],status='needs_user')
    scheduler.profile_jobs[3]=job['id'];scheduler.browsers.reel_profiles.add(3)
    page=object();scheduler.pages[job['id']]=page
    async def failed_stop(_):raise ValueError('Synthetic close timeout')
    scheduler.browsers.stop=failed_stop
    with pytest.raises(ValueError,match='close timeout'):immediate(scheduler.cancel(job['id']))
    assert not scheduler.cancelling and not scheduler.cancelling_profiles
    assert scheduler.profile_jobs[3]==job['id'] and 3 in scheduler.browsers.reel_profiles
    assert scheduler.pages[job['id']] is page and scheduler.list()[0]['can_cancel'] is True
    async def stopped(_):pass
    scheduler.browsers.stop=stopped
    assert immediate(scheduler.cancel(job['id']))=={'ok':True}
    assert not scheduler.profile_jobs and 3 not in scheduler.browsers.reel_profiles


def test_archive_and_restore_hide_only_cancelled_metadata_without_deleting_video_or_resuming(scheduler):
    job=create(scheduler);past(scheduler,job);immediate(scheduler.cancel(job['id']))
    original=scheduler.row(job['id']);checkpoint=original.checkpoint
    video=Path(scheduler.asset(job['asset_id']).path);content=video.read_bytes()
    async def never_stop(_):raise AssertionError('Metadata archive must not close Chrome')
    scheduler.browsers.stop=never_stop
    assert scheduler.list()[0]['can_archive'] is True
    result=scheduler.archive(job['id']);stamp=result['archived_at']
    assert stamp and scheduler.list()==[]
    archived=scheduler.list(include_archived=True)[0]
    assert archived['archived_at']==stamp and archived['can_restore'] is True
    assert archived['can_resume'] is False and archived['can_cancel'] is False and archived['can_archive'] is False
    assert scheduler.archive(job['id'])==result
    assert scheduler.row(job['id']).checkpoint==checkpoint and video.read_bytes()==content
    assert scheduler.asset(job['asset_id']).id==job['asset_id']
    assert scheduler.restore(job['id'])=={'ok':True,'archived_at':None}
    assert scheduler.restore(job['id'])=={'ok':True,'archived_at':None}
    restored=scheduler.list()[0]
    assert restored['status']=='cancelled' and restored['can_restore'] is False and restored['can_archive'] is True
    opened=[];scheduler._spawn=lambda row:opened.append(row.id);scheduler.tick();assert opened==[]


@pytest.mark.parametrize('status',['scheduled','waiting_profile','opening','uploading','needs_user','needs_review','completed','interrupted'])
def test_archive_rejects_every_state_except_cancelled(scheduler,status):
    job=create(scheduler);scheduler.update(job['id'],status=status)
    assert scheduler.list()[0]['can_archive'] is False
    with pytest.raises(ValueError):scheduler.archive(job['id'])
    assert scheduler.row(job['id']).archived_at is None


@pytest.mark.parametrize('attempted,checkpoint',[(True,'{}'),(False,'{"publish_attempted":true}'),(False,'invalid checkpoint')])
def test_uncertain_or_attempted_publication_cannot_be_archived_or_restored(scheduler,attempted,checkpoint):
    job=create(scheduler);scheduler.update(job['id'],status='cancelled',publish_attempted=attempted,checkpoint=checkpoint)
    assert scheduler.list()[0]['can_archive'] is False
    with pytest.raises(ValueError):scheduler.archive(job['id'])
    scheduler.update(job['id'],archived_at='2026-10-30T00:00:00+00:00')
    assert scheduler.list(include_archived=True)[0]['can_restore'] is False
    with pytest.raises(ValueError):scheduler.restore(job['id'])


@pytest.mark.parametrize('owner',['active_job','owned_job','cancelling_job','unknown_owner','unknown_cancelling_profile'])
def test_archive_rejects_its_own_active_or_unresolved_ownership(scheduler,owner):
    job=create(scheduler);scheduler.update(job['id'],status='cancelled')
    if owner=='active_job':scheduler.jobs[job['id']]=object()
    elif owner=='owned_job':scheduler.profile_jobs[3]=job['id'];scheduler.browsers.reel_profiles.add(3)
    elif owner=='cancelling_job':scheduler.cancelling.add(job['id'])
    elif owner=='unknown_owner':scheduler.browsers.reel_profiles.add(3)
    else:scheduler.cancelling_profiles.add(3)
    assert scheduler.list()[0]['can_archive'] is False
    with pytest.raises(ValueError):scheduler.archive(job['id'])


@pytest.mark.parametrize('other_cancelling',[False,True])
def test_older_cancelled_job_can_be_archived_and_restored_while_another_job_owns_same_profile(scheduler,other_cancelling):
    older=create(scheduler);newer=create(scheduler)
    scheduler.update(older['id'],status='cancelled');scheduler.update(newer['id'],status='needs_user')
    page=object();task=object()
    scheduler.profile_jobs[3]=newer['id'];scheduler.browsers.reel_profiles.add(3)
    scheduler.pages[newer['id']]=page;scheduler.jobs[newer['id']]=task
    if other_cancelling:scheduler.cancelling.add(newer['id']);scheduler.cancelling_profiles.add(3)
    async def never_stop(_):raise AssertionError('Do not close another job Chrome')
    scheduler.browsers.stop=never_stop
    assert next(row for row in scheduler.list() if row['id']==older['id'])['can_archive'] is True
    scheduler.archive(older['id'])
    assert [row['id'] for row in scheduler.list()]==[newer['id']]
    scheduler.restore(older['id'])
    assert scheduler.row(older['id']).status=='cancelled' and scheduler.row(newer['id']).status=='needs_user'
    assert scheduler.profile_jobs=={3:newer['id']} and scheduler.browsers.reel_profiles=={3}
    assert scheduler.pages=={newer['id']:page} and scheduler.jobs=={newer['id']:task}
    assert (newer['id'] in scheduler.cancelling)==other_cancelling
    assert (3 in scheduler.cancelling_profiles)==other_cancelling


def test_archived_jobs_cannot_cancel_resume_dispatch_or_be_recovered_into_pending(scheduler,monkeypatch):
    job=create(scheduler);past(scheduler,job);scheduler.update(job['id'],status='cancelled');scheduler.archive(job['id'])
    with pytest.raises(ValueError):immediate(scheduler.cancel(job['id']))
    with pytest.raises(ValueError):scheduler.resume(job['id'])
    scheduler.update(job['id'],status='scheduled')
    opened=[];scheduler._spawn=lambda row:opened.append(row.id);scheduler.tick();assert opened==[]
    async def no_open(*_,**__):raise AssertionError('Archived job cannot open Chrome')
    scheduler.browsers.ensure_open=no_open
    immediate(scheduler._run(job['id']))
    scheduler.update(job['id'],status='opening')
    restarted=ReelScheduler(scheduler.db,scheduler.state,scheduler.browsers,scheduler.tasks,scheduler.config)
    assert restarted.row(job['id']).status=='opening' and restarted.list()==[]
    restarted._spawn=lambda row:opened.append(row.id);restarted.tick();assert opened==[]


def test_cancelled_job_with_deleted_profile_can_be_archived_and_restored_as_metadata(scheduler):
    job=create(scheduler);scheduler.update(job['id'],status='cancelled',profile_id=None)
    scheduler.archive(job['id']);assert scheduler.list()==[]
    scheduler.restore(job['id']);assert scheduler.list()[0]['status']=='cancelled'
    assert scheduler.list()[0]['can_resume'] is False


def test_archive_and_restore_unknown_job_fail_without_changing_existing_rows(scheduler):
    job=create(scheduler)
    with pytest.raises(ValueError):scheduler.archive(99999)
    with pytest.raises(ValueError):scheduler.restore(99999)
    assert scheduler.row(job['id']).archived_at is None


VERIFY_URL='https://www.facebook.com/reel/123456'


def attempted_job(scheduler):
    job=create(scheduler);past(scheduler,job)
    scheduler.update(job['id'],status='needs_review',publish_attempted=True,checkpoint=json.dumps({
        'publish_attempted':True,'page_url':job['page_url'],'stage':'publishing','diagnostics':{'fixture':True}}))
    return job


def test_verify_link_persists_canonical_checkpoint_and_requeues_without_changing_chosen_inputs(scheduler):
    job=attempted_job(scheduler);before=scheduler.row(job['id']);page=object()
    scheduler.profile_jobs[3]=job['id'];scheduler.browsers.reel_profiles.add(3);scheduler.pages[job['id']]=page
    assert scheduler.list()[0]['can_verify_link'] is True and scheduler.list()[0]['verification_url'] is None
    assert scheduler.verify_link(job['id'],'https://m.facebook.com/reels/123456/?ref=fixture#tracking')=={'ok':True,'verification_url':VERIFY_URL}
    after=scheduler.row(job['id']);checkpoint=json.loads(after.checkpoint)
    assert checkpoint=={'publish_attempted':True,'page_url':job['page_url'],'stage':'publishing','diagnostics':{'fixture':True},'verification_url':VERIFY_URL}
    assert after.status=='scheduled' and after.publish_attempted is True and after.published_url is None
    for key in ('profile_id','profile_name','page_url','page_name','asset_id','caption','scheduled_at','timezone'):
        assert getattr(after,key)==getattr(before,key)
    assert scheduler.pages[job['id']] is page and scheduler.profile_jobs[3]==job['id']
    assert scheduler.list()[0]['can_verify_link'] is False and scheduler.list()[0]['verification_url']==VERIFY_URL
    restarted=ReelScheduler(scheduler.db,scheduler.state,scheduler.browsers,scheduler.tasks,scheduler.config)
    assert restarted.list()[0]['verification_url']==VERIFY_URL


@pytest.mark.parametrize('blocker',['concurrency','manual_browser','resources'])
def test_verify_link_queue_obeys_existing_concurrency_busy_profile_and_resource_wait(scheduler,blocker):
    job=attempted_job(scheduler);scheduler.verify_link(job['id'],VERIFY_URL);opened=[]
    scheduler._spawn=lambda row:opened.append(row.id)
    if blocker=='concurrency':scheduler.jobs={88:object(),89:object()}
    elif blocker=='manual_browser':scheduler.browsers.contexts[3]=object()
    else:scheduler.browsers.resources.blocked_reason=lambda _:'Fixture resource pressure'
    scheduler.tick();assert opened==[] and scheduler.row(job['id']).status=='waiting_profile'
    scheduler.jobs.clear();scheduler.browsers.contexts.clear();scheduler.browsers.resources.blocked_reason=lambda _:None
    scheduler.tick();assert opened==[job['id']]


@pytest.mark.parametrize('status',['scheduled','waiting_profile','opening','uploading','publishing','verifying','needs_user','cancelled','completed'])
def test_verify_link_rejects_nonreview_states_without_changing_checkpoint(scheduler,status):
    job=attempted_job(scheduler);scheduler.update(job['id'],status=status);before=scheduler.row(job['id']).checkpoint
    assert scheduler.list()[0]['can_verify_link'] is False
    with pytest.raises(ValueError):scheduler.verify_link(job['id'],VERIFY_URL)
    assert scheduler.row(job['id']).checkpoint==before and scheduler.row(job['id']).status==status


@pytest.mark.parametrize('blocker',['not_attempted','archived','deleted_profile','active','cancelling_job','cancelling_profile','other_owner','unknown_owner'])
def test_verify_link_rejects_unsafe_lifecycle_or_ownership_and_preserves_inputs(scheduler,blocker):
    job=attempted_job(scheduler)
    if blocker=='not_attempted':scheduler.update(job['id'],publish_attempted=False)
    elif blocker=='archived':scheduler.update(job['id'],archived_at='fixture-stamp')
    elif blocker=='deleted_profile':
        with scheduler.db.session.begin() as session:session.delete(session.get(Profile,3))
    elif blocker=='active':scheduler.jobs[job['id']]=object()
    elif blocker=='cancelling_job':scheduler.cancelling.add(job['id'])
    elif blocker=='cancelling_profile':scheduler.cancelling_profiles.add(3)
    elif blocker=='other_owner':scheduler.profile_jobs[3]=999;scheduler.browsers.reel_profiles.add(3)
    else:scheduler.browsers.reel_profiles.add(3)
    before=scheduler.row(job['id']);ownership=dict(scheduler.profile_jobs)
    assert scheduler.list(include_archived=True)[0]['can_verify_link'] is False
    with pytest.raises(ValueError):scheduler.verify_link(job['id'],VERIFY_URL)
    after=scheduler.row(job['id'])
    assert after.checkpoint==before.checkpoint and after.status==before.status and scheduler.profile_jobs==ownership


def test_interrupted_attempted_job_can_queue_verification_but_invalid_url_cannot_mutate_it(scheduler):
    job=attempted_job(scheduler);scheduler.update(job['id'],status='interrupted');before=scheduler.row(job['id']).checkpoint
    assert scheduler.list()[0]['can_verify_link'] is True
    with pytest.raises(ValueError):scheduler.verify_link(job['id'],'https://evil.test/reel/123456')
    assert scheduler.row(job['id']).checkpoint==before
    assert scheduler.verify_link(job['id'],VERIFY_URL)['ok'] is True


@pytest.mark.parametrize('retained',[False,True])
@pytest.mark.parametrize('proof',['valid','no_proof','wrong_author','wrong_caption','toast_only','different_reel','two_videos'])
def test_supplied_reel_link_navigates_only_owned_page_and_runs_reconciliation_without_upload_or_publish(scheduler,monkeypatch,retained,proof):
    job=attempted_job(scheduler);scheduler.verify_link(job['id'],VERIFY_URL)
    Path(scheduler.asset(job['asset_id']).path).unlink() # Verification does not need the source upload again.
    navigation=[];opened=[];created=[];stopped=[]
    class Page:
        url=job['page_url']
        def is_closed(self):return False
        async def goto(self,url,**options):navigation.append((self,url,options));self.url=url
        async def evaluate(self,_source,arguments):
            assert arguments.get('verificationOnly') is True
            return {
                'current_reel_url':VERIFY_URL if proof!='different_reel' else 'https://www.facebook.com/reel/999',
                'reel_video_visible':proof not in ('no_proof','toast_only'),
                'reel_video_count':2 if proof=='two_videos' else 0 if proof in ('no_proof','toast_only') else 1,
                'actor_matches':proof not in ('no_proof','wrong_author','toast_only'),
                'published_caption_matches':proof not in ('no_proof','wrong_caption','toast_only'),
                'published':proof=='toast_only','published_urls':[VERIFY_URL] if proof=='toast_only' else [],
            }
        async def wait_for_timeout(self,_):pass
        def locator(self,*_):raise AssertionError('Verification cannot click, fill or upload')
    page=Page()
    if retained:scheduler.pages[job['id']]=page
    class Context:
        async def new_page(self):created.append(page);return page
    async def ensure(pid,**options):opened.append((pid,options));return Context()
    async def stop(pid):stopped.append(pid)
    class VerificationOnly(module.ReelPublisher):
        async def _page_actor(self):raise AssertionError('No actor switching during verification')
        async def _open_reel_entry(self):raise AssertionError('No composer during verification')
        async def _upload(self,*_):raise AssertionError('No upload during verification')
        async def _click(self,*_):raise AssertionError('No publish during verification')
    scheduler.browsers.ensure_open=ensure;scheduler.browsers.stop=stop
    scheduler.profile_jobs[3]=job['id'];scheduler.browsers.reel_profiles.add(3)
    monkeypatch.setattr(module,'ReelPublisher',VerificationOnly);monkeypatch.setattr(module.asyncio,'timeout',lambda _:NoWait())
    immediate(scheduler._run(job['id']))
    assert opened==[(3,{'for_automation':True,'for_reel':True})]
    assert created==([] if retained else [page])
    assert navigation==[(page,VERIFY_URL,{'wait_until':'domcontentloaded','timeout':45000})]
    row=scheduler.row(job['id']);checkpoint=json.loads(row.checkpoint)
    assert row.publish_attempted is True and checkpoint['publish_attempted'] is True and checkpoint['verification_url']==VERIFY_URL
    assert row.page_url==job['page_url'] and row.caption==job['caption'] and row.asset_id==job['asset_id']
    successful=proof in ('valid','wrong_author','wrong_caption')
    assert (row.status=='completed')==successful
    if successful:
        assert row.published_url==VERIFY_URL and stopped==[3]
        assert row.message=='Đã thấy video hiển thị tại link Reel.'
        assert job['id'] not in scheduler.pages and 3 not in scheduler.profile_jobs
    else:
        assert row.status=='needs_review' and row.published_url is None and stopped==[]
        assert scheduler.pages[job['id']] is page and scheduler.profile_jobs[3]==job['id']


def test_scheduler_cannot_complete_from_light_video_evidence_without_saved_publish_attempt(scheduler,monkeypatch):
    job=create(scheduler)
    class NoPublishAttempt:
        def __init__(self,*_,**__):pass
        async def run(self):return {'url':VERIFY_URL,'evidence':'visible_reel_video'}
    run_fixture(scheduler,job,monkeypatch,NoPublishAttempt)
    row=scheduler.row(job['id'])
    assert row.status=='needs_user' and row.publish_attempted is False and row.published_url is None
