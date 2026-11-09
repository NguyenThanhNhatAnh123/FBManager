"""Queue the official Store page for a named profile range without starting browsers."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import Config
from app.core.first_open import schedule_first_open,STORE_URL,REQUEST_FILE


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--start',type=int,default=6)
    parser.add_argument('--end',type=int,default=30)
    args=parser.parse_args()
    if not 1<=args.start<=args.end<=1000:parser.error('Use an inclusive range between 1 and 1000.')
    config=Config()
    names=[f'Chrome_{number:03d}' for number in range(args.start,args.end+1)]
    database=config.data_dir/'manager.sqlite3'
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as connection:
        rows=connection.execute('SELECT id,name,profile_path FROM profiles WHERE name IN ('+','.join('?' for _ in names)+') ORDER BY id',names).fetchall()
    missing=set(names)-{row[1] for row in rows}
    if missing:raise SystemExit('Profiles not found: '+', '.join(sorted(missing)))
    root=config.profiles_dir.resolve()
    for _,_,value in rows:
        source=Path(value)
        if source.is_symlink() or source.resolve()==root or not source.resolve().is_relative_to(root) or (source/REQUEST_FILE).is_symlink():
            raise SystemExit('Unsafe profile directory; no requests written.')
    for pid,_,directory in rows:schedule_first_open(directory,root,pid)
    report={'updated_at':datetime.now(timezone.utc).isoformat(),'url':STORE_URL,'pending_count':len(rows),
            'profiles':[{'id':pid,'name':name,'request_file':str(Path(directory)/REQUEST_FILE)} for pid,name,directory in rows],
            'installed_extension':False,'opens_on':'next normal manual Open; consumed after Chrome accepts the URL'}
    destination=ROOT/'logs'/f'authenticator-first-open-{args.start}-{args.end}.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pending':len(rows),'first':names[0],'last':names[-1],'url':STORE_URL,'report':str(destination)},ensure_ascii=False))


if __name__=='__main__':main()
