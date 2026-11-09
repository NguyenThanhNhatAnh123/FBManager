"""Immediate review and exact explicit groups: fake transport, no real sends."""
import asyncio
from datetime import timedelta
import hashlib
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from page_manager.telegram_approval import _review_schedule
from page_manager.telegram_publication_notifications import PublicationNotifications
from test_telegram_approval import f, enabled, cycle, callback, CHAT, USER
from test_telegram_approval_cohorts import scope, start, cycle as scope_cycle, callback as scope_callback
from test_telegram_manual_delivery import confirm


def setup(f,count,*,mode='on_approval',expected=None,start_job=100,batch_id=None):
    f.ledger.withdraw('token',1)
    current=getattr(f,'current_jobs',{})
    group={'id':batch_id or str(uuid4()),'expected_count':expected or count}
    tickets=[]
    for index in range(count):
        binding={**f.binding,'job_id':start_job+index,'page_id':str(20000+start_job+index),'review_batch':dict(group)}
        if mode=='on_approval':
            binding.update(publish_mode=mode,scheduled_at=(f.now[0]-timedelta(minutes=1)).isoformat(),
                           approve_by=(f.now[0]+timedelta(hours=5)).isoformat())
        value=f.ledger.ensure(binding);item=f.ledger.get(value['request_id'])
        current[(binding['queue'],binding['job_id'])]={'binding':item['binding'],'status':'scheduled','published_url':None,'ready':True}
        tickets.append(item)
    f.current_jobs=current
    f.service.status_resolver=lambda queue,job:current[(queue,job)]
    return tickets


def summary(f):
    row=f.service._row();assert f.service._lease(row)
    try:asyncio.run(f.service._notify_batch(row))
    finally:f.service._release()


def test_individual_on_approval_has_truthful_label_and_deadline_not_old_anchor(f):
    enabled(f);items=setup(f,1,expected=16);cycle(f)
    assert f.calls.count('sendVideo')==1 and f.service.batches.recent()==[]
    assert 'Đăng ngay sau khi bạn duyệt' in f.messages[0]['text']
    assert 'Hạn duyệt: 31/12/2029 21:00' in f.messages[0]['text']
    assert 'Lịch: ' not in f.messages[0]['text']
    f.now[0]+=timedelta(hours=1);f.updates.append(callback(f));cycle(f)
    item=f.ledger.get(items[0]['request_id'])
    assert item['state']=='approved' and item['approved_at']==f.now[0].isoformat()
    assert item['publish_window_end']==(f.now[0]+timedelta(minutes=30)).isoformat()
    assert f.ledger.verdict(item['binding'])['can_publish'] is True
    f.now[0]+=timedelta(minutes=31)
    assert f.ledger.verdict(item['binding'])['can_publish'] is False


def test_two_exact_sixteen_groups_wait_for_own_previews_and_never_mix(f):
    enabled(f);immediate=setup(f,16);scheduled=setup(f,16,mode='scheduled',start_job=200)
    confirm(f,immediate[:15]);summary(f)
    assert f.service.batches.recent()==[] and f.messages==[]
    confirm(f,immediate[15:]);summary(f)
    first=f.service.batches.get(f.service.batches.recent()[0]['batch_id'])
    assert set(first['request_ids'])=={item['request_id'] for item in immediate}
    assert first['total']==16 and len(f.messages[0]['text'].encode('utf-16-le'))//2<=4096
    confirm(f,scheduled);summary(f)
    groups=f.service.batches.recent()
    assert len(groups)==2 and {group['total'] for group in groups}=={16}
    second=f.service.batches.get(next(group['batch_id'] for group in groups if group['batch_id']!=first['batch_id']))
    assert set(second['request_ids'])=={item['request_id'] for item in scheduled}
    assert not set(first['request_ids'])&set(second['request_ids'])
    assert f.calls.count('sendVideo')==0  # Receipts seeded as synthetic confirmed previews.


