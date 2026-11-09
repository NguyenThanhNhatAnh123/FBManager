"""Immediate human review uses synthetic SQLite rows and fake Bot API only."""
import asyncio
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from page_manager.errors import AppError
from test_telegram_approval import f, enabled, cycle, register_producer, CHAT, USER
from test_telegram_approval_cohorts import scope, start, cycle as scope_cycle


def ticket(f,number):
    return f.ledger.get(f.ledger.ensure({**f.binding,'job_id':number})['request_id'])


def confirm(f,tickets):
    with f.store.connect() as db:
        for index,item in enumerate(tickets,1):
            db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                (item['request_id'],item['binding_hash'],2,hashlib.sha256(item['request_id'].encode()).hexdigest(),
                 'sent',CHAT,1000+index,2000+index,None,f.now[0].isoformat()))


def test_first_manual_item_is_sent_while_later_items_are_still_producing(f):
    enabled(f);register_producer(f,[f.ticket]);cycle(f)
    assert f.calls.count('sendVideo')==1 and len(f.messages)==1
    assert f.service.batches.recent()==[]
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'
    second=ticket(f,2);register_producer(f,[second]);cycle(f)
    assert f.calls.count('sendVideo')==2 and len(f.messages)==2
    assert f.service.batches.recent()==[]  # Different manually started runs.


def test_confirmed_same_batch_is_reviewable_without_waiting_for_other_pending(f):
    enabled(f);second=ticket(f,2);third=ticket(f,3)
    register_producer(f,[f.ticket,second,third]);confirm(f,[f.ticket,second])
    row=f.service._row();assert f.service._lease(row)
    try:asyncio.run(f.service._notify_batch(row))
    finally:f.service._release()
    group=f.service.batches.recent()[0]
    assert group['total']==2
    assert set(f.service.batches.get(group['batch_id'])['request_ids'])=={f.ticket['request_id'],second['request_id']}
    assert f.calls.count('sendVideo')==0 and len(f.messages)==1
    assert f.ledger.get(third['request_id'])['state']=='pending'


def test_common_auto_batch_across_runs_groups_but_unknown_or_other_batches_do_not(f):
    enabled(f);second=ticket(f,2);third=ticket(f,3);fourth=ticket(f,4)
    batch=str(uuid4())
    register_producer(f,[f.ticket],batch_id=batch)
    register_producer(f,[second],batch_id=batch)
    register_producer(f,[third],batch_id=str(uuid4()))
    confirm(f,[f.ticket,second,third,fourth])
    ids=f.service.batches.candidates(2,CHAT)
    assert set(ids)=={f.ticket['request_id'],second['request_id']}
    for foreign in (third,fourth):
        with pytest.raises(AppError):
            f.service.batches.freeze([f.ticket['request_id'],foreign['request_id']],revision=3,policy_revision=2,chat_id=CHAT)
    assert f.service.batches.recent()==[]


@pytest.mark.parametrize('change',['hash','mapping','video','malformed_auto'])
def test_group_binding_corruption_never_sends_bulk_or_partial_approval(f,change):
    enabled(f);second=ticket(f,2);run=register_producer(f,[f.ticket,second]);confirm(f,[f.ticket,second])
    with f.store.connect() as db:
        if change=='hash':db.execute('UPDATE idea_manual_runs SET payload_hash=?',('f'*64,))
        elif change=='video':db.execute('UPDATE idea_pipeline_approved_videos SET video_sha256=?',('f'*64,))
        else:
            payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs WHERE id=?',(run,)).fetchone()[0])
            if change=='mapping':payload['variants'][0]['reservation_id']=str(uuid4())
            else:payload['automatic_batch']={'batch_id':'untrusted-name'}
            encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'))
            db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=?',(encoded,hashlib.sha256(encoded.encode()).hexdigest()))
    assert f.service.batches.candidates(2,CHAT)==[]
    with pytest.raises(AppError):f.service.batches.freeze([f.ticket['request_id'],second['request_id']],revision=3,policy_revision=2,chat_id=CHAT)
    assert f.calls==['getMe','getWebhookInfo'] and f.service.batches.recent()==[]


def test_frozen_group_is_rechecked_before_http_and_decision(f):
    enabled(f);second=ticket(f,2);register_producer(f,[f.ticket,second]);confirm(f,[f.ticket,second])
    group=f.service.batches.freeze([f.ticket['request_id'],second['request_id']],revision=3,policy_revision=2,chat_id=CHAT)
    f.service.batches.begin_send(group['batch_id'])
    with f.store.connect() as db:db.execute('UPDATE idea_manual_runs SET payload_hash=?',('f'*64,))
    with pytest.raises(AppError):f.service._batch_current(3,group['batch_id'])
    f.service.batches.sent(group['batch_id'],3000)
    with pytest.raises(AppError):f.service.batches.decide(group['nonce'],revision=3,policy_revision=2,actor_id=int(USER),chat_id=int(CHAT),update_id=80,message_id=3000)
    assert all(f.ledger.get(item['request_id'])['state']=='pending' for item in (f.ticket,second))


