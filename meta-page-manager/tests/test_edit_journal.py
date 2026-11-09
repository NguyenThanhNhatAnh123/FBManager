"""Independent journal invariants using disposable SQLite only."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from threading import Event, Thread
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from page_manager.store import Store


DIGEST = hashlib.sha256(b'offline edit fixture').hexdigest()


@pytest.fixture
def journal(tmp_path):
    store = Store(tmp_path)
    connections = []
    for label in ('First fixture', 'Second fixture'):
        cid = store.save_connection({'label': label, 'token_type': 'page'}, 'encrypted-fixture-only')
        store.sync(cid, 1, [
            {'id': pid, 'name': 'Fixture ' + pid, 'sources': ['assigned_pages']}
            for pid in ('555', '777')
        ], True, 'working', None, {})
        connections.append(cid)
    return store, connections, tmp_path


def begin(store, cid, rid, **changes):
    values = dict(revision=1, page_id='555', request_id=rid, kind='avatar', payload_hash=DIGEST)
    values.update(changes)
    return store.edit_begin(cid, **values)


def record(store, rid):
    with store.connect() as db:
        return dict(db.execute('SELECT * FROM page_edits WHERE request_id=?', (str(rid),)).fetchone())


def test_unfinished_request_survives_restart_as_unknown_without_restarting(journal):
    store, (cid, _), root = journal
    rid = uuid4()
    assert begin(store, cid, rid) == {'replayed': False, 'outcome': 'started'}
    store.edit_checkpoint(rid, {'uploaded_photo_id': '9001'})
    before = record(store, rid)
    restarted = Store(root)
    result = begin(restarted, cid, rid)
    assert result['replayed'] and not result['ok'] and result['outcome'] == 'unknown'
    assert result['requires_refresh'] is True
    assert record(restarted, rid) == before
    assert 'checkpoint' not in result and 'uploaded_photo_id' not in json.dumps(result)


@pytest.mark.parametrize('outcome', ['accepted', 'failed', 'partial', 'unknown'])
def test_finished_result_is_immutable_and_replayed_after_restart(journal, outcome):
    store, (cid, _), root = journal
    rid = uuid4()
    begin(store, cid, rid)
    store.edit_checkpoint(rid, {'uploaded_photo_id': '9001'})
    first = store.edit_finish(rid, outcome, 'Fixed safe fixture result', requires_refresh=outcome != 'failed')
    before = record(store, rid)
    restarted = Store(root)
    assert begin(restarted, cid, rid) == {'replayed': True, **first}
    assert restarted.edit_finish(rid, 'unknown', 'Replacement must not win', not first['requires_refresh']) == first
    with pytest.raises(AppError) as error:
        restarted.edit_checkpoint(rid, {'uploaded_photo_id': '9002'})
    assert error.value.code == 'edit_finished'
    assert record(restarted, rid) == before


@pytest.mark.parametrize('changed', ['connection', 'page', 'kind', 'hash'])
def test_request_id_cannot_cross_authorized_connection_page_kind_or_payload(journal, changed):
    store, (first, second), _ = journal
    rid = uuid4()
    begin(store, first, rid)
    before = record(store, rid)
    options = {'page_id': '777'} if changed == 'page' else {'kind': 'cover'} if changed == 'kind' else {'payload_hash': '0' * 64} if changed == 'hash' else {}
    with pytest.raises(AppError) as error:
        begin(store, second if changed == 'connection' else first, rid, **options)
    assert error.value.code == 'edit_conflict'
    assert record(store, rid) == before


@pytest.mark.parametrize('transition', ['rotate', 'disconnect', 'revoke_page'])
def test_stale_or_revoked_source_cannot_begin_or_replay_mutations(journal, transition):
    store, (cid, _), _ = journal
    rid = uuid4()
    begin(store, cid, rid)
    before = record(store, rid)
    if transition == 'rotate':
        store.save_connection({'label': 'Rotated fixture', 'token_type': 'page', 'revision': store.connection(cid)["revision"]}, 'new-encrypted-fixture', cid)
    elif transition == 'disconnect':
        store.disconnect(cid)
    else:
        store.sync(cid, 1, [], True, 'working', None, {})
    for candidate in (rid, uuid4()):
        with pytest.raises(AppError) as error:
            begin(store, cid, candidate)
        assert error.value.code == ('page_access_required' if transition == 'revoke_page' else 'stale_connection')
    assert record(store, rid) == before
    if transition == 'rotate':
        store.sync(cid, 2, [{'id': '555', 'name': 'Fixture 555', 'sources': ['assigned_pages']}], True, 'working', None, {})
        with pytest.raises(AppError) as error:
            begin(store, cid, rid, revision=2)
        assert error.value.code == 'edit_conflict'


@pytest.mark.parametrize('values', [
    {'uploaded_photo_id': 123}, {'uploaded_photo_id': True}, {'uploaded_photo_id': ''},
    {'uploaded_photo_id': '１２３'}, {'uploaded_photo_id': '1' * 33},
    {'uploaded_photo_id': 'https://example.test/123'}, {'uploaded_photo_id': '../123'},
    {'uploaded_photo_id': '123', 'token': 'private-fixture'}, {'image': b'fixture'}, ['123'],
])
def test_checkpoint_accepts_only_ascii_photo_id_without_secrets_or_image_data(journal, values):
    store, (cid, _), _ = journal
    rid = uuid4()
    begin(store, cid, rid)
    with pytest.raises(AppError) as error:
        store.edit_checkpoint(rid, values)
    assert error.value.code == 'invalid_edit_checkpoint'
    assert record(store, rid)['checkpoint'] == '{}'


def test_uploaded_photo_id_is_idempotent_but_cannot_be_replaced(journal):
    store, (cid, _), _ = journal
    rid = uuid4()
    begin(store, cid, rid)
    store.edit_checkpoint(rid, {'uploaded_photo_id': '9' * 32})
    store.edit_checkpoint(rid, {'uploaded_photo_id': '9' * 32})
    before = record(store, rid)
    with pytest.raises(AppError):
        store.edit_checkpoint(rid, {'uploaded_photo_id': '9002'})
    assert record(store, rid) == before
    assert json.loads(before['checkpoint']) == {'uploaded_photo_id': '9' * 32}


def test_late_upload_checkpoint_can_record_uncertainty_after_disconnect(journal):
    store, (cid, _), _ = journal
    rid = uuid4()
    begin(store, cid, rid)
    store.disconnect(cid)
    store.edit_checkpoint(rid, {'uploaded_photo_id': '9001'})
    result = store.edit_finish(rid, 'unknown', 'Connection changed after upload')
    assert result['outcome'] == 'unknown' and result['requires_refresh']
    assert json.loads(record(store, rid)['checkpoint']) == {'uploaded_photo_id': '9001'}


def test_competing_store_cannot_overwrite_terminal_result_during_read_write_gap(journal, monkeypatch):
    store, (cid, _), root = journal
    rid = uuid4()
    begin(store, cid, rid)
    rival = Store(root)
    first_read, release = Event(), Event()
    result, errors = [], []
    original, rival_original = store.connect, rival.connect

    class Cursor:
        def __init__(self, cursor): self.cursor = cursor
        def fetchone(self):
            row = self.cursor.fetchone()
            first_read.set()
            if not release.wait(5): raise AssertionError('Fixture release timed out')
            return row

    class Connection:
        def __init__(self, db): self.db = db
        def execute(self, sql, parameters=()):
            cursor = self.db.execute(sql, parameters)
            return Cursor(cursor) if sql.startswith('SELECT * FROM page_edits') else cursor

    @contextmanager
    def gated():
        with original() as db: yield Connection(db)

    @contextmanager
    def no_wait():
        with rival_original() as db:
            db.execute('PRAGMA busy_timeout=0')
            yield db

    def finish():
        try: result.append(store.edit_finish(rid, 'accepted', 'First result'))
        except BaseException as error: errors.append(error)

    monkeypatch.setattr(store, 'connect', gated)
    monkeypatch.setattr(rival, 'connect', no_wait)
    worker = Thread(target=finish, daemon=True)
    worker.start()
    try:
        assert first_read.wait(5)
        # Zero busy timeout makes this deterministic: a journal finalizer must
        # own the write reservation already when it reads an unfinished row.
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            rival.edit_finish(rid, 'failed', 'Rival must not overwrite')
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert result[0]['outcome'] == 'accepted'
    assert rival.edit_finish(rid, 'failed', 'Late rival result') == result[0]
