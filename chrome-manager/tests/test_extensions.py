"""Per-profile metadata and scope checks; never read OTP storage or change Windows policy."""
import json
from types import SimpleNamespace
import pytest
from app.services.extensions import EXTENSION_ID, STORE_URL, ExtensionManager, inspect_authenticator
from app.core.config import Config
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import Profile
from app.services.state import State
from app.services.resources import Resources
from app.services.browsers import BrowserManager
from app.automation.manager import TaskManager


def installed_files(root, name='Chrome_001', state=None, disable_reasons=None):
    profile = root / name
    extension = profile / 'Default' / 'Extensions' / EXTENSION_ID / '8.0.1_0'
    extension.mkdir(parents=True)
    (extension / 'manifest.json').write_text(json.dumps({'version': '8.0.1', 'manifest_version': 3}))
    if state is not None:
        row = {'state': state, 'disable_reasons': disable_reasons or []}
        (profile / 'Default' / 'Secure Preferences').write_text(json.dumps({'extensions': {'settings': {EXTENSION_ID: row}}}))
    return profile


def test_files_alone_do_not_claim_installed_or_runtime_verified(tmp_path):
    profile = installed_files(tmp_path)
    result = inspect_authenticator(profile, tmp_path)
    assert result == {'status': 'Files detected', 'runtime_verified': False, 'files_detected': True, 'versions': ['8.0.1']}


def test_installation_metadata_does_not_claim_runtime_verified(tmp_path):
    profile = installed_files(tmp_path, state=1)
    assert inspect_authenticator(profile, tmp_path)['status'] == 'Installed'
    assert not inspect_authenticator(profile, tmp_path)['runtime_verified']
    second = tmp_path / 'Chrome_002'
    second.mkdir()
    assert inspect_authenticator(second, tmp_path)['status'] == 'Not installed'


@pytest.mark.parametrize('state,reasons', [(0, []), (1, [1]), (1, [4, 16])])
def test_disabled_extension_is_not_reported_as_installed(tmp_path, state, reasons):
    profile = installed_files(tmp_path, state=state, disable_reasons=reasons)
    assert inspect_authenticator(profile, tmp_path)['status'] == 'Disabled'


def test_runtime_verification_requires_exact_extension_origin(tmp_path):
    profile = tmp_path / 'Chrome_001'
    profile.mkdir()
    foreign = SimpleNamespace(service_workers=[SimpleNamespace(url=f'https://example.com/{EXTENSION_ID}/worker.js')])
    assert not inspect_authenticator(profile, tmp_path, foreign)['runtime_verified']
    extension = SimpleNamespace(service_workers=[SimpleNamespace(url=f'chrome-extension://{EXTENSION_ID}/dist/background.js')])
    assert inspect_authenticator(profile, tmp_path, extension)['status'] == 'Verified'


def test_refuse_non_manager_paths(tmp_path):
    root = tmp_path / 'profiles'
    root.mkdir()
    with pytest.raises(ValueError):
        inspect_authenticator(tmp_path, root)
    with pytest.raises(ValueError):
        inspect_authenticator(root, root)


def test_corrupt_preferences_and_manifest_do_not_break_inventory(tmp_path):
    profile = installed_files(tmp_path, state=1)
    (profile / 'Default' / 'Secure Preferences').write_text('{broken')
    assert inspect_authenticator(profile, tmp_path)['status'] == 'Files detected'
    (profile / 'Default' / 'Extensions' / EXTENSION_ID / '8.0.1_0' / 'manifest.json').write_text('[]')
    assert inspect_authenticator(profile, tmp_path)['status'] == 'Not installed'


def test_inventory_does_not_expose_unrelated_preferences(tmp_path):
    profile = installed_files(tmp_path, state=1)
    prefs = profile / 'Default' / 'Secure Preferences'
    value = json.loads(prefs.read_text())
    value['account_info'] = [{'email': 'private-account@example.test'}]
    prefs.write_text(json.dumps(value))
    result = json.dumps(inspect_authenticator(profile, tmp_path))
    assert 'private-account' not in result
    assert set(json.loads(result)) == {'status', 'runtime_verified', 'files_detected', 'versions'}


def test_setup_keeps_proxy_requirement_and_declines_task_in_progress(tmp_path):
    config = Config(root=tmp_path, data_dir=tmp_path / 'data')
    config.prepare()
    db = Database(config.data_dir / 'manager.sqlite3')
    try:
        state = State(db)
        browsers = BrowserManager(db, state, config, Resources())
        tasks = TaskManager(db, state, browsers, config)
        manager = ExtensionManager(db, state, config, browsers, tasks)
        with db.session.begin() as session:
            session.add(Profile(id=1, name='Chrome_001', profile_path=str(config.profiles_dir / 'Chrome_001')))
        with pytest.raises(ValueError, match='Assign a proxy'):
            manager.request_setup(1)
        assert not manager.jobs and not browsers.extension_profiles
        assert state.settings.require_proxy is True
        tasks.profile_jobs[1] = 99
        with pytest.raises(ValueError, match='Stop the automation'):
            manager.request_setup(1)
        config.headless = True
        with pytest.raises(ValueError, match='visible Chrome'):
            manager.request_setup(1)
        assert STORE_URL == 'https://chromewebstore.google.com/detail/authenticator/bhghoamapcdpbohphigoooaddinpkbai'
    finally:
        db.engine.dispose()


def test_navigation_errors_expose_codes_without_credentials():
    from app.services.extensions import web_store_error_message
    from playwright.async_api import TimeoutError as BrowserTimeout
    assert '45 seconds' in web_store_error_message(BrowserTimeout('private-test-secret'))
    message=web_store_error_message(RuntimeError('https://user:private-test-secret@example.test net::ERR_TUNNEL_CONNECTION_FAILED'))
    assert 'ERR_TUNNEL_CONNECTION_FAILED' in message
    assert 'private-test-secret' not in message and 'example.test' not in message


def test_modern_chrome_install_metadata_without_legacy_state(tmp_path):
    profile=installed_files(tmp_path)
    row={'manifest':{'version':'8.0.1'},'location':1,'disable_reasons':[],'from_webstore':True}
    (profile/'Default'/'Secure Preferences').write_text(json.dumps({'extensions':{'settings':{EXTENSION_ID:row}}}))
    result=inspect_authenticator(profile,tmp_path)
    assert result['status']=='Installed' and not result['runtime_verified']
