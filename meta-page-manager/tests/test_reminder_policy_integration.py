"""Independent cross-boundary tests; temporary SQLite and fake Bot API only."""
import asyncio
from datetime import timedelta

import pytest

from page_manager.publication_approvals import review_deadline,review_is_open
from test_telegram_approval import f,enabled,cycle,CHAT,USER


POLICY='remind_until_decision_v1'


def pending(f,*,mode='scheduled'):
    f.ledger.withdraw('token',1)
    binding={**f.binding,'approval_policy':POLICY,
             'scheduled_at':(f.now[0]+timedelta(minutes=10)).isoformat()}
    if mode=='on_approval':
        binding.update(publish_mode='on_approval',scheduled_at=f.now[0].isoformat(),approve_by=None)
    item=f.ledger.get(f.ledger.ensure(binding)['request_id'])
    f.binding=item['binding'];f.ticket=item
    f.current={'binding':item['binding'],'status':'scheduled','published_url':None,'ready':True}
    f.service.status_resolver=lambda _queue,_job:f.current
    return item


def original_callback(f,message,*,update_id=500,decision='a'):
    data=message['reply_markup']['inline_keyboard'][0][0]['callback_data']
    if decision=='r':data=data[:-1]+'r'
    return {'update_id':update_id,'callback_query':{'id':'synthetic-query',
        'from':{'id':int(USER),'is_bot':False},
        'message':{'message_id':101,'chat':{'id':int(CHAT)}},'data':data}}


@pytest.mark.parametrize('mode',['scheduled','on_approval'])
def test_original_confirmed_buttons_allow_real_late_approval_after_seven_days(f,mode):
    item=pending(f,mode=mode);enabled(f);cycle(f)
    original=f.messages[0]
    assert f.calls.count('sendVideo')==1
    assert review_deadline(item) is None and item['approve_by'] is None
    f.now[0]+=timedelta(days=7)
    current=f.ledger.get(item['request_id'])
    assert current['state']=='pending' and review_is_open(current,f.now[0])
    assert f.ledger.verdict(item['binding'])['can_publish'] is False
    f.updates.append(original_callback(f,original));cycle(f)
    current=f.ledger.get(item['request_id'])
    assert current['state']=='approved' and current['approved_at']==f.now[0].isoformat()
    assert current['publish_window_end']==(f.now[0]+timedelta(minutes=30)).isoformat()
    assert f.ledger.verdict(item['binding'])['can_publish'] is True
    assert f.calls.count('sendVideo')==1


def test_late_rejection_is_durable_and_never_turns_into_publication_permission(f):
    item=pending(f);enabled(f);cycle(f);original=f.messages[0]
    f.now[0]+=timedelta(days=3)
    f.updates.append(original_callback(f,original,decision='r'));cycle(f)
    current=f.ledger.get(item['request_id'])
    assert current['state']=='rejected' and not f.ledger.verdict(item['binding'])['can_publish']
    count=len(f.messages)
    f.now[0]+=timedelta(days=20);cycle(f)
    assert len(f.messages)==count and f.calls.count('sendVideo')==1


def test_legacy_deadline_is_not_silently_revived_by_new_reminder_policy(f):
    enabled(f);cycle(f);original=f.messages[0]
    f.now[0]+=timedelta(hours=2)
    f.updates.append(original_callback(f,original));cycle(f)
    assert f.ledger.get(f.ticket['request_id'])['state']=='expired'
    assert not f.ledger.verdict(f.binding)['can_publish']
    assert f.calls.count('sendVideo')==1


def confirmed(f):
    from test_telegram_manual_delivery import confirm
    from page_manager.telegram_approval_reminders import TelegramApprovalReminders
    item=pending(f);enabled(f);confirm(f,[item])
    f.reminders=TelegramApprovalReminders(f.store,f.ledger,f.service.batches,
        status_resolver=lambda _queue,_job:f.current,clock=lambda:f.now[0])
    return item


def remind(f,sent,*,reminders=None,fail=False):
    reminders=reminders or f.reminders
    row=f.service._row();assert f.service._lease(row)
    row=f.service._row()
    async def sender(body,*,reminder_id):
        reminders.validate_attempt(row,reminder_id)
        sent.append(body)
        if fail:raise RuntimeError('Synthetic ambiguous network result')
        value={'message_id':5000+len(sent),'chat':{'id':int(CHAT)}}
        reminders.validate_attempt(row,reminder_id)
        return value
    try:return asyncio.run(reminders.run_once(row,sender))
    finally:f.service._release()


