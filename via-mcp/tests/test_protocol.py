"""Offline legacy MCP transport tests: no local managers or network."""
import io
import json

import pytest

from via_mcp.common import ToolError
from via_mcp.protocol import MAX_LINE, SUPPORTED, VERSION, Protocol


class StubRegistry:
    def __init__(self):
        self.calls = []
        self.mode = 'success'
        self.closed = False

    def tools(self):
        return [{'name': 'fixture_tool', 'description': 'Offline fixture',
                 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                 'annotations': {'readOnlyHint': True}}]

    def call(self, name, args):
        self.calls.append((name, args))
        if self.mode == 'tool_error':
            raise ToolError('Bearer fixture-bearer-private EAA' + 'X' * 40 + ' fixture@example.invalid',
                            'fixture_failure', uncertain=True)
        if self.mode == 'unexpected':
            raise RuntimeError('fixture-private-exception')
        if self.mode == 'failure':
            return {'ok': False, 'error': {'code': 'fixture_denial', 'message': 'Denied safely'}}
        return {'ok': True, 'result': {'name': name, 'arguments': args}}

    def close(self):
        self.closed = True


def request(method, request_id=1, params=None):
    return {'jsonrpc': '2.0', 'id': request_id, 'method': method,
            **({'params': params} if params is not None else {})}


def init_params(version=VERSION):
    return {'protocolVersion': version, 'capabilities': {},
            'clientInfo': {'name': 'offline-fixture', 'version': '1'}}


def ready():
    registry = StubRegistry()
    protocol = Protocol(registry)
    assert 'result' in protocol.handle(request('initialize', 0, init_params()))
    assert protocol.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    return protocol, registry


def serve(protocol, records):
    source = io.BytesIO(b''.join(record if isinstance(record, bytes) else json.dumps(record, ensure_ascii=False).encode('utf-8') + b'\n' for record in records))
    output = io.BytesIO()
    protocol.serve(source, output)
    wire = output.getvalue()
    return [json.loads(line) for line in wire.splitlines()], wire


@pytest.mark.parametrize('version', sorted(SUPPORTED) + ['2099-01-01'])
def test_explicit_legacy_version_negotiation_and_tools_only_capabilities(version):
    registry = StubRegistry()
    protocol = Protocol(registry)
    response = protocol.handle(request('initialize', 1, init_params(version)))
    result = response['result']
    assert result['protocolVersion'] == (version if version in SUPPORTED else VERSION)
    assert set(result['capabilities']) == {'tools'}
    assert result['serverInfo']['name'] and result['serverInfo']['version']
    assert not registry.calls and not protocol.ready


def test_ping_does_not_open_managers_or_require_ready():
    registry = StubRegistry()
    protocol = Protocol(registry)
    assert protocol.handle(request('ping'))['result'] == {}
    assert not registry.calls and not protocol.initialized


@pytest.mark.parametrize('stage', ['new', 'initialized'])
@pytest.mark.parametrize('method', ['tools/list', 'tools/call'])
def test_tools_require_complete_handshake(stage, method):
    registry = StubRegistry()
    protocol = Protocol(registry)
    if stage == 'initialized':
        protocol.handle(request('initialize', 0, init_params()))
    params = {'name': 'fixture_tool'} if method == 'tools/call' else {}
    response = protocol.handle(request(method, 1, params))
    assert response['error']['code'] == -32000
    assert not registry.calls


@pytest.mark.parametrize('params', [[], None, {'unexpected': True}, {'_meta': {}, 'unexpected': True}])
def test_malformed_initialized_notification_never_unlocks_tools(params):
    registry = StubRegistry()
    protocol = Protocol(registry)
    protocol.handle(request('initialize', 0, init_params()))
    assert protocol.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': params}) is None
    assert not protocol.ready
    assert protocol.handle(request('tools/call', 1, {'name': 'fixture_tool'}))['error']['code'] == -32000
    assert not registry.calls


