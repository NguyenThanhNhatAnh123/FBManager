"""Host shares only bounded eligibility metadata, without reading the child DB."""
from types import SimpleNamespace

import pytest

from app.services.page_workspace import PageWorkspace


def ready():
    return {'page_id': '555', 'can_publish': True, 'information_status': 'updated', 'reason': None, 'revision': 2}


def test_unavailable_workspace_and_missing_owner_are_blocked_without_creating_data(tmp_path):
    workspace = PageWorkspace(tmp_path / 'chrome-manager', 8000)
    for application in (None, SimpleNamespace(state=SimpleNamespace())):
        workspace.application = application
        verdict = workspace.posting_eligibility('https://www.facebook.com/555')
        assert not verdict['can_publish'] and verdict['reason'] == 'page_policy_unavailable'
        assert workspace.record_chrome_setup('https://www.facebook.com/555', 'unused', {}) == verdict
    assert not (tmp_path / 'meta-page-manager').exists()


def test_only_bounded_metadata_crosses_the_workspace_boundary(tmp_path):
    seen = []
    def check(target):
        seen.append(('check', target))
        return {**ready(), 'token': 'fixture-secret', 'receipts': ['private-proof'], 'field_fingerprints': {'bio': 'a' * 64}}
    def record(*args):
        seen.append(('record', args))
        return {**ready(), 'profile_id': 999}
    workspace = PageWorkspace(tmp_path / 'chrome-manager', 8000)
    workspace.application = SimpleNamespace(state=SimpleNamespace(manager=SimpleNamespace(
        posting_eligibility=check, record_chrome_setup=record)))
    assert workspace.posting_eligibility('https://www.facebook.com/555') == ready()
    proof = {'bio': 'b' * 64}
    assert workspace.record_chrome_setup('https://www.facebook.com/555', 'receipt', proof) == ready()
    assert seen == [('check', 'https://www.facebook.com/555'),
                    ('record', ('https://www.facebook.com/555', 'receipt', proof))]
    assert not (tmp_path / 'chrome-manager').exists() and not (tmp_path / 'meta-page-manager').exists()


@pytest.mark.parametrize('changed', [
    {'page_id': None}, {'page_id': 'vanity'}, {'can_publish': 'true'}, {'revision': -1},
    {'revision': 0}, {'revision': True}, {'revision': 2**64}, {'reason': ['secret']},
    {'information_status': 'not_updated'}, {'reason': 'fixture-private-token'},
])
def test_malformed_owner_verdict_cannot_enable_posting(tmp_path, changed):
    workspace = PageWorkspace(tmp_path / 'chrome-manager', 8000)
    workspace.application = SimpleNamespace(state=SimpleNamespace(manager=SimpleNamespace(
        posting_eligibility=lambda _target: {**ready(), **changed})))
    verdict = workspace.posting_eligibility('https://www.facebook.com/555')
    assert not verdict['can_publish'] and verdict['reason'] == 'page_policy_unavailable'
    assert set(verdict) == {'page_id', 'can_publish', 'information_status', 'reason', 'revision'}


def test_owner_exception_is_redacted_and_never_triggers_a_database_fallback(tmp_path):
    def fail(*_args): raise RuntimeError('fixture-private-token')
    workspace = PageWorkspace(tmp_path / 'chrome-manager', 8000)
    workspace.application = SimpleNamespace(state=SimpleNamespace(manager=SimpleNamespace(
        posting_eligibility=fail, record_chrome_setup=fail)))
    verdict = workspace.posting_eligibility('https://www.facebook.com/555')
    assert not verdict['can_publish'] and 'fixture-private' not in str(verdict)
    assert workspace.record_chrome_setup('https://www.facebook.com/555', 'receipt', {}) == verdict
    assert not (tmp_path / 'meta-page-manager').exists()
