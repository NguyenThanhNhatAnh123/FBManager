"""Independent token scheduling inputs; no Chrome imports or live state."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from page_manager.errors import AppError
from page_manager.reel_inputs import TokenReelInput, resolve_schedule


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


@pytest.mark.parametrize('local,zone,utc', [
    ('2026-07-01T12:00', 'America/Los_Angeles', '2026-07-01T19:00:00Z'),
    ('2026-01-01T12:00', 'America/Los_Angeles', '2026-01-01T20:00:00Z'),
    ('2026-11-01T12:00', 'Asia/Ho_Chi_Minh', '2026-11-01T05:00:00Z'),
    ('2026-11-01T12:00:05', 'Asia/Kathmandu', '2026-11-01T06:15:05Z'),
    ('2026-11-01T12:00', 'UTC', '2026-11-01T12:00:00Z'),
])
def test_local_schedule_uses_named_zone_and_preserves_wall_clock(local, zone, utc):
    result = resolve_schedule(local, zone)
    assert instant(result['scheduled_at']) == instant(utc)
    assert result['scheduled_local'] == datetime.fromisoformat(local).isoformat(timespec='seconds')
    assert result['timezone'] == zone


@pytest.mark.parametrize('zone,local', [
    ('America/Los_Angeles', '2026-03-08T02:30'),
    ('Australia/Lord_Howe', '2026-10-04T02:15'),
])
def test_nonexistent_hour_or_half_hour_is_rejected(zone, local):
    for fold in (None, 0, 1):
        with pytest.raises(AppError) as error:
            resolve_schedule(local, zone, fold)
        assert error.value.code == 'nonexistent_time'


@pytest.mark.parametrize('zone,local,first,second', [
    ('America/Los_Angeles', '2026-11-01T01:30', '2026-11-01T08:30:00Z', '2026-11-01T09:30:00Z'),
    ('Australia/Lord_Howe', '2026-04-05T01:45', '2026-04-04T14:45:00Z', '2026-04-04T15:15:00Z'),
])
def test_repeated_wall_clock_requires_choice_and_keeps_both_actual_offsets(zone, local, first, second):
    with pytest.raises(AppError) as error:
        resolve_schedule(local, zone)
    assert error.value.code == 'ambiguous_time'
    for fold, expected in ((0, first), (1, second)):
        result = resolve_schedule(local, zone, fold)
        assert result['fold'] == fold and instant(result['scheduled_at']) == instant(expected)


@pytest.mark.parametrize('local,zone,fold', [
    ('2026-02-30T12:00', 'UTC', None), ('2026-11-01', 'UTC', None),
    ('11/01/2026 12:00', 'UTC', None), ('2026-11-01T12:00Z', 'UTC', None),
    ('2026-11-01T12:00-07:00', 'America/Los_Angeles', None),
    ('2026-11-01T25:00', 'UTC', None), ('2026-11-01T12:00', '../UTC', None),
    ('2026-11-01T12:00', 'Not/AZone', None), ('2026-11-01T12:00', 'UTC', True),
    ('2026-11-01T12:00', 'UTC', False), ('2026-11-01T12:00', 'UTC', 2),
    ('2026-11-01T12:00', 'UTC', '1'), (None, 'UTC', None),
])
def test_invalid_calendar_zone_or_fold_cannot_silently_select_server_timezone(local, zone, fold):
    with pytest.raises(AppError):
        resolve_schedule(local, zone, fold)


def payload(**changes):
    return dict(request_id=str(uuid4()), connection_id=2, page_id='555', asset_id='a' * 32,
                scheduled_local='2026-11-01T12:00', timezone='Asia/Ho_Chi_Minh', caption='Fixture caption\n#tag') | changes


def test_on_approval_uses_actual_request_instant_and_explicit_cutoff():
    values=payload(scheduled_local=None,publish_mode='on_approval',approve_by='2030-01-01T14:00:00Z',timezone='America/Los_Angeles',approval_policy=None)
    body=TokenReelInput(**values);stamp=instant('2030-01-01T12:34:56Z')
    result=body.schedule(now=stamp)
    assert instant(result['scheduled_at'])==stamp and result['scheduled_local']=='2030-01-01T04:34:56'
    assert body.approve_by=='2030-01-01T14:00:00+00:00'


@pytest.mark.parametrize('changes',[
    {'publish_mode':'on_approval'},
    {'scheduled_local':None,'publish_mode':'on_approval','approve_by':None,'approval_policy':None},
    {'scheduled_local':None,'publish_mode':'on_approval','approve_by':'2030-01-01T14:00:00'},
    {'publish_mode':'on_approval','approve_by':'2030-01-01T14:00:00Z'},
    {'scheduled_local':None,'publish_mode':'on_approval','approve_by':'2030-01-01T14:00:00Z','fold':0},
    {'approve_by':'2030-01-01T14:00:00Z'},
    {'review_batch':{'id':str(uuid4()),'expected_count':True}},
    {'review_batch':{'id':'bad','expected_count':16}},
    {'review_batch':{'id':str(uuid4()),'expected_count':21}},
])
def test_immediate_mode_and_review_group_reject_ambiguous_or_invalid_inputs(changes):
    with pytest.raises(ValueError):TokenReelInput(**payload(**changes))


def test_schedule_model_keeps_exact_page_source_asset_and_caption():
    body = TokenReelInput(**payload())
    assert body.page_id == '555' and body.connection_id == 2 and body.asset_id == 'a' * 32
    assert body.caption == 'Fixture caption\n#tag'
    assert instant(body.schedule()['scheduled_at']) == instant('2026-11-01T05:00:00Z')


@pytest.mark.parametrize('changes', [
    {'request_id': 'invalid'}, {'connection_id': True}, {'connection_id': '2'}, {'connection_id': 0},
    {'page_id': '５５５'}, {'page_id': '../555'}, {'asset_id': '../../video.mp4'}, {'asset_id': 'A' * 32},
    {'fold': True}, {'fold': '1'}, {'fold': -1}, {'caption': 'a' * 2201},
    {'caption': 'private\x00control'}, {'caption': 'invalid\ud800unicode'},
    {'token': 'must-not-be-accepted'}, {'path': 'C:/private/secret.mp4'}, {'profile_id': 3},
])
def test_schedule_body_rejects_ambiguous_identifiers_unknown_fields_and_invalid_text(changes):
    with pytest.raises(ValueError):
        TokenReelInput(**payload(**changes))
