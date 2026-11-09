import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.core.config import Config
from app.main import create_app
from app.models.entities import Profile, Proxy, Task, TaskRun
from app.services.browsers import BrowserManager

@pytest.fixture
def app(tmp_path, monkeypatch):
    # Unit API tests do not launch a browser; real browser tests are separate.
    async def initialize(self): pass
    monkeypatch.setattr(BrowserManager, 'initialize', initialize)
    return create_app(Config(root=tmp_path, data_dir=tmp_path/'data'))

@pytest.fixture
def client(app):
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as client:
        assert client.get('/api/session').status_code == 200
        yield client

def test_create_100_and_persistence(client, app):
    response=client.post('/api/profiles/bulk-create',json={'count':100})
    assert response.status_code == 200 and response.json()['created'] == 100
    assert client.post('/api/profiles/bulk-create',json={'count':100}).json()['created'] == 0
    profiles=client.get('/api/profiles').json()
    assert profiles[0]['name']=='Chrome_001' and profiles[-1]['name']=='Chrome_100'
    assert len(set(p['profile_path'] for p in profiles))==100
    from app.database.session import Database
    separate=Database(app.state.services.config.data_dir/'manager.sqlite3')
    with separate.session() as session:
        assert len(list(session.scalars(select(Profile))))==100
    separate.engine.dispose()

def test_proxy_import_assigns_unique_and_hides_credentials(client, app):
    client.post('/api/profiles/bulk-create',json={'count':100})
    content='\n'.join(f'127.0.0.1:{30000+i}:user{i}:secret{i}' for i in range(100))
    result=client.post('/api/proxies/import',json={'content':content}).json()
    assert result['created']==100 and result['assigned']==100
    profiles=client.get('/api/profiles').json()
    assert len(set(p['proxy_id'] for p in profiles))==100
    for index, row in enumerate(profiles):
        assert row['proxy']['port']==30000+index
    assert client.post('/api/proxies/import',json={'content':content}).json()['duplicates']==100
    assert 'secret0' not in client.get('/api/proxies').text
    with app.state.services.db.session() as session:
        row=session.get(Proxy,1)
        assert 'secret0' not in row.encrypted_credentials

def test_require_proxy_edit_delete_and_history_reference(client, app):
    pid=client.post('/api/profiles',json={'name':'Original'}).json()['id']
    assert client.post(f'/api/profiles/{pid}/start').status_code==400
    before=client.get('/api/profiles').json()[0]['profile_path']
    revision=client.get('/api/profiles').json()[0]['revision']
    assert client.patch(f'/api/profiles/{pid}',json={'name':'Renamed','revision':revision}).status_code==200
    assert client.get('/api/profiles').json()[0]['profile_path']==before
    assert client.delete(f'/api/profiles/{pid}').status_code==400
    assert client.delete(f'/api/profiles/{pid}?confirm=true').status_code==200

def test_security_rejects_untrusted_origins_and_redacts_validation(client):
    assert client.post('/api/profiles',headers={'Origin':'https://evil.example'},json={'name':'no'}).status_code==403
    assert client.get('/api/session',headers={'Host':'evil.example'}).status_code==400
    response=client.post('/api/proxies/import',json={'content':123,'secret':'private-secret'})
    assert response.status_code==422 and 'private-secret' not in response.text
    remote = TestClient(client.app,base_url='http://127.0.0.1:8000',client=('192.0.2.1',1234))
    assert remote.get('/api/session').status_code==403

def test_tasks_allowlist_encryption_and_settings(client, app):
    config={'name':'Website test','authorized':True,'actions':[{'type':'open_url','url':'https://unapproved.test'}]}
    assert client.post('/api/tasks',json=config).status_code==400
    config['actions'][0]['url']='https://example.com'
    config['actions'].append({'type':'fill','selector':'#email','value':'private-form-value'})
    response=client.post('/api/tasks',json=config)
    assert response.status_code==201
    with app.state.services.db.session() as session:
        assert 'private-form-value' not in session.get(Task,response.json()['id']).configuration
    settings=client.get('/api/settings').json()
    settings['max_browsers']=20
    assert client.put('/api/settings',json=settings).json()['max_browsers']==20
    settings['max_browsers']=101
    assert client.put('/api/settings',json=settings).status_code==422

def test_websocket_requires_cookie_and_origin(client):
    with client.websocket_connect('ws://127.0.0.1:8000/ws',headers={'Origin':'http://127.0.0.1:8000'}) as websocket:
        data=websocket.receive_json()
        assert 'profiles' in data and 'cpu_percent' in data['system']


def test_dashboard_external_navigation_keeps_api_protected(client, app):
    dist=app.state.services.config.root/'frontend'/'dist'
    dist.mkdir(parents=True,exist_ok=True)
    (dist/'index.html').write_text('<!doctype html><title>Local dashboard</title>',encoding='utf-8')
    headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
    response=client.get('/',headers=headers)
    assert response.status_code==200 and '<title>Local dashboard</title>' in response.text
    assert client.get('/api/session',headers=headers).status_code==403
    assert client.get('/api/profiles',headers=headers).status_code==403
    assert client.post('/api/profiles',headers=headers,json={'name':'Blocked'}).status_code==403
