"""Hosted PODSach gateway contract, offline transports only."""
import gzip

import httpx
import pytest

from page_manager.uploader_gateway import (
    GatewayClient, GatewayError, HOST, ORIGIN, SHOP_PREFIX, TICKET_PREFIX,
)

TID = 'AbcdEfgh1234_-XY'
API = SHOP_PREFIX + '/api/overview'


class Clock:
    def __init__(self): self.value = 0.0
    def now(self): return self.value
    def sleep(self, delay): self.value += delay


def client(handler, **kwargs):
    clock = Clock()
    value = GatewayClient(base_url=ORIGIN, transport=httpx.MockTransport(handler),
                          gateway_clock=clock.now, gateway_sleep=clock.sleep,
                          follow_redirects=False, **kwargs)
    return value, clock


def test_direct_json_is_bounded_and_keeps_original_request():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'info':{'domain':'blueox179.com'}})
    value,_=client(handler)
    with value, value.stream('GET',API) as response:
        assert response.json()['info']['domain']=='blueox179.com'
        assert response.request is calls[0]
    assert len(calls)==1


def test_post_is_sent_once_and_known_ticket_polled_with_hooks():
    calls=[];flags=[];holder={}
    def hook(request):
        flags.append(holder['client'].is_gateway_poll(request))
        request.headers['X-PODSach-Expected-Shop']='blueox179.com'
    def handler(request):
        calls.append(request)
        assert request.headers['X-PODSach-Expected-Shop']=='blueox179.com'
        if len(calls)<3:return httpx.Response(202,json={'gateway_pending':TID})
        return httpx.Response(200,json={'started':True})
    value,_=client(handler,event_hooks={'request':[hook]});holder['client']=value
    with value:
        response=value.post(SHOP_PREFIX+'/api/upload/start',json={'batch':'one','limit':1})
    assert response.json()=={'started':True}
    assert [(r.method,r.url.path) for r in calls]==[
        ('POST',SHOP_PREFIX+'/api/upload/start'),('GET',TICKET_PREFIX+TID),('GET',TICKET_PREFIX+TID)]
    assert flags==[False,True,True]
    assert all(not value.is_gateway_poll(r) for r in calls)
    assert response.request.method=='POST'


@pytest.mark.parametrize('status',[200,400,401,403,404,409,500,502])
def test_ticket_final_response_preserves_status_without_replaying(status):
    seen=[]
    def handler(request):
        seen.append(request)
        if len(seen)==1:return httpx.Response(202,json={'gateway_pending':TID})
        return httpx.Response(status,json={'ok':status==200},headers={'X-Synthetic':'preserved'})
    value,_=client(handler)
    with value: response=value.get(API)
    assert response.status_code==status and response.headers['X-Synthetic']=='preserved'
    assert response.json()=={'ok':status==200} and len(seen)==2


@pytest.mark.parametrize('payload',[
    {}, {'gateway_pending':None}, {'gateway_pending':'short'},
    {'gateway_pending':'https://evil.test'}, {'gateway_pending':'../AbcdEfgh1234_-'},
    {'gateway_pending':TID,'url':'https://evil.test'},
])
def test_invalid_gateway_payload_never_causes_another_request(payload):
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(202,json=payload)
    value,_=client(handler)
    with value,pytest.raises(GatewayError) as error:value.post(SHOP_PREFIX+'/api/prep/start',json={})
    assert error.value.code=='uploader_gateway_invalid_ticket' and len(calls)==1


@pytest.mark.parametrize('body,content_type',[
    (b'{"gateway_pending":"'+TID.encode()+b'","gateway_pending":"'+TID.encode()+b'"}','application/json'),
    (b'not JSON','application/json'),(b'<!doctype html>','text/html'),
])
def test_malformed_or_duplicate_ticket_payload_is_rejected(body,content_type):
    value,_=client(lambda _r:httpx.Response(202,content=body,headers={'Content-Type':content_type}))
    with value,pytest.raises(GatewayError):value.get(API)


