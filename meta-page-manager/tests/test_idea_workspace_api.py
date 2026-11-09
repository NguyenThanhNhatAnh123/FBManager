"""The idea board only discovers local designs and inspects worker readiness."""
import asyncio
import json
import httpx
import io
import pytest
from uuid import uuid4
from PIL import Image

from conftest import FixtureBox,mark_page_information_updated
from page_manager.app import create_app
from page_manager.errors import AppError
from page_manager.idea_workspace import IdeaWorkspace, video_readiness
from page_manager.idea_runs import IdeaRuns
from page_manager.inputs import ConnectionInput
from page_manager.video_source import VideoSourceSettings
from test_http_security import session


def request(app,path,method='GET',body=None,headers=None):
    async def send():
        transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234))
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8010') as client:
            response=await client.request(method,path,json=body,headers=headers)
            return response.status_code,dict(response.headers),response.json() if response.headers.get('content-type','').startswith('application/json') else response.content
    return asyncio.run(send())


class Bridge:
    def inspect(self):
        return {'installed': True, 'available': False, 'configured_blueox': False,
                'active_domain': None, 'authenticated': None, 'source': None,
                'ready': False, 'blockers': ['Tool chưa chạy.']}


class Results:
    bridge=object()
    ffmpeg_path='fixture-decoder-not-executed'
    def __init__(self,*_):pass
    async def accept(self,*_):raise AssertionError('No worker callback during HTTP tests')


class ApprovedAudioFixture:
    """A reviewed capability fixture; no real audio is generated or approved."""
    ready=True
    def snapshot(self):
        return {'source_directory':'fixture-audio','render_signature':'c'*64,'ready':self.ready,
            'source_count':1,'approved_count':int(self.ready),'blockers':[],'items':[]}
    def approved_mixes(self):
        if not self.ready:raise AppError('Âm thanh chưa được chấp nhận.','audio_mixes_not_approved',409)
        return {'a'*64:{'approval_id':'b'*64,'pcm_sha256':'d'*64,'source_sha256':'a'*64,
            'render_signature':'c'*64,'sample_rate':48000,'channels':2,'duration_seconds':8}}


def runs_factory(root,manager,pipeline,**kwargs):
    async def no_process(*_args,**_kwargs):raise AssertionError('HTTP tests must never launch a worker')
    result=IdeaRuns(root,manager,pipeline,launcher=no_process,execution_pipeline='legacy',**kwargs)
    result.readiness=lambda:{'supported':True,'ready':True,'blocker_codes':[],
                             'uploader_binding':{'kind':'local','endpoint':'http://127.0.0.1:8770','shop':'blueox179.com'}}
    return result


def fixture(tmp_path):
    directory = tmp_path / 'ideas'; directory.mkdir()
    app = create_app(tmp_path / 'manager', secret_box=FixtureBox(),
                     idea_pipeline_directory=directory, podsach_bridge_factory=Bridge,
                     idea_runs_factory=runs_factory,idea_results_factory=Results,
                     audio_approvals_factory=lambda *_:ApprovedAudioFixture())
    app.state.manager.graph_factory = lambda: (_ for _ in ()).throw(AssertionError('No Graph in idea discovery'))
    return app, session(app), directory


def test_idea_board_requires_page_session_and_scan_requires_csrf(tmp_path):
    app, headers, _ = fixture(tmp_path)
    assert request(app, '/api/idea-pipeline')[0] == 401
    assert request(app, '/api/idea-pipeline', headers={'cookie': 'cm_session=wrong'})[0] == 401
    code, _, value = request(app, '/api/idea-pipeline', headers=headers)
    assert code == 200 and value['pipeline']['ready'] is False
    assert value['pipeline']['idea_count'] == 0
    assert value['policy']['production_enabled'] is False
    assert value['upload']['authenticated'] is None
    assert value['video_agent']['status'] == 'awaiting'
    assert request(app, '/api/idea-pipeline/scan', 'POST', {}, {'cookie': headers['cookie']})[0] == 403
    assert request(app, '/api/idea-pipeline/views/refresh', 'POST', {}, {'cookie': headers['cookie']})[0] == 403


