"""Confirmed preview reuse, synthetic rows and fake Telegram only."""
import asyncio
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from test_telegram_approval import f,enabled,CHAT


@pytest.fixture
def reuse(f):
    enabled(f);f.ledger.withdraw('token',1)
    old_binding={**f.binding,'job_id':10,'publish_mode':'on_approval',
        'scheduled_at':(f.now[0]-timedelta(hours=1)).isoformat(),
        'approve_by':(f.now[0]+timedelta(hours=5)).isoformat(),
        'review_batch':{'id':str(uuid4()),'expected_count':16}}
    old=f.ledger.get(f.ledger.ensure(old_binding)['request_id'])
    f.ledger.withdraw('token',10)
    new_binding={**old_binding,'job_id':20,'scheduled_at':f.now[0].isoformat(),
        'review_batch':{'id':str(uuid4()),'expected_count':15}}
    new=f.ledger.get(f.ledger.ensure(new_binding)['request_id'])
    with f.store.connect() as db:
        db.execute('CREATE TABLE reel_jobs(id INTEGER PRIMARY KEY,status TEXT,step TEXT,archived_at TEXT,checkpoint TEXT,published_url TEXT)')
        db.execute('INSERT INTO reel_jobs VALUES(?,?,?,?,?,?)',
            (10,'needs_review','stopped',f.now[0].isoformat(),json.dumps({'cancel_requested':True,'start_attempted':True,'uncertain':True}),None))
        db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
            (old['request_id'],old['binding_hash'],2,hashlib.sha256(b'old confirmed nonce').hexdigest(),
             'decided',CHAT,1000,1001,None,f.now[0].isoformat()))
    f.old,f.new=old,new;f.calls.clear();f.messages.clear()
    return f


def notify(f):
    row=f.service._row();assert f.service._lease(row)
    try:return asyncio.run(f.service._notify(row,f.new))
    finally:f.service._release()


def test_reuses_confirmed_same_media_preview_with_fresh_buttons_and_durable_provenance(reuse):
    f=reuse;assert notify(f) is True
    assert f.calls==['sendMessage'] and f.video_raw==[]
    body=f.messages[0]
    assert body['reply_to_message_id']==1000 and body['allow_sending_without_reply'] is False
    assert 'Duyệt lại cho lịch mới.' in body['text'] and 'Đăng ngay sau khi bạn duyệt' in body['text']
    with f.store.connect() as db:
        new=dict(db.execute('SELECT * FROM telegram_approval_delivery WHERE request_id=?',(f.new['request_id'],)).fetchone())
        old=dict(db.execute('SELECT * FROM telegram_approval_delivery WHERE request_id=?',(f.old['request_id'],)).fetchone())
        proof=dict(db.execute('SELECT * FROM telegram_approval_preview_reuse').fetchone())
    assert new['state']=='sent' and new['video_message_id']==old['video_message_id']==1000
    assert new['message_id']!=old['message_id'] and new['nonce_sha256']!=old['nonce_sha256']
    assert proof['source_request_id']==f.old['request_id'] and proof['request_id']==f.new['request_id']
    assert f.ledger.get(f.old['request_id'])['state']=='cancelled' and f.ledger.get(f.new['request_id'])['state']=='pending'
    assert notify(f) is False and f.calls==['sendMessage']


@pytest.mark.parametrize('change',['chat','policy','uncertain','missing_video','missing_buttons','not_cancelled',
    'cancel_missing','upload','finish','published','live_status','not_archived','not_stopped','delivery_hash','binding_hash'])
def test_ineligible_prior_receipt_falls_back_to_normal_upload(reuse,change):
    f=reuse
    with f.store.connect() as db:
        if change in ('chat','policy','uncertain','missing_video','missing_buttons','delivery_hash'):
            key,value={'chat':('chat_id','-100999'),'policy':('connection_revision',1),'uncertain':('state','uncertain'),
                'missing_video':('video_message_id',None),'missing_buttons':('message_id',None),'delivery_hash':('binding_hash','f'*64)}[change]
            db.execute('UPDATE telegram_approval_delivery SET '+key+'=? WHERE request_id=?',(value,f.old['request_id']))
        elif change in ('not_cancelled','binding_hash'):
            key,value=('state','approved') if change=='not_cancelled' else ('binding_hash','f'*64)
            db.execute('UPDATE publication_approval_requests SET '+key+'=? WHERE request_id=?',(value,f.old['request_id']))
        elif change in ('cancel_missing','upload','finish'):
            checkpoint={'cancel_requested':change!='cancel_missing'}
            if change=='upload':checkpoint['upload_attempted']=True
            if change=='finish':checkpoint['finish_ack']=True
            db.execute('UPDATE reel_jobs SET checkpoint=?',(json.dumps(checkpoint),))
        else:
            key,value={'published':('published_url','https://www.facebook.com/reel/12345'),
                'live_status':('status','scheduled'),'not_archived':('archived_at',None),'not_stopped':('step','review')}[change]
            db.execute('UPDATE reel_jobs SET '+key+'=?',(value,))
    assert notify(f) is True and f.calls==['sendVideo','sendMessage']
    assert 'reply_to_message_id' not in f.messages[0]
    with f.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM telegram_approval_preview_reuse').fetchone()[0]==0


@pytest.mark.parametrize('field,value',[('asset_id','b'*32),('asset_sha256','b'*64),('asset_size',999),
    ('page_id','99999'),('caption','Changed'),('actor_id',2),('actor_revision',2),('timezone','UTC'),
    ('approve_by','2030-01-01T06:00:00+00:00')])
def test_binding_changes_never_reuse_prior_preview(reuse,field,value):
    f=reuse;binding={**f.new['binding'],field:value}
    f.new=f.ledger.get(f.ledger.ensure(binding)['request_id'])
    with f.store.connect() as db:assert f.service._reusable_preview(db,f.service._row(),f.new) is None


def test_reused_button_send_uncertainty_is_held_without_reupload_or_repeat(reuse):
    f=reuse;f.options['fail']='sendMessage'
    with pytest.raises(AppError):notify(f)
    f.options.clear();assert notify(f) is False
    assert f.calls==['sendMessage'] and f.ledger.get(f.new['request_id'])['state']=='pending'
    with f.store.connect() as db:assert db.execute('SELECT state FROM telegram_approval_delivery WHERE request_id=?',(f.new['request_id'],)).fetchone()[0]=='uncertain'


def test_mutated_local_media_is_not_reused_or_uploaded(reuse):
    f=reuse;f.file.write_bytes(b'mutated')
    assert notify(f) is False and f.calls==[]
