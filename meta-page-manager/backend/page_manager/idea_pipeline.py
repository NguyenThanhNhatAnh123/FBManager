"""Local idea/agent ledger. No worker launch, network request, upload or publishing.

The explicit readiness flags and verified records are agent attestations: this
module validates bindings, never independently verifies a remote campaign. API
adapters must authenticate the assigned worker before exposing claim/complete.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from .errors import AppError
from .project_paths import IDEA_DIRECTORY,PRODUCER_ROOT


OUTPUT_DIRECTORY = PRODUCER_ROOT
SHOP = 'blueox179.com'
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 4000
MAX_IDEAS = 10000
STAGES = {'upload', 'video'}
METRICS = {'views', 'blue_reels_play_count', 'total_video_views'}


def utcnow():
    return datetime.now(timezone.utc)


def _fail(message='Dữ liệu idea không hợp lệ.', code='invalid_idea', status=422):
    raise AppError(message, code, status)


def _text(value, maximum=200):
    if not isinstance(value, str) or not value or len(value) > maximum or any(
            unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value):
        _fail()
    return value


def _uuid(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        _fail('Mã tác vụ phải là UUID.')


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        _fail('Dấu vân tay nội dung không hợp lệ.')
    return value


def _meta_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,32}', value):
        _fail('ID Meta không hợp lệ.')
    return value


def _instant(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError):
        _fail('Thời điểm phải có múi giờ.')


def _url(value):
    try:
        parsed = urlsplit(_text(value, 512))
        if (parsed.scheme != 'https' or parsed.netloc != SHOP or parsed.query or parsed.fragment
                or not re.fullmatch(r'/campaign/[A-Za-z0-9_-]{1,128}/?', parsed.path)):
            raise ValueError()
    except (ValueError, TypeError):
        _fail('Link campaign phải thuộc đúng shop blueox179.com.')
    return 'https://' + SHOP + parsed.path.rstrip('/')


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _safe_file(path, root):
    if not path.is_relative_to(root) or any(_linked(p) for p in (path, *path.parents)):
        raise OSError()
    value = path.lstat()
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or
            getattr(value, 'st_file_attributes', 0) & 0x400 or not 0 < value.st_size <= MAX_IMAGE_BYTES):
        raise OSError()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]


class IdeaPipeline:
    def __init__(self, manager, *, directory=IDEA_DIRECTORY, output_directory=OUTPUT_DIRECTORY,
                 clock=utcnow, stable_seconds=30):
        if type(stable_seconds) not in (int, float) or not 0 <= stable_seconds <= 86400:
            raise ValueError('Invalid stability interval')
        self.manager, self.store = manager, manager.store
        self.directory, self.output_directory = Path(directory).absolute(), Path(output_directory).absolute()
        self.clock, self.stable_seconds = clock, stable_seconds
        defaults = {'enabled': False, 'upload_ready': False, 'video_ready': False,
                    'campaign_source': None, 'black_campaign_source': None}
        with self.store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS idea_pipeline_settings(
                id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,settings TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS idea_pipeline_candidates(
                path TEXT PRIMARY KEY,signature TEXT NOT NULL,first_seen REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS idea_pipeline_ideas(
                id TEXT PRIMARY KEY,artwork_sha256 TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,
                campaign_state TEXT NOT NULL DEFAULT 'prepared',campaign TEXT,created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS idea_pipeline_variants(
                id TEXT PRIMARY KEY,idea_id TEXT NOT NULL REFERENCES idea_pipeline_ideas(id),
                ordinal INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'planned',video_sha256 TEXT UNIQUE,
                near_duplicate INTEGER NOT NULL DEFAULT 0,provenance TEXT NOT NULL DEFAULT '{}',
                reservation_id TEXT NOT NULL UNIQUE,UNIQUE(idea_id,ordinal));
              CREATE TABLE IF NOT EXISTS idea_production_iterations(
                id TEXT PRIMARY KEY,idea_id TEXT NOT NULL REFERENCES idea_pipeline_ideas(id),
                request_id TEXT NOT NULL UNIQUE,artwork_sha256 TEXT NOT NULL,
                number INTEGER NOT NULL CHECK(number>=2),variant_count INTEGER NOT NULL CHECK(variant_count BETWEEN 1 AND 14),
                created_at TEXT NOT NULL,UNIQUE(idea_id,number));
              CREATE TABLE IF NOT EXISTS idea_production_iteration_variants(
                iteration_id TEXT NOT NULL REFERENCES idea_production_iterations(id),
                ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 14),
                variant_id TEXT NOT NULL UNIQUE REFERENCES idea_pipeline_variants(id),
                PRIMARY KEY(iteration_id,ordinal));
              CREATE TABLE IF NOT EXISTS idea_pipeline_jobs(
                id TEXT PRIMARY KEY,idea_id TEXT NOT NULL REFERENCES idea_pipeline_ideas(id),
                stage TEXT NOT NULL,variant_id TEXT,job_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'queued',worker_id TEXT,fence INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1,lease_until TEXT,result TEXT,created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS idea_pipeline_operations(
                id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,response TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS idea_pipeline_views(
                variant_id TEXT NOT NULL REFERENCES idea_pipeline_variants(id),page_id TEXT NOT NULL,
                video_id TEXT NOT NULL,metric TEXT NOT NULL,value INTEGER,availability TEXT NOT NULL,
                observed_at TEXT NOT NULL,published_at TEXT NOT NULL,metric_verified INTEGER NOT NULL,
                published_time_provenance TEXT NOT NULL DEFAULT 'agent_attested',
                PRIMARY KEY(variant_id,metric,observed_at));
              CREATE INDEX IF NOT EXISTS idea_catalog_order ON idea_pipeline_ideas(created_at,id);
              CREATE INDEX IF NOT EXISTS idea_variants_catalog ON idea_pipeline_variants(idea_id,ordinal);
              CREATE INDEX IF NOT EXISTS idea_catalog_latest_views ON idea_pipeline_views(variant_id,metric,observed_at DESC);
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(idea_pipeline_views)')}
            if 'published_time_provenance' not in columns:
                db.execute("ALTER TABLE idea_pipeline_views ADD COLUMN published_time_provenance TEXT NOT NULL DEFAULT 'agent_attested'")
            db.execute('INSERT OR IGNORE INTO idea_pipeline_settings VALUES(1,1,?)', (_dump(defaults),))

    def _now(self):
        return _instant(self.clock())

    @staticmethod
    def _one(db, table, value):
        # Table names are internal constants, never client input.
        row = db.execute('SELECT * FROM ' + table + ' WHERE id=?', (value,)).fetchone()
        if row is None:
            _fail('Không có mục này.', 'not_found', 404)
        return dict(row)

    def _settings(self, db):
        row = db.execute('SELECT * FROM idea_pipeline_settings WHERE id=1').fetchone()
        values = json.loads(row['settings'])
        values.setdefault('black_campaign_source', None)
        return dict(values, revision=row['revision'])

    def configure(self, *, enabled=False, upload_ready=False, video_ready=False, campaign_source=None,
                  black_campaign_source=None, revision=None):
        if any(type(v) is not bool for v in (enabled, upload_ready, video_ready)):
            _fail()
        if revision is not None and (type(revision) is not int or revision < 1):
            _fail()
        def checked_source(value):
            if value is None: return None
            if (not isinstance(value, dict) or set(value) != {'shop', 'campaign_url', 'verified'}
                    or value['shop'] != SHOP or type(value['verified']) is not bool):
                _fail('Cần nguồn campaign cho đúng shop và trạng thái kiểm chứng rõ ràng.')
            return dict(value, campaign_url=_url(value['campaign_url']))
        campaign_source, black_campaign_source = checked_source(campaign_source), checked_source(black_campaign_source)
        if (campaign_source and black_campaign_source and
                campaign_source['campaign_url'] == black_campaign_source['campaign_url']):
            _fail('Nguồn áo đen phải khác nguồn campaign mặc định.')
        source_verified = bool(campaign_source and campaign_source['verified'] and
                               (black_campaign_source is None or black_campaign_source['verified']))
        if upload_ready and not source_verified or enabled and not (upload_ready and video_ready and source_verified):
            _fail('Chưa đủ kết nối agent và nguồn campaign đã kiểm chứng.', 'pipeline_not_ready', 409)
        values = dict(enabled=enabled, upload_ready=upload_ready, video_ready=video_ready,
                      campaign_source=campaign_source, black_campaign_source=black_campaign_source)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._settings(db)
            if revision is not None and current['revision'] != revision:
                _fail('Cấu hình pipeline đã thay đổi.', 'pipeline_revision_changed', 409)
            same_bindings = all(values[k] == current[k] for k in ('upload_ready', 'video_ready', 'campaign_source', 'black_campaign_source'))
            safe_pause = not enabled and same_bindings
            if any(db.execute("SELECT 1 FROM idea_pipeline_jobs WHERE status IN ('leased','submitting') LIMIT 1")) and not safe_pause and values != {k: current[k] for k in values}:
                _fail('Agent đang giữ tác vụ; chờ kết quả trước khi đổi cấu hình.', 'pipeline_busy', 409)
            db.execute('UPDATE idea_pipeline_settings SET revision=revision+1,settings=? WHERE id=1', (_dump(values),))
        return self.status()

    def _public_idea(self, db, row):
        variants = [self._public_variant(dict(v)) for v in db.execute(
            'SELECT * FROM idea_pipeline_variants WHERE idea_id=? ORDER BY ordinal', (row['id'],))]
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_pipeline_approved_videos'").fetchone():
            for variant in variants:
                proof = db.execute('''SELECT status FROM idea_pipeline_approved_videos
                    WHERE variant_id=? AND video_sha256=? AND reservation_id=?''',
                    (variant['id'],variant['video_sha256'],variant['reservation_id'])).fetchone() if variant['video_sha256'] else None
                if proof and proof['status'] in ('approved','ready'):
                    variant['verification']='worker_qa_approved'
                elif proof and proof['status']=='manual_review_pending':
                    variant['verification']='manual_visual_review_pending'
        allocated = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_manual_runs'").fetchone():
            run = db.execute("SELECT request_id,payload FROM idea_manual_runs WHERE idea_id=? AND status!='cancelled' ORDER BY rowid DESC LIMIT 1", (row['id'],)).fetchone()
            if run:
                try:
                    payload = json.loads(run['payload'])
                    count = payload.get('variant_count')
                    if type(count) is int and 1 <= count <= 14:
                        allocated = count
                        if 'production_iteration' in payload:
                            # Show this run's exact fresh membership with local
                            # ordinals, while historical canonical rows stay intact.
                            by_id = {variant['id']: variant for variant in variants}
                            selected = self.run_variants(row['id'], count,
                                production_iteration=payload['production_iteration'],
                                request_id=run['request_id'], db=db)
                            variants = [by_id[value['id']] | {'ordinal': value['ordinal']} for value in selected]
                        else:
                            variants = [variant for variant in variants if variant['ordinal'] <= count]
                except (ValueError,TypeError,AttributeError):pass
        return {k: row[k] for k in ('id', 'artwork_sha256', 'filename', 'campaign_state', 'created_at')} | {
            'campaign': json.loads(row['campaign']) if row['campaign'] else None, 'variants': variants,
            'allocated_variant_count': allocated}

    @staticmethod
    def _public_variant(row):
        return {k: row[k] for k in ('id', 'idea_id', 'ordinal', 'status', 'video_sha256', 'reservation_id')} | {
            'near_duplicate': bool(row['near_duplicate']), 'provenance': json.loads(row['provenance']), 'verification': 'metadata_only'}

    @staticmethod
    def _public_job(row):
        return {k: row[k] for k in ('id', 'idea_id', 'stage', 'variant_id', 'status', 'worker_id', 'fence', 'revision', 'lease_until', 'created_at')}

    def idea(self, idea_id):
        with self.store.connect() as db:
            return self._public_idea(db, self._one(db, 'idea_pipeline_ideas', _uuid(idea_id)))

    def reserve_variants(self, idea_id, count, db=None):
        """Select a run's immutable ordinal prefix without deleting legacy rows.

        Callers creating a run pass their transaction so reservation growth and
        its frozen payload commit together. Existing UUIDs and unselected rows
        remain available for historical run/replay validation.
        """
        idea_id = _uuid(idea_id)
        if type(count) is not int or not 1 <= count <= 14:
            _fail('Số video mỗi idea phải từ 1 đến 14.', 'invalid_idea_variant_count')
        if db is None:
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.reserve_variants(idea_id, count, db=connection)
        self._one(db, 'idea_pipeline_ideas', idea_id)
        for number in range(1, count + 1):
            variant_id = str(uuid5(UUID(idea_id), 'variant:' + str(number)))
            reservation = str(uuid5(UUID(variant_id), 'reel-reservation'))
            db.execute('''INSERT INTO idea_pipeline_variants(id,idea_id,ordinal,reservation_id)
                VALUES(?,?,?,?) ON CONFLICT(idea_id,ordinal) DO NOTHING''',
                (variant_id, idea_id, number, reservation))
        rows = [self._public_variant(dict(row)) for row in db.execute(
            'SELECT * FROM idea_pipeline_variants WHERE idea_id=? AND ordinal<=? ORDER BY ordinal',
            (idea_id, count))]
        if len(rows) != count or [row['ordinal'] for row in rows] != list(range(1, count + 1)):
            _fail('Bộ video đã lưu cần đối chiếu trước khi chạy.', 'idea_run_requires_review', 409)
        return rows

    def run_variants(self, idea_id, count, *, production_iteration=None, request_id=None, db=None):
        """Read a frozen run's exact variant set, independently of its UI view.

        Historical runs retain their original ordinal prefix. Explicit repeat
        runs use immutable iteration membership and run-local ordinals while
        canonical historical variant rows keep their original global ordinals.
        """
        idea_id = _uuid(idea_id)
        if type(count) is not int or not 1 <= count <= 14:
            _fail('Số video mỗi idea phải từ 1 đến 14.', 'invalid_idea_variant_count')
        if db is None:
            with self.store.connect() as connection:
                return self.run_variants(idea_id, count, production_iteration=production_iteration,
                                         request_id=request_id, db=connection)
        idea = self._one(db, 'idea_pipeline_ideas', idea_id)
        if production_iteration is None:
            rows = [self._public_variant(dict(row)) for row in db.execute(
                'SELECT * FROM idea_pipeline_variants WHERE idea_id=? AND ordinal<=? ORDER BY ordinal', (idea_id, count))]
        else:
            if (not isinstance(production_iteration, dict) or set(production_iteration) != {'id', 'number'}
                    or type(production_iteration.get('number')) is not int or not 2 <= production_iteration['number'] <= 2**63 - 1
                    or not isinstance(production_iteration.get('id'), str)
                    or _uuid(production_iteration['id']) != production_iteration['id']
                    or not isinstance(request_id, str) or _uuid(request_id) != request_id):
                _fail('Lượt sản xuất lại chưa khớp mã đã giữ.', 'idea_iteration_changed', 409)
            iteration = db.execute('SELECT * FROM idea_production_iterations WHERE id=?', (production_iteration['id'],)).fetchone()
            if (iteration is None or iteration['idea_id'] != idea_id or iteration['request_id'] != request_id
                    or iteration['artwork_sha256'] != idea['artwork_sha256'] or iteration['number'] != production_iteration['number']
                    or iteration['variant_count'] != count):
                _fail('Lượt sản xuất lại chưa khớp mã đã giữ.', 'idea_iteration_changed', 409)
            selected = db.execute('''SELECT v.*,m.ordinal AS run_ordinal FROM idea_production_iteration_variants m
                LEFT JOIN idea_pipeline_variants v ON v.id=m.variant_id
                WHERE m.iteration_id=? ORDER BY m.ordinal LIMIT 15''', (iteration['id'],)).fetchall()
            if any(row['idea_id'] != idea_id for row in selected):
                _fail('Variant không thuộc đúng idea.', 'idea_iteration_changed', 409)
            rows = [self._public_variant(dict(row)) | {'ordinal': row['run_ordinal']} for row in selected]
        if len(rows) != count or [row['ordinal'] for row in rows] != list(range(1, count + 1)):
            _fail('Danh sách variant đã thay đổi.', 'idea_iteration_changed', 409)
        return rows

    def reserve_iteration(self, idea_id, request_id, count, expected_source_sha256, *, db=None):
        """Append a user-requested fresh iteration without rewriting any history.

        IdeaRuns supplies its writer transaction, so iteration metadata, fresh
        reservations and the queued run either all commit or all roll back.
        This method does not enqueue, launch, register media or alter campaigns.
        """
        idea_id, request_id = _uuid(idea_id), _uuid(request_id)
        expected_source_sha256 = _sha(expected_source_sha256)
        if type(count) is not int or not 1 <= count <= 14:
            _fail('Số video mỗi idea phải từ 1 đến 14.', 'invalid_idea_variant_count')
        if db is None:
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.reserve_iteration(idea_id, request_id, count, expected_source_sha256, db=connection)
        idea = self._one(db, 'idea_pipeline_ideas', idea_id)
        if idea['artwork_sha256'] != expected_source_sha256:
            _fail('Ảnh nguồn không còn khớp bản được yêu cầu.', 'idea_iteration_source_changed', 409)
        previous = db.execute('SELECT * FROM idea_production_iterations WHERE request_id=?', (request_id,)).fetchone()
        if previous:
            if (previous['idea_id'] != idea_id or previous['variant_count'] != count
                    or previous['artwork_sha256'] != expected_source_sha256):
                _fail('Mã yêu cầu đã dùng cho lượt sản xuất khác.', 'idea_run_conflict', 409)
            metadata = {'id': previous['id'], 'number': previous['number']}
            return {'production_iteration': metadata, 'variants': self.run_variants(
                idea_id, count, production_iteration=metadata, request_id=request_id, db=db)}
        if (idea['campaign_state'] != 'verified'
                or not db.execute('SELECT 1 FROM idea_pipeline_variants WHERE idea_id=? AND video_sha256 IS NOT NULL LIMIT 1', (idea_id,)).fetchone()
                or db.execute("SELECT 1 FROM idea_pipeline_variants WHERE idea_id=? AND (status='needs_review' OR (status!='planned' AND video_sha256 IS NULL)) LIMIT 1", (idea_id,)).fetchone()):
            _fail('Chỉ tạo lượt mới cho idea đã có kết quả hoàn tất và không còn bước cần đối chiếu.', 'idea_iteration_requires_review', 409)
        if db.execute("SELECT 1 FROM idea_pipeline_jobs WHERE idea_id=? AND status NOT IN ('completed','cancelled') LIMIT 1", (idea_id,)).fetchone():
            _fail('Idea còn công việc chưa hoàn tất.', 'idea_iteration_requires_review', 409)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_manual_runs'").fetchone():
            if db.execute("SELECT 1 FROM idea_manual_runs WHERE idea_id=? AND status!='completed' LIMIT 1", (idea_id,)).fetchone():
                _fail('Lượt cũ chưa hoàn tất; đối chiếu trước khi sản xuất lại.', 'idea_iteration_requires_review', 409)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_auto_batch_items'").fetchone():
            if db.execute("SELECT 1 FROM idea_auto_batch_items WHERE idea_id=? AND status!='completed' LIMIT 1", (idea_id,)).fetchone():
                _fail('Idea còn thuộc một batch chưa hoàn tất.', 'idea_iteration_requires_review', 409)
        number = db.execute('SELECT COALESCE(MAX(number),1)+1 FROM idea_production_iterations WHERE idea_id=?', (idea_id,)).fetchone()[0]
        start = db.execute('SELECT COALESCE(MAX(ordinal),0) FROM idea_pipeline_variants WHERE idea_id=?', (idea_id,)).fetchone()[0]
        iteration_id = str(uuid4())
        db.execute('INSERT INTO idea_production_iterations VALUES(?,?,?,?,?,?,?)',
                   (iteration_id, idea_id, request_id, expected_source_sha256, number, count, self._now().isoformat()))
        for ordinal in range(1, count + 1):
            variant_id = str(uuid5(UUID(iteration_id), 'variant:' + str(ordinal)))
            reservation_id = str(uuid5(UUID(variant_id), 'reel-reservation'))
            db.execute('INSERT INTO idea_pipeline_variants(id,idea_id,ordinal,reservation_id) VALUES(?,?,?,?)',
                       (variant_id, idea_id, start + ordinal, reservation_id))
            db.execute('INSERT INTO idea_production_iteration_variants VALUES(?,?,?)', (iteration_id, ordinal, variant_id))
        metadata = {'id': iteration_id, 'number': number}
        return {'production_iteration': metadata, 'variants': self.run_variants(
            idea_id, count, production_iteration=metadata, request_id=request_id, db=db)}

    def settings(self):
        from .idea_catalog import settings
        return settings(self)

    def summary(self):
        from .idea_catalog import summary
        return summary(self)

    def catalog(self, limit=12, offset=0, search='', status='all', sort='warehouse', calendar='', timezone='America/Los_Angeles', *, clamp_offset=True):
        from .idea_catalog import catalog
        return catalog(self,limit,offset,search,status,sort,calendar,timezone,clamp_offset=clamp_offset)

    def ranking_page(self, limit=10, offset=0, *, clamp_offset=True):
        from .idea_catalog import ranking
        return ranking(self,limit,offset,clamp_offset=clamp_offset)

    def status(self):
        with self.store.connect() as db:
            settings = self._settings(db)
            rows = [dict(r) for r in db.execute('SELECT * FROM idea_pipeline_ideas ORDER BY created_at,id')]
            jobs = [self._public_job(dict(r)) for r in db.execute('SELECT * FROM idea_pipeline_jobs ORDER BY created_at DESC LIMIT 100')]
            count = db.execute('SELECT COUNT(*) FROM idea_pipeline_ideas').fetchone()[0]
            return {'settings': settings, 'directory': str(self.directory), 'output_directory': str(self.output_directory),
                    'shop': SHOP, 'ready': bool(settings['enabled'] and settings['upload_ready'] and settings['video_ready']
                        and settings['campaign_source'] and settings['campaign_source']['verified']
                        and (settings['black_campaign_source'] is None or settings['black_campaign_source']['verified'])),
                    'idea_count': count, 'ideas': [self._public_idea(db, r) for r in rows], 'jobs': jobs,
                    'policy': {'variants_per_idea': None, 'legacy_default_variant_count': 7,
                               'variant_count_mode': 'allocated_per_run', 'variant_count_min': 1, 'variant_count_max': 14,
                               'trigger': 'authorized_run', 'allowed_trigger_modes': ['manual_web_run_idea','automatic_idea_batch'],
                               'caption_mode': 'product_link_only',
                               'proven_allocation': 0.7, 'exploration_allocation': 0.3,
                               'allocation_active': False, 'ranking_age_hours': 24, 'near_duplicate_detection': 'agent_review_required'}}

    def scan(self):
        """Discover stable images; only creates local ideas and planned variants."""
        root = self.directory
        found, warnings, entries, directories = [], 0, 0, 0
        if any(_linked(p) for p in (root, *root.parents)) or not root.is_dir():
            return {'registered': 0, 'waiting': 0, 'skipped': 0, 'warning': 'Thư mục idea chưa sẵn sàng.'}
        pending = [(root, 0)]
        while pending and entries < MAX_ENTRIES and directories < 128:
            folder, depth = pending.pop(); directories += 1
            try:
                with os.scandir(folder) as listing:
                    for entry in listing:
                        entries += 1
                        if entries > MAX_ENTRIES:
                            break
                        path = Path(entry.path)
                        if _linked(path):
                            warnings += 1; continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth < 6: pending.append((path, depth + 1))
                        elif path.suffix.lower() in EXTENSIONS:
                            try: found.append((path, _safe_file(path, root)))
                            except OSError: warnings += 1
            except OSError: warnings += 1
        registered = waiting = skipped = 0
        for path, signature in sorted(found, key=lambda x: str(x[0]).casefold()):
            stamp = self._now()
            encoded = _dump(signature)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT * FROM idea_pipeline_candidates WHERE path=?', (str(path),)).fetchone()
                if old is None or old['signature'] != encoded:
                    db.execute('INSERT INTO idea_pipeline_candidates VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET signature=excluded.signature,first_seen=excluded.first_seen', (str(path), encoded, stamp.timestamp()))
                    waiting += 1; continue
                if stamp.timestamp() - old['first_seen'] < self.stable_seconds:
                    waiting += 1; continue
            try:
                if _safe_file(path, root) != signature: raise OSError()
                digest = hashlib.sha256()
                with path.open('rb') as handle:
                    opened = os.fstat(handle.fileno())
                    if [opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns] != signature:
                        raise OSError()
                    header = handle.read(32)
                    suffix = path.suffix.lower()
                    if not (suffix == '.png' and header.startswith(b'\x89PNG\r\n\x1a\n')
                            or suffix in {'.jpg', '.jpeg'} and header.startswith(b'\xff\xd8\xff')
                            or suffix == '.webp' and header[:4] == b'RIFF' and header[8:12] == b'WEBP'):
                        warnings += 1; skipped += 1; continue
                    digest.update(header)
                    while chunk := handle.read(1024 * 1024): digest.update(chunk)
                    closed = os.fstat(handle.fileno())
                    if [closed.st_dev, closed.st_ino, closed.st_size, closed.st_mtime_ns] != signature:
                        raise OSError()
                if _safe_file(path, root) != signature: raise OSError()
            except OSError:
                waiting += 1; continue
            sha = digest.hexdigest(); idea_id = str(uuid5(NAMESPACE_URL, 'via-idea:' + sha))
            relative = path.relative_to(root).as_posix()
            # Keep filenames as display data only; never infer a product URL.
            relative = ''.join(c for c in relative if unicodedata.category(c) not in ('Cc', 'Cf', 'Cs'))[:500]
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM idea_pipeline_ideas WHERE artwork_sha256=?', (sha,)).fetchone():
                    skipped += 1; continue
                if db.execute('SELECT COUNT(*) FROM idea_pipeline_ideas').fetchone()[0] >= MAX_IDEAS:
                    warnings += 1; continue
                db.execute('INSERT INTO idea_pipeline_ideas(id,artwork_sha256,filename,created_at) VALUES(?,?,?,?)', (idea_id, sha, relative, stamp.isoformat()))
                for number in range(1, 8):
                    variant_id = str(uuid5(UUID(idea_id), 'variant:' + str(number)))
                    reservation = str(uuid5(UUID(variant_id), 'reel-reservation'))
                    db.execute('INSERT INTO idea_pipeline_variants(id,idea_id,ordinal,reservation_id) VALUES(?,?,?,?)', (variant_id, idea_id, number, reservation))
                registered += 1
        with self.store.connect() as db:
            db.execute('DELETE FROM idea_pipeline_candidates WHERE path IN (SELECT path FROM idea_pipeline_candidates ORDER BY first_seen DESC LIMIT -1 OFFSET 8000)')
        return {'registered': registered, 'waiting': waiting, 'skipped': skipped,
                'warning': 'Một số tệp không an toàn hoặc vượt giới hạn đã bỏ qua.' if warnings or entries >= MAX_ENTRIES or pending else None}

    def _operation(self, db, request_id, kind, payload, action):
        key = _uuid(request_id)
        digest = hashlib.sha256(_dump([kind, payload]).encode()).hexdigest()
        row = db.execute('SELECT * FROM idea_pipeline_operations WHERE id=?', (key,)).fetchone()
        if row:
            if row['payload_hash'] != digest:
                _fail('Mã tác vụ đã dùng cho dữ liệu khác.', 'idea_operation_conflict', 409)
            return json.loads(row['response'])
        response = action()
        db.execute('INSERT INTO idea_pipeline_operations VALUES(?,?,?)', (key, digest, _dump(response)))
        return response

    def _ready(self, db, stage):
        settings = self._settings(db)
        if (not settings['enabled'] or not settings[stage + '_ready'] or not settings['campaign_source']
                or not settings['campaign_source']['verified']
                or settings['black_campaign_source'] is not None and not settings['black_campaign_source']['verified']):
            _fail('Tích hợp agent chưa sẵn sàng.', 'pipeline_not_ready', 409)
        return settings

    def queue_stage(self, idea_id, stage, *, variant_id=None, request_id):
        idea_id = _uuid(idea_id)
        if stage not in STAGES or stage == 'video' and variant_id is None or stage == 'upload' and variant_id is not None:
            _fail()
        variant_id = _uuid(variant_id) if variant_id is not None else None
        payload = dict(idea_id=idea_id, stage=stage, variant_id=variant_id)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            def action():
                self._ready(db, stage)
                idea = self._one(db, 'idea_pipeline_ideas', idea_id)
                if stage == 'upload' and idea['campaign_state'] != 'prepared':
                    _fail('Campaign đã gửi hoặc chưa rõ kết quả; cần đối chiếu trước.', 'campaign_requires_review', 409)
                if stage == 'video':
                    variant = self._one(db, 'idea_pipeline_variants', variant_id)
                    if variant['idea_id'] != idea_id or variant['status'] != 'planned' or idea['campaign_state'] != 'verified':
                        _fail('Variant hoặc link campaign chưa sẵn sàng.', 'variant_not_ready', 409)
                key = idea_id + ':' + stage + ':' + (variant_id or '')
                old = db.execute('SELECT * FROM idea_pipeline_jobs WHERE job_key=?', (key,)).fetchone()
                if old: return self._public_job(dict(old))
                job_id = str(uuid4())
                db.execute('INSERT INTO idea_pipeline_jobs(id,idea_id,stage,variant_id,job_key,created_at) VALUES(?,?,?,?,?,?)', (job_id, idea_id, stage, variant_id, key, self._now().isoformat()))
                return self._public_job(self._one(db, 'idea_pipeline_jobs', job_id))
            return self._operation(db, request_id, 'queue', payload, action)

    def claim(self, stage, worker_id, *, request_id, lease_seconds=300):
        if stage not in STAGES or type(lease_seconds) is not int or not 30 <= lease_seconds <= 3600:
            _fail()
        worker_id = _text(worker_id, 100)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            def action():
                self._ready(db, stage)
                now = self._now()
                expired = db.execute("SELECT id,idea_id,stage FROM idea_pipeline_jobs WHERE status='submitting' AND julianday(lease_until)<=julianday(?)", (now.isoformat(),)).fetchall()
                for row in expired:
                    db.execute("UPDATE idea_pipeline_jobs SET status='uncertain',revision=revision+1 WHERE id=?", (row['id'],))
                    if row['stage'] == 'upload': db.execute("UPDATE idea_pipeline_ideas SET campaign_state='uncertain' WHERE id=?", (row['idea_id'],))
                row = db.execute("SELECT * FROM idea_pipeline_jobs WHERE stage=? AND (status='queued' OR (status='leased' AND julianday(lease_until)<=julianday(?))) ORDER BY created_at,id LIMIT 1", (stage, now.isoformat())).fetchone()
                if row is None: return {'job': None}
                lease = (now + timedelta(seconds=lease_seconds)).isoformat()
                db.execute("UPDATE idea_pipeline_jobs SET status='leased',worker_id=?,fence=fence+1,revision=revision+1,lease_until=? WHERE id=?", (worker_id, lease, row['id']))
                return {'job': self._public_job(self._one(db, 'idea_pipeline_jobs', row['id']))}
            return self._operation(db, request_id, 'claim', dict(stage=stage, worker_id=worker_id, lease_seconds=lease_seconds), action)

    def complete(self, job_id, worker_id, fence, revision, outcome, result, *, request_id):
        job_id = _uuid(job_id); worker_id = _text(worker_id, 100)
        if type(fence) is not int or type(revision) is not int or fence < 1 or revision < 1 or outcome not in {'submitting', 'uncertain', 'verified', 'failed'} or not isinstance(result, dict):
            _fail()
        payload = dict(job_id=job_id, worker_id=worker_id, fence=fence, revision=revision, outcome=outcome, result=result)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            def action():
                row = self._one(db, 'idea_pipeline_jobs', job_id)
                if row['worker_id'] != worker_id or row['fence'] != fence or row['revision'] != revision:
                    _fail('Lease hoặc phiên bản tác vụ đã thay đổi.', 'stale_idea_lease', 409)
                expired = _instant(row['lease_until']) <= self._now()
                reconcile = (outcome == 'verified' and result.get('reconciled') is True and
                             (row['status'] == 'uncertain' or row['status'] == 'submitting' and expired))
                late_uncertain = outcome == 'uncertain' and row['status'] == 'submitting'
                if row['status'] not in {'leased', 'submitting', 'uncertain'} or expired and not (reconcile or late_uncertain):
                    _fail('Lease đã hết; cần đối chiếu tác vụ chưa rõ kết quả.', 'stale_idea_lease', 409)
                if row['status'] == 'uncertain' and not reconcile or outcome == 'failed' and row['status'] != 'leased':
                    _fail('Không gửi lại tác vụ chưa rõ kết quả.', 'idea_requires_review', 409)
                if outcome == 'verified' and row['status'] not in {'submitting', 'uncertain'}:
                    _fail('Phải ghi submitting trước thao tác bên ngoài.', 'idea_requires_checkpoint', 409)
                if outcome == 'verified':
                    if row['stage'] == 'upload': self._campaign_complete(db, row, result)
                    else: self._video_complete(db, row, result)
                elif result:
                    _fail('Checkpoint không nhận dữ liệu tùy ý.')
                if row['stage'] == 'upload' and outcome in {'submitting', 'uncertain'}:
                    db.execute('UPDATE idea_pipeline_ideas SET campaign_state=? WHERE id=?', (outcome, row['idea_id']))
                db.execute('UPDATE idea_pipeline_jobs SET status=?,revision=revision+1,result=? WHERE id=?', (outcome, _dump(result) if outcome == 'verified' else None, job_id))
                return self._public_job(self._one(db, 'idea_pipeline_jobs', job_id))
            return self._operation(db, request_id, 'complete', payload, action)

    def _campaign_complete(self, db, job, result):
        fields = {'idea_id', 'artwork_sha256', 'shop', 'campaign_url', 'campaign_source', 'evidence'}
        if (not fields.issubset(result) or set(result) - fields - {'reconciled', 'shirt_color'}
                or result['idea_id'] != job['idea_id'] or result['shop'] != SHOP):
            _fail('Campaign không khớp idea hoặc shop.')
        idea = self._one(db, 'idea_pipeline_ideas', job['idea_id'])
        if _sha(result['artwork_sha256']) != idea['artwork_sha256']:
            _fail('Campaign không khớp artwork.')
        url, source = _url(result['campaign_url']), _url(result['campaign_source'])
        settings = self._settings(db)
        black = settings['black_campaign_source']
        is_black_source = bool(black and black['verified'] and black['campaign_url'] == source)
        if is_black_source:
            if result.get('shirt_color') != 'black':
                _fail('Campaign nguồn áo đen phải được gắn đúng màu black.')
        elif (not settings['campaign_source'] or not settings['campaign_source']['verified']
              or settings['campaign_source']['campaign_url'] != source or 'shirt_color' in result):
            _fail('Campaign nguồn hoặc màu áo không khớp cấu hình.')
        evidence = result['evidence']
        if (not isinstance(evidence, dict) or set(evidence) != {'observed_at', 'artwork_matches', 'quote_matches', 'public_url'}
                or evidence['artwork_matches'] is not True or evidence['quote_matches'] is not True or _url(evidence['public_url']) != url
                or _instant(evidence['observed_at']) > self._now() + timedelta(minutes=5)):
            _fail('Chưa có bằng chứng kiểm chứng campaign.')
        binding = dict(shop=SHOP, campaign_url=url, campaign_source=source, evidence=evidence, attested_by=job['worker_id'])
        if is_black_source: binding['shirt_color'] = 'black'
        db.execute("UPDATE idea_pipeline_ideas SET campaign_state='verified',campaign=? WHERE id=?", (_dump(binding), idea['id']))

    def _register_video(self, db, idea_id, variant_id, artwork_sha256, video_sha256, near_duplicate, provenance):
        idea = self._one(db, 'idea_pipeline_ideas', _uuid(idea_id))
        variant = self._one(db, 'idea_pipeline_variants', _uuid(variant_id))
        if variant['idea_id'] != idea['id'] or _sha(artwork_sha256) != idea['artwork_sha256'] or idea['campaign_state'] != 'verified':
            _fail('Video không khớp idea hoặc chưa có link áo kiểm chứng.')
        sha = _sha(video_sha256)
        if type(near_duplicate) is not bool or not isinstance(provenance, dict) or not provenance or set(provenance) - {'skill', 'run_id', 'concept_id'}:
            _fail('Cần nguồn gốc video và kết quả kiểm tra trùng.')
        provenance = {k: _text(v, 200) for k, v in provenance.items()}
        old = db.execute('SELECT id FROM idea_pipeline_variants WHERE video_sha256=?', (sha,)).fetchone()
        if old and old['id'] != variant_id:
            _fail('Video đã thuộc một variant khác; không dùng lại.', 'duplicate_idea_video', 409)
        if variant['video_sha256'] is not None:
            if (variant['video_sha256'] != sha or bool(variant['near_duplicate']) != near_duplicate or json.loads(variant['provenance']) != provenance):
                _fail('Không thay video đã giữ chỗ.', 'idea_video_conflict', 409)
            return self._public_variant(variant)
        if db.execute('SELECT 1 FROM reel_jobs j JOIN reel_assets a ON a.id=j.asset_id WHERE a.sha256=? LIMIT 1', (sha,)).fetchone():
            _fail('Video đã có trong lịch sử Reel.', 'duplicate_idea_video', 409)
        db.execute('UPDATE idea_pipeline_variants SET video_sha256=?,near_duplicate=?,provenance=?,status=? WHERE id=?',
                   (sha, int(near_duplicate), _dump(provenance), 'needs_review' if near_duplicate else 'metadata_registered', variant_id))
        return self._public_variant(self._one(db, 'idea_pipeline_variants', variant_id))

    def register_video(self, idea_id, variant_id, artwork_sha256, video_sha256, *, near_duplicate=False, provenance=None):
        """Reserve metadata only; accepts no path/bytes and creates no Reel job."""
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._register_video(db, idea_id, variant_id, artwork_sha256, video_sha256, near_duplicate, provenance)

    def _video_complete(self, db, job, result):
        fields = {'idea_id', 'variant_id', 'artwork_sha256', 'video_sha256', 'near_duplicate', 'provenance'}
        if set(result) not in (fields, fields | {'reconciled'}) or result['idea_id'] != job['idea_id'] or result['variant_id'] != job['variant_id']:
            _fail('Kết quả video không khớp tác vụ.')
        self._register_video(db, **{k: result[k] for k in fields})

    def record_views(self, variant_id, page_id, video_id, metric, value, observed_at, published_at,
                     *, availability='available', metric_verified=False, published_time_provenance='agent_attested'):
        variant_id, page_id, video_id = _uuid(variant_id), _meta_id(page_id), _meta_id(video_id)
        if metric not in METRICS or availability not in {'available', 'unavailable'} or type(metric_verified) is not bool:
            _fail('Chỉ nhận metric lượt xem video có tên rõ ràng.')
        if published_time_provenance not in {'agent_attested', 'provider_published_at', 'job_completion'}:
            _fail('Cần nguồn thời điểm đăng rõ ràng.')
        if availability == 'available' and (type(value) is not int or not 0 <= value <= 10**15) or availability == 'unavailable' and value is not None:
            _fail('Thiếu dữ liệu khác với 0 lượt xem.')
        observed, published = _instant(observed_at), _instant(published_at)
        if observed < published or observed > self._now() + timedelta(minutes=5): _fail()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            variant = self._one(db, 'idea_pipeline_variants', variant_id)
            if variant['status'] != 'metadata_registered': _fail('Video chưa có metadata hợp lệ để theo dõi.')
            old_bindings = db.execute('SELECT page_id,video_id,published_at,published_time_provenance FROM idea_pipeline_views WHERE variant_id=?', (variant_id,)).fetchall()
            for old in old_bindings:
                if (old['page_id'], old['video_id']) != (page_id, video_id):
                    _fail('Không thay Page/video của snapshot.', 'idea_metric_binding_conflict', 409)
                if (published_time_provenance != 'job_completion' and old['published_time_provenance'] != 'job_completion'
                        and old['published_at'] != published.isoformat()):
                    _fail('Không thay thời điểm đăng đã kiểm chứng.', 'idea_metric_binding_conflict', 409)
            values = (variant_id, page_id, video_id, metric, value, availability, observed.isoformat(), published.isoformat(), int(metric_verified), published_time_provenance)
            existing = db.execute('SELECT * FROM idea_pipeline_views WHERE variant_id=? AND metric=? AND observed_at=?', (variant_id, metric, observed.isoformat())).fetchone()
            if existing:
                if tuple(existing) != values: _fail('Snapshot đã lưu khác dữ liệu.', 'idea_metric_conflict', 409)
            else:
                db.execute('INSERT INTO idea_pipeline_views VALUES(?,?,?,?,?,?,?,?,?,?)', values)
        return {'variant_id': variant_id, 'page_id': page_id, 'video_id': video_id, 'metric': metric, 'value': value,
                'availability': availability, 'metric_verified': metric_verified, 'observed_at': observed.isoformat(),
                'published_at': published.isoformat(), 'published_time_provenance': published_time_provenance}

    def ranking(self):
        """Preview only: median verified views near 24h, never schedules work.

        Metrics with different definitions are separate cohorts. Page-relative
        lift is shown only with at least three other variants on that Page.
        """
        import statistics
        with self.store.connect() as db:
            ideas = [dict(r) for r in db.execute('SELECT id,filename FROM idea_pipeline_ideas ORDER BY created_at,id')]
            rows = [dict(r) for r in db.execute("SELECT s.*,v.idea_id FROM idea_pipeline_views s JOIN idea_pipeline_variants v ON v.id=s.variant_id WHERE s.availability='available' AND s.metric_verified=1 AND s.published_time_provenance!='job_completion'")]
        closest = {}
        for row in rows:
            age = (_instant(row['observed_at']) - _instant(row['published_at'])).total_seconds() / 3600
            if not 22 <= age <= 26: continue
            key = (row['variant_id'], row['metric'])
            distance = abs(age - 24)
            if key not in closest or distance < closest[key][0]: closest[key] = (distance, row)
        samples = [entry[1] for entry in closest.values()]
        cohorts = []
        for metric in sorted({r['metric'] for r in samples}):
            relevant = [r for r in samples if r['metric'] == metric]
            entries = []
            for idea in ideas:
                own = [r for r in relevant if r['idea_id'] == idea['id']]
                lifts = []
                for sample in own:
                    others = [r['value'] for r in relevant if r['page_id'] == sample['page_id'] and r['idea_id'] != idea['id']]
                    if len(others) >= 3:
                        lifts.append((sample['value'] + 10) / (statistics.median(others) + 10))
                entries.append({'idea_id': idea['id'], 'filename': idea['filename'], 'sample_count': len(own),
                                'median_views_24h': statistics.median([r['value'] for r in own]) if own else None,
                                'median_page_lift': statistics.median(lifts) if lifts else None,
                                'confidence': 'insufficient' if len(own) < 3 else 'observed',
                                'allocation_group': 'exploration' if len(own) < 3 else 'proven'})
            entries.sort(key=lambda r: (r['median_views_24h'] is None, -(r['median_views_24h'] or 0), r['idea_id']))
            cohorts.append({'metric': metric, 'items': entries})
        measured = {r['idea_id'] for r in samples}
        return {'mode': 'preview_only', 'age_window_hours': [22, 26], 'target_age_hours': 24,
                'method': 'median_views_per_idea_same_metric_near_24h', 'cohorts': cohorts,
                'unmeasured': [r for r in ideas if r['id'] not in measured],
                'proposed_allocation': {'proven': 0.7, 'exploration': 0.3, 'active': False},
                'limitations': ['Missing views are unknown, never zero.', 'Different metric definitions are never combined.',
                                'Job completion time is not publication time and is excluded from age-based ranking.',
                                'Page lift needs three other variants; paid/organic mix is not inferred.',
                                'No automatic priority or queue changes are performed.']}
