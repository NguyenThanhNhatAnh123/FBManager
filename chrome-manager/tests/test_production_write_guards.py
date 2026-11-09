"""Shared-server write guards using disposable SQLite, no browser or network."""
from datetime import datetime,timedelta,timezone
import hashlib
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable

from app.api import routes
from app.core.config import Config
from app.core.reel_inputs import StartReel
from app.core.schemas import Settings
from app.database.session import Database
from app.models.entities import Profile,ReelAsset,ReelJob,Setting
from app.services.browsers import BrowserManager
from app.services.reel_scheduler import ReelScheduler
from app.services.state import State
from app.services import reel_scheduler as scheduler_module


@pytest.fixture
def guard_service(tmp_path):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    db=Database(config.data_dir/'manager.sqlite3');state=State(db)
    state.save_settings(Settings(require_proxy=False))
    browsers=BrowserManager(db,state,config,SimpleNamespace(blocked_reason=lambda _:None))
    tasks=SimpleNamespace(profile_jobs={})
    policy=SimpleNamespace(posting_eligibility=lambda _:dict(page_id='555',can_publish=True,information_status='updated',reason=None,revision=1))
    reels=ReelScheduler(db,state,browsers,tasks,config,posting_policy=policy)
    with db.session.begin() as session:
        session.add(Profile(id=3,name='Fixture',profile_path=str(config.profiles_dir/'fixture')))
    path=reels.assets.root/('a'*32+'.mp4');path.write_bytes(b'isolated fixture video')
    reels.add_asset(dict(id='a'*32,name='fixture.mp4',path=str(path),size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),mime='video/mp4'))
    service=SimpleNamespace(db=db,state=state,config=config,browsers=browsers,tasks=tasks,reels=reels)
    yield service
    db.engine.dispose()


@pytest.fixture
def guard_client(guard_service):
    app=FastAPI();app.state.services=guard_service;app.include_router(routes.router)
    with TestClient(app) as client:yield client


def payload(**changes):
    return dict(request_id=str(uuid4()),profile_id=3,page_url='https://www.facebook.com/profile.php?id=555',
        asset_id='a'*32,caption='Chosen caption',scheduled_at=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
        timezone='UTC',approval_policy=None,**changes)


def test_http_reel_requires_request_key_and_validates_uuid(guard_client):
    body=payload();body.pop('request_id')
    assert guard_client.post('/api/reels',json=body).status_code==428
    body['request_id']='invalid'
    assert guard_client.post('/api/reels',json=body).status_code==422


def test_same_reel_request_replays_after_due_without_file_or_posting_permission(guard_client,guard_service,monkeypatch):
    body=payload();first=guard_client.post('/api/reels',json=body)
    assert first.status_code==201
    original=datetime
    class Later(original):
        @classmethod
        def now(cls,tz=None):return original.now(timezone.utc)+timedelta(days=3)
    monkeypatch.setattr(scheduler_module,'datetime',Later)
    guard_service.reels.assets.root.joinpath('a'*32+'.mp4').unlink()
    guard_service.reels.posting_policy.posting_eligibility=lambda _:None
    retry=guard_client.post('/api/reels',json=body)
    assert retry.status_code==201 and retry.json()['id']==first.json()['id']
    with guard_service.db.session() as session:
        assert session.scalar(select(func.count()).select_from(ReelJob))==1


@pytest.mark.parametrize('change',[dict(caption='Other caption'),dict(profile_id=4),dict(asset_id='b'*32),dict(page_name='Other label'),dict(approval_policy='remind_until_decision_v1')])
def test_reusing_reel_key_for_different_payload_is_409_before_any_other_validation(guard_client,change):
    body=payload();first=guard_client.post('/api/reels',json=body)
    assert first.status_code==201
    assert guard_client.post('/api/reels',json=body|change).status_code==409


