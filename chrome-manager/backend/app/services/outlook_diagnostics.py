"""Public connectivity probes with no mailbox, credentials or response-body logs."""
import re
import time
import base64
import http.client
from urllib.parse import urlsplit
import httpx

TARGETS = (
    ('ipv6_control', 'https://api6.ipify.org?format=json'),
    ('outlook_personal', 'https://outlook.live.com/mail/'),
    ('microsoft_login', 'https://login.live.com/'),
    ('outlook_work', 'https://outlook.office.com/mail/'),
)


def failure_code(error):
    current, visited = error, set()
    chain = []
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        chain.append(current)
        if getattr(current, 'winerror', None) == 10013 or getattr(current, 'errno', None) in (13, 10013):
            return 'LOCAL_NETWORK_PERMISSION_DENIED'
        current = current.__cause__ or current.__context__
    for item in chain:
        if getattr(item, 'winerror', None) == 10054 or getattr(item, 'errno', None) in (54, 104, 10054):
            return 'CONNECTION_RESET'
        detail = str(item).upper()
        for token, code in (
            ('CERTIFICATE_VERIFY_FAILED', 'TLS_CERTIFICATE'),
            ('UNEXPECTED_EOF_WHILE_READING', 'TLS_UNEXPECTED_EOF'),
            ('UNSAFE_LEGACY_RENEGOTIATION_DISABLED', 'TLS_LEGACY_RENEGOTIATION_REJECTED'),
            ('TLSV1_ALERT_PROTOCOL_VERSION', 'TLS_PROTOCOL_REJECTED'),
            ('WRONG_VERSION_NUMBER', 'TLS_PROTOCOL_MISMATCH'),
        ):
            if token in detail:
                return code
    if isinstance(error, httpx.TimeoutException):
        return 'TIMEOUT'
    if isinstance(error, httpx.ProxyError):
        # Exception strings can contain proxy credentials. Return fixed tokens only.
        return 'PROXY_AUTH' if re.search(r'\b407\b', str(error)) else 'PROXY_TUNNEL'
    if isinstance(error, httpx.ConnectError):
        return 'TLS_CERTIFICATE' if 'certificate' in str(error).lower() else 'CONNECT_FAILED'
    if isinstance(error, httpx.RemoteProtocolError):
        return 'CONNECTION_CLOSED_BY_PEER'
    return 'NETWORK_ERROR'


def probe_login_tunnel(proxy):
    """Separate CONNECT success from the later Microsoft TLS handshake."""
    if proxy.protocol not in ('http', 'https'):
        return {'target': 'microsoft_login', 'error': 'TUNNEL_PROBE_UNSUPPORTED_PROTOCOL'}
    connection = (http.client.HTTPSConnection if proxy.protocol == 'https' else http.client.HTTPConnection)(
        proxy.host, proxy.port, timeout=10)
    credential = base64.b64encode((proxy.username + ':' + proxy.password).encode('utf-8')).decode('ascii')
    try:
        connection.set_tunnel('login.live.com', 443, headers={'Proxy-Authorization': 'Basic ' + credential})
        connection.connect()
        return {'target': 'microsoft_login', 'connect_tunnel': True}
    except Exception as error:
        result = {'target': 'microsoft_login', 'connect_tunnel': False, 'error': failure_code(error)}
        match = re.search(r'Tunnel connection failed: (\d{3})\b', str(error))
        if match:
            result['proxy_http_status'] = int(match.group(1))
        return result
    finally:
        connection.close()


def probe_proxy(proxy_id, proxy_url):
    results = []
    # An explicit proxy and trust_env=False prevent any direct fallback.
    with httpx.Client(proxy=proxy_url, timeout=10, trust_env=False, follow_redirects=False) as client:
        for name, url in TARGETS:
            start = time.monotonic()
            row = {'proxy_id': proxy_id, 'target': name}
            try:
                # Only status/redirect host are needed, never read a mailbox or body.
                with client.stream('GET', url) as response:
                    row.update(status=response.status_code,
                               redirect_host=urlsplit(response.headers.get('location', '')).hostname)
            except Exception as error:
                row['error'] = failure_code(error)
            row['latency_ms'] = round((time.monotonic() - start) * 1000)
            results.append(row)
    return results
