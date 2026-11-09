from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.models.entities import Base

class Database:
    def __init__(self, path):
        self.engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 20})
        @event.listens_for(self.engine, 'connect')
        def pragmas(connection, _):
            connection.create_function('cm_casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        # create_all is additive for tables, but does not add fields to an
        # existing SQLite table. Serialize this nullable-column migration.
        with self.engine.begin() as connection:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            columns={row[1] for row in connection.exec_driver_sql('PRAGMA table_info(reel_jobs)')}
            if columns and 'archived_at' not in columns:
                connection.exec_driver_sql('ALTER TABLE reel_jobs ADD COLUMN archived_at VARCHAR')
            if columns and 'approval_policy' not in columns:
                connection.exec_driver_sql('ALTER TABLE reel_jobs ADD COLUMN approval_policy VARCHAR')
            for name in ('request_id','request_hash'):
                if columns and name not in columns:
                    connection.exec_driver_sql(f'ALTER TABLE reel_jobs ADD COLUMN {name} VARCHAR')
            connection.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS uq_reel_jobs_request_id ON reel_jobs(request_id) WHERE request_id IS NOT NULL')
            for table in ('profiles','settings'):
                existing={row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info({table})')}
                if existing and 'revision' not in existing:
                    connection.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