def test_normalized_schedule_offset_and_caption_replay_same_key(guard_client):
    body=payload();body['caption']='One\r\nTwo';first=guard_client.post('/api/reels',json=body)
    retry=body|{'caption':'One\nTwo','scheduled_at':datetime.fromisoformat(body['scheduled_at']).astimezone(timezone(timedelta(hours=-7))).isoformat()}
    assert guard_client.post('/api/reels',json=retry).json()['id']==first.json()['id']


def test_unique_key_collision_rechecks_committed_winner(guard_service,monkeypatch):
    manager=guard_service.reels;body=StartReel(**payload());first=manager.create(body)
    original=manager._request_result;reads=[]
    def miss_first(key,digest):
        reads.append(key)
        return None if len(reads)==1 else original(key,digest)
    monkeypatch.setattr(manager,'_request_result',miss_first)
    assert manager.create(body)['id']==first['id'] and len(reads)==2


def test_profile_stale_editor_cannot_undo_other_edit(guard_client,guard_service):
    first=guard_client.get('/api/profiles').json()[0]
    body=dict(name='First editor',proxy_id=None,revision=first['revision'])
    assert guard_client.patch('/api/profiles/3',json=body).status_code==200
    assert guard_client.patch('/api/profiles/3',json=body|{'name':'Stale editor'}).status_code==409
    current=guard_client.get('/api/profiles').json()[0]
    assert current['name']=='First editor' and current['revision']==first['revision']+1
    assert guard_client.patch('/api/profiles/3',json={'name':'No revision'}).status_code==428


def test_settings_stale_editor_cannot_undo_other_setting(guard_client):
    initial=guard_client.get('/api/settings').json()
    first=guard_client.put('/api/settings',json=initial|{'max_browsers':7})
    assert first.status_code==200 and first.json()['revision']==initial['revision']+1
    assert guard_client.put('/api/settings',json=initial|{'action_delay_ms':5000}).status_code==409
    current=guard_client.get('/api/settings').json()
    assert current['max_browsers']==7 and current['action_delay_ms']==initial['action_delay_ms']
    current.pop('revision')
    assert guard_client.put('/api/settings',json=current).status_code==428


def test_legacy_write_guard_migration_preserves_history_and_is_repeatable(tmp_path):
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as connection:
        for model,removed in ((Profile,('revision',)),(Setting,('revision',)),(ReelAsset,()),(ReelJob,('request_id','request_hash'))):
            ddl=str(CreateTable(model.__table__).compile(dialect=dialect()))
            ddl='\n'.join(line for line in ddl.splitlines() if not any(line.strip().startswith(name+' ') for name in removed))
            connection.execute(ddl)
        connection.execute("INSERT INTO profiles(id,name,profile_path,created_at,updated_at) VALUES(3,'Original','fixture','old','old')")
        connection.execute("INSERT INTO settings(id,value) VALUES(1,'{}')")
        connection.execute('INSERT INTO reel_assets(id,name,path,size,sha256,mime,created_at) VALUES(?,?,?,?,?,?,?)',('a'*32,'fixture.mp4','fixture.mp4',12,'b'*64,'video/mp4','old'))
        connection.execute("INSERT INTO reel_jobs(id,profile_id,profile_name,page_url,asset_id,caption,scheduled_at,timezone,status,step,message,checkpoint,publish_attempted,created_at,updated_at) VALUES(1,3,'Original','https://www.facebook.com/Fixture',?,'Keep caption','2026-10-01T00:00:00+00:00','UTC','cancelled','cancelled','Keep history','{}',0,'old','old')",('a'*32,))
    for _ in range(2):
        db=Database(path)
        with db.session() as session:
            job=session.get(ReelJob,1)
            assert job.caption=='Keep caption' and job.status=='cancelled' and job.updated_at=='old'
            assert job.request_id is None and job.request_hash is None
            assert session.get(Profile,3).revision==1 and session.get(Setting,1).revision==1
        db.engine.dispose()
    with sqlite3.connect(path) as connection:
        assert any(row[1]=='uq_reel_jobs_request_id' and row[2] for row in connection.execute('PRAGMA index_list(reel_jobs)'))
