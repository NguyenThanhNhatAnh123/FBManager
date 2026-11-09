"""The archive field is additive and nullable in existing local databases."""
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable

from app.database.session import Database
from app.models.entities import Profile,ReelAsset,ReelJob


def test_existing_reel_rows_gain_nullable_archive_column_without_data_loss_and_migration_is_idempotent(tmp_path):
    path=tmp_path/'legacy.sqlite3'
    legacy=str(CreateTable(ReelJob.__table__).compile(dialect=dialect()))
    legacy='\n'.join(line for line in legacy.splitlines() if not line.strip().startswith('archived_at '))
    with sqlite3.connect(path) as connection:
        connection.execute(str(CreateTable(Profile.__table__).compile(dialect=dialect())))
        connection.execute(str(CreateTable(ReelAsset.__table__).compile(dialect=dialect())))
        connection.execute(legacy)
        connection.execute("INSERT INTO profiles(id,name,profile_path,created_at,updated_at) VALUES(3,'Fixture','fixture-profile','old','old')")
        connection.execute('INSERT INTO reel_assets(id,name,path,size,sha256,mime,created_at) VALUES(?,?,?,?,?,?,?)',('a'*32,'fixture.mp4','fixture.mp4',12,'b'*64,'video/mp4','old'))
        connection.execute('INSERT INTO reel_jobs(id,profile_id,profile_name,page_url,asset_id,caption,scheduled_at,timezone,status,step,message,checkpoint,publish_attempted,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (1,3,'Fixture','https://www.facebook.com/Fixture','a'*32,'Existing caption','2026-10-30T00:00:00+00:00','UTC','cancelled','cancelled','Keep history','{}',0,'old','old'))
        before=connection.execute('SELECT * FROM reel_jobs').fetchone()
        assert 'archived_at' not in {row[1] for row in connection.execute('PRAGMA table_info(reel_jobs)')}
    for _ in range(2):
        db=Database(path)
        with db.session() as session:
            row=session.get(ReelJob,1)
            assert row.archived_at is None and row.caption=='Existing caption' and row.status=='cancelled'
            assert row.publish_attempted is False and row.updated_at=='old'
        db.engine.dispose()
    with sqlite3.connect(path) as connection:
        columns=list(connection.execute('PRAGMA table_info(reel_jobs)'))
        archive=next(row for row in columns if row[1]=='archived_at')
        assert archive[3]==0 and archive[4] is None
        after=connection.execute('SELECT * FROM reel_jobs').fetchone()
        assert after[:-1]==before and after[-1] is None


def test_old_reel_bindings_gain_only_null_policy_and_history_bytes_stay_unchanged(tmp_path):
    path=tmp_path/'legacy-policy.sqlite3'
    legacy=str(CreateTable(ReelJob.__table__).compile(dialect=dialect()))
    legacy='\n'.join(line for line in legacy.splitlines() if not line.strip().startswith('approval_policy '))
    with sqlite3.connect(path) as connection:
        connection.execute(str(CreateTable(Profile.__table__).compile(dialect=dialect())))
        connection.execute(str(CreateTable(ReelAsset.__table__).compile(dialect=dialect())))
        connection.execute(legacy)
        connection.execute("INSERT INTO profiles(id,name,profile_path,created_at,updated_at) VALUES(3,'Fixture','fixture-profile','old','old')")
        connection.execute('INSERT INTO reel_assets(id,name,path,size,sha256,mime,created_at) VALUES(?,?,?,?,?,?,?)',('a'*32,'fixture.mp4','fixture.mp4',12,'b'*64,'video/mp4','old'))
        connection.execute('INSERT INTO reel_jobs(id,profile_id,profile_name,page_url,asset_id,caption,scheduled_at,timezone,status,step,message,checkpoint,publish_attempted,published_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (1,3,'Fixture','https://www.facebook.com/Fixture','a'*32,'Existing caption','2026-10-30T00:00:00+00:00','UTC','completed','done','Keep history','{"publish_attempted":true}',1,'https://www.facebook.com/reel/123','old','old'))
        before=connection.execute('SELECT * FROM reel_jobs').fetchone()
    for _ in range(2):
        db=Database(path)
        with db.session() as session:
            row=session.get(ReelJob,1)
            assert row.approval_policy is None and row.status=='completed'
            assert row.published_url=='https://www.facebook.com/reel/123'
        db.engine.dispose()
    with sqlite3.connect(path) as connection:
        after=connection.execute('SELECT * FROM reel_jobs').fetchone()
        assert after[:-1]==before and after[-1] is None
