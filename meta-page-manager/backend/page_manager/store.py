"""Private SQLite catalog; Page and post identity are canonical across tokens."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import hashlib
from pathlib import Path
import sqlite3
import re
from uuid import UUID
from .errors import AppError
from .reel_calendar import validate_calendar, validate_sort, local_date_key


SCOPED_USER = "c.token_type='user' AND NULLIF(c.business_id,'') IS NOT NULL"
UNVERIFIED_BUSINESS_SCOPE = f"({SCOPED_USER} AND c.business_scope_revision IS NOT c.token_revision)"
ACTIVE_PAGE_LINK = ("cp.active=1 AND cp.token_revision=c.token_revision AND c.connected=1 "
                    f"AND c.encrypted_token IS NOT NULL AND NOT {UNVERIFIED_BUSINESS_SCOPE}")


def now():return datetime.now(timezone.utc).isoformat()
def dump(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();data=self.root/'data';data.mkdir(parents=True,exist_ok=True)
        if data.is_symlink() or data.resolve()!=data:raise AppError('Thư mục dữ liệu riêng không hợp lệ.')
        self.path=data/'page-manager.sqlite3'
        if self.path.is_symlink() or self.path.exists() and self.path.stat().st_nlink>1:raise AppError('File dữ liệu riêng không hợp lệ.')
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS connections(
              id INTEGER PRIMARY KEY AUTOINCREMENT,label TEXT NOT NULL,token_type TEXT NOT NULL,
              app_id TEXT,business_id TEXT,encrypted_token TEXT,token_revision INTEGER NOT NULL DEFAULT 1,
              identity_id TEXT,identity_name TEXT,status TEXT NOT NULL DEFAULT 'unchecked',permissions TEXT,
              expires_at TEXT,checked_at TEXT,synced_at TEXT,error TEXT,connected INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
              business_scope_revision INTEGER);
            CREATE TABLE IF NOT EXISTS pages(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT,link TEXT,details TEXT NOT NULL DEFAULT '{}',
              checked_at TEXT,error TEXT,posts_checked_at TEXT,posts_has_more INTEGER NOT NULL DEFAULT 0,
              posts_error TEXT,posts_oldest TEXT,posts_latest_ids TEXT NOT NULL DEFAULT '[]',last_metadata_started_at TEXT);
            CREATE TABLE IF NOT EXISTS connection_pages(
              connection_id INTEGER NOT NULL REFERENCES connections(id),page_id TEXT NOT NULL REFERENCES pages(id),
              sources TEXT NOT NULL DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,encrypted_page_token TEXT,
              token_revision INTEGER NOT NULL,tasks TEXT,PRIMARY KEY(connection_id,page_id));
            CREATE TABLE IF NOT EXISTS posts(
              page_id TEXT NOT NULL REFERENCES pages(id),post_id TEXT NOT NULL,message TEXT NOT NULL,
              created_time TEXT NOT NULL,permalink_url TEXT,PRIMARY KEY(page_id,post_id));
            CREATE INDEX IF NOT EXISTS post_dates ON posts(page_id,created_time DESC);
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT NOT NULL,status TEXT NOT NULL,message TEXT NOT NULL,
              connection_label TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS page_edits(
              request_id TEXT PRIMARY KEY,connection_id INTEGER NOT NULL REFERENCES connections(id),
              token_revision INTEGER NOT NULL,page_id TEXT NOT NULL REFERENCES pages(id),kind TEXT NOT NULL,
              payload_hash TEXT NOT NULL,outcome TEXT NOT NULL DEFAULT 'started',message TEXT NOT NULL,
              requires_refresh INTEGER NOT NULL DEFAULT 1,checkpoint TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS page_edits_page ON page_edits(page_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS page_edits_accepted_page ON page_edits(page_id) WHERE outcome='accepted';
            CREATE TABLE IF NOT EXISTS chrome_page_setup_receipts(
              receipt_id TEXT PRIMARY KEY,page_id TEXT NOT NULL,field_fingerprints TEXT NOT NULL,
              created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS chrome_page_setup_page ON chrome_page_setup_receipts(page_id);
            CREATE INDEX IF NOT EXISTS pages_cached_link ON pages(link);
            CREATE TABLE IF NOT EXISTS reel_assets(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,
              sha256 TEXT NOT NULL,mime TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reel_jobs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL UNIQUE,payload_hash TEXT NOT NULL,
              page_id TEXT NOT NULL REFERENCES pages(id),page_name TEXT NOT NULL,
              connection_id INTEGER NOT NULL REFERENCES connections(id),connection_label TEXT NOT NULL,
              token_revision INTEGER NOT NULL,asset_id TEXT NOT NULL REFERENCES reel_assets(id),asset_name TEXT NOT NULL,
              caption TEXT NOT NULL,scheduled_at TEXT NOT NULL,scheduled_local TEXT NOT NULL,timezone TEXT NOT NULL,fold INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'scheduled',step TEXT NOT NULL DEFAULT 'scheduled',message TEXT NOT NULL,
              checkpoint TEXT NOT NULL DEFAULT '{}',video_id TEXT,published_url TEXT,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,archived_at TEXT);
            CREATE INDEX IF NOT EXISTS reel_due ON reel_jobs(status,scheduled_at);
            CREATE INDEX IF NOT EXISTS reel_asset_jobs ON reel_jobs(asset_id,id DESC);
            CREATE INDEX IF NOT EXISTS reel_asset_hash ON reel_assets(sha256,size,created_at,id);
            CREATE INDEX IF NOT EXISTS reel_page_publish ON reel_jobs(page_id,created_at);
            ''')
            columns={row[1] for row in db.execute('PRAGMA table_info(pages)')}
            if 'last_metadata_started_at' not in columns:db.execute('ALTER TABLE pages ADD COLUMN last_metadata_started_at TEXT')
            connection_columns={row[1] for row in db.execute('PRAGMA table_info(connections)')}
            if 'revision' not in connection_columns:db.execute('ALTER TABLE connections ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
            if 'business_scope_revision' not in connection_columns:db.execute('ALTER TABLE connections ADD COLUMN business_scope_revision INTEGER')
            reel_columns={row[1] for row in db.execute('PRAGMA table_info(reel_jobs)')}
            for name,kind in (('publish_mode',"TEXT NOT NULL DEFAULT 'scheduled'"),('approve_by','TEXT'),('review_batch','TEXT'),('approval_policy','TEXT')):
                if name not in reel_columns:db.execute('ALTER TABLE reel_jobs ADD COLUMN '+name+' '+kind)
            db.execute('CREATE INDEX IF NOT EXISTS connection_pages_page ON connection_pages(page_id,active,token_revision)')
            db.execute('CREATE INDEX IF NOT EXISTS posts_page_order ON posts(page_id,created_time DESC,post_id DESC)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _row(row):return dict(row) if row else None

    def connection(self,cid):
        with self.connect() as db:row=self._row(db.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone())
        if row is None:raise AppError('Không có kết nối này.','not_found',404)
        return row

    def connections(self,limit=None,offset=0,search='',*,connected_only=False):
        where,params=self._connection_filter(search,connected_only=connected_only)
        sql=f'SELECT c.*,(SELECT COUNT(*) FROM connection_pages cp WHERE cp.connection_id=c.id AND {ACTIVE_PAGE_LINK}) AS page_count FROM connections c'+where+' ORDER BY c.id'
        if limit is not None:sql+=' LIMIT ? OFFSET ?';params.extend((min(100,max(1,int(limit))),min(2**63-1,max(0,int(offset)))))
        with self.connect() as db:
            db.create_function('casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
            return [dict(row) for row in db.execute(sql,params)]

    def connection_page_count(self,cid):
        with self.connect() as db:
            return db.execute(f'SELECT COUNT(*) FROM connection_pages cp JOIN connections c ON c.id=cp.connection_id WHERE c.id=? AND {ACTIVE_PAGE_LINK}',(cid,)).fetchone()[0]

    @staticmethod
    def _connection_filter(search,*,connected_only=False):
        query=str(search or '').strip().casefold()
        predicates,params=[],[]
        if connected_only:predicates.append('c.connected=1')
        if query:
            predicates.append('(instr(casefold(c.label),?)>0 OR instr(CAST(c.id AS TEXT),?)>0)')
            params.extend((query,query))
        return (' WHERE '+' AND '.join(predicates),params) if predicates else ('',params)

    def connection_count(self,search='',*,connected_only=False):
        where,params=self._connection_filter(search,connected_only=connected_only)
        with self.connect() as db:
            db.create_function('casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
            return db.execute('SELECT COUNT(*) FROM connections c'+where,params).fetchone()[0]

    def connection_options(self,limit=100,offset=0,search=''):
        limit=min(100,max(1,int(limit)));offset=min(2**63-1,max(0,int(offset)))
        where,params=self._connection_filter(search)
        with self.connect() as db:
            db.create_function('casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
            total=db.execute('SELECT COUNT(*) FROM connections c'+where,params).fetchone()[0]
            rows=[dict(row) for row in db.execute('SELECT c.id,c.label,c.status,c.connected,(c.encrypted_token IS NOT NULL) AS has_token FROM connections c'+where+' ORDER BY c.id LIMIT ? OFFSET ?',params+[limit,offset])]
        for row in rows:row['connected']=bool(row['connected']);row['has_token']=bool(row['has_token'])
        return {'items':rows,'total':total,'limit':limit,'offset':offset}

    def save_connection(self,values,encrypted,cid=None):
        stamp=now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if cid is None:
                cursor=db.execute('INSERT INTO connections(label,token_type,app_id,business_id,encrypted_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (values['label'],values['token_type'],values.get('app_id'),values.get('business_id'),encrypted,stamp,stamp));cid=cursor.lastrowid
            else:
                old=db.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone()
                if old is None:raise AppError('Không có kết nối này.','not_found',404)
                revision=values.get('revision')
                if type(revision) is not int or revision!=old['revision']:
                    raise AppError('Kết nối đã thay đổi ở phiên khác. Đọc lại trước khi lưu; chưa ghi đè thay đổi mới.',
                                   'connection_revision_changed',409)
                rotated=encrypted is not None or any(old[key]!=values.get(key) for key in ('token_type','app_id','business_id'))
                if not old['connected'] and encrypted is None:raise AppError('Nhập token mới để kết nối lại.')
                db.execute('UPDATE connections SET label=?,token_type=?,app_id=?,business_id=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?',
                    (values['label'],values['token_type'],values.get('app_id'),values.get('business_id'),stamp,cid,revision))
                if rotated:
                    db.execute('UPDATE connections SET encrypted_token=?,token_revision=token_revision+1,business_scope_revision=NULL,status=\'unchecked\',identity_id=NULL,identity_name=NULL,permissions=NULL,expires_at=NULL,checked_at=NULL,synced_at=NULL,error=NULL,connected=1 WHERE id=?',(encrypted or old['encrypted_token'],cid))
                    db.execute('UPDATE connection_pages SET active=0,encrypted_page_token=NULL WHERE connection_id=?',(cid,))
        return cid

    def disconnect(self,cid,revision=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT revision FROM connections WHERE id=?',(cid,)).fetchone()
            if current is None:raise AppError('Không có kết nối này.','not_found',404)
            if revision is not None and (type(revision) is not int or revision!=current['revision']):
                raise AppError('Kết nối đã thay đổi ở phiên khác. Đọc lại trước khi ngắt kết nối.',
                               'connection_revision_changed',409)
            db.execute('UPDATE connections SET encrypted_token=NULL,token_revision=token_revision+1,business_scope_revision=NULL,revision=revision+1,status=\'disconnected\',connected=0,permissions=NULL,expires_at=NULL,error=NULL,updated_at=? WHERE id=?',(now(),cid))
            db.execute('UPDATE connection_pages SET active=0,encrypted_page_token=NULL WHERE connection_id=?',(cid,))

    @staticmethod
    def _current(db,cid,revision,page_id=None):
        row=db.execute('SELECT * FROM connections WHERE id=? AND token_revision=? AND connected=1 AND encrypted_token IS NOT NULL',(cid,revision)).fetchone()
        if row is None:raise AppError('Kết nối đã thay đổi; kết quả cũ không được lưu.','stale_connection',409)
        if page_id is not None:
            if row['token_type']=='user' and row['business_id'] and row['business_scope_revision']!=revision:
                raise AppError('Đồng bộ lại kết nối để xác minh danh sách Page thuộc BM đã chọn.','business_scope_unverified',409)
            if db.execute('SELECT 1 FROM connection_pages WHERE connection_id=? AND page_id=? AND active=1 AND token_revision=?',(cid,page_id,revision)).fetchone() is None:
                raise AppError('Kết nối không còn quyền truy cập Page này.','page_access_required',409)
        return row

    def health(self,cid,revision,*,status,identity_id=None,identity_name=None,permissions=None,error=None):
        with self.connect() as db:
            self._current(db,cid,revision)
            db.execute('UPDATE connections SET status=?,identity_id=COALESCE(?,identity_id),identity_name=COALESCE(?,identity_name),permissions=?,checked_at=?,error=?,updated_at=? WHERE id=?',
                (status,identity_id,identity_name,dump(permissions) if permissions is not None else None,now(),error,now(),cid))

    def sync(self,cid,revision,pages,complete,status,error,identity,started_at=None,*,verified_business_id=None):
        stamp=now();started_at=started_at or stamp
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            connection=self._current(db,cid,revision)
            scope_revision=None
            if connection['token_type']=='user' and connection['business_id']:
                if complete and status=='working' and verified_business_id==connection['business_id']:
                    scope_revision=revision
                else:
                    # Preserve history, but a partial or unverified result grants no access.
                    pages=[];complete=False
            if complete:db.execute('UPDATE connection_pages SET active=0 WHERE connection_id=?',(cid,))
            for page in pages:
                # Compare request start, not response completion: a delayed
                # catalog from another token cannot roll back newer metadata.
                db.execute('''INSERT INTO pages(id,name,category,link,last_metadata_started_at) VALUES(?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,category=COALESCE(excluded.category,pages.category),
                    link=COALESCE(excluded.link,pages.link),last_metadata_started_at=excluded.last_metadata_started_at
                    WHERE pages.last_metadata_started_at IS NULL OR excluded.last_metadata_started_at>=pages.last_metadata_started_at''',
                    (page['id'],page['name'],page.get('category'),page.get('link'),started_at))
                old=db.execute('SELECT * FROM connection_pages WHERE connection_id=? AND page_id=?',(cid,page['id'])).fetchone()
                sources=set(json.loads(old['sources'])) if not complete and old and old['token_revision']==revision else set()
                sources.update(page['sources'])
                secret=page.get('encrypted_page_token')
                tasks=dump(page['tasks']) if page.get('tasks') is not None else None
                if scope_revision is None and old and old['token_revision']==revision:
                    if secret is None:secret=old['encrypted_page_token']
                    if tasks is None:tasks=old['tasks']
                db.execute('INSERT INTO connection_pages(connection_id,page_id,sources,active,encrypted_page_token,token_revision,tasks) VALUES(?,?,?,1,?,?,?) ON CONFLICT(connection_id,page_id) DO UPDATE SET sources=excluded.sources,active=1,encrypted_page_token=excluded.encrypted_page_token,token_revision=excluded.token_revision,tasks=excluded.tasks',
                    (cid,page['id'],dump(sorted(sources)),secret,revision,tasks))
            if complete:db.execute('UPDATE connection_pages SET encrypted_page_token=NULL WHERE connection_id=? AND active=0',(cid,))
            db.execute('UPDATE connections SET status=?,identity_id=COALESCE(?,identity_id),identity_name=COALESCE(?,identity_name),checked_at=?,synced_at=?,error=?,updated_at=?,business_scope_revision=? WHERE id=?',
                (status,identity.get('id'),identity.get('name'),stamp,stamp,error,stamp,scope_revision,cid))

    def catalog(self):
        with self.connect() as db:
            pages=[dict(row) for row in db.execute('SELECT * FROM pages ORDER BY name COLLATE NOCASE,id')]
            links=[dict(row) for row in db.execute('SELECT cp.*,c.label,c.status,c.connected,c.encrypted_token,c.token_revision AS current_revision FROM connection_pages cp JOIN connections c ON c.id=cp.connection_id ORDER BY c.id')]
            posts=[dict(row) for row in db.execute('SELECT page_id,post_id,created_time FROM posts')]
        return pages,links,posts

    @staticmethod
    def _page_summary(start,end):
        active=ACTIVE_PAGE_LINK
        sql="""WITH summary AS (SELECT p.id,p.name,
            CASE WHEN EXISTS(SELECT 1 FROM connection_pages cp JOIN connections c ON c.id=cp.connection_id WHERE cp.page_id=p.id AND """+active+""" AND c.status IN ('working','unchecked')) THEN 'active'
                 WHEN EXISTS(SELECT 1 FROM connection_pages cp JOIN connections c ON c.id=cp.connection_id WHERE cp.page_id=p.id AND """+active+""" ) THEN 'needs_attention' ELSE 'disconnected' END AS status,
            CASE WHEN p.posts_checked_at IS NULL THEN NULL ELSE (SELECT COUNT(DISTINCT x.post_id) FROM posts x JOIN json_each(p.posts_latest_ids) j ON j.value=x.post_id WHERE x.page_id=p.id AND julianday(x.created_time)>=julianday(?) AND julianday(x.created_time)<julianday(?)) END AS posts_today,
            CASE WHEN p.posts_checked_at IS NOT NULL AND julianday(p.posts_checked_at)>=julianday(?) AND julianday(p.posts_checked_at)<julianday(?) AND (p.posts_error IS NULL OR p.posts_error='') AND (NOT p.posts_has_more OR julianday(p.posts_oldest)<julianday(?)) THEN 1 ELSE 0 END AS posts_today_complete
            FROM pages p) """
        return sql,[start,end,start,end,start]

    def catalog_window(self,start,end,*,connection_id=None,search='',status='',information_status='',today='',page_id=None,limit=None,offset=0,posting_eligible_only=False):
        prefix,params=self._page_summary(start,end);conditions=[]
        changed="(EXISTS(SELECT 1 FROM page_edits ie WHERE ie.page_id=s.id AND ie.outcome='accepted') OR EXISTS(SELECT 1 FROM chrome_page_setup_receipts ce WHERE ce.page_id=s.id))"
        revision="((SELECT COUNT(*) FROM page_edits ie WHERE ie.page_id=s.id AND ie.outcome='accepted')+(SELECT COUNT(*) FROM chrome_page_setup_receipts ce WHERE ce.page_id=s.id))"
        if information_status not in ('','updated','not_updated'):
            raise AppError('Bộ lọc thay đổi thông tin Page không hợp lệ.','invalid_information_status',422)
        if type(posting_eligible_only) is not bool:
            raise AppError('Bộ lọc Page đủ điều kiện chưa hợp lệ.','invalid_posting_eligibility_filter',422)
        if connection_id is not None:
            conditions.append(f'EXISTS(SELECT 1 FROM connection_pages cp JOIN connections c ON c.id=cp.connection_id WHERE cp.page_id=s.id AND c.id=? AND (NOT ({SCOPED_USER}) OR ({ACTIVE_PAGE_LINK})))')
            params.append(connection_id)
        query=str(search or '').strip().casefold()
        if query:conditions.append('(instr(casefold(s.name),?)>0 OR instr(s.id,?)>0)');params.extend((query,query))
        if status:conditions.append('s.status=?');params.append(status)
        if information_status:conditions.append(changed if information_status=='updated' else 'NOT '+changed)
        if posting_eligible_only:
            conditions.extend((changed,"length(s.id) BETWEEN 1 AND 32 AND substr(s.id,1,1) BETWEEN '1' AND '9' AND s.id NOT GLOB '*[^0-9]*'"))
        if page_id is not None:conditions.append('s.id=?');params.append(page_id)
        if today=='posted':conditions.append('s.posts_today>0')
        elif today=='zero':conditions.append('s.posts_today=0 AND s.posts_today_complete=1')
        elif today=='unknown':conditions.append('(s.posts_today IS NULL OR s.posts_today_complete=0)')
        where=' WHERE '+' AND '.join(conditions) if conditions else ''
        sql=prefix+"SELECT p.*,s.status AS public_status,s.posts_today,s.posts_today_complete,CASE WHEN "+changed+" THEN 'updated' ELSE 'not_updated' END AS information_status,"+revision+" AS posting_revision FROM summary s JOIN pages p ON p.id=s.id"+where+' ORDER BY p.name COLLATE NOCASE,p.id'
        with self.connect() as db:
            db.create_function('casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
            total=db.execute(prefix+'SELECT COUNT(*) FROM summary s'+where,params).fetchone()[0]
            if limit is not None:sql+=' LIMIT ? OFFSET ?';values=params+[min(100,max(1,int(limit))),min(2**63-1,max(0,int(offset)))]
            else:values=params
            pages=[dict(row) for row in db.execute(sql,values)]
            ids=[row['id'] for row in pages]
            links=[dict(row) for row in db.execute(f'SELECT cp.*,c.label,c.status,({ACTIVE_PAGE_LINK}) AS is_active FROM connection_pages cp JOIN connections c ON c.id=cp.connection_id WHERE cp.page_id IN ('+','.join('?' for _ in ids)+') ORDER BY c.id',ids)] if ids else []
        return pages,links,total

    @staticmethod
    def _posting_verdict(page_id,revision=0):
        valid=isinstance(page_id,str) and re.fullmatch(r'[1-9][0-9]{0,31}',page_id)
        revision=revision if valid and type(revision) is int and revision>=0 else 0
        ready=bool(valid and revision>0)
        return {'page_id':page_id if valid else None,'can_publish':ready,
                'information_status':'updated' if ready else 'not_updated',
                'reason':None if ready else 'page_information_required','revision':revision}

    def posting_eligibility(self,page_id,db=None):
        """Only accepted edit receipts establish eligibility; catalog data is not proof."""
        verdict=self._posting_verdict(page_id)
        if verdict['page_id'] is None:return verdict
        if db is None:
            with self.connect() as connection:return self.posting_eligibility(page_id,db=connection)
        revision=db.execute("""SELECT
            (SELECT COUNT(*) FROM page_edits WHERE page_id=? AND outcome='accepted')+
            (SELECT COUNT(*) FROM chrome_page_setup_receipts WHERE page_id=?)""",(page_id,page_id)).fetchone()[0]
        return self._posting_verdict(page_id,revision)

    def ensure_posting_ready(self,page_id,db=None):
        verdict=self.posting_eligibility(page_id,db=db)
        if not verdict['can_publish']:
            raise AppError('Page chưa có thay đổi thông tin đã được xác nhận. Cập nhật thông tin Page trước khi bật đăng.',
                           'page_information_required',422)
        return verdict

    def record_chrome_setup(self,page_id,receipt_id,field_fingerprints):
        """Trusted host callback after Chrome persisted readback, never a public HTTP action."""
        try:
            key=UUID(receipt_id)
            valid_receipt=isinstance(receipt_id,str) and str(key)==receipt_id and key.int!=0
        except (ValueError,TypeError,AttributeError):valid_receipt=False
        allowed={'avatar','cover','bio','address','categories'}
        if (self._posting_verdict(page_id)['page_id'] is None or not valid_receipt
                or not isinstance(field_fingerprints,dict) or not field_fingerprints
                or set(field_fingerprints)-allowed
                or any(not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value) for value in field_fingerprints.values())):
            raise AppError('Bằng chứng cập nhật Page qua Chrome không hợp lệ.','invalid_chrome_setup_receipt',422)
        encoded=json.dumps(field_fingerprints,sort_keys=True,separators=(',',':'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT page_id,field_fingerprints FROM chrome_page_setup_receipts WHERE receipt_id=?',(receipt_id,)).fetchone()
            if previous:
                if previous['page_id']!=page_id or previous['field_fingerprints']!=encoded:
                    raise AppError('Mã bằng chứng Chrome đã gắn với Page hoặc nội dung khác.','chrome_setup_receipt_conflict',409)
            else:
                db.execute('INSERT INTO chrome_page_setup_receipts(receipt_id,page_id,field_fingerprints,created_at) VALUES(?,?,?,?)',
                           (receipt_id,page_id,encoded,now()))
            return self.posting_eligibility(page_id,db=db)

    def overview_counts(self,start,end):
        prefix,params=self._page_summary(start,end)
        with self.connect() as db:
            pages=db.execute(prefix+'SELECT COUNT(*) AS pages,COALESCE(SUM(posts_today>0),0) AS pages_with_posts_today,MIN(posts_today_complete) AS today_count_known FROM summary',params).fetchone()
            connections=db.execute("SELECT COUNT(*) AS connections,COALESCE(SUM(status='working'),0) AS working_connections,COALESCE(SUM(status IN ('permission_limited','api_access_blocked','invalid','rate_limited','error')),0) AS needs_attention FROM connections WHERE connected=1").fetchone()
            saved_connections=db.execute('SELECT COUNT(*) FROM connections').fetchone()[0]
        return {**dict(pages),**dict(connections),'saved_connections':saved_connections,'today_count_known':bool(pages['pages'] and pages['today_count_known'])}

    def page(self,page_id,limit=25,offset=0):
        with self.connect() as db:
            row=self._row(db.execute('SELECT * FROM pages WHERE id=?',(page_id,)).fetchone())
            posts=[dict(value) for value in db.execute('SELECT x.post_id AS id,x.message,x.created_time,x.permalink_url FROM posts x INDEXED BY posts_page_order WHERE x.page_id=? AND x.post_id IN (SELECT value FROM json_each(?)) ORDER BY x.created_time DESC,x.post_id DESC LIMIT ? OFFSET ?',(page_id,row['posts_latest_ids'],min(100,max(1,int(limit))),min(2**63-1,max(0,int(offset)))))] if row else []
        if row is None:raise AppError('Không có Page này trong bộ nhớ đã đồng bộ.','not_found',404)
        return row,posts

    def post_count(self,page_id):
        with self.connect() as db:return db.execute('SELECT COUNT(*) FROM posts x JOIN pages p ON p.id=x.page_id WHERE x.page_id=? AND x.post_id IN (SELECT value FROM json_each(p.posts_latest_ids))',(page_id,)).fetchone()[0]

    def source(self,page_id,cid=None):
        with self.connect() as db:
            sql=f'SELECT c.*,cp.encrypted_page_token FROM connection_pages cp JOIN connections c ON c.id=cp.connection_id WHERE cp.page_id=? AND {ACTIVE_PAGE_LINK}'
            params=[page_id]
            if cid is not None:sql+=' AND c.id=?';params.append(cid)
            sql+=" ORDER BY CASE c.status WHEN 'working' THEN 0 WHEN 'unchecked' THEN 1 ELSE 2 END,c.id LIMIT 1"
            row=self._row(db.execute(sql,params).fetchone())
            if row is None and cid is not None and db.execute(f'SELECT 1 FROM connections c WHERE c.id=? AND c.connected=1 AND {UNVERIFIED_BUSINESS_SCOPE}',(cid,)).fetchone():
                raise AppError('Đồng bộ lại kết nối để xác minh danh sách Page thuộc BM đã chọn.','business_scope_unverified',409)
        if row is None:raise AppError('Chọn kết nối đã đồng bộ và đang truy cập được Page này.','page_access_required',403)
        return row

    def refresh(self,cid,revision,page_id,details,detail_error,posts,posts_has_more,posts_error,include_posts,started_at=None):
        stamp=now();started_at=started_at or stamp
        with self.connect() as db:
            self._current(db,cid,revision,page_id)
            old=db.execute('SELECT * FROM pages WHERE id=?',(page_id,)).fetchone()
            fresh=old['last_metadata_started_at'] is None or started_at>=old['last_metadata_started_at']
            if details is not None and fresh:
                saved=json.loads(old['details']);saved.update(details.get('details',{}))
                db.execute('UPDATE pages SET name=COALESCE(?,name),category=COALESCE(?,category),link=COALESCE(?,link),details=?,checked_at=?,error=?,last_metadata_started_at=? WHERE id=?',
                    (details.get('name'),details.get('category'),details.get('link'),dump(saved),stamp,detail_error,started_at,page_id))
            elif details is None and fresh:db.execute('UPDATE pages SET error=? WHERE id=?',(detail_error,page_id))
            if include_posts:
                if posts is not None:
                    for post in posts:db.execute('INSERT INTO posts(page_id,post_id,message,created_time,permalink_url) VALUES(?,?,?,?,?) ON CONFLICT(page_id,post_id) DO UPDATE SET message=excluded.message,created_time=excluded.created_time,permalink_url=excluded.permalink_url',
                        (page_id,post['id'],post['message'],post['created_time'],post.get('permalink_url')))
                    oldest=min((post['created_time'] for post in posts),default=None)
                    db.execute('UPDATE pages SET posts_checked_at=?,posts_has_more=?,posts_error=?,posts_oldest=?,posts_latest_ids=? WHERE id=?',(stamp,int(posts_has_more),posts_error,oldest,dump([post['id'] for post in posts]),page_id))
                else:db.execute('UPDATE pages SET posts_error=? WHERE id=?',(posts_error,page_id))

    def event(self,action,status,message,label=None):
        with self.connect() as db:
            db.execute('INSERT INTO events(action,status,message,connection_label,created_at) VALUES(?,?,?,?,?)',(action,status,message,label,now()))
            db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)')

    @staticmethod
    def _edit_key(request_id):
        try:return str(UUID(str(request_id)))
        except (ValueError,TypeError,AttributeError):raise AppError('Mã lượt sửa Page không hợp lệ.','invalid_request_id',422) from None

    @staticmethod
    def _edit_public(row):
        outcome=row['outcome']
        # A request still marked started can have reached Meta before a
        # connection loss or server restart. It is never safe to replay it.
        if outcome=='started':
            return {'ok':False,'outcome':'unknown','message':'Lượt sửa này đã bắt đầu nhưng chưa xác nhận được kết quả. Đọc lại Page trước khi tạo thay đổi mới.','requires_refresh':True}
        return {'ok':outcome=='accepted','outcome':outcome,'message':row['message'],'requires_refresh':bool(row['requires_refresh'])}

    def edit_begin(self,cid,revision,page_id,request_id,kind,payload_hash):
        key=self._edit_key(request_id)
        if kind not in ('metadata','text','avatar','cover') or not isinstance(payload_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',payload_hash):
            raise AppError('Thông tin lượt sửa Page không hợp lệ.','invalid_edit',422)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._current(db,cid,revision,page_id)
            previous=db.execute('SELECT * FROM page_edits WHERE request_id=?',(key,)).fetchone()
            if previous:
                if (previous['connection_id'],previous['token_revision'],previous['page_id'],previous['kind'],previous['payload_hash'])!=(cid,revision,page_id,kind,payload_hash):
                    raise AppError('Mã lượt sửa đã được dùng cho nội dung, Page hoặc kết nối khác. Tải lại trước khi sửa.','edit_conflict',409)
                return {'replayed':True,**self._edit_public(previous)}
            stamp=now()
            db.execute('INSERT INTO page_edits(request_id,connection_id,token_revision,page_id,kind,payload_hash,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (key,cid,revision,page_id,kind,payload_hash,'Đã bắt đầu một lượt sửa Page; chưa có xác nhận từ Meta.',stamp,stamp))
            return {'replayed':False,'outcome':'started'}

    def edit_checkpoint(self,request_id,values):
        key=self._edit_key(request_id)
        if not isinstance(values,dict) or set(values)-{'uploaded_photo_id'} or any(not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,32}',value) for value in values.values()):
            raise AppError('Tiến độ ảnh Page không hợp lệ.','invalid_edit_checkpoint',422)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT checkpoint,outcome FROM page_edits WHERE request_id=?',(key,)).fetchone()
            if row is None:raise AppError('Không có lượt sửa Page này.','not_found',404)
            if row['outcome']!='started':raise AppError('Lượt sửa đã có kết quả; không ghi đè tiến độ.','edit_finished',409)
            current=json.loads(row['checkpoint'])
            if current.get('uploaded_photo_id') is not None and values.get('uploaded_photo_id',current['uploaded_photo_id'])!=current['uploaded_photo_id']:
                raise AppError('Lượt sửa đã ghi nhận ảnh khác; không ghi đè tiến độ tải ảnh.','edit_checkpoint_conflict',409)
            current.update(values)
            db.execute('UPDATE page_edits SET checkpoint=?,updated_at=? WHERE request_id=?',(dump(current),now(),key))

    def edit_finish(self,request_id,outcome,message,requires_refresh=True):
        key=self._edit_key(request_id)
        if outcome not in ('accepted','failed','partial','unknown'):
            raise AppError('Kết quả lượt sửa Page không hợp lệ.','invalid_edit_outcome',422)
        safe_message=re.sub(r'[\x00-\x1f\x7f]',' ',str(message))[:600]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM page_edits WHERE request_id=?',(key,)).fetchone()
            if row is None:raise AppError('Không có lượt sửa Page này.','not_found',404)
            if row['outcome']=='started':
                db.execute('UPDATE page_edits SET outcome=?,message=?,requires_refresh=?,updated_at=? WHERE request_id=?',
                    (outcome,safe_message,int(bool(requires_refresh)),now(),key))
                row=db.execute('SELECT * FROM page_edits WHERE request_id=?',(key,)).fetchone()
            return self._edit_public(row)

    def events(self,limit=50,offset=0):
        with self.connect() as db:return [dict(row) for row in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ? OFFSET ?',(min(100,max(1,int(limit))),min(2**63-1,max(0,int(offset)))))]

    def event_count(self):
        with self.connect() as db:return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def reel_asset_save(self,row):
        with self.connect() as db:
            db.execute('INSERT INTO reel_assets(id,name,path,size,sha256,mime,created_at) VALUES(?,?,?,?,?,?,?)',
                (row['id'],row['name'],str(row['path']),row['size'],row['sha256'],row['mime'],now()))

    def reel_asset(self,asset_id):
        with self.connect() as db:row=self._row(db.execute('SELECT * FROM reel_assets WHERE id=?',(asset_id,)).fetchone())
        if row is None:raise AppError('Không có video đã tải lên này.','not_found',404)
        return row

    def reel_assets(self):
        with self.connect() as db:return [dict(row) for row in db.execute('SELECT * FROM reel_assets ORDER BY created_at DESC,id DESC')]

    def reel_assets_matching(self,sha256,size):
        with self.connect() as db:return [dict(row) for row in db.execute('SELECT * FROM reel_assets WHERE sha256=? AND size=? ORDER BY created_at,id',(sha256,size))]

    @staticmethod
    def _list_window(limit,offset):return min(100,max(1,int(limit))),min(2**63-1,max(0,int(offset)))

    def reel_asset_page(self,limit=12,offset=0,search='',status='all',asset_id=None,calendar='',timezone='America/Los_Angeles',*,clamp_offset=True):
        limit,offset=self._list_window(limit,offset)
        published="EXISTS(SELECT 1 FROM reel_jobs j WHERE j.asset_id=a.id AND j.archived_at IS NULL AND j.status='completed')"
        linked="EXISTS(SELECT 1 FROM reel_jobs j WHERE j.asset_id=a.id AND j.archived_at IS NULL)"
        where,params=[],[]
        if search:
            where.append("(instr(fold_text(a.name),?)>0 OR EXISTS(SELECT 1 FROM reel_jobs j WHERE j.asset_id=a.id AND j.archived_at IS NULL AND instr(fold_text(j.page_name||' '||j.page_id||' '||j.caption),?)>0))")
            params.extend([search.casefold()]*2)
        if status=='published':where.append(published)
        elif status=='scheduled':where.append(linked)
        elif status=='unused':where.append('NOT '+linked)
        if asset_id is not None:where.append('a.id=?');params.append(asset_id)
        clause=' WHERE '+' AND '.join(where) if where else ''
        with self.connect() as db:
            from .library_calendar import calendar_predicate
            predicate,dates=calendar_predicate(db,'a.created_at',calendar,timezone)
            if predicate:
                where.append(predicate);params.extend(dates)
                clause=' WHERE '+' AND '.join(where)
            db.create_function('fold_text',1,lambda value:str(value or '').casefold(),deterministic=True)
            total=db.execute('SELECT COUNT(*) FROM reel_assets a'+clause,params).fetchone()[0]
            if clamp_offset:offset=min(offset,max(0,((total-1)//limit)*limit))
            rows=[dict(row) for row in db.execute('SELECT a.* FROM reel_assets a'+clause+' ORDER BY a.created_at DESC,a.id DESC LIMIT ? OFFSET ?',(*params,limit,offset))]
            summary=dict(db.execute('SELECT COUNT(*) total,COALESCE(SUM('+published+'),0) published,COALESCE(SUM('+linked+'),0) scheduled,COALESCE(SUM(NOT '+linked+'),0) unused FROM reel_assets a').fetchone())
            for row in rows:
                counts=db.execute("SELECT COUNT(*) total,COALESCE(SUM(status='completed'),0) published FROM reel_jobs WHERE asset_id=? AND archived_at IS NULL",(row['id'],)).fetchone()
                row['linked_job_count']=counts['total'];row['has_published_jobs']=bool(counts['published'])
                row['linked_jobs']=[self._reel_row(job) for job in db.execute('SELECT * FROM reel_jobs WHERE asset_id=? AND archived_at IS NULL ORDER BY id DESC LIMIT 3',(row['id'],))]
        return {'rows':rows,'total':total,'limit':limit,'offset':offset,'summary':summary}

    @staticmethod
    def _reel_row(row):
        if row is None:return None
        result=dict(row);result['checkpoint']=json.loads(result['checkpoint'])
        if result.get('review_batch') is not None:result['review_batch']=json.loads(result['review_batch'])
        return result

    def reel_job(self,job_id):
        with self.connect() as db:row=self._reel_row(db.execute('SELECT * FROM reel_jobs WHERE id=?',(job_id,)).fetchone())
        if row is None:raise AppError('Không có lịch Reel này.','not_found',404)
        return row

    def reel_jobs(self):
        with self.connect() as db:return [self._reel_row(row) for row in db.execute('SELECT * FROM reel_jobs ORDER BY id DESC')]

    def reel_job_by_request(self,request_id):
        with self.connect() as db:return self._reel_row(db.execute('SELECT * FROM reel_jobs WHERE request_id=?',(str(request_id),)).fetchone())

    def reel_job_page(self,limit=12,offset=0,search='',status='all',request_id=None,calendar='',sort='recent',*,page_id=None,connection_id=None):
        try:validate_calendar(calendar);validate_sort(sort)
        except ValueError:raise AppError('Bộ lọc ngày hoặc cách sắp xếp lịch chưa hợp lệ.','invalid_reel_calendar',422) from None
        limit,offset=self._list_window(limit,offset)
        where,params=['archived_at IS NULL'],[]
        if search:
            where.append("instr(fold_text(page_name||' '||page_id||' '||connection_label||' '||asset_name||' '||caption||' '||id),?)>0");params.append(search.casefold())
        if status=='attention':where.append("status IN ('needs_user','needs_review')")
        elif status=='pending':where.append("status NOT IN ('completed','cancelled','needs_user','needs_review')")
        elif status!='all':where.append('status=?');params.append(status)
        if request_id is not None:where.append('request_id=?');params.append(str(request_id))
        if page_id is not None:where.append('page_id=?');params.append(page_id)
        if connection_id is not None:where.append('connection_id=?');params.append(connection_id)
        if calendar:
            where.append('substr(reel_local_date(scheduled_at,timezone),1,?)=?');params.extend((len(calendar),calendar))
        clause=' WHERE '+' AND '.join(where)
        ordering={'recent':'id DESC','scheduled_asc':'julianday(scheduled_at) IS NULL,julianday(scheduled_at) ASC,id ASC',
                  'scheduled_desc':'julianday(scheduled_at) IS NULL,julianday(scheduled_at) DESC,id DESC'}[sort]
        with self.connect() as db:
            db.create_function('fold_text',1,lambda value:str(value or '').casefold(),deterministic=True)
            if calendar:db.create_function('reel_local_date',2,local_date_key,deterministic=True)
            total=db.execute('SELECT COUNT(*) FROM reel_jobs'+clause,params).fetchone()[0]
            rows=[self._reel_row(row) for row in db.execute('SELECT * FROM reel_jobs'+clause+' ORDER BY '+ordering+' LIMIT ? OFFSET ?',(*params,limit,offset))]
            ids=list({row['asset_id'] for row in rows})
            assets={row['id']:dict(row) for row in db.execute('SELECT * FROM reel_assets WHERE id IN ('+','.join('?' for _ in ids)+')',ids)} if ids else {}
        return {'rows':rows,'assets':assets,'total':total,'limit':limit,'offset':offset}

    def reel_due(self,stamp):
        with self.connect() as db:return [self._reel_row(row) for row in db.execute("SELECT * FROM reel_jobs WHERE status='scheduled' AND archived_at IS NULL AND julianday(scheduled_at)<=julianday(?) ORDER BY scheduled_at,id",(stamp,))]

    def reel_create(self,values):
        from .publication_approvals import normalize_review_batch,_time,REMIND_UNTIL_DECISION
        key=self._edit_key(values['request_id'])
        mode=values.get('publish_mode','scheduled');deadline=values.get('approve_by');group=values.get('review_batch')
        if mode not in ('scheduled','on_approval') or mode=='scheduled' and deadline is not None:
            raise AppError('Cách đăng video không hợp lệ.','invalid_publish_mode',422)
        if group is not None:group=normalize_review_batch(group)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM reel_jobs WHERE request_id=?',(key,)).fetchone()
            policy=values.get('approval_policy',previous['approval_policy'] if previous else REMIND_UNTIL_DECISION)
            if policy not in (None,REMIND_UNTIL_DECISION):
                raise AppError('Chế độ duyệt không hợp lệ.','invalid_approval_policy',422)
            if mode=='on_approval':
                try:
                    if policy==REMIND_UNTIL_DECISION:
                        if deadline is not None:raise ValueError()
                    else:
                        deadline=_time(deadline).isoformat()
                        if _time(deadline)<=_time(values['scheduled_at']):raise ValueError()
                except (ValueError,TypeError):
                    raise AppError('Hạn duyệt đăng ngay không hợp lệ.','invalid_approval_deadline',422) from None
            immutable={name:values[name] for name in ('page_id','connection_id','token_revision','asset_id','caption','scheduled_at','scheduled_local','timezone','fold')}
            if mode=='on_approval':immutable.update(publish_mode=mode,approve_by=deadline)
            if group is not None:immutable['review_batch']=group
            if policy is not None:immutable['approval_policy']=policy
            digest=hashlib.sha256(dump(immutable).encode()).hexdigest()
            if previous:
                if previous['payload_hash']!=digest:raise AppError('Mã đặt lịch đã được dùng cho nội dung khác.','reel_conflict',409)
                return self._reel_row(previous),True
            if mode=='on_approval' and policy is None and _time(deadline)<=_time(now()):
                raise AppError('Hạn duyệt phải ở tương lai.','approval_deadline_in_past',422)
            if group is not None:
                members=db.execute("SELECT publish_mode,approve_by,review_batch,approval_policy FROM reel_jobs WHERE json_extract(review_batch,'$.id')=?",(group['id'],)).fetchall()
                if len(members)>=group['expected_count'] or any(json.loads(member['review_batch'])!=group
                    or member['publish_mode']!=mode or member['approve_by']!=deadline or member['approval_policy']!=policy for member in members):
                    raise AppError('Nhóm duyệt đã đủ video hoặc cấu hình khác.','publication_review_batch_conflict',409)
            self.ensure_posting_ready(values['page_id'],db=db)
            self._current(db,values['connection_id'],values['token_revision'],values['page_id'])
            if db.execute('SELECT 1 FROM reel_assets WHERE id=?',(values['asset_id'],)).fetchone() is None:raise AppError('Chọn video đã tải lên.','asset_required',422)
            stamp=now();fields=('page_id','page_name','connection_id','connection_label','token_revision','asset_id','asset_name','caption','scheduled_at','scheduled_local','timezone','fold')
            names=('request_id','payload_hash')+fields+('message','created_at','updated_at','publish_mode','approve_by','review_batch','approval_policy')
            message='Video chờ bạn duyệt; sẽ đăng ngay sau khi duyệt đúng nội dung.' if mode=='on_approval' else 'Đã lưu lịch. Video chỉ được gửi tới Facebook khi đến giờ.'
            params=(key,digest)+tuple(values[name] for name in fields)+(message,stamp,stamp,mode,deadline,dump(group) if group is not None else None,policy)
            cursor=db.execute('INSERT INTO reel_jobs('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',params)
            saved=self._reel_row(db.execute('SELECT * FROM reel_jobs WHERE id=?',(cursor.lastrowid,)).fetchone())
            approvals=getattr(self,'publication_approvals',None)
            if approvals is not None:
                from .publication_approvals import token_binding
                asset=dict(db.execute('SELECT * FROM reel_assets WHERE id=?',(saved['asset_id'],)).fetchone())
                approvals.ensure(token_binding(saved,asset),db=db)
            return saved,False

    @staticmethod
    def _reel_fields(fields):
        allowed={'status','step','message','video_id','published_url','started_at','finished_at','archived_at'}
        if set(fields)-allowed:raise AppError('Không được thay đổi nội dung lịch đã lưu.','invalid_reel_update',422)
        if 'status' in fields and fields['status'] not in {'scheduled','waiting_page','starting','uploading','processing','publishing','verifying','completed','needs_user','needs_review','cancelled'}:
            raise AppError('Trạng thái lịch không hợp lệ.','invalid_reel_status',422)
        if fields.get('video_id') is not None and not re.fullmatch(r'[0-9]{1,32}',str(fields['video_id'])):raise AppError('Mã video không hợp lệ.','invalid_video_id',422)
        for name in ('step','message'):
            if name in fields:fields[name]=re.sub(r'[\x00-\x1f\x7f]',' ',str(fields[name]))[:600]
        return fields

    def reel_update(self,job_id,**fields):return self.reel_checkpoint(job_id,{},**fields)

    def reel_checkpoint(self,job_id,patch,**fields):
        fields=self._reel_fields(fields)
        flags={'start_attempted','upload_attempted','finish_attempted','start_ack','upload_ack','finish_ack','uncertain','cancel_requested'}
        binding={'asset_sha256','asset_size','page_id','upload_version'}
        if not isinstance(patch,dict) or set(patch)-(flags|binding) or any(type(value) is not bool for key,value in patch.items() if key in flags):
            raise AppError('Tiến độ Reel không hợp lệ.','invalid_reel_checkpoint',422)
        if 'asset_sha256' in patch and (not isinstance(patch['asset_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',patch['asset_sha256'])):raise AppError('Dấu vân tay video không hợp lệ.','invalid_reel_checkpoint',422)
        if 'asset_size' in patch and (type(patch['asset_size']) is not int or not 0<patch['asset_size']<=1073741824):raise AppError('Dung lượng video không hợp lệ.','invalid_reel_checkpoint',422)
        if 'page_id' in patch and (not isinstance(patch['page_id'],str) or not re.fullmatch(r'[0-9]{1,32}',patch['page_id'])):raise AppError('Page không hợp lệ.','invalid_reel_checkpoint',422)
        if 'upload_version' in patch and (not isinstance(patch['upload_version'],str) or not re.fullmatch(r'v[1-9][0-9]?\.[0-9]',patch['upload_version'])):
            raise AppError('Phiên bản tải video Meta không hợp lệ.','invalid_reel_checkpoint',422)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=self._reel_row(db.execute('SELECT * FROM reel_jobs WHERE id=?',(job_id,)).fetchone())
            if old is None:raise AppError('Không có lịch Reel này.','not_found',404)
            if old['status']=='completed':
                if patch or any(old.get(name)!=value for name,value in fields.items()):raise AppError('Reel đã đăng; không chạy lại.','reel_completed',409)
                return old
            if old['video_id'] and 'video_id' in fields and fields['video_id']!=old['video_id']:
                raise AppError('Không được thay video của lịch này.','reel_video_conflict',409)
            current=old['checkpoint']
            for name,value in patch.items():
                if name in binding:
                    if name in current and current[name]!=value:raise AppError('Không được thay đổi Page hoặc video của lượt đăng.','reel_checkpoint_conflict',409)
                    current[name]=value
                    continue
                if current.get(name) and not value:raise AppError('Không được đặt lại dấu đã gửi của Reel.','reel_checkpoint_conflict',409)
                if value and not current.get(name):current[name+'_at']=now()
                current[name]=value
            fields['checkpoint']=dump(current);fields['updated_at']=now()
            db.execute('UPDATE reel_jobs SET '+','.join(name+'=?' for name in fields)+' WHERE id=?',tuple(fields.values())+(job_id,))
            approvals=getattr(self,'publication_approvals',None)
            if (fields.get('status')=='cancelled' or patch.get('cancel_requested') is True) and approvals is not None:
                approvals.withdraw('token',job_id,db=db)
            return self._reel_row(db.execute('SELECT * FROM reel_jobs WHERE id=?',(job_id,)).fetchone())

    def reel_publish_count(self,page_id,since):
        with self.connect() as db:return db.execute("SELECT COUNT(*) FROM reel_jobs WHERE page_id=? AND json_extract(checkpoint,'$.finish_attempted')=1 AND COALESCE(json_extract(checkpoint,'$.finish_attempted_at'),'')>=?",(page_id,since)).fetchone()[0]

    def reel_recover(self):
        active={'waiting_page','starting','uploading','processing','publishing','verifying'}
        with self.connect() as db:rows=[self._reel_row(row) for row in db.execute("SELECT * FROM reel_jobs WHERE status IN ('waiting_page','starting','uploading','processing','publishing','verifying')")]
        for row in rows:
            if row['status'] not in active:continue
            attempted=any(row['checkpoint'].get(name) for name in ('start_attempted','upload_attempted','finish_attempted'))
            self.reel_update(row['id'],status='needs_review' if attempted else 'scheduled',step='recovered',
                message='Server đã khởi động lại. Kiểm tra video đã tạo; hệ thống không tự gửi lại các bước đã gửi.' if attempted else 'Đã khôi phục lịch chưa bắt đầu sau khi khởi động server.')
