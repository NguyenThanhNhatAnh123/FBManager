"""Queue checks of existing proxies for the next normal Manager startup; no credentials read."""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import Config
from app.services.proxy_scope import ordinary_proxy_where


if __name__=='__main__':
    config=Config()
    with sqlite3.connect((config.data_dir/'manager.sqlite3').as_uri()+'?mode=ro',uri=True) as connection:
        condition=ordinary_proxy_where(connection)
        ids=[row[0] for row in connection.execute('SELECT id FROM proxies'+condition+' ORDER BY id')]
    target=config.data_dir/'pending-proxy-checks.json'
    if target.is_symlink():raise SystemExit('Unsafe proxy-check request file.')
    data={'version':1,'proxy_ids':ids,'requested_at':datetime.now(timezone.utc).isoformat()}
    target.write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps({'queued_for_startup':len(ids),'request_file':str(target)}))
