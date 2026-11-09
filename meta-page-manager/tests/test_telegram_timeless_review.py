"""New-policy callbacks remain usable; frozen legacy expiry is unchanged."""
from datetime import timedelta
import asyncio
from uuid import uuid4

from page_manager.publication_approvals import REMIND_UNTIL_DECISION
from page_manager.telegram_publication_notifications import PublicationNotifications
from test_telegram_approval import f,enabled,cycle,callback,CHAT,USER
from test_telegram_on_approval import setup
from test_telegram_manual_delivery import confirm
from test_telegram_approval_cohorts import scope,start,cycle as scoped_cycle,callback as scoped_callback


def timeless(f,items):
    result=[];group_id=str(uuid4())
    for item in items:
        binding={**item['binding'],'approval_policy':REMIND_UNTIL_DECISION}
        if 'review_batch' in binding:binding['review_batch']={**binding['review_batch'],'id':group_id}
        if binding.get('publish_mode')=='on_approval':binding['approve_by']=None
        fresh=f.ledger.get(f.ledger.ensure(binding)['request_id']);result.append(fresh)
        f.current_jobs[(binding['queue'],binding['job_id'])]['binding']=fresh['binding']
    return result


def test_individual_original_buttons_work_days_after_due_without_video_repeat(f):
    enabled(f);item=timeless(f,setup(f,1,expected=16))[0];cycle(f)
    f.now[0]+=timedelta(days=2);f.updates.append(callback(f));cycle(f)
    value=f.ledger.get(item['request_id'])
    assert value['state']=='approved' and value['approved_at']==f.now[0].isoformat()
    assert value['publish_window_end']==(f.now[0]+timedelta(minutes=30)).isoformat()
    assert f.calls.count('sendVideo')==1
    notifications=PublicationNotifications(f.store,f.ledger,status_resolver=f.service.status_resolver,clock=lambda:f.now[0])
    text=notifications._body({'event':'decision_ack'},{**value,'decision':'approve'},
        {'ready':True,'status':'scheduled','page_name':'Synthetic','published_url':None})
    assert 'không hết hạn' in text and 'Hạn duyệt:' not in text


def test_exact_group_snapshot_has_no_deadline_and_original_bulk_accepts_late_decision(f):
    enabled(f);items=timeless(f,setup(f,2,mode='scheduled'));confirm(f,items)
    group=f.service.batches.freeze([item['request_id'] for item in items],revision=3,policy_revision=2,chat_id=CHAT)
    assert group['approve_by'] is None and group['approval_policy']==REMIND_UNTIL_DECISION and group['reminder_interval_seconds']==300
    f.service.batches.begin_send(group['batch_id']);f.service.batches.sent(group['batch_id'],3000)
    f.now[0]+=timedelta(days=2)
    assert f.service.batches.decide(group['nonce'],revision=3,policy_revision=2,actor_id=int(USER),chat_id=int(CHAT),update_id=400,message_id=3000)['state']=='approved'
    assert all(f.ledger.get(item['request_id'])['publish_window_end']==(f.now[0]+timedelta(minutes=30)).isoformat() for item in items)


def test_explicit_new_policy_cohort_keeps_usable_original_buttons_after_due(scope):
    f=scope;items=[]
    for item in f.tickets:
        binding={**item['binding'],'approval_policy':REMIND_UNTIL_DECISION}
        fresh=f.ledger.get(f.ledger.ensure(binding)['request_id']);items.append(fresh)
        f.current[('token',binding['job_id'])]=fresh['binding']
    f.tickets=items;f.body['members']=[{key:item[key] for key in ('request_id','binding_hash')} for item in items]
    group=start(f)
    assert group['expires_at'] is None and group['approval_policy']==REMIND_UNTIL_DECISION
    scoped_cycle(f,group);f.now[0]+=timedelta(days=2)
    f.updates[:]=[scoped_callback(f.messages[0],0,902)];scoped_cycle(f,group)
    assert f.ledger.get(items[0]['request_id'])['state']=='approved'
    assert f.ledger.get(items[0]['request_id'])['publish_window_end']==(f.now[0]+timedelta(minutes=30)).isoformat()


def test_reminder_snapshot_distinguishes_live_send_from_expired_claim_without_writes(f):
    identifier=str(uuid4())
    with f.store.connect() as db:
        db.execute('INSERT INTO telegram_approval_reminders VALUES(?,?,?,?,?,?)',
            ('fixture','single',f.ticket['request_id'],'a'*64,f.now[0].isoformat(),1))
        db.execute('INSERT INTO telegram_approval_reminder_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',
            (identifier,'fixture',1,'sending',100,None,f.now[0].isoformat(),None,(f.now[0]+timedelta(seconds=120)).isoformat(),None))
    assert f.service.snapshot()['reminders']['counts']['sending']==1
    f.now[0]+=timedelta(seconds=121)
    counts=f.service.snapshot()['reminders']['counts']
    assert counts['sending']==0 and counts['uncertain']==1
    with f.store.connect() as db:assert db.execute('SELECT state FROM telegram_approval_reminder_attempts').fetchone()[0]=='sending'
    assert f.calls==[]
