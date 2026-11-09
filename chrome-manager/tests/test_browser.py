"""Opt-in integration tests against real Chrome and owned loopback fixture servers."""
import asyncio
import base64
import http.client
import json
import threading
from contextlib import ExitStack,contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import httpx
import pytest
from app.core.config import Config
from app.main import create_app

class Site(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', '/landed')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path.startswith('/ip'):
            body=json.dumps({'ip':'198.51.100.24'}).encode()
            content_type='application/json'
        else:
            body=b'<!doctype html><title>Owned QA Site</title><h1 id="title">Authorized test</h1><input id="email"><button id="submit" onclick="document.querySelector(\'#title\').textContent=\'Saved\'">Save</button>'
            content_type='text/html'
        self.send_response(200)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class ForwardProxy(BaseHTTPRequestHandler):
    hits=0
    def log_message(self, *_): pass
    def do_CONNECT(self):
        self.send_error(502)  # These tests never need external/TLS traffic.
    def do_GET(self):
        expected=getattr(self.server,'required_auth',None)
        if expected and self.headers.get('Proxy-Authorization')!=expected:
            self.send_response(407)
            self.send_header('Proxy-Authenticate','Basic realm="Owned QA proxy"')
            self.send_header('Content-Length','0')
            self.end_headers()
            return
        target=urlsplit(self.path)
        if target.hostname!='127.0.0.1':
            self.send_error(502)
            return
        self.server.hits+=1
        self.server.visited_paths.add(target.path)
        connection=http.client.HTTPConnection(target.hostname,target.port,timeout=5)
        try:
            connection.request('GET',(target.path or '/')+('?' + target.query if target.query else ''))
            response=connection.getresponse()
            data=response.read()
            self.send_response(response.status)
            self.send_header('Content-Type',response.getheader('Content-Type','text/plain'))
            if response.getheader('Location'):
                self.send_header('Location',response.getheader('Location'))
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            connection.close()

@contextmanager
def server(handler):
    instance=ThreadingHTTPServer(('127.0.0.1',0),handler)
    instance.hits=0
    instance.visited_paths=set()
    thread=threading.Thread(target=instance.serve_forever,daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)

async def until(check,timeout=45):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(.1)

@pytest.mark.browser
async def test_real_profiles_proxy_isolation_reopen_queue_and_tasks(tmp_path):
    with server(Site) as site, server(ForwardProxy) as p1, server(ForwardProxy) as p2, server(ForwardProxy) as p3:
        target=f'http://127.0.0.1:{site.server_port}'
        config=Config(root=tmp_path,data_dir=tmp_path/'data',channel='chrome',headless=True,ip_check_url=target+'/ip')
        app=create_app(config)
        async with app.router.lifespan_context(app):
            svc=app.state.services
            transport=httpx.ASGITransport(app=app,client=('127.0.0.1',50000))
            async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8000') as client:
                await client.get('/api/session')
                settings=(await client.get('/api/settings')).json()
                settings.update(max_browsers=2,allowed_origins=[target],action_delay_ms=500,min_available_memory_mb=128,max_memory_percent=98,max_cpu_percent=100)
                assert (await client.put('/api/settings',json=settings)).status_code==200
                created=(await client.post('/api/profiles/bulk-create',json={'count':3})).json()['ids']
                for proxy in (p1,p2,p3):
                    proxy.required_auth='Basic '+base64.b64encode(b'qa_user:qa_pass').decode()
                content='\n'.join(f'127.0.0.1:{proxy.server_port}:qa_user:qa_pass' for proxy in (p1,p2,p3))
                assert (await client.post('/api/proxies/import',json={'content':content})).json()['assigned']==3
                await client.post('/api/proxies/check',json={})
                async def checked():
                    return not svc.proxies.checking
                await until(checked)
                assert all(p['status']=='Working' for p in (await client.get('/api/proxies')).json())
                for pid in created:
                    svc.browsers.request_start(pid,open_start_page=False,for_automation=True)
                async def two_open():
                    return len(svc.browsers.contexts)==2
                await until(two_open)
                assert len(svc.browsers.contexts)+len(svc.browsers.reserved)<=2
                assert len(svc.browsers.launches)>=1
                ids=list(svc.browsers.contexts)
                first=svc.browsers.contexts[ids[0]]
                second=svc.browsers.contexts[ids[1]]
                await first.add_cookies([{'name':'session_test','value':'only-first','url':target,'expires':2000000000}])
                assert not any(c['name']=='session_test' for c in await second.cookies())
                page=await first.new_page()
                # Manual browsing: no TaskManager guard has been installed.
                await page.goto(target+'/redirect',timeout=15000)
                assert page.url==target+'/landed'
                assert await page.title()=='Owned QA Site'
                await page.goto(target)
                await page.evaluate("localStorage.setItem('persisted', 'yes')")
                assert p1.hits>0 and p2.hits>0 and p3.hits>0  # Checks used every assigned proxy.
                await client.post(f'/api/profiles/{ids[0]}/stop')
                async def third_open():
                    return created[2] in svc.browsers.contexts
                await until(third_open)
                await client.post('/api/profiles/stop-selected',json={'profile_ids':created})
                context=await svc.browsers.ensure_open(ids[0],for_automation=True)
                assert any(c['name']=='session_test' for c in await context.cookies())
                page=await context.new_page()
                await page.goto(target)
                assert await page.evaluate("localStorage.getItem('persisted')")=='yes'
                await svc.browsers.stop(ids[0])

                task={'name':'Owned website test','authorized':True,'close_browser':True,'actions':[
                    {'type':'open_url','url':target},{'type':'wait_load'},{'type':'read_title'},
                    {'type':'fill','selector':'#email','value':'qa@example.test'},
                    {'type':'click','selector':'#submit'},{'type':'read_text','selector':'#title'},
                    {'type':'scroll','pixels':400},{'type':'screenshot'},{'type':'close_tab'}]}
                tid=(await client.post('/api/tasks',json=task)).json()['id']
                runs=(await client.post(f'/api/tasks/{tid}/run',json={'profile_ids':created})).json()['run_ids']
                async def finished():
                    return not svc.tasks.jobs
                await until(finished,90)
                history=(await client.get('/api/tasks/history')).json()['items']
                assert {r['id'] for r in history}==set(runs)
                assert all(r['status']=='Completed' and r['progress']==100 for r in history)
                assert all(any(step.get('title')=='Owned QA Site' for step in r['result']) for r in history)
                assert len(list(config.screenshots_dir.glob('*.png')))==3
                assert len(svc.browsers.contexts)==0

                task['name']='Cancellation test'
                task['actions']=[{'type':'open_url','url':target},{'type':'wait_selector','selector':'#does-not-exist','timeout_ms':60000}]
                tid=(await client.post('/api/tasks',json=task)).json()['id']
                await client.post(f'/api/tasks/{tid}/run',json={'profile_ids':created})
                async def running():
                    return len(svc.browsers.contexts)>0
                await until(running)
                assert (await client.post(f'/api/tasks/{tid}/stop')).status_code==200
                latest=(await client.get('/api/tasks/history')).json()['items'][:3]
                assert all(r['status']=='Cancelled' for r in latest)
                assert not svc.tasks.jobs and not svc.browsers.contexts

@pytest.mark.browser
async def test_concurrent_regular_and_two_bm_profiles_keep_their_proxy_and_cookies(tmp_path):
    with ExitStack() as fixtures:
        site=fixtures.enter_context(server(Site))
        proxies=[fixtures.enter_context(server(ForwardProxy)) for _ in range(3)]
        origin=f'http://127.0.0.1:{site.server_port}'
        config=Config(root=tmp_path,data_dir=tmp_path/'data',channel='chrome',headless=True,ip_check_url=origin+'/ip')
        app=create_app(config)
        async with app.router.lifespan_context(app):
            svc=app.state.services
            transport=httpx.ASGITransport(app=app,client=('127.0.0.1',50000))
            async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8000') as client:
                assert (await client.get('/api/session')).status_code==200
                settings=(await client.get('/api/settings')).json()
                settings.update(max_browsers=3,min_available_memory_mb=128,max_memory_percent=98,max_cpu_percent=100,
                                require_proxy=True,proxy_routing='all',facebook_auto_refresh=False)
                assert (await client.put('/api/settings',json=settings)).status_code==200
                profile_ids=[]
                for index,proxy in enumerate(proxies):
                    body={'name':f'Isolated Chrome {index}'}
                    if index:
                        body['bm']={'portfolios':[{'bm_id':f'{index}01'},{'bm_id':f'{index}02'}]}
                    created=await client.post('/api/profiles',json=body)
                    assert created.status_code==201
                    profile_id=created.json()['id'];profile_ids.append(profile_id)
                    credentials=f'fixture{index}:route{index}'
                    proxy.required_auth='Basic '+base64.b64encode(credentials.encode()).decode()
                    endpoint=f'127.0.0.1:{proxy.server_port}:{credentials}'
                    if index:
                        saved=await client.put(f'/api/bm-profiles/{profile_id}/proxy',json={'content':endpoint,'check_after_save':False})
                        mobile=await client.put(f'/api/bm-profiles/{profile_id}/mobile',json={'enabled':True})
                        assert mobile.status_code==200
                    else:
                        saved=await client.post('/api/proxies/import',json={'content':endpoint})
                    assert saved.status_code==200

                contexts=await asyncio.gather(*(svc.browsers.ensure_open(profile_id,for_automation=index==0)
                                                for index,profile_id in enumerate(profile_ids)))
                assert set(svc.browsers.contexts)==set(profile_ids)
                for index,context in enumerate(contexts):
                    await context.add_cookies([{'name':'fixture_identity','value':str(index),'url':origin}])

                async def visit(index,context):
                    cookies={cookie['value'] for cookie in await context.cookies(origin) if cookie['name']=='fixture_identity'}
                    assert cookies=={str(index)}
                    page=await context.new_page()
                    await page.goto(f'{origin}/isolated-route-{index}',wait_until='domcontentloaded',timeout=15000)
                    assert await page.title()=='Owned QA Site'

                await asyncio.gather(*(visit(index,context) for index,context in enumerate(contexts)))
                for index,proxy in enumerate(proxies):
                    observed={path for path in proxy.visited_paths if path.startswith('/isolated-route-')}
                    assert observed=={f'/isolated-route-{index}'}

                visited_bms={profile_ids[1]:[],profile_ids[2]:[]}

                def business_fixture(profile_id):
                    async def fulfill(route):
                        visited_bms[profile_id].append(urlsplit(route.request.url).query)
                        await route.fulfill(status=200,content_type='text/html',body='<title>BM fixture</title>')
                    return fulfill

                # Use the real BM open route and Chrome contexts. Intercept the
                # destination in each context so no request reaches Meta.
                for profile_id,context in zip(profile_ids[1:],contexts[1:]):
                    await context.route('https://business.facebook.com/settings/**',business_fixture(profile_id))
                opened=await asyncio.gather(
                    client.post(f'/api/bm-profiles/{profile_ids[1]}/open',json={'bm_id':'102'}),
                    client.post(f'/api/bm-profiles/{profile_ids[2]}/open',json={'bm_id':'201'}))
                assert [response.status_code for response in opened]==[202,202]
                rejected=await asyncio.gather(
                    client.post(f'/api/bm-profiles/{profile_ids[1]}/open',json={'bm_id':'201'}),
                    client.post(f'/api/bm-profiles/{profile_ids[2]}/open',json={'bm_id':'102'}),
                    client.post(f'/api/bm-profiles/{profile_ids[2]}/open',json={}))
                assert [response.status_code for response in rejected]==[404,404,422]
                assert visited_bms=={profile_ids[1]:['business_id=102'],profile_ids[2]:['business_id=201']}


@pytest.mark.browser
async def test_failed_proxy_never_falls_back(tmp_path):
    # Reserve then close a port to obtain an unavailable proxy without contacting the Internet.
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        closed_port=sock.getsockname()[1]
    app=create_app(Config(root=tmp_path,data_dir=tmp_path/'data',headless=True))
    async with app.router.lifespan_context(app):
        svc=app.state.services
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',50000)),base_url='http://127.0.0.1:8000') as client:
            await client.get('/api/session')
            pid=(await client.post('/api/profiles',json={'name':'Fail closed'})).json()['id']
            await client.post('/api/proxies/import',json={'content':f'127.0.0.1:{closed_port}'})
            await client.post('/api/proxies/check',json={})
            async def checked(): return not svc.proxies.checking
            await until(checked,20)
            assert (await client.get('/api/proxies')).json()[0]['status'] in ('Failed','Timeout')
            assert (await client.post(f'/api/profiles/{pid}/start')).status_code==400
            assert not svc.browsers.contexts