def test_gateway_cannot_change_ticket_during_polling():
    seen=[]
    def handler(request):seen.append(request);return httpx.Response(202,json={'gateway_pending':TID if len(seen)==1 else 'AnotherTicket123'})
    value,_=client(handler)
    with value,pytest.raises(GatewayError) as error:value.get(API)
    assert error.value.code in ('uploader_gateway_invalid_ticket','uploader_gateway_ticket_changed')
    assert len(seen)==2


def test_deadline_and_poll_count_are_bounded():
    seen=[]
    def handler(request):seen.append(request);return httpx.Response(202,json={'gateway_pending':TID})
    value,clock=client(handler,gateway_timeout_seconds=1,gateway_poll_interval=0.4)
    with value,pytest.raises(GatewayError) as error:value.post(SHOP_PREFIX+'/api/upload/start',json={})
    assert error.value.code=='uploader_gateway_timeout' and clock.value==1
    assert sum(r.method=='POST' for r in seen)==1 and len(seen)<=4
    value,_=client(handler,gateway_max_polls=2,gateway_poll_interval=0)
    before=len(seen)
    with value,pytest.raises(GatewayError):value.get(API)
    assert len(seen)-before==3


def test_timeout_on_poll_does_not_resubmit_post():
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:return httpx.Response(202,json={'gateway_pending':TID})
        raise httpx.ReadTimeout('SYNTHETIC',request=request)
    value,_=client(handler)
    with value,pytest.raises(httpx.ReadTimeout):value.post(SHOP_PREFIX+'/api/upload/start',json={})
    assert [r.method for r in calls]==['POST','GET']


@pytest.mark.parametrize('route',[
    ORIGIN+TICKET_PREFIX+TID, ORIGIN+'/s/247smileworld.com/api/overview',
    'https://evil.test'+API,'http://'+HOST+API,
    'https://user:pass@'+HOST+API,
])
def test_direct_ticket_other_shop_and_foreign_origins_are_blocked(route):
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(200,json={})
    value,_=client(handler)
    with value,pytest.raises(GatewayError):value.get(route)
    assert not calls


def test_api_redirect_is_not_followed_even_if_caller_requests_it():
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(302,headers={'Location':'https://evil.test'})
    value,_=client(handler)
    with value:response=value.get(API,follow_redirects=True)
    assert response.status_code==302 and len(calls)==1


def test_compressed_json_is_not_decoded_twice_and_expansion_is_bounded():
    data=b'{"ok":true}'
    value,_=client(lambda _r:httpx.Response(200,content=gzip.compress(data),headers={'Content-Type':'application/json','Content-Encoding':'gzip'}))
    with value:response=value.get(API)
    assert response.json()=={'ok':True} and 'content-encoding' not in response.headers
    value,_=client(lambda _r:httpx.Response(200,content=gzip.compress(b'x'*1000),headers={'Content-Encoding':'gzip'}),gateway_max_response_bytes=128)
    with value,pytest.raises(GatewayError) as error:value.get(API)
    assert error.value.code=='uploader_gateway_response_too_large'


def test_poll_timeout_can_accommodate_server_25_second_long_poll():
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:return httpx.Response(202,json={'gateway_pending':TID})
        assert 25 < request.extensions['timeout']['read'] <= 35
        return httpx.Response(200,json={'ok':True})
    value,_=client(handler,timeout=5)
    with value:assert value.get(API).json()=={'ok':True}
    assert calls[0].extensions['timeout']['read']==5


def test_image_stream_and_login_are_not_ticket_interpreted():
    value,_=client(lambda _r:httpx.Response(200,content=b'x'*2000),gateway_max_response_bytes=100)
    with value,value.stream('GET',SHOP_PREFIX+'/img/input/source.png') as response:
        assert len(response.read())==2000


@pytest.mark.parametrize('kwargs',[
    {'gateway_timeout_seconds':0},{'gateway_timeout_seconds':301},
    {'gateway_timeout_seconds':float('nan')},{'gateway_timeout_seconds':True},
    {'gateway_poll_interval':-1},{'gateway_max_polls':0},
    {'gateway_max_response_bytes':524289},
])
def test_invalid_configuration_rejected(kwargs):
    with pytest.raises(ValueError):GatewayClient(**kwargs)
