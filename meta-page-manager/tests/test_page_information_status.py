"""Information-change history is separate from live token access and SQL-paged."""
from contextlib import contextmanager
import json
from uuid import uuid4

import pytest

from test_list_pagination import fixture, get


def catalog(tmp_path, count=1):
    app, headers = fixture(tmp_path)
    store = app.state.manager.store
    stamp = '2030-11-01T00:00:00+00:00'
    with store.connect() as db:
        db.executemany('INSERT INTO connections(id,label,token_type,encrypted_token,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                       [(cid, 'Source '+str(cid), 'system_user', 'private-fixture-token', 'working', stamp, stamp) for cid in (1, 2)])
        db.executemany('INSERT INTO pages(id,name,details) VALUES(?,?,?)',
                       [(str(1000+i), f'Page {i:04}', json.dumps({'about': 'Already visible on Facebook'})) for i in range(count)])
        db.executemany('INSERT INTO connection_pages(connection_id,page_id,sources,active,token_revision) VALUES(?,?,?,1,1)',
                       [(cid, str(1000+i), '["assigned_pages"]') for i in range(count) for cid in (1, 2)])
    return app, headers, store


def receipt(store, page_id='1000', kind='metadata', outcome='accepted', cid=1):
    request_id = str(uuid4())
    store.edit_begin(cid, 1, page_id, request_id, kind, 'a'*64)
    if outcome != 'started':
        store.edit_finish(request_id, outcome, 'Safe fixture result')
    return request_id


@pytest.mark.parametrize('kind', ['metadata', 'text', 'avatar', 'cover'])
def test_any_accepted_edit_marks_information_updated_without_claiming_all_fields(tmp_path, kind):
    app, _, store = catalog(tmp_path)
    receipt(store, kind=kind)
    result = app.state.manager.pages(limit=10)
    assert result['total'] == 1
    page = result['items'][0]
    assert page['information_status'] == 'updated' and page['status'] == 'active'
    assert len(page['connections']) == 2
    assert app.state.manager.page('1000')['page']['information_status'] == 'updated'
    # No payload, receipt hash or image-upload checkpoint enters the projection.
    public = json.dumps(result)
    assert 'private-fixture-token' not in public and 'a'*64 not in public
    assert 'checkpoint' not in public and 'request_id' not in public


@pytest.mark.parametrize('outcome', [None, 'started', 'failed', 'partial', 'unknown'])
def test_existing_facebook_content_and_unconfirmed_edits_do_not_count(tmp_path, outcome):
    app, _, store = catalog(tmp_path)
    if outcome is not None:
        receipt(store, outcome=outcome)
    result = app.state.manager.pages(limit=10)
    assert result['items'][0]['information_status'] == 'not_updated'
    assert result['items'][0]['status'] == 'active'
    assert app.state.manager.pages(information_status='updated', limit=10)['total'] == 0
    assert app.state.manager.pages(information_status='not_updated', limit=10)['total'] == 1


def test_later_failed_or_uncertain_attempts_do_not_erase_accepted_history(tmp_path):
    app, _, store = catalog(tmp_path)
    receipt(store, kind='avatar')
    for outcome in ('failed', 'partial', 'unknown', 'started'):
        receipt(store, kind='metadata', outcome=outcome)
    assert app.state.manager.pages(limit=10)['items'][0]['information_status'] == 'updated'


@pytest.mark.parametrize('change', ['rotate', 'disconnect', 'permission_limited'])
def test_history_survives_source_changes_and_does_not_replace_access_health(tmp_path, change):
    app, _, store = catalog(tmp_path)
    receipt(store, kind='cover')
    if change == 'rotate':
        for cid in (1, 2):
            store.save_connection({'label': 'Source '+str(cid), 'token_type': 'system_user', 'revision': store.connection(cid)["revision"]}, 'rotated-fixture-token', cid)
        health = 'disconnected'
    elif change == 'disconnect':
        for cid in (1, 2):
            store.disconnect(cid)
        health = 'disconnected'
    else:
        with store.connect() as db:
            db.execute("UPDATE connections SET status='permission_limited'")
        health = 'needs_attention'
    page = app.state.manager.pages(limit=10)['items'][0]
    assert page['information_status'] == 'updated' and page['status'] == health
    assert app.state.manager.pages(information_status='updated', status=health, limit=10)['total'] == 1


def test_information_filter_is_sql_paged_counted_and_canonical_across_sources(tmp_path, monkeypatch):
    app, headers, store = catalog(tmp_path, count=201)
    for index in range(0, 200, 2):
        receipt(store, page_id=str(1000+index), kind='avatar' if index % 4 else 'metadata')
    # The same Page changed through two sources remains one canonical Page.
    receipt(store, page_id='1000', kind='cover', cid=2)
    monkeypatch.setattr(store, 'catalog', lambda: pytest.fail('Cannot hydrate the full catalog'))
    first = get(app, '/api/pages?information_status=updated&limit=10&offset=0', headers)
    last = get(app, '/api/pages?information_status=updated&limit=10&offset=95', headers)
    assert first[0] == last[0] == 200
    assert first[2]['total'] == last[2]['total'] == 100
    assert [p['id'] for p in last[2]['items']] == [str(1000+i) for i in range(190, 200, 2)]
    assert all(p['information_status'] == 'updated' for p in last[2]['items'])
    assert not {p['id'] for p in first[2]['items']} & {p['id'] for p in last[2]['items']}
    opposite = app.state.manager.pages(information_status='not_updated', limit=10, offset=100)
    assert opposite['total'] == 101 and [p['id'] for p in opposite['items']] == ['1200']
    assert app.state.manager.pages(information_status='updated', connection_id=2, limit=10)['total'] == 100
    assert app.state.manager.pages(information_status='updated', search='Page 0198', limit=10)['total'] == 1
    assert app.state.manager.pages(information_status='updated', limit=10, offset=1000)['items'] == []
    assert app.state.manager.pages(limit=10)['total'] == 201


def test_projection_uses_indexed_receipts_and_fixed_number_of_reads(tmp_path, monkeypatch):
    app, _, store = catalog(tmp_path, count=101)
    receipt(store)
    statements = []
    connect = store.connect
    @contextmanager
    def traced():
        with connect() as db:
            db.set_trace_callback(statements.append)
            yield db
    monkeypatch.setattr(store, 'connect', traced)
    small = app.state.manager.pages(limit=10)
    selects = [sql for sql in statements if sql.lstrip().upper().startswith(('SELECT', 'WITH'))]
    assert len(small['items']) == 10 and len(selects) == 3
    count_query, window_query, links_query = selects
    assert 'page_edits' not in count_query, 'Unfiltered count does not inspect edit history'
    assert 'EXISTS(SELECT 1 FROM page_edits' in window_query and 'LIMIT 10 OFFSET 0' in window_query
    assert 'IN (' in links_query
    with connect() as db:
        plan = db.execute("EXPLAIN QUERY PLAN SELECT EXISTS(SELECT 1 FROM page_edits ie WHERE ie.page_id=? AND ie.outcome='accepted')", ('1000',)).fetchall()
    assert any('page_edits_accepted_page' in row[3] for row in plan)


@pytest.mark.parametrize('value', ['active', 'accepted', 'Updated', 'unknown'])
def test_http_filter_rejects_unknown_information_state(tmp_path, value):
    app, headers, store = catalog(tmp_path)
    assert get(app, '/api/pages?information_status='+value, headers)[0] == 422
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM page_edits').fetchone()[0] == 0


def test_posting_eligible_selector_filters_before_global_count_and_pagination(tmp_path, monkeypatch):
    app, headers, store = catalog(tmp_path, count=201)
    for pid in ('1150', '1199'): receipt(store, page_id=pid)
    receipt(store, page_id='1150', cid=2)
    with store.connect() as db:
        db.execute('INSERT INTO chrome_page_setup_receipts VALUES(?,?,?,?)',
            (str(uuid4()), '1200', '{}', '2030-11-01T00:00:00Z'))
    monkeypatch.setattr(store, 'catalog', lambda: pytest.fail('Selector must not hydrate unedited Pages'))
    first = get(app, '/api/pages?limit=2&posting_eligible_only=true', headers)
    last = get(app, '/api/pages?limit=2&offset=2&posting_eligible_only=true', headers)
    assert first[0] == last[0] == 200
    assert first[2]['total'] == last[2]['total'] == 3
    assert [row['id'] for row in first[2]['items']] == ['1150', '1199']
    assert [row['id'] for row in last[2]['items']] == ['1200']
    assert all(row['can_publish'] is True and row['information_status'] == 'updated'
        for row in first[2]['items'] + last[2]['items'])
    assert get(app, '/api/pages?limit=25', headers)[2]['total'] == 201
    assert get(app, '/api/pages?limit=25&posting_eligible_only=false', headers)[2]['total'] == 201
    assert get(app, '/api/pages?posting_eligible_only=true', headers)[2]['items'] == first[2]['items'] + last[2]['items']


def test_posting_eligible_selector_uses_exact_policy_id_rules(tmp_path):
    app, headers, store = catalog(tmp_path)
    identifiers = ('0', '0001', 'abc', '123x', '9' * 33, '8' * 32)
    with store.connect() as db:
        for pid in identifiers:
            db.execute('INSERT INTO pages(id,name) VALUES(?,?)', (pid, 'Before existing Page'))
            db.execute('INSERT INTO chrome_page_setup_receipts VALUES(?,?,?,?)',
                (str(uuid4()), pid, '{}', '2030-11-01T00:00:00Z'))
    value = get(app, '/api/pages?limit=25&posting_eligible_only=true', headers)[2]
    assert value['total'] == 1 and value['items'][0]['id'] == '8' * 32
    assert value['items'][0]['can_publish'] is True
    assert get(app, '/api/pages?limit=25&information_status=updated', headers)[2]['total'] == 6
    assert get(app, '/api/pages?limit=25&information_status=not_updated&posting_eligible_only=true', headers)[2]['total'] == 0


def test_posting_selector_filter_rejects_invalid_boolean_without_changing_history(tmp_path):
    from page_manager.errors import AppError
    app, headers, store = catalog(tmp_path)
    assert get(app, '/api/pages?limit=25&posting_eligible_only=unknown', headers)[0] == 422
    with pytest.raises(AppError): app.state.manager.pages(posting_eligible_only='true')
    assert get(app, '/api/pages?limit=25', headers)[2]['total'] == 1
