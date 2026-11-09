"""Profile-owned loopback adapter handles upstream auth before Chromium sees it."""
import asyncio
import json
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
import psutil
from app.core.proxy_parser import ParsedProxy
from app.services.socks_bridge import SocksBridge

ERRORS = {
    'UPSTREAM_AUTH': 'Proxy rejected its saved credentials. Check the account or provider IP allowlist.',
    'UPSTREAM_CONNECT': 'Could not connect through the assigned proxy. Direct fallback is disabled.',
    'UPSTREAM_TUNNEL': 'The assigned proxy rejected the destination tunnel.',
    'UPSTREAM_TIMEOUT': 'The assigned proxy connection timed out.',
    'UPSTREAM_TLS': 'The HTTPS proxy certificate could not be validated.',
    'ADAPTER_STOPPED': 'The private proxy adapter stopped. Stop and reopen this profile to reconnect through its assigned proxy.',
    'LOCAL_DENIED': 'A local proxy connection was denied because its profile ownership could not be verified.',
    'DIRECT_CONNECT': 'A direct website connection failed. Facebook routing still uses its assigned proxy.',
    'DIRECT_TIMEOUT': 'A direct website connection timed out. Facebook routing still uses its assigned proxy.',
}


def owns_proxy_connection(client_port, server_port, profile_path):
    """Only the Chrome process tree for this exact data directory may use the port."""
    expected = Path(profile_path).resolve()
    for attempt in range(3):
        try:
            for connection in psutil.net_connections(kind='tcp4'):
                if not connection.pid or not connection.laddr or not connection.raddr:
                    continue
                if (connection.laddr.ip != '127.0.0.1' or connection.laddr.port != client_port
                        or connection.raddr.ip != '127.0.0.1' or connection.raddr.port != server_port):
                    continue
                process = psutil.Process(connection.pid)
                for owner in [process, *process.parents()]:
                    try:
                        if owner.name().lower() not in ('chrome.exe', 'chromium.exe', 'chrome', 'chromium'):
                            continue
                        arguments = owner.cmdline()
                        for index, argument in enumerate(arguments):
                            raw_path = argument.split('=', 1)[1] if argument.startswith('--user-data-dir=') else arguments[index+1] if argument=='--user-data-dir' and index+1<len(arguments) else None
                            if raw_path and Path(raw_path.strip('"')).resolve() == expected:
                                return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                        continue
                return False
        except (psutil.Error, OSError):
            return False
        if attempt < 2:
            time.sleep(.02)
    return False


class BrowserProxyBridge:
    def __init__(self, upstream, profile_path, on_error=None, *, routing='all'):
        if routing not in ('all', 'facebook_only'):
            raise ValueError('Invalid proxy routing mode.')
        self.upstream, self.profile_path, self.on_error = upstream, Path(profile_path), on_error
        self.routing = routing
        self.process = self.monitor = self.socks = None
        self.authorization_jobs = set()
        self.authorization_limit = asyncio.Semaphore(8)
        self.reported = set()
        self.closing = False

    async def start(self):
        node = shutil.which('node.exe') or shutil.which('node')
        if not node:
            raise ValueError('Node.js is required for automatic proxy authentication.')
        upstream = self.upstream
        if upstream.protocol == 'socks5':
            self.socks = SocksBridge(upstream)
            options = await self.socks.start()
            address = urlsplit(options['server'])
            upstream = ParsedProxy('http', address.hostname, address.port, options['username'], options['password'])
        script = Path(__file__).resolve().parents[3] / 'scripts' / 'proxy_bridge.cjs'
        self.process = await asyncio.create_subprocess_exec(node, str(script), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        configuration = {'protocol':upstream.protocol,'host':upstream.host,'port':upstream.port,
                         'username':upstream.username,'password':upstream.password,'routing':self.routing}
        self.process.stdin.write((json.dumps(configuration)+'\n').encode('utf-8'))
        await self.process.stdin.drain()
        first = await asyncio.wait_for(self.process.stdout.readline(), 10)
        ready = json.loads(first)
        if ready.get('type') != 'ready' or not isinstance(ready.get('port'), int):
            raise ValueError('Private proxy authentication adapter did not start.')
        self.monitor = asyncio.create_task(self._monitor())
        return {'server':f"http://127.0.0.1:{ready['port']}"}

    async def _authorize(self, message):
        try:
            async with self.authorization_limit:
                allowed = await asyncio.to_thread(owns_proxy_connection, message['client_port'], message['server_port'], self.profile_path)
            if not self.closing and self.process and self.process.returncode is None:
                response = {'type':'authorize','id':message['id'],'allow':allowed}
                self.process.stdin.write((json.dumps(response)+'\n').encode())
                await self.process.stdin.drain()
        except (Exception, asyncio.CancelledError):
            pass  # No permission response means the adapter fails closed.

    async def _monitor(self):
        try:
            async for line in self.process.stdout:
                message = json.loads(line)
                if message.get('type') == 'authorize':
                    job = asyncio.create_task(self._authorize(message))
                    self.authorization_jobs.add(job)
                    job.add_done_callback(self.authorization_jobs.discard)
                elif message.get('type') == 'error':
                    code = message.get('code')
                    if code in ERRORS and code not in self.reported:
                        self.reported.add(code)
                        if self.on_error:
                            self.on_error(ERRORS[code])
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            if not self.closing and self.on_error:
                self.on_error(ERRORS['ADAPTER_STOPPED'])

    async def close(self):
        self.closing = True
        process = self.process
        if process and process.returncode is None:
            try:
                process.stdin.write(b'{"type":"close"}\n')
                await process.stdin.drain()
                await asyncio.wait_for(process.wait(), 5)
            except Exception:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        for job in tuple(self.authorization_jobs):
            job.cancel()
        await asyncio.gather(*self.authorization_jobs, return_exceptions=True)
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.socks:
            await self.socks.close()
