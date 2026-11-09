"""Exact review scopes use fake Telegram, inert fixture bytes and local SQLite."""
import asyncio
from datetime import timedelta
import hashlib
from uuid import uuid4

import httpx
import pytest

from page_manager.errors import AppError
from test_telegram_approval import f,saved,CHAT,USER,TOKEN,TelegramApproval


@pytest.fixture
def scope(f):
    saved(f);asyncio.run(f.service.check(2));f.calls.clear()
    tickets=[];current={(f.binding['queue'],f.binding['job_id']):dict(f.binding)}
    for index in range(14):
        binding={**f.binding,'job_id':100+index,'page_id':str(20000+index)}
        verdict=f.ledger.ensure(binding);tickets.append(f.ledger.get(verdict['request_id']))
        current[(binding['queue'],binding['job_id'])]=binding
    old=f.ledger.ensure({**f.binding,'job_id':9})
    f.ledger.decide(old['request_id'],old['binding_hash'],'approve',actor_id=int(USER),chat_id=int(CHAT),update_id=9000,message_id=9000)
    f.old_approved=old['request_id']
    f.service.cohorts.status_resolver=lambda queue,job:{'binding':current[(queue,job)],'status':'scheduled','published_url':None}
    f.body={'revision':2,'gate_revision':2,'request_id':str(uuid4()),
        'members':[{'request_id':ticket['request_id'],'binding_hash':ticket['binding_hash']} for ticket in tickets]}
    f.tickets=tickets;f.current=current
    return f


def start(f):return f.service.start_cohort(f.body)['cohort']
def cycle(f,cohort):asyncio.run(f.service._cohort_cycle(f.service._row(),cohort['cohort_id']))
def callback(body,index,update_id):
    return {'update_id':update_id,'callback_query':{'id':'fixture-query','from':{'id':int(USER),'is_bot':False},
        'message':{'message_id':101+index,'chat':{'id':int(CHAT)}},
        'data':body['reply_markup']['inline_keyboard'][0][0]['callback_data']}}


def test_freeze_exact14_is_local_idempotent_and_keeps_global_disabled_and_old_ticket_pending(scope):
    f=scope;cohort=start(f)
    assert cohort['active'] and cohort['total']==cohort['counts']['pending']==14
    assert f.calls==[] and f.service.snapshot()['enabled'] is False
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'
    assert f.ledger.get(f.old_approved)['state']=='approved'
    assert start(f)['cohort_id']==cohort['cohort_id']
    assert TOKEN not in str(f.service.snapshot()) and 'nonce' not in str(cohort)
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM telegram_approval_cohorts').fetchone()[0]==1


def test_only14_new_previews_and_one_exact_bulk_group_can_approve_without_global_enable(scope):
    f=scope;cohort=start(f)
    for _ in range(14):cycle(f,cohort)
    assert f.calls.count('sendVideo')==14 and f.calls.count('sendMessage')==15
    assert all(str(f.ticket['binding']['page_id']) not in body['text'] for body in f.messages)
    batch=f.service.batches.recent()[0]
    assert batch['total']==14 and batch['cohort_id']==cohort['cohort_id']
    f.updates[:]=[callback(f.messages[-1],len(f.messages)-1,1000)]
    cycle(f,cohort)
    assert all(f.ledger.get(ticket['request_id'])['state']=='approved' for ticket in f.tickets)
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'
    assert f.ledger.get(f.old_approved)['state']=='approved'
    assert f.service.snapshot()['cohort']['state']=='completed' and not f.service.snapshot()['enabled']
    sent=f.calls.count('sendVideo');f.service.tick()
    assert f.calls.count('sendVideo')==sent


def test_individual_decision_only_within_scope_and_batch_waits_all_confirmed_previews(scope):
    f=scope;cohort=start(f);cycle(f,cohort)
    f.updates[:]=[callback(f.messages[0],0,1001)];cycle(f,cohort);f.updates.clear()
    assert f.ledger.get(f.tickets[0]['request_id'])['state']=='approved'
    assert f.service.batches.recent()==[]
    for _ in range(12):cycle(f,cohort)
    assert f.calls.count('sendVideo')==14
    assert f.service.batches.recent()[0]['total']==13
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'


