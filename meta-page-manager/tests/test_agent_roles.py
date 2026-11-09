"""Role configuration is applied to a process, not only agent instructions."""
import json
import tomllib

import pytest

from page_manager.agent_roles import ROLE_TOOLS, codex_role_args, role_policy


@pytest.mark.parametrize('role', ROLE_TOOLS)
def test_cli_overrides_are_valid_toml_and_match_server_policy(role):
    args = codex_role_args(role)
    assert len(args) == 4 and args[::2] == ['-c', '-c']
    parsed = {}
    for override in args[1::2]:
        key, value = override.split('=', 1)
        parsed[key] = tomllib.loads('value=' + value)['value']
    tools = list(ROLE_TOOLS[role])
    assert parsed['mcp_servers.via_manager.enabled_tools'] == tools
    assert json.loads(parsed['mcp_servers.via_manager.env.VIA_MCP_ALLOWED_TOOLS']) == tools
    assert all(not name.startswith('chrome_') for name in tools)
    assert all('outlook' not in name and '_bm_' not in name for name in tools)


def test_role_policy_is_not_mutable_through_a_returned_value():
    policy = role_policy('producer')
    policy['enabled_tools'].append('pages_reel_create')
    policy['environment']['VIA_MCP_ALLOWED_TOOLS'] = '[]'
    assert role_policy('producer')['enabled_tools'] == ['via_status']
    assert json.loads(role_policy('producer')['environment']['VIA_MCP_ALLOWED_TOOLS']) == ['via_status']


@pytest.mark.parametrize('stage', ('image', 'qa', 'product'))
def test_producer_stage_policies_cannot_publish_or_read_application_data(stage):
    role = 'producer_' + stage
    policy = role_policy(role)
    assert policy['enabled_tools'] == []
    assert policy['environment']['VIA_MCP_ALLOWED_TOOLS'] == '[]'
    policy['enabled_tools'].append('pages_reel_create')
    assert role_policy(role)['enabled_tools'] == []


@pytest.mark.parametrize('role', ['unknown', '', None, [], 'Producer'])
def test_invalid_role_has_no_default_or_unrestricted_fallback(role):
    with pytest.raises(ValueError): role_policy(role)
    with pytest.raises(ValueError): codex_role_args(role)
