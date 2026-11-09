import json
import sqlite3
from types import SimpleNamespace
import pytest
from filelock import FileLock
from app.core.routing_setup import enable_facebook_only


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    profile = tmp_path / 'profiles/Chrome_001'
    profile.mkdir(parents=True)
    (profile / 'opaque-data').write_bytes(b'fixture-saved-browser-data')
    with sqlite3.connect(data / 'manager.sqlite3') as db:
        db.execute('CREATE TABLE profiles(id INTEGER PRIMARY KEY,profile_path TEXT,proxy_id INTEGER)')
        db.execute('INSERT INTO profiles VALUES(1,?,7)', (str(profile),))
        db.execute('CREATE TABLE proxies(id INTEGER PRIMARY KEY,encrypted_credentials BLOB)')
        db.execute('INSERT INTO proxies VALUES(7,?)', (b'opaque-fixture-credentials',))
        db.execute('CREATE TABLE settings(id INTEGER PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO settings VALUES(1,?)', (json.dumps({'max_browsers': 5, 'proxy_ip_version': 'ipv6'}),))
    monkeypatch.setattr('app.core.routing_setup.psutil.process_iter', lambda _: [])
    return tmp_path, data, profile


def test_enabling_only_changes_routing_and_retains_profiles_and_encrypted_proxy_credentials(workspace):
    root, data, profile = workspace
    report = enable_facebook_only(root, data)
    with sqlite3.connect(data / 'manager.sqlite3') as db:
        settings = json.loads(db.execute('SELECT value FROM settings').fetchone()[0])
        assert settings['proxy_routing'] == 'facebook_only'
        assert settings['max_browsers'] == 5 and settings['proxy_ip_version'] == 'ipv6'
        assert db.execute('SELECT proxy_id FROM profiles').fetchone()[0] == 7
        assert db.execute('SELECT encrypted_credentials FROM proxies').fetchone()[0] == b'opaque-fixture-credentials'
    assert (profile / 'opaque-data').read_bytes() == b'fixture-saved-browser-data'
    assert json.loads((root / report['settings_backup'] / 'settings.json').read_text()) == {'max_browsers': 5, 'proxy_ip_version': 'ipv6'}
    assert enable_facebook_only(root, data)['already_enabled']


def test_running_server_prevents_routing_change(workspace):
    root, data, _ = workspace
    with FileLock(str(data / 'manager.lock')):
        with pytest.raises(ValueError, match='Stop the Manager server'):
            enable_facebook_only(root, data)
    assert not (root / '.runtime').exists()


def test_open_profile_prevents_routing_change(workspace, monkeypatch):
    root, data, profile = workspace
    monkeypatch.setattr('app.core.routing_setup.psutil.process_iter', lambda _: [SimpleNamespace(info={'name':'chrome.exe','cmdline':['chrome.exe','--user-data-dir='+str(profile)]})])
    with pytest.raises(ValueError, match='browser is still open'):
        enable_facebook_only(root, data)
    assert not (root / '.runtime').exists()
