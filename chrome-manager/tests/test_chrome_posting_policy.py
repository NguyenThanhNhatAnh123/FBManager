"""Shared Page information gate: fake facade/temp DB only, no browser or Meta."""
import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from test_reel_scheduler import scheduler, create, past, run_fixture
from test_page_recipient import manager, start
from test_reel_publisher import factory, Page, initial, composer, editor, published, immediate
from app.core.reel_inputs import StartReel
from app.services.page_setup import PageSetupNeedsUser
from app.services.reel_publisher import ReelNeedsUser
from app.models.entities import ReelJob


def verdict(*,ready=False,page_id='555',reason=None):
    return {'page_id':page_id,'can_publish':ready,'information_status':'updated' if ready else 'not_updated',
            'reason':reason if reason is not None else None if ready else 'page_information_required','revision':2 if ready else 0}


def policy(scheduler,value):
    scheduler.posting_policy=SimpleNamespace(posting_eligibility=lambda _:value)


@pytest.mark.parametrize('value',[None, verdict(), verdict(page_id=None,reason='page_identity_unverified'),
    verdict(ready=True,page_id='999'), verdict(ready=True,page_id='0'),
    {**verdict(ready=True),'revision':0}, {**verdict(ready=True),'information_status':'not_updated'}])
def test_new_unedited_unknown_or_mismatched_page_cannot_create_direct_schedule(scheduler,value):
    policy(scheduler,value)
    with pytest.raises(ReelNeedsUser):create(scheduler)
    with scheduler.db.session() as session:assert session.query(ReelJob).count()==0


def test_absent_facade_is_fail_closed(scheduler):
    scheduler.posting_policy=None
    assert scheduler.posting_eligibility('https://www.facebook.com/profile.php?id=555')['can_publish'] is False
    with pytest.raises(ReelNeedsUser):create(scheduler)


def test_verified_alias_is_bound_to_numeric_page_in_saved_job(scheduler):
    body=StartReel(profile_id=3,page_url='https://www.facebook.com/verified-alias',asset_id='a'*32,
                   scheduled_at=datetime.now(timezone.utc)+timedelta(hours=1))
    result=scheduler.create(body);row=scheduler.row(result['id'])
    assert row.page_url=='https://www.facebook.com/profile.php?id=555'
    assert json.loads(row.checkpoint)['posting_policy']=={'page_id':'555','revision':1}
    assert result['can_publish'] is True and result['information_status']=='updated'
    assert result['posting_page_id']==result['page_id']=='555'


def test_changed_policy_blocks_saved_resume_and_due_queue_before_chrome(scheduler):
    job=create(scheduler);past(scheduler,job);policy(scheduler,verdict());opened=[]
    scheduler._spawn=lambda row:opened.append(row.id)
    scheduler.tick()
    row=scheduler.row(job['id'])
    assert row.status=='needs_user' and not opened
    assert scheduler.list()[0]['can_resume'] is False
    with pytest.raises(ReelNeedsUser):scheduler.resume(job['id'])


def test_runtime_rechecks_policy_and_does_not_open_browser(scheduler,monkeypatch):
    job=create(scheduler);policy(scheduler,verdict())
    class NeverPublisher:
        def __init__(self,*_,**__):raise AssertionError('No posting workflow for unedited Page')
    assert run_fixture(scheduler,job,monkeypatch,NeverPublisher)==[]
    assert scheduler.row(job['id']).status=='needs_user'


def test_checkpoint_rechecks_policy_before_any_publish_attempt_is_recorded(scheduler,monkeypatch):
    job=create(scheduler)
    class InterruptedBeforePublish:
        def __init__(self,*args,**kwargs):self.checkpoint=args[6]
        async def run(self):
            policy(scheduler,verdict())
            await self.checkpoint({'publish_attempted':True,'page_url':job['page_url']})
            raise AssertionError('Policy rejection must stop before publication')
    run_fixture(scheduler,job,monkeypatch,InterruptedBeforePublish)
    row=scheduler.row(job['id'])
    assert row.status=='needs_user' and row.publish_attempted is False


def test_same_page_becomes_schedulable_after_shared_verified_edit(scheduler):
    policy(scheduler,verdict())
    with pytest.raises(ReelNeedsUser):create(scheduler)
    policy(scheduler,verdict(ready=True))
    assert create(scheduler)['can_publish'] is True


