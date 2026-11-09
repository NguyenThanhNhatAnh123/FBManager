"""Loopback HTTP CONNECT adapter for Chromium's unsupported SOCKS5 authentication."""
import asyncio
import base64
import secrets
from urllib.parse import urlsplit
from python_socks.async_.asyncio import Proxy

class SocksBridge:
    def __init__(self, upstream):
        self.upstream = upstream
        self.server = None
        self.clients = set()
        self.password = secrets.token_urlsafe(24)

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, limit=65536)
        port = self.server.sockets[0].getsockname()[1]
        return {'server': f'http://127.0.0.1:{port}', 'username': 'manager', 'password': self.password}

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.clients.add(task)
        upstream_writer = None
        try:
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 15)
            lines = raw.decode('latin-1').split('\r\n')
            method, target, version = lines[0].split(' ', 2)
            headers = {}
            for line in lines[1:]:
                if ':' in line:
                    key, value = line.split(':', 1)
                    headers[key.lower()] = value.strip()
            expected = 'Basic ' + base64.b64encode(f'manager:{self.password}'.encode()).decode()
            if not secrets.compare_digest(headers.get('proxy-authorization', ''), expected):
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="Chrome Manager"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                await writer.drain()
                return
            destination = urlsplit('//' + target) if method == 'CONNECT' else urlsplit(target)
            host = destination.hostname
            port = destination.port or (443 if method == 'CONNECT' else 80)
            if not host:
                raise ValueError()
            # The upstream SOCKS server resolves the destination; never use a direct socket.
            proxy = Proxy.from_url(self.upstream.url())
            sock = await proxy.connect(dest_host=host, dest_port=port, timeout=20)
            remote_reader, upstream_writer = await asyncio.open_connection(sock=sock)
            if method == 'CONNECT':
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            else:
                path = destination.path or '/'
                if destination.query:
                    path += '?' + destination.query
                clean = [line for line in lines[1:] if line and not line.lower().startswith(('proxy-authorization:', 'proxy-connection:'))]
                upstream_writer.write((f'{method} {path} {version}\r\n' + '\r\n'.join(clean) + '\r\n\r\n').encode('latin-1'))
                await upstream_writer.drain()
            await writer.drain()
            async def pipe(source, dest):
                while data := await source.read(65536):
                    dest.write(data)
                    await dest.drain()
            flows = [asyncio.create_task(pipe(reader, upstream_writer)), asyncio.create_task(pipe(remote_reader, writer))]
            try:
                await asyncio.wait(flows, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for flow in flows:
                    flow.cancel()
                await asyncio.gather(*flows, return_exceptions=True)
        except (Exception, asyncio.CancelledError):
            # No remote addresses, headers, credentials, or request bodies are logged.
            pass
        finally:
            for stream in (upstream_writer, writer):
                if stream:
                    stream.close()
            self.clients.discard(task)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        clients = list(self.clients)
        for client in clients:
            client.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
