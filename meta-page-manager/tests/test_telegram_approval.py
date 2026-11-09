"""Fake Telegram/SQLite/media fixtures only; no bot or real media approval."""
import asyncio
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from conftest import FixtureBox
from page_manager.errors import AppError
from page_manager.publication_approvals import PublicationApprovals
from page_manager.telegram_approval import TelegramApproval, _schedule_label

TOKEN='12345:FAKE_TELEGRAM_TOKEN_SECRET_123456'
CHAT='-100123';USER='99'


class Store:
    def __init__(self,path):self.path=path
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()


@pytest.fixture
def f(tmp_path):
    store=Store(tmp_path/'fixture.sqlite3');now=[datetime(2030,1,1,tzinfo=timezone.utc)]
    ledger=PublicationApprovals(store,clock=lambda:now[0]);ledger.configure(True,1)
    file=tmp_path/('a'*32+'.mp4');file.write_bytes(b'INERT VIDEO FIXTURE, NOT PLAYABLE MEDIA')
    binding={'queue':'token','job_id':1,'page_id':'12345','asset_id':'a'*32,
        'asset_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'asset_size':file.stat().st_size,
        'caption':'https://blueox179.com/campaign/inert-fixture','scheduled_at':(now[0]+timedelta(hours=1)).isoformat(),
        'timezone':'America/Los_Angeles','actor_id':1,'actor_revision':1}
    verdict=ledger.ensure(binding);ticket=ledger.get(verdict['request_id'])
    calls=[];messages=[];updates=[];options={};video_raw=[]
    async def handler(request):
        method=request.url.path.rsplit('/',1)[-1];calls.append(method)
        if options.get('fail')==method:raise httpx.ReadTimeout('FAKE '+TOKEN+' '+str(request.url),request=request)
        if method=='getMe':
            if options.get('invalid_token'):return httpx.Response(401,json={'ok':False,'description':TOKEN})
            result={'id':12345,'is_bot':True,'username':'fixture_bot'}
        elif method=='getWebhookInfo':result={'url':'https://foreign.example/private-secret' if options.get('webhook') else ''}
        elif method=='sendVideo':video_raw.append(request.content);result={'message_id':100,'chat':{'id':int(CHAT)}}
        elif method=='sendMessage':
            body=json.loads(request.content);messages.append(body)
            result={'message_id':100+len(messages),'chat':{'id':int(CHAT)}}
        elif method=='getUpdates':result=list(updates)
        elif method=='answerCallbackQuery':result=True
        else:raise AssertionError('Unexpected Bot API method')
        return httpx.Response(200,json={'ok':True,'result':result})
    service=TelegramApproval(store,FixtureBox(),ledger,transport=httpx.MockTransport(handler),
        media_resolver=lambda queue,asset:file,clock=lambda:now[0],send_interval=0)
    return SimpleNamespace(store=store,now=now,ledger=ledger,file=file,binding=binding,ticket=ticket,
        calls=calls,messages=messages,updates=updates,options=options,video_raw=video_raw,service=service)


def saved(f):
    return f.service.configure({'revision':1,'enabled':False,'bot_token':TOKEN,'chat_id':CHAT,'allowed_user_ids':[USER]})


def enabled(f):
    saved(f);asyncio.run(f.service.check(2))
    return f.service.configure({'revision':2,'enabled':True})


def callback(f,**changes):
    body=f.messages[-1];button=body['reply_markup']['inline_keyboard'][0][0]
    query={'id':'fake-query-id','from':{'id':int(USER),'is_bot':False},'message':{'message_id':100+len(f.messages),'chat':{'id':int(CHAT)}},'data':button['callback_data']}
    query.update(changes)
    return {'update_id':10,'callback_query':query}


def cycle(f):asyncio.run(f.service._cycle(f.service._row()))