@pytest.mark.parametrize('change',['extra','cancel','changed_job','mixed_mode'])
def test_incomplete_changed_or_mixed_explicit_group_never_freezes_partial_button(f,change):
    enabled(f);items=setup(f,2);confirm(f,items)
    if change=='extra':setup(f,1,expected=2,start_job=300,batch_id=items[0]['binding']['review_batch']['id'])
    elif change=='cancel':f.ledger.withdraw('token',items[0]['binding']['job_id'])
    elif change=='changed_job':f.current_jobs[('token',items[0]['binding']['job_id'])]['binding']={**items[0]['binding'],'caption':'changed'}
    else:
        old=items[0]['binding'];new={key:value for key,value in old.items() if key not in ('publish_mode','approve_by')}
        new['scheduled_at']=(f.now[0]+timedelta(hours=1)).isoformat()
        f.ledger.ensure(new)
    summary(f)
    assert f.service.batches.recent()==[] and f.messages==[]


def test_bulk_on_approval_uses_shared_actual_decision_window_and_replay_does_not_extend(f):
    enabled(f);items=setup(f,2);confirm(f,items)
    group=f.service.batches.freeze([item['request_id'] for item in items],revision=3,policy_revision=2,chat_id=CHAT)
    f.service.batches.begin_send(group['batch_id']);f.service.batches.sent(group['batch_id'],3000)
    f.now[0]+=timedelta(hours=2)
    kwargs=dict(revision=3,policy_revision=2,actor_id=int(USER),chat_id=int(CHAT),update_id=500,message_id=3000)
    assert f.service.batches.decide(group['nonce'],**kwargs)['state']=='approved'
    until=(f.now[0]+timedelta(minutes=30)).isoformat()
    assert {f.ledger.get(item['request_id'])['publish_window_end'] for item in items}=={until}
    f.now[0]+=timedelta(minutes=2)
    assert f.service.batches.decide(group['nonce'],**kwargs)['replayed'] is True
    assert {f.ledger.get(item['request_id'])['publish_window_end'] for item in items}=={until}


def test_cohort_member_uses_shared_immediate_decision_window(scope):
    f=scope;new=[]
    for item in f.tickets:
        binding={**item['binding'],'publish_mode':'on_approval',
                 'scheduled_at':(f.now[0]-timedelta(minutes=1)).isoformat(),
                 'approve_by':(f.now[0]+timedelta(hours=5)).isoformat()}
        fresh=f.ledger.get(f.ledger.ensure(binding)['request_id']);new.append(fresh)
        f.current[('token',binding['job_id'])]=fresh['binding']
    f.tickets=new;f.body['members']=[{key:item[key] for key in ('request_id','binding_hash')} for item in new]
    cohort=start(f);scope_cycle(f,cohort)
    f.now[0]+=timedelta(hours=1)
    f.updates[:]=[scope_callback(f.messages[0],0,700)];scope_cycle(f,cohort)
    item=f.ledger.get(new[0]['request_id'])
    assert item['state']=='approved' and item['publish_window_end']==(f.now[0]+timedelta(minutes=30)).isoformat()


def test_on_approval_receipts_never_describe_old_anchor_as_chosen_post_time(f):
    enabled(f);items=setup(f,1,expected=16);item=items[0]
    notifications=PublicationNotifications(f.store,f.ledger,status_resolver=f.service.status_resolver,clock=lambda:f.now[0])
    status={'ready':True,'status':'scheduled','page_name':'Synthetic','published_url':None}
    for event in ('decision_ack','schedule_ready'):
        text=notifications._body({'event':event},{**item,'decision':'approve'},status)
        assert 'Đăng ngay sau khi bạn duyệt' in text and 'Hạn duyệt: ' in text
        assert 'Giờ đã chọn: ' not in text and 'chờ đến giờ' not in text
    assert 'Lịch: ' not in _review_schedule(item['binding'])
