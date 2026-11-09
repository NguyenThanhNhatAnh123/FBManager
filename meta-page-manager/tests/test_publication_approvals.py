"""Approval metadata in temporary SQLite only; no Telegram or Meta calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json

import pytest

from page_manager.errors import AppError
from page_manager.publication_approvals import PublicationApprovals,normalize_binding
from page_manager.store import Store


@pytest.fixture
def fixture(tmp_path):
    now=[datetime(2030,1,1,12,tzinfo=timezone.utc)]
    store=Store(tmp_path);ledger=PublicationApprovals(store,clock=lambda:now[0])
    binding={'queue':'token','job_id':1,'page_id':'555','asset_id':'a'*32,'asset_sha256':'b'*64,
        'asset_size':30,'caption':'Exact caption\nhttps://blueox179.com/campaign/test',
        'scheduled_at':(now[0]+timedelta(hours=1)).isoformat(),'timezone':'America/Los_Angeles','actor_id':1,'actor_revision':1}
    return ledger,binding,now


def require(ledger):return ledger.configure(True,ledger.settings()['gate_revision'])


def decide(ledger,row,decision='approve',**values):
    return ledger.decide(row['request_id'],row['binding_hash'],decision,
        **({'actor_id':123,'chat_id':-456,'update_id':1,'message_id':789}|values))


def test_legacy_default_cas_and_reads_never_create_a_request(fixture):
    ledger,binding,_=fixture
    assert ledger.settings()=={'required':False,'gate_revision':1}
    assert ledger.ensure(binding)['can_publish']
    with ledger.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM publication_approval_requests').fetchone()[0]==0
    assert require(ledger)=={'required':True,'gate_revision':2}
    assert not ledger.verdict(binding)['can_publish']
    assert ledger.pending()['total']==0
    with pytest.raises(AppError,match='Đọc lại'):ledger.configure(False,1)
    assert ledger.configure(True,2)['gate_revision']==2


def test_enqueue_idempotence_and_frozen_exact_binding(fixture):
    ledger,binding,_=fixture;require(ledger)
    first=ledger.ensure(binding)
    assert first==ledger.ensure(binding) and first['state']=='pending' and not first['can_publish']
    row=ledger.get(first['request_id'])
    assert row['binding']==binding and ledger.pending()['total']==1
    assert row['publish_window_end']=='2030-01-01T13:30:00+00:00'
    assert not {'actor_id','chat_id','message_id','update_id','nonce','token'} & row.keys()


def test_immediate_deadline_independent_of_old_request_anchor_and_window_from_real_decision(fixture):
    ledger,binding,now=fixture
    binding|={'publish_mode':'on_approval','scheduled_at':(now[0]-timedelta(minutes=20)).isoformat(),
              'approve_by':(now[0]+timedelta(hours=2)).isoformat()}
    first=ledger.ensure(binding)
    assert first['required'] is True and not first['can_publish'] and ledger.settings()['required'] is False
    assert ledger.pending()['total']==1
    now[0]+=timedelta(minutes=45)
    result=decide(ledger,first)
    assert result['approved_at']==now[0].isoformat()
    assert result['publish_window_end']==(now[0]+timedelta(minutes=30)).isoformat()
    assert ledger.require(binding)['can_publish']
    frozen=dict(result);now[0]+=timedelta(minutes=5)
    assert decide(ledger,first)==frozen
    assert decide(ledger,first,update_id=2)['publish_window_end']==frozen['publish_window_end']
    now[0]+=timedelta(minutes=26)
    assert ledger.verdict(binding)['reason']=='publication_approval_expired'


def test_immediate_cutoff_holds_without_catchup_or_automatic_reapproval(fixture):
    ledger,binding,now=fixture
    binding|={'publish_mode':'on_approval','scheduled_at':now[0].isoformat(),
              'approve_by':(now[0]+timedelta(minutes=5)).isoformat()}
    first=ledger.ensure(binding);now[0]+=timedelta(minutes=5)
    with pytest.raises(AppError) as error:decide(ledger,first)
    assert error.value.code=='publication_approval_expired'
    assert ledger.ensure(binding)['state']=='expired' and ledger.pending()['total']==0


def test_declared_review_batch_always_requires_human_approval_and_is_immutable(fixture):
    from uuid import uuid4
    ledger,binding,_=fixture
    group={'id':str(uuid4()),'expected_count':16}
    ticket=ledger.ensure(binding|{'review_batch':group})
    assert ticket['required'] and not ticket['can_publish'] and ledger.pending()['total']==1
    decide(ledger,ticket)
    assert ledger.verdict(binding|{'review_batch':group})['can_publish']
    changed={**group,'id':str(uuid4())}
    assert not ledger.verdict(binding|{'review_batch':changed})['can_publish']
    assert set(normalize_binding(binding))==set(binding)


@pytest.mark.parametrize('extra',[
    {'publish_mode':'scheduled'}, {'publish_mode':'on_approval'}, {'approve_by':'2030-01-01T14:00:00Z'},
    {'publish_mode':'on_approval','approve_by':'2030-01-01T12:00:00Z'},
    {'review_batch':None}, {'review_batch':{'id':'bad','expected_count':16}},
])
def test_invalid_optional_publication_modes_do_not_change_legacy_contract(fixture,extra):
    ledger,binding,_=fixture
    with pytest.raises(AppError):normalize_binding(binding|extra)


@pytest.mark.parametrize('patch',[
    {'caption':'Different'}, {'caption':'Exact caption\nhttps://blueox179.com/campaign/test '},
    {'page_id':'556'}, {'asset_sha256':'c'*64}, {'asset_id':'c'*32}, {'asset_size':31},
    {'scheduled_at':'2030-01-01T13:01:00+00:00'}, {'timezone':'UTC'}, {'actor_id':2}, {'actor_revision':2},
])
def test_approval_never_transfers_to_changed_schedule(fixture,patch):
    ledger,binding,_=fixture;require(ledger);row=ledger.ensure(binding);decide(ledger,row)
    assert ledger.verdict(binding|patch)['state']=='binding_changed'
    verdict=ledger.ensure(binding|patch)
    assert verdict['state']=='pending' and not verdict['can_publish'] and verdict['request_id']!=row['request_id']
    assert ledger.get(row['request_id'])['state']=='cancelled'
    with pytest.raises(AppError):decide(ledger,row,update_id=2)
    with ledger.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM publication_approval_requests').fetchone()[0]==2


def test_approve_before_due_remains_valid_during_frozen_window_then_holds(fixture):
    ledger,binding,now=fixture;require(ledger);row=ledger.ensure(binding)
    assert decide(ledger,row)['state']=='approved'
    now[0]=datetime(2030,1,1,13,0,2,tzinfo=timezone.utc)
    assert ledger.require(binding)['can_publish']
    now[0]=datetime(2030,1,1,13,30,tzinfo=timezone.utc)
    assert ledger.verdict(binding)['can_publish']
    now[0]+=timedelta(microseconds=1)
    assert ledger.verdict(binding)['reason']=='publication_approval_expired'
    with pytest.raises(AppError):ledger.require(binding)


def test_late_pending_approval_never_triggers_overdue_catchup(fixture):
    ledger,binding,now=fixture;require(ledger);row=ledger.ensure(binding)
    now[0]=datetime(2030,1,1,13,tzinfo=timezone.utc)
    assert ledger.verdict(binding)['state']=='expired' and ledger.pending()['total']==0
    with pytest.raises(AppError) as error:decide(ledger,row)
    assert error.value.code=='publication_approval_expired'
    assert decide(ledger,row,'reject')['state']=='rejected'


def test_decision_event_replay_cannot_change_decision_or_target(fixture):
    ledger,binding,_=fixture;require(ledger);row=ledger.ensure(binding)
    first=decide(ledger,row)
    assert decide(ledger,row)==first
    with pytest.raises(AppError):decide(ledger,row,'reject')
    with pytest.raises(AppError):decide(ledger,row,message_id=790)
    other=ledger.ensure(binding|{'job_id':2})
    with pytest.raises(AppError):decide(ledger,other)
    with pytest.raises(AppError):ledger.decide(row['request_id'],'c'*64,'approve',actor_id=123,chat_id=-456,update_id=2,message_id=789)


def test_rejection_cancel_and_restart_do_not_approve_or_resurrect(fixture):
    ledger,binding,_=fixture;require(ledger);row=ledger.ensure(binding)
    decide(ledger,row,'reject')
    assert ledger.verdict(binding)['state']=='rejected'
    with pytest.raises(AppError):decide(ledger,row,update_id=2)
    other=binding|{'job_id':2};second=ledger.ensure(other);decide(ledger,second,update_id=3)
    ledger.withdraw('token',2)
    restarted=PublicationApprovals(ledger.store,clock=ledger.clock)
    assert restarted.ensure(other)['state']=='cancelled' and not restarted.verdict(other)['can_publish']
    with pytest.raises(AppError):decide(restarted,second,update_id=4)
    assert restarted.settings()['required']


def test_concurrent_controllers_create_one_request(fixture):
    ledger,binding,_=fixture;require(ledger)
    second=PublicationApprovals(ledger.store,clock=ledger.clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda item:item.ensure(binding),(ledger,second)))
    assert results[0]['request_id']==results[1]['request_id'] and ledger.pending()['total']==1


@pytest.mark.parametrize('patch',[
    {'job_id':True},{'job_id':0},{'page_id':'0'},{'asset_id':'wrong'},{'asset_sha256':'A'*64},
    {'asset_size':False},{'caption':'a\x00b'},{'caption':'\ud800'},{'scheduled_at':'2030-01-01T13:00:00'},
    {'timezone':'../bad'},{'actor_revision':True},{'queue':'other'},{'path':'secret'}])
def test_invalid_bindings_fail_closed_without_partial_rows(fixture,patch):
    ledger,binding,_=fixture;require(ledger)
    with pytest.raises(AppError):ledger.ensure(binding|patch)
    assert ledger.pending()['total']==0


def test_equivalent_utc_time_and_queue_namespaces(fixture):
    ledger,binding,_=fixture;require(ledger);first=ledger.ensure(binding)
    assert ledger.ensure(binding|{'scheduled_at':'2030-01-01T05:00:00-08:00'})==first
    assert ledger.ensure(binding|{'queue':'chrome','actor_revision':0})['request_id']!=first['request_id']
