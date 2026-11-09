"""Common Page eligibility uses durable accepted receipts, never catalog guesses."""
import json
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from page_manager.service import PageManager
from page_manager.store import Store


@pytest.fixture
def policy(tmp_path):
    store = Store(tmp_path / 'manager')
    cid = store.save_connection({'label': 'Synthetic source', 'token_type': 'page'}, 'encrypted-fixture-token')
    store.sync(cid, 1, [
        {'id': '555', 'name': 'Same name', 'link': 'https://www.facebook.com/FixtureShared',
         'details': {'about': 'Existing information is not an accepted edit'}, 'sources': ['assigned_pages']},
        {'id': '777', 'name': 'Same name', 'link': 'https://www.facebook.com/Other', 'sources': ['assigned_pages']},
    ], True, 'working', None, {})
    manager = PageManager.__new__(PageManager)
    manager.store = store
    return store, cid, manager


def accepted(store, cid, pid='555', outcome='accepted', kind='metadata'):
    rid = str(uuid4())
    store.edit_begin(cid, 1, pid, rid, kind, 'a' * 64)
    if outcome != 'started': store.edit_finish(rid, outcome, 'Synthetic outcome')
    return rid


def values(store, cid, rid=None):
    asset_id = 'a' * 32
    with store.connect() as db:
        if db.execute('SELECT 1 FROM reel_assets WHERE id=?', (asset_id,)).fetchone() is None:
            store.reel_asset_save({'id': asset_id, 'name': 'fixture.mp4', 'path': 'fixture-path',
                'size': 100, 'sha256': 'b' * 64, 'mime': 'video/mp4'})
    return {'request_id': rid or str(uuid4()), 'page_id': '555', 'page_name': 'Same name',
        'connection_id': cid, 'connection_label': 'Synthetic source', 'token_revision': 1,
        'asset_id': asset_id, 'asset_name': 'fixture.mp4', 'caption': 'https://blueox179.com/campaign/fixture',
        'scheduled_at': '2030-11-02T06:00:00+00:00', 'scheduled_local': '2030-11-02T06:00', 'timezone': 'UTC', 'fold': 0}


def test_synced_content_working_token_and_names_never_approve_a_page(policy):
    store, _, manager = policy
    assert store.posting_eligibility('555') == {'page_id': '555', 'can_publish': False,
        'information_status': 'not_updated', 'reason': 'page_information_required', 'revision': 0}
    with pytest.raises(AppError) as error: store.ensure_posting_ready('555')
    assert error.value.code == 'page_information_required' and error.value.status_code == 422
    page = manager.pages(limit=10)['items'][0]
    assert not page['can_publish'] and page['posting_block_reason'] == 'page_information_required'
    assert page['posting_page_id'] in ('555', '777') and page['posting_revision'] == 0


@pytest.mark.parametrize('outcome', ['started', 'failed', 'partial', 'unknown'])
def test_unconfirmed_edit_does_not_enable_posting(policy, outcome):
    store, cid, _ = policy
    accepted(store, cid, outcome=outcome)
    assert not store.posting_eligibility('555')['can_publish']


@pytest.mark.parametrize('kind', ['metadata', 'text', 'avatar', 'cover'])
def test_any_accepted_receipt_survives_restart_without_new_approval(policy, kind):
    store, cid, _ = policy
    accepted(store, cid, kind=kind)
    before = store.posting_eligibility('555')
    assert before['can_publish'] and before['revision'] == 1
    # A legacy database has no Chrome ledger. Reopening creates only the empty table.
    with store.connect() as db: db.execute('DROP TABLE chrome_page_setup_receipts')
    restarted = Store(store.root)
    assert restarted.posting_eligibility('555') == before
    assert not restarted.posting_eligibility('777')['can_publish']
    with restarted.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM chrome_page_setup_receipts').fetchone()[0] == 0


def test_verified_chrome_proof_is_durable_idempotent_and_canonical_across_sources(policy):
    store, _, manager = policy
    rid = str(uuid4()); fields = {'bio': 'b' * 64, 'avatar': 'a' * 64}
    first = store.record_chrome_setup('555', rid, fields)
    again = store.record_chrome_setup('555', rid, dict(reversed(list(fields.items()))))
    assert first == again and first['can_publish'] and first['revision'] == 1
    assert Store(store.root).posting_eligibility('555') == first
    assert not store.posting_eligibility('777')['can_publish']
    result = manager.pages(information_status='updated', limit=10)
    assert result['total'] == 1 and result['items'][0]['id'] == '555'
    page = result['items'][0]
    assert page['can_publish'] and page['posting_block_reason'] is None and page['posting_revision'] == 1
    detail = manager.page('555')['page']
    assert {key: detail[key] for key in ('information_status', 'can_publish', 'posting_block_reason', 'posting_page_id', 'posting_revision')} == {
        key: page[key] for key in ('information_status', 'can_publish', 'posting_block_reason', 'posting_page_id', 'posting_revision')}
    public = json.dumps(result)
    assert rid not in public and 'field_fingerprints' not in public and 'a' * 64 not in public
    with store.connect() as db:
        columns = {row['name'] for row in db.execute('PRAGMA table_info(chrome_page_setup_receipts)')}
        assert columns == {'receipt_id', 'page_id', 'field_fingerprints', 'created_at'}


def test_chrome_only_page_can_have_proof_without_importing_pages_or_credentials(policy):
    store, _, _ = policy
    verdict = store.record_chrome_setup('999', str(uuid4()), {'cover': 'c' * 64})
    assert verdict['can_publish'] and verdict['page_id'] == '999'
    with store.connect() as db: assert db.execute('SELECT 1 FROM pages WHERE id=?', ('999',)).fetchone() is None


