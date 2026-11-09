"""Reel launch routing without opening Chrome, networking or real profiles."""
from types import SimpleNamespace

import pytest
from filelock import FileLock

from app.core.config import Config
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import Profile, Proxy
from app.services import browsers as module
from app.services.browsers import BrowserManager
from app.services.state import State


def immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in an offline fixture')


@pytest.fixture
def manager(tmp_path):
    config = Config(root=tmp_path, data_dir=tmp_path / 'data', headless=False)
    config.prepare()
    db = Database(config.data_dir / 'manager.sqlite3')
    state = State(db)
    state.save_settings(Settings(require_proxy=True, proxy_routing='all'))
    browser = BrowserManager(db, state, config, SimpleNamespace(blocked_reason=lambda _: None))
    directory = config.profiles_dir / 'ReelFixture101'
    directory.mkdir()
    (directory / 'retained-session-fixture.txt').write_text('existing fixture session', encoding='utf-8')
    with db.session.begin() as session:
        session.add(Proxy(id=21, host='192.0.2.21', port=12021, protocol='http',
                          identity='fixture-reel-proxy', status='Working'))
        session.flush()
        session.add(Profile(id=101, name='ReelFixture101', profile_path=str(directory), proxy_id=21))
    yield browser
    for lock in browser.locks.values():
        if lock.is_locked:
            lock.release()
    db.engine.dispose()


def fake_launch(manager, monkeypatch):
    calls = {'persistent': [], 'manual': []}

    class Context:
        def on(self, *_):
            pass

        async def close(self):
            calls['closed'] = True

    async def persistent(path, **options):
        calls['persistent'].append((path, options))
        return Context()

    class Manual(Context):
        def __init__(self, config, driver, path, proxy, args, log):
            calls['manual'].append({'path': str(path), 'proxy': proxy, 'args': args,
                                    'configured_headless': config.headless})

        async def start(self, open_start_page):
            calls['manual_start'] = open_start_page
            return self

    async def wait(awaitable, timeout):
        return await awaitable

    manager.playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=persistent))
    monkeypatch.setattr(module, 'ManualBrowser', Manual)
    monkeypatch.setattr(module, 'BrowserNetworkDiagnostics', lambda *_: None)
    monkeypatch.setattr(module.asyncio, 'wait_for', wait)
    return calls


@pytest.mark.parametrize('purpose', ['reel', 'facebook', 'outlook', 'automation', 'manual'])
def test_request_start_forwards_reel_flag_only_for_reel(manager, monkeypatch, purpose):
    calls = []

    def launch(pid, **options):
        calls.append((pid, options))

        async def pending():
            return None

        return pending()

    class Job:
        def add_done_callback(self, _):
            pass

    def task(coroutine):
        coroutine.close()
        return Job()

    monkeypatch.setattr(manager, '_launch', launch)
    monkeypatch.setattr(module.asyncio, 'create_task', task)
    options = {'for_automation': purpose != 'manual', 'open_start_page': purpose == 'manual',
               'timezone_id': 'America/Los_Angeles'}
    if purpose in ('reel', 'facebook', 'outlook'):
        options['for_' + purpose] = True
    assert manager.request_start(101, **options) == 'Queued'
    pid, forwarded = calls[0]
    assert pid == 101
    assert forwarded.get('for_reel', False) is (purpose == 'reel')
    assert forwarded.get('for_facebook', False) is (purpose == 'facebook')
    assert forwarded['for_automation'] is (purpose != 'manual')
    assert forwarded['open_start_page'] is (purpose == 'manual')
    assert forwarded['timezone_id'] == 'America/Los_Angeles'
    assert manager.mode(101) == ('manual' if purpose == 'manual' else 'automation')


