"""Explicit Via MCP capabilities for separately launched Codex processes.

Custom-agent instructions do not isolate MCP capabilities. Apply these CLI
overrides to each process; the server receives the same allowlist and rejects
calls outside it. Presets do not dispatch work or authorize publication.
"""
import json
from types import MappingProxyType


_COORDINATOR_READS = (
    'via_status', 'via_operation_status', 'pages_overview', 'pages_connections',
    'pages_list', 'pages_events', 'pages_reels_config', 'pages_reels_assets',
    'pages_reels_list', 'pages_reel_get', 'pages_video_source_status', 'pages_idea_status',
)

ROLE_TOOLS = MappingProxyType({
    # Frozen input and the authenticated uploader client provide production input.
    'producer': ('via_status',),
    # Stage profiles disable all MCP transports in provider_profile as well.
    # Empty allowlists remain defense in depth if a transport is enabled later.
    'producer_image': (),
    'producer_qa': (),
    'producer_product': (),
    'qa': ('via_status', 'via_operation_status', 'pages_reels_assets', 'pages_reels_list',
           'pages_reel_get', 'pages_video_source_status', 'pages_idea_status'),
    'coordinator': _COORDINATOR_READS,
    # This preset is used only for a separately authorized publication task.
    'publisher': _COORDINATOR_READS + (
        'via_new_operation_id', 'via_register_media', 'pages_reel_upload',
        'pages_reel_create', 'pages_reel_cancel', 'pages_reel_resume', 'pages_reel_verify',
    ),
})


def role_policy(role):
    """Return a fresh, serializable policy; unknown roles never select a default."""
    if not isinstance(role, str) or role not in ROLE_TOOLS:
        raise ValueError('Unknown worker role')
    tools = list(ROLE_TOOLS[role])
    return {'role': role, 'mcp_server': 'via_manager', 'enabled_tools': tools,
            'environment': {'VIA_MCP_ALLOWED_TOOLS': json.dumps(tools, separators=(',', ':'))}}


def codex_role_args(role):
    """Arguments for Codex -c: values are TOML, passed without a shell."""
    policy = role_policy(role)
    return [
        '-c', 'mcp_servers.via_manager.enabled_tools=' + json.dumps(policy['enabled_tools'], separators=(',', ':')),
        '-c', 'mcp_servers.via_manager.env.VIA_MCP_ALLOWED_TOOLS=' + json.dumps(policy['environment']['VIA_MCP_ALLOWED_TOOLS']),
    ]
