"""Opt-in API integration with an inert controller; no worker or remote IO."""
import asyncio
from http.cookies import SimpleCookie
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from conftest import FixtureBox
from page_manager.app import create_app
from page_manager.errors import AppError
from test_idea_workspace_api import Bridge,Results,runs_factory
from test_producer_diagnostics_api import Diagnostics,request as send


class Automation:
    def __init__(self,*dependencies):
        assert len(dependencies)==5
        self.enabled=False;self.revision=1;self.updates=[];self.ticks=0;self.stopped=False
    def status(self):
        return {'settings':{'enabled':self.enabled,'revision':self.revision,'timezone':'America/Los_Angeles',
                    'daily_time':'06:00','max_batches_per_day':1},
                'preview':{'candidate_count':7,'capacity':14,'total_videos':14,'allocations':[
                    {'idea_id':str(index),'variant_count':2,'position':index+1} for index in range(7)],
                    'deferred_count':0,'blocker_codes':[]},'active_batch':None,'blockers':[],
                'state':'idle' if self.enabled else 'disabled','checked_at':None,'coalesce_seconds':60}
    def configure(self,enabled,expected_revision):
        if self.revision!=expected_revision:raise AppError('Đọc lại chế độ trước khi lưu.','auto_settings_changed',409)
        self.updates.append((enabled,expected_revision))
        if enabled!=self.enabled:self.revision+=1
        self.enabled=enabled
        return self.status()
    def tick(self):self.ticks+=1
    async def shutdown(self):self.stopped=True


@pytest.fixture(params=[False,True],ids=['standalone','mounted'])
def fixture(tmp_path,request):
    diagnostics=Diagnostics();ideas=tmp_path/'ideas';ideas.mkdir()
    child=create_app(tmp_path/'manager',port=8000 if request.param else 8010,base_path='/pages' if request.param else '',
        secret_box=FixtureBox(),idea_pipeline_directory=ideas,podsach_bridge_factory=Bridge,
        idea_runs_factory=runs_factory,idea_results_factory=Results,producer_diagnostics_factory=lambda:diagnostics,
        idea_automation_factory=Automation)
    child.state.manager.graph_factory=lambda:(_ for _ in ()).throw(AssertionError('No Meta in automation API fixtures'))
    app=child
    if request.param:app=FastAPI();app.mount('/pages',child)
    value=SimpleNamespace(app=app,child=child,automation=child.state.idea_automation,diagnostics=diagnostics,
        prefix='/pages' if request.param else '',origin='http://127.0.0.1:'+('8000' if request.param else '8010'))
    code,headers,body=send(value,'/api/session');assert code==200
    cookie=SimpleCookie();cookie.load(headers['set-cookie']);name='mpm_pages_session' if request.param else 'mpm_session'
    value.headers={'cookie':name+'='+cookie[name].value,'x-mpm-csrf':body['csrf_token']}
    return value


def test_gets_only_project_disabled_default_and_dynamic_preview(fixture):
    for path in ('/api/idea-pipeline/automation','/api/idea-pipeline','/api/idea-pipeline?summary=true&limit=10'):
        code,_,value=send(fixture,path,headers=fixture.headers)
        assert code==200
        status=value if path.endswith('/automation') else value['automation']
        if not path.endswith('/automation'):assert value['policy']['production_enabled'] is False
        assert status['settings']['enabled'] is False and status['settings']['revision']==1
        assert status['preview']['total_videos']==status['preview']['capacity']==14
        assert len(status['preview']['allocations'])==7
        assert all(item['variant_count']==2 for item in status['preview']['allocations'])
    assert fixture.automation.ticks==0 and fixture.automation.updates==[] and fixture.diagnostics.checks==[]


def test_put_is_explicit_csrf_protected_and_revision_checked(fixture):
    path='/api/idea-pipeline/automation';body={'enabled':True,'revision':1}
    assert send(fixture,path,'PUT',body)[0]==401
    assert send(fixture,path,'PUT',body,{'cookie':fixture.headers['cookie']})[0]==403
    code,_,value=send(fixture,path,'PUT',body,fixture.headers)
    assert code==200 and value['settings']['enabled'] is True and value['settings']['revision']==2
    assert fixture.automation.updates==[(True,1)] and fixture.automation.ticks==0
    for board_path in ('/api/idea-pipeline','/api/idea-pipeline?summary=true&limit=10'):
        code,_,value=send(fixture,board_path,headers=fixture.headers)
        assert code==200 and value['policy']['production_enabled'] is True
        assert value['policy']['variants_per_idea'] is None and value['policy']['trigger']=='authorized_run'
    code,_,value=send(fixture,path,'PUT',{'enabled':False,'revision':1},fixture.headers)
    assert code==409 and value['code']=='auto_settings_changed'
    assert fixture.automation.enabled is True
    assert send(fixture,path,'PUT',{'enabled':False,'revision':2},fixture.headers)[0]==200
    assert fixture.automation.enabled is False and fixture.automation.revision==3
    assert fixture.diagnostics.checks==[]


@pytest.mark.parametrize('body',[
    {'enabled':'true','revision':1},{'enabled':1,'revision':1},{'enabled':True,'revision':True},
    {'enabled':True,'revision':0},{'enabled':True,'revision':'1'},{'enabled':True},
    {'enabled':True,'revision':1,'command':'PRIVATE'},
])
def test_automation_settings_do_not_coerce_boolean_revision_or_accept_commands(fixture,body):
    code,_,value=send(fixture,'/api/idea-pipeline/automation','PUT',body,fixture.headers)
    assert code==422 and 'PRIVATE' not in str(value)
    assert fixture.automation.updates==[] and fixture.automation.enabled is False


def test_standalone_timer_and_shutdown_delegate_to_controller(fixture):
    async def scenario():
        async with fixture.child.router.lifespan_context(fixture.child):await asyncio.sleep(0)
    asyncio.run(scenario())
    assert fixture.automation.stopped is True and fixture.diagnostics.stopped is True
    assert fixture.automation.ticks==(0 if fixture.prefix else 1)
