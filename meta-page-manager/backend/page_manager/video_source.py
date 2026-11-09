"""Fixed-folder imports into the existing token Reel queue; never publishes."""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .errors import AppError
from .reel_assets import MAX_VIDEO_BYTES, clean_video_name
from .reel_inputs import resolve_schedule
from .project_paths import VIDEO_DIRECTORY


DEFAULT_DIRECTORY = VIDEO_DIRECTORY
DEFAULT_CAPTION = ('Funny tees. Big laughs. Wear your sense of humor.\n'
                   'Shop more laughs: https://blueox179.com\n'
                   '#FunnyTees #TShirtQuotes #WearYourHumor')
MAX_FILES = 2000
MAX_DIRS = 256
MAX_ENTRIES = 8000
MAX_DEPTH = 8
MAX_ITEMS = 100_000
PENDING_RETRY_BATCH = 100
EXTENSIONS = {'.mp4', '.mov', '.m4v'}
FINAL = {'skipped', 'failed', 'needs_review', 'completed', 'cancelled'}


def utcnow():
    return datetime.now(timezone.utc)


def _caption(value):
    if not isinstance(value, str) or len(value) > 2200 or any(
            (unicodedata.category(char) in ('Cc', 'Cf', 'Cs') and char not in '\n\r\t') for char in value):
        raise ValueError('Caption phải là văn bản UTF-8 không quá 2200 ký tự.')
    return value.replace('\r\n', '\n').replace('\r', '\n')


class VideoSourceSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: StrictBool = False
    page_ids: list[str] = Field(min_length=1, max_length=100)
    connection_id: StrictInt = Field(gt=0)
    daily_time: str = '06:00'
    timezone: str = 'America/Los_Angeles'
    caption_template: str = Field(default=DEFAULT_CAPTION, max_length=2200)
    revision: StrictInt | None = Field(default=None, ge=1)

    @field_validator('page_ids')
    @classmethod
    def pages(cls, values):
        if any(not re.fullmatch(r'[0-9]{1,32}', value) for value in values) or len(set(values)) != len(values):
            raise ValueError('Chọn các ID Page khác nhau.')
        return values

    @field_validator('daily_time')
    @classmethod
    def local_time(cls, value):
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
            raise ValueError('Giờ đăng phải theo HH:MM.')
        return value

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        try:
            if not value or len(value) > 100:
                raise ValueError()
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError('Chọn múi giờ IANA hợp lệ.') from None
        return value

    @field_validator('caption_template')
    @classmethod
    def caption(cls, value):
        return _caption(value)


class _Deferred(Exception):
    pass


class _FileChanged(Exception):
    pass


def _linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _signature(value):
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]


def _checked(path, root, *, maximum=MAX_VIDEO_BYTES):
    path = Path(path).absolute()
    if not path.is_relative_to(root) or any(_linked(part) for part in (path, *path.parents)):
        raise _FileChanged()
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            getattr(info, 'st_file_attributes', 0) & 0x400 or not 0 <= info.st_size <= maximum):
        raise _FileChanged()
    return _signature(info)


def _walk(root):
    found = []
    if any(_linked(part) for part in (root, *root.parents)) or not root.is_dir():
        return found, 'Thư mục nguồn chưa sẵn sàng hoặc có liên kết không được phép.'
    pending = [(root, 0)]
    directories = entries = 0
    limited = False
    invalid = False
    while pending:
        folder, depth = pending.pop()
        directories += 1
        if directories > MAX_DIRS:
            limited = True
            break
        try:
            with os.scandir(folder) as listing:
                for entry in listing:
                    entries += 1
                    if entries > MAX_ENTRIES:
                        limited = True
                        break
                    path = Path(entry.path)
                    if _linked(path):
                        invalid = True
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < MAX_DEPTH:
                            pending.append((path, depth + 1))
                        else:
                            limited = True
                    elif path.suffix.lower() in EXTENSIONS:
                        try:
                            sig = _checked(path, root)
                            if sig[2] > 0:
                                found.append({'path': str(path), 'filename': path.relative_to(root).as_posix(), 'signature': sig})
                            else:
                                invalid = True
                        except (OSError, ValueError, _FileChanged):
                            invalid = True
                            continue
                        if len(found) >= MAX_FILES:
                            limited = True
                            break
        except OSError:
            invalid = True
            continue
        if limited and (entries > MAX_ENTRIES or len(found) >= MAX_FILES):
            break
    warning = ('Nguồn vượt giới hạn quét; chia thành thư mục nhỏ hơn.' if limited else
               'Một số tệp video rỗng, quá lớn hoặc không an toàn đã được bỏ qua.' if invalid else None)
    return sorted(found, key=lambda item: item['filename'].casefold()), warning


