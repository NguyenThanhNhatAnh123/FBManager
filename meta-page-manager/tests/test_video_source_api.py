"""Folder settings remain inside the existing Page session and Reel queue."""
import json

from conftest import FixtureBox,mark_page_information_updated
from page_manager.app import create_app
from page_manager.inputs import ConnectionInput
from test_http_security import request,session


TOKEN='FixtureFolderApiToken82'


def fixture(tmp_path,*,information_updated=True):
    directory=tmp_path/'videos';directory.mkdir()
    app=create_app(tmp_path/'manager',secret_box=FixtureBox(),video_source_directory=directory)
    manager=app.state.manager
    connection=manager.save_connection(ConnectionInput(label='Fixture source',token_type='system_user',token=TOKEN))
    manager.store.sync(connection['id'],manager.store.connection(connection['id'])['token_revision'],[
        {'id':pid,'name':'Fixture Page '+pid,'sources':['assigned_pages'],'tasks':None}
        for pid in ('555','777')
    ],True,'working',None,{'id':'88','name':'Fixture user'})
    if information_updated:
        for pid in ('555','777'):mark_page_information_updated(manager.store,pid,connection['id'])
    return app,session(app),directory


def settings():
    return {'enabled':True,'page_ids':['555','777'],'connection_id':1,'daily_time':'06:00',
            'timezone':'America/Los_Angeles','caption_template':'Funny tees. Big laughs. https://blueox179.com'}


def test_folder_status_requires_page_session_and_configure_requires_csrf(tmp_path):
    app,headers,directory=fixture(tmp_path)
    assert request(app,'/api/video-source')[0]==401
    assert request(app,'/api/video-source',headers={'cookie':'cm_session=wrong-app'})[0]==401
    status,_,body=request(app,'/api/video-source',headers=headers)
    assert status==200 and body['configured'] is False and body['settings']['enabled'] is False
    assert body['settings']['directory']==str(directory.resolve())
    assert body['items']==[] and body['stats']['queued']==0
    assert request(app,'/api/video-source/settings','PUT',settings(),{'cookie':headers['cookie']})[0]==403
    assert request(app,'/api/video-source/scan','POST',{}, {'cookie':headers['cookie']})[0]==403
    assert not app.state.manager.store.reel_jobs()


def test_save_explicit_source_rules_does_not_create_jobs_or_call_meta(tmp_path):
    app,headers,_=fixture(tmp_path)
    app.state.manager.graph_factory=lambda:(_ for _ in ()).throw(AssertionError('No Meta while saving source rules'))
    status,_,body=request(app,'/api/video-source/settings','PUT',settings(),headers)
    assert status==200 and body['configured'] is True
    assert all(body['settings'][key]==value for key,value in settings().items())
    assert not app.state.manager.store.reel_jobs()
    assert TOKEN not in json.dumps(body)
    status,_,body=request(app,'/api/video-source',headers=headers)
    assert status==200 and body['settings']['enabled'] is True and body['stats']['queued']==0


def test_source_cannot_be_enabled_for_pages_without_information_changes(tmp_path):
    app,headers,_=fixture(tmp_path,information_updated=False)
    status,_,body=request(app,'/api/video-source/settings','PUT',settings(),headers)
    assert status==422 and body['code']=='page_information_required'
    assert not app.state.video_source.status()['settings']['enabled']
    assert not app.state.manager.store.reel_jobs()


def test_client_cannot_select_another_filesystem_path_or_echo_invalid_input(tmp_path):
    app,headers,_=fixture(tmp_path)
    for extra in ({'directory':'C:/private/'+TOKEN},{'token':TOKEN}):
        status,_,body=request(app,'/api/video-source/settings','PUT',{**settings(),**extra},headers)
        assert status==422 and TOKEN not in json.dumps(body)
    assert not app.state.manager.store.reel_jobs()


def test_invalid_schedule_and_non_boolean_enable_do_not_activate_source(tmp_path):
    app,headers,_=fixture(tmp_path)
    for patch in ({'enabled':'true'},{'daily_time':'24:00'},{'timezone':'Not/A_Zone'},
                  {'page_ids':['555','555']},{'page_ids':[]},{'caption_template':'x'*2201}):
        status,_,_=request(app,'/api/video-source/settings','PUT',{**settings(),**patch},headers)
        assert status==422
    status,_,body=request(app,'/api/video-source',headers=headers)
    assert status==200 and body['settings']['enabled'] is False


def test_unlinked_page_cannot_become_an_automatic_target(tmp_path):
    app,headers,_=fixture(tmp_path)
    status,_,body=request(app,'/api/video-source/settings','PUT',{**settings(),'page_ids':['999']},headers)
    assert status in (400,403,409) and TOKEN not in json.dumps(body)
    assert not app.state.manager.store.reel_jobs()


def test_pausing_source_keeps_previously_saved_rules_and_queue(tmp_path):
    app,headers,_=fixture(tmp_path)
    request(app,'/api/video-source/settings','PUT',settings(),headers)
    revision=app.state.video_source.status()['settings']['revision']
    status,_,body=request(app,'/api/video-source/settings','PUT',{**settings(),'enabled':False,'revision':revision},headers)
    assert status==200 and body['settings']['enabled'] is False
    assert body['settings']['page_ids']==['555','777']
    assert not app.state.manager.store.reel_jobs()


def test_existing_source_cannot_be_overwritten_by_missing_or_stale_revision(tmp_path):
    app,headers,_=fixture(tmp_path)
    code,_,saved=request(app,'/api/video-source/settings','PUT',settings(),headers)
    assert code==200
    before=app.state.video_source.status()['settings']
    for patch,expected in (({},'source_revision_required'),({'revision':before['revision']-1},'source_revision_changed')):
        code,_,value=request(app,'/api/video-source/settings','PUT',{**settings(),'enabled':False,**patch},headers)
        assert code==409 and value['code']==expected
        assert app.state.video_source.status()['settings']==before
