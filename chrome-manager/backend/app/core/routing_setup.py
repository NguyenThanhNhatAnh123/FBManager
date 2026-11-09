"""Persist user-selected domain routing while the Manager is stopped."""
import json
from pathlib import Path
import sqlite3
import uuid
import psutil
from filelock import FileLock, Timeout
from app.core.schemas import Settings


def enable_facebook_only(root, data_dir):
    root, data_dir = Path(root).resolve(), Path(data_dir).resolve()
    if not data_dir.is_relative_to(root) or data_dir == root:
        raise ValueError('Unsafe Manager data directory.')
    lock = FileLock(str(data_dir / 'manager.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ValueError('Stop the Manager server with Ctrl+C before running start-facebook-proxy-only.bat.') from None
    try:
        database = data_dir / 'manager.sqlite3'
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            rows = list(connection.execute('SELECT profile_path,proxy_id FROM profiles'))
            paths = {Path(row[0]).resolve() for row in rows}
            previous = connection.execute('SELECT value FROM settings WHERE id=1').fetchone()
        if previous is None:
            raise ValueError('Start the Manager once before enabling Facebook-only routing.')
        for process in psutil.process_iter(['name', 'cmdline']):
            try:
                if (process.info.get('name') or '').lower() not in ('chrome.exe', 'chromium.exe'):
                    continue
                if any(a.startswith('--user-data-dir=') and Path(a.split('=', 1)[1].strip('"')).resolve() in paths for a in process.info.get('cmdline') or []):
                    raise ValueError('A Manager browser is still open. Close its window before changing routing.')
            except (psutil.Error, OSError):
                continue
        settings = Settings.model_validate_json(previous[0])
        if settings.proxy_routing == 'facebook_only':
            return {'proxy_routing': 'facebook_only', 'already_enabled': True}
        backup = root / '.runtime/backups' / ('before-facebook-routing-' + uuid.uuid4().hex)
        backup.mkdir(parents=True, exist_ok=False)
        (backup / 'settings.json').write_text(previous[0], encoding='utf-8')
        settings.proxy_routing = 'facebook_only'
        with sqlite3.connect(database) as connection:
            connection.execute('UPDATE settings SET value=? WHERE id=1', (settings.model_dump_json(),))
        return {'proxy_routing': 'facebook_only', 'settings_backup': backup.relative_to(root).as_posix(),
                'profile_count': len(rows), 'assigned_proxies': sum(row[1] is not None for row in rows),
                'profiles_and_proxies_unchanged': True}
    finally:
        lock.release()
