"""Authenticated, fixed-loopback relay for the existing dashboards."""
import asyncio
from contextlib import suppress
import re

import httpx
from starlette.responses import StreamingResponse
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect
from via_auth.middleware import CHROME_UI_PATHS
from via_auth.rest import legacy_api_path, problem

REQUEST_HEADERS=frozenset(('cookie','content-type','content-length','x-filename','x-via-csrf',
    'x-mpm-csrf','accept','range','if-range','if-none-match','if-modified-since'))
HOP_HEADERS=frozenset(('connection','keep-alive','proxy-authenticate','proxy-authorization',
    'te','trailer','transfer-encoding','upgrade','server','date'))


def allowed_path(path):
    return path in CHROME_UI_PATHS or path.startswith(('/api/','/pages/','/assets/')) or path in ('/pages','/favicon.ico','/vite.svg')


def request_limit(request):
    path=legacy_api_path(request.url.path)
    if request.method=='POST' and path in ('/api/reels/assets','/pages/api/reels/assets'):return 1073741824
    if request.method=='POST' and path=='/pages/api/idea-pipeline/images':return 25*1024**2
    if request.method=='POST' and re.fullmatch(r'/pages/api/pages/[0-9]{1,32}/image',path):return 8*1024**2
    return 2_500_000


async def forward_http(request,config,*,transport=None):
    # Cookie identities were checked at the public boundary. Credentials for
    # machine-to-machine calls never enter a browser relay request.
    actor=request.scope.get('via.identity')
    if not actor or actor.get('auth_method')=='mcp_token':
        return problem(401,'Đăng nhập để tiếp tục.')
    if not allowed_path(request.url.path):return problem(404,'Không tìm thấy trang.')
    limit=request_limit(request)
    try:
        length=int(request.headers.get('content-length','0'))
        if not 0<=length<=limit:return problem(413,'Tệp hoặc yêu cầu vượt giới hạn.')
    except ValueError:return problem(400,'Content-Length không hợp lệ.','invalid_length')
    origin=f"http://127.0.0.1:{config['manager_port']}"
    headers={key:value for key,value in request.headers.items() if key in REQUEST_HEADERS}
    # Host and Origin are reconstructed only after IdentityGate checked the
    # browser's real Host, Origin, identity, role and CSRF token.
    headers['origin']=origin
    manager_host=config.get('manager_host')
    if manager_host:headers['host']=manager_host
    headers['accept-encoding']='identity'
    raw_path=request.scope.get('raw_path',request.url.path.encode())
    query=request.scope.get('query_string',b'')
    url=httpx.URL(origin).copy_with(raw_path=raw_path+(b'?'+query if query else b''))
    too_large=False
    async def body():
        nonlocal too_large
        total=0
        async for chunk in request.stream():
            total+=len(chunk)
            if total>limit:
                too_large=True
                raise ValueError('body_too_large')
            yield chunk
    client=httpx.AsyncClient(trust_env=False,follow_redirects=False,transport=transport,
        timeout=httpx.Timeout(900,connect=5))
    try:
        upstream=await client.send(client.build_request(request.method,url,headers=headers,content=body()),stream=True)
    except Exception:
        await client.aclose()
        if too_large:return problem(413,'Tệp hoặc yêu cầu vượt giới hạn.')
        return problem(502,'Kiểm tra tiến độ trên server trước khi gửi lại thao tác.','manager_unavailable')
    response_headers=[]
    external=bool(config['public_origin'] and request.headers.get('host')==httpx.URL(config['public_origin']).netloc.decode())
    for key,value in upstream.headers.multi_items():
        if key in HOP_HEADERS:continue
        if key=='location':
            for local_origin in (origin,'http://'+manager_host if manager_host else origin):
                if value==local_origin or value.startswith(local_origin+'/'):
                    value=value[len(local_origin):] or '/'
                    break
        if key=='set-cookie' and external and '; secure' not in value.lower():value+='; Secure'
        if key=='content-security-policy':
            value=value.replace("connect-src 'self' ws://127.0.0.1:* ws://localhost:*", "connect-src 'self'"+((' '+config['public_origin'].replace('https://','wss://')) if external else ' ws://127.0.0.1:* ws://localhost:*'))
        response_headers.append((key.encode('latin1'),value.encode('latin1')))
    async def stream():
        try:
            async for chunk in upstream.aiter_raw():yield chunk
        finally:
            await upstream.aclose();await client.aclose()
    response=StreamingResponse(stream(),status_code=upstream.status_code)
    response.raw_headers=response_headers
    return response


def add_websocket_route(app,config,*,manager_config=None):
    async def socket(ws):
        from websockets.asyncio.client import connect
        actor=ws.scope.get('via.identity');gate=ws.scope.get('via.identity_gate')
        if not actor or actor.get('auth_method')=='mcp_token' or not gate:
            await ws.close(code=1008);return
        query=ws.scope.get('query_string',b'')
        if query not in (b'',b'mode=invalidate'):
            await ws.close(code=1008);return
        target=manager_config(actor) if manager_config else config
        if target is None:
            await ws.close(code=1008);return
        port=target['manager_port'];origin=f'http://127.0.0.1:{port}'
        authority=target.get('manager_host')
        upstream_url='ws://'+(authority or f'127.0.0.1:{port}')+'/ws'+('?mode=invalidate' if query else '')
        network={'host':'127.0.0.1','port':port} if authority else {}
        try:
            async with connect(upstream_url,origin=origin,additional_headers={'Cookie':ws.headers.get('cookie','')},
                proxy=None,compression=None,max_size=2_500_000,open_timeout=5,**network) as upstream:
                await ws.accept()
                async def browser_messages():
                    while True:
                        message=await ws.receive()
                        if message['type']=='websocket.disconnect':return
                        live=gate.live_actor(ws.scope)
                        if not live or live['id']!=actor['id'] or live['must_change_password']:return
                        if message.get('text') is not None:await upstream.send(message['text'])
                        elif message.get('bytes') is not None:await upstream.send(message['bytes'])
                async def server_messages():
                    async for message in upstream:
                        live=gate.live_actor(ws.scope)
                        if not live or live['id']!=actor['id'] or live['must_change_password']:return
                        if isinstance(message,str):await ws.send_text(message)
                        else:await ws.send_bytes(message)
                tasks=[asyncio.create_task(browser_messages()),asyncio.create_task(server_messages())]
                try:await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
        except (Exception,WebSocketDisconnect):pass
        finally:
            with suppress(Exception):await ws.close(code=1000)
    app.routes.append(WebSocketRoute('/ws',socket))
