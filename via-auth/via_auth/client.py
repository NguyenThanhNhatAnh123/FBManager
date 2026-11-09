"""Explicit local service authentication; never export this key to a browser."""
from pathlib import Path
import urllib.request
from .store import _absolute, _assert_private_path


def service_headers(directory=None):
    directory=_absolute(directory) if directory is not None else Path(__file__).resolve().parents[1]/'data'
    path=directory/'local-service.key'
    if not path.is_file():
        return {}
    _assert_private_path(path)
    value=path.read_text(encoding='ascii').strip()
    if not 40<=len(value)<=128:
        raise ValueError('Invalid local service credential')
    return {'X-Via-Service-Token':value}


def local_opener(*handlers, origin='http://127.0.0.1:8000', directory=None):
    """For reviewed host-side scripts only; no proxies, redirects or key forwarding."""
    from urllib.parse import urlsplit
    parsed=urlsplit(origin)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path or parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None or not parsed.port:
        raise ValueError('Invalid local manager origin')
    class Credential(urllib.request.BaseHandler):
        def http_request(self,request):
            target=urlsplit(request.full_url)
            if (target.scheme,target.hostname,target.port)!=(parsed.scheme,parsed.hostname,parsed.port) or target.username is not None or target.password is not None:
                raise ValueError('Local manager requests only')
            for key,value in service_headers(directory).items():
                request.add_unredirected_header(key,value)
            return request
        https_request=http_request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*_args,**_kwargs):return None
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),Credential(),NoRedirect(),*handlers)
