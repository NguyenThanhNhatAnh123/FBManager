"""Private HTTP MCP gateway. Identities and write journals stay user-bound."""
import asyncio
from contextlib import asynccontextmanager,suppress
from dataclasses import dataclass,field
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import time
from urllib.parse import unquote
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse,Response
from starlette.routing import Route

from .bridge import ManagerClient
from .chrome_tools import tools as chrome_tools
from .common import Tool,ToolError
from .journal import Journal
from .media import IMAGE,VIDEO,MediaVault
from .page_tools import tools as page_tools
from .protocol import MAX_LINE,Protocol,strict_json
from .registry import MediaInput,Registry,basic_tools

VERSION='1.4.1'
SESSION_IDLE=1800
MAX_SESSIONS=24
MAX_USER_SESSIONS=8
MAX_SPOOL_BYTES=4*1024**3
MIN_FREE_BYTES=512*1024**2
OPERATOR_WRITES=frozenset(('chrome_start_profile','chrome_stop_profile','chrome_check_facebook',
    'chrome_stop_facebook','chrome_reel_upload','chrome_create_reel','chrome_cancel_reel',
    'chrome_resume_reel','chrome_verify_reel','chrome_archive_reel','chrome_restore_reel',
    'pages_connection_check','pages_connection_sync','pages_refresh','pages_edit','pages_set_image',
    'pages_reel_upload','pages_reel_create','pages_reel_cancel','pages_reel_resume','pages_reel_verify',
    'pages_idea_scan','pages_idea_views_refresh'))
WORKSPACE_OWNER_WRITES=frozenset(('chrome_open_bm_profile','chrome_check_proxies','chrome_check_bm_proxy',
    'pages_video_source_configure','pages_video_source_scan'))


class SessionMediaVault(MediaVault):
    """Only gateway-created spool files; ancestor directory names are irrelevant."""
    def _path(self,value,kind):
        # Native MediaVault also excludes broad private folder names. A server
        # may itself be installed under D:\Data, so this narrower vault checks
        # the gateway-owned UUID session directory rather than ancestor names.
        if not isinstance(value,str) or '\0' in value:raise ToolError('Media không hợp lệ.','invalid_media')
        source=Path(value)
        if not source.is_absolute() or source.suffix.lower() not in (VIDEO if kind=='video' else IMAGE):
            raise ToolError('Media không hợp lệ.','invalid_media')
        for part in (source,*source.parents):
            if part.is_symlink() or part.is_junction():raise ToolError('Media qua liên kết bị từ chối.','invalid_media')
        resolved=source.resolve()
        if len(self.roots)!=1 or resolved.parent!=self.roots[0] or not re.fullmatch(r'[a-f0-9-]{36}\.(?:png|jpg|jpeg|mp4|mov|m4v)',resolved.name):
            raise ToolError('Media không thuộc phiên này.','invalid_media')
        return resolved


def load_config(path):
    values=strict_json(Path(path).read_text(encoding='utf-8-sig'))
    keys={'version','bind_host','port','manager_port','public_origin','allow_http_loopback'}
    if not isinstance(values,dict) or set(values)!=keys or type(values['version']) is not int or values['version']!=1:
        raise ValueError('Invalid server configuration')
    if values['bind_host']!='127.0.0.1' or type(values['allow_http_loopback']) is not bool:
        raise ValueError('Gateway must bind to loopback')
    if any(type(values[k]) is not int or not 1024<=values[k]<=65535 for k in ('port','manager_port')) or values['port']==values['manager_port']:
        raise ValueError('Invalid server ports')
    origin=values['public_origin']
    if origin is not None:
        from via_auth.access_tokens import canonical_public_origin
        values['public_origin']=canonical_public_origin(origin)
    return values


def private_folder(path):
    from via_auth.store import _assert_private_path,protect_private
    _assert_private_path(path,directory=True,missing=True)
    path.mkdir(parents=True,exist_ok=True)
    protect_private(path)
    return path


@dataclass
class Session:
    identifier:str
    user_id:int
    token_id:str
    token_digest:str
    client:object
    protocol:object
    media:object
    folder:Path
    version:str=''
    touched:float=field(default_factory=time.monotonic)
    lock:object=field(default_factory=asyncio.Lock)
    uploads:dict=field(default_factory=dict)
    media_bytes:int=0


