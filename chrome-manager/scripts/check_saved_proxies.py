"""Check saved proxies without a running dashboard; never print credential-bearing URLs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import ipaddress
import json
from pathlib import Path
import sqlite3
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.config import Config
from app.core.schemas import Settings
from app.models.entities import Proxy,Setting
from app.services.proxies import from_row,proxy_failure_message
from app.services.outlook_diagnostics import failure_code
from app.services.proxy_scope import ordinary_proxy_where


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--persist',action='store_true')
    args=parser.parse_args()
    config=Config()
    database=(config.data_dir/'manager.sqlite3').resolve()
    if not database.is_relative_to(ROOT):raise SystemExit('Database must be inside this workspace.')
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        condition=ordinary_proxy_where(connection)
        records=[dict(row) for row in connection.execute('SELECT id,identity,protocol,host,port,encrypted_credentials FROM proxies'+condition+' ORDER BY id')]
        stored=connection.execute('SELECT value FROM '+Setting.__tablename__+' WHERE id=1').fetchone()
        settings=Settings.model_validate_json(stored[0]) if stored else Settings()
    def check(record):
        result={'proxy_id':record['id'],'status':'Unavailable','error_code':None,'error_message':None,'ip_address':None,'latency_ms':None,
                'last_checked':datetime.now(timezone.utc).isoformat()}
        try:value=from_row(Proxy(**record))
        except Exception:
            return result|{'error_code':'CREDENTIALS_UNAVAILABLE'}
        start=time.monotonic()
        try:
            with httpx.Client(proxy=value.url(),timeout=12,trust_env=False,follow_redirects=False) as client:
                response=client.get(config.ip_check_url_for(settings.proxy_ip_version))
                response.raise_for_status()
                body=response.json()
                address=ipaddress.ip_address(body.get('ip') or body.get('origin','').split(',')[0].strip())
                if settings.proxy_ip_version in ('ipv4','ipv6') and address.version!=int(settings.proxy_ip_version[-1]):raise ValueError()
                result.update(status='Working',ip_address=str(address),latency_ms=round((time.monotonic()-start)*1000))
        except Exception as error:
            code=failure_code(error)
            if code=='LOCAL_NETWORK_PERMISSION_DENIED':return result|{'error_code':code}
            result.update(status='Timeout' if isinstance(error,httpx.TimeoutException) else 'Failed',error_code=code,
                          error_message='Proxy did not respond within 12 seconds.' if isinstance(error,httpx.TimeoutException) else proxy_failure_message(error))
        return result
    results=[]
    if records:
        results.append(check(records[0]))
        # One denied probe is enough to establish that this runtime cannot check the saved endpoints.
        if results[0]['status']!='Unavailable':
            with ThreadPoolExecutor(max_workers=10) as pool:results.extend(pool.map(check,records[1:]))
    saved=0
    if args.persist:
        identities={record['id']:record['identity'] for record in records}
        with sqlite3.connect(database) as connection:
            for result in results:
                if result['status']=='Unavailable':continue
                cursor=connection.execute('UPDATE proxies SET status=?,ip_address=?,latency_ms=?,error_message=?,last_checked=? WHERE id=? AND identity=?',
                    (result['status'],result['ip_address'],result['latency_ms'],result['error_message'],result['last_checked'],result['proxy_id'],identities[result['proxy_id']]))
                saved+=cursor.rowcount
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'method':'configured IP service through each saved upstream proxy; no direct fallback',
            'requested':len(records),'attempted':len(results),'saved':saved,'results':results}
    output=ROOT/'logs'/'proxy-check-current-validation.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    complete=len(results)==len(records) and all(row['status']!='Unavailable' for row in results)
    if complete and saved==len(records):
        pending=config.data_dir/'pending-proxy-checks.json'
        if pending.is_file() and not pending.is_symlink():
            data=json.loads(pending.read_text(encoding='utf-8'))
            if set(data.get('proxy_ids',[]))<={row['proxy_id'] for row in results}:pending.unlink()
    print(json.dumps({'requested':len(records),'attempted':len(results),'saved':saved,'counts':{status:sum(row['status']==status for row in results) for status in ('Working','Failed','Timeout','Unavailable')},
                      'unavailable_reason':next((row['error_code'] for row in results if row['status']=='Unavailable'),None),'report':str(output)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
