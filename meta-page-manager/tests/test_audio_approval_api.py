"""Audio approval HTTP boundaries using a prepared fixture and an inert service."""
import asyncio
from copy import deepcopy
from http.cookies import SimpleCookie
from types import SimpleNamespace
import wave

from fastapi import FastAPI
import httpx
import pytest

from conftest import FixtureBox
from page_manager.app import create_app
from page_manager.errors import AppError
from test_idea_automation_api import Automation
from test_idea_workspace_api import Bridge,Results,runs_factory
from test_producer_diagnostics_api import Diagnostics


class Audio:
    def __init__(self,path):
        self.path=path;self.gets=0;self.approvals=[];self.previews=[]
        self.value={'source_directory':'Downloads/FILE AM THANH','render_signature':'c'*64,'ready':False,
            'source_count':6,'approved_count':0,'blockers':[],
            'items':[{'id':format(index+1,'064x'),'source_filename':f'fixture-{index}.mp3',
                'source_sha256':'a'*64,'pcm_sha256':'b'*64,'duration_seconds':8,'sample_rate':16000,'channels':1,
                'status':'pending','prepared':True,'approved_at':None,
                'preview_url':'/api/idea-pipeline/audio-mixes/'+format(index+1,'064x')+'/audio'} for index in range(6)]}
    def snapshot(self):self.gets+=1;return deepcopy(self.value)
    def prepare(self):raise AssertionError('GET and approve must not render audio')
    def approve(self,ids,expected_signature):
        self.approvals.append((ids,expected_signature))
        if expected_signature!=self.value['render_signature']:raise AppError('Bản mix đã thay đổi.','audio_mix_changed',409)
        if any(key not in {row['id'] for row in self.value['items']} for key in ids):raise AppError('Không có bản mix này.','audio_mix_not_found',404)
        for row in self.value['items']:
            if row['id'] in ids:row.update(status='approved',approved_at='2030-01-01T12:00:00+00:00')
        self.value['approved_count']=sum(row['status']=='approved' for row in self.value['items'])
        self.value['ready']=self.value['approved_count']==self.value['source_count']
        return self.snapshot()
    def preview_path(self,key):
        self.previews.append(key)
        if key not in {row['id'] for row in self.value['items']}:raise AppError('Không có bản mix này.','audio_mix_not_found',404)
        return self.path


def send(f,path,method='GET',body=None,headers=None):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=f.app,client=('127.0.0.1',1234)),base_url=f.origin) as client:
            result=await client.request(method,f.prefix+path,json=body,headers=headers)
            return result.status_code,dict(result.headers),result.json() if result.headers.get('content-type','').startswith('application/json') else result.content
    return asyncio.run(scenario())


@pytest.fixture(params=[False,True],ids=['standalone','mounted'])
def fixture(tmp_path,request):
    path=tmp_path/'prepared.wav'
    with wave.open(str(path),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b'\0'*256000)
    audio=Audio(path);ideas=tmp_path/'ideas';ideas.mkdir()
    child=create_app(tmp_path/'manager',port=8000 if request.param else 8010,base_path='/pages' if request.param else '',
        secret_box=FixtureBox(),idea_pipeline_directory=ideas,podsach_bridge_factory=Bridge,
        idea_runs_factory=runs_factory,idea_results_factory=Results,producer_diagnostics_factory=Diagnostics,
        idea_automation_factory=Automation,audio_approvals_factory=lambda store,root:audio)
    child.state.manager.graph_factory=lambda:(_ for _ in ()).throw(AssertionError('No Meta in audio approval fixture'))
    app=child
    if request.param:app=FastAPI();app.mount('/pages',child)
    value=SimpleNamespace(app=app,child=child,audio=audio,prefix='/pages' if request.param else '',
        origin='http://127.0.0.1:'+('8000' if request.param else '8010'))
    code,headers,body=send(value,'/api/session');assert code==200
    cookie=SimpleCookie();cookie.load(headers['set-cookie']);name='mpm_pages_session' if request.param else 'mpm_session'
    value.headers={'cookie':name+'='+cookie[name].value,'x-mpm-csrf':body['csrf_token']}
    return value


