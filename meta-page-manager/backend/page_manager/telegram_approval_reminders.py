"""Durable five-minute review text; never uploads media or records approval."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
from uuid import uuid4

from .errors import AppError
from .publication_approvals import REMIND_UNTIL_DECISION,normalize_binding,review_is_open

INTERVAL_SECONDS=300


def _json(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def _hash(value):return hashlib.sha256(_json(value).encode()).hexdigest()
def _time(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
def _fail():raise AppError('Lời nhắc không còn khớp yêu cầu đang chờ duyệt.','telegram_configuration_changed',409)


class TelegramApprovalReminders:
    def __init__(self,store,ledger,batches,*,status_resolver,clock=None):
        self.store,self.ledger,self.batches=store,ledger,batches
        self.status_resolver=status_resolver;self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.lock=asyncio.Lock();self._ticket_cursor=0;self._batch_cursor=0
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS telegram_approval_reminders(
                unit_key TEXT PRIMARY KEY,kind TEXT NOT NULL,target_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,next_due_at TEXT NOT NULL,sequence INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS telegram_approval_reminder_attempts(
                id TEXT PRIMARY KEY,unit_key TEXT NOT NULL,sequence INTEGER NOT NULL,state TEXT NOT NULL,
                reply_message_id INTEGER NOT NULL,message_id INTEGER,started_at TEXT NOT NULL,
                finished_at TEXT,lease_until TEXT NOT NULL,reason TEXT,
                UNIQUE(unit_key,sequence));
                CREATE TABLE IF NOT EXISTS telegram_approval_reminder_members(
                reminder_id TEXT NOT NULL,request_id TEXT NOT NULL,binding_hash TEXT NOT NULL,
                PRIMARY KEY(reminder_id,request_id));''')

    def _now(self):return self.clock().astimezone(timezone.utc)

    def _current(self,db,row):
        current=db.execute('SELECT enabled,revision,policy_revision,chat_id,bot_id,checked_ok,checked_revision FROM telegram_approval_settings WHERE id=1').fetchone()
        return bool(current and current['enabled']==1 and current['checked_ok']==1
            and current['checked_revision']==current['revision']
            and all(current[key]==row[key] for key in ('revision','policy_revision','chat_id','bot_id')))

    def _ticket(self,db,row,request_id):
        found=db.execute('''SELECT r.*,d.message_id,d.video_message_id,d.updated_at AS delivered_at
            FROM publication_approval_requests r JOIN telegram_approval_delivery d USING(request_id)
            WHERE r.request_id=? AND d.state='sent' AND d.connection_revision=? AND d.chat_id=?
            AND d.binding_hash=r.binding_hash AND d.message_id>0 AND d.video_message_id>0''',
            (request_id,row['policy_revision'],row['chat_id'])).fetchone()
        if found is None:return None
        try:
            ticket=dict(found);binding=normalize_binding(json.loads(ticket['binding']));ticket['binding']=binding
            if (binding.get('approval_policy')!=REMIND_UNTIL_DECISION or not review_is_open(ticket,self._now())
                    or _hash(binding)!=ticket['binding_hash'] or not callable(self.status_resolver)):return None
            verdict=self.ledger.verdict(binding,db)
            if verdict['required'] is not True or verdict['state']!='pending' or verdict['request_id']!=request_id:return None
            current=self.status_resolver(binding['queue'],binding['job_id'])
            if (not isinstance(current,dict) or current.get('binding')!=binding
                    or current.get('status') not in ('scheduled','waiting_page','waiting_profile')
                    or current.get('published_url') or current.get('archived_at') or current.get('archived') is True
                    or current.get('cancel_requested') is True):return None
            return ticket
        except Exception:return None

    def _unit(self,db,row,kind,target_id):
        if kind=='batch':
            batch=db.execute('SELECT * FROM telegram_approval_batches WHERE batch_id=?',(target_id,)).fetchone()
            if not batch or batch['state']!='sent' or batch['cohort_id'] is not None or not batch['message_id']:return None
            try:members=self.batches._validate(db,batch)
            except Exception:return None
            tickets=[self._ticket(db,row,member['request_id']) for member in members]
            if any(ticket is None for ticket in tickets):return None
            reply=batch['message_id']
        elif kind=='single':
            ticket=self._ticket(db,row,target_id)
            if ticket is None:return None
            tickets=[ticket];reply=ticket['message_id']
        else:return None
        members=[{'request_id':ticket['request_id'],'binding_hash':ticket['binding_hash'],'message_id':ticket['message_id']} for ticket in tickets]
        digest=_hash([kind,target_id,reply,members])
        key=_hash([row['policy_revision'],row['chat_id'],row['bot_id'],digest])
        reference=max(max(_time(ticket['binding']['scheduled_at']),_time(ticket['delivered_at'])) for ticket in tickets)
        return {'unit_key':key,'kind':kind,'target_id':target_id,'payload_hash':digest,
            'reply_message_id':reply,'members':members,'first_due_at':reference+timedelta(seconds=INTERVAL_SECONDS)}

    def _candidates(self,db,row,*,rotate=False):
        units=[];covered=set()
        batch_rows=db.execute("SELECT batch_id,rowid AS seq FROM telegram_approval_batches WHERE state='sent' AND cohort_id IS NULL ORDER BY CASE WHEN rowid>? THEN 0 ELSE 1 END,rowid LIMIT 100",(self._batch_cursor if rotate else 0,)).fetchall()
        if rotate and batch_rows:self._batch_cursor=batch_rows[-1]['seq']
        for batch in batch_rows:
            unit=self._unit(db,row,'batch',batch['batch_id'])
            if unit:
                units.append(unit);covered.update(member['request_id'] for member in unit['members'])
        ticket_rows=db.execute("SELECT request_id,rowid AS seq FROM publication_approval_requests WHERE state='pending' ORDER BY CASE WHEN rowid>? THEN 0 ELSE 1 END,rowid LIMIT 100",(self._ticket_cursor if rotate else 0,)).fetchall()
        if rotate and ticket_rows:self._ticket_cursor=ticket_rows[-1]['seq']
        for ticket in ticket_rows:
            if ticket['request_id'] not in covered:
                unit=self._unit(db,row,'single',ticket['request_id'])
                if unit:units.append(unit)
        return sorted(units,key=lambda unit:(unit['first_due_at'],unit['unit_key']))

    def _claim(self,row,unit):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._current(db,row):return None
            current=self._unit(db,row,unit['kind'],unit['target_id'])
            if not current or current['unit_key']!=unit['unit_key']:return None
            db.execute('INSERT OR IGNORE INTO telegram_approval_reminders VALUES(?,?,?,?,?,0)',
                (unit['unit_key'],unit['kind'],unit['target_id'],unit['payload_hash'],unit['first_due_at'].isoformat()))
            saved=db.execute('SELECT * FROM telegram_approval_reminders WHERE unit_key=?',(unit['unit_key'],)).fetchone()
            now=self._now()
            # Switching from individual reminders to a group (or back after a
            # partial decision) must not reset any member's five-minute gap.
            where=' OR '.join('(m.request_id=? AND m.binding_hash=?)' for member in unit['members'])
            values=[value for member in unit['members'] for value in (member['request_id'],member['binding_hash'])]
            latest=db.execute("SELECT CASE WHEN a.state='sent' THEN a.finished_at ELSE a.started_at END AS at FROM telegram_approval_reminder_members m JOIN telegram_approval_reminder_attempts a ON a.id=m.reminder_id WHERE "+where+" ORDER BY julianday(at) DESC LIMIT 1",values).fetchone()
            if latest and now<_time(latest['at'])+timedelta(seconds=INTERVAL_SECONDS):
                db.execute('UPDATE telegram_approval_reminders SET next_due_at=? WHERE unit_key=?',
                    ((_time(latest['at'])+timedelta(seconds=INTERVAL_SECONDS)).isoformat(),unit['unit_key']))
                return None
            if now<_time(saved['next_due_at']):return None
            # A bounded IO claim is never replayed. Once its lease expires it
            # remains an unknown historical slot; only a later cadence may run.
            db.execute("UPDATE telegram_approval_reminder_attempts SET state='uncertain',reason='interrupted',finished_at=? WHERE unit_key=? AND state='sending' AND julianday(lease_until)<=julianday(?)",(now.isoformat(),unit['unit_key'],now.isoformat()))
            if db.execute("SELECT 1 FROM telegram_approval_reminder_attempts WHERE unit_key=? AND state='sending'",(unit['unit_key'],)).fetchone():return None
            sequence=saved['sequence']+1;identifier=str(uuid4())
            db.execute('UPDATE telegram_approval_reminders SET sequence=?,next_due_at=? WHERE unit_key=?',
                (sequence,(now+timedelta(seconds=INTERVAL_SECONDS)).isoformat(),unit['unit_key']))
            db.execute("INSERT INTO telegram_approval_reminder_attempts VALUES(?,?,?,'sending',?,NULL,?,NULL,?,NULL)",
                (identifier,unit['unit_key'],sequence,unit['reply_message_id'],now.isoformat(),(now+timedelta(seconds=120)).isoformat()))
            db.executemany('INSERT INTO telegram_approval_reminder_members VALUES(?,?,?)',
                [(identifier,member['request_id'],member['binding_hash']) for member in unit['members']])
            return identifier

    def validate_attempt(self,row,identifier):
        with self.store.connect() as db:
            attempt=db.execute('''SELECT a.*,u.kind,u.target_id,u.payload_hash,u.sequence AS current_sequence
                FROM telegram_approval_reminder_attempts a JOIN telegram_approval_reminders u USING(unit_key) WHERE a.id=?''',(identifier,)).fetchone()
            if (not attempt or attempt['state']!='sending' or attempt['sequence']!=attempt['current_sequence']
                    or self._now()>=_time(attempt['lease_until']) or not self._current(db,row)):_fail()
            current=self._unit(db,row,attempt['kind'],attempt['target_id'])
            if (not current or current['unit_key']!=attempt['unit_key'] or current['payload_hash']!=attempt['payload_hash']
                    or current['reply_message_id']!=attempt['reply_message_id']):_fail()

    def _finish(self,identifier,state,*,message_id=None):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            attempt=db.execute('SELECT * FROM telegram_approval_reminder_attempts WHERE id=?',(identifier,)).fetchone()
            if not attempt or attempt['state']!='sending':return
            now=self._now()
            db.execute('UPDATE telegram_approval_reminder_attempts SET state=?,message_id=?,finished_at=?,reason=? WHERE id=?',
                (state,message_id,now.isoformat(),None if state=='sent' else 'notification_uncertain',identifier))
            if state=='sent':db.execute('UPDATE telegram_approval_reminders SET next_due_at=? WHERE unit_key=? AND sequence=?',
                ((now+timedelta(seconds=INTERVAL_SECONDS)).isoformat(),attempt['unit_key'],attempt['sequence']))

    async def run_once(self,row,send_message,limit=1):
        result={'sent':0,'uncertain':0,'held':0}
        if type(limit) is not int or not 1<=limit<=10 or not callable(send_message):return result
        async with self.lock:
            with self.store.connect() as db:
                if not self._current(db,row):return result
                units=self._candidates(db,row,rotate=True)
            for unit in units:
                if result['sent']+result['uncertain']>=limit:break
                identifier=self._claim(row,unit)
                if not identifier:continue
                count=len(unit['members'])
                text=(f'Nhắc duyệt: {count} video trong nhóm này vẫn đang chờ quyết định. Đã đến giờ đăng. '
                    'Mở tin gốc được trả lời để dùng nút Duyệt cả nhóm; mỗi video vẫn có nút Duyệt/Từ chối riêng.' if unit['kind']=='batch' else
                    'Nhắc duyệt: video này vẫn đang chờ quyết định sau giờ đăng. Mở tin gốc được trả lời để dùng nút Duyệt hoặc Từ chối.')
                text+=' Via chưa đăng video chưa được duyệt. Nếu còn chờ, bot nhắc lại sau 5 phút.'
                body={'chat_id':row['chat_id'],'text':text,'reply_to_message_id':unit['reply_message_id'],'allow_sending_without_reply':False}
                try:
                    self.validate_attempt(row,identifier)
                    response=await send_message(body,reminder_id=identifier)
                    self.validate_attempt(row,identifier)
                    if (not isinstance(response,dict) or type(response.get('message_id')) is not int or response['message_id']<1
                            or not isinstance(response.get('chat'),dict) or str(response['chat'].get('id'))!=row['chat_id']):raise ValueError()
                except asyncio.CancelledError:
                    self._finish(identifier,'uncertain');raise
                except Exception:self._finish(identifier,'uncertain');result['uncertain']+=1
                else:self._finish(identifier,'sent',message_id=response['message_id']);result['sent']+=1
        return result

    def snapshot(self,row):
        with self.store.connect() as db:
            counts=dict(db.execute("SELECT CASE WHEN state='sending' AND julianday(lease_until)<=julianday(?) THEN 'uncertain' ELSE state END AS public_state,count(*) FROM telegram_approval_reminder_attempts GROUP BY public_state",(self._now().isoformat(),)).fetchall())
            units=self._candidates(db,row) if self._current(db,row) else []
            dates=[]
            for unit in units:
                saved=db.execute('SELECT next_due_at FROM telegram_approval_reminders WHERE unit_key=?',(unit['unit_key'],)).fetchone()
                dates.append(_time(saved['next_due_at']) if saved else unit['first_due_at'])
        return {'interval_seconds':INTERVAL_SECONDS,'pending_targets':len(units),
            'next_reminder_at':min(dates).isoformat() if dates else None,
            'counts':{state:counts.get(state,0) for state in ('sending','sent','uncertain')}}
