import httpx
import pytest
from via_mcp.bridge import ManagerClient
from via_mcp.common import ToolError


def test_private_service_header_is_sent_only_to_local_clients_and_never_returned():
    secret='fixture_service_'+'x'*48
    seen=[]
    def handler(request):
        assert request.url.host=='127.0.0.1' and request.url.port==8000
        assert request.headers['x-via-service-token']==secret
        seen.append(request)
        if request.url.path=='/api/session':
            return httpx.Response(200,json={'ok':True},headers={'set-cookie':'cm_session=fixture-session; Path=/'})
        return httpx.Response(200,json={'message':'Safe metadata '+secret,'nested':[secret]})
    client=ManagerClient(8000,None,service_token=secret,transport=httpx.MockTransport(handler))
    try:
        value=client.request('chrome','GET','/api/profiles',read_only=True)
        assert secret not in str(value) and value['message']=='Safe metadata [redacted]'
        assert len(seen)==2 and all(not c.trust_env and not c.follow_redirects for c in client.clients.values())
    finally:client.close()


def test_redirect_never_transmits_service_key_to_another_host():
    secret='fixture_service_'+'y'*48
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.host=='127.0.0.1'
        return httpx.Response(302,json={'safe':True},headers={'location':'https://other.invalid'})
    client=ManagerClient(8000,None,service_token=secret,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ToolError):client.request('chrome','GET','/api/profiles',read_only=True)
        assert len(seen)==1
    finally:client.close()
