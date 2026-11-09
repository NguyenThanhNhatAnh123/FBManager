"""Eligibility is data from the backend, never inferred from names or cached UI."""
import json

import pytest

from via_mcp.page_tools import _page
from via_mcp.posting_policy import public_posting_policy


PAGE = '61590000000001'


@pytest.mark.parametrize('payload', [
    {}, {'information_status': 'updated'}, {'can_publish': True},
    {'can_publish': True, 'information_status': 'not_updated', 'page_id': PAGE},
    {'can_publish': 'true', 'information_status': 'updated', 'page_id': PAGE},
    {'can_publish': 1, 'information_status': 'updated', 'page_id': PAGE},
    {'can_publish': True, 'information_status': 'updated', 'page_id': PAGE,
     'posting_block_reason': 'page_information_required'},
    {'can_publish': True, 'information_status': 'updated', 'page_id': 'Page Name'},
    {'can_publish': True, 'information_status': 'updated', 'page_id': '0'},
    {'can_publish': True, 'information_status': 'updated', 'page_id': '0001'},
    {'can_publish': True, 'information_status': 'updated', 'page_id': PAGE, 'revision': 0},
    {'can_publish': True, 'information_status': 'updated', 'page_id': PAGE, 'revision': True},
    {'can_publish': True, 'information_status': 'updated', 'page_id': 'https://facebook.com/fixture'},
])
def test_no_missing_ambiguous_or_coerced_proof_can_enable_posting(payload):
    result = public_posting_policy(payload)
    assert result['can_publish'] is False
    assert result['posting_block_reason'] is not None


def test_exact_identity_updated_proof_and_boolean_are_required():
    result = public_posting_policy({'page_id': PAGE, 'information_status': 'updated',
                                   'can_publish': True, 'revision': 4})
    assert result == {'posting_page_id': PAGE, 'information_status': 'updated',
                      'can_publish': True, 'posting_block_reason': None, 'posting_revision': 4}


@pytest.mark.parametrize('reason', ['page_information_required', 'page_identity_unverified', 'page_policy_unavailable'])
def test_only_bounded_reason_codes_are_projected(reason):
    assert public_posting_policy({'reason': reason})['posting_block_reason'] == reason


def test_private_receipts_fingerprints_and_raw_errors_are_never_projected():
    payload = {'id': PAGE, 'information_status': 'not_updated', 'can_publish': False,
               'posting_block_reason': {'secret': 'private-value'}, 'revision': True,
               'receipt_id': 'private-value', 'field_fingerprints': {'bio': 'private-value'},
               'error': 'private-value', 'token': 'private-value'}
    result = _page(payload)
    assert result['can_publish'] is False
    assert result['posting_page_id'] == PAGE
    assert result['posting_revision'] is None
    assert 'private-value' not in json.dumps(result)
