"""Bounded publishing eligibility metadata; absent proof never grants posting."""
import re


REASONS = frozenset({'page_information_required', 'page_identity_unverified', 'page_policy_unavailable'})


def public_posting_policy(value, *, page_id_hint=None):
    if not isinstance(value, dict):
        value = {}
    information = value.get('information_status')
    if information not in ('updated', 'not_updated'):
        information = 'unknown'
    identity = value.get('posting_page_id', value.get('page_id', page_id_hint))
    if not isinstance(identity, str) or not re.fullmatch(r'[1-9][0-9]{0,31}', identity):
        identity = None
    revision = value.get('posting_revision', value.get('revision'))
    if type(revision) is not int or not 0 <= revision <= 2**53 - 1:
        revision = None
    reason = value.get('posting_block_reason', value.get('reason'))
    allowed = (value.get('can_publish') is True and information == 'updated'
               and identity is not None and reason is None and revision is not None and revision > 0)
    if allowed:
        reason = None
    elif not isinstance(reason, str) or reason not in REASONS:
        reason = ('page_information_required' if information == 'not_updated'
                  else 'page_identity_unverified' if identity is None else 'page_policy_unavailable')
    return {'information_status': information, 'can_publish': allowed,
            'posting_block_reason': reason, 'posting_page_id': identity,
            'posting_revision': revision}