def test_five_minute_boundary_restart_and_repeat_have_one_durable_attempt_each(f):
    from page_manager.telegram_approval_reminders import TelegramApprovalReminders
    item=confirmed(f);sent=[]
    f.now[0]+=timedelta(minutes=10)
    remind(f,sent);assert sent==[]
    f.now[0]+=timedelta(seconds=299)
    remind(f,sent);assert sent==[]
    f.now[0]+=timedelta(seconds=1)
    remind(f,sent);assert len(sent)==1
    assert sent[0]['reply_to_message_id']==2001
    assert 'reply_markup' not in sent[0]  # Original confirmed buttons remain the authority.
    restarted=TelegramApprovalReminders(f.store,f.ledger,f.service.batches,
        status_resolver=lambda _queue,_job:f.current,clock=lambda:f.now[0])
    remind(f,sent,reminders=restarted);assert len(sent)==1
    f.now[0]+=timedelta(seconds=299)
    remind(f,sent,reminders=restarted);assert len(sent)==1
    f.now[0]+=timedelta(seconds=1)
    remind(f,sent,reminders=restarted);assert len(sent)==2
    with f.store.connect() as db:
        attempts=db.execute('SELECT state,sequence FROM telegram_approval_reminder_attempts ORDER BY sequence').fetchall()
    assert [(r['state'],r['sequence']) for r in attempts]==[('sent',1),('sent',2)]
    assert f.ledger.get(item['request_id'])['state']=='pending'
    assert 'sendVideo' not in f.calls


def test_uncertain_delivery_is_not_retried_within_slot_but_next_requested_interval_continues(f):
    from page_manager.telegram_approval_reminders import TelegramApprovalReminders
    confirmed(f);sent=[];f.now[0]+=timedelta(minutes=15)
    try:remind(f,sent,fail=True)
    except RuntimeError:pass
    assert len(sent)==1
    restarted=TelegramApprovalReminders(f.store,f.ledger,f.service.batches,
        status_resolver=lambda _queue,_job:f.current,clock=lambda:f.now[0])
    remind(f,sent,reminders=restarted);assert len(sent)==1
    f.now[0]+=timedelta(seconds=299)
    remind(f,sent,reminders=restarted);assert len(sent)==1
    f.now[0]+=timedelta(seconds=1)
    remind(f,sent,reminders=restarted);assert len(sent)==2
    with f.store.connect() as db:
        states=[r[0] for r in db.execute('SELECT state FROM telegram_approval_reminder_attempts ORDER BY sequence')]
    assert states==['uncertain','sent']


@pytest.mark.parametrize('change',['approve','reject','cancel','completed','binding'])
def test_reminders_stop_after_decision_or_current_job_stops_matching(f,change):
    item=confirmed(f);sent=[];f.now[0]+=timedelta(minutes=15)
    remind(f,sent);assert len(sent)==1
    if change in ('approve','reject'):
        f.ledger.decide(item['request_id'],item['binding_hash'],change,
            actor_id=USER,chat_id=CHAT,update_id=800,message_id=2001)
    elif change=='cancel':f.ledger.withdraw('token',1)
    elif change=='completed':f.current.update(status='completed',published_url='https://www.facebook.com/reel/123')
    else:f.current['binding']={**item['binding'],'caption':'changed'}
    f.now[0]+=timedelta(minutes=5)
    remind(f,sent);assert len(sent)==1


def confirmed_group(f):
    from uuid import uuid4
    from test_telegram_on_approval import setup
    from test_telegram_manual_delivery import confirm
    enabled(f);items=setup(f,16,mode='scheduled')
    updated=[];group_id=str(uuid4())
    for item in items:
        binding={**item['binding'],'approval_policy':POLICY,
            'review_batch':{'id':group_id,'expected_count':16}}
        new=f.ledger.get(f.ledger.ensure(binding)['request_id']);updated.append(new)
        f.current_jobs[('token',binding['job_id'])]['binding']=new['binding']
    confirm(f,updated)
    from page_manager.telegram_approval_reminders import TelegramApprovalReminders
    f.reminders=TelegramApprovalReminders(f.store,f.ledger,f.service.batches,
        status_resolver=f.service.status_resolver,clock=lambda:f.now[0])
    group=f.service.batches.freeze([x['request_id'] for x in updated],revision=3,policy_revision=2,chat_id=CHAT)
    f.service.batches.begin_send(group['batch_id']);f.service.batches.sent(group['batch_id'],3000)
    return updated,group


