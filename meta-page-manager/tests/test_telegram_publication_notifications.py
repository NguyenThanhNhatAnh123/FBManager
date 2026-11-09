"""Synthetic receipts/statuses/outbox only; no Telegram, Meta or application data."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace

import pytest

from conftest import immediate
from page_manager.publication_approvals import PublicationApprovals
from page_manager.store import Store
from page_manager.telegram_publication_notifications import PublicationNotifications


@pytest.fixture
def fixture(tmp_path):
    now=[datetime(2030,1,1,12,tzinfo=timezone.utc)]
    store=Store(tmp_path);ledger=PublicationApprovals(store,clock=lambda:now[0]);ledger.configure(True,1)
    scope={'revision':2,'policy_revision':1,'chat_id':'-100','bot_id':'123'}
    with store.connect() as db:
        db.executescript('''CREATE TABLE telegram_approval_settings(
            id INTEGER PRIMARY KEY,encrypted_token TEXT,enabled INTEGER,revision INTEGER,policy_revision INTEGER,
            chat_id TEXT,bot_id TEXT,checked_ok INTEGER,checked_revision INTEGER);
            INSERT INTO telegram_approval_settings VALUES(1,'fixture-encrypted',0,2,1,'-100','123',1,2);
            CREATE TABLE telegram_approval_delivery(request_id TEXT, binding_hash TEXT,connection_revision INTEGER,
                state TEXT,chat_id TEXT,message_id INTEGER,video_message_id INTEGER,
                PRIMARY KEY(request_id,connection_revision));''')
    jobs={};sent=[]
    def resolver(queue,job_id):return deepcopy(jobs.get((queue,job_id)))
    notifier=PublicationNotifications(store,ledger,status_resolver=resolver,clock=lambda:now[0])
    async def send(text,*,request_id):
        sent.append({'text':text,'request_id':request_id})
        return {'message_id':1000+len(sent),'chat':{'id':-100}}
    return SimpleNamespace(store=store,ledger=ledger,scope=scope,now=now,jobs=jobs,sent=sent,
        notifier=notifier,resolver=resolver,send=send)


def add(f,number=1,*,queue='token',decision='approve',status='scheduled',ready=True,delivery='decided'):
    binding={'queue':queue,'job_id':number,'page_id':str(500+number),'asset_id':f'{number:032x}',
        'asset_sha256':f'{number:064x}','asset_size':20,'caption':'Exact fixture caption',
        'scheduled_at':(f.now[0]+timedelta(hours=1)).isoformat(),'timezone':'America/Los_Angeles',
        'actor_id':1,'actor_revision':1 if queue=='token' else 0}
    ticket=f.ledger.ensure(binding)
    if decision:
        f.ledger.decide(ticket['request_id'],ticket['binding_hash'],decision,
            actor_id=99,chat_id=-100,update_id=number,message_id=100+number)
    with f.store.connect() as db:
        db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?)',
            (ticket['request_id'],ticket['binding_hash'],1,delivery,'-100',100+number,200+number))
    f.jobs[(queue,number)]={'binding':binding,'status':status,'step':status,'ready':ready,'published_url':None}
    return ticket


def run(f,**kwargs):return immediate(f.notifier.run_once(f.scope,f.send,**kwargs))


def test_catchup_approved_disabled_trial_ack_once_and_ready_combined(fixture):
    f=fixture;ticket=add(f)
    assert run(f)['sent']==1
    assert f.sent[0]['request_id']==ticket['request_id']
    assert 'Đã nhận duyệt' in f.sent[0]['text'] and 'sẵn sàng' in f.sent[0]['text']
    assert 'tại ứng dụng' in f.sent[0]['text'] and 'MetaScheduled' not in f.sent[0]['text']
    for _ in range(3):assert run(f)['sent']==0
    assert len(f.sent)==1
    snapshot=f.notifier.snapshot(f.scope)
    assert snapshot['counts']['sent']==1 and snapshot['items'][0]['event']=='decision_ack'
    assert not {'text','owner','bot_token','encrypted_token','nonce'} & snapshot['items'][0].keys()


def test_receipt_names_page_and_expired_schedule_needs_action(fixture):
    f=fixture;add(f);f.jobs[('token',1)]['page_name']='Fixture\x00 Page'
    run(f)
    assert 'Fixture  Page (501)' in f.sent[0]['text'] and 'Lịch nội bộ Via' in f.sent[0]['text']
    assert '\x00' not in f.sent[0]['text'] and 'SHA256' not in f.sent[0]['text']
    f.jobs[('token',1)].update(status='needs_user',step='approval_expired',ready=False)
    run(f)
    assert 'quá cửa sổ đăng' in f.sent[-1]['text']


@pytest.mark.parametrize('queue',['token','chrome'])
def test_ready_notification_after_ack_waited_for_checks(fixture,queue):
    f=fixture;add(f,queue=queue,ready=False)
    run(f);assert 'còn chờ' in f.sent[0]['text']
    f.jobs[(queue,1)]['ready']=True
    assert run(f)['sent']==1 and 'Lịch sẵn sàng tại ứng dụng' in f.sent[-1]['text']
    assert run(f)['sent']==0 and len(f.sent)==2


@pytest.mark.parametrize('url',[
    None,'https://evil.example/reel/123','http://www.facebook.com/reel/123',
    'https://user@www.facebook.com/reel/123','https://www.facebook.com/reel/123?token=secret',
    'https://www.facebook.com/reel/0','https://www.facebook.com/watch/123'])
def test_completed_without_valid_reel_proof_never_sends_success(fixture,url):
    f=fixture;add(f,status='completed',ready=False);f.jobs[('token',1)]['published_url']=url
    run(f)
    assert [item['event'] for item in f.notifier.snapshot()['items']]==['decision_ack']
    assert not any('Đã đăng và xác nhận' in item['text'] for item in f.sent)


def test_confirmed_completed_publishes_one_receipt_only(fixture):
    f=fixture;add(f);run(f)
    f.jobs[('token',1)].update(status='completed',ready=False,published_url='https://m.facebook.com/reels/12345/')
    assert run(f)['sent']==1 and 'https://www.facebook.com/reel/12345' in f.sent[-1]['text']
    for _ in range(3):assert run(f)['sent']==0
    f.jobs[('token',1)]['published_url']='https://www.facebook.com/reel/54321'
    assert run(f)['sent']==0


@pytest.mark.parametrize('status',['needs_user','needs_review','cancelled','interrupted'])
def test_paused_or_cancelled_change_notifies_once_without_raw_error(fixture,status):
    f=fixture;ticket=add(f);run(f)
    f.jobs[('token',1)].update(status=status,ready=False,message='SECRET RAW ERROR')
    if status=='cancelled':f.ledger.withdraw('token',1)
    assert run(f)['sent']==1
    assert f.notifier.snapshot()['items'][0]['event']==status
    f.jobs[('token',1)]['message']='Different secret'
    assert run(f)['sent']==0
    assert 'SECRET' not in json.dumps(f.sent) and 'Different secret' not in json.dumps(f.sent)
    if status=='needs_review':
        f.jobs[('token',1)]['status']='processing';run(f)
        f.jobs[('token',1)]['status']=status
        assert run(f)['sent']==1


@pytest.mark.parametrize('decision,delivery',[(None,'sent'),('approve','uncertain'),('approve','sending_buttons')])
def test_pending_or_unconfirmed_preview_never_authorizes_status_send(fixture,decision,delivery):
    f=fixture;add(f,decision=decision,delivery=delivery)
    assert run(f)['sent']==0 and f.sent==[]


def test_binding_change_unknown_job_and_config_edit_fail_closed(fixture):
    f=fixture;ticket=add(f);f.jobs[('token',1)]['binding']['caption']='changed'
    assert run(f)['sent']==0
    f.jobs[('token',1)]['binding']=f.ledger.get(ticket['request_id'])['binding']
    f.notifier.status_resolver=lambda *_:None
    assert run(f)['sent']==0
    f.notifier.status_resolver=f.resolver
    with f.store.connect() as db:db.execute("UPDATE telegram_approval_settings SET policy_revision=2,revision=3,checked_revision=3,chat_id='-200' WHERE id=1")
    assert run(f)['sent']==0
    f.scope.update(revision=3,policy_revision=2,chat_id='-200')
    assert run(f)['sent']==0 and f.sent==[]


def test_ambiguous_send_persists_hold_and_never_blind_resends(fixture):
    f=fixture;add(f);calls=[]
    async def unknown(text,*,request_id):calls.append(request_id);raise RuntimeError('SECRET NETWORK BODY')
    assert immediate(f.notifier.run_once(f.scope,unknown))['uncertain']==1
    restarted=PublicationNotifications(f.store,f.ledger,status_resolver=f.resolver,clock=lambda:f.now[0])
    assert immediate(restarted.run_once(f.scope,unknown))['sent']==0
    assert len(calls)==1 and restarted.snapshot()['counts']['uncertain']==1
    assert 'SECRET' not in json.dumps(restarted.snapshot())


def test_recover_expired_sending_claim_without_resend(fixture):
    f=fixture;add(f)
    # A status read may become unavailable between planning and sending.
    calls=[0]
    def transient(*args):
        calls[0]+=1
        return f.resolver(*args) if calls[0]==1 else None
    f.notifier.status_resolver=transient;run(f)
    assert f.notifier.snapshot()['counts']['pending']==1
    with f.store.connect() as db:
        db.execute("UPDATE telegram_publication_outbox SET state='sending',owner='crashed',lease_until=?",((f.now[0]-timedelta(seconds=1)).isoformat(),))
    f.notifier.status_resolver=f.resolver
    assert run(f)['sent']==0 and f.sent==[]
    assert f.notifier.snapshot()['counts']['uncertain']==1


def test_second_live_notifier_cannot_resend_an_active_claim(fixture):
    f=fixture;add(f)
    second=PublicationNotifications(f.store,f.ledger,status_resolver=f.resolver,clock=lambda:f.now[0])
    calls=[]
    def send(text,*,request_id):
        calls.append(request_id)
        assert immediate(second.run_once(f.scope,f.send))['sent']==0
        return {'message_id':999,'chat':{'id':-100}}
    assert immediate(f.notifier.run_once(f.scope,send))['sent']==1 and len(calls)==1


def test_bounded_candidate_cursor_reaches_tail_past_blocked_head(fixture):
    f=fixture
    for number in range(1,26):add(f,number)
    f.notifier.status_resolver=lambda queue,jid:f.resolver(queue,jid) if jid==25 else None
    results=[run(f,limit=5) for _ in range(5)]
    assert all(result['scanned']<=5 for result in results)
    assert len(f.sent)==1 and 'lịch #25' in f.sent[0]['text']


def test_bounded_pending_cursor_reaches_tail_when_old_statuses_unavailable(fixture):
    f=fixture
    for number in range(1,26):add(f,number)
    reads={}
    def transient(queue,jid):
        reads[jid]=reads.get(jid,0)+1
        return f.resolver(queue,jid) if reads[jid]==1 else None
    f.notifier.status_resolver=transient
    assert run(f,limit=100)['sent']==0 and f.notifier.snapshot()['counts']['pending']==25
    f.notifier.status_resolver=lambda queue,jid:f.resolver(queue,jid) if jid==25 else None
    for _ in range(5):run(f,limit=5)
    assert len(f.sent)==1 and 'lịch #25' in f.sent[0]['text']


def test_wrong_response_chat_is_uncertain_and_snapshot_is_bounded(fixture):
    f=fixture;add(f)
    async def wrong(text,*,request_id):return {'message_id':1,'chat':{'id':-999}}
    assert immediate(f.notifier.run_once(f.scope,wrong))['uncertain']==1
    snapshot=f.notifier.snapshot(f.scope,limit=1)
    assert snapshot['total']==1 and len(snapshot['items'])==1 and snapshot['counts']['uncertain']==1


def test_ack_local_time_includes_full_year_and_iana_zone(fixture):
    f=fixture
    f.now[0]=datetime(2030,12,31,23,30,tzinfo=timezone.utc)
    add(f);run(f)
    assert '31/12/2030 16:30 (America/Los_Angeles, UTC-08:00)' in f.sent[0]['text']


def test_disconnect_invalidates_never_sent_outbox_without_sending(fixture):
    f=fixture;add(f);calls=[0]
    def status(*args):
        calls[0]+=1
        return f.resolver(*args) if calls[0]==1 else None
    f.notifier.status_resolver=status;run(f)
    assert f.notifier.snapshot()['counts']['pending']==1
    with f.store.connect() as db:db.execute('UPDATE telegram_approval_settings SET encrypted_token=NULL,revision=3,policy_revision=2 WHERE id=1')
    run(f)
    assert f.sent==[] and f.notifier.snapshot()['counts']['invalidated']==1


def test_oversize_message_id_is_uncertain_without_overflow(fixture):
    f=fixture;add(f)
    async def invalid(text,*,request_id):return {'message_id':2**70,'chat':{'id':-100}}
    assert immediate(f.notifier.run_once(f.scope,invalid))['uncertain']==1
    assert f.notifier.snapshot()['counts']['uncertain']==1
