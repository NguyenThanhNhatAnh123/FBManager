"""Persisted shop-check attestations using synthetic sessions and mock HTTP only."""
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from page_manager.errors import AppError
from page_manager.store import Store
from page_manager.uploader_connection import ENDPOINT, UploaderConnection
from test_uploader_connection import Box, COOKIE, PASSWORD, build, stored


def local_session(tmp_path, *, saved=True):
    calls = []
    def forbidden(request):
        calls.append(request)
        raise AssertionError('Attestation helpers must not contact a tool or shop')
    store = Store(tmp_path)
    value = UploaderConnection(store, Box(), transport=httpx.MockTransport(forbidden))
    if saved:
        encoded = json.dumps([{'name': 'fixture-session', 'value': COOKIE, 'path': '/',
                               'secure': True, 'expires': None}])
        with store.connect() as db:
            db.execute('UPDATE uploader_remote_session SET encrypted_cookies=? WHERE id=1',
                       (Box().encrypt(encoded),))
    return value, store, calls


def test_saved_tool_session_alone_never_attests_shop_or_calls_network(tmp_path):
    value, store, calls = local_session(tmp_path)
    before = stored(store)
    assert value.status() == {'endpoint': ENDPOINT, 'session_saved': True}
    assert value.shop_verified() is None
    assert stored(store) == before and calls == []


def test_explicit_true_and_false_are_persisted_across_connection_instances(tmp_path):
    value, store, calls = local_session(tmp_path)
    assert value.record_shop_check(True) is True
    assert value.shop_verified() is True
    fresh = UploaderConnection(store, Box(), transport=value.transport)
    assert fresh.shop_verified() is True
    assert value.record_shop_check(False) is False
    assert fresh.shop_verified() is False
    row = stored(store)
    assert row['checked_revision'] == value.session_revision()
    assert row['checked_ok'] == 0 and row['checked_at']
    assert set(value.status()) == {'endpoint', 'session_saved'}
    assert COOKIE not in json.dumps(value.status()) and calls == []


@pytest.mark.parametrize('ok', [1, 0, 'true', None, [], {}])
def test_shop_attestation_requires_an_exact_boolean(tmp_path, ok):
    value, store, calls = local_session(tmp_path)
    before = stored(store)
    with pytest.raises((AppError, ValueError)):
        value.record_shop_check(ok)
    assert stored(store) == before
    assert value.shop_verified() is None and calls == []


def test_missing_session_cannot_receive_a_positive_attestation(tmp_path):
    value, store, calls = local_session(tmp_path, saved=False)
    before = stored(store)
    with pytest.raises((AppError, ValueError)):
        value.record_shop_check(True)
    assert value.shop_verified() is None
    assert stored(store) == before and calls == []


@pytest.mark.parametrize('kind', ['expired', 'future', 'naive', 'malformed'])
def test_expired_future_or_invalid_check_timestamps_are_not_verified(tmp_path, kind):
    value, store, calls = local_session(tmp_path)
    value.record_shop_check(True)
    now = datetime.now(timezone.utc)
    stamp = ((now - timedelta(seconds=301)).isoformat() if kind == 'expired' else
             (now + timedelta(seconds=60)).isoformat() if kind == 'future' else
             now.replace(tzinfo=None).isoformat() if kind == 'naive' else 'PRIVATE INVALID TIMESTAMP')
    with store.connect() as db:
        db.execute('UPDATE uploader_remote_session SET checked_at=? WHERE id=1', (stamp,))
    assert value.shop_verified() is None
    assert calls == [] and COOKIE not in json.dumps(value.status())


def test_check_is_bound_to_the_current_session_revision_and_valid_cookie(tmp_path):
    value, store, calls = local_session(tmp_path)
    value.record_shop_check(True)
    with store.connect() as db:
        db.execute('UPDATE uploader_remote_session SET revision=revision+1 WHERE id=1')
    assert value.shop_verified() is None
    value.record_shop_check(True)
    assert value.shop_verified() is True
    with store.connect() as db:
        db.execute("UPDATE uploader_remote_session SET encrypted_cookies='PRIVATE INVALID CIPHER' WHERE id=1")
    assert value.shop_verified() is None
    assert calls == []


def test_successful_relogin_and_disconnect_invalidate_previous_shop_check(tmp_path):
    value, store, calls = build(tmp_path)
    assert value.connect('fixture-user', PASSWORD)['session_saved'] is True
    value.record_shop_check(True)
    assert value.shop_verified() is True
    revision = value.session_revision()
    value.connect('fixture-user', PASSWORD)
    assert value.session_revision() == revision + 1
    assert value.shop_verified() is None
    value.record_shop_check(True)
    value.disconnect()
    assert value.shop_verified() is None
    assert value.status()['session_saved'] is False
    assert not any(row.url.path.endswith('/api/session/check') for row in calls)


