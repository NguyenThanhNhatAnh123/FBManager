"""Fake Bot API with the real approval transport, groups and status outbox."""
import asyncio
from copy import deepcopy
from datetime import timedelta

import pytest

from page_manager.errors import AppError
from page_manager.telegram_publication_notifications import PublicationNotifications
from test_telegram_approval import f, enabled, saved, callback, register_producer, CHAT, USER


def feedback(f):
    jobs={1:{'binding':deepcopy(f.binding),'status':'scheduled','step':'waiting','published_url':None}}
    def resolver(queue,job_id):
        result=deepcopy(jobs[job_id]);result['ready']=f.ledger.verdict(result['binding'])['can_publish'] and result['status']=='scheduled'
        return result
    f.service.status_resolver=resolver
    f.service.notifications=PublicationNotifications(f.store,f.ledger,status_resolver=resolver,clock=lambda:f.now[0])
    return jobs


def cycle(f,**kwargs):asyncio.run(f.service._cycle(f.service._row(),**kwargs))


def test_single_callback_persists_ack_schedule_and_verified_post_receipts_without_resending(f):
    jobs=feedback(f);enabled(f);cycle(f)
    assert len(f.messages)==1 and 'reply_markup' in f.messages[0]
    decision=callback(f);f.updates.append(decision);cycle(f)
    assert f.ledger.get(f.ticket['request_id'])['state']=='approved'
    assert len(f.messages)==2 and 'Đã nhận duyệt' in f.messages[1]['text'] and 'sẵn sàng' in f.messages[1]['text']
    assert '31/12/2029 17:00' in f.messages[1]['text']
    cycle(f);assert len(f.messages)==2
    jobs[1].update(status='completed',step='completed',published_url='https://www.facebook.com/reel/987654321')
    cycle(f)
    assert len(f.messages)==3 and 'Đã đăng và xác nhận Reel' in f.messages[2]['text']
    assert 'https://www.facebook.com/reel/987654321' in f.messages[2]['text']
    cycle(f);assert len(f.messages)==3 and f.calls.count('sendVideo')==1


def test_approved_trial_gets_persistent_ack_and_later_status_with_global_mode_off(f):
    jobs=feedback(f);saved(f);asyncio.run(f.service.check(2));f.service.start_trial(2,f.ticket['request_id'])
    cycle(f,trial_request_id=f.ticket['request_id'])
    f.updates.append(callback(f));cycle(f,trial_request_id=f.ticket['request_id'])
    assert not f.service.snapshot()['enabled'] and f.service.snapshot()['trial']['state']=='decided'
    assert len(f.messages)==2 and 'Đã nhận duyệt' in f.messages[-1]['text']
    jobs[1].update(status='completed',published_url='https://www.facebook.com/reel/987654321')
    async def watch():
        f.service.tick();await f.service._task
    asyncio.run(watch())
    assert len(f.messages)==3 and 'Đã đăng' in f.messages[-1]['text']
    assert f.calls.count('sendVideo')==1 and not f.service.snapshot()['enabled']


def test_notice_scope_cannot_poll_or_send_another_video_while_disabled(f):
    feedback(f);saved(f);asyncio.run(f.service.check(2));before=list(f.calls)
    for method in ('getUpdates','sendVideo'):
        with pytest.raises(AppError):asyncio.run(f.service._request(f.service._row(),method,{},notification_request_id=f.ticket['request_id']))
    with pytest.raises(AppError):asyncio.run(f.service._request(f.service._row(),'sendMessage',{},notification_request_id=f.ticket['request_id']))
    assert f.calls==before


def add_ticket(f,number):
    binding={**f.binding,'job_id':number,'page_id':str(12345+number)}
    return f.ledger.get(f.ledger.ensure(binding)['request_id'])


def test_twenty_confirmed_videos_get_group_without_waiting_for_twenty_first(f):
    tickets=[f.ticket]+[add_ticket(f,n) for n in range(2,22)];enabled(f)
    register_producer(f,tickets)
    # Choose the actual first20 in the same ordering as ledger.pending().
    ordered=f.ledger.pending()['items'];selected=ordered[:20]
    with f.store.connect() as db:
        for index,ticket in enumerate(selected,1):
            db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                (ticket['request_id'],ticket['binding_hash'],2,f'{index:064x}','sent',CHAT,100+index,200+index,None,f.now[0].isoformat()))
    row=f.service._row();assert f.service._lease(row)
    try:asyncio.run(f.service._notify_batch(row))
    finally:f.service._release()
    assert len(f.messages)==1 and 'Duyệt toàn bộ (20 video)'==f.messages[0]['reply_markup']['inline_keyboard'][0][0]['text']
    assert len(f.messages[0]['text'].encode('utf-16-le'))//2<=4096
    group=f.service.batches.get(f.service.batches.recent()[0]['batch_id'])
    assert set(group['request_ids'])=={ticket['request_id'] for ticket in selected}
    assert ordered[20]['request_id'] not in group['request_ids']


def test_group_callback_approves_exact_members_and_new_ticket_stays_pending(f):
    second=add_ticket(f,2);enabled(f)
    register_producer(f,[f.ticket,second])
    cycle(f);cycle(f)
    groups=[message for message in f.messages if message.get('reply_markup',{}).get('inline_keyboard',[[]])[0]
        and message['reply_markup']['inline_keyboard'][0][0]['callback_data'].startswith('tgb1:')]
    assert len(groups)==1
    summary=groups[0];index=f.messages.index(summary)
    third=add_ticket(f,3)
    update={'update_id':100,'callback_query':{'id':'batch-query','from':{'id':int(USER),'is_bot':False},
        'message':{'message_id':101+index,'chat':{'id':int(CHAT)}},'data':summary['reply_markup']['inline_keyboard'][0][0]['callback_data']}}
    asyncio.run(f.service._update(f.service._row(),update))
    assert f.ledger.get(f.ticket['request_id'])['state']==f.ledger.get(second['request_id'])['state']=='approved'
    assert f.ledger.get(third['request_id'])['state']=='pending'
    asyncio.run(f.service._update(f.service._row(),update))
    assert f.ledger.get(third['request_id'])['state']=='pending'


def test_bulk_summary_never_sent_in_one_video_trial(f):
    second=add_ticket(f,2);feedback(f);saved(f);asyncio.run(f.service.check(2));f.service.start_trial(2,f.ticket['request_id'])
    cycle(f,trial_request_id=f.ticket['request_id'])
    assert len(f.messages)==1 and f.calls.count('sendVideo')==1
    assert f.service.batches.recent()==[] and f.ledger.get(second['request_id'])['state']=='pending'
