"""Frozen approve-all metadata, synthetic tickets and temporary SQLite only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest

from page_manager.errors import AppError
from page_manager.telegram_approval_batches import TelegramApprovalBatches
from test_telegram_approval import f, enabled, CHAT, USER


@pytest.fixture
def group(f):
    enabled(f)
    tickets = [f.ticket]
    for index in range(2, 24):
        value = f.ledger.ensure({**f.binding, 'job_id': index, 'page_id': str(10000 + index)})
        tickets.append(f.ledger.get(value['request_id']))
    with f.store.connect() as db:
        for index, ticket in enumerate(tickets):
            db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                (ticket['request_id'], ticket['binding_hash'], 2, hashlib.sha256(str(index).encode()).hexdigest(),
                 'sent', CHAT, 1000 + index, 2000 + index, None, f.now[0].isoformat()))
    f.calls.clear()
    return SimpleNamespace(f=f, service=TelegramApprovalBatches(f.store, f.ledger, clock=lambda: f.now[0]), tickets=tickets)


def freeze(group, count=2, **patch):
    return group.service.freeze([item['request_id'] for item in group.tickets[:count]],
                                **({'revision': 3, 'policy_revision': 2, 'chat_id': CHAT} | patch))


def sent(group, value):
    group.service.begin_send(value['batch_id'])
    return group.service.sent(value['batch_id'], 3000)


def decide(group, value, **patch):
    return group.service.decide(value['nonce'], **({'revision': 3, 'policy_revision': 2,
        'actor_id': int(USER), 'chat_id': int(CHAT), 'update_id': 500, 'message_id': 3000} | patch))


def states(group):
    return [group.f.ledger.get(item['request_id'])['state'] for item in group.tickets]


def decision_count(group):
    with group.f.store.connect() as db:
        return db.execute('SELECT COUNT(*) FROM publication_approval_decisions').fetchone()[0]


def test_freeze_exact_members_only_from_confirmed_individual_messages_and_no_io(group):
    value = freeze(group)
    assert value['state'] == 'draft' and value['total'] == 2 and value['scheduled_at'] is None
    assert value['request_ids'] == [item['request_id'] for item in group.tickets[:2]]
    assert value['members'][0]['binding'] == group.tickets[0]['binding']
    assert value['members'][0]['message_id'] == 2000 and value['members'][1]['video_message_id'] == 1001
    assert len(value['nonce']) == 32
    assert group.f.calls == [] and decision_count(group) == 0 and set(states(group)) == {'pending'}
    with group.f.store.connect() as db:
        row = dict(db.execute('SELECT * FROM telegram_approval_batches').fetchone())
        assert value['nonce'] not in str(row) and row['nonce_sha256'] == hashlib.sha256(value['nonce'].encode()).hexdigest()
    assert 'nonce' not in group.service.get(value['batch_id'])
    assert 'nonce' not in str(group.service.recent()) and 'callback_data' not in str(group.service.recent())


def test_candidates_filter_before_limit_and_never_add_tickets_to_existing_group(group):
    value = freeze(group, 20)
    result = group.service.candidates(2, CHAT)
    assert set(result) == {item['request_id'] for item in group.tickets[20:]}
    sent(group, value)
    approved = decide(group, value)
    assert approved['request_ids'] == value['request_ids'] and approved['total'] == 20
    assert states(group) == ['approved'] * 20 + ['pending'] * 3
    assert decision_count(group) == 20 and group.f.calls == []


@pytest.mark.parametrize('count', [0, 1, 21])
def test_freeze_requires_two_to_twenty(group, count):
    with pytest.raises(AppError): freeze(group, count)
    assert group.service.recent() == []


def test_duplicate_and_non_uuid_members_never_create_partial_group(group):
    for ids in ([group.tickets[0]['request_id']] * 2, [group.tickets[0]['request_id'], 'bad']):
        with pytest.raises(AppError):
            group.service.freeze(ids, revision=3, policy_revision=2, chat_id=CHAT)
    assert group.service.recent() == []


@pytest.mark.parametrize('state', ['draft', 'sending_video', 'sending_buttons', 'uncertain', 'blocked', 'decided'])
def test_freeze_rejects_any_unconfirmed_or_already_decided_delivery(group, state):
    with group.f.store.connect() as db:
        db.execute('UPDATE telegram_approval_delivery SET state=? WHERE request_id=?', (state, group.tickets[1]['request_id']))
    with pytest.raises(AppError): freeze(group)
    assert group.service.recent() == [] and decision_count(group) == 0


@pytest.mark.parametrize('patch', [{'chat_id': '-100999'}, {'binding_hash': 'c' * 64}, {'video_message_id': None}, {'message_id': None}])
def test_freeze_requires_exact_individual_delivery_binding_and_receipt(group, patch):
    with group.f.store.connect() as db:
        db.execute('UPDATE telegram_approval_delivery SET ' + ','.join(key + '=?' for key in patch) + ' WHERE request_id=?',
                   (*patch.values(), group.tickets[1]['request_id']))
    with pytest.raises(AppError): freeze(group)
    assert group.service.recent() == []


def test_freeze_excludes_trial_mode_and_mismatched_connection_policy_chat(group):
    for patch in ({'revision': 2}, {'policy_revision': 1}, {'chat_id': '-100999'}):
        with pytest.raises(AppError): freeze(group, **patch)
    group.f.service.configure({'revision': 3, 'enabled': False})
    with pytest.raises(AppError): freeze(group, revision=4)
    assert group.service.recent() == []


@pytest.mark.parametrize('state', ['draft', 'sending', 'sent', 'uncertain'])
def test_open_or_ambiguous_group_members_cannot_join_a_second_group(group, state):
    value = freeze(group)
    if state != 'draft': group.service.begin_send(value['batch_id'])
    if state == 'sent': group.service.sent(value['batch_id'], 3000)
    if state == 'uncertain': group.service.uncertain(value['batch_id'])
    with pytest.raises(AppError) as error: freeze(group)
    assert error.value.code == 'telegram_batch_already_grouped'
    assert len(group.service.recent()) == 1
    candidates = group.service.candidates(2, CHAT)
    assert len(candidates) == 20 and len(set(candidates)) == 20
    assert set(candidates) <= {item['request_id'] for item in group.tickets[2:]}


def test_send_latch_is_once_and_restart_cannot_retry_ambiguous_summary(group):
    value = freeze(group); group.service.begin_send(value['batch_id'])
    restarted = TelegramApprovalBatches(group.f.store, group.f.ledger, clock=lambda: group.f.now[0])
    with pytest.raises(AppError): restarted.begin_send(value['batch_id'])
    restarted.uncertain(value['batch_id'])
    with pytest.raises(AppError): restarted.sent(value['batch_id'], 3000)
    with pytest.raises(AppError): decide(group, value)
    assert restarted.get(value['batch_id'])['state'] == 'uncertain' and decision_count(group) == 0


def test_confirmed_summary_receipt_is_idempotent_and_exact(group):
    value = freeze(group)
    with pytest.raises(AppError): group.service.sent(value['batch_id'], 3000)
    sent(group, value)
    assert group.service.sent(value['batch_id'], 3000)['state'] == 'sent'
    with pytest.raises(AppError): group.service.sent(value['batch_id'], 3001)
    with pytest.raises(AppError): group.service.begin_send(value['batch_id'])


def test_approve_all_is_durable_idempotent_and_canonical_event_is_reserved(group):
    value = freeze(group); sent(group, value)
    first = decide(group, value)
    assert first['state'] == 'approved' and first['replayed'] is False
    assert states(group) == ['approved', 'approved'] + ['pending'] * 21
    assert decision_count(group) == 2
    restarted = TelegramApprovalBatches(group.f.store, group.f.ledger, clock=lambda: group.f.now[0])
    group.service = restarted
    assert decide(group, value)['replayed'] is True and decision_count(group) == 2
    with pytest.raises(AppError): decide(group, value, update_id=501)
    foreign = group.tickets[2]
    with pytest.raises(AppError):
        group.f.ledger.decide(foreign['request_id'], foreign['binding_hash'], 'approve',
            actor_id=int(USER), chat_id=int(CHAT), update_id=500, message_id=3000)
    assert group.f.ledger.get(foreign['request_id'])['state'] == 'pending'


@pytest.mark.parametrize('patch', [{'revision': 2}, {'policy_revision': 1}, {'actor_id': 777},
    {'actor_id': True}, {'chat_id': '-100999'}, {'message_id': 3001}, {'update_id': True}])
def test_callback_identity_scope_and_confirmation_are_exact(group, patch):
    value = freeze(group); sent(group, value)
    with pytest.raises(AppError): decide(group, value, **patch)
    assert set(states(group)) == {'pending'} and decision_count(group) == 0


@pytest.mark.parametrize('change', ['expired', 'cancelled', 'binding_changed', 'approved_individually',
    'delivery_hash', 'delivery_message', 'delivery_uncertain', 'configuration', 'gate'])
def test_any_changed_member_rejects_whole_group_without_partial_approval(group, change):
    value = freeze(group); sent(group, value); ticket = group.tickets[1]
    if change == 'expired': group.f.now[0] += timedelta(hours=1)
    elif change == 'cancelled': group.f.ledger.withdraw('token', 2)
    elif change == 'binding_changed': group.f.ledger.ensure({**ticket['binding'], 'caption': 'Changed'})
    elif change == 'approved_individually':
        group.f.ledger.decide(ticket['request_id'], ticket['binding_hash'], 'approve',
            actor_id=int(USER), chat_id=int(CHAT), update_id=499, message_id=2001)
    elif change == 'configuration': group.f.service.configure({'revision': 3, 'enabled': False})
    elif change == 'gate': group.f.ledger.configure(False, 2)
    else:
        field, changed = {'delivery_hash': ('binding_hash', 'c' * 64), 'delivery_message': ('message_id', 8888),
                          'delivery_uncertain': ('state', 'uncertain')}[change]
        with group.f.store.connect() as db:
            db.execute('UPDATE telegram_approval_delivery SET ' + field + '=? WHERE request_id=?', (changed, ticket['request_id']))
    before = states(group); before_count = decision_count(group)
    with pytest.raises(AppError): decide(group, value)
    assert states(group) == before and decision_count(group) == before_count
    assert group.service.get(value['batch_id'])['state'] == 'sent'


def test_expiry_during_summary_http_stores_receipt_but_never_authorizes_callback(group):
    value = freeze(group); group.service.begin_send(value['batch_id'])
    group.f.now[0] += timedelta(hours=1)
    assert group.service.sent(value['batch_id'], 3000)['state'] == 'sent'
    with pytest.raises(AppError): decide(group, value)
    assert set(states(group)) == {'expired'} and decision_count(group) == 0


def test_group_deadline_is_earliest_member_and_later_ticket_cannot_be_partially_approved(group):
    first = group.tickets[0]
    replacement = group.f.ledger.ensure({**first['binding'],
        'scheduled_at': (group.f.now[0] + timedelta(minutes=10)).isoformat()})
    new_ticket = group.f.ledger.get(replacement['request_id'])
    group.tickets[0] = new_ticket
    with group.f.store.connect() as db:
        db.execute('INSERT INTO telegram_approval_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
            (new_ticket['request_id'], new_ticket['binding_hash'], 2, 'e' * 64,
             'sent', CHAT, 8000, 8001, None, group.f.now[0].isoformat()))
    value = freeze(group); sent(group, value)
    assert value['approve_by'] == new_ticket['approve_by']
    group.f.now[0] += timedelta(minutes=10)
    assert states(group)[:2] == ['expired', 'pending']
    with pytest.raises(AppError): decide(group, value)
    assert states(group)[:2] == ['expired', 'pending'] and decision_count(group) == 0


@pytest.mark.parametrize('nonce', ['', 'A' * 31, 'A' * 33, '!' * 32, 'A' * 32])
def test_unknown_or_malformed_summary_nonce_never_decides(group, nonce):
    value = freeze(group); sent(group, value)
    with pytest.raises(AppError): decide(group, value | {'nonce': nonce})
    assert set(states(group)) == {'pending'} and decision_count(group) == 0


def test_current_request_binding_corruption_and_frozen_member_corruption_fail_closed(group):
    value = freeze(group); sent(group, value)
    with group.f.store.connect() as db:
        binding = {**group.tickets[1]['binding'], 'caption': 'tampered'}
        db.execute('UPDATE publication_approval_requests SET binding=? WHERE request_id=?',
                   (json.dumps(binding), group.tickets[1]['request_id']))
    with pytest.raises(AppError): decide(group, value)
    assert decision_count(group) == 0
    with group.f.store.connect() as db:
        db.execute('UPDATE telegram_approval_batch_members SET position=99 WHERE batch_id=? AND position=1', (value['batch_id'],))
    with pytest.raises(AppError): group.service.get(value['batch_id'])
    assert decision_count(group) == 0


def test_existing_single_decision_event_prevents_group_callback_reuse(group):
    value = freeze(group); sent(group, value); foreign = group.tickets[2]
    group.f.ledger.decide(foreign['request_id'], foreign['binding_hash'], 'approve',
        actor_id=int(USER), chat_id=int(CHAT), update_id=500, message_id=3000)
    with pytest.raises(AppError): decide(group, value)
    assert states(group)[:2] == ['pending', 'pending'] and decision_count(group) == 1


def test_sql_failure_mid_batch_rolls_back_all_member_decisions(group):
    value = freeze(group); sent(group, value)
    with group.f.store.connect() as db:
        db.execute("""CREATE TRIGGER fail_second_batch_decision BEFORE UPDATE ON publication_approval_requests
            WHEN NEW.job_id=2 AND NEW.state='approved' BEGIN SELECT RAISE(ABORT,'fixture failure'); END""")
    with pytest.raises(Exception): decide(group, value)
    assert set(states(group)) == {'pending'} and decision_count(group) == 0
    assert group.service.get(value['batch_id'])['state'] == 'sent'


def test_competing_group_freezes_cannot_share_members(group):
    def run(_):
        try: return freeze(group)['batch_id']
        except AppError as error: return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(run, range(2)))
    assert result.count('telegram_batch_already_grouped') == 1 and len(group.service.recent()) == 1


def test_competing_callbacks_cannot_decide_group_twice(group):
    value = freeze(group); sent(group, value)
    def run(update_id):
        try: return decide(group, value, update_id=update_id)['state']
        except AppError as error: return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(run, (500, 501)))
    assert result.count('approved') == 1 and result.count('telegram_batch_decision_conflict') == 1
    assert decision_count(group) == 2 and states(group)[:2] == ['approved', 'approved']