def test_initialized_before_initialize_does_not_unlock_tools():
    protocol = Protocol(StubRegistry())
    assert protocol.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    assert not protocol.ready
    protocol.handle(request('initialize', 0, init_params()))
    assert not protocol.ready


@pytest.mark.parametrize('bad', [
    {}, {'name': 'client'}, {'version': '1'}, {'name': '', 'version': '1'},
    {'name': 'client', 'version': ''}, {'name': 1, 'version': '1'}, [], None,
])
def test_initialize_requires_identifiable_client(bad):
    protocol = Protocol(StubRegistry())
    response = protocol.handle(request('initialize', 1, {**init_params(), 'clientInfo': bad}))
    assert response['error']['code'] == -32602
    assert not protocol.initialized and not protocol.ready


@pytest.mark.parametrize('field,value', [('protocolVersion', 1), ('protocolVersion', None), ('capabilities', []), ('capabilities', None)])
def test_initialize_rejects_invalid_parameter_types(field, value):
    protocol = Protocol(StubRegistry())
    response = protocol.handle(request('initialize', 1, {**init_params(), field: value}))
    assert response['error']['code'] == -32602
    assert not protocol.initialized


@pytest.mark.parametrize('bad_id', [None, True, False, 1.5, [], {}])
def test_invalid_request_ids_do_not_execute(bad_id):
    protocol, registry = ready()
    response = protocol.handle(request('tools/call', bad_id, {'name': 'fixture_tool'}))
    assert response['error']['code'] == -32600 and response['id'] is None
    assert not registry.calls


@pytest.mark.parametrize('bad', [[], None, 1, {'jsonrpc': '1.0', 'id': 1, 'method': 'tools/call'},
                                  {'jsonrpc': '2.0', 'id': 1}, {'jsonrpc': '2.0', 'id': 1, 'method': 3}])
def test_invalid_envelope_is_a_protocol_error(bad):
    protocol, registry = ready()
    assert protocol.handle(bad)['error']['code'] == -32600
    assert not registry.calls


@pytest.mark.parametrize('method', ['tools/call', 'tools/list', 'initialize', 'unknown/notification'])
def test_notifications_have_no_reply_and_never_execute_tools(method):
    protocol, registry = ready()
    assert protocol.handle({'jsonrpc': '2.0', 'method': method, 'params': {'name': 'fixture_tool'}}) is None
    assert not registry.calls


def test_duplicate_request_id_does_not_reexecute_and_string_id_is_distinct():
    protocol, registry = ready()
    call = request('tools/call', 7, {'name': 'fixture_tool', 'arguments': {'value': 'once'}})
    assert protocol.handle(call)['result']['isError'] is False
    assert protocol.handle(call)['error']['code'] == -32600
    assert protocol.handle({**call, 'id': '7'})['result']['isError'] is False
    assert len(registry.calls) == 2


@pytest.mark.parametrize('params', [[], 'text', None, {'arguments': {}}, {'name': 4},
                                     {'name': 'fixture_tool', 'arguments': []},
                                     {'name': 'fixture_tool', 'extra': 'value'}])
def test_invalid_call_parameters_do_not_reach_registry(params):
    protocol, registry = ready()
    message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': params}
    assert protocol.handle(message)['error']['code'] == -32602
    assert not registry.calls


def test_list_returns_only_catalog_and_rejects_pagination():
    protocol, registry = ready()
    assert protocol.handle(request('tools/list', 1))['result'] == {'tools': registry.tools()}
    assert protocol.handle(request('tools/list', 2, {'cursor': 'unsupported'}))['error']['code'] == -32602
    assert not registry.calls


@pytest.mark.parametrize('params', [{'_meta': {}}, {'_meta': {'progressToken': 7}},
                                  {'cursor': None}, {'cursor': ''},
                                  {'cursor': None, '_meta': {'client': 'codex'}}])