def test_both_status_routes_project_only_bounded_cli_configuration(tmp_path):
    app, headers, _ = fixture(tmp_path)
    readiness = {'ready': True, 'blocker_codes': [], 'cli_configured': True,
                 'cli_runtime_verified': False, 'cli_configuration_source': 'environment',
                 'cli_path': 'DoNotEchoPrivatePath'}
    app.state.idea_runs.readiness = lambda: readiness
    for path in ('/api/idea-pipeline', '/api/idea-pipeline?summary=true'):
        code, _, value = request(app, path, headers=headers)
        assert code == 200
        assert value['execution']['cli_configured'] is True
        assert value['execution']['cli_runtime_verified'] is False
        assert value['execution']['cli_configuration_source'] == 'environment'
        assert 'DoNotEchoPrivatePath' not in json.dumps(value)
    readiness.update(cli_configured='true', cli_runtime_verified=1,
                     cli_configuration_source='DoNotEchoPrivateSource')
    for path in ('/api/idea-pipeline', '/api/idea-pipeline?summary=true'):
        code, _, value = request(app, path, headers=headers)
        assert code == 200
        assert not any(name.startswith('cli_') for name in value['execution'])
        assert 'DoNotEchoPrivateSource' not in json.dumps(value)


def test_both_status_routes_use_configured_producer_directory(tmp_path, monkeypatch):
    from page_manager import idea_workspace
    app, headers, _ = fixture(tmp_path)
    directory = tmp_path / 'custom-producer'
    app.state.idea_pipeline.output_directory = directory
    observed = []

    def inspect(root, *, workflow_directory):
        observed.append(workflow_directory)
        return {'status': 'awaiting', 'ready': False, 'workflow_ready': False}

    monkeypatch.setattr(idea_workspace, 'video_readiness', inspect)
    for path in ('/api/idea-pipeline', '/api/idea-pipeline?summary=true'):
        assert request(app, path, headers=headers)[0] == 200
    assert observed == [directory, directory]


