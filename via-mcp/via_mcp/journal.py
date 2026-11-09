"""Durable replay latch for bridge mutations, including non-idempotent APIs."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import UUID
from .common import ToolError


def stamp():return datetime.now(timezone.utc).isoformat()
def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


class Journal:
    def __init__(self,root):
        root=Path(root).resolve();folder=root/'data'
        if folder.is_symlink() or folder.is_junction():raise ToolError('Kho tiến độ MCP không hợp lệ.','invalid_storage')
        folder.mkdir(parents=True,exist_ok=True)
        self.path=folder/'operations.sqlite3'
        if self.path.is_symlink() or self.path.exists() and self.path.stat().st_nlink!=1:raise ToolError('Kho tiến độ MCP không hợp lệ.','invalid_storage')
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,tool TEXT NOT NULL,digest TEXT NOT NULL,status TEXT NOT NULL,result TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def key(value):
        try:return str(UUID(value))
        except (ValueError,TypeError,AttributeError):raise ToolError('operation_id phải là UUID hợp lệ. Dùng via_new_operation_id.','invalid_operation_id') from None

    def begin(self,operation_id,tool,arguments):
        key=self.key(operation_id);digest=hashlib.sha256(encode(arguments).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM operations WHERE id=?',(key,)).fetchone()
            if old:
                if (old['tool'],old['digest'])!=(tool,digest):raise ToolError('Mã thao tác đã dùng cho nội dung hoặc công cụ khác.','operation_conflict')
                return dict(old)
            now=stamp();db.execute('INSERT INTO operations(id,tool,digest,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',(key,tool,digest,'started',now,now))
            return None

    def finish(self,operation_id,status,result):
        if status not in ('completed','failed','unknown'):raise ValueError('Invalid operation status')
        with self.db() as db:
            db.execute("UPDATE operations SET status=?,result=?,updated_at=? WHERE id=? AND status='started'",(status,encode(result),stamp(),self.key(operation_id)))

    def get(self,operation_id):
        with self.db() as db:row=db.execute('SELECT * FROM operations WHERE id=?',(self.key(operation_id),)).fetchone()
        if row is None:raise ToolError('Chưa có mã thao tác này trong MCP.','operation_not_found')
        return {'operation_id':row['id'],'tool':row['tool'],'status':'unknown' if row['status']=='started' else row['status'],
                'result':json.loads(row['result']) if row['result'] else None,'created_at':row['created_at'],'updated_at':row['updated_at']}
