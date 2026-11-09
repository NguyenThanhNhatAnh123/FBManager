import asyncio
import ipaddress
import json
import time

import httpx
from sqlalchemy import select
from app.core.security import encrypt_credentials, decrypt_credentials
from app.models.entities import Proxy, Profile, BmProxy, BmProfile, now

from app.core.proxy_parser import ParsedProxy, parse_import

def from_row(proxy):
    username, password = decrypt_credentials(proxy.encrypted_credentials)
    return ParsedProxy(proxy.protocol, proxy.host, proxy.port, username, password)

def public_proxy(proxy):
    return {k: getattr(proxy, k) for k in ('id', 'host', 'port', 'protocol', 'status', 'ip_address', 'latency_ms', 'last_checked', 'error_message')} | {'has_credentials': bool(proxy.encrypted_credentials)}

def proxy_failure_message(error):
    # Inspect diagnostic tokens in memory only. Never return raw exception text:
    # connection errors can embed a credential-bearing URL.
    detail = str(error).lower()
    if isinstance(error, httpx.ProxyError):
        if '407' in detail or 'authentication' in detail:
            return '[PROXY_AUTH] Proxy rejected authentication. Check the account or provider IP allowlist.'
        return '[PROXY_TUNNEL] Proxy rejected the tunnel. Check HTTP/SOCKS5 protocol and IPv4/IPv6 support.'
    if isinstance(error, httpx.HTTPStatusError):
        return f'[IP_SERVICE_HTTP_{error.response.status_code}] The IP-check service rejected this request through the proxy.'
    if isinstance(error, httpx.ConnectError):
        if 'certificate' in detail or 'ssl' in detail:
            return '[TLS_CERTIFICATE] TLS certificate validation failed through the proxy.'
        return '[PROXY_CONNECT] Could not connect to the proxy endpoint. Check the port and provider availability.'
    if isinstance(error, httpx.RemoteProtocolError):
        return '[PROXY_PROTOCOL] Proxy closed the connection or returned an invalid protocol response.'
    if isinstance(error, httpx.NetworkError):
        return '[PROXY_NETWORK] Network communication through the proxy failed.'
    if isinstance(error, (ValueError, AttributeError, TypeError)):
        return '[IP_RESPONSE] The IP-check service did not return a valid address for the selected IP version.'
    return '[PROXY_CHECK] Proxy check failed. Direct fallback is disabled.'

