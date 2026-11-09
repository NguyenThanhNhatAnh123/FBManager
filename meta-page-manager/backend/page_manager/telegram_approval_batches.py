"""Frozen Telegram approve-all groups; metadata transactions only, no transport.

The transport must latch ``begin_send`` before contacting Telegram, call ``sent``
only for a confirmed message, and hold ambiguous sends with ``uncertain``. Raw
nonces are returned once by ``freeze`` and are never persisted or exposed by reads.
Global notifications and explicit durable cohorts have separate authorization;
single-video trials never grant authority to a group.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from uuid import UUID, uuid4

from .errors import AppError
from .publication_approvals import normalize_binding,review_deadline,review_deadline_passed,REMIND_UNTIL_DECISION


OPEN_STATES = ('draft', 'sending', 'sent', 'uncertain')
MAX_MEMBERS = 20


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError()
    return parsed.astimezone(timezone.utc)


def _fail(reason='invalid', status=409):
    raise AppError('Nhóm duyệt không còn hợp lệ. Đọc lại đúng nhóm video và lịch đã gửi.',
                   'telegram_batch_' + reason, status) from None


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _fail('invalid', 422)
    return value


def _identity(value, *, chat=False):
    if type(value) is int:
        value = str(value)
    pattern = r'-?[1-9][0-9]{0,15}' if chat else r'[1-9][0-9]{0,15}'
    if (not isinstance(value, str) or not re.fullmatch(pattern, value)
            or abs(int(value)) > 2**52 - 1):
        _fail('invalid', 422)
    return value


class TelegramApprovalBatches:
    def __init__(self, store, ledger, *, clock=None, cohorts=None, group_resolver=None):
        self.store, self.ledger = store, ledger
        self.cohorts=cohorts
        self.group_resolver=group_resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS telegram_approval_batches(
                    batch_id TEXT PRIMARY KEY,nonce_sha256 TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,policy_revision INTEGER NOT NULL,
                    gate_revision INTEGER NOT NULL,chat_id TEXT NOT NULL,
                    members_sha256 TEXT NOT NULL,state TEXT NOT NULL,
                    message_id INTEGER,created_at TEXT NOT NULL,approve_by TEXT NOT NULL,
                    decided_at TEXT,event_key TEXT UNIQUE,actor_id TEXT,update_id TEXT);
                CREATE TABLE IF NOT EXISTS telegram_approval_batch_members(
                    batch_id TEXT NOT NULL,position INTEGER NOT NULL,request_id TEXT NOT NULL,
                    binding_hash TEXT NOT NULL,binding TEXT NOT NULL,
                    video_message_id INTEGER NOT NULL,message_id INTEGER NOT NULL,
                    PRIMARY KEY(batch_id,position),UNIQUE(batch_id,request_id));
                CREATE INDEX IF NOT EXISTS telegram_approval_batch_request
                    ON telegram_approval_batch_members(request_id,batch_id);
            ''')
            if 'cohort_id' not in {row[1] for row in db.execute('PRAGMA table_info(telegram_approval_batches)')}:
                db.execute('ALTER TABLE telegram_approval_batches ADD COLUMN cohort_id TEXT')
            if 'producer_group' not in {row[1] for row in db.execute('PRAGMA table_info(telegram_approval_batches)')}:
                db.execute('ALTER TABLE telegram_approval_batches ADD COLUMN producer_group TEXT')

    def _now(self):
        return self.clock().astimezone(timezone.utc)

    def _settings(self, db, *, revision=None, policy_revision=None, chat_id=None, actor_id=None, cohort_id=None):
        row = db.execute('''SELECT enabled,revision,policy_revision,chat_id,allowed_users,
            checked_ok,checked_revision FROM telegram_approval_settings WHERE id=1''').fetchone()
        gate = self.ledger.settings(db)
        if cohort_id is not None:
            if self.cohorts is None:_fail('configuration_changed')
            self.cohorts.validate(cohort_id,revision=revision,policy_revision=policy_revision,
                                  chat_id=chat_id,actor_id=actor_id,db=db)
        if (row is None or (row['enabled'] != 1 if cohort_id is None else row['enabled'] != 0) or row['checked_ok'] != 1
                or row['checked_revision'] != row['revision']
                or revision is not None and row['revision'] != revision
                or policy_revision is not None and row['policy_revision'] != policy_revision
                or chat_id is not None and row['chat_id'] != chat_id):
            _fail('configuration_changed')
        if actor_id is not None and actor_id not in json.loads(row['allowed_users']):
            _fail('unauthorized', 403)
        return row, gate

    def _members(self, db, batch):
        rows = db.execute('SELECT * FROM telegram_approval_batch_members WHERE batch_id=? ORDER BY position LIMIT 21',
                          (batch['batch_id'],)).fetchall()
        if not 2 <= len(rows) <= MAX_MEMBERS:
            _fail('changed')
        members = []
        for index, row in enumerate(rows):
            if row['position'] != index:
                _fail('changed')
            try:
                binding = normalize_binding(json.loads(row['binding']))
            except (AppError, ValueError, TypeError):
                _fail('changed')
            if _hash(_json(binding)) != row['binding_hash']:
                _fail('changed')
            members.append({key: row[key] for key in ('request_id', 'binding_hash', 'video_message_id', 'message_id')}
                           | {'binding': binding})
        if len({item['request_id'] for item in members}) != len(members) or _hash(_json(members)) != batch['members_sha256']:
            _fail('changed')
        return members

    def _ticket(self, db, request_id, policy_revision, chat_id):
        row = db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?', (request_id,)).fetchone()
        delivery = db.execute('SELECT * FROM telegram_approval_delivery WHERE request_id=? AND connection_revision=?',
                              (request_id, policy_revision)).fetchone()
        if row is None or row['state'] != 'pending':
            _fail('member_changed')
        try:
            binding = normalize_binding(json.loads(row['binding']))
            expired = review_deadline_passed(row,self._now())
        except (AppError, ValueError, TypeError):
            _fail('member_changed')
        if expired:
            _fail('expired')
        verdict=self.ledger.verdict(binding,db)
        if (verdict['required'] is not True or verdict['state']!='pending'
                or verdict['request_id']!=request_id or verdict['binding_hash']!=row['binding_hash']):
            _fail('member_changed')
        if (binding['queue'] != row['queue'] or binding['job_id'] != row['job_id']
                or _hash(_json(binding)) != row['binding_hash'] or delivery is None or delivery['state'] != 'sent'
                or delivery['chat_id'] != chat_id or delivery['binding_hash'] != row['binding_hash']
                or type(delivery['video_message_id']) is not int or delivery['video_message_id'] < 1
                or type(delivery['message_id']) is not int or delivery['message_id'] < 1):
            _fail('member_changed')
        return ({'request_id': request_id, 'binding_hash': row['binding_hash'], 'binding': binding,
                 'video_message_id': delivery['video_message_id'], 'message_id': delivery['message_id']}, review_deadline(row))

    def _validate(self, db, batch, *, actor_id=None):
        _, gate = self._settings(db, revision=batch['revision'], policy_revision=batch['policy_revision'],
                                 chat_id=batch['chat_id'], actor_id=actor_id,cohort_id=batch['cohort_id'])
        if gate['gate_revision'] != batch['gate_revision']:
            _fail('configuration_changed')
        if batch['cohort_id'] is not None:self._scope_ready(db,batch['cohort_id'])
        members = self._members(db, batch)
        if batch['cohort_id'] is None:
            if self.group_resolver is not None and batch['producer_group'] is None:_fail('member_changed')
            self._group(db,members,expected=batch['producer_group'])
        for member in members:
            if batch['cohort_id'] is not None:
                self.cohorts.validate(batch['cohort_id'],request_id=member['request_id'],binding_hash=member['binding_hash'],db=db)
            current, _ = self._ticket(db, member['request_id'], batch['policy_revision'], batch['chat_id'])
            if current != member:
                _fail('member_changed')
        return members

    def _group(self,db,members,*,expected=None):
        # Explicit cohorts carry their own immutable scope. Normal automatic
        # summaries must share a trusted producer batch/run, never mere timing.
        if len({member['binding'].get('approval_policy') for member in members})!=1:_fail('member_changed')
        if self.group_resolver is None:return None
        groups={self.group_resolver(member['binding'],db) for member in members}
        if len(groups)!=1 or None in groups:_fail('member_changed')
        group=next(iter(groups))
        if expected is not None and group!=expected:_fail('member_changed')
        if group.startswith('review:'):
            _,identifier,count,_=group.split(':')
            # A declared review group is exact. A cancelled/rebound/extra
            # ticket requires a new group authorization, never a partial button.
            scope=db.execute("SELECT request_id FROM publication_approval_requests WHERE json_valid(binding) AND json_extract(binding,'$.review_batch.id')=?",(identifier,)).fetchall()
            if (len(members)!=int(count) or len(scope)!=int(count)
                    or {row['request_id'] for row in scope}!={member['request_id'] for member in members}):_fail('member_changed')
        return group

    def _scope_ready(self,db,cohort_id):
        scope=self.cohorts.validate(cohort_id,db=db)
        for member in scope['members']:
            delivery=db.execute('SELECT * FROM telegram_approval_delivery WHERE request_id=? AND connection_revision=?',
                (member['request_id'],scope['policy_revision'])).fetchone()
            if (not delivery or delivery['state'] not in ('sent','decided') or delivery['binding_hash']!=member['binding_hash']
                    or delivery['chat_id']!=scope['chat_id'] or type(delivery['video_message_id']) is not int
                    or delivery['video_message_id']<1 or type(delivery['message_id']) is not int or delivery['message_id']<1):
                _fail('member_changed')
        return scope

    @staticmethod
    def _batch(db, batch_id):
        row = db.execute('SELECT * FROM telegram_approval_batches WHERE batch_id=?', (batch_id,)).fetchone()
        if row is None:
            _fail('not_found', 404)
        return row

    def _view(self, db, batch, *, members=False):
        items = self._members(db, batch)
        value = {key: batch[key] for key in ('batch_id', 'state', 'approve_by', 'created_at', 'decided_at', 'message_id','cohort_id')}
        deadlines=[review_deadline(item['binding']) for item in items]
        value['approve_by']=min(value for value in deadlines if value is not None).isoformat() if any(value is not None for value in deadlines) else None
        if all(item['binding'].get('approval_policy')==REMIND_UNTIL_DECISION for item in items):
            value.update(approval_policy=REMIND_UNTIL_DECISION,reminder_interval_seconds=300)
        value.update(total=len(items), scheduled_at=None, page_ids=list(dict.fromkeys(item['binding']['page_id'] for item in items)))
        if members:
            value.update(members=items, request_ids=[item['request_id'] for item in items])
        return value

    def candidates(self, policy_revision, chat_id, *, limit=MAX_MEMBERS):
        """Return confirmed, pending and ungrouped ticket IDs; never send anything."""
        if type(policy_revision) is not int or policy_revision < 1 or type(limit) is not int or not 2 <= limit <= MAX_MEMBERS:
            _fail('invalid', 422)
        chat_id = _identity(chat_id, chat=True)
        with self.store.connect() as db:
            self._settings(db, policy_revision=policy_revision, chat_id=chat_id)
            rows = db.execute('''SELECT r.request_id FROM publication_approval_requests r
                JOIN telegram_approval_delivery d ON d.request_id=r.request_id AND d.connection_revision=?
                WHERE r.state='pending' AND (json_extract(r.binding,'$.approval_policy')='remind_until_decision_v1' OR julianday(r.approve_by)>julianday(?))
                  AND d.state='sent' AND d.chat_id=? AND d.binding_hash=r.binding_hash
                  AND d.video_message_id>0 AND d.message_id>0
                  AND NOT EXISTS(SELECT 1 FROM telegram_approval_batch_members m
                    JOIN telegram_approval_batches b ON b.batch_id=m.batch_id
                    WHERE m.request_id=r.request_id AND b.state IN ('draft','sending','sent','uncertain'))
                ORDER BY r.approve_by,r.request_id LIMIT ?''',
                (policy_revision, self._now().isoformat(), chat_id, 100 if self.group_resolver else limit)).fetchall()
            result,groups = [],{}
            for row in rows:
                try:member,_=self._ticket(db, row['request_id'], policy_revision, chat_id)
                except AppError:continue  # An unrelated held ticket cannot join or delay this group.
                if self.group_resolver:
                    group=self.group_resolver(member['binding'],db)
                    if group is None:continue
                    groups.setdefault(group,[]).append(member)
                    continue
                result.append(row['request_id'])
            if self.group_resolver:
                for members in groups.values():
                    if len(members)<2:continue
                    selected=members[:limit]
                    try:self._group(db,selected)
                    except AppError:continue
                    return [member['request_id'] for member in selected]
                return []
            return result

    def freeze(self, request_ids, *, revision, policy_revision, chat_id, cohort_id=None):
        if (not isinstance(request_ids, list) or not 2 <= len(request_ids) <= MAX_MEMBERS
                or type(revision) is not int or revision < 1 or type(policy_revision) is not int or policy_revision < 1):
            _fail('invalid', 422)
        request_ids = [_uuid(value) for value in request_ids]
        if len(set(request_ids)) != len(request_ids):
            _fail('invalid', 422)
        chat_id = _identity(chat_id, chat=True)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _, gate = self._settings(db, revision=revision, policy_revision=policy_revision, chat_id=chat_id,cohort_id=cohort_id)
            if cohort_id is not None:self._scope_ready(db,cohort_id)
            members, deadlines = [], []
            for request_id in request_ids:
                if db.execute('''SELECT 1 FROM telegram_approval_batch_members m
                    JOIN telegram_approval_batches b ON b.batch_id=m.batch_id
                    WHERE m.request_id=? AND b.state IN ('draft','sending','sent','uncertain')''', (request_id,)).fetchone():
                    _fail('already_grouped')
                member, deadline = self._ticket(db, request_id, policy_revision, chat_id)
                if cohort_id is not None:
                    self.cohorts.validate(cohort_id,request_id=request_id,binding_hash=member['binding_hash'],db=db)
                members.append(member)
                if deadline is not None:deadlines.append(deadline)
            producer_group=self._group(db,members) if cohort_id is None else None
            batch_id, nonce = str(uuid4()), secrets.token_urlsafe(24)
            db.execute('''INSERT INTO telegram_approval_batches
                (batch_id,nonce_sha256,revision,policy_revision,gate_revision,chat_id,members_sha256,state,created_at,approve_by,cohort_id,producer_group)
                VALUES(?,?,?,?,?,?,?,'draft',?,?,?,?)''',
                (batch_id, _hash(nonce), revision, policy_revision, gate['gate_revision'], chat_id,
                 _hash(_json(members)), self._now().isoformat(), (min(deadlines) if deadlines else min(_time(item['binding']['scheduled_at']) for item in members)).isoformat(),cohort_id,producer_group))
            db.executemany('INSERT INTO telegram_approval_batch_members VALUES(?,?,?,?,?,?,?)',
                [(batch_id, index, item['request_id'], item['binding_hash'], _json(item['binding']), item['video_message_id'], item['message_id'])
                 for index, item in enumerate(members)])
            return self._view(db, self._batch(db, batch_id), members=True) | {'nonce': nonce}

    def get(self, batch_id):
        _uuid(batch_id)
        with self.store.connect() as db:
            return self._view(db, self._batch(db, batch_id), members=True)

    def recent(self, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            _fail('invalid', 422)
        with self.store.connect() as db:
            return [self._view(db, row) for row in db.execute(
                'SELECT * FROM telegram_approval_batches ORDER BY created_at DESC,batch_id DESC LIMIT ?', (limit,)).fetchall()]

    def begin_send(self, batch_id):
        _uuid(batch_id)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = self._batch(db, batch_id)
            if batch['state'] != 'draft':
                _fail('send_held')
            self._validate(db, batch)
            db.execute("UPDATE telegram_approval_batches SET state='sending' WHERE batch_id=?", (batch_id,))
            return self._view(db, self._batch(db, batch_id), members=True)

    def sent(self, batch_id, message_id):
        _uuid(batch_id)
        if type(message_id) is not int or not 1 <= message_id <= 2**52 - 1:
            _fail('invalid', 422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = self._batch(db, batch_id)
            if batch['state'] == 'sent' and batch['message_id'] == message_id:
                return self._view(db, batch, members=True)
            if batch['state'] != 'sending':
                _fail('send_held')
            # Persist a confirmed receipt even if authorization expired during IO.
            # decide() still revalidates the current authorization and deadline.
            db.execute("UPDATE telegram_approval_batches SET state='sent',message_id=? WHERE batch_id=?", (message_id, batch_id))
            return self._view(db, self._batch(db, batch_id), members=True)

    def uncertain(self, batch_id):
        _uuid(batch_id)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = self._batch(db, batch_id)
            if batch['state'] not in ('draft', 'sending', 'uncertain'):
                _fail('send_held')
            db.execute("UPDATE telegram_approval_batches SET state='uncertain' WHERE batch_id=?", (batch_id,))
            return self._view(db, self._batch(db, batch_id), members=True)

    def decide(self, nonce, *, revision, policy_revision, actor_id, chat_id, update_id, message_id,cohort_id=None):
        """Approve this exact confirmed group, entirely or not at all.

        The canonical callback event is reserved for the first member, matching
        the existing ledger's replay fence. Further per-member audit events are
        domain-separated while retaining the same actual Telegram update ID.
        """
        if (not isinstance(nonce, str) or re.fullmatch(r'[A-Za-z0-9_-]{32}', nonce) is None
                or type(revision) is not int or revision < 1 or type(policy_revision) is not int or policy_revision < 1
                or type(update_id) is not int or not 0 <= update_id < 2**53
                or type(message_id) is not int or not 1 <= message_id <= 2**52 - 1):
            _fail('invalid', 422)
        actor, chat = _identity(actor_id), _identity(chat_id, chat=True)
        event = _hash(_json([chat, str(update_id)]))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = db.execute('SELECT * FROM telegram_approval_batches WHERE nonce_sha256=?', (_hash(nonce),)).fetchone()
            if batch is None:
                _fail('not_found', 404)
            if (batch['revision'] != revision or batch['policy_revision'] != policy_revision
                    or batch['chat_id'] != chat or batch['message_id'] != message_id or batch['cohort_id']!=cohort_id):
                _fail('unauthorized', 403)
            self._settings(db, revision=revision, policy_revision=policy_revision, chat_id=chat, actor_id=actor,cohort_id=cohort_id)
            if batch['state'] == 'approved':
                if batch['event_key'] != event or batch['actor_id'] != actor or batch['update_id'] != str(update_id):
                    _fail('decision_conflict')
                return self._view(db, batch, members=True) | {'replayed': True}
            if batch['state'] != 'sent':
                _fail('send_held')
            members = self._validate(db, batch, actor_id=actor)
            if db.execute('SELECT 1 FROM publication_approval_decisions WHERE event_key=?', (event,)).fetchone():
                _fail('decision_conflict')
            stamp = self._now().isoformat()
            for index, member in enumerate(members):
                member_event = event if index == 0 else _hash(_json(['telegram-approval-batch-v1', batch['batch_id'], chat, str(update_id), member['request_id']]))
                db.execute('INSERT INTO publication_approval_decisions VALUES(?,?,?,?,?,?,?,?,?)',
                    (member_event, member['request_id'], member['binding_hash'], 'approve', actor, chat,
                     str(update_id), str(message_id), stamp))
                ticket=db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?',(member['request_id'],)).fetchone()
                self.ledger._apply_decision(db,ticket,'approve',stamp)
            db.execute("UPDATE telegram_approval_batches SET state='approved',decided_at=?,event_key=?,actor_id=?,update_id=? WHERE batch_id=?",
                       (stamp, event, actor, str(update_id), batch['batch_id']))
            return self._view(db, self._batch(db, batch['batch_id']), members=True) | {'replayed': False}
