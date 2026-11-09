"""Known project metadata is read-only; no arbitrary path or runtime checks."""
import json

from page_manager.project_assets import project_assets
from test_idea_workspace_api import fixture,request


def test_project_asset_catalog_requires_session_and_is_adopted_by_board(tmp_path):
    app,headers,_=fixture(tmp_path)
    path='/api/idea-pipeline/project-assets'
    assert request(app,path)[0]==401
    expected=project_assets()
    for path in (path,'/api/idea-pipeline','/api/idea-pipeline?summary=true'):
        code,_,value=request(app,path,headers=headers)
        assert code==200
        assert (value if path.endswith('/project-assets') else value['project_assets'])==expected
    assert not app.state.idea_runs.list()['items'] and not app.state.manager.store.reel_jobs()


def test_project_catalog_has_no_mutating_or_arbitrary_file_endpoint(tmp_path):
    app,headers,_=fixture(tmp_path);path='/api/idea-pipeline/project-assets'
    assert request(app,path,'POST',{'path':'PRIVATE_INPUT'},headers)[0]==405
    code,_,value=request(app,path+'?path=C%3A%2FPRIVATE_INPUT',headers=headers)
    assert code==200 and value==project_assets() and 'PRIVATE_INPUT' not in json.dumps(value)
