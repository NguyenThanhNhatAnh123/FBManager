"""Private Telegram review transport. It records decisions; it never publishes.

Construction, snapshots and configuration perform no network IO. Explicit
checks, authorized reviews and receipts for decided tickets use the owned client.
"""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx

from .errors import AppError
from .publication_approvals import normalize_binding,review_deadline,review_deadline_passed,review_is_open,REMIND_UNTIL_DECISION
from .telegram_approval_batches import TelegramApprovalBatches
from .telegram_approval_cohorts import TelegramApprovalCohorts
from .telegram_publication_notifications import PublicationNotifications
from .telegram_approval_reminders import TelegramApprovalReminders

API = 'https://api.telegram.org'
MAX_RESPONSE = 512 * 1024
MAX_VIDEO = 50 * 1024 * 1024
CHECK_TTL = 300
TRIAL_TTL = 1800
METHODS = frozenset(('getMe', 'getWebhookInfo', 'getUpdates', 'sendVideo', 'sendMessage', 'answerCallbackQuery'))
REASONS = frozenset(('credentials_required', 'configuration_incomplete', 'not_checked', 'ready',
    'invalid_token', 'webhook_active', 'consumer_conflict', 'network_unavailable', 'check_timeout',
    'invalid_response', 'configuration_changed', 'storage_unavailable', 'media_unavailable',
    'media_changed', 'media_too_large', 'caption_too_long', 'notification_uncertain',
    'approval_unavailable', 'check_in_progress', 'trial_in_progress', 'trial_requires_disabled', 'trial_unavailable','cohort_in_progress'))


class _TokenFilter(logging.Filter):
    def filter(self, record):
        # HTTPX includes the bot token in its INFO request URL; httpcore can
        # include the same path in DEBUG. Never let those strings reach a sink.
        message = record.getMessage()
        redacted = re.sub(r'(?i)(https://api\.telegram\.org/bot)[^/\s\"\']+', r'\1[redacted]', message)
        redacted = re.sub(r'bot[0-9]+:[A-Za-z0-9_-]+', 'bot[redacted]', redacted)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


_FILTER = _TokenFilter()
for _logger in ('httpx', 'httpcore.http11', 'httpcore.http2', 'httpcore.connection', 'httpcore.proxy'):
    logging.getLogger(_logger).addFilter(_FILTER)


