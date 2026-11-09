"""Profile authorization is independent of browser / event-loop execution."""
from types import SimpleNamespace
from unittest.mock import Mock
import psutil
import pytest
from app.services import proxy_bridge


def connection(pid=30, client_port=50001, server_port=41001):
    return SimpleNamespace(pid=pid, laddr=SimpleNamespace(ip='127.0.0.1', port=client_port),
                           raddr=SimpleNamespace(ip='127.0.0.1', port=server_port))


def process(path, name='chrome.exe', parents=()):
    return SimpleNamespace(name=lambda: name, cmdline=lambda: ['chrome.exe', '--user-data-dir='+str(path)], parents=lambda: list(parents))


def test_profile_network_service_is_allowed_via_its_browser_parent(tmp_path, monkeypatch):
    browser = process(tmp_path/'Chrome_001')
    network = process(tmp_path/'unrelated', parents=[browser])
    monkeypatch.setattr(psutil, 'net_connections', lambda **_: [connection()])
    monkeypatch.setattr(psutil, 'Process', lambda _: network)
    assert proxy_bridge.owns_proxy_connection(50001, 41001, tmp_path/'Chrome_001')


@pytest.mark.parametrize('case', ['different_profile', 'different_port', 'missing_pid', 'not_chrome'])
def test_unauthorized_connections_are_denied(tmp_path, monkeypatch, case):
    row = connection(pid=None if case=='missing_pid' else 30, server_port=41002 if case=='different_port' else 41001)
    owner = process(tmp_path/('Chrome_002' if case=='different_profile' else 'Chrome_001'), name='python.exe' if case=='not_chrome' else 'chrome.exe')
    monkeypatch.setattr(psutil, 'net_connections', lambda **_: [row])
    monkeypatch.setattr(psutil, 'Process', lambda _: owner)
    monkeypatch.setattr(proxy_bridge.time, 'sleep', lambda _: None)
    assert not proxy_bridge.owns_proxy_connection(50001, 41001, tmp_path/'Chrome_001')


def test_access_denied_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(psutil, 'net_connections', Mock(side_effect=psutil.AccessDenied()))
    assert not proxy_bridge.owns_proxy_connection(50001, 41001, tmp_path)


def test_split_profile_argument_and_quoted_path(tmp_path, monkeypatch):
    owner=process(tmp_path)
    owner.cmdline=lambda: ['chrome.exe', '--user-data-dir', '"'+str(tmp_path)+'"']
    monkeypatch.setattr(psutil, 'net_connections', lambda **_: [connection()])
    monkeypatch.setattr(psutil, 'Process', lambda _: owner)
    assert proxy_bridge.owns_proxy_connection(50001, 41001, tmp_path)