def test_full_confirmed_group_of_sixteen_gets_one_reminder_not_sixteen(f):
    updated,_group=confirmed_group(f)
    f.now[0]+=timedelta(hours=1,minutes=5)
    sent=[];remind(f,sent)
    assert len(sent)==1 and sent[0]['reply_to_message_id']==3000
    assert '16' in sent[0]['text'] and 'reply_markup' not in sent[0]
    remind(f,sent);assert len(sent)==1
    assert not any(f.ledger.verdict(x['binding'])['can_publish'] for x in updated)


def test_partial_group_decision_keeps_other_pending_items_reminding_with_usable_buttons(f):
    items,_group=confirmed_group(f);f.now[0]+=timedelta(hours=1,minutes=5)
    sent=[];remind(f,sent);assert len(sent)==1 and sent[0]['reply_to_message_id']==3000
    first=items[0]
    f.ledger.decide(first['request_id'],first['binding_hash'],'approve',
        actor_id=USER,chat_id=CHAT,update_id=810,message_id=2001)
    f.now[0]+=timedelta(minutes=5);remind(f,sent)
    assert len(sent)==2
    assert sent[-1]['reply_to_message_id'] in range(2002,2017)
    assert f.ledger.get(first['request_id'])['state']=='approved'
    assert sum(f.ledger.get(x['request_id'])['state']=='pending' for x in items)==15


def test_long_offline_gap_does_not_send_a_catchup_burst(f):
    confirmed(f);sent=[];f.now[0]+=timedelta(days=10)
    remind(f,sent);assert len(sent)==1
    for _ in range(5):remind(f,sent)
    assert len(sent)==1
    f.now[0]+=timedelta(seconds=299);remind(f,sent);assert len(sent)==1
    f.now[0]+=timedelta(seconds=1);remind(f,sent);assert len(sent)==2


def test_two_workers_cannot_claim_the_same_five_minute_operation(f):
    from page_manager.telegram_approval_reminders import TelegramApprovalReminders
    confirmed(f);f.now[0]+=timedelta(minutes=15)
    async def scenario():
        row=f.service._row();assert f.service._lease(row);row=f.service._row()
        first=f.reminders
        entered=asyncio.Event();release=asyncio.Event();sent=[]
        async def sender(body,*,reminder_id):
            first.validate_attempt(row,reminder_id);sent.append(body);entered.set()
            await release.wait()
            first.validate_attempt(row,reminder_id)
            return {'message_id':5001,'chat':{'id':int(CHAT)}}
        work=asyncio.create_task(first.run_once(row,sender))
        try:
            await asyncio.wait_for(entered.wait(),3)
            second=TelegramApprovalReminders(f.store,f.ledger,f.service.batches,
                status_resolver=lambda _queue,_job:f.current,clock=lambda:f.now[0])
            async def forbidden(*_args,**_kwargs):pytest.fail('Second worker dispatched an already claimed reminder')
            await second.run_once(row,forbidden)
            assert len(sent)==1
            release.set();await asyncio.wait_for(work,3)
        finally:
            release.set()
            if not work.done():
                work.cancel()
                try:await work
                except asyncio.CancelledError:pass
            f.service._release()
    asyncio.run(scenario())


def test_normal_service_cycle_dispatches_reminders_without_a_separate_worker(f):
    item=pending(f);enabled(f);cycle(f)
    assert f.calls.count('sendVideo')==1
    assert hasattr(f.service,'reminders')
    f.service.reminders.status_resolver=lambda _queue,_job:f.current
    before=f.calls.count('sendMessage')
    f.now[0]+=timedelta(minutes=15);cycle(f)
    assert f.calls.count('sendMessage')==before+1
    assert f.calls.count('sendVideo')==1
    assert f.ledger.get(item['request_id'])['state']=='pending'
