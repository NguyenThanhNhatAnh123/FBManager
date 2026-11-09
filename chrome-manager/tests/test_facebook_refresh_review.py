"""Independent refresh ownership/cadence regressions; no browser or network I/O."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Config
from app.core.facebook import FacebookCheck
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import FacebookAccount, FacebookPage, FacebookRefreshSchedule, Profile, Proxy
from app.services.browsers import BrowserManager
from app.services.facebook import FacebookManager
from app.services.facebook_refresh import FacebookRefreshScheduler, INTERVAL
from app.services.state import State
from app.services import facebook_refresh as refresh_module


class PendingJob:
    def done(self):
        return False


@pytest.fixture
def review(tmp_path, monkeypatch):
    clock = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(refresh_module, 'utc_now', lambda: clock)
    config = Config(root=tmp_path, data_dir=tmp_path / 'data')
    config.prepare()
    db = Database(config.data_dir / 'review.sqlite3')
    state = State(db)
    state.save_settings(Settings(require_proxy=False, max_browsers=2))
    resources = SimpleNamespace(blocked_reason=lambda _: None)
    browsers = BrowserManager(db, state, config, resources)
    tasks = SimpleNamespace(profile_jobs={})
    with db.session.begin() as session:
        session.add(Proxy(id=1, host='192.0.2.31', port=18310, protocol='http', identity='offline-review', status='Working'))
        session.flush()
        session.add(Profile(id=1, name='Offline refresh review', profile_path=str(config.profiles_dir / 'fixture'), proxy_id=1,
                            created_at=(clock - timedelta(days=5)).isoformat()))
        session.flush()
        session.add(FacebookAccount(profile_id=1, name='Saved person', url='https://www.facebook.com/profile.php?id=101',
                                    status='complete', checked_at=(clock - timedelta(days=5)).isoformat(), check_date='2026-10-30',
                                    timezone='UTC', day_start='2026-10-30T00:00:00+00:00', day_end='2026-10-31T00:00:00+00:00'))
        session.add(FacebookPage(profile_id=1, page_key='555', name='Saved Page', url='https://www.facebook.com/profile.php?id=555',
                                 status='posted', checked_at=(clock - timedelta(days=5)).isoformat(), check_date='2026-10-30',
                                 timezone='UTC', day_start='2026-10-30T00:00:00+00:00', day_end='2026-10-31T00:00:00+00:00',
                                 posts='[{"url":"https://www.facebook.com/reel/999","date":"2026-10-30"}]', error_message='Saved evidence'))
    accepted = []
    facebook = SimpleNamespace(jobs={})

    def enqueue(body):
        accepted.append(body)
        facebook.jobs[1] = PendingJob()
        return {'accepted': [1], 'errors': []}

    facebook.enqueue = enqueue
    yield SimpleNamespace(db=db, state=state, browsers=browsers, tasks=tasks, facebook=facebook, accepted=accepted, at=clock)
    db.engine.dispose()


def schedule_row(review):
    with review.db.session() as session:
        return session.get(FacebookRefreshSchedule, 1)


def saved_evidence(review):
    with review.db.session() as session:
        records = [session.get(FacebookAccount, 1), *session.scalars(select(FacebookPage))]
        return [{column.name: getattr(record, column.name) for column in record.__table__.columns} for record in records]


@pytest.mark.parametrize('age', [timedelta(hours=2), timedelta(days=3)])
def test_restart_keeps_original_daily_reservation_and_catches_up_if_overdue(review, age):
    attempted = review.at - age
    reserved_due = attempted + INTERVAL
    with review.db.session.begin() as session:
        session.add(FacebookRefreshSchedule(profile_id=1, result='running', last_attempt_at=attempted.isoformat(), next_due_at=reserved_due.isoformat()))
    scheduler = FacebookRefreshScheduler(review.db, review.state, review.browsers, review.tasks, review.facebook)
    recovered = schedule_row(review)
    assert recovered.result == 'interrupted'
    assert recovered.next_due_at == reserved_due.isoformat()
    assert recovered.last_finished_at is None, 'Restart cannot claim the interrupted scan finished'
    scheduler.tick()
    assert bool(review.accepted) is (reserved_due <= review.at)
    assert saved_evidence(review)[0]['name'] == 'Saved person'


def test_input_failure_before_enqueue_preserves_due_and_attempt_history(review, monkeypatch):
    scheduler = FacebookRefreshScheduler(review.db, review.state, review.browsers, review.tasks, review.facebook)
    before = schedule_row(review)

    def reject_input(**_):
        raise ValueError('offline request construction failure')

    monkeypatch.setattr(refresh_module, 'FacebookCheck', reject_input)
    scheduler.tick()
    after = schedule_row(review)
    assert (after.next_due_at, after.last_attempt_at) == (before.next_due_at, before.last_attempt_at)
    assert after.result == 'waiting' and after.retry_after_at == (review.at + timedelta(seconds=60)).isoformat()
    assert not review.accepted and not review.facebook.jobs and not review.browsers.facebook_profiles


def test_successful_manual_check_moves_existing_automatic_due_without_scanning(review):
    scheduler = FacebookRefreshScheduler(review.db, review.state, review.browsers, review.tasks, review.facebook)
    with review.db.session.begin() as session:
        session.get(FacebookAccount, 1).checked_at = review.at.isoformat()
    scheduler.tick()
    assert schedule_row(review).next_due_at == (review.at + INTERVAL).isoformat()
    assert not review.accepted


def test_names_only_request_can_cross_midnight_without_relaxing_full_check_window():
    data = dict(profile_ids=[1], timezone='UTC', check_date='2020-01-01',
                day_start='2020-01-01T00:00:00+00:00', day_end='2020-01-02T00:00:00+00:00')
    assert FacebookCheck(**data, names_only=True).names_only
    with pytest.raises(ValidationError):
        FacebookCheck(**data)


@pytest.mark.parametrize('foreign_owner', ['queued_launch', 'manual_context', 'manual_facebook'])
def test_deferred_name_refresh_never_stops_foreign_session_or_changes_post_evidence(review, monkeypatch, foreign_owner):
    manager = FacebookManager(review.db, review.state, review.browsers, review.tasks)
    own_job, foreign = object(), object()
    manager.jobs[1] = own_job
    manager.names_only_jobs[1] = own_job
    review.browsers.facebook_profiles.add(1)
    before = saved_evidence(review)
    opened, stopped = [], []

    class Pause:
        def __await__(self):
            yield 'waiting-for-scan-slot'

    class ScanSlot:
        async def acquire(self):
            await Pause()

        def release(self):
            pass

    async def ensure_open(*args, **kwargs):
        opened.append(args)
        raise AssertionError('No launch is allowed after failed eligibility recheck')

    async def stop(pid):
        stopped.append(pid)

    manager.semaphore = ScanSlot()
    monkeypatch.setattr(review.browsers, 'ensure_open', ensure_open)
    monkeypatch.setattr(review.browsers, 'stop', stop)
    window = FacebookCheck(profile_ids=[1], names_only=True, timezone='UTC', check_date='2026-11-04',
                           day_start='2026-11-04T00:00:00+00:00', day_end='2026-11-05T00:00:00+00:00')
    operation = manager._run(1, window)
    try:
        assert operation.send(None) == 'waiting-for-scan-slot'
        if foreign_owner == 'queued_launch':
            review.browsers.launches[1] = foreign
        elif foreign_owner == 'manual_context':
            review.browsers.contexts[1] = foreign
            review.browsers.modes[1] = 'manual'
        else:
            manager.jobs[2] = foreign
        with pytest.raises(StopIteration) as completed:
            operation.send(None)
        assert completed.value.value['status'] == 'deferred'
        assert not opened and not stopped and saved_evidence(review) == before
        assert 1 not in manager.jobs and 1 not in review.browsers.facebook_profiles
        if foreign_owner == 'queued_launch':
            assert review.browsers.launches[1] is foreign
        elif foreign_owner == 'manual_context':
            assert review.browsers.contexts[1] is foreign
        else:
            assert manager.jobs[2] is foreign
    finally:
        operation.close()