def _hash_file(path, signature, root):
    before = _checked(path, root)
    if before != signature:
        raise _FileChanged()
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        if _signature(os.fstat(handle.fileno())) != before:
            raise _FileChanged()
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        if _signature(os.fstat(handle.fileno())) != before:
            raise _FileChanged()
    if _checked(path, root) != before:
        raise _FileChanged()
    return digest.hexdigest()


def _sidecar(path, root):
    target = Path(path).with_suffix('.txt')
    if not target.exists() and not _linked(target):
        return None
    before = _checked(target, root, maximum=8803)
    with target.open('rb') as handle:
        if _signature(os.fstat(handle.fileno())) != before:
            raise _FileChanged()
        raw = handle.read(8804)
        if len(raw) > 8803 or _signature(os.fstat(handle.fileno())) != before:
            raise _FileChanged()
    if _checked(target, root, maximum=8803) != before:
        raise _FileChanged()
    return _caption(raw.decode('utf-8-sig'))


class FolderVideoSource:
    def __init__(self, manager, assets, *, directory=DEFAULT_DIRECTORY, clock=utcnow,
                 stable_seconds=30, interval_seconds=30, max_imports_per_scan=2):
        if (isinstance(stable_seconds, bool) or not 0 <= stable_seconds <= 86400 or
                isinstance(interval_seconds, bool) or not 1 <= interval_seconds <= 86400 or
                type(max_imports_per_scan) is not int or not 1 <= max_imports_per_scan <= 10):
            raise ValueError('Giới hạn quét nguồn không hợp lệ.')
        self.manager, self.assets = manager, assets
        self.directory = Path(directory).absolute()
        self.clock, self.stable_seconds, self.interval_seconds = clock, stable_seconds, interval_seconds
        self.max_imports_per_scan = max_imports_per_scan
        self._task = None
        self._running = self._closing = False
        self._next_scan = None
        defaults = {'enabled': False, 'page_ids': [], 'connection_id': 1, 'daily_time': '06:00',
                    'timezone': 'America/Los_Angeles', 'caption_template': DEFAULT_CAPTION}
        with self.manager.store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS video_source_settings(
                id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,configured INTEGER NOT NULL,
                settings TEXT NOT NULL,cursor INTEGER NOT NULL DEFAULT 0,pending_cursor INTEGER NOT NULL DEFAULT 0,last_scan_at TEXT,last_error TEXT,
                files_seen INTEGER NOT NULL DEFAULT 0,waiting INTEGER NOT NULL DEFAULT 0,skipped INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS video_source_candidates(
                path TEXT PRIMARY KEY,filename TEXT NOT NULL,signature TEXT NOT NULL,first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,sha256 TEXT);
              CREATE TABLE IF NOT EXISTS video_source_items(
                id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,path TEXT NOT NULL,
                signature TEXT NOT NULL,size INTEGER NOT NULL,asset_id TEXT,config_revision INTEGER NOT NULL,
                payload TEXT,status TEXT NOT NULL,job_id INTEGER,message TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);''')
            columns={row[1] for row in db.execute('PRAGMA table_info(video_source_settings)')}
            if 'pending_cursor' not in columns:
                db.execute('ALTER TABLE video_source_settings ADD COLUMN pending_cursor INTEGER NOT NULL DEFAULT 0')
            db.execute('INSERT OR IGNORE INTO video_source_settings(id,revision,configured,settings) VALUES(1,1,0,?)', (json.dumps(defaults),))
            # Local import/enqueue interruption is resumable. Queue UUID lookup
            # is authoritative before any attempt to enqueue again.
            db.execute("UPDATE video_source_items SET status='ready',message=? WHERE job_id IS NULL AND status IN ('importing','enqueueing')",
                       ('Lượt nhập cục bộ bị gián đoạn; sẽ kiểm tra cùng mã trước khi xếp lịch.',))

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Clock must be timezone aware')
        return value.astimezone(timezone.utc)

    def _settings(self, db=None):
        if db is not None:
            row = dict(db.execute('SELECT * FROM video_source_settings WHERE id=1').fetchone())
        else:
            with self.manager.store.connect() as conn:
                row = dict(conn.execute('SELECT * FROM video_source_settings WHERE id=1').fetchone())
        row['settings'] = json.loads(row['settings'])
        return row

    def _guard(self, revision=None):
        current = self._settings()
        if self._closing or not current['configured'] or not current['settings']['enabled'] or revision is not None and current['revision'] != revision:
            raise _Deferred()
        return current

    def status(self, limit=100, offset=0):
        limit = min(100, max(1, int(limit)))
        offset = min(2**63 - 1, max(0, int(offset)))
        current = self._settings()
        with self.manager.store.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM video_source_items ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?', (limit, offset))]
            total = db.execute('SELECT COUNT(*) FROM video_source_items').fetchone()[0]
            counts = dict(db.execute('SELECT status,COUNT(*) FROM video_source_items GROUP BY status').fetchall())
            imported = db.execute('SELECT COUNT(*) FROM video_source_items WHERE asset_id IS NOT NULL').fetchone()[0]
            queued = db.execute('SELECT COUNT(*) FROM video_source_items WHERE job_id IS NOT NULL').fetchone()[0]
            next_page_id,_ = self._next_eligible_page(current,db)
            eligible_page_ids=[pid for pid in current['settings']['page_ids']
                               if self.manager.store.posting_eligibility(pid,db=db)['can_publish']]
        items = []
        for row in rows:
            payload = json.loads(row['payload']) if row['payload'] else {}
            items.append({key: row[key] for key in ('id', 'filename', 'status', 'job_id', 'message')} |
                         {key: payload.get(key) for key in ('page_id', 'page_name', 'scheduled_local', 'timezone')})
        settings = current['settings']
        return {'configured': bool(current['configured']),
                'settings': {**settings, 'directory': str(self.directory), 'revision': current['revision']},
                'stats': {'files_seen': current['files_seen'], 'waiting': current['waiting'], 'imported': imported,
                          'queued': queued, 'skipped': max(counts.get('skipped', 0), current['skipped']),
                          'errors': counts.get('failed', 0) + counts.get('needs_review', 0)},
                'items': items, 'total': total, 'limit': limit, 'offset': offset,
                'running': self._running or self._task is not None and not self._task.done(),
                'last_scan_at': current['last_scan_at'], 'last_error': current['last_error'],
                'posting_eligible_page_ids':eligible_page_ids,
                'posting_blocked_page_ids':[pid for pid in settings['page_ids'] if pid not in eligible_page_ids],
                'next_page_id': next_page_id}

    def _next_eligible_page(self,current,db):
        ids=current['settings']['page_ids']
        for offset in range(len(ids)):
            cursor=current['cursor']+offset
            pid=ids[cursor % len(ids)]
            if self.manager.store.posting_eligibility(pid,db=db)['can_publish']:
                return pid,cursor+1
        return None,current['cursor']

    def configure(self, body):
        body = body if isinstance(body, VideoSourceSettings) else VideoSourceSettings.model_validate(body)
        with self.manager.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._settings(db)
            if old['configured'] and body.revision is None:
                raise AppError('Đọc lại cấu hình nguồn trước khi lưu thay đổi.', 'source_revision_required', 409)
            if body.revision is not None and old['revision'] != body.revision:
                raise AppError('Cấu hình nguồn đã thay đổi; tải lại trước khi lưu.', 'source_revision_changed', 409)
            # Pausing an existing source must remain possible after access is
            # lost. Enabling (and initial setup) always validates every Page.
            if body.enabled or not old['configured']:
                source = db.execute('SELECT token_revision FROM connections WHERE id=?', (body.connection_id,)).fetchone()
                if source is None:
                    raise AppError('Chọn nguồn đang truy cập được các Page.', 'page_access_required', 403)
                for pid in body.page_ids:
                    self.manager.store._current(db, body.connection_id, source['token_revision'], pid)
                    if body.enabled:self.manager.store.ensure_posting_ready(pid,db=db)
            values = body.model_dump(exclude={'revision'})
            db.execute('UPDATE video_source_settings SET configured=1,settings=?,revision=revision+1,last_error=NULL WHERE id=1', (json.dumps(values),))
        if not body.enabled and self._task is not None and not self._task.done():
            self._task.cancel()
        self._next_scan = None
        return self.status()

    def request_scan(self):
        if self._closing or not self._settings()['settings']['enabled']:
            return self.status()
        if not self._running and (self._task is None or self._task.done()):
            coroutine = self.scan_once()
            try:
                self._task = asyncio.create_task(coroutine)
            except RuntimeError:
                coroutine.close()
                self._record_error('Chưa khởi động được lượt quét; thử lại khi server sẵn sàng.')
            else:
                def done(task):
                    if not task.cancelled():
                        with suppress(Exception):
                            task.exception()
                self._task.add_done_callback(done)
                self._next_scan = self._now() + timedelta(seconds=self.interval_seconds)
        return self.status()

    def tick(self):
        if not self._closing and (self._next_scan is None or self._now() >= self._next_scan):
            return self.request_scan()

    def _record_error(self, message):
        with self.manager.store.connect() as db:
            db.execute('UPDATE video_source_settings SET last_error=? WHERE id=1', (message,))

    def _item(self, sha=None, item_id=None):
        with self.manager.store.connect() as db:
            row = db.execute('SELECT * FROM video_source_items WHERE ' + ('sha256=?' if sha is not None else 'id=?'), (sha if sha is not None else item_id,)).fetchone()
        return dict(row) if row else None

    def _update(self, item_id, *, status, message, asset_id=None, payload=None, job_id=None):
        with self.manager.store.connect() as db:
            db.execute('UPDATE video_source_items SET status=?,message=?,asset_id=COALESCE(?,asset_id),payload=COALESCE(?,payload),job_id=COALESCE(?,job_id),updated_at=? WHERE id=?',
                       (status, message, asset_id, json.dumps(payload) if payload is not None else None, job_id, self._now().isoformat(), item_id))

    def _observe(self, files):
        stamp = self._now().timestamp()
        ready = []
        with self.manager.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in files:
                encoded = json.dumps(item['signature'])
                old = db.execute('SELECT * FROM video_source_candidates WHERE path=?', (item['path'],)).fetchone()
                same = old is not None and old['signature'] == encoded
                first = old['first_seen'] if same else stamp
                sha = old['sha256'] if same else None
                db.execute('INSERT INTO video_source_candidates VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET filename=excluded.filename,signature=excluded.signature,first_seen=excluded.first_seen,last_seen=excluded.last_seen,sha256=excluded.sha256',
                           (item['path'], item['filename'], encoded, first, stamp, sha))
                if same and stamp - first >= self.stable_seconds:
                    ready.append(dict(item, sha256=sha))
            db.execute('DELETE FROM video_source_candidates WHERE path IN (SELECT path FROM video_source_candidates ORDER BY last_seen DESC LIMIT -1 OFFSET ?)', (MAX_FILES * 2,))
        return ready

    def _jobs(self, db=None):
        if db is not None:
            return [dict(row) for row in db.execute('SELECT j.*,a.sha256,a.size AS video_size FROM reel_jobs j JOIN reel_assets a ON a.id=j.asset_id')]
        with self.manager.store.connect() as conn:
            return self._jobs(conn)

    def _slot(self, db, page_id, settings, *, exclude=None, preferred=None):
        now = self._now()
        zone = ZoneInfo(settings['timezone'])
        occupied = set()
        for job in self._jobs(db):
            if job['page_id'] == page_id and job['status'] != 'cancelled':
                occupied.add(datetime.fromisoformat(job['scheduled_at']).astimezone(zone).date())
        for row in db.execute("SELECT id,payload FROM video_source_items WHERE status NOT IN ('skipped','failed','cancelled') AND payload IS NOT NULL"):
            if row['id'] == exclude:
                continue
            value = json.loads(row['payload'])
            if value.get('page_id') == page_id and value.get('scheduled_at'):
                occupied.add(datetime.fromisoformat(value['scheduled_at']).astimezone(zone).date())
        if preferred and preferred.get('timezone') == settings['timezone']:
            instant = datetime.fromisoformat(preferred['scheduled_at'])
            local = instant.astimezone(zone)
            if instant > now and local.strftime('%H:%M') == settings['daily_time'] and local.date() not in occupied:
                return {key: preferred[key] for key in ('scheduled_at', 'scheduled_local', 'timezone', 'fold')}
        today = now.astimezone(zone).date()
        for offset in range(3660):
            day = today + timedelta(days=offset)
            if day in occupied:
                continue
            try:
                value = resolve_schedule(day.isoformat() + 'T' + settings['daily_time'], settings['timezone'], fold=0)
            except AppError:
                continue
            if datetime.fromisoformat(value['scheduled_at']) > now:
                return value
        raise AppError('Chưa tìm được ngày đăng còn trống trong giới hạn nguồn.', 'source_slots_full', 409)

    @staticmethod
    def _idea_approval(db,sha):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_pipeline_approved_videos'").fetchone():
            return None
        if not db.execute('SELECT 1 FROM idea_pipeline_approved_videos WHERE video_sha256=?',(sha,)).fetchone():
            return None
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_manual_runs'").fetchone():
            raise AppError('Video idea chưa có lượt chạy hợp lệ.','idea_approval_changed',409)
        row=db.execute('''SELECT a.*,v.reservation_id AS canonical_reservation,
            v.video_sha256 AS canonical_sha,v.idea_id AS canonical_idea,i.campaign_state,i.campaign,
            i.artwork_sha256 AS canonical_artwork,r.status AS owning_run_status,
            r.cancel_requested AS owning_run_cancelled,r.idea_id AS owning_idea,r.artwork_sha256 AS owning_artwork
            ,r.payload AS owning_payload,r.payload_hash AS owning_payload_hash
            FROM idea_pipeline_approved_videos a
            JOIN idea_pipeline_variants v ON v.id=a.variant_id
            JOIN idea_pipeline_ideas i ON i.id=v.idea_id
            JOIN idea_manual_runs r ON r.id=a.run_id
            WHERE a.video_sha256=?''',(sha,)).fetchone()
        if row is None:raise AppError('Kết quả video idea không còn khớp sổ theo dõi.','idea_approval_changed',409)
        row=dict(row)
        try:
            payload=json.loads(row['owning_payload'])
            manual=payload.get('review_policy')=='manual_review_v1'
            valid=(hashlib.sha256(row['owning_payload'].encode()).hexdigest()==row['owning_payload_hash']
                   and row['status'] in (('manual_review_pending',) if manual else ('approved','ready')) and row['canonical_sha']==sha
                   and row['canonical_idea']==row['idea_id'] and row['campaign_state']=='verified'
                   and row['owning_idea']==row['idea_id'] and row['owning_artwork']==row['canonical_artwork']
                   and row['reservation_id']==row['canonical_reservation']
                   and str(UUID(row['reservation_id']))==row['reservation_id']
                   and json.loads(row['campaign'])['campaign_url']==row['caption']
                   and re.fullmatch(r'https://blueox179\.com/campaign/[A-Za-z0-9][A-Za-z0-9_-]{0,127}',row['caption']))
        except (ValueError,TypeError,KeyError):valid=False
        if not valid:raise AppError('Video idea chưa khớp kết quả đã kiểm tra; không tự xếp lịch.','idea_approval_changed',409)
        if manual and not row['owning_run_cancelled'] and row['owning_run_status'] in ('running','importing','completed','needs_review'):
            return row
        if not row['owning_run_cancelled'] and row['owning_run_status'] in ('queued','preparing','running','importing'):
            raise _Deferred()
        if row['owning_run_cancelled'] or row['owning_run_status']!='completed':
            raise AppError('Lượt chạy idea đã dừng hoặc cần kiểm tra; chưa xếp lịch video mới.','idea_run_not_completed',409)
        return row

    def _reserve(self, candidate, sha, caption, revision):
        stamp = self._now().isoformat()
        with self.manager.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            approval=self._idea_approval(db,sha)
            old = db.execute('SELECT * FROM video_source_items WHERE sha256=?', (sha,)).fetchone()
            if old:
                if approval and old['id']!=approval['reservation_id']:
                    raise AppError('Video đã có mã lịch khác; cần đối chiếu trước khi tiếp tục.','idea_schedule_conflict',409)
                return dict(old)
            current = self._settings(db)
            if current['revision'] != revision or not current['settings']['enabled'] or self._closing:
                raise _Deferred()
            if approval and approval['source_revision']!=revision:
                raise AppError('Cấu hình chia Page đã thay đổi sau khi chạy idea; cần kiểm tra lại.','idea_schedule_changed',409)
            if db.execute('SELECT COUNT(*) FROM video_source_items').fetchone()[0] >= MAX_ITEMS:
                raise AppError('Nguồn đã đạt giới hạn lịch sử nhập; cần kiểm tra trước khi nhập thêm.', 'source_history_full', 409)
            key = approval['reservation_id'] if approval else str(uuid4())
            if approval:caption=approval['caption']
            history = db.execute('SELECT 1 FROM reel_jobs j JOIN reel_assets a ON a.id=j.asset_id WHERE a.sha256=? LIMIT 1', (sha,)).fetchone()
            payload = None
            state, message = 'skipped', 'Video đã có trong lịch sử hàng đợi token; không tạo lại.'
            if history is None:
                settings = current['settings']
                pid,next_cursor = self._next_eligible_page(current,db)
                if pid is None:
                    raise AppError('Chưa có Page đã đổi thông tin để nhận video. Mở Pages → Chỉnh sửa Page trước khi bật đăng.',
                                   'page_information_required',422)
                self.manager.store.ensure_posting_ready(pid,db=db)
                conn = db.execute('SELECT token_revision FROM connections WHERE id=?', (settings['connection_id'],)).fetchone()
                if conn is None:
                    raise AppError('Nguồn token đã thay đổi.', 'page_access_required', 409)
                source = self.manager.store._current(db, settings['connection_id'], conn['token_revision'], pid)
                page = db.execute('SELECT name FROM pages WHERE id=?', (pid,)).fetchone()
                rendered = _caption((caption if caption is not None else settings['caption_template']).replace('{page_name}', page['name']))
                payload = {'request_id': key, 'page_id': pid, 'page_name': page['name'],
                           'connection_id': settings['connection_id'], 'connection_label': source['label'],
                           'token_revision': conn['token_revision'], 'caption': rendered,
                           **self._slot(db, pid, settings)}
                state, message = 'ready', 'Đã giữ mã và Page; đang nhập video trước khi xếp lịch.'
                db.execute('UPDATE video_source_settings SET cursor=? WHERE id=1',(next_cursor,))
            db.execute('INSERT INTO video_source_items(id,sha256,filename,path,signature,size,config_revision,payload,status,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (key, sha, candidate['filename'], candidate['path'], json.dumps(candidate['signature']), candidate['signature'][2],
                        revision, json.dumps(payload) if payload else None, state, message, stamp, stamp))
            return dict(db.execute('SELECT * FROM video_source_items WHERE id=?', (key,)).fetchone())

    def _reconcile(self, item):
        payload = json.loads(item['payload']) if item['payload'] else {}
        found = next((job for job in self._jobs() if job['request_id'] == item['id']), None)
        if found is None:
            return False
        keys = ('page_id', 'connection_id', 'token_revision', 'asset_id', 'caption', 'scheduled_at', 'scheduled_local', 'timezone', 'fold')
        if any(found.get(key) != payload.get(key) for key in keys):
            self._update(item['id'], status='needs_review', message='Mã lịch đã được dùng cho dữ liệu khác; không tạo lại.', job_id=found['id'])
        else:
            state = found['status'] if found['status'] in ('completed', 'cancelled', 'needs_review') else 'queued'
            self._update(item['id'], status=state, message='Đã tìm thấy lịch token cùng mã; không gửi lại.', job_id=found['id'])
        return True

    async def _import(self, item, candidate, revision):
        for old in self.manager.store.reel_assets():
            if old['sha256'] == item['sha256'] and old['size'] == item['size']:
                try:
                    valid = await asyncio.to_thread(self.assets.resolve, old['id'])
                    self._guard(revision)
                    return valid
                except _Deferred:
                    raise
                except Exception:
                    continue
        self._update(item['id'], status='importing', message='Đang sao chép video vào kho riêng; chưa xếp lịch.')
        path, expected = Path(candidate['path']), candidate['signature']
        handle = None
        digest = hashlib.sha256()
        try:
            def opened():
                if _checked(path, self.directory) != expected:
                    raise _FileChanged()
                stream = path.open('rb')
                if _signature(os.fstat(stream.fileno())) != expected:
                    stream.close()
                    raise _FileChanged()
                return stream
            handle = await asyncio.to_thread(opened)
            async def stream():
                while True:
                    self._guard(revision)
                    block = await asyncio.to_thread(handle.read, 1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    yield block
                self._guard(revision)
                def unchanged():
                    if (_signature(os.fstat(handle.fileno())) != expected or
                            _checked(path, self.directory) != expected or digest.hexdigest() != item['sha256']):
                        raise _FileChanged()
                await asyncio.to_thread(unchanged)
            return await self.assets.ingest(stream(), clean_video_name(path.name), content_length=item['size'])
        finally:
            if handle is not None:
                handle.close()

    def _enqueue(self, item, rechecks=0):
        if self._reconcile(item):
            return
        self._guard()
        payload = json.loads(item['payload'])
        with self.manager.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._settings(db)
            settings = current['settings']
            approval=self._idea_approval(db,item['sha256'])
            if approval and (approval['source_revision']!=current['revision']
                             or approval['reservation_id']!=item['id']
                             or approval['caption']!=payload['caption']):
                raise AppError('Kết quả idea hoặc cấu hình chia Page đã đổi; chưa tạo lịch.','idea_schedule_changed',409)
            if not settings['enabled'] or self._closing:
                raise _Deferred()
            if payload['page_id'] not in settings['page_ids'] or payload['connection_id'] != settings['connection_id']:
                raise AppError('Page hoặc nguồn nhập đã thay đổi; kiểm tra video đang chờ.', 'source_target_changed', 409)
            self.manager.store._current(db, payload['connection_id'], payload['token_revision'], payload['page_id'])
            self.manager.store.ensure_posting_ready(payload['page_id'],db=db)
            payload.update(self._slot(db, payload['page_id'], settings, exclude=item['id'], preferred=payload))
            payload.update(asset_id=item['asset_id'], asset_name=self.manager.store.reel_asset(item['asset_id'])['name'])
            db.execute('UPDATE video_source_items SET payload=?,config_revision=?,status=\'enqueueing\',updated_at=? WHERE id=?',
                       (json.dumps(payload), current['revision'], self._now().isoformat(), item['id']))
        # Do not hold our writer transaction while the existing queue starts
        # its own transaction. No await separates this guard from reel_create.
        self._guard(current['revision'])
        if datetime.fromisoformat(payload['scheduled_at']) <= self._now():
            if rechecks >= 2:
                raise _Deferred()
            return self._enqueue(self._item(item_id=item['id']), rechecks + 1)
        job, _ = self.manager.store.reel_create(payload)
        self._update(item['id'], status='queued', message='Đã xếp vào hàng đợi token hiện có; chưa gửi tới Meta.', job_id=job['id'])

    async def _process(self, item, candidate=None):
        """Return whether media/import/enqueue work consumed the scan's IO quota."""
        if item['job_id'] is not None or self._reconcile(item):
            return False
        if item['status'] in FINAL:
            return False
        current = self._guard()
        revision = current['revision']
        payload = json.loads(item['payload'])
        if payload['page_id'] not in current['settings']['page_ids'] or payload['connection_id'] != current['settings']['connection_id']:
            self._update(item['id'], status='needs_review', message='Page hoặc nguồn đã đổi; không tự chuyển video sang Page khác.')
            return False
        worked=False
        try:
            self.manager._edit_source(payload['page_id'], payload['connection_id'], payload['token_revision'])
            self.manager.store.ensure_posting_ready(payload['page_id'])
            if item['asset_id'] is None:
                if candidate is None:
                    return False
                worked=True
                asset = await self._import(item, candidate, revision)
                self._update(item['id'], status='ready', message='Đã nhập video; chờ kiểm tra trước khi xếp lịch.', asset_id=asset['id'])
                item = self._item(item_id=item['id'])
            else:
                worked=True
                await asyncio.to_thread(self.assets.resolve, item['asset_id'])
            self._guard(revision)
            self._enqueue(item)
        except asyncio.CancelledError:
            self._update(item['id'], status='ready', message='Đã dừng nhập cục bộ trước khi xếp lịch; giữ cùng mã để tiếp tục.')
            raise
        except _Deferred:
            self._update(item['id'], status='ready', message='Đã tạm dừng trước khi xếp lịch; giữ video và cùng mã.')
            raise
        except AppError as error:
            if error.code=='page_information_required':
                self._update(item['id'], status='ready', message=error.detail)
            else:self._update(item['id'], status='needs_review', message='Nguồn hoặc Page không còn khớp; chưa tạo lịch mới.')
        except Exception:
            # A pause/file-copy change can be wrapped by asset ingestion. Only
            # keep it resumable when no queue job with this UUID exists.
            if self._reconcile(self._item(item_id=item['id'])):
                return worked
            try:
                self._guard(revision)
            except _Deferred:
                self._update(item['id'], status='ready', message='Đã tạm dừng nhập; sẽ kiểm tra cùng mã khi bật lại.')
                raise
            if candidate is not None:
                try:
                    changed = await asyncio.to_thread(_checked, candidate['path'], self.directory)
                except (OSError, ValueError, _FileChanged):
                    changed = None
                if changed != candidate['signature']:
                    self._update(item['id'], status='ready', message='Tệp nguồn đổi trong lúc sao chép; chờ quan sát ổn định lại trước khi nhập.')
                    return worked
            self._update(item['id'], status='failed', message='Chưa nhập hoặc xếp lịch được video; kiểm tra tệp và trạng thái nguồn.')
        return worked

    async def scan_once(self):
        if self._running or self._closing:
            return self.status()
        try:
            current = self._guard()
        except _Deferred:
            return self.status()
        self._running = True
        processed = skipped = waiting = 0
        files = []
        warning = None
        try:
            # Bound metadata retries separately from media IO. A blocked old
            # reservation must neither consume every import slot nor pin the
            # first 100 pending rows forever, including across restarts.
            handled=set()
            with self.manager.store.connect() as db:
                cursor=self._settings(db)['pending_cursor']
                query="SELECT rowid AS pending_rowid,* FROM video_source_items WHERE job_id IS NULL AND status IN ('ready','enqueueing') AND rowid"
                pending=[dict(row) for row in db.execute(query+'>? ORDER BY rowid LIMIT ?',(cursor,PENDING_RETRY_BATCH))]
                if len(pending)<PENDING_RETRY_BATCH:
                    pending.extend(dict(row) for row in db.execute(query+'<=? ORDER BY rowid LIMIT ?',(cursor,PENDING_RETRY_BATCH-len(pending))))
            for item in pending:
                self._guard()
                if item['asset_id'] is not None:
                    handled.add(item['id'])
                    processed+=int(await self._process(item))
                elif self._reconcile(item):handled.add(item['id'])
                with self.manager.store.connect() as db:
                    db.execute('UPDATE video_source_settings SET pending_cursor=? WHERE id=1',(item['pending_rowid'],))
                if processed>=self.max_imports_per_scan:break
            files, warning = await asyncio.to_thread(_walk, self.directory)
            ready = self._observe(files)
            waiting = len(files) - len(ready)
            hashed = 0
            for candidate in ready:
                current = self._guard()
                if processed >= self.max_imports_per_scan:
                    break
                sha = candidate.get('sha256')
                if sha is None:
                    if hashed >= self.max_imports_per_scan * 4:
                        break
                    try:
                        sha = await asyncio.to_thread(_hash_file, candidate['path'], candidate['signature'], self.directory)
                    except (OSError, ValueError, _FileChanged):
                        waiting += 1
                        continue
                    hashed += 1
                    with self.manager.store.connect() as db:
                        db.execute('UPDATE video_source_candidates SET sha256=? WHERE path=? AND signature=?',
                                   (sha, candidate['path'], json.dumps(candidate['signature'])))
                item = self._item(sha=sha)
                if item is not None and (item['job_id'] is not None or item['status'] in FINAL):
                    skipped += 1
                    continue
                if item is not None and item['id'] in handled:
                    waiting+=1
                    continue
                if item is None:
                    # A changed source can have interrupted an uncommitted
                    # import. Retire only that old local snapshot, preserving
                    # all imported/queued/uncertain publication history.
                    with self.manager.store.connect() as db:
                        db.execute("UPDATE video_source_items SET status='failed',message=?,updated_at=? WHERE path=? AND sha256!=? AND asset_id IS NULL AND job_id IS NULL AND status='ready'",
                                   ('Nội dung nguồn đã đổi trước khi nhập xong; bản cũ không được xếp lịch.', self._now().isoformat(), candidate['path'], sha))
                    try:
                        caption = await asyncio.to_thread(_sidecar, candidate['path'], self.directory)
                        self._guard(current['revision'])
                        item = self._reserve(candidate, sha, caption, current['revision'])
                    except AppError:
                        raise
                    except (UnicodeError, ValueError, OSError, _FileChanged):
                        warning = 'Một tệp caption không hợp lệ hoặc đang thay đổi; chưa nhập video tương ứng.'
                        continue
                if item['status'] == 'skipped':
                    skipped += 1
                    continue
                handled.add(item['id'])
                processed += int(await self._process(item, candidate))
        except (asyncio.CancelledError, _Deferred):
            warning = 'Lượt nhập đã tạm dừng; lịch token đã tạo được giữ nguyên.'
        except AppError as error:
            warning = error.detail if error.code=='page_information_required' else 'Nguồn hoặc liên kết Page đã thay đổi; chưa xếp thêm lịch.'
        except Exception:
            warning = 'Chưa quét được nguồn an toàn; kiểm tra thư mục và trạng thái ứng dụng.'
        finally:
            with self.manager.store.connect() as db:
                db.execute('UPDATE video_source_settings SET last_scan_at=?,last_error=?,files_seen=?,waiting=?,skipped=? WHERE id=1',
                           (self._now().isoformat(), warning, len(files), waiting, skipped))
            self._running = False
        return self.status()

    async def shutdown(self):
        self._closing = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