def test_list_accepts_client_request_metadata_and_empty_initial_cursor(params):
    protocol, registry = ready()
    response = protocol.handle(request('tools/list', 1, params))
    assert response['result'] == {'tools': registry.tools()}
    assert 'nextCursor' not in response['result']
    assert not registry.calls


@pytest.mark.parametrize('params', [{'_meta': None}, {'_meta': []}, {'cursor': 1},
                                  {'cursor': False}, {'cursor': []}, {'cursor': {}},
                                  {'unexpected': True}, {'cursor': 'not-issued', '_meta': {}}])
def test_list_rejects_malformed_metadata_and_unissued_cursors(params):
    protocol, registry = ready()
    assert protocol.handle(request('tools/list', 1, params))['error']['code'] == -32602
    assert not registry.calls


def test_unknown_methods_and_reinitialization_do_not_execute():
    protocol, registry = ready()
    assert protocol.handle(request('resources/list', 1))['error']['code'] == -32601
    assert protocol.handle(request('initialize', 2, init_params()))['error']['code'] == -32600
    assert not registry.calls


@pytest.mark.parametrize('mode', ['success', 'failure', 'tool_error', 'unexpected'])
def test_execution_result_schema_and_error_privacy(mode):
    protocol, registry = ready()
    registry.mode = mode
    response = protocol.handle(request('tools/call', 1, {'name': 'fixture_tool', 'arguments': {'safe': 'Tiếng Việt'}}))
    assert 'error' not in response
    result = response['result']
    assert result['isError'] is (mode != 'success')
    assert json.loads(result['content'][0]['text']) == result['structuredContent']
    wire = json.dumps(response, ensure_ascii=False)
    assert 'fixture-bearer-private' not in wire
    assert 'EAAXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX' not in wire
    assert 'fixture@example.invalid' not in wire
    assert 'fixture-private-exception' not in wire
    if mode == 'tool_error':
        assert result['structuredContent']['uncertain'] is True


@pytest.mark.parametrize('bad_line,code', [
    (b'not-json\n', -32700), (b'\xff\n', -32700),
    (b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":1,"method":"ping"}\n', -32700),
    (b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}\n', -32700),
    (b'[]\n', -32600), (b'null\n', -32600),
])
def test_bad_wire_is_bounded_error_and_next_message_still_works(bad_line, code):
    protocol = Protocol(StubRegistry())
    records, _ = serve(protocol, [bad_line, request('ping', 3)])
    assert records[0]['error']['code'] == code
    assert records[1] == {'jsonrpc': '2.0', 'id': 3, 'result': {}}


def test_oversize_wire_drains_exactly_one_line_and_recovers():
    protocol = Protocol(StubRegistry())
    records, wire = serve(protocol, [b'X' * (MAX_LINE * 2 + 10) + b'\n', request('ping', 1)])
    assert len(records) == 2
    assert records[0]['error']['code'] == -32700
    assert records[1]['result'] == {}
    assert len(wire) < 1000


def test_stdout_is_only_utf8_json_lines_and_notifications_are_silent(capsys):
    protocol = Protocol(StubRegistry())
    records, wire = serve(protocol, [request('initialize', 0, init_params()),
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        request('tools/call', 1, {'name': 'fixture_tool', 'arguments': {'caption': 'Tiếng Việt\nline two'}}),
        {'jsonrpc': '2.0', 'method': 'tools/call', 'params': {'name': 'fixture_tool'}}])
    assert len(records) == 2 and wire.endswith(b'\n')
    assert len(wire.splitlines()) == 2
    assert records[1]['result']['structuredContent']['result']['arguments']['caption'] == 'Tiếng Việt\nline two'
    assert capsys.readouterr() == ('', '')


def test_eof_does_not_stop_saved_schedules_or_close_application():
    registry = StubRegistry()
    protocol = Protocol(registry)
    records, wire = serve(protocol, [])
    assert records == [] and wire == b''
    assert not registry.closed and not registry.calls