def _now(): return datetime.now(timezone.utc)
def _json(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
def _hash(value): return hashlib.sha256(value).hexdigest()
def _fail(reason, status=409):
    raise AppError('Chưa sẵn sàng duyệt qua Telegram. Đọc lại trạng thái và kiểm tra cấu hình.',
                   'telegram_' + (reason if reason in REASONS else 'invalid_response'), status) from None


def _identity(value, *, chat=False):
    if (not isinstance(value, str) or not re.fullmatch(r'-?[1-9][0-9]{0,15}' if chat else r'[1-9][0-9]{0,15}', value)
            or abs(int(value)) > 2**52 - 1):
        _fail('configuration_incomplete', 422)
    return value


def _moment(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError()
    return parsed.astimezone(timezone.utc)


def _schedule_label(value, zone):
    """Render the frozen instant in its schedule zone, with an explicit year."""
    local = _moment(value).astimezone(ZoneInfo(zone))
    minutes = int(local.utcoffset().total_seconds()) // 60
    sign = '+' if minutes >= 0 else '-'
    hours, remainder = divmod(abs(minutes), 60)
    return (f'{local.day:02}/{local.month:02}/{local.year:04} {local.hour:02}:{local.minute:02} '
            f'({zone}, UTC{sign}{hours:02}:{remainder:02})')


def _review_schedule(binding):
    if binding.get('publish_mode')=='on_approval':
        if binding.get('approval_policy')==REMIND_UNTIL_DECISION:return 'Đăng ngay sau khi bạn duyệt · Duyệt không hết hạn'
        return 'Đăng ngay sau khi bạn duyệt · Hạn duyệt: '+_schedule_label(binding['approve_by'],binding['timezone'])
    return 'Lịch: '+_schedule_label(binding['scheduled_at'],binding['timezone'])


def _file(path, expected_hash, expected_size):
    path = Path(path)
    if not path.is_absolute(): _fail('media_unavailable')
    try:
        for part in (path, *path.parents):
            info = part.lstat()
            if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400: _fail('media_unavailable')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != expected_size: _fail('media_changed')
        if not 0 < info.st_size <= MAX_VIDEO: _fail('media_too_large')
        digest = hashlib.sha256()
        with path.open('rb') as source:
            before = os.fstat(source.fileno())
            while block := source.read(1024 * 1024): digest.update(block)
            after = os.fstat(source.fileno())
        signature = lambda m:(m.st_dev,m.st_ino,m.st_size,m.st_mtime_ns)
        if signature(before) != signature(after) or signature(path.stat()) != signature(before) or digest.hexdigest() != expected_hash:
            _fail('media_changed')
        return path
    except OSError: _fail('media_unavailable')


class TelegramApproval:
    def __init__(self, store, secret_box, ledger, *, transport=None, media_resolver=None, status_resolver=None, clock=None, send_interval=3.1):
        self.store, self.secrets, self.ledger = store, secret_box, ledger
        self.transport, self.media_resolver, self.clock = transport, media_resolver, clock or _now
        self.owner = uuid4().hex
        self.send_interval, self._last_send = send_interval, 0.0
        self._task = None; self._checking = False; self._closing = False; self._retry_at = None
        self._notification_offset = 0
        self.status_resolver=status_resolver;self._notice_scan_at=0.0
        with store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS telegram_approval_settings(
                id INTEGER PRIMARY KEY CHECK(id=1),encrypted_token TEXT,enabled INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1,policy_revision INTEGER NOT NULL DEFAULT 1,chat_id TEXT,allowed_users TEXT NOT NULL DEFAULT '[]',
                checked_revision INTEGER,checked_at TEXT,checked_ok INTEGER,bot_id TEXT,bot_username TEXT,
                reason TEXT NOT NULL DEFAULT 'credentials_required',next_offset INTEGER NOT NULL DEFAULT 0,
                last_poll_at TEXT,lease_owner TEXT,lease_until TEXT);
              INSERT OR IGNORE INTO telegram_approval_settings(id) VALUES(1);
              CREATE TABLE IF NOT EXISTS telegram_approval_delivery(
                request_id TEXT NOT NULL,binding_hash TEXT NOT NULL,connection_revision INTEGER NOT NULL,
                nonce_sha256 TEXT NOT NULL UNIQUE,state TEXT NOT NULL,chat_id TEXT NOT NULL,
                video_message_id INTEGER,message_id INTEGER,reason TEXT,updated_at TEXT NOT NULL,
                PRIMARY KEY(request_id,connection_revision));
              CREATE TABLE IF NOT EXISTS telegram_approval_updates(
                bot_id TEXT NOT NULL,update_id INTEGER NOT NULL,outcome TEXT NOT NULL,
                PRIMARY KEY(bot_id,update_id));
              CREATE TABLE IF NOT EXISTS telegram_approval_trial(
                id INTEGER PRIMARY KEY CHECK(id=1),request_id TEXT NOT NULL,revision INTEGER NOT NULL,
                expires_at TEXT NOT NULL,state TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_approval_preview_reuse(
                request_id TEXT NOT NULL,connection_revision INTEGER NOT NULL,
                source_request_id TEXT NOT NULL,video_message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,PRIMARY KEY(request_id,connection_revision));
            ''')
        self.cohorts=TelegramApprovalCohorts(store,ledger,clock=self.clock,status_resolver=status_resolver)
        self.batches=TelegramApprovalBatches(store,ledger,clock=self.clock,cohorts=self.cohorts,group_resolver=self._review_group)
        self.notifications=(PublicationNotifications(store,ledger,status_resolver=status_resolver,clock=self.clock)
            if callable(status_resolver) else None)
        self.reminders=TelegramApprovalReminders(store,ledger,self.batches,clock=self.clock,
            status_resolver=lambda queue,job:self.status_resolver(queue,job) if callable(self.status_resolver) else None)

    def _row(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM telegram_approval_settings WHERE id=1').fetchone())

    def snapshot(self):
        row = self._row(); configured = bool(row['encrypted_token'] and row['chat_id'] and json.loads(row['allowed_users']))
        checked = row['checked_revision'] == row['revision'] and row['checked_ok'] == 1
        expires = (_moment(row['checked_at']) + timedelta(seconds=CHECK_TTL)).isoformat() if row['checked_at'] else None
        pending = self.ledger.pending(limit=20,offset=0)
        with self.store.connect() as db:
            sending = db.execute("SELECT COUNT(*) FROM telegram_approval_delivery WHERE state IN ('sending_video','sending_buttons')").fetchone()[0]
            unknown = db.execute("SELECT COUNT(*) FROM telegram_approval_delivery WHERE state='uncertain'").fetchone()[0]
        lease_active=bool(row['lease_owner'] and row['lease_until'] and self.clock()<_moment(row['lease_until']))
        if not lease_active:unknown,sending=unknown+sending,0
        return {'enabled':row['enabled'] == 1,'configured':configured,'token_saved':bool(row['encrypted_token']),
            'revision':row['revision'],'chat_id':row['chat_id'],'allowed_user_ids':json.loads(row['allowed_users']),
            'bot':{'id':row['bot_id'],'username':row['bot_username']},
            'check':{'ok':checked,'status':'checking' if self._checking else 'ready' if checked else 'not_checked',
                'reason':row['reason'] if row['reason'] in REASONS else 'invalid_response',
                'checked_at':row['checked_at'],'expires_at':expires},
            'polling':{'active':bool(self._task and not self._task.done()),'last_poll_at':row['last_poll_at']},
            'gate':self.ledger.settings(),'pending':pending,'uncertain_notifications':unknown,'sending_notifications':sending,'trial':self._trial_info(row),
            'batches':{'items':self.batches.recent(limit=10),'total':self._batch_count()},
            'notifications':self.notifications.snapshot(self._notice_scope(row),limit=10) if self.notifications else {'counts':{},'items':[],'total':0},
            'cohort':self.cohorts.snapshot(),'reminders':self.reminders.snapshot(row)}

    def _batch_count(self):
        with self.store.connect() as db:return db.execute('SELECT COUNT(*) FROM telegram_approval_batches').fetchone()[0]

    @staticmethod
    def _notice_scope(row):
        return {key:row[key] for key in ('revision','policy_revision','chat_id','bot_id')}

    def _notification_current(self, revision, request_id):
        row=self._current(revision)
        if row['checked_ok']!=1 or row['checked_revision']!=revision:_fail('not_checked')
        with self.store.connect() as db:
            valid=db.execute('''SELECT 1 FROM telegram_approval_delivery d
                JOIN publication_approval_requests r ON r.request_id=d.request_id AND r.binding_hash=d.binding_hash
                WHERE d.request_id=? AND d.connection_revision=? AND d.chat_id=? AND d.state IN ('sent','decided')
                AND EXISTS(SELECT 1 FROM publication_approval_decisions x WHERE x.request_id=r.request_id AND x.binding_hash=r.binding_hash)''',
                (request_id,row['policy_revision'],row['chat_id'])).fetchone()
        if valid is None:_fail('configuration_changed')
        return row

    def _batch_current(self, revision, batch_id,*,cohort_id=None):
        row=self._current(revision,enabled=cohort_id is None)
        gate=self.ledger.settings()
        with self.store.connect() as db:
            batch=db.execute('SELECT * FROM telegram_approval_batches WHERE batch_id=?',(batch_id,)).fetchone()
            if (not batch or batch['cohort_id']!=cohort_id or batch['state']!='sending' or batch['revision']!=revision or batch['policy_revision']!=row['policy_revision']
                    or batch['chat_id']!=row['chat_id']
                    or row['checked_ok']!=1 or row['checked_revision']!=revision
                    or gate['gate_revision']!=batch['gate_revision']):_fail('configuration_changed')
            self.batches._validate(db,batch)
        if cohort_id is not None:self._cohort_current(revision,cohort_id)
        return row

    def _cohort_current(self,revision,cohort_id,*,request_id=None,binding_hash=None):
        row=self._current(revision)
        self.cohorts.validate(cohort_id,revision=revision,policy_revision=row['policy_revision'],chat_id=row['chat_id'],
            request_id=request_id,binding_hash=binding_hash)
        return row

    def start_cohort(self,body):
        self.cohorts.start(body,verify_media=self.preview_path)
        return self.snapshot()

    async def _run_notifications(self,row):
        if self.notifications is None:return
        async def send(text,*,request_id):
            return await self._request(row,'sendMessage',{'chat_id':row['chat_id'],'text':text},polling=True,notification_request_id=request_id)
        try:await self.notifications.run_once(self._notice_scope(row),send,limit=3)
        except Exception:pass  # Delivery problems must never undo approval or publication.

    async def _run_reminders(self,row):
        async def send(body,*,reminder_id):
            return await self._request(row,'sendMessage',body,polling=True,reminder_id=reminder_id)
        try:await self.reminders.run_once(row,send,limit=1)
        except Exception:pass  # Never interrupt callback polling or publication receipts.

    async def _notice_cycle(self,row):
        if not self._lease(row):return
        try:await self._run_notifications(row)
        finally:self._release()

    async def _notify_batch(self,row,*,cohort_id=None):
        # Normal delivery groups only confirmed previews from one trusted
        # producer batch/run; unrelated pending work never delays a review.
        if cohort_id is None:ids=self.batches.candidates(row['policy_revision'],row['chat_id'],limit=20)
        else:
            cohort=self.cohorts.validate(cohort_id,revision=row['revision'])
            pending={'items':[self.ledger.get(member['request_id']) for member in cohort['members']]}
            with self.store.connect() as db:
                for ticket in pending['items']:
                    delivery=db.execute('SELECT state FROM telegram_approval_delivery WHERE request_id=? AND connection_revision=?',
                        (ticket['request_id'],row['policy_revision'])).fetchone()
                    if not delivery or delivery['state'] not in ('sent','decided'):return
            ids=[ticket['request_id'] for ticket in pending['items'] if ticket['state']=='pending']
        if len(ids)<2:return
        batch=self.batches.freeze(ids,revision=row['revision'],policy_revision=row['policy_revision'],chat_id=row['chat_id'],cohort_id=cohort_id)
        lines=[f"Nhóm {len(batch['members'])} video chờ duyệt",'Xem các video và caption đã gửi ở trên. Nút dưới chỉ duyệt đúng danh sách này:']
        for index,member in enumerate(batch['members'],1):
            binding=member['binding']
            lines.append(f"{index}. Page {binding['page_id']} · {_review_schedule(binding)}\nVideo {binding['asset_sha256'][:12]}")
        lines.append('Video thêm sau không thuộc nhóm này. Via kiểm tra lại điều kiện trước khi gửi đăng theo đúng chế độ và lịch ở trên.')
        body={'chat_id':row['chat_id'],'text':'\n\n'.join(lines),
            'reply_markup':{'inline_keyboard':[[{'text':f"Duyệt toàn bộ ({len(ids)} video)",'callback_data':'tgb1:'+batch['nonce']+':a'}]]}}
        self.batches.begin_send(batch['batch_id'])
        try:
            sent=await self._request(row,'sendMessage',body,polling=True,batch_id=batch['batch_id'],cohort_id=cohort_id)
            self.batches.sent(batch['batch_id'],self._message(sent,row['chat_id']))
        except BaseException:
            self.batches.uncertain(batch['batch_id']);raise

    def _review_group(self,binding,db):
        """Resolve producer scope through frozen local registry, never a name."""
        if 'review_batch' in binding:
            batch=binding['review_batch']
            try:valid_id=isinstance(batch,dict) and isinstance(batch.get('id'),str) and str(UUID(batch['id']))==batch['id'] and UUID(batch['id']).int!=0
            except (ValueError,TypeError,AttributeError):return None
            if (not isinstance(batch,dict) or set(batch)!={'id','expected_count'}
                    or not valid_id
                    or type(batch['expected_count']) is not int or not 2<=batch['expected_count']<=20
                    or not callable(self.status_resolver)):return None
            try:current=self.status_resolver(binding['queue'],binding['job_id'])
            except Exception:return None
            if (not isinstance(current,dict) or current.get('binding')!=binding
                    or current.get('status') not in ('scheduled','waiting_page','waiting_profile') or current.get('published_url')):return None
            return f"review:{batch['id']}:{batch['expected_count']}:{binding.get('publish_mode','scheduled')}"
        if binding['queue']!='token':return None
        try:
            row=db.execute('''SELECT a.run_id,a.idea_id,a.variant_id,a.reservation_id,a.video_sha256,
                r.idea_id AS run_idea_id,r.payload,r.payload_hash
                FROM reel_jobs j JOIN idea_pipeline_approved_videos a ON a.reservation_id=j.request_id
                JOIN idea_manual_runs r ON r.id=a.run_id
                WHERE j.id=? AND a.status IN ('manual_review_pending','ready')''',(binding['job_id'],)).fetchone()
            if (row is None or row['video_sha256']!=binding['asset_sha256'] or row['idea_id']!=row['run_idea_id']
                    or not isinstance(row['payload'],str) or len(row['payload'].encode('utf-8'))>128*1024):return None
            payload=json.loads(row['payload'])
            encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
            if hashlib.sha256(encoded.encode('utf-8')).hexdigest()!=row['payload_hash']:return None
            if (not isinstance(payload,dict) or not isinstance(payload.get('variants'),list)
                    or sum(isinstance(v,dict) and v.get('variant_id')==row['variant_id']
                        and v.get('reservation_id')==row['reservation_id'] for v in payload['variants'])!=1):return None
            batch=payload.get('automatic_batch')
            value=batch.get('batch_id') if isinstance(batch,dict) else row['run_id'] if batch is None else None
            if not isinstance(value,str) or str(UUID(value))!=value or not UUID(value).int:return None
            return ('auto:' if batch is not None else 'run:')+value
        except (sqlite3.Error,ValueError,TypeError,AttributeError):return None

    def _trial_info(self, row):
        with self.store.connect() as db:
            trial=db.execute('SELECT * FROM telegram_approval_trial WHERE id=1').fetchone()
            if trial is None:return None
            value=dict(trial)
            delivery=db.execute('SELECT state,reason FROM telegram_approval_delivery WHERE request_id=? AND connection_revision=?',
                (value['request_id'],row['policy_revision'])).fetchone()
        active=(value['state']=='active' and value['revision']==row['revision'] and row['enabled']==0
            and row['checked_ok']==1 and row['checked_revision']==row['revision'] and self.clock()<_moment(value['expires_at']))
        value.update(active=active,delivery_state=delivery['state'] if delivery else None,reason=delivery['reason'] if delivery else None)
        if value['state']=='active' and not active:value['state']='expired' if self.clock()>=_moment(value['expires_at']) else 'configuration_changed'
        return {key:value[key] for key in ('request_id','expires_at','state','active','delivery_state','reason')}

    def start_trial(self, revision, request_id):
        """Authorize one exact pending ticket, without enabling global notifications."""
        row=self._current(revision)
        if row['enabled']!=0:_fail('trial_requires_disabled')
        cohort=self.cohorts.snapshot()
        if cohort and cohort['active']:_fail('cohort_in_progress')
        if (not row['encrypted_token'] or not row['chat_id'] or not json.loads(row['allowed_users'])
                or row['checked_ok']!=1 or row['checked_revision']!=revision or not row['checked_at']
                or self.clock()>=_moment(row['checked_at'])+timedelta(seconds=CHECK_TTL)):_fail('not_checked')
        ticket=self.ledger.get(request_id)
        if not review_is_open(ticket,self.clock()):_fail('trial_unavailable')
        deadline=review_deadline(ticket)
        expires=min(self.clock()+timedelta(seconds=TRIAL_TTL),deadline or self.clock()+timedelta(seconds=TRIAL_TTL)).isoformat()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT revision FROM telegram_approval_settings WHERE id=1').fetchone()[0]!=revision:_fail('configuration_changed')
            for scoped in db.execute("SELECT * FROM telegram_approval_cohorts WHERE state='active'").fetchall():
                if self.cohorts._view(db,scoped)['active']:_fail('cohort_in_progress')
            prior=db.execute('SELECT * FROM telegram_approval_trial WHERE id=1').fetchone()
            if prior and prior['revision']==revision and prior['request_id']==request_id:
                pass  # A replay neither sends again nor extends the listening window.
            elif prior and prior['revision']==revision and prior['state']=='active' and self.clock()<_moment(prior['expires_at']):
                _fail('trial_in_progress')
            else:
                db.execute('INSERT OR REPLACE INTO telegram_approval_trial VALUES(1,?,?,?,?)',(request_id,revision,expires,'active'))
        return self.snapshot()

    def _trial_current(self, revision, request_id):
        row=self._current(revision)
        trial=self._trial_info(row)
        if not trial or not trial['active'] or trial['request_id']!=request_id:_fail('trial_unavailable')
        return row

    def _finish_trial(self, revision, request_id, state):
        with self.store.connect() as db:
            db.execute("UPDATE telegram_approval_trial SET state=? WHERE id=1 AND revision=? AND request_id=? AND state='active'",
                (state,revision,request_id))

    def configure(self, body):
        if not isinstance(body,dict) or set(body)-{'revision','enabled','bot_token','chat_id','allowed_user_ids'}:
            _fail('configuration_incomplete',422)
        revision, enabled = body.get('revision'), body.get('enabled')
        if type(revision) is not int or revision < 1 or type(enabled) is not bool: _fail('configuration_incomplete',422)
        row = self._row()
        if row['revision'] != revision: _fail('configuration_changed')
        chat = body.get('chat_id', row['chat_id'])
        if chat is not None: _identity(chat,chat=True)
        users = body.get('allowed_user_ids',json.loads(row['allowed_users']))
        if not isinstance(users,list) or len(users)>20 or any(not isinstance(v,str) for v in users) or len(set(users))!=len(users):
            _fail('configuration_incomplete',422)
        users = sorted(_identity(value) for value in users)
        token = body.get('bot_token',None)
        encrypted = row['encrypted_token']
        token_changed = 'bot_token' in body
        if token_changed:
            if token is None: encrypted = None
            elif (not isinstance(token,str) or not re.fullmatch(r'[1-9][0-9]{0,19}:[A-Za-z0-9_-]{20,160}',token)):
                _fail('configuration_incomplete',422)
            else:
                try: encrypted = self.secrets.encrypt(token)
                except Exception: _fail('storage_unavailable',503)
                if not isinstance(encrypted,str) or encrypted == token: _fail('storage_unavailable',503)
        changed = token_changed or chat != row['chat_id'] or _json(users) != row['allowed_users']
        if enabled and (changed or not encrypted or not chat or not users or row['checked_ok'] != 1
                or row['checked_revision'] != revision or not row['checked_at']
                or self.clock() >= _moment(row['checked_at'])+timedelta(seconds=CHECK_TTL)):
            _fail('not_checked')
        with self.store.connect() as db:
            result = db.execute('''UPDATE telegram_approval_settings SET encrypted_token=?,enabled=?,chat_id=?,allowed_users=?,
                revision=revision+1,policy_revision=policy_revision+?,checked_revision=?,checked_at=?,checked_ok=?,bot_id=?,bot_username=?,reason=?,next_offset=?
                WHERE id=1 AND revision=?''',(encrypted,int(enabled),chat,_json(users),
                int(changed),None if changed else revision+1,None if changed else row['checked_at'],None if changed else row['checked_ok'],
                None if token_changed else row['bot_id'],None if token_changed else row['bot_username'],
                'credentials_required' if not encrypted else 'not_checked' if changed else row['reason'],
                0 if token_changed else row['next_offset'],revision))
            if result.rowcount != 1: _fail('configuration_changed')
        if self._task and not self._task.done(): self._task.cancel()
        return self.snapshot()

    def disconnect(self, revision):
        return self.configure({'revision':revision,'enabled':False,'bot_token':None,'chat_id':None,'allowed_user_ids':[]})

    def preview_path(self, request_id):
        ticket=self.ledger.get(request_id);binding=ticket['binding']
        if not callable(self.media_resolver):_fail('media_unavailable')
        return _file(self.media_resolver(binding['queue'],binding['asset_id']),binding['asset_sha256'],binding['asset_size'])

    def _current(self, revision, *, enabled=False):
        row = self._row()
        if self._closing or row['revision'] != revision or enabled and row['enabled'] != 1: _fail('configuration_changed')
        return row

    async def _request(self, row, method, body=None, *, files=None, polling=False, trial_request_id=None, notification_request_id=None, batch_id=None,cohort_id=None,cohort_member_id=None,preview_reuse_id=None,reminder_id=None):
        if method not in METHODS: _fail('invalid_response')
        if sum(bool(value) for value in (trial_request_id,notification_request_id,batch_id))>1:_fail('invalid_response')
        if cohort_id is not None and (trial_request_id or notification_request_id
                or method not in ('sendVideo','sendMessage','getUpdates','answerCallbackQuery')
                or method in ('sendVideo','sendMessage') and not (batch_id or cohort_member_id)):_fail('invalid_response')
        if cohort_member_id is not None and cohort_id is None:_fail('invalid_response')
        if (notification_request_id or batch_id) and method!='sendMessage':_fail('invalid_response')
        if preview_reuse_id is not None and method!='sendMessage':_fail('invalid_response')
        if reminder_id is not None and (method!='sendMessage' or not polling or trial_request_id or cohort_id or notification_request_id or batch_id or preview_reuse_id):_fail('invalid_response')
        if method in ('sendVideo','sendMessage'):
            await asyncio.sleep(max(0,self._last_send+self.send_interval-time.monotonic()))
            self._last_send=time.monotonic()
        def current():
            if reminder_id is not None:self.reminders.validate_attempt(row,reminder_id)
            if preview_reuse_id is not None:self._reuse_current(row,preview_reuse_id)
            if notification_request_id:return self._notification_current(row['revision'],notification_request_id)
            if batch_id:return self._batch_current(row['revision'],batch_id,cohort_id=cohort_id)
            if trial_request_id:return self._trial_current(row['revision'],trial_request_id)
            if cohort_id:return self._cohort_current(row['revision'],cohort_id,request_id=cohort_member_id)
            return self._current(row['revision'],enabled=polling)
        current()
        if polling:
            with self.store.connect() as db:
                updated=db.execute('UPDATE telegram_approval_settings SET lease_until=? WHERE id=1 AND revision=? AND lease_owner=?',
                    ((self.clock()+timedelta(seconds=120)).isoformat(),row['revision'],self.owner))
                if updated.rowcount!=1:_fail('consumer_conflict')
        try: token = self.secrets.decrypt(row['encrypted_token'])
        except Exception: _fail('storage_unavailable',503)
        try:
            async with httpx.AsyncClient(transport=self.transport,trust_env=False,follow_redirects=False,
                    timeout=httpx.Timeout(45 if files else 30,connect=5)) as client:
                async with client.stream('POST',API+'/bot'+token+'/'+method,
                        json=body or {} if files is None else None,data=body if files else None,files=files) as response:
                    if response.status_code == 401: _fail('invalid_token')
                    if response.status_code == 409: _fail('consumer_conflict')
                    if response.status_code == 429: _fail('network_unavailable')
                    if response.status_code != 200: _fail('invalid_response')
                    raw=bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw)>MAX_RESPONSE: _fail('invalid_response')
            def pairs(items):
                result={}
                for key,value in items:
                    if key in result:raise ValueError()
                    result[key]=value
                return result
            value=json.loads(bytes(raw),object_pairs_hook=pairs,
                parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(value,dict) or value.get('ok') is not True or 'result' not in value: _fail('invalid_response')
            current()
            return value['result']
        except AppError: raise
        except httpx.TimeoutException: _fail('check_timeout')
        except httpx.HTTPError: _fail('network_unavailable')
        except (ValueError,UnicodeError): _fail('invalid_response')

    async def check(self, revision):
        if self._checking: _fail('check_in_progress')
        row=self._current(revision)
        if not row['encrypted_token']: _fail('credentials_required')
        self._checking=True; reason='ready'; bot=None
        try:
            bot=await self._request(row,'getMe')
            if (not isinstance(bot,dict) or bot.get('is_bot') is not True or type(bot.get('id')) is not int
                    or not 0<bot['id']<2**52 or not isinstance(bot.get('username'),str)
                    or re.fullmatch(r'[A-Za-z0-9_]{1,64}',bot['username']) is None): _fail('invalid_response')
            webhook=await self._request(row,'getWebhookInfo')
            if not isinstance(webhook,dict) or not isinstance(webhook.get('url'),str): _fail('invalid_response')
            if webhook['url']: _fail('webhook_active')
        except AppError as error:
            reason=error.code.removeprefix('telegram_');bot=None
        finally: self._checking=False
        with self.store.connect() as db:
            result=db.execute('''UPDATE telegram_approval_settings SET checked_revision=?,checked_at=?,checked_ok=?,
                bot_id=?,bot_username=?,reason=? WHERE id=1 AND revision=?''',
                (revision,self.clock().isoformat(),int(reason=='ready'),str(bot['id']) if bot else None,
                 bot['username'] if bot else None,reason,revision))
            if result.rowcount!=1: _fail('configuration_changed')
        return self.snapshot()

    def _lease(self, row):
        now=self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT lease_owner,lease_until,revision FROM telegram_approval_settings WHERE id=1').fetchone()
            if current['revision']!=row['revision']: return False
            if current['lease_owner'] not in (None,self.owner) and current['lease_until'] and _moment(current['lease_until'])>now: return False
            db.execute('UPDATE telegram_approval_settings SET lease_owner=?,lease_until=? WHERE id=1',
                       (self.owner,(now+timedelta(seconds=120)).isoformat()))
        return True

    def _release(self):
        with self.store.connect() as db:
            db.execute('UPDATE telegram_approval_settings SET lease_owner=NULL,lease_until=NULL WHERE id=1 AND lease_owner=?',(self.owner,))

    def _delivery(self, request_id, revision, **fields):
        allowed={'state','video_message_id','message_id','reason'}
        if not fields or set(fields)-allowed: _fail('invalid_response')
        with self.store.connect() as db:
            db.execute('UPDATE telegram_approval_delivery SET '+','.join(name+'=?' for name in fields)+',updated_at=? WHERE request_id=? AND connection_revision=?',
                       (*fields.values(),self.clock().isoformat(),request_id,revision))

    def _reusable_preview(self,db,row,ticket,*,source_request_id=None):
        """A new review may reply to a real confirmed preview of cancelled work."""
        binding=normalize_binding(ticket['binding'])
        if binding['queue']!='token' or binding.get('publish_mode')!='on_approval':return None
        ignored={'job_id','scheduled_at','review_batch'}
        compare=lambda value:{key:item for key,item in value.items() if key not in ignored}
        try:
            rows=db.execute('''SELECT r.request_id,r.binding,r.binding_hash,d.video_message_id,
                d.message_id,d.binding_hash AS delivery_hash,j.checkpoint,j.status,j.step,j.archived_at,j.published_url
                FROM publication_approval_requests r JOIN telegram_approval_delivery d USING(request_id)
                JOIN reel_jobs j ON r.queue='token' AND j.id=r.job_id
                WHERE r.state='cancelled' AND d.state IN ('sent','decided')
                AND d.connection_revision=? AND d.chat_id=? AND r.request_id<>?
                AND (? IS NULL OR r.request_id=?) ORDER BY d.updated_at DESC,r.request_id LIMIT 100''',
                (row['policy_revision'],row['chat_id'],ticket['request_id'],source_request_id,source_request_id)).fetchall()
            for prior in rows:
                try:
                    old=normalize_binding(json.loads(prior['binding']));checkpoint=json.loads(prior['checkpoint'])
                    digest=hashlib.sha256(json.dumps(old,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                    if (compare(old)!=compare(binding) or prior['binding_hash']!=digest or prior['delivery_hash']!=digest
                            or not (prior['status']=='cancelled' or prior['status']=='needs_review' and prior['step']=='stopped' and prior['archived_at'])
                            or prior['published_url'] or not isinstance(checkpoint,dict)
                            or checkpoint.get('cancel_requested') is not True
                            or any(checkpoint.get(key) for key in ('upload_attempted','upload_ack','finish_attempted','finish_ack'))
                            or type(prior['video_message_id']) is not int or prior['video_message_id']<1
                            or type(prior['message_id']) is not int or prior['message_id']<1):continue
                    return {'request_id':prior['request_id'],'video_message_id':prior['video_message_id']}
                except (ValueError,TypeError,AppError):continue
        except sqlite3.Error:return None  # Older/independent stores retain the normal upload path.
        return None

    def _reuse_current(self,row,request_id):
        ticket=self.ledger.get(request_id)
        with self.store.connect() as db:
            receipt=db.execute('SELECT * FROM telegram_approval_preview_reuse WHERE request_id=? AND connection_revision=?',
                (request_id,row['policy_revision'])).fetchone()
            current=self._reusable_preview(db,row,ticket,source_request_id=receipt['source_request_id']) if receipt else None
        if (not current or current['video_message_id']!=receipt['video_message_id']
                or not review_is_open(ticket,self.clock())):_fail('configuration_changed')

    async def _notify(self, row, ticket, *, trial_request_id=None,cohort_id=None):
        request_id=ticket['request_id']; binding=ticket['binding']
        if trial_request_id:
            self._trial_current(row['revision'],trial_request_id)
            if request_id!=trial_request_id:_fail('trial_unavailable')
        if cohort_id:self._cohort_current(row['revision'],cohort_id,request_id=request_id,binding_hash=ticket['binding_hash'])
        if not review_is_open(ticket,self.clock()): return False
        with self.store.connect() as db:
            history=db.execute('SELECT * FROM telegram_approval_delivery WHERE request_id=?',(request_id,)).fetchall()
            if any(v['connection_revision']==row['policy_revision'] or v['state'] in ('sending_video','sending_buttons','uncertain') for v in history): return False
        nonce=secrets.token_urlsafe(24);inserted=False
        try:
            if not callable(self.media_resolver): _fail('media_unavailable')
            path=self.media_resolver(binding['queue'],binding['asset_id'])
            path=_file(path,binding['asset_sha256'],binding['asset_size'])
            if len(binding['caption'])>5000: _fail('caption_too_long')
            metadata=f"Page {binding['page_id']} · {binding['queue']}\n{_review_schedule(binding)}\nSHA256: {binding['asset_sha256']}"
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                reused=self._reusable_preview(db,row,ticket)
                db.execute('INSERT INTO telegram_approval_delivery(request_id,binding_hash,connection_revision,nonce_sha256,state,chat_id,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (request_id,ticket['binding_hash'],row['policy_revision'],_hash(nonce.encode()),'sending_buttons' if reused else 'sending_video',row['chat_id'],self.clock().isoformat()))
                if reused:
                    db.execute('INSERT INTO telegram_approval_preview_reuse VALUES(?,?,?,?,?)',
                        (request_id,row['policy_revision'],reused['request_id'],reused['video_message_id'],self.clock().isoformat()))
                    db.execute('UPDATE telegram_approval_delivery SET video_message_id=? WHERE request_id=? AND connection_revision=?',
                        (reused['video_message_id'],request_id,row['policy_revision']))
            inserted=True
            if not reused:
                with path.open('rb') as video:
                    sent=await self._request(row,'sendVideo',{'chat_id':row['chat_id'],'caption':metadata},
                        files={'video':('review.mp4',video,'video/mp4')},polling=True,trial_request_id=trial_request_id,cohort_id=cohort_id,cohort_member_id=request_id if cohort_id else None)
                _file(path,binding['asset_sha256'],binding['asset_size'])
                video_id=self._message(sent,row['chat_id'])
                self._delivery(request_id,row['policy_revision'],state='sending_buttons',video_message_id=video_id)
            caption=binding['caption']
            if len(caption)>2500:
                parts=[caption[index:index+2500] for index in range(0,len(caption),2500)]
                for index,part in enumerate(parts,1):
                    sent_part=await self._request(row,'sendMessage',{'chat_id':row['chat_id'],
                        'text':f'Caption nguyên văn — phần {index}/{len(parts)}:\n'+part},polling=True,trial_request_id=trial_request_id,cohort_id=cohort_id,cohort_member_id=request_id if cohort_id else None)
                    self._message(sent_part,row['chat_id'])
                caption=f'[Đã gửi đầy đủ {len(parts)} phần caption ở trên; kiểm tra từng phần trước khi duyệt.]'
            body={'chat_id':row['chat_id'],'text':metadata+'\n\nCaption đăng nguyên văn:\n'+caption+
                '\n\nDuyệt chỉ cho đúng video, Page, caption và lịch này. Thay đổi nội dung phải duyệt lại.',
                'reply_markup':{'inline_keyboard':[[{'text':'Duyệt','callback_data':'tg1:'+nonce+':a'},
                                                  {'text':'Từ chối','callback_data':'tg1:'+nonce+':r'}]]}}
            if reused:
                body['reply_to_message_id']=reused['video_message_id']
                body['allow_sending_without_reply']=False
                body['text']='Duyệt lại cho lịch mới. Bản xem trước là đúng video đã xác nhận gửi ở tin được trả lời dưới đây.\n\n'+body['text']
            sent=await self._request(row,'sendMessage',body,polling=True,trial_request_id=trial_request_id,cohort_id=cohort_id,cohort_member_id=request_id if cohort_id else None,
                preview_reuse_id=request_id if reused else None)
            self._delivery(request_id,row['policy_revision'],state='sent',message_id=self._message(sent,row['chat_id']),reason=None)
            return True
        except asyncio.CancelledError:
            if inserted:self._delivery(request_id,row['policy_revision'],state='uncertain',reason='notification_uncertain')
            raise
        except Exception as error:
            reason=error.code.removeprefix('telegram_') if isinstance(error,AppError) else 'invalid_response'
            reason=reason if reason in REASONS else 'notification_uncertain'
            if inserted:
                self._delivery(request_id,row['policy_revision'],state='uncertain',reason=reason)
                raise
            with self.store.connect() as db:
                db.execute('INSERT OR IGNORE INTO telegram_approval_delivery(request_id,binding_hash,connection_revision,nonce_sha256,state,chat_id,reason,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                    (request_id,ticket['binding_hash'],row['policy_revision'],_hash(nonce.encode()),'blocked',row['chat_id'],reason,self.clock().isoformat()))
            return False

    @staticmethod
    def _message(value, chat):
        if (not isinstance(value,dict) or type(value.get('message_id')) is not int or value['message_id']<1
                or not isinstance(value.get('chat'),dict) or type(value['chat'].get('id')) is not int
                or str(value['chat']['id'])!=chat): _fail('invalid_response')
        return value['message_id']

    async def _update(self, row, update, *, trial_request_id=None,cohort_id=None):
        if trial_request_id:self._trial_current(row['revision'],trial_request_id)
        cohort=self.cohorts.validate(cohort_id,revision=row['revision']) if cohort_id else None
        if not isinstance(update,dict) or type(update.get('update_id')) is not int or not 0<=update['update_id']<2**53: _fail('invalid_response')
        update_id=update['update_id']
        with self.store.connect() as db:
            known=db.execute('SELECT 1 FROM telegram_approval_updates WHERE bot_id=? AND update_id=?',(row['bot_id'],update_id)).fetchone()
        if known: return
        query=update.get('callback_query');outcome='ignored';answer=None
        if isinstance(query,dict):
            query_id=query.get('id');sender=query.get('from');message=query.get('message');data=query.get('data')
            if isinstance(query_id,str) and 0<len(query_id)<=256: answer=query_id
            if (answer is not None and isinstance(sender,dict) and sender.get('is_bot') is False
                    and type(sender.get('id')) is int and str(sender['id']) in json.loads(row['allowed_users'])
                    and isinstance(message,dict) and isinstance(message.get('chat'),dict)
                    and type(message['chat'].get('id')) is int and str(message['chat']['id'])==row['chat_id']
                    and type(message.get('message_id')) is int and isinstance(data,str)):
                match=re.fullmatch(r'tg1:([A-Za-z0-9_-]{32}):([ar])',data)
                batch_match=re.fullmatch(r'tgb1:([A-Za-z0-9_-]{32}):a',data)
                if batch_match and trial_request_id is None:
                    try:
                        result=self.batches.decide(batch_match[1],revision=row['revision'],policy_revision=row['policy_revision'],
                            actor_id=sender['id'],chat_id=message['chat']['id'],update_id=update_id,message_id=message['message_id'],cohort_id=cohort_id)
                        for request_id in result['request_ids']:self._delivery(request_id,row['policy_revision'],state='decided')
                        outcome='decided'
                    except AppError as error:
                        if error.status_code>=500:raise
                        outcome='rejected'
                elif match:
                    with self.store.connect() as db:
                        delivery=db.execute('SELECT * FROM telegram_approval_delivery WHERE nonce_sha256=?',(_hash(match[1].encode()),)).fetchone()
                        scoped=(db.execute('''SELECT 1 FROM telegram_approval_cohort_members m
                            JOIN telegram_approval_cohorts c ON c.cohort_id=m.cohort_id
                            WHERE m.request_id=? AND c.policy_revision=? LIMIT 1''',
                            (delivery['request_id'],row['policy_revision'])).fetchone() if delivery and cohort_id is None and trial_request_id is None else None)
                    if (delivery and (trial_request_id is None or delivery['request_id']==trial_request_id)
                            and scoped is None
                            and (cohort is None or any(member['request_id']==delivery['request_id'] and member['binding_hash']==delivery['binding_hash'] for member in cohort['members']))
                            and delivery['state']=='sent' and delivery['connection_revision']==row['policy_revision']
                            and delivery['chat_id']==row['chat_id'] and delivery['message_id']==message['message_id']):
                        ticket=self.ledger.get(delivery['request_id'])
                        if (ticket['binding_hash']==delivery['binding_hash'] and review_is_open(ticket,self.clock())):
                            try:
                                if cohort_id:
                                    self.cohorts.decide_member(cohort_id,ticket['request_id'],ticket['binding_hash'],'approve' if match[2]=='a' else 'reject',
                                        actor_id=sender['id'],chat_id=message['chat']['id'],update_id=update_id,message_id=message['message_id'],nonce_sha256=delivery['nonce_sha256'])
                                else:self.ledger.decide(ticket['request_id'],ticket['binding_hash'],'approve' if match[2]=='a' else 'reject',
                                        actor_id=sender['id'],chat_id=message['chat']['id'],update_id=update_id,message_id=message['message_id'])
                                outcome='decided';self._delivery(ticket['request_id'],row['policy_revision'],state='decided')
                            except AppError as error:
                                if error.status_code>=500: raise
                                outcome='rejected'
        # Decision is durable before acknowledging the update at Telegram.
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO telegram_approval_updates VALUES(?,?,?)',(row['bot_id'],update_id,outcome))
            db.execute('UPDATE telegram_approval_settings SET next_offset=MAX(next_offset,?),last_poll_at=? WHERE id=1 AND revision=?',
                (update_id+1,self.clock().isoformat(),row['revision']))
        if answer:
            with suppress(AppError):
                await self._request(row,'answerCallbackQuery',{'callback_query_id':answer,
                    'text':'Đã ghi nhận quyết định.' if outcome=='decided' else 'Yêu cầu không còn hợp lệ hoặc không thuộc quyền duyệt.','cache_time':0},polling=True,trial_request_id=trial_request_id,cohort_id=cohort_id)

    async def _cohort_cycle(self,row,cohort_id):
        if not self._lease(row):return
        try:
            cohort=self.cohorts.validate(cohort_id,revision=row['revision'])
            for member in cohort['members']:
                ticket=self.ledger.get(member['request_id'])
                if ticket['state']=='pending' and await self._notify(row,ticket,cohort_id=cohort_id):break
                with self.store.connect() as db:
                    delivery=db.execute('SELECT state FROM telegram_approval_delivery WHERE request_id=? AND connection_revision=?',
                        (member['request_id'],row['policy_revision'])).fetchone()
                if delivery and delivery['state'] in ('sending_video','sending_buttons','uncertain','blocked'):
                    self.cohorts.finish(cohort_id,'uncertain');return
            row=self._cohort_current(row['revision'],cohort_id)
            with suppress(AppError):await self._notify_batch(row,cohort_id=cohort_id)
            updates=await self._request(row,'getUpdates',{'offset':row['next_offset'],'limit':25,'timeout':20,
                'allowed_updates':['callback_query']},polling=True,cohort_id=cohort_id)
            if not isinstance(updates,list) or len(updates)>100:_fail('invalid_response')
            for update in sorted(updates,key=lambda value:value.get('update_id',-1) if isinstance(value,dict) else -1):
                self._cohort_current(row['revision'],cohort_id)
                await self._update(row,update,cohort_id=cohort_id)
            if not any(self.ledger.get(member['request_id'])['state']=='pending' for member in cohort['members']):
                self.cohorts.finish(cohort_id,'completed')
            with self.store.connect() as db:
                db.execute("UPDATE telegram_approval_settings SET last_poll_at=?,reason='ready' WHERE id=1 AND revision=?",(self.clock().isoformat(),row['revision']))
        except asyncio.CancelledError:raise
        except Exception as error:
            current=self.cohorts.snapshot()
            if current and current['cohort_id']==cohort_id and current['counts']['uncertain']:
                self.cohorts.finish(cohort_id,'uncertain')
            reason=error.code.removeprefix('telegram_') if isinstance(error,AppError) else 'approval_unavailable'
            with self.store.connect() as db:
                db.execute('UPDATE telegram_approval_settings SET reason=?,checked_ok=CASE WHEN ? THEN 0 ELSE checked_ok END WHERE id=1 AND revision=?',
                    (reason if reason in REASONS else 'invalid_response',int(reason in ('invalid_token','webhook_active','consumer_conflict')),row['revision']))
            self._retry_at=self.clock()+timedelta(seconds=30)
        finally:self._release()

    async def _cycle(self, row, *, trial_request_id=None):
        if not self._lease(row): return
        try:
            if trial_request_id:
                self._trial_current(row['revision'],trial_request_id)
                ticket=self.ledger.get(trial_request_id)
                if ticket['state']!='pending':
                    self._finish_trial(row['revision'],trial_request_id,'decided');return
                await self._notify(row,ticket,trial_request_id=trial_request_id)
                trial=self._trial_info(row)
                if trial['delivery_state']!='sent':
                    self._finish_trial(row['revision'],trial_request_id,'uncertain' if trial['delivery_state'] in ('sending_video','sending_buttons','uncertain') else 'blocked');return
                row=self._trial_current(row['revision'],trial_request_id)
            else:
                pending=self.ledger.pending(limit=100,offset=self._notification_offset)
                if self._notification_offset and (self._notification_offset>=pending['total'] or not pending['items']):
                    self._notification_offset=0
                    pending=self.ledger.pending(limit=100,offset=0)
                window_start=self._notification_offset
                for index,ticket in enumerate(pending['items']):
                    # Advance before IO; ambiguous sends are never blindly retried.
                    self._notification_offset=window_start+index+1
                    if await self._notify(row,ticket):break
                if self._notification_offset>=pending['total']:self._notification_offset=0
                row=self._current(row['revision'],enabled=True)
                with suppress(AppError):await self._notify_batch(row)
            await self._run_notifications(row)
            updates=await self._request(row,'getUpdates',{'offset':row['next_offset'],'limit':25,'timeout':20 if trial_request_id else 3,
                'allowed_updates':['callback_query']},polling=True,trial_request_id=trial_request_id)
            if not isinstance(updates,list) or len(updates)>100: _fail('invalid_response')
            for update in sorted(updates,key=lambda v:v.get('update_id',-1) if isinstance(v,dict) else -1):
                if trial_request_id:self._trial_current(row['revision'],trial_request_id)
                else:self._current(row['revision'],enabled=True)
                await self._update(row,update,trial_request_id=trial_request_id)
            await self._run_notifications(row)
            if not trial_request_id:await self._run_reminders(row)
            if trial_request_id and self.ledger.get(trial_request_id)['state']!='pending':
                self._finish_trial(row['revision'],trial_request_id,'decided')
            with self.store.connect() as db:
                db.execute("UPDATE telegram_approval_settings SET last_poll_at=?,reason='ready' WHERE id=1 AND revision=?",(self.clock().isoformat(),row['revision']))
        except asyncio.CancelledError: raise
        except Exception as error:
            if trial_request_id:
                trial=self._trial_info(row)
                if trial and trial['delivery_state'] in ('sending_video','sending_buttons','uncertain'):
                    self._finish_trial(row['revision'],trial_request_id,'uncertain')
            reason=error.code.removeprefix('telegram_') if isinstance(error,AppError) else 'approval_unavailable'
            with self.store.connect() as db:
                db.execute('UPDATE telegram_approval_settings SET reason=?,checked_ok=CASE WHEN ? THEN 0 ELSE checked_ok END WHERE id=1 AND revision=?',
                    (reason if reason in REASONS else 'invalid_response',int(reason in ('invalid_token','webhook_active','consumer_conflict')),row['revision']))
            self._retry_at=self.clock()+timedelta(seconds=30)
        finally:self._release()

    def tick(self):
        if self._closing or self._task and not self._task.done() or self._retry_at and self.clock()<self._retry_at: return
        row=self._row()
        trial=self._trial_info(row)
        trial_id=trial['request_id'] if row['enabled']==0 and trial and trial['active'] else None
        cohort=self.cohorts.snapshot()
        cohort_id=cohort['cohort_id'] if row['enabled']==0 and cohort and cohort['active'] else None
        if trial_id and cohort_id:return
        if row['checked_ok']!=1 or row['checked_revision']!=row['revision']: return
        notice_only=row['enabled']!=1 and not trial_id and not cohort_id
        if notice_only:
            if self.notifications is None or time.monotonic()<self._notice_scan_at:return
            self._notice_scan_at=time.monotonic()+10
        try:asyncio.get_running_loop()
        except RuntimeError:return
        self._task=asyncio.create_task(self._notice_cycle(row) if notice_only else
            self._cohort_cycle(row,cohort_id) if cohort_id else self._cycle(row,trial_request_id=trial_id))
        self._task.add_done_callback(lambda task:task.exception() if not task.cancelled() else None)

    async def shutdown(self):
        self._closing=True
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):await self._task
        self._release()
