"""Offline browser socket relay boundaries using a fake upstream connection."""
import asyncio
import json
from pathlib import Path
import sys
import threading

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'via-auth'))
from via_auth import IdentityGate, IdentityStore
from via_mcp.dashboard_proxy import add_websocket_route


@pytest.fixture
def relay(tmp_path, monkeypatch):
    store = IdentityStore(tmp_path / 'identity')
    accounts = store.bootstrap()
    user = store.list_users()[1]
    cookie = store.change_password(user['id'], accounts[1]['password'], 'Temporary browser relay password')
    config = {'port': 8040, 'manager_port': 8000, 'public_origin': 'https://via.example.test'}
    calls = []
    release = threading.Event()

    class Connection:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def send(self, value): calls.append(('message', value))
        async def __aiter__(self):
            yield json.dumps({'kind': 'fixture-change'})
            if await asyncio.to_thread(release.wait, 5):
                yield json.dumps({'kind': 'must-not-survive-revocation'})

    def connect(url, **kwargs):
        calls.append((url, kwargs))
        return Connection()
    monkeypatch.setattr('websockets.asyncio.client.connect', connect)
    app = Starlette()
    add_websocket_route(app, config)
    app = IdentityGate(app, store, port=8040, public_origin=config['public_origin'],
                       allow_service=False, mcp_resource=config['public_origin'] + '/mcp')
    try: yield store, user, cookie, app, calls, release
    finally: release.set()


def test_socket_uses_fixed_upstream_cookies_and_checks_live_revocation(relay):
    store, user, cookie, app, calls, release = relay
    headers = {'Cookie': 'via_identity=' + cookie + '; cm_session=fake-manager-session',
               'Origin': 'https://via.example.test', 'X-Via-Service-Token': 'never-forward-this',
               'X-Forwarded-Host': 'attacker.example'}
    with TestClient(app, base_url='https://via.example.test', client=('127.0.0.1', 1234)) as client:
        with client.websocket_connect('wss://via.example.test/ws?mode=invalidate', headers=headers) as socket:
            assert socket.receive_json() == {'kind': 'fixture-change'}
            target, options = calls[0]
            assert target == 'ws://127.0.0.1:8000/ws?mode=invalidate'
            assert options['origin'] == 'http://127.0.0.1:8000'
            assert options['additional_headers'] == {'Cookie': headers['Cookie']}
            assert options['proxy'] is None
            store.update_user(1, user['id'], {'revision': store.list_users()[1]['revision'], 'revoke_sessions': True})
            release.set()
            with pytest.raises(WebSocketDisconnect): socket.receive_json()


@pytest.mark.parametrize('cause', ['foreign_origin', 'pat_identity', 'invalid_mode'])
def test_socket_rejects_untrusted_requests_before_connecting_upstream(relay, cause):
    store, user, cookie, app, calls, release = relay
    headers = {'Cookie': 'via_identity=' + cookie, 'Origin': 'https://via.example.test'}
    path = 'wss://via.example.test/ws'
    if cause == 'foreign_origin': headers['Origin'] = 'https://foreign.example'
    if cause == 'pat_identity':
        token = store.create_access_token(user['id'], name='Fixture token', scopes=['via:read', 'via:write'],
                                         audience='https://via.example.test/mcp')
        headers['Authorization'] = 'Bearer ' + token['token']
    if cause == 'invalid_mode': path += '?mode=anything-else'
    with TestClient(app, base_url='https://via.example.test', client=('127.0.0.1', 1234)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers=headers): pass
    assert calls == []
