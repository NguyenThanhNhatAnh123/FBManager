import asyncio
from sqlalchemy import select
from app.core.schemas import Settings
from app.models.entities import Event, Setting

class RevisionConflict(ValueError):
    pass

class State:
    def __init__(self, db):
        self.db = db
        self.subscribers = set()
        with db.session.begin() as session:
            row = session.get(Setting, 1)
            if row is None:
                self.settings = Settings()
                session.add(Setting(id=1, value=self.settings.model_dump_json()))
                self.settings_revision = 1
            else:
                self.settings = Settings.model_validate_json(row.value)
                self.settings_revision = row.revision

    def public_settings(self):
        return self.settings.model_dump() | {'revision':self.settings_revision}

    def save_settings(self, settings, *, expected_revision=None):
        with self.db.session.begin() as session:
            row=session.get(Setting, 1)
            if expected_revision is not None and row.revision!=expected_revision:
                raise RevisionConflict('Cài đặt đã được người khác thay đổi. Tải lại trước khi lưu.')
            row.value = settings.model_dump_json()
            session.flush()
            revision=row.revision
        self.settings = settings
        self.settings_revision = revision
        self.emit()

    def emit(self):
        for queue in tuple(self.subscribers):
            if not queue.full():
                queue.put_nowait(True)

    def log(self, message, profile_id=None, level='info'):
        # Callers provide fixed messages only; never exception bodies or form values.
        with self.db.session.begin() as session:
            session.add(Event(profile_id=profile_id, message=message, level=level))
            cutoff = session.scalar(select(Event.id).order_by(Event.id.desc()).offset(4999).limit(1))
            if cutoff:
                session.query(Event).filter(Event.id < cutoff).delete()
        self.emit()