def test_failed_relogin_preserves_existing_valid_session_attestation(tmp_path):
    value, store, calls = build(tmp_path)
    value.connect('fixture-user', PASSWORD)
    value.record_shop_check(True)
    before = stored(store)
    rejected, _, _ = build(tmp_path, post_status=401)
    with pytest.raises(AppError):
        rejected.connect('fixture-user', PASSWORD)
    assert stored(store) == before
    assert value.shop_verified() is True
    assert not any(row.url.path.endswith('/api/session/check') for row in calls)


def test_old_session_table_migration_preserves_cookie_and_requires_a_new_check(tmp_path):
    store = Store(tmp_path)
    encoded = json.dumps([{'name': 'fixture-session', 'value': COOKIE, 'path': '/',
                           'secure': True, 'expires': None}])
    cipher = Box().encrypt(encoded)
    with store.connect() as db:
        db.execute('''CREATE TABLE uploader_remote_session(
            id INTEGER PRIMARY KEY CHECK(id=1), encrypted_cookies TEXT,
            revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT)''')
        db.execute('INSERT INTO uploader_remote_session(id,encrypted_cookies,revision) VALUES(1,?,7)', (cipher,))
    def forbidden(request):
        raise AssertionError('Migration must not check a real shop')
    value = UploaderConnection(store, Box(), transport=httpx.MockTransport(forbidden))
    row = stored(store)
    assert row['encrypted_cookies'] == cipher and row['revision'] == 7
    assert {'checked_revision', 'checked_at', 'checked_ok'} <= set(row)
    assert all(row[key] is None for key in ('checked_revision', 'checked_at', 'checked_ok'))
    assert value.status()['session_saved'] is True and value.shop_verified() is None
    value.record_shop_check(True)
    assert value.shop_verified() is True


@pytest.mark.parametrize('old_result', [True, False])
def test_probe_for_old_login_cannot_attest_or_revoke_new_login(tmp_path, old_result):
    value, store, _ = build(tmp_path)
    value.connect('fixture-user', PASSWORD)
    old_revision = value.session_revision()
    value.connect('fixture-user', PASSWORD)
    new_revision = value.session_revision()
    assert new_revision != old_revision and value.shop_verified() is None
    if old_result is False:
        value.record_shop_check(True, expected_revision=new_revision)
    before = stored(store)
    with pytest.raises(AppError) as caught:
        value.record_shop_check(old_result, expected_revision=old_revision)
    assert caught.value.code == 'uploader_connection_changed' and caught.value.status_code == 409
    assert stored(store) == before
    assert value.shop_verified() is (True if old_result is False else None)


def test_revision_change_during_cookie_validation_is_rejected_by_update_cas(tmp_path, monkeypatch):
    value, store, calls = local_session(tmp_path)
    starting_revision = value.session_revision()
    other = UploaderConnection(store, Box(), transport=value.transport)
    original = value._saved_cookies
    def disconnect_after_cookie_read(row=None):
        result = original(row)
        other.disconnect()
        return result
    monkeypatch.setattr(value, '_saved_cookies', disconnect_after_cookie_read)
    with pytest.raises(AppError) as caught:
        value.record_shop_check(True, expected_revision=starting_revision)
    assert caught.value.code == 'uploader_connection_changed'
    assert stored(store)['checked_revision'] is None and stored(store)['checked_ok'] is None
    assert other.shop_verified() is None and calls == []


@pytest.mark.parametrize('revision', [True, False, 0, -1, '1', 1.0, []])
def test_expected_probe_revision_must_be_an_exact_positive_integer(tmp_path, revision):
    value, store, calls = local_session(tmp_path)
    before = stored(store)
    with pytest.raises(AppError) as caught:
        value.record_shop_check(True, expected_revision=revision)
    assert caught.value.status_code == 422 and caught.value.code == 'invalid_uploader_shop_check'
    assert stored(store) == before and calls == []


def test_matching_probe_revision_attests_that_login_without_extra_network(tmp_path):
    value, store, calls = local_session(tmp_path)
    revision = value.session_revision()
    assert value.record_shop_check(True, expected_revision=revision) is True
    row = stored(store)
    assert row['checked_revision'] == revision and value.shop_verified() is True
    assert calls == []
