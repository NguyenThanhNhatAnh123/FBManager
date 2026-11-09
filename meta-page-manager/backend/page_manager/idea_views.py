"""Explicit, read-only view snapshots for exact published pipeline variants.

No worker is launched, no Reel is scheduled, and no provider mutation is used.
The existing queue records publication-confirmation time, not Meta's exact
publication timestamp. Such snapshots must stay out of same-age ranking.
"""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import re
import time
from uuid import UUID


MAX_TARGETS = 100
MAX_REFRESH_SECONDS = 25  # Leave room inside the MCP client's 30-second timeout.
CONCURRENCY = 2
INTERVAL_SECONDS = 3600
ID_RE = re.compile(r'[0-9]{1,32}\Z', re.ASCII)
SHA_RE = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)


def _views(value):
    """Unsigned-count semantics; tolerate canonical decimal wire text only.

    Meta's generated AdVideo schema names views as an unsigned integer. No
    compact display text, alternate metric, float or reach value is inferred.
    https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/advideo.py
    """
    if type(value) is int:
        return value if 0 <= value <= 10**15 else None
    if (isinstance(value, str) and re.fullmatch(r'(?:0|[1-9][0-9]{0,15})', value, re.ASCII)
            and int(value) <= 10**15):
        return int(value)
    return None


def utcnow():
    return datetime.now(timezone.utc)


