"""Explicit manual cohorts: one immutable audio plan, canonical per-idea runs.

This module only records/enqueues authorized work. IdeaRuns retains every worker,
source, uploader, workflow, consumer and publication gate. No worker is launched
here, and no variant/reservation identifiers are invented here.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .auto_batch_planner import allocate_counts
from .errors import AppError
from .idea_pipeline import EXTENSIONS, _safe_file


ACTIVE = ('queued', 'preparing', 'running', 'importing')
LEASE_SECONDS = 120


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _fail(code, status=409):
    raise AppError('Đợt sản xuất thủ công cần kiểm tra trước khi tiếp tục.', code, status)


class ManualProductionBatch:
    def __init__(self, store, pipeline, runs, audio_approvals, *, clock=None):
        self.store, self.pipeline, self.runs, self.audio = store, pipeline, runs, audio_approvals
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS idea_manual_production_batches(
                  id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,input_json TEXT NOT NULL,
                  snapshot TEXT NOT NULL,snapshot_sha256 TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('prepared','running','needs_review','complete')),
                  lease_owner TEXT,lease_until TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS idea_manual_production_batch_items(
                  batch_id TEXT NOT NULL REFERENCES idea_manual_production_batches(id),
                  position INTEGER NOT NULL,idea_id TEXT NOT NULL,request_id TEXT NOT NULL UNIQUE,
                  status TEXT NOT NULL CHECK(status IN ('pending','queued','completed','needs_review')),
                  run_id TEXT,error_code TEXT,PRIMARY KEY(batch_id,position),UNIQUE(batch_id,idea_id));
            ''')

    def _now(self):
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Timezone-aware clock required')
        return now.astimezone(timezone.utc)

    @staticmethod
    def _has(db, table):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    @staticmethod
    def _input(request_id, items, page_count):
        if (not _uuid(request_id) or type(page_count) is not int or page_count != 14
                or not isinstance(items, list) or not 1 <= len(items) <= 14):
            _fail('invalid_manual_production_batch', 422)
        clean = []
        for item in items:
            if (not isinstance(item, dict) or set(item) != {'idea_id', 'expected_source_sha256', 'repeat'}
                    or not _uuid(item['idea_id']) or not _sha(item['expected_source_sha256'])
                    or type(item['repeat']) is not bool):
                _fail('invalid_manual_production_batch', 422)
            clean.append(dict(item))
        if len({v['idea_id'] for v in clean}) != len(clean):
            _fail('invalid_manual_production_batch', 422)
        return {'page_count': page_count, 'items': clean}

    def _source(self, db):
        if self._has(db, 'idea_auto_batch_settings'):
            auto = db.execute('SELECT enabled FROM idea_auto_batch_settings WHERE id=1').fetchone()
            if auto is None or auto['enabled'] != 0:
                _fail('manual_batch_auto_enabled')
        if not self._has(db, 'video_source_settings'):
            _fail('idea_schedule_not_ready')
        row = db.execute('SELECT revision,configured,settings FROM video_source_settings WHERE id=1').fetchone()
        try:
            values = json.loads(row['settings'])
            ids = values['page_ids']
            if (row['configured'] != 1 or type(row['revision']) is not int or row['revision'] < 1
                    or values.get('enabled') is not True or not isinstance(ids, list) or len(ids) != 14
                    or any(not isinstance(pid, str) or re.fullmatch('[1-9][0-9]{0,31}', pid) is None for pid in ids)
                    or len(set(ids)) != 14 or type(values.get('connection_id')) is not int or values['connection_id'] < 1
                    or values.get('daily_time') != '06:00' or values.get('timezone') != 'America/Los_Angeles'):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            _fail('idea_schedule_not_ready')
        if not all(self.store.posting_eligibility(pid, db=db)['can_publish'] is True for pid in ids):
            _fail('page_information_required')
        return {'revision': row['revision'], 'settings_sha256': hashlib.sha256(_json(values).encode()).hexdigest()}

    def _verify_source(self, item):
        idea = self.pipeline.idea(item['idea_id'])
        if idea['artwork_sha256'] != item['expected_source_sha256']:
            _fail('manual_batch_source_changed')
        filename = idea['filename']
        if (not isinstance(filename, str) or not filename or ':' in filename
                or any(p in ('', '..') for p in filename.replace('\\', '/').split('/'))):
            _fail('manual_batch_source_changed')
        path = self.pipeline.directory / filename
        try:
            if path.suffix.lower() not in EXTENSIONS:
                raise OSError()
            before = _safe_file(path, self.pipeline.directory)
            digest = hashlib.sha256()
            with path.open('rb') as source:
                opened = os.fstat(source.fileno())
                if [opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns] != before:
                    raise OSError()
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                closed = os.fstat(source.fileno())
                if [closed.st_dev, closed.st_ino, closed.st_size, closed.st_mtime_ns] != before:
                    raise OSError()
            if _safe_file(path, self.pipeline.directory) != before or digest.hexdigest() != item['expected_source_sha256']:
                raise OSError()
        except OSError:
            _fail('manual_batch_source_changed')

    def _validate_candidates(self, db, items):
        for item in items:
            idea_id = item['idea_id']
            if item['repeat']:
                idea = db.execute('SELECT campaign_state FROM idea_pipeline_ideas WHERE id=?', (idea_id,)).fetchone()
                if (not idea or idea['campaign_state'] != 'verified'
                        or not db.execute('SELECT 1 FROM idea_pipeline_variants WHERE idea_id=? AND video_sha256 IS NOT NULL LIMIT 1', (idea_id,)).fetchone()
                        or db.execute("SELECT 1 FROM idea_pipeline_variants WHERE idea_id=? AND (status='needs_review' OR (status!='planned' AND video_sha256 IS NULL)) LIMIT 1", (idea_id,)).fetchone()
                        or db.execute("SELECT 1 FROM idea_pipeline_jobs WHERE idea_id=? AND status NOT IN ('completed','cancelled') LIMIT 1", (idea_id,)).fetchone()
                        or db.execute("SELECT 1 FROM idea_manual_runs WHERE idea_id=? AND status!='completed' LIMIT 1", (idea_id,)).fetchone()
                        or self._has(db, 'idea_auto_batch_items') and db.execute("SELECT 1 FROM idea_auto_batch_items WHERE idea_id=? AND status!='completed' LIMIT 1", (idea_id,)).fetchone()):
                    _fail('idea_iteration_requires_review')
            elif (db.execute("SELECT 1 FROM idea_manual_runs WHERE idea_id=? AND status!='cancelled' LIMIT 1", (idea_id,)).fetchone()
                    or db.execute("SELECT 1 FROM idea_pipeline_variants WHERE idea_id=? AND (status!='planned' OR video_sha256 IS NOT NULL) LIMIT 1", (idea_id,)).fetchone()):
                _fail('idea_run_requires_review')

    def _ready(self):
        if not callable(getattr(self.runs, 'on_result', None)):
            _fail('consumer_unavailable')
        if self.runs.readiness().get('ready') is not True:
            _fail('idea_run_not_ready')

    @staticmethod
    def _snapshot(row):
        if hashlib.sha256(row['snapshot'].encode()).hexdigest() != row['snapshot_sha256']:
            _fail('manual_batch_changed')
        try:
            return json.loads(row['snapshot'])
        except (ValueError, TypeError):
            _fail('manual_batch_changed')

    def _reconcile(self, db, row):
        plan = self._snapshot(row)
        for item in plan['items']:
            run = db.execute('SELECT id,status,idea_id,artwork_sha256,payload,payload_hash FROM idea_manual_runs WHERE request_id=?', (item['request_id'],)).fetchone()
            if not run:
                db.execute("UPDATE idea_manual_production_batch_items SET status='needs_review',error_code='run_missing' WHERE batch_id=? AND position=? AND run_id IS NOT NULL",
                           (row['id'], item['position']))
                continue
            try:
                payload = json.loads(run['payload'])
                if not isinstance(payload, dict):
                    payload = {}
            except (ValueError, TypeError):
                payload = {}
            valid = (run['idea_id'] == item['idea_id'] and run['artwork_sha256'] == item['expected_source_sha256']
                     and hashlib.sha256(run['payload'].encode()).hexdigest() == run['payload_hash']
                     and payload.get('variant_count', 7) == item['variant_count']
                     and payload.get('source_revision') == plan['source']['revision']
                     and payload.get('scheduling_bindings_hash') == plan['source']['settings_sha256']
                     and payload.get('trigger_mode', 'manual_web_run_idea') == 'manual_web_run_idea'
                     and payload.get('automatic_batch') is None
                     and ('production_iteration' in payload) == item['repeat']
                     and payload.get('audio_assignments') == plan['audio_assignments'][item['audio_offset']:item['audio_offset'] + item['variant_count']])
            state = ('completed' if run['status'] == 'completed' else 'queued' if run['status'] in ACTIVE else 'needs_review') if valid else 'needs_review'
            error = None if state != 'needs_review' else 'run_requires_review' if valid else 'run_binding_changed'
            db.execute('UPDATE idea_manual_production_batch_items SET status=?,run_id=?,error_code=? WHERE batch_id=? AND position=?',
                       (state, run['id'], error, row['id'], item['position']))
        states = [v[0] for v in db.execute('SELECT status FROM idea_manual_production_batch_items WHERE batch_id=?', (row['id'],))]
        state = ('needs_review' if 'needs_review' in states else 'complete' if all(v == 'completed' for v in states)
                 else 'running' if any(v != 'pending' for v in states) else 'prepared')
        db.execute('UPDATE idea_manual_production_batches SET status=?,updated_at=? WHERE id=?', (state, self._now().isoformat(), row['id']))

    def _public(self, db, row):
        plan = self._snapshot(row)
        receipts = {v['position']: dict(v) for v in db.execute('SELECT * FROM idea_manual_production_batch_items WHERE batch_id=? ORDER BY position', (row['id'],))}
        return {key: row[key] for key in ('id', 'request_id', 'status', 'created_at', 'updated_at')} | {
            'total_videos': 14, 'source_revision': plan['source']['revision'],
            'items': [{k: item[k] for k in ('idea_id', 'expected_source_sha256', 'repeat', 'position', 'variant_count', 'request_id')}
                      | {k: receipts[item['position']][k] for k in ('status', 'run_id', 'error_code')} for item in plan['items']]}

    def get(self, batch_id):
        if not _uuid(batch_id):
            _fail('invalid_manual_production_batch', 422)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM idea_manual_production_batches WHERE id=?', (batch_id,)).fetchone()
            if row is None:
                _fail('not_found', 404)
            self._reconcile(db, row)
            return self._public(db, db.execute('SELECT * FROM idea_manual_production_batches WHERE id=?', (batch_id,)).fetchone())

    def by_request(self, request_id):
        if not _uuid(request_id):
            _fail('invalid_manual_production_batch', 422)
        with self.store.connect() as db:
            row = db.execute('SELECT id FROM idea_manual_production_batches WHERE request_id=?', (request_id,)).fetchone()
        return self.get(row['id']) if row else None

    def request(self, request_id, *, items, page_count=14):
        inputs = self._input(request_id, items, page_count)
        encoded_input = _json(inputs)
        batch_id = str(uuid5(NAMESPACE_URL, 'via/manual-production-batch/' + request_id))
        with self.store.connect() as db:
            existing = db.execute('SELECT * FROM idea_manual_production_batches WHERE request_id=?', (request_id,)).fetchone()
            if existing and existing['input_json'] != encoded_input:
                _fail('manual_batch_conflict')
        if not existing:
            with self.store.connect() as db:
                self._source(db)
                if db.execute("SELECT 1 FROM idea_manual_runs WHERE status IN ('queued','preparing','running','importing') LIMIT 1").fetchone():
                    _fail('manual_batch_producer_busy')
            self._ready()
            for item in inputs['items']:
                self._verify_source(item)
            assignments = self.audio.plan_manual(batch_id, 14)
            if (not isinstance(assignments, list) or len(assignments) != 14
                    or any(not isinstance(v, dict) or type(v.get('index')) is not int or v['index'] != i
                           or not _sha(v.get('source_sha256')) for i, v in enumerate(assignments, 1))):
                _fail('audio_mix_changed')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT * FROM idea_manual_production_batches WHERE request_id=?', (request_id,)).fetchone()
                if existing:
                    if existing['input_json'] != encoded_input:
                        _fail('manual_batch_conflict')
                else:
                    source = self._source(db)
                    if db.execute("SELECT 1 FROM idea_manual_runs WHERE status IN ('queued','preparing','running','importing') LIMIT 1").fetchone():
                        _fail('manual_batch_producer_busy')
                    if db.execute("SELECT 1 FROM idea_manual_production_batches WHERE status IN ('prepared','running') LIMIT 1").fetchone():
                        _fail('manual_batch_producer_busy')
                    self._validate_candidates(db, inputs['items'])
                    allocation = allocate_counts([v['idea_id'] for v in inputs['items']], 14)['allocations']
                    planned, offset = [], 0
                    for entry, count in zip(inputs['items'], allocation):
                        child_id = str(uuid5(UUID(batch_id), 'idea:' + str(count['position']) + ':' + entry['idea_id']))
                        planned.append({**entry, **count, 'request_id': child_id, 'audio_offset': offset})
                        offset += count['variant_count']
                    snapshot = _json({'source': source, 'items': planned, 'audio_assignments': assignments})
                    now = self._now().isoformat()
                    db.execute('INSERT INTO idea_manual_production_batches(id,request_id,input_json,snapshot,snapshot_sha256,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                               (batch_id, request_id, encoded_input, snapshot, hashlib.sha256(snapshot.encode()).hexdigest(), 'prepared', now, now))
                    db.executemany('INSERT INTO idea_manual_production_batch_items(batch_id,position,idea_id,request_id,status) VALUES(?,?,?,?,?)',
                                   [(batch_id, v['position'], v['idea_id'], v['request_id'], 'pending') for v in planned])
        self._dispatch(batch_id)
        return self.get(batch_id)

    def _dispatch(self, batch_id):
        owner = str(uuid4())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM idea_manual_production_batches WHERE id=?', (batch_id,)).fetchone()
            self._reconcile(db, row)
            row = db.execute('SELECT * FROM idea_manual_production_batches WHERE id=?', (batch_id,)).fetchone()
            if row['status'] in ('complete', 'needs_review') or row['lease_owner'] and row['lease_until'] > self._now().isoformat():
                return
            db.execute('UPDATE idea_manual_production_batches SET lease_owner=?,lease_until=? WHERE id=?',
                       (owner, (self._now() + timedelta(seconds=LEASE_SECONDS)).isoformat(), batch_id))
            plan = self._snapshot(row)
        try:
            for item in plan['items']:
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT * FROM idea_manual_production_batches WHERE id=?', (batch_id,)).fetchone()
                    if row['lease_owner'] != owner:
                        return
                    self._reconcile(db, row)
                    if db.execute('SELECT status FROM idea_manual_production_batches WHERE id=?', (batch_id,)).fetchone()[0] == 'needs_review':
                        return
                    state = db.execute('SELECT status FROM idea_manual_production_batch_items WHERE batch_id=? AND position=?', (batch_id, item['position'])).fetchone()[0]
                    if state != 'pending':
                        continue
                    if self._source(db) != plan['source']:
                        _fail('manual_batch_source_changed')
                    own_ids = [v['request_id'] for v in plan['items']]
                    marks = ','.join('?' for _ in own_ids)
                    if db.execute("SELECT 1 FROM idea_manual_runs WHERE status IN ('queued','preparing','running','importing') AND request_id NOT IN (" + marks + ') LIMIT 1', own_ids).fetchone():
                        _fail('manual_batch_producer_busy')
                self._verify_source(item)
                self._ready()
                proofs = {v['source_sha256']: v['approved_mix'] for v in plan['audio_assignments'] if 'approved_mix' in v}
                if not proofs or self.audio.validate_snapshot(proofs) is not True:
                    _fail('audio_mix_changed')
                kwargs = {'variant_count': item['variant_count'], 'audio_assignments': plan['audio_assignments'][item['audio_offset']:item['audio_offset'] + item['variant_count']]}
                if item['repeat']:
                    run = self.runs.request_iteration(item['idea_id'], item['request_id'], expected_source_sha256=item['expected_source_sha256'], **kwargs)
                else:
                    run = self.runs.request(item['idea_id'], item['request_id'], **kwargs)
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute('SELECT lease_owner FROM idea_manual_production_batches WHERE id=?', (batch_id,)).fetchone()[0] != owner:
                        return
                    db.execute('UPDATE idea_manual_production_batch_items SET status=?,run_id=?,error_code=NULL WHERE batch_id=? AND position=?', ('queued', run['id'], batch_id, item['position']))
        except AppError as error:
            if error.code in {'manual_batch_source_changed', 'idea_run_requires_review', 'idea_run_conflict',
                              'idea_iteration_requires_review', 'idea_iteration_source_changed', 'audio_mix_changed',
                              'audio_mix_not_approved', 'audio_approval_mismatch'}:
                with self.store.connect() as db:
                    db.execute("UPDATE idea_manual_production_batch_items SET status='needs_review',error_code=? WHERE batch_id=? AND position=? AND status='pending'",
                               (error.code, batch_id, item['position']))
            # The durable pending row and child request UUID survive local gate
            # failures. A replay may resume after the operator fixes readiness.
            raise
        finally:
            with self.store.connect() as db:
                db.execute('UPDATE idea_manual_production_batches SET lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=?', (batch_id, owner))