class ProxyService:
    def __init__(self, db, state, config):
        self.db, self.state, self.config = db, state, config
        self.is_busy = lambda profile_id: False
        self.checking = set()
        self.jobs = set()
        self.semaphore = asyncio.Semaphore(10)

    def import_proxies(self, content, assign, default_protocol='http'):
        parsed, errors = parse_import(content, default_protocol)
        created, imported, duplicates = [], [], 0
        with self.db.session.begin() as session:
            identities = dict(session.execute(select(Proxy.identity,Proxy.id)).all())
            private_ids=set(session.scalars(select(BmProxy.proxy_id)))
            for line, value in parsed:
                if value.identity in identities:
                    if identities[value.identity] in private_ids:
                        errors.append({'line':line,'message':'Proxy này thuộc nhóm BM riêng, không nhập vào Proxy Manager thường.'})
                        continue
                    duplicates += 1
                    imported.append(identities[value.identity])
                    continue
                row = Proxy(protocol=value.protocol, host=value.host, port=value.port, identity=value.identity,
                            encrypted_credentials=encrypt_credentials(value.username, value.password))
                session.add(row)
                session.flush()
                identities[value.identity]=row.id
                created.append(row.id)
                imported.append(row.id)
            assignments = 0
            if assign:
                profiles = list(session.scalars(select(Profile).where(Profile.proxy_id == None,Profile.id.not_in(select(BmProfile.profile_id))).order_by(Profile.id)))
                used = select(Profile.proxy_id).where(Profile.proxy_id != None)
                proxies = list(session.scalars(select(Proxy).where(Proxy.id.not_in(used),Proxy.id.not_in(select(BmProxy.proxy_id))).order_by(Proxy.id)))
                profiles = [p for p in profiles if not self.is_busy(p.id)]
                for profile, proxy in zip(profiles, proxies):
                    profile.proxy_id = proxy.id
                    assignments += 1
        self.state.log(f'Imported {len(created)} proxies; assigned {assignments} profiles.')
        return {'created': len(created), 'duplicates': duplicates, 'assigned': assignments, 'errors': errors,'proxy_ids':list(dict.fromkeys(imported))}

    def schedule_checks(self, ids):
        accepted = []
        for proxy_id in ids:
            if proxy_id in self.checking:
                continue
            self.checking.add(proxy_id)
            job = asyncio.create_task(self._check(proxy_id))
            self.jobs.add(job)
            job.add_done_callback(self.jobs.discard)
            accepted.append(proxy_id)
        self.state.emit()
        return accepted

    def schedule_pending_checks(self):
        path=self.config.data_dir/'pending-proxy-checks.json'
        if not path.exists():return []
        try:
            if path.is_symlink() or path.stat().st_size>64000:raise ValueError()
            data=json.loads(path.read_text(encoding='utf-8'))
            ids=data.get('proxy_ids')
            if data.get('version')!=1 or not isinstance(ids,list) or len(ids)>1000 or any(type(value) is not int or value<=0 for value in ids):
                raise ValueError()
            with self.db.session() as session:
                existing=set(session.scalars(select(Proxy.id).where(Proxy.id.not_in(select(BmProxy.proxy_id)))))
            accepted=self.schedule_checks(list(dict.fromkeys(value for value in ids if value in existing)))
            path.unlink()
            self.state.log(f'Queued {len(accepted)} saved proxy checks requested for startup.')
            return accepted
        except (OSError,ValueError,AttributeError):
            self.state.log('Saved proxy-check request is invalid or unreadable; no additional checks were queued.',level='warning')
            return []

    async def _check(self, proxy_id):
        try:
            async with self.semaphore:
                with self.db.session() as session:
                    row = session.get(Proxy, proxy_id)
                    if not row:
                        return
                    value = from_row(row)
                    # Dedicated BM endpoints may provide IPv4 even when the
                    # ordinary pool requires IPv6. Preserve this job's chosen
                    # family for both its destination and response validation.
                    expected_version = 'auto' if session.get(BmProxy, proxy_id) is not None else self.state.settings.proxy_ip_version
                start = time.perf_counter()
                status, address, error, latency = 'Failed', None, None, None
                try:
                    async with httpx.AsyncClient(proxy=value.url(), timeout=12, trust_env=False, follow_redirects=False) as client:
                        response = await client.get(self.config.ip_check_url_for(expected_version))
                        response.raise_for_status()
                        body = response.json()
                        ip = ipaddress.ip_address(body.get('ip') or body.get('origin', '').split(',')[0].strip())
                        if (expected_version == 'ipv6' and ip.version != 6) or (expected_version == 'ipv4' and ip.version != 4):
                            raise ValueError('Unexpected IP address family.')
                        address = str(ip)
                        status = 'Working'
                        latency = round((time.perf_counter() - start) * 1000)
                except httpx.TimeoutException:
                    status, error = 'Timeout', 'Proxy did not respond within 12 seconds.'
                except Exception as exc:
                    error = proxy_failure_message(exc)
                with self.db.session.begin() as session:
                    row = session.get(Proxy, proxy_id)
                    if row:
                        row.status, row.ip_address, row.error_message = status, address, error
                        row.latency_ms, row.last_checked = latency, now()
        except Exception:
            with self.db.session.begin() as session:
                row = session.get(Proxy, proxy_id)
                if row:
                    row.status, row.error_message, row.last_checked = 'Failed', 'Credentials cannot be decrypted by this Windows account.', now()
        finally:
            self.checking.discard(proxy_id)
            self.state.emit()

    async def shutdown(self):
        for job in self.jobs:
            job.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)

