"""Opt-in integration check: install locked dependencies and three users in an isolated temp root.

Does not launch a host or browser. Requires Internet/Python registry, Windows and
Node. No production paths, databases, credentials or schedules are opened.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deployment.install import install
from tools.build_distribution import make_manifest


def run():
    directory=Path(tempfile.mkdtemp(prefix='via-installer-runtime-check-')).absolute()
    # Verify the absolute recursive-cleanup target before creating test content.
    if directory.parent.resolve()!=Path(tempfile.gettempdir()).resolve() or not directory.name.startswith('via-installer-runtime-check-'):
        raise RuntimeError('unsafe_test_directory')
    try:
        source=directory/'release';target=directory/'installed'
        manifest,files=make_manifest(ROOT,'1.1.0')
        for name,path in files:
            destination=source/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
        (source/'distribution-manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        first=install(source,target,python=Path(sys.executable))
        identity=target/'via-auth/data/identity.sqlite3'
        before=hashlib.sha256(identity.read_bytes()).hexdigest()
        with closing(sqlite3.connect(identity.as_uri()+'?mode=ro',uri=True)) as database:
            users=[{'username':name,'role':role} for name,role in database.execute('SELECT username,role FROM users ORDER BY id')]
        if users!=[{'username':'admin','role':'admin'},{'username':'operator1','role':'operator'},{'username':'operator2','role':'operator'}]:
            raise AssertionError('unexpected_fresh_accounts')
        schedules=target/'meta-page-manager/data/page-manager.sqlite3';schedules.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(schedules)) as database,database:
            database.execute('CREATE TABLE preservation_fixture(id INTEGER PRIMARY KEY,scheduled_at TEXT)')
            database.execute("INSERT INTO preservation_fixture VALUES(1,'2030-01-01T00:00:00Z')")
        before_schedule=hashlib.sha256(schedules.read_bytes()).hexdigest()
        old_marker=target/'chrome-manager/.venv/old-runtime-proof.txt';old_marker.write_text('old venv must be backed up',encoding='utf-8')
        second=install(source,target,target,python=Path(sys.executable))
        if hashlib.sha256(identity.read_bytes()).hexdigest()!=before:raise AssertionError('identity_was_modified_by_update')
        if hashlib.sha256(schedules.read_bytes()).hexdigest()!=before_schedule:raise AssertionError('schedules_were_modified_by_update')
        if not (Path(second['code_backup'])/'previous-venv/old-runtime-proof.txt').is_file():raise AssertionError('old_runtime_not_retained')
        return {'fresh_install':first['installed'],'update_install':second['installed'],'users':users,
                'identity_bytes_preserved':True,'schedule_bytes_preserved':True,'old_venv_retained':True,
                'services_started':False,'production_data_opened':False,'temporary_tree_removed':True}
    finally:
        if directory.parent.resolve()!=Path(tempfile.gettempdir()).resolve() or not directory.name.startswith('via-installer-runtime-check-'):
            raise RuntimeError('unsafe_test_cleanup')
        # The retained venv has long NumPy/typing/pip paths under its backup root.
        removal=str(directory)
        if os.name=='nt':removal='\\\\?\\'+removal
        shutil.rmtree(removal)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-locked-dependencies',action='store_true',required=True)
    parser.parse_args()
    try:print(json.dumps(run()))
    except Exception as error:
        print(json.dumps({'error':str(error) if isinstance(error,(AssertionError,ValueError)) else type(error).__name__}),file=sys.stderr)
        raise SystemExit(1)
