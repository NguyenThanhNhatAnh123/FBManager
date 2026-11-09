import csv
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

@dataclass
class ParsedProxy:
    protocol: str
    host: str
    port: int
    username: str = ''
    password: str = ''

    @property
    def identity(self):
        # Endpoint/account identity intentionally excludes the password.
        return hashlib.sha256(json.dumps([self.protocol, self.host, self.port, self.username]).encode()).hexdigest()

    def url(self):
        credentials = f'{quote(self.username, safe="")}:{quote(self.password, safe="")}@' if self.username else ''
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'{self.protocol}://{credentials}{host}:{self.port}'

def parse_proxy(text: str, default_protocol: str = 'http') -> ParsedProxy:
    if default_protocol not in ('http', 'https', 'socks5'):
        raise ValueError('Unsupported default proxy protocol.')
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        raise ValueError('Proxy must not contain spaces.')
    if '://' not in text:
        # Also accept the common host:port:username:password format.
        parts = text.split(':')
        if len(parts) in (3, 4) and not text.startswith('['):
            host, port, username = parts[:3]
            password = parts[3] if len(parts) == 4 else ''
            text = f'{default_protocol}://{quote(username, safe="")}:{quote(password, safe="")}@{host}:{port}'
        else:
            text = default_protocol + '://' + text
    try:
        parsed = urlsplit(text)
        protocol = parsed.scheme.lower()
        if protocol not in ('http', 'https', 'socks5'):
            raise ValueError()
        host = (parsed.hostname or '').lower()
        if not host or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = host.encode('idna').decode()
            if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
                raise ValueError()
            if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.')):
                raise ValueError()
        port = parsed.port if parsed.port is not None else {'http': 80, 'https': 443, 'socks5': 1080}[protocol]
        if not 1 <= port <= 65535:
            raise ValueError()
        username, password = unquote(parsed.username or ''), unquote(parsed.password or '')
        if password and not username or any(ord(c) < 32 for c in username + password):
            raise ValueError()
        return ParsedProxy(protocol, host, port, username, password)
    except (ValueError, UnicodeError):
        raise ValueError('Invalid proxy. Use host:port or protocol://user:password@host:port.') from None

def parse_import(content, default_protocol='http'):
    lines = [(i, line.strip()) for i, line in enumerate(content.lstrip('\ufeff').splitlines(), 1) if line.strip() and not line.lstrip().startswith('#')]
    if not lines:
        raise ValueError('The proxy list is empty.')
    parsed, errors = [], []
    header = [x.strip().lower() for x in next(csv.reader([lines[0][1]]))]
    has_header = 'host' in header or 'proxy' in header
    if len(lines) - int(has_header) > 1000:
        raise ValueError('Import at most 1000 proxies at a time.')
    for number, line in lines[1:] if has_header else lines:
        try:
            row = next(csv.reader([line]))
            if has_header:
                values = dict(zip(header, row))
                if 'proxy' in values:
                    value = parse_proxy(values['proxy'], default_protocol)
                else:
                    protocol = values.get('protocol') or default_protocol
                    host = values['host']
                    if ':' in host and not host.startswith('['):
                        host = f'[{host}]'
                    auth = ''
                    if values.get('username'):
                        auth = f'{quote(values["username"], safe="")}:{quote(values.get("password", ""), safe="")}@'
                    endpoint = f'{protocol}://{auth}{host}'
                    if values.get('port'):
                        endpoint += ':' + values['port']
                    value = parse_proxy(endpoint)
            elif len(row) == 1:
                value = parse_proxy(row[0], default_protocol)
            elif 2 <= len(row) <= 5:
                host, port = row[:2]
                username = row[2] if len(row) > 2 else ''
                password = row[3] if len(row) > 3 else ''
                protocol = row[4] if len(row) > 4 else default_protocol
                value = parse_proxy(f'{protocol}://{quote(username, safe="")}:{quote(password, safe="")}@{host}:{port}' if username else f'{protocol}://{host}:{port}')
            else:
                raise ValueError()
            parsed.append((number, value))
        except (ValueError, KeyError, csv.Error):
            errors.append({'line': number, 'message': 'Invalid proxy format. Input is hidden to protect credentials.'})
    return parsed, errors