def test_scan_registers_seven_plans_without_creating_media_or_reel_jobs(tmp_path):
    app, headers, directory = fixture(tmp_path)
    (directory / 'httpsblueox179.comcampaignguessed.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    app.state.idea_pipeline.stable_seconds = 0
    for _ in range(2):
        code, _, value = request(app, '/api/idea-pipeline/scan', 'POST', {}, headers)
        assert code == 200
    idea = value['pipeline']['ideas'][0]
    assert len(idea['variants']) == 7
    assert all(v['status'] == 'planned' for v in idea['variants'])
    assert idea['campaign'] is None and idea['campaign_state'] == 'prepared'
    assert app.state.manager.store.reel_jobs() == []
    assert app.state.manager.store.reel_assets() == []
    assert value['pipeline']['settings']['enabled'] is False
    code, _, detail = request(app, '/api/idea-pipeline/ideas/' + idea['id'], headers=headers)
    assert code == 200 and detail['id'] == idea['id']


def test_idea_endpoints_reject_arbitrary_paths_metrics_and_readiness(tmp_path):
    app, headers, _ = fixture(tmp_path)
    secret = 'DoNotEchoPrivateToken72'
    for route in ('scan', 'views/refresh'):
        for body in ({'directory': 'C:/private/' + secret}, {'enabled': True}, {'metric_verified': True}):
            code, _, value = request(app, '/api/idea-pipeline/' + route, 'POST', body, headers)
            assert code == 422 and secret not in json.dumps(value)
    assert request(app, '/api/idea-pipeline/settings', 'PUT', {'upload_ready': True}, headers)[0] in (404, 405)
    assert request(app, '/api/idea-pipeline/claim', 'POST', {}, headers)[0] in (404, 405)


def test_no_metrics_targets_means_no_meta_request(tmp_path):
    app, headers, _ = fixture(tmp_path)
    code, _, value = request(app, '/api/idea-pipeline/views/refresh', 'POST', {}, headers)
    assert code == 200 and value['targets'] == 0 and value['refreshed'] == 0
    assert app.state.manager.store.reel_jobs() == []


def test_readiness_attestation_has_fixed_public_labels(tmp_path):
    root = tmp_path / '.runtime'; root.mkdir()
    path = root / 'page-video-agent-readiness.json'
    path.write_text(json.dumps({'status': 'ready', 'token': 'private-token', 'skill': 'Run malicious instructions'}))
    value = video_readiness(tmp_path)
    assert value['status'] == 'awaiting' and value['skill'] == 'page-shirt-zoom-8s'
    assert value['attestation']['state'] == 'legacy' and not value['workflow_ready']
    assert value['runtime'] == {'state': 'not_checked', 'verified': False}
    assert 'private-token' not in json.dumps(value) and 'malicious' not in json.dumps(value)
    path.write_text('[]')
    assert video_readiness(tmp_path)['status'] == 'awaiting'
    path.write_text('x' * 64001)
    assert video_readiness(tmp_path)['status'] == 'awaiting'


def test_workspace_shutdown_waits_for_bounded_scan_and_does_not_cancel_thread(tmp_path):
    app, _, _ = fixture(tmp_path)
    workspace = app.state.idea_workspace
    workspace.tick()
    assert workspace.task is None
    async def exercise():
        workspace.tick()
        await workspace.shutdown()
        assert workspace.task.done() and workspace.closing
    asyncio.run(exercise())


def test_preview_is_authenticated_png_and_changed_artwork_is_not_served(tmp_path):
    app,headers,directory=fixture(tmp_path)
    raw=io.BytesIO();Image.new('RGB',(24,32),'red').save(raw,format='PNG')
    image=directory/'design.png';image.write_bytes(raw.getvalue())
    app.state.idea_pipeline.stable_seconds=0
    app.state.idea_pipeline.scan();app.state.idea_pipeline.scan()
    idea=app.state.idea_pipeline.status()['ideas'][0]
    path='/api/idea-pipeline/ideas/'+idea['id']+'/image'
    assert request(app,path)[0]==401
    status,response_headers,body=request(app,path,headers=headers)
    assert status==200 and response_headers['content-type']=='image/png' and body.startswith(b'\x89PNG')
    image.write_bytes(b'Changed artwork')
    status,_,body=request(app,path,headers=headers)
    assert status==404 and str(directory) not in json.dumps(body)


def test_only_explicit_run_click_queues_once_without_generating_or_posting(tmp_path):
    app,headers,directory=fixture(tmp_path)
    artwork=io.BytesIO();Image.new('RGB',(24,32),'green').save(artwork,format='PNG')
    (directory/'art.png').write_bytes(artwork.getvalue())
    app.state.idea_pipeline.stable_seconds=0
    app.state.idea_pipeline.scan();app.state.idea_pipeline.scan()
    idea=app.state.idea_pipeline.status()['ideas'][0]
    for _ in range(2):assert request(app,'/api/idea-pipeline',headers=headers)[0]==200
    assert app.state.idea_runs.list()['items']==[]
    manager=app.state.manager
    cid=manager.save_connection(ConnectionInput(label='Fixture',token_type='user',token='FixtureNoLiveRequests82'))['id']
    ids=[str(555+i) for i in range(14)]
    manager.store.sync(cid,1,[{'id':pid,'name':'Fixture Page','sources':['accounts']} for pid in ids],True,'working',None,{})
    for pid in ids:mark_page_information_updated(manager.store,pid,cid)
    app.state.video_source.configure(VideoSourceSettings(enabled=True,page_ids=ids,connection_id=cid))
    path='/api/idea-pipeline/ideas/'+idea['id']+'/run';key=str(uuid4())
    assert request(app,path,'POST',{'request_id':key},{'cookie':headers['cookie']})[0]==403
    assert request(app,path,'POST',{'request_id':key,'caption':'override'},headers)[0]==422
    code,_,first=request(app,path,'POST',{'request_id':key},headers)
    assert code==200 and first['accepted'] and first['run']['status']=='queued'
    assert first['run']['variant_count']==7
    assert app.state.idea_pipeline.status()['settings']['enabled'] is False
    (directory/'art.png').unlink()
    code,_,again=request(app,path,'POST',{'request_id':key},headers)
    assert code==200 and again['run']['id']==first['run']['id']
    (directory/'art.png').write_bytes(artwork.getvalue())
    assert request(app,path,'POST',{'request_id':str(uuid4())},headers)[0]==409
    assert len(app.state.idea_runs.list()['items'])==1 and manager.store.reel_jobs()==[]


def manual_run_fixture(tmp_path):
    app,headers,directory=fixture(tmp_path)
    artwork=io.BytesIO();Image.new('RGB',(24,32),'green').save(artwork,format='PNG')
    path=directory/'pilot.png';path.write_bytes(artwork.getvalue())
    pipeline=app.state.idea_pipeline;pipeline.stable_seconds=0;pipeline.scan();pipeline.scan()
    idea=pipeline.status()['ideas'][0]
    manager=app.state.manager
    cid=manager.save_connection(ConnectionInput(label='Fixture',token_type='user',token='FixtureNoLiveRequests82'))['id']
    ids=[str(555+i) for i in range(14)]
    manager.store.sync(cid,1,[{'id':pid,'name':'Fixture Page','sources':['accounts']} for pid in ids],True,'working',None,{})
    for pid in ids:mark_page_information_updated(manager.store,pid,cid)
    app.state.video_source.configure(VideoSourceSettings(enabled=True,page_ids=ids,connection_id=cid))
    return app,headers,path,idea,'/api/idea-pipeline/ideas/'+idea['id']+'/run'


@pytest.mark.parametrize('count',[1,14])
def test_manual_count_uses_exact_canonical_prefix_without_launching_worker(tmp_path,count):
    app,headers,_,idea,path=manual_run_fixture(tmp_path)
    code,_,value=request(app,path,'POST',{'request_id':str(uuid4()),'variant_count':count},headers)
    assert code==200 and value['run']['variant_count']==count
    assert value['run']['trigger_mode']=='manual_web_run_idea' and value['run']['automatic_batch'] is None
    current=app.state.idea_pipeline.idea(idea['id'])['variants']
    assert len(current)==count and current[:min(count,7)]==idea['variants'][:count]
    with app.state.manager.store.connect() as db:
        canonical=db.execute('SELECT id,reservation_id FROM idea_pipeline_variants WHERE idea_id=? ORDER BY ordinal',(idea['id'],)).fetchall()
        assert len(canonical)==max(7,count)
        assert [tuple(row) for row in canonical[:7]]==[(item['id'],item['reservation_id']) for item in idea['variants']]
        payload=json.loads(db.execute('SELECT payload FROM idea_manual_runs WHERE id=?',(value['run']['id'],)).fetchone()[0])
    assert payload['variant_count']==len(payload['variants'])==count
    assert [item['variant_id'] for item in payload['variants']]==[item['id'] for item in current[:count]]
    assert app.state.idea_runs._task is None and not app.state.manager.store.reel_jobs()
    assert not app.state.idea_automation.planner.settings()['enabled']


@pytest.mark.parametrize('count',[0,15,-1,True,False,'14',14.0,None,{},[]])
def test_manual_count_rejects_coercion_and_out_of_bounds_before_reserving(tmp_path,count):
    app,headers,_,idea,path=manual_run_fixture(tmp_path)
    code,_,value=request(app,path,'POST',{'request_id':str(uuid4()),'variant_count':count},headers)
    assert code==422 and value['code']=='invalid_idea_run'
    assert not app.state.idea_runs.list()['items']
    assert app.state.idea_pipeline.idea(idea['id'])['variants']==idea['variants']


def test_manual_count_replay_is_bound_before_current_consumer_or_source_checks(tmp_path):
    app,headers,artwork,_,path=manual_run_fixture(tmp_path);key=str(uuid4())
    body={'request_id':key,'variant_count':14}
    code,_,first=request(app,path,'POST',body,headers);assert code==200
    artwork.unlink();app.state.idea_workspace.consumer_ready=False
    app.state.audio_approvals.ready=False
    app.state.idea_runs.readiness=lambda:(_ for _ in ()).throw(AssertionError('Replay must not probe readiness'))
    with app.state.manager.store.connect() as db:db.execute('UPDATE video_source_settings SET configured=0')
    code,_,again=request(app,path,'POST',body,headers)
    assert code==200 and again['run']['id']==first['run']['id']
    for changed in ({'request_id':key},{'request_id':key,'variant_count':7}):
        code,_,value=request(app,path,'POST',changed,headers)
        assert code==409 and value['code']=='idea_run_conflict'
    assert len(app.state.idea_runs.list()['items'])==1


def test_explicit_count_cannot_start_without_approved_audio(tmp_path):
    app,headers,_,_,path=manual_run_fixture(tmp_path)
    app.state.audio_approvals.ready=False
    code,_,value=request(app,path,'POST',{'request_id':str(uuid4()),'variant_count':14},headers)
    assert code==409 and value['code']=='audio_mixes_not_approved'
    assert not app.state.idea_runs.list()['items'] and app.state.idea_runs._task is None


@pytest.mark.parametrize('gate',['consumer','readiness','schedule','artwork'])
def test_explicit_count_keeps_all_existing_preflight_gates(tmp_path,gate):
    app,headers,artwork,idea,path=manual_run_fixture(tmp_path)
    if gate=='consumer':app.state.idea_workspace.consumer_ready=False
    elif gate=='readiness':app.state.idea_runs.readiness=lambda:{'ready':False}
    elif gate=='schedule':
        with app.state.manager.store.connect() as db:db.execute('UPDATE video_source_settings SET configured=0')
    else:artwork.unlink()
    code,_,_=request(app,path,'POST',{'request_id':str(uuid4()),'variant_count':14},headers)
    assert code==(404 if gate=='artwork' else 409)
    assert not app.state.idea_runs.list()['items'] and app.state.idea_runs._task is None
    assert app.state.idea_pipeline.idea(idea['id'])['variants']==idea['variants']


@pytest.mark.parametrize('extra',[{'trigger_mode':'automatic_idea_batch'},{'automatic_batch':{}},
    {'audio_assignments':[]},{'workflow_ready':True},{'continuity':True},{'allow_continuity':True},
    {'workflow_policy':'automatic_reviewed_hash'},{'caption':'DoNotEchoPrivateValue'}])
def test_manual_count_cannot_supply_automatic_context_or_qa_overrides(tmp_path,extra):
    app,headers,_,_,path=manual_run_fixture(tmp_path)
    code,_,value=request(app,path,'POST',{'request_id':str(uuid4()),'variant_count':14,**extra},headers)
    assert code==422 and value['code']=='invalid_idea_run'
    assert 'DoNotEchoPrivateValue' not in json.dumps(value) and not app.state.idea_runs.list()['items']


@pytest.mark.parametrize('code,message',[
    ('uploader_workspace_busy','Tool upload đang có input'),
    ('uploader_workspace_unknown','Chưa đọc được trạng thái vùng làm việc')])
def test_workspace_gate_has_clear_manual_execution_message(tmp_path,code,message):
    app,headers,_=fixture(tmp_path)
    app.state.idea_runs.readiness=lambda:{'ready':False,'blocker_codes':[code]}
    status,_,value=request(app,'/api/idea-pipeline?summary=true',headers=headers)
    assert status==200 and any(message in text for text in value['execution']['blockers'])


def test_web_image_upload_preserves_artwork_and_never_starts_production(tmp_path):
    app,headers,directory=fixture(tmp_path)
    raw=io.BytesIO();Image.new('RGBA',(24,32),(80,20,10,150)).save(raw,format='PNG');artwork=raw.getvalue()
    async def upload():
        transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234))
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8010') as client:
            h={**headers,'X-Filename':'design.png','Content-Type':'application/octet-stream'}
            denied=await client.post('/api/idea-pipeline/images',content=artwork,headers={'cookie':headers['cookie'],'X-Filename':'design.png'})
            assert denied.status_code==403
            first=await client.post('/api/idea-pipeline/images',content=artwork,headers=h)
            second=await client.post('/api/idea-pipeline/images',content=artwork,headers=h)
            assert first.status_code==second.status_code==200
            assert first.json()['idea']['id']==second.json()['idea']['id'] and second.json()['duplicate'] is True
            assert first.json()['duplicate'] is False and len(first.json()['idea']['variants'])==7
            bad=await client.post('/api/idea-pipeline/images',content=artwork,headers={**h,'X-Filename':'..%2Fprivate.png'})
            assert bad.status_code==422
            large=await client.post('/api/idea-pipeline/images',content=b'',headers={**h,'Content-Length':str(25*1024*1024+1)})
            assert large.status_code==413
    asyncio.run(upload())
    assert (directory/'design.png').read_bytes()==artwork
    assert app.state.idea_runs.list()['items']==[] and app.state.manager.store.reel_jobs()==[]
