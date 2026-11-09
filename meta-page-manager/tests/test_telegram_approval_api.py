"""Loopback ASGI/fake Bot API only; no external requests or real credentials."""
import asyncio
import httpx
import pytest

from test_producer_diagnostics_api import fixture, request

TOKEN='12345:FAKE_TELEGRAM_TOKEN_SECRET_123456'


def setup(f):
    service=f.child.state.telegram_approval;calls=[]
    def transport(req):
        method=req.url.path.rsplit('/',1)[-1];calls.append(method)
        return httpx.Response(200,json={'ok':True,'result':{'id':12345,'is_bot':True,'username':'fixture_bot'} if method=='getMe' else {'url':''}})
    service.transport=httpx.MockTransport(transport)
    return service,calls


def test_get_and_board_embed_are_read_only_and_token_is_write_only(fixture):
    f=fixture;service,calls=setup(f)
    code,_,value=request(f,'/api/telegram-approval',headers=f.headers)
    assert code==200 and value['enabled'] is False and value['token_saved'] is False
    body={'revision':1,'enabled':False,'bot_token':TOKEN,'chat_id':'-100123','allowed_user_ids':['99']}
    code,_,value=request(f,'/api/telegram-approval','PUT',body,f.headers)
    assert code==200 and value['token_saved'] is True and TOKEN not in str(value)
    for path in ('/api/telegram-approval','/api/idea-pipeline?summary=true&limit=10'):
        code,_,value=request(f,path,headers=f.headers)
        assert code==200 and TOKEN not in str(value)
        if path.startswith('/api/idea-pipeline'):assert value['telegram']['token_saved'] is True
    assert calls==[]


def test_telegram_mutations_require_session_csrf_strict_revision_and_safe_validation(fixture):
    f=fixture;service,calls=setup(f)
    assert request(f,'/api/telegram-approval')[0]==401
    assert request(f,'/api/telegram-approval','PUT',{'revision':1,'enabled':False})[0]==401
    assert request(f,'/api/telegram-approval','PUT',{'revision':1,'enabled':False},{'cookie':f.headers['cookie']})[0]==403
    for body in ({'revision':True,'enabled':False},{'revision':1,'enabled':'false'},
                 {'revision':1,'enabled':False,'bot_token':TOKEN,'foreign_secret':TOKEN}):
        code,_,value=request(f,'/api/telegram-approval','PUT',body,f.headers)
        assert code==422 and TOKEN not in str(value)
    assert calls==[]


def test_explicit_check_then_enable_and_disconnect(fixture):
    f=fixture;service,calls=setup(f)
    body={'revision':1,'enabled':False,'bot_token':TOKEN,'chat_id':'-100123','allowed_user_ids':['99']}
    assert request(f,'/api/telegram-approval','PUT',body,f.headers)[0]==200
    assert request(f,'/api/telegram-approval','PUT',{'revision':2,'enabled':True},f.headers)[0]==409
    code,_,value=request(f,'/api/telegram-approval/check','POST',{'revision':2},f.headers)
    assert code==200 and value['check']['ok'] is True and calls==['getMe','getWebhookInfo']
    code,_,value=request(f,'/api/telegram-approval','PUT',{'revision':2,'enabled':True},f.headers)
    assert code==200 and value['enabled'] is True
    code,_,value=request(f,'/api/telegram-approval/connection','DELETE',{'revision':3},f.headers)
    assert code==200 and value['token_saved'] is False
    assert calls==['getMe','getWebhookInfo']


def test_preview_is_authenticated_and_never_takes_a_path(fixture):
    f=fixture;setup(f)
    assert request(f,'/api/telegram-approval/requests/not-a-uuid/video',headers=f.headers)[0]==404
    assert request(f,'/api/telegram-approval/requests/not-a-uuid/video')[0]==401


def test_lifespan_stops_only_owned_telegram_service(fixture):
    f=fixture;service,calls=setup(f)
    async def lifespan():
        async with f.child.router.lifespan_context(f.child):pass
    asyncio.run(lifespan())
    assert service._closing is True and calls==[]


def test_one_video_trial_route_requires_csrf_and_exact_strict_target(fixture,monkeypatch):
    f=fixture;service,calls=setup(f);received=[]
    target='12345678-1234-4123-8123-123456789abc'
    monkeypatch.setattr(service,'start_trial',lambda revision,request_id:received.append((revision,request_id)) or {'enabled':False,'trial':{'request_id':request_id}})
    body={'revision':2,'request_id':target}
    assert request(f,'/api/telegram-approval/trial','POST',body)[0]==401
    assert request(f,'/api/telegram-approval/trial','POST',body,{'cookie':f.headers['cookie']})[0]==403
    for invalid in ({**body,'revision':True},{**body,'enabled':True},{**body,'request_id':'../other'},
                    {**body,'request_id':[target]},{**body,'bot_token':TOKEN}):
        code,_,value=request(f,'/api/telegram-approval/trial','POST',invalid,f.headers)
        assert code==422 and TOKEN not in str(value)
    assert received==[] and calls==[]
    code,_,value=request(f,'/api/telegram-approval/trial','POST',body,f.headers)
    assert code==200 and value['enabled'] is False and received==[(2,target)] and calls==[]


def test_exact_cohort_route_requires_csrf_closed_schema_and_never_enables_global(fixture,monkeypatch):
    f=fixture;service,calls=setup(f);received=[]
    target='12345678-1234-4123-8123-123456789abc'
    other='12345678-1234-4123-8123-123456789abd'
    body={'revision':2,'gate_revision':2,'request_id':target,
        'members':[{'request_id':target,'binding_hash':'a'*64},{'request_id':other,'binding_hash':'b'*64}]}
    monkeypatch.setattr(service,'start_cohort',lambda value:received.append(value) or {'enabled':False,'cohort':{'total':2}})
    path='/api/telegram-approval/cohorts'
    assert request(f,path,'POST',body)[0]==401
    assert request(f,path,'POST',body,{'cookie':f.headers['cookie']})[0]==403
    for invalid in ({**body,'enabled':True},{**body,'revision':True},{**body,'gate_revision':False},
                    {**body,'members':body['members'][:1]},{**body,'members':body['members']*11},
                    {**body,'members':[{'request_id':target,'binding_hash':'a'*64,'path':'private'},body['members'][1]]}):
        assert request(f,path,'POST',invalid,f.headers)[0]==422
    assert received==[] and calls==[]
    code,_,value=request(f,path,'POST',body,f.headers)
    assert code==200 and value['enabled'] is False and received==[body] and calls==[]