def test_old_delivered_ticket_callback_is_ignored_by_cohort(scope):
    f=scope;cohort=start(f);nonce='o'*32
    with f.store.connect() as db:
        db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
            (f.ticket['request_id'],f.ticket['binding_hash'],2,hashlib.sha256(nonce.encode()).hexdigest(),
             'sent',CHAT,40,50,None,f.now[0].isoformat()))
    update={'update_id':1002,'callback_query':{'id':'old-query','from':{'id':int(USER),'is_bot':False},
        'message':{'message_id':50,'chat':{'id':int(CHAT)}},'data':'tg1:'+nonce+':a'}}
    asyncio.run(f.service._update(f.service._row(),update,cohort_id=cohort['cohort_id']))
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'
    assert f.calls==[]  # No polling lease: even an acknowledgement fails closed.


@pytest.mark.parametrize('change',['revision','gate_revision','hash','duplicate','extra','missing','bool_revision'])
def test_invalid_scope_cannot_change_old_tickets_or_enable_bot(scope,change):
    f=scope
    if change in ('revision','gate_revision'):f.body[change]+=1
    elif change=='hash':f.body['members'][0]['binding_hash']='f'*64
    elif change=='duplicate':f.body['members'][1]=f.body['members'][0]
    elif change=='extra':f.body['members'][0]['path']='private'
    elif change=='missing':f.body.pop('members')
    else:f.body['revision']=True
    with pytest.raises(AppError):start(f)
    assert f.calls==[] and not f.service.snapshot()['enabled'] and f.service.snapshot()['cohort'] is None


def test_only_one_active_scope_and_no_trial_overlap(scope):
    f=scope;cohort=start(f)
    f.body={**f.body,'request_id':str(uuid4())}
    with pytest.raises(AppError,match='Phiên duyệt'):start(f)
    with pytest.raises(AppError):f.service.start_trial(2,f.ticket['request_id'])
    assert f.calls==[] and f.service.snapshot()['cohort']['cohort_id']==cohort['cohort_id']


def test_active_trial_prevents_scope_start(scope):
    f=scope;f.service.start_trial(2,f.ticket['request_id'])
    with pytest.raises(AppError):start(f)
    assert f.calls==[] and f.service.snapshot()['cohort'] is None


@pytest.mark.parametrize('change',['settings','gate','job','deadline','media'])
def test_scope_fences_configuration_job_deadline_and_actual_bytes(scope,change):
    f=scope;cohort=start(f)
    if change=='settings':f.service.configure({'revision':2,'enabled':False,'allowed_user_ids':['100']})
    elif change=='gate':f.ledger.configure(False,2)
    elif change=='job':f.current[('token',100)]={**f.current[('token',100)],'caption':'changed'}
    elif change=='deadline':f.now[0]+=timedelta(hours=1)
    else:f.file.write_bytes(b'CHANGED INERT MEDIA')
    cycle(f,cohort)
    assert not set(f.calls)&{'sendVideo','sendMessage'}
    expected='expired' if change=='deadline' else 'pending'
    assert f.ledger.get(f.ticket['request_id'])['state']==expected
    assert all(f.ledger.get(ticket['request_id'])['state']==expected for ticket in f.tickets)
    with f.store.connect() as db:
        assert {row[0] for row in db.execute('SELECT state FROM publication_approval_requests WHERE request_id<>?',(f.old_approved,))}=={'pending'}
        assert db.execute('SELECT state FROM publication_approval_requests WHERE request_id=?',(f.old_approved,)).fetchone()[0]=='approved'


@pytest.mark.parametrize('method',['sendVideo','sendMessage'])
def test_uncertain_send_holds_scope_and_never_resends(scope,method):
    f=scope;cohort=start(f);f.options['fail']=method;cycle(f,cohort)
    assert f.service.snapshot()['cohort']['state']=='uncertain'
    count=len(f.calls);f.options.clear();f.service.tick()
    assert len(f.calls)==count and f.ledger.get(f.ticket['request_id'])['state']=='pending'


