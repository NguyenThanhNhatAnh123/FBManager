"""Read-only hashes of existing scheduling intent, for a controlled host reload."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'via-auth'))
from via_auth.store import protect_private

TABLES={
 'chrome':{'path':'chrome-manager/data/manager.sqlite3','tables':{
  'reel_jobs':None,'reel_assets':None,
  'profiles':['id','name','proxy_id','profile_path'],
  'proxies':['id','protocol','host','port','encrypted_credentials'],
  'tasks':['id','name','configuration'],
 }},
 'pages':{'path':'meta-page-manager/data/page-manager.sqlite3','tables':{
  'reel_jobs':None,'reel_assets':None,
  'video_source_settings':['id','revision','configured','settings','cursor','pending_cursor'],
  'video_source_items':None,
  'connections':['id','label','token_type','app_id','business_id','encrypted_token','token_revision'],
  'connection_pages':['connection_id','page_id','encrypted_page_token','active'],
 }},
}


def fingerprint(db,table,columns=None):
 available=[row[1] for row in db.execute('PRAGMA table_info("'+table+'")')]
 if not available:raise ValueError('missing_preservation_table')
 if columns is None:columns=available
 if not set(columns)<=set(available):raise ValueError('missing_preservation_column')
 rows=[list(row) for row in db.execute('SELECT '+','.join('"'+column+'"' for column in columns)+' FROM "'+table+'"')]
 encoded=sorted(json.dumps(row,ensure_ascii=True,separators=(',',':')) for row in rows)
 digest=hashlib.sha256('\n'.join(encoded).encode()).hexdigest()
 return {'columns':columns,'rows':len(rows),'sha256':digest}


def snapshot(baseline=None):
 result={}
 for manager,spec in TABLES.items():
  with closing(sqlite3.connect((ROOT/spec['path']).as_uri()+'?mode=ro',uri=True)) as db:
   db.execute('PRAGMA query_only=ON')
   if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('database_integrity_failed')
   entries={}
   for table,columns in spec['tables'].items():
    if baseline:columns=baseline[manager]['tables'][table]['columns']
    entries[table]=fingerprint(db,table,columns)
   counts=dict(Counter(row[0] for row in db.execute('SELECT status FROM reel_jobs')))
   result[manager]={'tables':entries,'schedule_status_counts':counts}
 return result


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--capture',type=Path);parser.add_argument('--compare',type=Path)
 args=parser.parse_args()
 if bool(args.capture)==bool(args.compare):raise ValueError('choose_one_operation')
 if args.capture:
  result=snapshot();args.capture.parent.mkdir(parents=True,exist_ok=True);protect_private(args.capture.parent)
  with args.capture.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
  protect_private(args.capture)
  print(json.dumps({'captured':True,'schedules':{key:value['schedule_status_counts'] for key,value in result.items()}}))
 else:
  before=json.loads(args.compare.read_text(encoding='utf-8'));after=snapshot(before)
  differences=[manager+'.'+table for manager in before for table in before[manager]['tables']
               if before[manager]['tables'][table]!=after[manager]['tables'][table]]
  print(json.dumps({'preserved':not differences,'changed_tables':differences,
                   'schedules':{key:value['schedule_status_counts'] for key,value in after.items()}}))
  if differences:return 1
 return 0


if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:
  print(json.dumps({'error':str(error) if type(error) is ValueError else 'snapshot_failed'}),file=sys.stderr)
  raise SystemExit(1)
