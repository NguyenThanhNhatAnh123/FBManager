"""Deterministic daily production plans; no worker, file, network or publishing IO.

The controller owns discovery/readiness and calls this module after initializing
the canonical idea/source ledgers. Claims allocate counts only; canonical variant
UUIDs remain owned by IdeaPipeline.reserve_variants. Historical seven-variant
ideas, jobs and batches are never rewritten here.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import re
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from .errors import AppError


ZONE = 'America/Los_Angeles'
DAILY_TIME = '06:00'
MAX_PAGES = 14
PREVIEW_WINDOW = 1000
DEFERRED_WINDOW = 50
ITEM_STATES = {'pending', 'queued', 'completed', 'needs_review'}
ITEM_ERROR_CODES = {'dispatch_failed', 'source_changed', 'idea_changed', 'run_conflict',
                    'run_needs_review', 'run_cancelled', 'uncertain', 'preflight_blocked',
                    'worker_unavailable', 'approval_required'}


def utcnow():
    return datetime.now(timezone.utc)


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def allocate_counts(ordered_idea_ids, page_count, start_index=0):
    """Split P videos across N ideas, rotating the first P when N exceeds P."""
    if (not isinstance(ordered_idea_ids, (list, tuple)) or any(not _uuid(i) for i in ordered_idea_ids)
            or len(set(ordered_idea_ids)) != len(ordered_idea_ids)
            or type(page_count) is not int or not 1 <= page_count <= MAX_PAGES
            or type(start_index) is not int or start_index < 0):
        raise ValueError('Invalid daily allocation')
    total = len(ordered_idea_ids)
    if not total:
        return {'allocations': [], 'deferred_ids': [], 'next_index': 0, 'total_videos': 0}
    start = start_index % total
    rotated = list(ordered_idea_ids[start:]) + list(ordered_idea_ids[:start])
    selected = rotated[:page_count]
    base, remainder = divmod(page_count, len(selected))
    allocations = [{'idea_id': iid, 'variant_count': base + (index < remainder), 'position': index + 1}
                   for index, iid in enumerate(selected)]
    return {'allocations': allocations, 'deferred_ids': rotated[len(selected):],
            'next_index': (start + len(selected)) % total, 'total_videos': page_count}


class AutoBatchPlanner:
    def __init__(self, store, *, clock=utcnow):
        self.store, self.clock = store, clock
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS idea_auto_batch_settings(
                  id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                  revision INTEGER NOT NULL DEFAULT 1,cursor_created_at TEXT,cursor_idea_id TEXT,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS idea_auto_batches(
                  id TEXT PRIMARY KEY,day_key TEXT NOT NULL UNIQUE,settings_revision INTEGER NOT NULL,
                  source_revision INTEGER NOT NULL,snapshot TEXT NOT NULL,plan_hash TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS idea_auto_batch_items(
                  batch_id TEXT NOT NULL REFERENCES idea_auto_batches(id),idea_id TEXT NOT NULL UNIQUE,
                  artwork_sha256 TEXT NOT NULL,variant_count INTEGER NOT NULL CHECK(variant_count BETWEEN 1 AND 14),
                  position INTEGER NOT NULL,request_id TEXT NOT NULL UNIQUE,
                  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','queued','completed','needs_review')),
                  run_id TEXT,error_code TEXT,updated_at TEXT NOT NULL,
                  PRIMARY KEY(batch_id,idea_id),UNIQUE(batch_id,position));
                CREATE INDEX IF NOT EXISTS auto_batch_order ON idea_auto_batches(created_at,id);
                CREATE INDEX IF NOT EXISTS auto_batch_item_state ON idea_auto_batch_items(batch_id,status,position);
            ''')
            db.execute('INSERT OR IGNORE INTO idea_auto_batch_settings(id,updated_at) VALUES(1,?)', (self._now().isoformat(),))

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Timezone-aware clock required')
        return value.astimezone(timezone.utc)

    def _day(self, value=None):
        if value is None:
            return self._now().astimezone(ZoneInfo(ZONE)).date().isoformat()
        try:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except (TypeError, ValueError):
            raise AppError('Ngày batch không hợp lệ.', 'invalid_auto_day', 422) from None
        return value

    @staticmethod
    def _has(db, table):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def _settings(self, db):
        return dict(db.execute('SELECT * FROM idea_auto_batch_settings WHERE id=1').fetchone())

    def settings(self):
        with self.store.connect() as db:
            row = self._settings(db)
        return {'enabled': bool(row['enabled']), 'revision': row['revision'], 'timezone': ZONE,
                'daily_time': DAILY_TIME, 'max_batches_per_day': 1, 'updated_at': row['updated_at']}

    def configure(self, enabled, expected_revision):
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 1:
            raise AppError('Cấu hình tự động không hợp lệ.', 'invalid_auto_settings', 422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._settings(db)
            if row['revision'] != expected_revision:
                raise AppError('Cấu hình tự động đã thay đổi. Đọc lại trước khi lưu.', 'auto_settings_changed', 409)
            if bool(row['enabled']) != enabled:
                db.execute('UPDATE idea_auto_batch_settings SET enabled=?,revision=revision+1,updated_at=? WHERE id=1',
                           (int(enabled), self._now().isoformat()))
        return self.settings()

    def _source(self, db):
        source = {'revision': 0, 'page_ids': [], 'connection_id': None, 'daily_time': DAILY_TIME, 'timezone': ZONE}
        blockers = []
        if not self._has(db, 'video_source_settings'):
            return source, ['schedule_unavailable']
        row = db.execute('SELECT configured,revision,settings FROM video_source_settings WHERE id=1').fetchone()
        try:
            values = json.loads(row['settings']) if row else {}
            ids = values.get('page_ids', [])
            valid_ids = (isinstance(ids, list) and len(set(ids)) == len(ids)
                         and all(isinstance(pid, str) and re.fullmatch(r'[1-9][0-9]{0,31}', pid) for pid in ids))
            if not valid_ids:
                raise ValueError()
            connection_id = values.get('connection_id')
            source = {'revision': row['revision'], 'page_ids': ids,
                      'connection_id': connection_id if type(connection_id) is int and connection_id > 0 else None,
                      'daily_time': DAILY_TIME if values.get('daily_time') == DAILY_TIME else None,
                      'timezone': ZONE if values.get('timezone') == ZONE else None}
            if (row['configured'] != 1 or values.get('enabled') is not True or len(ids) != MAX_PAGES
                    or type(row['revision']) is not int or row['revision'] < 1
                    or type(source['connection_id']) is not int or source['connection_id'] < 1
                    or source['daily_time'] != DAILY_TIME or source['timezone'] != ZONE):
                blockers.append('schedule_unavailable')
            if ids and not all(self.store.posting_eligibility(pid, db=db)['can_publish'] for pid in ids):
                blockers.append('page_information_required')
        except (ValueError, TypeError, KeyError, AttributeError):
            return source, ['schedule_unavailable']
        return source, blockers

    def _candidates(self, db, settings):
        if not self._has(db, 'idea_pipeline_ideas') or not self._has(db, 'idea_pipeline_variants'):
            return [], 0
        conditions = ["i.campaign_state IN ('prepared','verified')",
            'EXISTS(SELECT 1 FROM idea_pipeline_variants v WHERE v.idea_id=i.id)',
            "NOT EXISTS(SELECT 1 FROM idea_pipeline_variants v WHERE v.idea_id=i.id AND (v.video_sha256 IS NOT NULL OR v.status!='planned'))",
            'NOT EXISTS(SELECT 1 FROM idea_auto_batch_items a WHERE a.idea_id=i.id)']
        if self._has(db, 'idea_manual_runs'):
            conditions.append('NOT EXISTS(SELECT 1 FROM idea_manual_runs r WHERE r.idea_id=i.id)')
        if self._has(db, 'idea_pipeline_jobs'):
            conditions.append("""NOT EXISTS(SELECT 1 FROM idea_pipeline_jobs j WHERE j.idea_id=i.id AND
                (j.status IN ('leased','submitting','running','in_progress','needs_review','uncertain') OR
                 (j.worker_id IS NOT NULL AND j.status NOT IN ('completed','cancelled'))))""")
        where = ' AND '.join(conditions)
        count = db.execute('SELECT COUNT(*) FROM idea_pipeline_ideas i WHERE ' + where).fetchone()[0]
        key = (settings['cursor_created_at'], settings['cursor_idea_id'])
        order = 'i.created_at,i.id'
        args = []
        if all(isinstance(value, str) for value in key):
            order = 'CASE WHEN (i.created_at,i.id)>(?,?) THEN 0 ELSE 1 END,' + order
            args.extend(key)
        rows = db.execute('SELECT i.id,i.artwork_sha256,i.created_at FROM idea_pipeline_ideas i WHERE ' + where
                          + ' ORDER BY ' + order + ' LIMIT ?', (*args, PREVIEW_WINDOW)).fetchall()
        # Corrupt legacy identities remain unselected; no names or paths enter a plan.
        return [dict(row) for row in rows if _uuid(row['id']) and _sha(row['artwork_sha256'])], count

    def _preview(self, db, day_key):
        settings = self._settings(db)
        source, blockers = self._source(db)
        ideas, count = self._candidates(db, settings)
        capacity = len(source['page_ids'])
        if not 1 <= capacity <= MAX_PAGES:
            allocation = {'allocations': [], 'deferred_ids': [], 'total_videos': 0}
            blockers.append('invalid_capacity')
        else:
            allocation = allocate_counts([row['id'] for row in ideas], capacity)
        by_id = {row['id']: row for row in ideas}
        allocations = [{**item, 'artwork_sha256': by_id[item['idea_id']]['artwork_sha256']}
                       for item in allocation['allocations']]
        core = {'day_key': day_key, 'settings_revision': settings['revision'], 'source': source,
                'candidate_count': count, 'allocations': allocations, 'total_videos': allocation['total_videos'],
                'deferred_count': max(0, count - len(allocations)),
                'deferred_idea_ids': allocation['deferred_ids'][:DEFERRED_WINDOW]}
        plan_hash = hashlib.sha256(_json(core).encode('utf-8')).hexdigest()
        if not count or not allocations:
            blockers.append('no_eligible_ideas')
        if not settings['enabled']:
            blockers.append('auto_disabled')
        existing = db.execute('SELECT id FROM idea_auto_batches WHERE day_key=?', (day_key,)).fetchone()
        incomplete = db.execute("SELECT id FROM idea_auto_batches WHERE status!='completed' ORDER BY created_at,id LIMIT 1").fetchone()
        if existing:
            frozen = self._batch(db, existing['id'])
            core = dict(frozen['snapshot'])
            plan_hash = frozen['plan_hash']
            blockers = [code for code in blockers if code != 'no_eligible_ideas']
            blockers.append('batch_already_claimed')
        elif incomplete:
            blockers.append('previous_batch_incomplete')
        return {**core, 'plan_hash': plan_hash, 'capacity': len(core['source']['page_ids']),
                'enabled': bool(settings['enabled']), 'revision': settings['revision'],
                'ready': not blockers, 'blocker_codes': list(dict.fromkeys(blockers))}

    def preview(self, day_key=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._preview(db, self._day(day_key))

    def claim_day(self, expected_revision, expected_preview_hash=None):
        if type(expected_revision) is not int or expected_revision < 1 or not _sha(expected_preview_hash):
            raise AppError('Đọc lại bản xem trước trước khi tạo batch.', 'invalid_auto_claim', 422)
        day_key, stamp = self._day(), self._now().isoformat()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            settings = self._settings(db)
            if settings['revision'] != expected_revision:
                raise AppError('Cấu hình tự động đã thay đổi.', 'auto_settings_changed', 409)
            if not settings['enabled']:
                raise AppError('Tự động đang tắt.', 'auto_disabled', 409)
            existing = db.execute('SELECT id,plan_hash FROM idea_auto_batches WHERE day_key=?', (day_key,)).fetchone()
            if existing:
                if existing['plan_hash'] != expected_preview_hash:
                    raise AppError('Batch của ngày này đã có kế hoạch khác.', 'auto_preview_changed', 409)
                return {**self._batch(db, existing['id']), 'replayed': True}
            plan = self._preview(db, day_key)
            if plan['plan_hash'] != expected_preview_hash:
                raise AppError('Danh sách idea hoặc nguồn đã thay đổi. Đọc lại bản xem trước.', 'auto_preview_changed', 409)
            if not plan['ready']:
                code = 'auto_batch_incomplete' if 'previous_batch_incomplete' in plan['blocker_codes'] else 'auto_plan_unavailable'
                raise AppError('Chưa thể tạo batch tự động từ điều kiện hiện tại.', code, 409)
            snapshot = {key: plan[key] for key in ('day_key', 'settings_revision', 'source', 'candidate_count',
                        'allocations', 'total_videos', 'deferred_count', 'deferred_idea_ids')}
            batch_id = str(uuid4())
            db.execute('INSERT INTO idea_auto_batches VALUES(?,?,?,?,?,?,?,?,?)',
                       (batch_id, day_key, settings['revision'], plan['source']['revision'], _json(snapshot),
                        plan['plan_hash'], 'pending', stamp, stamp))
            for item in plan['allocations']:
                request_id = str(uuid5(NAMESPACE_URL, 'page-auto-batch:' + batch_id + ':' + item['idea_id']))
                db.execute('''INSERT INTO idea_auto_batch_items(batch_id,idea_id,artwork_sha256,variant_count,
                    position,request_id,status,updated_at) VALUES(?,?,?,?,?,?,'pending',?)''',
                    (batch_id, item['idea_id'], item['artwork_sha256'], item['variant_count'], item['position'], request_id, stamp))
            last = plan['allocations'][-1]['idea_id']
            created = db.execute('SELECT created_at FROM idea_pipeline_ideas WHERE id=?', (last,)).fetchone()[0]
            db.execute('UPDATE idea_auto_batch_settings SET cursor_created_at=?,cursor_idea_id=? WHERE id=1', (created, last))
            return {**self._batch(db, batch_id), 'replayed': False}

    def _batch(self, db, batch_id):
        row = db.execute('SELECT * FROM idea_auto_batches WHERE id=?', (batch_id,)).fetchone()
        if row is None:
            raise AppError('Không có batch tự động này.', 'auto_batch_not_found', 404)
        items = [dict(item) for item in db.execute('SELECT * FROM idea_auto_batch_items WHERE batch_id=? ORDER BY position', (batch_id,))]
        return {key: row[key] for key in ('id', 'day_key', 'settings_revision', 'source_revision', 'plan_hash', 'status', 'created_at', 'updated_at')} | {
            'snapshot': json.loads(row['snapshot']), 'items': items}

    def batch(self, batch_id):
        if not _uuid(batch_id):
            raise AppError('Mã batch không hợp lệ.', 'invalid_auto_batch', 422)
        with self.store.connect() as db:
            return self._batch(db, batch_id)

    def item(self, batch_id, idea_id):
        if not _uuid(batch_id) or not _uuid(idea_id):
            raise AppError('Mã item tự động không hợp lệ.', 'invalid_auto_item', 422)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM idea_auto_batch_items WHERE batch_id=? AND idea_id=?', (batch_id, idea_id)).fetchone()
        if row is None:
            raise AppError('Không có item tự động này.', 'auto_item_not_found', 404)
        return dict(row)

    def current_batch(self, day_key=None):
        with self.store.connect() as db:
            row = db.execute('SELECT id FROM idea_auto_batches WHERE day_key=?', (self._day(day_key),)).fetchone()
            return self._batch(db, row['id']) if row else None

    def visible_allocation(self, idea_ids):
        """Return only frozen counts for the displayed ideas, without batch snapshots."""
        ids=list(dict.fromkeys(idea_ids))
        if len(ids)>100 or any(not _uuid(value) for value in ids):
            raise AppError('Danh sách idea không hợp lệ.', 'invalid_auto_window', 422)
        with self.store.connect() as db:
            row=db.execute("SELECT id,status FROM idea_auto_batches WHERE status!='completed' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                row=db.execute('SELECT id,status FROM idea_auto_batches WHERE day_key=?',(self._day(),)).fetchone()
            if row is None:return None
            items=[]
            if ids:
                placeholders=','.join('?' for _ in ids)
                items=[dict(item) for item in db.execute('SELECT idea_id,variant_count FROM idea_auto_batch_items '
                    'WHERE batch_id=? AND idea_id IN ('+placeholders+') ORDER BY position LIMIT 100',(row['id'],*ids))]
            return {'id':row['id'],'status':row['status'],'items':items}

    def list_batches(self, limit=10, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise AppError('Phân trang batch không hợp lệ.', 'invalid_auto_window', 422)
        with self.store.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM idea_auto_batches').fetchone()[0]
            ids = db.execute('SELECT id FROM idea_auto_batches ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
            return {'items': [self._batch(db, row['id']) for row in ids], 'total': count, 'limit': limit, 'offset': offset}

    def find_incomplete_batch(self):
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM idea_auto_batches WHERE status!='completed' ORDER BY created_at,id LIMIT 1").fetchone()
            return self._batch(db, row['id']) if row else None

    def mark_item(self, batch_id, idea_id, status, run_id=None, error_code=None):
        if (not _uuid(batch_id) or not _uuid(idea_id) or not isinstance(status, str) or status not in ITEM_STATES
                or run_id is not None and not _uuid(run_id)
                or status == 'needs_review' and (not isinstance(error_code, str) or error_code not in ITEM_ERROR_CODES)
                or status != 'needs_review' and error_code is not None):
            raise AppError('Tiến độ item tự động không hợp lệ.', 'invalid_auto_item', 422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM idea_auto_batch_items WHERE batch_id=? AND idea_id=?', (batch_id, idea_id)).fetchone()
            if row is None:
                raise AppError('Không có item tự động này.', 'auto_item_not_found', 404)
            rid = run_id or row['run_id']
            if row['run_id'] is not None and rid != row['run_id']:
                raise AppError('Item đã gắn với lượt chạy khác.', 'auto_run_conflict', 409)
            if (row['status'] == 'completed' and status != 'completed'
                    or row['status'] == 'needs_review' and status not in ('needs_review', 'completed')
                    or row['status'] == 'queued' and status == 'pending'):
                raise AppError('Không tự chạy lại item đã hoàn tất hoặc cần đối chiếu.', 'auto_item_held', 409)
            if rid is not None or status in ('queued', 'completed'):
                if not rid or not self._has(db, 'idea_manual_runs'):
                    raise AppError('Chưa có lượt chạy gắn với item.', 'auto_run_conflict', 409)
                run = db.execute('SELECT request_id,idea_id,artwork_sha256,status FROM idea_manual_runs WHERE id=?', (rid,)).fetchone()
                if (run is None or run['request_id'] != row['request_id'] or run['idea_id'] != idea_id
                        or run['artwork_sha256'] != row['artwork_sha256']
                        or status == 'completed' and run['status'] != 'completed'
                        or status == 'queued' and run['status'] not in ('queued', 'preparing', 'running', 'importing')):
                    raise AppError('Lượt chạy không khớp item tự động.', 'auto_run_conflict', 409)
                # This records a canonical dispatch that already happened. The
                # authorizer gates new requests; disabling must not prevent
                # reconciliation after a request/ledger crash boundary.
            stamp = self._now().isoformat()
            db.execute('UPDATE idea_auto_batch_items SET status=?,run_id=?,error_code=?,updated_at=? WHERE batch_id=? AND idea_id=?',
                       (status, rid, error_code, stamp, batch_id, idea_id))
            states = [item[0] for item in db.execute('SELECT status FROM idea_auto_batch_items WHERE batch_id=?', (batch_id,))]
            batch_state = ('completed' if all(value == 'completed' for value in states) else
                           'needs_review' if 'needs_review' in states else 'queued' if 'queued' in states else 'pending')
            db.execute('UPDATE idea_auto_batches SET status=?,updated_at=? WHERE id=?', (batch_state, stamp, batch_id))
            return self._batch(db, batch_id)

    def authorize(self, idea_id, request_id, count, context, *, running=False):
        if (not _uuid(idea_id) or not _uuid(request_id) or type(count) is not int or not 1 <= count <= MAX_PAGES
                or type(running) is not bool or not isinstance(context, dict)
                or set(context) != {'batch_id', 'settings_revision', 'day_key'} or not _uuid(context.get('batch_id'))):
            return False
        with self.store.connect() as db:
            row = db.execute('''SELECT a.*,b.settings_revision AS frozen_revision,b.day_key,b.source_revision,b.snapshot
                FROM idea_auto_batch_items a JOIN idea_auto_batches b ON b.id=a.batch_id
                WHERE a.batch_id=? AND a.idea_id=?''', (context['batch_id'], idea_id)).fetchone()
            if (row is None or row['request_id'] != request_id or row['variant_count'] != count
                    or row['status'] not in ('pending', 'queued')
                    or type(context.get('settings_revision')) is not int or context['settings_revision'] != row['frozen_revision']
                    or context.get('day_key') != row['day_key'] or not running and not self._settings(db)['enabled']):
                return False
            idea = db.execute('SELECT artwork_sha256 FROM idea_pipeline_ideas WHERE id=?', (idea_id,)).fetchone()
            if (self._has(db, 'idea_manual_runs') and db.execute(
                    'SELECT 1 FROM idea_manual_runs WHERE idea_id=? AND request_id!=? LIMIT 1',
                    (idea_id, request_id)).fetchone()):
                return False
            source, blockers = self._source(db)
            return bool(idea and idea['artwork_sha256'] == row['artwork_sha256'] and not blockers
                        and source == json.loads(row['snapshot'])['source'])
