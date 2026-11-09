"""Page contact/hour changes use disposable stores and synthetic Graph only."""
from copy import deepcopy
import json
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest

from conftest import FixtureBox, immediate
from page_manager.errors import AppError
from page_manager.graph import GraphError, GraphMutationUncertain
from page_manager.inputs import ConnectionInput, ConnectionUpdate, PageEdit, PageEditSource, PageRefresh
from page_manager.service import PageManager
from test_page_edits import DERIVED, OTHER, TOKEN, EditGraph, graph_call, link


ADDRESS = {'street': '1942 Broadway St. STE 314C', 'city': 'Boulder',
           'state': 'CO', 'zip': '80302', 'country': 'United States'}
WEBSITE = 'https://blueox179.com'


class ContactGraph(EditGraph):
    def __init__(self):
        super().__init__()
        self.details_value = {'id': '555', 'name': 'Fixture Page', 'website': WEBSITE}

    async def details(self, token, pid):
        self.calls.append(('details', token, pid))
        return deepcopy(self.details_value), None


@pytest.fixture
def contact(tmp_path):
    graph = ContactGraph()
    manager = PageManager(tmp_path, graph_factory=lambda: graph, secret_box=FixtureBox())
    cid = manager.save_connection(ConnectionInput(label='Fixture source', token_type='system_user', token=TOKEN))['id']
    link(manager, cid, derived=DERIVED)
    return manager, graph, cid


def change(cid, **fields):
    return PageEdit(connection_id=cid, request_id=uuid4(), **fields)


@pytest.mark.parametrize('always_open', [True, False])
def test_contact_payload_has_only_explicit_fields_and_preserves_false(always_open):
    body = change(1, location=ADDRESS, website=' ' + WEBSITE + ' ', is_always_open=always_open)
    assert body.graph_fields() == {'location': ADDRESS, 'website': WEBSITE, 'is_always_open': always_open}
    assert change(1, is_always_open=always_open).graph_fields() == {'is_always_open': always_open}
    assert change(1, website='').graph_fields() == {'website': ''}


@pytest.mark.parametrize('values', [
    {'website': None}, {'website': 123}, {'website': 'blueox179.com'},
    {'website': '//blueox179.com'}, {'website': 'javascript:alert(1)'},
    {'website': 'file:///private'}, {'website': 'https://owner:password@example.com'},
    {'website': 'https://owner@example.com'}, {'website': 'https://example.com:444'},
    {'website': 'https://example.com:bad'}, {'website': 'https:///missing-host'},
    {'website': 'https://example.com/bad path'}, {'website': 'https://example.com/\x00'},
    {'website': 'https://example.com/\r\nAuthorization:private'},
    {'website': 'https://example.com/\ud800'}, {'website': 'https://example.com/' + 'x' * 2048},
    {'is_always_open': None}, {'is_always_open': 'true'}, {'is_always_open': 'false'},
    {'is_always_open': 0}, {'is_always_open': 1}, {'is_always_open': []},
    {'website': WEBSITE, 'hours': {'mon_1_open': '00:00'}},
    {'website': WEBSITE, 'token': TOKEN},
])
def test_contact_input_rejects_ambiguous_values_and_unapproved_fields(values):
    with pytest.raises(ValueError):
        change(1, **values)


@pytest.mark.parametrize('always_open', [True, False])
def test_graph_form_encodes_true_and_false_without_token_or_extra_fields(always_open):
    seen = []
    def respond(request):
        seen.append(request)
        assert request.method == 'POST' and str(request.url) == 'https://graph.facebook.com/v26.0/555'
        assert request.headers['authorization'] == 'Bearer ' + DERIVED
        form = parse_qs(request.content.decode())
        assert set(form) == {'location', 'website', 'is_always_open'}
        assert json.loads(form['location'][0]) == ADDRESS
        assert form['website'] == [WEBSITE]
        assert form['is_always_open'] == ['true' if always_open else 'false']
        assert DERIVED not in str(request.url) and DERIVED.encode() not in request.content
        return httpx.Response(200, json={'success': True})
    fields = change(1, location=ADDRESS, website=WEBSITE, is_always_open=always_open).graph_fields()
    assert graph_call(respond, lambda graph: graph.update_page(DERIVED, '555', fields)) is True
    assert len(seen) == 1


