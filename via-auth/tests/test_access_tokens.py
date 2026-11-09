"""Real temporary identities/PATs and fake HTTP targets; no manager data touched."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from via_auth import IdentityGate, IdentityStore
from via_auth.access_tokens import TOKEN_PATTERN, valid_audience
from via_auth.middleware import COOKIE
from via_auth.store import IdentityError

pytestmark = pytest.mark.identity
AUDIENCE = 'https://via.example.test/mcp'
PASSWORD = 'Private temporary fixture password 2026'


@pytest.fixture
def identities(tmp_path):
    clock = [2_000_000_000]
    store = IdentityStore(tmp_path / 'identity', clock=lambda: clock[0])
    credentials = store.bootstrap()
    sessions = {}
    for item in credentials:
        user = next(row for row in store.list_users() if row['username'] == item['username'])
        raw = store.change_password(user['id'], item['password'], PASSWORD)
        sessions[item['username']] = raw
    return store, sessions, clock


def issue(store, username='operator1', scopes=None, **kwargs):
    user = next(row for row in store.list_users() if row['username'] == username)
    return store.create_access_token(user['id'], name='Fixture Codex',
        scopes=['via:read'] if scopes is None else scopes, audience=AUDIENCE,
        expected_revision=user['revision'], **kwargs)


def test_all_three_users_have_separate_hash_only_tokens_and_owner_lists(identities):
    store, _, _ = identities
    tokens = {user['username']: issue(store, user['username']) for user in store.list_users()}
    assert len({item['token'] for item in tokens.values()}) == 3
    for user in store.list_users():
        item = tokens[user['username']]
        assert TOKEN_PATTERN.fullmatch(item['token'])
        actor = store.resolve_access_token(item['token'], audience=AUDIENCE)
        assert (actor['id'], actor['username'], actor['role']) == (user['id'], user['username'], user['role'])
        assert actor['auth_method'] == 'mcp_token' and actor['scopes'] == ['via:read']
        listed = store.list_access_tokens(user['id'])
        assert [row['id'] for row in listed] == [item['id']]
        assert 'token' not in listed[0] and 'digest' not in listed[0]
    with store.connect() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM mcp_access_tokens')]
    encoded = json.dumps(rows)
    for item in tokens.values():
        assert item['token'] not in encoded and item['token'].encode() not in store.path.read_bytes()
        assert hashlib.sha256(item['token'].encode()).hexdigest() in encoded


def test_initial_passwords_cannot_issue_tokens(tmp_path):
    store = IdentityStore(tmp_path / 'new-identity')
    store.bootstrap()
    for user in store.list_users():
        with pytest.raises(IdentityError, match='password_change_required'):
            store.create_access_token(user['id'], name='Blocked', scopes=['via:read'], audience=AUDIENCE)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM mcp_access_tokens').fetchone()[0] == 0


def test_ownership_and_revocation_are_separate_even_for_admin(identities):
    store, _, _ = identities
    first, second = issue(store, 'operator1'), issue(store, 'operator2')
    users = {user['username']: user for user in store.list_users()}
    for name in ('admin', 'operator2'):
        with pytest.raises(IdentityError) as error:
            store.revoke_access_token(users[name]['id'], first['id'])
        assert (error.value.code, error.value.status) == ('token_not_found', 404)
    assert store.resolve_access_token(first['token'])
    store.revoke_access_token(users['operator1']['id'], first['id'])
    store.revoke_access_token(users['operator1']['id'], first['id'])
    assert store.resolve_access_token(first['token']) is None
    assert store.resolve_access_token(second['token'])['username'] == 'operator2'


@pytest.mark.parametrize('change', ['password', 'username', 'role', 'disable', 'reset_password', 'revoke_sessions'])
def test_security_changes_invalidate_issued_tokens(identities, change):
    store, _, _ = identities
    item = issue(store)
    actor = store.resolve_access_token(item['token'])
    if change == 'password':
        store.change_password(actor['id'], PASSWORD, PASSWORD + ' changed')
    else:
        values = {'revision': actor['revision']}
        values.update({'username': 'renamed-operator'} if change == 'username' else
                      {'role': 'admin'} if change == 'role' else
                      {'active': False} if change == 'disable' else {change: True})
        store.update_user(1, actor['id'], values)
    assert store.resolve_access_token(item['token'], audience=AUDIENCE) is None
    assert store.list_access_tokens(actor['id'])[0]['valid_revision'] is False


def test_expiry_audience_and_last_used_throttle(identities):
    store, _, clock = identities
    item = issue(store, days=1)
    actor = store.resolve_access_token(item['token'], audience=AUDIENCE)
    assert actor['expires_at'] == clock[0] + 86400
    assert store.resolve_access_token(item['token'], audience='https://other.example.test/mcp') is None
    assert store.resolve_access_token(item['token'] + 'changed') is None
    assert store.list_access_tokens(actor['id'])[0]['last_used_at'] == clock[0]
    clock[0] += 59
    assert store.resolve_access_token(item['token'])
    assert store.list_access_tokens(actor['id'])[0]['last_used_at'] == clock[0] - 59
    clock[0] += 1
    assert store.resolve_access_token(item['token'])
    assert store.list_access_tokens(actor['id'])[0]['last_used_at'] == clock[0]
    clock[0] = item['expires_at']
    assert store.resolve_access_token(item['token']) is None


@pytest.mark.parametrize('values', [
    {'name': ''}, {'name': 'x' * 65}, {'name': 'hidden\nname'},
    {'scopes': []}, {'scopes': ['via:write']}, {'scopes': ['via:read', 'admin']},
    {'scopes': ['via:read', 'via:read']}, {'scopes': 'via:read'}, {'scopes': [{}]},
    {'days': 0}, {'days': 31}, {'days': True}, {'days': '30'},
    {'audience': 'http://remote.example/mcp'}, {'audience': 'https://via.example.test/api'},
    {'audience': 'https://user:password@via.example.test/mcp'},
])
def test_invalid_token_configuration_is_rejected(identities, values):
    store, _, _ = identities
    defaults = {'name': 'Fixture', 'scopes': ['via:read'], 'audience': AUDIENCE, 'days': 30}
    with pytest.raises(IdentityError) as error:
        store.create_access_token(2, **(defaults | values))
    assert error.value.code == 'invalid_input'


@pytest.mark.parametrize('audience', [
    'https://via.example.test:not-a-port/mcp', 'https://via.example.test:999999/mcp',
    'https://via.example.test\n/mcp', 'https://via.example.test\\invalid/mcp',
    'http://127.0.0.1/mcp', 'https://via.example.test/mcp?token=anything',
    'https://via.example.test/mcp#fragment',
])
def test_audience_rejects_ambiguous_or_noncanonical_urls(audience):
    assert valid_audience(audience) is False


def test_active_limit_is_atomic_and_revocation_or_expiry_frees_capacity(identities):
    store, _, clock = identities
    def create(_):
        try: return issue(store, days=1)
        except IdentityError as error:
            assert error.code == 'token_limit_reached'
            return None
    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(create, range(20)))
    issued = [item for item in results if item]
    assert len(issued) == 10
    store.revoke_access_token(2, issued[0]['id'])
    assert issue(store, days=1)
    clock[0] += 86400
    assert issue(store, days=1)


def test_issuance_rechecks_expected_user_revision_in_transaction(identities):
    store, _, _ = identities
    user = store.list_users()[1]
    store.update_user(1, user['id'], {'revision': user['revision'], 'revoke_sessions': True})
    with pytest.raises(IdentityError) as error:
        store.create_access_token(user['id'], name='Stale', scopes=['via:read'],
                                  audience=AUDIENCE, expected_revision=user['revision'])
    assert (error.value.code, error.value.status) == ('user_changed', 409)
    assert store.list_access_tokens(user['id']) == []


def site(store, resource=AUDIENCE):
    async def probe(request):
        actor = request.scope['via.identity']
        return JSONResponse({'username': actor['username'], 'role': actor['role']})
    downstream = Starlette(routes=[
        Route('/api/probe', probe, methods=['GET', 'POST']),
        Route('/api/profiles/1/start', probe, methods=['POST']),
        Route('/pages/api/pages/123/edit-options', probe, methods=['POST']),
    ])
    return IdentityGate(downstream, store, port=8000, mcp_resource=resource)


def bearer(token): return {'Authorization': 'Bearer ' + token}


def cookie_headers(store, raw):
    return {'Cookie': COOKIE + '=' + raw, 'X-Via-CSRF': store.resolve(raw)['csrf_token']}


def test_read_scope_blocks_writes_and_write_scope_still_respects_operator_role(identities):
    store, _, _ = identities
    read = issue(store, 'admin')
    write = issue(store, 'admin', ['via:read', 'via:write'])
    operator = issue(store, 'operator1', ['via:read', 'via:write'])
    with TestClient(site(store), base_url='http://127.0.0.1:8000', client=('127.0.0.1', 1234)) as client:
        assert client.get('/api/probe', headers=bearer(read['token'])).status_code == 200
        denied = client.post('/api/probe', headers=bearer(read['token']))
        assert denied.status_code == 403 and denied.json()['code'] == 'token_scope_required'
        assert client.post('/api/probe', headers=bearer(write['token'])).status_code == 200
        assert client.post('/api/probe', headers=bearer(operator['token'])).status_code == 403
        allowed = client.post('/api/profiles/1/start', headers=bearer(operator['token']))
        assert allowed.status_code == 200 and allowed.json()['username'] == 'operator1'
        assert client.post('/pages/api/pages/123/edit-options', headers=bearer(read['token'])).status_code == 200
        assert client.get('/api/probe', headers=bearer(read['token']) | {'Origin': 'https://foreign.example'}).status_code == 403


def test_pat_cannot_manage_tokens_users_password_or_cookie_sessions(identities):
    store, _, _ = identities
    item = issue(store, 'admin', ['via:read', 'via:write'])
    with TestClient(site(store), base_url='http://127.0.0.1:8000', client=('127.0.0.1', 1234)) as client:
        for method, path in [('GET', '/auth/api/me'), ('GET', '/auth/api/mcp-tokens'),
                             ('POST', '/auth/api/mcp-tokens'), ('DELETE', '/auth/api/mcp-tokens/' + item['id']),
                             ('GET', '/auth/api/users'), ('POST', '/auth/api/password'), ('POST', '/auth/api/logout')]:
            response = client.request(method, path, headers=bearer(item['token']), json={})
            assert response.status_code == 401, (method, path, response.text)
        assert store.resolve_access_token(item['token'])


def test_each_cookie_user_can_issue_list_and_revoke_only_own_token(identities):
    store, sessions, _ = identities
    issued = {}
    with TestClient(site(store), base_url='http://127.0.0.1:8000', client=('127.0.0.1', 1234)) as client:
        for username, raw in sessions.items():
            headers = cookie_headers(store, raw)
            response = client.post('/auth/api/mcp-tokens', headers=headers,
                json={'name': username + ' Codex', 'scopes': ['via:read', 'via:write'], 'days': 1})
            assert response.status_code == 201
            issued[username] = response.json()
            listed = client.get('/auth/api/mcp-tokens', headers=headers)
            assert [row['id'] for row in listed.json()['tokens']] == [issued[username]['id']]
            assert issued[username]['token'] not in listed.text
        headers = cookie_headers(store, sessions['operator1'])
        assert client.delete('/auth/api/mcp-tokens/' + issued['operator2']['id'], headers=headers).status_code == 404
        assert client.delete('/auth/api/mcp-tokens/' + issued['operator1']['id'], headers=headers).status_code == 200
        assert store.resolve_access_token(issued['operator1']['token']) is None
        assert store.resolve_access_token(issued['operator2']['token'])


def test_cookie_token_creation_requires_csrf_and_configured_mcp_audience(identities):
    store, sessions, _ = identities
    raw = sessions['admin']
    body = {'name': 'Fixture Codex', 'scopes': ['via:read']}
    with TestClient(site(store), base_url='http://127.0.0.1:8000', client=('127.0.0.1', 1234)) as client:
        denied = client.post('/auth/api/mcp-tokens', headers={'Cookie': COOKIE + '=' + raw}, json=body)
        assert denied.status_code == 403 and denied.json()['code'] == 'csrf_required'
        rejected = client.post('/auth/api/mcp-tokens', headers=cookie_headers(store, raw), json=body | {'audience': AUDIENCE})
        assert rejected.status_code == 400
    with TestClient(site(store, None), base_url='http://127.0.0.1:8000', client=('127.0.0.1', 1234)) as client:
        denied = client.post('/auth/api/mcp-tokens', headers=cookie_headers(store, raw), json=body)
        assert denied.status_code == 503 and denied.json()['code'] == 'mcp_not_configured'
