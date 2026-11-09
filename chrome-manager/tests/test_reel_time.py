from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from app.core.reel_time import resolve_schedule


def body(local,zone,fold=None):return SimpleNamespace(scheduled_at=None,scheduled_local=local,timezone=zone,fold=fold)


@pytest.mark.parametrize('local,zone,expected',[
    ('2026-10-30T20:00','Asia/Ho_Chi_Minh','2026-10-30T13:00:00+00:00'),
    ('2026-07-01T12:00','America/Los_Angeles','2026-07-01T19:00:00+00:00'),
    ('2026-01-01T12:00','America/Los_Angeles','2026-01-01T20:00:00+00:00'),
    ('2026-10-30T12:00','UTC','2026-10-30T12:00:00+00:00'),
])
def test_iana_wall_clock_uses_actual_offset_and_dst(local,zone,expected):
    value,_=resolve_schedule(body(local,zone));assert value.isoformat()==expected


def test_nonexistent_spring_time_is_rejected():
    with pytest.raises(ValueError,match='không tồn tại'):resolve_schedule(body('2026-03-08T02:30','America/Los_Angeles'))


def test_fall_repeat_needs_explicit_occurrence():
    with pytest.raises(ValueError,match='hai lần'):resolve_schedule(body('2026-11-01T01:30','America/Los_Angeles'))
    first,_=resolve_schedule(body('2026-11-01T01:30','America/Los_Angeles',0))
    second,_=resolve_schedule(body('2026-11-01T01:30','America/Los_Angeles',1))
    assert first.isoformat()=='2026-11-01T08:30:00+00:00'
    assert (second-first).total_seconds()==3600


def test_datetime_model_and_explicit_utc_input_are_supported():
    value,_=resolve_schedule(body(datetime(2026,10,30,20),'Asia/Ho_Chi_Minh'))
    assert value.hour==13
    source=SimpleNamespace(scheduled_at=datetime(2026,10,30,12,tzinfo=timezone.utc),scheduled_local=None,timezone='UTC',fold=None)
    assert resolve_schedule(source)[0]==source.scheduled_at


@pytest.mark.parametrize('local,zone',[('2026-02-30T12:00','UTC'),('bad','UTC'),('2026-10-30T12:00','Not/AZone')])
def test_bad_calendar_and_timezone_fail_closed(local,zone):
    with pytest.raises(ValueError):resolve_schedule(body(local,zone))
