"""Client role policies are also enforced at the MCP server boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'meta-page-manager' / 'backend'))
from page_manager.agent_roles import ROLE_TOOLS, role_policy
from via_mcp.common import ToolError
from via_mcp.protocol import Protocol
from via_mcp.registry import Registry, parse_allowed_tools


def blocked(*_args, **_kwargs):
    pytest.fail('A forbidden tool reached the application or journal')


@pytest.mark.parametrize('raw', ['', 'null', '{}', 'true', '1', '"via_status"',
    '["via_status","via_status"]', '["Via_status"]', '["via_status",false]',
    '["via_status"', '["chrome_*"]', '[' * 5000])
def test_invalid_environment_policy_never_falls_back_to_full_catalog(raw):
    with pytest.raises(ValueError): parse_allowed_tools(raw)


def test_unset_and_explicit_empty_policy_have_distinct_meanings():
    assert parse_allowed_tools(None) is None
    assert parse_allowed_tools('[]') == ()
    registry = Registry(None, None, None, allowed_tools=parse_allowed_tools('[]'))
    assert registry.tools() == []
    with pytest.raises(ToolError) as error: registry.call('via_status', {})
    assert error.value.code == 'tool_not_allowed'
    assert len(Registry(None, None, None).tools()) == 61


@pytest.mark.parametrize('value', [('missing_tool',), ('via_status', 'missing_tool'),
    ('via_status', 'via_status'), 'via_status', (True,)])
def test_unknown_or_invalid_resolved_policy_fails_closed(value):
    with pytest.raises(ValueError): Registry(None, None, None, allowed_tools=value)


@pytest.mark.parametrize('role', ROLE_TOOLS)
def test_role_catalog_matches_canonical_policy_and_rejects_every_excluded_tool(role):
    client = SimpleNamespace(request=blocked, upload=blocked)
    journal = SimpleNamespace(key=blocked, begin=blocked, finish=blocked, get=blocked)
    full = Registry(None, None, None)
    policy = role_policy(role)
    registry = Registry(client, SimpleNamespace(register=blocked), journal,
        allowed_tools=parse_allowed_tools(policy['environment']['VIA_MCP_ALLOWED_TOOLS']))
    assert {tool['name'] for tool in registry.tools()} == set(ROLE_TOOLS[role])
    if role != 'publisher': assert all(tool.read_only for tool in registry.catalog.values())
    for name in set(full.catalog) - set(ROLE_TOOLS[role]):
        with pytest.raises(ToolError) as error: registry.call(name, {})
        assert error.value.code == 'tool_not_allowed'
    with pytest.raises(ToolError) as error: registry.call('nonexistent_tool', {})
    assert error.value.code == 'unknown_tool'


def test_producer_handshake_exposes_one_tool_and_rpc_publish_is_rejected():
    client = SimpleNamespace(request=lambda *_args, **_kwargs: {'stats': {'pages': 2}})
    journal = SimpleNamespace(key=blocked, begin=blocked)
    protocol = Protocol(Registry(client, None, journal, allowed_tools=ROLE_TOOLS['producer']))
    protocol.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
    protocol.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    catalog = protocol.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert [item['name'] for item in catalog['result']['tools']] == ['via_status']
    result = protocol.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
        'name': 'pages_reel_create', 'arguments': {}}})
    assert result['result']['isError']
    assert result['result']['structuredContent']['code'] == 'tool_not_allowed'
    status = protocol.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
        'name': 'via_status', 'arguments': {}}})
    assert status['result']['structuredContent']['ok'] is True


@pytest.mark.parametrize('raw,expected', [('["via_status"]', 1), ('[]', 0),
    ('null', None), ('["missing_tool"]', None)])
def test_server_entrypoint_enforces_environment_policy_in_an_isolated_copy(tmp_path, raw, expected):
    source = Path(__file__).resolve().parents[1]
    shutil.copy2(source / 'server.py', tmp_path / 'server.py')
    shutil.copytree(source / 'via_mcp', tmp_path / 'via_mcp', ignore=shutil.ignore_patterns('__pycache__'))
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
            'name': 'pages_reel_create', 'arguments': {}}},
    ]
    result = subprocess.run([sys.executable, '-I', '-B', str(tmp_path / 'server.py')],
        input=''.join(json.dumps(item) + '\n' for item in messages), text=True,
        capture_output=True, timeout=10, env={**os.environ, 'VIA_MCP_ALLOWED_TOOLS': raw},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if expected is None:
        assert result.returncode == 1 and not result.stdout
        assert not (tmp_path / 'data').exists()
    else:
        assert result.returncode == 0
        replies = {item['id']: item for item in map(json.loads, result.stdout.splitlines())}
        assert len(replies[2]['result']['tools']) == expected
        assert replies[3]['result']['structuredContent']['code'] == 'tool_not_allowed'