def test_forced_manual_items_can_be_reviewed_with_global_gate_toggle_off(f):
    enabled(f);second=ticket(f,2);register_producer(f,[f.ticket,second]);f.ledger.configure(False,2)
    assert f.ledger.settings()['required'] is False
    assert f.ledger.verdict(f.binding)['required'] is True
    cycle(f);cycle(f)
    group=f.service.batches.get(f.service.batches.recent()[0]['batch_id'])
    assert group['total']==2 and f.calls.count('sendVideo')==2
    nonce=f.messages[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'].split(':')[1]
    f.service.batches.decide(nonce,revision=3,policy_revision=2,actor_id=int(USER),chat_id=int(CHAT),update_id=81,message_id=100+len(f.messages))
    assert all(f.ledger.get(item['request_id'])['state']=='approved' for item in (f.ticket,second))


def test_non_required_old_tickets_cannot_use_bulk_when_global_gate_is_off(f):
    enabled(f);second=ticket(f,2);confirm(f,[f.ticket,second]);f.ledger.configure(False,2)
    with pytest.raises(AppError):f.service.batches.freeze([f.ticket['request_id'],second['request_id']],revision=3,policy_revision=2,chat_id=CHAT)
    assert f.service.batches.recent()==[]


def test_non_required_old_delivery_does_not_delay_forced_group(f):
    enabled(f);manual=[]
    for number in (2,3):
        manual.append(f.ledger.get(f.ledger.ensure({**f.binding,'job_id':number,'asset_sha256':'b'*64})['request_id']))
    register_producer(f,manual);confirm(f,[f.ticket,*manual]);f.ledger.configure(False,2)
    assert set(f.service.batches.candidates(2,CHAT))=={item['request_id'] for item in manual}
    assert f.ledger.verdict(f.binding)['required'] is False


def test_forced_cohort_uses_item_policy_even_when_global_gate_is_off(scope):
    f=scope;register_producer(f,f.tickets);f.ledger.configure(False,2);f.body['gate_revision']=3
    cohort=start(f);scope_cycle(f,cohort)
    assert f.calls.count('sendVideo')==1 and f.service.snapshot()['cohort']['state']=='active'
    assert f.service.snapshot()['gate']['required'] is False


def test_snapshot_during_http_separates_sending_and_unknown_then_cancel_holds(scope):
    f=scope;cohort=start(f)
    async def run():
        begun=asyncio.Event()
        async def handler(request):
            assert request.url.path.endswith('/sendVideo')
            begun.set();await asyncio.Event().wait()
        f.service.transport=httpx.MockTransport(handler)
        task=asyncio.create_task(f.service._cohort_cycle(f.service._row(),cohort['cohort_id']))
        await begun.wait()
        snapshot=f.service.snapshot()
        assert snapshot['sending_notifications']==1 and snapshot['uncertain_notifications']==0
        assert snapshot['cohort']['state']=='active'
        assert snapshot['cohort']['counts']['sending']==1 and snapshot['cohort']['counts']['uncertain']==0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
    snapshot=f.service.snapshot()
    assert snapshot['sending_notifications']==0 and snapshot['uncertain_notifications']==1
    assert snapshot['cohort']['state']=='uncertain' and snapshot['cohort']['counts']['uncertain']==1
    before=list(f.calls);f.service.tick();assert f.calls==before


def test_orphaned_sending_receipt_is_unknown_and_never_retried(scope):
    f=scope;cohort=start(f);item=f.tickets[0]
    with f.store.connect() as db:
        db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
            (item['request_id'],item['binding_hash'],2,'f'*64,'sending_video',CHAT,None,None,None,f.now[0].isoformat()))
    assert f.service.snapshot()['cohort']['state']=='uncertain'
    scope_cycle(f,cohort)
    assert f.calls==[] and f.service.snapshot()['cohort']['state']=='uncertain'


def test_normal_long_poll_is_short_for_prompt_item_delivery(f):
    enabled(f);captured=[];original=f.service.transport
    async def handler(request):
        if request.url.path.endswith('/getUpdates'):captured.append(json.loads(request.content)['timeout'])
        return await original.handle_async_request(request)
    f.service.transport=httpx.MockTransport(handler);cycle(f)
    assert captured==[3]
