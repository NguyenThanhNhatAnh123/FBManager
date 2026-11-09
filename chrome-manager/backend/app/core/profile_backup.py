"""Workspace-bounded backup before a Manager browser-engine change."""
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
import psutil


def backup_io_path(path):
    # Extended-length paths avoid MAX_PATH failures for deep Chrome storage.
    value = str(Path(path).resolve())
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


def backup_before_chromium(root, data_dir, *, target='chromium'):
    if target not in ('chromium', 'standard-chrome'):
        raise ValueError('Unsupported browser backup target.')
    root, data_dir = Path(root).resolve(), Path(data_dir).resolve()
    profiles_root = (root / 'profiles').resolve()
    database = data_dir / 'manager.sqlite3'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        rows = list(connection.execute('SELECT id,profile_path FROM profiles ORDER BY id'))
    paths = {}
    for profile_id, raw_path in rows:
        source = Path(raw_path)
        if source.is_symlink() or not source.resolve().is_relative_to(profiles_root) or source.resolve() == profiles_root:
            raise ValueError('Unsafe profile path; browser mode was not changed.')
        paths[profile_id] = source.resolve()
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            if (process.info.get('name') or '').lower() not in ('chrome.exe', 'chromium.exe'):
                continue
            for argument in process.info.get('cmdline') or []:
                if argument.startswith('--user-data-dir=') and Path(argument.split('=', 1)[1].strip('"')).resolve() in paths.values():
                    raise ValueError('A Manager profile is still open. Close its browser before changing the browser engine.')
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    backup = root / '.runtime' / 'backups' / ('before-' + target + '-' + uuid.uuid4().hex)
    if not backup.resolve().is_relative_to(root):
        raise ValueError('Unsafe backup directory.')
    backup.mkdir(parents=True, exist_ok=False)
    for profile_id, source in paths.items():
        if source.is_dir():
            shutil.copytree(backup_io_path(source), backup_io_path(backup / 'profiles' / str(profile_id)), symlinks=True,
                            ignore=shutil.ignore_patterns('Cache', 'Code Cache', 'GPUCache', 'ShaderCache', 'GrShaderCache', 'DawnGraphiteCache', 'Crashpad', 'BrowserMetrics'))
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source_db:
        with sqlite3.connect(backup / 'manager.sqlite3') as target_db:
            source_db.backup(target_db)
    (backup / 'manifest.json').write_text(json.dumps({'profiles': {str(pid): str(path.relative_to(root)) for pid, path in paths.items()},
                                                     'cache_directories_excluded': True}, indent=2), encoding='utf-8')
    return backup.relative_to(root).as_posix()