def test_ensure_open_keeps_exact_owned_profile_context_and_reel_flag(manager, monkeypatch):
    calls = []
    context = object()
    manager.contexts[101] = context
    manager.modes[101] = 'automation'
    manager.reel_profiles.add(101)
    monkeypatch.setattr(manager, 'request_start', lambda pid, **options: calls.append((pid, options)))
    assert immediate(manager.ensure_open(101, for_automation=True, for_reel=True,
                                         timezone_id='America/Los_Angeles')) is context
    assert calls == [(101, {'open_start_page': False, 'for_automation': True, 'for_facebook': False,
                           'for_outlook': False, 'for_recipient': False, 'for_reel': True,
                           'timezone_id': 'America/Los_Angeles'})]
    assert manager.reel_profiles == {101}


@pytest.mark.parametrize('configured_headless', [False, True])
def test_reel_launch_is_hidden_with_same_saved_session_proxy_timezone_and_lock(manager, monkeypatch, configured_headless):
    manager.config.headless = configured_headless
    manager.reel_profiles.add(101)
    calls = fake_launch(manager, monkeypatch)
    context = immediate(manager._launch(101, open_start_page=False, for_automation=True,
                                        for_reel=True, timezone_id='America/Los_Angeles'))
    assert not calls['manual'] and len(calls['persistent']) == 1
    path, options = calls['persistent'][0]
    assert options['headless'] is True
    assert options['proxy'] == {'server': 'http://192.0.2.21:12021'}
    assert options['timezone_id'] == 'America/Los_Angeles'
    assert options['service_workers'] == 'block'
    assert options['ignore_default_args'] == ['--disable-extensions']
    assert '--proxy-bypass-list=<-loopback>' in options['args']
    assert path == str(manager.config.profiles_dir / 'ReelFixture101')
    assert manager.contexts[101] is context
    assert manager.locks[101].is_locked
    assert str(manager.locks[101].lock_file) == str(manager.config.profiles_dir / 'ReelFixture101' / '.manager.lock')
    assert manager.reel_profiles == {101}
    assert (manager.config.profiles_dir / 'ReelFixture101' / 'retained-session-fixture.txt').read_text(encoding='utf-8') == 'existing fixture session'
    with manager.db.session() as session:
        profile = session.get(Profile, 101)
        assert profile.profile_path == path and profile.proxy_id == 21


@pytest.mark.parametrize('configured_headless', [False, True])
def test_nonreel_automation_still_uses_global_headless_setting(manager, monkeypatch, configured_headless):
    manager.config.headless = configured_headless
    calls = fake_launch(manager, monkeypatch)
    immediate(manager._launch(101, open_start_page=False, for_automation=True))
    assert len(calls['persistent']) == 1 and not calls['manual']
    assert calls['persistent'][0][1]['headless'] is configured_headless


@pytest.mark.parametrize('configured_headless', [False, True])
def test_normal_open_preserves_manual_browser_and_start_page(manager, monkeypatch, configured_headless):
    manager.config.headless = configured_headless
    calls = fake_launch(manager, monkeypatch)
    immediate(manager._launch(101, open_start_page=True))
    assert not calls['persistent'] and len(calls['manual']) == 1
    assert calls['manual'][0]['configured_headless'] is configured_headless
    assert calls['manual'][0]['path'] == str(manager.config.profiles_dir / 'ReelFixture101')
    assert calls['manual'][0]['proxy'] == {'server': 'http://192.0.2.21:12021'}
    assert calls['manual_start'] is True


def test_headless_reel_respects_existing_profile_lock_and_never_launches(manager, monkeypatch):
    calls = fake_launch(manager, monkeypatch)
    lock = FileLock(str(manager.config.profiles_dir / 'ReelFixture101' / '.manager.lock'))
    with lock:
        with pytest.raises(RuntimeError, match='locked by another manager'):
            immediate(manager._launch(101, open_start_page=False, for_automation=True, for_reel=True))
        assert lock.is_locked
        assert not calls['persistent'] and not calls['manual']
        assert 101 not in manager.contexts and 101 not in manager.reserved


@pytest.mark.parametrize('for_automation', [False, True])
def test_reel_owned_profile_still_blocks_unrelated_open(manager, for_automation):
    manager.reel_profiles.add(101)
    with pytest.raises(ValueError, match='Reel'):
        manager.request_start(101, for_automation=for_automation)
    assert not manager.launches and not manager.contexts
    assert manager.reel_profiles == {101}