def test_numeric_binding_cannot_follow_a_changed_identity(scheduler):
    job=create(scheduler);scheduler.update(job['id'],status='cancelled')
    policy(scheduler,verdict(ready=True,page_id='999'))
    with pytest.raises(ReelNeedsUser,match='ID Page'):scheduler.resume(job['id'])
    assert json.loads(scheduler.row(job['id']).checkpoint)['posting_policy']['page_id']=='555'


def test_already_attempted_publication_can_still_be_verified_without_posting_permission(scheduler,monkeypatch):
    job=create(scheduler);scheduler.update(job['id'],status='needs_review',publish_attempted=True,
        checkpoint=json.dumps({'publish_attempted':True,'page_url':job['page_url']}))
    policy(scheduler,verdict())
    assert scheduler.list()[0]['can_resume'] is True
    scheduler.resume(job['id'])
    class VerificationOnly:
        def __init__(self,*args,prior_checkpoint,**kwargs):assert prior_checkpoint['publish_attempted'] is True
        async def run(self):return {'url':'https://www.facebook.com/reel/999','evidence':'published_reel_permalink'}
    assert run_fixture(scheduler,job,monkeypatch,VerificationOnly)
    assert scheduler.row(job['id']).status=='completed'


def test_guard_revoked_immediately_before_publish_never_clicks(factory):
    page=Page(initial(),composer(),editor(),editor(),editor(),published());calls=[]
    def guard():
        calls.append(True)
        return len(calls)<4
    publisher=factory(page,posting_guard=guard)
    with pytest.raises(ReelNeedsUser):immediate(publisher.run())
    assert len(calls)==4 and publisher.publish_attempted is True
    assert ('click','[data-cm-reel="publish"]') not in page.actions


@pytest.mark.parametrize('guard',[None,lambda:False])
def test_publisher_without_verified_information_stops_before_navigation_or_upload(factory,guard):
    page=Page(initial(),composer(),editor())
    with pytest.raises(ReelNeedsUser):immediate(factory(page,posting_guard=guard).run())
    assert not any(action[0] in ('goto','upload','click') for action in page.actions)


def test_only_verified_nonblank_setup_callback_records_hash_receipt(manager):
    start(manager,expected_page_url='https://www.facebook.com/profile.php?id=555',setup={'bio':'Authorized new bio'})
    manager.checkpoint(3,page_url='https://www.facebook.com/profile.php?id=555',accepted=True,switched=True)
    calls=[]
    def record(url,receipt,fields):calls.append((url,receipt,fields));return verdict(ready=True)
    manager.posting_policy=SimpleNamespace(record_chrome_setup=record)
    digest=manager.input_fingerprint('bio','Authorized new bio')
    manager.verified_setup_field(3,'bio','updating',fingerprint=digest)
    manager.verified_setup_field(3,'cover','skipped',fingerprint='a'*64)
    assert not calls
    manager.verified_setup_field(3,'bio','completed',fingerprint=digest)
    assert calls[0][0].endswith('id=555') and str(UUID(calls[0][1]))==calls[0][1]
    assert calls[0][2]=={'bio':digest}
    assert 'Authorized new bio' not in json.dumps(calls)


@pytest.mark.parametrize('change',['accept_only','not_switched','blank','wrong_fingerprint','missing_facade'])
def test_acceptance_or_unverified_setup_does_not_approve_posting(manager,change):
    setup={} if change=='blank' else {'bio':'Authorized new bio'}
    start(manager,expected_page_url='https://www.facebook.com/profile.php?id=555',setup=setup,accept_only=change=='accept_only')
    manager.checkpoint(3,page_url='https://www.facebook.com/profile.php?id=555',accepted=True,switched=change!='not_switched')
    calls=[]
    manager.posting_policy=None if change=='missing_facade' else SimpleNamespace(record_chrome_setup=lambda *args:calls.append(args) or verdict(ready=True))
    digest='a'*64 if change=='wrong_fingerprint' else manager.input_fingerprint('bio','Authorized new bio')
    with pytest.raises(PageSetupNeedsUser):manager.verified_setup_field(3,'bio','completed',fingerprint=digest)
    assert not calls and json.loads(manager.row(3).fields)['bio']['status']=='needs_user'
