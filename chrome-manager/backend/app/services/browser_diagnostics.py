"""Passive connection diagnostics: no interception, account data, or full URLs."""
import re
from urllib.parse import urlsplit


def auth_site(url):
    try:
        value = urlsplit(url)
        if value.scheme not in ('http', 'https'):
            return None
        host = value.hostname or ''
        if host == 'facebook.com' or host.endswith('.facebook.com') or host == 'fbcdn.net' or host.endswith('.fbcdn.net'):
            return 'Facebook'
        if any(host == base or host.endswith('.' + base) for base in ('google.com', 'gstatic.com', 'recaptcha.net')):
            return 'Google verification'
    except (TypeError, ValueError):
        pass
    return None


def safe_network_failure(failure):
    match = re.search(r'net::(ERR_[A-Z_]{1,70})(?![A-Z_])', str(failure))
    return match.group(1) if match else 'NETWORK_ERROR'


class BrowserNetworkDiagnostics:
    def __init__(self, context, log):
        self.context, self.log = context, log
        self.seen = set()
        self.verification_pages = set()
        self.closed = False
        context.on('requestfailed', self.request_failed)
        context.on('response', self.response_received)
        context.on('close', self.close)

    def report(self, key, message):
        if not self.closed and key not in self.seen and len(self.seen) < 25:
            self.seen.add(key)
            try:
                self.log(message)
            except Exception:
                pass  # Optional diagnostics must never interrupt login traffic.

    def request_failed(self, request):
        site = auth_site(request.url)
        if not site:
            return
        code = safe_network_failure(request.failure)
        if code == 'ERR_ABORTED':
            return  # Navigation cancellation alone is not a connection failure.
        self.report((site, code), f'{site} connection failed ({code}).')

    def response_received(self, response):
        site = auth_site(response.url)
        if not site:
            return
        if response.status >= 400:
            self.report((site, response.status), f'{site} returned HTTP {response.status}.')
        # Classify only known document routes; never store or print URL tokens.
        if site != 'Facebook':
            return
        request = response.request
        try:
            if not request.is_navigation_request() or request.frame is not request.frame.page.main_frame:
                return
            page = request.frame.page
            path = urlsplit(response.url).path
            if path.startswith(('/two_step_verification/', '/checkpoint/')):
                if len(self.verification_pages) < 50:
                    self.verification_pages.add(page)
            elif page in self.verification_pages and (path in ('/login', '/login.php') or path.startswith('/login/')):
                self.report(('login_return',), 'Facebook returned to its login page after verification. The connection log cannot determine the account-side reason.')
        except Exception:
            pass  # Diagnostic metadata must never interfere with browsing.

    def close(self, *_):
        self.closed = True
        self.verification_pages.clear()
        self.context.remove_listener('requestfailed', self.request_failed)
        self.context.remove_listener('response', self.response_received)
        self.context.remove_listener('close', self.close)
