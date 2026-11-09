from datetime import datetime, timezone
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now():
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase):
    pass

class Profile(Base):
    __tablename__ = 'profiles'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    profile_path: Mapped[str] = mapped_column(unique=True)
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey('proxies.id', ondelete='RESTRICT'), unique=True)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)
    revision: Mapped[int] = mapped_column(default=1, server_default='1', nullable=False)
    __mapper_args__ = {'version_id_col': revision}

class Proxy(Base):
    __tablename__ = 'proxies'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str]
    port: Mapped[int]
    protocol: Mapped[str]
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    identity: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(default='Unchecked')
    ip_address: Mapped[str | None]
    latency_ms: Mapped[int | None]
    error_message: Mapped[str | None]
    last_checked: Mapped[str | None]

class BmProfile(Base):
    __tablename__ = 'bm_profiles'
    profile_id: Mapped[int] = mapped_column(ForeignKey('profiles.id', ondelete='CASCADE'), primary_key=True)
    portfolios: Mapped[str] = mapped_column(Text, default='[]')
    note: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)

class BmBrowserSettings(Base):
    __tablename__ = 'bm_browser_settings'
    profile_id: Mapped[int] = mapped_column(ForeignKey('bm_profiles.profile_id', ondelete='CASCADE'), primary_key=True)
    mobile_enabled: Mapped[bool] = mapped_column(default=False)

class BmProxy(Base):
    __tablename__ = 'bm_proxies'
    proxy_id: Mapped[int] = mapped_column(ForeignKey('proxies.id', ondelete='CASCADE'), primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey('profiles.id', ondelete='SET NULL'), unique=True)

class PageRecipientJob(Base):
    __tablename__ = 'page_recipient_jobs'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey('profiles.id', ondelete='CASCADE'), unique=True)
    status: Mapped[str] = mapped_column(default='queued')
    step: Mapped[str] = mapped_column(default='opening')
    message: Mapped[str] = mapped_column(default='')
    configuration: Mapped[str] = mapped_column(Text)
    checkpoint: Mapped[str] = mapped_column(Text, default='{}')
    fields: Mapped[str] = mapped_column(Text, default='{}')
    page_id: Mapped[str | None]
    page_name: Mapped[str | None]
    code_deadline: Mapped[str | None]
    started_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)

class ReelAsset(Base):
    __tablename__ = 'reel_assets'
    id: Mapped[str] = mapped_column(String(32),primary_key=True)
    name: Mapped[str]
    path: Mapped[str] = mapped_column(unique=True)
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)

class ReelJob(Base):
    __tablename__ = 'reel_jobs'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey('profiles.id',ondelete='SET NULL'),index=True)
    profile_name: Mapped[str]
    page_url: Mapped[str]
    page_name: Mapped[str | None]
    asset_id: Mapped[str] = mapped_column(ForeignKey('reel_assets.id',ondelete='RESTRICT'))
    caption: Mapped[str] = mapped_column(Text,default='')
    scheduled_at: Mapped[str] = mapped_column(index=True)
    timezone: Mapped[str] = mapped_column(default='UTC')
    status: Mapped[str] = mapped_column(default='scheduled',index=True)
    step: Mapped[str] = mapped_column(default='waiting')
    message: Mapped[str] = mapped_column(default='')
    checkpoint: Mapped[str] = mapped_column(Text,default='{}')
    publish_attempted: Mapped[bool] = mapped_column(default=False)
    published_url: Mapped[str | None]
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]
    archived_at: Mapped[str | None]
    approval_policy: Mapped[str | None]
    request_id: Mapped[str | None] = mapped_column(String(36))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now,onupdate=now)

class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    configuration: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(default='Idle')
    created_at: Mapped[str] = mapped_column(default=now)

class TaskRun(Base):
    __tablename__ = 'task_runs'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id', ondelete='RESTRICT'))
    profile_id: Mapped[int | None] = mapped_column(ForeignKey('profiles.id', ondelete='SET NULL'))
    profile_name: Mapped[str]
    batch_id: Mapped[str] = mapped_column(default='legacy', index=True)
    status: Mapped[str] = mapped_column(default='Pending', index=True)
    result: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None]
    progress: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=now)
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]

class Setting(Base):
    __tablename__ = 'settings'
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    value: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(default=1, server_default='1', nullable=False)
    __mapper_args__ = {'version_id_col': revision}

class Event(Base):
    __tablename__ = 'events'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(index=True)
    level: Mapped[str] = mapped_column(default='info')
    message: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)

class FacebookAccount(Base):
    __tablename__ = 'facebook_accounts'
    profile_id: Mapped[int] = mapped_column(ForeignKey('profiles.id', ondelete='CASCADE'), primary_key=True)
    name: Mapped[str | None]
    url: Mapped[str | None]
    status: Mapped[str] = mapped_column(default='unchecked')
    checked_at: Mapped[str | None]
    error_message: Mapped[str | None]
    timezone: Mapped[str | None]
    check_date: Mapped[str | None]
    day_start: Mapped[str | None]
    day_end: Mapped[str | None]

class FacebookPage(Base):
    __tablename__ = 'facebook_pages'
    __table_args__ = (UniqueConstraint('profile_id', 'page_key'), {'sqlite_autoincrement': True})
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey('profiles.id', ondelete='CASCADE'), index=True)
    page_key: Mapped[str]
    name: Mapped[str]
    url: Mapped[str]
    status: Mapped[str] = mapped_column(default='unchecked')
    checked_at: Mapped[str | None]
    check_date: Mapped[str | None]
    day_start: Mapped[str | None]
    day_end: Mapped[str | None]
    timezone: Mapped[str | None]
    posts: Mapped[str] = mapped_column(Text, default='[]')
    error_message: Mapped[str | None]

class FacebookRefreshSchedule(Base):
    __tablename__ = 'facebook_refresh_schedules'
    profile_id: Mapped[int] = mapped_column(ForeignKey('profiles.id',ondelete='CASCADE'),primary_key=True)
    next_due_at: Mapped[str]
    last_attempt_at: Mapped[str | None]
    last_finished_at: Mapped[str | None]
    result: Mapped[str] = mapped_column(default='waiting')
    retry_after_at: Mapped[str | None]

class OutlookLogin(Base):
    __tablename__ = 'outlook_logins'
    profile_id: Mapped[int] = mapped_column(ForeignKey('profiles.id', ondelete='CASCADE'), primary_key=True)
    email: Mapped[str]
    status: Mapped[str] = mapped_column(default='idle')
    message: Mapped[str | None]
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)
