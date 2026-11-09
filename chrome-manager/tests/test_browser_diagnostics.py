from types import SimpleNamespace
from app.services.browser_diagnostics import BrowserNetworkDiagnostics, auth_site, safe_network_failure


class Context:
    def __init__(self): self.listeners={}
    def on(self,event,callback): self.listeners[event]=callback
    def remove_listener(self,event,callback): self.listeners.pop(event,None)


def test_sensitive_url_and_failure_text_are_never_logged():
    messages=[]; diagnostic=BrowserNetworkDiagnostics(Context(),messages.append)
    diagnostic.request_failed(SimpleNamespace(url='https://www.facebook.com/login?encrypted_context=private_token',failure='net::ERR_TUNNEL_CONNECTION_FAILED https://private_user:private_password@host'))
    assert messages==['Facebook connection failed (ERR_TUNNEL_CONNECTION_FAILED).']
    assert not any(secret in messages[0] for secret in ('private_token','private_user','private_password'))


def test_unrelated_hosts_and_cancelled_navigation_are_not_logged():
    messages=[]; diagnostic=BrowserNetworkDiagnostics(Context(),messages.append)
    diagnostic.request_failed(SimpleNamespace(url='https://facebook.com.evil.test',failure='net::ERR_FAILED'))
    diagnostic.request_failed(SimpleNamespace(url='https://www.facebook.com/',failure='net::ERR_ABORTED'))
    assert not messages
    assert auth_site('https://www.gstatic.com/recaptcha/file.js')=='Google verification'
    assert auth_site('data:text/plain,facebook.com') is None
    assert safe_network_failure('sensitive unstructured text')=='NETWORK_ERROR'


def test_website_http_errors_are_counted_once_and_safe():
    messages=[]; diagnostic=BrowserNetworkDiagnostics(Context(),messages.append)
    response=SimpleNamespace(url='https://www.google.com/recaptcha/api2/reload?token=private',status=429)
    for _ in range(20): diagnostic.response_received(response)
    assert messages==['Google verification returned HTTP 429.']


def test_login_return_tracks_only_main_frame_and_no_account_data():
    messages=[]; context=Context(); diagnostic=BrowserNetworkDiagnostics(context,messages.append)
    # Real Playwright pages are hashable; use an identity-only test object too.
    class Page:pass
    page=Page();frame=SimpleNamespace(page=page);page.main_frame=frame
    request=SimpleNamespace(frame=frame,is_navigation_request=lambda:True)
    for route in ('/two_step_verification/authentication/?encrypted_context=secret','/login.php?email=private'):
        diagnostic.response_received(SimpleNamespace(url='https://www.facebook.com'+route,status=200,request=request))
    assert len(messages)==1 and messages[0].startswith('Facebook returned to its login page')
    assert 'secret' not in messages[0] and 'private' not in messages[0]
    diagnostic.close()
    assert not context.listeners and not diagnostic.verification_pages
    diagnostic.request_failed(SimpleNamespace(url='https://www.facebook.com/',failure='net::ERR_FAILED'))
    assert len(messages)==1


def test_diagnostic_output_is_bounded():
    messages=[]; diagnostic=BrowserNetworkDiagnostics(Context(),messages.append)
    for index in range(100):
        diagnostic.response_received(SimpleNamespace(url='https://www.google.com/',status=400+index))
    assert len(messages)==25


def test_log_storage_failure_does_not_interrupt_browsing():
    def failed_log(_):raise RuntimeError('storage unavailable')
    diagnostic=BrowserNetworkDiagnostics(Context(),failed_log)
    diagnostic.request_failed(SimpleNamespace(url='https://www.facebook.com/',failure='net::ERR_PROXY_CONNECTION_FAILED'))
    assert len(diagnostic.seen)==1
