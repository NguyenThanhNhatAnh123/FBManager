"""Durable per-schedule publication decisions; no transport or secret storage."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .errors import AppError


FIELDS = {'queue','job_id','page_id','asset_id','asset_sha256','asset_size','caption',
          'scheduled_at','timezone','actor_id','actor_revision'}
MESSAGE = 'Lịch đang chờ duyệt đúng video, Page, caption và giờ đăng.'
REMIND_UNTIL_DECISION = 'remind_until_decision_v1'
REMINDER_INTERVAL_SECONDS = 300


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _time(value):
    if not isinstance(value, str):raise ValueError()
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError()
    return result.astimezone(timezone.utc)


def _review_binding(value):
    """Read frozen policy from a binding, public ticket or SQLite row."""
    row=dict(value)
    binding=row.get('binding',row)
    if isinstance(binding,str):binding=json.loads(binding)
    return normalize_binding(binding)


def review_deadline(value):
    """None means genuinely no decision deadline, never an invented future date."""
    row=dict(value);binding=_review_binding(row)
    if binding.get('approval_policy')==REMIND_UNTIL_DECISION:return None
    deadline=row.get('approve_by')
    if deadline is None:deadline=binding.get('approve_by',binding['scheduled_at'])
    return _time(deadline)


def review_deadline_passed(value,now):
    deadline=review_deadline(value)
    return deadline is not None and now.astimezone(timezone.utc)>=deadline


def review_is_open(value,now):
    return dict(value).get('state')=='pending' and not review_deadline_passed(value,now)


is_reviewable=review_is_open


def normalize_review_batch(value):
    try:
        if not isinstance(value,dict) or set(value)!={'id','expected_count'}:raise ValueError()
        parsed=UUID(value['id'])
        if str(parsed)!=value['id'] or parsed.int==0:raise ValueError()
        if type(value['expected_count']) is not int or not 2<=value['expected_count']<=20:raise ValueError()
        return dict(value)
    except (ValueError,TypeError,KeyError,AttributeError):
        raise AppError('Nhóm duyệt video không hợp lệ.','publication_review_batch_invalid',422) from None


def normalize_binding(value):
    try:
        if not isinstance(value,dict) or not FIELDS<=set(value) or set(value)-FIELDS-{'publish_mode','approve_by','review_batch','approval_policy'}:raise ValueError()
        policy=value.get('approval_policy')
        if 'approval_policy' in value and policy!=REMIND_UNTIL_DECISION:raise ValueError()
        immediate='publish_mode' in value or 'approve_by' in value
        if immediate and (value.get('publish_mode')!='on_approval' or 'approve_by' not in value or value['queue']!='token'):raise ValueError()
        if value['queue'] not in ('token','chrome'):raise ValueError()
        if any(type(value[key]) is not int or not 1<=value[key]<=2**63-1 for key in ('job_id','actor_id')):raise ValueError()
        if type(value['actor_revision']) is not int or not 0<=value['actor_revision']<=2**63-1:raise ValueError()
        for key,pattern in (('page_id',r'[1-9][0-9]{0,31}'),('asset_id',r'[a-f0-9]{32}'),('asset_sha256',r'[a-f0-9]{64}')):
            if not isinstance(value[key],str) or not re.fullmatch(pattern,value[key]):raise ValueError()
        if type(value['asset_size']) is not int or not 0<value['asset_size']<=1073741824:raise ValueError()
        if not isinstance(value['caption'],str) or len(value['caption'])>5000 or '\x00' in value['caption']:raise ValueError()
        value['caption'].encode('utf-8')
        if not isinstance(value['timezone'],str) or len(value['timezone'])>120:raise ValueError()
        ZoneInfo(value['timezone'])
        result={**value,'scheduled_at':_time(value['scheduled_at']).isoformat()}
        if immediate:
            if policy==REMIND_UNTIL_DECISION:
                if value['approve_by'] is not None:raise ValueError()
            else:
                deadline=_time(value['approve_by'])
                if deadline<=_time(result['scheduled_at']):raise ValueError()
                result['approve_by']=deadline.isoformat()
        if 'review_batch' in value:result['review_batch']=normalize_review_batch(value['review_batch'])
        return result
    except (ValueError,TypeError,KeyError,OverflowError):
        raise AppError('Nội dung lịch chưa đủ để gắn yêu cầu duyệt.','publication_binding_invalid',422) from None


def token_binding(row, asset):
    result={'queue':'token','job_id':row['id'],'page_id':row['page_id'],
        'asset_id':row['asset_id'],'asset_sha256':asset['sha256'],'asset_size':asset['size'],
        'caption':row['caption'],'scheduled_at':row['scheduled_at'],'timezone':row['timezone'],
        'actor_id':row['connection_id'],'actor_revision':row['token_revision']}
    if row.get('publish_mode','scheduled')=='on_approval':
        result.update(publish_mode='on_approval',approve_by=row.get('approve_by'))
    if row.get('review_batch') is not None:result['review_batch']=row['review_batch']
    if row.get('approval_policy') is not None:result['approval_policy']=row['approval_policy']
    return result


class PublicationApprovals:
    def __init__(self,store,*,clock=None):
        self.store=store;self.clock=clock or (lambda:datetime.now(timezone.utc))
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS publication_approval_settings(
                    id INTEGER PRIMARY KEY CHECK(id=1),required INTEGER NOT NULL,gate_revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO publication_approval_settings VALUES(1,0,1);
                CREATE TABLE IF NOT EXISTS publication_approval_requests(
                    request_id TEXT PRIMARY KEY,queue TEXT NOT NULL,job_id INTEGER NOT NULL,
                    binding_hash TEXT NOT NULL,binding TEXT NOT NULL,state TEXT NOT NULL,
                    created_at TEXT NOT NULL,approved_at TEXT,decided_at TEXT,
                    approve_by TEXT NOT NULL,publish_window_end TEXT NOT NULL,
                    UNIQUE(queue,job_id,binding_hash));
                CREATE TABLE IF NOT EXISTS publication_approval_decisions(
                    event_key TEXT PRIMARY KEY,request_id TEXT NOT NULL,binding_hash TEXT NOT NULL,
                    decision TEXT NOT NULL,actor_id TEXT NOT NULL,chat_id TEXT NOT NULL,
                    update_id TEXT NOT NULL,message_id TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS publication_approval_pending ON publication_approval_requests(state,approve_by);
            ''')
        store.publication_approvals=self

    def _now(self):return self.clock().astimezone(timezone.utc)

    def settings(self,db=None):
        if db is None:
            with self.store.connect() as connection:return self.settings(connection)
        row=db.execute('SELECT required,gate_revision FROM publication_approval_settings WHERE id=1').fetchone()
        if row is None or row['required'] not in (0,1) or type(row['gate_revision']) is not int or row['gate_revision']<1:
            raise AppError('Chưa đọc được cổng duyệt đăng.','publication_approval_unavailable',503)
        return {'required':bool(row['required']),'gate_revision':row['gate_revision']}

    def configure(self,required,expected_revision):
        if type(required) is not bool or type(expected_revision) is not int or expected_revision<1:
            raise AppError('Cấu hình duyệt không hợp lệ.','publication_settings_invalid',422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.settings(db)
            if current['gate_revision']!=expected_revision:
                raise AppError('Cổng duyệt đã thay đổi. Đọc lại trước khi lưu.','publication_settings_conflict',409)
            if required!=current['required']:
                db.execute('UPDATE publication_approval_settings SET required=?,gate_revision=gate_revision+1 WHERE id=1',(int(required),))
            return self.settings(db)

    def _projection(self,row,required,*,changed=False):
        state='binding_changed' if changed else row['state'] if row else 'pending'
        now=self._now()
        if row and not changed and (state=='pending' and review_deadline_passed(row,now)
                or state=='approved' and now>_time(row['publish_window_end'])):state='expired'
        allowed=not required or state=='approved'
        reason=None if allowed else {'expired':'publication_approval_expired','rejected':'publication_approval_rejected',
            'cancelled':'publication_approval_cancelled','binding_changed':'publication_binding_changed'}.get(state,'publication_approval_required')
        result={'required':required,'can_publish':allowed,'state':state if required else 'not_required','reason':reason,
            'request_id':row['request_id'] if row else None,'binding_hash':row['binding_hash'] if row else None,
            'approve_by':review_deadline(row).isoformat() if row and review_deadline(row) is not None else None,
            'publish_window_end':row['publish_window_end'] if row and not (
                state=='pending' and _review_binding(row).get('approval_policy')==REMIND_UNTIL_DECISION) else None}
        if row and _review_binding(row).get('approval_policy')==REMIND_UNTIL_DECISION:
            result.update(approval_policy=REMIND_UNTIL_DECISION,reminder_interval_seconds=REMINDER_INTERVAL_SECONDS,
                due_at=_review_binding(row)['scheduled_at'],approved_at=row['approved_at'])
            if row['approved_at'] is None:result['publish_window_end']=None
        return result

    def verdict(self,binding,db=None):
        value=normalize_binding(binding);digest=hashlib.sha256(_canonical(value).encode()).hexdigest()
        if db is None:
            with self.store.connect() as connection:return self.verdict(value,connection)
        required=self.settings(db)['required'] or self._manual_required(value,db)
        row=db.execute('SELECT * FROM publication_approval_requests WHERE queue=? AND job_id=? AND binding_hash=?',(value['queue'],value['job_id'],digest)).fetchone()
        changed=False
        if row is None:
            row=db.execute('SELECT * FROM publication_approval_requests WHERE queue=? AND job_id=? ORDER BY created_at DESC,request_id LIMIT 1',(value['queue'],value['job_id'])).fetchone()
            changed=row is not None
        return self._projection(row,required,changed=changed)

    @staticmethod
    def _manual_required(binding,db):
        # Human review belongs to this job even if the global switch is later off.
        if binding.get('approval_policy')==REMIND_UNTIL_DECISION or binding.get('publish_mode')=='on_approval' or 'review_batch' in binding:return True
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_pipeline_approved_videos'").fetchone():return False
        if db.execute("SELECT 1 FROM idea_pipeline_approved_videos WHERE video_sha256=? AND status='manual_review_pending'",
            (binding['asset_sha256'],)).fetchone():return True
        if binding['queue']!='token':return False
        return db.execute('''SELECT 1 FROM reel_jobs j JOIN idea_pipeline_approved_videos a
            ON a.reservation_id=j.request_id WHERE j.id=? AND a.status='manual_review_pending' LIMIT 1''',
            (binding['job_id'],)).fetchone() is not None

    def ensure(self,binding,db=None):
        value=normalize_binding(binding)
        if db is None:
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE');return self.ensure(value,connection)
        if self.settings(db)['required'] or self._manual_required(value,db):
            encoded=_canonical(value);digest=hashlib.sha256(encoded.encode()).hexdigest()
            request_id=str(uuid5(NAMESPACE_URL,'via-publication-approval:'+digest))
            # The historical NOT NULL column remains a due-sort anchor for the
            # new policy. It is not an expiry and is never exposed as one.
            deadline=review_deadline(value) or _time(value['scheduled_at'])
            db.execute("UPDATE publication_approval_requests SET state='cancelled',decided_at=COALESCE(decided_at,?) WHERE queue=? AND job_id=? AND binding_hash<>? AND state IN ('pending','approved')",
                (self._now().isoformat(),value['queue'],value['job_id'],digest))
            db.execute('''INSERT OR IGNORE INTO publication_approval_requests
                VALUES(?,?,?,?,?,'pending',?,NULL,NULL,?,?)''',
                (request_id,value['queue'],value['job_id'],digest,encoded,self._now().isoformat(),
                 deadline.isoformat(),(deadline+timedelta(minutes=30)).isoformat()))
        return self.verdict(value,db)

    def require(self,binding,db=None):
        verdict=self.verdict(binding,db)
        if not verdict['can_publish']:raise AppError(MESSAGE,verdict['reason'],409)
        return verdict

    def _row(self,row):
        if row is None:raise AppError('Không có yêu cầu duyệt này.','publication_approval_not_found',404)
        result={key:row[key] for key in ('request_id','binding_hash','state','created_at','approved_at','decided_at','approve_by','publish_window_end')}
        result['binding']=json.loads(row['binding'])
        result['state']=self._projection(row,True)['state']
        if result['binding'].get('approval_policy')==REMIND_UNTIL_DECISION:
            result['approve_by']=None
            if result['approved_at'] is None:result['publish_window_end']=None
            result.update(approval_policy=REMIND_UNTIL_DECISION,reminder_interval_seconds=REMINDER_INTERVAL_SECONDS,
                due_at=result['binding']['scheduled_at'])
        return result

    def get(self,request_id):
        try:
            if not isinstance(request_id,str) or str(UUID(request_id))!=request_id:raise ValueError()
        except ValueError:raise AppError('Mã yêu cầu duyệt không hợp lệ.','publication_approval_not_found',404) from None
        with self.store.connect() as db:return self._row(db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?',(request_id,)).fetchone())

    def pending(self,limit=100,offset=0):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=2**63-1:
            raise AppError('Giới hạn danh sách không hợp lệ.','publication_list_invalid',422)
        with self.store.connect() as db:
            required=self.settings(db)['required']
            stamp=self._now().isoformat()
            where=" WHERE state='pending' AND (json_extract(binding,'$.approval_policy')='remind_until_decision_v1' OR julianday(approve_by)>julianday(?))"
            if not required:
                forced="(json_extract(publication_approval_requests.binding,'$.approval_policy')='remind_until_decision_v1' OR json_extract(publication_approval_requests.binding,'$.publish_mode')='on_approval' OR json_type(publication_approval_requests.binding,'$.review_batch')='object')"
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_pipeline_approved_videos'").fetchone():
                    forced+=" OR EXISTS(SELECT 1 FROM idea_pipeline_approved_videos a WHERE a.status='manual_review_pending' AND (a.video_sha256=json_extract(publication_approval_requests.binding,'$.asset_sha256') OR (publication_approval_requests.queue='token' AND a.reservation_id=(SELECT j.request_id FROM reel_jobs j WHERE j.id=publication_approval_requests.job_id))))"
                where+=' AND ('+forced+')'
            total=db.execute('SELECT COUNT(*) FROM publication_approval_requests'+where,(stamp,)).fetchone()[0]
            found=db.execute('SELECT * FROM publication_approval_requests'+where+' ORDER BY approve_by,request_id LIMIT ? OFFSET ?',(stamp,limit,offset)).fetchall()
            return {'items':[self._row(row) for row in found],'total':total,'limit':limit,'offset':offset}

    def decide(self,request_id,binding_hash,decision,*,actor_id,chat_id,update_id,message_id):
        if decision not in ('approve','reject') or not isinstance(binding_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',binding_hash):
            raise AppError('Quyết định duyệt không hợp lệ.','publication_decision_invalid',422)
        values=[]
        for value in (actor_id,chat_id,update_id,message_id):
            if type(value) is int:value=str(value)
            if not isinstance(value,str) or not re.fullmatch(r'-?[0-9]{1,20}',value):
                raise AppError('Danh tính quyết định không hợp lệ.','publication_decision_invalid',422)
            values.append(value)
        actor,chat,update,message=values
        if int(actor)<=0 or int(chat)==0 or int(update)<0 or int(message)<=0:
            raise AppError('Danh tính quyết định không hợp lệ.','publication_decision_invalid',422)
        event=hashlib.sha256(_canonical([chat,update]).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?',(request_id,)).fetchone()
            if row is None or row['binding_hash']!=binding_hash:
                raise AppError('Yêu cầu duyệt không còn khớp nội dung.','publication_binding_changed',409)
            old=db.execute('SELECT * FROM publication_approval_decisions WHERE event_key=?',(event,)).fetchone()
            if old:
                if any(old[key]!=value for key,value in {'request_id':request_id,'binding_hash':binding_hash,'decision':decision,'actor_id':actor,'chat_id':chat,'message_id':message}.items()):
                    raise AppError('Quyết định đã được dùng cho yêu cầu khác.','publication_decision_conflict',409)
                return self._row(row)
            desired='approved' if decision=='approve' else 'rejected'
            if row['state'] not in ('pending',desired):
                raise AppError('Yêu cầu này đã được quyết định hoặc hủy.','publication_decision_conflict',409)
            if decision=='approve' and row['state']=='pending' and review_deadline_passed(row,self._now()):
                raise AppError('Đã qua hạn duyệt giờ đăng này. Chọn một lịch tương lai.','publication_approval_expired',409)
            stamp=self._now().isoformat()
            db.execute('INSERT INTO publication_approval_decisions VALUES(?,?,?,?,?,?,?,?,?)',
                (event,request_id,binding_hash,decision,actor,chat,update,message,stamp))
            self._apply_decision(db,row,decision,stamp)
            return self._row(db.execute('SELECT * FROM publication_approval_requests WHERE request_id=?',(request_id,)).fetchone())

    def _apply_decision(self,db,row,decision,stamp):
        """Internal shared updater after transport/member validation in one transaction."""
        if decision not in ('approve','reject'):raise AppError('Quyết định không hợp lệ.','publication_decision_invalid',422)
        desired='approved' if decision=='approve' else 'rejected'
        if row['state'] not in ('pending',desired):
            raise AppError('Yêu cầu đã thay đổi.','publication_decision_conflict',409)
        instant=_time(stamp)
        if decision=='approve' and row['state']=='pending' and review_deadline_passed(row,instant):
            raise AppError('Đã qua hạn duyệt video này.','publication_approval_expired',409)
        approved_at=(row['approved_at'] or instant.isoformat()) if decision=='approve' else row['approved_at']
        window=row['publish_window_end']
        binding=normalize_binding(json.loads(row['binding']))
        if decision=='approve' and (binding.get('publish_mode')=='on_approval' or binding.get('approval_policy')==REMIND_UNTIL_DECISION):
            anchor=max(_time(approved_at),_time(binding['scheduled_at']))
            window=(anchor+timedelta(minutes=30)).isoformat()
        db.execute('UPDATE publication_approval_requests SET state=?,decided_at=COALESCE(decided_at,?),approved_at=?,publish_window_end=? WHERE request_id=? AND binding_hash=?',
            (desired,instant.isoformat(),approved_at,window,row['request_id'],row['binding_hash']))

    def withdraw(self,queue,job_id,db=None):
        if queue not in ('token','chrome') or type(job_id) is not int or job_id<1:return
        if db is None:
            with self.store.connect() as connection:return self.withdraw(queue,job_id,connection)
        db.execute("UPDATE publication_approval_requests SET state='cancelled',decided_at=COALESCE(decided_at,?) WHERE queue=? AND job_id=? AND state IN ('pending','approved')",(self._now().isoformat(),queue,job_id))
