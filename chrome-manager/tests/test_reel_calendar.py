"""Read-only global schedule filtering on disposable Chrome SQLite only."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import workspace
from app.database.session import Database
from app.models.entities import Profile, ReelAsset, ReelJob
from app.core.reel_calendar import validate_calendar, local_date_key


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


@pytest.fixture
def calendar_client(tmp_path):
    db = Database(tmp_path / 'calendar.sqlite3'); projected = []
    def public(job, asset):
        projected.append(job.id)
        return {'id': job.id, 'scheduled_at': job.scheduled_at, 'timezone': job.timezone}
    app = FastAPI(); app.state.services = SimpleNamespace(db=db, reels=SimpleNamespace(public=public))
    app.include_router(workspace.router)
    with db.session.begin() as session:
        session.add(Profile(id=1, name='Fixture', profile_path=str(tmp_path / 'profile')))
        session.add(ReelAsset(id='a' * 32, name='Fixture.mp4', path='inert-path', size=10, sha256='b' * 64, mime='video/mp4'))
        session.flush()
        for number in [*DATES, *range(100, 230)]:
            instant, zone = DATES.get(number, ('2030-01-01T12:00:00Z', 'UTC'))
            session.add(ReelJob(id=number, profile_id=1, profile_name='Fixture', page_url='https://www.facebook.com/profile.php?id=123',
                page_name='Fixture Page', asset_id='a' * 32, caption='Fixture caption', scheduled_at=instant,
                timezone=zone, status='scheduled', created_at='2026-01-01T00:00:00Z', checkpoint='{}'))
    with TestClient(app) as client: yield client, projected
    db.engine.dispose()


def read(fixture, query):
    client, projected = fixture
    response = client.get('/api/workspace/reels?' + query)
    assert response.status_code == 200, response.text
    return response.json()


def test_calendar_before_global_count_and_paging_hydrates_only_window(calendar_client):
    first = read(calendar_client, 'limit=1&calendar=2026-11-05')
    second = read(calendar_client, 'limit=1&offset=1&calendar=2026-11-05')
    assert first['total'] == second['total'] == first['summary']['all'] == 3
    assert [first['items'][0]['id'], second['items'][0]['id']] == [5, 4]
    assert calendar_client[1] == [5, 4]
    assert read(calendar_client, 'limit=1&calendar=2026')['total'] == 10
    assert read(calendar_client, 'limit=1&calendar=2026-11')['total'] == 7
    assert read(calendar_client, 'limit=1&calendar=2026-12-31')['items'][0]['id'] == 6
    assert read(calendar_client, 'limit=1&calendar=2027')['total'] == 0
    assert read(calendar_client, 'limit=1&offset=100&calendar=2026-11-05')['offset'] == 2


def test_instant_sort_with_offset_strings_and_stable_ties(calendar_client):
    asc = read(calendar_client, 'limit=10&calendar=2026-11-05&sort=scheduled_asc')
    desc = read(calendar_client, 'limit=10&calendar=2026-11-05&sort=scheduled_desc')
    assert [job['id'] for job in asc['items']] == [2, 5, 4]
    assert [job['id'] for job in desc['items']] == [4, 5, 2]
    assert read(calendar_client, 'limit=1')['items'][0]['id'] == 229


def test_dst_days_use_local_calendar_for_gap_and_both_fold_instants(calendar_client):
    spring = read(calendar_client, 'limit=10&calendar=2026-03-08&sort=scheduled_asc')
    fall = read(calendar_client, 'limit=10&calendar=2026-11-01&sort=scheduled_asc')
    assert [job['id'] for job in spring['items']] == [7, 8]
    assert [job['id'] for job in fall['items']] == [9, 10]
    assert read(calendar_client, 'limit=10&calendar=2026-11-04')['items'][0]['id'] == 1
    assert read(calendar_client, 'limit=10&calendar=2026-11-06')['items'][0]['id'] == 3


@pytest.mark.parametrize('calendar', ['0999', '0000', '2026-', '2026-2', '2026-13', '2026-02-30', '2025-02-29', '２０２６', ' 2026', '2026'+'x'*1000])
def test_invalid_calendar_returns_422(calendar_client, calendar):
    assert calendar_client[0].get('/api/workspace/reels', params={'calendar': calendar}).status_code == 422


def test_invalid_sort_and_stored_unknown_zone_fail_closed(calendar_client):
    assert calendar_client[0].get('/api/workspace/reels?sort=invalid').status_code == 422
    assert local_date_key('2026-11-05T14:00:00Z', 'Not/AZone') is None
    assert local_date_key('2026-11-05T14:00:00', 'UTC') is None
    assert validate_calendar('2024-02-29') == '2024-02-29'
    assert validate_calendar('1000') == '1000' and validate_calendar('9999-12') == '9999-12'
