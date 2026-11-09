"""Synthetic session/CSRF/cache API checks; no provider command is launched."""
import asyncio
from copy import deepcopy
from http.cookies import SimpleCookie
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from conftest import FixtureBox
from page_manager.app import create_app
from test_idea_workspace_api import Bridge,Results,runs_factory


class Diagnostics:
    def __init__(self):self.checks=[];self.gets=0;self.stopped=False
    def snapshot(self):
        self.gets+=1
        row={'installed':True,'authenticated':None,'checked_at':None,'expires_at':None,'status':'not_checked',
             'reason':'check_required','version':None,'checking':False,'configuration_source':'default',
             'image_generation':{'configured':None,'flag_supported':None,'verified':False,'reason':'not_checked'},
             'production_verified':False}
        return {'primary':'codex','backup':'claude','providers':{name:deepcopy(row) for name in ('codex','claude')},
                'fallback':{'mode':'manual_review','automatic':False,'reason':'backup_requires_manual_review'}}
    async def check(self,provider):self.checks.append(provider);return self.snapshot()
    async def shutdown(self):self.stopped=True


def request(fixture,path,method='GET',body=None,headers=None):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app,client=('127.0.0.1',1234)),base_url=fixture.origin) as client:
            response=await client.request(method,fixture.prefix+path,json=body,headers=headers)
            return response.status_code,dict(response.headers),response.json()
    return asyncio.run(send())


@pytest.fixture(params=[False,True],ids=['standalone','mounted'])
def fixture(tmp_path,request):
    diagnostics=Diagnostics();ideas=tmp_path/'ideas';ideas.mkdir()
    child=create_app(tmp_path/'manager',port=8000 if request.param else 8010,base_path='/pages' if request.param else '',
        secret_box=FixtureBox(),idea_pipeline_directory=ideas,podsach_bridge_factory=Bridge,
        idea_runs_factory=runs_factory,idea_results_factory=Results,producer_diagnostics_factory=lambda:diagnostics)
    child.state.manager.graph_factory=lambda:(_ for _ in ()).throw(AssertionError('No Meta in producer diagnostics'))
    app=child
    if request.param:app=FastAPI();app.mount('/pages',child)
    value=SimpleNamespace(app=app,child=child,diagnostics=diagnostics,prefix='/pages' if request.param else '',
                          origin='http://127.0.0.1:'+('8000' if request.param else '8010'))
    code,headers,body=globals()['request'](value,'/api/session');assert code==200
    cookie=SimpleCookie();cookie.load(headers['set-cookie']);name='mpm_pages_session' if request.param else 'mpm_session'
    value.headers={'cookie':name+'='+cookie[name].value,'x-mpm-csrf':body['csrf_token']}
    yield value


def test_read_snapshot_and_existing_board_status_never_start_checks(fixture):
    for path in ('/api/idea-pipeline/producers','/api/idea-pipeline','/api/idea-pipeline?summary=true&limit=10'):
        code,_,body=request(fixture,path,headers=fixture.headers)
        assert code==200
        snapshot=body if path.endswith('/producers') else body['producers']
        assert snapshot['providers']['codex']['authenticated'] is None
        assert snapshot['fallback']['automatic'] is False
    assert fixture.diagnostics.checks==[]


def test_check_requires_session_and_csrf_and_has_no_login_or_arbitrary_command_input(fixture):
    root='/api/idea-pipeline/producers'
    assert request(fixture,root)[0]==401
    assert request(fixture,root+'/check','POST',{'provider':'all'})[0]==401
    assert request(fixture,root+'/check','POST',{'provider':'all'},{'cookie':fixture.headers['cookie']})[0]==403
    for body in ({'provider':'other'},{'provider':'codex','command':'PRIVATE'},{'provider':'claude','password':'PRIVATE'}):
        code,_,value=request(fixture,root+'/check','POST',body,fixture.headers)
        assert code==422 and 'PRIVATE' not in str(value)
    assert fixture.diagnostics.checks==[]


@pytest.mark.parametrize('provider',['codex','claude','all'])
def test_explicit_check_selects_only_bounded_provider(fixture,provider):
    code,_,body=request(fixture,'/api/idea-pipeline/producers/check','POST',{'provider':provider},fixture.headers)
    assert code==200 and fixture.diagnostics.checks==[provider]
    assert body['primary']=='codex' and body['backup']=='claude'
    assert all(row['production_verified'] is False for row in body['providers'].values())


def test_app_lifespan_stops_its_diagnostic_service(fixture):
    # Exit immediately without letting the standalone monitor tick.
    async def check():
        async with fixture.child.router.lifespan_context(fixture.child):pass
    asyncio.run(check())
    assert fixture.diagnostics.stopped is True