def test_contact_service_uses_exact_page_source_and_keeps_cache_until_readback(contact):
    manager, graph, cid = contact
    before = manager.store.page('555')
    body = change(cid, location=ADDRESS, website=WEBSITE, is_always_open=True)
    result = immediate(manager.edit_page('555', body))
    assert result['outcome'] == 'accepted' and result['ok'] is True
    assert graph.calls == [('identity', DERIVED), ('update', DERIVED, '555', body.graph_fields())]
    assert manager.store.page('555') == before
    calls = deepcopy(graph.calls)
    assert immediate(manager.edit_page('555', body)) == result and graph.calls == calls
    altered = PageEdit(connection_id=cid, request_id=body.request_id, website='https://example.test')
    with pytest.raises(AppError):
        immediate(manager.edit_page('555', altered))
    assert graph.calls == calls


@pytest.mark.parametrize('error,expected', [
    (GraphError('permission_limited', 200), 'failed'),
    (GraphMutationUncertain(), 'unknown'),
])
def test_contact_failure_or_uncertainty_is_terminal_and_never_replayed(contact, error, expected):
    manager, graph, cid = contact
    graph.update_error = error
    body = change(cid, website=WEBSITE, is_always_open=False)
    result = immediate(manager.edit_page('555', body))
    assert result['outcome'] == expected
    calls = deepcopy(graph.calls)
    assert sum(call[0] == 'update' for call in calls) == 1
    assert immediate(manager.edit_page('555', body)) == result and graph.calls == calls
    assert all(secret not in json.dumps(result) + json.dumps(manager.store.events()) for secret in (TOKEN, DERIVED))


@pytest.mark.parametrize('change_binding', ['foreign_identity', 'rotate_source'])
def test_contact_source_change_or_wrong_identity_prevents_write(contact, change_binding):
    manager, graph, cid = contact
    if change_binding == 'foreign_identity':
        graph.ids[DERIVED] = '777'
    else:
        graph.on_identity = lambda: manager.save_connection(
            ConnectionUpdate(label='Rotated', token_type='system_user', token=OTHER, revision=manager.store.connection(cid)["revision"]), cid)
    result = immediate(manager.edit_page('555', change(cid, website=WEBSITE, is_always_open=True)))
    assert result['outcome'] == 'failed' and not any(call[0] == 'update' for call in graph.calls)


@pytest.mark.parametrize('value', [None, False, True])
def test_edit_options_keep_unknown_and_false_hours_separate(contact, value):
    manager, graph, cid = contact
    graph.current.update(website=WEBSITE, is_always_open=value)
    result = immediate(manager.edit_options('555', PageEditSource(connection_id=cid)))
    assert result['current']['website'] == WEBSITE
    assert result['current']['is_always_open'] is value
    assert {'website', 'is_always_open'} <= set(result['supported_fields'])
    graph.unavailable = ['website', 'is_always_open']
    result = immediate(manager.edit_options('555', PageEditSource(connection_id=cid)))
    assert not {'website', 'is_always_open'}.intersection(result['supported_fields'])
    assert not any(call[0] == 'update' for call in graph.calls)


@pytest.mark.parametrize('value', [False, True])
def test_refresh_caches_exact_boolean_without_turning_false_into_unknown(contact, value):
    manager, graph, cid = contact
    graph.details_value['is_always_open'] = value
    result = immediate(manager.refresh('555', PageRefresh(connection_id=cid, include_posts=False), 'UTC'))
    assert result['page']['details']['website'] == WEBSITE
    assert result['page']['details']['is_always_open'] is value


@pytest.mark.parametrize('value', [None, 0, 1, 'true'])
def test_missing_or_malformed_hours_are_not_invented_as_closed(contact, value):
    manager, graph, cid = contact
    if value is not None:
        graph.details_value['is_always_open'] = value
    result = immediate(manager.refresh('555', PageRefresh(connection_id=cid, include_posts=False), 'UTC'))
    assert 'is_always_open' not in result['page']['details']


def test_contact_fields_are_requested_by_both_details_readers():
    seen = []
    def respond(request):
        fields = request.url.params['fields']
        seen.append(fields)
        assert {'website', 'is_always_open'} <= set(fields.split(','))
        return httpx.Response(200, json={'id': '555', 'bio': 'Fixture bio',
                                        'website': WEBSITE, 'is_always_open': False})
    details, warning = graph_call(respond, lambda graph: graph.details(DERIVED, '555'))
    assert warning is None and details['is_always_open'] is False
    options, unavailable = graph_call(respond, lambda graph: graph.edit_details(DERIVED, '555'))
    assert unavailable == [] and options['website'] == WEBSITE and options['is_always_open'] is False
    assert len(seen) == 2
