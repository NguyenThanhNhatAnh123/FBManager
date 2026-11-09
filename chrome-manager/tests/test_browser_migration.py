import json
from pathlib import Path
import sqlite3
import pytest
from filelock import FileLock
from app.core import browser_migration as migration
from app.core.config import Config


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    with sqlite3.connect(data / 'manager.sqlite3') as db:
        db.execute('CREATE TABLE profiles(id INTEGER PRIMARY KEY,name TEXT,profile_path TEXT,proxy_id INTEGER)')
        db.execute('CREATE TABLE proxies(id INTEGER PRIMARY KEY,encrypted_credentials BLOB)')
        db.execute('INSERT INTO proxies VALUES(1,?)', (b'opaque-encrypted-fixture',))
        for index in range(1, 101):
            path = tmp_path / 'profiles' / f'Chrome_{index:03d}'
            path.mkdir(parents=True)
            db.execute('INSERT INTO profiles VALUES(?,?,?,?)', (index, path.name, str(path), 1 if index == 1 else None))
    profile = tmp_path / 'profiles' / 'Chrome_001'
    (profile / 'Last Version').write_text('156.0.8078.4')
    network = profile / 'Default' / 'Network'
    network.mkdir(parents=True)
    (network / 'Cookies').write_bytes(b'opaque-browser-cookie-database-fixture')
    (profile / 'Local State').write_bytes(b'opaque-local-state-fixture')
    (tmp_path / '.env').write_text('CM_PORT=8000\nCM_BROWSER_CHANNEL=\nCM_HEADLESS=false\n')
    (data / 'authenticator-auto.json').write_text(json.dumps({'enabled': True, 'bundle': 'retained-software'}))
    monkeypatch.setattr(migration, 'installed_chrome', lambda: Path('C:/Program Files/Google/Chrome/Application/chrome.exe'))
    monkeypatch.setattr(migration, 'executable_version', lambda _: '156.0.8078.48')
    monkeypatch.setattr('app.core.profile_backup.psutil.process_iter', lambda _: [])
    monkeypatch.delenv('CM_BROWSER_CHANNEL', raising=False)
    return tmp_path, data, profile


def test_switch_preserves_all_profiles_proxy_credentials_and_cookie_files(workspace):
    root, data, profile = workspace
    database_before = (data / 'manager.sqlite3').read_bytes()
    files_before = {p.relative_to(profile): p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    report = migration.switch_to_standard_chrome(root, data)
    assert report['profile_count'] == 100 and report['assigned_proxies'] == 1
    assert report['profile_directories_reused'] and not report['browser_storage_edited']
    assert (data / 'manager.sqlite3').read_bytes() == database_before
    assert {p.relative_to(profile): p.read_bytes() for p in profile.rglob('*') if p.is_file()} == files_before
    backup = root / report['backup']
    assert backup.is_relative_to(root / '.runtime' / 'backups')
    assert (backup / 'profiles/1/Default/Network/Cookies').read_bytes() == files_before[Path('Default/Network/Cookies')]
    assert (backup / '.env').read_text().find('CM_BROWSER_CHANNEL=\n') >= 0
    assert json.loads((backup / 'authenticator-auto.json').read_text())['enabled'] is True
    assert json.loads((data / 'authenticator-auto.json').read_text())['enabled'] is False
    assert 'CM_PORT=8000' in (root / '.env').read_text()
    assert 'CM_BROWSER_CHANNEL=chrome' in (root / '.env').read_text()
    config = Config(root=root, data_dir=data, channel='chrome')
    assert config.channel == 'chrome' and config.authenticator_deployment is None


def test_running_server_blocks_switch_without_any_changes(workspace):
    root, data, _ = workspace
    before = (root / '.env').read_bytes()
    with FileLock(str(data / 'manager.lock')):
        with pytest.raises(ValueError, match='Stop the Manager server'):
            migration.switch_to_standard_chrome(root, data)
    assert (root / '.env').read_bytes() == before
    assert not (root / '.runtime').exists()


def test_browser_downgrade_is_refused_before_backup_or_configuration(workspace, monkeypatch):
    root, data, _ = workspace
    monkeypatch.setattr(migration, 'executable_version', lambda _: '155.0.8000.1')
    with pytest.raises(ValueError, match='older than an existing profile'):
        migration.switch_to_standard_chrome(root, data)
    assert json.loads((data / 'authenticator-auto.json').read_text())['enabled']
    assert not (root / '.runtime').exists()


def test_configuration_failure_restores_previous_mode_and_keeps_backup(workspace, monkeypatch):
    root, data, _ = workspace
    env_before, deployment_before = (root / '.env').read_bytes(), (data / 'authenticator-auto.json').read_bytes()
    original_write = migration.atomic_write
    calls = 0
    def fail_second_write(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('fixture disk failure')
        return original_write(path, content)
    monkeypatch.setattr(migration, 'atomic_write', fail_second_write)
    with pytest.raises(OSError):
        migration.switch_to_standard_chrome(root, data)
    assert (root / '.env').read_bytes() == env_before
    assert (data / 'authenticator-auto.json').read_bytes() == deployment_before
    assert len(list((root / '.runtime/backups').glob('before-standard-chrome-*'))) == 1


def test_repeated_launcher_does_not_create_repeated_profile_backups(workspace):
    root, data, _ = workspace
    first = migration.switch_to_standard_chrome(root, data)
    second = migration.switch_to_standard_chrome(root, data)
    assert second['already_configured'] and second['backup'] == first['backup']
    assert len(list((root / '.runtime/backups').glob('before-standard-chrome-*'))) == 1


def test_profile_outside_workspace_is_refused(workspace):
    root, data, _ = workspace
    with sqlite3.connect(data / 'manager.sqlite3') as db:
        db.execute('UPDATE profiles SET profile_path=? WHERE id=1', (str(root.parent / 'personal-chrome'),))
    with pytest.raises(ValueError, match='Unsafe profile'):
        migration.switch_to_standard_chrome(root, data)
    assert not (root / '.runtime').exists()
