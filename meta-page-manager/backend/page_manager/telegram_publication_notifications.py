"""Hash-bound publication status outbox. No Telegram client or publishing code."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from .publication_approvals import normalize_binding,REMIND_UNTIL_DECISION


STATES=('pending','sending','sent','uncertain','invalidated')
JOB_STATES={'scheduled','waiting_page','waiting_profile','starting','opening','switching','uploading',
    'processing','caption','review','publishing','verifying','completed','needs_user','needs_review','cancelled','interrupted'}
PAUSED={'needs_user','needs_review','cancelled','interrupted'}


def _canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def _hash(value):return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()
def _moment(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError()
    return result.astimezone(timezone.utc)


def _scope(value):
    try:
        result={key:value[key] for key in ('revision','policy_revision','chat_id','bot_id')}
        if any(type(result[key]) is not int or result[key]<1 for key in ('revision','policy_revision')):return None
        for key in ('chat_id','bot_id'):
            if type(result[key]) is int:result[key]=str(result[key])
            if not isinstance(result[key],str) or not re.fullmatch(r'-?[1-9][0-9]{0,19}',result[key]):return None
        if int(result['bot_id'])<1:return None
        return result
    except (TypeError,KeyError,ValueError):return None


def _scope_key(scope):return _hash([scope['policy_revision'],scope['chat_id'],scope['bot_id']])


def _reel(value):
    try:
        url=urlsplit(value)
        match=re.fullmatch(r'/reels?/([1-9][0-9]{0,31})/?',url.path)
        if (url.scheme=='https' and url.hostname in ('facebook.com','www.facebook.com','m.facebook.com')
                and url.netloc==url.hostname and not url.query and not url.fragment and match):
            return 'https://www.facebook.com/reel/'+match[1]
    except (TypeError,ValueError):pass
    return None


def _local(binding):
    value=_moment(binding['scheduled_at']).astimezone(ZoneInfo(binding['timezone']))
    offset=value.strftime('%z')
    return value.strftime('%d/%m/%Y %H:%M')+' ('+binding['timezone']+', UTC'+offset[:3]+':'+offset[3:]+')'


class PublicationNotifications:
    def __init__(self,store,ledger,*,status_resolver,clock=None):
        self.store,self.ledger,self.status_resolver=store,ledger,status_resolver
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.owner=uuid4().hex;self.lock=asyncio.Lock()
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS telegram_publication_outbox(
                    id TEXT PRIMARY KEY,request_id TEXT NOT NULL,binding_hash TEXT NOT NULL,
                    policy_revision INTEGER NOT NULL,chat_id TEXT NOT NULL,bot_id TEXT NOT NULL,
                    event TEXT NOT NULL,event_key TEXT NOT NULL,state TEXT NOT NULL,
                    expected_status TEXT,published_url TEXT,announces_ready INTEGER NOT NULL DEFAULT 0,
                    text TEXT,message_id INTEGER,reason TEXT,owner TEXT,lease_until TEXT,send_revision INTEGER,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS telegram_publication_pending ON telegram_publication_outbox(state,policy_revision,chat_id,bot_id);
                CREATE TABLE IF NOT EXISTS telegram_publication_watch(
                    scope_key TEXT NOT NULL,request_id TEXT NOT NULL,binding_hash TEXT NOT NULL,
                    last_status TEXT NOT NULL,transition INTEGER NOT NULL,ready_announced INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(scope_key,request_id));
                CREATE TABLE IF NOT EXISTS telegram_publication_cursor(
                    scope_key TEXT PRIMARY KEY,scan_cursor INTEGER NOT NULL DEFAULT 0,send_cursor INTEGER NOT NULL DEFAULT 0);
            ''')

    def _now(self):return self.clock().astimezone(timezone.utc)

    def _current(self,scope,db=None):
        if db is None:
            with self.store.connect() as conn:return self._current(scope,conn)
        try:
            row=db.execute('''SELECT revision,policy_revision,chat_id,bot_id,checked_ok,checked_revision,
                encrypted_token IS NOT NULL AS has_token FROM telegram_approval_settings WHERE id=1''').fetchone()
            return bool(row and row['has_token'] and row['checked_ok']==1 and row['checked_revision']==row['revision']
                and all(row[key]==scope[key] for key in scope))
        except Exception:return False

    def _invalidate_changed_scopes(self):
        with self.store.connect() as db:
            row=db.execute('SELECT policy_revision,chat_id,bot_id,encrypted_token IS NOT NULL AS has_token FROM telegram_approval_settings WHERE id=1').fetchone()
            if row is None:return
            db.execute("""UPDATE telegram_publication_outbox SET state='invalidated',reason='configuration_changed',updated_at=?
                WHERE state='pending' AND (?=0 OR policy_revision<>? OR chat_id<>COALESCE(?,'') OR bot_id<>COALESCE(?,''))""",
                (self._now().isoformat(),row['has_token'],row['policy_revision'],row['chat_id'],row['bot_id']))

    def _ticket(self,scope,request_id,db=None):
        if db is None:
            with self.store.connect() as conn:return self._ticket(scope,request_id,conn)
        row=db.execute('''SELECT r.*,r.rowid AS seq FROM publication_approval_requests r
            JOIN telegram_approval_delivery d ON d.request_id=r.request_id AND d.binding_hash=r.binding_hash
            WHERE r.request_id=? AND d.connection_revision=? AND d.chat_id=?
              AND d.state IN ('sent','decided') AND d.message_id>0 AND d.video_message_id>0
              AND EXISTS(SELECT 1 FROM publication_approval_decisions x WHERE x.request_id=r.request_id
                  AND x.binding_hash=r.binding_hash AND x.chat_id=? AND x.decision IN ('approve','reject'))''',
            (request_id,scope['policy_revision'],scope['chat_id'],scope['chat_id'])).fetchone()
        if row is None:return None
        value=dict(row);binding=normalize_binding(json.loads(value['binding']))
        if _hash(binding)!=value['binding_hash']:return None
        value['binding']=binding
        value['decision']='approve' if value['approved_at'] is not None else 'reject'
        return value

    async def _status(self,ticket):
        binding=ticket['binding'];result=self.status_resolver(binding['queue'],binding['job_id'])
        if inspect.isawaitable(result):result=await result
        if (not isinstance(result,dict) or result.get('status') not in JOB_STATES
                or _hash(normalize_binding(result.get('binding')))!=ticket['binding_hash']):return None
        name=result.get('page_name')
        name=re.sub(r'[\x00-\x1f\x7f]',' ',name)[:100] if isinstance(name,str) else ''
        return {'status':result['status'],'ready':result.get('ready') is True and result['status']=='scheduled' and ticket['decision']=='approve',
                'published_url':_reel(result.get('published_url')),'page_name':name,
                'reason':'approval_expired' if result.get('step')=='approval_expired' else None}

    def _candidates(self,scope,limit):
        key=_scope_key(scope)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._current(scope,db):return []
            db.execute('INSERT OR IGNORE INTO telegram_publication_cursor(scope_key) VALUES(?)',(key,))
            cursor=db.execute('SELECT scan_cursor FROM telegram_publication_cursor WHERE scope_key=?',(key,)).fetchone()[0]
            rows=db.execute('''SELECT r.request_id,r.rowid AS seq FROM publication_approval_requests r
                JOIN telegram_approval_delivery d ON d.request_id=r.request_id AND d.binding_hash=r.binding_hash
                WHERE d.connection_revision=? AND d.chat_id=? AND d.state IN ('sent','decided')
                  AND d.message_id>0 AND d.video_message_id>0 AND r.decided_at IS NOT NULL
                  AND NOT EXISTS(SELECT 1 FROM telegram_publication_outbox n WHERE n.request_id=r.request_id
                    AND n.policy_revision=d.connection_revision AND n.chat_id=d.chat_id AND n.bot_id=?
                    AND n.event='published' AND n.state IN ('sending','sent','uncertain'))
                ORDER BY CASE WHEN r.rowid>? THEN 0 ELSE 1 END,r.rowid LIMIT ?''',
                (scope['policy_revision'],scope['chat_id'],scope['bot_id'],cursor,limit)).fetchall()
            if rows:db.execute('UPDATE telegram_publication_cursor SET scan_cursor=? WHERE scope_key=?',(rows[-1]['seq'],key))
            return [row['request_id'] for row in rows]

    def _enqueue(self,db,scope,ticket,event,event_key,*,expected_status=None,url=None):
        identifier=_hash([_scope_key(scope),ticket['request_id'],ticket['binding_hash'],event,event_key])
        stamp=self._now().isoformat()
        db.execute('''INSERT OR IGNORE INTO telegram_publication_outbox
            (id,request_id,binding_hash,policy_revision,chat_id,bot_id,event,event_key,state,expected_status,published_url,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?,?)''',
            (identifier,ticket['request_id'],ticket['binding_hash'],scope['policy_revision'],scope['chat_id'],scope['bot_id'],event,event_key,expected_status,url,stamp,stamp))

    def _plan(self,scope,ticket,status):
        key=_scope_key(scope)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._current(scope,db) or self._ticket(scope,ticket['request_id'],db) is None:return
            watch=db.execute('SELECT * FROM telegram_publication_watch WHERE scope_key=? AND request_id=?',(key,ticket['request_id'])).fetchone()
            transition=watch['transition']+int(watch['last_status']!=status['status']) if watch else 1
            db.execute('''INSERT INTO telegram_publication_watch VALUES(?,?,?,?,?,0)
                ON CONFLICT(scope_key,request_id) DO UPDATE SET last_status=excluded.last_status,transition=excluded.transition''',
                (key,ticket['request_id'],ticket['binding_hash'],status['status'],transition))
            self._enqueue(db,scope,ticket,'decision_ack',ticket['decision'])
            if ticket['decision']!='approve':return
            ack=db.execute("SELECT state FROM telegram_publication_outbox WHERE request_id=? AND policy_revision=? AND chat_id=? AND bot_id=? AND event='decision_ack'",(ticket['request_id'],scope['policy_revision'],scope['chat_id'],scope['bot_id'])).fetchone()
            ready_claimed=watch['ready_announced'] if watch else False
            if status['ready'] and not ready_claimed and ack and ack['state'] in ('sent','uncertain'):
                self._enqueue(db,scope,ticket,'schedule_ready','ready')
            if status['status'] in PAUSED:
                self._enqueue(db,scope,ticket,status['status'],str(transition),expected_status=status['status'])
            if status['status']=='completed' and status['published_url']:
                attempted=db.execute("SELECT 1 FROM telegram_publication_outbox WHERE request_id=? AND policy_revision=? AND chat_id=? AND bot_id=? AND event='published' AND state IN ('sending','sent','uncertain')",(ticket['request_id'],scope['policy_revision'],scope['chat_id'],scope['bot_id'])).fetchone()
                if not attempted:self._enqueue(db,scope,ticket,'published',status['published_url'],expected_status='completed',url=status['published_url'])

    def _pending(self,scope,limit):
        key=_scope_key(scope)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Only expired claims are recovery holds; a second live instance
            # must never reinterpret an in-flight send as safe to send again.
            db.execute("UPDATE telegram_publication_outbox SET state='uncertain',reason='interrupted',updated_at=? WHERE state='sending' AND julianday(lease_until)<=julianday(?)",(self._now().isoformat(),self._now().isoformat()))
            db.execute('INSERT OR IGNORE INTO telegram_publication_cursor(scope_key) VALUES(?)',(key,))
            cursor=db.execute('SELECT send_cursor FROM telegram_publication_cursor WHERE scope_key=?',(key,)).fetchone()[0]
            rows=db.execute("SELECT *,rowid AS seq FROM telegram_publication_outbox WHERE state='pending' AND policy_revision=? AND chat_id=? AND bot_id=? ORDER BY CASE WHEN rowid>? THEN 0 ELSE 1 END,rowid LIMIT ?",(scope['policy_revision'],scope['chat_id'],scope['bot_id'],cursor,limit)).fetchall()
            if rows:db.execute('UPDATE telegram_publication_cursor SET send_cursor=? WHERE scope_key=?',(rows[-1]['seq'],key))
            return [dict(row) for row in rows]

    def _body(self,item,ticket,status):
        binding=ticket['binding'];event=item['event'];immediate=binding.get('publish_mode')=='on_approval'
        if event=='decision_ack':
            title='Đã nhận duyệt video.' if ticket['decision']=='approve' else 'Đã nhận quyết định không duyệt video.'
            detail=('Lịch nội bộ Via đã lưu tại ứng dụng và sẵn sàng chờ đến giờ đăng.' if status['ready']
                else 'Lịch nội bộ Via đã lưu tại ứng dụng; còn chờ điều kiện đăng hoặc kiểm tra tiến trình.')
            if immediate:detail=('Video đã được duyệt và sẵn sàng gửi đăng ngay khi Page và kết nối sẵn sàng.' if status['ready']
                else 'Quyết định đã lưu; Via kiểm tra điều kiện và tiến trình trước khi gửi đăng.')
            if ticket['decision']!='approve':detail='Quyết định Từ chối đã lưu. Lịch đang bị chặn đăng.'
        elif event=='schedule_ready':
            title='Video sẵn sàng gửi đăng.' if immediate else 'Lịch sẵn sàng tại ứng dụng.'
            detail='Via sẽ kiểm tra lại điều kiện trước khi gửi đăng ngay sau duyệt.' if immediate else 'Ứng dụng sẽ kiểm tra lại điều kiện khi đến giờ đăng.'
        elif event=='published':title='Đã đăng và xác nhận Reel.';detail=status['published_url']
        elif event=='cancelled':title='Lịch đã được hủy tại ứng dụng.';detail='Kiểm tra lịch đã lưu để xem tiến trình được giữ lại.'
        else:title='Lịch cần bạn kiểm tra.';detail={'needs_user':'Cần kiểm tra Page, nguồn hoặc thao tác đang chờ.',
            'needs_review':'Cần đối chiếu kết quả trước khi tiếp tục; ứng dụng không tự gửi lại lệnh đăng.',
            'interrupted':'Tiến trình bị gián đoạn; mở ứng dụng để kiểm tra trước khi tiếp tục.'}[event]
        if event=='needs_user' and status.get('reason')=='approval_expired':detail='Lịch đã quá cửa sổ đăng. Chọn lịch tương lai và duyệt lại.'
        route='Page Manager' if binding['queue']=='token' else 'Chrome Manager'
        page=(status['page_name']+' ('+binding['page_id']+')') if status.get('page_name') else binding['page_id']
        schedule=('Chế độ: Đăng ngay sau khi bạn duyệt.\nDuyệt không hết hạn; bot nhắc mỗi 5 phút khi đến hạn và còn chờ quyết định.' if binding.get('approval_policy')==REMIND_UNTIL_DECISION
            else 'Chế độ: Đăng ngay sau khi bạn duyệt.\nHạn duyệt: '+_local({**binding,'scheduled_at':binding['approve_by']})) if immediate else 'Giờ đã chọn: '+_local(binding)
        return title+'\n'+detail+'\nPage '+page+' · '+route+' · lịch #'+str(binding['job_id'])+'\n'+schedule

    def _invalidate(self,item,reason):
        with self.store.connect() as db:
            db.execute("UPDATE telegram_publication_outbox SET state='invalidated',reason=?,updated_at=? WHERE id=? AND state='pending'",(reason,self._now().isoformat(),item['id']))

    def _claim(self,scope,item,ticket,status,text):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._current(scope,db) or self._ticket(scope,ticket['request_id'],db) is None:return False
            announces=status['ready'] and item['event'] in ('decision_ack','schedule_ready') and ticket['decision']=='approve'
            changed=db.execute("UPDATE telegram_publication_outbox SET state='sending',text=?,announces_ready=?,owner=?,lease_until=?,send_revision=?,updated_at=? WHERE id=? AND state='pending'",
                (text,int(announces),self.owner,(self._now()+timedelta(seconds=120)).isoformat(),scope['revision'],self._now().isoformat(),item['id']))
            if changed.rowcount and announces:
                db.execute('UPDATE telegram_publication_watch SET ready_announced=1 WHERE scope_key=? AND request_id=?',(_scope_key(scope),ticket['request_id']))
            return changed.rowcount==1

    def _finish(self,item,state,*,message_id=None,reason=None):
        with self.store.connect() as db:
            db.execute("UPDATE telegram_publication_outbox SET state=?,message_id=?,reason=?,updated_at=? WHERE id=? AND state='sending' AND owner=?",(state,message_id,reason,self._now().isoformat(),item['id'],self.owner))

    async def run_once(self,scope,send_message,limit=20):
        result={'scanned':0,'sent':0,'uncertain':0,'held':0};scope=_scope(scope)
        if scope is None or not callable(send_message) or type(limit) is not int or not 1<=limit<=100:return result
        async with self.lock:
            try:self._invalidate_changed_scopes()
            except Exception:result['held']+=1;return result
            if not self._current(scope):return result
            for request_id in self._candidates(scope,limit):
                result['scanned']+=1
                try:
                    ticket=self._ticket(scope,request_id)
                    status=await self._status(ticket) if ticket else None
                    if status is None:result['held']+=1;continue
                    self._plan(scope,ticket,status)
                except Exception:result['held']+=1
            for item in self._pending(scope,limit):
                try:
                    if not self._current(scope):break
                    ticket=self._ticket(scope,item['request_id'])
                    if ticket is None:self._invalidate(item,'scope_changed');continue
                    status=await self._status(ticket)
                    if status is None:result['held']+=1;continue
                    if item['event']=='schedule_ready' and not status['ready']:result['held']+=1;continue
                    if item['expected_status'] and status['status']!=item['expected_status']:
                        self._invalidate(item,'status_changed');continue
                    if item['event']=='published' and status['published_url']!=item['published_url']:
                        self._invalidate(item,'publication_unverified');continue
                    if not self._claim(scope,item,ticket,status,self._body(item,ticket,status)):continue
                    try:
                        response=send_message(self._body(item,ticket,status),request_id=ticket['request_id'])
                        if inspect.isawaitable(response):response=await response
                        if (not isinstance(response,dict) or type(response.get('message_id')) is not int or not 0<response['message_id']<=2**63-1
                                or not isinstance(response.get('chat'),dict) or str(response['chat'].get('id'))!=scope['chat_id']
                                or not self._current(scope)):
                            raise ValueError()
                    except asyncio.CancelledError:
                        self._finish(item,'uncertain',reason='interrupted');raise
                    except Exception:
                        self._finish(item,'uncertain',reason='notification_uncertain');result['uncertain']+=1
                    else:self._finish(item,'sent',message_id=response['message_id']);result['sent']+=1
                except asyncio.CancelledError:raise
                except Exception:result['held']+=1
        return result

    def snapshot(self,scope=None,limit=20):
        empty={'counts':{state:0 for state in STATES},'items':[],'total':0}
        if type(limit) is not int or not 1<=limit<=100:return empty
        params=[];where=''
        if scope is not None:
            scope=_scope(scope)
            if scope is None:return empty
            where=' WHERE policy_revision=? AND chat_id=? AND bot_id=?';params=[scope['policy_revision'],scope['chat_id'],scope['bot_id']]
        with self.store.connect() as db:
            counts=dict(db.execute('SELECT state,COUNT(*) FROM telegram_publication_outbox'+where+' GROUP BY state',params).fetchall())
            rows=db.execute('SELECT request_id,event,state,message_id,reason,created_at,updated_at FROM telegram_publication_outbox'+where+' ORDER BY rowid DESC LIMIT ?',(*params,limit)).fetchall()
        result={'counts':{state:counts.get(state,0) for state in STATES},'items':[dict(row) for row in rows]}
        result['total']=sum(result['counts'].values());return result
