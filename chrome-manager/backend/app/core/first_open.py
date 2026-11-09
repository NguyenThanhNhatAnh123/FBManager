"""One-time manual startup destination; never edits Chrome's preferences or extension data."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.core.extension_bundle import EXTENSION_ID

STORE_URL=f'https://chromewebstore.google.com/detail/authenticator/{EXTENSION_ID}'
REQUEST_FILE='.manager-first-open.json'


def read_request(directory):
    path=Path(directory)/REQUEST_FILE
    try:
        if path.is_symlink() or path.stat().st_size>8192:return None
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('version')==1 and data.get('url')==STORE_URL:
            return data
    except (OSError,ValueError,AttributeError):
        pass
    return None


def write_request(directory,data):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/REQUEST_FILE
    if target.is_symlink():raise ValueError('Unsafe first-open request file.')
    temporary=directory/(REQUEST_FILE+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def schedule_first_open(directory,profiles_root,profile_id):
    source,root=Path(directory),Path(profiles_root).resolve()
    resolved=source.resolve()
    if source.is_symlink() or resolved==root or not resolved.is_relative_to(root):
        raise ValueError('Unsafe profile directory.')
    write_request(resolved,{'version':1,'profile_id':profile_id,'url':STORE_URL,
                            'requested_at':datetime.now(timezone.utc).isoformat(),'opened_at':None})


def pending_first_open(directory):
    data=read_request(directory)
    return STORE_URL if data and not data.get('opened_at') else None


def complete_first_open(directory):
    data=read_request(directory)
    if data and not data.get('opened_at'):
        data['opened_at']=datetime.now(timezone.utc).isoformat()
        write_request(directory,data)
