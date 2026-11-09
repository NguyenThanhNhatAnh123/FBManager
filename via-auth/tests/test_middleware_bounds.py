import asyncio
import json
from pathlib import Path
import pytest
from via_auth import IdentityGate, IdentityStore


def scope(path='/auth/api/login',method='POST',headers=()):
    return {'type':'http','http_version':'1.1','method':method,'scheme':'http','path':path,'root_path':'',
            'query_string':b'','client':('127.0.0.1',1000),'server':('127.0.0.1',8000),
            'headers':[(b'host',b'127.0.0.1:8000'),(b'content-type',b'application/json'),*headers]}


def run(gate,request,messages):
    sent=[]
    async def receive():
        return messages.pop(0) if messages else {'type':'http.disconnect'}
    async def send(message):sent.append(message)
    asyncio.run(gate(request,receive,send))
    return sent


async def forbidden(*_):
    raise AssertionError('Rejected request reached business app')


def test_auth_body_size_is_checked_before_reading_and_without_content_length(tmp_path):
    gate=IdentityGate(forbidden,IdentityStore(tmp_path/'auth'))
    for request,messages in [(scope(headers=[(b'content-length',b'20000')]),[]),
        (scope(),[{'type':'http.request','body':b'x'*16385,'more_body':False}])]:
        sent=run(gate,request,messages)
        assert sent[0]['status']==413


def test_exception_after_response_start_never_sends_a_second_response(tmp_path):
    store=IdentityStore(tmp_path/'auth')
    store.ensure_service_key()
    key=store.service_path.read_text()
    async def broken(scope,receive,send):
        await send({'type':'http.response.start','status':200,'headers':[]})
        raise RuntimeError('inert fixture exception')
    gate=IdentityGate(broken,store)
    sent=[]
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    async def send(message):sent.append(message)
    with pytest.raises(RuntimeError,match='inert fixture'):
        asyncio.run(gate(scope('/api/example','GET',[(b'x-via-service-token',key.encode())]),receive,send))
    assert [x['type'] for x in sent]==['http.response.start']


def test_untrusted_origin_and_fake_principal_header_cannot_initialize_session(tmp_path):
    gate=IdentityGate(forbidden,IdentityStore(tmp_path/'auth'))
    for headers,status in [([(b'origin',b'https://other.invalid')],403),
        ([(b'x-via-user',b'admin'),(b'x-forwarded-user',b'admin')],401)]:
        sent=run(gate,scope('/api/session','GET',headers),[])
        assert sent[0]['status']==status