def test_scope_start_requires_fresh_check_and_replay_never_extends_deadline(scope):
    f=scope;cohort=start(f);deadline=cohort['expires_at'];f.now[0]+=timedelta(minutes=1)
    assert start(f)['expires_at']==deadline
    f.now[0]+=timedelta(minutes=5)
    with pytest.raises(AppError):start(f)
    assert f.calls==[]


def test_scope_batch_cannot_be_used_in_global_callback_or_include_older_pending_ticket(scope):
    f=scope;cohort=start(f)
    for _ in range(14):cycle(f,cohort)
    summary=f.messages[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'];nonce=summary.split(':')[1]
    with pytest.raises(AppError):
        f.service.batches.decide(nonce,revision=2,policy_revision=2,actor_id=int(USER),chat_id=int(CHAT),
            update_id=1010,message_id=100+len(f.messages))
    with pytest.raises(AppError):
        f.service.batches.freeze([f.tickets[0]['request_id'],f.ticket['request_id']],revision=2,policy_revision=2,
            chat_id=CHAT,cohort_id=cohort['cohort_id'])
    assert all(f.ledger.get(ticket['request_id'])['state']=='pending' for ticket in f.tickets)


def test_restart_keeps_exact_scope_and_confirmed_deliveries_without_resending(scope):
    f=scope;cohort=start(f);cycle(f,cohort)
    original=f.service
    f.service=TelegramApproval(f.store,original.secrets,f.ledger,transport=original.transport,
        media_resolver=original.media_resolver,status_resolver=original.cohorts.status_resolver,
        clock=lambda:f.now[0],send_interval=0)
    assert f.service.snapshot()['cohort']['cohort_id']==cohort['cohort_id']
    assert f.calls.count('sendVideo')==1
    cycle(f,cohort)
    assert f.calls.count('sendVideo')==2 and f.service.snapshot()['cohort']['counts']['delivered']==2
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'


def test_changed_single_member_blocks_entire_cohort_batch_atomically(scope):
    f=scope;cohort=start(f)
    for _ in range(14):cycle(f,cohort)
    nonce=f.messages[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'].split(':')[1]
    f.current[('token',113)]={**f.current[('token',113)],'caption':'changed after previews'}
    with pytest.raises(AppError):
        f.service.batches.decide(nonce,revision=2,policy_revision=2,actor_id=int(USER),chat_id=int(CHAT),
            update_id=2000,message_id=100+len(f.messages),cohort_id=cohort['cohort_id'])
    assert all(f.ledger.get(ticket['request_id'])['state']=='pending' for ticket in f.tickets)
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM publication_approval_decisions').fetchone()[0]==1  # Old approved ticket only.


def test_cohort_individual_callback_rechecks_authorization_in_decision_transaction(scope):
    f=scope;cohort=start(f);cycle(f,cohort)
    ticket=f.tickets[0]
    with f.store.connect() as db:
        delivery=dict(db.execute('SELECT * FROM telegram_approval_delivery WHERE request_id=?',(ticket['request_id'],)).fetchone())
        db.execute("UPDATE telegram_approval_settings SET allowed_users='[\"100\"]'")
    with pytest.raises(AppError):
        f.service.cohorts.decide_member(cohort['cohort_id'],ticket['request_id'],ticket['binding_hash'],'approve',
            actor_id=int(USER),chat_id=int(CHAT),update_id=2001,message_id=delivery['message_id'],nonce_sha256=delivery['nonce_sha256'])
    assert f.ledger.get(ticket['request_id'])['state']=='pending'


def test_other_telegram_consumer_invalidates_connection_before_any_more_scope_previews(scope):
    f=scope;cohort=start(f);original=f.service.transport
    async def conflicted(request):
        if request.url.path.endswith('/getUpdates'):return httpx.Response(409,json={'ok':False})
        return await original.handle_async_request(request)
    f.service.transport=httpx.MockTransport(conflicted)
    cycle(f,cohort)
    assert f.service.snapshot()['check']['ok'] is False
    assert f.service.snapshot()['check']['reason']=='consumer_conflict'
    assert f.calls.count('sendVideo')==1
    count=len(f.calls);f.now[0]+=timedelta(seconds=31);f.service.tick()
    assert len(f.calls)==count and f.ledger.get(f.ticket['request_id'])['state']=='pending'
