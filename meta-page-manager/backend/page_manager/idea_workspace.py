"""Read-only agent readiness and local idea discovery; no worker execution."""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import stat
import time
from pathlib import Path

from .idea_pipeline import OUTPUT_DIRECTORY
from .podsach_bridge import PodsachBridge


VIDEO_THREAD_ID = '01a18532-711b-7790-819c-2dc37233ea58'
VIDEO_THREAD_TITLE = 'Tạo video theo phân bổ Page'
SOURCE_URL = 'https://blueox179.com/campaign/zitat25'
BLACK_SOURCE_URL = 'https://blueox179.com/campaign/ge2610'
VIDEO_ATTESTATION_SCHEMA = 1
VIDEO_WORKFLOW_VERSION = 'page-shirt-zoom-8s-v3'
VIDEO_ATTESTATION_TTL_SECONDS = 24 * 60 * 60
VIDEO_WORKFLOW_FILES = (
    'page-manager-bridge/bridge.py',
    'page-manager-bridge/README.md',
    '.agents/skills/page-shirt-zoom-8s/SKILL.md',
    '.agents/skills/page-shirt-zoom-8s/scripts/render_video.py',
    '.agents/skills/page-shirt-zoom-8s/scripts/audio_tools.py',
    '.agents/skills/page-shirt-zoom-8s/references/defaults.json',
)


def _regular_bytes(path, maximum):
    """Read fixed workflow files without following links or accepting a changed file."""
    if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
           for p in (path, *path.parents)):
        raise ValueError('Linked file')
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
        raise ValueError('Invalid file')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('Changed file')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) > maximum or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or len(raw) != after.st_size):
        raise ValueError('Changed file')
    return raw


def video_workflow_configuration(*, workflow_directory=None):
    """Fingerprint only the fixed producer contract, skill and renderer; no execution."""
    directory = Path(workflow_directory) if workflow_directory is not None else OUTPUT_DIRECTORY
    hashes = []
    for name in VIDEO_WORKFLOW_FILES:
        try:
            raw = _regular_bytes(directory / name, 2 * 1024 * 1024)
            if not raw:
                raise ValueError('Empty file')
            hashes.append((name, hashlib.sha256(raw).hexdigest()))
        except (OSError, ValueError):
            return {'ready': False, 'workflow_version': VIDEO_WORKFLOW_VERSION,
                    'workflow_sha256': None, 'reason': 'workflow_files_unavailable'}
    payload = json.dumps({'version': VIDEO_WORKFLOW_VERSION, 'files': hashes},
                         separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return {'ready': True, 'workflow_version': VIDEO_WORKFLOW_VERSION,
            'workflow_sha256': hashlib.sha256(payload).hexdigest(), 'reason': None}


def _attestation_ttl():
    try:
        value = int(os.environ.get('PM_VIDEO_ATTESTATION_TTL_SECONDS', VIDEO_ATTESTATION_TTL_SECONDS))
        if 60 <= value <= VIDEO_ATTESTATION_TTL_SECONDS:
            return value
    except (TypeError, ValueError):
        pass
    return VIDEO_ATTESTATION_TTL_SECONDS


def _attestation_time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Invalid time')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Missing timezone')
    return parsed.astimezone(timezone.utc)