def test_audio_snapshot_and_board_only_read_prepared_metadata(fixture):
    f=fixture
    for path in ('/api/idea-pipeline/audio-mixes','/api/idea-pipeline','/api/idea-pipeline?summary=true&limit=10'):
        code,_,value=send(f,path,headers=f.headers)
        assert code==200
        assert (value if path.endswith('/audio-mixes') else value['audio_mixes'])==f.audio.value
    assert f.audio.gets==3 and not f.audio.approvals and not f.audio.previews
    assert f.child.state.idea_runs.audio_approvals is f.audio
    assert f.child.state.idea_results.audio_approvals is f.audio
    assert not f.child.state.idea_automation.enabled and not f.child.state.idea_runs.list()['items']


def test_explicit_partial_approval_requires_session_and_csrf(fixture):
    f=fixture;path='/api/idea-pipeline/audio-mixes/approve'
    body={'mix_ids':[f.audio.value['items'][0]['id']],'render_signature':'c'*64}
    assert send(f,path,'POST',body)[0]==401
    assert send(f,path,'POST',body,{'cookie':f.headers['cookie']})[0]==403
    code,_,value=send(f,path,'POST',body,f.headers)
    assert code==200 and value['approved_count']==1 and value['ready'] is False
    assert value['items'][0]['status']=='approved' and all(row['status']=='pending' for row in value['items'][1:])
    assert f.audio.approvals==[(body['mix_ids'],'c'*64)]
    assert not f.child.state.idea_automation.enabled and not f.child.state.idea_runs.list()['items']


@pytest.mark.parametrize('body',[
    {'mix_ids':[],'render_signature':'c'*64},{'mix_ids':['1'*64]*2,'render_signature':'c'*64},
    {'mix_ids':[format(i,'064x') for i in range(101)],'render_signature':'c'*64},
    {'mix_ids':[1],'render_signature':'c'*64},{'mix_ids':['../private'],'render_signature':'c'*64},
    {'mix_ids':['1'*64],'render_signature':'invalid'}, {'mix_ids':['1'*64]},
    {'mix_ids':['1'*64],'render_signature':'c'*64,'approved':True},
    {'mix_ids':['1'*64],'render_signature':'c'*64,'path':'PRIVATE_INPUT'},
])
def test_approval_body_is_strict_unique_and_bounded(fixture,body):
    code,_,value=send(fixture,'/api/idea-pipeline/audio-mixes/approve','POST',body,fixture.headers)
    assert code==422 and 'PRIVATE_INPUT' not in str(value)
    assert not fixture.audio.approvals


def test_stale_signature_is_not_reinterpreted_as_approval(fixture):
    body={'mix_ids':[fixture.audio.value['items'][0]['id']],'render_signature':'d'*64}
    code,_,value=send(fixture,'/api/idea-pipeline/audio-mixes/approve','POST',body,fixture.headers)
    assert code==409 and value['code']=='audio_mix_changed'
    assert fixture.audio.value['approved_count']==0


def test_preview_is_authenticated_wav_and_supports_bounded_range(fixture):
    f=fixture;key=f.audio.value['items'][0]['id'];path='/api/idea-pipeline/audio-mixes/'+key+'/audio'
    assert send(f,path)[0]==401
    code,headers,body=send(f,path,headers=f.headers)
    assert code==200 and headers['content-type']=='audio/wav' and body==f.audio.path.read_bytes()
    assert 'inline' in headers['content-disposition'] and str(f.audio.path) not in str(headers)
    code,headers,part=send(f,path,headers={**f.headers,'Range':'bytes=0-15'})
    assert code==206 and part==body[:16] and headers['content-range'].startswith('bytes 0-15/')
    before=list(f.audio.previews)
    assert send(f,'/api/idea-pipeline/audio-mixes/not-a-path/audio',headers=f.headers)[0]==422
    assert f.audio.previews==before
    assert send(f,'/api/idea-pipeline/audio-mixes/'+'f'*64+'/audio',headers=f.headers)[0]==404
    assert not f.audio.approvals
