"""One-ticket Telegram trials; fake transport, temporary DB and inert media only."""
import asyncio
from datetime import timedelta
import hashlib

import pytest

from page_manager.errors import AppError
from page_manager.telegram_approval import TelegramApproval
from test_telegram_approval import f, saved, callback, CHAT, USER


def checked(f):
    saved(f)
    asyncio.run(f.service.check(2))
    f.calls.clear()


def second(f):
    return f.ledger.get(f.ledger.ensure({**f.binding,'job_id':2,'page_id':'67890'})['request_id'])


def cycle(f, request_id):
    asyncio.run(f.service._cycle(f.service._row(),trial_request_id=request_id))


def test_trial_sends_only_selected_second_ticket_once_and_preserves_other_pending(f):
    other=second(f);checked(f)
    before=f.ledger.get(f.ticket['request_id'])
    value=f.service.start_trial(2,other['request_id'])
    assert value['enabled'] is False and value['trial']['active'] is True
    assert f.calls==[]
    expiry=value['trial']['expires_at'];f.now[0]+=timedelta(seconds=1)
    assert f.service.start_trial(2,other['request_id'])['trial']['expires_at']==expiry
    cycle(f,other['request_id']);cycle(f,other['request_id'])
    assert f.calls.count('sendVideo')==f.calls.count('sendMessage')==1
    assert f.calls.count('getUpdates')==2
    assert '67890' in f.messages[0]['text']
    assert f.ledger.get(f.ticket['request_id'])==before
    assert f.service.snapshot()['enabled'] is False
    assert f.service.snapshot()['trial']['delivery_state']=='sent'


def test_trial_scope_blocks_foreign_notification_before_any_delivery_or_io(f):
    other=second(f);checked(f);f.service.start_trial(2,f.ticket['request_id'])
    with pytest.raises(AppError):asyncio.run(f.service._notify(f.service._row(),other,trial_request_id=f.ticket['request_id']))
    assert f.calls==[]
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM telegram_approval_delivery').fetchone()[0]==0


def test_trial_callback_accepts_selected_ticket_only_and_stops_after_decision(f):
    other=second(f);checked(f);target=f.ticket['request_id'];f.service.start_trial(2,target)
    foreign_nonce='F'*32  # Valid callback format: exercise target scope, not parser rejection.
    with f.store.connect() as db:
        db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
            (other['request_id'],other['binding_hash'],2,hashlib.sha256(foreign_nonce.encode()).hexdigest(),
             'sent',CHAT,998,999,None,f.now[0].isoformat()))
    cycle(f,target)
    foreign=callback(f,data='tg1:'+foreign_nonce+':a',message={'message_id':999,'chat':{'id':int(CHAT)}})
    f.updates.append(foreign);cycle(f,target)
    assert f.ledger.get(other['request_id'])['state']=='pending'
    assert f.ledger.get(target)['state']=='pending'
    f.updates[:]=[{**callback(f),'update_id':11}];cycle(f,target)
    assert f.ledger.get(target)['state']=='approved'
    assert f.ledger.get(other['request_id'])['state']=='pending'
    value=f.service.snapshot()
    assert value['enabled'] is False and value['trial']['active'] is False and value['trial']['state']=='decided'
    assert f.calls.count('sendVideo')==1


@pytest.mark.parametrize('change',['expire','disconnect','change_chat'])
def test_trial_revocation_blocks_sends_and_callbacks(f,change):
    checked(f);target=f.ticket['request_id'];f.service.start_trial(2,target);cycle(f,target)
    decision=callback(f);before=list(f.calls)
    if change=='expire':f.now[0]+=timedelta(minutes=31)
    elif change=='disconnect':f.service.disconnect(2)
    else:f.service.configure({'revision':2,'enabled':False,'chat_id':'-100456'})
    with pytest.raises(AppError):asyncio.run(f.service._update(f.service._row(),decision,trial_request_id=target))
    assert f.calls==before and f.ledger.get(target)['state']=='pending'
    assert f.service.snapshot()['trial']['active'] is False


def test_only_one_active_trial_and_fresh_check_required(f):
    other=second(f);checked(f);target=f.ticket['request_id'];f.service.start_trial(2,target)
    with pytest.raises(AppError) as error:f.service.start_trial(2,other['request_id'])
    assert error.value.code=='telegram_trial_in_progress'
    f.now[0]+=timedelta(minutes=6)
    with pytest.raises(AppError):f.service.start_trial(2,target)
    assert f.calls==[]


def test_ambiguous_trial_video_send_is_held_without_resend(f):
    checked(f);target=f.ticket['request_id'];f.service.start_trial(2,target);f.options['fail']='sendVideo'
    cycle(f,target)
    state=f.service.snapshot()
    assert state['trial']['state']=='uncertain' and state['trial']['active'] is False
    assert f.calls==['sendVideo'] and f.ledger.get(target)['state']=='pending'
    f.service.start_trial(2,target)
    assert f.service.snapshot()['trial']['active'] is False and f.calls==['sendVideo']


def test_trial_survives_adapter_restart_without_notifying_another_ticket(f):
    other=second(f);checked(f);target=f.ticket['request_id'];f.service.start_trial(2,target);cycle(f,target)
    service=f.service
    f.service=TelegramApproval(f.store,service.secrets,f.ledger,transport=service.transport,
        media_resolver=service.media_resolver,clock=service.clock,send_interval=0)
    cycle(f,target)
    assert f.calls.count('sendVideo')==1 and f.service.snapshot()['trial']['active'] is True
    assert f.ledger.get(other['request_id'])['state']=='pending'


def test_disabled_tick_receives_only_authorized_trial(f):
    second(f);checked(f);target=f.ticket['request_id'];f.service.start_trial(2,target)
    async def run():
        f.service.tick()
        await f.service._task
    asyncio.run(run())
    assert f.calls==['sendVideo','sendMessage','getUpdates']
    assert f.service.snapshot()['enabled'] is False


def test_trial_cannot_start_on_globally_enabled_connection(f):
    checked(f);f.service.configure({'revision':2,'enabled':True})
    with pytest.raises(AppError) as error:f.service.start_trial(3,f.ticket['request_id'])
    assert error.value.code=='telegram_trial_requires_disabled' and f.calls==[]