def video_readiness(root, *, now=None, workflow_directory=None):
    """Validate a bounded, expiring producer claim; never treat it as a live probe."""
    configuration = video_workflow_configuration(workflow_directory=workflow_directory)
    ttl = _attestation_ttl()
    result = {'thread_id': VIDEO_THREAD_ID, 'title': VIDEO_THREAD_TITLE,
              'status': 'awaiting', 'ready': False, 'workflow_ready': False, 'skill': None,
              'message': 'Chưa có xác nhận quy trình video còn hiệu lực; chưa kiểm tra worker đang chạy.',
              'blockers': [], 'configuration': configuration,
              'attestation': {'state': 'missing', 'ready': False, 'checked_at': None,
                              'expires_at': None, 'ttl_seconds': ttl},
              'continuity': {'eligible': False, 'basis': None, 'reviewed_at': None, 'validated_at': None},
              'runtime': {'state': 'not_checked', 'verified': False}}
    path = Path(root) / '.runtime' / 'page-video-agent-readiness.json'
    try:
        raw = _regular_bytes(path, 64000)
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('status') not in ('ready', 'blocked'):
            result['attestation']['state'] = 'invalid'
            return result
        # Labels are allowlisted. Worker output, paths and blocker prose are
        # not copied into public HTTP responses or treated as instructions.
        result['skill'] = 'page-shirt-zoom-8s'
        required = {'schema_version', 'kind', 'checked_at', 'workflow_version', 'workflow_sha256',
                    'producer_workflow_status'}
        if not required.issubset(value):
            state = 'legacy'
        elif (type(value['schema_version']) is not int or value['schema_version'] != VIDEO_ATTESTATION_SCHEMA
              or value['kind'] != 'producer_workflow_attestation'
              or value['producer_workflow_status'] not in ('ready', 'blocked')):
            state = 'invalid'
        else:
            checked = _attestation_time(value['checked_at'])
            current = now if now is not None else datetime.now(timezone.utc)
            if current.tzinfo is None or current.utcoffset() is None:
                raise ValueError('Missing clock timezone')
            expires = checked + timedelta(seconds=ttl)
            result['attestation'].update(checked_at=checked.isoformat(), expires_at=expires.isoformat())
            unchanged = (configuration['ready'] and value['workflow_version'] == VIDEO_WORKFLOW_VERSION
                         and value['workflow_sha256'] == configuration['workflow_sha256'])
            # This is a new machine hash comparison against the original review,
            # not a new producer/human claim or runtime probe. Only authorized
            # automatic dispatch may use it after the original claim expires.
            if checked <= current and unchanged and value['status'] == value['producer_workflow_status'] == 'ready':
                result['continuity'].update(eligible=True, basis='reviewed_hash_unchanged',
                    reviewed_at=checked.isoformat(), validated_at=current.isoformat())
            if checked > current:
                state = 'future'
            elif current >= expires:
                state = 'stale'
            elif not unchanged:
                state = 'mismatch'
            else:
                state = 'valid'
        result['attestation']['state'] = state
        ready = state == 'valid' and value['status'] == value.get('producer_workflow_status') == 'ready'
        result['attestation']['ready'] = ready
        result['ready'] = result['workflow_ready'] = ready
        result['status'] = 'ready' if ready else 'blocked' if state == 'valid' else 'awaiting'
        result['message'] = ('Producer đã xác nhận quy trình video cho đúng phiên bản và còn hạn; chưa xác minh runtime.'
                             if ready else 'Xác nhận quy trình video cần được kiểm tra hoặc làm mới; chưa xác minh runtime.')
        if state == 'stale' and result['continuity']['eligible']:
            result['message'] = ('Xác nhận producer đã hết hạn; máy vừa đối chiếu hash khớp bản đã duyệt. '
                                 'Auto có thể dùng đối chiếu này khi các điều kiện phiên và QA đạt; chưa xác minh runtime.')
        if not ready:
            result['blockers'] = [
                'Lượt chạy thủ công cần xác nhận producer còn hạn; Auto chỉ dùng hash đã duyệt khi các kiểm tra độc lập đạt.'
                if result['continuity']['eligible'] else
                'Cần producer kiểm tra quy trình hiện tại và cập nhật xác nhận có thời hạn.']
        return result
    except FileNotFoundError:
        return result
    except (OSError, ValueError, UnicodeError, RecursionError):
        result['attestation']['state'] = 'invalid'
        return result


def uploader_workspace_blocker(provider):
    """Absent fields retain legacy adapters; present malformed state fails closed."""
    if 'workspace' not in provider:return None
    workspace=provider['workspace']
    fields={'state','input_count','review_pending','pending_batch_count'}
    if not isinstance(workspace,dict) or set(workspace)!=fields:return 'uploader_workspace_unknown'
    state=workspace['state']
    counts_valid=(type(workspace['input_count']) is int and workspace['input_count']>=0
                  and type(workspace['pending_batch_count']) is int and workspace['pending_batch_count']>=0
                  and type(workspace['review_pending']) is bool)
    if state=='idle' and counts_valid and workspace['input_count']==workspace['pending_batch_count']==0 and workspace['review_pending'] is False:
        return None
    if state=='busy' and counts_valid:return 'uploader_workspace_busy'
    return 'uploader_workspace_unknown'


