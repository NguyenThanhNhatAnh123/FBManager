"""Bounded resolution of the hosted PODSach gateway's asynchronous replies.

Only the gateway-generated ticket is polled. The original request, particularly
a POST that can create a campaign, is never replayed. Poll URLs are constructed
from a validated opaque ID in the one fixed Blueox shop namespace.
"""
import json
import math
import re
import threading
import time

import httpx

ORIGIN = 'https://ul.eagleglobal2023.com'
HOST = 'ul.eagleglobal2023.com'
SHOP_PREFIX = '/s/blueox179.com'
API_PREFIX = SHOP_PREFIX + '/api/'
TICKET_PREFIX = SHOP_PREFIX + '/_gw/ticket/'
TICKET = re.compile(r'[A-Za-z0-9_-]{16}\Z', re.ASCII)
MAX_RESPONSE_BYTES = 512 * 1024


class GatewayError(httpx.RequestError):
    """Fixed, credential-free error codes for callers' own safe diagnostics."""
    def __init__(self, code, *, request):
        self.code = code
        super().__init__('Hosted uploader gateway did not confirm the operation.', request=request)


def _origin_ok(url):
    return (url.scheme == 'https' and url.host == HOST and url.port in (None, 443)
            and not url.username and not url.password and not url.fragment)


class GatewayClient(httpx.Client):
    """An httpx client with a deadline-limited, fixed-shop ticket resolver.

    Existing origin/auth event hooks remain active on each internally generated
    GET. A hook can call ``client.is_gateway_poll(request)`` to distinguish those
    requests from an attempted direct ticket request by its caller. Initial
    network timeouts are preserved; explicit long-running API calls should use
    a read timeout above the gateway's 60-second synchronous window.
    """
    def __init__(self, *args, gateway_timeout_seconds=120, gateway_poll_interval=0.4,
                 gateway_max_polls=200, gateway_max_response_bytes=MAX_RESPONSE_BYTES,
                 gateway_clock=None, gateway_sleep=None, **kwargs):
        if (type(gateway_timeout_seconds) not in (int, float) or not math.isfinite(gateway_timeout_seconds)
                or not 0 < gateway_timeout_seconds <= 300
                or type(gateway_poll_interval) not in (int, float) or not math.isfinite(gateway_poll_interval)
                or not 0 <= gateway_poll_interval <= 1
                or type(gateway_max_polls) is not int or not 1 <= gateway_max_polls <= 1000
                or type(gateway_max_response_bytes) is not int or not 1 <= gateway_max_response_bytes <= MAX_RESPONSE_BYTES):
            raise ValueError('Invalid hosted uploader gateway limits')
        self._gateway_duration = gateway_timeout_seconds
        self._gateway_interval = gateway_poll_interval
        self._gateway_max_polls = gateway_max_polls
        self._gateway_limit = gateway_max_response_bytes
        self._gateway_clock = gateway_clock or time.monotonic
        self._gateway_sleep = gateway_sleep or time.sleep
        self._gateway_requests = {}
        self._gateway_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def is_gateway_poll(self, request):
        with self._gateway_lock:
            expected = self._gateway_requests.get(id(request))
        return (expected is not None and request.method == 'GET' and _origin_ok(request.url)
                and request.url.path == expected and not request.url.query)

    def _remaining(self, deadline, request):
        remaining = deadline - self._gateway_clock()
        if remaining <= 0:
            raise GatewayError('uploader_gateway_timeout', request=request)
        return remaining

    def _read(self, response, deadline, original):
        try:
            length = response.headers.get('content-length')
            if length is not None and (not length.isdecimal() or int(length) > self._gateway_limit):
                raise GatewayError('uploader_gateway_response_too_large', request=original)
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=16384):
                self._remaining(deadline, original)
                data.extend(chunk)
                if len(data) > self._gateway_limit:
                    raise GatewayError('uploader_gateway_response_too_large', request=original)
            self._remaining(deadline, original)
            return bytes(data)
        finally:
            response.close()

    def _ticket_id(self, raw, response, original):
        if 'application/json' not in response.headers.get('content-type', '').lower():
            raise GatewayError('uploader_gateway_invalid_ticket', request=original)
        def unique(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError('Duplicate key')
                result[key] = value
            return result
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
            ticket = value.get('gateway_pending') if isinstance(value, dict) else None
            if not isinstance(value, dict) or set(value) != {'gateway_pending'} or not isinstance(ticket, str) or not TICKET.fullmatch(ticket):
                raise ValueError('Invalid ticket')
        except (ValueError, UnicodeError, RecursionError):
            raise GatewayError('uploader_gateway_invalid_ticket', request=original) from None
        return ticket

    def _materialize(self, response, raw, original):
        # _read() has decoded Content-Encoding, so the rebuilt bounded response
        # must not ask httpx to decompress those bytes a second time.
        headers = response.headers.copy()
        headers.pop('content-encoding', None)
        headers.pop('transfer-encoding', None)
        headers['content-length'] = str(len(raw))
        return httpx.Response(response.status_code, headers=headers, content=raw,
                              request=original, extensions=response.extensions)

    def send(self, request, *, stream=False, **kwargs):
        if not _origin_ok(request.url):
            raise GatewayError('invalid_uploader_target', request=request)
        path = request.url.path
        if path.startswith('/s/') and not (path == SHOP_PREFIX or path.startswith(SHOP_PREFIX + '/')):
            raise GatewayError('invalid_uploader_target', request=request)
        if path.startswith(TICKET_PREFIX):
            # All actual polls go through super().send below. A caller cannot
            # consume an unrelated one-shot ticket using this client.
            raise GatewayError('invalid_uploader_read', request=request)
        if not path.startswith(API_PREFIX):
            return super().send(request, stream=stream, **kwargs)
        deadline = self._gateway_clock() + self._gateway_duration
        remaining = self._remaining(deadline, request)
        timeouts = request.extensions.get('timeout')
        if isinstance(timeouts, dict):
            request.extensions['timeout'] = {key: min(value, remaining) if isinstance(value, (int, float)) else remaining for key, value in timeouts.items()}
        # API JSON is materialized within a strict byte limit even if its
        # consumer requested streaming. Image routes retain normal streaming.
        kwargs['follow_redirects'] = False
        response = super().send(request, stream=True, **kwargs)
        polls, ticket = 0, None
        while True:
            raw = self._read(response, deadline, request)
            if response.status_code != 202:
                return self._materialize(response, raw, request)
            received = self._ticket_id(raw, response, request)
            if ticket is not None and received != ticket:
                raise GatewayError('uploader_gateway_ticket_changed', request=request)
            ticket = received
            if polls >= self._gateway_max_polls:
                raise GatewayError('uploader_gateway_timeout', request=request)
            remaining = self._remaining(deadline, request)
            if self._gateway_interval:
                self._gateway_sleep(min(self._gateway_interval, remaining))
            remaining = self._remaining(deadline, request)
            poll_path = TICKET_PREFIX + ticket
            poll_request = self.build_request('GET', ORIGIN + poll_path,
                headers={'Accept': 'application/json'},
                timeout=httpx.Timeout(min(35, remaining), connect=min(5, remaining)))
            with self._gateway_lock:
                self._gateway_requests[id(poll_request)] = poll_path
            try:
                poll_kwargs = {'stream': True, 'follow_redirects': False}
                if 'auth' in kwargs: poll_kwargs['auth'] = kwargs['auth']
                response = super().send(poll_request, **poll_kwargs)
            finally:
                with self._gateway_lock:
                    self._gateway_requests.pop(id(poll_request), None)
            polls += 1