def register_producer(f,tickets,*,batch_id=None,run_id=None):
    """Synthetic frozen producer rows; never real producer output or media QA."""
    run_id=run_id or str(uuid4());idea_id=str(uuid4());variants=[]
    for index,ticket in enumerate(tickets,1):
        variants.append({'variant_id':str(uuid4()),'reservation_id':str(uuid4()),'ordinal':index})
    payload={'variants':variants}
    if batch_id is not None:payload['automatic_batch']={'batch_id':batch_id}
    encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    with f.store.connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS reel_jobs(id INTEGER PRIMARY KEY,request_id TEXT);
            CREATE TABLE IF NOT EXISTS idea_pipeline_approved_videos(video_sha256 TEXT,variant_id TEXT,
                reservation_id TEXT,idea_id TEXT,run_id TEXT,status TEXT);
            CREATE TABLE IF NOT EXISTS idea_manual_runs(id TEXT PRIMARY KEY,idea_id TEXT,payload TEXT,payload_hash TEXT);''')
        db.execute('INSERT INTO idea_manual_runs VALUES(?,?,?,?)',(run_id,idea_id,encoded,hashlib.sha256(encoded.encode()).hexdigest()))
        for ticket,variant in zip(tickets,variants):
            db.execute('INSERT INTO reel_jobs VALUES(?,?)',(ticket['binding']['job_id'],variant['reservation_id']))
            db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?,?,?)',
                (ticket['binding']['asset_sha256'],variant['variant_id'],variant['reservation_id'],idea_id,run_id,'manual_review_pending'))
    return run_id


@pytest.mark.parametrize('instant,zone,label', [
    ('2026-11-05T14:00:00+00:00','America/Los_Angeles','05/11/2026 06:00 (America/Los_Angeles, UTC-08:00)'),
    ('2026-07-01T13:00:00Z','America/Los_Angeles','01/07/2026 06:00 (America/Los_Angeles, UTC-07:00)'),
    ('2027-01-01T02:00:00Z','America/Los_Angeles','31/12/2026 18:00 (America/Los_Angeles, UTC-08:00)'),
    ('2026-12-31T18:00:00Z','Asia/Ho_Chi_Minh','01/01/2027 01:00 (Asia/Ho_Chi_Minh, UTC+07:00)'),
    ('2026-11-01T08:30:00Z','America/Los_Angeles','01/11/2026 01:30 (America/Los_Angeles, UTC-07:00)'),
    ('2026-11-01T09:30:00Z','America/Los_Angeles','01/11/2026 01:30 (America/Los_Angeles, UTC-08:00)'),
    ('2026-11-05T00:00:00Z','Asia/Kathmandu','05/11/2026 05:45 (Asia/Kathmandu, UTC+05:45)'),
])
def test_schedule_label_uses_full_local_date_and_actual_dst_offset(instant,zone,label):
    assert _schedule_label(instant,zone)==label


@pytest.mark.parametrize('instant,zone',[('2026-11-05T14:00:00','UTC'),('bad','UTC'),('2026-11-05T14:00:00Z','Not/AZone')])
def test_schedule_label_never_falls_back_to_server_timezone(instant,zone):
    with pytest.raises((ValueError,KeyError)):_schedule_label(instant,zone)


def test_snapshots_configuration_and_disabled_tick_make_zero_network_calls(f):
    assert f.service.snapshot()['gate']['required'] is True
    value=saved(f);f.service.tick()
    assert value['enabled'] is False and value['token_saved'] is True and f.calls==[]
    assert TOKEN not in str(value)
    with f.store.connect() as db:
        encrypted=db.execute('SELECT encrypted_token FROM telegram_approval_settings').fetchone()[0]
    assert encrypted!=TOKEN and TOKEN not in encrypted


@pytest.mark.parametrize('body',[{'revision':True,'enabled':False},{'revision':1,'enabled':'true'},
    {'revision':1,'enabled':False,'chat_id':'@username'}, {'revision':1,'enabled':False,'allowed_user_ids':['0']},
    {'revision':1,'enabled':False,'allowed_user_ids':['99','99']},{'revision':1,'enabled':False,'other':'SECRET'}])
def test_invalid_configuration_fails_without_io_or_secret_echo(f,body):
    with pytest.raises(AppError):f.service.configure(body)
    assert f.calls==[]


def test_enable_requires_current_explicit_successful_check(f):
    saved(f)
    with pytest.raises(AppError,match='Telegram'):f.service.configure({'revision':2,'enabled':True})
    result=asyncio.run(f.service.check(2));assert result['check']['ok'] is True
    assert f.calls==['getMe','getWebhookInfo']
    f.now[0]+=timedelta(minutes=6)
    with pytest.raises(AppError):f.service.configure({'revision':2,'enabled':True})


@pytest.mark.parametrize('option,reason',[('webhook','webhook_active'),('invalid_token','invalid_token')])
def test_check_is_read_only_and_refuses_foreign_webhook_or_revoked_token(f,option,reason,caplog):
    saved(f);f.options[option]=True
    with caplog.at_level(logging.DEBUG):result=asyncio.run(f.service.check(2))
    assert result['check']['ok'] is False and result['check']['reason']==reason
    assert not set(f.calls)&{'sendVideo','sendMessage','getUpdates','deleteWebhook'}
    assert TOKEN not in str(result) and TOKEN not in caplog.text and 'private-secret' not in str(result)


def test_send_video_bytes_before_buttons_and_no_automatic_approval(f,caplog):
    frozen=f.ledger.get(f.ticket['request_id'])
    enabled(f);f.calls.clear()
    with caplog.at_level(logging.INFO):cycle(f)
    assert f.calls==['sendVideo','sendMessage','getUpdates']
    assert f.file.read_bytes() in f.video_raw[0]
    assert f.binding['caption'] in f.messages[0]['text']
    assert f.binding['asset_sha256'] in f.messages[0]['text'] and f.binding['page_id'] in f.messages[0]['text']
    local='Lịch: 31/12/2029 17:00 (America/Los_Angeles, UTC-08:00)'
    assert local in f.messages[0]['text'] and local.encode('utf-8') in f.video_raw[0]
    assert len(f.messages[0]['reply_markup']['inline_keyboard'][0][0]['callback_data'].encode())<=64
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'
    assert f.ledger.get(f.ticket['request_id'])==frozen
    assert TOKEN not in caplog.text


def test_invalid_schedule_display_is_blocked_before_delivery_latch_or_http(f,monkeypatch):
    enabled(f);f.calls.clear()
    def invalid(*args):raise ValueError('Synthetic formatting failure')
    monkeypatch.setattr('page_manager.telegram_approval._schedule_label',invalid)
    cycle(f)
    assert 'sendVideo' not in f.calls and 'sendMessage' not in f.calls
    assert f.service.snapshot()['uncertain_notifications']==0
    with f.store.connect() as db:
        assert db.execute('SELECT state FROM telegram_approval_delivery').fetchone()[0]=='blocked'


def test_valid_callback_is_durable_and_replays_cannot_decide_again(f):
    enabled(f);cycle(f);f.updates.append(callback(f));cycle(f);cycle(f)
    assert f.ledger.get(f.ticket['request_id'])['state']=='approved'
    with f.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM publication_approval_decisions').fetchone()[0]==1
        assert db.execute('SELECT next_offset FROM telegram_approval_settings').fetchone()[0]==11
    assert f.calls.count('sendVideo')==1 and f.calls.count('sendMessage')==1


@pytest.mark.parametrize('change',['user','chat','message','nonce','hash','expired','cancelled'])
def test_callback_identity_binding_expiry_and_cancellation_fail_closed(f,change):
    enabled(f);cycle(f);update=callback(f);query=update['callback_query']
    if change=='user':query['from']['id']=100
    if change=='chat':query['message']['chat']['id']=-999
    if change=='message':query['message']['message_id']=999
    if change=='nonce':query['data']='tg1:'+'z'*32+':a'
    if change=='hash':
        with f.store.connect() as db:db.execute("UPDATE telegram_approval_delivery SET binding_hash=?",('f'*64,))
    if change=='expired':f.now[0]+=timedelta(hours=2)
    if change=='cancelled':f.ledger.withdraw('token',1)
    f.updates.append(update);cycle(f)
    assert f.ledger.get(f.ticket['request_id'])['state']!='approved'
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM publication_approval_decisions').fetchone()[0]==0


@pytest.mark.parametrize('method',['sendVideo','sendMessage'])
def test_ambiguous_notification_is_held_and_never_automatically_sent_again(f,method):
    enabled(f);f.options['fail']=method;cycle(f)
    count=f.calls.count(method);f.options.clear();cycle(f);cycle(f)
    assert f.calls.count(method)==count
    assert f.service.snapshot()['uncertain_notifications']==1
    assert f.ledger.get(f.ticket['request_id'])['state']=='pending'


def test_mutated_media_never_sent_and_safe_preview_rejects(f):
    enabled(f);f.file.write_bytes(b'CHANGED');cycle(f)
    assert 'sendVideo' not in f.calls and 'sendMessage' not in f.calls
    with pytest.raises(AppError):f.service.preview_path(f.ticket['request_id'])


def test_known_sent_first_request_does_not_starve_next_pending_ticket(f):
    enabled(f);cycle(f)
    f.ledger.ensure({**f.binding,'job_id':2,'page_id':'54321'})
    cycle(f)
    assert f.calls.count('sendVideo')==2


def test_rotating_window_reaches_ticket_101_without_resending_first_100(f):
    enabled(f)
    for number in range(2,102):f.ledger.ensure({**f.binding,'job_id':number})
    register_producer(f,f.ledger.pending(limit=100)['items']+f.ledger.pending(limit=100,offset=100)['items'])
    tickets=f.ledger.pending(limit=100,offset=0)['items']
    row=f.service._row()
    with f.store.connect() as db:
        for index,ticket in enumerate(tickets):
            db.execute('INSERT INTO telegram_approval_delivery(request_id,binding_hash,connection_revision,nonce_sha256,state,chat_id,video_message_id,message_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (ticket['request_id'],ticket['binding_hash'],row['policy_revision'],hashlib.sha256(str(index).encode()).hexdigest(),
                 'sent',CHAT,1000+index,2000+index,f.now[0].isoformat()))
    cycle(f)
    assert f.service._notification_offset==100 and 'sendVideo' not in f.calls
    cycle(f)
    assert f.calls.count('sendVideo')==1
    single_messages=[message for message in f.messages if message['reply_markup']['inline_keyboard'][0][0]['callback_data'].startswith('tg1:')]
    batch_messages=[message for message in f.messages if message['reply_markup']['inline_keyboard'][0][0]['callback_data'].startswith('tgb1:')]
    assert len(single_messages)==1 and len(batch_messages)==2
    assert f.service._notification_offset==0
    with f.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM telegram_approval_delivery WHERE state='sent'").fetchone()[0]==101
    cycle(f);assert f.calls.count('sendVideo')==1


def test_rotating_window_recovers_when_pending_set_shrinks(f):
    enabled(f);f.service._notification_offset=100
    cycle(f)
    assert f.calls.count('sendVideo')==1 and f.service._notification_offset==0


def test_toggle_enabled_preserves_known_message_binding_and_rejects_config_race(f):
    enabled(f);cycle(f)
    f.service.configure({'revision':3,'enabled':False})
    f.service.configure({'revision':4,'enabled':True})
    f.updates.append(callback(f));cycle(f)
    assert f.ledger.get(f.ticket['request_id'])['state']=='approved'
    with pytest.raises(AppError):f.service.configure({'revision':3,'enabled':False})


def test_full_long_caption_is_sent_in_order_before_buttons(f):
    f.binding['caption']='a'*2500+'b'*2500
    f.ledger.withdraw('token',1)
    f.ledger.ensure({**f.binding,'job_id':2})
    enabled(f);cycle(f)
    assert len(f.messages)==3
    pieces=[body['text'].split('\n',1)[1] for body in f.messages[:-1]]
    assert ''.join(pieces)==f.binding['caption']
    assert 'reply_markup' not in f.messages[0] and 'reply_markup' in f.messages[-1]


def test_single_consumer_lease_and_owned_shutdown(f):
    enabled(f);row=f.service._row();assert f.service._lease(row)
    other=TelegramApproval(f.store,FixtureBox(),f.ledger,transport=httpx.MockTransport(lambda _:None),clock=lambda:f.now[0])
    assert other._lease(row) is False
    asyncio.run(f.service.shutdown());assert other._lease(row) is True


def test_disconnect_removes_saved_token_without_external_calls(f):
    saved(f);result=f.service.disconnect(2)
    assert result['token_saved'] is False and result['enabled'] is False and result['allowed_user_ids']==[]
    assert f.calls==[]


def test_configuration_changed_during_check_cannot_commit_stale_identity(f):
    saved(f)
    async def race():
        started=asyncio.Event();release=asyncio.Event()
        async def handler(request):
            started.set();await release.wait()
            return httpx.Response(200,json={'ok':True,'result':{'id':12345,'is_bot':True,'username':'fixture_bot'}})
        f.service.transport=httpx.MockTransport(handler)
        task=asyncio.create_task(f.service.check(2));await started.wait()
        f.service.disconnect(2);release.set()
        with pytest.raises(AppError) as error:await task
        assert error.value.code=='telegram_configuration_changed'
    asyncio.run(race())
    assert f.service.snapshot()['check']['ok'] is False


def test_shutdown_cancels_only_owned_long_poll_and_releases_lease(f):
    enabled(f);f.ledger.withdraw('token',1)
    async def run():
        started=asyncio.Event();cancelled=[]
        async def handler(request):
            assert request.url.path.endswith('/getUpdates');started.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:cancelled.append(True);raise
        f.service.transport=httpx.MockTransport(handler)
        f.service.tick();await started.wait();await f.service.shutdown()
        assert cancelled==[True] and f.service._task.done()
    asyncio.run(run())
    assert f.service._row()['lease_owner'] is None


@pytest.mark.parametrize('raw',[b'{"ok":true,"ok":true,"result":{}}',b'{"ok":true,"result":NaN}'])
def test_malformed_bot_json_never_becomes_a_successful_check(f,raw):
    saved(f);f.service.transport=httpx.MockTransport(lambda _:httpx.Response(200,content=raw))
    result=asyncio.run(f.service.check(2))
    assert result['check']['ok'] is False and result['check']['reason']=='invalid_response'