def _execution_status(readiness, consumer_ready, blockers):
    value = {'mode': 'manual', 'supported': bool(readiness.get('ready') and consumer_ready),
             'blockers': blockers}
    for name in ('cli_configured', 'cli_runtime_verified'):
        if type(readiness.get(name)) is bool:
            value[name] = readiness[name]
    if readiness.get('cli_configuration_source') in ('default', 'environment'):
        value['cli_configuration_source'] = readiness['cli_configuration_source']
    if isinstance(readiness.get('orchestration'), dict):
        value['orchestration'] = readiness['orchestration']
    return value


class IdeaWorkspace:
    def __init__(self, root, manager, pipeline, *, bridge=None):
        self.root = Path(root)
        self.manager, self.pipeline = manager, pipeline
        self.bridge = bridge or PodsachBridge()
        self.task = None
        self.closing = False
        self.last_tick = 0.0
        self.last_scan = None
        self.scan_error = None
        self.lock = asyncio.Lock()
        self.runs = None
        self.consumer_ready = False
        self.views = None

    async def scan(self):
        async with self.lock:
            try:
                self.last_scan = await asyncio.to_thread(self.pipeline.scan)
                self.scan_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.scan_error = 'Chưa đọc được kho idea. Kiểm tra quyền truy cập thư mục rồi thử lại.'
            return self.last_scan

    def tick(self):
        if self.closing or self.task and not self.task.done() or time.monotonic() - self.last_tick < 30:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.last_tick = time.monotonic()
        self.task = asyncio.create_task(self.scan())

    async def status(self):
        pipeline, upload, agent, ranking = await asyncio.gather(
            asyncio.to_thread(self.pipeline.status),
            asyncio.to_thread(self.bridge.inspect),
            asyncio.to_thread(video_readiness, self.root, workflow_directory=self.pipeline.output_directory),
            asyncio.to_thread(self.pipeline.ranking),
        )
        run_status=await asyncio.to_thread(self.runs.status) if self.runs is not None else None
        readiness=run_status['readiness'] if run_status else {}
        performance=await asyncio.to_thread(self.views.latest) if self.views is not None else None
        labels={'cli_unavailable':'Chưa tìm thấy worker Codex.',
                'adapter_unavailable':'Adapter video chưa sẵn sàng.',
                'source_unverified':'Campaign mẫu chưa được kiểm tra.',
                'upload_unavailable':'Tool upload chưa kết nối đúng shop blueox179.',
                'video_workflow_unavailable':'Quy trình video chưa sẵn sàng.',
                'production_pipeline_unavailable':'Bộ điều phối từng video chưa sẵn sàng.'}
        labels.update(uploader_workspace_busy='Tool upload đang có input, review hoặc batch chưa xong. Giữ dữ liệu hiện có và xử lý trước khi chạy idea mới.',
                      uploader_workspace_unknown='Chưa đọc được trạng thái vùng làm việc tool upload; kiểm tra tool trước khi tạo ảnh/video.')
        labels['schedule_unavailable']='Cần bật nguồn lịch 14 Page lúc 06:00 giờ Los Angeles.'
        labels['page_information_required']='Các Page trong nguồn lịch phải được đổi thông tin trước khi tạo nội dung đăng.'
        blockers=[labels.get(code,'Worker còn bước cần kiểm tra.') for code in readiness.get('blocker_codes',[])]
        if not self.consumer_ready:blockers.append('Bộ kiểm tra và nhận video chưa sẵn sàng.')
        return {'pipeline': pipeline, 'upload': upload, 'video_agent': agent,
                'ranking': ranking, 'last_scan': self.last_scan,
                'scan_error': self.scan_error,
                'execution': _execution_status(readiness, self.consumer_ready, blockers),
                'runs': {'items':run_status['items'] if run_status else []},
                'performance':performance,
                'policy': {'source_requested': SOURCE_URL,
                           'black_source_requested': BLACK_SOURCE_URL,
                           'trigger': 'authorized_run', 'allowed_trigger_modes': ['manual_web_run_idea','automatic_idea_batch'],
                           'caption_mode': 'product_link_only',
                           'variants_per_idea': None, 'legacy_default_variant_count': 7,
                           'variant_count_mode': 'allocated_per_run', 'variant_count_min': 1, 'variant_count_max': 14,
                           'timezone': 'America/Los_Angeles',
                           'daily_time': '06:00', 'max_reels_per_page_per_day': 1,
                           'production_enabled': False,
                           'metrics_collection': 'hourly_and_explicit',
                           'ranking_mode': 'preview_only'}}

    async def page_status(self, *, limit=None, offset=0, search='', status='all', sort='warehouse',
                          ranking_limit=None, ranking_offset=0, calendar='', timezone='America/Los_Angeles',
                          clamp_offset=True):
        """Browser summary plus requested SQL windows, without recursive global ledgers."""
        pipeline, upload, agent, readiness = await asyncio.gather(
            asyncio.to_thread(self.pipeline.summary),
            asyncio.to_thread(self.bridge.inspect),
            asyncio.to_thread(video_readiness, self.root, workflow_directory=self.pipeline.output_directory),
            asyncio.to_thread(self.runs.readiness) if self.runs is not None else asyncio.sleep(0,result={}),
        )
        if limit is not None:
            page=await asyncio.to_thread(self.pipeline.catalog,limit,offset,search,status,sort,calendar,timezone,clamp_offset=clamp_offset)
            pipeline['ideas']=page['items']
            pipeline['pagination']={key:page[key] for key in ('total','limit','offset')}
        ids=[item['id'] for item in pipeline['ideas']]
        runs=await asyncio.to_thread(self.runs.for_ideas,ids) if self.runs is not None and ids else {'items':[]}
        ranking=await asyncio.to_thread(self.pipeline.ranking_page,ranking_limit,ranking_offset,clamp_offset=clamp_offset) if ranking_limit is not None else {'mode':'preview_only','cohorts':[]}
        labels={'cli_unavailable':'Chưa tìm thấy worker Codex.',
                'adapter_unavailable':'Adapter video chưa sẵn sàng.',
                'source_unverified':'Campaign mẫu chưa được kiểm tra.',
                'upload_unavailable':'Tool upload chưa kết nối đúng shop blueox179.',
                'video_workflow_unavailable':'Quy trình video chưa sẵn sàng.',
                'production_pipeline_unavailable':'Bộ điều phối từng video chưa sẵn sàng.',
                'schedule_unavailable':'Cần bật nguồn lịch 14 Page lúc 06:00 giờ Los Angeles.',
                'page_information_required':'Các Page trong nguồn lịch phải được đổi thông tin trước khi tạo nội dung đăng.'}
        labels.update(uploader_workspace_busy='Tool upload đang có input, review hoặc batch chưa xong. Giữ dữ liệu hiện có và xử lý trước khi chạy idea mới.',
                      uploader_workspace_unknown='Chưa đọc được trạng thái vùng làm việc tool upload; kiểm tra tool trước khi tạo ảnh/video.')
        blockers=[labels.get(code,'Worker còn bước cần kiểm tra.') for code in readiness.get('blocker_codes',[])]
        if not self.consumer_ready:blockers.append('Bộ kiểm tra và nhận video chưa sẵn sàng.')
        return {'pipeline':pipeline,'upload':upload,'video_agent':agent,'ranking':ranking,
                'last_scan':self.last_scan,'scan_error':self.scan_error,'runs':runs,
                'execution':_execution_status(readiness, self.consumer_ready, blockers),
                'performance':{'items':[]},
                'policy':{'source_requested':SOURCE_URL,'black_source_requested':BLACK_SOURCE_URL,
                    'trigger':'authorized_run','allowed_trigger_modes':['manual_web_run_idea','automatic_idea_batch'],
                    'caption_mode':'product_link_only','variants_per_idea':None,'legacy_default_variant_count':7,
                    'variant_count_mode':'allocated_per_run','variant_count_min':1,'variant_count_max':14,
                    'timezone':'America/Los_Angeles','daily_time':'06:00','max_reels_per_page_per_day':1,
                    'production_enabled':False,'metrics_collection':'hourly_and_explicit','ranking_mode':'preview_only'}}

    async def shutdown(self):
        self.closing = True
        if self.task and not self.task.done():
            # The scan only performs bounded local work. Let its thread finish
            # before the host releases the application's process lock.
            with suppress(asyncio.CancelledError):
                await self.task