@pytest.mark.parametrize('changed', ['page', 'fingerprint'])
def test_receipt_id_cannot_be_rebound(policy, changed):
    store, _, _ = policy
    rid = str(uuid4())
    store.record_chrome_setup('555', rid, {'bio': 'a' * 64})
    with pytest.raises(AppError) as error:
        store.record_chrome_setup('777' if changed == 'page' else '555', rid,
            {'bio': 'b' * 64 if changed == 'fingerprint' else 'a' * 64})
    assert error.value.code == 'chrome_setup_receipt_conflict'
    assert store.posting_eligibility('555')['revision'] == 1
    assert not store.posting_eligibility('777')['can_publish']


@pytest.mark.parametrize('pid', [None, '', '0', '00555', 'name', 555, True, '5' * 33])
def test_invalid_page_identity_is_blocked_without_catalog_or_token_fallback(policy, pid):
    store, _, _ = policy
    assert store.posting_eligibility(pid)['page_id'] is None
    with pytest.raises(AppError): store.ensure_posting_ready(pid)
    with pytest.raises(AppError): store.record_chrome_setup(pid, str(uuid4()), {'bio': 'a' * 64})


@pytest.mark.parametrize('proof', [{}, {'password': 'a' * 64}, {'bio': 'a' * 63}, {'bio': 'A' * 64},
    {'bio': False}, {'bio': 'a' * 64, 'profile_id': 'b' * 64}, None])
def test_invalid_proof_never_writes_a_receipt(policy, proof):
    store, _, _ = policy
    with pytest.raises(AppError) as error: store.record_chrome_setup('555', str(uuid4()), proof)
    assert error.value.code == 'invalid_chrome_setup_receipt'
    assert store.posting_eligibility('555')['revision'] == 0


@pytest.mark.parametrize('receipt_id', [None, 'not-a-uuid', '00000000-0000-0000-0000-000000000000',
    'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'])
def test_invalid_receipt_identity_does_not_approve_the_page(policy, receipt_id):
    store, _, _ = policy
    with pytest.raises(AppError) as error: store.record_chrome_setup('555', receipt_id, {'bio': 'a' * 64})
    assert error.value.code == 'invalid_chrome_setup_receipt'
    assert not store.posting_eligibility('555')['can_publish']


def test_optional_connection_keeps_eligibility_check_inside_the_enqueue_transaction(policy, monkeypatch):
    store, cid, _ = policy
    accepted(store, cid)
    with store.connect() as db:
        monkeypatch.setattr(store, 'connect', lambda: pytest.fail('Cannot open another policy transaction'))
        assert store.posting_eligibility('555', db=db)['can_publish']
        assert store.ensure_posting_ready('555', db=db)['revision'] == 1


def test_enqueue_rejects_unedited_page_before_insert_but_replay_is_read_only(policy, monkeypatch):
    store, cid, _ = policy
    body = values(store, cid)
    with pytest.raises(AppError) as error: store.reel_create(body)
    assert error.value.code == 'page_information_required'
    with store.connect() as db: assert db.execute('SELECT COUNT(*) FROM reel_jobs').fetchone()[0] == 0
    accepted(store, cid)
    row, replayed = store.reel_create(body)
    assert not replayed
    monkeypatch.setattr(store, 'ensure_posting_ready', lambda *_args, **_kwargs: pytest.fail('Replay is not a new publication'))
    again, replayed = store.reel_create(body)
    assert replayed and again['id'] == row['id']


@pytest.mark.parametrize('target', ['555', 'https://www.facebook.com/555', 'https://facebook.com/555/',
    'https://m.facebook.com/profile.php?id=555', 'https://www.facebook.com/FixtureShared',
    'https://facebook.com/FixtureShared/'])
def test_numeric_or_exact_unique_cached_alias_resolves_to_stable_id(policy, target):
    store, cid, manager = policy
    accepted(store, cid)
    assert manager.posting_eligibility(target) == store.posting_eligibility('555')


@pytest.mark.parametrize('target', ['https://www.facebook.com/Same_name', 'https://www.facebook.com/unknown',
    'http://www.facebook.com/555', 'https://www.facebook.com.evil.test/555',
    'https://user:password@www.facebook.com/555', 'https://www.facebook.com/profile.php?id=555&id=777',
    'https://www.facebook.com/FixtureShared?other=1', 'https://www.facebook.com/FixtureShared#fragment'])
def test_unknown_unsafe_or_ambiguous_target_never_uses_a_name_guess(policy, target):
    store, cid, manager = policy
    accepted(store, cid)
    verdict = manager.posting_eligibility(target)
    assert not verdict['can_publish'] and verdict['page_id'] is None
    assert verdict['reason'] == 'page_identity_unverified'


def test_duplicate_alias_is_blocked_even_when_one_candidate_has_accepted_proof(policy):
    store, cid, manager = policy
    accepted(store, cid)
    with store.connect() as db: db.execute('UPDATE pages SET link=? WHERE id=?', ('https://www.facebook.com/FixtureShared', '777'))
    assert manager.posting_eligibility('https://www.facebook.com/FixtureShared')['page_id'] is None


def test_trusted_manager_callback_records_proof_only_after_resolving_identity(policy):
    store, _, manager = policy
    rid = str(uuid4())
    assert not manager.record_chrome_setup('https://www.facebook.com/unknown', rid, {'bio': 'a' * 64})['can_publish']
    assert store.posting_eligibility('777')['revision'] == 0
    assert manager.record_chrome_setup('https://www.facebook.com/Other', rid, {'bio': 'a' * 64})['can_publish']
    assert store.posting_eligibility('777')['revision'] == 1
