"""Back up Manager data and select standard Chrome; never edit browser storage."""
import json
import os
import re
import sqlite3
from pathlib import Path
from filelock import FileLock, Timeout
from app.core.chrome_installation import installed_chrome, executable_version
from app.core.profile_backup import backup_before_chromium


def version_tuple(value):
    if not re.fullmatch(r'\d+(?:\.\d+){3}', value):
        raise ValueError('Invalid browser version metadata; migration was refused.')
    return tuple(map(int, value.split('.')))


def inspect_standard_chrome(root, data_dir):
    root, data_dir = Path(root).resolve(), Path(data_dir).resolve()
    if not data_dir.is_relative_to(root) or data_dir == root:
        raise ValueError('The data directory must be inside this Manager workspace.')
    executable = installed_chrome()
    if not executable:
        raise ValueError('Install Google Chrome from google.com/chrome before switching.')
    version = executable_version(executable)
    target = version_tuple(version)
    with sqlite3.connect((data_dir / 'manager.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
        profiles = list(connection.execute('SELECT id,name,profile_path,proxy_id FROM profiles ORDER BY id'))
    for _, _, raw_path, _ in profiles:
        path = Path(raw_path)
        if path.is_symlink() or not path.resolve().is_relative_to(root / 'profiles') or path.resolve() == root / 'profiles':
            raise ValueError('Unsafe profile directory; migration was refused.')
        last = path / 'Last Version'
        if last.is_file() and version_tuple(last.read_text(encoding='utf-8').strip()) > target:
            raise ValueError('Installed Chrome is older than an existing profile. Update Chrome first; data was not changed.')
    return {'browser': 'Google Chrome', 'executable': str(executable), 'version': version,
            'profile_count': len(profiles), 'assigned_proxies': sum(bool(row[3]) for row in profiles)}


def atomic_write(path, content):
    staging = path.with_name(path.name + '.migration-tmp')
    try:
        staging.write_bytes(content)
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def switch_to_standard_chrome(root, data_dir):
    root, data_dir = Path(root).resolve(), Path(data_dir).resolve()
    if not data_dir.is_relative_to(root) or data_dir == root:
        raise ValueError('The data directory must be inside this Manager workspace.')
    lock = FileLock(str(data_dir / 'manager.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ValueError('Stop the Manager server with Ctrl+C, then run use-standard-chrome.bat. No configuration was changed.') from None
    try:
        report = inspect_standard_chrome(root, data_dir)
        env_path, deployment_path = root / '.env', data_dir / 'authenticator-auto.json'
        if any(path.is_symlink() for path in (env_path, deployment_path)):
            raise ValueError('Configuration symlinks are not supported; migration was refused.')
        originals = {path: path.read_bytes() if path.is_file() else None for path in (env_path, deployment_path)}
        env = (originals[env_path] or b'').decode('utf-8-sig')
        lines = [line for line in env.splitlines() if not re.match(r'^\s*(?:export\s+)?CM_BROWSER_CHANNEL\s*=', line)]
        updated_env = ('\n'.join(lines).rstrip() + '\nCM_BROWSER_CHANNEL=chrome\n').lstrip('\n').encode('utf-8')
        deployment = json.loads(originals[deployment_path]) if originals[deployment_path] else None
        already_standard = (re.search(r'^CM_BROWSER_CHANNEL=chrome$', env, re.M)
                            and (not deployment or deployment.get('enabled') is not True))
        previous_report = data_dir / 'standard-chrome-migration.json'
        if already_standard and previous_report.is_file():
            report.update(already_configured=True, backup=json.loads(previous_report.read_text(encoding='utf-8'))['backup'])
            return report
        backup = root / backup_before_chromium(root, data_dir, target='standard-chrome')
        for path, contents in originals.items():
            if contents is not None:
                (backup / path.name).write_bytes(contents)
        report.update(backup=backup.relative_to(root).as_posix(), profile_directories_reused=True,
                      proxy_assignments_unchanged=True, browser_storage_edited=False,
                      authenticator_autoload=False)
        try:
            if deployment is not None:
                deployment['enabled'] = False
                atomic_write(deployment_path, json.dumps(deployment, indent=2).encode('utf-8'))
            atomic_write(env_path, updated_env)
            # Settings, credentials, profile directories and their contents are untouched.
            atomic_write(previous_report, json.dumps(report, indent=2).encode('utf-8'))
        except Exception:
            for path, contents in originals.items():
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, contents)
            raise
        return report
    finally:
        lock.release()