def _instant(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class IdeaViews:
    def __init__(self, manager, pipeline, clock=utcnow):
        self.manager, self.pipeline, self.store = manager, pipeline, manager.store
        self.clock = clock
        self.lock = asyncio.Lock()
        self.task = None
        self.closing = False
        self.next_tick = 0.0
        self.last_refresh_at = None
        self.last_summary = None
        with self.store.connect() as db:
            db.execute('CREATE INDEX IF NOT EXISTS idea_views_latest ON idea_pipeline_views(variant_id,metric,observed_at DESC)')

    def _targets(self):
        # Exact reservation + completed job + asset hash. Never match a video
        # by caption, campaign, filename, Page name or provider URL.
        with self.store.connect() as db:
            rows = db.execute('''
                SELECT v.id AS variant_id,v.video_sha256,v.reservation_id,
                       j.id AS job_id,j.page_id,j.connection_id,j.video_id,j.finished_at
                  FROM idea_pipeline_variants v
                  JOIN reel_jobs j ON j.request_id=v.reservation_id
                  JOIN reel_assets a ON a.id=j.asset_id AND a.sha256=v.video_sha256
                 WHERE v.status='metadata_registered' AND j.status='completed'
                 ORDER BY j.id DESC LIMIT ?''', (MAX_TARGETS,)).fetchall()
        return [dict(row) for row in rows]

    def _still_bound(self, target):
        with self.store.connect() as db:
            row = db.execute('''
                SELECT 1 FROM idea_pipeline_variants v
                  JOIN reel_jobs j ON j.request_id=v.reservation_id
                  JOIN reel_assets a ON a.id=j.asset_id AND a.sha256=v.video_sha256
                 WHERE v.id=? AND v.status='metadata_registered' AND v.video_sha256=?
                   AND v.reservation_id=? AND j.id=? AND j.page_id=?
                   AND j.connection_id=? AND j.video_id=? AND j.status='completed'
                   AND j.finished_at=?''',
                tuple(target[key] for key in ('variant_id', 'video_sha256', 'reservation_id',
                    'job_id', 'page_id', 'connection_id', 'video_id', 'finished_at'))).fetchone()
        return row is not None

    def _save(self, target, observed, published, value, verified):
        if not self._still_bound(target):
            return False
        self.pipeline.record_views(
            target['variant_id'], target['page_id'], target['video_id'], 'views',
            value if verified else None, observed.isoformat(), published.isoformat(),
            availability='available' if verified else 'unavailable',
            metric_verified=verified, published_time_provenance='job_completion')
        return verified

    async def _read(self, target, observed):
        published = _instant(target.get('finished_at'))
        if (published is None or published > observed
                or not isinstance(target.get('page_id'), str)
                or not ID_RE.fullmatch(target['page_id'])
                or not isinstance(target.get('video_id'), str)
                or not ID_RE.fullmatch(target['video_id'])
                or not isinstance(target.get('video_sha256'), str)
                or not SHA_RE.fullmatch(target['video_sha256'])
                or type(target.get('connection_id')) is not int
                or target['connection_id'] < 1):
            return False
        value, verified = None, False
        try:
            async with self.manager._page_lock(target['page_id']), \
                    self.manager._lock(target['connection_id']), self.manager.capacity:
                source = self.manager._edit_source(target['page_id'], target['connection_id'])
                async with self.manager.graph_factory() as graph:
                    token, _ = await self.manager._edit_token(graph, target['page_id'], source)
                    data = await graph.get(target['video_id'], token, {'fields': 'id,from,views'})
                    self.manager._edit_source(target['page_id'], source['id'], source['token_revision'])
                owner = data.get('from') if isinstance(data, dict) else None
                if (isinstance(data, dict) and data.get('id') == target['video_id']
                        and isinstance(owner, dict) and owner.get('id') == target['page_id']
                        and _views(data.get('views')) is not None):
                    value, verified = _views(data['views']), True
        except Exception:
            # Provider errors, credential errors and changing source bindings
            # become unknown snapshots, never raw messages or guessed zeros.
            value, verified = None, False
        try:
            return self._save(target, observed, published, value, verified)
        except Exception:
            # A conflicting snapshot or changed ledger must not be overwritten.
            return False

    @staticmethod
    def _summary(targets, refreshed, *, busy=False):
        limitations = [
            'Chỉ đọc video đã hoàn tất và khớp reservation cùng SHA256; tối đa 100 video mỗi lượt.',
            'Thiếu hoặc không đọc được lượt xem là chưa rõ, không tính thành 0.',
            'Giờ hoàn tất tác vụ là mốc gần đúng; các snapshot này không dùng xếp hạng cùng tuổi 24 giờ.',
            'Không tạo video, thay đổi ưu tiên hay tạo lịch đăng.',
        ]
        if busy:
            limitations.append('Đang có một lượt đọc view; không khởi động lượt trùng.')
        return {'targets': targets, 'refreshed': refreshed,
                'unavailable': targets - refreshed, 'limitations': limitations}

    async def refresh(self):
        if self.closing:
            return self._summary(0, 0)
        if self.lock.locked():
            return self._summary(0, 0, busy=True)
        async with self.lock:
            observed = _instant(self.clock())
            if observed is None:
                return self._summary(0, 0)
            targets = self._targets()
            pending = asyncio.Queue()
            for target in targets:
                pending.put_nowait(target)
            completed = []

            async def worker():
                while True:
                    try:
                        target = pending.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    if await self._read(target, observed):
                        completed.append(target['variant_id'])

            tasks = [asyncio.create_task(worker()) for _ in range(min(CONCURRENCY, len(targets)))]
            try:
                async with asyncio.timeout(MAX_REFRESH_SECONDS):
                    await asyncio.gather(*tasks)
            except TimeoutError:
                # Requests are reads only. Any count not safely recorded stays
                # unavailable, including work not begun before the deadline.
                pass
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            self.last_refresh_at = observed.isoformat()
            self.last_summary = self._summary(len(targets), len(completed))
            return self.last_summary

    def latest(self):
        """Latest allowlisted count per variant/metric; no guessed post clock."""
        with self.store.connect() as db:
            rows = db.execute('''SELECT s.*,v.idea_id FROM idea_pipeline_views s
                JOIN idea_pipeline_variants v ON v.id=s.variant_id
                WHERE s.observed_at=(SELECT MAX(t.observed_at) FROM idea_pipeline_views t
                    WHERE t.variant_id=s.variant_id AND t.metric=s.metric)
                ORDER BY s.observed_at DESC,s.variant_id,s.metric LIMIT 100''').fetchall()
        items = []
        for raw in rows:
            row = dict(raw)
            try:
                if any(str(UUID(row[key])) != row[key] for key in ('variant_id', 'idea_id')):
                    continue
                if any(not isinstance(row[key], str) or not ID_RE.fullmatch(row[key])
                       for key in ('page_id', 'video_id')):
                    continue
                if (row['metric'] not in {'views', 'blue_reels_play_count', 'total_video_views'}
                        or row['availability'] not in {'available', 'unavailable'}
                        or row['published_time_provenance'] not in {'job_completion', 'provider_published_at', 'agent_attested'}
                        or _instant(row['observed_at']) is None):
                    continue
                value = _views(row['value']) if row['availability'] == 'available' else None
                verified = row['metric_verified'] == 1 and value is not None
                items.append({key: row[key] for key in ('idea_id', 'variant_id', 'page_id', 'video_id',
                    'metric', 'observed_at', 'published_time_provenance')} | {
                    'value': value if verified else None, 'metric_verified': verified,
                    'availability': 'available' if verified else 'unavailable'})
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        known = sum(item['availability'] == 'available' for item in items)
        return {'items': items, 'total': len(items), 'known': known, 'unavailable': len(items) - known,
                'automatic_interval_seconds': INTERVAL_SECONDS,
                'running': self.lock.locked(), 'last_refresh_at': self.last_refresh_at,
                'limitations': self._summary(0, 0)['limitations']}

    def tick(self):
        """Hourly read-only refresh; empty eligible sets never open Graph."""
        if (self.closing or self.task is not None and not self.task.done()
                or time.monotonic() < self.next_tick):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.next_tick = time.monotonic() + INTERVAL_SECONDS
        self.task = asyncio.create_task(self.refresh())
        def done(task):
            if not task.cancelled():
                task.exception()
        self.task.add_done_callback(done)

    async def shutdown(self):
        self.closing = True
        if self.task is not None:
            if not self.task.done():
                self.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.task
