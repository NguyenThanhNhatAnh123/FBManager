"""Versioned Idea reads stay bounded and do not inspect unrelated producer state."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from conftest import FixtureBox
from page_manager.app import BODY_LIMIT,IdentityStore,create_app
from test_idea_catalog_paging import no_full_reads,seed
from test_idea_workspace_api import fixture,request


def forbidden(*_args,**_kwargs):
    raise AssertionError('This view must not hydrate unrelated state')


def test_versioned_idea_default_is_bounded_and_advertises_available_resources(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);seed(app,41);no_full_reads(app,monkeypatch)
    code,_,session=request(app,'/api/session')
    assert code==200 and {'idea-pipeline','telegram-approval'}<=set(session['app']['rest_api_resources'])
    assert session['app']['rest_api_features']==['idea_views']
    code,_,value=request(app,'/api/v1/idea-pipeline',headers=headers)
    assert code==200 and value['pipeline']['pagination']=={'total':41,'limit':25,'offset':0}
    assert len(value['pipeline']['ideas'])==25 and len(value['runs']['items'])==25
    assert {'producers','automation','telegram','audio_mixes','project_assets'}<=set(value)
    code,_,schema=request(app,'/api/v1/openapi.json',headers=headers)
    assert code==200
    for path in ('idea-pipeline','idea-pipeline/producers/check','idea-pipeline/images',
                 'idea-pipeline/ideas/{idea_id}/iterations','telegram-approval','telegram-approval/cohorts'):
        assert '/api/v1/'+path in schema['paths']
    assert '/api/v1/session' not in schema['paths']


def test_ideas_view_reads_visible_allocations_without_global_batch_or_supplements(tmp_path,monkeypatch):
    from page_manager import app as app_module
    app,headers,_=fixture(tmp_path);ids=seed(app,41);no_full_reads(app,monkeypatch)
    automation=app.state.idea_automation;batch_id=str(uuid4())
    with app.state.manager.store.connect() as db:
        db.execute('INSERT INTO idea_auto_batches VALUES(?,?,?,?,?,?,?,?,?)',
            (batch_id,'2030-11-01',1,1,'{"private":"NO_BATCH_SNAPSHOT"}','a'*64,'pending','2030-11-01','2030-11-01'))
        for position,iid in enumerate(ids):
            db.execute('''INSERT INTO idea_auto_batch_items(batch_id,idea_id,artwork_sha256,variant_count,
                position,request_id,status,updated_at) VALUES(?,?,?,?,?,?,?,?)''',
                (batch_id,iid,'a'*64,2,position+1,str(uuid4()),'pending','2030-11-01'))
    for obj,name in ((automation,'status'),(automation.planner,'preview'),(automation.planner,'_batch'),
                     (app.state.producer_diagnostics,'snapshot'),(app.state.telegram_approval,'snapshot'),
                     (app.state.audio_approvals,'snapshot')):
        monkeypatch.setattr(obj,name,forbidden)
    monkeypatch.setattr(app_module,'project_assets',forbidden)
    code,_,value=request(app,'/api/v1/idea-pipeline?view=ideas&limit=3&offset=25',headers=headers)
    assert code==200 and [item['id'] for item in value['pipeline']['ideas']]==ids[25:28]
    assert value['automation']['active_batch']=={'id':batch_id,'status':'pending','items':[
        {'idea_id':iid,'variant_count':2} for iid in ids[25:28]]}
    assert set(value['automation'])=={'settings','active_batch'}
    assert not {'producers','telegram','audio_mixes','project_assets'}&set(value)
    assert value['ranking']=={'mode':'preview_only','cohorts':[]}
    assert 'NO_BATCH_SNAPSHOT' not in json.dumps(value) and 'MUST_NOT_BE_SERIALIZED' not in json.dumps(value)


def test_connections_view_reads_only_requested_supplements_and_no_idea_window(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);seed(app,41);no_full_reads(app,monkeypatch)
    for obj,name in ((app.state.idea_pipeline,'catalog'),(app.state.idea_pipeline,'ranking_page'),
                     (app.state.idea_runs,'for_ideas'),(app.state.idea_automation,'status'),
                     (app.state.idea_automation.planner,'visible_allocation'),
                     (app.state.producer_diagnostics,'snapshot'),(app.state.audio_approvals,'snapshot')):
        monkeypatch.setattr(obj,name,forbidden)
    seen=[]
    monkeypatch.setattr(app.state.telegram_approval,'snapshot',lambda:seen.append('telegram') or {'pending':{'items':[]}})
    code,_,value=request(app,'/api/v1/idea-pipeline?view=connections&include=telegram&limit=10&ranking_limit=10',headers=headers)
    assert code==200 and value['pipeline']['idea_count']==41 and seen==['telegram']
    assert not {'ideas','jobs'}&set(value['pipeline'])
    assert not {'runs','ranking','performance','automation','audio_mixes','project_assets','producers'}&set(value)
    assert value['telegram']=={'pending':{'items':[]}} and value['policy']['production_enabled'] is False


def test_versioned_scan_response_is_bounded_without_a_global_ledger_read(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);seed(app,41);no_full_reads(app,monkeypatch)
    code,_,value=request(app,'/api/v1/idea-pipeline/scan','POST',{},headers)
    assert code==200 and value['pipeline']['pagination']=={'total':41,'limit':25,'offset':0}
    assert len(value['pipeline']['ideas'])==25 and len(value['runs']['items'])==25


@pytest.mark.parametrize('query',['view=unknown','view=ideas&include=unknown','view=ideas&limit=101','view=ideas&offset=-1'])
def test_invalid_views_are_rejected_before_loading_state(tmp_path,monkeypatch,query):
    app,headers,_=fixture(tmp_path)
    monkeypatch.setattr(app.state.idea_workspace,'page_status',forbidden)
    assert request(app,'/api/v1/idea-pipeline?'+query,headers=headers)[0]==422


def test_versioned_idea_and_ranking_offsets_are_exact_legacy_still_clamps(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);seed(app,9);no_full_reads(app,monkeypatch)
    for offset in (20,10**30):
        code,_,value=request(app,f'/api/v1/idea-pipeline?view=ideas&limit=3&offset={offset}&ranking_limit=3&ranking_offset={offset}',headers=headers)
        assert code==200 and value['pipeline']['pagination']=={'total':9,'limit':3,'offset':offset}
        assert value['pipeline']['ideas']==[] and value['runs']['items']==[]
        assert value['ranking']['offset']==offset and value['ranking']['cohorts']==[]
    code,_,value=request(app,'/api/idea-pipeline?summary=true&limit=3&offset=20&ranking_limit=3&ranking_offset=20',headers=headers)
    assert code==200 and value['pipeline']['pagination']['offset']==6 and value['ranking']['offset']==6


def test_new_aliases_keep_auth_csrf_body_limits_and_secondary_exclusion(tmp_path):
    app,headers,_=fixture(tmp_path)
    assert request(app,'/api/v1/idea-pipeline?view=ideas')[0]==401
    assert request(app,'/api/v1/telegram-approval')[0]==401
    for path in ('idea-pipeline/scan','telegram-approval/check'):
        assert request(app,'/api/v1/'+path,'POST',{}, {'cookie':headers['cookie']})[0]==403
        assert request(app,'/api/v1/'+path,'POST',{'oversized':'x'*(BODY_LIMIT+1)},headers)[0]==413
    secondary=create_app(tmp_path/'secondary',secret_box=FixtureBox(),secondary_workspace=True,
        workspace_user_id=2,identity_store=IdentityStore(tmp_path/'central-identity'),
        podsach_bridge_factory=forbidden,uploader_connection_factory=forbidden,idea_runs_factory=forbidden,
        producer_diagnostics_factory=forbidden,idea_automation_factory=forbidden)
    from test_http_security import session
    secondary_headers=session(secondary)
    for path in ('/api/idea-pipeline','/api/v1/idea-pipeline','/api/v1/idea-pipeline/producers'):
        assert request(secondary,path,headers=secondary_headers)[0]==404
    assert request(secondary,'/api/v1/telegram-approval',headers=secondary_headers)[0]==200
    _,_,capabilities=request(secondary,'/api/session')
    assert 'idea-pipeline' not in capabilities['app']['rest_api_resources']
    assert capabilities['app']['rest_api_features']==[]


def test_new_resource_aliases_reach_handlers_and_keep_idea_upload_specific_cap(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path)
    code,_,automation=request(app,'/api/v1/idea-pipeline/automation',headers=headers)
    assert code==200
    assert request(app,'/api/v1/idea-pipeline/automation','PUT',
        {'enabled':False,'revision':automation['settings']['revision']},headers)[0]==200
    code,_,telegram=request(app,'/api/v1/telegram-approval',headers=headers)
    assert code==200
    assert request(app,'/api/v1/telegram-approval','PUT',
        {'enabled':False,'revision':telegram['revision']},headers)[0]==200
    captured=[]
    def save(raw,filename):
        captured.append((len(raw),filename));return {'stored':True}
    monkeypatch.setattr(app.state.idea_images,'save',save)
    async def upload():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8010') as client:
            response=await client.post('/api/v1/idea-pipeline/images',content=b'x'*(BODY_LIMIT+1),
                headers={**headers,'x-filename':'fixture.png','content-type':'application/octet-stream'})
            assert response.status_code==200 and response.json()=={'stored':True}
    asyncio.run(upload())
    assert captured==[(BODY_LIMIT+1,'fixture.png')]
