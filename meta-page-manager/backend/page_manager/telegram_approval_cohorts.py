"""Explicit, durable review scopes while global Telegram delivery stays off.

Only the transport owns network IO. This ledger never selects global pending
tickets, extends an old authorization, or grants publication by itself.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import UUID, uuid4

from .errors import AppError
from .publication_approvals import normalize_binding,review_deadline,review_deadline_passed,REMIND_UNTIL_DECISION


def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def _hash(value):return hashlib.sha256(_json(value).encode()).hexdigest()
def _time(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError()
    return value.astimezone(timezone.utc)
def _fail(reason='changed',status=409):
    raise AppError('Phiên duyệt Telegram không còn khớp danh sách đã khóa. Đọc lại trạng thái.',
                   'telegram_cohort_'+reason,status) from None
def _uuid(value):
    try:
        if not isinstance(value,str) or str(UUID(value))!=value or not UUID(value).int:raise ValueError()
    except (ValueError,TypeError,AttributeError):_fail('invalid',422)
    return value


class TelegramApprovalCohorts:
    def __init__(self,store,ledger,*,clock=None,status_resolver=None):
        self.store,self.ledger=store,ledger
        self.clock=clock or (lambda:datetime.now(timezone.utc));self.status_resolver=status_resolver
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS telegram_approval_cohorts(
                cohort_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,revision INTEGER NOT NULL,
                policy_revision INTEGER NOT NULL,gate_revision INTEGER NOT NULL,chat_id TEXT NOT NULL,
                bot_id TEXT NOT NULL,users_sha256 TEXT NOT NULL,members_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS telegram_approval_cohort_members(
                cohort_id TEXT NOT NULL,position INTEGER NOT NULL,request_id TEXT NOT NULL,
                binding_hash TEXT NOT NULL,binding TEXT NOT NULL,
                PRIMARY KEY(cohort_id,position),UNIQUE(cohort_id,request_id));''')

    def _settings(self,db,revision,gate_revision,*,fresh=False):
        row=db.execute('SELECT * FROM telegram_approval_settings WHERE id=1').fetchone()
        gate=self.ledger.settings(db)
        if (row is None or row['enabled']!=0 or row['revision']!=revision
                or row['checked_revision']!=revision or row['checked_ok']!=1
                or not row['encrypted_token'] or not row['chat_id'] or not row['bot_id']
                or not json.loads(row['allowed_users'])
                or gate['gate_revision']!=gate_revision):_fail('configuration_changed')
        if fresh and (not row['checked_at'] or self.clock()>=_time(row['checked_at'])+timedelta(seconds=300)):
            _fail('not_checked')
        return row

    def _members(self,db,cohort):
        rows=[dict(row) for row in db.execute('SELECT * FROM telegram_approval_cohort_members WHERE cohort_id=? ORDER BY position LIMIT 21',(cohort['cohort_id'],))]
        if not 2<=len(rows)<=20 or [row['position'] for row in rows]!=list(range(len(rows))):_fail()
        members=[]
        for row in rows:
            try:binding=normalize_binding(json.loads(row['binding']))
            except (ValueError,TypeError,AppError):_fail()
            if _hash(binding)!=row['binding_hash']:_fail()
            members.append({'request_id':row['request_id'],'binding_hash':row['binding_hash'],'binding':binding})
        if _hash(members)!=cohort['members_sha256']:_fail()
        return members

    def _ticket(self,db,member,*,pending=False):
        ticket=db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?',(member['request_id'],)).fetchone()
        if (ticket is None or ticket['binding_hash']!=member['binding_hash']
                or ticket['state'] not in (('pending',) if pending else ('pending','approved','rejected'))
                or review_deadline_passed(ticket,self.clock())):_fail('member_changed')
        try:binding=normalize_binding(json.loads(ticket['binding']))
        except (ValueError,TypeError,AppError):_fail('member_changed')
        if binding!=member['binding'] or _hash(binding)!=ticket['binding_hash']:_fail('member_changed')
        verdict=self.ledger.verdict(binding,db)
        if (verdict['required'] is not True or verdict['request_id']!=member['request_id']
                or verdict['binding_hash']!=member['binding_hash'] or verdict['state']!=ticket['state']):_fail('member_changed')
        if not callable(self.status_resolver):_fail('job_unavailable')
        try:current=self.status_resolver(binding['queue'],binding['job_id'])
        except Exception:_fail('job_unavailable')
        if (not isinstance(current,dict) or current.get('binding')!=binding
                or current.get('status') not in ('scheduled','waiting_page')
                or current.get('published_url')):_fail('member_changed')
        return ticket

    def validate(self,cohort_id,*,revision=None,policy_revision=None,chat_id=None,actor_id=None,
                 request_id=None,binding_hash=None,db=None):
        if db is None:
            with self.store.connect() as connection:return self.validate(cohort_id,revision=revision,
                policy_revision=policy_revision,chat_id=chat_id,actor_id=actor_id,request_id=request_id,
                binding_hash=binding_hash,db=connection)
        _uuid(cohort_id)
        cohort=db.execute('SELECT * FROM telegram_approval_cohorts WHERE cohort_id=?',(cohort_id,)).fetchone()
        if cohort is None or cohort['state']!='active':_fail('expired')
        members=self._members(db,cohort)
        deadlines=[value for member in members if (value:=review_deadline(member['binding'])) is not None]
        if deadlines and self.clock()>=min(deadlines):_fail('expired')
        row=self._settings(db,cohort['revision'],cohort['gate_revision'])
        if (row['policy_revision']!=cohort['policy_revision'] or row['chat_id']!=cohort['chat_id']
                or row['bot_id']!=cohort['bot_id'] or _hash(json.loads(row['allowed_users']))!=cohort['users_sha256']
                or revision is not None and revision!=cohort['revision']
                or policy_revision is not None and policy_revision!=cohort['policy_revision']
                or chat_id is not None and str(chat_id)!=cohort['chat_id']
                or actor_id is not None and str(actor_id) not in json.loads(row['allowed_users'])):_fail('configuration_changed')
        for member in members:self._ticket(db,member)
        if request_id is not None and not any(member['request_id']==request_id
                and (binding_hash is None or member['binding_hash']==binding_hash) for member in members):_fail('out_of_scope')
        return dict(cohort)|{'members':members}

    def start(self,body,*,verify_media):
        if (not isinstance(body,dict) or set(body)!={'revision','gate_revision','request_id','members'}
                or type(body.get('revision')) is not int or body['revision']<1
                or type(body.get('gate_revision')) is not int or body['gate_revision']<1
                or not isinstance(body.get('members'),list) or not 2<=len(body['members'])<=20):_fail('invalid',422)
        _uuid(body['request_id']);ids=[]
        for item in body['members']:
            if (not isinstance(item,dict) or set(item)!={'request_id','binding_hash'}
                    or not isinstance(item.get('binding_hash'),str) or not re.fullmatch('[a-f0-9]{64}',item['binding_hash'])):_fail('invalid',422)
            ids.append(_uuid(item['request_id']))
        if len(set(ids))!=len(ids):_fail('invalid',422)
        # Verify actual bytes through the trusted queue resolver, never a supplied path.
        for item in body['members']:verify_media(item['request_id'])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self._settings(db,body['revision'],body['gate_revision'],fresh=True)
            prior=db.execute('SELECT * FROM telegram_approval_cohorts WHERE request_id=?',(body['request_id'],)).fetchone()
            if prior:
                expected=[{'request_id':m['request_id'],'binding_hash':m['binding_hash']} for m in self._members(db,prior)]
                if (expected!=body['members'] or prior['revision']!=body['revision'] or prior['gate_revision']!=body['gate_revision']):_fail('replay_conflict')
                self.validate(prior['cohort_id'],db=db)
                return self._view(db,prior)
            trial=db.execute('SELECT * FROM telegram_approval_trial WHERE id=1').fetchone()
            if trial and trial['revision']==row['revision'] and trial['state']=='active' and self.clock()<_time(trial['expires_at']):_fail('trial_in_progress')
            for old in db.execute("SELECT * FROM telegram_approval_cohorts WHERE state='active'").fetchall():
                try:
                    self.validate(old['cohort_id'],db=db)
                    if any(self._ticket(db,m)['state']=='pending' for m in self._members(db,old)):_fail('in_progress')
                except AppError as error:
                    if error.code=='telegram_cohort_in_progress':raise
            members=[];deadlines=[]
            for item in body['members']:
                ticket=self.ledger._row(db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?',(item['request_id'],)).fetchone())
                member={**item,'binding':ticket['binding']}
                current=self._ticket(db,member,pending=True)
                members.append(member)
                deadline=review_deadline(current)
                if deadline is not None:deadlines.append(deadline)
            if len({member['binding'].get('approval_policy') for member in members})!=1:_fail('member_changed')
            cohort_id=str(uuid4())
            db.execute('INSERT INTO telegram_approval_cohorts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (cohort_id,body['request_id'],row['revision'],row['policy_revision'],body['gate_revision'],row['chat_id'],row['bot_id'],
                 _hash(json.loads(row['allowed_users'])),_hash(members),'active',self.clock().isoformat(),
                 (min(deadlines) if deadlines else min(_time(m['binding']['scheduled_at']) for m in members)).isoformat()))
            db.executemany('INSERT INTO telegram_approval_cohort_members VALUES(?,?,?,?,?)',
                [(cohort_id,index,m['request_id'],m['binding_hash'],_json(m['binding'])) for index,m in enumerate(members)])
            return self._view(db,db.execute('SELECT * FROM telegram_approval_cohorts WHERE cohort_id=?',(cohort_id,)).fetchone())

    def _view(self,db,cohort):
        members=self._members(db,cohort);counts={key:0 for key in ('pending','approved','rejected','delivered','sending','uncertain')}
        lease=db.execute('SELECT lease_owner,lease_until FROM telegram_approval_settings WHERE id=1').fetchone()
        sending_active=bool(cohort['state']=='active' and lease and lease['lease_owner'] and lease['lease_until']
            and self.clock()<_time(lease['lease_until']))
        for member in members:
            ticket=db.execute('SELECT state FROM publication_approval_requests WHERE request_id=?',(member['request_id'],)).fetchone()
            if ticket and ticket['state'] in counts:counts[ticket['state']]+=1
            delivery=db.execute('SELECT state FROM telegram_approval_delivery WHERE request_id=? AND connection_revision=?',(member['request_id'],cohort['policy_revision'])).fetchone()
            if delivery and delivery['state'] in ('sent','decided'):counts['delivered']+=1
            if delivery and delivery['state']=='uncertain':counts['uncertain']+=1
            if delivery and delivery['state'] in ('sending_video','sending_buttons'):
                counts['sending' if sending_active else 'uncertain']+=1
        state=cohort['state']
        if state=='active':
            try:self.validate(cohort['cohort_id'],db=db)
            except AppError as error:state='expired' if error.code=='telegram_cohort_expired' else 'configuration_changed'
            else:
                if counts['uncertain']:state='uncertain'
                elif counts['pending']==0:state='completed'
        deadlines=[value for m in members if (value:=review_deadline(m['binding'])) is not None]
        policy=({'approval_policy':REMIND_UNTIL_DECISION,'reminder_interval_seconds':300}
            if all(m['binding'].get('approval_policy')==REMIND_UNTIL_DECISION for m in members) else {})
        return {key:cohort[key] for key in ('cohort_id','request_id','created_at','expires_at')}|policy|{
            'expires_at':min(deadlines).isoformat() if deadlines else None,
            'state':state,'active':state=='active','total':len(members),'counts':counts,
            'members':[{'request_id':m['request_id'],'binding_hash':m['binding_hash']} for m in members]}

    def snapshot(self):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM telegram_approval_cohorts ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
            return self._view(db,row) if row else None

    def finish(self,cohort_id,state):
        if state not in ('completed','uncertain'):_fail('invalid',422)
        with self.store.connect() as db:db.execute("UPDATE telegram_approval_cohorts SET state=? WHERE cohort_id=? AND state='active'",(state,cohort_id))

    def decide_member(self,cohort_id,request_id,binding_hash,decision,*,actor_id,chat_id,update_id,message_id,nonce_sha256):
        if (decision not in ('approve','reject') or type(update_id) is not int or not 0<=update_id<2**53
                or type(message_id) is not int or not 0<message_id<2**52):_fail('invalid',422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cohort=self.validate(cohort_id,actor_id=actor_id,chat_id=chat_id,request_id=request_id,binding_hash=binding_hash,db=db)
            delivery=db.execute('SELECT * FROM telegram_approval_delivery WHERE request_id=? AND connection_revision=?',(request_id,cohort['policy_revision'])).fetchone()
            if (not delivery or delivery['state']!='sent' or delivery['binding_hash']!=binding_hash
                    or delivery['chat_id']!=str(chat_id) or delivery['message_id']!=message_id
                    or delivery['nonce_sha256']!=nonce_sha256):_fail('out_of_scope')
            event=_hash([str(chat_id),str(update_id)])
            if db.execute('SELECT 1 FROM publication_approval_decisions WHERE event_key=?',(event,)).fetchone():_fail('replay_conflict')
            ticket=db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?',(request_id,)).fetchone()
            if ticket['state']!='pending':_fail('member_changed')
            stamp=self.clock().isoformat()
            db.execute('INSERT INTO publication_approval_decisions VALUES(?,?,?,?,?,?,?,?,?)',
                (event,request_id,binding_hash,decision,str(actor_id),str(chat_id),str(update_id),str(message_id),stamp))
            self.ledger._apply_decision(db,ticket,decision,stamp)
