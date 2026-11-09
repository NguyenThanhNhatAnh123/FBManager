"""Read-only global calendar windows on disposable PM SQLite; no real schedules."""
import pytest

from page_manager.errors import AppError
from page_manager.reel_calendar import validate_calendar, local_date_key
from test_reel_sql_pages import library
from test_list_pagination import get


DATES = {
    1: ('2026-11-05T06:00:00Z', 'America/Los_Angeles'),
    2: ('2026-11-05T06:00:00-08:00', 'America/Los_Angeles'),
    3: ('2026-11-05T22:30:00Z', 'Asia/Ho_Chi_Minh'),
    4: ('2026-11-05T22:30:00Z', 'America/Los_Angeles'),
    5: ('2026-11-05T16:00:00+02:00', 'UTC'),
    6: ('2027-01-01T02:00:00Z', 'America/Los_Angeles'),
    7: ('2026-03-08T09:30:00Z', 'America/Los_Angeles'),
    8: ('2026-03-08T10:30:00Z', 'America/Los_Angeles'),
    9: ('2026-11-01T08:30:00Z', 'America/Los_Angeles'),
    10: ('2026-11-01T09:30:00Z', 'America/Los_Angeles'),
}


def calendar_library(library):
    app, store, headers = library
    with store.connect() as db:
        for key, (instant, zone) in DATES.items():
            db.execute('UPDATE reel_jobs SET scheduled_at=?,timezone=? WHERE id=?', (instant, zone, key))
    return app, store, headers


def test_calendar_filters_before_global_count_and_bounded_sql_page(library, monkeypatch):
    app, store, headers = calendar_library(library)
    monkeypatch.setattr(store, 'reel_jobs', lambda: pytest.fail('Must not hydrate the full legacy list'))
    first = get(app, '/api/reels?limit=1&calendar=2026-11-05', headers)[2]
    second = get(app, '/api/reels?limit=1&offset=1&calendar=2026-11-05', headers)[2]
    assert first['total'] == second['total'] == 3
    assert [first['jobs'][0]['id'], second['jobs'][0]['id']] == [5, 4]
    assert len(first['jobs']) == len(second['jobs']) == 1
    assert get(app, '/api/reels?limit=1&calendar=2026', headers)[2]['total'] == 10
    assert get(app, '/api/reels?limit=1&calendar=2026-11', headers)[2]['total'] == 7
    assert get(app, '/api/reels?limit=1&calendar=2026-12-31', headers)[2]['jobs'][0]['id'] == 6
    assert get(app, '/api/reels?limit=1&calendar=2027', headers)[2]['total'] == 0


def test_scheduled_sort_uses_actual_instant_then_stable_id(library):
    app, store, headers = calendar_library(library)
    before = store.reel_job(2)
    asc = get(app, '/api/reels?limit=10&calendar=2026-11-05&sort=scheduled_asc', headers)[2]
    desc = get(app, '/api/reels?limit=10&calendar=2026-11-05&sort=scheduled_desc', headers)[2]
    assert [job['id'] for job in asc['jobs']] == [2, 5, 4]
    assert [job['id'] for job in desc['jobs']] == [4, 5, 2]
    assert store.reel_job(2) == before


def test_dst_gap_and_both_folds_share_correct_local_calendar_without_24h_math(library):
    app, store, headers = calendar_library(library)
    spring = get(app, '/api/reels?limit=10&calendar=2026-03-08&sort=scheduled_asc', headers)[2]
    fall = get(app, '/api/reels?limit=10&calendar=2026-11-01&sort=scheduled_asc', headers)[2]
    assert [job['id'] for job in spring['jobs']] == [7, 8]
    assert [job['id'] for job in fall['jobs']] == [9, 10]
    assert get(app, '/api/reels?limit=10&calendar=2026-11-04', headers)[2]['total'] == 1
    assert get(app, '/api/reels?limit=10&calendar=2026-11-06', headers)[2]['jobs'][0]['id'] == 3


@pytest.mark.parametrize('calendar', ['0999', '0000', '2026-', '2026-2', '2026-00', '2026-13',
    '2026-02-30', '2025-02-29', '2026-11-05T00:00', '２０２６', ' 2026', '2026'+'x'*1000])
def test_invalid_calendar_is_422_not_normalized_or_ignored(library, calendar):
    app, store, headers = library
    from urllib.parse import quote
    assert get(app, '/api/reels?limit=1&calendar=' + quote(calendar), headers)[0] == 422
    with pytest.raises(AppError): store.reel_job_page(1, calendar=calendar)


@pytest.mark.parametrize('query', ['calendar=2026', 'sort=scheduled_asc', 'sort=scheduled_desc', 'limit=1&sort=arbitrary'])
def test_legacy_no_limit_cannot_silently_ignore_new_filters(library, query):
    app, store, headers = library
    assert get(app, '/api/reels?' + query, headers)[0] == 422


def test_invalid_stored_timezone_or_naive_timestamp_never_matches_calendar():
    assert local_date_key('2026-11-05T14:00:00Z', 'Not/AZone') is None
    assert local_date_key('2026-11-05T14:00:00', 'UTC') is None
    assert validate_calendar('2024-02-29') == '2024-02-29'
    assert validate_calendar('1000') == '1000' and validate_calendar('9999-12') == '9999-12'
