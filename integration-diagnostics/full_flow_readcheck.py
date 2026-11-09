"""Read live health and queue metadata; never start workers or contact Meta."""
import collections
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.request
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'via-auth'))
from via_auth.client import local_opener


BASE = 'http://127.0.0.1:8000'
SAFE = {
    'ready', 'available', 'installed', 'enabled', 'configured', 'configured_blueox',
    'active_domain', 'authenticated', 'selected_provider', 'provider', 'kind',
    'blockers', 'reason', 'status', 'stage', 'mode', 'supported', 'cli_configured',
    'runtime_checked', 'verified', 'workflow_version', 'workflow_sha256',
    'schema_version', 'checked_at', 'expires_at', 'max_age_seconds', 'age_seconds',
    'configuration', 'runtime', 'attestation', 'execution', 'video_agent', 'upload',
    'providers', 'local', 'web', 'connection_saved', 'login_diagnostic',
    'page_count', 'daily_time', 'timezone', 'stats', 'active_count', 'running_browsers',
    'queued', 'starting', 'running_tasks', 'pending_tasks', 'cli_version',
    'last_error_code', 'last_scan_at', 'last_success_at', 'last_error_at', 'interval_seconds',
    'attestation_status', 'runtime_check', 'settings', 'daily_publish_time', 'publish_timezone',
    'scan_interval_seconds', 'max_imports_per_scan', 'scheduler', 'running',
    'queued_browsers', 'starting_browsers',
}


def safe(value):
    if isinstance(value, dict):
        return {k: safe(v) for k, v in value.items() if k in SAFE}
    if isinstance(value, list):
        return [safe(v) for v in value[:32]]
    return value


def main():
    opener = local_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def get(path):
        with opener.open(BASE + path, timeout=20) as response:
            return json.load(response)
    get('/api/session')
    get('/pages/api/session')
    report = {'read_only': True, 'endpoints': {}}
    for label, path in {
        'pipeline': '/pages/api/idea-pipeline',
        'uploader': '/pages/api/idea-pipeline/upload/status',
        'source': '/pages/api/video-source',
        'chrome_system': '/api/system/status',
        'chrome_reels': '/api/reels',
        'page_reels': '/pages/api/reels',
        'connections': '/pages/api/connections',
        'automation': '/pages/api/idea-pipeline/automation',
        'producers': '/pages/api/idea-pipeline/producers',
    }.items():
        try:
            value = get(path)
            projected = safe(value) if isinstance(value, dict) else {}
            projected['_keys'] = sorted(value) if isinstance(value, dict) else ['list']
            if label.endswith('reels'):
                rows = value if isinstance(value, list) else next((value[k] for k in ('jobs', 'items', 'reels') if isinstance(value.get(k), list)), [])
                projected['queue_status_counts'] = dict(collections.Counter(r.get('status', 'unknown') for r in rows))
            if label == 'source' and isinstance(value.get('config'), dict):
                projected['config'] = safe(value['config'])
                projected['config']['selected_pages'] = len(value['config'].get('page_ids', []))
            if label == 'source':
                settings = value.get('settings', {})
                projected['selected_page_count'] = len(settings.get('page_ids', []))
                for field in ('posting_eligible_page_ids', 'posting_blocked_page_ids'):
                    projected[field + '_count'] = len(value.get(field, []))
                projected['stats'] = value.get('stats', {})
                projected['has_error'] = bool(value.get('last_error'))
            if label == 'pipeline':
                rows = value.get('runs', [])
                if isinstance(rows, dict):
                    rows = rows.get('items', [])
                projected['run_status_counts'] = dict(collections.Counter(r.get('status', 'unknown') for r in rows))
            if label == 'connections':
                rows = value.get('items', [])
                projected['connection_count'] = len(rows)
                projected['status_counts'] = dict(collections.Counter(r.get('status', 'unknown') for r in rows))
                projected['checks'] = [{k: row.get(k) for k in ('status', 'has_token', 'connected', 'page_count', 'checked_at', 'synced_at', 'expires_at')} for row in rows]
            if label=='automation':
                projected={k:value.get(k) for k in ('state','blockers','checked_at','coalesce_seconds')}
                projected['settings']={k:value.get('settings',{}).get(k) for k in ('enabled','revision')}
                preview=value.get('preview',{})
                projected['preview']={k:preview.get(k) for k in ('capacity','candidate_count','total_videos','deferred_count','blocker_codes')}
                projected['preview']['allocated_counts']=[v.get('variant_count') for v in preview.get('allocations',[])]
                batch=value.get('active_batch')
                projected['active_batch']=None if not batch else {'status':batch.get('status'),'items':len(batch.get('items',[]))}
            if label=='producers':
                projected={k:value.get(k) for k in ('primary','backup','fallback')}
                projected['providers']={name:{k:provider.get(k) for k in ('installed','authenticated','status','version','checked_at','expires_at','image_generation','production_verified')}
                    for name,provider in value.get('providers',{}).items() if name in ('codex','claude')}
            report['endpoints'][label] = projected
        except (OSError, ValueError) as exc:
            report['endpoints'][label] = {'error_type': type(exc).__name__, 'http_status': getattr(exc, 'code', None)}
    output = Path(__file__).parent / '.runtime' / 'full-flow-live-status.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
