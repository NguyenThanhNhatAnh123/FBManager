"""Check public Microsoft endpoints through saved proxies; never sign into Outlook."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import socket
import sys
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.models.entities import Proxy
from app.services.proxies import from_row
from app.services.outlook_diagnostics import probe_proxy, probe_login_tunnel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='Check every assigned proxy; defaults to the first three.')
    parser.add_argument('--deep', action='store_true', help='Also check CONNECT, DNS families and Manager browser executable metadata.')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    data_dir = (ROOT / os.getenv('CM_DATA_DIR', 'data')).resolve()
    if not data_dir.is_relative_to(ROOT):
        raise ValueError('The database must be inside this Manager workspace.')
    with sqlite3.connect((data_dir / 'manager.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            'SELECT p.id,p.protocol,p.host,p.port,p.encrypted_credentials FROM proxies p '
            'WHERE p.id IN (SELECT proxy_id FROM profiles WHERE proxy_id IS NOT NULL) ORDER BY p.id')]
        profile_paths = {Path(row[0]).resolve() for row in connection.execute('SELECT profile_path FROM profiles')}
    if not args.all:
        rows = rows[:3]
    def check(row):
        try:
            proxy = from_row(Proxy(**row))
            results = probe_proxy(row['id'], proxy.url())
            if args.deep:
                results.append({'proxy_id': row['id'], **probe_login_tunnel(proxy)})
            return results
        except Exception:
            return [{'proxy_id': row['id'], 'error': 'PROXY_DIAGNOSTIC_UNAVAILABLE'}]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [row for group in pool.map(check, rows) for row in group]
    report = {'method': 'public_endpoints_through_upstream_proxy', 'accounts_used': False,
              'browser_adapter_tested': False, 'direct_fallback': False, 'results': results}
    if args.deep:
        dns = []
        for hostname in ('login.live.com', 'outlook.live.com', 'outlook.office.com'):
            try:
                addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
                dns.append({'host': hostname, 'ipv4': any(a[0] == socket.AF_INET for a in addresses),
                            'ipv6': any(a[0] == socket.AF_INET6 for a in addresses)})
            except OSError:
                dns.append({'host': hostname, 'error': 'LOCAL_DNS_UNAVAILABLE'})
        report['local_dns_families'] = dns  # Local DNS is distinct from the proxy resolver.
        import psutil
        from app.core.chrome_installation import executable_version
        browsers = []
        for process in psutil.process_iter(['name']):
            if (process.info.get('name') or '').lower() not in ('chrome.exe', 'chromium.exe'):
                continue
            try:
                arguments = process.cmdline()
                if any(argument.startswith('--type=') for argument in arguments):
                    continue  # Child processes inherit the browser's proxy configuration.
                owned = next((Path(a.split('=', 1)[1].strip('"')).resolve() for a in arguments
                              if a.startswith('--user-data-dir=') and Path(a.split('=', 1)[1].strip('"')).resolve() in profile_paths), None)
                if owned:
                    executable = process.exe()
                    browsers.append({'profile_directory': owned.name, 'executable': executable,
                                     'version': executable_version(executable),
                                     'uses_proxy_adapter': any(a.startswith('--proxy-server=http://127.0.0.1:') for a in arguments),
                                     'remote_debugging_flag': any(a.startswith('--remote-debugging-') for a in arguments)})
            except (psutil.Error, OSError, ValueError):
                continue
        report['manager_browser_processes'] = browsers
    output = ROOT / 'logs' / 'outlook-connectivity-validation.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    for row in results:
        outcome = row.get('error') or ('CONNECT OK' if row.get('connect_tunnel') else 'HTTP ' + str(row.get('status', 'unknown')))
        print(f"Proxy #{row['proxy_id']} {row.get('target', 'diagnostic')}: {outcome}", flush=True)
    print('Saved logs/outlook-connectivity-validation.json. No account or mailbox was accessed.')
    return report


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError):
        raise SystemExit('Outlook connectivity check could not run. No proxy settings were changed.') from None
