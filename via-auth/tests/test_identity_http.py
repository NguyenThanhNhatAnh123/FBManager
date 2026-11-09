"""Real HTTP identity protocol tests with temporary accounts and a fake clock."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from via_auth import IdentityGate,IdentityStore
from via_auth.middleware import COOKIE
from via_auth.store import token_hash

pytestmark=pytest.mark.identity

class IsolatedSite(SimpleNamespace):
    def __repr__(self):return '<isolated identity fixture>'


@pytest.fixture
def identity_http(tmp_path):
    clock=[2_000_000_000]
    store=IdentityStore(tmp_path/'identity',clock=lambda:clock[0])
    accounts={row['username']:row for row in store.bootstrap()}
    async def probe(request):
        actor=request.scope['via.identity']
        return JSONResponse({'username':actor['username'],'role':actor['role']})
    downstream=Starlette(routes=[Route('/api/probe',probe,methods=['GET','POST'])])
    app=IdentityGate(downstream,store=store,port=8000)
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',1234)) as client:
        yield IsolatedSite(client=client,store=store,accounts=accounts,clock=clock)


def login(site,username='admin'):
    account=site.accounts[username]
    response=site.client.post('/auth/api/login',json={'username':username,'password':account['password']})
    assert response.status_code==200
    return site.client.get('/auth/api/me').json()


def test_login_serves_only_its_allowlisted_geist_assets_before_authentication(identity_http):
    client=identity_http.client
    frontend=Path(__file__).resolve().parents[2]/'meta-page-manager'/'frontend'
    assets=[('fonts.css','text/css')]+[
        (f'fonts/geist-{subset}-wght-normal.woff2','font/woff2')
        for subset in ('latin','latin-ext','vietnamese')
    ]
    login_page=client.get('/auth/login')
    assert "font-src 'self'" in login_page.headers['content-security-policy']
    for relative,media_type in assets:
        path='/auth/static/'+relative
        expected=(frontend/relative).read_bytes()
        response=client.get(path)
        assert response.status_code==200 and response.content==expected
        assert response.headers['content-type'].split(';')[0]==media_type
        head=client.head(path)
        assert head.status_code==200 and not head.content
        assert int(head.headers['content-length'])==len(expected)
        assert client.post(path).status_code==401
        assert client.get(path,headers={'Origin':'https://foreign.example'}).status_code==403
        assert client.get(path,headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    for path in ('/auth/static/fonts/Geist-LICENSE.txt','/auth/static/fonts/%2e%2e/%2e%2e/data/identity.sqlite3'):
        assert client.get(path,follow_redirects=False).status_code==303


def test_auth_request_body_limit_and_errors_never_echo_password(identity_http):
    site=identity_http;client=site.client;sentinel='SecretFixturePassword-No-Echo'
    known=client.post('/auth/api/login',json={'username':'admin','password':sentinel})
    unknown=client.post('/auth/api/login',json={'username':'missinguser','password':sentinel})
    assert known.status_code==unknown.status_code==401
    assert known.json()['code']==unknown.json()['code']=='invalid_credentials'
    assert sentinel not in known.text+unknown.text
    assert client.post('/auth/api/login',content='{}',headers={'Content-Type':'text/plain'}).status_code==415
    # A streamed request has no trustworthy Content-Length to preflight.
    raw=json.dumps({'username':'admin','password':'x'*17_000}).encode()
    response=client.post('/auth/api/login',content=iter((raw[:9000],raw[9000:])),headers={'Content-Type':'application/json'})
    assert response.status_code==413 and response.json()['code']=='body_too_large'
    assert client.post('/auth/api/login',json={'username':'admin','password':sentinel},headers={'Origin':'https://foreign.example'}).status_code==403


def test_password_rotation_revokes_raw_session_and_idle_expiry_is_enforced(identity_http):
    site=identity_http;client=site.client;me=login(site)
    old=client.cookies.get(COOKIE)
    assert client.get('/api/probe').status_code==403
    password='NewPrivateFixturePassword-2026'
    response=client.post('/auth/api/password',json={'current_password':site.accounts['admin']['password'],'new_password':password},headers={'X-Via-CSRF':me['csrf_token']})
    assert response.status_code==200
    raw=client.cookies.get(COOKIE)
    assert raw!=old and client.get('/api/probe').json()=={'username':'admin','role':'admin'}
    assert client.get('/api/probe',headers={'Cookie':COOKIE+'='+old}).status_code==401
    with site.store.connect() as db:
        sessions=[dict(row) for row in db.execute('SELECT * FROM sessions')]
        encoded=db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
    assert len(sessions)==1 and sessions[0]['digest']==token_hash(raw)
    assert raw not in json.dumps(sessions) and password not in encoded and encoded.startswith('scrypt-v1$')
    site.clock[0]+=31*60
    assert client.get('/api/probe').status_code==401


def test_failed_login_limit_is_enforced_before_correct_password(identity_http):
    site=identity_http;client=site.client
    for _ in range(10):
        assert client.post('/auth/api/login',json={'username':'operator1','password':'WrongFixturePassword'}).status_code==401
    response=client.post('/auth/api/login',json={'username':'operator1','password':site.accounts['operator1']['password']})
    assert response.status_code==429 and response.json()['code']=='login_rate_limited'
    site.clock[0]+=301
    assert client.post('/auth/api/login',json={'username':'operator1','password':site.accounts['operator1']['password']}).status_code==200