class Gateway:
    def __init__(self,root,config,store,*,client_factory=ManagerClient,catalog=None,workspaces=None):
        self.root=Path(root).resolve();self.config=config;self.store=store
        self.client_factory=client_factory;self.catalog=catalog
        self.workspaces=workspaces
        self.resource=(config['public_origin'] or f"http://127.0.0.1:{config['port']}")+'/mcp'
        self.sessions={};self.lock=asyncio.Lock()
        self.spool=self.root/'via-mcp'/'.runtime'/'remote-media'
        self.journals=self.root/'via-mcp'/'data'/'remote-users'

    def manager_config(self,actor):
        if self.workspaces is None:return self.config
        entry=self.workspaces.get(actor['id']) if actor else None
        if entry is None:return None
        from via_auth import workspace_host
        port=entry['manager_port']
        return {**self.config,'manager_port':port,'manager_host':workspace_host(actor['id'],port)}

    def actor(self,request):
        raw=request.headers.get('authorization','')
        if not raw.startswith('Bearer '):return None,None
        token=raw[7:]
        actor=self.store.resolve_access_token(token,audience=self.resource)
        return actor,token

    def live(self,session,token):
        actor=self.store.resolve_access_token(token,audience=self.resource)
        return bool(actor and actor['id']==session.user_id and actor['token_id']==session.token_id
                    and secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(),session.token_digest))

    def register_upload(self,session,args):
        match=re.fullmatch(r'upload:([a-f0-9-]{36})',args.file_path)
        row=session.uploads.get(match[1]) if match else None
        if not row or row['kind']!=args.kind:raise ToolError('Media không thuộc phiên này. Chọn lại tệp trên máy của bạn.','invalid_media')
        return {key:value for key,value in row.items() if key!='path'}

    def new_session(self,actor,token):
        target=self.manager_config(actor)
        if target is None:raise ValueError('Workspace unavailable')
        entry=self.workspaces[actor['id']] if self.workspaces is not None else None
        secondary=entry is not None and entry['root']!='.'
        options={'workspace_user_id':actor['id']} if entry else {}
        if secondary:options['workspace_root']=self.root/entry['root']
        identifier=str(uuid4());folder=private_folder(self.spool/identifier)
        media=SessionMediaVault([folder]);client=self.client_factory(target['manager_port'],media,bearer_token=token,**options)
        try:
            journal_root=private_folder(self.journals/str(actor['id']))
            journal=Journal(journal_root)
            registry=Registry(client,media,journal,catalog=self.catalog or basic_tools()+chrome_tools()+page_tools())
            session=Session(identifier,actor['id'],actor['token_id'],hashlib.sha256(token.encode()).hexdigest(),client,None,media,folder)
            registry.catalog['via_register_media']=Tool('via_register_media',
                'Transfer a user-selected image/video from this client through the private Via gateway. The client supplies a local absolute path; the server accepts session-bound upload handles only. Does not publish.',
                MediaInput,lambda ctx,args:self.register_upload(session,args))
            write='via:write' in actor['scopes']
            permitted={name for name,tool in registry.catalog.items() if
                       not (secondary and name.startswith('pages_idea_')) and
                       ((tool.read_only and name!='via_register_media') or write and
                       (name=='via_register_media' or actor['role']=='admin' or name in OPERATOR_WRITES
                        or entry is not None and name in WORKSPACE_OWNER_WRITES))}
            registry.denied=frozenset(registry.catalog)-permitted
            registry.catalog={key:value for key,value in registry.catalog.items() if key in permitted}
            session.protocol=Protocol(registry)
            return session
        except BaseException:
            client.close();self.remove_folder(folder);raise

    def remove_folder(self,folder):
        # Never delete through symlinks/junctions or outside this gateway's spool.
        from via_auth.store import _assert_private_path
        if folder.parent!=self.spool or not re.fullmatch(r'[a-f0-9-]{36}',folder.name):return
        if not folder.exists():return
        _assert_private_path(folder,directory=True)
        for entry in folder.iterdir():
            if entry.is_dir():raise ValueError('Unexpected media directory')
            _assert_private_path(entry)
        shutil.rmtree(folder)

    async def close(self,session):
        self.sessions.pop(session.identifier,None)
        try:await self.owned_work(session.client.close)
        finally:await self.owned_work(self.remove_folder,session.folder)

    async def reap(self):
        for session in tuple(self.sessions.values()):
            if not session.lock.locked() and time.monotonic()-session.touched>SESSION_IDLE:
                await self.close(session)

    @staticmethod
    def fail(code,status):return JSONResponse({'code':code,'detail':'Kết nối Codex chưa hoàn tất. Kiểm tra token và phiên kết nối.'},status)

    async def session_for(self,request):
        actor,token=self.actor(request)
        if not actor:return None,None,self.fail('authentication_required',401)
        identifier=request.headers.get('mcp-session-id','')
        session=self.sessions.get(identifier)
        if not session:return None,None,self.fail('session_not_found',404)
        if not self.live(session,token):return None,None,self.fail('session_not_found',404)
        if request.headers.get('mcp-protocol-version')!=session.version:return None,None,self.fail('protocol_version_required',400)
        return session,token,None

    async def bounded_body(self,request,limit):
        body=bytearray()
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>limit:raise ValueError('body_too_large')
        return bytes(body)

    async def owned_work(self,function,*args,on_cancel=None):
        """Retain session ownership while a synchronous manager call finishes."""
        task=asyncio.create_task(asyncio.to_thread(function,*args))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            # ASGI shutdown/disconnect must not unlock a still-running mutation
            # or delete its media. The durable operation journal records outcome.
            while not task.done():
                with suppress(Exception,asyncio.CancelledError):await asyncio.shield(task)
            if on_cancel and not task.cancelled() and task.exception() is None:
                await on_cancel(task.result())
            else:
                with suppress(BaseException):task.result()
            raise

    async def mcp(self,request):
        if not self.config['public_origin'] and not self.config['allow_http_loopback']:return self.fail('mcp_not_configured',503)
        if request.method=='GET':return Response(status_code=405,headers={'Allow':'POST, DELETE'})
        if request.method=='DELETE':
            session,token,rejected=await self.session_for(request)
            if rejected:return rejected
            async with session.lock:
                if not self.live(session,token):return self.fail('authentication_required',401)
                await self.close(session)
            return Response(status_code=204)
        if request.headers.get('content-type','').split(';')[0]!='application/json':return self.fail('content_type_required',415)
        actor,token=self.actor(request)
        if not actor:return self.fail('authentication_required',401)
        if self.manager_config(actor) is None:return self.fail('workspace_unavailable',403)
        try:message=strict_json((await self.bounded_body(request,MAX_LINE)).decode('utf-8'))
        except (ValueError,UnicodeError,RecursionError,TimeoutError,ClientDisconnect):return self.fail('invalid_json',400)
        if not isinstance(message,dict):return self.fail('invalid_json_rpc',400)
        initial=message.get('method')=='initialize' and not request.headers.get('mcp-session-id')
        if initial:
            async with self.lock:
                await self.reap()
                if len(self.sessions)>=MAX_SESSIONS or sum(s.user_id==actor['id'] for s in self.sessions.values())>=MAX_USER_SESSIONS:
                    return self.fail('session_limit',429)
                if not self.store.resolve_access_token(token,audience=self.resource):return self.fail('authentication_required',401)
                session=await self.owned_work(self.new_session,actor,token,on_cancel=self.close)
                self.sessions[session.identifier]=session
        else:
            session,token,rejected=await self.session_for(request)
            if rejected:return rejected
        async with session.lock:
            if session.identifier not in self.sessions or not self.live(session,token):return self.fail('session_not_found',404)
            session.touched=time.monotonic()
            reply=await self.owned_work(session.protocol.handle,message)
            session.touched=time.monotonic()
            if initial:
                if not reply or 'result' not in reply:
                    await self.close(session)
                    return JSONResponse(reply or {'code':'invalid_initialize'},400)
                session.version=reply['result']['protocolVersion']
                reply['result']['serverInfo']={'name':'via-manager','version':VERSION}
            headers={'Mcp-Session-Id':session.identifier,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
            return JSONResponse(reply,headers=headers) if reply is not None else Response(status_code=204,headers=headers)

    async def upload(self,request):
        if not self.config['public_origin'] and not self.config['allow_http_loopback']:return self.fail('mcp_not_configured',503)
        session,token,rejected=await self.session_for(request)
        if rejected:return rejected
        actor,_=self.actor(request)
        if 'via:write' not in actor['scopes']:return self.fail('token_scope_required',403)
        kind=request.query_params.get('kind');digest=request.headers.get('x-content-sha256','')
        try:name=unquote(request.headers.get('x-filename',''),errors='strict')
        except UnicodeError:return self.fail('invalid_media',400)
        try:length=int(request.headers.get('content-length',''))
        except ValueError:return self.fail('length_required',411)
        limit=1073741824 if kind=='video' else 8388608
        if (set(request.query_params)!= {'kind'} or kind not in ('image','video') or not re.fullmatch('[a-f0-9]{64}',digest)
            or not name or len(name)>200 or any(ord(c)<32 for c in name) or '/' in name or '\\' in name or ':' in name
            or Path(name).suffix.lower() not in (VIDEO if kind=='video' else IMAGE)):
            return self.fail('invalid_media',400)
        if not 0<length<=limit:return self.fail('media_too_large',413)
        async with session.lock:
            if session.identifier not in self.sessions or not self.live(session,token):return self.fail('session_not_found',404)
            if not session.protocol.ready:return self.fail('initialize_required',400)
            if len(session.uploads)>=128:return self.fail('media_limit',429)
            async with self.lock:
                if sum(s.media_bytes for s in self.sessions.values())+length>MAX_SPOOL_BYTES:return self.fail('media_quota',507)
                if shutil.disk_usage(self.spool).free<length+MIN_FREE_BYTES:return self.fail('disk_space_required',507)
                session.media_bytes+=length
            identifier=str(uuid4());path=session.folder/(identifier+Path(name).suffix.lower())
            committed=False
            try:
                total=0;hashed=hashlib.sha256()
                async with asyncio.timeout(900):
                    with path.open('xb') as handle:
                        async for chunk in request.stream():
                            total+=len(chunk)
                            if total>length:raise ValueError('length_mismatch')
                            hashed.update(chunk);handle.write(chunk)
                if total!=length or not secrets.compare_digest(hashed.hexdigest(),digest):raise ValueError('media_changed')
                if not self.live(session,token):return self.fail('authentication_required',401)
                row=await self.owned_work(session.media.register,str(path),kind)
                row['name']=name;row['note']='Tệp riêng của phiên Codex này; chưa đăng lên Facebook.'
                session.uploads[identifier]=row
                committed=True;session.touched=time.monotonic()
                return JSONResponse({'upload_id':identifier},201)
            except (ValueError,OSError,ToolError,TimeoutError,ClientDisconnect):return self.fail('invalid_media',400)
            finally:
                if not committed:
                    session.media_bytes-=length
                    with suppress(FileNotFoundError):path.unlink()


def create_app(root,config,*,store=None,client_factory=ManagerClient,catalog=None,workspaces=None,dashboard_transport=None):
    from via_auth import IdentityGate,IdentityStore
    if config['public_origin'] is not None:
        from via_auth.access_tokens import canonical_public_origin
        config={**config,'public_origin':canonical_public_origin(config['public_origin'])}
    store=store or IdentityStore(Path(root)/'via-auth'/'data')
    gateway=Gateway(root,config,store,client_factory=client_factory,catalog=catalog,workspaces=workspaces)
    @asynccontextmanager
    async def lifespan(app):
        store.prepare();private_folder(gateway.spool);private_folder(gateway.journals)
        # A prior gateway process may have exited during a transfer; these are
        # session-only media copies, never manager assets or saved schedules.
        for folder in gateway.spool.iterdir():
            if folder.is_dir() and re.fullmatch(r'[a-f0-9-]{36}',folder.name):gateway.remove_folder(folder)
        async def cleanup():
            while True:
                await asyncio.sleep(30)
                async with gateway.lock:await gateway.reap()
        reaper=asyncio.create_task(cleanup())
        try:yield
        finally:
            reaper.cancel()
            with suppress(asyncio.CancelledError):await reaper
            for session in tuple(gateway.sessions.values()):
                async with session.lock:await gateway.close(session)
    async def health(request):return JSONResponse({'name':'via-remote-mcp','ready':True,'version':VERSION})
    async def dashboard(request):
        from .dashboard_proxy import forward_http
        target=gateway.manager_config(request.scope.get('via.identity'))
        if target is None:return JSONResponse({'code':'workspace_unavailable'},403)
        return await forward_http(request,target,transport=dashboard_transport)
    routes=[Route('/health',health),Route('/mcp',gateway.mcp,methods=['GET','POST','DELETE']),
            Route('/mcp/media',gateway.upload,methods=['POST']),
            Route('/{path:path}',dashboard,methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'])]
    app=Starlette(routes=routes,lifespan=lifespan)
    app.state.gateway=gateway
    # Optional dashboard proxy module also supplies the fixed-upstream socket route.
    from .dashboard_proxy import add_websocket_route
    add_websocket_route(app,config,manager_config=gateway.manager_config)
    app.add_middleware(IdentityGate,store=store,port=config['port'],public_origin=config['public_origin'],
        allow_service=False,mcp_resource=gateway.resource if config['public_origin'] or config['allow_http_loopback'] else None,mcp_paths=('/mcp','/mcp/media'),
        portal_only=False,public_paths=('/health',),**({'workspace_owner':True} if workspaces is not None else {}))
    return app
