"""Fixed-folder MCP tools use fake HTTP and temporary journals only."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from test_bridge_security import CSRF, make_client
from test_page_tools import CAT, CID, OP, PID, FakeClient, call
from via_mcp.common import ToolError
from via_mcp.journal import Journal
from via_mcp.page_tools import VIDEO_SOURCE_DIRECTORY, LEGACY_VIDEO_SOURCE_DIRECTORY
from via_mcp.privacy import protect
from via_mcp.registry import Registry


OTHER = '61590000000002'
SETTINGS = {'enabled': True, 'page_ids': [PID, OTHER], 'connection_id': CID,
            'daily_time': '06:00', 'timezone': 'America/Los_Angeles',
            'caption_template': '{filename}\nA fresh Reel.'}


def configure(**overrides):
    return {'operation_id': OP, **deepcopy(SETTINGS), **overrides}


def status(**overrides):
    return {'configured': True, 'settings': {**deepcopy(SETTINGS), 'directory': VIDEO_SOURCE_DIRECTORY},
            'stats': {'files_seen': 3, 'waiting': 1, 'imported': 2, 'queued': 2, 'skipped': 0, 'errors': 0},
            'items': [{'filename': 'funny-01.mp4', 'status': 'queued', 'page_id': PID,
                       'page_name': 'Fixture Page', 'scheduled_local': '2030-11-01T06:00:00',
                       'timezone': SETTINGS['timezone'], 'job_id': 9, 'message': 'Queued safely.'}],
            'running': False, 'next_page_id': OTHER, 'last_scan_at': '2030-10-31T12:00:00+00:00',
            'last_error': None, **overrides}


def test_catalog_has_three_explicit_tools_and_no_path_or_secret_inputs():
    assert len(CAT) == 27
    assert CAT['pages_video_source_status'].read_only is True
    for name in ('pages_video_source_configure', 'pages_video_source_scan'):
        tool = CAT[name]
        assert tool.read_only is False and tool.destructive and tool.open_world
        assert 'PUBLIC' in tool.description and 'operation_id' in tool.descriptor()['inputSchema']['required']
    for name in ('pages_video_source_status', 'pages_video_source_configure', 'pages_video_source_scan'):
        schema = CAT[name].descriptor()['inputSchema']
        assert schema['additionalProperties'] is False
        assert not {'path', 'directory', 'file_path', 'token', 'url', 'headers', 'bytes'}.intersection(schema['properties'])


def test_status_is_one_read_with_bounded_public_projection():
    raw = status()
    raw.update(token='SECRET', asset_root='PRIVATE', bytes='PRIVATE')
    raw['settings'].update(token='SECRET', asset_root='PRIVATE')
    raw['stats']['private'] = 'PRIVATE'
    raw['items'] = [{**raw['items'][0], 'filename': f'clip-{index:03d}.mp4',
                     'path': 'PRIVATE', 'access_token': 'SECRET'} for index in range(130)]
    result, calls = call('pages_video_source_status', responses=[raw])
    assert calls == [('pages', 'GET', '/api/video-source', {'read_only': True})]
    assert result['settings']['directory'] == VIDEO_SOURCE_DIRECTORY
    assert result['workspace_binding']=='project'
    assert len(result['items']) == 100 and result['items'][0]['filename'] == 'clip-030.mp4'
    assert set(result['stats']) == {'files_seen', 'waiting', 'imported', 'queued', 'skipped', 'errors'}
    assert all(value not in json.dumps(result) for value in ('SECRET', 'PRIVATE', 'asset_root', 'access_token'))


def test_initial_unconfigured_status_does_not_invent_pages_or_connection():
    raw = status(configured=False, next_page_id=None)
    raw['settings'].update(enabled=False, page_ids=[], connection_id=None)
    result, _ = call('pages_video_source_status', responses=[raw])
    assert result['configured'] is False
    assert result['settings']['enabled'] is False and result['settings']['page_ids'] == []
    assert result['settings']['connection_id'] is None and result['next_page_id'] is None


def test_configure_forwards_complete_exact_config_once_without_operation_or_source_path():
    result, calls = call('pages_video_source_configure', configure(), responses=[status(),status()])
    assert calls == [('pages','GET','/api/video-source',{'read_only':True}),
        ('pages', 'PUT', '/api/video-source/settings', {'body': SETTINGS, 'read_only': False})]
    assert result['settings']['page_ids'] == [PID, OTHER]
    assert result['configured'] is True
    assert 'operation_id' not in calls[1][3]['body'] and 'directory' not in calls[1][3]['body']


def test_configure_preserves_explicit_disabled_and_empty_caption():
    raw = status()
    raw['settings'].update(enabled=False, caption_template='')
    result, calls = call('pages_video_source_configure', configure(enabled=False, caption_template=''), responses=[status(),raw])
    assert result['settings']['enabled'] is False and result['settings']['caption_template'] == ''
    assert calls[1][3]['body']['enabled'] is False


def test_optional_revision_is_read_and_forwarded_but_not_bound_to_advanced_response():
    raw = status()
    raw['settings']['revision'] = 5
    result, calls = call('pages_video_source_configure', configure(revision=4), responses=[status(),raw])
    assert calls[1][3]['body'] == {**SETTINGS, 'revision': 4}
    assert result['settings']['revision'] == 5
    result, _ = call('pages_video_source_status', responses=[raw])
    assert result['settings']['revision'] == 5


def test_omitted_revision_is_not_sent_or_invented_for_older_statuses():
    result, calls = call('pages_video_source_configure', configure(), responses=[status(),status()])
    assert 'revision' not in calls[1][3]['body'] and 'revision' not in result['settings']


@pytest.mark.parametrize('revision', [None, True, False, 0, -1, '4', 1.5])
def test_explicit_revision_must_be_a_positive_integer(revision):
    with pytest.raises(ValidationError):
        CAT['pages_video_source_configure'].model.model_validate(configure(revision=revision))
    raw = status()
    raw['settings']['revision'] = revision
    with pytest.raises(ToolError) as caught:
        call('pages_video_source_status', responses=[raw])
    assert caught.value.code == 'invalid_response'


def test_stale_revision_conflict_is_not_retried_or_replayed(tmp_path, make_client):
    def respond(request):
        if request.method=='GET' and request.url.path=='/pages/api/video-source':return httpx.Response(200,json=status())
        assert request.method == 'PUT' and request.url.path == '/pages/api/video-source/settings'
        assert json.loads(request.content)['revision'] == 4
        return httpx.Response(409, json={'detail': 'Fixture revision conflict'})
    client, seen, _ = make_client(respond)
    registry = Registry(client, None, Journal(tmp_path / 'journal'))
    args = configure(revision=4)
    first = registry.call('pages_video_source_configure', args)
    again = registry.call('pages_video_source_configure', args)
    assert first['ok'] is False and first['code'] == 'upstream_rejected'
    assert again['replayed'] is True
    assert sum(request.method == 'PUT' for request in seen) == 1


@pytest.mark.parametrize('changed', [
    {'enabled': False}, {'page_ids': [OTHER, PID]}, {'page_ids': [PID]},
    {'connection_id': CID + 1}, {'daily_time': '07:00'}, {'timezone': 'UTC'},
    {'caption_template': 'Unapproved caption'},
])
def test_configure_different_target_or_scheduling_criteria_is_uncertain_and_not_retried(changed):
    raw = status()
    raw['settings'].update(changed)
    if OTHER not in raw['settings']['page_ids']:
        raw['next_page_id'] = PID
    client = FakeClient(status(),raw)
    tool = CAT['pages_video_source_configure']
    with pytest.raises(ToolError) as caught:
        tool.handler(SimpleNamespace(client=client), tool.model.model_validate(configure()))
    assert caught.value.code == 'binding_mismatch' and caught.value.uncertain is True
    assert len(client.calls) == 2


def test_configure_bound_caption_compares_the_same_protected_echo_as_manager_client():
    caption = 'Reply to owner@example.invalid'
    raw = status()
    raw['settings']['caption_template'] = protect(caption)
    client = FakeClient(status(),raw)
    tool = CAT['pages_video_source_configure']
    result = tool.handler(SimpleNamespace(client=client, protect=protect),
                          tool.model.model_validate(configure(caption_template=caption)))
    assert result['settings']['caption_template'] == 'Reply to [email đã ẩn]'
    assert client.calls[1][3]['body']['caption_template'] == caption


@pytest.mark.parametrize('override', [
    {'enabled': 'true'}, {'enabled': 1}, {'enabled': None},
    {'page_ids': []}, {'page_ids': [PID, PID]}, {'page_ids': ['https://example.invalid']},
    {'page_ids': [123]}, {'page_ids': [str(index) for index in range(101)]},
    {'connection_id': True}, {'connection_id': 0}, {'connection_id': '7'},
    {'daily_time': '6:00'}, {'daily_time': '24:00'}, {'daily_time': '06:60'},
    {'daily_time': '06:00:00'}, {'daily_time': '06:00\n'}, {'timezone': '../../UTC'},
    {'caption_template': 'x' * 2201}, {'caption_template': 'x\x00y'},
    {'caption_template': 'x\ud800'}, {'caption_template': None},
    {'directory': 'C:/private'}, {'path': 'C:/private'}, {'token': 'PRIVATE'},
    {'operation_id': 'not-a-uuid'},
])
def test_configure_strict_inputs_reject_ambiguous_or_unbounded_values_before_http(override):
    with pytest.raises(ValidationError):
        CAT['pages_video_source_configure'].model.model_validate(configure(**override))


def test_configure_requires_all_criteria_and_status_or_scan_cannot_smuggle_arguments():
    for field in SETTINGS:
        args = configure()
        args.pop(field)
        with pytest.raises(ValidationError):
            CAT['pages_video_source_configure'].model.model_validate(args)
    for name in ('pages_video_source_status', 'pages_video_source_scan'):
        for key in ('directory', 'path', 'page_ids', 'token', 'url'):
            args = {'operation_id': OP} if name.endswith('_scan') else {}
            with pytest.raises(ValidationError):
                CAT[name].model.model_validate({**args, key: 'PRIVATE'})


def test_scan_is_one_fixed_post_with_empty_body():
    result, calls = call('pages_video_source_scan', {'operation_id': OP}, responses=[status(),status()])
    assert result['stats']['queued'] == 2
    assert calls == [('pages','GET','/api/video-source',{'read_only':True}),
        ('pages', 'POST', '/api/video-source/scan', {'body': {}, 'read_only': False})]


@pytest.mark.parametrize('name,args', [
    ('pages_video_source_configure', configure()),
    ('pages_video_source_scan', {'operation_id': OP}),
])
def test_durable_registry_replay_never_reconfigures_or_rescans(tmp_path, name, args):
    client = FakeClient(status(),status())
    registry = Registry(client, None, Journal(tmp_path))
    first = registry.call(name, args)
    again = registry.call(name, args)
    assert first['ok'] is True and again['replayed'] is True and len(client.calls) == 2


@pytest.mark.parametrize('name,args', [
    ('pages_video_source_configure', configure()),
    ('pages_video_source_scan', {'operation_id': OP}),
])
def test_uncertain_scan_or_configure_is_not_repeated_after_new_registry(tmp_path, name, args):
    client = FakeClient(status(),ToolError('Fixture network uncertainty', 'manager_unavailable', uncertain=True))
    journal = Journal(tmp_path)
    first = Registry(client, None, journal).call(name, args)
    again = Registry(client, None, journal).call(name, args)
    assert first['uncertain'] is True and again['code'] == 'operation_uncertain'
    assert len(client.calls) == 2


def test_operation_id_cannot_be_reused_for_changed_config(tmp_path):
    client = FakeClient(status(),status())
    registry = Registry(client, None, Journal(tmp_path))
    assert registry.call('pages_video_source_configure', configure())['ok'] is True
    with pytest.raises(ToolError):
        registry.call('pages_video_source_configure', configure(daily_time='07:00'))
    assert len(client.calls) == 2


@pytest.mark.parametrize('name,args', [
    ('pages_video_source_status', {}),
    ('pages_video_source_configure', configure()),
    ('pages_video_source_scan', {'operation_id': OP}),
])
def test_source_directory_must_match_fixed_authorized_source(name, args):
    raw = status()
    raw['settings']['directory'] = 'C:\\private-source\\secret'
    with pytest.raises(ToolError) as caught:
        call(name, args, responses=[raw])
    assert caught.value.code == 'binding_mismatch'
    assert caught.value.uncertain is False  # All three fail on a GET before any write.
    assert 'private-source' not in str(caught.value)


def test_fixed_source_normalizes_windows_slashes_without_echoing_a_different_path():
    raw = status()
    raw['settings']['directory'] = VIDEO_SOURCE_DIRECTORY.lower().replace('\\', '/')
    result, _ = call('pages_video_source_status', responses=[raw])
    assert result['settings']['directory'] == VIDEO_SOURCE_DIRECTORY


def test_status_rejects_malformed_bindings_and_never_invents_counts():
    for updates in ({'stats': {'queued': True}}, {'stats': {'queued': -1}},
                    {'next_page_id': '999'}, {'running': 'false'}, {'configured': None},
                    {'items': {}}, {'settings': None}):
        with pytest.raises(ToolError) as caught:
            call('pages_video_source_status', responses=[status(**updates)])
        assert caught.value.code == 'invalid_response'


def test_status_omits_private_absolute_filenames_and_sanitizes_paths_in_messages():
    raw = status(last_error='Cannot open C:\\private-media\\asset.mp4')
    raw['items'] = [{'filename': value, 'status': 'error'} for value in (
        'C:\\private-media\\asset.mp4', '/private/asset.mp4', '../asset.mp4',
        'folder/../../asset.mp4', 'https://example.invalid/asset.mp4')]
    raw['items'].append({'filename': 'safe.mp4', 'status': 'error', 'message': 'Failed at C:\\private-media\\asset.mp4', 'path': 'PRIVATE'})
    result, _ = call('pages_video_source_status', responses=[raw])
    assert [row['filename'] for row in result['items']] == ['safe.mp4']
    assert 'private-media' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)
    assert result['last_error'] == 'Cannot open [local path hidden]'


@pytest.mark.parametrize('method,path,name,args', [
    ('PUT', '/pages/api/video-source/settings', 'pages_video_source_configure', configure()),
    ('POST', '/pages/api/video-source/scan', 'pages_video_source_scan', {'operation_id': OP}),
])
def test_new_routes_use_page_session_csrf_and_never_replay_a_changed_session(make_client, method, path, name, args):
    def respond(request):
        if request.method=='GET' and request.url.path=='/pages/api/video-source':return httpx.Response(200,json=status())
        assert request.method == method and request.url.path == path
        assert request.headers['x-mpm-csrf'] == CSRF
        return httpx.Response(403, json={'detail': 'Fixture expired session'})
    client, seen, _ = make_client(respond)
    tool = CAT[name]
    with pytest.raises(ToolError) as caught:
        tool.handler(SimpleNamespace(client=client), tool.model.model_validate(args))
    assert caught.value.code == 'session_changed'
    assert sum(request.method == method and request.url.path == path for request in seen) == 1


def legacy_status():
    raw=status();raw['settings']['directory']=LEGACY_VIDEO_SOURCE_DIRECTORY
    return raw


def test_historical_source_is_readable_and_keeps_completed_cancelled_rows():
    raw=legacy_status();raw['settings']['directory']=LEGACY_VIDEO_SOURCE_DIRECTORY.lower().replace('\\','/')
    raw['items']=[{**raw['items'][0],'status':'completed'},
        {**raw['items'][0],'filename':'cancelled.mp4','status':'cancelled','job_id':10}]
    result,calls=call('pages_video_source_status',responses=[raw])
    assert result['workspace_binding']=='legacy_read_only' and result['settings']['directory']==LEGACY_VIDEO_SOURCE_DIRECTORY
    assert [row['status'] for row in result['items']]==['completed','cancelled']
    assert len(calls)==1 and calls[0][1]=='GET'


@pytest.mark.parametrize('name,args',[
    ('pages_video_source_scan',{'operation_id':OP}),('pages_video_source_configure',configure()),
])
def test_legacy_source_mutations_are_preflight_read_only_and_never_replayed(tmp_path,name,args):
    client=FakeClient(legacy_status());registry=Registry(client,None,Journal(tmp_path/'legacy'))
    first=registry.call(name,args);again=registry.call(name,args)
    assert first['code']=='binding_mismatch' and first['uncertain'] is False and again['replayed']
    assert [row[1] for row in client.calls]==['GET']


@pytest.mark.parametrize('name,args,method',[
    ('pages_video_source_scan',{'operation_id':OP},'POST'),('pages_video_source_configure',configure(),'PUT'),
])
def test_post_write_binding_switch_is_uncertain_and_never_repeated(tmp_path,name,args,method):
    client=FakeClient(status(),legacy_status());registry=Registry(client,None,Journal(tmp_path/'switched'))
    first=registry.call(name,args);again=registry.call(name,args)
    assert first['code']=='binding_mismatch' and first['uncertain'] is True
    assert again['code']=='operation_uncertain' and [row[1] for row in client.calls]==['GET',method]


def test_preflight_network_failure_has_no_mutation_uncertainty_or_write(tmp_path):
    client=FakeClient(ToolError('Fixture GET failure','manager_unavailable',uncertain=True))
    result=Registry(client,None,Journal(tmp_path/'read-failure')).call('pages_video_source_scan',{'operation_id':OP})
    assert result['code']=='manager_unavailable' and result['uncertain'] is False
    assert [row[1] for row in client.calls]==['GET']


@pytest.mark.parametrize('name,args',[
    ('pages_video_source_scan',{'operation_id':OP}),('pages_video_source_configure',configure()),
])
def test_legacy_http_status_is_readable_but_mutations_never_send_put_or_post(tmp_path,make_client,name,args):
    def handler(request):
        assert request.method=='GET' and request.url.path=='/pages/api/video-source'
        return httpx.Response(200,json=legacy_status())
    client,seen,_=make_client(handler);registry=Registry(client,None,Journal(tmp_path/'legacy-http'))
    assert registry.call('pages_video_source_status',{})['result']['workspace_binding']=='legacy_read_only'
    mutation=registry.call(name,args)
    assert mutation['code']=='binding_mismatch' and mutation['uncertain'] is False
    assert all(request.method=='GET' for request in seen)
