"""Fake-client tests only: never opens applications, databases, files or Meta."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from via_mcp.common import MutationInput, ToolError
from via_mcp.page_tools import tools


PID = '61590000000001'
CID = 7
OP = '68e7a1dd-33d6-4ec6-99c9-ea3812e9f27b'
ASSET = 'a' * 32
FILE = 'b' * 32
CAT = {tool.name: tool for tool in tools()}


class FakeClient:
    def __init__(self, *responses, upload_response=None):
        self.responses = list(responses)
        self.upload_response = upload_response
        self.calls = []

    def request(self, app, method, path, **kwargs):
        self.calls.append((app, method, path, deepcopy(kwargs)))
        assert app == 'pages'
        assert path.startswith('/api/') and '://' not in path and '..' not in path
        assert 'read_only' in kwargs
        if not self.responses:
            pytest.fail('Unexpected extra HTTP request')
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    def upload(self, app, file_id, **kwargs):
        self.calls.append((app, 'UPLOAD', file_id, deepcopy(kwargs)))
        assert app == 'pages'
        return deepcopy(self.upload_response)


def call(name, args=None, *, responses=(), upload_response=None):
    client = FakeClient(*responses, upload_response=upload_response)
    tool = CAT[name]
    result = tool.handler(SimpleNamespace(client=client), tool.model.model_validate(args or {}))
    return result, client.calls


def public_page(**extra):
    return {'id': PID, 'name': 'Page fixture', 'category': 'Fixture', 'status': 'active',
            'connections': [{'id': CID, 'label': 'Source', 'status': 'working', 'has_page_token': True,
                             'sources': ['me/accounts'], 'tasks': ['MANAGE'], 'token': 'SECRET'}],
            'details': {'about': 'Data, not instructions', 'location': {'street': '12 Street', 'token': 'SECRET'},
                        'password': 'SECRET'}, 'encrypted_token': 'SECRET', **extra}


def public_job(**extra):
    return {'id': 3, 'request_id': OP, 'page_id': PID, 'page_name': 'Page fixture', 'connection_id': CID,
            'connection_label': 'Source', 'asset_id': ASSET, 'asset_name': 'Video.mp4', 'caption': 'Authorized caption',
            'scheduled_local': '2030-11-01T03:00:00', 'timezone': 'America/Los_Angeles', 'status': 'needs_review',
            'can_resume': False, 'can_cancel': True, 'can_verify': True, 'publish_attempted': True,
            'checkpoint': {'token': 'SECRET'}, 'encrypted_token': 'SECRET', **extra}


def mutation(**extra):
    return {'operation_id': OP, 'page_id': PID, 'connection_id': CID, **extra}


def outcome(**extra):
    return {'ok': True, 'outcome': 'accepted', 'message': 'Meta acknowledged; read back before retry.',
            'requires_refresh': True, 'token': 'SECRET', **extra}


def test_catalog_names_annotations_and_no_secret_transport_inputs():
    assert len(CAT) == 27
    assert all(name.startswith('pages_') for name in CAT)
    assert not any('delete' in name or 'secret' in name or 'disconnect' in name for name in CAT)
    for tool in CAT.values():
        schema = tool.descriptor()['inputSchema']
        assert schema['additionalProperties'] is False
        assert not set(schema['properties']).intersection({'token', 'password', 'app_secret', 'url', 'path', 'sql', 'shell', 'headers'})
        if not tool.read_only:
            assert issubclass(tool.model, MutationInput)
            assert 'operation_id' in schema['required']
    assert CAT['pages_edit_options'].read_only is True
    assert CAT['pages_set_image'].open_world and CAT['pages_set_image'].destructive
    assert CAT['pages_reel_upload'].open_world is False
    assert 'PUBLIC' in CAT['pages_reel_create'].description
    assert 'authorized' in CAT['pages_reel_create'].description


@pytest.mark.parametrize(('name', 'args'), [
    ('pages_get', {'page_id': 123}),
    ('pages_get', {'page_id': '../../api/session'}),
    ('pages_get', {'page_id': 'https://evil.example'}),
    ('pages_get', {'page_id': PID, 'timezone': '../../UTC'}),
    ('pages_list', {'connection_id': True}),
    ('pages_list', {'limit': 101}),
    ('pages_list', {'search': 'a\x00b'}),
    ('pages_connections', {'connected_only': 'false'}),
    ('pages_events', {'limit': 0}),
    ('pages_categories', {'page_id': PID, 'connection_id': '7'}),
    ('pages_categories', {'page_id': PID, 'connection_id': CID, 'q': 'x' * 101}),
    ('pages_refresh', mutation(limit=True)),
    ('pages_refresh', mutation(include_posts='yes')),
    ('pages_refresh', {'operation_id': OP, 'page_id': PID}),
    ('pages_edit', mutation(operation_id='not-a-uuid', bio='Test')),
    ('pages_edit', mutation()),
    ('pages_edit', mutation(bio=None)),
    ('pages_edit', mutation(location=None)),
    ('pages_edit', mutation(location={})),
    ('pages_edit', mutation(location={'street': None})),
    ('pages_edit', mutation(location={'street': 'Street', 'raw_request': 'no'})),
    ('pages_edit', mutation(category_ids=['123', '123'])),
    ('pages_edit', mutation(category_ids=['Beauty Salon'])),
    ('pages_edit', mutation(category_ids=[])),
    ('pages_set_image', mutation(kind='avatar', file_id='C:/private/file.png')),
    ('pages_set_image', mutation(kind='video', file_id=FILE)),
    ('pages_reel_upload', {'operation_id': OP, 'file_id': 'https://evil.example/video.mp4'}),
    ('pages_reel_create', mutation(asset_id=ASSET, scheduled_local='2030-11-01T03:00+07:00')),
    ('pages_reel_create', mutation(asset_id=ASSET, scheduled_local='2030-02-30T03:00')),
    ('pages_reel_create', mutation(asset_id=ASSET, scheduled_local='2030-11-01T03:00', fold=True)),
    ('pages_reel_create', mutation(asset_id=ASSET, scheduled_local='2030-11-01T03:00', caption='\ud800')),
    ('pages_reel_cancel', {'operation_id': OP, 'job_id': 3}),
])
def test_strict_inputs_reject_ambiguous_or_unsafe_values_before_http(name, args):
    with pytest.raises(ValidationError):
        CAT[name].model.model_validate(args)


def test_unknown_top_level_inputs_cannot_smuggle_requests_or_tokens():
    for key in ('token', 'headers', 'url', 'method', 'raw_body', 'path', 'command'):
        with pytest.raises(ValidationError):
            CAT['pages_edit'].model.model_validate(mutation(bio='test', **{key: 'SECRET'}))


def test_connection_metadata_is_allowlisted_filtered_and_paginated():
    rows = [{'id': index, 'label': 'Source ' + str(index), 'connected': index % 2 == 0,
             'has_token': True, 'token': 'SECRET', 'app_secret': 'SECRET', 'encrypted_token': 'SECRET',
             'permissions': [{'permission': 'pages_show_list', 'status': 'granted', 'token': 'SECRET'}]}
            for index in range(1, 12)]
    result, calls = call('pages_connections', {'connected_only': True, 'limit': 2, 'offset': 1}, responses=[
        {'items': [rows[3], rows[5]], 'total': 5, 'limit': 2, 'offset': 1}])
    assert [row['id'] for row in result['items']] == [4, 6]
    assert result['total'] == 5 and result['next_offset'] == 3
    assert result['items'][0]['has_token'] is True
    assert 'SECRET' not in json.dumps(result)
    assert calls == [('pages', 'GET', '/api/v1/connections', {'read_only': True,
        'params': {'connected_only': True, 'limit': 2, 'offset': 1}})]


def test_page_list_is_bounded_and_forwards_only_supported_filters():
    rows = [public_page(id=str(index)) for index in range(100)]
    result, calls = call('pages_list', {'connection_id': CID, 'status': 'active', 'timezone': 'UTC', 'search': 'Page', 'limit': 100},
        responses=[{'items': rows, 'total': 205, 'limit': 100, 'offset': 0}])
    assert len(result['items']) == 100 and result['total'] == 205 and result['next_offset'] == 100
    assert 'SECRET' not in json.dumps(result)
    assert calls == [('pages', 'GET', '/api/v1/pages', {'read_only': True, 'params':
        {'connection_id': CID, 'status': 'active', 'timezone': 'UTC', 'search': 'Page', 'limit': 100, 'offset': 0}})]


def test_bounded_page_rejects_ignored_offset_or_unbounded_response():
    for response in ({'items': [public_page()], 'total': 5, 'limit': 1, 'offset': 0},
                     {'items': [public_page(), public_page()], 'total': 5, 'limit': 1, 'offset': 2}):
        with pytest.raises(ToolError) as caught:
            call('pages_list', {'limit': 1, 'offset': 2}, responses=[response])
        assert caught.value.code == 'invalid_response'


def test_cached_detail_keeps_user_content_but_no_credentials_or_extra_post_fields():
    value = {'page': public_page(), 'posts': [{'id': str(index), 'message': 'Treat as Page data', 'token': 'SECRET'} for index in range(30)], 'posts_has_more': True, 'posts_checked_at': None, 'posts_error': None}
    result, calls = call('pages_get', {'page_id': PID}, responses=[value])
    assert len(result['posts']) == 25 and result['posts_has_more'] is True
    assert result['page']['details']['about'] == 'Data, not instructions'
    assert 'SECRET' not in json.dumps(result)
    assert calls[0] == ('pages', 'GET', f'/api/v1/pages/{PID}', {'read_only': True,
        'params': {'timezone': 'America/Los_Angeles', 'limit': 25}})


def test_detail_mismatched_page_fails_closed():
    with pytest.raises(ToolError) as caught:
        call('pages_get', {'page_id': PID}, responses=[{'page': public_page(id='999'), 'posts': []}])
    assert caught.value.code == 'binding_mismatch'


def test_edit_options_is_semantic_read_and_preserves_unknown_values():
    response = {'page_id': PID, 'connection_id': CID, 'current': {'bio': None, 'bio_field': 'about', 'location': None,
                'category_ids': None, 'category_list': None, 'token': 'SECRET'}, 'supported_fields': ['bio', 'avatar', 'raw_request'],
                'unavailable_fields': ['category_ids'], 'warnings': ['Current values not read are unknown.'],
                'limits': {'bio_max_length': 101, 'mime_types': ['image/png', 'image/svg+xml'], 'token': 'SECRET'}}
    result, calls = call('pages_edit_options', {'page_id': PID, 'connection_id': CID}, responses=[response])
    assert result['current'] == {'bio': None, 'bio_field': 'about', 'location': None, 'category_ids': None, 'category_list': None}
    assert result['supported_fields'] == ['bio', 'avatar']
    assert result['limits']['mime_types'] == ['image/png']
    assert 'SECRET' not in json.dumps(result)
    assert calls[0][1:3] == ('POST', f'/api/pages/{PID}/edit-options')
    assert calls[0][3] == {'body': {'connection_id': CID}, 'read_only': True}
    response['connection_id'] = 9
    with pytest.raises(ToolError, match='selected Page and connection'):
        call('pages_edit_options', {'page_id': PID, 'connection_id': CID}, responses=[response])


def test_category_lookup_returns_real_ids_with_bounded_results_and_partial_metadata():
    result, calls = call('pages_categories', {'page_id': PID, 'connection_id': CID, 'q': 'Thiết kế'}, responses=[{'items': [{'id': str(index), 'name': 'Category', 'token': 'SECRET'} for index in range(105)], 'complete': False}])
    assert len(result['items']) == 100 and result['truncated'] and result['complete'] is False
    assert calls[0][3]['params'] == {'connection_id': CID, 'q': 'Thiết kế'}
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('action', ['check', 'sync'])
def test_connection_actions_are_explicit_cache_writes_and_match_target(action):
    result, calls = call('pages_connection_' + action, {'operation_id': OP, 'connection_id': CID}, responses=[{'connection': {'id': CID, 'label': 'Source', 'token': 'SECRET'}, 'pages_seen': 3, 'complete': True}])
    assert calls[0] == ('pages', 'POST', f'/api/connections/{CID}/{action}', {'read_only': False})
    assert result['connection']['id'] == CID and 'SECRET' not in json.dumps(result)
    with pytest.raises(ToolError) as caught:
        call('pages_connection_' + action, {'operation_id': OP, 'connection_id': CID}, responses=[{'connection': {'id': CID + 1}}])
    assert caught.value.uncertain


def test_refresh_requires_and_forwards_exact_source_without_operation_id_leak():
    result, calls = call('pages_refresh', mutation(timezone='UTC', include_posts=False, limit=12), responses=[{'page': public_page(), 'posts': []}])
    assert result['page']['id'] == PID
    assert calls == [('pages', 'POST', f'/api/pages/{PID}/refresh', {'read_only': False, 'params': {'timezone': 'UTC'}, 'body': {'connection_id': CID, 'include_posts': False, 'limit': 12}})]


def test_edit_sends_only_intended_fields_and_stable_operation_uuid_once():
    result, calls = call('pages_edit', mutation(bio='Chosen bio', location={'street': ' 12 Road '}), responses=[outcome(outcome='unknown', ok=False)])
    assert calls == [('pages', 'POST', f'/api/pages/{PID}/edit', {'read_only': False, 'body': {'bio': 'Chosen bio', 'location': {'street': '12 Road'}, 'connection_id': CID, 'request_id': OP}})]
    assert result['outcome'] == 'unknown' and 'SECRET' not in json.dumps(result)
    assert len(calls) == 1


def test_page_image_and_video_use_only_root_file_capabilities():
    result, calls = call('pages_set_image', mutation(kind='cover', file_id=FILE), upload_response=outcome())
    assert calls == [('pages', 'UPLOAD', FILE, {'kind': 'cover', 'params': {'page_id': PID, 'connection_id': CID, 'request_id': OP}})]
    assert result['outcome'] == 'accepted'
    result, calls = call('pages_reel_upload', {'operation_id': OP, 'file_id': FILE}, upload_response={'asset': {'id': ASSET, 'name': 'Video.mp4', 'size': 123, 'mime': 'video/mp4', 'path': 'PRIVATE', 'token': 'SECRET'}})
    assert calls == [('pages', 'UPLOAD', FILE, {'kind': 'video', 'params': {}})]
    assert result['asset']['id'] == ASSET and 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)


def test_malformed_write_ack_is_uncertain_not_false_success():
    with pytest.raises(ToolError) as caught:
        call('pages_edit', mutation(bio='Chosen'), responses=[{'ok': True}])
    assert caught.value.uncertain and caught.value.code == 'invalid_response'


def test_reel_list_and_asset_list_are_bounded_and_do_not_return_private_storage():
    result, calls = call('pages_reels_list', {'page_id': PID, 'connection_id': CID, 'status': 'needs_review', 'limit': 1},
        responses=[{'jobs': [public_job()], 'total': 2, 'limit': 1, 'offset': 0,
                    'scheduler': {'scheduler_running': True, 'active_count': 0, 'token': 'SECRET'}}])
    assert result['total'] == 2 and result['next_offset'] == 1 and len(result['items']) == 1
    assert 'SECRET' not in json.dumps(result)
    assert calls == [('pages', 'GET', '/api/v1/reels', {'read_only': True, 'params':
        {'page_id': PID, 'connection_id': CID, 'status': 'needs_review', 'limit': 1, 'offset': 0}})]
    result, calls = call('pages_reels_assets', responses=[{'assets': [{'id': ASSET, 'name': 'Video.mp4', 'path': 'PRIVATE', 'token': 'SECRET'}],
        'total': 1, 'limit': 50, 'offset': 0}])
    assert result['items'] == [{'id': ASSET, 'name': 'Video.mp4'}]
    assert calls == [('pages', 'GET', '/api/v1/reels/assets', {'read_only': True, 'params': {'limit': 50, 'offset': 0}})]


def test_reel_get_returns_exact_job_and_fails_when_missing():
    result, calls = call('pages_reel_get', {'job_id': 3}, responses=[{'job': public_job()}])
    assert result['job']['id'] == 3 and 'SECRET' not in json.dumps(result)
    assert calls == [('pages', 'GET', '/api/v1/reels/3', {'read_only': True})]
    with pytest.raises(ToolError) as caught:
        call('pages_reel_get', {'job_id': 99}, responses=[ToolError('Fixture item not found.', 'not_found')])
    assert caught.value.code == 'not_found'


def test_create_publication_keeps_exact_target_content_time_and_idempotency_key():
    args = mutation(asset_id=ASSET, caption='Authorized caption', scheduled_local='2030-11-01T03:00')
    result, calls = call('pages_reel_create', args, responses=[{'job': public_job(status='scheduled'), 'replayed': True}])
    assert calls[0] == ('pages', 'POST', '/api/reels', {'read_only': False, 'body': {'page_id': PID, 'connection_id': CID, 'asset_id': ASSET, 'caption': 'Authorized caption', 'scheduled_local': '2030-11-01T03:00', 'timezone': 'America/Los_Angeles', 'request_id': OP}})
    assert result['job']['request_id'] == OP and result['replayed']
    assert 'SECRET' not in json.dumps(result)


def test_create_caption_binding_uses_the_same_safe_projection_as_the_http_client():
    from via_mcp.privacy import protect
    caption = 'Authorized contact fixture@example.test'
    row = public_job(caption=protect(caption), status='scheduled')
    client = FakeClient({'job': row})
    args = CAT['pages_reel_create'].model.model_validate(mutation(asset_id=ASSET, caption=caption, scheduled_local='2030-11-01T03:00'))
    result = CAT['pages_reel_create'].handler(SimpleNamespace(client=client, protect=protect), args)
    assert client.calls[0][3]['body']['caption'] == caption
    assert result['job']['caption'] == protect(caption)
    assert 'fixture@example.test' not in json.dumps(result)


@pytest.mark.parametrize('changed', [{'request_id': str(uuid4())}, {'page_id': '999'}, {'connection_id': CID + 1}, {'asset_id': 'c' * 32}, {'caption': 'Other content'}, {'timezone': 'UTC'}, {'scheduled_local': '2030-11-01T04:00:00'}])
def test_create_binding_mismatch_is_uncertain_and_never_retried(changed):
    client = FakeClient({'job': public_job(**changed)})
    args = CAT['pages_reel_create'].model.model_validate(mutation(asset_id=ASSET, caption='Authorized caption', scheduled_local='2030-11-01T03:00'))
    with pytest.raises(ToolError) as caught:
        CAT['pages_reel_create'].handler(SimpleNamespace(client=client), args)
    assert caught.value.code == 'binding_mismatch' and caught.value.uncertain
    assert len(client.calls) == 1


@pytest.mark.parametrize('action', ['cancel', 'resume', 'verify'])
def test_job_actions_preflight_bound_source_and_allowed_flag(action):
    selected = public_job(**{'can_' + action: True, 'publish_attempted': action != 'resume'})
    result, calls = call('pages_reel_' + action, mutation(job_id=3), responses=[{'job': selected}, {'job': selected}])
    assert [value[1] for value in calls] == ['GET', 'POST']
    assert calls[0] == ('pages', 'GET', '/api/v1/reels/3', {'read_only': True})
    assert calls[1] == ('pages', 'POST', '/api/reels/3/' + action, {'read_only': False, 'body': {}})
    assert result['job']['page_id'] == PID


@pytest.mark.parametrize('changed', [{'id': 4}, {'page_id': '999'}, {'connection_id': CID + 1}, {'connection_id': True},
    {'can_cancel': 'true'}, {'can_cancel': False}, {'archived_at': '2026-11-06T00:00:00Z'}])
def test_job_target_or_flag_rejection_sends_no_mutation(changed):
    client = FakeClient({'job': public_job(**changed)})
    args = CAT['pages_reel_cancel'].model.model_validate(mutation(job_id=3))
    with pytest.raises(ToolError):
        CAT['pages_reel_cancel'].handler(SimpleNamespace(client=client), args)
    assert len(client.calls) == 1 and client.calls[0][1] == 'GET'


def test_resume_cannot_bypass_a_publish_attempt_even_with_a_stale_true_flag():
    client = FakeClient({'job': public_job(can_resume=True, publish_attempted=True)})
    args = CAT['pages_reel_resume'].model.model_validate(mutation(job_id=3))
    with pytest.raises(ToolError) as caught:
        CAT['pages_reel_resume'].handler(SimpleNamespace(client=client), args)
    assert caught.value.code == 'action_not_allowed' and len(client.calls) == 1


def test_post_action_binding_failure_is_marked_uncertain():
    client = FakeClient({'job': public_job()}, {'job': public_job(page_id='999')})
    args = CAT['pages_reel_cancel'].model.model_validate(mutation(job_id=3))
    with pytest.raises(ToolError) as caught:
        CAT['pages_reel_cancel'].handler(SimpleNamespace(client=client), args)
    assert caught.value.uncertain and len(client.calls) == 2


def test_remote_error_is_not_retried_inside_the_catalog_handler():
    error = ToolError('Transport failed safely.', 'transport_failed', uncertain=True)
    client = FakeClient(error)
    args = CAT['pages_edit'].model.model_validate(mutation(bio='Chosen'))
    with pytest.raises(ToolError) as caught:
        CAT['pages_edit'].handler(SimpleNamespace(client=client), args)
    assert caught.value is error and len(client.calls) == 1
