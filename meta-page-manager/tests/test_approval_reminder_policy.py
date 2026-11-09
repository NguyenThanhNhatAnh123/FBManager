"""No-deadline decision policy uses frozen metadata, not fake future dates."""
from datetime import timedelta,datetime
import json

import pytest

from test_publication_approvals import fixture,decide
from page_manager.publication_approvals import (
    REMIND_UNTIL_DECISION,normalize_binding,review_deadline,review_deadline_passed,review_is_open,
)
from page_manager.reel_inputs import TokenReelInput
from uuid import uuid4


def test_pending_remains_reviewable_days_after_due_without_changing_binding(fixture):
    ledger,binding,now=fixture
    value=binding|{'approval_policy':REMIND_UNTIL_DECISION}
    first=ledger.ensure(value);assert first['required'] and not first['can_publish']
    assert first['approve_by'] is None and first['publish_window_end'] is None
    with ledger.store.connect() as db:
        raw=db.execute('SELECT * FROM publication_approval_requests').fetchone()
        assert raw['approve_by']==value['scheduled_at']
        assert review_deadline(raw) is None
    now[0]+=timedelta(days=15)
    ticket=ledger.get(first['request_id'])
    assert ticket['state']=='pending' and review_is_open(ticket,now[0])
    assert not review_deadline_passed(ticket,now[0]) and ledger.pending()['total']==1
    assert ledger.ensure(value)['binding_hash']==first['binding_hash']
    assert ledger.verdict(value)['can_publish'] is False


def test_late_approval_executes_from_actual_decision_and_replay_never_extends(fixture):
    ledger,binding,now=fixture;value=binding|{'approval_policy':REMIND_UNTIL_DECISION}
    ticket=ledger.ensure(value);now[0]+=timedelta(days=2)
    accepted=decide(ledger,ticket)
    assert accepted['approved_at']==now[0].isoformat()
    assert datetime.fromisoformat(accepted['publish_window_end'])==now[0]+timedelta(minutes=30)
    assert ledger.require(value)['can_publish']
    now[0]+=timedelta(minutes=2)
    assert decide(ledger,ticket)==accepted


def test_early_approval_keeps_future_due_time_and_window(fixture):
    ledger,binding,now=fixture;value=binding|{'approval_policy':REMIND_UNTIL_DECISION}
    ticket=ledger.ensure(value);accepted=decide(ledger,ticket)
    assert datetime.fromisoformat(accepted['publish_window_end'])==datetime.fromisoformat(value['scheduled_at'])+timedelta(minutes=30)


@pytest.mark.parametrize('decision',['reject','cancel'])
def test_rejected_or_cancelled_pending_policy_stays_terminal(fixture,decision):
    ledger,binding,now=fixture;value=binding|{'approval_policy':REMIND_UNTIL_DECISION}
    ticket=ledger.ensure(value);now[0]+=timedelta(days=1)
    if decision=='reject':decide(ledger,ticket,'reject')
    else:ledger.withdraw('token',value['job_id'])
    assert ledger.pending()['total']==0
    assert not review_is_open(ledger.get(ticket['request_id']),now[0])
    assert not ledger.ensure(value)['can_publish']


def test_new_immediate_requests_need_no_cutoff_but_old_explicit_cutoff_keeps_legacy():
    body={'request_id':str(uuid4()),'page_id':'555','connection_id':1,'asset_id':'a'*32,
          'publish_mode':'on_approval','timezone':'America/Los_Angeles'}
    new=TokenReelInput(**body)
    assert new.approval_policy==REMIND_UNTIL_DECISION and new.approve_by is None
    old=TokenReelInput(**body,approve_by='2030-01-01T14:00:00Z',approval_policy=None)
    assert old.approval_policy is None
    with pytest.raises(ValueError):TokenReelInput(**body,approval_policy=REMIND_UNTIL_DECISION,approve_by='2030-01-01T14:00:00Z')


def test_binding_policy_is_immutable_and_unknown_values_fail(fixture):
    ledger,binding,_=fixture
    ledger.configure(True,ledger.settings()['gate_revision'])
    original=ledger.ensure(binding)
    new=ledger.ensure(binding|{'approval_policy':REMIND_UNTIL_DECISION})
    assert new['binding_hash']!=original['binding_hash']
    assert ledger.get(original['request_id'])['state']=='cancelled'
    for bad in (None,False,'forever',''):
        with pytest.raises(Exception):normalize_binding(binding|{'approval_policy':bad})
