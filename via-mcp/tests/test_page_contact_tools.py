"""Contact/hour tools use synthetic client responses only; no Meta or local data."""
import json

import pytest
from pydantic import ValidationError

from test_page_tools import CAT, CID, OP, PID, call, mutation, outcome, public_page


@pytest.mark.parametrize('always_open', [True, False])
def test_contact_change_sends_only_approved_fields_once_with_exact_binding(always_open):
    result, calls = call('pages_edit', mutation(
        website=' https://blueox179.com ', is_always_open=always_open,
        location={'street': '1942 Broadway St. STE 314C', 'city': 'Boulder',
                  'state': 'CO', 'zip': '80302', 'country': 'United States'}),
        responses=[outcome(outcome='unknown', ok=False)])
    assert len(calls) == 1 and result['outcome'] == 'unknown'
    assert calls[0] == ('pages', 'POST', f'/api/pages/{PID}/edit', {
        'read_only': False, 'body': {
            'website': 'https://blueox179.com', 'is_always_open': always_open,
            'location': {'street': '1942 Broadway St. STE 314C', 'city': 'Boulder',
                         'state': 'CO', 'zip': '80302', 'country': 'United States'},
            'connection_id': CID, 'request_id': OP}})
    assert 'SECRET' not in json.dumps(result)


def test_explicit_website_clear_does_not_set_hours_or_other_fields():
    _, calls = call('pages_edit', mutation(website=''), responses=[outcome()])
    assert calls[0][3]['body'] == {'website': '', 'connection_id': CID, 'request_id': OP}


def test_false_alone_is_an_intended_change_and_is_not_omitted():
    _, calls = call('pages_edit', mutation(is_always_open=False), responses=[outcome()])
    assert calls[0][3]['body'] == {'is_always_open': False, 'connection_id': CID, 'request_id': OP}


@pytest.mark.parametrize('value', [None, 'true', 'false', 0, 1, {}, []])
def test_always_open_requires_an_explicit_boolean(value):
    with pytest.raises(ValidationError):
        CAT['pages_edit'].model.model_validate(mutation(is_always_open=value))


@pytest.mark.parametrize('value', [
    None, 5, 'blueox179.com', '//blueox179.com', 'javascript:alert(1)',
    'file:///private', 'ftp://blueox179.com', 'https://owner:password@example.com',
    'https://owner@example.com', 'https://example.com:444', 'https://example.com:bad',
    'https:///no-host', 'https://example.com/bad path', 'https://example.com/\x00x',
    'https://example.com/\r\nAuthorization:secret', 'https://example.com/\ud800',
    'https://example.com/' + 'x' * 2048,
])
def test_unsafe_or_ambiguous_website_is_rejected_before_transport(value):
    with pytest.raises(ValidationError):
        CAT['pages_edit'].model.model_validate(mutation(website=value))


@pytest.mark.parametrize('value', ['http://blueox179.com', 'https://blueox179.com:443/shop'])
def test_standard_http_and_https_websites_remain_supported(value):
    args = CAT['pages_edit'].model.model_validate(mutation(website=value))
    assert args.website == value


@pytest.mark.parametrize('always_open', [None, False, True])
def test_options_preserve_unknown_or_false_hours_and_filter_extra_data(always_open):
    response = {'page_id': PID, 'connection_id': CID,
                'current': {'website': None, 'is_always_open': always_open,
                            'token': 'SECRET', 'hours': {'private': 'SECRET'}},
                'supported_fields': ['website', 'is_always_open', 'raw_request'],
                'unavailable_fields': ['website', 'token']}
    result, calls = call('pages_edit_options', {'page_id': PID, 'connection_id': CID},
                         responses=[response])
    assert result['current']['website'] is None
    assert result['current']['is_always_open'] is always_open
    assert result['supported_fields'] == ['website', 'is_always_open']
    assert result['unavailable_fields'] == ['website']
    assert 'SECRET' not in json.dumps(result) and 'hours' not in result['current']
    assert calls[0][3] == {'body': {'connection_id': CID}, 'read_only': True}


@pytest.mark.parametrize('always_open', [False, True])
def test_cached_detail_exposes_contact_hours_but_no_avatar_transport_or_secrets(always_open):
    page = public_page(details={'website': 'https://blueox179.com',
                              'is_always_open': always_open, 'token': 'SECRET'},
                       avatar_url='/api/pages/123/avatar')
    result, _ = call('pages_get', {'page_id': PID}, responses=[{'page': page, 'posts': []}])
    assert result['page']['details'] == {'website': 'https://blueox179.com',
                                         'is_always_open': always_open}
    assert 'avatar_url' not in result['page'] and 'SECRET' not in json.dumps(result)


def test_extended_edit_contract_still_excludes_other_write_fields():
    schema = CAT['pages_edit'].descriptor()['inputSchema']
    assert {'website', 'is_always_open'} <= schema['properties'].keys()
    assert schema['additionalProperties'] is False
    assert CAT['pages_edit'].read_only is False and CAT['pages_edit'].destructive is True
    for key in ('hours', 'token', 'headers', 'about', 'cover', 'url', 'raw_request'):
        with pytest.raises(ValidationError):
            CAT['pages_edit'].model.model_validate(mutation(website='https://blueox179.com', **{key: 'SECRET'}))
