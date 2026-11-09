"""Private, frozen-run PODSach client. Never logs auth material or raw provider logs.

This program does not start runs. A live canonical run, its staged source and
its selected uploader must already exist. Every mutation is latched before I/O;
unknown outcomes stop further mutations for this run instead of being retried.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import quote
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from page_manager.project_paths import RUNS_DIRECTORY,LEGACY_RUNS_DIRECTORY,select_existing_run_directory
RUNS_ROOT = RUNS_DIRECTORY
LEGACY_RUNS_ROOT = LEGACY_RUNS_DIRECTORY
LOCAL = 'http://127.0.0.1:8770'
WEB = 'https://ul.eagleglobal2023.com/s/blueox179.com'
SHOP = 'blueox179.com'
MAX_JSON = 128 * 1024
MAX_RESPONSE = 512 * 1024
MAX_IMAGE = 25 * 1024 * 1024
SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z')
ITEM = re.compile(r'(?:[a-f0-9]{12}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\Z')
STATE_NAME = 'uploader-client-state.json'


class ClientError(Exception):
    def __init__(self, code, *, ambiguous=False):
        super().__init__(code)
        self.code, self.ambiguous = code, ambiguous


def fail(code, **kwargs):
    raise ClientError(code, **kwargs)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or not UUID(value).int:
            fail('invalid_run_binding')
    except (ValueError, TypeError, AttributeError):
        fail('invalid_run_binding')
    return value


def regular(path, maximum):
    try:
        for part in (path, *path.parents):
            meta = part.lstat()
            if part.is_symlink() or getattr(meta, 'st_file_attributes', 0) & 0x400:
                fail('unsafe_run_file')
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or not 0 < meta.st_size <= maximum:
            fail('unsafe_run_file')
        return meta
    except (OSError, ValueError):
        fail('unsafe_run_file')


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail('invalid_json')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _value: fail('invalid_json'))
    except (ValueError, UnicodeError, RecursionError):
        fail('invalid_json')


def read_json(path, maximum=MAX_JSON):
    regular(path, maximum)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > maximum or signature(before) != signature(after):
        fail('changed_run_file')
    return parse_json(raw)


def signature(meta):
    return meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns


def source_hash(path):
    regular(path, MAX_IMAGE)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if signature(before) != signature(after):
        fail('changed_run_file')
    return digest.hexdigest()


class ReadOnlyRunStore:
    """Read the fixed private authority DB without Store schema/migration writes."""
    def __init__(self):
        self.path=ROOT/'data'/'page-manager.sqlite3'
        regular(self.path,2*1024*1024*1024)

    @contextmanager
    def connect(self):
        before=regular(self.path,2*1024*1024*1024)
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            after=regular(self.path,2*1024*1024*1024)
            if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):fail('invalid_run_binding')
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db
        finally:db.close()


def iteration_variants(db, row, data, count):
    """Read one explicitly authorized iteration; never reuse its legacy prefix."""
    iteration = data.get('production_iteration')
    if (not isinstance(iteration, dict) or set(iteration) != {'id', 'number'}
            or type(iteration.get('number')) is not int or not 2 <= iteration['number'] <= 2**63 - 1):
        fail('invalid_run_binding')
    if any(type(value.get('ordinal')) is not int or value['ordinal'] != ordinal
           for ordinal, value in enumerate(data['variants'], 1)):
        fail('invalid_run_binding')
    iteration_id = uuid(iteration['id'])
    request_id = uuid(row.get('request_id'))
    recorded = db.execute('''SELECT * FROM idea_production_iterations WHERE id=?''',
                          (iteration_id,)).fetchone()
    if (recorded is None or recorded['idea_id'] != data['idea_id']
            or recorded['request_id'] != request_id or recorded['artwork_sha256'] != data['artwork_sha256']
            or recorded['number'] != iteration['number'] or recorded['variant_count'] != count):
        fail('invalid_run_binding')
    rows = [dict(value) for value in db.execute('''SELECT v.id AS variant_id,v.reservation_id,
        m.ordinal,v.idea_id FROM idea_production_iteration_variants m
        LEFT JOIN idea_pipeline_variants v ON v.id=m.variant_id
        WHERE m.iteration_id=? ORDER BY m.ordinal LIMIT 15''', (iteration_id,))]
    if (len(rows) != count or [value['ordinal'] for value in rows] != list(range(1, count + 1))
            or any(value['idea_id'] != data['idea_id'] for value in rows)
            or len({value['variant_id'] for value in rows}) != count
            or len({value['reservation_id'] for value in rows}) != count):
        fail('invalid_run_binding')
    return [{key: value[key] for key in ('variant_id', 'reservation_id', 'ordinal')} for value in rows]


def load_binding(run_input, store, *, runs_root=None):
    path=Path(run_input)
    if not path.is_absolute() or path.name != 'run-input.json':
        fail('invalid_run_location')
    if runs_root is None:
        selected=select_existing_run_directory(path.parent.name,primary_runs=RUNS_ROOT,legacy_runs=LEGACY_RUNS_ROOT)
        if selected is None or selected!=path.parent:fail('invalid_run_location')
        root=selected.parent
    else:root=Path(runs_root)
    try:
        if path.resolve(strict=True) != path or path.parent.parent != root.resolve(strict=True):
            fail('invalid_run_location')
    except (OSError, ValueError):
        fail('invalid_run_location')
    run_id = uuid(path.parent.name)
    data = read_json(path)
    if not isinstance(data, dict) or data.get('schema_version') != '1.0' or data.get('run_id') != run_id:
        fail('invalid_run_binding')
    uuid(data.get('idea_id'))
    if not isinstance(data.get('artwork_sha256'), str) or not re.fullmatch('[a-f0-9]{64}', data['artwork_sha256']):
        fail('invalid_run_binding')
    if type(data.get('lease_fence')) is not int or data['lease_fence'] < 1:
        fail('invalid_run_binding')
    variants = data.get('variants')
    count = data.get('variant_count', 7)
    if type(count) is not int or not 1 <= count <= 14 or not isinstance(variants, list) or len(variants) != count:
        fail('invalid_run_binding')
    for row in variants:
        if not isinstance(row, dict): fail('invalid_run_binding')
        uuid(row.get('variant_id')); uuid(row.get('reservation_id'))
    if len({v['variant_id'] for v in variants}) != count or len({v['reservation_id'] for v in variants}) != count:
        fail('invalid_run_binding')
    uploader = data.get('uploader')
    if uploader not in ({'kind': 'local', 'endpoint': LOCAL, 'shop': SHOP},
                        {'kind': 'web', 'endpoint': WEB, 'shop': SHOP}):
        fail('uploader_binding_missing')
    source = Path(data.get('source_path', ''))
    if not source.is_absolute() or source.parent != path.parent or source.name not in ('source.png', 'source.jpg', 'source.jpeg', 'source.webp'):
        fail('invalid_source_binding')
    if source_hash(source) != data['artwork_sha256']:
        fail('invalid_source_binding')
    try:
        with store.connect() as db:
            row = db.execute('SELECT * FROM idea_manual_runs WHERE id=?', (run_id,)).fetchone()
            if row is None: fail('run_not_authorized')
            row = dict(row)
            frozen = parse_json(row['payload'].encode('utf-8'))
            if hashlib.sha256(canonical(frozen).encode()).hexdigest() != row['payload_hash']:
                fail('invalid_run_binding')
            if row['status'] not in ('preparing', 'running') or row['cancel_requested'] or row['fence'] != data['lease_fence']:
                fail('run_not_active')
            if row['idea_id'] != data['idea_id'] or row['artwork_sha256'] != data['artwork_sha256']:
                fail('invalid_run_binding')
            for key in ('uploader', 'source_snapshot', 'source_revision', 'variants', 'caption_mode', 'trigger_mode'):
                if key not in frozen or data.get(key) != frozen[key]: fail('invalid_run_binding')
            frozen_count = frozen.get('variant_count', 7)
            if type(frozen_count) is not int or frozen_count != count:
                fail('invalid_run_binding')
            for key in ('automatic_batch', 'workflow', 'audio_assignments', 'audio_mix_policy', 'approved_audio_mixes',
                        'execution_pipeline', 'execution_pipeline_sha256', 'production_iteration','review_policy'):
                if (key in data) != (key in frozen) or data.get(key) != frozen.get(key):
                    fail('invalid_run_binding')
            if (frozen['caption_mode'] != 'product_link_only'
                    or frozen['trigger_mode'] not in ('manual_web_run_idea', 'automatic_idea_batch')
                    or frozen['trigger_mode'] == 'automatic_idea_batch' and not isinstance(frozen.get('automatic_batch'), dict)):
                fail('invalid_run_binding')
            idea = db.execute('SELECT artwork_sha256 FROM idea_pipeline_ideas WHERE id=?', (data['idea_id'],)).fetchone()
            current_variants = (iteration_variants(db, row, data, count) if 'production_iteration' in data else
                [dict(v) for v in db.execute('SELECT id AS variant_id,reservation_id,ordinal FROM idea_pipeline_variants WHERE idea_id=? ORDER BY ordinal LIMIT ?', (data['idea_id'], count))])
            schedule = db.execute('SELECT revision,settings FROM video_source_settings WHERE id=1').fetchone()
            settings = db.execute('SELECT settings FROM idea_pipeline_settings WHERE id=1').fetchone()
            if idea is None or idea['artwork_sha256'] != data['artwork_sha256'] or current_variants != variants:
                fail('invalid_run_binding')
            if schedule is None or schedule['revision'] != frozen['source_revision'] or hashlib.sha256(canonical(parse_json(schedule['settings'].encode())).encode()).hexdigest() != frozen.get('scheduling_bindings_hash'):
                fail('run_sources_changed')
            current = parse_json(settings['settings'].encode()) if settings else {}
            if any(current.get(k) != frozen['source_snapshot'].get(k) for k in ('campaign_source', 'black_campaign_source')):
                fail('run_sources_changed')
    except sqlite3.Error:
        # Permission/locking/schema IO failures are not evidence that frozen
        # input differs. Keep raw SQLite messages and records out of output.
        fail('run_authority_unavailable')
    except (KeyError, TypeError):
        fail('invalid_run_binding')
    return {'path': path, 'folder': path.parent, 'source': source, 'data': data, 'store': store, 'runs_root': root}


def route_kind(method, path):
    if method not in ('GET', 'POST') or not isinstance(path, str) or '?' in path or '#' in path or '%' in path or '\\' in path:
        fail('route_not_allowed')
    if method == 'GET' and path in ('/api/overview', '/api/job'): return path
    if method == 'GET' and re.fullmatch(r'/api/batch/[A-Za-z0-9][A-Za-z0-9_-]{0,99}/log', path): return 'log'
    if method == 'POST' and path in ('/api/input/upload', '/api/prep/start', '/api/review/export', '/api/upload/start'): return path
    if method == 'POST' and path.startswith('/api/item/') and ITEM.fullmatch(path[10:]): return 'item'
    fail('route_not_allowed')


def http_json(client, method, path, *, body=None, files=None):
    try:
        with client.stream(method, path, headers={'X-PODSach-Expected-Shop': SHOP, 'Accept': 'application/json'},
                           json=body if files is None and method == 'POST' else None, files=files,
                           timeout=httpx.Timeout(70, connect=5)) as response:
            if response.status_code == 401: fail('uploader_session_expired')
            if not 200 <= response.status_code < 300:
                fail('uploader_request_rejected', ambiguous=method == 'POST' and response.status_code >= 500)
            content_length = response.headers.get('content-length')
            if content_length is not None and (not content_length.isdecimal() or int(content_length) > MAX_RESPONSE):
                fail('uploader_response_invalid', ambiguous=method == 'POST')
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=16384):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE: fail('uploader_response_invalid', ambiguous=method == 'POST')
        try: value = parse_json(bytes(raw))
        except ClientError: fail('uploader_response_invalid', ambiguous=method == 'POST')
        if not isinstance(value, dict) or value.get('error'): fail('uploader_request_rejected', ambiguous=method == 'POST')
        return value
    except httpx.HTTPError:
        fail('uploader_outcome_unknown' if method == 'POST' else 'uploader_unavailable', ambiguous=method == 'POST')


def basename(value):
    return isinstance(value, str) and 0 < len(value) <= 250 and value not in ('.', '..') and not any(c in value for c in '/\\:\x00')


def input_matches(client, overview, binding):
    values = overview.get('input')
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict) or not basename(values[0].get('name')):
        fail('uploader_workspace_busy')
    name = values[0]['name']
    digest, total = hashlib.sha256(), 0
    try:
        with client.stream('GET', '/img/input/' + quote(name, safe=''), headers={'X-PODSach-Expected-Shop': SHOP}) as response:
            if response.status_code != 200: fail('uploader_workspace_unverified')
            for chunk in response.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > MAX_IMAGE: fail('uploader_workspace_unverified')
                digest.update(chunk)
    except httpx.HTTPError: fail('uploader_unavailable')
    if digest.hexdigest() != binding['data']['artwork_sha256'] or total != binding['source'].stat().st_size:
        fail('uploader_workspace_busy')
    return name


def clean_string(value, maximum=5000):
    if not isinstance(value, str): return ''
    value = ''.join(c for c in value[:maximum] if c in '\n\t' or ord(c) >= 32)
    value = re.sub(r'(?i)(?:Bearer\s+\S+|(?:access_token|api[_-]?key|password|cookie|authorization)\s*[:=]\s*[^\s,;]+)', '[redacted]', value)
    value = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[redacted]', value)
    value = re.sub(r'[A-Za-z]:[\\/][^\r\n]+', '[private path]', value)
    return value


def safe_row(row):
    if not isinstance(row, dict): return {}
    strings = ('id', 'slug', 'source', 'quote', 'title', 'confidence', 'shirt', 'color', 'side', 'origin', 'default_product', 'filename', 'url_slug', 'full_title', 'source_mockup')
    result = {k: clean_string(row[k]) for k in strings if isinstance(row.get(k), str)}
    for key in ('width', 'height', 'size', 'approved', 'extract_ok', 'design_input'):
        if type(row.get(key)) in (int, bool): result[key] = row[key]
    if 'status' in row: result['status'] = 'done' if row['status'] == 'done' else 'error' if str(row['status']).startswith('error') else 'pending'
    return result


def sanitized(value, kind):
    result = {}
    for key in ('started', 'ok', 'count', 'discarded'):
        if type(value.get(key)) in (int, bool): result[key] = value[key]
    if basename(value.get('batch')): result['batch'] = value['batch']
    for key in ('saved', 'skipped', 'files', 'moved'):
        if isinstance(value.get(key), list): result[key] = [v for v in value[key][:100] if basename(v)]
    if isinstance(value.get('item'), dict): result['item'] = safe_row(value['item'])
    if kind == '/api/overview':
        info = value.get('info', {})
        result['info'] = {'domain': info.get('domain') if info.get('domain') == SHOP else None,
                          'authenticated': info.get('authenticated') is True}
        if SLUG.fullmatch(str(info.get('fallback_origin', ''))): result['info']['fallback_origin'] = info['fallback_origin']
        result['input'] = [safe_row(v) for v in value.get('input', [])[:100] if isinstance(v, dict)]
        for item, original in zip(result['input'], [v for v in value.get('input', [])[:100] if isinstance(v, dict)]):
            if basename(original.get('name')): item['name'] = original['name']
        review = value.get('review')
        result['review'] = {'batch': review.get('batch') if basename(review.get('batch')) else None, 'items': [safe_row(v) for v in review.get('items', [])[:100]]} if isinstance(review, dict) else None
        result['batches'] = [{'name': b.get('name') if basename(b.get('name')) else None, 'rows': [safe_row(v) for v in b.get('rows', [])[:100]]} for b in value.get('batches', [])[:100] if isinstance(b, dict)]
        result['job'] = sanitized(value.get('job', {}), '/api/job')
    if kind == '/api/job':
        for key in ('running', 'done', 'total'):
            if type(value.get(key)) in (bool, int): result[key] = value[key]
        if value.get('type') in ('prep', 'upload', 'login', 'colours', ''): result['type'] = value['type']
        result['has_error'] = bool(value.get('error'))
    if kind == 'log':
        log = value.get('log', '')
        result['raw_log_omitted'] = True
        result['candidate_campaign_urls'] = sorted(set(re.findall(r'https://blueox179\.com/campaign/[A-Za-z0-9_-]+', log)))[:100] if isinstance(log, str) else []
    return result


def read_state(binding):
    path = binding['folder'] / STATE_NAME
    identity = {'run_id': binding['data']['run_id'], 'artwork_sha256': binding['data']['artwork_sha256'],
                'uploader': binding['data']['uploader']}
    if not path.exists(): return {**identity, 'operations': []}
    value = read_json(path)
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in identity.items()) or not isinstance(value.get('operations'), list) or len(value['operations']) > 100:
        fail('uploader_checkpoint_invalid')
    return value


def write_state(binding, value):
    target = binding['folder'] / STATE_NAME
    temporary = binding['folder'] / (STATE_NAME + '.new')
    try:
        with temporary.open('xb') as stream:
            stream.write((canonical(value) + '\n').encode()); stream.flush(); os.fsync(stream.fileno())
        if target.exists(): regular(target, MAX_JSON)
        os.replace(temporary, target)
    except (OSError, ValueError): fail('uploader_checkpoint_invalid')


@contextmanager
def mutation_lock(binding):
    lock = binding['folder'] / 'uploader-client.lock'
    try: descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError: fail('uploader_operation_in_progress')
    try:
        os.close(descriptor); yield
    finally:
        try: lock.unlink()
        except OSError: pass


def preflight(client, binding, kind, path, body, state, shop_verifier=None):
    overview = http_json(client, 'GET', '/api/overview')
    info, job = overview.get('info'), overview.get('job')
    verified = (info.get('authenticated') is True if isinstance(info, dict) else False)
    if (not verified and isinstance(info, dict) and info.get('authenticated') is None
            and binding['data']['uploader']['kind'] == 'web'
            and callable(shop_verifier)):
        verified = shop_verifier() is True
    if not isinstance(info, dict) or info.get('domain') != SHOP or not verified:
        fail('uploader_not_ready')
    if not isinstance(job, dict) or job.get('running') is not False: fail('uploader_workspace_busy')
    if not isinstance(overview.get('input'), list) or not isinstance(overview.get('batches'), list): fail('uploader_response_invalid')
    own_batch = state.get('exported_batch')
    for batch in overview['batches']:
        if not isinstance(batch, dict) or not isinstance(batch.get('rows'), list): fail('uploader_response_invalid')
        if any(not isinstance(row, dict) for row in batch['rows']): fail('uploader_response_invalid')
        if any(row.get('status') != 'done' for row in batch['rows']) and batch.get('name') != own_batch:
            fail('uploader_workspace_busy')
    review = overview.get('review')
    if kind in ('/api/input/upload', '/api/prep/start') and review is not None: fail('uploader_workspace_busy')
    if kind == '/api/input/upload':
        if overview['input']:
            name = input_matches(client, overview, binding)
            return {'already_present': True, 'saved': [name]}
    elif kind == '/api/upload/start':
        if review is not None or not own_batch or body.get('batch') != own_batch: fail('uploader_workspace_busy')
        batch = next((b for b in overview['batches'] if b.get('name') == own_batch), None)
        if not batch or len(batch['rows']) != 1 or batch['rows'][0].get('source_mockup') != state.get('input_name'):
            fail('uploader_workspace_unverified')
    else:
        name = input_matches(client, overview, binding)
        state['input_name'] = name
        if kind in ('item', '/api/review/export'):
            if not isinstance(review, dict) or not basename(review.get('batch')) or not isinstance(review.get('items'), list) or len(review['items']) != 1 or review['items'][0].get('source') != name:
                fail('uploader_workspace_busy')
            if kind == 'item' and str(review['items'][0].get('id')) != path.rsplit('/', 1)[1]: fail('uploader_workspace_busy')
    return None


def validate_body(binding, kind, body, file):
    if kind == '/api/input/upload':
        if body is not None or file is None or Path(file) != binding['source']: fail('invalid_source_binding')
        return
    if file is not None or not isinstance(body, dict): fail('invalid_request_body')
    if kind == '/api/prep/start': allowed = {'origin'}
    elif kind == 'item': allowed = {'slug', 'quote', 'approved', 'shirt', 'side', 'color', 'origin', 'default_product'}
    elif kind == '/api/review/export': allowed = set()
    elif kind == '/api/upload/start': allowed = {'batch', 'origin', 'limit'}
    else: allowed = set()
    if set(body) - allowed: fail('invalid_request_body')
    if 'limit' in body and (type(body['limit']) is not int or body['limit'] != 1): fail('invalid_request_body')
    if 'origin' in body:
        sources = binding['data']['source_snapshot']
        templates = {sources.get(k, {}).get('campaign_url', '').rsplit('/', 1)[-1] for k in ('campaign_source', 'black_campaign_source')}
        if body['origin'] not in templates: fail('unverified_campaign_source')
    if kind == '/api/prep/start' and 'origin' not in body: fail('unverified_campaign_source')
    if kind == 'item':
        for key in ('quote', 'color', 'default_product'):
            if key in body and (not isinstance(body[key], str) or len(body[key]) > (5000 if key == 'quote' else 80)): fail('invalid_request_body')
        if 'slug' in body and (not isinstance(body['slug'], str) or not SLUG.fullmatch(body['slug'])): fail('invalid_request_body')
        if 'approved' in body and type(body['approved']) is not bool: fail('invalid_request_body')
        if 'shirt' in body and body['shirt'] not in ('white', 'black'): fail('invalid_request_body')
        if 'side' in body and body['side'] not in ('front', 'back'): fail('invalid_request_body')
    if kind == '/api/upload/start' and (not SLUG.fullmatch(str(body.get('batch', ''))) or body.get('limit') != 1 or 'origin' not in body): fail('invalid_request_body')


def perform_request(binding, client, *, method, path, body=None, file=None, shop_verifier=None):
    kind = route_kind(method, path)
    if method == 'GET':
        if body is not None or file is not None: fail('invalid_request_body')
        value = http_json(client, method, path)
        if kind == '/api/overview' and (not isinstance(value.get('info'), dict) or value['info'].get('domain') != SHOP): fail('uploader_not_ready')
        if (kind == '/api/overview' and value['info'].get('authenticated') is None
                and binding['data']['uploader']['kind'] == 'web'
                and callable(shop_verifier) and shop_verifier() is True):
            value['info'] = {**value['info'], 'authenticated': True}
        return {'ok': True, 'result': sanitized(value, kind)}
    validate_body(binding, kind, body, file)
    with mutation_lock(binding):
        binding = load_binding(binding['path'], binding['store'], runs_root=binding['runs_root'])
        state = read_state(binding)
        digest = hashlib.sha256(canonical({'path': path, 'body': body, 'source_sha256': binding['data']['artwork_sha256'] if file else None}).encode()).hexdigest()
        if any(op.get('outcome') in ('submitting', 'uncertain') for op in state['operations']): fail('uploader_outcome_unknown', ambiguous=True)
        previous = next((op for op in state['operations'] if op.get('digest') == digest), None)
        if previous: return {'ok': True, 'replayed': True, 'result': previous.get('result', {})}
        if kind == '/api/upload/start' and any(op.get('path') == path for op in state['operations']): fail('uploader_submission_already_attempted')
        cached = preflight(client, binding, kind, path, body, state, shop_verifier)
        if cached: return {'ok': True, 'result': cached}
        # Inspection may take time; cancellation/fence/source changes during it
        # must be checked again immediately before latching external mutation.
        binding = load_binding(binding['path'], binding['store'], runs_root=binding['runs_root'])
        operation = {'path': path, 'digest': digest, 'lease_fence': binding['data']['lease_fence'], 'outcome': 'submitting'}
        if len(state['operations']) >= 100: fail('uploader_checkpoint_full')
        state['operations'].append(operation);write_state(binding, state)
        try:
            if kind == '/api/input/upload':
                regular(binding['source'], MAX_IMAGE)
                with binding['source'].open('rb') as stream:
                    raw = http_json(client, method, path, files={'files': (binding['source'].name, stream, 'application/octet-stream')})
                if source_hash(binding['source']) != binding['data']['artwork_sha256']: fail('changed_run_file', ambiguous=True)
                names = raw.get('saved')
                if not isinstance(names, list) or len(names) != 1 or not basename(names[0]) or raw.get('skipped'): fail('uploader_response_invalid', ambiguous=True)
                state['input_name'] = names[0]
            else:
                raw = http_json(client, method, path, body=body)
            result = sanitized(raw, kind)
            if kind == '/api/review/export':
                if not basename(raw.get('batch')) or raw.get('count') != 1: fail('uploader_response_invalid', ambiguous=True)
                state['exported_batch'] = raw['batch']
            operation.update(outcome='accepted', result=result);write_state(binding, state)
            return {'ok': True, 'result': result}
        except Exception:
            operation['outcome'] = 'uncertain';write_state(binding, state)
            raise


@contextmanager
def provider_client(binding):
    if binding['data']['uploader']['kind'] == 'web':
        from page_manager.uploader_connection import UploaderConnection
        from page_manager.security import SecretBox
        with UploaderConnection(binding['store'], SecretBox(),initialize=False).client() as client:
            yield client
    else:
        with httpx.Client(base_url=LOCAL, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(70, connect=5), headers={'X-PODSach-Expected-Shop': SHOP}) as client:
            yield client


class PrivateParser(argparse.ArgumentParser):
    def error(self, _message):
        print(json.dumps({'ok': False, 'code': 'invalid_client_arguments', 'needs_review': True}))
        raise SystemExit(2)


def main(argv=None):
    parser = PrivateParser(prog='uploader_client', description='Use the frozen uploader of one active, authorized idea run.')
    parser.add_argument('--run-input', required=True)
    parser.add_argument('--method', required=True, choices=('GET', 'POST'))
    parser.add_argument('--path', required=True)
    parser.add_argument('--json-file')
    parser.add_argument('--file')
    arguments = parser.parse_args(argv)
    try:
        binding = load_binding(arguments.run_input, ReadOnlyRunStore())
        body = None
        if arguments.json_file:
            supplied = Path(arguments.json_file)
            if not supplied.is_absolute() or supplied.parent != binding['folder']: fail('invalid_request_body')
            body = read_json(supplied)
        elif arguments.method == 'POST' and not arguments.file:
            body = {}
        shop_verifier = None
        if binding['data']['uploader']['kind'] == 'web':
            from page_manager.uploader_connection import UploaderConnection
            from page_manager.security import SecretBox
            shop_verifier = UploaderConnection(binding['store'], SecretBox(),initialize=False).shop_verified
        with provider_client(binding) as client:
            result = perform_request(binding, client, method=arguments.method, path=arguments.path, body=body, file=arguments.file, shop_verifier=shop_verifier)
        print(json.dumps(result, ensure_ascii=True));return 0
    except ClientError as error:
        print(json.dumps({'ok': False, 'code': error.code, 'needs_review': True, 'outcome_unknown': error.ambiguous}));return 2
    except sqlite3.Error:
        print(json.dumps({'ok': False, 'code': 'run_authority_unavailable', 'needs_review': True,
                          'outcome_unknown': arguments.method == 'POST'}));return 2
    except (KeyboardInterrupt, Exception):
        print(json.dumps({'ok': False, 'code': 'uploader_operation_stopped', 'needs_review': True, 'outcome_unknown': arguments.method == 'POST'}));return 2


if __name__ == '__main__':
    raise SystemExit(main())
